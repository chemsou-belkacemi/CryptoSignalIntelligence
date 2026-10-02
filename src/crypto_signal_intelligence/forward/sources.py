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
    "api.nasdaq.com": ("/api/quote/NDX/historical",),
    # Émissions de stablecoins (F3_STABLECOINS) : événements publics des contrats, sans clé.
    "api.trongrid.io": ("/v1/contracts/",),
    "ethereum-rpc.publicnode.com": ("/",),
}
TRON_API = "https://api.trongrid.io"
ETH_RPC = "https://ethereum-rpc.publicnode.com"
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

    def post_json(self, url: str, payload: dict, timeout: float | None = None):
        """POST JSON (appels JSON-RPC), même liste blanche et mêmes limites que `get`."""
        parsed = httpx.URL(url)
        prefixes = ALLOWED.get(parsed.host or "")
        if parsed.scheme != "https" or prefixes is None or not parsed.path.startswith(prefixes):
            raise PermissionError(f"adresse hors liste blanche : {url}")
        try:
            response = self._client.post(url, json=payload,
                                         timeout=httpx.Timeout(timeout, connect=10.0) if timeout else httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError as exc:
            raise SourceError(f"réseau : {type(exc).__name__}") from None
        if response.status_code != 200:
            raise SourceError(f"HTTP {response.status_code} sur {parsed.host}")
        if len(response.content) > MAX_BYTES:
            raise SourceError(f"réponse trop volumineuse ({len(response.content)} octets)")
        try:
            return json.loads(response.content)
        except ValueError:
            raise SourceError(f"JSON illisible : {url}") from None


# --- Chaînes publiques (F3_STABLECOINS) ------------------------------------------------------------------

def eth_rpc(client: PublicSources, method: str, params: list):
    """Appel JSON-RPC au nœud Ethereum public ; une erreur du nœud devient une SourceError."""
    reply = client.post_json(ETH_RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(reply, dict) or "result" not in reply:
        message = (reply.get("error") or {}).get("message", "réponse sans résultat") if isinstance(reply, dict) else "?"
        raise SourceError(f"RPC {method} : {str(message)[:120]}")
    return reply["result"]


def eth_block_number(client: PublicSources) -> int:
    return int(eth_rpc(client, "eth_blockNumber", []), 16)


def eth_block_timestamp(client: PublicSources, number: int) -> pd.Timestamp:
    block = eth_rpc(client, "eth_getBlockByNumber", [hex(number), False])
    if not isinstance(block, dict) or "timestamp" not in block:
        raise SourceError(f"bloc {number} sans horodatage")
    return pd.Timestamp(int(block["timestamp"], 16), unit="s", tz="UTC")


def eth_logs(client: PublicSources, *, address: str, topic: str, from_block: int, to_block: int) -> list[dict]:
    """Journaux d'événements d'un contrat pour une signature, sur une plage de blocs récente."""
    logs = eth_rpc(client, "eth_getLogs", [{"address": address, "topics": [topic],
                                            "fromBlock": hex(max(from_block, 0)), "toBlock": hex(to_block)}])
    if not isinstance(logs, list):
        raise SourceError("eth_getLogs : liste attendue")
    return logs


def tron_events(client: PublicSources, *, contract: str, event_name: str, since_ms: int, limit: int = 200) -> list[dict]:
    """Événements d'un contrat TRC-20 depuis `since_ms` (ordre chronologique), API publique de TronGrid."""
    reply = client.get_json(f"{TRON_API}/v1/contracts/{contract}/events",
                            {"event_name": event_name, "min_block_timestamp": int(since_ms), "limit": int(limit),
                             "order_by": "block_timestamp,asc"})
    data = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(data, list):
        raise SourceError("TronGrid : réponse sans « data »")
    return data


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


def _nasdaq_from_fred(client: PublicSources, since: str) -> list[list]:
    text = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "NASDAQ100", "cosd": since},
                      timeout=45.0).decode()
    rows = [r for r in csv.reader(io.StringIO(text))][1:]
    return [[d, _number(v)] for d, v in rows if v not in ("", ".")]


def _nasdaq_from_exchange(client: PublicSources, since: str) -> list[list]:
    payload = client.get_json("https://api.nasdaq.com/api/quote/NDX/historical",
                              {"assetclass": "index", "fromdate": since, "limit": "100"})
    rows = (((payload or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    closes = [[pd.Timestamp(r["date"]).date().isoformat(), _number(str(r["close"]).replace(",", ""))] for r in rows]
    return sorted(closes)


def fetch_nasdaq100(client: PublicSources, *, now: datetime) -> dict:
    """Clôtures du Nasdaq 100 sur les 60 derniers jours : FRED (série NASDAQ100), sinon l'API publique de Nasdaq
    (même indice, source notée). La corrélation 30 jours avec BTC est calculée à partir de cet extrait."""
    since = (pd.Timestamp(now) - pd.Timedelta(days=60)).date().isoformat()
    errors = []
    for name, read in (("FRED", _nasdaq_from_fred), ("Nasdaq", _nasdaq_from_exchange)):
        try:
            closes = read(client, since)
        except (SourceError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{name} : {exc}")
            continue
        if closes:
            return {"closes": closes, "last": closes[-1], "source": name}
        errors.append(f"{name} : vide")
    raise SourceError("Nasdaq 100 indisponible (" + " ; ".join(errors) + ")")


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
