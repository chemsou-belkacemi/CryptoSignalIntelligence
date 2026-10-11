"""Familles CH, ST et AN du zoo : définitions calculées À LA MAIN sur de petites séries construites bougie par bougie
(cas positifs, négatifs et frontières). Chandeliers : TA-Lib n'est pas installé, exemples écrits d'après ses sources
(`ta_CDL*.c`, réglages par défaut). Données SYNTHÉTIQUES, aucun réseau, aucun rendement."""
from __future__ import annotations

import numpy as np
import pytest

from crypto_signal_intelligence.patterns import levels, primitives, smc
from crypto_signal_intelligence.patterns.indicators import block_events
from crypto_signal_intelligence.research.zoo import indicators as ind
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, LATENCE_NS, Bougies, agreger
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import analysts as A
from crypto_signal_intelligence.research.zoo.strategies import candles as CH
from crypto_signal_intelligence.research.zoo.strategies import structure as S

from .zoo_outils import heure


def bougies(o, h, lo, c, *, unite: int = 1, debut: str = "2019-01-07", heures=None) -> Bougies:
    """Bougies construites à la main (2019-01-07 est un lundi)."""
    o, h, lo, c = (np.asarray(v, dtype=float) for v in (o, h, lo, c))
    n = len(c)
    open_h = (heure(debut) + unite * np.arange(n, dtype=np.int64)) if heures is None else np.asarray(heures)
    vol = np.full(n, 1e6)
    return Bougies(unite, open_h, (open_h + unite) * HOUR_NS + LATENCE_NS, o, h, lo, c, vol, vol / 2, vol / 100)


def ctx_de(test_id: str, frame: Bougies, quotidien: Bougies | None = None) -> Contexte:
    test = PAR_ID[test_id]
    return Contexte("AAAUSDT", test.execution_heures, test.params, 0, quotidien=quotidien)


def test_familles_codees_et_registre():
    for test in choisir(["CH", "ST", "AN"]):
        assert test.etat_code == "code", test.id
    # révision du 2026-10-11 : CH02, CH11, CH12, CH15, CH16 IMPOSSIBLE (écart d'ouverture) ; AN05 doublon de ST05
    assert len(choisir(["CH"])) == 29 and len(choisir(["ST"])) == 22 and len(choisir(["AN"])) == 5
    # ST01 : m du registre = m du ZigZag de INDICATEURS.md § 1 pour 4 h et 1 j
    assert PAR_ID["ST01_4h"].params["m_haut"] == primitives.ZIGZAG_M["4h"]
    assert PAR_ID["ST01_1j"].params["m_bas"] == primitives.ZIGZAG_M["1d"]


# --- Chandeliers : fonds construits -------------------------------------------------------------------------------

def fond(sens: int, n: int = 12):
    """Fond de `n` bougies de corps 1 et de plage 2 (ombres de 0,5) : noires descendantes (sens −1, clôtures 99 … 88)
    ou blanches montantes (sens +1, clôtures 101 … 112) ; ouverture = clôture précédente."""
    k = np.arange(n, dtype=float)
    if sens < 0:
        o, c = 100 - k, 99 - k
        return list(o), list(o + 0.5), list(c - 0.5), list(c)
    o, c = 100 + k, 101 + k
    return list(o), list(c + 0.5), list(o - 0.5), list(c)


def avec(base, *bars):
    o, h, lo, c = (list(v) for v in base)
    for bar in bars:
        for col, value in zip((o, h, lo, c), bar, strict=True):
            col.append(value)
    return o, h, lo, c


def remplace(base, index, bar):
    o, h, lo, c = (list(v) for v in base)
    o[index], h[index], lo[index], c[index] = bar
    return o, h, lo, c


def detecte(nom, ohlc):
    return np.flatnonzero(CH.figure_talib(nom, *ohlc)).tolist()


def test_moyennes_des_reglages_talib():
    base = fond(-1)
    x = CH._Chandeliers(*base)
    # corps 1, plage 2 partout : BodyShort = 1 (10 précédentes), BodyDoji = 0,1 × 2, Near = 0,2 × 2, Equal = 0,05 × 2
    assert np.isnan(x.moyenne("BodyShort")[9]) and x.moyenne("BodyShort")[10] == pytest.approx(1.0)
    assert x.moyenne("BodyDoji")[11] == pytest.approx(0.2)
    assert np.isnan(x.moyenne("Near")[4]) and x.moyenne("Near")[5] == pytest.approx(0.4)
    assert x.moyenne("Equal")[7] == pytest.approx(0.1)
    assert x.moyenne("ShadowLong")[3] == pytest.approx(1.0)       # période 0 : le corps de la bougie elle-même
    # plage « ombres » : moitié de la somme des deux ombres
    assert x.plage(CH.OMBRES)[0] == pytest.approx(1.0)


def test_marteau():
    base = fond(-1)
    # corps 0,2 < 1 ; ombre basse 0,8 > corps ; ombre haute 0,05 < 0,2 ; bas du corps 87,8 ≤ 87,5 + 0,4
    assert detecte("CDLHAMMER", avec(base, (88, 88.05, 87.0, 87.8))) == [12]
    assert detecte("CDLHAMMER", avec(base, (88, 88.3, 87.0, 87.8))) == []         # ombre haute 0,3 ≥ 0,2
    assert detecte("CDLHAMMER", avec(base, (88, 88.35, 87.0, 88.3))) == []        # corps 88 > plus bas + Near
    assert detecte("CDLHAMMER", avec(base, (88, 88.05, 87.85, 87.8))) == []       # ombre basse 0,05 ≤ corps


def test_marteau_inverse_exige_un_ecart():
    base = fond(-1)
    assert detecte("CDLINVERTEDHAMMER", avec(base, (87.7, 88.6, 87.65, 87.9))) == [12]
    # ouverture = clôture précédente : le corps touche le précédent, aucune figure (cas du marché sous H0)
    assert detecte("CDLINVERTEDHAMMER", avec(base, (88.0, 88.9, 87.95, 87.9))) == []


def test_avalement_haussier_cas_80_et_frontiere():
    base = fond(-1)
    assert detecte("CDLENGULFING", avec(base, (88, 89.6, 87.9, 89.5))) == [12]    # clôture > ouverture précédente
    assert detecte("CDLENGULFING", avec(base, (88, 89.1, 87.9, 89.0))) == []      # corps égaux aux deux bouts
    assert detecte("CDLENGULFING", avec(base, (88, 88.8, 87.9, 88.7))) == []      # n'avale pas


def test_harami_et_harami_en_croix():
    base = remplace(fond(-1), 11, (89, 89.5, 86.5, 87))          # 1re bougie : noire, corps 2 > 1
    harami = avec(base, (87, 87.6, 86.9, 87.5))                  # corps 0,5 ≤ 1,1, contenu (égal en bas : cas 80)
    assert detecte("CDLHARAMI", harami) == [12]
    assert detecte("CDLHARAMICROSS", harami) == []               # corps 0,5 > doji 0,21
    croix = avec(base, (87, 87.2, 86.9, 87.1))                   # corps 0,1 ≤ 0,1 × 2,1
    assert detecte("CDLHARAMICROSS", croix) == [12] and detecte("CDLHARAMI", croix) == [12]
    assert detecte("CDLHARAMI", avec(base, (87, 89.6, 86.9, 89.5))) == []         # dépasse le corps


def test_doji_libellule():
    base = fond(-1)
    assert detecte("CDLDRAGONFLYDOJI", avec(base, (88, 88.1, 87.0, 88.05))) == [12]
    assert detecte("CDLDRAGONFLYDOJI", avec(base, (88, 88.4, 87.0, 88.05))) == []   # ombre haute 0,35
    assert detecte("CDLDRAGONFLYDOJI", avec(base, (88, 88.1, 87.95, 88.05))) == []  # ombre basse 0,05


def test_trois_soldats_blancs():
    base = fond(+1)
    bars = [(112, 113.55, 111.9, 113.5), (113.5, 115.05, 113.4, 115.0), (115, 116.55, 114.9, 116.5)]
    assert detecte("CDL3WHITESOLDIERS", avec(base, *bars)) == [14]
    longue_meche = bars[:2] + [(115, 117.0, 114.9, 116.5)]                  # ombre haute 0,5 sur la 3e
    assert detecte("CDL3WHITESOLDIERS", avec(base, *longue_meche)) == []


def test_trois_interieurs_strict():
    base = fond(-1)
    bars = [(88, 88.5, 85.5, 86), (86.5, 86.9, 86.4, 86.8), (86.8, 88.3, 86.7, 88.2)]
    assert detecte("CDL3INSIDE", avec(base, *bars)) == [14]
    colle = [bars[0], (86, 86.9, 85.9, 86.8), bars[2]]                     # 2e ouverte à la clôture de la 1re
    assert detecte("CDL3INSIDE", avec(base, *colle)) == []


def test_trois_exterieurs_strict():
    base = fond(-1)
    bars = [(88, 88.1, 87.4, 87.5), (87.4, 88.3, 87.3, 88.2), (88.2, 88.7, 88.1, 88.6)]
    assert detecte("CDL3OUTSIDE", avec(base, *bars)) == [14]
    colle = [bars[0], (87.5, 88.3, 87.4, 88.2), bars[2]]
    assert detecte("CDL3OUTSIDE", avec(base, *colle)) == []


def test_ceinture_haussiere():
    base = fond(-1)
    assert detecte("CDLBELTHOLD", avec(base, (88, 89.8, 87.95, 89.5))) == [12]
    assert detecte("CDLBELTHOLD", avec(base, (88, 89.8, 87.7, 89.5))) == []       # ombre basse 0,3
    assert detecte("CDLBELTHOLD", avec(base, (88, 89.0, 87.95, 88.9))) == []      # corps 0,9 pas long


def test_trois_methodes_ascendantes():
    base = fond(+1)
    bars = [(112, 114.5, 111.5, 114), (114, 114.1, 113.6, 113.7), (113.7, 113.8, 113.3, 113.4),
            (113.4, 113.5, 113.0, 113.1), (113.2, 114.7, 113.15, 114.6)]
    assert detecte("CDLRISEFALL3METHODS", avec(base, *bars)) == [16]
    colle = bars[:4] + [(113.1, 114.7, 113.05, 114.6)]                    # 5e ouverte à la clôture précédente
    assert detecte("CDLRISEFALL3METHODS", avec(base, *colle)) == []


def test_pigeon_voyageur():
    base = fond(-1)
    bars = [(88, 88.5, 85.5, 86), (87.5, 87.6, 86.9, 87.0)]
    assert detecte("CDLHOMINGPIGEON", avec(base, *bars)) == [13]
    assert detecte("CDLHOMINGPIGEON", avec(base, bars[0], (86, 86.1, 85.4, 85.6))) == []


def test_plus_bas_identiques():
    base = fond(-1)
    assert detecte("CDLMATCHINGLOW", avec(base, (88, 88.2, 87.8, 87.95))) == [12]  # Equal = 0,05 × 2 = 0,1
    assert detecte("CDLMATCHINGLOW", avec(base, (88, 88.2, 87.8, 87.85))) == []


def test_lookback_de_talib():
    # avalement dès la 2e bougie : TA-Lib ne rend rien avant son lookback (2)
    ohlc = ([101, 100, 101.5, 101], [101.2, 101.6, 101.6, 102.1], [99.9, 99.9, 100.9, 100.9], [100, 101.5, 101, 102])
    assert CH._engulfing(CH._Chandeliers(*ohlc)).tolist() == [False, True, False, True]
    assert detecte("CDLENGULFING", ohlc) == [3]


def test_figure_tendance_prealable():
    hammer = avec(fond(-1), (88, 88.05, 87.0, 87.8))
    frame = bougies(*hammer)
    out = CH.figure(frame, ctx_de("CH01_1h", frame))
    assert np.flatnonzero(out.entree).tolist() == [12] and out.sortie is None
    # même marteau après une montée : clôture au-dessus de la SMA 10, aucune entrée
    o, h, lo, c = fond(+1)
    up = bougies(*avec((o, h, lo, c), (110.8, 110.95, 110.0, 110.9)))       # marteau (écart) au-dessus de la SMA
    assert CH.figure_talib("CDLHAMMER", up.ouverture, up.haut, up.bas, up.cloture)[12]
    assert not CH.figure(up, ctx_de("CH01_1h", up)).entree.any()
    # continuation (CH10) : tendance haussière à la 1re bougie de la figure (t − 2)
    soldiers = bougies(*avec(fond(+1), (112, 113.55, 111.9, 113.5), (113.5, 115.05, 113.4, 115.0),
                             (115, 116.55, 114.9, 116.5)))
    assert np.flatnonzero(CH.figure(soldiers, ctx_de("CH10_1h", soldiers)).entree).tolist() == [14]
    # avalement (2 bougies) : la tendance se lit à t − 1
    engulf = bougies(*avec(fond(-1), (88, 89.6, 87.9, 89.5)))
    assert np.flatnonzero(CH.figure(engulf, ctx_de("CH03_1h", engulf)).entree).tolist() == [12]


# --- CH20 Heikin Ashi, CH21 Renko --------------------------------------------------------------------------------

def test_heikin_ashi_a_la_main():
    o = [10, 10, 8.5, 8.8, 10.4, 11.8]
    h = [11, 10.5, 9, 10.5, 12, 13]
    lo = [9, 8, 8, 8.7, 10.3, 11.7]
    c = [10, 8.5, 8.8, 10.4, 11.8, 12.9]
    ha_o, ha_h, ha_l, ha_c = CH.bougies_heikin_ashi(o, h, lo, c)
    assert ha_c.tolist() == pytest.approx([10, 9.25, 8.575, 9.6, 11.125, 12.35])
    assert ha_o.tolist() == pytest.approx([10, 10, 9.625, 9.1, 9.35, 10.2375])
    assert ha_l[3] == pytest.approx(8.7) and ha_h[4] == pytest.approx(12)
    frame = bougies(o, h, lo, c)
    out = CH.heikin_ashi(frame, ctx_de("CH20_1h", frame))
    # 3 : haussière avec mèche basse ; 4 : 1re haussière sans mèche depuis la baissière de 2 ; 5 : pas la 1re
    assert np.flatnonzero(out.entree).tolist() == [4]
    assert np.flatnonzero(out.sortie).tolist() == [1, 2]


def test_renko_briques_a_la_main():
    closes = [10, 10.5, 11.2, 12.1, 10.9, 9.8, 10.5, 12.0, 13.5, 14.2, 11.9, 10.9]
    entry, down = CH.briques_renko(closes, np.ones(len(closes)))
    # 2, 3 : briques haussières sans baissière avant ; 5 : baissière (2 boîtes sous le haut 12) ; 7, 8 : 1re et 2e
    # haussières après elle ; 10 et 11 : baissières
    assert np.flatnonzero(entry).tolist() == [8]
    assert np.flatnonzero(down).tolist() == [5, 10, 11]
    # plusieurs briques dans la même bougie : la 2e haussière y est formée
    entry, down = CH.briques_renko([10, 8.9, 12.3], np.ones(3))
    assert entry.tolist() == [False, False, True] and down.tolist() == [False, True, False]
    # boîte inconnue : aucune brique
    entry, down = CH.briques_renko([10, 8.9, 12.3], np.full(3, np.nan))
    assert not entry.any() and not down.any()


def test_boite_renko_connue_au_premier_du_mois():
    rng = np.random.default_rng(3)
    n = 24 * 70                                                     # 2019-01-01 → 2019-03-11
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    opens = np.r_[closes[0], closes[:-1]]
    h1 = bougies(opens, np.maximum(opens, closes) * 1.003, np.minimum(opens, closes) * 0.997, closes,
                 debut="2019-01-01")
    daily = agreger(h1, DAY_H)
    atr = ind.atr(daily.haut, daily.bas, daily.cloture, 14)
    box = CH.boite_renko(h1, daily, 14)
    jan, feb, mar = ((h1.ouverture_h >= heure(m)) for m in ("2019-01-01", "2019-02-01", "2019-03-01"))
    assert np.isnan(box[jan & ~feb]).all()                          # aucune journée avant le 1er janvier
    assert (box[feb & ~mar] == atr[30]).all()                       # ATR du 31 janvier
    assert (box[mar] == atr[58]).all()                              # ATR du 28 février
    out = CH.renko(h1, Contexte("AAAUSDT", 1, PAR_ID["CH21_1h"].params, 0, quotidien=daily))
    assert out.entree.dtype == bool and len(out.sortie) == n


# --- ST : ZigZag, Dow, Fibonacci -----------------------------------------------------------------------------------

def test_zigzag_exemple_et_copie_identique():
    high = [10, 11, 12, 11, 10]
    low = [9, 10, 11, 10.5, 9.5]
    piv = S.zigzag_rapide(high, low, np.ones(5), 2.0)          # INDICATEURS.md § 1, exemple vérifiable
    assert piv.indice.tolist() == [0, 2] and piv.sens.tolist() == [-1, 1] and piv.connu.tolist() == [1, 4]
    for seed in range(8):
        rng = np.random.default_rng(seed)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 2500)))
        h = c * (1 + np.abs(rng.normal(0, 0.004, 2500)))
        lo = c * (1 - np.abs(rng.normal(0, 0.004, 2500)))
        atr = primitives.atr(h, lo, c)
        assert np.array_equal(S.atr_wilder(h, lo, c), atr, equal_nan=True)
        for m in (2.0, 2.5, 3.0):
            ref = primitives.zigzag(h, lo, atr, m)
            got = S.zigzag_rapide(h, lo, atr, m)
            assert [(p.index, 1 if p.kind == "high" else -1, p.price, p.known_at) for p in ref] == \
                list(zip(got.indice.tolist(), got.sens.tolist(), got.prix.tolist(), got.connu.tolist(), strict=True))


def piv(*items):
    """Pivots fabriqués : (sens, prix, bougie de confirmation)."""
    sens = np.array([i[0] for i in items], dtype=np.int8)
    return sens, np.array([i[1] for i in items], dtype=float), np.array([i[2] for i in items], dtype=np.int64)


def test_dow_a_la_main():
    sens, prix, connu = piv((-1, 10, 3), (1, 20, 6), (-1, 12, 9), (1, 25, 12), (-1, 15, 15), (1, 22, 18),
                            (-1, 11, 21))
    state, out = S._dow(24, sens, prix, connu)
    assert np.flatnonzero(state).tolist() == list(range(12, 18))       # 25 > 20 et 12 > 10, puis 22 < 25
    assert np.flatnonzero(out).tolist() == [21]                         # 11 < 15


def test_fibonacci_a_la_main():
    sens, prix, connu = piv((-1, 100, 2), (1, 120, 5), (-1, 104, 12))
    n = 14
    low = np.full(n, 118.0)
    close = np.full(n, 118.5)
    low[4], close[4] = 110, 115          # contact AVANT la confirmation du sommet : ignoré
    low[5], close[5] = 113, 114
    low[6], close[6] = 112.0, 112.2      # contact du niveau 38,2 % (112,36), clôture dessous
    low[7], close[7] = 112.5, 112.9      # 1re clôture au-dessus : signal
    close[8], close[9] = 119, 120.0      # sortie : clôture ≥ H
    entry, out = S._fibonacci(low, close, sens, prix, connu, 0.382)
    assert np.flatnonzero(entry).tolist() == [7]
    assert out[9] and not out[8] and not out[:7].any()
    # contact et clôture au-dessus dans la même bougie : signal ; un seul par jambe
    low2, close2 = low.copy(), close.copy()
    low2[6], close2[6] = 112.0, 113.0
    entry, _ = S._fibonacci(low2, close2, sens, prix, connu, 0.382)
    assert np.flatnonzero(entry).tolist() == [6]
    # nouveau pivot (bas) confirmé avant la clôture au-dessus : la jambe est close
    sens3, prix3, connu3 = piv((-1, 100, 2), (1, 120, 5), (-1, 104, 7))
    entry, _ = S._fibonacci(low, close, sens3, prix3, connu3, 0.382)
    assert not entry.any()
    # sortie sous le creux de la jambe
    close4 = close.copy()
    close4[8] = 99.9
    _, out = S._fibonacci(low, close4, sens, prix, connu, 0.382)
    assert out[8]


# --- ST06-ST10, ST14 : niveaux de la veille --------------------------------------------------------------------

def deux_jours(manquante: int | None = None):
    """Jour 1 (2019-01-07) : H 110 (10 h), B 90 (15 h), C 100 ; jour 2 : bougies posées heure par heure."""
    n = 48
    o, c = np.full(n, 100.0), np.full(n, 100.0)
    h, lo = np.full(n, 100.5), np.full(n, 99.5)
    h[10], lo[15] = 110, 90
    day2 = {2: (94.0, 95.0), 3: (89.5, 91.0), 4: (93.0, 96.0), 6: (None, 105.6), 7: (None, 110.5),
            8: (None, 111.5), 9: (None, 120.5)}
    for hour, (low, close) in day2.items():
        i = 24 + hour
        c[i] = close
        if low is not None:
            lo[i] = low
    o[1:] = c[:-1]
    h = np.maximum(h, np.maximum(o, c) + 0.05)
    lo = np.minimum(lo, np.minimum(o, c) - 0.05)
    hours = heure("2019-01-07") + np.arange(n, dtype=np.int64)
    keep = np.ones(n, dtype=bool)
    if manquante is not None:
        keep[manquante] = False
    frame = bougies(o[keep], h[keep], lo[keep], c[keep], heures=hours[keep])
    return frame, agreger(frame, DAY_H)


def test_niveaux_des_pivots():
    p = S.niveaux_pivots("classique", np.array([110.0]), np.array([90.0]), np.array([100.0]))
    assert (p["P"][0], p["R1"][0], p["S1"][0], p["R2"][0]) == pytest.approx((100, 110, 90, 120))
    c = S.niveaux_pivots("camarilla", np.array([110.0]), np.array([90.0]), np.array([100.0]))
    assert (c["L3"][0], c["H3"][0], c["H4"][0]) == pytest.approx((94.5, 105.5, 111))
    w = S.niveaux_pivots("woodie", np.array([110.0]), np.array([90.0]), np.array([100.0]))
    assert (w["P"][0], w["S1"][0]) == pytest.approx((100, 90))


@pytest.mark.parametrize("test_id,heure_entree", [("ST06_1h", 3), ("ST07_1h", 7), ("ST08_1h", 2), ("ST09_1h", 8),
                                                  ("ST10_1h", 3), ("ST14_1h", 7)])
def test_pivots_journaliers_a_la_main(test_id, heure_entree):
    frame, daily = deux_jours()
    fn = PAR_ID[test_id].strategie_fonction()
    out = fn(frame, ctx_de(test_id, frame, daily))
    assert np.flatnonzero(out.entree).tolist() == [24 + heure_entree]       # rien le jour 1 (pas de veille)
    hour = frame.ouverture_h % DAY_H
    eod = hour >= 22
    day2 = np.arange(48) >= 24
    target = {"ST06_1h": 100.0, "ST07_1h": 120.0, "ST08_1h": 105.5, "ST10_1h": 100.0}.get(test_id)
    expected = eod if target is None else eod | (day2 & (frame.cloture >= target - 1e-9))
    assert out.sortie.tolist() == expected.tolist()
    # veille incomplète (une heure manquante) : aucun niveau, aucun signal
    frame, daily = deux_jours(manquante=5)
    assert not fn(frame, ctx_de(test_id, frame, daily)).entree.any()


def test_plus_haut_semaine_precedente():
    n = 42 * 3
    c = np.full(n, 100.0)
    h = np.full(n, 101.0)
    h[10] = 120.0                         # plus haut de la semaine 1
    c[42 + 5], c[42 + 9] = 121.0, 122.0   # semaine 2 : 1re clôture au-dessus en 5, pas en 9
    h = np.maximum(h, c + 0.5)
    o = np.r_[100.0, c[:-1]]
    frame = bougies(o, h, np.minimum(o, c) - 0.5, c, unite=4)
    out = S.plus_haut_semaine(frame, ctx_de("ST15_4h", frame))
    assert np.flatnonzero(out.entree).tolist() == [42 + 5]
    position = np.arange(n) % 42
    assert np.flatnonzero(out.sortie).tolist() == np.flatnonzero(position >= 40).tolist()   # dimanche 16 h et 20 h
    keep = np.ones(n, dtype=bool)
    keep[20] = False                      # semaine 1 incomplète : aucun niveau
    partial = frame.filtre(keep)
    assert not S.plus_haut_semaine(partial, ctx_de("ST15_4h", partial)).entree.any()


# --- ST11, ST12, ST13 ----------------------------------------------------------------------------------------------

def journalier(closes, highs):
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return bougies(o, np.maximum(h, np.maximum(o, c)), np.minimum(o, c) - 1, c, unite=DAY_H, debut="2019-01-01")


def test_plus_haut_annuel_et_historique():
    n = 420
    c, h = np.full(n, 100.0), np.full(n, 101.0)
    h[30] = 130.0
    c[380], h[380] = 125.0, 126.0
    c[400], h[400] = 127.0, 127.5
    c[410], h[410] = 131.0, 131.5
    frame = journalier(c, h)
    annual = S.plus_haut_annuel(frame, ctx_de("ST11_1j", frame))
    assert np.flatnonzero(annual.entree).tolist() == [400, 410]     # 380 : sous le 130 de la bougie 30
    historic = S.plus_haut_historique(frame, ctx_de("ST12_1j", frame))
    assert np.flatnonzero(historic.entree).tolist() == [410]
    # moins de 365 bougies avant : aucun plus haut historique
    c2, h2 = np.full(n, 100.0), np.full(n, 101.0)
    c2[300], h2[300] = 140.0, 140.5
    c2[365], h2[365] = 141.0, 141.5
    frame2 = journalier(c2, h2)
    assert np.flatnonzero(S.plus_haut_historique(frame2, ctx_de("ST12_1j", frame2)).entree).tolist() == [365]


def test_niveaux_ronds():
    assert S.niveau_majeur_au_dessus(np.array([67300.0, 0.3, 9.5, 99.0, 100.0, -1.0]))[:5].tolist() == \
        pytest.approx([70000, 0.3, 10, 100, 100])
    prices = np.exp(np.random.default_rng(1).uniform(-14, 12, 3000))
    ref = [levels.round_levels(float(p))["major_above"] for p in prices]
    assert S.niveau_majeur_au_dessus(prices).tolist() == ref
    closes = [67300, 69999, 70001, 95, 99, 101, 105, 9.5, 10.2, 0.3, 0.31]
    frame = journalier(closes, closes)
    out = S.niveau_rond(frame, ctx_de("ST13_4h", frame))
    # 69 999 → 70 001 ; 99 → 101 croise 100 ; 9,5 → 10,2 croise 10 ; 0,3 (niveau égal) → 0,31
    assert np.flatnonzero(out.entree).tolist() == [2, 5, 8, 10]


# --- AN ---------------------------------------------------------------------------------------------------------

def test_ote_a_la_main():
    sens, prix, connu = piv((-1, 100, 2), (1, 120, 5))
    n = 11
    high, low, close = np.full(n, 119.0), np.full(n, 115.0), np.full(n, 116.0)
    bars = {6: (112, 108, 109), 7: (109, 106, 107), 8: (108.5, 106.5, 108), 9: (120.6, 108, 120.5)}
    for t, (hh, ll, cc) in bars.items():
        high[t], low[t], close[t] = hh, ll, cc
    # zone : 62 % = 107,6 ; 79 % = 104,2 ; contact en 7 (clôture dessous), 1re clôture au-dessus en 8
    entry, out = A._ote(high, low, close, sens, prix, connu, 0.62, 0.79)
    assert np.flatnonzero(entry).tolist() == [8] and np.flatnonzero(out).tolist() == [9]
    # bougie entièrement sous la zone : pas de contact ; la suivante coupe la zone et clôture au-dessus : signal
    high[6:9], low[6:9], close[6:9] = [112, 104, 108.5], [108, 102, 103], [109, 103, 108]
    entry, _ = A._ote(high, low, close, sens, prix, connu, 0.62, 0.79)
    assert np.flatnonzero(entry).tolist() == [8]
    # jamais de contact (plus bas 108) : aucun signal
    entry, _ = A._ote(np.full(n, 119.0), np.full(n, 108.0), np.full(n, 110.0), sens, prix, connu, 0.62, 0.79)
    assert not entry.any()


def test_elliott_a_la_main():
    n = 14
    close = np.full(n, 112.0)
    close[8:13] = [115, 119, 121, 130, 132.5]
    sens, prix, connu = piv((-1, 100, 2), (1, 120, 5), (-1, 110, 8))     # vague 2 = 50 %
    entry, out = A._elliott(close, sens, prix, connu, 0.382, 0.786, 1.618)
    assert np.flatnonzero(entry).tolist() == [10]
    assert np.flatnonzero(out).tolist() == [12]                          # 132,5 ≥ 100 + 1,618 × 20 = 132,36
    for p2 in (102.0, 114.0):                                            # vague 2 de 90 % ou de 30 % : rien
        sens2, prix2, connu2 = piv((-1, 100, 2), (1, 120, 5), (-1, p2, 8))
        assert not A._elliott(close, sens2, prix2, connu2, 0.382, 0.786, 1.618)[0].any()
    stop = close.copy()
    stop[11] = 109.9
    assert A._elliott(stop, sens, prix, connu, 0.382, 0.786, 1.618)[1][11]
    # nouveau pivot (haut) confirmé avant la clôture au-dessus de P1 : rien
    sens3, prix3, connu3 = piv((-1, 100, 2), (1, 120, 5), (-1, 110, 8), (1, 119, 9))
    assert not A._elliott(close, sens3, prix3, connu3, 0.382, 0.786, 1.618)[0].any()


def breaker_bougies(retour_close: float = 101.8):
    """20 doji (ATR 1), bougie haussière 20 (zone [99,8 ; 101,2]), déplacement baissier (21, 22 : OB baissier connu
    en 22), clôture au-dessus de la zone en 24 (breaker haussier), 1er retour en 26, clôture sous la zone en 27."""
    bars = [(100, 100.5, 99.5, 100)] * 20 + [
        (100, 101.2, 99.8, 101), (101, 101.1, 99.4, 99.5), (99.5, 99.6, 98.4, 98.5), (98.5, 100.1, 98.4, 100),
        (100, 101.6, 99.9, 101.5), (101.5, 102.6, 101.4, 102.5), (102.5, 102.6, 101.0, retour_close),
        (retour_close, 101.9, 99.6, 99.7)]
    return bougies(*(list(col) for col in zip(*bars, strict=True)), unite=4)


def test_breaker_a_la_main():
    frame = breaker_bougies()
    atr = S.atr_wilder(frame.haut, frame.bas, frame.cloture)
    assert atr[20] == pytest.approx((13 + 1.4) / 14)
    index, side, bottom, top, known = A.order_blocks_rapides(frame.ouverture, frame.haut, frame.bas, frame.cloture,
                                                             atr)
    assert (20, -1, 99.8, 101.2, 22) in list(zip(index.tolist(), side.tolist(), bottom.tolist(), top.tolist(),
                                                 known.tolist(), strict=True))
    ref = smc.order_blocks(frame.ouverture, frame.haut, frame.bas, frame.cloture, atr)
    assert [(b.index, b.known_at) for b in ref] == list(zip(index.tolist(), known.tolist(), strict=True))
    breakers = [e for e in block_events(ref, frame.haut, frame.bas, frame.cloture) if e.kind == "BREAKER"]
    assert any(e.block.index == 20 and e.side == "bull" and e.at == 24 for e in breakers)
    out = A.breaker(frame, ctx_de("AN06_4h", frame))
    assert np.flatnonzero(out.entree).tolist() == [26]
    assert np.flatnonzero(out.sortie).tolist() == [27]                 # 99,7 < 99,8
    # 1er retour qui clôture dans la zone : aucun signal (un essai par breaker)
    inside = breaker_bougies(retour_close=101.1)
    assert not A.breaker(inside, ctx_de("AN06_4h", inside)).entree.any()


def test_order_blocks_copie_identique():
    for seed in range(6):
        rng = np.random.default_rng(seed)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 2500)))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.001, 2500))
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, 2500)))
        lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, 2500)))
        atr = primitives.atr(h, lo, c)
        ref = smc.order_blocks(o, h, lo, c, atr)
        got = A.order_blocks_rapides(o, h, lo, c, atr)
        assert [(b.index, 1 if b.side == "bull" else -1, b.bottom, b.top, b.known_at) for b in ref] == \
            list(zip(*(g.tolist() for g in got), strict=True))
        at = A._breakers(c, got[1], got[2], got[3], got[4])
        events = {(e.block.index, e.at) for e in block_events(ref, h, lo, c) if e.kind == "BREAKER" and e.side == "bull"}
        assert events == {(int(i), int(x)) for i, s, x in zip(got[0], got[1], at, strict=True) if s == -1 and x >= 0}


def test_swing_de_gann_a_la_main():
    high = [10, 11, 12, 13, 12.5, 12, 11, 11.5, 12.5, 13.5, 13, 12]
    low = [9, 10, 11, 12, 11.5, 11, 10, 10.5, 11, 12.5, 11, 9.5]
    close = [9.5, 10.5, 11.5, 12.5, 12, 11.5, 10.5, 11, 12, 13.2, 11.5, 9.8]
    top_at, bottom_at, top_idx, bottom_idx = A._gann(np.array(high, float), np.array(low, float), 2)
    # creux 9 (bougie 0) connu en 2 ; sommet 13 (bougie 3) connu en 5 ; creux 10 (bougie 6) en 8 ; sommet 13,5 en 11
    assert {t: v for t, v in enumerate(bottom_at) if not np.isnan(v)} == {2: 9.0, 8: 10.0}
    assert {t: v for t, v in enumerate(top_at) if not np.isnan(v)} == {5: 13.0, 11: 13.5}
    assert top_idx[5] == 3 and bottom_idx[8] == 6
    frame = bougies(close, high, low, close, unite=DAY_H, debut="2019-01-01")
    out = A.gann_swing(frame, ctx_de("AN13_1j", frame))
    assert np.flatnonzero(out.entree).tolist() == [9, 10]               # 13,2 > 13 ; jusqu'à 9,8 < 10
    assert np.flatnonzero(out.sortie).tolist() == [11]
