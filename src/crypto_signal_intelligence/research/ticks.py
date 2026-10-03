"""Coûts d'exécution mesurés sur les transactions (point 3 du plan de travail, docs/TICKS.md, déclaré le 2026-10-03).

Les archives publiques de Binance Spot donnent chaque transaction agrégée (aggTrades : prix, quantité, heure, côté
de l'initiateur) mais PAS le carnet d'ordres complet ; le moteur `hftbacktest`, qui simule la file d'attente d'un
ordre limite, exige un carnet niveau 2 et ne peut donc pas tourner sur ces données (déclaré). Ce module mesure ce
que les transactions permettent de mesurer honnêtement, à chaque clôture 15 min d'un échantillon de journées :
1. l'écart acheteur / vendeur : dernier prix payé par un acheteur au marché et dernier prix reçu par un vendeur au
   marché dans les 60 s qui précèdent la clôture ;
2. le prix d'un PETIT achat au marché après un délai (1, 5, 40 s) : première transaction initiée par un acheteur
   après la clôture + délai, contre le prix de clôture de la bougie (ce que nos simulations supposent, plus les
   coûts du modèle) ;
3. le remplissage d'un ordre limite d'achat posé à la clôture à prix − x : selon la règle des bougies (le plus bas
   touche le prix), selon les transactions (une transaction initiée par un vendeur s'exécute STRICTEMENT sous le
   prix : remplissage certain), et la zone incertaine (le prix est touché sans être traversé : dépend de la file).
Aucune hypothèse de marché, aucun essai ; c'est une mesure des coûts.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.archives import parse_checksum, verify
from ..data.http import PublicHttpClient

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ALGOUSDT", "ATOMUSDT")
DAYS = tuple(date(y, m, 15) for y, m in ((2024, 7), (2024, 8), (2024, 9), (2024, 10), (2024, 11), (2024, 12),
                                          (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2025, 5), (2025, 6)))
DELAYS_S = (1, 5, 40)
LIMIT_OFFSETS = (0.0, 0.001, 0.003)
WINDOWS_MIN = (15, 60)
SPREAD_LOOKBACK_S = 60
COLUMNS = ("agg_id", "price", "qty", "first_id", "last_id", "time", "buyer_is_maker", "best_match")


@dataclass
class Result:
    built_at: str
    pairs: list[str]
    days: list[str]
    spread_bps: dict = field(default_factory=dict)
    market_buy_bps: dict = field(default_factory=dict)
    limit_fill: dict = field(default_factory=dict)
    trades_read: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# --- Données -------------------------------------------------------------------------------------------------------

def archive_path(symbol: str, day: date) -> str:
    return f"/data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day.isoformat()}.zip"


def parse_aggtrades(content: bytes) -> pd.DataFrame:
    """CSV des archives (sans en-tête, ou avec selon l'époque) ; heure en ms ou en µs (Spot depuis 2025) → UTC."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read(archive.namelist()[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, names=list(COLUMNS))
    if not str(frame.iloc[0]["price"]).replace(".", "", 1).isdigit():
        frame = frame.iloc[1:]
    stamp = frame["time"].astype("int64")
    unit = "us" if stamp.iloc[0] > 10**14 else "ms"
    when = pd.to_datetime(stamp, unit=unit, utc=True)
    out = pd.DataFrame({"time": when, "ns": when.dt.as_unit("ns").astype("int64").to_numpy(), "price": frame["price"].astype(float),
                        "qty": frame["qty"].astype(float),
                        "buyer_is_maker": frame["buyer_is_maker"].astype(str).str.lower().isin(("true", "1"))})
    return out.sort_values("time", kind="stable").reset_index(drop=True)


def load_day(client: PublicHttpClient, symbol: str, day: date, raw_dir: Path) -> pd.DataFrame:
    """Archive du jour, vérifiée contre son empreinte publiée, gardée en cache disque."""
    path = archive_path(symbol, day)
    expected = parse_checksum(client.get(path + ".CHECKSUM").text, path.rsplit("/", 1)[1])
    target = raw_dir / symbol / path.rsplit("/", 1)[1]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        content = target.read_bytes()
    else:
        content = client.get_bytes(path)
        verify(content, expected, target.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_name(target.name + ".tmp").write_bytes(content)
        target.with_name(target.name + ".tmp").replace(target)
    return parse_aggtrades(content)


# --- Mesures ------------------------------------------------------------------------------------------------------

def closes(trades: pd.DataFrame, day: date) -> pd.DataFrame:
    """Prix de clôture de chaque bougie 15 min (dernière transaction avant la clôture) et plus bas de la fenêtre
    qui suit, pour les fenêtres de remplissage."""
    start = pd.Timestamp(day, tz="UTC")
    marks = pd.date_range(start + pd.Timedelta(minutes=15), start + pd.Timedelta(hours=23, minutes=45), freq="15min")
    times = trades["ns"].to_numpy()
    idx = np.searchsorted(times, marks.as_unit("ns").asi8, side="left") - 1
    valid = idx >= 0
    return pd.DataFrame({"mark": marks[valid], "close": trades["price"].to_numpy()[idx[valid]], "last_index": idx[valid]})


def spread_at(trades: pd.DataFrame, mark: pd.Timestamp, last_index: int) -> float | None:
    """(dernier prix acheteur-initiateur − dernier prix vendeur-initiateur) / milieu, dans les 60 s avant `mark`."""
    window = trades.iloc[max(0, last_index - 5000): last_index + 1]
    window = window[window["time"] >= mark - pd.Timedelta(seconds=SPREAD_LOOKBACK_S)]
    buys, sells = window[~window["buyer_is_maker"]], window[window["buyer_is_maker"]]
    if buys.empty or sells.empty:
        return None
    ask, bid = float(buys["price"].iloc[-1]), float(sells["price"].iloc[-1])
    return (ask - bid) / ((ask + bid) / 2) * 1e4


def market_buy_after(trades: pd.DataFrame, mark: pd.Timestamp, close: float, delay_s: int) -> float | None:
    """Écart (pb) entre la première transaction initiée par un acheteur après `mark` + délai et la clôture."""
    times = trades["ns"].to_numpy()
    start = int(np.searchsorted(times, (mark + pd.Timedelta(seconds=delay_s)).value, side="left"))
    after = trades.iloc[start: start + 2000]
    buys = after[~after["buyer_is_maker"]]
    if buys.empty or buys["time"].iloc[0] - mark > pd.Timedelta(minutes=2):
        return None
    return (float(buys["price"].iloc[0]) / close - 1) * 1e4


def limit_fill(trades: pd.DataFrame, mark: pd.Timestamp, close: float, offset: float, window_min: int) -> str:
    """Ordre limite d'achat à close × (1 − offset), posé à `mark`, vivant `window_min` minutes :
    « non_touche » (prix jamais atteint), « traverse » (une vente au marché sous le prix : rempli), « touche_seulement »
    (atteint sans être traversé : dépend de la file d'attente)."""
    limit = close * (1 - offset)
    times = trades["ns"].to_numpy()
    a = int(np.searchsorted(times, mark.value, side="left"))
    b = int(np.searchsorted(times, (mark + pd.Timedelta(minutes=window_min)).value, side="left"))
    window = trades.iloc[a:b]
    if window.empty or float(window["price"].min()) > limit:
        return "non_touche"
    sells = window[window["buyer_is_maker"]]
    if not sells.empty and float(sells["price"].min()) < limit:
        return "traverse"
    return "touche_seulement"


def measure_day(trades: pd.DataFrame, day: date) -> dict:
    marks = closes(trades, day)
    spreads: list[float] = []
    buys: dict[int, list[float]] = {d: [] for d in DELAYS_S}
    fills: dict[str, list[str]] = {f"{o}_{w}": [] for o in LIMIT_OFFSETS for w in WINDOWS_MIN}
    for row in marks.itertuples():
        s = spread_at(trades, row.mark, int(row.last_index))
        if s is not None:
            spreads.append(s)
        for d in DELAYS_S:
            value = market_buy_after(trades, row.mark, float(row.close), d)
            if value is not None:
                buys[d].append(value)
        for o in LIMIT_OFFSETS:
            for w in WINDOWS_MIN:
                fills[f"{o}_{w}"].append(limit_fill(trades, row.mark, float(row.close), o, w))
    return {"spreads": spreads, "buys": buys, "fills": fills, "trades": int(len(trades))}


def summarize(per_pair: dict[str, list[dict]]) -> Result:
    result = Result(datetime.now().isoformat(), sorted(per_pair), [d.isoformat() for d in DAYS])
    for symbol, days in per_pair.items():
        spreads = np.concatenate([np.asarray(d["spreads"], float) for d in days]) if days else np.array([])
        result.spread_bps[symbol] = {"median": round(float(np.median(spreads)), 3) if len(spreads) else None,
                                     "p90": round(float(np.percentile(spreads, 90)), 3) if len(spreads) else None, "n": int(len(spreads))}
        result.market_buy_bps[symbol] = {}
        for delay in DELAYS_S:
            values = np.concatenate([np.asarray(d["buys"][delay], float) for d in days]) if days else np.array([])
            result.market_buy_bps[symbol][f"{delay}s"] = {
                "mean": round(float(values.mean()), 3) if len(values) else None,
                "median": round(float(np.median(values)), 3) if len(values) else None,
                "p90_abs": round(float(np.percentile(np.abs(values), 90)), 3) if len(values) else None, "n": int(len(values))}
        result.limit_fill[symbol] = {}
        for key in days[0]["fills"] if days else []:
            outcomes = pd.Series([o for d in days for o in d["fills"][key]]).value_counts(normalize=True)
            candle = float(outcomes.get("traverse", 0) + outcomes.get("touche_seulement", 0))
            result.limit_fill[symbol][key] = {"bougie_dit_rempli": round(candle, 4),
                                              "traverse_certain": round(float(outcomes.get("traverse", 0)), 4),
                                              "touche_seulement": round(float(outcomes.get("touche_seulement", 0)), 4)}
        result.trades_read[symbol] = int(sum(d["trades"] for d in days))
    return result


def run(settings: Settings, *, progress: Callable[[str], None] | None = None, client: PublicHttpClient | None = None,
        pairs: tuple[str, ...] = PAIRS, days: tuple[date, ...] = DAYS) -> Result:
    say = progress or (lambda _text: None)
    archive = client or PublicHttpClient.archives(settings.data.archive_base_url)
    raw_dir = settings.data_dir / "raw" / "aggTrades"
    per_pair: dict[str, list[dict]] = {}
    errors = []
    for symbol in pairs:
        per_pair[symbol] = []
        for day in days:
            say(f"{symbol} {day}")
            try:
                per_pair[symbol].append(measure_day(load_day(archive, symbol, day, raw_dir), day))
            except Exception as exc:  # noqa: BLE001 - une journée illisible n'arrête pas les autres
                errors.append(f"{symbol} {day} : {type(exc).__name__}: {exc}")
    result = summarize(per_pair)
    result.errors = errors
    out = settings.reports_dir / f"TICKS-{datetime.now():%Y%m%dT%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(asdict(result) | {"doc": "docs/TICKS.md"}, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
