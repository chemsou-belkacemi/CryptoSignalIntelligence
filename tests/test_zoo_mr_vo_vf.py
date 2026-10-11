"""Zoo, familles MR, VO et VF : définitions calculées À LA MAIN, règles comparées à une réécriture indépendante en
boucles Python, cas construits (journée, séance, IBS, dernière heure) passés au moteur de positions. Données
SYNTHÉTIQUES seulement, aucun rendement."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import indicators as ind
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, LATENCE_NS, Bougies, Paire, agreger
from crypto_signal_intelligence.research.zoo.positions import simuler
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import mean_reversion as MR
from crypto_signal_intelligence.research.zoo.strategies import volatility as VO
from crypto_signal_intelligence.research.zoo.strategies import volume_flow as VF

from .zoo_outils import etape, heure, paire

NAN = np.nan
H0 = heure("2019-01-01")


def proche(a, b, atol=1e-12):
    np.testing.assert_allclose(np.asarray(a, float), np.asarray(b, float), rtol=0, atol=atol, equal_nan=True)


def barres(o, h, lo, c, v=None, *, unite: int = 1, debut_h: int = H0, heures=None) -> Bougies:
    o, h, lo, c = (np.asarray(x, dtype=float) for x in (o, h, lo, c))
    v = np.ones(len(c)) if v is None else np.asarray(v, dtype=float)
    open_h = (np.asarray(heures, dtype=np.int64) if heures is not None
              else debut_h + unite * np.arange(len(c), dtype=np.int64))
    return Bougies(unite, open_h, (open_h + unite) * HOUR_NS + LATENCE_NS, o, h, lo, c, v, v / 2, v / 100)


def plates(c, v=None, **kw) -> Bougies:
    """Bougies sans mèche : O = H = L = C."""
    return barres(c, c, c, c, v, **kw)


def ctx(params: dict, unite: int = 1, quotidien: Bougies | None = None) -> Contexte:
    return Contexte("AAAUSDT", unite, params, 0, quotidien=quotidien)


def indices(flags) -> list[int]:
    return [int(i) for i in np.flatnonzero(flags)]


def marche(n: int, seed: int, *, manquantes=(), start: str = "2019-01-01", vol: float = 0.01, derive: float = 0.0,
           chocs: float = 0.01) -> Paire:
    """Marche aléatoire 1 h, mèches et volumes aléatoires, heures manquantes (trous)."""
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(derive, vol, n)))
    p = paire(closes, start=start, mecha=0.004, manquantes=manquantes)
    h1 = p.h1
    k = len(h1)
    high = h1.haut * (1 + np.abs(rng.normal(0, 0.003, k)))
    low = h1.bas * (1 - np.abs(rng.normal(0, 0.003, k)))
    vol = np.exp(rng.normal(13, 1, k))
    vol[rng.random(k) < chocs] *= 8                       # quelques chocs de volume (VF05)
    return Paire(p.symbole, replace(h1, haut=high, bas=low, volume=vol, achat=vol * 0.5))


# --- Registre ---------------------------------------------------------------------------------------------------

def test_les_trois_familles_sont_codees_avec_les_parametres_du_registre():
    tests = choisir(["MR", "VO", "VF"])
    assert len(tests) == 23 + 14 + 21
    assert all(t.etat_code == "code" for t in tests)
    assert dict(PAR_ID["VO08_4h"].params) == {"bb": 20, "k_bb": 2.0, "kc": 20, "k_kc": 1.5}
    assert dict(PAR_ID["VF17_1h"].params) == {"rapide": 34, "lente": 55, "signal": 13}
    assert PAR_ID["VO01_1j"].execution == PAR_ID["VO02_1j"].execution == PAR_ID["MR19_1j"].execution == "1h"


# --- Définitions à la main ------------------------------------------------------------------------------------------

def test_rsi_de_wilder_a_la_main():
    proche(MR.rsi([1, 2, 3, 2, 3], 2), [NAN, NAN, 100, 50, 75])        # G 1 → 0,5 → 0,75 ; P 0 → 0,5 → 0,25


def test_ecarts_types_glissants_et_bollinger():
    proche(MR.ecart_type_glissant([1, 2, 3, 4, 6], 3, ddof=1), [NAN, NAN, 1, 1, np.sqrt(7 / 3)])
    proche(MR.ecart_type_glissant([1, 2, 3, 4, 6], 3, ddof=1, inclure=False), [NAN, NAN, NAN, 1, 1])
    lower, mid, upper = MR.bandes_bollinger(np.array([1.0, 2, 3, 4]), 3, 2.0)
    sd = np.sqrt(2 / 3)                                                    # écart-type de population de 1, 2, 3
    proche(mid, [NAN, NAN, 2, 3])
    proche(lower, [NAN, NAN, 2 - 2 * sd, 3 - 2 * sd])
    proche(upper, [NAN, NAN, 2 + 2 * sd, 3 + 2 * sd])


def test_stochastique_williams_cci_a_la_main():
    frame = barres([0] * 4, [2, 3, 4, 5], [1, 1, 2, 2], [1.5, 2, 4, 3])
    k, d = MR.stochastique_lente(frame, 2, 2, 2)                           # brut 50, 100, 33,3
    proche(k, [NAN, NAN, 75, (100 + 100 / 3) / 2])
    proche(d, [NAN, NAN, NAN, (75 + (100 + 100 / 3) / 2) / 2])
    proche(MR.williams_pct_r(frame, 2), [NAN, -50, 0, -200 / 3])
    flat = plates([5.0] * 3)
    proche(MR.stochastique_lente(flat, 2, 1, 1)[0], [NAN, 50, 50])         # écart nul : 50
    proche(MR.williams_pct_r(flat, 2), [NAN, -50, -50])
    proche(MR.cci_valeurs(plates([1.0, 2, 3, 2]), 3), [NAN, NAN, 100, -50])  # TP 3 : (3 − 2) / (0,015 × 2/3)
    proche(MR.cci_valeurs(flat, 3), [NAN, NAN, NAN])                       # écart moyen nul : indéfini


def test_vwap_du_jour_repart_a_minuit():
    frame = plates([10.0, 20, 30, 40], [1, 3, 1, 1], debut_h=H0 + 22)    # 22:00, 23:00, puis 00:00 et 01:00
    proche(MR.vwap_du_jour(frame), [10, 17.5, 30, 35])


def test_volumes_obv_ad_chaikin_mfi_force_klinger_a_la_main():
    proche(VF.obv_valeurs(plates([1.0, 2, 2, 1, 3], [5, 1, 2, 3, 4])), [0, 1, 1, -2, 2])
    ad_frame = barres([0] * 4, [2, 2, 2, 1], [0, 0, 0, 1], [2, 0, 1, 1], [1, 2, 3, 4])  # CLV 1, −1, 0, 0 (H = L)
    proche(VF.ligne_ad_valeurs(ad_frame), [1, -1, -1, -1])
    proche(VF.chaikin_valeurs(ad_frame, 1, 2), [NAN, -1, -1 / 3, -1 / 9])   # EMA 2 : 0, −2/3, −8/9
    proche(VF.mfi_valeurs(plates([1.0, 2, 1, 3], [1, 1, 2, 1]), 2), [NAN, NAN, 50, 60])
    proche(VF.force_elder_valeurs(plates([1.0, 2, 4, 3], [1, 1, 1, 2]), 2), [NAN, NAN, 1.5, 1.5 - 7 / 3])
    kl = barres([0] * 4, [3, 4, 5, 4], [1, 2, 2, 3], [2, 3, 4, 3], [1, 1, 1, 2])
    # tendance +1 (cm = 2 + 2), +1 continue (cm = 4 + 3), −1 (cm = 3 + 1)
    proche(VF._force_volume(kl.haut, kl.bas, kl.cloture, kl.volume), [NAN, 100, 800 / 7, -300])


def test_regression_lineaire_et_compression_ttm_construite():
    proche(VO._regression_lineaire(np.array([1.0, 2, 4]), 3), [NAN, NAN, 7 / 3 + 1.5])
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 30)
    expected = [np.polyval(np.polyfit(np.arange(5), x[t - 4:t + 1], 1), 4) for t in range(4, 30)]
    proche(VO._regression_lineaire(x, 5)[4:], expected, atol=1e-9)
    # plat (Bollinger de largeur nulle dans Keltner), puis saut de 10 à 13, puis plat à 13
    c = np.array([10.0] * 5 + [13.0] * 4)
    frame = barres(c, c + 0.5, c - 0.5, c)
    on, defined, mom = VO.squeeze_composantes(frame, 3, 2.0, 3, 1.5)
    assert indices(on & defined) == [3, 4, 7, 8]                         # largeur nulle de nouveau après le saut
    proche(mom[4:], [0, 0.875 + 1.75 / 3, 0.625 + 1.0, -0.875 + 1.0, -0.625 + 1.25 / 3])
    out = VO.squeeze(frame, ctx({"bb": 3, "k_bb": 2.0, "kc": 3, "k_kc": 1.5}))
    assert indices(out.entree) == [5]                                  # fin de compression, momentum > 0
    assert indices(out.sortie)[0] == 8                                 # 2 baisses : 1,625 → 0,125 → −0,21


# --- Cas construits ---------------------------------------------------------------------------------------------

def test_mr02_entree_et_sortie_aux_bougies_attendues():
    out = MR.rsi_30_70(plates([10.0, 9, 8, 7, 6, 8, 10]), ctx({"n": 2, "bas": 30.0, "haut": 70.0}))
    assert indices(out.entree) == [5] and indices(out.sortie) == [6]  # RSI 0 → 66,7 → 85,7


def test_mr03_reintegration_stricte():
    c = np.array([10.0, 10, 10, 10, 6, 9, 10, 10])
    out = MR.bollinger_reintegration(plates(c), ctx({"n": 3, "k": 1.0}))
    lower, mid, _ = MR.bandes_bollinger(c, 3, 1.0)
    assert c[4] < lower[4] and c[5] > lower[5]
    assert indices(out.entree) == [5]
    assert indices(out.sortie)[:2] == [2, 3] and 6 in indices(out.sortie)    # clôture ≥ médiane


def test_mr11_choc_de_rendement_et_vo09_atr_de_la_veille():
    c = np.r_[100.0, 100 + np.tile([0.0, 1.0], 10), 95.0]            # 20 rendements de ± 1 % puis −5,9 %
    out = MR.choc_rendement(plates(c), ctx({"n": 20, "z": -2.0}))
    assert indices(out.entree) == [21] and out.sortie is None
    c = np.array([10.0, 10, 10, 12.5])
    out = VO.cassure_atr(barres(c, c + 1, c - 1, c), ctx({"n": 2, "k": 1.0}))
    assert indices(out.entree) == [3]           # 12,5 > 10 + ATR_{t−1} = 2 ; avec ATR_t = 2,75 : faux


def test_mr12_mr20_mr01_regles_de_connors():
    c = np.array([1.0, 2, 3, 4, 5, 6, 5.9, 5.8, 5.7, 6.5])             # SMA 5 en 8 : 5,68 < 5,7
    out = MR.trois_baisses(plates(c), ctx({"baisses": 3, "sma": 5, "sortie_sma": 2}))
    assert indices(out.entree) == [8] and 9 in indices(out.sortie)
    c = np.array([1.0, 2, 3, 4, 5, 6, 7, 6.8, 6.5, 7.5])
    out = MR.double_sept(plates(c), ctx({"sma": 6, "n": 3}))
    assert indices(out.entree) == [8]                                   # 6,5 = plus bas des 3, > SMA 6 = 5,88
    assert 9 in indices(out.sortie) and 8 not in indices(out.sortie)
    c = np.r_[np.ones(20), 5.0, 5 - 0.1 * np.arange(1, 12)]
    out = MR.rsi2_connors(plates(c), ctx({"rsi": 2, "seuil": 10.0, "sma": 20, "sortie_sma": 5}))
    r, s20 = MR.rsi(c, 2), ind.sma(c, 20)
    assert indices(out.entree) == [t for t in range(len(c)) if r[t] < 10 and c[t] > s20[t]] != []


def test_vo06_nr4_premiere_cassure_dans_les_deux_jours():
    span = np.array([5.0, 4, 3, 2, 3, 3, 3, 3])
    high = np.array([10.0, 10, 10, 10, 10.5, 11, 11, 11])
    close = np.array([8.0, 8, 8, 9, 9.8, 10.5, 10.6, 10.2])
    frame = barres(close, high, high - span, close, unite=DAY_H)
    out = VO.nr(frame, ctx({"n": 4, "fenetre": 2}, unite=DAY_H))
    assert indices(out.entree) == [5] and out.sortie is None          # NR4 en 3 (plus haut 10), 1re clôture > 10 en 5
    close[4] = 10.2                                                   # cassure dès le 1er jour : 4 seulement
    out = VO.nr(barres(close, high, high - span, close, unite=DAY_H), ctx({"n": 4, "fenetre": 2}, unite=DAY_H))
    assert indices(out.entree) == [4]


def test_vf05_choc_de_volume_hors_bougie_courante():
    v = np.r_[np.ones(20), 5.01, 5.0]
    c = np.r_[np.ones(20), 2.0, 3.0]
    frame = barres(c - 0.5, c, c - 0.5, c, v)
    out = VF.choc_volume(frame, ctx({"multiple": 5.0, "n": 20}))
    assert indices(out.entree) == [20]                     # 5,01 > 5 × 1 ; puis 5 < 5 × moyenne(1… 5,01)


# --- Règles d'exécution 1 h, journée et séance, passées au moteur de positions ------------------------------------

STAGE = etape("2019-01-01", "2019-03-01")


def _positions(test_id: str, pair: Paire, **params):
    test = PAR_ID[test_id]
    frame = pair.unite(test.execution_heures)
    p = dict(test.params) | params
    c = Contexte(pair.symbole, test.execution_heures, p, STAGE.debut_ns, quotidien=pair.unite(DAY_H))
    out = test.strategie_fonction()(frame, c)
    member = np.ones(len(frame), dtype=bool)
    return out, frame, simuler(pair.symbole, out, frame, pair.grille, member, STAGE, test.n_max)


def _journees(closes_par_heure: np.ndarray, start: str = "2019-01-01") -> Paire:
    return paire(closes_par_heure, start=start, mecha=0.0)


def test_vo01_cassure_vendue_a_la_fin_de_la_journee():
    day0 = np.r_[100.0, np.full(23, 104.0)]            # amplitude de la veille ≈ 4 (ouverture 100, mèches nulles)
    day1 = np.r_[np.full(5, 101.0), 103.5, 103.6, np.full(17, 101.0)]
    out, frame, pos = _positions("VO01_1j", _journees(np.r_[day0, day1, np.full(24, 101.0)]))
    # ouverture du jour 1 = 104 (clôture précédente) : niveau 104 + 0,5 × 4 = 106 → aucun signal
    assert not out.entree.any()
    day1 = np.r_[np.full(5, 104.0), 106.5, 107.0, np.full(17, 105.0)]
    out, frame, pos = _positions("VO01_1j", _journees(np.r_[day0, day1, np.full(24, 105.0)]))
    assert indices(out.entree) == [29]                 # 1re clôture > 106 : bougie 05:00-06:00 du jour 1 (une seule)
    assert len(pos) == 1
    assert pos.entree_h[0] == H0 + 24 + 7 and pos.sortie_h[0] == H0 + 48     # entrée 07:00, vente à 24:00


def test_vo02_dual_thrust_exige_quatre_journees_consecutives():
    base = np.r_[np.full(24 * 4, 100.0)]
    base[24 * 2 + 5] = 102.0                               # une mèche : amplitude Dual Thrust = 2
    day = np.r_[100.5, 101.2, np.full(22, 100.0)]
    out, _, pos = _positions("VO02_1j", _journees(np.r_[base, day, np.full(24, 100.0)]))
    assert indices(out.entree) == [24 * 4 + 1]             # 101,2 > 100 + 0,5 × 2
    assert pos.entree_h[0] == H0 + 24 * 4 + 3 and pos.sortie_h[0] == H0 + 24 * 5
    hole = _journees(np.r_[base, day, np.full(24, 100.0)])
    trou = Paire(hole.symbole, hole.h1.filtre(np.arange(len(hole.h1)) != 30))      # journée 1 incomplète
    out, _, _ = _positions("VO02_1j", trou)
    assert not out.entree.any()


@pytest.mark.parametrize("test_id, debut, fin", [("VO03_1h", 0, 8), ("VO04_1h", 7, 16), ("VO05_1h", 13, 22)])
def test_vo03_a_vo05_seances(test_id, debut, fin):
    c = np.full(24 * 3, 100.0)
    c[24 + debut] = 100.4                                  # 1re heure de la séance du jour 1 : plus haut 100,4
    c[24 + debut + 2] = 100.5                              # 1re clôture au-dessus
    c[24 + debut + 3] = 100.6                              # 2e : ignorée (un signal par séance)
    pair = paire(c, mecha=0.0)
    out, _, pos = _positions(test_id, pair)
    assert indices(out.entree) == [24 + debut + 2]
    assert pos.entree_h[0] == H0 + 24 + debut + 4 and pos.sortie_h[0] == H0 + 24 + fin
    assert set(np.flatnonzero(out.sortie) % 24) == {fin - 2, fin - 1}


def test_vo11_derniere_heure_de_la_journee():
    c = np.full(24 * 3, 100.0)
    c[24 + 21] = 100.2                                     # 21:00-22:00 du jour 1 au-dessus de l'ouverture
    out, _, pos = _positions("VO11_1h", paire(c, mecha=0.0))
    assert indices(out.entree) == [24 + 21]
    assert pos.entree_h[0] == H0 + 24 + 23 and pos.sortie_h[0] == H0 + 48       # 23:00 → 24:00


def test_mr19_ibs_marque_sur_la_bougie_de_minuit():
    day0 = np.r_[101.0, 104.0, np.full(21, 103.0), 100.5]     # plus bas 100,5 ≈ clôture : IBS < 0,2
    out, _, pos = _positions("MR19_1j", _journees(np.r_[day0, np.full(48, 100.5)]))
    assert indices(out.entree) == [24]                       # bougie 00:00-01:00 du jour 1
    assert pos.entree_h[0] == H0 + 26 and pos.sortie_h[0] == H0 + 50            # 02:00 → 02:00 le lendemain


def test_mr09_ecart_a_la_vwap_et_fin_de_journee():
    rng = np.random.default_rng(5)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 24 * 9)))
    c[24 * 8 + 5:] = c[24 * 8 + 4] * 0.9                       # chute de 10 % à 05:00 du jour 8, sans retour
    out, frame, pos = _positions("MR09_1h", paire(c, mecha=0.0))
    assert indices(out.entree)[0] == 24 * 8 + 5
    assert pos.entree_h[0] == H0 + 24 * 8 + 7 and pos.sortie_h[0] == H0 + 24 * 9  # vente à la fin de la journée


# --- Réécriture indépendante en boucles (règles complètes, données aléatoires avec trous) ------------------------

def r_sma(x, n):
    out = np.full(len(x), NAN)
    for t in range(n - 1, len(x)):
        w = x[t - n + 1:t + 1]
        if not np.isnan(w).any():
            out[t] = sum(w) / n
    return out


def r_lisse(x, n, alpha):
    out = np.full(len(x), NAN)
    defined = np.flatnonzero(~np.isnan(x))
    if len(defined) < n:
        return out
    f = defined[0]
    value = float(np.mean(x[f:f + n]))
    out[f + n - 1] = value
    for t in range(f + n, len(x)):
        value = value + alpha * (x[t] - value)
        out[t] = value
    return out


def r_rsi(c, n):
    gain = [NAN] + [max(c[t] - c[t - 1], 0.0) for t in range(1, len(c))]
    loss = [NAN] + [max(c[t - 1] - c[t], 0.0) for t in range(1, len(c))]
    g, p = r_lisse(np.array(gain), n, 1 / n), r_lisse(np.array(loss), n, 1 / n)
    return np.array([NAN if np.isnan(a) or np.isnan(b) else (100.0 if b == 0 else 100 - 100 / (1 + a / b))
                     for a, b in zip(g, p, strict=True)])


def r_croise(a, b):
    return np.array([t > 0 and not np.isnan(a[t] + b[t] + a[t - 1] + b[t - 1]) and a[t] > b[t] and a[t - 1] <= b[t - 1]
                     for t in range(len(a))])


def gt(a, b):
    return np.array([not np.isnan(x + y) and x > y for x, y in zip(a, b, strict=True)])


def r_std(x, n, ddof, inclure=True):
    out = np.full(len(x), NAN)
    for t in range(n - 1 if inclure else n, len(x)):
        w = x[t - n + 1:t + 1] if inclure else x[t - n:t]
        if not np.isnan(w).any():
            m = sum(w) / n
            out[t] = (sum((v - m) ** 2 for v in w) / (n - ddof)) ** 0.5
    return out


def reference_signaux(test_id: str, frame: Bougies) -> tuple[np.ndarray, np.ndarray | None]:
    """Règle du § 12, réécrite en boucles à partir du texte du registre (paramètres du registre)."""
    p = PAR_ID[test_id].params
    sid = test_id[:4]
    o, h, lo, c, v = frame.ouverture, frame.haut, frame.bas, frame.cloture, frame.volume
    n = len(c)
    hh = lambda k: np.array([max(h[t - k + 1:t + 1]) if t >= k - 1 else NAN for t in range(n)])  # noqa: E731
    ll = lambda k: np.array([min(lo[t - k + 1:t + 1]) if t >= k - 1 else NAN for t in range(n)])  # noqa: E731
    tp = (h + lo + c) / 3
    if sid == "MR01":
        r, s = r_rsi(c, p["rsi"]), r_sma(c, p["sma"])
        return gt(np.full(n, p["seuil"]), r) & gt(c, s), gt(c, r_sma(c, p["sortie_sma"]))
    if sid == "MR02":
        r = r_rsi(c, p["n"])
        return r_croise(r, np.full(n, p["bas"])), gt(r, np.full(n, p["haut"]))
    if sid == "MR03":
        mid = r_sma(c, p["n"])
        lower = mid - p["k"] * r_std(c, p["n"], 0)
        entry = np.array([t > 0 and c[t - 1] < lower[t - 1] and c[t] > lower[t] for t in range(n)])
        return entry, np.array([not np.isnan(mid[t]) and c[t] >= mid[t] for t in range(n)])
    if sid == "MR05":
        top, bottom = hh(p["k"]), ll(p["k"])
        rng_ = top - bottom
        raw = np.array([NAN if np.isnan(rng_[t]) else (50.0 if rng_[t] == 0 else 100 * (c[t] - bottom[t]) / rng_[t])
                        for t in range(n)])
        k_line = r_sma(raw, p["lissage"])
        d_line = r_sma(k_line, p["d"])
        return r_croise(k_line, d_line) & gt(np.full(n, p["bas"]), k_line), gt(k_line, np.full(n, p["haut"]))
    if sid == "MR06":
        top = hh(p["n"])
        rng_ = top - ll(p["n"])
        wr = np.array([NAN if np.isnan(rng_[t]) else -100 * (top[t] - c[t]) / rng_[t] for t in range(n)])
        return r_croise(wr, np.full(n, p["bas"])), gt(wr, np.full(n, p["haut"]))
    if sid == "MR07":
        k = p["n"]
        cci = np.full(n, NAN)
        for t in range(k - 1, n):
            w = tp[t - k + 1:t + 1]
            m = sum(w) / k
            md = sum(abs(x - m) for x in w) / k
            cci[t] = (tp[t] - m) / (0.015 * md) if md > 0 else NAN
        return r_croise(cci, np.full(n, p["bas"])), gt(cci, np.full(n, p["haut"]))
    if sid == "MR09":
        vwap, acc = np.full(n, NAN), {}
        for t in range(n):
            d = frame.ouverture_h[t] // 24
            pv, vv = acc.get(d, (0.0, 0.0))
            acc[d] = (pv + tp[t] * v[t], vv + v[t])
            vwap[t] = acc[d][0] / acc[d][1]
        gap = c / vwap - 1
        sd = r_std(gap, p["fenetre"], 1, inclure=False)
        entry = np.array([not np.isnan(sd[t]) and sd[t] > 0 and gap[t] / sd[t] < p["z"] for t in range(n)])
        end = np.isin(frame.cloture_h % 24, (23, 0))
        return entry, (c >= vwap) | end
    if sid == "MR11":
        ret = np.r_[NAN, c[1:] / c[:-1] - 1]
        sd = r_std(ret, p["n"], 1, inclure=False)
        return gt(p["z"] * sd, ret), None
    if sid == "MR12":
        entry = np.array([t >= 3 and c[t] < c[t - 1] < c[t - 2] < c[t - 3] for t in range(n)]) & gt(c, r_sma(c, p["sma"]))
        return entry, gt(c, r_sma(c, p["sortie_sma"]))
    if sid == "MR20":
        k = p["n"]
        low7 = np.array([t >= k - 1 and c[t] == min(c[t - k + 1:t + 1]) for t in range(n)])
        high7 = np.array([t >= k - 1 and c[t] == max(c[t - k + 1:t + 1]) for t in range(n)])
        return low7 & gt(c, r_sma(c, p["sma"])), high7
    if sid in ("MR19", "VO01", "VO02", "VO11"):
        daily = agreger(frame, DAY_H)
        by_day = {int(daily.ouverture_h[j] // 24): j for j in range(len(daily))}
        opens = {int(frame.ouverture_h[t] // 24): o[t] for t in range(n) if frame.ouverture_h[t] % 24 == 0}
        entry, seen = np.zeros(n, dtype=bool), set()
        for t in range(n):
            d, hour = int(frame.ouverture_h[t] // 24), int(frame.ouverture_h[t] % 24)
            if sid == "MR19":
                j = by_day.get(d - 1)
                if hour == 0 and j is not None and daily.haut[j] > daily.bas[j]:
                    entry[t] = (daily.cloture[j] - daily.bas[j]) / (daily.haut[j] - daily.bas[j]) < p["seuil"]
                continue
            if d not in opens:
                continue
            if sid == "VO11":
                entry[t] = hour == p["heure_signal"] - 1 and c[t] > opens[d]
                continue
            days = [by_day.get(d - k) for k in range(1, (p["n"] if sid == "VO02" else 1) + 1)]
            if any(j is None for j in days):
                continue
            if sid == "VO01":
                amp = daily.haut[days[0]] - daily.bas[days[0]]
            else:
                amp = max(max(daily.haut[j] for j in days) - min(daily.cloture[j] for j in days),
                          max(daily.cloture[j] for j in days) - min(daily.bas[j] for j in days))
            if d not in seen and c[t] > opens[d] + p["k"] * amp:
                entry[t] = True
                seen.add(d)
        exit_flags = None if sid in ("MR19", "VO11") else np.isin(frame.cloture_h % 24, (23, 0))
        return entry, exit_flags
    if sid in ("VO03", "VO04", "VO05"):
        start, end = p["debut"], p["fin"]
        first = {int(frame.ouverture_h[t] // 24): h[t] for t in range(n) if frame.ouverture_h[t] % 24 == start}
        entry, seen = np.zeros(n, dtype=bool), set()
        for t in range(n):
            d, hour = int(frame.ouverture_h[t] // 24), int(frame.ouverture_h[t] % 24)
            if start < hour < end and d in first and d not in seen and c[t] > first[d]:
                entry[t] = True
                seen.add(d)
        return entry, np.isin(frame.cloture_h % 24, (end - 1, end))
    if sid in ("VO06", "VO07"):
        k = p["n"]
        span = h - lo
        narrow = [t >= k - 1 and span[t] <= min(span[t - k + 1:t + 1]) for t in range(n)]
        entry = np.zeros(n, dtype=bool)
        for t in range(n):
            for lag in range(1, p["fenetre"] + 1):
                s = t - lag
                if s >= 0 and narrow[s] and c[t] > h[s] and all(c[u] <= h[s] for u in range(s + 1, t)):
                    entry[t] = True
        return entry, None
    if sid == "VO08":
        k = p["bb"]
        mid, sd = r_sma(c, k), r_std(c, k, 0)
        tr = np.r_[NAN, [max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])) for t in range(1, n)]]
        width = r_sma(tr, p["kc"])
        on = [not np.isnan(width[t] + sd[t]) and mid[t] - p["k_bb"] * sd[t] > mid[t] - p["k_kc"] * width[t]
              and mid[t] + p["k_bb"] * sd[t] < mid[t] + p["k_kc"] * width[t] for t in range(n)]
        x = c - ((hh(k) + ll(k)) / 2 + mid) / 2
        mom = np.full(n, NAN)
        for t in range(k - 1, n):
            w = x[t - k + 1:t + 1]
            if not np.isnan(w).any():
                mom[t] = np.polyval(np.polyfit(np.arange(k), w, 1), k - 1)
        entry = np.array([t > 0 and on[t - 1] and not on[t] and not np.isnan(width[t] + sd[t]) and mom[t] > 0
                          for t in range(n)])
        exits = np.array([t > 1 and mom[t] < mom[t - 1] < mom[t - 2] for t in range(n)])
        return entry, exits
    if sid == "VO09":
        tr = np.r_[NAN, [max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])) for t in range(1, n)]]
        atr = r_lisse(tr, p["n"], 1 / p["n"])
        return np.array([t > 0 and not np.isnan(atr[t - 1]) and c[t] > c[t - 1] + p["k"] * atr[t - 1]
                         for t in range(n)]), None
    if sid in ("VF01", "VF02", "VF03"):
        if sid == "VF01":
            line = np.cumsum([0.0] + [np.sign(c[t] - c[t - 1]) * v[t] for t in range(1, n)])
        else:
            line = np.cumsum([((c[t] - lo[t]) - (h[t] - c[t])) / (h[t] - lo[t]) * v[t] if h[t] > lo[t] else 0.0
                              for t in range(n)])
        if sid == "VF03":
            osc = r_lisse(line, p["rapide"], 2 / (p["rapide"] + 1)) - r_lisse(line, p["lente"], 2 / (p["lente"] + 1))
            return gt(osc, np.zeros(n)), np.array([not np.isnan(x) and x <= 0 for x in osc])
        avg = r_sma(line, p["sma"])
        return r_croise(line, avg), r_croise(-line, -avg)
    if sid == "VF04":
        k = p["n"]
        mfi = np.full(n, NAN)
        for t in range(k, n):
            pos = sum(tp[u] * v[u] for u in range(t - k + 1, t + 1) if tp[u] > tp[u - 1])
            neg = sum(tp[u] * v[u] for u in range(t - k + 1, t + 1) if tp[u] < tp[u - 1])
            mfi[t] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
        return r_croise(mfi, np.full(n, p["bas"])), gt(mfi, np.full(n, p["haut"]))
    if sid == "VF05":
        return np.array([t >= p["n"] and v[t] > p["multiple"] * sum(v[t - p["n"]:t]) / p["n"] and c[t] > o[t]
                         for t in range(n)]), None
    if sid == "VF16":
        efi = r_lisse(np.r_[NAN, (c[1:] - c[:-1]) * v[1:]], p["ema"], 2 / (p["ema"] + 1))
        return gt(efi, np.zeros(n)), np.array([not np.isnan(x) and x <= 0 for x in efi])
    if sid == "VF17":
        force = np.full(n, NAN)
        cm, trend_prev = 0.0, 0
        for t in range(1, n):
            trend = 1 if h[t] + lo[t] + c[t] > h[t - 1] + lo[t - 1] + c[t - 1] else -1
            dm = h[t] - lo[t]
            cm = cm + dm if (t > 1 and trend == trend_prev) else (h[t - 1] - lo[t - 1]) + dm
            force[t] = 0.0 if cm == 0 else v[t] * abs(2 * (dm / cm - 1)) * trend * 100
            trend_prev = trend
        kvo = r_lisse(force, p["rapide"], 2 / (p["rapide"] + 1)) - r_lisse(force, p["lente"], 2 / (p["lente"] + 1))
        sig = r_lisse(kvo, p["signal"], 2 / (p["signal"] + 1))
        return r_croise(kvo, sig), r_croise(-kvo, -sig)
    raise AssertionError(test_id)


FAMILLES = choisir(["MR", "VO", "VF"])
#: deux jeux par cadre : 1 h et 4 h sur 30 et 60 journées (trous compris), journalier sur 260 journées
_COURTES = (marche(24 * 30, seed=31, manquantes=(100, 101, 330, 500)),
            marche(24 * 60, seed=33, vol=0.02, chocs=0.05, manquantes=(24 * 7 + 5,)))
def _jours_de_choc(pair: Paire, tous_les: int = 25) -> Paire:
    """Volume × 20 une journée sur `tous_les` (chocs de volume journaliers, VF05 1j)."""
    days = pair.h1.ouverture_h // DAY_H
    factor = np.where((days - days[0]) % tous_les == tous_les - 1, 20.0, 1.0)
    return Paire(pair.symbole, replace(pair.h1, volume=pair.h1.volume * factor, achat=pair.h1.achat * factor))


_LONGUES = (marche(24 * 260, seed=32, manquantes=(24 * 40 + 3,)),
            _jours_de_choc(marche(24 * 260, seed=34, vol=0.006, derive=0.0003, chocs=0.05)))


@pytest.mark.parametrize("test", FAMILLES, ids=[t.id for t in FAMILLES])
def test_regle_identique_a_la_reecriture_en_boucles(test):
    pairs = _LONGUES if test.execution == "1j" else _COURTES
    total = 0
    for pair in pairs:
        frame = pair.unite(test.execution_heures)
        c = Contexte(pair.symbole, test.execution_heures, test.params, 0, quotidien=pair.unite(DAY_H))
        out = test.strategie_fonction()(frame, c)
        entry, exits = reference_signaux(test.id, frame)
        assert out.entree.dtype == bool and len(out.entree) == len(frame)
        np.testing.assert_array_equal(out.entree, entry)
        if exits is None:
            assert out.sortie is None
        else:
            np.testing.assert_array_equal(out.sortie, exits)
        total += int(entry.sum())
    assert total > 0, "aucun signal : comparaison sans objet"
