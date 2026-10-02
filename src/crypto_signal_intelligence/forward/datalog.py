"""Relevé QUOTIDIEN des données de contexte (journal F0_DONNEES, docs/FORWARD_TESTS.md, mission phase 1.3).

Ce n'est pas un test : une collecte en lecture seule, information uniquement ; aucune de ces données n'influence un
test en cours. Chaque jour UTC après 00:10 : une entrée par source publique (`forward/sources.py`), puis les
indicateurs calculés sur les bougies Binance déjà stockées (ATR et ADX journaliers de chaque paire de la liste
halal, corrélation glissante 30 jours entre BTC et le Nasdaq 100). Une source en échec est réessayée à chaque
passage ; le jour est clos quand tout a réussi, ou à 20:00 UTC (le trou reste visible, jamais comblé après coup).
"""
from __future__ import annotations

import time
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from .halal import HalalList, admitted
from .journal import Journal
from .sources import FETCHERS, PublicSources, SourceError

JOURNAL_ID = "F0_DONNEES"
SOURCE, INDICATORS, ERROR, DAY = "SOURCE", "INDICATEURS", "ERREUR", "JOUR"
RECORD_AFTER = pd.Timedelta(minutes=10)
CLOSE_ANYWAY = pd.Timedelta(hours=20)
PERIOD = 14                       # ATR et ADX de Wilder sur 14 jours
HISTORY_DAYS = 90
CORR_DAYS = 30
DEPTH_LIMIT, DEPTH_LIMIT_MAJORS = 1000, 5000      # niveaux lus (poids Binance 50 et 250) ; BTC et ETH : 5000
DEPTH_BAND = 0.01                                # profondeur à ±1 % du milieu
DEPTH_PAUSE = 0.8                                # ~75 demandes par minute, très en dessous des limites publiques


def book_metrics(book: dict, band: float = DEPTH_BAND) -> dict:
    """Écart achat-vente (pb du milieu) et profondeur en USDT à ±`band` ; `truncated` si le carnet lu ne va pas
    jusqu'à la borne (la profondeur est alors un minimum)."""
    bids = [(float(p), float(q)) for p, q in book.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in book.get("asks", [])]
    if not bids or not asks:
        raise ValueError("carnet vide")
    mid = (bids[0][0] + asks[0][0]) / 2
    return {"spread_bps": round((asks[0][0] - bids[0][0]) / mid * 1e4, 3),
            "bid_depth_usdt": round(sum(p * q for p, q in bids if p >= mid * (1 - band)), 2),
            "ask_depth_usdt": round(sum(p * q for p, q in asks if p <= mid * (1 + band)), 2),
            "truncated": bool(bids[-1][0] > mid * (1 - band) or asks[-1][0] < mid * (1 + band))}


def depth_snapshot(settings: Settings, halal: HalalList, *, client: PublicHttpClient | None = None,
                   sleep=time.sleep) -> dict:
    """Instantané du carnet public de chaque paire de la liste halal (une fois par jour, à heure fixe)."""
    own = client is None
    client = client or PublicHttpClient.depth(settings.data.rest_base_url)
    out: dict = {"pairs": {}, "errors": {}, "band_pct": DEPTH_BAND * 100}
    try:
        for symbol in halal.symbols:
            limit = DEPTH_LIMIT_MAJORS if symbol in ("BTCUSDT", "ETHUSDT") else DEPTH_LIMIT
            try:
                out["pairs"][symbol] = book_metrics(client.get_json("/api/v3/depth", {"symbol": symbol, "limit": limit}))
            except (HttpError, ValueError, KeyError, TypeError) as exc:
                out["errors"][symbol] = f"{type(exc).__name__}: {exc}"[:120]
            sleep(DEPTH_PAUSE)
    finally:
        if own:
            client.close()
    return out


def journal(settings: Settings) -> Journal:
    return Journal(settings.root / "forward" / f"{JOURNAL_ID}.jsonl")


def day_of(now: datetime) -> str:
    return (pd.Timestamp(now) - RECORD_AFTER).strftime("%Y-%m-%d")


def daily_bars(hourly: pd.DataFrame, before: pd.Timestamp) -> pd.DataFrame:
    """Journées UTC COMPLÈTES (24 bougies 1 h) terminées avant `before`."""
    frame = hourly[hourly["open_time"] + pd.Timedelta(hours=1) <= before].copy()
    if frame.empty:
        return pd.DataFrame(columns=["day", "open", "high", "low", "close"])
    frame["day"] = frame["open_time"].dt.floor("D")
    grouped = frame.groupby("day").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                       close=("close", "last"), n=("close", "size"))
    return grouped[grouped["n"] == 24].drop(columns="n").reset_index()


def wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Moyenne de Wilder (première valeur = moyenne simple des `period` premières)."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def atr_adx(daily: pd.DataFrame, period: int = PERIOD) -> tuple[float | None, float | None]:
    """(ATR en % de la dernière clôture, ADX) de Wilder ; None si l'historique est trop court."""
    if len(daily) < 2 * period + 1:
        return None, None
    high, low, close = (daily[c].to_numpy(float) for c in ("high", "low", "close"))
    prev = np.r_[np.nan, close[:-1]]
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)[1:]
    up, down = high[1:] - high[:-1], low[:-1] - low[1:]
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    atr = wilder(tr, period)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100 * wilder(plus, period) / atr
        minus_di = 100 * wilder(minus, period) / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = wilder(dx[~np.isnan(dx)], period)
    if np.isnan(atr[-1]) or len(adx) == 0 or np.isnan(adx[-1]):
        return None, None
    return round(float(atr[-1] / close[-1] * 100), 4), round(float(adx[-1]), 2)


def btc_ndx_correlation(btc_daily: pd.DataFrame, ndx: list[list], days: int = CORR_DAYS) -> dict:
    """Corrélation des rendements journaliers (log) de BTC et du Nasdaq 100 sur les `days` derniers jours de
    cotation communs (le Nasdaq ne cote pas le week-end : rendements calculés d'une séance à la suivante)."""
    btc = btc_daily.assign(day=btc_daily["day"].dt.date.astype(str)).set_index("day")["close"]
    nasdaq = pd.Series({d: v for d, v in ndx})
    common = sorted(set(btc.index) & set(nasdaq.index))
    if len(common) < days // 2:
        return {"correlation": None, "sessions": len(common)}
    joint = pd.DataFrame({"btc": btc.loc[common], "ndx": nasdaq.loc[common]}).astype(float)
    returns = np.log(joint).diff().dropna().tail(days)
    return {"correlation": round(float(returns["btc"].corr(returns["ndx"])), 4), "sessions": int(len(returns)),
            "from": returns.index[0], "to": returns.index[-1]}


def recorded(log: Journal, day: str, kind: str) -> set[str]:
    return {e["data"]["name"] for e in log.entries({kind}) if e["data"]["day"] == day}


def indicators(settings: Settings, halal: HalalList, *, now: datetime, nasdaq: list[list] | None) -> dict:
    store = CandleStore(settings.data_dir)
    before = pd.Timestamp(now).floor("D")
    since = before - pd.Timedelta(days=HISTORY_DAYS)
    out: dict = {"pairs": {}, "missing": []}
    btc_daily = None
    for symbol in halal.symbols:
        hourly = store.load_since(symbol, "1h", since)          # tableau vide si la paire n'est pas stockée
        if hourly.empty:
            out["missing"].append(symbol)
            continue
        daily = daily_bars(hourly, before)
        if symbol == "BTCUSDT":
            btc_daily = daily
        atr, adx = atr_adx(daily)
        if atr is None:
            out["missing"].append(symbol)
            continue
        out["pairs"][symbol] = {"atr14_pct": atr, "adx14": adx, "last_day": daily["day"].iloc[-1].date().isoformat()}
    if btc_daily is not None and nasdaq:
        out["btc_ndx_corr30"] = btc_ndx_correlation(btc_daily, nasdaq)
    return out


def record_day(settings: Settings, *, now: datetime, client: PublicSources | None = None,
               halal: HalalList | None = None, fetchers: dict | None = None,
               depth: PublicHttpClient | bool | None = None) -> dict:
    """Relevé du jour : sources manquantes, puis indicateurs ; idempotent, réessaie les sources en échec."""
    log = journal(settings)
    day = day_of(now)
    if day in {e["data"]["day"] for e in log.entries({DAY})}:
        return {"day": day, "already": True}
    fetchers = fetchers if fetchers is not None else FETCHERS
    own = client is None
    client = client or PublicSources()
    done = recorded(log, day, SOURCE)
    counts: dict = {"day": day, "sources": len(done), "errors": 0}
    try:
        for name, fetch in fetchers.items():
            if name in done:
                continue
            try:
                data = fetch(client, now=now)
            except (SourceError, PermissionError, KeyError, TypeError, ValueError, IndexError) as exc:
                log.append(ERROR, {"day": day, "name": name, "error": f"{type(exc).__name__}: {exc}"[:300]}, now=now)
                counts["errors"] += 1
                continue
            log.append(SOURCE, {"day": day, "name": name, "data": data}, now=now)
            counts["sources"] += 1
    finally:
        if own:
            client.close()
    if not any(e["data"]["day"] == day for e in log.entries({"CARNET"})) and depth is not False:
        halal = halal or admitted(settings)
        book_client = depth if isinstance(depth, PublicHttpClient) else None
        log.append("CARNET", {"day": day, **depth_snapshot(settings, halal, client=book_client)}, now=now)
    if not any(e["data"]["day"] == day for e in log.entries({INDICATORS})):
        nasdaq = next((e["data"]["data"]["closes"] for e in log.entries({SOURCE})
                       if e["data"]["day"] == day and e["data"]["name"] == "nasdaq100"), None)
        halal = halal or admitted(settings)
        values = indicators(settings, halal, now=now, nasdaq=nasdaq)
        log.append(INDICATORS, {"day": day, "halal_sha256": halal.sha256, **values}, now=now)
    moment = pd.Timestamp(now)
    if counts["sources"] == len(fetchers) or moment - moment.floor("D") >= CLOSE_ANYWAY:
        log.append(DAY, counts | {"complete": counts["sources"] == len(fetchers)}, now=now)
    return counts


def summary(settings: Settings) -> dict:
    log = journal(settings)
    days = [e["data"] for e in log.entries({DAY})]
    latest = {}
    for entry in log.entries({SOURCE}):
        latest[entry["data"]["name"]] = entry["data"]["day"]
    return {"days": len(days), "first_day": days[0]["day"] if days else None, "last": days[-1] if days else None,
            "latest_by_source": latest, "verified": log.verify()}
