"""Criblage : conditions causales et rendements futurs (fixtures SYNTHÉTIQUES)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research.screen import SERIES_CONDITIONS, forward_returns

from .conftest import canonical


def test_conditions_are_causal_adding_the_future_changes_nothing():
    full = canonical(3000, "15m", symbol="ETHUSDT", seed=7, vol=0.01)
    past = full.iloc[:2000].reset_index(drop=True)
    for name, condition in SERIES_CONDITIONS.items():
        assert np.array_equal(condition(full)[:2000], condition(past)), name


def test_forward_return_enters_at_next_open_and_exits_at_close():
    df = pd.DataFrame({"open": [10.0, 11.0, 12.0, 13.0], "close": [10.5, 11.5, 12.5, 13.5]})
    fwd = forward_returns(df, 2)
    assert fwd[0] == 12.5 / 11.0 - 1 and np.isnan(fwd[2]) and np.isnan(fwd[3])


def test_breakout_retest_fires_once_after_a_real_retest():
    from crypto_signal_intelligence.research.screen import _breakout_retest
    n = 40
    high = [100.0] * 25 + [103.0, 102.5, 102.2, 103.5] + [104.0] * (n - 29)
    low = [99.0] * 25 + [101.0, 100.05, 100.9, 101.5] + [103.0] * (n - 29)
    close = [99.5] * 25 + [102.0, 100.8, 101.9, 103.2] + [103.5] * (n - 29)
    open_ = [99.5] * 25 + [100.2, 101.8, 100.9, 102.0] + [103.5] * (n - 29)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})
    events = _breakout_retest(df)
    assert list(np.flatnonzero(events)) == [27]   # retest à 100,05 ≤ 100 × 1,001… puis reprise haussière en 27


def test_confidence_interval_brackets_the_reported_event_weighted_mean():
    from crypto_signal_intelligence.research.screen import _day_block_ci
    rng = np.random.default_rng(1)
    times = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC").repeat(rng.integers(1, 20, 400))
    frame = pd.DataFrame({"time": times, "excess": rng.normal(0.001, 0.01, len(times))})
    low, high = _day_block_ci(frame, 10, 2000, 3)
    assert low < frame["excess"].mean() * 100 < high
