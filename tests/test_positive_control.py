"""Contrôle positif de la mesure (research/positive_control.py) : excès sur le même instant, corrélation de rang à la
main, score planté, rééchantillonnage par blocs, taille minimale détectable, bout en bout synthétique. SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research import positive_control as pc
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

NOW = datetime(2026, 10, 3, tzinfo=UTC)
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT")


def decisions(days: int = 120, pairs: int = 6, seed: int = 0, informative: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range("2024-01-01", periods=days * 6, freq="4h", tz="UTC")
    rows = []
    for t in times:
        net = rng.normal(0.001, 0.02, pairs)
        p = 0.5 + informative * (net - net.mean()) / 0.02 + rng.normal(0, 0.05, pairs)
        rows += [{"decision_time": t, "symbol": f"P{i}USDT", "p": p[i], "observed_net": net[i]} for i in range(pairs)]
    return pd.DataFrame(rows)


def test_excess_is_measured_against_the_same_instant_mean():
    table = pc.excess_table(decisions(days=2))
    assert np.allclose(table.groupby("time")["excess"].sum(), 0.0, atol=1e-12)
    thin = decisions(days=2, pairs=4)
    assert pc.excess_table(thin).empty                                       # moins de 5 paires par instant : retiré


def test_rank_ic_by_hand():
    times = pd.to_datetime(["2024-01-01 00:00"] * 5 + ["2024-01-01 04:00"] * 5, utc=True)
    table = pd.DataFrame({"time": times, "excess": [1.0, 2, 3, 4, 5, 1, 2, 3, 4, 5], "s": [1.0, 2, 3, 4, 5, 5, 4, 3, 2, 1]})
    ic = pc.daily_rank_ic(table, "s")
    assert len(ic) == 1 and ic.iloc[0] == pytest.approx(0.0)                # +1 puis −1 dans la même journée


def test_information_finds_an_informative_score_and_not_a_blind_one():
    blind = pc.information(pc.excess_table(decisions(days=260, seed=1)), "p", horizon_hours=4)     # 26 blocs de 10 jours
    sharp = pc.information(pc.excess_table(decisions(days=260, seed=1, informative=0.05)), "p", horizon_hours=4)
    assert not blind["detected"] and sharp["detected"] and sharp["rank_ic"] > 0.3
    assert sharp["top_minus_bottom_pct"] > 0 and len(sharp["excess_by_decile_pct"]) == 10


def test_planted_score_reaches_the_target_correlation():
    table = pc.excess_table(decisions(seed=2))
    rng = np.random.default_rng(0)
    table["s"] = pc.planted_score(table, 0.5, rng)
    ic = pc.daily_rank_ic(table, "s").mean()
    assert 0.35 < ic < 0.55
    table["s"] = pc.planted_score(table, 0.0, rng)
    assert abs(pc.daily_rank_ic(table, "s").mean()) < 0.05


def test_block_resampling_keeps_the_span_and_the_trades():
    times = pd.date_range("2024-01-01", periods=300, freq="12h", tz="UTC")
    trades = pd.DataFrame({"time": times, "r": np.arange(300, dtype=float)})
    sample = pc.resample_blocks(trades, np.random.default_rng(3))
    assert sample["time"].min() >= times[0].floor("D") and sample["time"].max() < times[0].floor("D") + pd.Timedelta(days=160)
    assert set(sample["r"]) <= set(trades["r"]) and 200 < len(sample) < 400


def test_minimum_detectable_size():
    rows = [{"g": "A", "d": 0.0, "detection_rate": 0.03}, {"g": "A", "d": 0.1, "detection_rate": 0.5},
            {"g": "A", "d": 0.2, "detection_rate": 0.85}, {"g": "B", "d": 0.0, "detection_rate": 0.02},
            {"g": "B", "d": 0.2, "detection_rate": 0.6}]
    assert pc.minimum_detectable(rows, "d", ("g",)) == {"A": 0.2, "B": None}


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 500, "1h", symbol=symbol, start="2023-09-01", seed=i, drift=0.00003 * (i - 2)), symbol, "1h")
    settings.protocol.development_end = datetime(2024, 12, 30, 23, 59, 59, tzinfo=UTC)
    settings.protocol.bootstrap_samples = 200
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(pc, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(pc, "FIRST_DAY", pd.Timestamp("2023-10-01", tz="UTC"))
    ml_dir = settings.reports_dir / "MLX"
    ml_dir.mkdir(parents=True)
    decisions(days=300, seed=4, informative=0.02).to_parquet(ml_dir / "decisions.parquet", index=False)
    wf_dir = settings.reports_dir / "WFX"
    wf_dir.mkdir(parents=True)
    rng = np.random.default_rng(5)
    times = pd.date_range("2024-01-01", periods=400, freq="16h", tz="UTC")
    pd.DataFrame({"setup_time": times, "entry_time": times, "r_multiple": rng.normal(0, 1, 400)}).to_csv(
        wf_dir / "trades_oos_base_central.csv", index=False)
    return settings


def test_run_records_a_zero_trial_control(stored):
    result = pc.run(stored, now=NOW, symbols=list(PAIRS), screen_reps=6, ml_reps=4, wf_reps=6,
                    ml_runs={"x": ("MLX", 4)}, wf_runs={"X": "WFX"})
    assert {r["horizon_days"] for r in result.screen} == {1, 7} and len(result.screen) == 10
    big = [r for r in result.screen if r["horizon_days"] == 1 and r["delta_pct"] == 1.0][0]
    assert big["detection_rate"] == 1.0 and big["mean_excess_estimate_pct"] == pytest.approx(1.0, abs=0.15)
    assert [r["rho"] for r in result.ml] == list(pc.RHOS) and result.ml[-1]["detection_rate"] == 1.0
    assert result.ml_real["x"]["detected"] and result.ml_real["x"]["run"] == "MLX"
    assert [r["delta_r"] for r in result.walk_forward] == list(pc.WF_DELTAS) and result.walk_forward[-1]["detection_rate"] == 1.0
    assert set(result.minimum_detectable) == {"screen_delta_pct", "ml_rho", "walk_forward_delta_r"}
    registry = ExperimentRegistry(stored.experiments_db)
    assert registry.get(result.run_id)["kind"] == "CONTROL" and registry.program_trials() == 0
