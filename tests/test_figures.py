"""Détecteur de figures (patterns/figures.py, docs/INDICATEURS.md § 9) : figures construites à la main avec leurs
niveaux de transaction, figures baissières sans niveaux, géométrie invalide, causalité (falsifier le futur ne change
aucune figure déjà détectée)."""
from __future__ import annotations

import numpy as np
import pytest

from crypto_signal_intelligence.patterns import figures as fg
from crypto_signal_intelligence.patterns.primitives import Pivot


def gartley_pivots(sign: float = 1.0):
    x, a = 100.0, 110.0
    b = a - 0.618 * (a - x)
    c = b + 0.6 * (a - b)
    kinds = ("low", "high", "low", "high") if sign > 0 else ("high", "low", "high", "low")
    prices = (x, a, b, c) if sign > 0 else (-x + 300, -a + 300, -b + 300, -c + 300)
    return [Pivot(i * 5, kind, price, i * 5 + 2) for i, (kind, price) in enumerate(zip(kinds, prices, strict=True))]


def test_bullish_gartley_prz_and_levels():
    pivots = gartley_pivots()
    close, atr_values = np.full(40, 106.0), np.full(40, 1.0)
    [fig] = [f for f in fg.harmonics(pivots, close, atr_values) if f.family == "GARTLEY"]
    assert fig.side == "bull" and fig.detected_at == pivots[-1].known_at
    lo, hi = fig.zone
    assert lo == pytest.approx(110 - 1.05 * 0.786 * 10) and hi == pytest.approx(110 - 0.95 * 0.786 * 10)
    assert fig.entry == pytest.approx(hi) and fig.stop == pytest.approx(min(100.0, lo) - 0.25)
    ad = 110 - fig.entry
    assert fig.targets == pytest.approx((fig.entry + 0.382 * ad, fig.entry + 0.618 * ad, 110.0)) and fig.valid


def test_bearish_gartley_is_logged_without_levels():
    pivots = gartley_pivots(-1.0)
    close, atr_values = np.full(40, 194.0), np.full(40, 1.0)
    figs = [f for f in fg.harmonics(pivots, close, atr_values) if f.family == "GARTLEY"]
    assert [f.side for f in figs] == ["bear"] and figs[0].entry is None and not figs[0].valid


def test_price_already_beyond_the_prz_is_dropped():
    pivots = gartley_pivots()
    close = np.full(40, 90.0)
    assert [f for f in fg.harmonics(pivots, close, np.full(40, 1.0)) if f.family == "GARTLEY"] == []


def test_triangle_breakout_levels():
    pivots = [Pivot(0, "high", 110.0, 2), Pivot(5, "low", 90.0, 7), Pivot(10, "high", 106.0, 12), Pivot(15, "low", 94.0, 17)]
    n = 30
    close = np.full(n, 100.0)
    close[21] = 103.0                                    # ligne haute : 110 − 0,4 × 21 = 101,6
    figs = fg.triangles(pivots, close + 1, close - 1, close)
    [fig] = figs
    assert fig.side == "bull" and fig.detected_at == 21 and fig.notes["kind"] == "symetrique"
    height = 110.0 - (90.0 - 0.4 * 5)                   # écart des lignes au premier pivot : 110 − 88
    assert fig.entry == pytest.approx(101.6) and fig.stop == pytest.approx(90 + 0.4 * (21 - 5))
    assert fig.targets == pytest.approx((101.6 + height / 3, 101.6 + 2 * height / 3, 101.6 + height))


def test_trendline_break():
    n = 40
    high, low, close = np.full(n, 100.0), np.full(n, 95.0), np.full(n, 97.0)
    for i, price in ((0, 120.0), (10, 115.0), (20, 110.0)):
        high[i] = price
    pivots = [Pivot(0, "high", 120.0, 3), Pivot(5, "low", 95.0, 8), Pivot(10, "high", 115.0, 13),
              Pivot(15, "low", 95.0, 18), Pivot(20, "high", 110.0, 23)]
    close[26] = 108.0                                    # ligne : 120 − 0,5 × 26 = 107
    [fig] = fg.trendlines(pivots, high, low, close, np.full(n, 1.0))
    assert fig.side == "bull" and fig.detected_at == 26 and fig.entry == pytest.approx(107.0)
    assert fig.stop == pytest.approx(95.0 - 0.25) and fig.valid


def test_invalid_geometry_is_flagged():
    assert not fg.Figure("ICT", "bull", 1, (1,), entry=100.0, stop=99.95, targets=(101.0,)).valid
    assert not fg.Figure("ICT", "bull", 1, (1,), entry=100.0, stop=99.0, targets=(99.5,)).valid


def random_walk(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.006, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.006, n))
    return o, h, lo, c


def test_detector_finds_figures_on_a_random_walk():
    families = {f.family for f in fg.detect(*random_walk(), m=2.0)}
    assert len(families) >= 3                            # le détecteur fonctionne (ce n'est pas une mesure de valeur)


@pytest.mark.parametrize("cut", [600, 1000, 1300])
def test_figures_already_detected_never_change_when_the_future_is_falsified(cut):
    o, h, lo, c = random_walk()
    rng = np.random.default_rng(11)
    fo, fh, flo, fc = (a.copy() for a in (o, h, lo, c))
    scale = rng.uniform(0.6, 1.4, len(c) - cut - 1)
    for a in (fo, fh, flo, fc):
        a[cut + 1:] *= scale
    fh[cut + 1:] = np.maximum.reduce([fh[cut + 1:], fo[cut + 1:], fc[cut + 1:]])
    flo[cut + 1:] = np.minimum.reduce([flo[cut + 1:], fo[cut + 1:], fc[cut + 1:]])
    true = [f for f in fg.detect(o, h, lo, c, m=2.0) if f.detected_at <= cut]
    fake = [f for f in fg.detect(fo, fh, flo, fc, m=2.0) if f.detected_at <= cut]
    assert true and true == fake
