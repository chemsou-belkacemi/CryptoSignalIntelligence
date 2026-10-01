"""ML intraday (lot 5 bis, docs/ML_INTRADAY.md) : causalité, cibles, purge, limites de risque, étalonnage,
surveillance, règle de stabilité, protocole de bout en bout. Données SYNTHÉTIQUES : on teste le code."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario, RegimeSection
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.features.higher_tf import resample_complete
from crypto_signal_intelligence.ml.intraday import dataset as ds
from crypto_signal_intelligence.ml.intraday import monitor
from crypto_signal_intelligence.ml.intraday import protocol as proto
from crypto_signal_intelligence.ml.intraday.models import SPECS, Payoff, calibration_report, fit, fit_platt
from crypto_signal_intelligence.ml.intraday.portfolio import (
    block_bootstrap,
    exceptional_dependence,
    run_book,
    trade_mean_ci,
)
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.protocol import FinalTestLocked
from crypto_signal_intelligence.risk.exposure import ExposureBook, Position, RiskLimits, to_ns

from .conftest import canonical

NO_COSTS = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
COSTS = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)


def market(n15: int = 3000, *, start: str = "2024-01-01", seed: int = 0):
    setup = canonical(n15, "15m", symbol="ETHUSDT", start=start, seed=seed)
    context = canonical(n15 // 4 + 4, "1h", symbol="ETHUSDT", start=start, seed=seed + 1)
    btc = canonical(n15 // 4 + 4, "1h", symbol="BTCUSDT", start=start, seed=seed + 2)
    return setup, context, btc


# --- Données et causalité ------------------------------------------------------------------------

def test_4h_bars_are_complete_and_available_only_after_their_last_hour():
    hours = canonical(30, "1h", start="2024-01-01 02:00")          # 02:00 → bloc 00-04 incomplet
    bars = resample_complete(hours.drop(index=[13]), 4)             # trou dans le bloc 12-16
    starts = bars["open_time"].dt.hour.tolist()
    assert 0 not in starts[:1] and pd.Timestamp("2024-01-01 12:00", tz="UTC") not in set(bars["open_time"])
    first = bars.iloc[0]
    members = hours[(hours["open_time"] >= first["open_time"]) & (hours["open_time"] < first["open_time"] + pd.Timedelta(hours=4))]
    assert first["available_at"] == members["available_at"].max()
    assert first["close"] == members["close"].iloc[-1] and first["high"] == members["high"].max()


def test_features_are_causal_and_a_leaky_4h_join_is_detected():
    setup, context, btc = market()
    moments = [pd.Timestamp("2024-01-20 05:30", tz="UTC"), pd.Timestamp("2024-01-25 13:30", tz="UTC"),
               pd.Timestamp("2024-01-29 21:30", tz="UTC")]
    kwargs = {"symbol": "ETHUSDT", "regimes": RegimeSection(), "costs": COSTS, "decisions": moments}
    assert ds.causality_violations(setup, context, btc, **kwargs) == []
    leaks = ds.causality_violations(setup, context, btc, **kwargs, resampler=proto._leaky_resampler)
    assert leaks and any(f.startswith("h4_") for leak in leaks for f in leak["features"])


def test_dataset_has_every_family_and_float32_features():
    setup, context, btc = market(1500)
    frame = ds.pair_dataset(setup, context, btc, symbol="ETHUSDT", regimes=RegimeSection(), costs=COSTS)
    assert set(ds.FEATURES) <= set(frame.columns) and len(ds.FEATURES) == len(set(ds.FEATURES))
    assert all(frame[f].dtype == np.float32 for f in ds.FEATURES)
    assert {f"net_{k}_{h}" for k in ds.TARGETS for h in ds.HORIZONS} <= set(frame.columns)
    assert ds.feature_set(("prix", "transactions")) == ds.FAMILIES["prix"] + ds.FAMILIES["transactions"]


# --- Cibles --------------------------------------------------------------------------------------

def candles(opens, highs, lows, closes, start="2024-01-01"):
    n = len(opens)
    times = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open_time": times, "open": opens, "high": highs, "low": lows, "close": closes})


def test_fixed_horizon_target_is_open_t_plus_1_to_close_t_plus_h_net_of_costs():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 40)))
    opens = np.r_[100, close[:-1]]
    frame = candles(opens, np.maximum(opens, close) * 1.01, np.minimum(opens, close) * 0.99, close)
    net, bars = ds.targets(frame, COSTS, 4, "fh")
    market_cost, fee = 3 / 1e4, 10 / 1e4
    expected = close[4] * (1 - market_cost) * (1 - fee) / (opens[1] * (1 + market_cost) * (1 + fee)) - 1
    assert net[0] == pytest.approx(expected) and bars[0] == 4
    assert np.isnan(net[-4:]).all() and (bars[-4:] == -1).all()
    delayed, delayed_bars = ds.targets(frame, NO_COSTS, 4, "fh", delay=1)
    assert delayed[0] == pytest.approx(close[5] / opens[2] - 1) and delayed_bars[0] == 5


def test_target_crossing_a_data_gap_is_invalid():
    frame = candles(*(np.full(30, 100.0),) * 4)
    frame = frame.drop(index=[10]).reset_index(drop=True)
    net, _ = ds.targets(frame, NO_COSTS, 4, "fh")
    assert np.isnan(net[6:10]).all() and np.isfinite(net[0])


def barrier_market(path):
    """100 bougies d'historique (rendements alternés ±1 %), puis le chemin à tester après la décision."""
    closes = list(100 * np.exp(np.cumsum([0.01 if i % 2 else -0.01 for i in range(100)])))
    opens, highs, lows = list(closes), list(closes), list(closes)
    for o, h, low, c in path:
        opens.append(o)
        highs.append(h)
        lows.append(low)
        closes.append(c)
    return candles(opens, highs, lows, closes)


def levels(frame, horizon=4, decision=99):
    sigma = ds.realized_vol(frame["close"]).iloc[decision] * np.sqrt(horizon)
    entry = frame["open"].iloc[decision + 1]
    return entry, entry * (1 + ds.BARRIER_K * sigma), entry * (1 - ds.BARRIER_K * sigma)


@pytest.mark.parametrize("case", ["objectif", "stop_et_objectif", "ouverture_sous_stop", "temps"])
def test_triple_barrier_exit_rules_are_conservative(case):
    base = 100 * np.exp(-0.01)          # dernière clôture de l'historique (rendement alterné final : -1 %)
    entry = base
    probe = barrier_market([(entry, entry, entry, entry)] * 5)
    _, up, down = levels(probe)
    paths = {
        "objectif": [(entry, entry * 1.001, entry * 0.999, entry), (entry, up * 1.001, entry, entry)],
        "stop_et_objectif": [(entry, up * 1.01, down * 0.99, entry)],
        "ouverture_sous_stop": [(entry, entry, entry, entry), (down * 0.98, down * 0.99, down * 0.97, down * 0.98)],
        "temps": [(entry, entry * 1.001, entry * 0.999, entry * 1.0005)] * 4,
    }
    path = paths[case] + [(entry, entry, entry, entry)] * (6 - len(paths[case]))
    frame = barrier_market(path)
    net, bars = ds.targets(frame, NO_COSTS, 4, "tb")
    exit_price, offset = {
        "objectif": (up, 2), "stop_et_objectif": (down, 1), "ouverture_sous_stop": (down * 0.98, 2),
        "temps": (entry * 1.0005, 4)}[case]
    assert net[99] == pytest.approx(exit_price / entry - 1) and bars[99] == offset


def test_triple_barrier_without_volatility_history_is_invalid():
    frame = candles(*(np.full(50, 100.0),) * 4)
    net, bars = ds.targets(frame, NO_COSTS, 4, "tb")
    assert np.isnan(net[:40]).all() and (bars[:40] == -1).all()


# --- Purge et validations ------------------------------------------------------------------------

def tiny_prepared(n: int = 6000, symbols=("ETHUSDT", "BTCUSDT")) -> proto.Prepared:
    metas, matrices = [], []
    for i, symbol in enumerate(symbols):
        setup, context, btc = market(n, seed=10 * i)
        frame = ds.pair_dataset(setup, context, btc, symbol=symbol, regimes=RegimeSection(), costs=COSTS)
        matrices.append(frame[list(ds.FEATURES)].to_numpy(np.float32))
        metas.append(frame.drop(columns=list(ds.FEATURES)))
    meta = pd.concat(metas, ignore_index=True)
    meta["symbol"] = meta["symbol"].astype("category")
    return proto.Prepared(meta, np.vstack(matrices), {}, {"data_hashes": {}}, pd.DataFrame(), {"central": COSTS},
                          pd.Timestamp("2030-01-01", tz="UTC"), list(symbols), proto.INTRADAY)


def test_development_folds_are_the_seven_declared_validations():
    folds = proto.make_folds(pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2025-06-30 23:59:59", tz="UTC"))
    assert len(folds) == 7
    assert folds[0].train_start == pd.Timestamp("2021-01-01", tz="UTC")
    assert folds[0].calib_start == pd.Timestamp("2021-11-01", tz="UTC")
    assert folds[-1].valid_start == pd.Timestamp("2025-01-01", tz="UTC")
    assert folds[-1].valid_end == pd.Timestamp("2025-06-30 23:59:59", tz="UTC")


def test_split_purges_overlapping_targets_and_strides_training_rows(monkeypatch):
    prep = tiny_prepared(4000)
    fold = proto.Fold(0, pd.Timestamp("2024-01-05", tz="UTC"), pd.Timestamp("2024-01-25", tz="UTC"),
                      pd.Timestamp("2024-02-01", tz="UTC"), pd.Timestamp("2024-02-10", tz="UTC"))
    for kind in ds.TARGETS:
        rows = proto.split_rows(prep, fold, kind, 8)
        decision = prep.meta["decision_time"]
        barrier = decision + 8 * ds.STEP
        assert (barrier.iloc[rows.fit] <= fold.calib_start).all()
        assert (barrier.iloc[rows.calib] <= fold.valid_start).all()
        assert (decision.iloc[rows.valid] >= fold.valid_start).all() and (barrier.iloc[rows.valid] <= fold.valid_end).all()
        assert not set(rows.fit) & set(rows.calib) and not set(rows.calib) & set(rows.valid)
        position = (prep.meta["open_time"].iloc[rows.fit] - pd.Timestamp("2000-01-01", tz="UTC")) // ds.STEP
        assert (position % 8 == 0).all()


# --- Limites de risque centralisées --------------------------------------------------------------

T0 = pd.Timestamp("2024-03-01 10:00", tz="UTC")


def ns(minutes: int) -> int:
    return to_ns(T0 + pd.Timedelta(minutes=minutes))


def test_exposure_limits_are_shared_across_strategies_and_count_existing_positions():
    limits = RiskLimits(position_fraction=0.1, max_positions=3, max_total_exposure=0.3, max_asset_exposure=0.1,
                        max_strategy_exposure=0.2, daily_loss_limit=0.03)
    existing = Position("SOLUSDT", "BOT_MANUEL", 0.1, ns(-60), None)       # ouverte, sortie inconnue
    book = ExposureBook(limits, positions=[existing])
    assert book.try_open("ETHUSDT", "ML_INTRADAY", ns(0), ns(60), 0.01)[1] is None
    assert book.try_open("ETHUSDT", "SWING", ns(1), ns(60), 0.01)[1] == "ASSET_EXPOSURE"
    assert book.try_open("BTCUSDT", "ML_INTRADAY", ns(2), ns(60), 0.01)[1] is None
    assert book.try_open("XRPUSDT", "ML_INTRADAY", ns(3), ns(60), 0.01)[1] == "MAX_POSITIONS"
    book.advance(ns(61))                                                      # deux sorties réalisées
    assert len(book.positions) == 1 and book.equity == pytest.approx(1 + 2 * 0.1 * 0.01)
    assert book.try_open("XRPUSDT", "ML_INTRADAY", ns(62), ns(90), 0.0)[1] is None


def test_strategy_and_total_exposure_limits():
    limits = RiskLimits(position_fraction=0.1, max_positions=10, max_total_exposure=0.3, max_asset_exposure=0.1,
                        max_strategy_exposure=0.2)
    book = ExposureBook(limits)
    for i, symbol in enumerate(("A", "B")):
        assert book.try_open(symbol, "S1", ns(i), ns(100))[1] is None
    assert book.try_open("C", "S1", ns(3), ns(100))[1] == "STRATEGY_EXPOSURE"
    assert book.try_open("C", "S2", ns(4), ns(100))[1] is None
    assert book.try_open("D", "S2", ns(5), ns(100))[1] == "TOTAL_EXPOSURE"


def test_daily_loss_blocks_entries_until_the_next_utc_day():
    book = ExposureBook(RiskLimits(position_fraction=0.5, max_positions=2, max_total_exposure=1.0,
                                   max_asset_exposure=0.5, max_strategy_exposure=1.0, daily_loss_limit=0.03))
    book.try_open("A", "S", ns(0), ns(30), net=-0.08)                         # -4 % du capital
    assert book.try_open("B", "S", ns(31), ns(60))[1] == "DAILY_LOSS_LIMIT"
    next_day = to_ns(T0.floor("D") + pd.Timedelta(days=1, hours=1))
    assert book.try_open("B", "S", next_day, next_day + 10**9)[1] is None


def test_limits_are_validated():
    with pytest.raises(ValueError):
        RiskLimits(position_fraction=0.2, max_asset_exposure=0.1)
    with pytest.raises(ValueError):
        RiskLimits(max_total_exposure=1.5)


def test_settings_carry_the_central_limits(settings):
    limits = RiskLimits.from_settings(settings.risk)
    assert limits.position_fraction == 0.10 and limits.max_positions == 5 and limits.max_total_exposure == 0.5


def test_run_book_prioritizes_expected_gain_and_reports_each_candidate():
    candidates = pd.DataFrame({"symbol": ["A", "B", "C", "A"],
                               "decision_time": [T0, T0, T0, T0 + pd.Timedelta(minutes=15)],
                               "bars": [4, 4, 4, 4], "net": [0.01, 0.02, -0.01, 0.0], "score": [0.1, 0.3, 0.2, 0.5]})
    limits = RiskLimits(position_fraction=0.1, max_positions=2, max_total_exposure=0.5, max_asset_exposure=0.1,
                        max_strategy_exposure=0.5)
    trades, status = run_book(candidates, limits)
    assert list(status) == ["MAX_POSITIONS", "ENTER", "ENTER", "MAX_POSITIONS"]
    assert sorted(trades["symbol"]) == ["B", "C"] and set(trades["candidate"]) == {1, 2}


# --- Modèles et étalonnage -----------------------------------------------------------------------

def test_every_model_family_fits_and_platt_repairs_miscalibrated_probabilities():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(3000, 4)).astype(np.float32)
    y = (X[:, 0] + rng.normal(0, 1, 3000) > 0.5).astype(int)
    for spec in (SPECS[0], SPECS[1], SPECS[5]):
        p = fit(spec, X[:2000], y[:2000], seed=1).predict_proba(X[2000:])
        assert p.shape == (1000,) and ((p >= 0) & (p <= 1)).all()
    true_p = 1 / (1 + np.exp(-(2 * X[:, 0] - 1)))
    outcome = (rng.random(3000) < true_p).astype(int)
    distorted = true_p ** 3                                                   # sous-estime fortement
    platt = fit_platt(distorted[:2000], outcome[:2000])
    before = calibration_report(distorted[2000:], outcome[2000:], base_rate=float(outcome[:2000].mean()))
    after = calibration_report(platt(distorted[2000:]), outcome[2000:], base_rate=float(outcome[:2000].mean()))
    assert after["ece"] < before["ece"] and after["brier"] < before["brier"]
    assert after["brier_skill"] > 0 and len(after["reliability"]) == 10


def test_expected_net_uses_training_payoffs():
    payoff = Payoff.from_returns(np.array([0.02, 0.01, -0.01, -0.03]))
    assert payoff.mean_win == pytest.approx(0.015) and payoff.mean_loss == pytest.approx(-0.02)
    assert payoff.expected(np.array([0.5]))[0] == pytest.approx(-0.0025)
    assert payoff.expected(np.array([4 / 7]))[0] == pytest.approx(0.0, abs=1e-12)


# --- Incertitude et trades exceptionnels ---------------------------------------------------------

def test_block_bootstrap_and_trade_ci_are_reproducible_and_bracket_the_estimate():
    rng = np.random.default_rng(5)
    returns = rng.normal(0.001, 0.01, 400)
    first = block_bootstrap(returns, block_days=10, samples=500, seed=1)
    assert first == block_bootstrap(returns, block_days=10, samples=500, seed=1)
    low, high = first["sharpe_ci95"]
    assert low < returns.mean() / returns.std(ddof=1) * np.sqrt(365) < high
    times = pd.date_range("2024-01-01", periods=400, freq="D", tz="UTC")
    ci = trade_mean_ci(returns, times, block_days=10, samples=500, seed=1)
    assert ci[0] < returns.mean() < ci[1]
    assert block_bootstrap(returns[:15], block_days=10, samples=10, seed=1)["sharpe_ci95"] is None


def test_dependence_on_exceptional_trades_is_measured():
    trades = pd.DataFrame({"net": [0.5] + [-0.001] * 199, "pnl": [0.05] + [-0.0001] * 199})
    out = exceptional_dependence(trades)
    assert out["avg_net_without_top1pct"] < 0 < trades["net"].mean()
    assert out["top10_share_of_gross_gain"] == 1.0


# --- Surveillance --------------------------------------------------------------------------------

def test_monitor_suspends_new_entries_but_keeps_existing_positions():
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    fresh = {f"P{i}": now for i in range(16)}
    healthy = monitor.decide(trained_at=datetime(2026, 6, 1, tzinfo=UTC), now=now, last_available=fresh,
                             recent_net=np.full(100, 0.001), recent_times=pd.date_range("2026-06-01", periods=100,
                                                                                           freq="D", tz="UTC"),
                             current_drawdown=-0.02, reference_drawdown=-0.05,
                             recent_p=np.full(200, 0.5), recent_outcome=np.r_[np.ones(100), np.zeros(100)])
    assert healthy.allow_new_entries and healthy.existing_positions == "KEEP_UNTIL_PLANNED_EXIT"
    stale = dict(fresh) | {f"P{i}": None for i in range(5)}
    bad = monitor.decide(trained_at=datetime(2026, 1, 1, tzinfo=UTC), now=now, last_available=stale,
                         recent_net=np.full(100, -0.01) + np.linspace(0, 1e-4, 100),
                         recent_times=pd.date_range("2026-06-01", periods=100, freq="D", tz="UTC"),
                         current_drawdown=-0.09, reference_drawdown=-0.05,
                         recent_p=np.full(200, 0.7), recent_outcome=np.r_[np.ones(100), np.zeros(100)])
    assert not bad.allow_new_entries and bad.existing_positions == "KEEP_UNTIL_PLANNED_EXIT"
    text = " ".join(bad.reasons)
    for reason in ("MODEL_TOO_OLD", "PERFORMANCE_DEGRADED", "CALIBRATION_DRIFT", "DATA_QUALITY"):
        assert reason in text
    assert monitor.degradation_reason(np.zeros(10), [], current_drawdown=-0.08, reference_drawdown=-0.05)


# --- Règle de stabilité et méta-filtre -----------------------------------------------------------

def grid_rows(key: str, sharpes, trades_per_fold: int, status=None) -> list[dict]:
    system = proto.System("fh", 4, "logistic_l2", 0.0)
    return [system.to_dict() | {"key": key, "fold": i, "sharpe": s, "trades": trades_per_fold,
                                "status": (status or {}).get(i, "OK")} for i, s in enumerate(sharpes)]


def test_stability_rule_needs_seventy_percent_of_validations_and_enough_trades():
    rows = (grid_rows("stable", [1, 1, 1, 1, 1, -1, -1], 40) + grid_rows("fragile", [3, 3, 3, 3, -1, -1, -1], 40)
            + grid_rows("rare", [1, 1, 1, 1, 1, 1, 1], 20)
            + grid_rows("failed", [1, 1, 1, 1, None, None, None], 80, {4: "FAILED", 5: "FAILED", 6: "FAILED"}))
    table = proto.summarize(pd.DataFrame(rows)).set_index("key")
    assert table.loc["stable", "admissible"] and table.loc["stable", "positive_folds"] == 5
    assert not table.loc["fragile", "admissible"] and not table.loc["rare", "admissible"]
    assert not table.loc["failed", "admissible"]
    assert proto.required_positive(7) == 5 and proto.required_positive(5) == 4
    assert proto.kept(3, 4) and not proto.kept(2, 4) and not proto.kept(2, 2)


def test_meta_filter_needs_enough_out_of_training_signals():
    rng = np.random.default_rng(2)
    columns = {name: rng.normal(size=400) for name in proto.META_FEATURES}
    history = pd.DataFrame(columns | {"net": rng.normal(0, 0.01, 400)})
    assert proto._meta_mask(history.iloc[:200], history) is None
    mask = proto._meta_mask(history, history.iloc[:50])
    assert mask is not None and mask.dtype == bool and len(mask) == 50


def test_declared_trial_count_matches_the_protocol():
    assert proto.DECLARED_TRIALS == 224


# --- Protocole de bout en bout -------------------------------------------------------------------

@pytest.fixture
def small_program(monkeypatch):
    """Le protocole intraday en miniature (même moteur) : logistique seule, une marge, un horizon, validations
    d'un mois après 3 mois d'entraînement."""
    from dataclasses import replace

    from crypto_signal_intelligence.ml import engine
    small = replace(proto.INTRADAY, specs=(SPECS[0],), margins=(0.0,), horizons=(4,), train_months=3,
                    calib_months=1, valid_months=1, first_valid_months=3, random_draws=3)
    monkeypatch.setitem(engine.PROGRAMS, proto.STRATEGY_ID, small)
    return small


@pytest.fixture
def small_protocol(settings, small_program):
    """Protocole complet en miniature : 2 paires, 7 mois."""
    store = CandleStore(settings.data_dir)
    for i, symbol in enumerate(("BTCUSDT", "ETHUSDT")):
        store.save(canonical(4 * 24 * 212, "15m", symbol=symbol, start="2024-01-01", seed=i), symbol, "15m")
        store.save(canonical(24 * 212, "1h", symbol=symbol, start="2024-01-01", seed=i + 7), symbol, "1h")
    settings.data.symbols = ["BTCUSDT", "ETHUSDT"]
    settings.data.history_start = datetime(2024, 1, 1, tzinfo=UTC).date()
    settings.protocol.development_end = datetime(2024, 7, 31, 23, 59, 59, tzinfo=UTC)
    settings.protocol.bootstrap_samples = 50
    return settings


def test_selection_on_noise_concludes_no_edge_and_keeps_the_final_test_locked(small_protocol):
    settings = small_protocol
    now = datetime(2026, 10, 1, tzinfo=UTC)
    result = proto.select(settings, now=now, allow_dirty=True,      # dossier de test : pas de dépôt git
                          program=proto.engine.PROGRAMS[proto.STRATEGY_ID])
    assert result.leak_audit["passed"]
    payload = result.payload
    assert payload["n_trials"] == 2 + len(proto.FAMILY_VARIANTS) + 2
    for name in ("summary.json", "report.md", "grid.csv", "calibration.csv", "variants.csv", "decisions.parquet"):
        assert (result.report_dir / name).exists(), name
    journal = pd.read_parquet(result.report_dir / "decisions.parquet")
    allowed = {"ABSTAIN", "ENTER", "FILTERED", "MAX_POSITIONS", "ASSET_EXPOSURE", "STRATEGY_EXPOSURE",
               "TOTAL_EXPOSURE", "DAILY_LOSS_LIMIT"}
    assert len(journal) > 0 and set(journal["decision"]) <= allowed
    assert {"p", "expected_net", "observed_net", "model_spec", "model_sha256", "data_hash",
            "cost_round_trip_bps"} <= set(journal.columns)
    assert (journal["model_sha256"].str.len() == 64).all()
    assert payload["source_fingerprint"] == proto.source_fingerprint()
    registry = ExperimentRegistry(settings.experiments_db)
    run = registry.get(result.run_id)
    assert run["status"] == "COMPLETED" and run["metrics"]["n_trials"] == payload["n_trials"]
    if result.conclusion == proto.NO_EDGE:
        with pytest.raises(FinalTestLocked):
            proto.final(settings, now=now, allow_final_test=True)
    with pytest.raises(FinalTestLocked):
        proto.final(settings, now=now, allow_final_test=False)


def test_a_failed_run_is_recorded_with_its_status(small_protocol, small_program):
    from dataclasses import replace
    settings = small_protocol

    def broken(*_args, **_kwargs):
        raise RuntimeError("panne simulée")

    with pytest.raises(RuntimeError, match="panne"):
        proto.select(settings, now=datetime(2026, 10, 1, tzinfo=UTC), allow_dirty=True,
                     program=replace(small_program, leak_audit=broken))
    rows = ExperimentRegistry(settings.experiments_db).recent(5)
    assert rows[0]["status"] == "FAILED" and rows[0]["kind"] == proto.KIND_SELECT


def test_code_must_be_committed_before_any_data_is_read(small_protocol, small_program):
    from dataclasses import replace

    def forbidden(*_args, **_kwargs):
        raise AssertionError("données lues avant le contrôle du code")

    with pytest.raises(RuntimeError, match="non commité"):
        proto.select(small_protocol, now=datetime(2026, 10, 1, tzinfo=UTC),
                     program=replace(small_program, prepare=forbidden))


# --- Corrections de la relecture du 2026-10-01 (avant exécution) ---------------------------------

def test_protocol_version_matches_the_document_history():
    import re
    from pathlib import Path
    history = (Path(__file__).resolve().parents[1] / "docs" / "ML_INTRADAY.md").read_text(encoding="utf-8")
    versions = [int(v) for v in re.findall(r"^- 2026-\d\d-\d\d, v(\d+)", history, flags=re.MULTILINE)]
    assert versions and max(versions) == proto.PROTOCOL_VERSION


def test_each_family_variant_isolates_one_family():
    variants = proto.FAMILY_VARIANTS
    assert len(variants) == 6 and all(v != proto.ALL_FAMILIES for v in variants.values())
    for name, families in variants.items():
        assert ("calendrier" in families) == (name != "sans calendrier")
    assert set(variants["sans calendrier"]) == set(proto.ALL_FAMILIES) - {"calendrier"}
    assert set(variants["+contexte"]) == set(proto.ALL_FAMILIES) - {"marche"}


def prepared_with_inputs(n: int = 3000):
    symbols = ("ETHUSDT", "BTCUSDT")
    metas, matrices, inputs = [], [], {}
    for i, symbol in enumerate(symbols):
        setup, context, btc = market(n, seed=10 * i)
        setup = setup.drop(index=[700, 701]).sample(frac=1, random_state=i)      # trou et désordre
        inputs[symbol] = {"setup": setup, "context": context, "btc": btc}
        frame = ds.pair_dataset(setup, context, btc, symbol=symbol, regimes=RegimeSection(), costs=COSTS)
        matrices.append(frame[list(ds.FEATURES)].to_numpy(np.float32))
        metas.append(frame.drop(columns=list(ds.FEATURES)))
    meta = pd.concat(metas, ignore_index=True)
    meta["symbol"] = meta["symbol"].astype("category")
    return proto.Prepared(meta, np.vstack(matrices), inputs, {"data_hashes": {}}, pd.DataFrame(),
                          {"central": COSTS}, pd.Timestamp("2030-01-01", tz="UTC"), list(symbols), proto.INTRADAY)


def test_recomputed_scenario_targets_align_with_the_dataset_rows():
    prep = prepared_with_inputs()
    for kind in ds.TARGETS:
        for horizon in (2, 16):
            net, bars = proto.scenario_targets(prep, prep.symbols, kind, horizon, COSTS, 0)
            np.testing.assert_allclose(net, prep.meta[f"net_{kind}_{horizon}"].to_numpy(float), rtol=1e-6,
                                       equal_nan=True)
            assert (bars == prep.meta[f"bars_{kind}_{horizon}"].to_numpy()).all()


def test_triple_barrier_with_entry_delay_starts_one_bar_later():
    entry = 100.0
    spike = (entry, entry * 1.5, entry * 0.5, entry)          # ignorée : avant l'entrée retardée
    calm = (entry, entry * 1.001, entry * 0.999, entry)
    frame = barrier_market([calm, spike] + [calm] * 6)
    net, bars = ds.targets(frame, NO_COSTS, 4, "tb", delay=1)
    # entrée à l'ouverture de t+2 (la bougie « spike »), barrières touchées dans la même bougie → stop
    assert bars[99] == 2 and net[99] < 0
    frame = barrier_market([spike, calm] + [calm] * 6)
    net, bars = ds.targets(frame, NO_COSTS, 4, "tb", delay=1)
    assert bars[99] == 5 and net[99] == pytest.approx(0.0)


def test_validation_rows_require_known_context():
    prep = tiny_prepared(4000)
    fold = proto.Fold(0, pd.Timestamp("2024-01-05", tz="UTC"), pd.Timestamp("2024-01-25", tz="UTC"),
                      pd.Timestamp("2024-02-01", tz="UTC"), pd.Timestamp("2024-02-10", tz="UTC"))
    rows = proto.split_rows(prep, fold, "fh", 4)
    assert prep.context_known()[rows.valid].all()
    prep.X[rows.valid[:10], proto.FEATURE_INDEX["h4_ret_6"]] = np.nan          # contexte 4 h périmé
    prep._columns.pop("_context")
    assert len(proto.split_rows(prep, fold, "fh", 4).valid) == len(rows.valid) - 10


def one_trade_run(index: int, start: str, net: float) -> proto.FoldRun:
    begin = pd.Timestamp(start, tz="UTC")
    fold = proto.Fold(index, begin - pd.DateOffset(months=12), begin - pd.DateOffset(months=2), begin,
                      begin + pd.Timedelta(days=30) - pd.Timedelta(seconds=1))
    trades = pd.DataFrame({"symbol": ["ETHUSDT"], "entry_time": [begin + pd.Timedelta(days=2)],
                           "exit_time": [begin + pd.Timedelta(days=2, hours=1)], "notional": [0.1], "net": [net],
                           "pnl": [0.1 * net], "candidate": [0]})
    empty = np.array([], dtype=float)
    prediction = proto.Prediction(np.array([0]), empty, empty, Payoff(0.0, 0.0), 0.5, {}, None, "", 0, 0)
    return proto.FoldRun(fold, prediction, pd.DataFrame({"row": [0]}), np.ones(1, dtype=bool), trades,
                         np.array(["ENTER"], dtype=object), {"sharpe": None})


def test_folds_are_chained_without_rescaling(settings):
    runs = [one_trade_run(0, "2025-07-01", 0.10), one_trade_run(1, "2025-08-01", 0.10)]
    out = proto.aggregate(runs, settings)
    assert out["total_return"] == pytest.approx(1.01 ** 2 - 1, abs=1e-5)
    assert out["days"] == 60 and out["trades"] == 2 and out["win_rate"] == 1.0


def test_a_failed_fold_counts_as_cash_in_the_analysis(settings, monkeypatch):
    prep = tiny_prepared(4000)
    folds = [proto.Fold(i, pd.Timestamp("2024-01-02", tz="UTC"), pd.Timestamp("2024-01-20", tz="UTC"),
                        pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"))
             for i, (start, end) in enumerate((("2024-01-25", "2024-02-01"), ("2024-02-02", "2024-02-09")))]

    def broken(*_args, **_kwargs):
        raise ValueError("ajustement impossible")

    monkeypatch.setattr(proto.engine, "predict_fold", broken)
    runs = proto.run_system(prep, proto.System("fh", 4, "logistic_l2", 0.0), folds, RiskLimits(), seed=1)
    assert [r.state for r in runs] == ["FAILED", "FAILED"]
    out = proto.aggregate(runs, settings)
    assert out["days"] == 8 + 8 and out["total_return"] == 0.0 and out["trades"] == 0


def record_selection(registry: ExperimentRegistry, settings, *, conclusion: str) -> str:
    system = proto.System("fh", 4, "logistic_l2", 0.0).to_dict()
    run_id = "MLI-TEST"
    registry.record(run_id=run_id, created_at="2026-10-01T00:00:00+00:00", kind=proto.KIND_SELECT, hypothesis="h",
                    strategy=proto.STRATEGY_ID, strategy_version=proto.PROTOCOL_VERSION, variant="v", params={},
                    period_label="DEVELOPMENT", period_start="a", period_end="b", universe=[], data_hashes={},
                    git_commit="x", dependencies={}, seed=1, cost_scenario="central", simulation_rules={},
                    metrics={"conclusion": conclusion, "final_system": system,
                             "source_fingerprint": proto.source_fingerprint(),
                             "config_fingerprint": proto.config_fingerprint(settings)},
                    status="COMPLETED", report_dir=str(settings.root / "reports" / run_id))
    return run_id


def test_final_test_is_consulted_at_most_once_and_needs_the_minute_check(settings):
    registry = ExperimentRegistry(settings.experiments_db)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    record_selection(registry, settings, conclusion=proto.ADMISSIBLE)
    with pytest.raises(FinalTestLocked, match="1 min"):
        proto.final(settings, now=now, allow_final_test=True)
    registry.consult_final_test("MLIF-ANCIEN", proto.STRATEGY_ID)
    with pytest.raises(FinalTestLocked, match="déjà été consultée"):
        proto.final(settings, now=now, allow_final_test=True)


def test_final_refuses_a_different_decision_code_or_configuration(settings, monkeypatch):
    registry = ExperimentRegistry(settings.experiments_db)
    record_selection(registry, settings, conclusion=proto.ADMISSIBLE)
    monkeypatch.setattr(proto.finer, "minute_check_status", lambda _dir: (True, "test"))
    settings.costs["central"] = settings.costs["central"].model_copy(update={"fee_bps": 7})
    with pytest.raises(FinalTestLocked, match="différents"):
        proto.final(settings, now=datetime(2026, 10, 1, tzinfo=UTC), allow_final_test=True)


def test_fingerprints_are_stable_and_sensitive():
    from crypto_signal_intelligence.config import load_settings
    first = load_settings()
    assert proto.source_fingerprint() == proto.source_fingerprint()
    changed = first.model_copy(update={"risk": first.risk.model_copy(update={"max_positions": 3})})
    assert proto.config_fingerprint(first) != proto.config_fingerprint(changed)
