"""ML swing (lot 5 ter, docs/ML_SWING.md) : décisions toutes les 4 h, cibles, causalité et mutation, coupe
transversale, plis ancrés, règle d'admission v6, protocole de bout en bout. Données SYNTHÉTIQUES."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.ml import engine
from crypto_signal_intelligence.ml.swing import dataset as ds
from crypto_signal_intelligence.ml.swing import protocol as swing
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.risk.exposure import RiskLimits

from .conftest import canonical

NO_COSTS = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
COSTS = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)
H = pd.Timedelta(hours=1)


def hourly(n: int = 24 * 150, *, symbol: str = "ETHUSDT", seed: int = 0, start: str = "2024-01-01"):
    return canonical(n, "1h", symbol=symbol, start=start, seed=seed)


def test_declared_trials_and_protocol_version_match_the_document():
    assert swing.DECLARED_TRIALS == 136 and len(swing.SPECS) == 7
    assert {spec.family for spec in swing.SPECS} == {"logistic", "lightgbm", "xgboost", "catboost"}
    history = (Path(__file__).resolve().parents[1] / "docs" / "ML_SWING.md").read_text(encoding="utf-8")
    versions = [int(v) for v in re.findall(r"^- 2026-\d\d-\d\d, v(\d+)", history, flags=re.MULTILINE)]
    assert versions and max(versions) == swing.PROTOCOL_VERSION


def test_decisions_are_taken_every_four_hours_at_block_ends():
    frame = ds.pair_decisions(hourly(24 * 40), hourly(24 * 40, symbol="BTCUSDT", seed=1), symbol="ETHUSDT",
                              costs=COSTS, horizons=(24,), kinds=("fh",))
    assert (frame["decision_time"].dt.hour % 4 == 0).all()
    assert (frame["decision_time"].diff().dropna() == pd.Timedelta(hours=4)).all()
    assert set(swing.SWING.features) == set(ds.FEATURES) and "r_720" not in ds.FEATURES


def test_fixed_horizon_target_and_strict_barrier_target():
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60)))
    opens = np.r_[100, close[:-1]]
    frame = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC"), "open": opens,
                          "high": np.maximum(opens, close) * 1.01, "low": np.minimum(opens, close) * 0.99,
                          "close": close})
    net, bars = ds.targets(frame, NO_COSTS, 24, "fh")
    assert net[0] == pytest.approx(close[24] / opens[1] - 1) and bars[0] == 24
    delayed, delayed_bars = ds.targets(frame, NO_COSTS, 24, "fh", delay=1)
    assert delayed[0] == pytest.approx(close[25] / opens[2] - 1) and delayed_bars[0] == 25
    # triple barrière : un plus haut exactement égal à l'objectif ne compte pas (objectif DÉPASSÉ seulement)
    flat = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC"),
                         "open": 100.0, "high": 100.0, "low": 100.0,
                         "close": [100.0 * (1.01 if i % 2 else 0.99) for i in range(200)]})
    flat["open"] = flat["close"].shift(1).fillna(100.0)
    flat["high"], flat["low"] = flat[["open", "close"]].max(axis=1), flat[["open", "close"]].min(axis=1)
    sigma = ds.realized_vol(flat["close"]).iloc[180] * np.sqrt(4)
    entry = flat["open"].iloc[181]
    up = entry * (1 + ds.BARRIER_K * sigma)
    touch = flat.copy()
    touch.loc[182, "high"] = up                                           # contact exact : pas de sortie
    net_touch, bars_touch = ds.targets(touch, NO_COSTS, 4, "tb")
    assert bars_touch[180] != 2
    exceed = flat.copy()
    exceed.loc[182, "high"] = up * 1.001
    net_exceed, bars_exceed = ds.targets(exceed, NO_COSTS, 4, "tb")
    assert bars_exceed[180] == 2 and net_exceed[180] == pytest.approx(up / entry - 1)


def test_pair_features_are_causal_and_a_leaky_daily_bar_is_detected():
    h1, btc = hourly(24 * 150), hourly(24 * 150, symbol="BTCUSDT", seed=1)
    moments = [pd.Timestamp("2024-04-10 11:00", tz="UTC"), pd.Timestamp("2024-05-02 11:00", tz="UTC")]
    assert ds.causality_violations(h1, btc, decisions=moments) == []
    leaks = ds.causality_violations(h1, btc, decisions=moments, resampler=swing._leaky_resampler)
    assert leaks and any(f.startswith(("d1_", "h4_")) for leak in leaks for f in leak["features"])


def test_cross_section_ranks_pairs_at_the_same_decision_time():
    frame = pd.DataFrame({"decision_time": pd.to_datetime(["2024-01-01 04:00"] * 3 + ["2024-01-01 08:00"] * 2, utc=True),
                          "r_168": [0.1, -0.2, 0.05, 0.3, np.nan], "r_720": [0.0, 0.1, 0.2, 0.0, 0.1]})
    out = ds.add_cross_section(frame)
    assert list(out["xs_rank_168"][:3]) == [1.0, 1 / 3, 2 / 3]
    assert out["xs_excess_168"][0] == pytest.approx(0.1 - 0.05) and np.isnan(out["xs_rank_168"][4])
    assert out["xs_rank_168"][3] == 1.0


def test_folds_are_anchored_with_six_validations():
    from crypto_signal_intelligence.config import load_settings
    settings = load_settings()
    folds = swing.SWING.folds(settings, swing.SWING.first_valid(settings),
                              pd.Timestamp("2025-06-30 23:59:59", tz="UTC"))
    assert len(folds) == 6 and all(f.train_start == pd.Timestamp("2021-01-01", tz="UTC") for f in folds)
    assert folds[0].valid_start == pd.Timestamp("2022-07-01", tz="UTC")
    assert folds[0].calib_start == pd.Timestamp("2022-04-01", tz="UTC")
    assert folds[-1].valid_end == pd.Timestamp("2025-06-30 23:59:59", tz="UTC")


def test_strict_rule_needs_enough_trades_in_each_counted_validation():
    system = engine.System("fh", 24, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    rows = [system.to_dict() | {"key": "peu", "fold": i, "sharpe": 1.0, "trades": 10 if i < 2 else 40,
                                "status": "OK"} for i in range(6)]
    rows += [system.to_dict() | {"key": "assez", "fold": i, "sharpe": 1.0 if i < 5 else -1.0, "trades": 30,
                                 "status": "OK"} for i in range(6)]
    table = engine.summarize(pd.DataFrame(rows), swing.RULE).set_index("key")
    assert table.loc["peu", "positive_folds"] == 4 and not table.loc["peu", "stable"]
    assert table.loc["assez", "positive_folds"] == 5 and table.loc["assez", "stable"]
    assert swing.RULE.required_positive(6) == 5


# --- Bout en bout ----------------------------------------------------------------------------------------

@pytest.fixture
def small_swing(monkeypatch, settings):
    """Le protocole swing en miniature : 3 paires, 9 mois, logistique seule, validations d'un mois."""
    rule = replace(swing.RULE, min_trades=5, min_trades_per_fold=1)
    small = replace(swing.SWING, specs=(swing.SPECS[0],), margins=(0.0,), horizons=(24,), first_valid_months=5,
                    calib_months=1, valid_months=1, random_draws=3, selection=rule)
    monkeypatch.setitem(engine.PROGRAMS, swing.STRATEGY_ID, small)
    store = CandleStore(settings.data_dir)
    for i, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        store.save(canonical(4 * 24 * 10, "15m", symbol=symbol, start="2024-09-20", seed=i), symbol, "15m")
        store.save(canonical(24 * 273, "1h", symbol=symbol, start="2024-01-01", seed=i + 7), symbol, "1h")
    settings.data.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    settings.data.history_start = datetime(2024, 1, 1, tzinfo=UTC).date()
    settings.protocol.development_end = datetime(2024, 9, 29, 23, 59, 59, tzinfo=UTC)
    settings.protocol.bootstrap_samples = 50
    return settings, small


def test_recomputed_scenario_targets_align_with_swing_decisions(small_swing):
    settings, small = small_swing
    prep = small.prepare(settings, end=pd.Timestamp("2024-09-29 23:59:59", tz="UTC"), progress=lambda _t: None)
    assert prep.program is small and (prep.meta["decision_time"].dt.hour % 4 == 0).all()
    for kind in ds.TARGETS:
        net, bars = swing.scenario_targets(prep, kind, 24, settings.costs["central"], 0)
        np.testing.assert_allclose(net, prep.meta[f"net_{kind}_24"].to_numpy(float), rtol=1e-6, equal_nan=True)
        assert (bars == prep.meta[f"bars_{kind}_24"].to_numpy()).all()


def test_swing_selection_runs_end_to_end_with_the_strict_rule(small_swing):
    settings, small = small_swing
    now = datetime(2026, 10, 1, tzinfo=UTC)
    result = swing.select(settings, now=now, allow_dirty=True, program=small)
    payload = result.payload
    assert result.leak_audit["passed"] and payload["program"] == swing.STRATEGY_ID
    assert payload["n_trials"] == 2 + len(swing.FAMILY_VARIANTS) + 2
    assert payload["selection_rule"]["strict"] and len(payload["folds"]) == 4
    for name in ("summary.json", "report.md", "grid.csv", "decisions.parquet"):
        assert (result.report_dir / name).exists(), name
    run = ExperimentRegistry(settings.experiments_db).get(result.run_id)
    assert run["kind"] == swing.KIND_SELECT and run["status"] == "COMPLETED"
    assert set(payload["strict_checks"]) == set(result.summary.loc[result.summary["stable"], "key"])


def test_strict_checks_report_every_criterion(small_swing):
    settings, small = small_swing
    prep = small.prepare(settings, end=pd.Timestamp("2024-09-29 23:59:59", tz="UTC"), progress=lambda _t: None)
    folds = small.folds(settings, small.first_valid(settings), pd.Timestamp("2024-09-29 23:59:59", tz="UTC"))
    system = engine.System("fh", 24, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    runs = engine.run_system(prep, system, folds, RiskLimits(), seed=1)
    details = engine.strict_checks(prep, system, runs, RiskLimits(), settings=settings, rule=small.selection)
    assert set(details["checks"]) == {"ic_gain_moyen_positif", "positif_couts_defavorables",
                                      "positif_sans_meilleurs_trades", "concentration_limitee"}
    assert details["passed"] == all(details["checks"].values())
