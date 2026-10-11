"""Indicateurs du zoo : valeurs calculées À LA MAIN (définitions de Wilder, TA-Lib, Kaufman, Hull, Alexander, point et
figure). Aucune donnée réelle."""
from __future__ import annotations

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import indicators as ind

NAN = np.nan


def close(a, b):
    np.testing.assert_allclose(np.asarray(a, float), np.asarray(b, float), rtol=0, atol=1e-12, equal_nan=True)


def test_moyennes_simples_exponentielles_wilder_ponderees():
    close(ind.sma([1, 2, 3, 4, 5], 3), [NAN, NAN, 2, 3, 4])
    close(ind.sma([NAN, 1, 2, 3], 2), [NAN, NAN, 1.5, 2.5])
    close(ind.ema([1, 2, 3, 4, 5], 3), [NAN, NAN, 2, 3, 4])                  # α = 0,5, amorce = SMA 3
    close(ind.rma([1, 2, 3, 4, 5], 2), [NAN, 1.5, 2.25, 3.125, 4.0625])      # α = 1/2
    close(ind.wma([1, 2, 3], 3), [NAN, NAN, 14 / 6])
    close(ind.ema([NAN, NAN, 2, 4, 6], 2), [NAN, NAN, NAN, 3, 3 + 2 / 3 * 3])


def test_vrai_range_atr_extremes_et_croisements():
    h, lo, c = [10, 12, 11], [8, 9, 7], [9, 11, 10]
    close(ind.vrai_range(h, lo, c), [NAN, 3, 4])
    close(ind.atr(h, lo, c, 2), [NAN, NAN, 3.5])
    close(ind.max_glissant([1, 3, 2, 5, 4], 2, inclure=False), [NAN, NAN, 3, 3, 5])
    close(ind.max_glissant([1, 3, 2, 5, 4], 2), [NAN, 3, 3, 5, 5])
    close(ind.min_glissant([1, 3, 2, 5, 4], 2, inclure=False), [NAN, NAN, 1, 2, 2])
    assert ind.croise_dessus([1, 2, 3, 1, 2], [2, 2, 2, 2, 2]).tolist() == [False, False, True, False, False]
    assert ind.croise_dessus([NAN, 3, 1, 3], [2, 2, 2, 2]).tolist() == [False, False, False, True]
    assert ind.croise_dessous([3, 1, 1, 3, 1], [2, 2, 2, 2, 2]).tolist() == [False, True, False, False, True]


def test_aroon_vortex_trix():
    up, down = ind.aroon([1, 3, 2, 5, 4], [0, 2, 1, 4, 3], 2)
    close(up, [NAN, NAN, 50, 100, 50])
    close(down, [NAN, NAN, 0, 50, 0])                # plus bas des fenêtres : 0 (il y a 2), 1 (il y a 1), 1 (il y a 2)
    plus, minus = ind.vortex([10, 12, 11, 13], [8, 9, 7, 10], [9, 11, 10, 12], 2)
    close(plus, [NAN, NAN, 6 / 7, 8 / 7])
    close(minus, [NAN, NAN, 6 / 7, 6 / 7])
    line, signal = ind.trix(np.linspace(100, 120, 80), 3, 2)
    assert np.isnan(line[:7]).all() and not np.isnan(line[7])
    assert (line[10:] > 0).all() and not np.isnan(signal[-1])


def test_kama_a_la_main():
    out = ind.kama([10, 11, 13, 12], 2, 2, 30)
    fast, slow = 2 / 3, 2 / 31
    k2 = 11 + fast ** 2 * (13 - 11)
    sc3 = ((1 / 3) * (fast - slow) + slow) ** 2
    close(out, [NAN, NAN, k2, k2 + sc3 * (12 - k2)])


def test_hull_sans_retard_sur_une_droite_et_macd():
    line = np.arange(40, dtype=float)
    out = ind.hma(line, 4)
    close(out[-5:], line[-5:])
    m, s = ind.macd(line, 3, 6, 2)
    close(m[10:], ind.ema(line, 3)[10:] - ind.ema(line, 6)[10:])
    assert not np.isnan(s[-1])


def test_supertrend_sar_dmi_sens():
    up = np.linspace(100, 200, 120)
    down = np.linspace(200, 100, 120)
    for series, sign in ((up, 1), (down, -1)):
        h, lo = series * 1.01, series * 0.99
        assert ind.supertrend(h, lo, series, 10, 3)[-1] == sign
        assert ind.sar_direction(h, lo)[-1] == sign
        plus, minus, adx = ind.dmi(h, lo, series, 14)
        assert (plus[-1] > minus[-1]) == (sign == 1) and adx[-1] > 25
    crash = np.r_[up, np.linspace(200, 120, 30)]
    assert ind.sar_direction(crash * 1.01, crash * 0.99)[-1] == -1


def test_ichimoku_nuage_applicable_decale():
    h = np.linspace(10, 40, 120)
    lo = h - 1
    out = ind.ichimoku(h, lo, h - 0.5, 9, 26, 52, 26)
    span_a = (out["tenkan"] + out["kijun"]) / 2
    close(out["nuage_a"][26:], span_a[:-26])
    assert np.isnan(out["nuage_haut"][:77]).all() and not np.isnan(out["nuage_haut"][77])


def test_filtre_alexander_a_la_main():
    closes = [100, 96, 101, 106, 110, 104, 103]
    assert ind.filtre_alexander(closes, 0, 0.05).tolist() == [False, False, True, True, True, False, False]
    assert ind.filtre_alexander(closes, 3, 0.05).tolist() == [False] * 7       # départ à l'indice 3 : plus bas 106


def test_point_et_figure_a_la_main():
    levels = np.array([0.5, 1.5, 3.5, -0.5, -1.5, 2.5, 4.5, 5.5, 1.5, -2.5])
    closes = 1.01 ** levels              # niveaux en boîtes de 1 % (log), demi-boîtes : aucun arrondi ambigu
    buy, sell = ind.point_figure(closes, 0.01, 3)
    assert np.flatnonzero(buy).tolist() == [6]
    assert np.flatnonzero(sell).tolist() == [9]


def test_causalite_des_indicateurs_par_troncature():
    rng = np.random.default_rng(5)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    h, lo = c * 1.01, c * 0.99
    funcs = {
        "ema": lambda k: ind.ema(c[:k], 20), "atr": lambda k: ind.atr(h[:k], lo[:k], c[:k], 14),
        "kama": lambda k: ind.kama(c[:k]), "hma": lambda k: ind.hma(c[:k]),
        "st": lambda k: ind.supertrend(h[:k], lo[:k], c[:k]).astype(float),
        "sar": lambda k: ind.sar_direction(h[:k], lo[:k]).astype(float),
        "pf": lambda k: ind.point_figure(c[:k])[0].astype(float),
        "adx": lambda k: ind.dmi(h[:k], lo[:k], c[:k])[2],
    }
    for name, f in funcs.items():
        full = f(400)
        for k in (50, 123, 301):
            np.testing.assert_allclose(f(k), full[:k], equal_nan=True, err_msg=name)


def test_pas_de_valeur_avant_la_fenetre():
    with pytest.raises(ValueError):
        ind.moyenne("WMA", [1, 2, 3], 2)
    assert np.isnan(ind.sma([1, 2], 3)).all()
