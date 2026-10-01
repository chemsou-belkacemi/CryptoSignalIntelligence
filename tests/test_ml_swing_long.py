"""ML swing long (docs/ML_SWING_LONG.md) : magasin long coupé à la fin de DEVELOPMENT, appartenance à la date par
la liquidité, cibles recalculées alignées, plis ancrés au 2017-08-17 (12 validations), champ `history_start` du
moteur, audit des fuites et ses mutations, sélection de bout en bout et registre. Données SYNTHÉTIQUES : ces
tests vérifient le code, jamais une performance de marché."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.features.loader import MissingData
from crypto_signal_intelligence.ml import engine
from crypto_signal_intelligence.ml.intraday import protocol as intraday
from crypto_signal_intelligence.ml.swing import dataset as ds
from crypto_signal_intelligence.ml.swing import long
from crypto_signal_intelligence.ml.swing import protocol as swing
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings
from crypto_signal_intelligence.research.protocol import FinalTestLocked
from crypto_signal_intelligence.research.protocol import period as resolve_period
from crypto_signal_intelligence.research.universe import RESEARCH_UNIVERSE

from .conftest import canonical

DOC = Path(__file__).resolve().parents[1] / "docs" / "ML_SWING_LONG.md"
NOW = datetime(2026, 10, 1, tzinfo=UTC)
DEVELOPMENT_END = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
H = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)

# Magasin long synthétique : la fin de DEVELOPMENT est ramenée au 2024-09-29, les bougies continuent après.
END = pd.Timestamp("2024-09-29 23:59:59", tz="UTC")
STORE_START, STORE_END = "2024-01-01", "2024-10-15"
LATE_LISTING = "2024-04-10"
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT")     # ADA cotée tard, XRP jamais liquide
VOLUME_COLUMNS = ("base_volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume")
THIN_START, THIN_END = pd.Timestamp("2024-04-01", tz="UTC"), pd.Timestamp("2024-06-01", tz="UTC")


def quiet(_text: str) -> None:
    return None


def hourly(symbol: str, seed: int, *, start: str = STORE_START, until: str = STORE_END,
           volume: float = 10.0) -> pd.DataFrame:
    """Bougies 1 h synthétiques ; le volume d'origine (≈ 0,4 M$ par jour) est multiplié par `volume`."""
    n = int((pd.Timestamp(until) - pd.Timestamp(start)) / H)
    frame = canonical(n, "1h", symbol=symbol, start=start, seed=seed)
    for column in VOLUME_COLUMNS:
        frame[column] = frame[column] * volume
    return frame


def synthetic_frames() -> dict[str, pd.DataFrame]:
    frames = {"BTCUSDT": hourly("BTCUSDT", 11), "ETHUSDT": hourly("ETHUSDT", 12), "SOLUSDT": hourly("SOLUSDT", 13),
              "ADAUSDT": hourly("ADAUSDT", 14, start=LATE_LISTING), "XRPUSDT": hourly("XRPUSDT", 15, volume=1.0)}
    thin = (frames["SOLUSDT"]["open_time"] >= THIN_START) & (frames["SOLUSDT"]["open_time"] < THIN_END)
    for column in VOLUME_COLUMNS:                                    # SOL : deux mois de volume dix fois plus faible
        frames["SOLUSDT"].loc[thin, column] = frames["SOLUSDT"].loc[thin, column] / 10
    return frames


@pytest.fixture
def long_store(monkeypatch, settings):
    """Magasin long temporaire de 5 paires ; les réglages NORMAUX gardent leur univers et leur début d'historique."""
    frames = synthetic_frames()
    store = CandleStore(long_settings(settings).data_dir)
    for symbol, frame in frames.items():
        store.save(frame, symbol, "1h")
    monkeypatch.setattr(long, "UNIVERSE", PAIRS)
    settings.protocol.development_end = END.to_pydatetime()
    settings.protocol.bootstrap_samples = 50
    return settings, frames


@pytest.fixture
def small_long(monkeypatch, long_store):
    """Le protocole en miniature : logistique seule, un horizon, une marge, validations d'un mois."""
    settings, frames = long_store
    rule = replace(long.RULE, min_trades=5, min_trades_per_fold=1, min_entry_periods_per_fold=1, min_ci_blocks=5)
    small = replace(long.LONG, specs=(long.SPECS[0],), margins=(0.0,), horizons=(72,), calib_months=1,
                    valid_months=1, min_calib_rows=100, random_draws=3, selection=rule,
                    first_valid_start=date(2024, 6, 1))
    monkeypatch.setitem(engine.PROGRAMS, long.STRATEGY_ID, small)
    monkeypatch.setattr(swing, "AUDIT_PAIRS", ("BTCUSDT", "ETHUSDT"))     # audit complet, mais plus court
    monkeypatch.setattr(swing, "AUDIT_TIMES", 2)
    return settings, small, frames


def cut(frame: pd.DataFrame, end: pd.Timestamp = END) -> pd.DataFrame:
    return frame[frame["open_time"] <= end].reset_index(drop=True)


def reference_volume(h1: pd.DataFrame) -> pd.Series:
    """Calcul INDÉPENDANT du volume moyen par jour : fenêtre glissante de 720 heures de pandas, indexée par
    l'heure d'ouverture ; inconnu tant que la paire n'a pas 30 jours de bougies."""
    series = h1.set_index("open_time")["quote_volume"]
    volume = series.rolling("720h").sum() / 30
    return volume.where(volume.index >= volume.index[0] + 719 * H)


# --- Protocole déclaré -------------------------------------------------------------------------------------

def test_declared_grid_has_42_trials_and_reuses_the_swing_protocol():
    program = long.LONG
    assert program.declared_trials == long.DECLARED_TRIALS == 42
    assert (program.strategy_id, program.kind_select, program.kind_final, program.run_prefix) == (
        "ML_SWING_LONG", "ML_SWING_LONG_SELECT", "ML_SWING_LONG_FINAL", "MLL")
    assert program.targets == ("fh", "tb") and program.horizons == (72, 168) and program.margins == (0.0, 0.0025)
    assert [spec.name for spec in program.specs] == ["logistic_l2", "lgbm_leaves15_n300", "xgb_depth3_n300",
                                                     "catboost_depth4_n400"]
    for spec in program.specs:                                       # hyperparamètres du swing, inchangés
        assert spec is swing.SWING.specs_by_name[spec.name]
    assert len(program.targets) * len(program.horizons) * len(program.specs) * len(program.margins) == 32
    assert program.family_variants is swing.FAMILY_VARIANTS and len(program.family_variants) == 8
    assert program.selection is swing.RULE and program.selection.strict and program.selection.excess_check
    assert program.families is ds.FAMILIES and program.meta_features == swing.META_FEATURES
    assert program.context_required == swing.CONTEXT_REQUIRED
    assert (program.calib_months, program.valid_months, program.train_months) == (3, 6, None)
    assert tuple(program.training_stride(h) for h in program.horizons) == (4, 10)
    assert long.UNIVERSE == RESEARCH_UNIVERSE and len(long.UNIVERSE) == 40
    assert (long.LIQUIDITY_BARS, long.MIN_DAILY_QUOTE_VOLUME) == (720, 1_000_000.0)
    assert {"ml/swing/long.py", "research/long_history.py", "research/universe.py"} <= set(program.decision_modules)
    assert set(swing.DECISION_MODULES) <= set(program.decision_modules)
    assert all((engine.PACKAGE_ROOT / module).exists() for module in program.decision_modules)
    assert engine.PROGRAMS["ML_SWING_LONG"] is program and engine.PROGRAMS["ML_SWING"] is swing.SWING


def test_document_declares_what_the_code_does():
    doc = DOC.read_text(encoding="utf-8")
    versions = [int(v) for v in re.findall(r"^- 2026-\d\d-\d\d, v(\d+)", doc, flags=re.MULTILINE)]
    assert versions and max(versions) == long.PROTOCOL_VERSION
    assert long.LONG.doc == "docs/ML_SWING_LONG.md" and "avant toute exécution" in doc
    for text in ("**42 essais**", "32 essais", "2017-08-17", "2019-07-01", "12 validations", "9 des 12",
                 "1 000 000", "720", "ML_SWING_LONG", "7,3 %", "299/4096"):
        assert text in doc, text
    for name in long.MODEL_NAMES:
        assert f"`{name}`" in doc, name
    assert all(f"`{name}`" in doc for name in swing.CONTEXT_REQUIRED)


def test_twelve_anchored_validations_start_in_july_2019():
    from crypto_signal_intelligence.config import load_settings
    settings = load_settings()
    assert settings.data.history_start == date(2021, 1, 1)            # réglages normaux : sans effet ici
    program = long.LONG
    assert program.first_valid(settings) == pd.Timestamp("2019-07-01", tz="UTC")
    folds = program.folds(settings, program.first_valid(settings), DEVELOPMENT_END)
    assert len(folds) == long.VALIDATIONS == 12
    assert all(f.train_start == pd.Timestamp("2017-08-17", tz="UTC") for f in folds)
    assert [f.valid_start for f in folds] == [pd.Timestamp("2019-07-01", tz="UTC") + pd.DateOffset(months=6 * i)
                                              for i in range(12)]
    assert all(f.calib_start == f.valid_start - pd.DateOffset(months=3) for f in folds)
    assert folds[0].calib_start == pd.Timestamp("2019-04-01", tz="UTC")
    assert folds[0].valid_end == pd.Timestamp("2019-12-31 23:59:59", tz="UTC")
    assert folds[-1].valid_start == pd.Timestamp("2025-01-01", tz="UTC") and folds[-1].valid_end == DEVELOPMENT_END
    period = engine.development_period(program, settings, now=NOW)
    assert period.label == "DEVELOPMENT" and period.start.isoformat() == "2017-08-17T00:00:00+00:00"
    assert pd.Timestamp(period.end) == DEVELOPMENT_END


def test_nine_positive_validations_out_of_twelve_and_the_declared_chance():
    assert long.RULE.required_positive(12) == 9
    assert long.RULE.kept(9, 12) and not long.RULE.kept(8, 12)
    assert long.chance_of_stability() == pytest.approx(299 / 4096)
    draws = np.random.default_rng(0).integers(0, 2, (200_000, 12)).sum(axis=1)      # pile ou face simulé
    assert (draws >= 9).mean() == pytest.approx(299 / 4096, abs=0.003)
    text = " ".join(long.reserves(638))
    assert "au moins 9 validations positives sur 12" in text and "7,3 %" in text and "638 essais" in text
    assert "42 essais" in text and "environ 2,3 systèmes" in text
    for word in ("survivantes", "liquidité", "4 cotées avant 2018", "constants", "liquides ou non", "2021-2025",
                 "15 min", "Aucun ordre"):
        assert word in text, word


# --- Champ `history_start` du moteur ------------------------------------------------------------------------

def test_history_start_field_is_optional_and_leaves_other_programs_unchanged(settings):
    configured = pd.Timestamp("2021-01-01", tz="UTC")
    for program in (swing.SWING, intraday.INTRADAY):
        assert program.history_start is None and program.first_valid_start is None
        assert program.start(settings) == configured
        first = program.first_valid(settings)
        assert first == configured + pd.DateOffset(months=program.first_valid_months)
        assert program.folds(settings, first, DEVELOPMENT_END) == engine.make_folds(
            first, DEVELOPMENT_END, train_months=program.train_months, calib_months=program.calib_months,
            valid_months=program.valid_months, history_start=configured)
        period = engine.development_period(program, settings, now=NOW)
        assert period == resolve_period(settings, "development", now=NOW)
        assert period.start.isoformat() == "2021-01-01T00:00:00+00:00"
    assert len(swing.SWING.folds(settings, swing.SWING.first_valid(settings), DEVELOPMENT_END)) == 6
    settings.data.history_start = date(2022, 3, 1)                    # sans champ : les réglages décident
    assert swing.SWING.start(settings) == pd.Timestamp("2022-03-01", tz="UTC")
    assert engine.development_period(swing.SWING, settings, now=NOW) == resolve_period(settings, "development",
                                                                                         now=NOW)


def test_a_program_history_start_moves_anchor_first_validation_and_recorded_period(settings):
    moved = replace(swing.SWING, history_start=date(2019, 1, 1))
    start = pd.Timestamp("2019-01-01", tz="UTC")
    assert moved.start(settings) == start
    assert moved.first_valid(settings) == pd.Timestamp("2020-07-01", tz="UTC")       # + 18 mois
    folds = moved.folds(settings, moved.first_valid(settings), DEVELOPMENT_END)
    assert len(folds) == 10 and all(f.train_start == start for f in folds)
    period = engine.development_period(moved, settings, now=NOW)
    assert period.start == datetime(2019, 1, 1, tzinfo=UTC) and pd.Timestamp(period.end) == DEVELOPMENT_END
    sliding = replace(intraday.INTRADAY, history_start=date(2019, 1, 1))
    assert sliding.first_valid(settings) == start + pd.DateOffset(months=intraday.INTRADAY.first_valid_months)
    fixed = replace(moved, first_valid_start=date(2021, 1, 1))
    assert fixed.first_valid(settings) == pd.Timestamp("2021-01-01", tz="UTC")
    assert fixed.folds(settings, fixed.first_valid(settings), DEVELOPMENT_END)[0].train_start == start


# --- Liquidité : appartenance à la date ---------------------------------------------------------------------

def flat(n: int = 900, quote: float = 50_000.0) -> pd.DataFrame:
    """Volume constant de 50 000 $ par heure : exactement 1 200 000 $ par jour."""
    times = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open_time": times, "available_at": times + pd.Timedelta(hours=1, seconds=2),
                         "quote_volume": quote})


def test_liquidity_is_the_mean_daily_volume_of_the_720_closed_bars_before_the_decision():
    frame = flat()
    volume = long.daily_quote_volume(frame)
    assert np.isnan(volume[:719]).all() and (volume[719:] == 1_200_000.0).all()   # 30 jours de cotation exigés
    frame.loc[800, "quote_volume"] = 80_000.0                        # + 30 000 $ dans une seule bougie
    volume = long.daily_quote_volume(frame)
    assert volume[799] == 1_200_000.0                                 # la bougie SUIVANTE n'est pas lue
    assert volume[800] == 1_201_000.0                                 # la bougie de la décision, clôturée, l'est
    assert (volume[719:800] == 1_200_000.0).all() and (volume[800:] == 1_201_000.0).all()
    longer = flat(1600)
    longer.loc[800, "quote_volume"] = 80_000.0
    volume = long.daily_quote_volume(longer)
    assert (volume[800:1520] == 1_201_000.0).all() and (volume[1520:] == 1_200_000.0).all()   # 720 bougies, pas 721
    np.testing.assert_allclose(long.daily_quote_volume(flat()), reference_volume(flat()).to_numpy(), equal_nan=True)


def test_liquidity_threshold_is_exact(monkeypatch):
    monkeypatch.setattr(long, "MIN_DAILY_QUOTE_VOLUME", 1_200_000.0)
    frame = flat()
    decisions = ds.decision_mask(frame)
    rows = long.decision_rows(frame)
    assert not rows[:719].any() and (rows[719:] == decisions[719:]).all() and rows[719]     # égal au seuil : liquide
    assert rows.sum() == decisions[719:].sum() == (900 - 719 + 3) // 4
    short = flat()
    short.loc[100, "quote_volume"] -= 1.0                             # un dollar de moins dans la fenêtre
    rows = long.decision_rows(short)
    assert not rows[:820].any()                                       # fenêtres 719 … 819 : sous le seuil
    assert (rows[820:] == decisions[820:]).all() and rows[820:].any()   # la bougie 100 est sortie de la fenêtre
    monkeypatch.setattr(long, "MIN_DAILY_QUOTE_VOLUME", 1_000_000.0)
    assert (long.decision_rows(short)[719:] == decisions[719:]).all()


def test_a_missing_hour_counts_as_zero_volume_and_listing_needs_thirty_days():
    holed = flat().drop(index=[300, 301]).reset_index(drop=True)     # deux heures sans bougie
    volume = pd.Series(long.daily_quote_volume(holed), index=holed["open_time"])
    start = holed["open_time"].iloc[0]
    assert volume[volume.index < start + 719 * H].isna().all()        # 30 jours comptés en HEURES, pas en lignes
    inside = volume[(volume.index >= start + 719 * H) & (volume.index <= start + 1019 * H)]
    assert len(inside) and (inside == 718 * 50_000 / 30).all()
    assert (volume[volume.index >= start + 1021 * H] == 1_200_000.0).all()
    assert np.isnan(long.daily_quote_volume(flat(700))).all()         # moins de 30 jours de cotation
    assert len(long.daily_quote_volume(flat(0))) == 0 and not long.decision_rows(flat(700)).any()


def test_liquidity_does_not_change_when_candles_after_the_decision_are_cut_or_falsified():
    rng = np.random.default_rng(5)
    frame = flat(1500)
    frame["quote_volume"] = rng.lognormal(10.5, 0.6, len(frame))
    full = long.daily_quote_volume(frame)
    for row in (719, 760, 1203, 1499):
        truncated = long.daily_quote_volume(frame.iloc[:row + 1])
        assert truncated[row] == full[row]                            # couper après la décision ne change rien
        falsified = frame.copy()
        falsified.loc[row + 1:, "quote_volume"] *= 50
        assert long.daily_quote_volume(falsified)[row] == full[row]
    moments = [frame["open_time"].iloc[row] for row in (760, 1203)]
    assert long.liquidity_violations(frame, decisions=moments) == []
    leaks = long.liquidity_violations(frame, decisions=moments, lead=24)      # MUTATION : fenêtre décalée d'un jour
    assert {(v["open_time"], v["check"]) for v in leaks} == {
        (str(m), f"liquidité : {label}") for m in moments for label in ("tronqué", "futur falsifié")}
    assert long.liquidity_violations(frame, decisions=[pd.Timestamp("2030-01-01", tz="UTC")], lead=24) == []


# --- Données préparées --------------------------------------------------------------------------------------

def expected_decisions(h1: pd.DataFrame) -> pd.DatetimeIndex:
    """Heures d'ouverture des décisions attendues d'une paire : fin d'un bloc de 4 h et volume ≥ seuil."""
    volume = reference_volume(h1)
    keep = (volume.to_numpy() >= long.MIN_DAILY_QUOTE_VOLUME) & ((volume.index + H).hour % 4 == 0)
    return volume.index[keep]


def test_only_liquid_decisions_are_kept_everywhere(long_store):
    settings, frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    meta = prep.meta
    assert prep.symbols == list(PAIRS) and prep.program is long.LONG and prep.X.shape == (len(meta), len(ds.FEATURES))
    assert "XRPUSDT" not in set(meta["symbol"].astype(str))          # volume d'origine : jamais 1 M$ par jour
    assert (meta["quote_volume_30d"] >= long.MIN_DAILY_QUOTE_VOLUME).all()
    for symbol in PAIRS:
        h1 = cut(frames[symbol])
        rows = meta[meta["symbol"] == symbol]
        expected = expected_decisions(h1)
        assert list(rows["open_time"]) == list(expected), symbol
        assert (rows["decision_time"] == rows["open_time"] + H).all()
        np.testing.assert_allclose(rows["quote_volume_30d"], reference_volume(h1).loc[expected], rtol=1e-9)
    sol = meta[meta["symbol"] == "SOLUSDT"]
    thin = (sol["decision_time"] > THIN_START + 29 * DAY) & (sol["decision_time"] <= THIN_END)
    assert not thin.any()                                             # 30 jours de faible volume : plus de décision
    assert (sol["decision_time"] < THIN_START).any() and (sol["decision_time"] > THIN_END + 30 * DAY).any()
    assert 0 < len(sol) < len(meta[meta["symbol"] == "BTCUSDT"])
    for kind in ds.TARGETS:                                           # lignes d'entraînement : la même population
        for horizon in long.HORIZONS:
            train = engine.training_mask(prep, kind, horizon)
            assert train.any() and set(meta.loc[train, "symbol"].astype(str)) == {"BTCUSDT", "ETHUSDT", "SOLUSDT",
                                                                                 "ADAUSDT"}


def test_market_reference_of_the_excess_criterion_uses_liquid_decisions_only(long_store):
    """Critère 7 : la moyenne « de toutes les décisions au même instant » porte sur la même population que les
    décisions (liquides, contexte connu, sans trou), pas sur la paire illiquide pourtant cotée."""
    settings, frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    reference = engine.same_time_mean_net(prep, "fh", 72)
    moment = pd.Timestamp("2024-08-15 12:00", tz="UTC")
    illiquid = ds.pair_decisions(cut(frames["XRPUSDT"]), cut(frames["BTCUSDT"]), symbol="XRPUSDT",
                                 costs=settings.costs["central"], horizons=(72,), kinds=("fh",))
    xrp = illiquid[illiquid["decision_time"] == moment].iloc[0]
    assert np.isfinite(xrp["net_fh_72"]) and not xrp["gap_recent"] and np.isfinite(xrp["d1_ret_7"])   # décision valide…
    at = prep.meta[prep.meta["decision_time"] == moment]
    assert sorted(at["symbol"].astype(str)) == ["ADAUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]           # … mais illiquide
    liquid_mean = float(at["net_fh_72"].astype(float).mean())
    assert reference.loc[moment.tz_convert(None)] == pytest.approx(liquid_mean, abs=1e-9)
    with_illiquid = (at["net_fh_72"].astype(float).sum() + float(xrp["net_fh_72"])) / 5
    assert abs(with_illiquid - liquid_mean) > 1e-6


def test_cross_section_ranks_every_listed_pair_liquid_or_not(long_store):
    settings, frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    everything = ds.add_cross_section(pd.concat(
        [ds.pair_frame(cut(frames[s]), cut(frames["BTCUSDT"])).assign(symbol=s) for s in PAIRS], ignore_index=True))
    expected = everything.set_index(["symbol", "open_time"])[list(ds.FAMILIES["coupe"])]
    index = pd.MultiIndex.from_arrays([prep.meta["symbol"].astype(str), prep.meta["open_time"]])
    columns = [prep.program.feature_index[name] for name in ds.FAMILIES["coupe"]]
    np.testing.assert_allclose(prep.X[:, columns], expected.loc[index].to_numpy(np.float32), equal_nan=True)
    moment = pd.Timestamp("2024-08-15 12:00", tz="UTC")
    ranks = prep.X[(prep.meta["decision_time"] == moment).to_numpy(), prep.program.feature_index["xs_rank_168"]]
    assert len(ranks) == 4 and set(np.round(ranks * 5).astype(int)) < {1, 2, 3, 4, 5}      # rangs sur 5 paires


def test_recomputed_scenario_targets_stay_aligned_on_the_liquid_decisions(long_store):
    settings, frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    meta = prep.meta
    for kind in ds.TARGETS:
        for horizon in long.HORIZONS:
            net, bars = long.scenario_targets(prep, kind, horizon, settings.costs["central"], 0)
            assert len(net) == len(bars) == len(meta)
            np.testing.assert_allclose(net, meta[f"net_{kind}_{horizon}"].to_numpy(float), rtol=1e-6, equal_nan=True)
            assert (bars == meta[f"bars_{kind}_{horizon}"].to_numpy()).all()
    # Scénario défavorable, une bougie de retard : calcul à la main sur les bougies de la paire de chaque ligne.
    costs = settings.costs["adverse"]
    net, bars = long.scenario_targets(prep, "fh", 72, costs, 1)
    market, fee = (costs.slippage_bps + costs.half_spread_bps) / 1e4, costs.fee_bps / 1e4
    picks = np.random.default_rng(3).choice(np.flatnonzero(np.isfinite(net)), size=25, replace=False)
    assert {str(s) for s in meta["symbol"].iloc[picks]} == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"}
    for row in picks:
        candles = frames[str(meta["symbol"].iloc[row])].set_index("open_time")
        opened = meta["open_time"].iloc[row]
        entry, exit_price = candles.loc[opened + 2 * H, "open"], candles.loc[opened + 73 * H, "close"]
        assert net[row] == pytest.approx(exit_price * (1 - market) * (1 - fee) / (entry * (1 + market) * (1 + fee)) - 1)
        assert bars[row] == 73


def test_a_pair_listed_late_has_no_decision_before_thirty_days_and_a_known_context(long_store):
    settings, frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    meta = prep.meta
    listed = frames["ADAUSDT"]["open_time"].min()
    assert listed == pd.Timestamp(LATE_LISTING, tz="UTC")
    ada = (meta["symbol"] == "ADAUSDT").to_numpy()
    assert meta.loc[ada, "decision_time"].min() == listed + 30 * DAY          # 720 bougies clôturées
    assert meta.loc[~ada & (meta["symbol"] == "BTCUSDT").to_numpy(), "decision_time"].min() == pd.Timestamp(
        STORE_START, tz="UTC") + 30 * DAY
    assert prep.context_known()[ada].all()                                    # contexte 4 h, 1 jour et BTC connus
    program = replace(long.LONG, first_valid_start=date(2024, 4, 1), valid_months=1, calib_months=1)
    for fold in program.folds(settings, program.first_valid(settings), END):
        rows = engine.split_rows(prep, fold, "fh", 72)
        for part in (rows.fit, rows.calib, rows.valid):
            late = part[ada[part]]
            assert (meta["decision_time"].iloc[late] >= listed + 30 * DAY).all()
    last = program.folds(settings, program.first_valid(settings), END)[-1]
    assert ada[engine.split_rows(prep, last, "fh", 72).valid].any()           # ensuite, la paire décide bien


def test_nothing_after_the_end_of_development_is_read(long_store):
    settings, frames = long_store
    before = long.prepare(settings, end=END, progress=quiet)
    store = CandleStore(long_settings(settings).data_dir)
    for symbol, frame in frames.items():                              # bougies postérieures FALSIFIÉES
        falsified = frame.copy()
        later = falsified["open_time"] > END
        assert later.sum() > 300
        for column in ("open", "high", "low", "close", *VOLUME_COLUMNS):
            falsified.loc[later, column] = falsified.loc[later, column] * 3.0
        store.save(falsified, symbol, "1h")
    after = long.prepare(settings, end=pd.Timestamp("2030-01-01", tz="UTC"), progress=quiet)   # date plus tardive
    assert after.end == END == before.end
    pd.testing.assert_frame_equal(before.meta, after.meta)
    np.testing.assert_array_equal(before.X, after.X)
    assert before.common["data_hashes"] == after.common["data_hashes"]
    assert after.daily_closes.equals(before.daily_closes) and after.daily_closes.index.max() <= END
    for data in after.inputs.values():
        assert set(data) == {"context", "btc"}                        # bougies 1 h seulement, aucune 15 min
        assert all(frame["open_time"].max() <= END for frame in data.values())
    assert after.meta["decision_time"].max() <= END + pd.Timedelta(seconds=1)
    tail = after.meta["decision_time"] > END - 71 * H                 # cible qui dépasserait la coupure : absente
    assert tail.any() and after.meta.loc[tail, "net_fh_72"].isna().all()
    assert after.meta.loc[tail, "net_tb_72"].isna().all() and after.meta["net_fh_72"].notna().any()


def test_only_the_final_stage_may_read_beyond_development(long_store, monkeypatch):
    settings, _frames = long_store
    now = datetime(2024, 10, 14, 12, tzinfo=UTC)
    with pytest.raises(FinalTestLocked, match="i-understand-final-test"):
        long.final(settings, now=now, allow_final_test=False)
    with pytest.raises(FinalTestLocked, match="aucune sélection"):     # verrous du moteur avant toute lecture
        long.final(settings, now=now, allow_final_test=True)
    seen: dict = {}

    def fake_final(program, settings, **kwargs):
        seen["prep"] = program.prepare(settings, end=kwargs["now"], progress=quiet)
        return {}

    monkeypatch.setattr(engine, "final", fake_final)
    long.final(settings, now=now, allow_final_test=True)
    assert seen["prep"].end == pd.Timestamp(now) and seen["prep"].meta["decision_time"].max() > END
    assert long.LONG.prepare(settings, end=now, progress=quiet).meta["decision_time"].max() <= END + pd.Timedelta(
        seconds=1)


def test_an_incomplete_long_store_is_refused(long_store, monkeypatch):
    settings, frames = long_store
    monkeypatch.setattr(long, "UNIVERSE", (*PAIRS, "DOTUSDT"))        # paire absente du magasin long
    with pytest.raises(MissingData, match="DOTUSDT"):
        long.prepare(settings, end=END, progress=quiet)
    stale = frames["ETHUSDT"][frames["ETHUSDT"]["open_time"] < END - 5 * DAY]
    CandleStore(long_settings(settings).data_dir).save(stale, "DOTUSDT", "1h")
    with pytest.raises(RuntimeError, match="incomplet.*DOTUSDT"):
        long.prepare(settings, end=END, progress=quiet)
    assert not CandleStore(settings.data_dir).path("BTCUSDT", "1h").exists()   # le magasin courant n'est pas lu


# --- Audit des fuites ---------------------------------------------------------------------------------------

def test_leak_audit_passes_and_detects_every_mutation(long_store, monkeypatch):
    """L'audit complet (celui du swing, plus les contrôles propres à ce programme), sur deux paires et deux
    instants par paire pour rester rapide."""
    settings, _frames = long_store
    monkeypatch.setattr(swing, "AUDIT_PAIRS", ("BTCUSDT", "ETHUSDT"))
    monkeypatch.setattr(swing, "AUDIT_TIMES", 2)
    prep = long.prepare(settings, end=END, progress=quiet)
    audit = long.leak_audit(settings, prep, seed=settings.protocol.seed)
    assert audit["passed"] and audit["violations"] == [] and audit["mutation_detected"]
    assert audit["mutation_by_timeframe"] == {"4h": True, "1d": True}
    assert audit["checked_pairs"] == ["BTCUSDT", "ETHUSDT"]
    assert audit["liquidity"] == {"violations": 0, "mutation_detected": True, "checked_pairs": 2,
                                  "times_per_pair": 2, "min_daily_quote_volume": 1_000_000.0}
    assert audit["cutoff"] == {"end": str(END), "development_end": str(END), "pairs_beyond": []}
    assert audit["population"] == {"violations": 0, "checked_pairs": len(PAIRS)}


def swing_audit_stub(**overrides):
    """Résultat canné de l'audit du swing (testé dans tests/test_ml_swing.py) : les tests des contrôles propres
    à ce programme n'ont pas à le recalculer."""
    base = {"violations": [], "mutation_detected": True, "mutation_by_timeframe": {"4h": True, "1d": True},
            "checked_pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "times_per_pair": 4, "passed": True}
    return lambda settings, prep, *, seed: base | overrides


def test_leak_audit_fails_when_the_liquidity_filter_reads_the_future(long_store, monkeypatch):
    settings, _frames = long_store
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub())
    prep = long.prepare(settings, end=END, progress=quiet)
    assert long.leak_audit(settings, prep, seed=1)["passed"]
    honest = long.daily_quote_volume
    monkeypatch.setattr(long, "daily_quote_volume", lambda h1, *, lead=0: honest(h1, lead=lead + 6))
    audit = long.leak_audit(settings, prep, seed=1)
    assert not audit["passed"] and audit["liquidity"]["violations"] > 0
    assert {"liquidité : tronqué", "liquidité : futur falsifié"} <= {v["check"] for v in audit["violations"]}


def test_leak_audit_fails_when_its_own_mutation_is_not_detected_or_a_candle_is_beyond_the_cutoff(long_store,
                                                                                               monkeypatch):
    settings, frames = long_store
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub())
    prep = long.prepare(settings, end=END, progress=quiet)
    prep.inputs["ETHUSDT"]["context"] = frames["ETHUSDT"]            # bougies non coupées
    audit = long.leak_audit(settings, prep, seed=1)
    assert not audit["passed"] and audit["cutoff"]["pairs_beyond"] == ["ETHUSDT"]
    assert {"check": "bougie après la coupure", "symbol": "ETHUSDT"} in audit["violations"]
    prep.inputs["ETHUSDT"]["context"] = cut(frames["ETHUSDT"])
    assert long.leak_audit(settings, prep, seed=1)["passed"]
    monkeypatch.setattr(long, "LEAK_LEAD_BARS", 0)                    # mutation neutralisée : l'audit ne prouve plus rien
    audit = long.leak_audit(settings, prep, seed=1)
    assert audit["violations"] == [] and not audit["liquidity"]["mutation_detected"] and not audit["passed"]


def test_leak_audit_fails_when_a_swing_mutation_is_not_detected(long_store, monkeypatch):
    settings, _frames = long_store
    prep = long.prepare(settings, end=END, progress=quiet)
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub(mutation_detected=False, passed=False,
                                                              mutation_by_timeframe={"4h": False, "1d": True}))
    audit = long.leak_audit(settings, prep, seed=1)
    assert audit["mutation_by_timeframe"] == {"4h": False, "1d": True} and not audit["passed"]
    assert audit["liquidity"]["mutation_detected"] and not audit["mutation_detected"]
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub(violations=[{"check": "coupe transversale"}], passed=False))
    audit = long.leak_audit(settings, prep, seed=1)
    assert audit["violations"] == [{"check": "coupe transversale"}] and not audit["passed"]


def test_leak_audit_catches_a_leak_inside_the_preparation_itself(long_store, monkeypatch):
    """Fuite injectée SEULEMENT dans `_prepare` (fenêtre de liquidité décalée de 240 h vers le futur) : la
    fonction de liquidité contrôlée reste honnête, mais la population préparée diffère, et l'audit échoue."""
    settings, _frames = long_store
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub())
    honest = long.daily_quote_volume
    monkeypatch.setattr(long, "daily_quote_volume", lambda h1, *, lead=0: honest(h1, lead=lead + 240))
    leaky_prep = long.prepare(settings, end=END, progress=quiet)
    monkeypatch.setattr(long, "daily_quote_volume", honest)
    audit = long.leak_audit(settings, leaky_prep, seed=1)
    assert not audit["passed"] and audit["population"]["violations"] > 0
    assert any(v["check"].startswith("population") or v["check"].startswith("volume") for v in audit["violations"])


def test_a_preparation_beyond_development_fails_the_audit(long_store, monkeypatch):
    settings, frames = long_store
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub())
    beyond = long._prepare(settings, end=END + pd.Timedelta(days=15), progress=quiet)
    audit = long.leak_audit(settings, beyond, seed=1)
    assert not audit["passed"] and audit["cutoff"]["pairs_beyond"]


def test_selection_is_refused_without_exactly_twelve_validations(long_store):
    settings, _frames = long_store
    with pytest.raises(RuntimeError, match="validations au lieu des 12"):
        long.select(settings, now=NOW, allow_dirty=True)          # fin de DEVELOPMENT des tests : bien moins de 12
    assert not settings.experiments_db.exists() or ExperimentRegistry(settings.experiments_db).program_trials() == 0


# --- Bout en bout -------------------------------------------------------------------------------------------

def test_selection_runs_end_to_end_and_is_recorded_in_the_normal_registry(small_long):
    settings, small, _frames = small_long
    messages: list[str] = []
    result = long.select(settings, now=NOW, allow_dirty=True, progress=messages.append, program=small)
    payload = result.payload
    assert result.run_id.startswith("MLL") and not result.run_id.startswith("MLLF")
    assert result.leak_audit["passed"] and result.leak_audit["liquidity"]["mutation_detected"]
    assert payload["program"] == "ML_SWING_LONG" and payload["protocol_version"] == long.PROTOCOL_VERSION
    assert payload["n_trials"] == payload["declared_trials"] == small.declared_trials == 2 + 8 + 2
    assert payload["selection_rule"]["strict"] and payload["selection_rule"]["excess_check"]
    assert [f["valid_start"][:10] for f in payload["folds"]] == ["2024-06-01", "2024-07-01", "2024-08-01",
                                                                  "2024-09-01"]
    assert all(f["train_start"].startswith("2017-08-17") for f in payload["folds"])
    assert result.conclusion in (engine.NO_EDGE, engine.ADMISSIBLE)
    assert any("variables XRPUSDT" in m for m in messages) and "audit des fuites" in messages
    for name in ("summary.json", "report.md", "grid.csv", "decisions.parquet", "leak_audit.json"):
        assert (result.report_dir / name).exists(), name
    report = (result.report_dir / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# ML swing long") and "survivantes" in report and "Aucun ordre" in report
    journal = pd.read_parquet(result.report_dir / "decisions.parquet")
    assert len(journal) and set(journal["symbol"]) <= {"BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"}
    assert journal["decision_time"].max() <= END
    # Registre et rapports des réglages NORMAUX, pas ceux du magasin long.
    assert result.report_dir.parent == settings.reports_dir and settings.experiments_db.exists()
    assert not long_settings(settings).experiments_db.exists()
    run = ExperimentRegistry(settings.experiments_db).get(result.run_id)
    assert run["kind"] == "ML_SWING_LONG_SELECT" and run["status"] == "COMPLETED"
    assert run["strategy"] == "ML_SWING_LONG" and run["period_label"] == "DEVELOPMENT"
    assert run["period_start"] == "2017-08-17T00:00:00+00:00"         # et non le 2021-01-01 des réglages
    assert pd.Timestamp(run["period_end"]) == END and run["universe"] == list(PAIRS)
    assert run["metrics"]["n_trials"] == 12 and run["metrics"]["verdict"] == result.conclusion
    assert run["params"]["horizons"] == [72] and run["params"]["min_daily_quote_volume"] == 1_000_000.0
    assert run["params"]["history_start"] == "2017-08-17" and run["seed"] == settings.protocol.seed
    assert set(run["data_hashes"]) == set(PAIRS)
    assert ExperimentRegistry(settings.experiments_db).program_trials("DEVELOPMENT") == 12


def test_a_failed_selection_is_recorded_with_the_program_period(small_long, monkeypatch):
    settings, small, _frames = small_long
    monkeypatch.setattr(swing, "leak_audit", swing_audit_stub())
    monkeypatch.setattr(long, "LEAK_LEAD_BARS", 0)                    # audit en échec → arrêt avant tout essai
    with pytest.raises(RuntimeError, match="audit des fuites en échec"):
        long.select(settings, now=NOW, allow_dirty=True, program=small)
    registry = ExperimentRegistry(settings.experiments_db)
    failed = registry.get(registry.recent(1)[0]["run_id"])
    assert failed["kind"] == "ML_SWING_LONG_SELECT" and failed["status"] == "FAILED"
    assert failed["period_start"] == "2017-08-17T00:00:00+00:00" and failed["metrics"]["n_trials"] == 0
