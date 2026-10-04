"""Supports et résistances contre niveaux placebo (docs/NIVEAUX.md) : regroupement, issues à barrières, événements,
causalité, écart réel − placebo et son intervalle, exécution de bout en bout. Données SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import level_reaction as lr


def test_clusters_by_hand():
    out = lr.clusters([(100.0, 5), (100.4, 9), (103.0, 2), (99.8, 20)], tolerance=0.5)
    assert out == [((5, 9, 20), pytest.approx(100.0666667)), ((2,), 103.0)]


def test_outcome_barriers():
    close = np.array([100.0, 100.0, 100.0, 100.0])
    a = np.ones(4)
    assert lr.outcome(np.array([100, 100.5, 101.0, 100]), np.array([100, 99.5, 99.5, 100]), close, a, 0) == lr.UP
    assert lr.outcome(np.array([100, 100.5, 100.5, 100]), np.array([100, 99.5, 99.0, 100]), close, a, 0) == lr.DOWN
    assert lr.outcome(np.array([100, 101.0, 100, 100]), np.array([100, 99.0, 100, 100]), close, a, 0) == lr.NULL
    assert lr.outcome(np.full(4, 100.2), np.full(4, 99.8), close, a, 0) == lr.NULL           # aucune dans l'horizon


def test_events_rejection_and_breakout():
    level = lr.Level(key=(1,), price=110.0, placebo=False, start=1, end=10)
    close = np.array([100, 105, 108, 109.5, 111, 112, 112, 112, 112, 112], float)
    high = close + np.array([0, 0, 0, 0.6, 0, 0, 0, 0, 0, 0], float)       # bougie 3 : plus haut 110,1 sans clôturer au-dessus
    low = close - 0.5
    a = np.ones(10)
    found = dict(lr.events(level, high, low, close, a))
    assert found["rejet_resistance"] == 3 and found["cassure_resistance"] == 4
    assert "rebond_support" not in found                                   # jamais au-dessus du niveau avant ces bougies
    later = lr.Level(key=(1,), price=110.0, placebo=False, start=5, end=10)
    assert "cassure_resistance" not in dict(lr.events(later, high, low, close, a))   # clôture précédente déjà au-dessus


def walk(n: int, seed: int):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    return np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c


def test_levels_and_events_never_read_the_future():
    h, lo, c = walk(3000, 7)
    cut = 2000
    fh, flo, fc = h.copy(), lo.copy(), c.copy()
    scale = np.random.default_rng(2).uniform(0.6, 1.4, len(c) - cut)
    fh[cut:] *= scale
    flo[cut:] *= scale
    fc[cut:] *= scale
    a, fa = lr.atr(h, lo, c), lr.atr(fh, flo, fc)

    def known(hh, ll, cc, aa):
        out = set()
        for level in lr.levels_over_time(hh, ll, aa, 3.0, symbol="T", timeframe="1h"):
            if level.start < cut:
                out.add((level.key, round(level.price, 9), level.placebo, level.start))
                for name, t in lr.events(level, hh, ll, cc, aa):
                    if t < cut:
                        out.add((level.key, name, t))
        return out

    assert known(h, lo, c, a) == known(fh, flo, fc, fa)


def test_placebos_are_shifted_one_to_three_atr_and_away_from_real_levels():
    h, lo, c = walk(2000, 11)
    a = lr.atr(h, lo, c)
    levels = lr.levels_over_time(h, lo, a, 3.0, symbol="T", timeframe="1h")
    real = {lv.key: lv for lv in levels if not lv.placebo}
    placebos = [lv for lv in levels if lv.placebo]
    assert placebos
    for p in placebos[:200]:
        base = real.get(p.key[0])
        if base is None:
            continue
        shift = abs(p.price - base.price) / a[p.start - 1]
        assert 1.0 - 1e-9 <= shift <= 3.0 + 1e-9


def synthetic_events(real_rate: float, placebo_rate: float, n: int = 4000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.Timestamp("2019-01-01", tz="UTC") + pd.to_timedelta(rng.integers(0, 2000, n), unit="D")
    placebo = rng.random(n) < 0.5
    success = rng.random(n) < np.where(placebo, placebo_rate, real_rate)
    return pd.DataFrame({"symbol": "T", "timeframe": "1h", "event": "rejet_resistance", "placebo": placebo,
                         "time": times, "outcome": 1, "success": success.astype(object)})


def test_compare_finds_a_real_effect_and_none_when_equal():
    strong = lr.compare(synthetic_events(0.60, 0.50), samples=2000)
    assert strong["diff"] == pytest.approx(0.10, abs=0.03) and lr.verdict(strong) == lr.EFFECT
    none = lr.compare(synthetic_events(0.50, 0.50, seed=3), samples=2000)
    assert lr.verdict(none) == lr.NOTHING
    inverse = lr.compare(synthetic_events(0.40, 0.50, seed=4), samples=2000)
    assert lr.verdict(inverse) == lr.INVERSE
    frame = synthetic_events(0.5, 0.5)
    frame.loc[frame.index[:10], "success"] = None
    assert lr.compare(frame, samples=200)["null_real"] + lr.compare(frame, samples=200)["null_placebo"] == 10


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research import long_history
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    rng = np.random.default_rng(5)
    n = 24 * 400
    times = pd.date_range("2024-06-01", periods=n, freq="h", tz="UTC")

    def fake(settings, symbol):
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open_time": times, "open": o, "high": np.maximum(o, c) * 1.002,
                             "low": np.minimum(o, c) * 0.998, "close": c})

    monkeypatch.setattr(long_history, "load_long", fake)
    monkeypatch.setattr(lr, "code_state", lambda: "abc123")
    payload = lr.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), symbols=["AUSDT", "BUSDT"])
    assert set(payload["rows"]) == {f"{tf}/{e}" for tf in lr.TIMEFRAMES for e in (*lr.DECISIVE, *lr.DESCRIPTIVE)}
    assert all("verdict" in payload["rows"][f"{tf}/{e}"] for tf in lr.TIMEFRAMES for e in lr.DECISIVE)
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 4 and entry["period_end"].startswith("2025-06-30")
    monkeypatch.setattr(lr, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(lr.DirtyCode):
        lr.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), symbols=["AUSDT"])
