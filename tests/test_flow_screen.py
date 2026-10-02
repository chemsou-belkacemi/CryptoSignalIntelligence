"""Étape 6 du plan (research/flow_screen.py, criblage J) : part des achats au marché par journée, drapeaux contre les
centiles des journées PRÉCÉDENTES, veto « offre nouvelle », MVRV connu avec retard, rendements à terme, audit des
fuites et bout en bout sur un magasin synthétique. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research import flow_screen as fs
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

START = pd.Timestamp("2024-01-01", tz="UTC")
DAY = pd.Timedelta(days=1)
NOW = datetime(2025, 1, 15, tzinfo=UTC)


def hourly(days: int, share: float = 0.5, symbol: str = "AAAUSDT") -> pd.DataFrame:
    """Bougies 1 h plates à 100, volume 1 000 USDT par heure, part d'achats au marché `share` (constante ou par jour)."""
    frame = canonical(24 * days, "1h", symbol=symbol, start=str(START.date()), seed=1, drift=0.0)
    frame["open"] = frame["close"] = frame["high"] = frame["low"] = 100.0
    frame["quote_volume"] = 1000.0
    shares = np.full(len(frame), share) if np.isscalar(share) else np.repeat(share, 24)[: len(frame)]
    frame["taker_buy_quote_volume"] = 1000.0 * shares
    return frame


def test_daily_flow_is_known_the_next_day_and_needs_full_days():
    frame = hourly(3, share=0.6)
    frame = frame.iloc[:-5]                                                     # dernière journée : 19 bougies, invalide
    share = fs.daily_flow({"AAAUSDT": frame})
    assert list(share.index) == [START + DAY, START + 2 * DAY]                  # indexée au lendemain 00:00
    assert share["AAAUSDT"].to_list() == pytest.approx([0.6, 0.6])


def test_flow_flags_compare_to_previous_days_only(monkeypatch):
    monkeypatch.setattr(fs, "FLOW_WINDOW", 10)
    monkeypatch.setattr(fs, "FLOW_MIN_DAYS", 5)
    values = [0.45, 0.5, 0.55, 0.48, 0.52, 0.47, 0.53, 0.49, 0.51, 0.5] + [0.9, 0.1, 0.5]
    share = pd.DataFrame({"A": values}, index=pd.date_range(START, periods=len(values), freq="D", tz="UTC"))
    high, low = fs.flow_flags(share)
    assert high["A"].to_numpy()[10] and not high["A"].to_numpy()[:10].any()    # 0,9 ≥ 90e centile des 10 précédents
    assert low["A"].to_numpy()[11] and not low["A"].to_numpy()[:11].any()
    assert not high["A"].to_numpy()[12] and not low["A"].to_numpy()[12]
    # Un jour extrême ne doit pas s'auto-déclencher par comparaison avec lui-même : fenêtre sur les jours d'avant.
    single = pd.DataFrame({"A": [0.5] * 5 + [0.95]}, index=pd.date_range(START, periods=6, freq="D", tz="UTC"))
    assert fs.flow_flags(single)[0]["A"].to_numpy()[5]


def test_new_supply_flags_cover_days_30_to_180_after_first_close():
    index = pd.date_range(START, periods=200, freq="D", tz="UTC")
    close = pd.DataFrame({"OLD": 100.0, "NEW": [np.nan] * 10 + [100.0] * 190}, index=index)
    flags = fs.new_supply_flags(close)
    new = flags["NEW"].to_numpy()
    assert not new[39] and new[40] and new[190] and not new[191]                # 30 et 180 jours après le jour 10
    assert flags["OLD"].to_numpy()[30] and not flags["OLD"].to_numpy()[181]


def test_mvrv_flags_apply_the_two_day_latency(monkeypatch):
    monkeypatch.setattr(fs, "MVRV_WINDOW", 10)
    monkeypatch.setattr(fs, "MVRV_MIN_DAYS", 5)
    index = pd.date_range(START, periods=20, freq="D", tz="UTC")
    before, after = [2.0, 1.9, 2.1, 1.8, 2.2, 1.95, 2.05, 2.15, 2.25, 2.3], [2.3, 2.25, 2.2, 2.1, 2.0, 2.4, 2.35, 2.45, 2.5]
    mvrv = pd.Series(before + [0.5] + after, index=index)
    low, high = fs.mvrv_flags(mvrv, index)
    assert low.to_numpy()[12] and not low.to_numpy()[10:12].any() and not low.to_numpy()[13]   # 0,5 du jour 10 connu au jour 12
    assert not high.to_numpy()[12] and high.to_numpy()[-1]                                   # 2,5 ≥ 80e centile


def test_forward_returns_buy_at_0100_and_sell_at_close_h_days_later():
    index = pd.date_range(START, periods=5, freq="D", tz="UTC")
    close = pd.DataFrame({"A": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=index)
    price = pd.DataFrame({"A": [100.5, 101.5, 102.5, 103.5, 104.5]}, index=index)
    panel = fa.Panel(close, close * 0 + 1e6, price)
    fwd = fs.forward_returns(panel, 1)["A"].to_numpy()
    assert fwd[0] == pytest.approx(101.0 / 100.5 - 1) and np.isnan(fwd[-1])
    assert fs.forward_returns(panel, 2)["A"].to_numpy()[0] == pytest.approx(102.0 / 100.5 - 1)


def test_events_frame_subtracts_the_pair_drift():
    index = pd.date_range(START, periods=4, freq="D", tz="UTC")
    fwd = pd.DataFrame({"A": [0.01, 0.03, -0.02, np.nan]}, index=index)
    flags = pd.DataFrame({"A": [True, False, True, True]}, index=index)
    frame = fs.events_frame(flags, fwd)
    assert len(frame) == 2 and frame["excess"].to_list() == pytest.approx([0.01 - 0.02 / 3, -0.02 - 0.02 / 3])


PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")


def fake_mvrv(asset: str, **_kw) -> pd.Series:
    index = pd.date_range("2022-01-01", "2025-06-30", freq="D", tz="UTC")
    rng = np.random.default_rng(len(asset))
    return pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, len(index)))), index=index)


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        frame = canonical(24 * 500, "1h", symbol=symbol, start="2023-09-01", seed=i, drift=0.00003 * (i - 2))
        rng = np.random.default_rng(100 + i)
        frame["taker_buy_quote_volume"] = frame["quote_volume"] * rng.uniform(0.3, 0.7, len(frame))
        store.save(frame, symbol, "1h")
    settings.protocol.development_end = datetime(2024, 12, 30, 23, 59, 59, tzinfo=UTC)
    settings.protocol.bootstrap_samples = 200
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(fs, "FIRST_DAY", pd.Timestamp("2024-01-01", tz="UTC"))
    monkeypatch.setattr(fs, "FLOW_WINDOW", 60)
    monkeypatch.setattr(fs, "FLOW_MIN_DAYS", 40)
    monkeypatch.setattr(fs, "MVRV_WINDOW", 120)
    monkeypatch.setattr(fs, "MVRV_MIN_DAYS", 60)
    return settings


def test_run_measures_fifteen_trials_and_caches_mvrv(stored):
    result = fs.run(stored, now=NOW, symbols=list(PAIRS), mvrv_fetcher=fake_mvrv)
    assert result.leak_audit["passed"] and result.leak_audit["mutation_detected"]
    assert result.n_trials == 15 and len(result.rows) == 15
    assert {r.condition for r in result.rows} == set(fs.CONDITIONS) and {r.horizon_h for r in result.rows} == {24, 168, 720}
    j3 = [r for r in result.rows if r.condition == "J3_NEW_SUPPLY_VETO"]
    # Magasin ouvert le 2023-09-01 : « offre nouvelle » du 2023-10-02 au 2024-02-29, soit 60 journées de 2024 par paire.
    assert [r.events for r in j3 if r.horizon_h == 24] == [60 * len(PAIRS)]
    j1 = [r for r in result.rows if r.condition == "J1_FLOW_BUY_TOP" and r.horizon_h == 24][0]
    assert j1.events > 0 and j1.pairs == len(PAIRS) and j1.ci95_excess_pct is not None
    assert not any(r.beats_costs for r in result.rows if r.condition in fs.VETO_CONDITIONS)
    assert fs.mvrv_path(stored, "btc").exists() and fs.mvrv_path(stored, "eth").exists()
    registry = ExperimentRegistry(stored.experiments_db)
    assert registry.get(result.run_id)["kind"] == "SCREEN" and registry.program_trials() == 15
    cached = fs.load_mvrv(stored, "btc", end=pd.Timestamp("2024-12-30", tz="UTC"), fetcher=lambda *_a, **_k: pytest.fail("réseau"))
    assert cached.index.max() <= pd.Timestamp("2024-12-30", tz="UTC") and len(cached) > 300


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(fa.DirtyCode):
        fs.run(stored, now=NOW, symbols=list(PAIRS), mvrv_fetcher=fake_mvrv)
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(fs, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(fs.LeakAuditFailed):
        fs.run(stored, now=NOW, symbols=list(PAIRS), mvrv_fetcher=fake_mvrv)
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0


def test_fetch_mvrv_pages_through_the_community_api():
    calls = []

    class Source:
        def get_json(self, url, params=None):
            calls.append(params)
            if params.get("next_page_token"):
                return {"data": [{"asset": "btc", "time": "2017-01-03T00:00:00.000000000Z", "CapMVRVCur": "1.2"}]}
            return {"data": [{"asset": "btc", "time": "2017-01-01T00:00:00.000000000Z", "CapMVRVCur": "1.0"},
                             {"asset": "btc", "time": "2017-01-02T00:00:00.000000000Z", "CapMVRVCur": None}], "next_page_token": "p2"}

    series = fs.fetch_mvrv("btc", client=Source())
    assert len(calls) == 2 and calls[0]["metrics"] == "CapMVRVCur" and calls[1]["next_page_token"] == "p2"
    assert series.to_list() == [1.0, 1.2] and series.index[0] == pd.Timestamp("2017-01-01", tz="UTC")
