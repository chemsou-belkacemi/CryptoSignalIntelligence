"""Intervalles de rendement (docs/QUANTILES.md) : rendements à terme et pinball à la main, référence Q0, conforme
adaptatif causal et sa mutation, purge, bout en bout synthétique avec une table σ̂ en service factice. SYNTHÉTIQUE."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import quantiles as q
from crypto_signal_intelligence.research import volatility as v1
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

NOW = datetime(2026, 10, 2, tzinfo=UTC)
DOC = Path(__file__).resolve().parents[1] / "docs" / "QUANTILES.md"
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT")


def day(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def candles(returns, *, start: str = "2024-01-01") -> pd.DataFrame:
    frame = canonical(len(returns) + 1, "1h", start=start)
    return frame.assign(close=100 * np.exp(np.r_[0.0, np.cumsum(returns)]))


def hourly(symbol: str, *, days: int, seed: int, start: str = "2024-01-01") -> pd.DataFrame:
    return canonical(24 * days, "1h", symbol=symbol, start=start, seed=seed)


def test_declared_constants_match_the_document():
    text = DOC.read_text(encoding="utf-8")
    assert q.N_TRIALS == 6 and "6 comparaisons" in text and pytest.approx(1 - 0.05 / 6) == q.LEVEL
    assert q.QUANTILES == (0.05, 0.25, 0.75, 0.95) and q.GAMMA == 0.005 and q.COVERAGE_TOL == 0.03
    assert all(model in text for model in q.MODELS) and q.SERVICE_RUN in text


def test_forward_log_returns_by_hand():
    returns = np.full(24 * 9, 0.001)
    table = q.forward_log_returns(candles(returns)).set_index("origin")
    origin = day("2024-01-02")                                              # clôture de la bougie de 23:00 du 1er
    assert table.loc[origin, "ret_1"] == pytest.approx(24 * 0.001) and table.loc[origin, "ret_7"] == pytest.approx(7 * 24 * 0.001)
    assert np.isnan(table.loc[day("2024-01-10"), "ret_1"])                   # la clôture du 11 n'existe pas


def test_pinball_and_total_pinball():
    assert q.pinball(np.array([1.0]), np.array([0.0]), 0.95)[0] == pytest.approx(0.95)       # r > q : α (r − q)
    assert q.pinball(np.array([-1.0]), np.array([0.0]), 0.95)[0] == pytest.approx(0.05)      # r < q : (1 − α)(q − r)
    total = q.total_pinball(np.array([0.0]), {a: np.array([0.0]) for a in q.QUANTILES})
    assert total[0] == 0.0


def test_adaptive_sequence_is_causal_and_moves_levels_towards_the_target():
    times = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    z = np.full(60, 5.0)                                                     # toujours au-dessus des seuils : couverture nulle
    qz = lambda levels: np.asarray(levels, float) * 0.0  # noqa: E731 - seuils nuls : 1{z ≤ 0} vaut 0
    levels, thresholds = q.adaptive_sequence(times, z, qz, 3)
    assert np.allclose(levels[:3], q.QUANTILES)                              # rien de résolu avant 3 jours
    assert (np.diff(levels[3:], axis=0) >= 0).all() and levels[-1, 0] > 0.05  # les niveaux montent : couverture trop basse
    truncated, _ = q.adaptive_sequence(times[:30], z[:30], qz, 3)
    assert np.allclose(levels[:30], truncated)                               # ne dépend que des origines résolues
    mutated, _ = q.adaptive_sequence(times, z, qz, 3, lag_days=0)
    assert not np.allclose(levels, mutated)                                  # lire l'origine du jour change les niveaux
    state, pending = np.array(q.QUANTILES, float), []
    first, _ = q.adaptive_sequence(times[:30], z[:30], qz, 3, state=state, pending=pending)
    second, _ = q.adaptive_sequence(times[30:], z[30:], qz, 3, state=state, pending=pending)
    assert np.allclose(np.vstack([first, second]), levels)                   # enchaînement d'un mois à l'autre


def fake_sigma(settings, pairs=PAIRS, *, start="2023-12-15", days=420) -> Path:
    """Table de prévisions « en service » factice (REF_SERVICE) : variance 24·H × 0,0001 pour chaque (paire, origine, H)."""
    origins = pd.date_range(start, periods=days, freq="D", tz="UTC")
    rows = []
    for symbol in pairs:
        for horizon in q.HORIZONS:
            rows.append(pd.DataFrame({"symbol": symbol, "origin": origins, "horizon": horizon, "model": q.SERVICE_MODEL,
                                      "forecast": 24 * horizon * 1e-4, "realized": np.nan}))
    path = settings.reports_dir / q.SERVICE_RUN / "forecasts.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(rows, ignore_index=True).to_parquet(path, index=False)
    return path


def test_rows_join_features_returns_and_service_sigma(settings):
    fake_sigma(settings)
    series = {s: hourly(s, days=120, seed=i, start="2023-10-01") for i, s in enumerate(PAIRS)}
    frames = {s: v1.daily_frame(f, series["BTCUSDT"]) for s, f in series.items()}
    returns = {s: q.forward_log_returns(f) for s, f in series.items()}
    rows = q.build_rows(frames, returns, q.load_service_sigma(settings))
    part = rows[3]
    assert set(part["symbol"]) == set(PAIRS) and part["origin"].min() >= day("2023-12-15")
    assert np.allclose(part["z"], part["ret"] / part["sigma"], equal_nan=True) and (part["log_sigma"] == np.log(part["sigma"])).all()
    train = q.training_rows(part, day("2024-01-10"), 3)
    assert (train["origin"] + pd.Timedelta(days=3) <= day("2024-01-10")).all() and np.isfinite(train["ret"]).all()


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for index, symbol in enumerate(PAIRS):                                           # 2023-10-10 → 2025-02-10
        store.save(hourly(symbol, days=490, seed=index, start="2023-10-10"), symbol, "1h")
    settings.protocol.development_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    fake_sigma(settings, start="2024-02-15", days=400)
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(q, "LGBM_PARAMS", q.LGBM_PARAMS | {"min_data_in_leaf": 20})
    monkeypatch.setattr(v1, "FIRST_FORECAST", "2024-03-01")
    monkeypatch.setattr(v1, "YEARS", (2024, 2025))
    monkeypatch.setattr(q, "MIN_YEARS_COVERED", 1)
    monkeypatch.setattr(q, "code_state", lambda: "0123abcd")
    return settings


def test_run_audits_then_records_six_comparisons(stored):
    result = q.run(stored, now=NOW, symbols=list(PAIRS))
    audit = result.leak_audit
    assert audit["passed"] and audit["mutation_detected"] and not audit["violations"] and len(audit["checked_pairs"]) == 3
    assert result.n_trials == 6 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 6, abs=1e-6)
    assert [(r.model, r.horizon_days) for r in result.rows] == [(m, h) for h in q.HORIZONS for m in q.CANDIDATES]
    assert all(r.pairs == 4 and r.days > 250 and r.ci_pinball_diff is not None for r in result.rows)
    assert all(set(r.coverage_90_by_year) == {"2024", "2025"} for r in result.rows)
    assert result.verdict in {q.USEFUL, q.NO_IMPROVEMENT} and set(result.selected) == set(q.HORIZONS)
    report = stored.reports_dir / result.run_id
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    assert summary["protocol_version"] == 1 and len(summary["baseline_rows"]) == 3 and summary["baseline_rows"][0]["pinball_diff"] == 0.0
    saved = pd.read_parquet(report / "quantiles_3d.parquet")
    assert {"Q0_SERVICE_EMPIRIQUE_5", "C1_LGBM_QUANTILE_95", "C2_CONFORME_ADAPTATIF_25"} <= set(saved.columns)
    assert (saved["origin"] + pd.Timedelta(days=3) <= pd.Timestamp(stored.protocol.development_end)).all()
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "QUANTILES" and run["strategy"] == "RETURN_QUANTILES" and run["metrics"]["n_trials"] == 6
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 6 and "service_sigma" in result.data_hashes


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(q, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(v1.DirtyCode):
        q.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(q, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(q, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(q.LeakAuditFailed):
        q.run(stored, now=NOW, symbols=list(PAIRS))
    with pytest.raises(v1.IncompleteData):
        q.run(stored, now=NOW, symbols=list(PAIRS), service_run="VOL-INEXISTANT")
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
