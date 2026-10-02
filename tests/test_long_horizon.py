"""Lot 8 — horizons longs (research/long_horizon.py, docs/LONG_HORIZON.md) : coûts, simulation avec bande et stop
calculée à la main, Sharpe déflaté, durée de perte, règle, poids, causalité, bout en bout. Données SYNTHÉTIQUES."""
from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research import long_horizon as lh
from crypto_signal_intelligence.research import volatility as vol
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

START = pd.Timestamp("2024-01-01", tz="UTC")
DAY = pd.Timedelta(days=1)
NOW = datetime(2026, 10, 2, tzinfo=UTC)


def daily_panel(prices: dict[str, list[float]]) -> fa.Panel:
    """Panneau direct : clôture du jour d (connue à d + 1) = prix de 01:00 du jour d + 1."""
    index = pd.date_range(START, periods=len(next(iter(prices.values()))), freq="D", tz="UTC")
    close = pd.DataFrame(prices, index=index)
    return fa.Panel(close, close * 0 + 5e6, close)


def test_costs_per_side_follow_liquidity():
    costs = lh.cost_per_side(["BTCUSDT", "ETHUSDT", "SOLUSDT"], adverse=False)
    assert costs.to_dict() == pytest.approx({"BTCUSDT": 9.5e-4, "ETHUSDT": 9.5e-4, "SOLUSDT": 12.5e-4})
    adverse = lh.cost_per_side(["BTCUSDT", "SOLUSDT"], adverse=True)
    assert adverse.to_dict() == pytest.approx({"BTCUSDT": 1e-3 + 4e-4, "SOLUSDT": 1e-3 + 1e-3})


def test_band_fees_and_full_rebalance_by_hand():
    panel = daily_panel({"AUSDT": [100.0] * 30, "BUSDT": [100.0] * 30})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY, START + 15 * DAY])
    costs = pd.Series({"AUSDT": 0.001, "BUSDT": 0.001})
    targets = {decisions[0]: pd.Series({"AUSDT": 0.5}), decisions[1]: pd.Series({"AUSDT": 0.53}),
               decisions[2]: pd.Series({"AUSDT": 0.6, "BUSDT": 0.2})}
    run = lh.simulate(panel, targets, decisions, costs=costs, band=0.05)
    # 1re décision : achat de 0,5 (frais 0,0005) ; 2e : écart de 3 points < 5 : rien ; 3e : A +10 points, B entre.
    assert run.trades == 3 and run.fees == pytest.approx(0.0005 + 0.0005 * 0.0 + (0.1 * 0.9995 + 0.2 * 0.9995) * 0.001, rel=1e-3)
    assert run.weekly.iloc[1] == pytest.approx(0.0, abs=1e-12)                         # aucun échange, prix stables
    full = lh.simulate(panel, targets, decisions, costs=costs, full_rebalance=True)
    assert full.trades == 4                                                         # la 2e décision échange aussi A
    free = lh.simulate(panel, targets, decisions, costs=costs * 0, band=0.05)
    assert free.values.iloc[-1] == pytest.approx(1.0) and (free.exposure <= 1 + 1e-12).all()


def test_entering_or_leaving_always_trades_and_no_leverage():
    panel = daily_panel({"AUSDT": [100.0] * 20})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY])
    targets = {decisions[0]: pd.Series({"AUSDT": 0.03}), decisions[1]: pd.Series({"AUSDT": 0.0})}
    run = lh.simulate(panel, targets, decisions, costs=pd.Series({"AUSDT": 0.0}), band=0.05)
    assert run.trades == 2 and run.exposure.iloc[0] == pytest.approx(0.03) and run.exposure.iloc[1] == 0.0
    over = lh.simulate(panel, {decisions[0]: pd.Series({"AUSDT": 1.4})}, decisions, costs=pd.Series({"AUSDT": 0.001}),
                       full_rebalance=True)
    assert over.exposure.iloc[0] <= 1.0 + 1e-12


def test_stop_sells_after_a_close_below_the_line_and_waits_for_the_next_selection():
    prices = [100.0] * 5 + [80.0, 74.0, 60.0, 70.0] + [70.0] * 30
    panel = daily_panel({"AUSDT": prices})
    decisions = pd.DatetimeIndex([START + DAY, START + 8 * DAY, START + 15 * DAY])
    targets = {decisions[0]: pd.Series({"AUSDT": 1.0})}
    run = lh.simulate(panel, targets, decisions, costs=pd.Series({"AUSDT": 0.0}), full_rebalance=True, stop=0.25)
    # Acheté à 100 ; la clôture 74 (< 75) est connue le jour suivant : vente au prix de 01:00 de ce jour, soit 74.
    assert run.values.iloc[-1] == pytest.approx(0.74) and run.trades == 2
    no_stop = lh.simulate(panel, targets, decisions, costs=pd.Series({"AUSDT": 0.0}), full_rebalance=True)
    assert no_stop.values.iloc[-1] == pytest.approx(0.70)
    # Le stop lit la CLÔTURE connue, pas le prix d'exécution : une mèche à 01:00 sous la ligne ne déclenche rien.
    index = panel.close.index
    close = pd.DataFrame({"AUSDT": [100.0] * len(index)}, index=index)
    price = close.copy()
    price.iloc[6] = 70.0                                                             # prix de 01:00 isolé sous 75
    wick = lh.simulate(fa.Panel(close, close * 0 + 5e6, price), targets, decisions, costs=pd.Series({"AUSDT": 0.0}),
                       full_rebalance=True, stop=0.25)
    assert wick.trades == 1 and wick.values.iloc[-1] == pytest.approx(1.0)


def test_deflated_sharpe_and_drawdown_duration():
    rng = np.random.default_rng(1)
    strong = rng.normal(0.02, 0.03, 300)
    noise = rng.normal(0.0, 0.03, 300)
    assert lh.deflated_sharpe(strong, 716) > 0.99 and lh.deflated_sharpe(noise, 716) < 0.5
    assert lh.deflated_sharpe(strong, 10) >= lh.deflated_sharpe(strong, 10_000)          # plus d'essais : plus sévère
    assert lh.deflated_sharpe(strong[:20], 716) is None
    t, sr = 300, strong.mean() / strong.std(ddof=1)
    gamma = 0.5772156649015329
    from scipy.stats import norm
    sr0 = math.sqrt(1 / t) * ((1 - gamma) * norm.ppf(1 - 1 / 716) + gamma * norm.ppf(1 - 1 / (716 * math.e)))
    assert sr0 == pytest.approx(0.1825, abs=2e-3)                                  # ≈ 3,2 écarts-types / √300
    values = pd.Series([1.0, 1.2, 1.0, 0.9, 1.1, 1.25, 1.1, 1.2],
                       index=pd.date_range(START, periods=8, freq="D", tz="UTC"))
    assert lh.drawdown_days(values) == 4                                           # sommet du jour 1, dépassé au jour 5
    still = pd.Series([1.0, 1.5, 1.2, 1.1], index=pd.date_range(START, periods=4, freq="D", tz="UTC"))
    assert lh.drawdown_days(still) == 2
    del sr


def stats(annual: float, dd: float, folds_dd: list[float], dsr: float = 0.5) -> dict:
    return {"annual_return": annual, "max_drawdown": dd, "deflated_sharpe": dsr,
            "folds": [{"sharpe": 0.0, "max_drawdown": d} for d in folds_dd]}


def test_each_decision_criterion():
    ref = stats(0.40, -0.80, [-0.5] * 7)
    good_dd = stats(0.33, -0.45, [-0.3] * 5 + [-0.6] * 2)
    judged = lh.judge(good_dd, ref, ci=[-0.1, 0.5])
    assert judged["perte_reduite"] and not judged["risque_ajuste"] and judged["validations_perte_plus_faible"] == 5
    assert not lh.judge(stats(0.31, -0.45, [-0.3] * 7), ref, ci=None)["perte_reduite"]          # < 80 % du rendement
    assert not lh.judge(stats(0.35, -0.49, [-0.3] * 7), ref, ci=None)["perte_reduite"]          # perte > 60 %
    assert not lh.judge(stats(0.35, -0.40, [-0.3] * 4 + [-0.6] * 3), ref, ci=None)["perte_reduite"]   # 4 validations
    assert lh.judge(stats(0.1, -0.9, [-0.9] * 7, dsr=0.96), ref, ci=[0.01, 0.5])["risque_ajuste"]
    assert not lh.judge(stats(0.1, -0.9, [-0.9] * 7, dsr=0.94), ref, ci=[0.01, 0.5])["risque_ajuste"]
    assert not lh.judge(stats(0.1, -0.9, [-0.9] * 7, dsr=0.99), ref, ci=[-0.01, 0.5])["risque_ajuste"]
    negative_ref = stats(-0.10, -0.80, [-0.5] * 7)
    assert lh.judge(stats(-0.10, -0.40, [-0.3] * 7), negative_ref, ci=None)["perte_reduite"]
    ok, ko = {"risque_ajuste": True, "perte_reduite": True}, {"risque_ajuste": False, "perte_reduite": False}
    assert lh.verdict(ok, ok) == "INTERESSANT_RISQUE_AJUSTE" and lh.verdict(ok, ko) == "NON_INTERESSANT"
    assert lh.verdict({"risque_ajuste": False, "perte_reduite": True}, {"risque_ajuste": True, "perte_reduite": True}) \
        == "INTERESSANT_PERTE_REDUITE"


def test_weights_of_a_follow_trend_forecast_volatility_and_funding():
    days = 200
    up, down = [100 * 1.003 ** d for d in range(days)], [100 * 0.997 ** d for d in range(days)]
    panel = daily_panel({"BTCUSDT": up, "ETHUSDT": up, "SOLUSDT": down, "XRPUSDT": up, "ADAUSDT": up, "DOTUSDT": up,
                         "LTCUSDT": up})
    for symbol, factor in (("XRPUSDT", 3.0), ("DOTUSDT", 2.5), ("SOLUSDT", 2.0), ("ADAUSDT", 1.5)):
        panel.volume[symbol] *= factor                                               # ordre de liquidité connu
    decisions = fa.decisions_of(panel, first=START + 120 * DAY)
    sigma = pd.DataFrame(0.5, index=decisions, columns=panel.symbols)
    sigma["ETHUSDT"] = 1.0
    sigma["DOTUSDT"] = np.nan                                                        # pas de prévision : exclue
    sigma["ADAUSDT"] = 0.25                                                          # calme : plafonnée à 1/5
    funding = pd.DataFrame(0.0001, index=decisions, columns=panel.symbols)
    funding["XRPUSDT"] = 0.001
    inputs = lh.Inputs(panel, decisions, sigma, funding)
    book = fa.Book(panel, decisions)
    first = decisions[0]
    assert lh.baskets(inputs, book)[first] == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT"]   # pas DOT, pas LTC
    votes = lh.trend_votes(book).loc[first]
    assert votes["BTCUSDT"] == 2 and votes["SOLUSDT"] == 0            # 182 jours d'historique absents : 2 votes sur 3
    plain = lh.weights_a(inputs, book, with_funding=False).loc[first]
    # Cible FIGÉE 0,5 : BTC 1/5, ETH 0,5/1,0 × 1/5, SOL baisse (0 vote) : 0.
    assert plain["BTCUSDT"] == pytest.approx(0.2) and plain["ETHUSDT"] == pytest.approx(0.1)
    assert plain["SOLUSDT"] == 0.0 and plain["DOTUSDT"] == 0.0 and plain["XRPUSDT"] == pytest.approx(0.2)
    assert plain["ADAUSDT"] == pytest.approx(0.2) and plain["LTCUSDT"] == 0.0           # min(1, 0,5/0,25) = 1
    filtered = lh.weights_a(inputs, book, with_funding=True).loc[first]
    assert filtered["XRPUSDT"] == pytest.approx(0.1) and filtered["BTCUSDT"] == pytest.approx(0.2)
    assert plain.sum() <= 1.0 + 1e-12
    bench = lh.weights_bench_a(inputs, book).loc[first]
    assert bench[bench > 0].to_dict() == pytest.approx({s: 0.2 for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")})
    assert lh.funding_activation(inputs, book) == pytest.approx(1 / 5)              # XRP seul au-dessus de la limite
    static = lh.static_targets(lh.weights_bench_a(inputs, book), 0.6)
    assert list(static) == [first] and static[first].sum() == pytest.approx(0.6) and len(static[first]) == 5


def test_vote_needs_two_positive_horizons_of_three():
    # Hausse sur 4 et 12 semaines mais baisse sur 26 semaines : 2 votes ; baisse récente seule : 1 vote.
    rising_late = [100 * 0.995 ** d for d in range(300)] + [100 * 0.995 ** 300 * 1.004 ** d for d in range(100)]
    falling_late = [100 * 1.002 ** d for d in range(350)] + [100 * 1.002 ** 350 * 0.997 ** d for d in range(50)]
    panel = daily_panel({"BTCUSDT": rising_late, "ETHUSDT": falling_late, "SOLUSDT": rising_late, "XRPUSDT": rising_late,
                         "ADAUSDT": rising_late})
    decisions = fa.decisions_of(panel, first=START + 390 * DAY)
    votes = lh.trend_votes(fa.Book(panel, decisions)).loc[decisions[0]]
    assert votes["BTCUSDT"] == 2 and votes["ETHUSDT"] == 1
    sigma = pd.DataFrame(0.5, index=decisions, columns=panel.symbols)
    weights = lh.weights_a(lh.Inputs(panel, decisions, sigma, pd.DataFrame(index=decisions)), fa.Book(panel, decisions), with_funding=False)
    assert weights.loc[decisions[0], "BTCUSDT"] == pytest.approx(0.2) and weights.loc[decisions[0], "ETHUSDT"] == 0.0


def test_funding_mean_accepts_timestamp_objects_and_seven_day_window():
    decisions = pd.DatetimeIndex([pd.Timestamp("2024-05-13", tz="UTC")])
    stamps = [pd.Timestamp("2024-05-13", tz="UTC") - k * pd.Timedelta(hours=8) for k in range(1, 30)]
    frame = pd.DataFrame({"time": stamps, "available_at": pd.Series(stamps, dtype=object), "rate": 0.0002,
                          "interval_hours": 8})
    out = lh.funding_mean({"BTCUSDT": frame}, decisions)
    assert out.loc[decisions[0], "BTCUSDT"] == pytest.approx(0.0002)           # 21 règlements dans les 7 jours
    frame.loc[0, "available_at"] = pd.Timestamp("2024-05-13 00:00:01", tz="UTC")   # connu après la décision : exclu
    frame["rate"] = [1.0] + [0.0002] * 28
    assert lh.funding_mean({"BTCUSDT": frame}, decisions).loc[decisions[0], "BTCUSDT"] == pytest.approx(0.0002)


def test_low_volatility_selection():
    rng = np.random.default_rng(3)
    days = 400
    prices = {f"P{k}USDT": list(100 * np.exp(np.cumsum(rng.normal(0, 0.005 * (k + 1), days)))) for k in range(8)}
    panel = daily_panel(prices)
    decisions = fa.decisions_of(panel, first=START + 200 * DAY)
    inputs = lh.Inputs(panel, decisions, pd.DataFrame(index=decisions), pd.DataFrame(index=decisions))
    selections = lh.selections_b(inputs, fa.Book(panel, decisions))
    first = next(iter(selections))
    assert selections[first] == [f"P{k}USDT" for k in range(5)] and len(selections) == math.ceil(len(decisions) / 26)


# --- Bout en bout -------------------------------------------------------------------------------------------------

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "DOTUSDT")


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 760, "1h", symbol=symbol, start="2023-01-01", seed=i, drift=0.00002 * (i - 3)), symbol, "1h")
    settings.protocol.development_end = datetime(2024, 12, 30, 23, 59, 59, tzinfo=UTC)
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 0.0)
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(vol, "MIN_HISTORY_DAYS", 120)
    monkeypatch.setattr(vol, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(lh, "FIRST_DECISION", pd.Timestamp("2024-05-06", tz="UTC"))
    monkeypatch.setattr(lh, "FOLD_STARTS", tuple(pd.Timestamp(d, tz="UTC") for d in (
        "2024-05-06", "2024-06-10", "2024-07-15", "2024-08-19", "2024-09-23", "2024-10-28", "2024-12-02")))
    monkeypatch.setattr(lh, "B_EVERY", 8)
    monkeypatch.setattr(fa, "BOOTSTRAP_SAMPLES", 300)
    return settings


def test_forecasts_are_causal(stored):
    frames = fa.load_frames(stored, list(PAIRS), pd.Timestamp(stored.protocol.development_end))
    panel = fa.build_panel(frames)
    decisions = fa.decisions_of(panel, first=lh.FIRST_DECISION)
    sigma = lh.vol_forecasts(frames, decisions, seed=1)
    assert sigma.notna().to_numpy().mean() > 0.9
    moment = decisions[len(decisions) // 2]
    cut = {s: f[f["open_time"] < moment] for s, f in frames.items()}
    again = lh.vol_forecasts(cut, decisions[decisions <= moment], seed=1)
    np.testing.assert_allclose(again.loc[moment].to_numpy(), sigma.loc[moment].to_numpy(), rtol=1e-9)


def test_run_measures_the_three_trials_and_records_them(stored):
    result = lh.run(stored, now=NOW, symbols=list(PAIRS))
    assert result.leak_audit["passed"] and result.leak_audit["mutation_detected"]
    # Sans historique de financement, le filtre ne s'active jamais : l'essai A + funding n'est pas consommé.
    assert set(result.verdicts) == {"A", "B"} and result.n_trials == 2 and result.coverage["funding_activation"] == 0.0
    assert result.coverage["b_rebalances"] == len(result.coverage["b_selections"])
    for verdict in result.verdicts.values():
        assert verdict["verdict"] in {"INTERESSANT_RISQUE_AJUSTE", "INTERESSANT_PERTE_REDUITE", "NON_INTERESSANT"}
        assert set(verdict) >= {"central", "adverse", "reference", "buy_and_hold"} and verdict["reference"].startswith("STATIC_")
        assert "vs_buy_and_hold" in verdict["central"]
    for scenario in ("central", "adverse"):
        assert set(result.models[scenario]) == {"A", "REF_A", "STATIC_A", "B", "REF_B", "STATIC_B", "B_SANS_STOP", "BTC"}
        assert result.models[scenario]["STATIC_A"]["average_exposure"] <= 1.0 + 1e-9
        a = result.models[scenario]["A"]
        assert {"annual_return", "volatility", "sharpe", "deflated_sharpe", "max_drawdown", "drawdown_days", "trades",
                "fees_pct", "average_exposure", "folds"} <= set(a)
    assert result.models["adverse"]["A"]["fees_pct"] >= result.models["central"]["A"]["fees_pct"] or \
        result.models["adverse"]["A"]["trades"] != result.models["central"]["A"]["trades"]
    assert "non implémenté" in result.coverage["unlock_filter"]
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "LONG_HORIZON" and run["metrics"]["n_trials"] == 2 and run["git_commit"] == "0123abcd"
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 2


def test_uncommitted_code_or_a_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(fa.DirtyCode):
        lh.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(lh, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(lh.LeakAuditFailed):
        lh.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0


def test_an_audit_that_cannot_catch_its_mutation_fails(stored, monkeypatch):
    frames = fa.load_frames(stored, list(PAIRS), pd.Timestamp(stored.protocol.development_end))
    inputs = lh.prepare(stored, frames, {})
    monkeypatch.setattr(lh, "all_weights", lambda inputs: {"A": pd.DataFrame(0.0, index=inputs.decisions, columns=inputs.panel.symbols)})
    audit = lh.leak_audit(stored, frames, {}, inputs, seed=1)
    assert audit["violations"] == [] and not audit["mutation_detected"] and not audit["passed"]
