"""Données de contexte contre direction (docs/CONTEXTE_PREDICTION.md) : retards, moyennes, rangs, rendements calculés à
la main, causalité (falsifier la suite ne change ni variable ni rang), écart tiers haut − bas, bout en bout.
Données SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import context_screen as cs

D0 = pd.Timestamp("2018-01-01", tz="UTC")


def days(n: int, start=D0) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def test_lag_and_rolling_mean_by_hand():
    s = pd.Series(np.arange(1.0, 11.0), index=days(10))
    lagged = cs.at_decision(cs.rolling_mean(s), 2)
    # décision au jour 9 (index 8) : moyenne des jours 1 à 7 (valeurs 1..7) → 4, lue deux jours plus tard
    assert lagged[D0 + pd.Timedelta(days=8)] == pytest.approx(4.0)
    assert np.isnan(lagged[D0 + pd.Timedelta(days=5)])                      # moins de 5 jours présents
    holes = s.copy()
    holes.iloc[[3, 4, 5]] = np.nan                                         # 4 jours présents sur 7 : rien
    assert np.isnan(cs.rolling_mean(holes)[D0 + pd.Timedelta(days=6)])


def test_rank_vs_past_excludes_today():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 2.0], index=days(5))
    r = cs.rank_vs_past(s, days=4, minimum=4)
    # jour 5 : 2 parmi [1, 2, 3, 4] → (1 plus petit + ½ égal) / 4
    assert r.iloc[4] == pytest.approx(0.375) and r.iloc[:4].isna().all()


def test_forward_returns_by_hand():
    close = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41], index=days(5))
    r1 = cs.forward_returns(close, 1)
    t = D0 + pd.Timedelta(days=2)                                          # entrée clôture du jour 1 (110), sortie jour 2 (121)
    assert r1[t] == pytest.approx(np.log(121 / 110))
    r3 = cs.forward_returns(close, 3)
    assert r3[D0 + pd.Timedelta(days=1)] == pytest.approx(np.log(133.1 / 100))
    late = cs.forward_returns(close, 1, delay=1)
    assert late[t] == pytest.approx(np.log(133.1 / 121))                   # entrée un jour plus tard


def synthetic_raw(n: int = 1500, seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = days(n, pd.Timestamp("2018-01-01", tz="UTC"))
    btc = 10000 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    eth = 500 * np.exp(np.cumsum(rng.normal(0, 0.04, n)))
    frame = lambda **cols: pd.DataFrame(cols, index=idx)                   # noqa: E731
    return {
        "flows:FlowInExNtv": frame(btc=rng.uniform(1e4, 2e4, n), eth=rng.uniform(1e5, 2e5, n)),
        "flows:FlowOutExNtv": frame(btc=rng.uniform(1e4, 2e4, n), eth=rng.uniform(1e5, 2e5, n)),
        "flows:SplyExNtv": frame(btc=np.full(n, 2.5e6), eth=np.full(n, 1.5e7)),
        "binance_daily:close": frame(BTCUSDT=btc, ETHUSDT=eth),
        "coinbase:close_usd": frame(BTC=btc * (1 + rng.normal(0, 0.001, n))),
        "upbit:close_krw": frame(BTC=btc * 1300 * (1 + rng.normal(0.01, 0.005, n))),
        "ecb:per_eur": frame(KRW=np.full(n, 1430.0), USD=np.full(n, 1.1)),
        "stablecoins:circulating_usd": frame(all=1e11 * np.exp(np.cumsum(rng.normal(0.001, 0.002, n)))),
        "ratios_archive:sum_toptrader_long_short_ratio": frame(BTCUSDT=rng.uniform(0.8, 1.6, n)),
        "wikipedia:views": frame(Bitcoin=rng.uniform(5000, 15000, n)),
    }


def test_features_and_ranks_never_read_the_future():
    raw = synthetic_raw()
    end = pd.Timestamp("2030-01-01", tz="UTC")
    cut = pd.Timestamp("2019-06-01", tz="UTC")
    fake = {k: v.copy() for k, v in raw.items()}
    for frame in fake.values():
        frame.loc[frame.index >= cut] *= np.random.default_rng(3).uniform(0.5, 2, frame.loc[frame.index >= cut].shape)
    a, b = cs.build_features(raw, end), cs.build_features(fake, end)
    for code in cs.FEATURES:
        known = a[code].index <= cut                                       # décision T ≤ cut : données ≤ T − 1 < cut
        pd.testing.assert_series_equal(a[code][known], b[code].reindex(a[code].index)[known], check_names=False)
        ra, rb = cs.rank_vs_past(a[code]), cs.rank_vs_past(b[code])
        pd.testing.assert_series_equal(ra[ra.index <= cut], rb[rb.index <= cut], check_names=False)


def test_premiums_by_hand():
    raw = synthetic_raw(400)
    raw["coinbase:close_usd"]["BTC"] = raw["binance_daily:close"]["BTCUSDT"] * 1.002
    raw["upbit:close_krw"]["BTC"] = raw["binance_daily:close"]["BTCUSDT"] * 1300 * 1.05
    f = cs.build_features(raw, pd.Timestamp("2030-01-01", tz="UTC"))
    assert f["CB_PREMIUM"].iloc[10] == pytest.approx(0.002)
    assert f["KR_PREMIUM"].iloc[10] == pytest.approx(1300 * 1.05 / (1430 / 1.1) - 1)


def test_compare_detects_effect_and_null():
    idx = days(2500)
    rng = np.random.default_rng(1)
    rank = pd.Series(rng.random(2500), index=idx)
    ret = pd.Series(rng.normal(0, 0.03, 2500) + np.where(rank > 2 / 3, 0.01, 0.0), index=idx)
    strong = cs.compare(rank, ret, samples=2000)
    assert cs.verdict(strong) == cs.UP and strong["diff_pct"] == pytest.approx(1.0, abs=0.3)
    null = cs.compare(rank, pd.Series(rng.normal(0, 0.03, 2500), index=idx), samples=2000)
    assert cs.verdict(null) == cs.NOTHING
    short = cs.compare(rank.iloc[:150], ret.iloc[:150], samples=200)
    assert short["diff_pct"] is None and cs.verdict(short) == cs.NOTHING


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    monkeypatch.setattr(cs, "code_state", lambda: "abc123")
    payload = cs.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=synthetic_raw(2900))
    assert set(payload["rows"]) == {f"{c}/{h}j" for c in cs.FEATURES for h in cs.HORIZONS}
    assert all(r["verdict"] in (cs.UP, cs.DOWN, cs.NOTHING) for r in payload["rows"].values())
    last = max(r["last"] for r in payload["rows"].values() if r.get("last"))
    assert last <= "2025-06-30"                                            # sortie dans DEVELOPMENT
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 21 and set(entry["data_hashes"]) == set(synthetic_raw(10))
    monkeypatch.setattr(cs, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(cs.DirtyCode):
        cs.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=synthetic_raw(400))
