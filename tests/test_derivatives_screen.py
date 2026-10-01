"""Criblage du marché à terme (docs/DERIVATIVES.md) de bout en bout, sur données SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.derivatives import features as fx
from crypto_signal_intelligence.derivatives.history import DerivativesStore
from crypto_signal_intelligence.research import derivatives_screen as screen
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .test_derivatives_features import synthetic

NOW = datetime(2026, 10, 1, tzinfo=UTC)
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.fixture
def stored(settings):
    candles, derivatives = CandleStore(settings.data_dir), DerivativesStore(settings.data_dir)
    for i, symbol in enumerate(PAIRS):
        spot, funding, premium, metrics = synthetic(seed=i, days=200, symbol=symbol)
        candles.save(spot, symbol, "1h")
        for name, frame in (("funding", funding), ("premium", premium), ("metrics", metrics)):
            derivatives.merge(name, symbol, frame)
    settings.data.symbols = list(PAIRS)
    settings.data.history_start = datetime(2024, 1, 1, tzinfo=UTC).date()
    settings.protocol.development_end = datetime(2024, 7, 10, 23, 59, 59, tzinfo=UTC)
    return settings


def test_screen_audits_then_measures_the_declared_trials_only_on_development(stored):
    result = screen.run(stored, now=NOW)
    assert result.leak_audit["passed"] and result.leak_audit["checked_pairs"] == list(PAIRS)
    assert result.leak_audit["mutation_by_family"] == {"funding": True, "premium": True, "metrics": True}
    assert result.n_trials == 12 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 12, abs=1e-6)
    assert {(r.condition, r.horizon_h) for r in result.rows} == {(c, h) for c in fx.CONDITIONS for h in (24, 72, 168)}
    assert all(r.events > 0 for r in result.rows if r.horizon_h == 24)
    report = stored.reports_dir / result.run_id / "summary.json"
    assert report.exists() and '"conditions"' in report.read_text(encoding="utf-8")
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "SCREEN" and run["strategy"] == "SCREEN_DERIVATIVES" and run["metrics"]["n_trials"] == 12
    assert run["period_end"].startswith("2024-07-10")
    verdicts = {m["strategy"]: m["verdict"] for m in CsiApi(stored).dispatch("GET", "/models", {}, None)["models"]}
    assert "SCREEN_DERIVATIVES" in verdicts                                  # visible dans l'onglet Suivi


def test_screen_produces_nothing_when_a_leak_would_go_unnoticed(stored, monkeypatch):
    blind = {name: (fx.BUILDERS[name], family) for name, (_, family) in fx.MUTATIONS.items()}   # « mutation » inerte
    monkeypatch.setattr(fx, "MUTATIONS", blind)
    with pytest.raises(screen.LeakAuditFailed):
        screen.run(stored, now=NOW)
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0      # aucun essai enregistré


def test_excess_is_measured_against_the_same_evaluable_period():
    times = pd.to_datetime(["2024-05-01 04:00", "2024-05-01 08:00", "2024-05-02 04:00", "2024-05-02 08:00"], utc=True)
    frame = pd.DataFrame({"decision_time": list(times) * 2, "symbol": ["A"] * 4 + ["B"] * 4,
                          "ret_24": [0.01, 0.03, 0.02, 0.04, -0.01, 0.0, 0.01, 0.02]})
    evaluable = pd.Series([True, True, True, False, True, True, True, True]).to_numpy()
    events = pd.Series([True, False, False, False, True, False, False, False]).to_numpy()
    row = screen._row("FUNDING_LOW", 24, frame, evaluable, events, hurdle_pct=0.26)
    # dérive de A sur ses décisions évaluables (0,01 ; 0,03 ; 0,02) = 0,02 ; de B = 0,005
    assert row.events == 2 and row.mean_excess_pct == pytest.approx(((0.01 - 0.02) + (-0.01 - 0.005)) / 2 * 100)
    # transversal : au premier instant, moyenne des paires évaluables = 0
    assert row.mean_cross_excess_pct == pytest.approx(((0.01 - 0.0) + (-0.01 - 0.0)) / 2 * 100)
    assert row.ci_excess_pct is None and not row.beats_costs                  # trop peu de blocs : pas d'IC
