"""Étape 4 du plan (research/trend_daily.py) : canaux de Donchian à la main, ensemble, ciblage de volatilité, simulation
journalière (bande, frais, pas de levier), audit des fuites, bout en bout sur un magasin synthétique. SYNTHÉTIQUE :
rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research import trend_daily as td
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

START = pd.Timestamp("2024-01-01", tz="UTC")
DAY = pd.Timedelta(days=1)
NOW = datetime(2025, 1, 15, tzinfo=UTC)


def frame(values, columns=("A",)):
    index = pd.date_range(START, periods=len(values), freq="D", tz="UTC")
    return pd.DataFrame({c: values for c in columns}, index=index, dtype=float)


def test_channel_entry_and_asymmetric_exit():
    # 10 jours plats à 100, cassure à 101 (entrée sur le canal 4), repli sous le plus bas des 2 derniers jours.
    close = frame([100] * 10 + [101, 102, 103, 101.5, 100.9, 100.5, 104])
    pos = td.channel_positions(close, 4)["A"].to_numpy()
    assert pos[10] == 1 and pos[12] == 1                                     # entrée le jour de la cassure
    assert pos[14] == 0                                                      # 100,9 < min(102, 103, 101,5) → sortie
    assert pos[16] == 1                                                      # nouvelle cassure
    assert pos[:10].sum() == 0
    with_gap = close.copy()
    with_gap.loc[with_gap.index[12], "A"] = np.nan
    assert td.channel_positions(with_gap, 4)["A"].to_numpy()[12] == 0        # journée sans clôture : fermée


def test_ensemble_and_realized_volatility():
    close = frame([100 * 1.01 ** d for d in range(130)])
    ens = td.ensemble(close)["A"]
    assert ens.iloc[-1] == pytest.approx(1.0) and set(np.round(ens.unique(), 4)) <= {0.0, round(1 / 3, 4), round(2 / 3, 4), 1.0}
    sigma = td.realized_sigma(close)["A"]
    assert sigma.iloc[-1] == pytest.approx(0.0, abs=1e-9)                    # rendement constant : volatilité nulle


def panel_from(prices: dict[str, list[float]]) -> fa.Panel:
    index = pd.date_range(START, periods=len(next(iter(prices.values()))), freq="D", tz="UTC")
    close = pd.DataFrame(prices, index=index, dtype=float)
    volume = pd.DataFrame(5e6, index=index, columns=list(prices))
    return fa.Panel(close, volume, close.shift(-1).ffill())


def test_weights_follow_ensemble_basket_and_vol_target(monkeypatch):
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 0.0)
    days = 200
    up = [100 * 1.004 ** d for d in range(days)]
    noisy = list(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0.004, 0.08, days))))
    panel = panel_from({"BTCUSDT": up, "ETHUSDT": up, "SOLUSDT": [100] * days, "XRPUSDT": noisy})
    decisions = td.decisions_of(panel, first=START + 150 * DAY)
    book = fa.Book(panel, decisions)
    plain = td.weights(panel, book, decisions, vol_target=False)
    assert plain.loc[decisions[-1], "BTCUSDT"] == pytest.approx(0.1) and plain.loc[decisions[-1], "SOLUSDT"] == 0.0
    assert plain.sum(axis=1).max() <= 1.0 + 1e-12
    targeted = td.weights(panel, book, decisions, vol_target=True)
    assert targeted.loc[decisions[-1], "BTCUSDT"] == pytest.approx(0.1)        # volatilité quasi nulle : plafonnée
    xrp_plain, xrp_target = plain["XRPUSDT"], targeted["XRPUSDT"]
    assert (xrp_target <= xrp_plain + 1e-12).all() and (xrp_target < xrp_plain).any()   # volatilité > 50 % réduit le poids


def test_simulation_band_costs_and_no_leverage():
    panel = panel_from({"A": [100.0] * 5, "B": [10.0] * 5})
    decisions = panel.close.index[:4]
    targets = pd.DataFrame({"A": [0.5, 0.52, 0.0, 0.0], "B": [0.5, 0.5, 0.9, 0.9]}, index=decisions)
    run = td.simulate(panel, targets, costs=pd.Series({"A": 0.001, "B": 0.001}))
    # Jour 1 : deux achats de 50 % (ramenés à 99,9 % pour payer les frais) ; jour 2 : 52 % dans la bande, rien ;
    # jour 3 : sortie de A (toujours échangée) et achat de B ; jour 4 : dans la bande.
    assert run.trades == 4 and run.values.iloc[0] == 1.0 and run.values.iloc[1] == pytest.approx(0.999, abs=1e-5)
    assert run.values.min() > 0 and run.exposure.max() <= 1.0 + 1e-9
    assert 0.0018 < run.fees < 0.0021


def test_static_targets_split_exposure_over_members():
    table = pd.DataFrame({"A": [0.1, 0.0], "B": [0.1, 0.1], "C": [0.0, 0.1]}, index=[0, 1])
    static = td.static_targets(table, 0.3)
    assert static.loc[0].to_dict() == pytest.approx({"A": 0.15, "B": 0.15, "C": 0.0})
    assert static.sum(axis=1).to_list() == pytest.approx([0.3, 0.3])


PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "DOTUSDT")


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 500, "1h", symbol=symbol, start="2023-09-01", seed=i, drift=0.00003 * (i - 2)), symbol, "1h")
    settings.protocol.development_end = datetime(2024, 12, 30, 23, 59, 59, tzinfo=UTC)
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 0.0)
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(td, "FIRST_DECISION", pd.Timestamp("2024-04-01", tz="UTC"))
    monkeypatch.setattr(td, "SAMPLES", 300)
    monkeypatch.setattr(td, "BLOCK_DAYS", 14)
    return settings


def test_run_measures_two_trials_and_records_them(stored):
    result = td.run(stored, now=NOW, symbols=list(PAIRS))
    assert result.leak_audit["passed"] and result.leak_audit["mutation_detected"]
    assert set(result.verdicts) == set(td.VARIANTS) and result.n_trials == 2
    for verdict in result.verdicts.values():
        assert verdict["verdict"] in {"INTERESSANT_RISQUE_AJUSTE", "INTERESSANT_PERTE_REDUITE", "NON_INTERESSANT"}
        assert verdict["reference"].startswith("STATIC_") and "vs_buy_and_hold" in verdict["central"]
    for scenario in ("central", "adverse"):
        assert set(result.models[scenario]) == {"ENSEMBLE", "ENSEMBLE_VOL", "REF_BH", "BTC", "STATIC_ENSEMBLE", "STATIC_ENSEMBLE_VOL"}
        assert result.models[scenario]["BTC"]["trades"] == 1 and result.models[scenario]["ENSEMBLE"]["days"] > 200
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "TREND_DAILY" and run["metrics"]["n_trials"] == 2 and ExperimentRegistry(stored.experiments_db).program_trials() == 2


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(fa.DirtyCode):
        td.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(td, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(td.LeakAuditFailed):
        td.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
