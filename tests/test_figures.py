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


def test_triangle_breakout_on_the_bar_that_confirms_the_last_pivot():
    """Relecture C1 : la clôture de la bougie où le 4e pivot devient connu compte déjà comme cassure."""
    pivots = [Pivot(0, "high", 110.0, 2), Pivot(5, "low", 90.0, 7), Pivot(10, "high", 106.0, 12), Pivot(15, "low", 94.0, 17)]
    close = np.full(30, 100.0)
    close[17] = 104.0                                    # ligne haute à 17 : 110 − 0,4 × 17 = 103,2
    [fig] = fg.triangles(pivots, close + 1, close - 1, close)
    assert fig.side == "bull" and fig.detected_at == 17 and fig.entry == pytest.approx(103.2)


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


def _classics(pivots, close, family):
    out = fg.classics(pivots, np.asarray(close, float), np.ones(len(close)), m=2.0)
    return [f for f in out if f.family == family]


def test_inverse_head_and_shoulders_levels_and_confirmation_bar():
    pivots = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 80.0, 13),
              Pivot(15, "high", 100.0, 18), Pivot(20, "low", 91.0, 23)]
    close = np.full(30, 95.0)
    close[25] = 101.0
    [fig] = _classics(pivots, close, "HEAD_SHOULDERS")
    assert fig.side == "bull" and fig.detected_at == 25 and fig.entry == pytest.approx(100.0)
    assert fig.stop == pytest.approx(91.0 - 0.25) and fig.targets == pytest.approx((100 + 20 / 3, 100 + 40 / 3, 120.0))
    close[23] = 101.0                                     # clôture au-dessus dès la bougie qui confirme L3
    assert _classics(pivots, close, "HEAD_SHOULDERS")[0].detected_at == 23


def test_head_and_shoulders_needs_level_shoulders_and_symmetry():
    close = np.full(40, 95.0)
    close[35] = 101.0
    uneven = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 80.0, 13),
              Pivot(15, "high", 100.0, 18), Pivot(20, "low", 96.0, 23)]           # |90 − 96| > 0,25 × 20
    assert not _classics(uneven, close, "HEAD_SHOULDERS")
    lopsided = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(8, "low", 80.0, 11),
                Pivot(20, "high", 100.0, 23), Pivot(30, "low", 91.0, 33)]         # 22 / 8 > 2
    assert not _classics(lopsided, close, "HEAD_SHOULDERS")


def test_bearish_head_and_shoulders_is_mirrored_without_levels():
    pivots = [Pivot(0, "high", 110.0, 3), Pivot(5, "low", 100.0, 8), Pivot(10, "high", 120.0, 13),
              Pivot(15, "low", 100.0, 18), Pivot(20, "high", 109.0, 23)]
    close = np.full(30, 105.0)
    close[25] = 99.0
    [fig] = _classics(pivots, close, "HEAD_SHOULDERS")
    assert fig.side == "bear" and fig.detected_at == 25 and fig.entry is None and not fig.valid


def test_double_bottom_levels():
    pivots = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 90.5, 13)]
    close = np.full(20, 95.0)
    close[14] = 101.0
    [fig] = _classics(pivots, close, "DOUBLE")
    assert fig.side == "bull" and fig.detected_at == 14 and fig.entry == pytest.approx(100.0)
    assert fig.stop == pytest.approx(89.75) and fig.targets[-1] == pytest.approx(110.0)
    unequal = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 92.0, 13)]
    assert not _classics(unequal, close, "DOUBLE")
    close_together = [Pivot(0, "low", 90.0, 2), Pivot(2, "high", 100.0, 4), Pivot(4, "low", 90.5, 6)]
    assert not _classics(close_together, close, "DOUBLE")


def test_flag_levels_and_slow_pole_rejected():
    pivots = [Pivot(0, "low", 80.0, 3), Pivot(5, "high", 100.0, 8), Pivot(9, "low", 92.0, 12)]
    close = np.full(20, 95.0)
    close[14] = 101.0
    [fig] = _classics(pivots, close, "FLAG")
    assert fig.entry == pytest.approx(100.0) and fig.stop == pytest.approx(91.75)
    assert fig.targets == pytest.approx((100 + 20 / 3, 100 + 40 / 3, 120.0))
    slow = [Pivot(0, "low", 80.0, 3), Pivot(15, "high", 100.0, 18), Pivot(19, "low", 92.0, 22)]
    assert not _classics(slow, np.full(30, 95.0), "FLAG")
    deep = [Pivot(0, "low", 80.0, 3), Pivot(5, "high", 100.0, 8), Pivot(9, "low", 88.0, 12)]   # repli de 60 %
    assert not _classics(deep, close, "FLAG")


def test_cup_with_handle_levels():
    pivots = [Pivot(0, "high", 100.0, 3), Pivot(10, "low", 80.0, 13), Pivot(20, "high", 99.0, 23),
              Pivot(23, "low", 94.0, 26)]
    close = np.full(32, 96.0)
    close[28] = 101.0
    [fig] = _classics(pivots, close, "CUP_HANDLE")
    assert fig.side == "bull" and fig.detected_at == 28 and fig.entry == pytest.approx(100.0)
    assert fig.stop == pytest.approx(93.75) and fig.targets[-1] == pytest.approx(119.0)
    deep_handle = pivots[:3] + [Pivot(23, "low", 88.0, 26)]                          # anse sous la moitié
    assert not _classics(deep_handle, close, "CUP_HANDLE")
    off_center = [Pivot(0, "high", 100.0, 3), Pivot(2, "low", 80.0, 5), Pivot(20, "high", 99.0, 23),
                  Pivot(23, "low", 94.0, 26)]
    assert not _classics(off_center, close, "CUP_HANDLE")


def test_breakout_must_come_before_the_next_pivot_is_known():
    pivots = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 90.5, 13),
              Pivot(16, "high", 99.0, 18)]
    close = np.full(25, 95.0)
    close[20] = 101.0                                     # après la confirmation du pivot suivant (18)
    assert not [f for f in _classics(pivots, close, "DOUBLE") if f.anchors == (0, 5, 10)]


def test_flag_pole_uses_the_atr_of_the_last_pivot():
    """Relecture : le mât est comparé à 2 m × ATR de P2 (dernier pivot), pas de P1."""
    pivots = [Pivot(0, "low", 80.0, 3), Pivot(5, "high", 100.0, 8), Pivot(9, "low", 92.0, 12)]
    close = np.full(20, 95.0)
    close[14] = 101.0
    atr = np.ones(20)
    atr[9] = 3.0                                          # 2 × 2 × 3 = 12 ≤ 20 : drapeau gardé
    assert [f.family for f in fg.classics(pivots, close, atr, m=2.0) if f.family == "FLAG"] == ["FLAG"]
    atr[9] = 6.0                                          # 24 > 20 : écarté (avec l'ATR de P1 = 1, il serait gardé)
    assert not [f for f in fg.classics(pivots, close, atr, m=2.0) if f.family == "FLAG"]
    assert fg.classics(pivots, close, np.where(np.arange(20) == 9, 3.0, 1.0), m=2.0)[0].stop == pytest.approx(92.0 - 0.75)


def test_breakout_on_the_bar_where_the_next_pivot_becomes_known_is_kept():
    pivots = [Pivot(0, "low", 90.0, 3), Pivot(5, "high", 100.0, 8), Pivot(10, "low", 90.5, 13),
              Pivot(16, "high", 99.0, 18)]
    close = np.full(25, 95.0)
    close[18] = 101.0
    assert [f.detected_at for f in _classics(pivots, close, "DOUBLE") if f.anchors == (0, 5, 10)] == [18]


def test_non_alternating_pivots_give_no_classic_figure():
    pivots = [Pivot(0, "low", 90.0, 3), Pivot(5, "low", 100.0, 8), Pivot(10, "low", 90.5, 13)]
    close = np.full(20, 95.0)
    close[14] = 101.0
    assert not fg.classics(pivots, close, np.ones(20), m=2.0)
