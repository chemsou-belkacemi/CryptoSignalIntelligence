"""Portefeuilles hebdomadaires à date (research/xsection.py) : décision sur le passé, univers du mois, rendement de
lundi à lundi, coûts de rotation. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import xsection as xs


def test_weights_use_only_past_closes_and_the_month_universe(monkeypatch):
    monkeypatch.setattr(xs, "PICK", 1)
    monkeypatch.setattr(xs, "LOOKBACK", 5)
    monkeypatch.setattr(xs, "VOL_DAYS", 5)
    index = pd.date_range("2024-01-01", periods=40, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    closes = pd.DataFrame({"UP": 100 * np.cumprod(1 + 0.03 + rng.normal(0, 0.03, 40)), "FLAT": 100 + rng.normal(0, 0.01, 40),
                           "OUT": 100 * 1.05 ** np.arange(40)}, index=index)
    members = {pd.Period("2024-01", "M"): {"UP", "FLAT"}, pd.Period("2024-02", "M"): {"UP", "FLAT"}}
    mondays = pd.date_range("2024-01-15", periods=3, freq="W-MON", tz="UTC")
    weights = xs.weekly_weights(closes, members, mondays)
    assert weights["MOM_4S"].loc[mondays[0], "UP"] == 1.0 and weights["MOM_4S"]["OUT"].sum() == 0.0   # OUT hors univers
    assert weights["CALMES"].loc[mondays[0], "FLAT"] == 1.0 and weights["MOYENNE"].loc[mondays[0]].sum() == pytest.approx(1.0)
    changed = closes.copy()
    changed.loc[changed.index >= mondays[0], "FLAT"] = 1000.0                   # le futur change à partir du lundi
    again = xs.weekly_weights(changed, members, mondays[:1])
    assert again["MOM_4S"].loc[mondays[0]].equals(weights["MOM_4S"].loc[mondays[0]])


def test_returns_and_turnover_costs():
    index = pd.date_range("2024-01-01", periods=15, freq="D", tz="UTC")
    closes = pd.DataFrame({"A": np.linspace(100, 114, 15)}, index=index)
    mondays = pd.DatetimeIndex([index[0], index[7], index[14]])
    returns = xs.holding_returns(closes, mondays)
    assert returns.loc[index[0], "A"] == pytest.approx(107 / 100 - 1)
    weights = pd.DataFrame({"A": [1.0, 1.0, 0.0]}, index=mondays)
    net, turnover = xs.portfolio_series(weights, returns)
    assert turnover.tolist() == [1.0, 0.0, 1.0] and net.iloc[0] == pytest.approx(0.07 - xs.COST_PER_SIDE)
