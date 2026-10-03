"""Volatilité v4 (research/volatility_v4.py) : réétalonnage purgé, mise à l'échelle purgée, GARCH causal. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import volatility_v4 as v4


def part(days: int = 900, bias: float = 1.5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    origins = pd.date_range("2019-01-01", periods=days, freq="D", tz="UTC")
    forecast = np.exp(rng.normal(-7, 0.5, days))
    return pd.DataFrame({"origin": origins, "forecast": forecast, "realized": forecast * bias * np.exp(rng.normal(-0.05, 0.3, days))})


def test_recalibration_learns_only_from_the_purged_past_and_removes_a_bias(monkeypatch):
    monkeypatch.setattr(v4, "MIN_FIT_ROWS", 100)
    data = part()
    out = v4.recalibrated(data, 3)
    assert np.isnan(out[:120]).all()                                     # pas assez de passé connu au début
    late = slice(600, None)
    assert np.mean(data["realized"].to_numpy()[late] / out[late]) == pytest.approx(1.0, abs=0.08)
    changed = data.copy()
    changed.loc[700:, "realized"] *= 100                                  # le futur change : les prévisions passées non
    assert np.allclose(v4.recalibrated(changed, 3)[:690], out[:690], equal_nan=True)


def test_scaling_uses_the_purged_mean_ratio(monkeypatch):
    monkeypatch.setattr(v4, "MIN_FIT_ROWS", 100)
    data = part(bias=2.0)
    raw = data["forecast"].to_numpy()
    scaled = v4.scaled_to_rv(data, raw, 7)
    assert np.nanmean(scaled[400:] / raw[400:]) == pytest.approx(np.mean(data["realized"] / data["forecast"]), rel=0.1)


def test_garch_forecasts_are_causal():
    rng = np.random.default_rng(1)
    index = pd.date_range("2019-01-01", periods=900, freq="D", tz="UTC")
    returns = pd.Series(rng.standard_t(5, 900) * 0.03, index=index)
    origins = pd.date_range("2021-03-01", "2021-03-31", freq="D", tz="UTC")
    base = v4.garch_forecasts(returns, origins, 3)
    assert base.notna().all() and (base > 0).all()
    future = returns.copy()
    future[future.index > pd.Timestamp("2021-03-10", tz="UTC")] *= 5    # le futur après le 10 mars change
    again = v4.garch_forecasts(future, origins, 3)
    early = origins[origins <= pd.Timestamp("2021-03-10", tz="UTC")]
    assert np.allclose(base[early], again[early])
