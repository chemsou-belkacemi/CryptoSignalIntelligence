"""Lot 5 : régression logistique, AUC, méta-labeling purgé et critères (données SYNTHÉTIQUES)."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.ml.logistic import auc, fit_logistic
from crypto_signal_intelligence.ml.meta import FEATURES, evaluate_windows, features_at_decision, judge
from crypto_signal_intelligence.research.walk_forward import make_windows


def test_logistic_recovers_a_known_relation_and_is_deterministic():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(4000, 3))
    true = 1.5 * X[:, 0] - 1.0 * X[:, 1]
    y = (rng.random(4000) < 1 / (1 + np.exp(-true))).astype(float)
    model = fit_logistic(X, y, ["a", "b", "c"], l2=0.0)
    coef = model.coef * 1 / model.scale                     # retour à l'échelle d'origine
    assert coef[0] == pytest.approx(1.5, abs=0.15) and coef[1] == pytest.approx(-1.0, abs=0.15)
    assert abs(coef[2]) < 0.15
    again = fit_logistic(X, y, ["a", "b", "c"], l2=0.0)
    assert np.array_equal(model.predict_proba(X), again.predict_proba(X))
    with pytest.raises(ValueError):
        fit_logistic(X, y + 2, ["a", "b", "c"])


def test_auc_by_ranks_handles_ties_and_degenerate_cases():
    assert auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert auc(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert auc(np.array([0, 1, 0, 1]), np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    assert auc(np.array([1, 1]), np.array([0.2, 0.3])) is None


def synthetic(n_days: int, *, signal: float, seed: int) -> pd.DataFrame:
    """Trades quotidiens ; `signal` relie la variable atr_pct à l'issue (0 = aucun lien)."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2021-01-01", periods=n_days * 3, freq="8h", tz="UTC")
    x = rng.normal(size=len(times))
    win = rng.random(len(times)) < 1 / (1 + np.exp(-signal * x))
    data = pd.DataFrame({f: rng.normal(size=len(times)) for f in FEATURES})
    data["atr_pct"] = x
    data["setup_time"], data["exit_time"] = times, times + pd.Timedelta(hours=6)
    data["label"] = win.astype(float)
    data["r_multiple"] = np.where(win, 1.0, -1.0)
    data["symbol"] = "ETHUSDT"
    return data


def test_meta_labeling_detects_a_real_relation_and_rejects_noise(settings):
    windows = make_windows(datetime(2021, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC),
                           train_months=12, test_months=6)
    strong = synthetic(1460, signal=2.0, seed=3)
    predictions, rows, model = evaluate_windows(strong, windows, settings)
    verdict, criteria, oos = judge(predictions, rows, settings)
    assert verdict == "USEFUL_OOS", criteria
    assert oos["auc"] > 0.7 and oos["expectancy_r_kept"] > oos["expectancy_r_all"]
    assert model is not None and abs(model.to_dict()["standardized_coefficients"]["atr_pct"]) > 0.5

    noise = synthetic(1460, signal=0.0, seed=4)
    verdict, criteria, oos = judge(*evaluate_windows(noise, windows, settings)[:2], settings)
    assert verdict == "NOT_USEFUL" and not criteria[1]["passed"]


def test_training_is_purged_of_trades_still_open_at_the_test_start(settings):
    """Un trade ouvert avant le test mais clos dedans n'entre jamais dans l'entraînement."""
    windows = make_windows(datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 12, 31, tzinfo=UTC),
                           train_months=12, test_months=6)
    data = synthetic(730, signal=1.0, seed=5)
    start = pd.Timestamp(windows[0].test_start)
    straddling = data["setup_time"] < start
    data.loc[straddling, "exit_time"] = start + pd.Timedelta(days=1)          # tous clos APRÈS le début du test
    _, rows, _ = evaluate_windows(data, windows[:1], settings)
    assert rows[0]["train_trades"] == 0 and "skipped" in rows[0]


def test_too_few_trades_is_insufficient_not_a_verdict(settings):
    windows = make_windows(datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 6, 30, tzinfo=UTC),
                           train_months=12, test_months=6)
    small = synthetic(500, signal=2.0, seed=6)
    verdict, criteria, _ = judge(*evaluate_windows(small, windows, settings)[:2], settings)
    assert verdict == "INSUFFICIENT_DATA" and not criteria[0]["passed"]


def test_decision_features_are_dimensionless_and_causal_inputs_only():
    frame = pd.DataFrame({
        "decision_time": pd.to_datetime(["2024-01-01 10:15", "2024-01-01 22:45"], utc=True),
        "close": [100.0, 200.0], "atr14": [2.0, 4.0], "rsi14": [50.0, 70.0], "ema20": [99.0, 190.0],
        "ema50": [98.0, 180.0], "volume_ratio": [1.5, 0.0], "ret_1": [0.01, -0.01], "ret_4": [0.0, 0.02],
        "realized_vol_96": [0.02, 0.03], "bb_mid": [100.0, 200.0], "bb_sigma": [1.0, 0.0],
        "donchian_high": [101.0, 199.0], "ctx_ema50_slope": [0.001, -0.002], "ctx_atr_pct": [0.01, 0.02],
        "ctx_ret_24h": [0.02, -0.03], "ctx_close": [100.0, 200.0], "ctx_ema50": [95.0, 210.0],
        "btc_ret_24h": [0.01, 0.0], "btc_atr_pct": [0.01, 0.01], "btc_ema50_slope": [0.0, 0.0],
        "ctx_trend": ["BULL", "BEAR"], "ctx_volatility": ["HIGH", "NORMAL"],
    })
    out = features_at_decision(frame)
    assert out["atr_pct"].tolist() == [0.02, 0.02] and out["rsi"].tolist() == [0.5, 0.7]
    assert np.isnan(out.loc[1, "bb_z"])                       # écart-type nul : valeur absente, pas infinie
    assert out["trend_bull"].tolist() == [1.0, 0.0] and out["vol_high"].tolist() == [1.0, 0.0]
    assert out.loc[0, "hour_sin"] == pytest.approx(np.sin(2 * np.pi * 10.25 / 24))
