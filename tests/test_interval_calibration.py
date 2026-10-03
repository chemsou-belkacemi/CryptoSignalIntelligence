"""Calibrage des intervalles (research/interval_calibration.py) et bootstrap stationnaire (research/intervals.py) :
histoire rééchantillonnée par blocs, nulle bien calibrée sur données synthétiques, règle de choix. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research import interval_calibration as ic
from crypto_signal_intelligence.research.intervals import stationary_bootstrap_ci


def test_stationary_bootstrap_brackets_the_mean_and_is_calibrated_under_the_null():
    times = pd.date_range("2020-01-01", periods=1500, freq="D", tz="UTC")
    hits = 0
    for seed in range(60):
        values = np.random.default_rng(seed).normal(0.0, 0.02, len(times))
        ci, block = stationary_bootstrap_ci(values, times, reps=400, seed=seed, block_days=10)
        assert ci is not None and block == 10 and ci[0] < values.mean() < ci[1]
        hits += ci[0] > 0
    assert hits <= 5                                                     # ≈ 2,5 % attendus, 60 répétitions
    assert stationary_bootstrap_ci(values[:50], times[:50])[0] is None   # trop peu de jours


def test_resampled_history_moves_whole_days_and_keeps_the_calendar():
    index = pd.date_range("2020-01-01", periods=300, freq="D", tz="UTC")
    fwd = pd.DataFrame({"A": np.arange(300.0), "B": np.arange(300.0) + 1000}, index=index)
    out = ic.resampled_history(fwd, np.random.default_rng(1), mean_block=10)
    assert out.index.equals(fwd.index) and ((out["B"] - out["A"]) == 1000).all()      # journées entières, paires ensemble
    steps = np.diff(out["A"].to_numpy())
    assert (steps == 1).mean() > 0.8                                                  # surtout des suites de jours


def test_choice_rule_keeps_the_current_method_unless_another_is_calibrated_and_more_powerful():
    def rows(method, null, power):
        return [{"chain": "criblage", "method": method, "planted_pct": 0.0, "low_above_zero": n} for n in null] + \
               [{"chain": "criblage", "method": method, "planted_pct": 0.25, "low_above_zero": power}]
    data = rows("blocs_de_jours", [0.0, 0.01], 0.5) + rows("stationnaire_arch", [0.02, 0.03], 0.7) + rows("calendaire_student", [0.06, 0.02], 0.9)
    choice = ic.choose(data)["criblage"]
    assert choice["retained"] == "stationnaire_arch" and not choice["methods"]["calendaire_student"]["calibrated"]
    data = rows("blocs_de_jours", [0.02, 0.03], 0.5) + rows("stationnaire_arch", [0.0, 0.0], 0.9)
    assert ic.choose(data)["criblage"]["retained"] == "blocs_de_jours"
