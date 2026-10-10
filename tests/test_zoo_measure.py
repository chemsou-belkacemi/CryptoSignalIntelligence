"""Mesure du zoo (docs/ZOO.md § 4) : μ* sous dérive constante, rendements quotidiens et frais calculés à la main,
intervalle par blocs (identique à `calendar_mean_ci`), références de la forme (b) (exposition égale, même composition,
univers), R180 (EV02), même heure (VO11), garde-fous. Données SYNTHÉTIQUES."""
from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.intervals import calendar_mean_ci
from crypto_signal_intelligence.research.zoo import measure as M
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS
from crypto_signal_intelligence.research.zoo.positions import Positions, simuler
from crypto_signal_intelligence.research.zoo.spec import (
    DERIVE_PAIRE,
    EXPOSITION_EGALE,
    MEME_COMPOSITION,
    PAR_ID,
    UNIVERS_ELIGIBLE,
    Poids,
    Signaux,
)

from .zoo_outils import etape, heure, paire, univers_toujours

TEST_1J = PAR_ID["TR10_1j"]


def drift_pair(days: int, g_day: float, start: str = "2019-01-01", symbole: str = "AAAUSDT"):
    g = (1 + g_day) ** (1 / 24) - 1
    return paire(100 * (1 + g) ** np.arange(1, 24 * days + 1), start=start, symbole=symbole, mecha=0.0)


def measure_state(p, unit: int, on: tuple[int, int], stage, test=TEST_1J):
    bars = p.unite(unit)
    state = np.zeros(len(bars), dtype=bool)
    state[on[0]:on[1]] = True
    members = univers_toujours([p.symbole])
    je = M.jours_etape(stage, {p.symbole: p.grille}, members)
    pos = simuler(p.symbole, Signaux(state, ~state), bars, p.grille, np.ones(len(bars), bool), stage, None)
    return M.mesurer_a(test, {p.symbole: pos}, {p.symbole: p.grille}, je), pos, je


def test_mu_star_nul_sous_derive_constante_regle_tenue_300_jours():
    stage = etape("2019-01-01", "2020-03-01")
    p = drift_pair(420, 0.002)
    mesure, pos, je = measure_state(p, 24, (50, 350), stage)
    assert len(pos) == 1 and pos.sortie_h[0] - pos.entree_h[0] == 300 * 24
    assert np.isclose(je.m[p.symbole], 1.002 - 1, rtol=0, atol=1e-13)
    s = mesure.stat(M.NUL, min_transactions=0, min_blocs=2)
    assert abs(s.stat) < 1e-12
    assert abs(s.mu - 0.002) < 1e-12
    assert np.isclose(mesure.e.sum(), 300.0)


def test_mu_star_presque_nul_en_cours_de_journee():
    """Entrée et sortie en cours de journée (4 h) : écart du second ordre de la dérive horaire, déclaré < 1e-8."""
    stage = etape("2019-01-01", "2020-03-01")
    p = drift_pair(420, 0.002)
    mesure, pos, _ = measure_state(p, 4, (301, 1801), stage)
    assert pos.entree_h[0] % 24 != 0
    s = mesure.stat(M.NUL, min_transactions=0, min_blocs=2)
    assert abs(s.stat) < 1e-8


def test_couts_par_cote_et_aller_retour():
    assert M.cout_par_cote("BTCUSDT", "central") == pytest.approx(0.00095)
    assert M.cout_par_cote("SOLUSDT", "central") == pytest.approx(0.00125)
    assert M.cout_par_cote("ETHUSDT", "defavorable") == pytest.approx(0.0014)
    assert M.cout_par_cote("SOLUSDT", "defavorable") == pytest.approx(0.0020)
    assert 2 * M.cout_par_cote("BTCUSDT", "central") == pytest.approx(0.0019)
    assert 2 * M.cout_par_cote("SOLUSDT", "defavorable") == pytest.approx(0.0040)
    assert M.cout_par_cote("SOLUSDT", M.NUL) == 0.0


def test_rendements_quotidiens_et_frais_a_la_main():
    """Position de 22:00 à 02:00 : journée 1 (2 h) et journée 2 (2 h), frais d'entrée sur la valeur d'entrée, frais de
    sortie sur la valeur de sortie, rapportés à la valeur du début de la partie détenue."""
    closes = 100 + np.arange(48, dtype=float)                 # clôture de la bougie k (fin de l'heure k + 1)
    p = paire(closes, symbole="SOLUSDT", mecha=0.0)
    stage = etape("2019-01-01", "2019-01-03")
    t0 = heure("2019-01-01")
    pos = Positions("SOLUSDT", np.array([0]), np.array([t0 + 22]), np.array([t0 + 26]), np.array([0], dtype=np.int8))
    je = M.jours_etape(stage, {"SOLUSDT": p.grille}, univers_toujours(["SOLUSDT"]))
    mesure = M.mesurer_a(TEST_1J, {"SOLUSDT": pos}, {"SOLUSDT": p.grille}, je)
    price = {h: closes[h - t0 - 1] for h in range(t0 + 1, t0 + 49)}
    c = 0.00125
    r1 = price[t0 + 24] / price[t0 + 22] - 1
    r2 = price[t0 + 26] / price[t0 + 24] - 1
    assert mesure.g[:2] == pytest.approx([r1, r2])
    assert mesure.e[:2] == pytest.approx([2 / 24, 2 / 24])
    assert mesure.c["central"][:2] == pytest.approx([c, c * price[t0 + 26] / price[t0 + 24]])
    net = mesure.net("central")
    assert net[:2] == pytest.approx([r1 - c, r2 - c * price[t0 + 26] / price[t0 + 24]])
    trade = mesure.transactions["SOLUSDT"][0]
    ratio = price[t0 + 26] / price[t0 + 22]
    assert trade[1] == pytest.approx(ratio - 1 - c - c * ratio)


def test_ratio_par_blocs_identique_a_calendar_mean_ci():
    rng = np.random.default_rng(3)
    times = pd.Timestamp("2019-01-01", tz="UTC") + pd.to_timedelta(np.sort(rng.integers(0, 400, 600)), unit="D")
    values = rng.normal(0.01, 0.05, 600)
    expected = calendar_mean_ci(values, times, block_days=7)
    days = (times.as_unit("ns").asi8 // HOUR_NS) // DAY_H
    sums = np.bincount(days - days.min(), weights=values)
    counts = np.bincount(days - days.min()).astype(float)
    all_days = np.arange(days.min(), days.max() + 1)
    mean, se, blocks, ci = M.ratio_blocs(sums, counts, all_days, int(days.min()), 7)
    assert [round(ci[0], 6), round(ci[1], 6)] == pytest.approx(expected, abs=1e-6)
    assert mean == pytest.approx(values.mean())
    assert M.p_bilaterale(mean, se, blocks) < 1


def _grilles_hebdo(prices: dict[str, list[float]], start: str = "2019-01-01", weeks: int = 4):
    """Clôtures 1 h constantes par semaine (lundi-clôture à lundi-clôture), valeurs données."""
    grids = {}
    first = heure(start)
    origin = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=1)
    for sym, values in prices.items():
        n = 24 * 7 * (weeks + 2) + 24
        close_h = first - 24 + np.arange(n) + 1
        index = np.clip((close_h - first) // 168, 0, len(values) - 1)
        closes = np.asarray(values, dtype=float)[index]
        grids[sym] = paire(closes, start=str(origin), symbole=sym, mecha=0.0).grille
    return grids, first


def _poids(exec_h, weights, symbols, **extra):
    decisions = (np.asarray(exec_h) - 24) * HOUR_NS + 2_000_000_000
    return Poids(decisions, np.asarray(exec_h), tuple(symbols), np.asarray(weights, dtype=float), **extra)


def test_reference_exposition_egale_a_la_main():
    # 2019-01-01 est un mardi 00:00 = clôture du lundi : semaines de mardi 00:00 à mardi 00:00
    grids, first = _grilles_hebdo({"AAAUSDT": [100, 110, 99, 120, 120], "BBBUSDT": [50, 50, 60, 54, 54]})
    stage = etape("2018-12-31", "2019-01-22")
    exec_h = [first + 168 * k for k in (0, 1, 2)]
    weights = [[0.5, 0.0], [0.0, 0.0], [0.5, 0.5]]
    test = PAR_ID["TR37_1j"]
    universe = np.ones((3, 2), dtype=bool)
    mesure = M.mesurer_b(test, _poids(exec_h, weights, ["AAAUSDT", "BBBUSDT"], univers=universe), grids, stage)
    assert test.reference == EXPOSITION_EGALE
    assert mesure.exposition == pytest.approx(0.5)
    ra = [110 / 100 - 1, 99 / 110 - 1, 120 / 99 - 1]
    rb = [50 / 50 - 1, 60 / 50 - 1, 54 / 60 - 1]
    strategy = [0.5 * ra[0], 0.0, 0.5 * ra[2] + 0.5 * rb[2]]
    reference = [0.25 * (ra[k] + rb[k]) for k in range(3)]
    assert mesure.strategie[M.NUL] == pytest.approx(strategy)
    assert mesure.reference[M.NUL] == pytest.approx(reference)
    assert mesure.exces[M.NUL] == pytest.approx(np.array(strategy) - np.array(reference))


def test_reference_meme_composition_a_la_main():
    grids, first = _grilles_hebdo({"BTCUSDT": [100, 105, 95, 100, 100], "ETHUSDT": [10, 12, 11, 9, 9]})
    stage = etape("2018-12-31", "2019-01-22")
    exec_h = [first + 168 * k for k in (0, 1, 2)]
    inside = np.array([True, False, True])
    a = np.array([[0.0, 1.0]] * 3)        # ETH
    b = np.array([[1.0, 0.0]] * 3)        # BTC
    weights = np.where(inside[:, None], a, b)
    test = PAR_ID["RO02_1j"]
    assert test.reference == MEME_COMPOSITION
    mesure = M.mesurer_b(test, _poids(exec_h, weights, ["BTCUSDT", "ETHUSDT"], composante_a=a, composante_b=b,
                                      dans_a=inside), grids, stage)
    f = 2 / 3
    btc = [105 / 100 - 1, 95 / 105 - 1, 100 / 95 - 1]
    eth = [12 / 10 - 1, 11 / 12 - 1, 9 / 11 - 1]
    strategy = [eth[0], btc[1], eth[2]]
    reference = [f * eth[k] + (1 - f) * btc[k] for k in range(3)]
    assert mesure.descriptif["part_a"] == pytest.approx(f)
    assert mesure.exces[M.NUL] == pytest.approx(np.array(strategy) - np.array(reference))


def test_frais_de_rotation_forme_b():
    grids, first = _grilles_hebdo({"AAAUSDT": [100, 100, 100, 100], "BBBUSDT": [50, 50, 50, 50]})
    stage = etape("2018-12-31", "2019-01-22")
    exec_h = [first + 168 * k for k in (0, 1, 2)]
    weights = [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    test = PAR_ID["XS01_1j"]
    assert test.reference == UNIVERS_ELIGIBLE
    mesure = M.mesurer_b(test, _poids(exec_h, weights, ["AAAUSDT", "BBBUSDT"], univers=np.ones((3, 2), bool)), grids,
                         stage)
    c = 0.00125
    # semaine 1 : achat de A (1 × c) ; semaine 2 : vente de A et achat de B (2 × c) ; semaine 3 : rien, puis
    # liquidation à la dernière clôture de l'étape (c)
    s = mesure.strategie["central"]
    assert s[0] == pytest.approx(-c)
    assert s[1] == pytest.approx((1 - c) * (1 - 2 * c) / (1 - c) - 1, rel=1e-3)
    assert s[-1] < 0


def test_r180_a_la_main():
    days = np.arange(1000, 1005)
    first = np.array([700, 900, 800])          # la 2e paire est cotée depuis moins de 180 jours
    returns = np.array([[0.01, 0.5, -0.02]] * 5)
    out = M.r180(days, ["A", "B", "C"], first, returns)
    assert out == pytest.approx([(0.01 - 0.02) / 2] * 5)


def test_m_derniere_heure_vo11():
    closes = np.full(24 * 120, 100.0)
    for d in range(120):
        closes[24 * d + 23] = 101.0 if d % 2 == 0 else 99.0     # bougie 23:00-24:00 : +1 % puis −1 %
        if d + 1 < 120:
            closes[24 * (d + 1):24 * (d + 1) + 23] = closes[24 * d + 23]
    p = paire(closes, mecha=0.0)
    stage = etape("2019-01-01", "2019-04-30")
    je = M.jours_etape(stage, {p.symbole: p.grille}, univers_toujours([p.symbole]))
    m = M.m_derniere_heure(je, {p.symbole: p.grille})[p.symbole]
    expected = []
    level = 100.0
    for d in range(119):
        target = 101.0 if d % 2 == 0 else 99.0
        expected.append(target / level - 1)
        level = target
    assert m == pytest.approx(24 * np.mean(expected), rel=1e-9)


def _mesure_manuelle(num_by_year: dict[int, float], pairs: dict[str, float]) -> M.MesureA:
    stage = etape("2019-01-01", "2021-01-01")
    days = stage.jours()
    years = pd.to_datetime(days, unit="D").year
    g = np.zeros(len(days))
    for y, v in num_by_year.items():
        g[years == y] = v / (years == y).sum()
    e = np.ones(len(days))
    zero = np.zeros(len(days))
    paires = {s: {"g": v, "e": 100.0, "ref": 0.0, "c_central": 0.0, "c_defavorable": 0.0} for s, v in pairs.items()}
    trades = {"A": np.zeros((200, 0))}
    return M.MesureA("TEST", stage, days, g, e, zero.copy(), zero.copy(), {"central": zero.copy(),
                     "defavorable": zero.copy()}, paires, trades, {}, 0.0)


def test_garde_fou_sans_meilleure_annee_et_sans_meilleure_paire():
    from crypto_signal_intelligence.research.zoo.decide import gardes_decouverte
    concentrated = _mesure_manuelle({2019: 10.0, 2020: -2.0}, {"A": 9.0, "B": -1.0})
    ok, detail = gardes_decouverte(concentrated)
    assert not ok and detail["sans_meilleure_annee"]["annee"] == 2019
    spread = _mesure_manuelle({2019: 5.0, 2020: 4.0}, {"A": 5.0, "B": 4.0})
    ok, detail = gardes_decouverte(spread)
    assert ok and detail["sans_meilleure_paire"]["paire"] == "A"
    one_pair = _mesure_manuelle({2019: 5.0, 2020: 4.0}, {"A": 10.0, "B": -1.0})
    assert not gardes_decouverte(one_pair)[0]


def test_condition_jointe_et_sens():
    from crypto_signal_intelligence.research.zoo.decide import p_decision
    mesure = _mesure_manuelle({2019: 5.0, 2020: 4.0}, {"A": 5.0, "B": 4.0})
    mesure.ref[:] = 0.03                       # dérive plus forte que le rendement : μ* < 0
    p, short, _ = p_decision(mesure)
    assert not short and p == 1.0
    mesure.ref[:] = -0.05                      # μ* > 0 mais rendement net absolu μ ≤ 0 → condition jointe en échec
    mesure.g[:] = -0.01
    p, _, details = p_decision(mesure)
    assert details["central"]["stat"] > 0 and details["central"]["mu"] < 0 and p == 1.0


def test_jours_etape_reference_univers_sous_90_jours():
    stage = etape("2019-01-01", "2019-12-31")
    long_pair = drift_pair(200, 0.001, symbole="AAAUSDT")
    short_pair = drift_pair(30, 0.01, symbole="BBBUSDT", start="2019-03-01")
    je = M.jours_etape(stage, {"AAAUSDT": long_pair.grille, "BBBUSDT": short_pair.grille},
                       univers_toujours(["AAAUSDT", "BBBUSDT"]))
    assert je.jours_membre["BBBUSDT"] < 90
    assert je.m["BBBUSDT"] == pytest.approx(je.u_moyen)
    assert je.m["AAAUSDT"] == pytest.approx(0.001, abs=1e-12)
    _ = MappingProxyType({}), DERIVE_PAIRE


def test_placebos_descriptifs_meme_duree_hors_positions():
    stage = etape("2019-01-01", "2020-03-01")
    p = drift_pair(420, 0.002)
    mesure, pos, je = measure_state(p, 4, (301, 400), stage)
    out = M.placebos_a(mesure, {p.symbole: pos}, {p.symbole: p.grille}, je, tirages=20)
    assert out["transactions"] == 1 and out["placebos"] == 20
    # dérive constante : un placebo de même durée gagne exactement autant que le signal
    assert out["ecart_signal_moins_placebos"] == pytest.approx(0.0, abs=1e-12)
