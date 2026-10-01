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
    predictions, rows, models = evaluate_windows(strong, windows, settings)
    kept = predictions[predictions["keep"]]
    deployable = filtered_trades(kept, symbols=np.where(np.arange(len(kept)) % 2, "ETHUSDT", "SOLUSDT"))
    verdict, criteria, oos = judge(predictions, rows, settings, filtered_central=deployable,
                                   filtered_adverse=deployable)
    assert verdict == "USEFUL_OOS", criteria
    assert oos["auc"] > 0.7 and oos["expectancy_r_kept_subset"] > oos["expectancy_r_all"]
    assert models and abs(models[-1].model.to_dict()["standardized_coefficients"]["atr_pct"]) > 0.5
    assert all(r["converged"] for r in rows if "skipped" not in r)

    noise = synthetic(1460, signal=0.0, seed=4)
    predictions, rows, _ = evaluate_windows(noise, windows, settings)
    verdict, criteria, oos = judge(predictions, rows, settings, filtered_central=filtered_trades(predictions),
                                   filtered_adverse=filtered_trades(predictions))
    assert verdict == "NOT_USEFUL" and not criteria[2]["passed"]       # Brier : pas mieux que le taux de base


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
    assert verdict == "INSUFFICIENT_DATA" and not criteria[1]["passed"]


def test_a_window_needs_ten_minority_events_per_feature(settings):
    """25 variables : moins de 250 événements de la classe minoritaire, fenêtre non jugée."""
    windows = make_windows(datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 12, 31, tzinfo=UTC),
                           train_months=12, test_months=6)
    thin = synthetic(120, signal=2.0, seed=7)                 # environ 360 trades sur 4 mois : trop peu
    thin["setup_time"] = thin["setup_time"] + pd.Timedelta(days=200)
    thin["exit_time"] = thin["setup_time"] + pd.Timedelta(hours=6)
    _, rows, models = evaluate_windows(thin, windows, settings)
    assert models == [] and all("skipped" in r for r in rows)


def filtered_trades(rows: pd.DataFrame, *, r_shift: float = 0.0, symbols=None) -> pd.DataFrame:
    """Trades re-simulés minimaux à partir de lignes de prédiction (colonnes lues par judge)."""
    return pd.DataFrame({"r_multiple": rows["r_multiple"].to_numpy() + r_shift,
                         "entry_time": rows["setup_time"].to_numpy(),
                         "symbol": symbols if symbols is not None else rows["symbol"].to_numpy()})


def test_deployable_strategy_must_survive_adverse_costs_and_concentration(settings):
    windows = make_windows(datetime(2021, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC),
                           train_months=12, test_months=6)
    predictions, rows, _ = evaluate_windows(synthetic(1460, signal=2.0, seed=3), windows, settings)
    kept = predictions[predictions["keep"]]
    good = filtered_trades(kept, symbols=np.where(np.arange(len(kept)) % 2, "ETHUSDT", "SOLUSDT"))
    verdict, criteria, _ = judge(predictions, rows, settings, filtered_central=good,
                                 filtered_adverse=filtered_trades(kept, r_shift=-2.0))
    assert verdict == "NOT_USEFUL" and not criteria[5]["passed"] and criteria[4]["passed"]    # adverse < 0
    one_pair = filtered_trades(kept, symbols=np.where(np.arange(len(kept)) % 10, "ETHUSDT", "SOLUSDT"))
    verdict, criteria, _ = judge(predictions, rows, settings, filtered_central=one_pair, filtered_adverse=one_pair)
    assert verdict == "NOT_USEFUL" and not criteria[6]["passed"]       # 90 % du PnL sur ETH
    verdict, criteria, _ = judge(predictions, rows, settings, integrity=(False, "trou"), filtered_central=good,
                                 filtered_adverse=good)
    assert verdict == "NOT_USEFUL" and not criteria[0]["passed"]


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


def test_features_are_joined_at_the_decision_bar_not_the_fill_bar():
    """Mutation ciblée : une jointure sur l'heure de remplissage lirait la bougie suivante."""
    from crypto_signal_intelligence.ml.meta import label_trades
    times = pd.date_range("2024-01-01 00:15", periods=6, freq="15min", tz="UTC")
    frame = pd.DataFrame({
        "decision_time": times, "close": 100.0, "atr14": 2.0, "rsi14": 50.0, "ema20": 99.0, "ema50": 98.0,
        "volume_ratio": 1.0, "ret_1": np.arange(6, dtype=float), "ret_4": 0.0, "realized_vol_96": 0.01,
        "bb_mid": 100.0, "bb_sigma": 1.0, "donchian_high": 101.0, "ctx_ema50_slope": 0.0, "ctx_atr_pct": 0.01,
        "ctx_ret_24h": 0.0, "ctx_close": 100.0, "ctx_ema50": 99.0, "btc_ret_24h": 0.0, "btc_atr_pct": 0.01,
        "btc_ema50_slope": 0.0, "ctx_trend": "BULL", "ctx_volatility": "NORMAL",
    })
    trades = pd.DataFrame({"entry_status": ["FILLED_OPEN"], "exit_reason": ["TP"], "setup_time": [times[2]],
                           "entry_time": [times[3]], "exit_time": [times[4]], "entry_limit": [100.0],
                           "stop": [98.0], "target": [104.0], "r_multiple": [1.9]})
    labelled = label_trades(trades, frame, "ETHUSDT")
    assert labelled["ret_1"].tolist() == [2.0]                  # ligne de décision (k = 2), pas k + 1
    assert labelled["stop_atr"].tolist() == [1.0] and labelled["target_r"].tolist() == [2.0]


def test_decision_features_do_not_depend_on_the_future():
    """Mêmes variables à l'instant t avec l'historique coupé à t ou complet."""
    from crypto_signal_intelligence.config import RegimeSection
    from crypto_signal_intelligence.features.builder import SetupFeatureParams, build_decision_frame

    from .conftest import canonical
    setup, ctx = canonical(3000, seed=1), canonical(800, "1h", seed=2)
    btc = canonical(800, "1h", seed=3, symbol="BTCUSDT")

    def build(s, c, b):
        return features_at_decision(build_decision_frame(s, c, b, setup_timeframe="15m", params=SetupFeatureParams(),
                                                         regimes=RegimeSection()))
    full = build(setup, ctx, btc)
    cut = setup["available_at"].iloc[2000]
    past = build(*(f[f["available_at"] <= cut] for f in (setup, ctx, btc)))
    t = setup["open_time"].iloc[2000] + pd.Timedelta(minutes=15)
    a, b = full[full["decision_time"] == t], past[past["decision_time"] == t]
    assert len(a) == 1
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_btc_context_too_old_is_unknown_for_the_model_as_for_the_strategy():
    frame = pd.DataFrame({
        "decision_time": pd.to_datetime(["2024-01-01 10:15"], utc=True),
        "available_at": pd.to_datetime(["2024-01-01 10:15:02"], utc=True),
        "btc_available_at": pd.to_datetime(["2024-01-01 07:00:02"], utc=True),       # 3 h : périmé
        "close": [100.0], "atr14": [2.0], "rsi14": [50.0], "ema20": [99.0], "ema50": [98.0], "volume_ratio": [1.0],
        "ret_1": [0.0], "ret_4": [0.0], "realized_vol_96": [0.01], "bb_mid": [100.0], "bb_sigma": [1.0],
        "donchian_high": [101.0], "ctx_ema50_slope": [0.0], "ctx_atr_pct": [0.01], "ctx_ret_24h": [0.0],
        "ctx_close": [100.0], "ctx_ema50": [99.0], "btc_ret_24h": [0.05], "btc_atr_pct": [0.01],
        "btc_ema50_slope": [0.0], "ctx_trend": ["BULL"], "ctx_volatility": ["NORMAL"],
    })
    out = features_at_decision(frame)
    assert out[["btc_ret_24h", "btc_atr_pct", "btc_ema50_slope"]].isna().all(axis=None)


def test_model_veto_frees_the_pair_for_the_next_setup():
    """Un setup refusé par le filtre libère la paire : le setup suivant (DUPLICATE sans filtre) est pris."""
    from crypto_signal_intelligence.backtest.simulator import simulate

    from .test_simulator import FLAT, OneShot, frame_from, rules
    bars = [FLAT] * 8
    plain = simulate(frame_from(bars), "TESTUSDT", OneShot({0, 1}), rules())
    assert plain.no_trade.get("DUPLICATE") == 1 and len(plain.trades) == 1
    first = frame_from(bars)["decision_time"].iloc[0].to_pydatetime()
    filtered = simulate(frame_from(bars), "TESTUSDT", OneShot({0, 1}), rules(),
                        decision_filter=lambda decision_time, levels: decision_time != first)
    assert filtered.no_trade.get("ML_FILTER") == 1 and "DUPLICATE" not in filtered.no_trade
    assert len(filtered.trades) == 1 and filtered.trades[0].setup_time != first
