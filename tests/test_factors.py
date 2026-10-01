"""Lot 7 — portefeuilles hebdomadaires (docs/FACTORS.md) : panneau, éligibilité, poids, HMM, simulation, mesures,
règle, audit des fuites, bout en bout. Données SYNTHÉTIQUES."""
from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

START = pd.Timestamp("2024-01-01", tz="UTC")
DAY = pd.Timedelta(days=1)
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def flat_days(prices: list[float], *, start: pd.Timestamp = START, volume: float = 1e6, hours: int = 24) -> pd.DataFrame:
    """Bougies 1 h où le prix est constant dans la journée : jour d au prix prices[d]."""
    rows = []
    for d, p in enumerate(prices):
        for hour in range(hours):
            rows.append((start + d * DAY + pd.Timedelta(hours=hour), p, p, p, p, volume / 24))
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "quote_volume"])


def trend(first: float, daily: float, days: int) -> list[float]:
    return [first * (1 + daily) ** d for d in range(days)]


# --- Panneau et éligibilité ---------------------------------------------------------------------------------

def test_panel_reads_the_last_known_close_and_the_first_price_after_the_decision():
    frame = flat_days([100.0, 110.0, 120.0])
    panel = fa.build_panel({"BTCUSDT": frame})
    moment = START + DAY                                             # instant de décision après le jour 0
    assert panel.close.loc[moment, "BTCUSDT"] == 100.0               # clôture connue : celle du jour 0
    assert panel.price.loc[moment, "BTCUSDT"] == 110.0               # exécution : ouverture de 01:00 du jour 1
    assert panel.volume.loc[moment, "BTCUSDT"] == pytest.approx(1e6)
    assert panel.close.index[0] == moment                            # rien n'est connu avant la fin du jour 0

    def with_hours_on_day_one(hours: int) -> fa.Panel:
        day_one = frame["open_time"].dt.floor("D") == START + DAY
        keep = ~day_one | (frame["open_time"].dt.hour < hours)
        return fa.build_panel({"BTCUSDT": pd.concat([frame[keep].iloc[30:], frame[keep].iloc[:30]])})   # désordonné

    after_day_one = START + 2 * DAY
    assert np.isnan(with_hours_on_day_one(19).close.loc[after_day_one, "BTCUSDT"])       # 19 bougies : invalide
    assert with_hours_on_day_one(20).close.loc[after_day_one, "BTCUSDT"] == 110.0        # 20 bougies : valide
    assert with_hours_on_day_one(20).volume.loc[after_day_one, "BTCUSDT"] == pytest.approx(1e6 * 20 / 24)
    no_exec = frame[frame["open_time"] != moment + pd.Timedelta(hours=1)]
    assert np.isnan(fa.build_panel({"BTCUSDT": no_exec}).price.loc[moment, "BTCUSDT"])   # pas de bougie de 01:00
    with pytest.raises(fa.IncompleteData):
        fa.build_panel({"BTCUSDT": frame.iloc[:0]})


def test_eligibility_needs_history_liquidity_and_uses_only_the_past():
    days = 120
    panel = fa.build_panel({"BTCUSDT": flat_days([100.0] * days), "LOWUSDT": flat_days([5.0] * days, volume=5e5),
                            "NEWUSDT": flat_days([7.0] * 60, start=START + 60 * DAY)})
    book = fa.Book(panel, panel.close.index)
    eligible = book.eligible
    assert not eligible.loc[START + 89 * DAY, "BTCUSDT"] and eligible.loc[START + 90 * DAY, "BTCUSDT"]
    assert not eligible["LOWUSDT"].any()                              # volume médian sous 1 M$
    assert not eligible["NEWUSDT"].any()                              # 60 jours d'historique seulement

    def with_missing_days(first: int, last: int) -> pd.Series:
        frame = flat_days([100.0] * days)
        missing = [START + d * DAY for d in range(first, last + 1)]
        frame = frame[~frame["open_time"].dt.floor("D").isin(missing)]
        return fa.Book(fa.build_panel({"BTCUSDT": frame}), panel.close.index).eligible["BTCUSDT"]

    moment = START + 110 * DAY                                        # fenêtre de 90 jours : journées 20 à 109
    assert not with_missing_days(40, 45).loc[moment]                  # 84 journées valides sur 90 : insuffisant
    assert with_missing_days(40, 44).loc[moment]                      # 85 sur 90 : suffisant
    assert not with_missing_days(109, 109).loc[moment]                # la dernière journée doit être valide
    assert with_missing_days(109, 109).loc[moment - DAY]


def test_decisions_are_mondays_with_a_full_week_of_prices_after_them():
    panel = fa.build_panel({"BTCUSDT": flat_days([100.0] * 40)})
    decisions = fa.decisions_of(panel, first=START)
    assert (decisions.dayofweek == 0).all()
    assert decisions[0] == START + 7 * DAY                           # le lundi 1er janvier, aucune clôture n'est connue
    assert (decisions[1:] - decisions[:-1] == 7 * DAY).all()
    assert decisions[-1] + 7 * DAY <= panel.price.dropna().index.max() < decisions[-1] + 14 * DAY


# --- Poids ---------------------------------------------------------------------------------------------------

def four_assets(days: int = 150) -> fa.Panel:
    return fa.build_panel({"BTCUSDT": flat_days(trend(100, 0.001, days)), "UPUSDT": flat_days(trend(10, 0.004, days)),
                           "MIDUSDT": flat_days(trend(20, 0.002, days)), "DOWNUSDT": flat_days(trend(30, -0.003, days))})


def test_ranking_picks_the_best_k_and_leaves_the_rest_in_cash():
    panel = four_assets()
    decisions = fa.decisions_of(panel, first=START + 98 * DAY)
    book = fa.Book(panel, decisions)
    assert book.momentum(28).loc[decisions[0], "UPUSDT"] == pytest.approx(1.004 ** 28 - 1)   # 28 jours, pas 27 ni 29
    assert book.returns.loc[decisions[0], "DOWNUSDT"] == pytest.approx(-0.003)
    top2 = book.top(book.momentum(28), 2).loc[decisions[0]]
    assert top2["UPUSDT"] == top2["MIDUSDT"] == 0.5 and top2["BTCUSDT"] == top2["DOWNUSDT"] == 0.0
    top5 = book.weights("MOM_L28_K5").loc[decisions[0]]
    assert (top5 == 0.2).all() and top5.sum() == pytest.approx(0.8)   # 4 paires éligibles : 1/5 chacune, 20 % en USDT
    assert book.weights("REVERSAL_K5").loc[decisions[0]].sum() == pytest.approx(0.8)
    lowest = book.top(book.momentum(7), 1, lowest=True).loc[decisions[0]]
    assert lowest["DOWNUSDT"] == 1.0
    assert book.weights(fa.EW).loc[decisions[0]].tolist() == [0.25] * 4
    tsmom = book.weights("TSMOM_L28").loc[decisions[0]]
    assert tsmom["DOWNUSDT"] == 0.0 and tsmom["UPUSDT"] == 0.25 and tsmom.sum() == pytest.approx(0.75)
    only_btc = book.weights(fa.BTC)
    assert len(only_btc) == 1 and only_btc.iloc[0]["BTCUSDT"] == 1.0  # acheté une fois, puis conservé


def test_regimes_switch_between_the_basket_and_cash():
    up = fa.build_panel({"BTCUSDT": flat_days(trend(100, 0.002, 260)), "UPUSDT": flat_days(trend(10, 0.001, 260))})
    down = fa.build_panel({"BTCUSDT": flat_days(trend(100, -0.002, 260)), "UPUSDT": flat_days(trend(10, 0.001, 260))})
    for panel, invested in ((up, True), (down, False)):
        decisions = fa.decisions_of(panel, first=START + 210 * DAY)
        book = fa.Book(panel, decisions)
        for key in ("REGIME_SMA200", "REGIME_SMA100", "REGIME_MOM28", "DUAL_SMA200", "DUAL_MOM28"):
            assert (book.weights(key).sum(axis=1) > 0).all() == invested, key
    early = fa.Book(up, fa.decisions_of(up, first=START + 105 * DAY))
    assert early.weights("REGIME_SMA200").iloc[0].sum() == 0          # moyenne 200 jours pas encore calculable


def test_volatility_managed_exposure_never_exceeds_one_and_drops_when_volatility_rises():
    rng = np.random.default_rng(4)
    calm = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    wild = calm[-1] * np.exp(np.cumsum(rng.normal(0, 0.06, 80)))
    panel = fa.build_panel({"BTCUSDT": flat_days([*calm, *wild])})
    decisions = fa.decisions_of(panel, first=START + 91 * DAY)
    exposure = fa.Book(panel, decisions).weights("VOLMAN_BTC")["BTCUSDT"]
    assert exposure.max() <= 1.0 and (exposure.iloc[:fa.VOL_MIN_DECISIONS - 1] == 1.0).all()   # cible pas encore définie
    assert exposure.iloc[-1] < 0.5 < exposure.iloc[40]


# --- HMM -------------------------------------------------------------------------------------------------------

def two_regimes(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.normal(0.001, 0.01, 300), rng.normal(-0.002, 0.05, 300), rng.normal(0.001, 0.01, 200)])


def test_hmm_separates_calm_from_turbulent_and_filters_causally():
    x = two_regimes()
    params = fa.hmm_fit(x)
    assert params == fa.hmm_fit(x)                                    # déterministe
    calm, wild = params.calm, 1 - params.calm
    assert params.var[calm] == pytest.approx(1e-4, rel=0.3) and params.var[wild] == pytest.approx(25e-4, rel=0.3)
    filtered = fa.hmm_filter(x, params)[:, calm]
    assert filtered[50:300].mean() > 0.9 and filtered[350:600].mean() < 0.1 and filtered[650:].mean() > 0.9
    np.testing.assert_allclose(fa.hmm_filter(x[:400], params), fa.hmm_filter(x, params)[:400])   # aucun regard futur
    changed = x.copy()
    changed[400:] *= 3
    np.testing.assert_allclose(fa.hmm_filter(changed, params)[:400], fa.hmm_filter(x, params)[:400])


def test_hmm_regime_learns_from_the_past_only():
    x = two_regimes(1)
    prices = 100 * np.exp(np.cumsum(x))
    panel = fa.build_panel({"BTCUSDT": flat_days(list(prices))})
    decisions = fa.decisions_of(panel, first=START + 320 * DAY)
    regime = fa.Book(panel, decisions).regime_hmm.reindex(decisions)
    cut = decisions[len(decisions) // 2]
    truncated = fa.build_panel({"BTCUSDT": flat_days(list(prices[:(cut - START).days]))})
    past = decisions[decisions <= cut]
    assert fa.Book(truncated, past).regime_hmm.reindex(past).equals(regime.reindex(past))
    turbulent = decisions[(decisions > START + 340 * DAY) & (decisions < START + 590 * DAY)]
    calm = decisions[decisions > START + 660 * DAY]
    assert regime.reindex(turbulent).mean() < 0.2 and regime.reindex(calm).mean() > 0.8


# --- Simulation ------------------------------------------------------------------------------------------------

def test_simulation_matches_a_hand_computed_portfolio():
    """A : 100 → 110, B : 100 → 90 d'une exécution à la suivante ; 50/50 ; coût 0,1 % par côté."""
    a = [100.0] * 8 + [110.0] * 14
    b = [100.0] * 8 + [90.0] * 14
    panel = fa.build_panel({"AUSDT": flat_days(a), "BUSDT": flat_days(b)})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY])      # deux lundis consécutifs (jours 1 et 8)
    weights = pd.DataFrame(0.5, index=decisions, columns=["AUSDT", "BUSDT"])
    sim = fa.simulate(panel, weights, decisions, cost=0.001)
    # 1re exécution : 1,0 échangé, frais 0,001 ; positions 0,4995 chacune ; une semaine plus tard : 0,54945 et 0,44955.
    assert sim.turnover.iloc[0] == pytest.approx(1.0) and sim.exposure.iloc[0] == pytest.approx(1.0)
    assert sim.weekly.iloc[0] == pytest.approx(0.999 / 1.0 - 1)
    assert sim.turnover.iloc[1] == pytest.approx(0.0999 / 0.999)       # retour à 50/50
    assert sim.weekly.iloc[1] == pytest.approx(-0.0999 * 0.001 / 0.999)   # prix stables ensuite : seuls les frais
    free = fa.simulate(panel, weights, decisions, cost=0.0)
    assert free.weekly.iloc[0] == pytest.approx(0.0) and free.values.iloc[-1] == pytest.approx(1.0)
    half = fa.simulate(panel, weights * 0.5, decisions, cost=0.0)
    assert half.exposure.iloc[0] == pytest.approx(0.5)                 # le reste en USDT


def test_delay_moves_the_execution_and_a_pair_without_price_is_not_traded():
    a = [100.0, 100.0, 120.0] + [120.0] * 20
    panel = fa.build_panel({"AUSDT": flat_days(a)})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY])
    weights = pd.DataFrame(1.0, index=decisions, columns=["AUSDT"])
    assert fa.simulate(panel, weights, decisions, cost=0.0).weekly.iloc[0] == pytest.approx(0.2)        # achat à 100
    assert fa.simulate(panel, weights, decisions, cost=0.0, delay_days=1).weekly.iloc[0] == pytest.approx(0.0)  # à 120
    hole = flat_days(a)
    hole = hole[hole["open_time"] != START + DAY + pd.Timedelta(hours=1)]      # pas de prix à la 1re exécution
    late = fa.simulate(fa.build_panel({"AUSDT": hole}), weights, decisions, cost=0.0)
    assert late.exposure.iloc[0] == 0.0 and late.weekly.iloc[0] == 0.0 and late.exposure.iloc[1] == pytest.approx(1.0)
    assert late.skipped_orders == 1 and fa.simulate(panel, weights, decisions, cost=0.0).skipped_orders == 0


def test_a_position_without_price_stays_and_never_creates_leverage():
    """A détenue à 100 % ; à la 2e décision, A n'a pas de prix et la cible est 100 % B : rien à vendre, donc rien
    à acheter. Sans ce plafond, le portefeuille achèterait B à crédit."""
    a = flat_days([100.0] * 30)
    a = a[a["open_time"] != START + 8 * DAY + pd.Timedelta(hours=1)]
    panel = fa.build_panel({"AUSDT": a, "BUSDT": flat_days([50.0] * 8 + [60.0] * 22)})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY, START + 15 * DAY])
    weights = pd.DataFrame({"AUSDT": [1.0, 0.0, 0.0], "BUSDT": [0.0, 1.0, 1.0]}, index=decisions)
    sim = fa.simulate(panel, weights, decisions, cost=0.001)
    assert sim.exposure.iloc[1] == pytest.approx(1.0) and sim.turnover.iloc[1] == 0.0      # A conservée, B non achetée
    assert sim.skipped_orders == 1 and sim.weekly.iloc[1] == pytest.approx(0.0)
    assert sim.turnover.iloc[2] == pytest.approx(2.0)                # la semaine suivante : A vendue, B achetée
    assert (sim.exposure <= 1.0 + 1e-12).all() and sim.values.min() > 0.99
    half = pd.DataFrame({"AUSDT": [1.0, 0.5, 0.5], "BUSDT": [0.0, 0.5, 0.5]}, index=decisions)
    assert fa.simulate(panel, half, decisions, cost=0.001).exposure.iloc[1] == pytest.approx(1.0)   # pas 150 %
    # A bloquée à 50 %, le reste en USDT, cible 50 % B : les frais de l'achat ne se paient pas à crédit.
    start = pd.DataFrame({"AUSDT": [0.5, 0.5, 0.5], "BUSDT": [0.0, 0.5, 0.5]}, index=decisions)
    tight = fa.simulate(panel, start, decisions, cost=0.01)
    assert tight.exposure.iloc[1] <= 1.0 and tight.exposure.iloc[1] == pytest.approx(1.0)


# --- Mesures et règle --------------------------------------------------------------------------------------------

def test_sharpe_drawdown_and_folds():
    assert fa.sharpe(np.full(10, 0.01)) == 0.0                        # aucune variation : pas de Sharpe
    weekly = np.array([0.02, -0.01, 0.03, 0.0])
    assert fa.sharpe(weekly) == pytest.approx(weekly.mean() / weekly.std(ddof=1) * math.sqrt(52))
    assert fa.max_drawdown(np.array([1.0, 1.2, 0.9, 1.0])) == pytest.approx(-0.25)
    moments = pd.DatetimeIndex(["2018-07-02", "2019-06-24", "2019-07-01", "2025-06-23"], tz="UTC")
    assert fa.fold_of(moments).tolist() == [0, 0, 1, 6]
    assert fa.N_TRIALS == 18 and len({t.key for t in fa.TRIALS}) == 18 and pytest.approx(1 - 0.025 / 18) == fa.LEVEL


def test_sharpe_difference_interval_is_paired_and_deterministic():
    rng = np.random.default_rng(0)
    bench = rng.normal(0.002, 0.04, 364)
    better = bench + 0.004                                            # même risque, rendement plus élevé chaque semaine
    ci = fa.sharpe_diff_ci(better, bench, level=fa.LEVEL, seed=1)
    assert ci is not None and ci[0] > 0
    assert fa.sharpe_diff_ci(bench, bench, level=fa.LEVEL, seed=1) == [0.0, 0.0]
    noise = rng.normal(0.002, 0.04, 364)
    unrelated = fa.sharpe_diff_ci(noise, bench, level=fa.LEVEL, seed=1)
    assert unrelated[0] < 0 < unrelated[1]
    assert unrelated == fa.sharpe_diff_ci(noise, bench, level=fa.LEVEL, seed=1)
    wide, narrow = unrelated, fa.sharpe_diff_ci(noise, bench, level=0.80, seed=1)
    assert wide[0] < narrow[0] and narrow[1] < wide[1]                # le niveau corrigé élargit l'intervalle
    assert fa.sharpe_diff_ci(bench[:20], bench[:20], level=0.95, seed=1) is None      # trop peu de semaines


def as_simulation(weekly: np.ndarray, index: pd.DatetimeIndex) -> fa.Simulation:
    values = pd.Series(np.concatenate([[1.0], np.cumprod(1 + weekly)]))
    return fa.Simulation(pd.Series(weekly, index=index), values, pd.Series([1.0, 0.5]), pd.Series([1.0, 0.5]))


def test_describe_and_compare_measure_what_the_protocol_says():
    index = pd.date_range("2018-07-02", "2025-06-23", freq="7D", tz="UTC")
    assert len(index) == 365
    rng = np.random.default_rng(5)
    bench = rng.normal(0.0, 0.03, len(index))
    folds = fa.fold_of(index)
    assert sorted(set(folds.tolist())) == list(range(7)) and (np.bincount(folds) >= 52).all()
    strategy = bench.copy()
    strategy[folds == 2] += 0.02                                      # nettement meilleure dans UNE validation
    strategy[folds != 2] -= 0.0005                                    # un peu moins bonne ailleurs
    weights = pd.DataFrame({"BTCUSDT": 1.0}, index=index)
    weights.iloc[:100] = 0.0
    weights.iloc[100:120] = 0.19                                      # moins de 20 % investi : semaine « en USDT »
    sim, ref = as_simulation(strategy, index), as_simulation(bench, index)
    described = fa.describe(sim)
    assert described["weeks"] == 365 and described["total_return"] == round(float(np.prod(1 + strategy) - 1), 4)
    assert described["annual_return"] == round(float(np.prod(1 + strategy) ** (52 / 365) - 1), 4)
    assert described["turnover_per_year"] == round(1.5 / (365 / 52), 2) and described["average_exposure"] == 0.75
    assert described["max_drawdown"] == round(fa.max_drawdown(sim.values.to_numpy()), 4) < 0
    measured = fa.compare(sim, ref, ref, sim, ref, weights, seed=1)
    assert measured["folds_better"] == 1 and measured["fold_sharpes"][2][0] > measured["fold_sharpes"][2][1]
    assert measured["sharpe_diff"] == round(fa.sharpe(strategy) - fa.sharpe(bench), 4) > 0
    assert measured["sharpe_diff_without_best_fold"] < 0 < measured["sharpe_diff"]
    assert measured["adverse_sharpe_diff"] == -measured["sharpe_diff"]          # scénario défavorable : rôles inversés
    assert measured["sharpe_diff_vs_btc"] == measured["sharpe_diff"]
    assert measured["invested_share"] == round(245 / 365, 4)
    assert measured["benchmark_max_drawdown"] == round(fa.max_drawdown(ref.values.to_numpy()), 4)
    assert measured["sharpe_diff_ci"] == fa.sharpe_diff_ci(strategy, bench, level=fa.LEVEL, seed=1)
    failed = {k for k, ok in measured["checks"].items() if not ok}
    assert not measured["lead"] and {"stable_par_validation", "positif_scenario_defavorable",
                                     "positif_sans_meilleure_validation"} <= failed


def passing(**overrides) -> dict:
    return {"sharpe_diff_ci": [0.05, 0.9], "folds_better": 5, "adverse_sharpe_diff": 0.2, "max_drawdown": -0.4,
            "benchmark_max_drawdown": -0.6, "sharpe_diff_without_best_fold": 0.1, "invested_share": 0.5} | overrides


def test_every_criterion_alone_rejects_a_lead():
    assert fa.judge(passing())["lead"]
    failing = {
        "ic_ecart_de_sharpe_positif": [{"sharpe_diff_ci": [-0.01, 0.9]}, {"sharpe_diff_ci": None}],
        "stable_par_validation": [{"folds_better": 4}],
        "positif_scenario_defavorable": [{"adverse_sharpe_diff": -0.01}, {"adverse_sharpe_diff": 0.0}],
        "perte_maximale_contenue": [{"max_drawdown": -0.7}],
        "positif_sans_meilleure_validation": [{"sharpe_diff_without_best_fold": -0.05}],
        "assez_investi": [{"invested_share": 0.1}],
    }
    for check, cases in failing.items():
        for case in cases:
            verdict = fa.judge(passing(**case))
            assert not verdict["lead"] and [k for k, ok in verdict["checks"].items() if not ok] == [check], (check, case)


# --- Audit des fuites et bout en bout ----------------------------------------------------------------------------

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT")


@pytest.fixture
def stored(settings, monkeypatch):
    """Magasin long synthétique : 6 paires, 2 ans de bougies 1 h ; seuils ramenés à la période synthétique."""
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 730, "1h", symbol=symbol, start="2023-01-01", seed=i, drift=0.00002 * (i - 2)),
                   symbol, "1h")
    settings.protocol.development_end = datetime(2024, 9, 29, 23, 59, 59, tzinfo=UTC)   # 3 mois « réservés » après
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 0.0)
    monkeypatch.setattr(fa, "FIRST_DECISION", pd.Timestamp("2024-01-01", tz="UTC"))
    monkeypatch.setattr(fa, "FOLD_STARTS", tuple(pd.Timestamp(d, tz="UTC") for d in (
        "2024-01-01", "2024-02-05", "2024-03-11", "2024-04-15", "2024-05-20", "2024-06-24", "2024-08-05")))
    monkeypatch.setattr(fa, "BOOTSTRAP_SAMPLES", 300)
    monkeypatch.setattr(fa, "AUDIT_DECISIONS", 5)
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    return settings


def test_leak_audit_passes_on_causal_weights_and_catches_a_leak(stored):
    frames = fa.load_frames(stored, list(PAIRS), pd.Timestamp(stored.protocol.development_end))
    panel = fa.build_panel(frames)
    decisions = fa.decisions_of(panel)
    assert decisions[0] == fa.FIRST_DECISION and decisions[-1] == pd.Timestamp("2024-09-16", tz="UTC")
    audit = fa.leak_audit(frames, panel, decisions, seed=3)
    assert audit["passed"] and audit["violations"] == [] and audit["mutation_detected"]
    assert audit["trials_checked"] == 20                              # 18 essais et 2 références
    moment = decisions[10]
    leaky_full = fa.Book(panel, decisions, leaky=True).weights("MOM_L28_K5").loc[moment]
    cut = fa.build_panel({s: f[f["open_time"] < moment] for s, f in frames.items()})
    leaky_cut = fa.Book(cut, decisions[decisions <= moment], leaky=True).weights("MOM_L28_K5").loc[moment]
    assert not np.allclose(leaky_full.to_numpy(), leaky_cut.reindex(leaky_full.index).fillna(0.0).to_numpy())


def test_an_audit_that_cannot_catch_the_mutation_fails_and_nothing_is_recorded(stored, monkeypatch):
    """Aucune paire éligible : tous les poids sont nuls, la mutation ne peut pas être prise en défaut. L'audit doit
    alors échouer (un contrôle qui ne détecte rien ne prouve rien) et aucun essai n'est enregistré."""
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 1e18)
    frames = fa.load_frames(stored, list(PAIRS), pd.Timestamp(stored.protocol.development_end))
    panel = fa.build_panel(frames)
    audit = fa.leak_audit(frames, panel, fa.decisions_of(panel), seed=3)
    assert audit["violations"] == [] and not audit["mutation_detected"] and not audit["passed"]
    with pytest.raises(fa.LeakAuditFailed):
        fa.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
    assert not list(stored.reports_dir.glob("FACT-*"))


def test_run_measures_the_declared_trials_on_development_only(stored):
    result = fa.run(stored, now=NOW, symbols=list(PAIRS))
    assert len(result.rows) == 18 and {r["key"] for r in result.rows} == {t.key for t in fa.TRIALS}
    assert result.leak_audit["passed"] and set(result.data_hashes) == set(PAIRS)
    leads = [r["key"] for r in result.rows if r["lead"]]
    assert result.verdict == (f"{len(leads)} PISTE(S) À CONFIRMER" if leads else "AUCUNE_PISTE")
    assert all({"skipped_orders", "adverse_skipped_orders"} <= set(r) for r in result.rows)
    assert set(result.benchmarks) == {fa.EW, fa.BTC} and result.coverage["decisions"] == 38
    assert result.coverage["first"].startswith("2024-01-01") and result.coverage["last"].startswith("2024-09-16")
    assert result.coverage["cost_per_side"] == pytest.approx(0.0013)
    assert result.coverage["adverse_cost_per_side"] == pytest.approx(0.0018)
    for row in result.rows:
        assert set(row["checks"]) == {"ic_ecart_de_sharpe_positif", "stable_par_validation",
                                      "positif_scenario_defavorable", "perte_maximale_contenue",
                                      "positif_sans_meilleure_validation", "assez_investi"}
        assert row["lead"] == all(row["checks"].values())
    assert (stored.reports_dir / result.run_id / "summary.json").exists()
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "FACTORS" and run["metrics"]["n_trials"] == 18 and run["metrics"]["verdict"] == result.verdict
    assert run["period_label"] == "DEVELOPMENT" and run["period_end"].startswith("2024-09-29")
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 18 == result.program_trials
    assert run["params"]["cost_per_side"] == pytest.approx(0.0013) and run["params"]["bootstrap_samples"] == 300
    # Le tableau de bord liste ce protocole avec le verdict enregistré, sans le reformuler.
    from crypto_signal_intelligence.api.server import CsiApi
    listed = {m["kind"]: m for m in CsiApi(stored).dispatch("GET", "/models", {}, None)["models"]}
    assert listed["FACTORS"]["verdict"] == result.verdict and "hebdomadaires" in listed["FACTORS"]["label"]
    # Rien n'est lu après la fin de DEVELOPMENT : un futur falsifié ne change aucun résultat.
    store = CandleStore(long_settings(stored).data_dir)
    end = pd.Timestamp(stored.protocol.development_end)
    for symbol in PAIRS:
        frame = store.load(symbol, "1h")
        later = frame["open_time"] > end
        assert later.sum() > 2000                                     # le « futur » existe bien dans le magasin
        for column in ("open", "high", "low", "close", "quote_volume"):
            frame.loc[later, column] = frame.loc[later, column] * 3.0
        store.save(frame, symbol, "1h")
    again = fa.run(stored, now=NOW, symbols=list(PAIRS))
    assert again.rows == result.rows and again.data_hashes == result.data_hashes


def test_incomplete_data_is_refused_before_any_trial(stored):
    CandleStore(long_settings(stored).data_dir).path("SOLUSDT", "1h").unlink()
    with pytest.raises(fa.IncompleteData, match="SOLUSDT"):
        fa.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0


def test_command_prints_the_verdict_and_refuses_incomplete_data(stored, monkeypatch):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    monkeypatch.setattr(cli, "_heavy_job", lambda settings: None)
    refused = CliRunner().invoke(cli.app, ["factors"])                # univers réel : absent du magasin synthétique
    assert refused.exit_code == 3 and "Aucun résultat" in refused.output
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
    monkeypatch.setattr(fa, "RESEARCH_UNIVERSE", PAIRS)
    monkeypatch.setenv("CSI_PROTOCOL__DEVELOPMENT_END", "2024-09-29T23:59:59Z")
    done = CliRunner().invoke(cli.app, ["factors"])
    assert done.exit_code == 0, done.output
    assert "Verdict :" in done.output and "Rapport :" in done.output and "pas un avantage démontré" in done.output
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 18


def test_uncommitted_code_is_refused_before_anything_is_read(stored, monkeypatch):
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(fa.DirtyCode, match="DIRTY"):
        fa.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
    monkeypatch.setattr(fa, "code_state", lambda: "NO_GIT_COMMIT")
    with pytest.raises(fa.DirtyCode):
        fa.run(stored, now=NOW, symbols=list(PAIRS))
    result = fa.run(stored, now=NOW, symbols=list(PAIRS), allow_dirty=True)       # essai local, enregistré comme tel
    assert ExperimentRegistry(stored.experiments_db).get(result.run_id)["git_commit"] == "NO_GIT_COMMIT"


def test_the_audit_reports_a_real_leak_in_a_signal_and_in_the_simulation(stored, monkeypatch):
    frames = fa.load_frames(stored, list(PAIRS), pd.Timestamp(stored.protocol.development_end))
    panel = fa.build_panel(frames)
    decisions = fa.decisions_of(panel)
    honest_momentum = fa.Book.momentum
    monkeypatch.setattr(fa.Book, "momentum", lambda self, days: honest_momentum(self, days).shift(-1))
    audit = fa.leak_audit(frames, panel, decisions, seed=3)
    leaking = {v["trial"] for v in audit["violations"]}
    assert not audit["passed"] and {"MOM_L28_K5", "DUAL_MOM28", "TSMOM_L56"} <= leaking and fa.EW not in leaking
    monkeypatch.setattr(fa.Book, "momentum", honest_momentum)
    honest_simulate = fa.simulate

    def peeking(panel, weights, decisions, **kwargs):     # valorise avec le prix du LENDEMAIN
        return honest_simulate(fa.Panel(panel.close, panel.volume, panel.price.shift(-1)), weights, decisions, **kwargs)

    monkeypatch.setattr(fa, "simulate", peeking)
    audit = fa.leak_audit(frames, panel, decisions, seed=3)
    assert not audit["passed"] and [v["check"] for v in audit["violations"]] == ["simulation"]


def test_a_binance_outage_does_not_switch_the_btc_trend_off_for_200_days():
    prices = trend(100, 0.002, 400)
    frame = flat_days(prices)
    outage = [START + d * DAY for d in (200, 201, 202)]               # trois journées sans bougie
    frame = frame[~frame["open_time"].dt.floor("D").isin(outage)]
    panel = fa.build_panel({"BTCUSDT": frame})
    decisions = fa.decisions_of(panel, first=START + 280 * DAY)
    assert (fa.Book(panel, decisions).weights("REGIME_SMA200").sum(axis=1) > 0).all()


def test_positive_control_a_planted_momentum_becomes_a_lead(monkeypatch):
    """Contrôle positif : des paires dont la tendance persiste des mois (momentum planté) doivent donner au moins une
    piste ; sinon la chaîne poids → simulation → mesures → règle ne pourrait jamais rien trouver."""
    rng = np.random.default_rng(11)
    days, pairs = 1500, 12
    index = pd.date_range("2020-01-01", periods=days, freq="D", tz="UTC")
    drift = np.zeros((days, pairs))
    level = rng.normal(0, 0.006, pairs)
    for d in range(days):
        if d % 90 == 0:
            level = rng.normal(0, 0.006, pairs)                       # nouvelles tendances tous les 90 jours
        drift[d] = level
    returns = drift + rng.normal(0, 0.02, (days, pairs)) + rng.normal(0, 0.01, (days, 1))
    close = pd.DataFrame(100 * np.exp(np.cumsum(returns, axis=0)),
                         index=index, columns=["BTCUSDT", *(f"P{k}USDT" for k in range(1, pairs))])
    panel = fa.Panel(close, close * 0 + 5e6, close)                   # exécution au dernier prix connu (contrôle)
    first = pd.Timestamp("2020-06-01", tz="UTC")
    monkeypatch.setattr(fa, "FOLD_STARTS", tuple(first + pd.Timedelta(days=26 * 7 * k) for k in range(7)))
    monkeypatch.setattr(fa, "BOOTSTRAP_SAMPLES", 4000)
    decisions = fa.decisions_of(panel, first=first)
    book = fa.Book(panel, decisions)
    weights = {key: book.weights(key) for key in ("MOM_L56_K3", fa.EW, fa.BTC)}
    sims = {key: fa.simulate(panel, w, decisions, cost=0.0013) for key, w in weights.items()}
    adverse = {key: fa.simulate(panel, w, decisions, cost=0.0018, delay_days=1) for key, w in weights.items()}
    measured = fa.compare(sims["MOM_L56_K3"], sims[fa.EW], adverse["MOM_L56_K3"], adverse[fa.EW], sims[fa.BTC],
                          weights["MOM_L56_K3"], seed=1)
    assert measured["lead"], measured["checks"]
    noise = fa.Panel(close.iloc[:, :1].join(pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (days, pairs - 1)), axis=0)),
                                                         index=index, columns=close.columns[1:])), panel.volume, None)  # type: ignore[arg-type]
    noise.price = noise.close
    book = fa.Book(noise, decisions)
    w, ew, btc = book.weights("MOM_L56_K3"), book.weights(fa.EW), book.weights(fa.BTC)
    blank = fa.compare(fa.simulate(noise, w, decisions, cost=0.0013), fa.simulate(noise, ew, decisions, cost=0.0013),
                       fa.simulate(noise, w, decisions, cost=0.0018, delay_days=1),
                       fa.simulate(noise, ew, decisions, cost=0.0018, delay_days=1),
                       fa.simulate(noise, btc, decisions, cost=0.0013), w, seed=1)
    assert not blank["lead"]                                          # sans momentum planté : pas de piste
