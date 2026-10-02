"""Prévision de volatilité v2 (docs/VOLATILITY.md § 14) : variables v2 calculées à la main, DVOL joint vers le passé
et sa mutation, causalité, combinaison des modèles en service, V4 sur ses seules lignes, règle contre la référence
en service, bout en bout synthétique. Données SYNTHÉTIQUES : elles testent le code, jamais une prévision."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import volatility as v1
from crypto_signal_intelligence.research import volatility_v2 as v2
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.long_history import long_settings

from .conftest import canonical

NOW = datetime(2026, 10, 2, tzinfo=UTC)
DOC = Path(__file__).resolve().parents[1] / "docs" / "VOLATILITY.md"
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT")


def day(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def candles(returns, *, start: str = "2024-01-01", spread: float = 0.01) -> pd.DataFrame:
    """Bougies 1 h de rendements log `returns` ; plus haut = clôture × (1 + spread), plus bas = clôture / (1 + spread)."""
    frame = canonical(len(returns) + 1, "1h", start=start)
    close = 100 * np.exp(np.r_[0.0, np.cumsum(returns)])
    return frame.assign(close=close, high=close * (1 + spread), low=close / (1 + spread))


def hourly(symbol: str, *, days: int, seed: int, start: str = "2024-01-01") -> pd.DataFrame:
    return canonical(24 * days, "1h", symbol=symbol, start=start, seed=seed)


def fake_dvol(start: str = "2023-10-01", days: int = 600, level: float = 50.0, **_kw) -> pd.Series:
    index = pd.date_range(start, periods=days, freq="D", tz="UTC")
    return pd.Series(level + np.random.default_rng(7).normal(0, 3, days), index=index)


def fast_trees(monkeypatch) -> None:
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 20)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})


def test_declared_constants_match_the_document():
    text = DOC.read_text(encoding="utf-8")
    assert v2.N_TRIALS == 12 and "12 comparaisons" in text and pytest.approx(1 - 0.05 / 12) == v2.LEVEL
    assert all(model in text for model in v2.MODELS) and v2.DVOL_START in text and "2021-03-24" in text
    assert v2.SERVICE == {1: "M5_LGBM_POOLED", 3: "M5_LGBM_POOLED", 7: "M4_HAR_POOLED_BTC"}
    assert set(v2.INPUTS) >= set(v1.INPUTS) | set(v2.NEG) | set(v2.RANGE) | set(v2.DVOL)


def test_negative_share_and_parkinson_match_a_hand_computation():
    # Une bougie de base précède les rendements : jour 1 = 23 rendements de +1 % (aucun négatif, couverture 23/24) ;
    # jour 2 = 24 rendements de ±2 % alternés (la moitié négative).
    returns = np.r_[np.full(23, 0.01), 0.02 * np.where(np.arange(25) % 2, 1.0, -1.0)]
    frame = v2.daily_frame(candles(returns, spread=0.01)).set_index("origin")
    first, second = frame.loc[day("2024-01-02")], frame.loc[day("2024-01-03")]
    assert first["neg_share_d"] == pytest.approx(0.0) and second["neg_share_d"] == pytest.approx(0.5)
    park = (2 * math.log(1.01)) ** 2 / (4 * math.log(2))
    assert second["log_park_d"] == pytest.approx(math.log(park))
    assert np.isnan(second["neg_share_w"]) and np.isnan(second["log_park_w"])       # 168 h : pas encore couvertes
    assert np.isnan(second["log_dvol_var"]) and np.isnan(second["dvol_spread"])     # sans DVOL


def test_dvol_is_joined_backward_only_and_the_next_day_is_a_detected_leak():
    returns = np.random.default_rng(1).normal(0, 0.01, 24 * 14)
    h1 = candles(returns)
    dvol = pd.Series([40.0, 50.0, 60.0, 70.0, 80.0], index=pd.date_range("2024-01-07", periods=5, freq="D", tz="UTC"))
    frame = v2.daily_frame(h1, None, dvol).set_index("origin")
    # À l'origine du 09/01 00:00, la journée DVOL du 08/01 vient de se clôturer : 50 ; celle du 09/01 (60) est future.
    expected = math.log((50 / 100) ** 2 / v2.HOURS_PER_YEAR)
    assert frame.loc[day("2024-01-09"), "log_dvol_var"] == pytest.approx(expected)
    assert frame.loc[day("2024-01-09"), "dvol_spread"] == pytest.approx(expected - frame.loc[day("2024-01-09"), "log_var_w"])
    assert np.isnan(frame.loc[day("2024-01-07"), "log_dvol_var"])                   # avant la première clôture connue
    leaky = v2.daily_frame(h1, None, dvol, leaky_dvol=True).set_index("origin")
    assert leaky.loc[day("2024-01-09"), "log_dvol_var"] == pytest.approx(math.log((60 / 100) ** 2 / v2.HOURS_PER_YEAR))
    assert v2.with_dvol(frame.reset_index(), None)[list(v2.DVOL)].isna().all().all()


def test_variables_are_causal_and_both_mutations_are_detected():
    h1 = hourly("BTCUSDT", days=40, seed=3)
    dvol = fake_dvol(start="2024-01-01", days=60)
    origins = [day("2024-02-01"), day("2024-02-05")]
    assert v2.causality_violations(h1, h1, dvol, origins=origins, seed=1) == []
    found = v2.causality_violations(h1, h1, dvol, origins=origins, seed=1, leaky=True)
    assert found and all("log_var_d" in v["features"] for v in found)
    found = v2.causality_violations(h1, h1, dvol, origins=origins, seed=1, leaky_dvol=True)
    assert found and all({"log_dvol_var", "dvol_spread"} <= set(v["features"]) for v in found)


def test_mean_candidate_averages_the_service_models_and_v4_needs_dvol(monkeypatch):
    fast_trees(monkeypatch)
    series = {symbol: hourly(symbol, days=200, seed=index, start="2023-10-01") for index, symbol in enumerate(PAIRS)}
    dvol = fake_dvol(start="2024-01-15", days=200)                                   # connu seulement à partir du 16/01
    data = pd.concat([v2.daily_frame(frame, series["BTCUSDT"], dvol).assign(symbol=symbol) for symbol, frame in series.items()],
                     ignore_index=True)
    refit = day("2024-04-01")
    for horizon, service in ((1, "trees"), (7, "har_btc")):
        models = v2.fit_at(data, refit, horizon, seed=1)
        assert models.rows_dvol < models.rows and models.har_dvol is not None
        month = data[(data["origin"] >= refit) & (data["origin"] < day("2024-04-10"))]
        out = v2.month_forecasts(models, month, horizon)
        m4 = models.har_btc.predict(month[[*v1.OWN, *v1.MARKET_FEATURES]].to_numpy(float))
        m5 = models.trees.predict(month[list(v1.FEATURES)].to_numpy(float))
        assert out["V1_MEAN_M4_M5"].to_numpy() == pytest.approx((m4 + m5) / 2)
        assert out[v2.REFERENCE].to_numpy() == pytest.approx(m5 if service == "trees" else m4)
        assert np.isfinite(out[["V2_HAR_SEMIVAR", "V3_LGBM_ENRICHED", "V4_HAR_DVOL"]].to_numpy()).all()
    early = data[(data["origin"] >= day("2024-01-05")) & (data["origin"] < day("2024-01-10"))]
    assert np.isnan(v2.month_forecasts(models, early, 7)["V4_HAR_DVOL"].to_numpy()).all()  # DVOL inconnu : pas de V4


def test_rule_adapts_the_years_criterion_to_the_years_covered():
    origins = pd.date_range("2021-01-01", "2025-06-30", freq="D", tz="UTC")
    table = pd.DataFrame({"symbol": "A", "origin": origins, "qlike": 0.4, "qlike_base": 0.5, "log_error": 0.1, "log_error_base": 0.2})
    row = v2._row("V4_HAR_DVOL", 1, table)
    assert row.years_needed == 4 and row.years_better == 5 and row.criteria["years"]
    long = pd.date_range("2019-01-01", "2025-06-30", freq="D", tz="UTC")
    full = pd.DataFrame({"symbol": "A", "origin": long, "qlike": 0.4, "qlike_base": 0.5, "log_error": 0.1, "log_error_base": 0.2})
    assert v2._row("V1_MEAN_M4_M5", 1, full).years_needed == 6                     # 2019 à 2025 : 7 années couvertes


def test_fetch_dvol_history_pages_by_year_through_the_whitelisted_endpoint():
    calls = []

    class Source:
        def get_json(self, url, params=None):
            calls.append((url, params))
            start = params["start_timestamp"]
            return {"result": {"data": [[start, 1, 2, 3, 55.5], [start + 86_400_000, 1, 2, 3, 56.5]]}}

    series = v2.fetch_dvol_history(start="2021-03-24", end=day("2022-06-01"), client=Source())
    assert [c[0] for c in calls] == [v2.DVOL_URL] * 2 and calls[0][1]["currency"] == "BTC" and calls[0][1]["resolution"] == "1D"
    assert series.index[0] == day("2021-03-24") and series.iloc[0] == 55.5 and len(series) == 4

    class Misaligned:
        def get_json(self, url, params=None):
            return {"result": {"data": [[params["start_timestamp"] + 8 * 3_600_000, 1, 2, 3, 55.5]]}}

    from crypto_signal_intelligence.forward.sources import SourceError
    with pytest.raises(SourceError):                                         # bougie à 08:00 UTC : l'hypothèse du § 14 tombe
        v2.fetch_dvol_history(start="2021-03-24", end=day("2021-04-01"), client=Misaligned())


@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for index, symbol in enumerate(PAIRS):                                           # 2023-10-10 → 2025-02-10
        store.save(hourly(symbol, days=490, seed=index, start="2023-10-10"), symbol, "1h")
    settings.protocol.development_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    fast_trees(monkeypatch)
    monkeypatch.setattr(v1, "MIN_HISTORY_DAYS", 60)
    monkeypatch.setattr(v1, "FIRST_FORECAST", "2024-03-01")
    monkeypatch.setattr(v1, "YEARS", (2024, 2025))
    monkeypatch.setattr(v1, "MIN_YEARS_BETTER", 2)
    monkeypatch.setattr(v2, "code_state", lambda: "0123abcd")
    return settings


def dvol_from_march(**_kw) -> pd.Series:
    return fake_dvol(start="2024-03-20", days=400)


def test_run_records_twelve_comparisons_with_v4_on_its_own_sample(stored):
    result = v2.run(stored, now=NOW, symbols=list(PAIRS), dvol_fetcher=dvol_from_march)
    audit = result.leak_audit
    assert audit["passed"] and audit["checked_pairs"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"] and not audit["violations"]
    assert all(audit["mutation_detected"].values()) and all(audit["dvol_mutation_detected"].values())
    assert result.n_trials == 12 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 12, abs=1e-6)
    assert [(r.model, r.horizon_days) for r in result.rows] == [(m, h) for h in v2.HORIZONS for m in v2.CANDIDATES]
    by_model = {(r.model, r.horizon_days): r for r in result.rows}
    assert by_model[("V1_MEAN_M4_M5", 1)].days == 337 and by_model[("V2_HAR_SEMIVAR", 7)].days == 331
    assert 250 < by_model[("V4_HAR_DVOL", 1)].days < 337          # DVOL connu depuis le 21/03 ; 100 lignes avant d'ajuster V4
    assert all(r.pairs == 4 and r.ci_qlike_diff is not None and r.years_needed == 1 for r in result.rows)
    assert result.verdict in {v2.BETTER, v2.NO_IMPROVEMENT} and set(result.selected) == set(v2.HORIZONS)
    assert result.data_hashes["DVOL"] and set(result.data_hashes) == set(PAIRS) | {"DVOL"}
    assert result.coverage["dvol"]["days"] == 318 and result.coverage["pairs"]["rows"]["ADAUSDT"]["dvol_rows"] < 480
    assert v2.dvol_path(stored).exists()
    report = stored.reports_dir / result.run_id
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    assert summary["protocol_version"] == 2 and summary["service"] == {"1": "M5_LGBM_POOLED", "3": "M5_LGBM_POOLED", "7": "M4_HAR_POOLED_BTC"}
    saved = pd.read_parquet(report / "forecasts.parquet")
    v4 = saved[saved["model"] == "V4_HAR_DVOL"]
    assert v4["forecast"].isna().any() and v4["forecast"].notna().any()             # NaN avant DVOL, prévu après
    assert (saved["origin"] <= pd.Timestamp(stored.protocol.development_end)).all()  # aucune origine après DEVELOPMENT
    assert all(len(v) == 6 for v in audit["origins"].values())                       # 3 à DVOL connu + 3 quelconques
    assert list(saved.columns) == list(v2.FORECAST_COLUMNS) and len(saved) == 5 * 4 * (337 + 335 + 331)
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "VOLATILITY" and run["strategy"] == "VOLATILITY_FORECAST_V2" and run["strategy_version"] == 2
    assert run["metrics"]["n_trials"] == 12 == run["metrics"]["program_trials"] == result.program_trials
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 12
    cached = v2.load_dvol(stored, end=day("2025-01-31"), fetcher=lambda **_k: pytest.fail("réseau"))
    assert cached.index.max() < day("2025-01-31") and len(cached) == 317


def test_uncommitted_code_or_failed_audit_records_nothing(stored, monkeypatch):
    monkeypatch.setattr(v2, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(v1.DirtyCode):
        v2.run(stored, now=NOW, symbols=list(PAIRS), dvol_fetcher=dvol_from_march)
    monkeypatch.setattr(v2, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(v2, "leak_audit", lambda *a, **k: {"passed": False, "violations": [{"x": 1}]})
    with pytest.raises(v1.LeakAuditFailed):
        v2.run(stored, now=NOW, symbols=list(PAIRS), dvol_fetcher=dvol_from_march)
    with pytest.raises(v1.IncompleteData):
        monkeypatch.setattr(v2, "leak_audit", lambda *a, **k: {"passed": True})
        monkeypatch.setattr(v2, "load_dvol", lambda *a, **k: pd.Series(dtype=float))
        v2.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0
