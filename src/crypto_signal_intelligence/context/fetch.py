"""Lecture des sources de contexte (docs/CONTEXTE.md). Chaque fonction lit UNE source par les clients en liste
blanche (`forward/sources.PublicSources`, `data/http.PublicHttpClient`) et rend des enregistrements
{key, date, field, value} ; une réponse inattendue lève `SourceError` (rien n'est deviné ni complété)."""
from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, datetime

import httpx
import pandas as pd

from ..data.http import NotFound, PublicHttpClient
from ..forward.sources import PublicSources, SourceError, _number

COINMETRICS = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
FLOW_METRICS = ("FlowInExNtv", "FlowOutExNtv", "SplyExNtv")
FLOW_ASSETS = ("btc", "eth")                     # seuls actifs gratuits pour ces métriques (catalogue du 2026-10-04)
CATEGORIES = ("artificial-intelligence", "ai-agents", "meme-token", "layer-1", "decentralized-finance-defi",
              "stablecoins")
#: Jeton de la liste halal → protocole DefiLlama, vérifié le 2026-10-04 (nom et identifiant CoinGecko renvoyés par
#: l'API). PORTAL écarté : le protocole « portal » de DefiLlama est le pont Wormhole, pas le jeton PORTAL.
DEFILLAMA = {"ENS": "ens", "GRT": "the-graph", "LPT": "livepeer", "ZRO": "layerzero", "AXL": "axelar",
             "VIRTUAL": "virtuals-protocol", "API3": "api3", "TWT": "trust-wallet", "HYPER": "hyperlane",
             "KAITO": "kaito", "LINK": "chainlink", "WAL": "walrus-protocol", "OPEN": "openledger",
             "FIL": "filecoin", "HOLO": "holoworld-ai"}
FRED_SERIES = ("SP500", "DGS10", "M2SL", "WM2NS")
WIKI_ARTICLES = ("Bitcoin", "Cryptocurrency")
# Règle des robots de Wikimedia (https://w.wiki/4wJS) : nom, version, adresse du projet et bibliothèque.
WIKI_USER_AGENT = (f"crypto-signal-intelligence/0.1 (+https://github.com/chemsou-belkacemi/CryptoSignalIntelligence ; "
                   f"releve quotidien, quelques requetes par jour) python-httpx/{httpx.__version__}")   # ASCII (en-tête)
BASIS_PAIRS = ("BTCUSDT", "ETHUSDT")
BASIS_CONTRACTS = ("CURRENT_QUARTER", "NEXT_QUARTER", "PERPETUAL")
RATIO_PATHS = {"accounts": "/futures/data/globalLongShortAccountRatio",
               "top_positions": "/futures/data/topLongShortPositionRatio"}
ARCHIVE_METRICS = ("sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
                   "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio")
MAX_PAIN_EXPIRIES = 3


def _day(value) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert("UTC")


# --- CoinMetrics (flux vers les plateformes) ------------------------------------------------------------------

def coinmetrics_flows(client: PublicSources, *, start: str = "2010-01-01", pages: int = 20) -> list[dict]:
    params: dict | None = {"assets": ",".join(FLOW_ASSETS), "metrics": ",".join(FLOW_METRICS), "frequency": "1d",
              "page_size": 10000, "start_time": start}
    url, out = COINMETRICS, []
    for _ in range(pages):
        payload = client.get_json(url, params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise SourceError("CoinMetrics : réponse sans « data »")
        for row in data:
            for metric in FLOW_METRICS:
                if row.get(metric) is not None:
                    out.append({"key": row["asset"], "date": row["time"], "field": metric, "value": _number(row[metric])})
        url, params = payload.get("next_page_url"), None
        if not url:
            break
    return out


# --- CoinGecko (indices de marché, instantané) ------------------------------------------------------------------

def coingecko_global(client: PublicSources, *, day: pd.Timestamp) -> list[dict]:
    data = (client.get_json("https://api.coingecko.com/api/v3/global") or {}).get("data") or {}
    total = _number(data["total_market_cap"]["usd"])
    share = {k: _number(v) for k, v in data["market_cap_percentage"].items() if k in ("btc", "eth", "usdt")}
    values = {"total_mcap_usd": total, "dom_btc_pct": share["btc"], "dom_eth_pct": share["eth"],
              "dom_usdt_pct": share.get("usdt"), "total2_usd": total * (1 - share["btc"] / 100),
              "total3_usd": total * (1 - (share["btc"] + share["eth"]) / 100),
              "total_volume_usd": _number(data["total_volume"]["usd"])}
    return [{"key": "global", "date": day, "field": k, "value": v} for k, v in values.items() if v is not None]


def coingecko_categories(client: PublicSources, *, day: pd.Timestamp) -> list[dict]:
    rows = client.get_json("https://api.coingecko.com/api/v3/coins/categories")
    if not isinstance(rows, list):
        raise SourceError("CoinGecko : liste de catégories attendue")
    out = []
    for row in rows:
        if row.get("id") in CATEGORIES:
            for field in ("market_cap", "volume_24h"):
                if row.get(field) is not None:
                    out.append({"key": row["id"], "date": day, "field": field, "value": _number(row[field])})
    return out


# --- Deribit (options : max pain, puts/calls) ------------------------------------------------------------------

def _expiry(code: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.strptime(code, "%d%b%y")).tz_localize("UTC") + pd.Timedelta(hours=8)


def max_pain(strikes: list[float], calls: dict[float, float], puts: dict[float, float]) -> float:
    """Strike qui minimise la valeur totale payée aux acheteurs d'options à l'échéance (open interest × valeur
    intrinsèque) ; en cas d'égalité, le plus bas."""
    def payout(price: float) -> float:
        return (sum(oi * max(0.0, price - k) for k, oi in calls.items())
                + sum(oi * max(0.0, k - price) for k, oi in puts.items()))
    return min(sorted(strikes), key=payout)


def deribit_options(client: PublicSources, currency: str, *, now: datetime) -> list[dict]:
    payload = client.get_json("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                              {"currency": currency, "kind": "option"})
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, list) or not result:
        raise SourceError(f"Deribit {currency} : aucune option")
    moment = pd.Timestamp(now)
    by_expiry: dict[pd.Timestamp, dict] = {}
    oi = {"C": 0.0, "P": 0.0}
    volume = {"C": 0.0, "P": 0.0}
    for row in result:
        parts = str(row["instrument_name"]).split("-")
        if len(parts) != 4:
            continue
        expiry, strike, side = _expiry(parts[1]), float(parts[2]), parts[3]
        interest = float(row.get("open_interest") or 0.0)
        oi[side] += interest
        volume[side] += float(row.get("volume") or 0.0)
        slot = by_expiry.setdefault(expiry, {"C": {}, "P": {}})
        slot[side][strike] = slot[side].get(strike, 0.0) + interest
    day = moment.floor("D")
    out = [{"key": currency, "date": day, "field": "put_call_oi", "value": oi["P"] / oi["C"] if oi["C"] else None},
           {"key": currency, "date": day, "field": "put_call_volume", "value": volume["P"] / volume["C"] if volume["C"] else None},
           {"key": currency, "date": day, "field": "open_interest", "value": oi["C"] + oi["P"]}]
    upcoming = sorted(e for e in by_expiry if e > moment + pd.Timedelta(hours=24))[:MAX_PAIN_EXPIRIES]
    for rank, expiry in enumerate(upcoming, 1):
        slot = by_expiry[expiry]
        strikes = sorted(set(slot["C"]) | set(slot["P"]))
        out += [{"key": currency, "date": day, "field": f"max_pain_{rank}", "value": max_pain(strikes, slot["C"], slot["P"])},
                {"key": currency, "date": day, "field": f"days_to_expiry_{rank}",
                 "value": (expiry - moment) / pd.Timedelta(days=1)},
                {"key": currency, "date": day, "field": f"open_interest_{rank}",
                 "value": sum(slot["C"].values()) + sum(slot["P"].values())}]
    return [r for r in out if r["value"] is not None]


# --- Binance marché à terme (base, ratios), données publiques ------------------------------------------------

def binance_basis(client: PublicHttpClient, pair: str, contract: str, *, limit: int = 500) -> list[dict]:
    rows = client.get_json("/futures/data/basis", {"pair": pair, "contractType": contract, "period": "1d", "limit": limit})
    out = []
    for row in rows or []:
        when = pd.Timestamp(int(row["timestamp"]), unit="ms", tz="UTC")
        for field in ("basis", "basisRate", "annualizedBasisRate", "futuresPrice", "indexPrice"):
            if row.get(field) not in (None, ""):
                out.append({"key": f"{pair}:{contract}", "date": when, "field": field, "value": _number(row[field])})
    return out


def binance_ratios(client: PublicHttpClient, symbol: str, *, limit: int = 30) -> list[dict]:
    out = []
    for name, path in RATIO_PATHS.items():
        for row in client.get_json(path, {"symbol": symbol, "period": "1d", "limit": limit}) or []:
            when = pd.Timestamp(int(row["timestamp"]), unit="ms", tz="UTC")
            out.append({"key": symbol, "date": when, "field": f"{name}_ratio", "value": _number(row["longShortRatio"])})
            out.append({"key": symbol, "date": when, "field": f"{name}_long_share", "value": _number(row["longAccount"])})
    return out


def binance_metrics_archive(client: PublicHttpClient, symbol: str, day: date) -> list[dict]:
    """Archive journalière publique des ratios (pas de 5 min) : dernière ligne du jour. [] si l'archive n'existe pas."""
    path = f"/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day.isoformat()}.zip"
    try:
        content = client.get(path).content
    except NotFound:
        return []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        text = archive.read(archive.namelist()[0]).decode()
    lines = [r for r in csv.DictReader(io.StringIO(text)) if r.get("create_time")]
    if not lines:
        return []
    last = lines[-1]
    out = []
    for field in ARCHIVE_METRICS:
        value = last.get(field)
        if value not in (None, ""):
            out.append({"key": symbol, "date": pd.Timestamp(day, tz="UTC"), "field": field, "value": float(value)})
    return out


def binance_daily(client: PublicHttpClient, symbol: str, *, start: pd.Timestamp) -> list[dict]:
    from ..data.rest import fetch_klines
    raw = fetch_klines(client, symbol, "1d", int(start.timestamp() * 1000))
    out = []
    for row in raw.itertuples(index=False):
        values = list(row)
        when = pd.Timestamp(int(values[0]), unit="ms", tz="UTC")
        out.append({"key": symbol, "date": when, "field": "close", "value": float(values[4])})
        out.append({"key": symbol, "date": when, "field": "quote_volume", "value": float(values[7])})
    return out


# --- Primes Coinbase et coréenne ---------------------------------------------------------------------------

def coinbase_daily(client: PublicSources, base: str, *, start: pd.Timestamp, end: pd.Timestamp,
                   pause: float = 0.2) -> list[dict]:
    """Clôtures journalières de BASE-USD sur Coinbase (300 jours par appel). SourceError « HTTP 404 » : non coté."""
    out, cursor = [], start
    while cursor < end:
        stop = min(cursor + pd.Timedelta(days=299), end)
        rows = client.get_json(f"https://api.exchange.coinbase.com/products/{base}-USD/candles",
                               {"granularity": 86400, "start": cursor.isoformat(), "end": stop.isoformat()})
        if not isinstance(rows, list):
            raise SourceError("Coinbase : liste de bougies attendue")
        for t, _low, _high, _open, close, volume in rows:
            when = pd.Timestamp(int(t), unit="s", tz="UTC")
            out.append({"key": base, "date": when, "field": "close_usd", "value": float(close)})
            out.append({"key": base, "date": when, "field": "volume_base", "value": float(volume)})
        cursor = stop + pd.Timedelta(days=1)
        time.sleep(pause)
    return out


def upbit_daily(client: PublicSources, base: str, *, start: pd.Timestamp, end: pd.Timestamp,
                pause: float = 0.15) -> list[dict]:
    """Clôtures journalières (UTC) de KRW-BASE sur Upbit, 200 par appel, du plus récent au plus ancien."""
    out, cursor = [], end
    while cursor > start:
        rows = client.get_json("https://api.upbit.com/v1/candles/days",
                               {"market": f"KRW-{base}", "count": 200, "to": cursor.strftime("%Y-%m-%d %H:%M:%S")})
        if not isinstance(rows, list):
            raise SourceError("Upbit : liste de bougies attendue")
        if not rows:
            break
        for row in rows:
            when = pd.Timestamp(row["candle_date_time_utc"], tz="UTC")
            if when >= start:
                out.append({"key": base, "date": when, "field": "close_krw", "value": _number(row["trade_price"])})
                out.append({"key": base, "date": when, "field": "value_krw", "value": _number(row["candle_acc_trade_price"])})
        oldest = min(pd.Timestamp(r["candle_date_time_utc"], tz="UTC") for r in rows)
        if len(rows) < 200 or oldest <= start:
            break
        cursor = oldest
        time.sleep(pause)
    return out


def ecb_rates(client: PublicSources, *, start: str) -> list[dict]:
    """Taux de référence BCE (1 EUR = x devise) du won et du dollar, jours ouvrés."""
    text = client.get("https://data-api.ecb.europa.eu/service/data/EXR/D.KRW+USD.EUR.SP00.A",
                      {"startPeriod": start, "format": "csvdata"}).decode()
    return [{"key": row["CURRENCY"], "date": row["TIME_PERIOD"], "field": "per_eur", "value": _number(row["OBS_VALUE"])}
            for row in csv.DictReader(io.StringIO(text)) if row.get("OBS_VALUE")]


# --- DefiLlama -----------------------------------------------------------------------------------------------

def defillama_revenue(client: PublicSources, token: str, slug: str) -> list[dict]:
    out = []
    for field, kind in (("revenue_usd", "dailyRevenue"), ("fees_usd", "dailyFees")):
        payload = client.get_json(f"https://api.llama.fi/summary/fees/{slug}",
                                  {"dataType": kind, "excludeTotalDataChartBreakdown": "true"})
        for stamp, value in payload.get("totalDataChart") or []:
            out.append({"key": token, "date": pd.Timestamp(int(stamp), unit="s", tz="UTC"), "field": field,
                        "value": float(value)})
    return out


def defillama_tvl(client: PublicSources, token: str, slug: str, *, day: pd.Timestamp) -> list[dict]:
    value = client.get_json(f"https://api.llama.fi/tvl/{slug}")
    return [{"key": token, "date": day, "field": "tvl_usd", "value": _number(value)}]


def stablecoin_supply(client: PublicSources) -> list[dict]:
    rows = client.get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    if not isinstance(rows, list):
        raise SourceError("DefiLlama : liste attendue")
    return [{"key": "all", "date": pd.Timestamp(int(r["date"]), unit="s", tz="UTC"), "field": "circulating_usd",
             "value": float((r.get("totalCirculatingUSD") or {}).get("peggedUSD") or "nan")} for r in rows]


# --- Macro, or, attention ----------------------------------------------------------------------------------------

def fred(client: PublicSources, series: str, *, start: str = "1990-01-01") -> list[dict]:
    text = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": series, "cosd": start}).decode()
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise SourceError(f"FRED {series} : CSV illisible")
    return [{"key": series, "date": row[0], "field": "value", "value": float(row[1])}
            for row in reader if len(row) >= 2 and row[1] not in ("", ".")]


def nasdaq_gld(client: PublicSources, *, start: str, end: str) -> list[dict]:
    payload = client.get_json("https://api.nasdaq.com/api/quote/GLD/historical",
                              {"assetclass": "etf", "fromdate": start, "todate": end, "limit": "9999"})
    rows = (((payload or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    return [{"key": "GLD", "date": pd.Timestamp(r["date"]), "field": "close",
             "value": _number(str(r["close"]).replace("$", "").replace(",", ""))} for r in rows]


def wikipedia_views(client: PublicSources, article: str, *, start: str, end: str) -> list[dict]:
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{article}/daily/{start}/{end}")
    items = (client.get_json(url, headers={"User-Agent": WIKI_USER_AGENT}) or {}).get("items")
    if not isinstance(items, list):
        raise SourceError(f"Wikipédia {article} : réponse sans « items »")
    return [{"key": article, "date": pd.Timestamp(r["timestamp"][:8]), "field": "views", "value": float(r["views"])}
            for r in items]
