"""Registre unifié et rapport d'information (research/information_report.py) : schéma commun, sections ML,
walk-forward et volatilité, 0 essai. SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research import information_report as ir
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

NOW = datetime(2026, 10, 3, tzinfo=UTC)


def decisions(days: int = 300, pairs: int = 6, seed: int = 0, informative: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for t in pd.date_range("2023-01-01", periods=days * 6, freq="4h", tz="UTC"):
        net = rng.normal(0.0, 0.02, pairs)
        p = 0.5 + informative * (net - net.mean()) / 0.02 + rng.normal(0, 0.05, pairs)
        rows += [{"decision_time": t, "symbol": f"P{i}USDT", "p": p[i], "observed_net": net[i], "cost_round_trip_bps": 21.0,
                  "decision": "ENTER" if p[i] > 0.6 else "ABSTAIN"} for i in range(pairs)]
    return pd.DataFrame(rows)


def trades(n: int = 300, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range("2023-01-01", periods=n, freq="20h", tz="UTC")
    r = rng.normal(-0.1, 1.0, n)
    return pd.DataFrame({"symbol": "BTCUSDT", "setup_time": times, "entry_time": times, "exit_time": times + pd.Timedelta(hours=5),
                         "r_multiple": r, "gross_return": r * 0.01 + 0.002, "net_return": r * 0.01, "trend_regime": np.where(r > 0, "BULL", "RANGE"),
                         "volatility_regime": "HIGH", "entry_status": "FILLED", "mae_r": -0.5, "mfe_r": 0.7})


def test_report_reads_every_source_into_one_schema(settings):
    ml_dir = settings.reports_dir / "MLX"
    ml_dir.mkdir(parents=True)
    decisions().to_parquet(ml_dir / "decisions.parquet", index=False)
    wf_dir = settings.reports_dir / "WFX"
    wf_dir.mkdir(parents=True)
    for scenario in ir.SCENARIOS:
        trades().to_csv(wf_dir / f"trades_oos_base_{scenario}.csv", index=False)
    vol_dir = settings.reports_dir / "VOLX"
    vol_dir.mkdir(parents=True)
    rng = np.random.default_rng(2)
    forecast = np.exp(rng.normal(-8, 0.5, 2000))
    pd.DataFrame({"symbol": "A", "origin": pd.Timestamp("2024-01-01", tz="UTC"), "horizon": 1, "model": ir.VOL_MODEL,
                  "forecast": forecast, "realized": forecast * np.exp(rng.normal(0, 0.3, 2000))}).to_parquet(vol_dir / "forecasts.parquet", index=False)
    report = ir.run(settings, now=NOW, allow_dirty=True, ml_sources={"ML_X": ("MLX", 4)}, wf_sources={"WF_X": "WFX"}, vol_source="VOLX")
    ml = report.ml["ML_X"]
    assert ml["detected"] and ml["rank_ic"] > 0.1 and ml["top_decile"]["excess_pct"] > 0 and len(ml["excess_by_decile_pct"]) == 10
    assert ml["top_decile"]["gross_pct"] - ml["top_decile"]["net_pct"] == close_to(0.21)
    wf = report.walk_forward["WF_X"]
    assert set(wf["scenarios"]) == set(ir.SCENARIOS) and set(wf["by_trend"]) == {"BULL", "RANGE"} and wf["by_trend"]["BULL"]["r_mean"] > 0
    assert report.volatility["1d"]["log_slope"] > 0.8 and len(report.volatility["1d"]["realized_over_forecast_by_decile"]) == 10
    saved = pd.read_parquet(settings.reports_dir / report.run_id / "predictions.parquet")
    assert list(saved.columns) == list(ir.UNIFIED) and set(saved["family"]) == {"ML", "WALK_FORWARD"}
    assert report.cells > 0 and report.expected_false_alarms == round(report.cells * 0.05, 1)
    registry = ExperimentRegistry(settings.experiments_db)
    assert registry.get(report.run_id)["kind"] == "REPORT" and registry.program_trials() == 0


def close_to(value):
    import pytest
    return pytest.approx(value, abs=1e-6)
