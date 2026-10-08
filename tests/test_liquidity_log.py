"""Relevé de liquidité en shadow (forward/liquidity_log.py, docs/LIQUIDITE.md). Carnets et klines FACTICES, aucun
réseau : clients de la liste blanche branchés sur un transport httpx simulé."""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi
from crypto_signal_intelligence.data.http import DEPTH_ALLOWED_PATHS, REST_ALLOWED_PATHS, PublicHttpClient
from crypto_signal_intelligence.forward import liquidity_log as liq
from crypto_signal_intelligence.forward import telegram_live
from crypto_signal_intelligence.forward.liquidity_log import start_background as REAL_START

NOW = datetime(2026, 10, 8, 12, 6, tzinfo=UTC)          # cycle de 12:00 dû (5 min après le quart d'heure)
BOOK = {"bids": [["99.9", "10"], ["99.5", "20"], ["99.0", "30"], ["98.5", "40"]],
        "asks": [["100.1", "5"], ["100.4", "10"], ["101.0", "20"], ["103.0", "100"]]}
SIGNAL_TEXT = "👑 WHALE HUNTING\nPAIR: DOGE/USDT\nENTRY 1: 0.2\nT1: 0.22\nSL: 0.18"


# --- carnet ------------------------------------------------------------------------------------------------
def test_book_metrics_spread_depth_and_imbalance():
    m = liq.book_metrics(BOOK)
    assert m["bid"] == 99.9 and m["ask"] == 100.1
    assert m["spread_pct"] == pytest.approx(0.2)
    half, one, two = m["depth"]["0.5"], m["depth"]["1"], m["depth"]["2"]
    assert (half["bid_usdt"], half["ask_usdt"]) == (2989, 1504)       # 99,9×10 + 99,5×20 ; 100,1×5 + 100,4×10 (USDT entiers)
    assert half["imbalance"] == pytest.approx((2989 - 1504.5) / (2989 + 1504.5), abs=1e-4)
    assert (one["bid_usdt"], one["ask_usdt"]) == (5959, 3524)
    assert (two["bid_usdt"], two["ask_usdt"]) == (9899, 3524)
    assert not half["truncated"] and not one["truncated"]
    assert two["truncated"]                         # achats lus jusqu'à 98,5 seulement : borne 98 non atteinte
    assert liq.CURVE[-1] == 0.02 and m["curve_bid_usdt"][-1] == 9899 and m["curve_ask_usdt"][0] == 0


def test_market_slippage_buy_and_sell():
    m = liq.book_metrics(BOOK)
    buy, sell = m["slippage"]["buy"], m["slippage"]["sell"]
    assert buy["100"] == 0.0 and sell["500"] == 0.0
    base = 5 + 10 + (2000 - 500.5 - 1004) / 101.0                # 2 000 USDT : trois niveaux de vente
    assert buy["2000"] == pytest.approx((2000 / base / 100.1 - 1) * 100, abs=1e-5)
    # Vente de 2 000 USDT au milieu = 20 unités : 10 à 99,9 puis 10 à 99,5, prix moyen 99,7.
    assert sell["2000"] == pytest.approx((1 - 99.7 / 99.9) * 100, abs=1e-5)
    too_big = liq.market_buy(liq._levels(BOOK["asks"], descending=False), 1e6)
    assert too_big["filled"] is False and too_big["slip_pct"] is None


def test_empty_or_crossed_book_is_refused():
    with pytest.raises(ValueError):
        liq.book_metrics({"bids": [], "asks": [["1", "1"]]})
    with pytest.raises(ValueError):
        liq.book_metrics({"bids": [["2", "1"]], "asks": [["1", "1"]]})


def test_entry_check_before_entering():
    m = liq.book_metrics(BOOK)
    assert liq.entry_check(m, 500)["status"] == liq.SUFFICIENT
    big = liq.entry_check(m, 2000)
    assert big["status"] == liq.INSUFFICIENT and any("achat" in r for r in big["reasons"])
    assert big["buy_slip_pct"] > 0.2 and big["sell_slip_pct"] > 0.2
    prudent = liq.entry_check(m, 1000)          # pas de relevé à 1 000 : estimé avec celui de 2 000 (prudent)
    assert prudent["evaluated_size"] == 2000 and prudent["status"] == liq.INSUFFICIENT
    exact = liq.entry_check_book(BOOK, 1000)    # sur le carnet brut : exact, glissement d'achat ≈ 0,15 %
    assert exact["status"] == liq.SUFFICIENT and exact["buy_slip_pct"] == pytest.approx(0.1496, abs=1e-3)
    assert liq.entry_check(m, 5000)["status"] == liq.UNKNOWN
    wide = {"bids": [["99", "1000"]], "asks": [["100", "1000"]]}           # écart ≈ 1 %
    check = liq.entry_check_book(wide, 100)
    assert check["status"] == liq.INSUFFICIENT and any("écart" in r for r in check["reasons"])
    thin = {"bids": [["99.99", "1"]], "asks": [["100.01", "1"]]}          # 100 USDT de chaque côté seulement
    assert any("profondeur lue insuffisante" in r for r in liq.entry_check_book(thin, 500)["reasons"])
    with pytest.raises(ValueError):
        liq.entry_check(m, 0)


# --- flux --------------------------------------------------------------------------------------------------
def klines(now: datetime, n: int = 200) -> list[list]:
    """`n` bougies 1 min, la dernière EN COURS à `now` (volume énorme : elle ne doit pas compter)."""
    start = pd.Timestamp(now).floor("min") - pd.Timedelta(minutes=n - 1)
    rows = []
    for i in range(n):
        open_ms = int((start + pd.Timedelta(minutes=i)).timestamp() * 1000)
        recent = i >= n - 61
        quote = 1e9 if i == n - 1 else (30.0 if recent else 10.0)
        taker = 0.75 if i >= n - 16 else 0.25
        close = 100.0 + i * 0.01
        rows.append([open_ms, str(close), str(close), str(close), str(close), "1", open_ms + 59_999, str(quote), 5,
                     "0.5", str(quote * taker), "0"])
    return rows


def test_flow_from_one_minute_klines_uses_closed_bars_only():
    flow = liq.flow_metrics(klines(NOW), now=NOW)
    assert flow["bars"] == 199 and flow["gaps"] == 0 and flow["baseline_minutes"] == 139
    f15, f60 = flow["15m"], flow["60m"]
    assert f15["taker_buy_share"] == 0.75 and f15["trades"] == 75 and f15["quote_volume"] == 450.0
    assert f60["taker_buy_share"] == pytest.approx((15 * 22.5 + 45 * 7.5) / 1800)
    assert f15["rel_volume"] == 3.0 and f60["rel_volume"] == 3.0
    assert f15["return_pct"] == pytest.approx((101.98 / 101.83 - 1) * 100, abs=1e-3)
    assert liq.flow_metrics([], now=NOW)["bars"] == 0
    short = liq.flow_metrics(klines(NOW, 30), now=NOW)
    assert short["60m"] is None and short["15m"]["rel_volume"] is None          # référence trop courte


# --- relevé ------------------------------------------------------------------------------------------------
class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self.now.timestamp()


class Binance:
    """Transport simulé : carnet et klines, ou panne. Note l'heure de chaque demande."""

    def __init__(self, clock: FakeClock, failing: set[str] | None = None, status: int | None = None):
        self.clock, self.failing, self.status, self.calls = clock, failing or set(), status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        symbol = request.url.params.get("symbol")
        self.calls.append((request.url.path, symbol, self.clock.now))
        self.clock.now += timedelta(milliseconds=200)
        if symbol in self.failing or "*" in self.failing:
            if self.status is None:
                raise httpx.ConnectError("injoignable")
            return httpx.Response(self.status, json={"code": -1, "msg": "refus"})
        if request.url.path == "/api/v3/depth":
            return httpx.Response(200, json=BOOK)
        return httpx.Response(200, json=klines(self.clock.now))


def worker_for(settings, clock: FakeClock, binance: Binance, pairs=("BTCUSDT", "ETHUSDT")) -> liq.LiquidityWorker:
    transport = httpx.MockTransport(binance)
    base = settings.data.rest_base_url
    depth = PublicHttpClient.depth(base, transport=transport, retries=1, sleep=clock.sleep)
    rest = PublicHttpClient.rest(base, transport=transport, retries=1, sleep=clock.sleep)
    return liq.LiquidityWorker(settings, clock=clock, depth=depth, rest=rest, monotonic=clock.monotonic,
                               sleep=clock.sleep, pairs=lambda s: list(pairs))


def drain(worker: liq.LiquidityWorker, limit: int = 200) -> int:
    done = 0
    while done < limit and worker.step():
        done += 1
    return done


def files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_periodic_cycle_records_each_pair_with_bounded_rate_and_writes_only_its_journals(settings):
    clock = FakeClock(NOW)
    binance = Binance(clock)
    before = files(settings.root)
    worker = worker_for(settings, clock, binance)
    assert drain(worker) == 2
    journal = liq.periodic_journal(settings, NOW)
    cycle = [e["data"] for e in journal.entries({liq.CYCLE})]
    assert cycle == [{"cycle": "2026-10-08T12:00:00+00:00", "pairs": ["BTCUSDT", "ETHUSDT"], "skipped": [],
                      "max_pairs": liq.MAX_PAIRS}]
    records = [e["data"] for e in journal.entries({liq.RECORD})]
    assert [r["symbol"] for r in records] == ["BTCUSDT", "ETHUSDT"]
    assert records[0]["trigger"] == liq.PERIODIC and records[0]["book"]["spread_pct"] == pytest.approx(0.2)
    assert records[0]["flow"]["15m"]["taker_buy_share"] == 0.75 and journal.verify()["ok"]
    # Débit : 4 demandes (carnet + klines par paire), espacées d'au moins REQUEST_INTERVAL secondes.
    assert [c[0] for c in binance.calls] == ["/api/v3/depth", "/api/v3/klines"] * 2
    gaps = [(b[2] - a[2]).total_seconds() for a, b in zip(binance.calls, binance.calls[1:], strict=False)]
    assert min(gaps) >= liq.REQUEST_INTERVAL and 60 / liq.REQUEST_INTERVAL <= 30
    # Aucune écriture hors des journaux du relevé (et de leurs verrous).
    created = files(settings.root) - before
    assert created and all(Path(f).name.startswith("F0_LIQUIDITE") and f.startswith("forward/") for f in created)
    assert drain(worker) == 0                       # cycle fait : rien avant le quart d'heure suivant
    clock.now = NOW + timedelta(minutes=15)
    assert drain(worker) == 2


def test_pairs_per_cycle_are_capped(settings):
    clock = FakeClock(NOW)
    pairs = [f"P{i}USDT" for i in range(liq.MAX_PAIRS + 10)]
    worker = worker_for(settings, clock, Binance(clock), pairs=pairs)
    worker.enqueue_cycle(pd.Timestamp("2026-10-08T12:00Z"), NOW)
    cycle = next(liq.periodic_journal(settings, NOW).entries({liq.CYCLE}))["data"]
    assert len(cycle["pairs"]) == liq.MAX_PAIRS and len(cycle["skipped"]) == 10 and len(worker.queue) == liq.MAX_PAIRS


def test_rate_limiter_spaces_requests():
    clock = FakeClock(NOW)
    limiter = liq.RateLimiter(2.0, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(5):
        limiter.wait()
    assert limiter.calls == 5 and clock.now - NOW == timedelta(seconds=8)


def drop(settings, rows, now=NOW):
    return telegram_live.store_drop(settings, rows, now=now)


def test_a_telegram_signal_is_recorded_first_and_only_once(settings):
    clock = FakeClock(NOW)
    binance = Binance(clock)
    drop(settings, [
        {"signal_id": "c:1", "source_chat_id": "-100", "raw_text": SIGNAL_TEXT, "received_at": "2026-10-08T12:05:50Z"},
        {"signal_id": "c:2", "source_chat_id": "-100", "raw_text": "TP1 atteint ✅", "received_at": "2026-10-08T12:05:51Z"},
        {"signal_id": "c:3", "source_chat_id": "-100", "raw_text": SIGNAL_TEXT, "received_at": "2026-10-08T10:00:00Z"},
    ])
    worker = worker_for(settings, clock, binance)
    assert worker.step()
    assert binance.calls[0][1] == "DOGEUSDT"           # avant les paires du cycle périodique
    drain(worker)
    signals = [e["data"] for e in liq.signals_journal(settings).entries({liq.RECORD})]
    assert len(signals) == 1
    record = signals[0]
    assert record["signal_id"] == "robot:c:1" and record["symbol"] == "DOGEUSDT" and record["trigger"] == liq.SIGNAL
    assert record["received_at"].startswith("2026-10-08T12:05:50") and 0 < record["delay_s"] < 60
    assert record["book"]["depth"]["1"]["imbalance"] is not None and record["flow"]["60m"]["trades"] == 300
    # Redémarrage : le signal déjà relevé ne l'est pas une deuxième fois.
    again = worker_for(settings, clock, binance)
    drain(again)
    assert len(list(liq.signals_journal(settings).entries({liq.RECORD}))) == 1


def test_a_signal_that_cannot_be_measured_in_time_is_marked_missed(settings):
    clock = FakeClock(NOW)
    drop(settings, [{"signal_id": "c:9", "source_chat_id": "-1", "raw_text": SIGNAL_TEXT,
                     "received_at": "2026-10-08T12:05:00Z"}])
    worker = worker_for(settings, clock, Binance(clock))
    worker.queue.extend(worker.signal_tasks(clock()))
    clock.now += timedelta(minutes=40)
    worker.run_task(worker.queue.popleft())
    missed = [e["data"] for e in liq.signals_journal(settings).entries({liq.MISSED})]
    assert missed and missed[0]["signal_id"] == "robot:c:9"


def test_binance_outage_is_logged_and_abandons_the_cycle_without_raising(settings):
    clock = FakeClock(NOW)
    pairs = [f"P{i}USDT" for i in range(6)]
    worker = worker_for(settings, clock, Binance(clock, failing={"*"}), pairs=pairs)
    assert drain(worker) == liq.FAILURES_TO_ABANDON
    journal = liq.periodic_journal(settings, NOW)
    errors = [e["data"] for e in journal.entries({liq.ERROR})]
    assert len(errors) == liq.FAILURES_TO_ABANDON and errors[0]["status"] is None
    missed = next(journal.entries({liq.MISSED}))["data"]
    assert missed["pairs"] == pairs[liq.FAILURES_TO_ABANDON:] and "injoignable" in missed["reason"]
    assert worker.step() is False                   # en retrait, aucune demande
    assert not list(journal.entries({liq.RECORD})) and journal.verify()["ok"]


def test_rate_limit_answer_suspends_the_log(settings):
    clock = FakeClock(NOW)
    binance = Binance(clock, failing={"*"}, status=429)
    worker = worker_for(settings, clock, binance, pairs=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert drain(worker) == 1
    calls = len(binance.calls)
    clock.now += timedelta(seconds=liq.BAN_BACKOFF_SECONDS - 10)
    assert worker.step() is False and len(binance.calls) == calls
    error = next(liq.periodic_journal(settings, NOW).entries({liq.ERROR}))["data"]
    assert error["status"] == 429


def test_an_unknown_pair_does_not_stop_the_others(settings):
    clock = FakeClock(NOW)
    worker = worker_for(settings, clock, Binance(clock, failing={"NOPEUSDT"}, status=400),
                        pairs=["NOPEUSDT", "BTCUSDT"])
    assert drain(worker) == 2
    journal = liq.periodic_journal(settings, NOW)
    assert [e["data"]["symbol"] for e in journal.entries({liq.RECORD})] == ["BTCUSDT"]
    assert next(journal.entries({liq.ERROR}))["data"]["status"] == 400


def test_only_whitelisted_public_paths_are_used(settings):
    assert liq.DEPTH_PATH in DEPTH_ALLOWED_PATHS and liq.KLINES_PATH in REST_ALLOWED_PATHS
    worker = liq.LiquidityWorker(settings, clock=lambda: NOW, pairs=lambda s: [])
    try:
        assert worker.depth.allowed_paths == DEPTH_ALLOWED_PATHS and worker.rest.allowed_paths == REST_ALLOWED_PATHS
    finally:
        worker.close()


def test_the_log_has_no_influence_on_tests_advice_or_bsm():
    """Aucun module de décision (tests en direct, avis, signaux, BSM) ne lit le relevé."""
    src = Path(liq.__file__).resolve().parents[1]
    readers = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py")
                     if "import liquidity_log" in (text := p.read_text(encoding="utf-8")) or "liquidity_log import" in text)
    assert readers == ["api/server.py", "live/scanner.py"]


# --- fil et surveillance ------------------------------------------------------------------------------------
def test_background_thread_starts_once_and_can_be_disabled(settings, monkeypatch):
    monkeypatch.setattr(liq, "start_background", REAL_START)
    started = []

    class Idle:
        def __init__(self, settings_, *, clock):
            started.append(clock)

        def run(self, stop: threading.Event) -> None:
            stop.wait(5)

    monkeypatch.setattr(liq, "LiquidityWorker", Idle)
    monkeypatch.setattr(liq, "_STATE", {})
    settings.forward.liquidity_log = False
    assert liq.start_background(settings, clock=lambda: NOW) is False and started == []
    settings.forward.liquidity_log = True
    assert liq.start_background(settings, clock=lambda: NOW) is True
    assert liq.start_background(settings, clock=lambda: NOW) is False and len(started) == 1
    liq._STATE["stop"].set()
    liq._STATE["thread"].join(5)


def test_the_monitor_worker_starts_the_log(settings, monkeypatch):
    from crypto_signal_intelligence.live.scanner import UserPairWorker
    calls = []
    monkeypatch.setattr(liq, "start_background", lambda settings_, *, clock: calls.append(clock) or True)
    UserPairWorker(settings, downloader=lambda *a, **k: None, clock=lambda: NOW).run_once()
    assert len(calls) == 1


def test_the_worker_loop_survives_unexpected_errors(settings):
    worker = liq.LiquidityWorker(settings, clock=lambda: NOW, pairs=lambda s: [])
    stop = threading.Event()

    def boom():
        stop.set()
        raise OSError("disque plein")

    worker.step = boom  # type: ignore[method-assign]
    worker.run(stop)                                  # rend la main sans lever
    worker.close()


# --- API ---------------------------------------------------------------------------------------------------
def test_liquidity_route(settings):
    api = CsiApi(settings, now=lambda: NOW)
    empty = api.dispatch("GET", "/liquidity", {}, None)
    assert empty["signals"] == [] and empty["pairs"] == [] and empty["size_usdt"] == 500
    clock = FakeClock(NOW)
    drop(settings, [{"signal_id": "c:1", "source_chat_id": "-100", "raw_text": SIGNAL_TEXT,
                     "received_at": "2026-10-08T12:05:50Z"}])
    drain(worker_for(settings, clock, Binance(clock)))
    out = CsiApi(settings, now=lambda: clock.now).dispatch("GET", "/liquidity", {"size": ["500"], "limit": ["5"]}, None)
    assert [s["symbol"] for s in out["signals"]] == ["DOGEUSDT"] and out["counts"]["signals"] == 1
    signal = out["signals"][0]
    assert signal["provider"] and signal["check"]["status"] == liq.SUFFICIENT
    assert signal["taker_buy_share"]["15m"] == 0.75 and signal["depth"]["1"]["bid_usdt"] == 5959.0
    assert [p["symbol"] for p in out["pairs"]] == ["BTCUSDT", "ETHUSDT"] and out["last_cycle"].startswith("2026-10-08T12:00")
    big = CsiApi(settings, now=lambda: clock.now).dispatch("GET", "/liquidity", {"size": ["2000"]}, None)
    assert big["signals"][0]["check"]["status"] == liq.INSUFFICIENT
    for bad in ({"size": ["abc"]}, {"size": ["-5"]}, {"size": ["nan"]}, {"limit": ["0"]}):
        with pytest.raises(ApiError):
            api.dispatch("GET", "/liquidity", bad, None)
