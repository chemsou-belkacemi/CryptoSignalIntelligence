"""Test en direct F18_ASSISTANT (forward/f18.py) : démarrage sur une racine temporaire avec code commité, évaluation
d'une clôture 4 h une seule fois dès que sa bougie est en magasin, journal (EVALUATION, APPEL, RESOLUTION, VERDICT,
CLOTURE), discipline lue dans le journal, résolution de l'appel et de ses placebos, trou constaté, mesures et verdict,
gel des modules de l'assistant. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.assistant import evaluate as ev
from crypto_signal_intelligence.assistant import outbox, state
from crypto_signal_intelligence.assistant import rules as R
from crypto_signal_intelligence.forward import f18, registry
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.forward.tests import BY_ID
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .conftest import PROJECT
from .test_assistant import GOOD_BOOK, GREEN, T, range_rejection, uptrend_pullback

HALAL = HalalList(("BTCUSDT", "RNGUSDT"), {}, "a" * 64, "b" * 64)
START_AT = datetime(2026, 10, 8, 9, tzinfo=UTC)
NOW = datetime(2026, 10, 9, 8, 5, tzinfo=UTC)


def after_t(h1: pd.DataFrame, *, hours: int, path) -> pd.DataFrame:
    """Prolonge les bougies 1 h après T avec `path(k)` → clôture de la k-ième heure (mèches ± 0,1)."""
    rows = []
    prev = float(h1["close"].iloc[-1])
    for k in range(hours):
        c = path(k)
        rows.append((T + k * R.HOUR, prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c, 1.0))
        prev = c
    extra = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "quote_volume"])
    extra["available_at"] = extra["open_time"] + R.HOUR + pd.Timedelta(seconds=2)
    return pd.concat([h1, extra], ignore_index=True)


def btc() -> pd.DataFrame:
    """BTC en hausse au-dessus de son EMA50, sans configuration (volume de confirmation faible)."""
    return uptrend_pullback(last_volume=0.5)


def write_store(settings, frames: dict[str, pd.DataFrame]):
    store = ev.store_for(settings)
    for symbol, frame in frames.items():
        path = store.path(symbol, "1h")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    return store


@pytest.fixture
def quiet(monkeypatch):
    """Feu VERT, prévision H24 fixe, aucune news, pas de pas de cotation : les lectures externes sont remplacées."""
    monkeypatch.setattr(ev, "light_at", lambda settings, at: GREEN)
    monkeypatch.setattr(ev, "forecast_at", lambda settings, at: {"available": True, "pairs": {"RNGUSDT": {"move_24h_pct": 1.0}}})
    monkeypatch.setattr(ev, "news_at", lambda settings, at: [])
    monkeypatch.setattr(ev, "tick_for", lambda settings, symbol: None)


def started(settings, monkeypatch, now=START_AT):
    monkeypatch.setattr(registry, "current_commit", lambda: "abc123def456")
    return registry.start(settings, f18.TEST, now=now, halal=HALAL)


# --- Démarrage et gel ----------------------------------------------------------------------------------------------------------

def test_start_on_a_temporary_root_with_committed_code(settings, monkeypatch):
    with pytest.raises(registry.DirtyCode):
        monkeypatch.setattr(registry, "current_commit", lambda: "abc123+DIRTY")
        registry.start(settings, f18.TEST, now=START_AT, halal=HALAL)
    data = started(settings, monkeypatch)
    assert data["test_id"] == "F18_ASSISTANT" and data["interim_at"][:10] == "2026-11-19" and data["final_at"][:10] == "2026-12-31"
    assert set(data["fingerprints"]) == {"doc", "params", "code"} and data["halal"]["symbols"] == ["BTCUSDT", "RNGUSDT"]
    assert registry.status(settings, f18.TEST, now=NOW)["state"] == registry.RUNNING
    experiments = ExperimentRegistry(settings.experiments_db)
    assert experiments.program_trials("FORWARD") == 1 and experiments.program_trials("DEVELOPMENT") == 0
    with pytest.raises(registry.AlreadyStarted):
        registry.start(settings, f18.TEST, now=START_AT, halal=HALAL)
    assert registry.check_frozen(settings, f18.TEST, now=NOW) is None


def test_f18_is_registered_and_freezes_every_assistant_module(settings):
    assert BY_ID["F18_ASSISTANT"][1] is f18 and "F17" not in " ".join(BY_ID)
    names = {getattr(o, "__name__", "") for o in registry.frozen_objects(f18.TEST)}
    for name in ("crypto_signal_intelligence.forward.f18", "crypto_signal_intelligence.assistant.rules",
                 "crypto_signal_intelligence.assistant.evaluate", "crypto_signal_intelligence.assistant.state",
                 "crypto_signal_intelligence.assistant.outbox", "crypto_signal_intelligence.forward.costs",
                 "day_block_ci95", "day_block_ci", "verdict", "book_metrics", "decide", "macro_events", "risk_items",
                 "volume_profile", "zigzag", "cluster_levels", "CandleStore"):
        assert name in names, name
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    text = registry.section(doc, f18.TEST_ID)
    assert registry.missing_fields(text) == []
    for value in ("Placebos", "3 appels par jour", "1,5 R", "84 jours", "sha256", "INSUFFISANT", "aucun gain"):
        assert value in text, value
    assert registry.section(doc, "F17") is None
    assert "F18_ASSISTANT" not in registry.section(doc, "Cadre")


# --- Évaluation en service ------------------------------------------------------------------------------------------------------

def test_record_decisions_evaluates_each_close_once_journals_the_call_and_updates_state(settings, monkeypatch, quiet):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f18.TEST_ID)
    h1 = range_rejection()
    store = write_store(settings, {"RNGUSDT": h1, "BTCUSDT": btc()})
    book = lambda s: GOOD_BOOK  # noqa: E731
    # Avant la clôture + latence : la bougie 1 h n'est pas encore connue → on attend, rien n'est inscrit.
    early = f18.record_decisions(settings, journal, start, now=T + pd.Timedelta(seconds=1), store=store, book=book)
    assert early["evaluations"] == 0 and "waiting" in early and journal.first(f18.EVALUATION) is None
    out = f18.record_decisions(settings, journal, start, now=NOW, store=store, book=book)
    assert out == {"evaluations": 1, "calls": 1}
    evaluation = journal.first(f18.EVALUATION)["data"]
    assert evaluation["at"] == T.isoformat() and evaluation["regimes"] == {"HAUSSE": 1, "RANGE": 1, "BAISSE": 0, "INDECIS": 0, "NON_EVALUABLE": 0}
    assert len(evaluation["call_ids"]) == 1 and evaluation["silence"] is None
    assert evaluation["discipline"] == {"active": {}, "rest_until": {}, "calls_today": 0} and "calls" not in evaluation
    call = journal.first(f18.CALL)["data"]
    assert call["symbol"] == "RNGUSDT" and call["regime"] == "RANGE" and len(call["placebo_offsets_h"]) == 20
    assert call["placebo_offsets_h"] == R.placebo_offsets(call["call_id"])
    # Même clôture au passage suivant : rien de nouveau ; la boîte et l'état ont été écrits.
    assert f18.record_decisions(settings, journal, start, now=NOW + timedelta(hours=1), store=store, book=book) == {"evaluations": 0, "calls": 0}
    waiting = outbox.pending(settings, now=NOW)
    assert [m["id"] for m in waiting] == [f"APPEL:{call['call_id']}"] and "Shadow : aucun ordre" in waiting[0]["text"]
    current = state.read(settings)
    assert current["available"] and current["calls_made"] == 1 and current["active_calls"][0]["call_id"] == call["call_id"]
    assert current["active_calls"][0]["status"] == R.RUNNING and current["next_evaluation_at"] == (T + R.H4).isoformat()
    assert current["resume"].splitlines()[2].startswith("1 appel actif") and current["places_orders"] is False
    # Clôture suivante : la paire a un appel actif → refus APPEL_ACTIF ; 1 appel déjà fait ce jour.
    store = write_store(settings, {"RNGUSDT": after_t(h1, hours=4, path=lambda k: 101.6), "BTCUSDT": after_t(btc(), hours=4, path=lambda k: 161.0)})
    later = NOW + timedelta(hours=4)
    assert f18.record_decisions(settings, journal, start, now=later, store=store, book=book)["evaluations"] == 1
    second = list(journal.entries({f18.EVALUATION}))[1]["data"]
    assert second["discipline"]["calls_today"] == 1 and call["symbol"] in second["discipline"]["active"]
    assert second["refusals_by_reason"].get(R.ACTIVE, 0) >= 1 or second["candidates"] == 0
    # Rien dans signals/ ni dans le registre des signaux (F2 intact).
    assert not settings.signals_db.exists() and not (settings.root / "signals" / "shadow").exists()


def test_nothing_is_evaluated_before_the_start_nor_after_the_end(settings, monkeypatch, quiet):
    start = started(settings, monkeypatch, now=datetime(2026, 10, 9, 9, tzinfo=UTC))        # démarré après la clôture de 08:00
    journal = registry.journal_for(settings, f18.TEST_ID)
    store = write_store(settings, {"RNGUSDT": range_rejection(), "BTCUSDT": btc()})
    assert f18.record_decisions(settings, journal, start, now=datetime(2026, 10, 9, 9, 5, tzinfo=UTC), store=store, book=lambda s: GOOD_BOOK) == {"evaluations": 0, "calls": 0}
    assert f18.record_decisions(settings, journal, start, now=datetime(2027, 1, 2, tzinfo=UTC), store=store, book=lambda s: GOOD_BOOK) == {"evaluations": 0, "calls": 0}
    assert journal.first(f18.EVALUATION) is None


def test_discipline_is_read_from_the_journal(settings, monkeypatch):
    started(settings, monkeypatch)
    journal = registry.journal_for(settings, f18.TEST_ID)
    store = write_store(settings, {})
    base = {"entry": 100.0, "stop": 96.0, "tp1": 104.0, "tp2": 110.0, "regime": "RANGE", "setup": R.REJECTION}
    for i, symbol in enumerate(("AUSDT", "BUSDT", "CUSDT")):
        journal.append(f18.CALL, base | {"call_id": f"c{i}", "symbol": symbol, "at": T.isoformat()}, now=NOW)
    journal.append(f18.CALL, base | {"call_id": "old", "symbol": "DUSDT", "at": (T - 5 * R.DAY).isoformat()}, now=NOW)
    journal.append(f18.RESOLUTION, {"call_id": "old", "symbol": "DUSDT", "at": (T - 5 * R.DAY).isoformat(), "status": "RESOLU",
                                    "results": {"central": {"exit_at": (T - R.DAY).isoformat()}}}, now=NOW)
    d = f18.discipline_at(journal, store, at=T + R.H4, now=NOW + timedelta(hours=4))
    assert d["calls_today"] == 3 and set(d["active"]) == {"AUSDT", "BUSDT", "CUSDT"}                 # sans bougies : en cours
    assert d["rest_until"] == {"DUSDT": (T + R.DAY).isoformat()}                                       # sortie + 48 h
    assert f18.discipline_at(journal, store, at=T + R.DAY, now=NOW + timedelta(days=1))["calls_today"] == 0


# --- Résolution, mesures, verdict ------------------------------------------------------------------------------------------------

def winning_path(k: int) -> float:
    """Monte de 101,5 à 112 en 20 heures (TP1 puis TP2 touchés), puis reste à 112."""
    return min(112.0, 101.5 + 0.55 * k)


def test_resolve_call_and_placebos_then_verdict_and_closure(settings, monkeypatch, quiet):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f18.TEST_ID)
    h1 = range_rejection()
    store = write_store(settings, {"RNGUSDT": h1, "BTCUSDT": btc()})
    f18.record_decisions(settings, journal, start, now=NOW, store=store, book=lambda s: GOOD_BOOK)
    call = journal.first(f18.CALL)["data"]
    full = after_t(h1, hours=16 * 24, path=winning_path)
    store = write_store(settings, {call["symbol"]: full})
    assert f18.resolve(settings, journal, now=NOW + timedelta(hours=2), store=store) == {}            # trop tôt
    assert f18.resolve(settings, journal, now=NOW + timedelta(days=3), store=store) == {}             # placebos futurs en cours
    assert outbox.counts(settings)["EN_ATTENTE"] == 1
    done = f18.resolve(settings, journal, now=T + timedelta(days=16), store=store)
    assert done == {f18.RESOLVED: 1}
    res = journal.first(f18.RESOLUTION)["data"]
    central = res["results"]["central"]
    assert res["status"] == f18.RESOLVED and central["outcome"] == R.TP2 and central["hits"] == 1 and central["r"] > 0
    assert len(central["placebos"]) == 20 and central["placebos_resolved"] == 20 and central["excess"] == pytest.approx(central["r"] - central["placebo_mean"], abs=1e-6)
    assert res["results"]["defavorable"]["r"] < central["r"]
    assert [m["id"] for m in outbox.pending(settings, now=T + timedelta(days=16))] == [f"RESOLUTION:{call['call_id']}"]
    assert state.read(settings)["active_calls"] == []
    # Mesures : 1 appel résolu → EN_COURS avant la date d'évaluation, INSUFFISANT après ; puis VERDICT et CLOTURE.
    running = f18.stats(journal, start, now=T + timedelta(days=16))
    assert running["resolved"] == 1 and running["verdict"] == "EN_COURS" and running["by_regime"]["RANGE"]["resolved"] == 1
    assert running["scenarios"]["central"]["tp1_rate"] == 1.0 and running["scenarios"]["central"]["worst_streak"] == 0
    assert running["refusals_by_reason"] == {} and running["calls_per_week"] > 0
    end = pd.Timestamp(start["final_at"]) + timedelta(days=1)
    assert f18.stats(journal, start, now=end)["verdict"] == "INSUFFISANT"
    assert f18.finalize(journal, start, now=end) == "VERDICT" and f18.finalize(journal, start, now=end) == "CLOTURE"
    assert registry.status(settings, f18.TEST, now=end)["state"] == registry.CLOSED
    assert journal.verify()["ok"]


def test_a_gap_is_declared_two_days_after_the_placebo_window(settings, monkeypatch, quiet):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f18.TEST_ID)
    h1 = range_rejection()
    store = write_store(settings, {"RNGUSDT": h1, "BTCUSDT": btc()})
    f18.record_decisions(settings, journal, start, now=NOW, store=store, book=lambda s: GOOD_BOOK)
    call = journal.first(f18.CALL)["data"]
    store = write_store(settings, {call["symbol"]: after_t(h1, hours=30, path=lambda k: 101.6)})     # bougies arrêtées après 30 h
    end = pd.Timestamp(call["resolution_end"])
    assert f18.resolve(settings, journal, now=end + timedelta(days=1), store=store) == {}
    assert f18.resolve(settings, journal, now=end + f18.GAP_AFTER, store=store) == {f18.GAP: 1}
    res = journal.first(f18.RESOLUTION)["data"]
    assert res["status"] == f18.GAP and res["results"] is None
    assert f18.stats(journal, start, now=end + f18.GAP_AFTER)["gaps"] == 1
    assert "TROU" in outbox.pending(settings, now=end + f18.GAP_AFTER)[-1]["text"]


def test_daily_report_renders_the_assistant_test(settings, monkeypatch, quiet):
    from crypto_signal_intelligence.forward import report
    started(settings, monkeypatch)
    text = report.markdown(report.build(settings, now=NOW))
    assert "## F18_ASSISTANT" in text and "Évaluations 4 h : 0" in text and "aucun gain démontré" in text
