"""ML swing (lot 5 ter, docs/ML_SWING.md) : décisions toutes les 4 h, cibles, causalité et mutations, coupe
transversale, plis ancrés, règle d'admission v6 complétée en v2 (jours d'entrée, IC de Student sur blocs
calendaires, excès sur le marché), verrou commun de la période finale, protocole de bout en bout. Données
SYNTHÉTIQUES."""
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
from crypto_signal_intelligence.research.protocol import FinalTestLocked
from crypto_signal_intelligence.risk.exposure import RiskLimits

from .conftest import canonical

NO_COSTS = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
COSTS = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)
H = pd.Timedelta(hours=1)
DOC = Path(__file__).resolve().parents[1] / "docs" / "ML_SWING.md"


def hourly(n: int = 24 * 150, *, symbol: str = "ETHUSDT", seed: int = 0, start: str = "2024-01-01"):
    return canonical(n, "1h", symbol=symbol, start=start, seed=seed)


def test_declared_trials_and_protocol_version_match_the_document():
    assert swing.DECLARED_TRIALS == 136 and len(swing.SPECS) == 7
    assert {spec.family for spec in swing.SPECS} == {"logistic", "lightgbm", "xgboost", "catboost"}
    doc = DOC.read_text(encoding="utf-8")
    versions = [int(v) for v in re.findall(r"^- 2026-\d\d-\d\d, v(\d+)", doc, flags=re.MULTILINE)]
    assert versions and max(versions) == swing.PROTOCOL_VERSION


def test_code_matches_the_written_protocol():
    """Ce qui décide est écrit dans docs/ML_SWING.md : variantes, pas d'entraînement, contexte requis,
    variables du méta-filtre, règle d'admission."""
    doc = DOC.read_text(encoding="utf-8")
    assert list(swing.FAMILY_VARIANTS) == ["prix", "prix+volume", "prix+transactions", "prix+volume+transactions",
                                           "sans contexte", "sans marché", "sans coupe transversale",
                                           "sans calendrier"]
    for name in list(swing.FAMILY_VARIANTS)[:4]:                 # le calendrier reste dans les quatre premières
        assert swing.FAMILY_VARIANTS[name] == (*name.split("+"), "calendrier")
        assert " + ".join((*name.split("+"), "calendrier")) in doc
    for name, removed in (("sans contexte", "contexte"), ("sans marché", "marche"),
                          ("sans coupe transversale", "coupe"), ("sans calendrier", "calendrier")):
        assert swing.FAMILY_VARIANTS[name] == tuple(f for f in ds.FAMILIES if f != removed)
    assert tuple(swing.SWING.training_stride(h) for h in ds.HORIZONS) == (1, 4, 10) and "soit 1, 4" in doc
    assert swing.CONTEXT_REQUIRED == ("h4_ret_6", "d1_ret_7", "btc_ret_24")
    assert all(f"`{name}`" in doc for name in swing.CONTEXT_REQUIRED)
    assert all(f"`{name}`" in doc for name in swing.META_FEATURES if name not in ("p", "expected"))
    rule = swing.RULE
    assert (rule.min_trades, rule.min_trades_per_fold, rule.min_entry_days_per_fold, rule.min_ci_blocks) == (150, 20,
                                                                                                             20, 20)
    assert rule.strict and rule.excess_check and rule.ci_method == "student_calendar"
    assert rule.max_group_share == 0.6 and rule.required_positive(6) == 5


def test_decisions_are_taken_every_four_hours_at_block_ends():
    frame = ds.pair_decisions(hourly(24 * 40), hourly(24 * 40, symbol="BTCUSDT", seed=1), symbol="ETHUSDT",
                              costs=COSTS, horizons=(24,), kinds=("fh",))
    assert (frame["decision_time"].dt.hour % 4 == 0).all()
    assert (frame["decision_time"].diff().dropna() == pd.Timedelta(hours=4)).all()
    assert set(swing.SWING.features) == set(ds.FEATURES) and "r_720" not in ds.FEATURES


# --- Cibles (règle du propriétaire : stop, objectif, coûts, remplissage testés) ------------------------

def alternating(n: int = 200) -> pd.DataFrame:
    """Clôtures 99 / 101 en alternance : volatilité réalisée constante, barrières jamais touchées seules."""
    frame = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
                          "close": [100.0 * (1.01 if i % 2 else 0.99) for i in range(n)]})
    frame["open"] = frame["close"].shift(1).fillna(100.0)
    frame["high"], frame["low"] = frame[["open", "close"]].max(axis=1), frame[["open", "close"]].min(axis=1)
    return frame


ROW, HORIZON = 180, 4


def barriers(frame: pd.DataFrame) -> tuple[float, float, float]:
    sigma = ds.realized_vol(frame["close"]).iloc[ROW] * np.sqrt(HORIZON)
    entry = frame["open"].iloc[ROW + 1]
    return entry, entry * (1 + ds.BARRIER_K * sigma), entry * (1 - ds.BARRIER_K * sigma)


def barrier_exit(frame: pd.DataFrame, costs: CostScenario = NO_COSTS) -> tuple[float, int]:
    net, bars = ds.targets(frame, costs, HORIZON, "tb")
    return float(net[ROW]), int(bars[ROW])


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
    flat = alternating()
    entry, up, _down = barriers(flat)
    assert barrier_exit(flat) == (pytest.approx(flat["close"].iloc[ROW + HORIZON] / entry - 1), HORIZON)
    touch = flat.copy()
    touch.loc[ROW + 2, "high"] = up                                      # contact exact : pas de sortie
    assert barrier_exit(touch)[1] == HORIZON
    exceed = flat.copy()
    exceed.loc[ROW + 2, "high"] = up * 1.001
    assert barrier_exit(exceed) == (pytest.approx(up / entry - 1), 2)


def test_barrier_stop_is_hit_at_touch_and_wins_a_bar_that_also_exceeds_the_target():
    flat = alternating()
    entry, up, down = barriers(flat)
    touch = flat.copy()
    touch.loc[ROW + 2, "low"] = down                                     # stop au contact
    assert barrier_exit(touch) == (pytest.approx(down / entry - 1), 2)
    both = flat.copy()
    both.loc[ROW + 2, ["low", "high"]] = [down * 0.99, up * 1.01]       # même bougie : le stop l'emporte
    assert barrier_exit(both) == (pytest.approx(down / entry - 1), 2)
    first_bar = flat.copy()
    first_bar.loc[ROW + 1, "low"] = down * 0.98                          # bougie d'entrée : sa mèche compte
    assert barrier_exit(first_bar) == (pytest.approx(down / entry - 1), 1)


def test_barrier_gaps_exit_at_the_open_below_the_stop_and_at_the_target_above_it():
    flat = alternating()
    entry, up, down = barriers(flat)
    below = flat.copy()
    below.loc[ROW + 3, ["open", "low"]] = [down * 0.95, down * 0.94]     # ouverture sous le stop
    assert barrier_exit(below) == (pytest.approx(down * 0.95 / entry - 1), 3)
    above = flat.copy()
    above.loc[ROW + 3, ["open", "high"]] = [up * 1.05, up * 1.06]        # ouverture au-dessus de l'objectif
    assert barrier_exit(above) == (pytest.approx(up / entry - 1), 3)
    level = flat.copy()
    level.loc[ROW + 3, ["open", "high"]] = [up, up]                      # ouverture AU niveau : pas dépassé
    assert barrier_exit(level)[1] == HORIZON


def test_targets_pay_costs_on_both_fills_and_a_data_gap_has_no_target():
    flat = alternating()
    market, fee = (COSTS.slippage_bps + COSTS.half_spread_bps) / 1e4, COSTS.fee_bps / 1e4
    entry, exit_price = flat["open"].iloc[ROW + 1], flat["close"].iloc[ROW + HORIZON]
    expected = exit_price * (1 - market) * (1 - fee) / (entry * (1 + market) * (1 + fee)) - 1
    for kind in ds.TARGETS:
        net, bars = ds.targets(flat, COSTS, HORIZON, kind)
        assert net[ROW] == pytest.approx(expected) and bars[ROW] == HORIZON
        assert net[ROW] < ds.targets(flat, NO_COSTS, HORIZON, kind)[0][ROW]
    holed = flat.drop(index=ROW + 2).reset_index(drop=True)              # bougie manquante dans la fenêtre
    for kind in ds.TARGETS:
        net, bars = ds.targets(holed, COSTS, HORIZON, kind)
        assert np.isnan(net[ROW]) and bars[ROW] == -1
        assert np.isfinite(net[ROW - 3]) and np.isfinite(net[ROW + 2])  # fenêtres entières de part et d'autre


# --- Causalité --------------------------------------------------------------------------------------------

def test_pair_features_are_causal_and_each_leaky_context_bar_is_detected_by_its_own_family():
    h1, btc = hourly(24 * 150), hourly(24 * 150, symbol="BTCUSDT", seed=1)
    moments = [pd.Timestamp("2024-04-10 11:00", tz="UTC"), pd.Timestamp("2024-05-02 11:00", tz="UTC")]
    assert ds.causality_violations(h1, btc, decisions=moments) == []
    assert set(swing.MUTATIONS) == {"4h", "1d"}
    for name, (resampler, prefix) in swing.MUTATIONS.items():
        flagged = {f for leak in ds.causality_violations(h1, btc, decisions=moments, resampler=resampler)
                   for f in leak["features"]}
        assert flagged and all(f.startswith(prefix) for f in flagged), name


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


# --- Règle d'admission v6 (complétée en v2) -------------------------------------------------------------------

def test_strict_rule_needs_enough_trades_in_each_counted_validation():
    system = engine.System("fh", 24, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    rows = [system.to_dict() | {"key": "peu", "fold": i, "sharpe": 1.0, "trades": 10 if i < 2 else 40,
                                "entry_days": 30, "status": "OK"} for i in range(6)]
    rows += [system.to_dict() | {"key": "assez", "fold": i, "sharpe": 1.0 if i < 5 else -1.0, "trades": 30,
                                 "entry_days": 30, "status": "OK"} for i in range(6)]
    table = engine.summarize(pd.DataFrame(rows), swing.RULE).set_index("key")
    assert table.loc["peu", "positive_folds"] == 4 and not table.loc["peu", "stable"]
    assert table.loc["assez", "positive_folds"] == 5 and table.loc["assez", "stable"]


def test_a_validation_needs_twenty_distinct_entry_days_to_count():
    entries = pd.to_datetime([f"2023-01-{day:02d} 04:00" for day in (2, 9, 16, 23) for _ in range(5)], utc=True)
    trades = pd.DataFrame({"symbol": [f"P{i % 5}USDT" for i in range(20)], "entry_time": entries,
                           "exit_time": entries + pd.Timedelta(days=7), "notional": 0.1, "net": 0.01,
                           "pnl": 0.001, "candidate": range(20)})
    fold = engine.Fold(0, pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2022-10-01", tz="UTC"),
                       pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2023-06-30 23:59:59", tz="UTC"))
    metrics = engine.fold_metrics(trades, fold, valid_rows=500, submitted=20)
    assert metrics["trades"] == 20 and metrics["entry_days"] == 4 and metrics["sharpe"] > 0
    assert not swing.RULE.counts(metrics["sharpe"], metrics["trades"], metrics["entry_days"])   # 4 paris, pas 20
    assert swing.RULE.counts(metrics["sharpe"], metrics["trades"], 20)
    system = engine.System("fh", 168, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    rows = [system.to_dict() | {"key": "paquets", "fold": i, "sharpe": 1.0, "trades": 40,
                                "entry_days": 4 if i < 2 else 25, "status": "OK"} for i in range(6)]
    table = engine.summarize(pd.DataFrame(rows), swing.RULE).set_index("key")
    assert table.loc["paquets", "positive_folds"] == 4 and not table.loc["paquets", "stable"]


def passing_metrics(**overrides) -> dict:
    return {"avg_net_ci95": [0.001, 0.01], "excess_ci95": [0.0005, 0.008], "adverse_avg_net": 0.002,
            "avg_net_without_top1pct": 0.003, "pnl_share_max_symbol": 0.3, "pnl_share_max_fold": 0.4} | overrides


def test_every_strict_criterion_alone_rejects_a_system():
    verdict = engine.judge_strict(passing_metrics(), swing.RULE)
    assert verdict["passed"] and set(verdict["checks"]) == {
        "ic_gain_moyen_positif", "ic_exces_sur_le_marche_positif", "positif_couts_defavorables",
        "positif_sans_meilleurs_trades", "concentration_limitee"}
    failing = {
        "ic_gain_moyen_positif": [{"avg_net_ci95": [-0.001, 0.01]}, {"avg_net_ci95": None}],
        "ic_exces_sur_le_marche_positif": [{"excess_ci95": [-0.002, 0.004]}, {"excess_ci95": None}],
        "positif_couts_defavorables": [{"adverse_avg_net": -0.0001}, {"adverse_avg_net": 0.0},
                                       {"adverse_avg_net": None}],
        "positif_sans_meilleurs_trades": [{"avg_net_without_top1pct": -0.0001}, {"avg_net_without_top1pct": None}],
        "concentration_limitee": [{"pnl_share_max_symbol": 0.61}, {"pnl_share_max_fold": 0.7},
                                  {"pnl_share_max_fold": None}],
    }
    for check, cases in failing.items():
        for case in cases:
            verdict = engine.judge_strict(passing_metrics(**case), swing.RULE)
            assert not verdict["passed"], (check, case)
            assert [name for name, ok in verdict["checks"].items() if not ok] == [check], (check, case)
    without_excess = engine.judge_strict(passing_metrics(excess_ci95=None), replace(swing.RULE, excess_check=False))
    assert without_excess["passed"] and "ic_exces_sur_le_marche_positif" not in without_excess["checks"]


def test_a_variant_that_beats_the_reference_but_fails_the_strict_rule_is_not_kept():
    rule = swing.RULE
    assert engine.variant_kept(6, 6, True, rule)
    assert not engine.variant_kept(6, 6, False, rule)          # meilleure partout, mais non admissible
    assert not engine.variant_kept(4, 6, True, rule)           # admissible, mais pas meilleure assez souvent
    assert engine.variant_kept(6, 6, False, replace(rule, strict=False))      # règle non stricte (intraday v5)


# --- Incertitude : faux positifs sous un gain nul, et dérive commune du marché ---------------------------------

def no_edge_trades(rng: np.random.Generator, *, staggered: bool, days: int = 1096, horizon: int = 7):
    """Gains d'espérance NULLE : positions de 7 jours portées par un facteur de marché commun. Paquets de 5
    entrées tous les 7 jours (sorties synchronisées), ou entrées étalées dont les fenêtres se chevauchent."""
    market = rng.normal(0, 0.03, days + horizon)
    window = np.convolve(market, np.ones(horizon), "valid")[:days]          # somme sur 7 jours dès le jour d
    if staggered:
        entries = np.repeat(np.arange(days), rng.binomial(2, 0.3, days))
    else:
        entries = np.repeat(np.arange(0, days, horizon), 5)
    nets = window[entries] + rng.normal(0, 0.03 * np.sqrt(horizon), len(entries))
    return nets, pd.Timestamp("2022-07-01 04:00", tz="UTC") + pd.to_timedelta(entries, unit="D")


@pytest.mark.parametrize("staggered", [False, True])
def test_mean_interval_keeps_false_positives_near_nominal_without_edge(staggered):
    rng = np.random.default_rng(2026)
    draws, hits = 500, 0
    for _ in range(draws):
        nets, times = no_edge_trades(rng, staggered=staggered)
        ci = engine.mean_ci(nets, times, block_days=14, samples=0, seed=0, method="student_calendar", min_blocks=20)
        assert ci is not None
        hits += ci[0] > 0
    assert hits / draws <= 0.035                 # nominal 2,5 % ; blocs de jours avec trades (v1) : 4 à 7 %


def test_calendar_interval_needs_enough_blocks_with_trades():
    times = pd.Timestamp("2023-01-01", tz="UTC") + pd.to_timedelta(np.arange(0, 190, 10), unit="D")
    values = np.linspace(-0.01, 0.03, len(times))
    assert engine.mean_ci(values, times, block_days=10, samples=0, seed=0, method="student_calendar",
                          min_blocks=20) is None              # 19 blocs avec trades
    ci = engine.mean_ci(values, times, block_days=10, samples=0, seed=0, method="student_calendar", min_blocks=10)
    assert ci is not None and ci[0] < float(values.mean()) < ci[1]
    assert engine.mean_ci([], [], block_days=10, samples=0, seed=0, method="student_calendar") is None
    with pytest.raises(ValueError, match="inconnue"):
        engine.mean_ci(values, times, block_days=10, samples=0, seed=0, method="normale")


def test_random_picks_in_a_rising_market_pass_the_mean_criterion_but_not_the_excess_criterion():
    """Sans pouvoir de sélection, la hausse commune suffit au critère 3 ; le critère 7 la retire."""
    times = pd.date_range("2022-07-01 04:00", periods=6 * 1096, freq="4h")       # UTC sans fuseau
    plain_passes = excess_passes = 0
    draws = 40
    for seed in range(draws):
        rng = np.random.default_rng(seed)
        nets = (0.004 + rng.normal(0, 0.01, len(times)))[:, None] + rng.normal(0, 0.02, (len(times), 16))
        reference = pd.Series(nets.mean(axis=1), index=pd.DatetimeIndex(times).as_unit("ns"))
        when, pair = np.nonzero(rng.random(nets.shape) < 0.02)                   # choix au hasard
        trades = pd.DataFrame({"entry_time": times[when].tz_localize("UTC"), "net": nets[when, pair]})
        plain = engine.mean_ci(trades["net"], trades["entry_time"], block_days=10, samples=0, seed=0,
                               method="student_calendar", min_blocks=20)
        excess, entry = engine.excess_over_market(trades, reference)
        assert len(excess) == len(trades)
        beyond = engine.mean_ci(excess, entry, block_days=10, samples=0, seed=0, method="student_calendar",
                                min_blocks=20)
        verdict = engine.judge_strict(passing_metrics(avg_net_ci95=plain, excess_ci95=beyond), swing.RULE)
        plain_passes += verdict["checks"]["ic_gain_moyen_positif"]
        excess_passes += verdict["passed"]
    assert plain_passes == draws and excess_passes <= 0.1 * draws


# --- Bout en bout ----------------------------------------------------------------------------------------

@pytest.fixture
def small_swing(monkeypatch, settings):
    """Le protocole swing en miniature : 3 paires, 9 mois, logistique seule, validations d'un mois."""
    rule = replace(swing.RULE, min_trades=5, min_trades_per_fold=1, min_entry_days_per_fold=1, min_ci_blocks=5)
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
    assert result.leak_audit["mutation_by_timeframe"] == {"4h": True, "1d": True}
    assert payload["n_trials"] == 2 + len(swing.FAMILY_VARIANTS) + 2
    assert payload["selection_rule"]["strict"] and len(payload["folds"]) == 4
    assert payload["selection_rule"]["ci_method"] == "student_calendar" and payload["selection_rule"]["excess_check"]
    for name in ("summary.json", "report.md", "grid.csv", "decisions.parquet"):
        assert (result.report_dir / name).exists(), name
    assert "Student" in (result.report_dir / "report.md").read_text(encoding="utf-8")
    run = ExperimentRegistry(settings.experiments_db).get(result.run_id)
    assert run["kind"] == swing.KIND_SELECT and run["status"] == "COMPLETED"
    assert set(payload["strict_checks"]) == set(result.summary.loc[result.summary["stable"], "key"])
    for decision in payload["variants"][:-1]:                     # variantes de familles
        assert decision["kept"] == engine.variant_kept(decision["wins_vs_reference"], decision["evaluated"],
                                                       decision["admissible"], small.selection)


def test_strict_checks_report_every_criterion(small_swing):
    settings, small = small_swing
    prep = small.prepare(settings, end=pd.Timestamp("2024-09-29 23:59:59", tz="UTC"), progress=lambda _t: None)
    folds = small.folds(settings, small.first_valid(settings), pd.Timestamp("2024-09-29 23:59:59", tz="UTC"))
    system = engine.System("fh", 24, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    runs = engine.run_system(prep, system, folds, RiskLimits(), seed=1)
    details = engine.strict_checks(prep, system, runs, RiskLimits(), settings=settings, rule=small.selection)
    assert set(details["checks"]) == {"ic_gain_moyen_positif", "ic_exces_sur_le_marche_positif",
                                      "positif_couts_defavorables", "positif_sans_meilleurs_trades",
                                      "concentration_limitee"}
    assert details["passed"] == all(details["checks"].values())
    reference = engine.same_time_mean_net(prep, "fh", 24)
    assert reference.index.is_unique and (reference.index.hour % 4 == 0).all()
    trades = engine.all_trades(runs)
    if len(trades):
        excess, _ = engine.excess_over_market(trades, reference)
        assert len(excess) == len(trades)                           # chaque entrée a sa référence
        assert details["avg_excess"] == pytest.approx(float(excess.mean()), abs=1e-6)


def test_prediction_cache_gives_identical_runs(small_swing):
    settings, small = small_swing
    prep = small.prepare(settings, end=pd.Timestamp("2024-09-29 23:59:59", tz="UTC"), progress=lambda _t: None)
    folds = small.folds(settings, small.first_valid(settings), pd.Timestamp("2024-09-29 23:59:59", tz="UTC"))
    cache: dict = {}
    first = engine.System("fh", 24, "logistic_l2", 0.0, program=swing.STRATEGY_ID)
    engine.run_system(prep, first, folds, RiskLimits(), seed=1, cache=cache)
    assert len(cache) == len(folds)
    other_margin = replace(first, margin=0.002)
    cached = engine.run_system(prep, other_margin, folds, RiskLimits(), seed=1, cache=cache)
    fresh = engine.run_system(prep, other_margin, folds, RiskLimits(), seed=1)
    assert len(cache) == len(folds)                                  # aucun nouvel ajustement
    for a, b in zip(cached, fresh, strict=True):
        assert a.metrics == b.metrics and a.trades.equals(b.trades)


def test_final_period_is_locked_for_the_whole_program_once_any_strategy_consulted_it(small_swing):
    settings, small = small_swing
    ExperimentRegistry(settings.experiments_db).consult_final_test("MLIF-AUTRE", "ML_INTRADAY")
    with pytest.raises(FinalTestLocked, match="déjà été consultée"):
        swing.final(settings, now=datetime(2026, 10, 1, tzinfo=UTC), allow_final_test=True, program=small)
