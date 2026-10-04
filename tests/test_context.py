"""Données de contexte (docs/CONTEXTE.md) : lecture des sources (clients FACTICES, aucun réseau), magasin
(relevé jamais réécrit, historique remplacé), max pain, primes, relevé une fois par jour avec reprise des seules
séries en échec, liste blanche."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.context import collect, fetch, store, views
from crypto_signal_intelligence.context.collect import (
    record_day as real_record_day,  # avant le bouchon de conftest
)
from crypto_signal_intelligence.data.http import NotFound, PublicHttpClient
from crypto_signal_intelligence.forward.sources import PublicSources, SourceError

NOW = datetime(2026, 10, 4, 4, 0, tzinfo=UTC)
DAY_MS = 86_400_000


class FakeWeb:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[str] = []

    def _check(self, url: str):
        self.calls.append(url)
        for word in self.fail:
            if word in url:
                raise SourceError(f"HTTP 503 sur {word}")

    def get(self, url, params=None, timeout=None, headers=None) -> bytes:
        self._check(url)
        if "ecb" in url:
            return (b"KEY,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\n"
                    b"x,D,KRW,2026-10-02,1500\nx,D,USD,2026-10-02,1.25\nx,D,KRW,2026-10-03,1500\nx,D,USD,2026-10-03,1.25\n")
        if "fred" in url:
            return f"observation_date,{params['id']}\n2026-09-01,100.5\n2026-10-01,.\n".encode()
        if "treasury" in url:
            return b'Date,"3 Mo","10 Yr","30 Yr"\n10/02/2026,4.10,5.28,5.60\n10/01/2026,4.11,,5.61\n'
        if "federalreserve" in url:
            head = '"Series Description","x"\n"Unit:","Currency"\n'
            if params["series"] == fetch.FED_H6["monthly"][0]:
                return (head + '"Time Period","M2_N.M","M2.M"\n2026-07,23157.4,23217.9\n2026-08,23300.0,ND\n').encode()
            return (head + '"Time Period","M2_N.WM","M2.WM"\n2026-08-31,23305.5,\n').encode()
        raise AssertionError(url)

    def get_json(self, url, params=None, headers=None):
        self._check(url)
        if "coinmetrics" in url:
            return {"data": [{"asset": "btc", "time": "2026-10-02T00:00:00.000000000Z", "FlowInExNtv": "10",
                              "FlowOutExNtv": "12", "SplyExNtv": "2678000"}]}
        if url.endswith("/global"):
            return {"data": {"total_market_cap": {"usd": 3.0e12}, "total_volume": {"usd": 1e11},
                             "market_cap_percentage": {"btc": 60.0, "eth": 10.0, "usdt": 5.0}}}
        if "categories" in url:
            return [{"id": "meme-token", "market_cap": 5e10, "volume_24h": 3e9}, {"id": "autre", "market_cap": 1}]
        if "deribit" in url:
            return {"result": [{"instrument_name": f"{params['currency']}-30OCT26-90000-C", "open_interest": 10, "volume": 4},
                               {"instrument_name": f"{params['currency']}-30OCT26-80000-P", "open_interest": 5, "volume": 2}]}
        if "coinbase" in url:
            base = url.split("/products/")[1].split("-")[0]
            if base != "SOL":
                raise SourceError("HTTP 404 sur api.exchange.coinbase.com")
            return [[int(pd.Timestamp("2026-10-03", tz="UTC").timestamp()), 1, 1, 1, 202.0, 5.0]]
        if "upbit" in url:
            if "SOL" not in params["market"]:
                raise SourceError("HTTP 404 sur api.upbit.com")
            return [{"candle_date_time_utc": "2026-10-03T00:00:00", "trade_price": 300000.0, "candle_acc_trade_price": 1e9}]
        if "summary/fees" in url:
            return {"totalDataChart": [[int(pd.Timestamp("2026-10-03", tz="UTC").timestamp()), 1234.0]]}
        if "/tvl/" in url:
            return 5.0e8
        if "stablecoincharts" in url:
            return [{"date": str(int(pd.Timestamp("2026-10-03", tz="UTC").timestamp())), "totalCirculatingUSD": {"peggedUSD": 3.1e11}}]
        if "nasdaq" in url:
            return {"data": {"tradesTable": {"rows": [{"date": "10/02/2026", "close": "$377.91"}]}}}
        if "wikimedia" in url:
            assert headers and "github.com" in headers["User-Agent"]
            return {"items": [{"timestamp": "2026100300", "views": 2500}]}
        raise AssertionError(url)


class FakeFutures:
    def get_json(self, path, params=None):
        stamp = int(pd.Timestamp("2026-10-03", tz="UTC").timestamp() * 1000)
        if path == "/futures/data/basis":
            return [{"timestamp": stamp, "basis": "100", "basisRate": "0.001", "annualizedBasisRate": "0.05",
                     "futuresPrice": "85000", "indexPrice": "84900"}]
        if params["symbol"] == "NOPERPUSDT":
            raise SourceError("HTTP 400")
        return [{"timestamp": stamp, "longShortRatio": "2.0", "longAccount": "0.6667"}]


class FakeSpot:
    def get_json(self, path, params=None):
        assert path == "/api/v3/klines"
        start = pd.Timestamp("2026-10-03", tz="UTC")
        if params["startTime"] > start.timestamp() * 1000:
            return []
        close = {"SOLUSDT": "200", "ETHBTC": "0.03", "PAXGUSDT": "3800", "NOPERPUSDT": "1"}[params["symbol"]]
        return [[int(start.timestamp() * 1000), "1", "1", "1", close, "1", int(start.timestamp() * 1000) + DAY_MS - 1,
                 "1000", 1, "1", "1", "0"]]


class FakeArchives:
    def get(self, path):
        raise NotFound("absent", 404)


@pytest.fixture
def clients(settings, monkeypatch):
    monkeypatch.setattr(collect, "halal_symbols", lambda settings: ["NOPERPUSDT", "SOLUSDT"])
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    return collect.Clients(settings, web=FakeWeb(), futures=FakeFutures(), archives=FakeArchives(), spot=FakeSpot())


def test_store_keeps_first_observed_value_and_last_history(settings):
    seen = datetime(2026, 10, 3, tzinfo=UTC)
    first = store.rows([{"key": "btc", "date": "2026-10-02", "field": "f", "value": 1.0}], kind=store.OBSERVED, source="s", now=seen)
    again = store.rows([{"key": "btc", "date": "2026-10-02", "field": "f", "value": 9.0}], kind=store.OBSERVED, source="s", now=NOW)
    assert store.upsert(settings, "demo", first) == 1 and store.upsert(settings, "demo", again) == 0
    hist1 = store.rows([{"key": "btc", "date": "2026-10-02", "field": "f", "value": 2.0}], kind=store.HISTORY, source="s", now=seen)
    hist2 = store.rows([{"key": "btc", "date": "2026-10-02", "field": "f", "value": 3.0}], kind=store.HISTORY, source="s", now=NOW)
    store.upsert(settings, "demo", hist1)
    store.upsert(settings, "demo", hist2)
    frame = store.load(settings, "demo").set_index("kind")["value"]
    assert frame[store.OBSERVED] == 1.0 and frame[store.HISTORY] == 3.0           # relevé intact, historique révisé
    assert store.rows([{"key": "k", "date": "2026-10-02", "field": "f", "value": float("nan")}],
                      kind=store.HISTORY, source="s", now=NOW).empty
    with pytest.raises(ValueError):
        store.path_for(settings, "../evil")


def test_max_pain_by_hand():
    calls, puts = {90.0: 10.0, 100.0: 5.0}, {80.0: 8.0, 100.0: 2.0}
    # payé aux acheteurs : à 80 → 8·0 + 2·20 = 40 ; à 90 → 2·10 + 0 = 20 (+ puts 0) ; à 100 → calls 10·10 = 100
    assert fetch.max_pain([80.0, 90.0, 100.0], calls, puts) == 90.0


def test_parsers_on_fake_sources(clients):
    web = clients.web
    day = pd.Timestamp(NOW).floor("D")
    flows = fetch.coinmetrics_flows(web, start="2026-10-01")
    assert {r["field"] for r in flows} == set(fetch.FLOW_METRICS)
    glob = {r["field"]: r["value"] for r in fetch.coingecko_global(web, day=day)}
    assert glob["total2_usd"] == pytest.approx(1.2e12) and glob["total3_usd"] == pytest.approx(0.9e12)
    assert [r["key"] for r in fetch.coingecko_categories(web, day=day)] == ["meme-token", "meme-token"]
    options = {r["field"]: r["value"] for r in fetch.deribit_options(web, "BTC", now=NOW)}
    assert options["put_call_oi"] == 0.5 and options["put_call_volume"] == 0.5 and options["max_pain_1"] == 80000.0
    assert options["days_to_expiry_1"] > 26
    assert fetch.fred(web, "M2SL") == [{"key": "M2SL", "date": "2026-09-01", "field": "value", "value": 100.5}]
    assert fetch.treasury_10y(web, 2026) == [{"key": "UST10Y", "date": pd.Timestamp("2026-10-02"), "field": "yield_pct",
                                              "value": 5.28}]                       # case vide écartée
    m2 = {(r["key"], str(r["date"].date())): r["value"] for r in fetch.fed_m2(web)}
    assert m2 == {("M2SL", "2026-07-01"): 23217.9, ("WM2NS", "2026-08-31"): 23305.5}   # « ND » écarté
    assert fetch.nasdaq_gld(web, start="2026-10-01", end="2026-10-03")[0]["value"] == 377.91
    assert fetch.wikipedia_views(web, "Bitcoin", start="20261001", end="20261003")[0]["value"] == 2500.0
    basis = fetch.binance_basis(clients.futures, "BTCUSDT", "CURRENT_QUARTER")
    assert {r["key"] for r in basis} == {"BTCUSDT:CURRENT_QUARTER"} and len(basis) == 5


def test_backfill_writes_history_and_lists_where_pairs_trade(settings, clients):
    out = collect.backfill(settings, now=NOW, clients=clients, archives=False)
    assert all("error" not in r for r in out.values()), out
    assert out["coinbase"]["present"] == 1 and out["ratios"]["present"] == 1        # NOPERP absent partout sauf spot
    status = {(r["series"], r["kind"]) for r in store.summary(settings)}
    assert ("flows", store.HISTORY) in status and ("market_global", store.OBSERVED) in status   # pas d'historique gratuit
    state = collect.load_state(settings)
    assert state["listed"]["coinbase"]["keys"] == ["SOL"]
    korea = views.korea_premium(settings)
    # 300 000 KRW / (1500 / 1,25 = 1200 KRW par $) = 250 $ ; Binance 200 $ → prime +25 %
    assert korea.loc[pd.Timestamp("2026-10-03", tz="UTC"), "SOL"] == pytest.approx(0.25)
    assert views.coinbase_premium(settings).loc[pd.Timestamp("2026-10-03", tz="UTC"), "SOL"] == pytest.approx(0.01)


def test_daily_record_once_after_3am_and_retries_only_failures(settings, clients):
    assert real_record_day(settings, now=datetime(2026, 10, 4, 2, 0, tzinfo=UTC), clients=clients) is None
    clients.web.fail = {"coingecko"}
    first = real_record_day(settings, now=NOW, clients=clients)
    assert set(first["errors"]) == {"market_global", "market_categories"}
    clients.web.fail, clients.web.calls = set(), []
    second = real_record_day(settings, now=NOW.replace(hour=5), clients=clients)
    assert second["errors"] == {} and all("coingecko" in c for c in clients.web.calls)   # seules les séries en échec
    assert real_record_day(settings, now=NOW.replace(hour=6), clients=clients) is None
    observed = store.load(settings, "flows")
    assert set(observed["kind"]) == {store.OBSERVED}


def test_new_hosts_stay_within_the_whitelist():
    web = PublicSources()
    for url in ("https://api.llama.fi/protocols", "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                "https://wikimedia.org/w/index.php", "https://api.upbit.com/v1/orders",
                "https://www.federalreserve.gov/apps/login", "https://home.treasury.gov/admin",
                "https://api.nasdaq.com/api/quote/AAPL/historical"):
        with pytest.raises(PermissionError):
            web.get(url)
    futures = PublicHttpClient.futures_rest("https://fapi.binance.com")
    futures._check_path("/futures/data/basis")
    for path in ("/fapi/v1/order", "/fapi/v2/account", "/fapi/v1/listenKey"):
        with pytest.raises(PermissionError):
            futures._check_path(path)


def test_state_file_is_json(settings, clients):
    collect.backfill(settings, now=NOW, clients=clients, archives=False, only=["ecb"])
    json.loads((store.context_dir(settings) / "_journal.json").read_text(encoding="utf-8"))


def test_backfill_never_overwrites_a_daily_record_written_meanwhile(settings, clients, monkeypatch):
    """Défaut du 2026-10-04 : le téléchargement gardait le journal lu au départ et effaçait, en le sauvant, le relevé
    quotidien écrit entre-temps par la surveillance."""
    real = fetch.ecb_rates

    def concurrent(client, *, start):
        state = collect.load_state(settings)
        state.setdefault("days", {})["2026-10-04"] = {"done": ["flows"], "errors": {}}
        collect.save_state(settings, state)                    # écrit « pendant » le téléchargement
        return real(client, start=start)

    monkeypatch.setattr(fetch, "ecb_rates", concurrent)
    collect.backfill(settings, now=NOW, clients=clients, archives=False, only=["ecb"])
    assert collect.load_state(settings)["days"]["2026-10-04"]["done"] == ["flows"]


def test_protocol_without_tvl_is_absent_not_an_error():
    class NoTvl:
        def get_json(self, url, params=None, headers=None):
            raise SourceError("JSON illisible : https://api.llama.fi/tvl/x")
    assert fetch.defillama_tvl(NoTvl(), "FIL", "filecoin", day=pd.Timestamp("2026-10-04", tz="UTC")) == []
