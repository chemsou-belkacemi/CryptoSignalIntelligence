"""Confirmation de la volatilité sur la période finale (research/volatility_confirm.py, docs/VOLATILITY.md § 18) :
verrou de la période finale, lecture unique, coupure stricte (un futur falsifié ne change rien), origines dans la
fenêtre, règle de confirmation. Données SYNTHÉTIQUES : elles testent le code, jamais une prévision de marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import volatility as v1
from crypto_signal_intelligence.research import volatility_confirm as vc
from crypto_signal_intelligence.research import volatility_hourly as vh
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.protocol import FinalTestLocked

from .conftest import canonical

NOW = datetime(2026, 10, 3, tzinfo=UTC)
START = pd.Timestamp("2025-04-01", tz="UTC")
CUTOFF = pd.Timestamp("2025-05-31 23:00", tz="UTC")


def fast_trees(monkeypatch) -> None:
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 20)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})


def series(symbol: str, seed: int, *, days: int = 560) -> pd.DataFrame:
    return canonical(24 * days, "1h", symbol=symbol, start="2024-01-01", seed=seed)


def falsified_after(frame: pd.DataFrame, cutoff: pd.Timestamp, seed: int) -> pd.DataFrame:
    frame = frame.copy()
    future = (pd.to_datetime(frame["open_time"], utc=True) > cutoff).to_numpy()
    rng = np.random.default_rng(seed)
    frame.loc[future, "close"] = frame.loc[future, "close"].to_numpy(float) * rng.uniform(0.3, 3.0, int(future.sum()))
    return frame


def test_final_period_is_locked_without_the_flag(settings):
    with pytest.raises(FinalTestLocked, match="i-understand-final-test"):
        vc.run(settings, now=NOW)


def test_final_period_can_be_read_only_once(settings):
    ExperimentRegistry(settings.experiments_db).consult_final_test("VOLC-ancien", vc.STRATEGY)
    with pytest.raises(FinalTestLocked, match="une seule lecture"):
        vc.run(settings, now=NOW, allow_final_test=True)


def test_rehearsal_stays_inside_development():
    assert vc.REHEARSAL_CUTOFF <= pd.Timestamp("2025-06-30 23:59:59", tz="UTC") < vc.START
    assert pd.Timestamp("2025-07-01", tz="UTC") == vc.START and pd.Timestamp("2026-09-30 23:00", tz="UTC") == vc.CUTOFF


def test_forecasts_ignore_everything_after_the_cutoff_and_stay_in_the_window(monkeypatch):
    fast_trees(monkeypatch)
    raw = {"BTCUSDT": series("BTCUSDT", 1), "ETHUSDT": series("ETHUSDT", 2), "SOLUSDT": series("SOLUSDT", 3)}

    def frames(source: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
        market = source["BTCUSDT"]
        return ({s: v1.daily_frame(f, market) for s, f in source.items()},
                {s: vh.hourly_frame(f, market) for s, f in source.items()})

    clean_d, clean_h = frames(raw)
    fake_d, fake_h = frames({s: falsified_after(f, CUTOFF, seed=i) for i, (s, f) in enumerate(raw.items())})
    for get_d, get_h in ((clean_d, clean_h), (fake_d, fake_h)):
        get_d["out"] = vc.daily_forecasts(get_d, seed=1, start=START, cutoff=CUTOFF)
        get_h["out"] = vc.hourly_forecasts(get_h, seed=1, start=START, cutoff=CUTOFF)
    for horizon in (1, 3, 7):
        a, b = clean_d["out"][horizon], fake_d["out"][horizon]
        assert len(a) > 0
        pd.testing.assert_frame_equal(a, b)                       # le futur falsifié ne change rien
        assert a["origin"].min() >= START
        assert (a["origin"] + pd.Timedelta(days=horizon) - v1.STEP).max() <= CUTOFF
        assert np.allclose(a[vc.MEAN], (a["M4_HAR_POOLED_BTC"] + a["M5_LGBM_POOLED"]) / 2)
    a, b = clean_h["out"], fake_h["out"]
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b)
    assert a["origin"].min() >= START and (a["origin"] + pd.Timedelta(hours=23)).max() <= CUTOFF


def wide(days: int, *, model_noise: float, reference_noise: float, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    origins = pd.date_range("2025-07-01", periods=days, freq="D", tz="UTC")
    rows = []
    for symbol in ("A", "B", "C", "D", "E"):
        realized = rng.lognormal(-6, 0.5, days)
        rows.append(pd.DataFrame({"symbol": symbol, "origin": origins, "realized": realized,
                                  "M5_LGBM_POOLED": realized * rng.lognormal(0, model_noise, days),
                                  v1.BASELINE: realized * rng.lognormal(0, reference_noise, days)}))
    return pd.concat(rows, ignore_index=True)


def test_rule_outcomes():
    d1 = vc.COMPARISONS[0]
    good = vc.judge(d1, wide(450, model_noise=0.2, reference_noise=0.8))
    assert good.outcome == vc.CONFIRMED and all(good.criteria.values())
    worse = vc.judge(d1, wide(450, model_noise=0.8, reference_noise=0.2))
    assert worse.outcome == vc.CONTRADICTED and worse.ci[0] > 0
    same = vc.judge(d1, wide(450, model_noise=0.5, reference_noise=0.5))
    assert same.outcome == vc.INCONCLUSIVE
    short = vc.judge(d1, wide(60, model_noise=0.2, reference_noise=0.8))          # moins de 20 blocs : pas d'IC
    assert short.outcome == vc.NO_DATA


def test_rule_needs_both_years():
    d1 = vc.COMPARISONS[0]
    only_2025 = wide(150, model_noise=0.2, reference_noise=0.8)          # juillet à novembre 2025 seulement
    verdict = vc.judge(d1, only_2025)
    assert verdict.criteria["both_years"] is False and verdict.outcome != vc.CONFIRMED


def test_sequence_stops_at_the_first_failure():
    def v(key: str, outcome: str) -> vc.Verdict:
        return vc.Verdict(key, "m", "r", "h", 0, 0, 0, None, None, None, None, {}, None, None, {}, outcome, outcome)

    out = vc.apply_sequence([v("D1", vc.CONFIRMED), v("D3", vc.CONFIRMED), v("D7", vc.INCONCLUSIVE),
                             v("V3", vc.CONFIRMED), v("H24", vc.CONFIRMED)])
    assert [(x.key, x.verdict) for x in out] == [("H24", vc.CONFIRMED), ("D7", vc.INCONCLUSIVE), ("D3", vc.NOT_TESTED),
                                                 ("D1", vc.NOT_TESTED), ("V3", vc.NOT_TESTED)]
    assert [x.outcome for x in out][2:] == [vc.CONFIRMED, vc.CONFIRMED, vc.CONFIRMED]      # chiffres gardés, descriptifs


def test_comparisons_and_sequence_are_the_declared_ones():
    assert [c.key for c in vc.COMPARISONS] == ["D1", "D3", "D7", "V3", "H24"]
    assert vc.SEQUENCE == ("H24", "D7", "D3", "D1", "V3") and vc.LEVEL == 0.95


# --- Chemin complet sur données synthétiques (lecture « finale » simulée) -------------------------------------------

@pytest.fixture
def synthetic_final(settings, monkeypatch):
    fast_trees(monkeypatch)
    data = {s: series(s, i, days=560) for i, s in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT"), start=1)}
    monkeypatch.setattr(vc, "load_long", lambda settings_, symbol: data[symbol].copy())
    monkeypatch.setattr(vc, "open_times", lambda settings_, symbol: pd.to_datetime(data[symbol]["open_time"], utc=True))
    monkeypatch.setattr(vc, "START", START)
    monkeypatch.setattr(vc, "CUTOFF", CUTOFF)
    monkeypatch.setattr(vc, "YEARS", (2025,))
    monkeypatch.setattr(vc, "REHEARSAL_START", pd.Timestamp("2025-02-01", tz="UTC"))
    monkeypatch.setattr(vc, "REHEARSAL_CUTOFF", pd.Timestamp("2025-03-31 23:00", tz="UTC"))
    monkeypatch.setattr(vc, "code_state", lambda: "abc123")
    for module in (v1, vh):
        monkeypatch.setattr(module, "leak_audit", lambda *a, **k: {"passed": True})
    return data


def consultations(settings) -> int:
    return ExperimentRegistry(settings.experiments_db).final_test_consultations_total()


def test_full_read_is_recorded_and_cannot_be_repeated(settings, synthetic_final):
    result = vc.run(settings, now=NOW, allow_final_test=True, symbols=["ETHUSDT", "SOLUSDT"])
    assert result.consultations_total == 1 and len(result.verdicts) == 5
    registry = ExperimentRegistry(settings.experiments_db)
    run = registry.get(result.run_id)
    assert run["period_label"] == "FINAL_TEST" and run["metrics"]["n_trials"] == 5
    with pytest.raises(FinalTestLocked, match="une seule lecture"):
        vc.run(settings, now=NOW, allow_final_test=True, symbols=["ETHUSDT", "SOLUSDT"])


def test_a_crash_after_consultation_still_counts(settings, synthetic_final, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("plantage pendant la lecture")

    monkeypatch.setattr(vc, "load_until", boom)
    with pytest.raises(RuntimeError, match="plantage"):
        vc.run(settings, now=NOW, allow_final_test=True, symbols=["ETHUSDT", "SOLUSDT"])
    assert consultations(settings) == 1
    with pytest.raises(FinalTestLocked):
        vc.run(settings, now=NOW, allow_final_test=True, symbols=["ETHUSDT", "SOLUSDT"])


def test_rehearsal_counts_nothing(settings, synthetic_final):
    result = vc.run(settings, now=NOW, rehearsal=True, symbols=["ETHUSDT", "SOLUSDT"])
    assert result.rehearsal and len(result.verdicts) == 5
    assert consultations(settings) == 0
    assert ExperimentRegistry(settings.experiments_db).count_runs() == 0


def test_incomplete_store_does_not_consume_the_read(settings, synthetic_final, monkeypatch):
    short = {s: f[pd.to_datetime(f["open_time"], utc=True) <= CUTOFF - pd.Timedelta(days=5)] for s, f in synthetic_final.items()}
    monkeypatch.setattr(vc, "load_long", lambda settings_, symbol: short[symbol].copy())
    monkeypatch.setattr(vc, "open_times", lambda settings_, symbol: pd.to_datetime(short[symbol]["open_time"], utc=True))
    with pytest.raises(v1.IncompleteData, match="rien n'est lu"):
        vc.run(settings, now=NOW, allow_final_test=True, symbols=["ETHUSDT", "SOLUSDT"])
    assert consultations(settings) == 0


def test_final_read_refuses_uncommitted_code(settings, synthetic_final):
    with pytest.raises(v1.DirtyCode):
        vc.run(settings, now=NOW, allow_final_test=True, allow_dirty=True)
    assert consultations(settings) == 0


def test_coverage_reads_only_open_times_from_the_real_store_layout(settings):
    """Sur un magasin long réel (synthétique), la couverture lit la colonne des heures sans charger de prix."""
    from crypto_signal_intelligence.data.store import CandleStore
    from crypto_signal_intelligence.research.long_history import long_settings
    frame = series("ETHUSDT", 1, days=30)
    CandleStore(long_settings(settings).data_dir).save(frame, "ETHUSDT", "1h")
    times = vc.open_times(settings, "ETHUSDT")
    assert len(times) == len(frame) and str(times.dt.tz) == "UTC"
    with pytest.raises(v1.MissingData):
        vc.open_times(settings, "XYZUSDT")
