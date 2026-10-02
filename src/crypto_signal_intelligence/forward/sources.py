"""Sources PUBLIQUES gratuites du relevé quotidien (mission du 2026-10-02, phase 1.3 ; sources validées par le
propriétaire le 2026-10-02). Lecture seule : GET en HTTPS sur une liste blanche d'adresses exactes, sans clé,
sans cookie, sans redirection, réponse bornée. Aucune de ces données n'influence un test en cours.

Chaque fonction `fetch_*` lit une source et rend un dictionnaire de valeurs (et l'extrait brut utile) ; toute
réponse inattendue lève `SourceError` : rien n'est deviné ni complété.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime

import httpx
import pandas as pd

USER_AGENT = "crypto-signal-intelligence/0.1 (releve quotidien public, lecture seule)"
MAX_BYTES = 2_000_000
ALLOWED = {
    "www.deribit.com": ("/api/v2/public/get_volatility_index_data",),
    "api.alternative.me": ("/fng/",),
    "fred.stlouisfed.org": ("/graph/fredgraph.csv",),
    "data-api.ecb.europa.eu": ("/service/data/EXR/",),
    "api.kraken.com": ("/0/public/Ticker",),
    "www.bitstamp.net": ("/api/v2/ticker/",),
    "www.okx.com": ("/api/v5/public/liquidation-orders",),
}
# Indice dollar (ICE) : formule publique, à partir des taux de référence de la BCE (pas la cotation ICE elle-même).
DXY_CONSTANT = 50.14348112
DXY_WEIGHTS = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091, "USDSEK": 0.042, "USDCHF": 0.036}


class SourceError(RuntimeError):
    pass


class PublicSources:
    """Client GET limité à `ALLOWED` (hôte exact et début de chemin)."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False,
                                    transport=transport, headers={"User-Agent": USER_AGENT})

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> bytes:
        parsed = httpx.URL(url)
        prefixes = ALLOWED.get(parsed.host or "")
        if parsed.scheme != "https" or prefixes is None or not parsed.path.startswith(prefixes):
            raise PermissionError(f"adresse hors liste blanche : {url}")
        try:
            if timeout:
                response = self._client.get(url, params=params, timeout=httpx.Timeout(timeout, connect=10.0))
            else:
                response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"réseau : {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SourceError(f"HTTP {response.status_code} sur {parsed.host}")
        if len(response.content) > MAX_BYTES:
            raise SourceError(f"réponse trop volumineuse ({len(response.content)} octets)")
        return response.content

    def get_json(self, url: str, params: dict | None = None):
        try:
            return json.loads(self.get(url, params))
        except ValueError:
            raise SourceError(f"JSON illisible : {url}") from None


def _number(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise SourceError(f"valeur non finie : {value}")
    return number


def fetch_dvol(client: PublicSources, *, now: datetime) -> dict:
    """Indice de volatilité implicite DVOL de Deribit (BTC, ETH), clôtures journalières des 5 derniers jours."""
    end = int(pd.Timestamp(now).timestamp() * 1000)
    out = {}
    for currency in ("BTC", "ETH"):
        payload = client.get_json("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                                  {"currency": currency, "start_timestamp": end - 5 * 86_400_000,
                                   "end_timestamp": end, "resolution": "1D"})
        rows = (payload.get("result") or {}).get("data") or []
        if not rows:
            raise SourceError(f"DVOL {currency} vide")
        out[currency] = {"days": [[pd.Timestamp(int(r[0]), unit="ms", tz="UTC").date().isoformat(), _number(r[4])]
                                  for r in rows], "last": _number(rows[-1][4])}
    return out


def fetch_fear_greed(client: PublicSources, *, now: datetime) -> dict:
    payload = client.get_json("https://api.alternative.me/fng/", {"limit": 2})
    rows = payload.get("data") or []
    if not rows:
        raise SourceError("Fear & Greed vide")
    return {"value": int(rows[0]["value"]), "label": str(rows[0]["value_classification"]),
            "day": pd.Timestamp(int(rows[0]["timestamp"]), unit="s", tz="UTC").date().isoformat()}


def fetch_nasdaq100(client: PublicSources, *, now: datetime) -> dict:
    """Clôtures du Nasdaq 100 (FRED, série NASDAQ100) sur les 60 derniers jours : la corrélation 30 jours avec BTC
    est calculée à partir de cet extrait et des bougies Binance."""
    since = (pd.Timestamp(now) - pd.Timedelta(days=60)).date().isoformat()
    text = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "NASDAQ100", "cosd": since},
                      timeout=90.0).decode()                        # FRED répond parfois lentement
    rows = [r for r in csv.reader(io.StringIO(text))][1:]
    closes = [[d, _number(v)] for d, v in rows if v not in ("", ".")]
    if not closes:
        raise SourceError("Nasdaq 100 vide")
    return {"closes": closes, "last": closes[-1]}


def dxy_from_ecb(rates: dict[str, float]) -> float:
    """Indice dollar recalculé (formule ICE) à partir des taux BCE : 1 EUR = x devise."""
    usd = rates["USD"]
    pairs = {"EURUSD": usd, "USDJPY": rates["JPY"] / usd, "GBPUSD": usd / rates["GBP"], "USDCAD": rates["CAD"] / usd,
             "USDSEK": rates["SEK"] / usd, "USDCHF": rates["CHF"] / usd}
    return round(DXY_CONSTANT * math.prod(pairs[k] ** w for k, w in DXY_WEIGHTS.items()), 4)


def fetch_dxy(client: PublicSources, *, now: datetime) -> dict:
    text = client.get("https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY+GBP+CAD+SEK+CHF.EUR.SP00.A",
                      {"lastNObservations": 5, "format": "csvdata"}).decode()
    by_day: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        by_day.setdefault(row["TIME_PERIOD"], {})[row["CURRENCY"]] = _number(row["OBS_VALUE"])
    days = [[day, dxy_from_ecb(rates)] for day, rates in sorted(by_day.items()) if len(rates) == 6]
    if not days:
        raise SourceError("taux BCE incomplets")
    return {"days": days, "last": days[-1], "note": "recalculé aux taux de référence BCE (≈ 14:15 GMT), pas la cotation ICE"}


def fetch_stablecoins(client: PublicSources, *, now: datetime) -> dict:
    """Écart à la parité dollar de USDT et USDC : milieu achat-vente sur Kraken, contrôle sur Bitstamp."""
    kraken = client.get_json("https://api.kraken.com/0/public/Ticker", {"pair": "USDTZUSD,USDCUSD"})
    if kraken.get("error"):
        raise SourceError(f"Kraken : {kraken['error']}")
    out = {}
    for coin, key in (("USDT", "USDTZUSD"), ("USDC", "USDCUSD")):
        row = kraken["result"][key]
        mid = (_number(row["b"][0]) + _number(row["a"][0])) / 2
        stamp = client.get_json(f"https://www.bitstamp.net/api/v2/ticker/{coin.lower()}usd/")
        check = (_number(stamp["bid"]) + _number(stamp["ask"])) / 2
        out[coin] = {"kraken_mid": round(mid, 6), "bitstamp_mid": round(check, 6),
                     "deviation_pct": round((mid - 1) * 100, 4)}
    return out


def fetch_liquidations(client: PublicSources, *, now: datetime, pages: int = 10) -> dict:
    """Liquidations des 24 dernières heures sur les perpétuels BTC et ETH d'OKX (PARTIEL : une seule bourse)."""
    end = int(pd.Timestamp(now).timestamp() * 1000)
    out: dict = {"source": "OKX seule (partiel)"}
    for underlying in ("BTC-USDT", "ETH-USDT"):
        totals = {"long": 0.0, "short": 0.0}
        count, after, complete = 0, "", False
        for _ in range(pages):
            params = {"instType": "SWAP", "uly": underlying, "state": "filled", "limit": 100}
            if after:
                params["after"] = after
            payload = client.get_json("https://www.okx.com/api/v5/public/liquidation-orders", params)
            details = (payload.get("data") or [{}])[0].get("details") or []
            if not details:
                complete = True
                break
            for item in details:
                if int(item["ts"]) < end - 86_400_000:
                    complete = True
                    continue
                side = "long" if item.get("posSide") == "long" else "short"
                totals[side] += _number(item["sz"]) * _number(item["bkPx"])
                count += 1
            if complete:
                break
            after = str(min(int(item["ts"]) for item in details))
        out[underlying] = {"orders": count, "long_contracts_value": round(totals["long"], 2),
                           "short_contracts_value": round(totals["short"], 2), "complete_24h": complete,
                           "unit": "taille en contrats × prix de faillite (valeur indicative)"}
    return out


FETCHERS = {"dvol": fetch_dvol, "fear_greed": fetch_fear_greed, "nasdaq100": fetch_nasdaq100, "dxy": fetch_dxy,
            "stablecoins": fetch_stablecoins, "liquidations_okx": fetch_liquidations}
