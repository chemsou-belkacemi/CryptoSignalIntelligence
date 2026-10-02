"""Étape 8 du plan (research/pivot_screen.py, criblage K) : agrégation en blocs complets, pivots confirmés utilisables
seulement après leurs bougies de confirmation, événements K1/K2 (un par niveau), audit des fuites et sa mutation,
bout en bout synthétique. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import factors as fa
from crypto_signal_intelligence.research import pivot_screen as ps
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

NOW = datetime(2025, 1, 15, tzinfo=UTC)


def bars_from(highs, lows, closes, opens=None) -> pd.DataFrame:
    n = len(closes)
    index = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open_time": index, "open": opens if opens is not None else closes, "high": highs, "low": lows,
                         "close": closes, "available_at": index + pd.Timedelta(hours=4)})


def test_aggregate_keeps_complete_utc_blocks_only():
    h1 = canonical(24 * 3, "1h", start="2024-01-01")
    h1 = h1.drop(index=[30]).reset_index(drop=True)                      # bloc 4 h de 04:00 le 2 janvier : incomplet
    four = ps.aggregate(h1, 4)
    assert len(four) == 17 and pd.Timestamp("2024-01-02 04:00", tz="UTC") not in set(four["open_time"])
    block = ps.aggregate(h1, 4).iloc[0]
    first = h1.iloc[:4]
    assert block["open"] == first["open"].iloc[0] and block["close"] == first["close"].iloc[-1]
    assert block["high"] == first["high"].max() and block["low"] == first["low"].min()
    assert block["available_at"] == first["available_at"].iloc[-1]
    assert len(ps.aggregate(h1, 24)) == 2                                   # le 2 janvier (23 bougies) est exclu


def test_pivot_is_usable_only_after_its_confirmation_bars():
    high = np.array([10, 11, 12, 15, 13, 12, 11, 11, 11, 11, 11, 11], dtype=float)
    low = high - 1
    res, res_i, sup, sup_i = ps.pivot_levels(high, low, k=3, max_age=4)
    # Pivot haut à i = 3 (15 > 10, 11, 12 et ≥ 13, 12, 11) : confirmé à la clôture de 6, utilisable de 7 à 10 (4 bougies).
    assert np.isnan(res[:7]).all() and res[7] == 15 and res_i[7] == 3 and res[10] == 15 and np.isnan(res[11])
    leaky = ps.pivot_levels(high, low, k=3, max_age=4, leaky=True)[0]
    assert leaky[4] == 15                                                  # mutation : utilisable dès i + 1
    # Pivot bas à i = 6 (10 < 14, 12, 11 et ≤ 10, 10, 10) : utilisable de 10 à 11 ; le creux initial (i = 0) n'a pas de gauche.
    assert np.isnan(sup[:10]).all() and sup[10] == 10 and sup_i[10] == 6


def test_support_bounce_and_resistance_break_fire_once_per_level():
    # Creux à 90 (i = 3) confirmé par 3 bougies à 94 ; touche à 90,2 avec clôture haussière à 93 en t = 8 (K1), puis une
    # seconde touche du même support en t = 9 (ignorée : niveau déjà utilisé) ; résistance 100 (pivot haut i = 11,
    # utilisable dès 15) cassée en t = 18 (clôture 101 après une clôture 98 ≤ 100).
    close = np.array([100, 98, 96, 92, 95, 95, 95, 96, 93, 93, 96, 100, 97, 96, 95, 96, 97, 98, 101, 102, 103, 104], float)
    low = close - 1.0
    low[3], low[8], low[9] = 90.0, 90.2, 90.2
    high = close + 1.0
    high[7], high[11] = 96.0, 100.0                                        # pas de pivot haut en 7 ; pivot haut en 11
    opens = close - 0.5                                                    # clôtures haussières partout
    events = ps.events_of(bars_from(high, low, close, opens))
    assert np.flatnonzero(events["K1_SUPPORT_BOUNCE"]).tolist() == [8]
    assert np.flatnonzero(events["K2_RESISTANCE_BREAK"]).tolist() == [18]
    bearish = ps.events_of(bars_from(high, low, close, close + 0.5))
    assert not bearish["K1_SUPPORT_BOUNCE"].any()                           # clôture baissière : pas de rebond


def test_collect_enters_next_open_and_removes_the_pair_drift():
    close = np.linspace(100, 121, 22)
    bars = bars_from(close + 1, close - 1, close, close)
    events = np.zeros(22, dtype=bool)
    events[5] = True
    frame = ps.collect(bars, events, 6, "A")
    assert len(frame) == 1 and frame["ret"].iloc[0] == pytest.approx(close[11] / close[6] - 1)
    fwd = ps.forward_returns(bars, 6)
    assert frame["excess"].iloc[0] == pytest.approx(frame["ret"].iloc[0] - np.nanmean(fwd))


PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for i, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 500, "1h", symbol=symbol, start="2023-09-01", seed=i, drift=0.00003 * (i - 2)), symbol, "1h")
    settings.protocol.development_end = datetime(2024, 12, 30, 23, 59, 59, tzinfo=UTC)
    settings.protocol.bootstrap_samples = 200
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(ps, "FIRST_DAY", pd.Timestamp("2023-10-01", tz="UTC"))
    return settings


def test_run_audits_then_records_four_trials(stored):
    result = ps.run(stored, now=NOW, symbols=list(PAIRS))
    assert result.leak_audit["passed"] and result.leak_audit["mutation_detected"] == {"4h": True, "1d": True}
    assert result.n_trials == 4 == len(result.rows)
    assert [r.condition for r in result.rows] == ["K1_SUPPORT_BOUNCE_4H", "K1_SUPPORT_BOUNCE_1D", "K2_RESISTANCE_BREAK_4H", "K2_RESISTANCE_BREAK_1D"]
    assert {r.horizon_h for r in result.rows} == {24, 168} and all(r.events > 0 and r.pairs == len(PAIRS) for r in result.rows)
    assert result.coverage["bars"]["1d"]["BTCUSDT"] == 487 and result.coverage["bars"]["4h"]["ETHUSDT"] == 487 * 6
    registry = ExperimentRegistry(stored.experiments_db)
    assert registry.get(result.run_id)["strategy"] == "SCREEN_PIVOT_K" and registry.program_trials() == 4


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(fa.DirtyCode):
        ps.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(fa, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(ps, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(ps.LeakAuditFailed):
        ps.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
