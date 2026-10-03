"""Indicateurs dérivés du prix (patterns/, docs/INDICATEURS.md) : les exemples de la doc, cas construits à la main,
et causalité (falsifier les bougies suivantes ne change rien à ce qui est connu avant)."""
from __future__ import annotations

import numpy as np
import pytest

from crypto_signal_intelligence.patterns import primitives as pr
from crypto_signal_intelligence.patterns import smc
from crypto_signal_intelligence.patterns import volume as vo


def test_atr_wilder():
    h, lo, c = np.full(20, 11.0), np.full(20, 9.0), np.full(20, 10.0)
    a = pr.atr(h, lo, c)
    assert np.isnan(a[:13]).all() and a[13] == pytest.approx(2.0) and a[19] == pytest.approx(2.0)
    h2 = h.copy()
    h2[14] = 14.0                                               # TR 5 à l'indice 14
    assert pr.atr(h2, lo, c)[14] == pytest.approx((13 * 2 + 5) / 14)


def test_fractal_pivots_known_two_bars_later():
    h = np.array([1, 2, 5, 2, 1, 1, 1.0])
    lo = np.array([0.5, 1, 4, 1, 0.2, 0.6, 0.7])
    pivots = pr.fractal_pivots(h, lo)
    assert pr.Pivot(2, "high", 5.0, 4) in pivots and pr.Pivot(4, "low", 0.2, 6) in pivots


def test_zigzag_doc_example():
    h = np.array([10, 11, 12, 11, 10.0])
    lo = np.array([9, 10, 11, 10.5, 9.5])
    pivots = pr.zigzag(h, lo, np.ones(5), m=2.0)
    assert pivots == [pr.Pivot(0, "low", 9.0, 1), pr.Pivot(2, "high", 12.0, 4)]


def test_fvg_doc_example_and_fill():
    h = np.array([10, 12, 13, 12, 11.0])
    lo = np.array([9, 10.5, 11, 10.5, 9.9])
    [gap] = [g for g in smc.fair_value_gaps(h, lo, np.ones(5)) if g.side == "bull"]
    assert (gap.bottom, gap.top, gap.known_at, gap.filled_at) == (10.0, 11.0, 2, 4)
    assert not smc.filled_by(gap, 3) and smc.filled_by(gap, 4)
    assert smc.fair_value_gaps(h, lo, np.full(5, 3.0)) == []      # 1 < 0,5 × 3 : trop petit


def test_order_block_doc_example():
    o = np.array([10, 9.2, 10.0])
    c = np.array([9, 10, 11.5])
    h = np.array([10.2, 10.1, 11.6])
    lo = np.array([8.8, 9.1, 9.9])
    [ob] = smc.order_blocks(o, h, lo, c, np.ones(3))
    assert (ob.side, ob.index, ob.bottom, ob.top, ob.known_at) == ("bull", 0, 8.8, 10.2, 2)
    o2 = o.copy()
    o2[1] = 10.5                                                 # une autre bougie baissière ensuite : pas la dernière
    assert [b.index for b in smc.order_blocks(o2, h, lo, c, np.ones(3))] != [0]


def test_sweep_doc_example():
    lo = np.r_[np.full(20, 100.0), 99.0]
    h = np.r_[np.full(20, 105.0), 102.0]
    c = np.r_[np.full(20, 103.0), 101.0]
    assert smc.liquidity_sweeps(h, lo, c) == [smc.Sweep(20, "bull", 100.0, 20)]
    c[-1] = 99.5                                                 # clôture sous le plus bas : cassure, pas sweep
    assert smc.liquidity_sweeps(h, lo, c) == []


def test_structure_choch_then_bos():
    # Pivot haut 105 (indice 2), pivot bas 95 (indice 6), puis cassures.
    h = np.array([100, 103, 105, 103, 101, 99, 97, 98, 104, 106, 107, 108, 110.0])
    lo = np.array([98, 100, 102, 100, 98, 96, 95, 96, 99, 104, 105, 106, 108.0])
    c = np.array([99, 102, 104, 101, 99, 97, 96, 97, 103, 105.5, 106, 107, 109.0])
    breaks = smc.structure(h, lo, c, np.full(13, 1.0))
    first = breaks[0]
    assert (first.side, first.kind, first.level, first.index) == ("bull", "CHoCH", 105.0, 9)
    assert first.mss is True                                     # FVG haussier entre le pivot et la cassure


def test_premium_discount():
    out = smc.premium_discount(108, 100, 110)
    assert out["zone"] == "premium" and out["middle"] == 105 and out["ote"] == pytest.approx((102.1, 103.8))


def test_cvd_doc_example():
    assert list(vo.volume_delta([10, 10], [7, 3])) == [4, -4]
    assert vo.cvd([10, 10], [7, 3])[-1] == 0


def test_volume_profile_doc_example():
    prof = vo.volume_profile([10, 10], [0, 5], [10, 10])
    assert prof.volume[:25] == pytest.approx(0.2) and prof.volume[25:] == pytest.approx(0.6)
    assert prof.poc == pytest.approx((5.0, 5.2)) and (prof.val, prof.vah) == pytest.approx((5.0, 9.8))


def test_anchored_vwap():
    out = vo.anchored_vwap([2, 4, 6], [0, 2, 4], [1, 3, 5], [1, 1, 2], anchor=1)
    assert np.isnan(out[0]) and out[1] == pytest.approx(3.0) and out[2] == pytest.approx((3 + 2 * 5) / 3)


def random_walk(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.005, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.005, n))
    return o, h, lo, c


@pytest.mark.parametrize("cut", [150, 250, 330])
def test_nothing_known_changes_when_the_future_is_falsified(cut):
    o, h, lo, c = random_walk()
    rng = np.random.default_rng(9)
    fo, fh, flo, fc = (a.copy() for a in (o, h, lo, c))
    scale = rng.uniform(0.7, 1.3, len(c) - cut - 1)
    for a in (fo, fh, flo, fc):
        a[cut + 1:] *= scale
    fh[cut + 1:] = np.maximum.reduce([fh[cut + 1:], fo[cut + 1:], fc[cut + 1:]])
    flo[cut + 1:] = np.minimum.reduce([flo[cut + 1:], fo[cut + 1:], fc[cut + 1:]])
    a_true, a_fake = pr.atr(h, lo, c), pr.atr(fh, flo, fc)
    assert np.allclose(a_true[:cut + 1], a_fake[:cut + 1], equal_nan=True)

    def same(f):
        return [x for x in f(False) if x.known_at <= cut] == [x for x in f(True) if x.known_at <= cut]

    pick = {False: (o, h, lo, c, a_true), True: (fo, fh, flo, fc, a_fake)}
    assert same(lambda k: pr.zigzag(pick[k][1], pick[k][2], pick[k][4], 2.0))
    assert same(lambda k: pr.fractal_pivots(pick[k][1], pick[k][2]))
    assert same(lambda k: [g.__class__(g.index, g.side, g.bottom, g.top, g.known_at, None)
                           for g in smc.fair_value_gaps(pick[k][1], pick[k][2], pick[k][4])])
    assert same(lambda k: smc.order_blocks(*pick[k]))
    assert same(lambda k: smc.liquidity_sweeps(pick[k][1], pick[k][2], pick[k][3]))
    assert same(lambda k: smc.structure(pick[k][1], pick[k][2], pick[k][3], pick[k][4]))
