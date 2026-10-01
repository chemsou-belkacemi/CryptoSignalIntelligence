"""Surveillance continue (fixtures SYNTHÉTIQUES, horloge simulée, aucun réseau)."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.live.lock import InstanceAlreadyRunning, InstanceLock
from crypto_signal_intelligence.live.scanner import last_close, next_close, run_forever, scan_cycle

from .conftest import canonical

DECISION = datetime(2024, 2, 1, 6, 0, tzinfo=UTC)          # clôture 15m ET 1h
SETUP_BARS = 31 * 96 + 24                                   # jusqu'à 06:00
CONTEXT_BARS = 31 * 24 + 6


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def store_candles(settings, *, setup_bars=SETUP_BARS, context_bars=CONTEXT_BARS, btc_bars=CONTEXT_BARS):
    store = CandleStore(settings.data_dir)
    store.save(canonical(setup_bars, "15m", symbol="ETHUSDT", start="2024-01-01", seed=1), "ETHUSDT", "15m")
    store.save(canonical(context_bars, "1h", symbol="ETHUSDT", start="2024-01-01", seed=2), "ETHUSDT", "1h")
    store.save(canonical(btc_bars, "1h", symbol="BTCUSDT", start="2024-01-01", seed=3), "BTCUSDT", "1h")


def no_download(*args, **kwargs):
    return None


def test_candle_close_arithmetic():
    step = timedelta(minutes=15)
    assert last_close(datetime(2024, 2, 1, 6, 7, tzinfo=UTC), step) == DECISION
    assert last_close(DECISION, step) == DECISION
    assert next_close(DECISION, step) == DECISION + step


def test_only_one_instance_can_hold_the_lock(tmp_path):
    path = tmp_path / "state" / "run.lock"
    with InstanceLock(path), pytest.raises(InstanceAlreadyRunning, match="pid="):
        InstanceLock(path).acquire()
    with InstanceLock(path):          # libéré à la sortie : réacquisition possible
        pass


def test_cycle_analyses_the_expected_close_for_every_strategy(settings):
    store_candles(settings)
    clock = FakeClock(DECISION + timedelta(seconds=20))
    report = scan_cycle(settings, now=clock(), symbols=["ETHUSDT"], downloader=no_download, clock=clock,
                        sleep=clock.sleep)
    assert report.decision_close == DECISION and not report.errors and not report.missing_after_wait
    assert {o.strategy for o in report.outcomes} == {"DONCHIAN_VOLUME_BREAKOUT", "EMA_PULLBACK_CONTINUATION",
                                                     "RANGE_REENTRY"}
    assert all(o.decision_time == DECISION for o in report.outcomes)
    assert {"data_s", "analysis_s", "publication_delay_s"} <= set(report.timings)


def test_missing_hourly_context_is_awaited_then_blocks_the_pair(settings):
    """Clôture simultanée 15m/1h : la bougie 1h de 06:00 manque → attente bornée, puis aucune analyse."""
    store_candles(settings, context_bars=CONTEXT_BARS - 1)
    clock = FakeClock(DECISION + timedelta(seconds=20))
    calls = []
    report = scan_cycle(settings, now=clock(), symbols=["ETHUSDT"], clock=clock, sleep=clock.sleep,
                        downloader=lambda *a, **k: calls.append((a[1], a[2], k["rest_only"])))
    assert report.missing_after_wait == ["ETHUSDT 1h"]
    assert {o.reason_code for o in report.outcomes} == {"REQUIRED_CONTEXT_UNAVAILABLE"}
    waited = (clock() - DECISION).total_seconds() - 20
    assert settings.live.candle_wait_seconds <= waited <= settings.live.candle_wait_seconds + settings.live.retry_seconds
    assert all(rest_only for *_, rest_only in calls) and ("ETHUSDT", "1h", True) in calls
    assert len({c for c in calls if c[:2] == ("ETHUSDT", "15m")}) == 1   # déjà présente : demandée une fois


def test_missing_setup_candle_is_never_replaced_by_the_previous_one(settings):
    store_candles(settings, setup_bars=SETUP_BARS - 1)
    clock = FakeClock(DECISION + timedelta(seconds=20))
    report = scan_cycle(settings, now=clock(), symbols=["ETHUSDT"], downloader=no_download, clock=clock,
                        sleep=clock.sleep)
    assert report.missing_after_wait == ["ETHUSDT 15m"]
    assert {o.reason_code for o in report.outcomes} == {"STALE_DATA"}


def test_run_forever_waits_for_closes_writes_status_and_releases_the_lock(settings):
    store_candles(settings)
    clock = FakeClock(DECISION - timedelta(minutes=3))
    seen = []

    news_calls = []
    cycles = run_forever(settings, clock=clock, sleep=clock.sleep, max_cycles=1, downloader=no_download,
                         on_cycle=seen.append, news_collector=lambda s, now: news_calls.append(now))
    assert cycles == 1 and seen[0].decision_close == DECISION
    assert clock() >= DECISION + timedelta(seconds=settings.live.grace_seconds)
    status = json.loads((settings.root / settings.live.status_file).read_text(encoding="utf-8"))
    assert status["cycles"] == 1 and status["last_cycle"]["decision_close"].startswith("2024-02-01 06:00")
    assert len(news_calls) == 1 and news_calls[0] >= seen[0].finished_at      # news APRÈS l'analyse
    with InstanceLock(settings.root / settings.live.lock_file):
        pass


def test_restart_never_republishes_an_old_setup(settings, monkeypatch):
    """Même bougie analysée deux fois (redémarrage) : un seul fichier, jamais un nouvel identifiant."""
    from crypto_signal_intelligence.domain.enums import Action, EntryMode
    from crypto_signal_intelligence.domain.market import EntryIntent, StrategyResult
    from crypto_signal_intelligence.strategies.donchian import DonchianVolumeBreakout

    def always_buy(self, context):
        close = float(context.setup["close"])
        return StrategyResult(strategy_id=self.strategy_id, strategy_version=1, action=Action.BUY,
                              setup_time=context.decision_time, regime=context.regime,
                              entry_intent=EntryIntent(EntryMode.LIMIT, close, 10, 2),
                              invalidation_reference=close * 0.98, exit_policy_id="FIXED_SL_ONE_TP_V1", target_r=2.0)

    monkeypatch.setattr(DonchianVolumeBreakout, "evaluate", always_buy)
    store_candles(settings)
    clock = FakeClock(DECISION + timedelta(seconds=20))
    kwargs = dict(symbols=["ETHUSDT"], strategies=["DONCHIAN_VOLUME_BREAKOUT"], downloader=no_download, clock=clock,
                  sleep=clock.sleep)
    first = scan_cycle(settings, now=clock(), **kwargs)
    assert [o.publication_status for o in first.outcomes] == ["PUBLISHED"], first.outcomes
    clock.now += timedelta(minutes=2)                          # redémarrage avant la clôture suivante
    second = scan_cycle(settings, now=clock(), **kwargs)
    assert [(o.publication_status, o.signal_id) for o in second.outcomes] == [("DUPLICATE", first.outcomes[0].signal_id)]
    assert len(list(settings.publication_dir().glob("*.txt"))) == 1
    clock.now = DECISION + timedelta(minutes=40)               # fenêtre d'entrée close : rien de périmé
    late = scan_cycle(settings, now=clock(), decision_close=DECISION, **kwargs)
    assert late.outcomes[0].signal_id is None and late.outcomes[0].reason_code in {"EXPIRED", "STALE_DATA"}


def test_live_window_gives_the_same_decision_as_full_history(settings):
    """Point 3 : les dernières bougies suffisent — mêmes indicateurs (à 1e-6 près) et même décision."""
    import numpy as np
    import pandas as pd

    from crypto_signal_intelligence.features.context import iter_contexts
    from crypto_signal_intelligence.features.loader import decision_frame, load_inputs
    from crypto_signal_intelligence.strategies import registry
    store = CandleStore(settings.data_dir)
    store.save(canonical(9000, "15m", symbol="ETHUSDT", start="2023-11-01", seed=4), "ETHUSDT", "15m")
    store.save(canonical(2300, "1h", symbol="ETHUSDT", start="2023-11-01", seed=5), "ETHUSDT", "1h")
    store.save(canonical(2300, "1h", symbol="BTCUSDT", start="2023-11-01", seed=6), "BTCUSDT", "1h")
    for strategy_id in registry.STRATEGIES:
        strategy = registry.build(strategy_id, settings.strategies)
        full = decision_frame(settings, strategy, load_inputs(settings, "ETHUSDT")).tail(1).reset_index(drop=True)
        live = decision_frame(settings, strategy, load_inputs(
            settings, "ETHUSDT", setup_tail=settings.live.setup_tail_bars,
            context_tail=settings.live.context_tail_bars)).tail(1).reset_index(drop=True)
        numeric = [c for c in full.columns if c in live.columns and pd.api.types.is_numeric_dtype(full[c]) and not pd.api.types.is_bool_dtype(full[c])
                   and c != "bars_available"]
        assert np.allclose(full[numeric].to_numpy(float), live[numeric].to_numpy(float), rtol=1e-6, atol=1e-9,
                           equal_nan=True), strategy_id
        decide = [strategy.evaluate(next(iter_contexts(frame, "ETHUSDT", "15m", strategy.setup_keys))[1])
                  for frame in (full, live)]
        assert (decide[0].action, decide[0].no_trade_reason) == (decide[1].action, decide[1].no_trade_reason)


def test_heavy_jobs_can_lower_their_own_priority():
    import subprocess
    import sys
    code = "from crypto_signal_intelligence.live.priority import lower_priority; print(lower_priority())"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.strip() == "True"


@pytest.mark.skipif(sys.platform != "win32", reason="plafond mémoire par Job Object : Windows seulement")
def test_heavy_jobs_get_a_memory_ceiling_that_fails_cleanly():
    """Au-delà du plafond, l'allocation échoue proprement (MemoryError) au lieu d'épuiser la machine."""
    import subprocess
    code = "\n".join([
        "import sys",
        "from crypto_signal_intelligence.live.priority import heavy_job",
        "print(heavy_job(150))",
        "try:",
        "    block = bytearray(400 * 1024 * 1024)",
        "except MemoryError:",
        "    print('refus')",
        "    sys.exit(3)",
        "print('alloué')",
    ])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert "'memory_limited': True" in out.stdout and "refus" in out.stdout and out.returncode == 3
    ok = subprocess.run([sys.executable, "-c", code.replace("400 * 1024", "20 * 1024")], capture_output=True,
                        text=True, timeout=120)
    assert ok.returncode == 0 and "alloué" in ok.stdout          # sous le plafond : rien ne change


def test_health_distinguishes_started_ready_stale_and_degraded(settings):
    from crypto_signal_intelligence.live.health import check
    from crypto_signal_intelligence.live.scanner import CycleReport, write_status
    assert not check(settings, now=DECISION).ready                      # démarré, aucun cycle : pas prêt
    report = CycleReport(decision_close=DECISION, started_at=DECISION, finished_at=DECISION + timedelta(seconds=50))
    path = settings.root / settings.live.status_file
    write_status(path, report, cycles=1, started_at=DECISION)
    fresh = check(settings, now=DECISION + timedelta(minutes=5))
    assert fresh.ready and not fresh.degraded
    assert not check(settings, now=DECISION + timedelta(minutes=40)).ready   # boucle figée
    report.missing_after_wait.append("ETHUSDT 1h")
    write_status(path, report, cycles=2, started_at=DECISION)
    degraded = check(settings, now=DECISION + timedelta(minutes=5))
    assert degraded.ready and degraded.degraded and "ETHUSDT 1h" in degraded.detail


def test_run_stops_cleanly_when_asked(settings):
    store_candles(settings)
    clock = FakeClock(DECISION - timedelta(minutes=3))
    requests = iter([False, False, True])
    cycles = run_forever(settings, clock=clock, sleep=clock.sleep, downloader=no_download,
                         should_stop=lambda: next(requests, True), news_collector=lambda s, now: None)
    assert cycles == 0
    with InstanceLock(settings.root / settings.live.lock_file):   # verrou rendu à l'arrêt
        pass


def test_network_outage_is_retried_then_caught_up_without_stale_signals(settings):
    """Point 5 : coupure réseau pendant l'attente bornée, puis rattrapage au cycle suivant."""
    import httpx
    store_candles(settings, setup_bars=SETUP_BARS - 1, context_bars=CONTEXT_BARS - 1, btc_bars=CONTEXT_BARS - 1)
    clock = FakeClock(DECISION + timedelta(seconds=20))
    outage = {"on": True}
    calls = []

    def flaky(settings_, symbol, timeframe, *, now, rest_only, **kwargs):
        calls.append((symbol, timeframe))
        if outage["on"]:
            raise httpx.ConnectError("réseau coupé")
        store_candles(settings_)                                  # la connexion revient : bougies rattrapées

    first = scan_cycle(settings, now=clock(), symbols=["ETHUSDT"], downloader=flaky, clock=clock, sleep=clock.sleep)
    assert first.errors and all("ConnectError" in e for e in first.errors)
    assert {o.reason_code for o in first.outcomes} == {"REQUIRED_CONTEXT_UNAVAILABLE"}   # rien de publié
    assert len(calls) > 3                                          # réessayé pendant l'attente bornée
    outage["on"] = False
    clock.now += timedelta(minutes=1)
    second = scan_cycle(settings, now=clock(), decision_close=DECISION, symbols=["ETHUSDT"], downloader=flaky,
                        clock=clock, sleep=clock.sleep)
    assert not second.missing_after_wait and all(o.decision_time == DECISION for o in second.outcomes)


def test_dashboard_is_readable_and_escapes_untrusted_text(settings):
    from crypto_signal_intelligence.news.parse import RawItem
    from crypto_signal_intelligence.news.store import NewsStore
    from crypto_signal_intelligence.reporting.dashboard import write_dashboard
    NewsStore(settings.news_db).upsert(
        source_id="coindesk", category="CRYPTO_MEDIA", now=DECISION, window=timedelta(hours=48), similarity=0.5,
        item=RawItem("g", "https://x.test/a", "<script>alert(1)</script> Bitcoin", "", DECISION, None), assets=["BTC"])
    page = write_dashboard(settings, now=DECISION + timedelta(minutes=1)).read_text(encoding="utf-8")
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page   # texte externe échappé
    assert '<div id="stale" hidden>' in page and "(Paris)" in page and "1 R = perte au stop" in page
    assert "PAS PRÊT" in page and "NON vérifiée" in page and "jamais « aucune mauvaise nouvelle »" in page


def test_dashboard_exists_before_the_first_cycle(settings):
    store_candles(settings)
    clock = FakeClock(DECISION - timedelta(minutes=3))
    requests = iter([False, True])
    run_forever(settings, clock=clock, sleep=clock.sleep, downloader=no_download,
                should_stop=lambda: next(requests, True), news_collector=lambda s, now: None)
    page = (settings.root / "state" / "dashboard.html").read_text(encoding="utf-8")
    assert "PAS PRÊT" in page


def test_monitor_resolves_external_signals_after_each_cycle_and_survives_errors(settings):
    store_candles(settings)
    clock = FakeClock(DECISION - timedelta(minutes=3))
    calls = []

    def failing(settings_, *, now):
        calls.append(now)
        raise RuntimeError("base verrouillée")

    cycles = run_forever(settings, clock=clock, sleep=clock.sleep, max_cycles=2, downloader=no_download,
                         news_collector=lambda s, now: None, signal_resolver=failing)
    assert cycles == 2 and len(calls) == 2                      # une résolution par cycle, malgré l'erreur
    status = json.loads((settings.root / settings.live.status_file).read_text(encoding="utf-8"))
    assert "base verrouillée" in status["last_external_resolution"]["error"]


def test_cycle_refreshes_ready_owner_pairs_without_analysing_them(settings):
    """Une paire ajoutée et prête est rafraîchie à chaque cycle (sinon son avis passe « données périmées »)."""
    from decimal import Decimal

    from crypto_signal_intelligence.external.universe import UserUniverse

    store_candles(settings)
    universe = UserUniverse(settings.external_db)
    universe.request("QTUMUSDT", Decimal("0.001"), reason="test", now=DECISION)
    universe.mark_ready("QTUMUSDT", now=DECISION)
    fetched = []

    def downloader(settings_, symbol, timeframe, *, now, **kwargs):
        fetched.append((symbol, timeframe, kwargs.get("rest_only")))

    clock = FakeClock(DECISION + timedelta(seconds=20))
    report = scan_cycle(settings, now=clock(), decision_close=DECISION, downloader=downloader,
                        clock=clock, sleep=clock.sleep)
    assert ("QTUMUSDT", "15m", True) in fetched and ("QTUMUSDT", "1h", True) in fetched
    assert all(o.symbol != "QTUMUSDT" for o in report.outcomes)          # aucune stratégie sur cette paire
    assert not any("QTUMUSDT" in m for m in report.missing_after_wait)   # jamais attendue


def test_owner_pairs_are_downloaded_by_a_separate_worker_never_by_the_cycle_loop(settings):
    """Le téléchargement complet d'une paire ajoutée (minutes) se fait hors de la boucle : celle-ci ne
    l'attend jamais ; le fil séparé la passe READY et l'état publié en garde la trace."""
    from decimal import Decimal

    from crypto_signal_intelligence.external.universe import UserUniverse
    from crypto_signal_intelligence.live.scanner import UserPairWorker

    store_candles(settings)
    universe = UserUniverse(settings.external_db)
    universe.request("QTUMUSDT", Decimal("0.001"), reason="signal soumis à la main", now=DECISION)
    clock = FakeClock(DECISION - timedelta(minutes=3))
    full_downloads = []

    def downloader(settings_, symbol, timeframe, *, now, **kwargs):
        if kwargs.get("rest_only"):                              # rafraîchissement du cycle : rien à faire
            return None
        full_downloads.append((symbol, timeframe))
        CandleStore(settings_.data_dir).save(
            canonical(300, timeframe, symbol=symbol, start="2024-01-01", seed=9), symbol, timeframe)

    class NotStarted(UserPairWorker):                            # fil jamais lancé : on observe la boucle seule
        def start(self):
            self.started = True

    worker = NotStarted(settings, downloader=downloader, clock=clock)
    cycles = run_forever(settings, clock=clock, sleep=clock.sleep, max_cycles=1, downloader=downloader,
                         news_collector=lambda s, now: None, signal_resolver=lambda s, now: {},
                         user_pair_worker=worker)
    assert cycles == 1 and worker.started and full_downloads == []   # la boucle n'a rien téléchargé
    assert universe.get("QTUMUSDT")["status"] == "REQUESTED"

    result = worker.run_once()                                  # ce que fait le fil, de façon déterministe
    assert full_downloads == [("QTUMUSDT", "15m"), ("QTUMUSDT", "1h")] and result["ready"] == ["QTUMUSDT"]
    assert universe.get("QTUMUSDT")["status"] == "READY" and worker.last == result
    assert worker.run_once() == {"processed": [], "ready": [], "failed": []} and worker.last == result


def test_user_pair_worker_thread_starts_and_stops(settings):
    from crypto_signal_intelligence.live.scanner import UserPairWorker
    calls = []
    worker = UserPairWorker(settings, downloader=lambda *a, **k: calls.append(a), clock=lambda: DECISION,
                            interval_seconds=0.01)
    worker.start()
    worker.stop(timeout=5)
    assert not worker._thread.is_alive()
