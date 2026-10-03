"""Bibliothèque § 10 de docs/INDICATEURS.md : niveaux de période, sessions, chiffres ronds, indicateurs classiques,
flux, ICT avancé. Valeurs à la main et causalité (falsifier le futur ne change rien au passé). Données SYNTHÉTIQUES."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.patterns import indicators as ind
from crypto_signal_intelligence.patterns import levels as lv
from crypto_signal_intelligence.patterns.smc import OrderBlock


def walk(n: int = 400, seed: int = 0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.005, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.005, n))
    return o, h, lo, c


def test_sma_ema_rsi_by_hand():
    x = np.arange(1.0, 11.0)
    assert np.isnan(ind.sma(x, 3)[1]) and ind.sma(x, 3)[2] == 2.0
    e = ind.ema(x, 3)
    assert e[2] == 2.0 and e[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
    up = ind.rsi(np.arange(20.0), 14)
    assert np.isnan(up[13]) and up[14] == 100.0
    c = np.array([1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2.0])
    assert ind.rsi(c, 14)[14] == pytest.approx(50.0)


def test_macd_bollinger_keltner_shapes():
    o, h, lo, c = walk()
    line, sig, hist = ind.macd(c)
    assert np.isnan(line[24]) and np.isfinite(line[25]) and np.allclose(hist[60:], (line - sig)[60:])
    low, mid, up = ind.bollinger(c)
    assert mid[19] == pytest.approx(c[:20].mean()) and up[19] - mid[19] == pytest.approx(2 * c[:20].std())
    kl, km, ku = ind.keltner(h, lo, c)
    assert np.allclose((ku - km)[40:], (km - kl)[40:])


def test_stochastic_and_cci_by_hand():
    h = np.arange(10.0, 30.0)
    lo = h - 2
    c = h - 1
    k, d = ind.stochastic(h, lo, c)
    raw = 100 * (c[13] - lo[:14].min()) / (h[:14].max() - lo[:14].min())
    assert np.isnan(k[14]) and k[15] == pytest.approx(np.mean([100 * (c[i] - lo[i - 13:i + 1].min())
                                                                / (h[i - 13:i + 1].max() - lo[i - 13:i + 1].min())
                                                                for i in (13, 14, 15)]))
    assert raw > 0 and np.isfinite(d[17])
    tp = np.full(25, 10.0)
    assert ind.cci(tp, tp, tp)[24] == 0.0                                      # écart nul : 0


def test_ichimoku_cloud_is_the_one_computed_26_bars_earlier():
    o, h, lo, c = walk()
    cloud = ind.ichimoku(h, lo, c)
    i = 120
    assert cloud["senkou_a"][i] == pytest.approx((cloud["tenkan"][i - 26] + cloud["kijun"][i - 26]) / 2)
    assert cloud["chikou_diff"][i] == pytest.approx(c[i] - c[i - 26])


def test_supertrend_follows_a_trend():
    up = np.linspace(100, 200, 100)
    line, trend = ind.supertrend(up + 1, up - 1, up)
    assert trend[-1] == 1 and line[-1] < up[-1]
    down = up[::-1]
    _, trend = ind.supertrend(down + 1, down - 1, down)
    assert trend[-1] == -1


def test_pivot_points_by_hand():
    p = ind.pivot_points(110.0, 90.0, 100.0)
    assert p["P"] == 100.0 and p["R1"] == 110.0 and p["S1"] == 90.0 and p["R2"] == 120.0 and p["S3"] == 70.0


def test_rsi_divergence_on_fractal_lows():
    c = np.full(80, 100.0)
    c[30:36] = [99, 96, 92, 88, 92, 96]                  # premier creux à 88 après une chute rapide (RSI très bas)
    c[36:50] = np.linspace(97, 99, 14)
    c[50:56] = [98.8, 98.4, 98.0, 87.5, 92, 95]          # second creux plus bas (87,5), RSI plus haut
    c[56:] = 96
    assert ind.rsi_divergences(c + 0.5, c - 0.5, c) == [ind.Divergence("bull", 33, 53, 55)]   # connue à 53 + 2


def test_taker_ratio_and_big_activity():
    assert ind.taker_ratio([100.0, 50.0], [60.0, 50.0])[0] == pytest.approx(1.5)
    assert np.isnan(ind.taker_ratio([100.0, 50.0], [60.0, 50.0])[1])
    qv = np.r_[np.ones(20), 5.5, 1.0]
    big = ind.big_activity(qv)
    assert big[20] and not big[:20].any() and not big[21]


def test_equal_highs_and_when_they_are_taken():
    h = np.full(40, 100.0)
    lo = h - 2
    h[10], h[20], h[30] = 105.0, 105.04, 106.0
    c = h - 1
    [eq] = [e for e in ind.equal_levels(h, lo, c) if e.side == "high" and e.first == 10]
    assert eq.second == 20 and eq.level == pytest.approx(105.04) and eq.known_at == 22 and eq.taken_at == 30


def test_breaker_and_mitigation():
    block = OrderBlock(5, "bull", 95.0, 100.0, 7)
    h = np.full(20, 110.0)
    lo = np.full(20, 105.0)
    c = np.full(20, 108.0)
    lo[10] = 99.0                                         # retour dans la zone
    c[14], lo[14] = 94.0, 93.0                             # clôture sous la zone : breaker baissier
    events = ind.block_events([block], h, lo, c)
    assert [(e.kind, e.side, e.at) for e in events] == [("MITIGATION", "bull", 10), ("BREAKER", "bear", 14)]


@pytest.mark.parametrize("cut", [150, 260])
def test_indicators_never_read_the_future(cut):
    o, h, lo, c = walk(seed=4)
    fo, fh, flo, fc = (a.copy() for a in (o, h, lo, c))
    scale = np.random.default_rng(1).uniform(0.7, 1.3, len(c) - cut - 1)
    for a in (fo, fh, flo, fc):
        a[cut + 1:] *= scale
    series = {
        "rsi": lambda o, h, lo, c: ind.rsi(c),
        "macd": lambda o, h, lo, c: ind.macd(c)[2],
        "stoch": lambda o, h, lo, c: ind.stochastic(h, lo, c)[1],
        "cci": lambda o, h, lo, c: ind.cci(h, lo, c),
        "ema200": lambda o, h, lo, c: ind.ema(c, 200),
        "boll": lambda o, h, lo, c: ind.bollinger(c)[2],
        "kelt": lambda o, h, lo, c: ind.keltner(h, lo, c)[0],
        "ichi": lambda o, h, lo, c: ind.ichimoku(h, lo, c)["senkou_b"],
        "super": lambda o, h, lo, c: ind.supertrend(h, lo, c)[0],
    }
    for name, f in series.items():
        a, b = f(o, h, lo, c)[:cut + 1], f(fo, fh, flo, fc)[:cut + 1]
        assert np.allclose(a, b, equal_nan=True), name
    known = [d for d in ind.rsi_divergences(h, lo, c) if d.known_at <= cut]
    fake = [d for d in ind.rsi_divergences(fh, flo, fc) if d.known_at <= cut]
    assert known == fake
    eq_true = [(e.first, e.second, e.level) for e in ind.equal_levels(h, lo, c) if e.known_at <= cut]
    eq_fake = [(e.first, e.second, e.level) for e in ind.equal_levels(fh, flo, fc) if e.known_at <= cut]
    assert eq_true == eq_fake


def hours(days: int, start: str = "2026-01-05") -> pd.DataFrame:      # lundi
    times = pd.date_range(start, periods=24 * days, freq="h", tz="UTC")
    price = 100 + np.arange(len(times)) * 0.1
    return pd.DataFrame({"open_time": times, "open": price, "high": price + 1, "low": price - 1, "close": price + 0.05})


def test_previous_day_week_month_levels():
    h1 = hours(40, start="2026-01-01")
    at = pd.Timestamp("2026-02-03 10:00", tz="UTC")
    levels = lv.previous_levels(h1, at)
    day = h1[(h1["open_time"] >= "2026-02-02") & (h1["open_time"] < "2026-02-03")]
    assert levels["PDH"] == day["high"].max() and levels["PDO"] == day["open"].iloc[0] and levels["PDC"] == day["close"].iloc[-1]
    week = h1[(h1["open_time"] >= "2026-01-26") & (h1["open_time"] < "2026-02-02")]
    assert levels["PWL"] == week["low"].min()
    jan = h1[h1["open_time"] < "2026-02-01"]
    assert levels["PMH"] == jan["high"].max() and levels["PMO"] == jan["open"].iloc[0]
    holed = h1.drop(index=day.index[:1])
    assert lv.previous_levels(holed, at)["PDH"] is None                         # jour incomplet : pas de niveaux


def test_previous_levels_ignore_the_future():
    h1 = hours(40, start="2026-01-01")
    at = pd.Timestamp("2026-02-03 10:00", tz="UTC")
    fake = h1.copy()
    later = fake["open_time"] >= at
    fake.loc[later, ["open", "high", "low", "close"]] *= 3
    assert lv.previous_levels(h1, at) == lv.previous_levels(fake, at)


def test_sessions():
    h1 = hours(2)
    table = lv.sessions(h1)
    asia = table[(table["session"] == "ASIE")].iloc[0]
    part = h1[h1["open_time"] < pd.Timestamp("2026-01-05 08:00", tz="UTC")]
    assert asia["high"] == part["high"].max() and asia["known_at"] == pd.Timestamp("2026-01-05 08:00", tz="UTC")
    assert len(table) == 6


def test_round_levels():
    r = lv.round_levels(67_300)
    assert (r["major_below"], r["major_above"], r["minor_below"], r["minor_above"]) == (60_000, 70_000, 65_000, 70_000)
    r = lv.round_levels(2.3)
    assert (r["major_below"], r["major_above"], r["minor_below"], r["minor_above"]) == (2, 3, 2.0, 2.5)
    r = lv.round_levels(70_000)
    assert r["major_above"] == 70_000 and r["major_below"] == 60_000
    with pytest.raises(ValueError):
        lv.round_levels(0)
