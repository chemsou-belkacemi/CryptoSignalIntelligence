"""Confirmation des lignes de tendance sur la période réservée : fenêtre et coupure (rien après la coupure n'est lu),
contrôle de complétude sur les seules dates, consultation enregistrée après ce contrôle et avant tout calcul, une
seule lecture, répétition sans rien enregistrer. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import figures_history as fh
from crypto_signal_intelligence.research import trendline_final as tf
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.protocol import FinalTestLocked
from tests.test_figures_history import LATENCY, hour_walk

START = pd.Timestamp("2025-03-01", tz="UTC")
CUTOFF = pd.Timestamp("2025-06-15 23:59:59", tz="UTC")
FIRST = pd.Timestamp("2024-10-01", tz="UTC")
HOURS = 24 * 300


def hours_of(symbol: str) -> pd.DataFrame:
    frame = hour_walk(HOURS, sum(map(ord, symbol)))
    return frame.assign(open_time=FIRST + pd.to_timedelta(np.arange(HOURS), unit="h"))


def minutes_of(symbol: str) -> pd.DataFrame:
    hours = hours_of(symbol)
    rep = hours.loc[hours.index.repeat(60)].reset_index(drop=True)
    rep["open_time"] = FIRST + pd.to_timedelta(np.arange(len(rep)), unit="min")
    return rep


def test_window_setups_respect_start_warmup_and_cutoff():
    h1 = hours_of("AUSDT")
    setups = tf.window_setups(h1, "AUSDT", start=START, cutoff=CUTOFF)
    assert setups and all(s.method == "TRENDLINE" and s.timeframe == "1h" for s in setups)
    assert all(s.at >= START and s.at + tf.HORIZON <= CUTOFF for s in setups)
    late = tf.window_setups(h1, "AUSDT", start=FIRST, cutoff=CUTOFF)
    assert all(s.at >= FIRST + fh.WARMUP for s in late)


def test_nothing_after_the_cutoff_is_read():
    h1 = hours_of("AUSDT")
    m = minutes_of("AUSDT")
    fake_h1, fake_m = h1.copy(), m.copy()
    for frame in (fake_h1, fake_m):
        after = pd.to_datetime(frame["open_time"], utc=True) > CUTOFF
        frame.loc[after, ["open", "high", "low", "close"]] *= 3.0
    cut = pd.to_datetime(m["open_time"], utc=True) <= CUTOFF

    def rows(hours, minutes):
        mm = fh.Minutes.from_frame(minutes[pd.to_datetime(minutes["open_time"], utc=True) <= CUTOFF].reset_index(drop=True))
        return tf.pair_rows(hours, mm, "AUSDT", start=START, cutoff=CUTOFF, latency=LATENCY)

    true, fake = rows(h1, m), rows(fake_h1, fake_m)
    assert cut.any() and true and pd.DataFrame(true).equals(pd.DataFrame(fake))


def test_placebos_stay_inside_the_window(monkeypatch):
    """Bornes réellement passées par `pair_rows` (capturées), pas recalculées par le test."""
    h1, m = hours_of("AUSDT"), fh.Minutes.from_frame(minutes_of("AUSDT"))
    seen = []
    original = tf.tc.with_uniform_placebos

    def spy(row, minutes, *, lo_ns, hi_ns):
        seen.append((lo_ns, hi_ns))
        return original(row, minutes, lo_ns=lo_ns, hi_ns=hi_ns)

    monkeypatch.setattr(tf.tc, "with_uniform_placebos", spy)
    out = tf.pair_rows(h1, m, "AUSDT", start=START, cutoff=CUTOFF, latency=LATENCY)
    assert out and len(seen) == len(out)
    lo, hi = START.as_unit("ns").value, CUTOFF.as_unit("ns").value - tf.HOLD.value
    assert all(a == lo and b <= hi for a, b in seen)


def test_presence_uses_dates_only_and_flags_holes_and_early_ends():
    hours = pd.Series(pd.date_range("2025-01-01", "2025-06-15 23:00", freq="h", tz="UTC"))
    minutes = pd.Series(pd.date_range("2025-01-01", "2025-06-15 23:59", freq="min", tz="UTC"))
    assert tf.presence(hours, minutes, start=START, cutoff=CUTOFF)["ok"]
    holed = minutes[(minutes < "2025-04-01") | (minutes >= "2025-04-03")]
    assert not tf.presence(hours, holed, start=START, cutoff=CUTOFF)["ok"]             # 2 jours sur 136 : 98,5 %
    early = minutes[minutes < "2025-06-14"]
    assert not tf.presence(hours, early, start=START, cutoff=CUTOFF)["ok"]
    assert tf.presence(hours[hours < "2025-01-15"], minutes, start=START, cutoff=CUTOFF) is None


@pytest.fixture
def fake_data(settings, monkeypatch, tmp_path):
    from crypto_signal_intelligence.research import long_history, minute_history, pit_universe
    members = pd.DataFrame({"month": pd.to_datetime(["2025-01-01", "2025-01-01"], utc=True), "symbol": ["AUSDT", "BUSDT"],
                            "rank": [1, 2], "median_quote_volume": [1.0, 1.0]})
    monkeypatch.setattr(pit_universe, "load_membership", lambda s: members)
    monkeypatch.setattr(long_history, "load_long", lambda s, symbol: hours_of(symbol))
    monkeypatch.setattr(minute_history, "load_minutes", lambda s, symbol: minutes_of(symbol))
    checks = {sym: {"ok": True, "covered": 1.0} for sym in ("AUSDT", "BUSDT")}
    monkeypatch.setattr(tf, "check_complete", lambda s, symbols, start, cutoff: dict(checks))
    monkeypatch.setattr(tf, "START", START)
    monkeypatch.setattr(tf, "CUTOFF", CUTOFF)
    monkeypatch.setattr(tf, "code_state", lambda: "abc123")
    return checks


def test_final_read_needs_the_flag_records_the_consultation_first_and_only_once(settings, fake_data, monkeypatch):
    with pytest.raises(FinalTestLocked):
        tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), workers=1)
    registry = ExperimentRegistry(settings.experiments_db)
    original = tf._one

    def watched(args):                                  # chaque calcul de paire voit la consultation déjà enregistrée
        assert registry.final_test_consulted(tf.STRATEGY) == 1
        return original(args)

    monkeypatch.setattr(tf, "_one", watched)
    payload = tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), allow_final_test=True, workers=1)
    assert payload["consultations_total"] == 1 and payload["n_trials"] == 2
    entry = registry.get(payload["run_id"])
    assert entry is not None and entry["period_label"] == "FINAL_TEST" and entry["metrics"]["n_trials"] == 2
    assert set(payload["result"]["decision"]) >= {"piste", "gain"}
    with pytest.raises(tf.AlreadyConsulted):
        tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), allow_final_test=True, workers=1)


def test_incomplete_minutes_refuse_before_the_consultation(settings, fake_data):
    fake_data["BUSDT"] = {"ok": False, "covered": 0.5}
    with pytest.raises(tf.tc.IncompleteMinutes):
        tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), allow_final_test=True, workers=1)
    assert ExperimentRegistry(settings.experiments_db).final_test_consultations_total() == 0


def test_rehearsal_reads_development_only_and_records_nothing(settings, fake_data, monkeypatch):
    monkeypatch.setattr(tf, "REHEARSAL_START", START)
    seen = {}
    original = tf._one

    def watched(args):
        seen["window"] = (args[2], args[3])
        return original(args)

    monkeypatch.setattr(tf, "_one", watched)
    payload = tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), rehearsal=True, workers=1)
    registry = ExperimentRegistry(settings.experiments_db)
    assert payload["rehearsal"] and payload["n_trials"] == 0 and payload["consultations_total"] is None
    assert registry.final_test_consultations_total() == 0 and registry.get(payload["run_id"]) is None
    assert seen["window"][1] <= pd.Timestamp("2025-06-30 23:59:59", tz="UTC")


def test_a_failure_after_the_consultation_is_recorded_as_failed(settings, fake_data, monkeypatch):
    def broken(args):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(tf, "_one", broken)
    with pytest.raises(RuntimeError):
        tf.run(settings, now=datetime(2026, 10, 5, tzinfo=UTC), allow_final_test=True, workers=1)
    registry = ExperimentRegistry(settings.experiments_db)
    assert registry.final_test_consulted(tf.STRATEGY) == 1
    with registry.connect() as db:
        rows = db.execute("SELECT status, metrics FROM runs WHERE strategy=?", (tf.STRATEGY,)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "FAILED" and "panne simulée" in rows[0][1]


def test_universe_ignores_membership_from_the_period_itself(settings, monkeypatch):
    from crypto_signal_intelligence.research import pit_universe
    members = pd.DataFrame({"month": pd.to_datetime(["2025-06-01", "2025-07-01"], utc=True), "symbol": ["AUSDT", "NEWUSDT"],
                            "rank": [1, 1], "median_quote_volume": [1.0, 1.0]})
    monkeypatch.setattr(pit_universe, "load_membership", lambda s: members)
    assert tf.universe(settings) == ["AUSDT"]
