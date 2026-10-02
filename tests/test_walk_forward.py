"""Walk-forward, sélection, critères d'admission et fenêtre de décisions (valeurs synthétiques)."""
import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.backtest.simulator import simulate
from crypto_signal_intelligence.config import AdmissionSection
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.domain.enums import ValidationVerdict
from crypto_signal_intelligence.research import admission
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.walk_forward import (
    Window,
    make_windows,
    run,
    select_combo,
    train_score,
)

from .conftest import canonical
from .test_simulator import FLAT, OneShot, frame_from, rules

UTC0 = datetime(2021, 1, 1, tzinfo=UTC)


def test_windows_are_anchored_contiguous_and_end_with_the_period():
    end = datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC)
    windows = make_windows(UTC0, end, train_months=18, test_months=6)
    assert len(windows) == 6
    assert windows[0].test_start == datetime(2022, 7, 1, tzinfo=UTC)
    assert all(w.train_start == UTC0 for w in windows)
    for previous, current in zip(windows, windows[1:], strict=False):
        assert current.test_start - previous.test_end == timedelta(seconds=1)
    assert windows[-1].test_end == end
    assert make_windows(UTC0, datetime(2022, 1, 1, tzinfo=UTC), train_months=18, test_months=6) == []


def test_training_score_purges_trades_that_end_inside_the_test():
    window = Window(0, UTC0, datetime(2022, 7, 1, tzinfo=UTC), datetime(2022, 12, 31, tzinfo=UTC))
    trades = pd.DataFrame({
        "entry_status": ["FILLED_OPEN"] * 3 + ["EXPIRED"],
        "exit_reason": ["TP", "SL", "TP", None],
        "setup_time": pd.to_datetime(["2022-01-10 00:00", "2022-06-30 23:00", "2022-06-30 23:30", "2022-02-01 00:00"],
                                     utc=True),
        "exit_time": pd.to_datetime(["2022-01-11 00:00", "2022-06-30 23:45", "2022-07-01 01:00", None], utc=True),
        "r_multiple": [2.0, -1.0, 2.0, None],
    })
    # Le 3e trade se termine dans la fenêtre de test : exclu de l'entraînement (purge).
    assert train_score(trades, window) == (2, pytest.approx(0.5))


def test_selection_prefers_a_stable_zone_over_an_isolated_peak():
    grid = {"a": (1, 2, 3), "b": (10, 20)}
    expectancy = {(1, 10): 0.3, (1, 20): 0.3, (2, 10): 0.3, (2, 20): -0.5, (3, 10): -0.5, (3, 20): 1.0}
    scores = {combo: (50, e) for combo, e in expectancy.items()}
    combo, how, plateau = select_combo(scores, grid, min_trades=30, default=(2, 20))
    assert combo == (1, 10) and how == "PLATEAU" and plateau == pytest.approx(0.3)
    thin = {combo: (5, e) for combo, e in expectancy.items()}
    assert select_combo(thin, grid, min_trades=30, default=(2, 20))[:2] == ((2, 20), "DEFAUT_AUCUNE_COMBINAISON_ELIGIBLE")


def test_trades_per_month_uses_the_calendar_period_not_the_trade_span(settings):
    """Régression : un seul trade clos dans la journée donnait 1e9 trades/mois."""
    from crypto_signal_intelligence.research.backtest_run import UniverseRun
    trades = pd.DataFrame([{
        "symbol": "BTCUSDT", "entry_status": "FILLED_OPEN", "exit_reason": "TP", "r_multiple": 1.0,
        "r_multiple_optimistic": 1.0, "net_return": 0.01, "gross_return": 0.012, "bars_held": 3, "mae_r": 0.1,
        "mfe_r": 1.1, "ambiguous": False, "trend_regime": "BULL", "volatility_regime": "NORMAL",
        "entry_time": pd.Timestamp("2024-03-01 10:00", tz="UTC"), "exit_time": pd.Timestamp("2024-03-01 11:00", tz="UTC"),
    }])
    six_months = UniverseRun(trades=trades, evaluated_bars=100, months=6.0).summary(settings)
    assert six_months["trades_per_month"] == pytest.approx(1 / 6, abs=0.01)
    assert UniverseRun(trades=trades, evaluated_bars=100).summary(settings)["trades_per_month"] is None


def test_decisions_stop_at_decisions_end_but_open_trades_finish():
    bars = [FLAT, FLAT, (100, 104.5, 99.8, 104), FLAT, FLAT]
    frame = frame_from(bars)
    strategy = OneShot({0, 3})
    result = simulate(frame, "TESTUSDT", strategy, rules(), decisions_end=frame["decision_time"].iloc[0])
    assert result.evaluated_bars == 1 and strategy.calls == 1
    assert len(result.trades) == 1 and result.trades[0].exit_reason == "TP"  # TP à la bougie 2, après la fenêtre


def closed(n_per_group: int, *, symbols=("BTCUSDT", "ETHUSDT"), years=(2022, 2023, 2024), r=0.3) -> pd.DataFrame:
    rows = [{"symbol": s, "entry_time": pd.Timestamp(f"{y}-03-01", tz="UTC"), "r_multiple": r,
             "entry_status": "FILLED_OPEN", "exit_reason": "TP"}
            for s in symbols for y in years for _ in range(n_per_group)]
    return pd.DataFrame(rows)


GOOD = {"trades_closed": 210, "expectancy_r": 0.3, "expectancy_r_ci95_block_bootstrap": (0.1, 0.5),
        "max_drawdown_r_closed_trades": -5.0}


def verdict(central=None, adverse=None, trades=None, ablations=None, windows=4, integrity=(True, "ok")):
    return admission.evaluate(central=central or GOOD, adverse=adverse or {"expectancy_r": 0.1},
                              central_trades=closed(35) if trades is None else trades, windows_with_trades=windows,
                              ablations={"sans_filtre": {"expectancy_r": 0.2}} if ablations is None else ablations,
                              integrity=integrity, rules=AdmissionSection())


def test_admission_validates_only_when_every_criterion_holds():
    result, criteria = verdict()
    assert result == ValidationVerdict.VALIDATED_OOS and all(c.passed for c in criteria) and len(criteria) == 7
    assert verdict(central=GOOD | {"expectancy_r": -0.1})[0] == ValidationVerdict.REJECTED
    assert verdict(central=GOOD | {"expectancy_r_ci95_block_bootstrap": (-0.05, 0.4)})[0] == \
        ValidationVerdict.INCONCLUSIVE
    assert verdict(windows=2)[0] == ValidationVerdict.INCONCLUSIVE
    assert verdict(integrity=(False, "fuite"))[0] == ValidationVerdict.INCONCLUSIVE


def test_admission_flags_useless_filters_and_concentration():
    result, criteria = verdict(ablations={"sans_filtre_volume": {"expectancy_r": 0.4}})
    assert result == ValidationVerdict.INCONCLUSIVE
    assert not criteria[6].passed and "sans apport démontré : sans_filtre_volume" in criteria[6].detail
    assert "nouvelle hypothèse" in criteria[6].detail   # jamais « à retirer » sur les mêmes fenêtres
    concentrated = pd.concat([closed(35, symbols=("BTCUSDT",)), closed(5, symbols=("ETHUSDT",), r=0.2)])
    result, criteria = verdict(trades=concentrated)
    assert result == ValidationVerdict.INCONCLUSIVE and not criteria[4].passed


def test_walk_forward_end_to_end_on_synthetic_data(settings):
    """Chaîne complète sur 4 mois synthétiques : fenêtres, trades de test DANS leur fenêtre, traces."""
    store = CandleStore(settings.data_dir)
    store.save(canonical(96 * 120, symbol="BTCUSDT", seed=11), "BTCUSDT", "15m")
    store.save(canonical(24 * 120, "1h", symbol="BTCUSDT", seed=12), "BTCUSDT", "1h")
    settings = settings.model_copy(update={
        "data": settings.data.model_copy(update={"symbols": ["BTCUSDT"], "history_start": date(2024, 1, 1)}),
        "protocol": settings.protocol.model_copy(update={
            "development_end": datetime(2024, 4, 29, 23, 59, 59, tzinfo=UTC),
            "final_test_start": datetime(2024, 4, 30, tzinfo=UTC)}),
        "walk_forward": settings.walk_forward.model_copy(update={"train_min_months": 2, "test_months": 1,
                                                                 "min_train_trades": 1}),
    })
    result = run(settings, "RANGE_REENTRY", now=datetime(2024, 6, 1, tzinfo=UTC))
    assert [w["test_start"][:10] for w in result.windows] == ["2024-03-01", "2024-04-01"]
    assert set(result.summaries) >= {"base/central", "base/adverse", "base/stress", "sans_filtre_btc/central",
                                     "avec_filtre_rsi/central", "v1_fixe/central"}
    for universe in result.oos.values():
        trades = universe.trades
        if trades.empty:
            continue
        for w in result.windows:
            in_window = trades[trades["window"] == w["index"]]
            setup = pd.to_datetime(in_window["setup_time"], utc=True)
            assert (setup >= pd.Timestamp(w["test_start"])).all() and (setup <= pd.Timestamp(w["test_end"])).all()
        exits = pd.to_datetime(trades["exit_time"], utc=True).dropna()
        assert (exits <= pd.Timestamp(settings.protocol.development_end)).all()  # test final jamais lu
    assert isinstance(result.verdict, ValidationVerdict) and len(result.criteria) == 7
    payload = json.loads((result.report_dir / "summary.json").read_text(encoding="utf-8"))
    # 6 combinaisons de la grille + chaque variante jouée en plus (ablations, extensions, profils de sortie)
    assert payload["n_trials"] == 11 and payload["verdict"] == result.verdict.value
    assert (result.report_dir / "report.md").read_text(encoding="utf-8").startswith("# Walk-forward RANGE_REENTRY")
    recorded = ExperimentRegistry(settings.experiments_db).get(result.run_id)
    assert recorded["kind"] == "WALK_FORWARD" and recorded["metrics"]["n_trials"] == 11
    assert recorded["metrics"]["grid_combinations"] == 6
    assert payload["program_trials"] == 11 and recorded["metrics"]["program_trials"] == 11


def test_development_end_cannot_be_moved_into_the_final_test(settings):
    """CSI_PROTOCOL__DEVELOPMENT_END repoussée : refus au lieu d'une lecture silencieuse du test final."""
    from crypto_signal_intelligence.research.protocol import FROZEN_DEVELOPMENT_END, FinalTestLocked, period
    later = settings.model_copy(update={"protocol": settings.protocol.model_copy(update={
        "development_end": datetime(2026, 1, 1, tzinfo=UTC), "final_test_start": datetime(2026, 1, 2, tzinfo=UTC)})})
    with pytest.raises(FinalTestLocked):
        period(later, "development", now=datetime(2026, 9, 1, tzinfo=UTC))
    earlier = settings.model_copy(update={"protocol": settings.protocol.model_copy(update={
        "development_end": datetime(2024, 12, 31, tzinfo=UTC)})})
    assert period(earlier, "development", now=datetime(2026, 9, 1, tzinfo=UTC)).end < FROZEN_DEVELOPMENT_END
    with pytest.raises(FinalTestLocked):
        period(settings, "final-test", now=datetime(2026, 9, 1, tzinfo=UTC))


def test_final_test_consultations_and_trials_are_counted_program_wide(settings):
    registry = ExperimentRegistry(settings.experiments_db)
    assert registry.consult_final_test("BT-1", "A") == 1
    assert registry.consult_final_test("BT-2", "B") == 2        # B a déjà pu voir ce que A y a vu
    common = dict(created_at="2026-01-01", hypothesis="h", strategy_version=1, variant="v", params={},
                  period_start="2020-01-01", period_end="2025-06-30", universe=[], data_hashes={},
                  git_commit="x", dependencies={}, seed=1, cost_scenario="central", simulation_rules={},
                  status="COMPLETED")
    registry.record(run_id="WF-1", kind="WALK_FORWARD", strategy="A", period_label="DEVELOPMENT",
                    metrics={"n_trials": 18}, **common)
    registry.record(run_id="BT-3", kind="BACKTEST_REFERENCE", strategy="B", period_label="DEVELOPMENT",
                    metrics={}, **common)
    registry.record(run_id="BT-4", kind="BACKTEST_REFERENCE", strategy="B", period_label="FINAL_TEST",
                    metrics={}, **common)
    assert registry.program_trials() == 19


def test_every_variant_played_beyond_the_grid_counts_as_a_trial():
    from crypto_signal_intelligence.research.walk_forward import BASE, _variants, trial_count
    from crypto_signal_intelligence.strategies.donchian import DonchianVolumeBreakout
    variants = _variants(DonchianVolumeBreakout, ["central", "adverse", "stress"])
    extra = {name for name, _, _, _ in variants if name != BASE}
    assert len(extra) == len(DonchianVolumeBreakout.ablations) + len(DonchianVolumeBreakout.extensions) + 2
    assert trial_count(18, variants) == 18 + len(extra)
    assert trial_count(18, [(BASE, s, {}, None) for s in ("central", "adverse")]) == 18   # coûts : mêmes décisions
