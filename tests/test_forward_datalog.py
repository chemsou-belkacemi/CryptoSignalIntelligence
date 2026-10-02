"""Relevé quotidien des données de contexte (forward/sources.py, forward/datalog.py) : réponses simulées, aucun
réseau ; indicateurs calculés à la main. SYNTHÉTIQUE."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward import datalog
from crypto_signal_intelligence.forward import sources as src
from crypto_signal_intelligence.forward.halal import HalalList

from .conftest import PROJECT, canonical

NOW = datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
ECB = ("KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
       + "".join(f"x,D,{c},EUR,SP00,A,2026-10-01,{v}\n" for c, v in
                 {"USD": 1.1298, "JPY": 178.49, "GBP": 0.8537, "CAD": 1.6095, "SEK": 11.331, "CHF": 0.9437}.items()))


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "www.deribit.com":
        return httpx.Response(200, json={"result": {"data": [[1790812800000, 35, 36, 34, 35.2], [1790899200000, 35, 37, 35, 36.4]]}})
    if host == "api.alternative.me":
        return httpx.Response(200, json={"data": [{"value": "72", "value_classification": "Greed", "timestamp": "1790899200"}]})
    if host == "fred.stlouisfed.org":
        return httpx.Response(200, text="observation_date,NASDAQ100\n2026-09-30,30408.5\n2026-10-01,.\n")
    if host == "data-api.ecb.europa.eu":
        return httpx.Response(200, text=ECB)
    if host == "api.kraken.com":
        return httpx.Response(200, json={"error": [], "result": {"USDTZUSD": {"b": ["0.9996"], "a": ["0.9998"]},
                                                                  "USDCUSD": {"b": ["0.9999"], "a": ["1.0001"]}}})
    if host == "www.bitstamp.net":
        return httpx.Response(200, json={"bid": "0.9997", "ask": "0.9998"})
    if host == "www.okx.com":
        ts = int(pd.Timestamp(NOW).timestamp() * 1000)
        if "after" in request.url.params:
            return httpx.Response(200, json={"data": [{"details": []}]})
        return httpx.Response(200, json={"data": [{"details": [
            {"ts": str(ts - 3_600_000), "posSide": "long", "sz": "2", "bkPx": "60000"},
            {"ts": str(ts - 7_200_000), "posSide": "short", "sz": "1", "bkPx": "61000"},
            {"ts": str(ts - 90_000_000), "posSide": "long", "sz": "5", "bkPx": "59000"}]}]})
    return httpx.Response(404)


def client():
    return src.PublicSources(transport=httpx.MockTransport(handler))


def test_only_whitelisted_https_addresses_are_read():
    c = client()
    with pytest.raises(PermissionError):
        c.get("https://fapi.binance.com/fapi/v1/order")
    with pytest.raises(PermissionError):
        c.get("http://api.alternative.me/fng/")
    with pytest.raises(PermissionError):
        c.get("https://api.kraken.com/0/private/Balance")


def test_each_source_by_hand():
    c = client()
    assert src.fetch_dvol(c, now=NOW)["BTC"]["last"] == 36.4
    assert src.fetch_fear_greed(c, now=NOW) == {"value": 72, "label": "Greed", "day": "2026-10-02"}
    assert src.fetch_nasdaq100(c, now=NOW)["closes"] == [["2026-09-30", 30408.5]]          # « . » = pas de séance
    dxy = src.fetch_dxy(c, now=NOW)
    assert dxy["last"][0] == "2026-10-01" and dxy["last"][1] == pytest.approx(101.73, abs=0.01)
    peg = src.fetch_stablecoins(c, now=NOW)
    assert peg["USDT"]["deviation_pct"] == pytest.approx(-0.03) and peg["USDC"]["deviation_pct"] == pytest.approx(0.0)
    liq = src.fetch_liquidations(c, now=NOW)["BTC-USDT"]
    assert liq["orders"] == 2 and liq["long_contracts_value"] == 120000 and liq["short_contracts_value"] == 61000
    assert liq["complete_24h"]


def test_dxy_formula_matches_ice_weights():
    rates = {"USD": 1.0, "JPY": 1.0, "GBP": 1.0, "CAD": 1.0, "SEK": 1.0, "CHF": 1.0}
    assert src.dxy_from_ecb(rates) == pytest.approx(src.DXY_CONSTANT)


def test_atr_and_adx_by_hand():
    rising = pd.DataFrame({"day": pd.date_range("2026-01-01", periods=40, tz="UTC"),
                           "open": np.arange(40) + 100.0, "high": np.arange(40) + 101.0,
                           "low": np.arange(40) + 99.0, "close": np.arange(40) + 100.5})
    atr, adx = datalog.atr_adx(rising)
    assert atr == pytest.approx(2.0 / 139.5 * 100, abs=1e-3)          # vrai range constant de 2
    assert adx == pytest.approx(100.0)                                  # tendance pure : +DI seul
    assert datalog.atr_adx(rising.head(20)) == (None, None)


def test_daily_bars_keep_only_complete_utc_days():
    hourly = canonical(24 * 3 + 5, "1h", symbol="BTCUSDT", start="2026-09-28")
    days = datalog.daily_bars(hourly, pd.Timestamp("2026-10-01 05:00", tz="UTC"))
    assert len(days) == 3 and (days["day"].dt.hour == 0).all()


def test_btc_nasdaq_correlation_uses_common_sessions():
    days = pd.date_range("2026-08-01", periods=60, tz="UTC")
    rng = np.random.default_rng(3)
    btc = pd.DataFrame({"day": days, "close": 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 60)))})
    ndx = [[d.date().isoformat(), float(c)] for d, c in zip(days, btc["close"], strict=True) if d.weekday() < 5]
    out = datalog.btc_ndx_correlation(btc, ndx)
    assert out["correlation"] == pytest.approx(1.0) and out["sessions"] == 30


def test_record_day_is_idempotent_and_retries_failed_sources(settings):
    store = CandleStore(settings.data_dir)
    store.save(canonical(24 * 80, "1h", symbol="BTCUSDT", start="2026-07-10"), "BTCUSDT", "1h")
    halal = HalalList(("BTCUSDT", "ETHUSDT"), {}, "a" * 64, "b" * 64)
    calls = {"n": 0}

    def flaky(c, *, now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise src.SourceError("panne")
        return {"value": 1}

    fetchers = {"fear_greed": src.fetch_fear_greed, "flaky": flaky}
    out = datalog.record_day(settings, now=NOW, client=client(), halal=halal, fetchers=fetchers, depth=False)
    assert out == {"day": "2026-10-02", "sources": 1, "errors": 1}
    log = datalog.journal(settings)
    ind = next(log.entries({datalog.INDICATORS}))["data"]
    assert "BTCUSDT" in ind["pairs"] and ind["missing"] == ["ETHUSDT"]
    assert datalog.summary(settings)["days"] == 0                                  # jour pas encore clos
    out = datalog.record_day(settings, now=NOW + timedelta(hours=1), client=client(), halal=halal, fetchers=fetchers,
                             depth=False)
    assert out["sources"] == 2 and datalog.summary(settings)["days"] == 1
    assert datalog.record_day(settings, now=NOW + timedelta(hours=2), client=client(), halal=halal,
                              fetchers=fetchers, depth=False)["already"]
    assert sum(1 for _ in log.entries({datalog.INDICATORS})) == 1 and log.verify()["ok"]
    assert json.dumps(next(log.entries({datalog.SOURCE}))["data"])


def test_book_metrics_by_hand():
    book = {"bids": [["99.95", "10"], ["99.5", "20"], ["98.0", "100"]], "asks": [["100.05", "5"], ["100.9", "10"]]}
    out = datalog.book_metrics(book)
    assert out["spread_bps"] == pytest.approx(10.0)
    assert out["bid_depth_usdt"] == pytest.approx(99.95 * 10 + 99.5 * 20)      # 98 est hors de −1 %
    assert out["ask_depth_usdt"] == pytest.approx(100.05 * 5 + 100.9 * 10) and out["truncated"]   # pas jusqu'à +1 %


def test_depth_client_reads_only_the_public_book():
    from crypto_signal_intelligence.data.http import PublicHttpClient
    depth = PublicHttpClient.depth("https://data-api.binance.vision")
    for path in ("/api/v3/order", "/api/v3/account", "/api/v3/klines", "/sapi/v1/capital/config/getall"):
        with pytest.raises(PermissionError):
            depth.get(path)
    candles = PublicHttpClient.rest("https://data-api.binance.vision")
    with pytest.raises(PermissionError):
        candles.get("/api/v3/depth")                       # la liste blanche des bougies n'a pas changé


def test_correlation_is_recorded_once_the_nasdaq_arrives(settings):
    store = CandleStore(settings.data_dir)
    store.save(canonical(24 * 80, "1h", symbol="BTCUSDT", start="2026-07-10"), "BTCUSDT", "1h")
    halal = HalalList(("BTCUSDT",), {}, "a" * 64, "b" * 64)
    state = {"down": True}

    def nasdaq(c, *, now):
        if state["down"]:
            raise src.SourceError("réseau : ReadTimeout")
        days = pd.date_range("2026-08-01", "2026-09-30", tz="UTC")
        return {"closes": [[d.date().isoformat(), 100 + i] for i, d in enumerate(days) if d.weekday() < 5]}

    datalog.record_day(settings, now=NOW, client=client(), halal=halal, fetchers={"nasdaq100": nasdaq}, depth=False)
    log = datalog.journal(settings)
    assert not list(log.entries({datalog.CORRELATION}))
    state["down"] = False
    datalog.record_day(settings, now=NOW + timedelta(hours=1), client=client(), halal=halal,
                       fetchers={"nasdaq100": nasdaq}, depth=False)
    corr = next(log.entries({datalog.CORRELATION}))["data"]["btc_ndx_30d"]
    assert corr["sessions"] >= 15 and corr["correlation"] is not None


def test_fiat_currencies_are_structural_exclusions():
    from crypto_signal_intelligence.forward.halal import load_screen, structural_reason
    screen = load_screen(PROJECT / "config" / "halal_screen.yaml")
    for base in ("GBP", "EUR", "JPY", "TRY", "USDC"):
        assert structural_reason(base, screen) == "stablecoins_et_fiat", base
