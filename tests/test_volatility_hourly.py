"""Volatilité à toute heure, protocole v3 (docs/VOLATILITY.md § 16) : lignes horaires à la main, causalité et mutation,
profil heure × jour, purge glissante, trimestres, règle, bout en bout synthétique. SYNTHÉTIQUE."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import volatility as v1
from crypto_signal_intelligence.research import volatility_hourly as vh
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

NOW = datetime(2026, 10, 2, tzinfo=UTC)
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT")


def day(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def candles(returns, *, start: str = "2024-01-01") -> pd.DataFrame:
    frame = canonical(len(returns) + 1, "1h", start=start)
    return frame.assign(close=100 * np.exp(np.r_[0.0, np.cumsum(returns)]))


def hourly(symbol: str, *, days: int, seed: int, start: str = "2024-01-01") -> pd.DataFrame:
    return canonical(24 * days, "1h", symbol=symbol, start=start, seed=seed)


def test_declared_constants_match_the_document():
    text = (vh.__file__ and __import__("pathlib").Path(vh.__file__).resolve().parents[3] / "docs" / "VOLATILITY.md").read_text(encoding="utf-8")
    assert vh.N_TRIALS == 4 and "4 comparaisons" in text and pytest.approx(1 - 0.05 / 4) == vh.LEVEL
    assert all(model in text for model in vh.MODELS) and vh.HORIZONS == (4, 24) and vh.TRAIN_WINDOW.days == 1095
    assert [vh.block_days_for(h) for h in vh.HORIZONS] == [10, 10]


def test_hourly_rows_match_a_hand_computation():
    returns = np.r_[np.full(48, 0.01), np.full(30, 0.02)]
    frame = vh.hourly_frame(candles(returns)).set_index("origin")
    at = day("2024-01-03 01:00")                                            # clôture de la bougie ouverte à 00:00 le 3/1
    row = frame.loc[at]
    assert row["var_24"] == pytest.approx(0.01 ** 2) and row["log_var_24"] == pytest.approx(np.log(0.01 ** 2))
    assert row["rv2_4"] == pytest.approx(4 * 0.02 ** 2) and row["rv2_24"] == pytest.approx(24 * 0.02 ** 2)
    assert row["hour"] == 1 and row["dow"] == 2                              # mercredi 3 janvier 2024
    assert np.isnan(row["log_var_720"]) and np.isnan(frame.iloc[-1]["rv2_4"])  # pas 720 h d'historique ; pas 4 h après la fin
    assert len(frame) == 79


def test_variables_are_causal_and_a_one_hour_look_ahead_is_detected():
    h1 = hourly("BTCUSDT", days=40, seed=3)
    origins = [day("2024-02-01 13:00"), day("2024-02-05 03:00")]
    assert vh.causality_violations(h1, h1, origins=origins, seed=1) == []
    found = vh.causality_violations(h1, h1, origins=origins, seed=1, leaky=True)
    assert found and all("log_var_24" in v["features"] for v in found)


def test_profile_and_purged_rolling_training(monkeypatch):
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})
    series = {s: hourly(s, days=120, seed=i, start="2023-10-01") for i, s in enumerate(PAIRS)}
    data = pd.concat([vh.hourly_frame(f, series["BTCUSDT"]).assign(symbol=s) for s, f in series.items()], ignore_index=True)
    refit = day("2024-01-01")
    train = vh.training_rows(data, refit, 24)
    assert (train["origin"] + pd.Timedelta(hours=24) <= refit).all() and (train["origin"] > refit - vh.TRAIN_WINDOW).all()
    models = vh.fit_at(data, refit, 24, seed=1)
    assert models.har is not None and models.trees is not None and len(models.profile) == 7 * 24
    y = np.log(train["rv2_24"].to_numpy(float))
    assert models.profile.loc[(0, 0)] == pytest.approx(y[(train["dow"] == 0) & (train["hour"] == 0)].mean())
    quarter = data[(data["origin"] >= refit) & (data["origin"] < day("2024-01-05"))]
    out = vh.quarter_forecasts(models, quarter, 24)
    assert out["R0_RECENT_24H"].to_numpy() == pytest.approx(quarter["var_24"].to_numpy() * 24)
    assert np.isfinite(out[["H1_HAR_PROFILE", "H2_LGBM_PROFILE"]].to_numpy()).all() and (out[list(vh.MODELS)].to_numpy() > 0).all()


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for index, symbol in enumerate(PAIRS):                                           # 2023-10-10 → 2025-02-10
        store.save(hourly(symbol, days=490, seed=index, start="2023-10-10"), symbol, "1h")
    settings.protocol.development_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})
    monkeypatch.setattr(vh, "MIN_HISTORY_DAYS", 60)
    monkeypatch.setattr(vh, "FIRST_FORECAST", "2024-04-01")
    monkeypatch.setattr(v1, "YEARS", (2024, 2025))
    monkeypatch.setattr(v1, "MIN_YEARS_BETTER", 2)
    monkeypatch.setattr(vh, "code_state", lambda: "0123abcd")
    return settings


def test_run_audits_then_records_four_comparisons(stored):
    result = vh.run(stored, now=NOW, symbols=list(PAIRS))
    audit = result.leak_audit
    assert audit["passed"] and audit["checked_pairs"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"] and all(audit["mutation_detected"].values())
    assert result.n_trials == 4 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 4, abs=1e-6)
    assert [(r.model, r.horizon_days) for r in result.rows] == [(m, h) for h in vh.HORIZONS for m in vh.CANDIDATES]
    # Origines du 2024-04-01 00:00 à la fin de DEVELOPMENT moins H heures, 4 paires par heure : 306 jours à 4 h.
    assert {r.horizon_days: r.days for r in result.rows} == {4: 306, 24: 306}
    assert all(r.pairs == 4 and r.forecasts > 4 * 24 * 300 and r.ci_qlike_diff is not None for r in result.rows)
    assert result.verdict in {vh.USEFUL, vh.NO_IMPROVEMENT} and set(result.selected) == set(vh.HORIZONS)
    report = stored.reports_dir / result.run_id
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    assert summary["protocol_version"] == 3 and summary["horizons_hours"] == [4, 24]
    saved = pd.read_parquet(report / "forecasts_4h.parquet")
    assert list(saved.columns) == ["symbol", "origin", "realized", *vh.MODELS]
    assert (saved["origin"] + pd.Timedelta(hours=4) - pd.Timedelta(hours=1) <= pd.Timestamp(stored.protocol.development_end)).all()
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "VOLATILITY" and run["strategy"] == "VOLATILITY_HOURLY" and run["strategy_version"] == 3
    assert run["metrics"]["n_trials"] == 4 == ExperimentRegistry(stored.experiments_db).program_trials()


def test_a_first_forecast_inside_a_quarter_loses_no_origin(stored, monkeypatch):
    monkeypatch.setattr(vh, "FIRST_FORECAST", "2024-05-15")                           # pas un début de trimestre
    result = vh.run(stored, now=NOW, symbols=list(PAIRS))
    saved = pd.read_parquet(stored.reports_dir / result.run_id / "forecasts_4h.parquet")
    assert saved["origin"].min() == day("2024-05-15") and saved["origin"].dt.floor("D").nunique() == {r.horizon_days: r.days for r in result.rows}[4]
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["metrics"]["horizon_unit"] == "hours" and run["metrics"]["rows"][0]["horizon_unit"] == "hours"


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(vh, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(v1.DirtyCode):
        vh.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(vh, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(vh, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(v1.LeakAuditFailed):
        vh.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
