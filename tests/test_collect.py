"""Collecteur en shadow (collect/, docs/COLLECTE.md) : liste fermée d'adresses, agrégations sur messages FICTIFS,
filtre du carnet, calculs d'options sur un résumé fictif, comptage Reddit sans contenu stocké, reconnexion, état,
route API, et aucune écriture hors des journaux C_*. Aucun réseau."""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi, make_handler
from crypto_signal_intelligence.collect import (
    attention,
    base,
    carnet,
    flux,
    liquidations,
    net,
    options,
    service,
)
from crypto_signal_intelligence.forward.journal import Journal

NOW = datetime(2026, 10, 10, 12, 0, 30, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)
T0 = MS - MS % 60_000 - 5 * 60_000          # 11:55:00, minute close depuis longtemps


class FakeClock:
    """Horloge et temps monotone pilotés par les tests (jamais décroissants)."""

    def __init__(self, start: datetime = NOW):
        self.at = start
        self.mono = 1000.0

    def __call__(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.mono += seconds


def make_stream(messages_by_url: dict, *, clock: FakeClock | None = None, step: float = 0.0, fail_after: bool = False):
    """Faux flux WebSocket : rend les messages prévus pour l'adresse, puis se coupe (ou lève)."""
    calls: list[str] = []

    @asynccontextmanager
    async def stream(url: str):
        net.check_url(url)
        calls.append(url)
        key = next((k for k in messages_by_url if k in url), None)

        async def messages():
            for message in messages_by_url.get(key, []):
                if clock is not None and step:
                    clock.advance(step)
                yield message
            if fail_after:
                raise net.NetError("connexion coupée (test)")
        yield messages()

    stream.calls = calls  # type: ignore[attr-defined]
    return stream


def context(settings, clock: FakeClock, *, stream=None, http=None, sleep=None) -> base.Context:
    state = base.State(settings, clock=clock, monotonic=clock.monotonic, min_interval=0.0)
    ctx = base.Context(settings, base.Recorder(settings, state), state, clock=clock, monotonic=clock.monotonic,
                       http=http, stream=stream)
    if sleep is not None:
        ctx.sleep = sleep
    return ctx


# --- liste fermée d'adresses ------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "wss://fstream.binance.com/ws/!forceOrder@arr",
    "wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@1000ms/ethusdt@aggTrade",
    "https://www.deribit.com/api/v2/public/get_index_price",
    "https://www.reddit.com/r/CryptoCurrency/new.json",
])
def test_closed_list_accepts_the_declared_public_addresses(url):
    assert net.check_url(url) == url


@pytest.mark.parametrize("url", [
    "https://api.binance.com/api/v3/order",                       # ordres : jamais
    "https://api.binance.com/api/v3/klines",                      # même les bougies : c'est data/http.py qui les lit
    "wss://stream.binance.com:9443/ws/listenKeyAbc",              # flux utilisateur
    "wss://fstream.binance.com/ws/abc@depth@private",
    "https://www.deribit.com/api/v2/private/get_account_summary",
    "https://www.deribit.com/api/v2/public/../private/x",
    "https://www.reddit.com/r/CryptoCurrency/hot.json",
    "https://www.reddit.com/api/v1/me",
    "http://www.reddit.com/r/Bitcoin/new.json",                   # pas de http en clair
    "ws://stream.binance.com:9443/ws/btcusdt@aggTrade",
    "https://evil.invalid/https://www.deribit.com/api/v2/public/",
    "https://www.deribit.com.evil.invalid/api/v2/public/x",
    "https://user:pw@www.deribit.com/api/v2/public/x",
    "https://trends.google.com/trends/explore",                   # Google Trends : NON_DISPONIBLE, hors liste
])
def test_closed_list_refuses_everything_else_before_any_call(url):
    with pytest.raises(net.RefusedUrl):
        net.check_url(url)


def test_http_client_refuses_before_touching_the_transport():
    hits = []

    def handler(request):
        hits.append(str(request.url))
        return httpx.Response(200, json={"result": {}})

    client = net.CollectHttp(transport=httpx.MockTransport(handler))
    with pytest.raises(net.RefusedUrl):
        client.get_json("https://api.binance.com/api/v3/account")
    with pytest.raises(net.RefusedUrl):
        client.get_json("https://www.deribit.com/api/v2/public/get_index_price", {"api_key": "x"})
    assert hits == []
    assert client.get_json("https://www.deribit.com/api/v2/public/get_index_price", {"index_name": "btc_usd"}) == {"result": {}}
    assert hits and "index_name=btc_usd" in hits[0]
    client.close()


# --- liquidations ------------------------------------------------------------------------------------------------
def liq(symbol, side, price, qty, t):
    return {"e": "forceOrder", "E": t, "o": {"s": symbol, "S": side, "p": str(price), "ap": str(price), "q": str(qty),
                                             "z": str(qty), "T": t}}


def test_liquidations_aggregate_per_minute_large_orders_and_hourly_resume():
    messages = [liq("BTCUSDT", "SELL", 100_000, 0.5, T0 + 1_000),        # long liquidé 50 000
                liq("BTCUSDT", "SELL", 100_000, 2.0, T0 + 2_000),        # 200 000 : GROS
                liq("ETHUSDT", "BUY", 4_000, 5, T0 + 3_000),             # court liquidé 20 000
                liq("ETHBUSD", "BUY", 4_000, 5, T0 + 3_500),             # pas en USDT : ignoré
                liq("SOLUSDT", "SELL", 200, 10, T0 + 61_000),            # minute suivante
                {"e": "autre"}, {"o": "pas un dict"}]
    out = liquidations.aggregate(messages, now_ms=T0 + 3 * 60_000)
    kinds = [k for k, _ in out]
    assert kinds == [liquidations.LARGE, liquidations.MINUTE, liquidations.MINUTE]
    large = out[0][1]
    assert large["symbol"] == "BTCUSDT" and large["notional_usdt"] == 200_000 and large["long_liquidated"]
    first = out[1][1]
    assert first["minute"] == base.iso_ms(T0) and first["n"] == 3 and first["pairs_count"] == 2
    assert first["notional_usdt"] == 270_000 and first["long_liq_usdt"] == 250_000 and first["short_liq_usdt"] == 20_000
    assert first["pairs"]["BTCUSDT"] == [2, 250_000, 250_000, 0] and first["pairs"]["ETHUSDT"] == [1, 20_000, 0, 20_000]
    assert first["others"]["pairs"] == 0
    assert out[2][1]["pairs"] == {"SOLUSDT": [1, 2_000, 2_000, 0]}
    # Rien tant que la minute n'est pas close depuis 3 s ; rien pour une minute sans notionnel.
    assert liquidations.aggregate(messages[:1], now_ms=T0 + 60_000) == []
    assert liquidations.aggregate([], now_ms=T0 + 10 * 60_000) == []
    # Résumé horaire quand l'heure passe : totaux, part des longs, top 5.
    agg = liquidations.MinuteAggregator()
    hour = T0 - T0 % 3_600_000
    for i in range(3):
        agg.add(liquidations.parse(liq("BTCUSDT", "SELL", 100, 10, hour + i * 60_000 + 500)))
    agg.add(liquidations.parse(liq("XRPUSDT", "BUY", 2, 500, hour + 60_000 + 700)))
    entries = agg.flush(hour + 3_600_000 + 2 * 60_000)
    assert [k for k, _ in entries] == [liquidations.MINUTE] * 3 + [liquidations.RESUME]
    resume = entries[-1][1]
    assert resume["hour"] == base.iso_ms(hour) and resume["n"] == 4 and resume["notional_usdt"] == 4_000
    assert resume["long_share"] == 0.75 and resume["minutes_with_liquidations"] == 3
    assert [t["symbol"] for t in resume["top"]] == ["BTCUSDT", "XRPUSDT"]


def test_liquidations_minute_keeps_the_top_pairs_and_aggregates_the_rest():
    messages = [liq(f"P{i}USDT", "SELL", 1, 1_000 * (i + 1), T0 + 100 + i) for i in range(14)]
    (_, entry), = liquidations.aggregate(messages, now_ms=T0 + 2 * 60_000)
    assert len(entry["pairs"]) == liquidations.TOP_PAIRS and entry["pairs_count"] == 14
    assert "P13USDT" in entry["pairs"] and "P0USDT" not in entry["pairs"]
    assert entry["others"] == {"pairs": 4, "n": 4, "notional_usdt": 10_000, "long_liq_usdt": 10_000, "short_liq_usdt": 0}


# --- flux --------------------------------------------------------------------------------------------------------
def trade(symbol, price, qty, t, *, maker_is_buyer):
    return {"stream": f"{symbol.lower()}@aggTrade", "data": {"e": "aggTrade", "s": symbol, "p": str(price), "q": str(qty),
                                                            "T": t, "m": maker_is_buyer}}


def test_flux_minute_entry_covers_all_pairs_with_imbalances_and_large_trades():
    messages = [trade("BTCUSDT", 100_000, 1.2, T0 + 100, maker_is_buyer=False),     # taker ACHÈTE 120 000 : GROS
                trade("BTCUSDT", 100_000, 0.2, T0 + 200, maker_is_buyer=True),      # taker vend 20 000
                trade("ETHUSDT", 4_000, 1, T0 + 300, maker_is_buyer=True),          # vend 4 000
                {"stream": "x@kline", "data": {"e": "kline"}}]
    out = flux.aggregate(messages, now_ms=T0 + 2 * 60_000)
    assert [k for k, _ in out] == [flux.LARGE, flux.MINUTE]
    large = out[0][1]
    assert large == {"symbol": "BTCUSDT", "taker_side": "BUY", "price": 100_000.0, "quantity": 1.2, "notional_usdt": 120_000,
                     "time": base.iso_ms(T0 + 100)}
    minute = out[1][1]
    assert "fields" not in minute and minute["minute"] == base.iso_ms(T0)
    assert minute["pairs"]["BTCUSDT"] == [120_000, 20_000, 2, round(100_000 / 140_000, 3), None, 1, 120_000]
    assert minute["pairs"]["ETHUSDT"] == [0, 4_000, 1, -1.0, None, 0, 0]
    assert minute["taker_buy_usdt"] == 120_000 and minute["taker_sell_usdt"] == 24_000


def test_flux_large_threshold_is_higher_for_btc_and_eth():
    assert flux.large_usdt("BTCUSDT") == flux.large_usdt("ETHUSDT") == 100_000 and flux.large_usdt("SOLUSDT") == 50_000
    messages = [trade("BTCUSDT", 100_000, 0.6, T0 + 100, maker_is_buyer=False),     # 60 000 : pas gros pour BTC
                trade("SOLUSDT", 200, 300, T0 + 200, maker_is_buyer=False)]         # 60 000 : gros pour SOL
    out = flux.aggregate(messages, now_ms=T0 + 2 * 60_000)
    assert [(k, d.get("symbol")) for k, d in out][:1] == [(flux.LARGE, "SOLUSDT")]
    assert out[-1][1]["pairs"]["BTCUSDT"][5:] == [0, 0] and out[-1][1]["pairs"]["SOLUSDT"][5:] == [1, 60_000]


def test_flux_empty_minutes_count_as_zero_in_the_five_minute_window():
    agg = flux.MinuteAggregator(pairs=["BTCUSDT", "ETHUSDT"])
    agg.add(flux.parse(trade("BTCUSDT", 100, 10, T0 + 10, maker_is_buyer=False)))          # minute 0 : achat 1 000
    agg.add(flux.parse(trade("BTCUSDT", 100, 10, T0 + 4 * 60_000 + 10, maker_is_buyer=True)))   # minute 4 : vente 1 000
    entries = agg.flush(T0 + 6 * 60_000)
    assert [d["minute"] for _, d in entries] == [base.iso_ms(T0 + i * 60_000) for i in range(5)]   # minutes 1-3 vides écrites
    assert entries[2][1]["pairs"] == {"BTCUSDT": [0, 0, 0, None, None, 0, 0], "ETHUSDT": [0, 0, 0, None, None, 0, 0]}
    assert entries[4][1]["pairs"]["BTCUSDT"][4] == 0.0                     # 5 minutes closes : (1000 − 1000) / 2000
    assert entries[4][1]["pairs"]["ETHUSDT"][4] is None                    # total nul sur 5 min


def test_flux_five_minute_imbalance_needs_five_closed_minutes():
    agg = flux.MinuteAggregator()
    for i in range(6):
        agg.add(flux.parse(trade("BTCUSDT", 100, 10 if i < 5 else 30, T0 + i * 60_000 + 10, maker_is_buyer=False)))
        agg.add(flux.parse(trade("BTCUSDT", 100, 10, T0 + i * 60_000 + 20, maker_is_buyer=True)))
    entries = agg.flush(T0 + 7 * 60_000)
    imb5 = [data["pairs"]["BTCUSDT"][4] for _, data in entries]
    assert imb5[:4] == [None] * 4 and imb5[4] == 0.0
    # 6e minute : achats 1000+1000+1000+1000+3000 = 7000, ventes 5000 → (7000-5000)/12000
    assert imb5[5] == pytest.approx(2000 / 12000, abs=1e-3)       # arrondi à 3 décimales
    assert entries[5][1]["pairs"]["BTCUSDT"][3] == 0.5


def test_flux_large_trades_are_capped_per_minute_but_always_counted():
    messages = [trade("BTCUSDT", 100_000, 1, T0 + 100 + i, maker_is_buyer=False) for i in range(5)]
    out = flux.aggregate(messages, now_ms=T0 + 2 * 60_000)
    assert [k for k, _ in out].count(flux.LARGE) == flux.MAX_LARGE_PER_MINUTE
    assert out[-1][1]["pairs"]["BTCUSDT"][5:] == [5, 500_000]


# --- carnet ------------------------------------------------------------------------------------------------------
BOOK = {"bids": [["99.9", "10"], ["99.6", "20"], ["99.2", "30"], ["98.5", "40"]],
        "asks": [["100.1", "5"], ["100.4", "10"], ["100.9", "20"], ["103.0", "100"]]}


def test_book_sample_by_hand_and_truncation_flags():
    s = carnet.book_sample(BOOK["bids"], BOOK["asks"])
    assert s["bid"] == 99.9 and s["ask"] == 100.1 and s["spread_pct"] == pytest.approx(0.2)
    # ±0,5 % du milieu 100 : achats ≥ 99,5 → 999 + 1992 ; ventes ≤ 100,5 → 500,5 + 1004
    assert (s["bid_0.5"], s["ask_0.5"]) == (2991, 1504)
    assert (s["bid_1"], s["ask_1"]) == (2991 + 2976, 1504 + 2018)
    assert s["imb_1"] == pytest.approx((5967 - 3522.5) / (5967 + 3522.5), abs=1e-3)
    assert s["truncated"]["0.5"] == [False, False] and s["truncated"]["1"] == [False, False]
    deep = carnet.book_sample(BOOK["bids"][:1], BOOK["asks"][:1])
    assert deep["truncated"]["1"] == [True, True]            # un seul niveau : la bande n'est pas couverte
    with pytest.raises(ValueError):
        carnet.book_sample([["100", "1"]], [["99", "1"]])


def test_book_change_filter_ten_percent():
    first = carnet.book_sample(BOOK["bids"], BOOK["asks"])
    assert carnet.changed(None, first)
    same = dict(first, bid_1=round(first["bid_1"] * 1.05))
    assert not carnet.changed(first, same)
    moved = first | {"ask_0.5": round(first["ask_0.5"] * 1.11)}
    assert carnet.changed(first, moved)
    tilted = dict(first, imb_1=first["imb_1"] - 0.2)
    assert carnet.changed(first, tilted)
    assert not carnet.changed(first, dict(first, imb_1=first["imb_1"] - 0.05))


def test_pair_tracker_writes_only_on_change_caps_per_hour_and_resumes_each_hour():
    tracker = carnet.PairTracker("BTCUSDT")
    hour = T0 - T0 % 3_600_000
    base_sample = carnet.book_sample(BOOK["bids"], BOOK["asks"])
    out = tracker.observe(base_sample, now_ms=hour + 5_000, mono=0.0)
    assert [k for k, _ in out] == [carnet.SAMPLE] and out[0][1]["symbol"] == "BTCUSDT"
    assert tracker.observe(base_sample, now_ms=hour + 10_000, mono=5.0) == []       # inchangé : rien
    # Changements > 10 % toutes les 5 s : au plus une écriture par 10 min (espacement), 6 par heure calendaire.
    written_at = []
    for i in range(1, 700):
        sample = dict(base_sample, bid_1=round(base_sample["bid_1"] * (1 + 0.3 * i)))
        now_ms = hour + 10_000 + 5_000 * i
        written_at += [now_ms for k, _ in tracker.observe(sample, now_ms=now_ms, mono=5.0 * (i + 1)) if k == carnet.SAMPLE]
    assert len(written_at) == carnet.MAX_WRITES_PER_HOUR - 1
    assert min(b - a for a, b in zip(written_at, written_at[1:], strict=False)) >= carnet.MIN_WRITE_GAP_SECONDS * 1000
    assert tracker.samples == 701 and tracker.written == carnet.MAX_WRITES_PER_HOUR
    # Heure suivante : résumé de l'heure close (sur TOUS les échantillons), compteur calendaire remis à zéro, puis une
    # écriture possible dès que l'espacement est respecté.
    out = tracker.observe(base_sample, now_ms=hour + 3_600_000 + 5_000, mono=4000.0)
    assert [k for k, _ in out] == [carnet.RESUME, carnet.SAMPLE]
    resume = out[0][1]
    assert resume["hour"] == base.iso_ms(hour) and resume["samples"] == 701 and resume["written"] == carnet.MAX_WRITES_PER_HOUR
    assert resume["stats"]["spread_pct"] == {"min": base_sample["spread_pct"], "max": base_sample["spread_pct"],
                                             "mean": base_sample["spread_pct"]}
    assert resume["stats"]["bid_1"]["max"] == round(base_sample["bid_1"] * (1 + 0.3 * 699))
    assert tracker.samples == 1 and tracker.written == 1


def test_tracked_pairs_add_the_assistant_active_calls_read_only(settings):
    path = settings.root / "state" / "assistant.json"
    path.parent.mkdir(parents=True)
    payload = {"active_calls": [{"symbol": "DOGEUSDT"}, {"symbol": "BTCUSDT"}, {"symbol": "ETHBTC"}] + [{"symbol": f"X{i}USDT"} for i in range(40)]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    pairs = carnet.tracked_pairs(settings)
    assert pairs[:16] == list(settings.data.symbols) and pairs[16] == "DOGEUSDT" and "ETHBTC" not in pairs
    assert len(pairs) == carnet.MAX_PAIRS and path.read_bytes() == before
    path.write_text("{pas du json", encoding="utf-8")
    assert carnet.tracked_pairs(settings) == list(settings.data.symbols)
    assert carnet.stream_url(["BTCUSDT", "ETHUSDT"]) == \
        "wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@1000ms/ethusdt@depth20@1000ms"


def depth(symbol, scale=1.0):
    return {"stream": f"{symbol.lower()}@depth20@1000ms",
            "data": {"bids": [[p, str(float(q) * scale)] for p, q in BOOK["bids"]], "asks": BOOK["asks"]}}


def test_book_collector_samples_every_five_seconds_and_writes_the_journal(settings):
    clock = FakeClock()
    messages = [depth("BTCUSDT"), depth("ETHUSDT"), depth("BTCUSDT"), depth("BTCUSDT", 1.01), depth("BTCUSDT", 3.0)]
    stream = make_stream({"@depth20": messages}, clock=clock, step=3.0)
    ctx = context(settings, clock, stream=stream)
    asyncio.run(carnet.run(ctx))
    entries = list(Journal(base.journal_path(settings, base.CARNET, NOW)).entries())
    kinds = [(e["kind"], e["data"]["symbol"]) for e in entries]
    # échantillons à t=3 (BTC seul), t=9 (BTC+ETH, BTC inchangé), t=15 (BTC ×3 : > 10 % mais à moins de 10 min : rien)
    assert kinds == [("CARNET_5S", "BTCUSDT"), ("CARNET_5S", "ETHUSDT")]
    assert ctx.state.sources[base.CARNET]["messages"] == 5 and ctx.state.sources[base.CARNET]["entries"] == 2
    assert "16 paires" in ctx.state.sources[base.CARNET]["detail"]


# --- options -----------------------------------------------------------------------------------------------------
def option(name, iv, oi, underlying=100_000.0):
    return {"instrument_name": name, "mark_iv": iv, "open_interest": oi, "underlying_price": underlying,
            "bid_price": 0.01, "ask_price": 0.02, "volume": 1}


SUMMARY = [option("BTC-13OCT26-100000-C", 40.0, 10), option("BTC-13OCT26-100000-P", 42.0, 30),    # ~3 jours
           option("BTC-13OCT26-90000-P", 50.0, 100), option("BTC-13OCT26-110000-C", 45.0, 5),
           option("BTC-6NOV26-100000-C", 55.0, 20), option("BTC-6NOV26-100000-P", 57.0, 20),       # ~27 jours
           option("BTC-6NOV26-90000-P", 63.0, 50), option("BTC-6NOV26-110000-C", 52.0, 40),
           option("BTC-6NOV26-85000-P", 68.0, 10), option("BTC-6NOV26-115000-C", 54.0, 10),
           option("BTC-25DEC26-100000-C", 60.0, 5), option("BTC-25DEC26-100000-P", 61.0, 5),
           option("BTC-PERPETUAL", None, 1), {"instrument_name": "n'importe quoi"}]


def test_options_summary_atm_iv_skew_put_call_and_max_pain():
    out = options.summarize(SUMMARY, now=NOW, index_price=100_000.0)
    assert out["instruments"] == 12
    assert out["oi_calls"] == 90 and out["oi_puts"] == 215 and out["put_call_oi"] == round(215 / 90, 4)
    atm = out["atm_iv_30d"]
    assert atm["expiry"].startswith("2026-11-06T08:00") and atm["strike"] == 100_000 and atm["iv_pct"] == 56.0 and atm["legs"] == 2
    assert 26 < atm["days"] < 28
    skew = out["skew_25d"]
    assert skew["put_strike"] < 100_000 < skew["call_strike"] and skew["skew_pts"] == round(skew["put_iv_pct"] - skew["call_iv_pct"], 2)
    assert skew["skew_pts"] > 0
    pain = out["max_pain"]
    assert pain["expiry"].startswith("2026-10-13") and pain["strike"] == 100_000 and pain["oi"] == 145
    assert "Black-Scholes" in out["delta_method"]
    empty = options.summarize([{"instrument_name": "BTC-PERPETUAL"}], now=NOW)
    assert empty["atm_iv_30d"] is None and empty["put_call_oi"] is None and empty["instruments"] == 0


def test_bs_delta_is_near_half_at_the_money_and_signed_by_side():
    assert options.bs_delta(100, 100, 50, 0.1, call=True) == pytest.approx(0.53, abs=0.01)
    assert options.bs_delta(100, 100, 50, 0.1, call=False) == pytest.approx(-0.47, abs=0.01)
    assert options.bs_delta(100, 150, 50, 0.1, call=True) < 0.05
    assert options.bs_delta(0, 100, 50, 0.1, call=True) is None
    assert options.parse_instrument("ETH-27DEC24-3d5-P") == (pd.Timestamp("2024-12-27 08:00", tz="UTC"), 3.5, "P")
    assert options.parse_instrument("ETH-PERPETUAL") is None


def deribit_transport(fail: set[str] = frozenset()):
    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        if path in fail:
            return httpx.Response(500, text="panne")
        if path == "get_index_price":
            return httpx.Response(200, json={"result": {"index_price": 100_000.0, "estimated_delivery_price": 100_000.0}})
        if path == "get_volatility_index_data":
            assert request.url.params["resolution"] == "3600" and request.url.params["currency"] in ("BTC", "ETH")
            return httpx.Response(200, json={"result": {"data": [[MS - 7_200_000, 50, 51, 49, 50.5], [MS - 3_600_000, 50.5, 52, 50, 51.2]],
                                                        "continuation": None}})
        if path == "get_book_summary_by_currency":
            assert request.url.params["kind"] == "option"
            return httpx.Response(200, json={"result": SUMMARY if request.url.params["currency"] == "BTC" else []})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_options_snapshot_keeps_the_rest_when_one_reading_fails():
    http = net.CollectHttp(transport=deribit_transport())
    data = options.snapshot(http, "BTC", now=NOW)
    assert data["index_price"] == 100_000 and data["dvol"] == {"value": 51.2, "at": base.iso_ms(MS - 3_600_000)}
    assert data["atm_iv_30d"]["iv_pct"] == 56.0 and data["errors"] == {}
    broken = options.snapshot(net.CollectHttp(transport=deribit_transport({"get_volatility_index_data"})), "BTC", now=NOW)
    assert broken["dvol"] is None and "dvol" in broken["errors"] and broken["index_price"] == 100_000
    eth = options.snapshot(http, "ETH", now=NOW)
    assert eth["instruments"] == 0 and eth["atm_iv_30d"] is None
    assert options.next_slot(NOW) == pd.Timestamp("2026-10-10 12:15:20", tz="UTC")


def test_options_source_runs_every_quarter_hour_with_a_fake_clock(settings):
    clock = FakeClock()
    http = net.CollectHttp(transport=deribit_transport())

    async def sleep(seconds):
        clock.advance(seconds)
        if clock.at >= NOW + timedelta(minutes=31):
            ctx.stop.set()

    ctx = context(settings, clock, http=http, sleep=sleep)
    asyncio.run(options.run(ctx))
    entries = list(Journal(base.journal_path(settings, base.OPTIONS, NOW)).entries({options.KIND}))
    assert [e["data"]["currency"] for e in entries] == ["BTC", "ETH", "BTC", "ETH"]
    assert entries[0]["data"]["time"] == "2026-10-10T12:15:20+00:00"


# --- attention ---------------------------------------------------------------------------------------------------
def post(ident, created, title, text="", author="pseudo_secret"):
    return {"kind": "t3", "data": {"name": f"t3_{ident}", "id": ident, "created_utc": created, "title": title,
                                   "selftext": text, "author": author, "url": "https://reddit.invalid/x"}}


def test_reddit_counts_mentions_as_whole_words_and_stores_no_content(settings):
    hour = pd.Timestamp("2026-10-10 11:00", tz="UTC")
    t = hour.timestamp()
    listing = {"data": {"children": [
        post("a", t + 10, "Bitcoin is pumping", "I bought $ETH too"),
        post("b", t + 20, "ETHEREUM gas fees", "Solana vs ethereum"),
        post("c", t + 30, "BTCs and ethers are not tickers", "linkage"),          # pas des mots entiers
        post("d", t + 3600, "too new: next hour", ""),
        post("e", t - 5, "too old: previous hour", ""),
        {"kind": "t3", "data": "pas un dict"},
    ]}}
    assets = attention.patterns(["BTC", "ETH", "SOL", "LINK"])
    seen: set[str] = set()
    counts = attention.count_posts(listing, hour_start=hour, assets=assets, seen=seen)
    assert counts["posts_in_hour"] == 3 and counts["posts_scanned"] == 6
    assert counts["mentions"] == {"BTC": 1, "ETH": 2, "SOL": 1, "LINK": 0}
    assert counts["hour_fully_covered"] is True and len(seen) == 3
    again = attention.count_posts(listing, hour_start=hour, assets=assets, seen=seen)
    assert again["posts_in_hour"] == 0                                              # déjà comptés
    serialized = json.dumps(counts) + json.dumps(sorted(seen))
    for secret in ("pumping", "pseudo_secret", "t3_a", "reddit.invalid", "gas fees"):
        assert secret not in serialized


def test_reddit_hour_spaces_requests_and_records_errors_per_subreddit(settings):
    hour = pd.Timestamp("2026-10-10 11:00", tz="UTC")
    hits, gaps = [], []

    def handler(request):
        hits.append((request.url.path, request.headers.get("User-Agent")))
        assert request.url.params["limit"] == "100"
        if "CryptoMarkets" in request.url.path:
            return httpx.Response(429, text="trop vite")
        return httpx.Response(200, json={"data": {"children": [post("z", hour.timestamp() + 1, "bitcoin")]}})

    http = net.CollectHttp(transport=httpx.MockTransport(handler))
    out = attention.reddit_hour(http, settings, hour_start=hour, seen=set(), sleep=gaps.append)
    assert [p for p, _ in hits] == ["/r/CryptoCurrency/new.json", "/r/Bitcoin/new.json", "/r/CryptoMarkets/new.json"]
    assert all(ua and "crypto-signal-intelligence" in ua for _, ua in hits)
    assert gaps == [attention.REQUEST_GAP_SECONDS, attention.REQUEST_GAP_SECONDS]
    assert out["posts_in_hour"] == 1 and out["mentions"]["BTC"] == 1 and out["content_stored"] is False
    assert "CryptoMarkets" in out["errors"] and "429" in out["errors"]["CryptoMarkets"]
    assert attention.next_hour_slot(NOW) == pd.Timestamp("2026-10-10 12:02", tz="UTC")


def test_google_trends_is_unavailable_and_reddit_source_runs_hourly(settings, monkeypatch):
    monkeypatch.setattr(attention, "REQUEST_GAP_SECONDS", 0.0)      # l'espacement réel de 2 s est testé plus haut
    clock = FakeClock()
    hits = []

    def handler(request):
        hits.append(request.url.path)
        return httpx.Response(200, json={"data": {"children": []}})

    async def sleep(seconds):
        clock.advance(seconds)
        if clock.at >= NOW + timedelta(minutes=65):
            ctx.stop.set()

    ctx = context(settings, clock, http=net.CollectHttp(transport=httpx.MockTransport(handler)), sleep=sleep)
    asyncio.run(attention.run(ctx))
    status = ctx.state.sources[base.ATTENTION]
    assert status["trends"] == base.UNAVAILABLE and "NON_DISPONIBLE" in status["detail"] and "pytrends" in status["detail"]
    assert not hasattr(attention, "trends_day")                        # aucun chemin de code pytrends
    entries = list(Journal(base.journal_path(settings, base.ATTENTION, NOW)).entries())
    assert [e["kind"] for e in entries] == [attention.REDDIT, attention.REDDIT] and len(hits) == 6
    assert entries[0]["data"]["hour"] == "2026-10-10T11:00:00+00:00"


# --- reconnexion, état, service ----------------------------------------------------------------------------------
def test_backoff_doubles_from_one_to_sixty_seconds_and_resets():
    delays = service.backoff_delays()
    seq = [next(delays)] + [delays.send(False) for _ in range(7)]
    assert seq == [1, 2, 4, 8, 16, 32, 60, 60]
    assert delays.send(True) == 1


def test_supervisor_reconnects_a_cut_stream_with_growing_waits(settings, monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_FIRST", 0.001)
    monkeypatch.setattr(service, "BACKOFF_MAX", 0.004)
    clock = FakeClock()
    stream = make_stream({"forceOrder": [liq("BTCUSDT", "SELL", 100_000, 2.0, T0 + 1_000)]}, fail_after=True)
    ctx = context(settings, clock, stream=stream)
    asyncio.run(service.supervise(base.LIQUIDATIONS, ctx, liquidations.run, max_runs=3))
    assert len(stream.calls) == 3
    status = ctx.state.sources[base.LIQUIDATIONS]
    assert status["reconnections"] == 3 and status["errors"] == 3 and status["status"] == base.RECONNECTING
    assert "connexion coupée" in status["last_error"] and status["next_retry_s"] == 0.004
    assert status["entries"] == 6                      # par connexion : un LIQ_GROS (≥ 100 000 USDT) et la LIQ_MINUTE close
    state = json.loads(base.state_path(settings).read_text(encoding="utf-8"))
    assert state["sources"]["LIQUIDATIONS"]["messages"] == 3 and state["places_orders"] is False


def test_stop_cancels_blocked_streams_and_waits_quickly(settings):
    """Flux fictif qui ne rend jamais rien + sources qui dorment : `stop` posé après 0,1 s → retour en < 1 s, ARRETE."""
    import time as real_time

    @asynccontextmanager
    async def frozen_stream(url):
        net.check_url(url)

        async def messages():
            await asyncio.Future()          # jamais de message
            yield {}
        yield messages()

    clock = FakeClock()
    ctx = context(settings, clock, stream=frozen_stream, http=net.CollectHttp(transport=deribit_transport()))

    async def scenario():
        asyncio.get_running_loop().call_later(0.1, ctx.stop.set)
        await service.run_all(ctx)

    started = real_time.monotonic()
    asyncio.run(scenario())
    assert real_time.monotonic() - started < 1.0
    state = base.read_state(settings)
    assert all(v["status"] == base.STOPPED for v in state["sources"].values())
    assert all(v["errors"] == 0 for v in state["sources"].values())


def test_pair_list_change_reloads_the_book_stream_without_counting_an_error(settings, monkeypatch):
    monkeypatch.setattr(carnet, "PAIRS_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(service, "BACKOFF_FIRST", 0.001)
    path = settings.root / "state" / "assistant.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    clock = FakeClock()
    calls = []

    @asynccontextmanager
    async def stream(url):
        calls.append(url)
        if len(calls) == 1:
            path.write_text(json.dumps({"active_calls": [{"symbol": "DOGEUSDT"}]}), encoding="utf-8")

        async def messages():
            for _ in range(3):
                clock.advance(1.0)
                yield depth("BTCUSDT")
        yield messages()

    ctx = context(settings, clock, stream=stream)
    asyncio.run(service.supervise(base.CARNET, ctx, carnet.run, max_runs=2))
    assert len(calls) == 2 and "dogeusdt@depth20" in calls[1] and "dogeusdt" not in calls[0]
    status = ctx.state.sources[base.CARNET]
    # 1re connexion : rechargement (pas une erreur, pas d'attente) ; 2e : flux fini normalement → une reconnexion comptée.
    assert status["errors"] == 1 and "flux terminé" in status["last_error"] and status["reconnections"] == 1


def test_recorder_survives_a_clock_going_backwards(settings):
    clock = FakeClock()
    ctx = context(settings, clock)
    first = ctx.recorder.append(base.FLUX, "X", {"a": 1}, now=clock())
    clock.advance(-30)
    second = ctx.recorder.append(base.FLUX, "X", {"a": 2}, now=clock())
    assert second["at"] == "2026-10-10T12:00:30.001000+00:00" and second["data"]["clock_adjusted"] is True
    assert second["data"]["clock_at"] == "2026-10-10T12:00:00+00:00" and second["prev"] == first["hash"]
    assert Journal(base.journal_path(settings, base.FLUX, NOW)).verify()["ok"]
    assert ctx.state.sources[base.FLUX]["entries"] == 2 and ctx.state.sources[base.FLUX]["errors"] == 0


def test_supervisor_marks_a_refused_address_unavailable_without_retrying(settings):
    clock = FakeClock()
    calls = []

    async def bad(ctx):
        calls.append(1)
        net.check_url("https://api.binance.com/api/v3/order")

    ctx = context(settings, clock)
    asyncio.run(service.supervise(base.OPTIONS, ctx, bad))
    assert calls == [1] and ctx.state.sources[base.OPTIONS]["status"] == base.UNAVAILABLE


def test_state_file_and_api_route(settings):
    clock = FakeClock()
    state = base.State(settings, clock=clock, monotonic=clock.monotonic, min_interval=10.0)
    assert state.write() and not state.write()              # au plus une écriture par intervalle
    state.message(base.FLUX)
    assert not state.write()
    clock.advance(11)
    assert state.write()
    payload = base.read_state(settings)
    assert payload["available"] and payload["sources"]["FLUX"]["messages"] == 1 and payload["written_at"] == "2026-10-10T12:00:41+00:00"
    api = CsiApi(settings, now=lambda: NOW)
    out = api.dispatch("GET", "/collecte", {}, None)
    assert out["available"] and out["sources"]["FLUX"]["status"] == base.IN_SERVICE
    assert out["journal_bytes_month"] == dict.fromkeys(base.SOURCES, 0) and out["places_orders"] is False
    assert "shadow" in out["note"]
    base.state_path(settings).unlink()
    assert api.dispatch("GET", "/collecte", {}, None)["available"] is False


def test_collecte_route_requires_the_token(settings):
    handler = make_handler(CsiApi(settings, now=lambda: NOW), token="jeton-de-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/collecte"
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(urllib.request.Request(url), timeout=10)
        assert refused.value.code == 401
        request = urllib.request.Request(url, headers={"Authorization": "Bearer jeton-de-test"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200 and json.loads(response.read())["available"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_service_writes_only_its_own_journals_and_state(settings, monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_FIRST", 0.001)
    root = settings.root
    untouchable = {root / "forward" / "F4_TELEGRAM.jsonl": b'{"seq":0}\n', root / "signals" / "shadow" / "x.txt": b"BUY",
                   root / "state" / "assistant.json": json.dumps({"active_calls": [{"symbol": "DOGEUSDT"}]}).encode(),
                   root / "state" / "run_status.json": b"{}"}
    for path, content in untouchable.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    clock = FakeClock()
    stream = make_stream({"forceOrder": [liq("BTCUSDT", "SELL", 100_000, 2.0, T0 + 1_000)],
                          "@aggTrade": [trade("BTCUSDT", 100_000, 1, T0 + 100, maker_is_buyer=False)],
                          "@depth20": [depth("DOGEUSDT")]}, clock=clock, step=6.0)
    ctx = context(settings, clock, stream=stream)
    asyncio.run(service.run_all(ctx, sources=(base.LIQUIDATIONS, base.FLUX, base.CARNET), max_runs=1))
    for path, content in untouchable.items():
        assert path.read_bytes() == content
    assert any("dogeusdt@depth20" in url for url in stream.calls)     # appel actif de l'assistant suivi (lecture seule)
    written = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
                     and p not in untouchable)
    assert written == ["forward/C_CARNET-2026-10.jsonl", "forward/C_CARNET-2026-10.jsonl.lock",
                       "forward/C_FLUX-2026-10.jsonl", "forward/C_FLUX-2026-10.jsonl.lock",
                       "forward/C_LIQUIDATIONS-2026-10.jsonl", "forward/C_LIQUIDATIONS-2026-10.jsonl.lock",
                       "state/C_ETAT.json"]
    for source in (base.LIQUIDATIONS, base.FLUX, base.CARNET):
        assert Journal(base.journal_path(settings, source, NOW)).verify()["ok"]
        assert ctx.state.sources[source]["status"] == base.STOPPED
    assert ctx.state.sources[base.OPTIONS]["status"] == base.STARTING      # non lancée ici
    state = base.read_state(settings)
    assert state["sources"]["FLUX"]["entries"] == 2 and state["sources"]["CARNET"]["entries"] == 1   # FLUX_GROS + FLUX_MINUTE


def test_journal_names_rotate_by_month(settings):
    october = base.journal_path(settings, base.FLUX, NOW)
    november = base.journal_path(settings, base.FLUX, NOW + timedelta(days=25))
    assert october.name == "C_FLUX-2026-10.jsonl" and november.name == "C_FLUX-2026-11.jsonl"
    assert october.parent == settings.root / "forward"
    with pytest.raises(ValueError):
        base.journal_path(settings, "F4_TELEGRAM", NOW)


def test_collecteur_health_command_checks_the_state_freshness(settings, monkeypatch):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    runner = CliRunner()
    assert runner.invoke(cli.app, ["collecteur-health"]).exit_code == 1
    clock = FakeClock(datetime.now(UTC) - timedelta(seconds=100))
    base.State(settings, clock=clock, monotonic=clock.monotonic).write(force=True)
    assert runner.invoke(cli.app, ["collecteur-health", "--max-age", "300"]).exit_code == 0
    assert runner.invoke(cli.app, ["collecteur-health", "--max-age", "60"]).exit_code == 1
    dead = base.State(settings, clock=clock, monotonic=clock.monotonic)
    for name in base.SOURCES:
        dead.touch(name, status=base.UNAVAILABLE)
    dead.write(force=True)
    assert runner.invoke(cli.app, ["collecteur-health", "--max-age", "300"]).exit_code == 1    # plus aucune source vivante
