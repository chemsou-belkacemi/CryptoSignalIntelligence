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


def test_cross_section_ranks_only_the_requested_universe():
    """BTC sert de facteur à I sans être candidat ; hors univers, il n'entre pas non plus dans G."""
    from crypto_signal_intelligence.research.screen import _cross_sectional
    rng = np.random.default_rng(5)
    index = pd.date_range("2023-01-01", periods=1500, freq="h", tz="UTC")
    walk = lambda drift: pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(index)))), index=index)  # noqa: E731
    closes = pd.DataFrame({f"P{i}USDT": walk(0.0) for i in range(5)})
    btc = walk(0.002)                                          # BTC le plus fort : serait toujours classé
    for kind in ("G", "I"):
        chosen = _cross_sectional(closes, kind, btc)
        assert list(chosen.columns) == list(closes.columns) and chosen.to_numpy().any()
    with_btc = _cross_sectional(closes.assign(BTCUSDT=btc), "G", btc)
    assert with_btc["BTCUSDT"].any()                           # dans l'univers demandé : candidat en G
    assert "BTCUSDT" not in _cross_sectional(closes.assign(BTCUSDT=btc), "I", btc)


def test_long_horizons_get_longer_ci_blocks():
    from crypto_signal_intelligence.research.screen import block_days_for
    assert [block_days_for(h) for h in (1, 24, 72, 168)] == [10, 10, 10, 14]


def test_screen_refuses_invalid_horizons(settings):
    from datetime import UTC, datetime

    import pytest

    from crypto_signal_intelligence.research.screen import run
    with pytest.raises(ValueError):
        run(settings, now=datetime(2026, 10, 1, tzinfo=UTC), symbols=["ETHUSDT"], horizons=(0,))
