"""Relevé F0_ECARTS : écarts de prix entre bourses et prime coréenne (point 9 du plan de travail ; docs/SPREADS.md).

Toutes les 10 minutes (au passage de détection des tests en direct), meilleurs prix d'achat et de vente de BTC, ETH,
SOL et XRP sur Binance (carnet public, 5 niveaux), Coinbase, Kraken, Bitstamp et OKX, plus le prix coréen d'Upbit
(KRW-actif rapporté à KRW-USDT). Tout passe par les clients en LISTE BLANCHE (`data/http.py`, `forward/sources.py`) ;
`ccxt` n'est pas utilisé en service, ses appels réseau contournant la liste blanche. Aucune clé, aucun ordre.

Écart brut d'un aller-retour : acheter au meilleur prix de vente d'une bourse, vendre au meilleur prix d'achat de
l'autre (prix ramenés en USDT par le cours USDT/USD de Kraken) ; écart net : moins les frais « taker » publics d'un
particulier sur les deux bourses. Les retraits, délais de transfert et fonds immobilisés ne sont PAS comptés : un
écart net positif ici reste un maximum théorique.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..data.http import PublicHttpClient
from .journal import Journal, utc_iso
from .sources import PublicSources, SourceError

log = logging.getLogger("csi.spreads")
JOURNAL_ID = "F0_ECARTS"
SNAPSHOT, EVERY = "RELEVE", pd.Timedelta(minutes=10)
ASSETS = ("BTC", "ETH", "SOL", "XRP")
#: Frais taker publics d'un particulier, au palier le plus bas (2026-10) ; Binance avec la remise BNB.
TAKER_FEE = {"binance": 0.00075, "coinbase": 0.006, "kraken": 0.004, "bitstamp": 0.004, "okx": 0.001}
KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}


def journal(settings: Settings) -> Journal:
    return Journal(settings.root / "forward" / f"{JOURNAL_ID}.jsonl")


def _best(levels: list) -> float | None:
    return float(levels[0][0]) if levels else None


def binance_quotes(client: PublicHttpClient) -> dict[str, dict]:
    out = {}
    for asset in ASSETS:
        book = client.get_json("/api/v3/depth", {"symbol": f"{asset}USDT", "limit": 5})
        out[asset] = {"bid": _best(book.get("bids", [])), "ask": _best(book.get("asks", []))}
    return out


def kraken_quotes(source: PublicSources) -> tuple[dict[str, dict], float | None]:
    names = ",".join([*KRAKEN_PAIRS.values(), "USDTZUSD"])
    reply = source.get_json("https://api.kraken.com/0/public/Ticker", {"pair": names})
    if reply.get("error"):
        raise SourceError(f"Kraken : {reply['error']}")
    result = reply.get("result", {})

    def find(code: str) -> dict | None:
        return next((v for k, v in result.items() if k == code or k.replace("X", "").replace("Z", "") == code.replace("X", "").replace("Z", "")), None)

    out = {}
    for asset, pair in KRAKEN_PAIRS.items():
        row = find(pair)
        out[asset] = {"bid": float(row["b"][0]), "ask": float(row["a"][0])} if row else {"bid": None, "ask": None}
    usdt = find("USDTZUSD")
    usdt_usd = (float(usdt["b"][0]) + float(usdt["a"][0])) / 2 if usdt else None
    return out, usdt_usd


def bitstamp_quotes(source: PublicSources) -> dict[str, dict]:
    out = {}
    for asset in ASSETS:
        row = source.get_json(f"https://www.bitstamp.net/api/v2/ticker/{asset.lower()}usd/")
        out[asset] = {"bid": float(row["bid"]), "ask": float(row["ask"])}
    return out


def coinbase_quotes(source: PublicSources) -> dict[str, dict]:
    out = {}
    for asset in ASSETS:
        row = source.get_json(f"https://api.exchange.coinbase.com/products/{asset}-USD/ticker")
        out[asset] = {"bid": float(row["bid"]), "ask": float(row["ask"])}
    return out


def okx_quotes(source: PublicSources) -> dict[str, dict]:
    out = {}
    for asset in ASSETS:
        data = (source.get_json("https://www.okx.com/api/v5/market/ticker", {"instId": f"{asset}-USDT"}).get("data") or [{}])[0]
        out[asset] = {"bid": float(data["bidPx"]), "ask": float(data["askPx"])} if data else {"bid": None, "ask": None}
    return out


def upbit_quotes(source: PublicSources) -> dict[str, dict]:
    """Prix coréens en KRW (meilleurs prix du carnet) ; KRW-USDT sert à les ramener en USDT."""
    markets = ",".join([*(f"KRW-{a}" for a in ASSETS), "KRW-USDT"])
    rows = source.get_json("https://api.upbit.com/v1/orderbook", {"markets": markets})
    out = {}
    for row in rows:
        unit = (row.get("orderbook_units") or [{}])[0]
        out[row["market"].split("-")[1]] = {"bid": float(unit.get("bid_price")), "ask": float(unit.get("ask_price"))}
    return out


def gaps(venues: dict[str, dict[str, dict]], *, asset: str) -> dict[str, dict]:
    """Pour chaque bourse face à Binance : écart brut et net (frais taker des deux côtés) des deux sens, en pb."""
    base = venues["binance"][asset]
    out = {}
    for venue, quotes in venues.items():
        if venue == "binance":
            continue
        other = quotes.get(asset) or {}
        if not all(v for v in (base.get("bid"), base.get("ask"), other.get("bid"), other.get("ask"))):
            continue
        fees = TAKER_FEE["binance"] + TAKER_FEE[venue]
        buy_binance = other["bid"] / base["ask"] - 1          # acheter sur Binance, vendre ailleurs
        sell_binance = base["bid"] / other["ask"] - 1         # acheter ailleurs, vendre sur Binance
        best = max(buy_binance, sell_binance)
        out[venue] = {"gross_bps": round(best * 1e4, 2), "net_bps": round((best - fees) * 1e4, 2),
                      "direction": "achat Binance" if buy_binance >= sell_binance else "vente Binance"}
    return out


def snapshot(settings: Settings, *, now: datetime, source: PublicSources | None = None,
             binance: PublicHttpClient | None = None) -> dict:
    client = source or PublicSources()
    book = binance or PublicHttpClient.depth(settings.data.rest_base_url)
    errors: dict[str, str] = {}
    venues: dict[str, dict[str, dict]] = {}
    usdt_usd = None
    fetchers = {"binance": lambda: binance_quotes(book), "coinbase": lambda: coinbase_quotes(client),
                "bitstamp": lambda: bitstamp_quotes(client), "okx": lambda: okx_quotes(client)}
    try:
        venues["kraken"], usdt_usd = kraken_quotes(client)
    except Exception as exc:  # noqa: BLE001 - une source en panne n'arrête pas les autres
        errors["kraken"] = f"{type(exc).__name__}: {exc}"[:200]
    for name, fetch in fetchers.items():
        try:
            venues[name] = fetch()
        except Exception as exc:  # noqa: BLE001
            errors[name] = f"{type(exc).__name__}: {exc}"[:200]
    korea: dict[str, float] = {}
    try:
        upbit = upbit_quotes(client)
        usdt_krw = (upbit["USDT"]["bid"] + upbit["USDT"]["ask"]) / 2
        for asset in ASSETS:
            if asset in upbit and "binance" in venues and venues["binance"][asset].get("bid"):
                mid_kr = (upbit[asset]["bid"] + upbit[asset]["ask"]) / 2 / usdt_krw
                mid_bn = (venues["binance"][asset]["bid"] + venues["binance"][asset]["ask"]) / 2
                korea[asset] = round((mid_kr / mid_bn - 1) * 1e4, 2)
    except Exception as exc:  # noqa: BLE001
        errors["upbit"] = f"{type(exc).__name__}: {exc}"[:200]
    if usdt_usd:                                               # prix en USD ramenés en USDT
        for venue in ("coinbase", "kraken", "bitstamp"):
            for quotes in venues.get(venue, {}).values():
                for side in ("bid", "ask"):
                    if quotes.get(side):
                        quotes[side] = quotes[side] / usdt_usd
    result = {"time": utc_iso(pd.Timestamp(now)), "usdt_usd": usdt_usd, "venues": venues, "korea_premium_bps": korea,
              "gaps": {a: gaps(venues, asset=a) for a in ASSETS if "binance" in venues}, "errors": errors}
    if source is None:
        client.close()
    return result


def maybe_record(settings: Settings, *, now: datetime) -> dict | None:
    """Un relevé au plus toutes les 10 minutes (journal en ajout seul)."""
    log_ = journal(settings)
    moment = pd.Timestamp(now)
    last = None
    for entry in log_.entries({SNAPSHOT}):
        last = entry["data"]["time"]
    if last is not None and moment - pd.Timestamp(last) < EVERY:
        return None
    data = snapshot(settings, now=now)
    log_.append(SNAPSHOT, data, now=moment)
    return {"errors": len(data["errors"])}


def summary(settings: Settings, path: Path | None = None) -> dict:
    """Par bourse et actif : relevés, écart brut moyen et maximal, part des relevés à écart NET positif ; prime coréenne."""
    log_ = Journal(path) if path else journal(settings)
    rows, korea = [], []
    for entry in log_.entries({SNAPSHOT}):
        data = entry["data"]
        for asset, by_venue in data.get("gaps", {}).items():
            for venue, g in by_venue.items():
                rows.append({"asset": asset, "venue": venue, **g})
        for asset, value in data.get("korea_premium_bps", {}).items():
            korea.append({"asset": asset, "premium_bps": value})
    out: dict = {"snapshots": sum(1 for _ in log_.entries({SNAPSHOT})), "pairs": {}, "korea": {}}
    if rows:
        frame = pd.DataFrame(rows)
        for (venue, asset), part in frame.groupby(["venue", "asset"]):
            out["pairs"][f"{venue}/{asset}"] = {"n": int(len(part)), "gross_mean_bps": round(float(part["gross_bps"].mean()), 2),
                                                "gross_max_bps": round(float(part["gross_bps"].max()), 2),
                                                "net_positive_share": round(float((part["net_bps"] > 0).mean()), 4)}
    if korea:
        frame = pd.DataFrame(korea)
        out["korea"] = {a: {"mean_bps": round(float(g["premium_bps"].mean()), 1), "min_bps": round(float(g["premium_bps"].min()), 1),
                            "max_bps": round(float(g["premium_bps"].max()), 1)} for a, g in frame.groupby("asset")}
    return out
