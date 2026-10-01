"""Criblage du marché à terme (docs/DERIVATIVES.md, protocole v2) de bout en bout, sur données SYNTHÉTIQUES."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.derivatives import features as fx
from crypto_signal_intelligence.derivatives.history import DerivativesStore
from crypto_signal_intelligence.research import derivatives_screen as screen
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.intervals import calendar_mean_ci

from .test_derivatives_features import synthetic

NOW = datetime(2026, 10, 1, tzinfo=UTC)
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.fixture
def stored(settings, monkeypatch):
    candles, derivatives = CandleStore(settings.data_dir), DerivativesStore(settings.data_dir)
    for i, symbol in enumerate(PAIRS):
        spot, funding, premium, metrics = synthetic(seed=i, days=200, symbol=symbol)
        candles.save(spot, symbol, "1h")
        for name, frame in (("funding", funding), ("premium", premium), ("metrics", metrics)):
            derivatives.merge(name, symbol, frame)
    settings.data.symbols = list(PAIRS)
    settings.data.history_start = datetime(2024, 1, 1, tzinfo=UTC).date()
    settings.protocol.development_end = datetime(2024, 7, 10, 23, 59, 59, tzinfo=UTC)
    # Couverture exigée ramenée à la période synthétique (en réel : 2021-04 et 2022-01).
    monkeypatch.setattr(screen, "LATEST_START", {k: pd.Timestamp("2024-01-02", tz="UTC") for k in screen.LATEST_START})
    return settings


def test_screen_audits_then_measures_the_declared_trials_only_on_development(stored):
    result = screen.run(stored, now=NOW)
    assert result.leak_audit["passed"] and result.leak_audit["checked_pairs"] == list(PAIRS)
    assert result.leak_audit["mutation_by_family"] == {"funding": True, "premium": True, "metrics": True}
    assert result.n_trials == 12 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 12, abs=1e-6)
    assert {(r.condition, r.horizon_h) for r in result.rows} == {(c, h) for c in fx.CONDITIONS for h in (24, 72, 168)}
    assert all(r.events > 0 for r in result.rows if r.horizon_h == 24)
    assert set(result.data_hashes) == set(PAIRS) and all(len(h) == 4 for h in result.data_hashes.values())
    report = stored.reports_dir / result.run_id / "summary.json"
    assert report.exists() and '"conditions"' in report.read_text(encoding="utf-8")
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "SCREEN" and run["strategy"] == "SCREEN_DERIVATIVES" and run["metrics"]["n_trials"] == 12
    assert run["period_end"].startswith("2024-07-10") and run["data_hashes"] == result.data_hashes
    assert run["metrics"]["verdict"] in ("AUCUNE_PISTE",) or run["metrics"]["verdict"].endswith("PISTE(S) À CONFIRMER")
    verdicts = {m["strategy"]: m["verdict"] for m in CsiApi(stored).dispatch("GET", "/models", {}, None)["models"]}
    assert verdicts["SCREEN_DERIVATIVES"] == run["metrics"]["verdict"]     # l'onglet Suivi lit le verdict enregistré


def test_nothing_after_the_end_of_development_is_read(stored):
    first = screen.run(stored, now=NOW)
    end = pd.Timestamp(stored.protocol.development_end)
    rng = np.random.default_rng(0)
    candles, derivatives = CandleStore(stored.data_dir), DerivativesStore(stored.data_dir)
    for symbol in PAIRS:                                                  # futur falsifié, Spot et marché à terme
        spot = candles.load(symbol, "1h")
        later = spot["open_time"] > end
        for column in ("open", "high", "low", "close"):
            spot.loc[later, column] = spot.loc[later, column] * rng.uniform(0.5, 1.5, int(later.sum()))
        candles.save(spot, symbol, "1h")
        for name in ("funding", "premium", "metrics"):
            frame = derivatives.load(name, symbol)
            later = frame["time"] > end
            for column in frame.columns.difference(["time", "available_at"]):
                frame.loc[later, column] = frame.loc[later, column] * rng.uniform(0.5, 1.5, int(later.sum()))
            frame.to_parquet(derivatives.path(name, symbol), index=False)
    second = screen.run(stored, now=NOW)
    assert [asdict(r) for r in second.rows] == [asdict(r) for r in first.rows]
    assert second.data_hashes == first.data_hashes


def test_incomplete_data_is_refused_before_any_trial(stored):
    DerivativesStore(stored.data_dir).path("metrics", "SOLUSDT").unlink()
    with pytest.raises(screen.IncompleteData, match="SOLUSDT metrics"):
        screen.run(stored, now=NOW)
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0


def test_the_leak_audit_needs_its_three_pairs(stored, monkeypatch):
    monkeypatch.setattr(screen, "check_complete", lambda *args, **kwargs: {})
    DerivativesStore(stored.data_dir).path("metrics", "SOLUSDT").unlink()
    with pytest.raises(screen.LeakAuditFailed):
        screen.run(stored, now=NOW)
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0


def test_screen_produces_nothing_when_a_leak_would_go_unnoticed(stored, monkeypatch):
    blind = {name: (fx.BUILDERS[name], family) for name, (_, family) in fx.MUTATIONS.items()}   # « mutation » inerte
    monkeypatch.setattr(fx, "MUTATIONS", blind)
    with pytest.raises(screen.LeakAuditFailed):
        screen.run(stored, now=NOW)
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0      # aucun essai enregistré


# --- Règle « piste » et mesures (cas construits) -------------------------------------------------------------

def crafted(event_returns, *, pairs=("A", "B", "C"), years=(2022, 2023, 2024)):
    """Trois ans de décisions (04:00 et 08:00) pour 7 paires ; événements à 04:00 un jour sur 10 pour `pairs`
    pendant `years`, de rendement tiré de `event_returns` ; 0 ailleurs."""
    days = pd.date_range("2022-01-01", "2024-12-31", freq="D", tz="UTC")
    values = iter(event_returns)
    rows = []
    for index, day in enumerate(days):
        for hour in (4, 8):
            for pair in "ABCDEFG":
                event = hour == 4 and index % 10 == 0 and pair in pairs and day.year in years
                rows.append((day + pd.Timedelta(hours=hour), pair, next(values) if event else 0.0, event))
    frame = pd.DataFrame(rows, columns=["decision_time", "symbol", "ret_24", "event"])
    return frame, np.ones(len(frame), dtype=bool), frame["event"].to_numpy()


def constant(value):
    while True:
        yield value


def test_a_lead_needs_costs_a_positive_interval_and_spread_results():
    noisy = iter(np.random.default_rng(3).normal(0.012, 0.004, 1000))
    frame, evaluable, events = crafted(noisy)
    row = screen._row("FUNDING_LOW", 24, frame, evaluable, events, hurdle_pct=0.26)
    assert row.lead and row.ci_excess_pct[0] > 0 and row.max_pair_share <= 0.6 and row.max_year_share <= 0.6
    hits = frame[events]
    drift = frame.groupby("symbol")["ret_24"].mean()
    excess = hits["ret_24"].to_numpy() - hits["symbol"].map(drift).to_numpy()
    expected = calendar_mean_ci(excess, hits["decision_time"], block_days=10, min_blocks=20, level=screen.LEVEL)
    assert row.ci_excess_pct == pytest.approx([v * 100 for v in expected], abs=1e-4)     # niveau Bonferroni
    loose = calendar_mean_ci(excess, hits["decision_time"], block_days=10, min_blocks=20, level=0.95)
    assert row.ci_excess_pct[0] < loose[0] * 100                                        # plus large qu'à 95 %
    below_costs = screen._row("FUNDING_LOW", 24, *crafted(constant(0.002)), hurdle_pct=0.26)
    assert below_costs.ci_excess_pct[0] > 0 and not below_costs.lead                   # IC > 0 mais sous les coûts
    one_pair = screen._row("FUNDING_LOW", 24, *crafted(constant(0.012), pairs=("A",)), hurdle_pct=0.26)
    assert one_pair.max_pair_share == 1.0 and not one_pair.lead
    one_year = screen._row("FUNDING_LOW", 24, *crafted(constant(0.012), years=(2022,)), hurdle_pct=0.26)
    assert one_year.max_year_share == 1.0 and not one_year.lead
    swinging = (0.004 + (0.05 if i % 2 else -0.05) for i in range(1000))
    wide = screen._row("FUNDING_LOW", 24, *crafted(swinging), hurdle_pct=0.26)
    assert wide.mean_return_pct > 0.26 and wide.ci_excess_pct[0] < 0 and not wide.lead


def test_cross_sectional_excess_leaves_the_pair_out_and_needs_five_others():
    t1, t2 = pd.Timestamp("2024-05-01 04:00", tz="UTC"), pd.Timestamp("2024-05-02 04:00", tz="UTC")
    frame = pd.DataFrame({"decision_time": [t1] * 7 + [t2] * 4, "symbol": list("ABCDEFG") + list("ABCD"),
                          "ret_24": [0.05] + [0.01] * 6 + [0.05, 0.01, 0.01, 0.01]})
    events = np.array([True] + [False] * 6 + [True, False, False, False])
    row = screen._row("OI_FLUSH", 24, frame, np.ones(len(frame), dtype=bool), events, hurdle_pct=0.26)
    assert row.events == 2 and row.mean_cross_excess_pct == pytest.approx(4.0)      # seul t1 a 6 autres paires
