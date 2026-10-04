"""Primes des altcoins en coupe hebdomadaire (docs/XSECTION_PRIMES.md) : primes à la main et règle de qualité, retards
au jour près (dimanche → lundi), écart tiers haut − bas à la main, détection d'un effet connu, causalité, bout en bout.
Données SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import xsection_premium as xp

START = pd.Timestamp("2018-01-01", tz="UTC")                                # un lundi
END = pd.Timestamp("2030-01-01", tz="UTC")


def synthetic_raw(n: int = 900, pairs: int = 15, seed: int = 0, effect: float = 0.0) -> dict[str, pd.DataFrame]:
    """`effect` : la prime coréenne moyenne de la semaine précédente pousse le rendement de la semaine suivante."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(START, periods=n, freq="D", tz="UTC")
    names = [f"A{i:02d}" for i in range(pairs)]
    premium = rng.normal(0.01, 0.01, (n, pairs))
    weekly = pd.DataFrame(premium, index=idx).rolling(7).mean().shift(1).to_numpy()   # connue la veille
    drift = np.nan_to_num(effect * (weekly - 0.01))
    returns = rng.normal(0, 0.02, (n, pairs)) + drift
    close = 10 * np.exp(np.cumsum(returns, axis=0))
    binance = pd.DataFrame(close, index=idx, columns=[f"{c}USDT" for c in names])
    binance["BTCUSDT"] = 30000.0
    rates = pd.DataFrame({"KRW": 1430.0, "USD": 1.1}, index=idx)
    krw = 1430.0 / 1.1
    upbit = pd.DataFrame(close * krw * (1 + premium), index=idx, columns=names)
    coinbase = pd.DataFrame(close * (1 + rng.normal(0, 0.002, (n, pairs))), index=idx, columns=names)
    return {"binance_daily:close": binance, "upbit:close_krw": upbit, "coinbase:close_usd": coinbase, "ecb:per_eur": rates}


def test_premiums_by_hand_and_named_exclusions():
    raw = synthetic_raw(60)
    raw["binance_daily:close"]["STRAXUSDT"] = 1.0
    raw["upbit:close_krw"]["STRAX"] = 0.1 * 1430 / 1.1
    raw["binance_daily:close"]["IOTXUSDT"] = 1.0
    raw["coinbase:close_usd"]["IOTX"] = 1.22
    raw["upbit:close_krw"]["IOTX"] = 1430 / 1.1
    raw["upbit:close_krw"].iloc[5, 0] = raw["binance_daily:close"].iloc[5, 0] * 1430 / 1.1 * 2.5   # vrai pic +150 %
    prem, closes = xp.daily_premiums(raw, END)
    day = raw["upbit:close_krw"].index[3]
    expected = raw["upbit:close_krw"].loc[day, "A01"] / (1430 / 1.1) / raw["binance_daily:close"].loc[day, "A01USDT"] - 1
    assert prem["KR"].loc[day, "A01"] == pytest.approx(expected)
    assert prem["KR"].iloc[5, 0] == pytest.approx(1.5)                      # gardé : les rangs l'absorbent
    cb_day = raw["coinbase:close_usd"].index[4]
    assert prem["CB"].loc[cb_day, "A02"] == pytest.approx(
        raw["coinbase:close_usd"].loc[cb_day, "A02"] / raw["binance_daily:close"].loc[cb_day, "A02USDT"] - 1)
    assert "STRAX" not in prem["KR"].columns and "STRAX" not in closes.columns          # exclue de l'étude
    assert "IOTX" not in prem["CB"].columns and "IOTX" in prem["KR"].columns           # exclue côté Coinbase seulement
    assert "BTC" not in closes.columns and "A00" in closes.columns


def test_ecb_rate_of_friday_is_used_on_sunday():
    raw = synthetic_raw(60)
    idx = raw["ecb:per_eur"].index
    rates = pd.DataFrame({"KRW": 1430.0 + np.arange(len(idx)), "USD": 1.1}, index=idx)
    raw["ecb:per_eur"] = rates[idx.dayofweek < 5]                          # jours ouvrés seulement, taux variable
    prem, _ = xp.daily_premiums(raw, END)
    sunday = idx[idx.dayofweek == 6][2]
    friday = sunday - pd.Timedelta(days=2)
    krw = rates.loc[friday, "KRW"] / 1.1
    expected = raw["upbit:close_krw"].loc[sunday, "A01"] / krw / raw["binance_daily:close"].loc[sunday, "A01USDT"] - 1
    assert prem["KR"].loc[sunday, "A01"] == pytest.approx(expected)


def test_coinbase_sunday_spike_counts_from_monday():
    raw = synthetic_raw(200)
    mondays = pd.date_range(START + pd.Timedelta(days=63), periods=10, freq="7D", tz="UTC")
    base = xp.weekly_variables(xp.daily_premiums(raw, END)[0], mondays)
    spiked = {k: v.copy() for k, v in raw.items()}
    spiked["coinbase:close_usd"].loc[mondays[3] - pd.Timedelta(days=1), "A04"] *= 1.1
    moved = xp.weekly_variables(xp.daily_premiums(spiked, END)[0], mondays)
    diff = (moved["CB_LEVEL"]["A04"] - base["CB_LEVEL"]["A04"]).abs() > 1e-12
    assert diff.idxmax() == mondays[3]


def test_interval_uses_the_declared_level():
    rng = np.random.default_rng(4)
    weeks = pd.DataFrame({"monday": pd.date_range("2019-01-07", periods=300, freq="7D", tz="UTC"), "pairs": 30,
                          "spread": rng.normal(0.002, 0.03, 300), "top_minus_all": 0.0, "bottom_minus_all": 0.0,
                          "rank_corr": 0.0})
    strict, loose = xp.summarize(weeks)["ci_pct"], xp.summarize(weeks, level=0.95)["ci_pct"]
    assert strict[0] < loose[0] and strict[1] > loose[1]


def test_run_return_is_monday_close_to_next_monday_close(settings, monkeypatch):
    monkeypatch.setattr(xp, "code_state", lambda: "abc123")
    raw = synthetic_raw(2900, pairs=20)
    seen: dict = {}
    real = xp.weekly_spreads

    def spy(variable, returns):
        seen.setdefault("r", returns)
        return real(variable, returns)

    monkeypatch.setattr(xp, "weekly_spreads", spy)
    xp.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=raw)
    r, close = seen["r"], raw["binance_daily:close"]["A03USDT"]
    t = r.index[100]
    assert t.dayofweek == 0
    assert r.loc[t, "A03"] == pytest.approx(close.loc[t + pd.Timedelta(days=7)] / close.loc[t] - 1)


def test_sunday_counts_from_the_next_monday_and_monday_only_the_week_after():
    raw = synthetic_raw(200)
    mondays = pd.date_range(START + pd.Timedelta(days=63), periods=10, freq="7D", tz="UTC")
    base = xp.weekly_variables(xp.daily_premiums(raw, END)[0], mondays)
    for shift, first_moved in ((-1, 0), (0, 1)):                          # dimanche T − 1, puis lundi T
        day = mondays[3] + pd.Timedelta(days=shift)
        spiked = {k: v.copy() for k, v in raw.items()}
        spiked["upbit:close_krw"].loc[day, "A02"] *= 1.2
        moved = xp.weekly_variables(xp.daily_premiums(spiked, END)[0], mondays)
        diff = (moved["KR_LEVEL"]["A02"] - base["KR_LEVEL"]["A02"]).abs() > 1e-12
        assert diff.idxmax() == mondays[3 + first_moved]
        jump = (moved["KR_JUMP"]["A02"] - base["KR_JUMP"]["A02"]).abs() > 1e-12
        assert jump.idxmax() == mondays[3 + first_moved]


def test_jump_uses_the_28_days_ending_eight_days_before():
    raw = synthetic_raw(200)
    mondays = pd.date_range(START + pd.Timedelta(days=63), periods=10, freq="7D", tz="UTC")
    prem = xp.daily_premiums(raw, END)[0]["KR"]["A03"]
    t = mondays[4]
    level = prem.loc[t - pd.Timedelta(days=7):t - pd.Timedelta(days=1)].mean()
    before = prem.loc[t - pd.Timedelta(days=35):t - pd.Timedelta(days=8)].mean()
    v = xp.weekly_variables(xp.daily_premiums(raw, END)[0], mondays)
    assert v["KR_LEVEL"].loc[t, "A03"] == pytest.approx(level) and v["KR_JUMP"].loc[t, "A03"] == pytest.approx(level - before)


def test_weekly_spread_by_hand():
    monday = pd.Timestamp("2020-01-06", tz="UTC")
    x = pd.DataFrame([np.arange(12.0)], index=[monday], columns=[f"P{i}" for i in range(12)])
    r = pd.DataFrame([np.arange(12.0) / 100], index=[monday], columns=x.columns)
    weeks = xp.weekly_spreads(x, r)
    # tiers de 4 : bas = 0..3 (moyenne 1,5 %), haut = 8..11 (9,5 %), écart 8 %
    assert weeks.loc[0, "spread"] == pytest.approx(0.08) and weeks.loc[0, "rank_corr"] == pytest.approx(1.0)
    assert xp.weekly_spreads(x.iloc[:, :9], r.iloc[:, :9]).empty               # moins de 10 paires


def test_pair_contributions_add_up_to_the_mean_spread():
    rng = np.random.default_rng(6)
    mondays = pd.date_range("2020-01-06", periods=20, freq="7D", tz="UTC")
    cols = [f"P{i}" for i in range(15)]
    x = pd.DataFrame(rng.normal(size=(20, 15)), index=mondays, columns=cols)
    r = pd.DataFrame(rng.normal(0, 0.05, (20, 15)), index=mondays, columns=cols)
    weeks = xp.weekly_spreads(x, r)
    assert xp.pair_contributions(x, r).sum() == pytest.approx(weeks["spread"].mean())


def test_effect_is_found_and_null_is_not():
    def spreads(effect, seed):
        raw = synthetic_raw(1500, pairs=30, seed=seed, effect=effect)
        prem, closes = xp.daily_premiums(raw, END)
        mondays = pd.date_range(START + pd.Timedelta(days=42), closes.index.max() - pd.Timedelta(days=8), freq="7D", tz="UTC")
        returns = xp.holding_returns(closes, mondays)
        return xp.summarize(xp.weekly_spreads(xp.weekly_variables(prem, mondays)["KR_LEVEL"], returns))
    assert xp.verdict(spreads(8.0, 1)) == xp.UP
    assert xp.verdict(spreads(0.0, 2)) == xp.NOTHING


def test_variables_never_read_after_sunday():
    raw = synthetic_raw(400)
    mondays = pd.date_range(START + pd.Timedelta(days=63), periods=40, freq="7D", tz="UTC")
    cut = mondays[20]
    rng = np.random.default_rng(5)
    fake = {k: v.copy() for k, v in raw.items()}
    for frame in fake.values():
        later = frame.index >= cut
        frame.loc[later] = frame.loc[later] * rng.uniform(0.8, 1.25, frame.loc[later].shape)
    a = xp.weekly_variables(xp.daily_premiums(raw, END)[0], mondays)
    b = xp.weekly_variables(xp.daily_premiums(fake, END)[0], mondays)
    for name in xp.VARIABLES:
        pd.testing.assert_frame_equal(a[name].loc[:cut], b[name].loc[:cut])
        assert not a[name].loc[mondays[21]:].equals(b[name].loc[mondays[21]:])


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    monkeypatch.setattr(xp, "code_state", lambda: "abc123")
    raw = synthetic_raw(2900, pairs=20)
    payload = xp.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=raw)
    assert set(payload["rows"]) == set(xp.VARIABLES)
    assert all("without_leader" in r for r in payload["rows"].values())
    assert all(r["verdict"] in (xp.UP, xp.DOWN, xp.NOTHING) for r in payload["rows"].values())
    assert max(r["last"] for r in payload["rows"].values()) <= "2025-06-23"   # sortie au plus tard le lundi 2025-06-30
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 4 and set(entry["data_hashes"]) == set(raw)
    fake = {k: v.copy() for k, v in raw.items()}
    for frame in fake.values():
        frame.loc[frame.index > pd.Timestamp("2025-06-30", tz="UTC")] *= 3.0     # après DEVELOPMENT : aucun effet
    again = xp.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=fake)
    assert again["rows"] == payload["rows"]
    monkeypatch.setattr(xp, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(xp.DirtyCode):
        xp.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), raw=raw)
