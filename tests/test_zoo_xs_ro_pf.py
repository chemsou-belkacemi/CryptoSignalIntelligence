"""Familles XS (facteurs en coupe), RO (rotation) et PF (portefeuille) du zoo, forme (b) : définitions calculées à la
main sur de petits panneaux, quintile et rang, rééquilibrage, poids de la référence, ligne de décision sur une journée
sans bougie, XS24 exécuté à 02:00, comptage à blanc. Données SYNTHÉTIQUES seulement (aucun rendement réel)."""
from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.zoo import measure as M
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, LATENCE_NS
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte, Panneau
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import factors as F
from crypto_signal_intelligence.research.zoo.strategies import portfolio as P
from crypto_signal_intelligence.research.zoo.strategies import rotation as R

DEBUT = "2019-01-06"          # un dimanche


def jour(text: str) -> int:
    return int(pd.Timestamp(text, tz="UTC").value // (DAY_H * HOUR_NS))


def panneau(cloture: dict[str, list[float] | np.ndarray], *, debut: str = DEBUT, haut=None, volume=None, achat=None,
            membres: dict[str, np.ndarray | bool] | None = None) -> Panneau:
    """Panneau construit à la main : clôtures par paire (NaN = pas de bougie), plus hauts = clôtures par défaut,
    volume 1e6 et achats moitié, toutes les paires membres sauf indication."""
    symbols = tuple(sorted(cloture))
    close = np.column_stack([np.asarray(cloture[s], dtype=float) for s in symbols])
    n = len(close)

    def table(values, default):
        if values is None:
            return default
        return np.column_stack([np.asarray(values.get(s, default[:, k]), dtype=float) for k, s in enumerate(symbols)])

    high = table(haut, close)
    vol = table(volume, np.where(np.isfinite(close), 1e6, np.nan))
    buy = table(achat, vol / 2)
    member = np.ones(close.shape, dtype=bool)
    for k, s in enumerate(symbols):
        if membres and s in membres:
            member[:, k] = membres[s]
    return Panneau(jour(debut) + np.arange(n, dtype=np.int64), symbols, close, high, close, close, vol, buy, member)


def ctx(test_id: str) -> Contexte:
    return Contexte("", DAY_H, PAR_ID[test_id].params, 0, externes=MappingProxyType({}))


def lancer(test_id: str, board: Panneau):
    return PAR_ID[test_id].strategie_fonction()(board, ctx(test_id))


def ligne(poids, board: Panneau, row: int) -> int:
    """Indice de la décision prise à la clôture de la ligne `row` du panneau."""
    return int(np.flatnonzero(poids.decisions == board.disponible[row])[0])


# --- Registre ---------------------------------------------------------------------------------------------------

def test_toutes_les_strategies_xs_ro_pf_sont_codees():
    tests = choisir(["XS", "RO", "PF"])
    assert len(tests) == 21
    assert all(t.etat_code == "code" for t in tests)
    assert PAR_ID["XS17_1j"].params["cotation_min_jours"] == 365 and PAR_ID["XS20_1j"].deja_vu


# --- Outils de coupe --------------------------------------------------------------------------------------------

def test_rendements_premiere_cotation_propagation_et_fenetre():
    close = np.array([[np.nan, 100.0], [10.0, 110.0], [np.nan, 99.0], [12.0, np.nan], [15.0, 99.0]])
    r = F.rendements(close)
    assert np.isnan(r[:2, 0]).all() and np.isnan(r[2:4, 0]).all() and r[4, 0] == pytest.approx(0.25)
    assert r[1, 1] == pytest.approx(0.1) and r[2, 1] == pytest.approx(-0.1) and np.isnan(r[3:, 1]).all()
    assert F.premiere_cotation(close).tolist() == [1, 0]
    filled = F.propager(close)
    assert np.isnan(filled[0, 0]) and filled[2, 0] == 10.0 and filled[3, 1] == 99.0
    # rendement sur 2 jours en ligne 4 : clôture 4 / dernière clôture connue au plus tard en ligne 2
    out = F.rendement_n(close, filled, F.premiere_cotation(close), 4, 2)
    assert out[0] == pytest.approx(15 / 10 - 1) and out[1] == pytest.approx(99 / 99 - 1)
    # cotée depuis moins de 3 jours en ligne 3 : pas de rendement sur 3 jours
    assert np.isnan(F.rendement_n(close, filled, F.premiere_cotation(close), 3, 3)[0])
    window = F.fenetre(close, 1, 3)
    assert window.shape == (3, 2) and np.isnan(window[0]).all() and window[2, 1] == 110.0


def test_ligne_cotee_recule_sur_une_journee_sans_aucune_bougie():
    close = np.array([[1.0, 2.0], [np.nan, 3.0], [np.nan, np.nan], [np.nan, np.nan]])
    assert F.ligne_cotee(close, 1) == 1          # une seule paire suffit
    assert F.ligne_cotee(close, 3) == 1
    assert F.ligne_cotee(close, 3, recul=2) == -1
    assert F.ligne_cotee(close, 2, recul=1) == -1


def test_ecart_type_asymetrie_et_regression_a_la_main():
    x = np.array([[1.0], [2.0], [3.0], [10.0]])
    assert F.ecart_type(x)[0] == pytest.approx(np.sqrt(50 / 3))
    # moyenne 4 ; m2 = (9 + 4 + 1 + 36) / 4 = 12,5 ; m3 = (−27 − 8 − 1 + 216) / 4 = 45
    assert F.asymetrie(x)[0] == pytest.approx(45 / 12.5 ** 1.5)
    assert np.isnan(F.asymetrie(np.array([[1.0], [np.nan], [2.0]]))[0])          # moins de 3 valeurs
    # régression de (2, 4, 7) sur (1, 2, 3) : bêta 2,5 ; résidus (1/6, −1/3, 1/6), SCR 1/6, n − 2 = 1
    beta, sigma = F.regression_btc(np.array([[2.0], [4.0], [7.0]]), np.array([1.0, 2.0, 3.0]))
    assert beta[0] == pytest.approx(2.5) and sigma[0] == pytest.approx(np.sqrt(1 / 6))
    beta, _ = F.regression_btc(np.array([[2.0], [np.nan], [4.0], [7.0]]), np.array([1.0, 5.0, 2.0, 3.0]))
    assert beta[0] == pytest.approx(2.5)                                          # journée manquante ignorée


@pytest.mark.parametrize("n, k", [(2, 2), (3, 3), (7, 3), (14, 3), (20, 4), (40, 8)])
def test_taille_du_quintile(n, k):
    values = np.arange(n, dtype=float)
    chosen = F.quintile_choisi(values, np.ones(n, dtype=bool), "haut")
    assert chosen.sum() == k and chosen[-k:].all()
    assert F.quintile_choisi(values, np.ones(n, dtype=bool), "bas")[:k].all()


def test_quintile_egalites_par_ordre_alphabetique_et_non_eligibles_ignores():
    values = np.array([5.0, 1.0, 5.0, 5.0, 9.0, np.nan])
    eligible = np.array([True, True, True, True, False, False])
    chosen = F.quintile_choisi(values, eligible, "haut")
    assert chosen.tolist() == [True, False, True, True, False, False]
    eligible[3] = True
    values[1] = 5.0
    assert F.quintile_choisi(values, eligible, "haut").tolist() == [True, True, True, False, False, False]
    assert not F.quintile_choisi(values, np.zeros(6, dtype=bool), "bas").any()


# --- XS : facteurs hebdomadaires ----------------------------------------------------------------------------------

def _momentum_board() -> Panneau:
    """8 paires, 15 journées depuis un dimanche : rendement 7 j = a_k en ligne 7, b_k en ligne 14."""
    days = np.arange(15)
    closes, names = {}, "ABCDEFGH"
    for k, name in enumerate(names):
        a, b = 0.1 * (k + 1), 0.1 * (8 - k)
        c7 = 100 * (1 + a)
        closes[f"{name * 3}USDT"] = np.where(days <= 7, 100 * (1 + a * days / 7), c7 * (1 + b * (days - 7) / 7))
    return panneau(closes, membres={"HHHUSDT": False})


def test_xs01_quintile_haut_reequilibrage_et_calendrier():
    board = _momentum_board()
    out = lancer("XS01_1j", board)
    assert len(out.decisions) == 3                      # dimanches : lignes 0, 7, 14
    week0, week1, week2 = (ligne(out, board, r) for r in (0, 7, 14))
    assert not out.poids[week0].any() and not out.univers[week0].any()      # aucun rendement 7 j
    names = np.array(board.symboles)
    assert set(names[out.poids[week1] > 0]) == {"EEEUSDT", "FFFUSDT", "GGGUSDT"}   # HHH non membre
    assert out.poids[week1][out.poids[week1] > 0] == pytest.approx([1 / 3] * 3)
    assert set(names[out.poids[week2] > 0]) == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}   # rééquilibrage
    assert out.univers[week1].sum() == 7 and not out.univers[week1][names == "HHHUSDT"].any()
    # décision lundi 00:00 (+ latence) sur la clôture du dimanche ; échange à la clôture du lundi (mardi 00:00)
    assert out.decisions[week1] == (board.jours[7] + 1) * DAY_H * HOUR_NS + LATENCE_NS
    assert out.execution_h[week1] == (board.jours[7] + 2) * DAY_H
    # référence : les membres éligibles à poids égaux
    ref = M.poids_reference(PAR_ID["XS01_1j"], out, exposition=1.0)
    assert ref[week1][out.univers[week1]] == pytest.approx([1 / 7] * 7) and ref[week1].sum() == pytest.approx(1)


def test_xs_dimanche_sans_bougie_decide_sur_la_veille():
    board = _momentum_board()
    close = board.cloture.copy()
    close[14] = np.nan                              # maintenance : aucune bougie journalière le dimanche
    gap = Panneau(board.jours, board.symboles, close, close, close, close, board.volume, board.achat, board.membre)
    out = lancer("XS01_1j", gap)
    k = ligne(out, gap, 14)
    expected = close[13] / close[6] - 1             # ligne 13, rendement sur 7 j
    names = np.array(gap.symboles)
    top = names[:7][np.argsort(-expected[:7])[:3]]
    assert set(names[out.poids[k] > 0]) == set(top)


def test_xs09_xs10_xs11_xs15_sur_28_jours():
    days = np.arange(29)
    alternating = 100 * np.where(days % 2 == 0, 1.0, 1.1)        # rendements +10 % et −9,09 %
    plus_minus = 100 * np.cumprod(np.r_[1.0, np.where(np.arange(1, 29) % 2 == 1, 1.05, 0.95)])
    late = np.where(days >= 5, 50.0, np.nan)
    board = panneau({"AAAUSDT": alternating, "BBBUSDT": plus_minus, "CCCUSDT": late},
                    volume={"AAAUSDT": np.r_[np.full(10, 1000.0), np.nan, np.full(18, 1000.0)],
                            "BBBUSDT": np.full(29, 2000.0)})
    vol = F.variable(board, "volume_28j", 28)
    assert vol[0] == pytest.approx(1000) and vol[1] == pytest.approx(2000) and np.isnan(vol[2])   # CCC trop jeune
    amihud = F.variable(board, "amihud_28j", 28)
    # lignes 1 à 28 : 14 rendements de +0,1 et 14 de −1/11 ; la journée 10 (volume manquant) est ignorée
    r = np.where(np.arange(1, 29) % 2 == 1, 0.1, 1 / 1.1 - 1)
    keep = np.arange(1, 29) != 10
    assert amihud[0] == pytest.approx(np.mean(np.abs(r[keep])) / 1000)
    assert F.variable(board, "max_28j", 28)[0] == pytest.approx(0.1)
    assert F.variable(board, "asymetrie_28j", 28)[1] == pytest.approx(0.0, abs=1e-9)   # ±5 % symétriques


def test_xs12_xs14_btc_facteur_exclu_et_bete_double():
    rng = np.random.default_rng(3)
    btc_r = rng.normal(0, 0.03, 120)
    noise = rng.normal(0, 0.01, 120)
    closes = {"BTCUSDT": 100 * np.cumprod(1 + btc_r), "AAAUSDT": 50 * np.cumprod(1 + 2 * btc_r),
              "BBBUSDT": 20 * np.cumprod(1 + 0.5 * btc_r + noise)}
    board = panneau(closes)
    beta = F.variable(board, "beta_90j", 119)
    sigma = F.variable(board, "vol_idio_28j", 119)
    btc = board.symboles.index("BTCUSDT")
    assert np.isnan(beta[btc]) and np.isnan(sigma[btc])
    a, b = board.symboles.index("AAAUSDT"), board.symboles.index("BBBUSDT")
    assert beta[a] == pytest.approx(2.0) and sigma[a] == pytest.approx(0.0, abs=1e-12)
    x, m = btc_r[-90:] * 0.5 + noise[-90:], btc_r[-90:]
    assert beta[b] == pytest.approx(np.cov(x, m)[0, 1] / np.var(m, ddof=1))
    resid = noise[-28:] - noise[-28:].mean() - (np.polyfit(btc_r[-28:], 0.5 * btc_r[-28:] + noise[-28:], 1)[0] - 0.5) \
        * (btc_r[-28:] - btc_r[-28:].mean())
    assert sigma[b] == pytest.approx(np.sqrt((resid ** 2).sum() / 26))
    without = panneau({k: v for k, v in closes.items() if k != "BTCUSDT"})
    assert np.isnan(F.variable(without, "beta_90j", 119)).all()          # sans BTC : rien d'éligible


def test_xs17_plus_haut_historique_et_365_jours_de_cotation():
    n = 401                                          # ligne 399 = dimanche (57 semaines)
    old = np.full(n, 100.0)
    high = old.copy()
    high[10] = 200.0
    young = np.where(np.arange(n) >= 100, 80.0, np.nan)
    others = {f"{c * 3}USDT": np.full(n, 10.0 + i) for i, c in enumerate("BCDE")}
    board = panneau({"AAAUSDT": old, "ZZZUSDT": young, **others}, haut={"AAAUSDT": high})
    value = F.variable(board, "proximite_ath", 399)
    assert value[board.symboles.index("AAAUSDT")] == pytest.approx(0.5)
    out = lancer("XS17_1j", board)
    k = ligne(out, board, 399)
    assert not out.univers[k][board.symboles.index("ZZZUSDT")]       # cotée depuis 300 jours seulement
    assert out.univers[k].sum() == 5


def test_xs18_plus_haut_des_365_dernieres_journees():
    n = 380
    close = np.full(n, 100.0)
    high = close.copy()
    high[10], high[100] = 400.0, 125.0             # ligne 10 hors de la fenêtre 15..379
    board = panneau({"AAAUSDT": close}, haut={"AAAUSDT": high})
    assert F.variable(board, "proximite_52s", 379)[0] == pytest.approx(0.8)
    assert np.isnan(F.variable(board, "proximite_52s", 363)[0])           # moins de 365 jours de cotation


def test_xs19_xs20_xs23():
    n = 200
    vol = np.full(n, 100.0)
    vol[-7:] = 300.0
    buy = vol / 2
    buy[-7:] = 180.0
    board = panneau({"AAAUSDT": np.arange(1.0, n + 1)}, volume={"AAAUSDT": vol}, achat={"AAAUSDT": buy})
    assert F.variable(board, "choc_volume_hebdo", n - 1)[0] == pytest.approx(3.0)
    assert F.variable(board, "flux_acheteur_7j", n - 1)[0] == pytest.approx(0.6)
    # clôtures 1..200 : SMA 21 = 190, SMA 200 = 100,5
    assert F.variable(board, "mad_21_200", n - 1)[0] == pytest.approx(190 / 100.5)
    assert np.isnan(F.variable(board, "mad_21_200", n - 2)[0])


# --- XS24 -------------------------------------------------------------------------------------------------------

def test_xs24_plus_fortes_baisses_executees_a_02h_et_journee_manquante():
    day1 = np.array([-0.05, -0.04, -0.03, 0.01, 0.02, 0.03])
    closes = {f"{c * 3}USDT": [100.0, 100 * (1 + day1[i]), np.nan, 100 * (1 + day1[i]) * (1 + 0.01 * i)]
              for i, c in enumerate("ABCDEF")}
    board = panneau(closes)
    out = lancer("XS24_1j", board)
    assert len(out.decisions) == 4
    assert out.execution_h.tolist() == [(d + 1) * DAY_H + 2 for d in board.jours]
    assert (out.execution_h * HOUR_NS > out.decisions).all()
    assert not out.poids[0].any()                                         # aucun rendement la 1re journée
    assert out.poids[1][:3] == pytest.approx([1 / 3] * 3) and not out.poids[1][3:].any()
    assert not out.poids[2].any() and not out.univers[2].any()            # journée sans bougie : aucun signal
    # journée suivante : rendement depuis la dernière clôture connue (2 jours) ; les plus faibles sont A, B, C
    assert out.poids[3][:3] == pytest.approx([1 / 3] * 3)


# --- RO ---------------------------------------------------------------------------------------------------------

def test_ro02_eth_si_ethbtc_au_dessus_de_sa_sma_50():
    days = np.arange(64)
    eth = np.where(days <= 55, 10.0 + days, 5.0)
    board = panneau({"BTCUSDT": np.full(64, 100.0), "ETHUSDT": eth})
    out = lancer("RO02_1j", board)
    assert out.dans_a.tolist() == [False] * 7 + [True, False, False]       # dimanches 0, 7, …, 63
    eth_col, btc_col = board.symboles.index("ETHUSDT"), board.symboles.index("BTCUSDT")
    assert (out.composante_a[:, eth_col] == 1).all() and (out.composante_b[:, btc_col] == 1).all()
    assert out.poids[7, eth_col] == 1 and out.poids[8, btc_col] == 1 and out.poids[0, btc_col] == 1
    ref = M.poids_reference(PAR_ID["RO02_1j"], out, exposition=1.0, dans_a=0.25)
    assert ref[0].tolist() == [0.75, 0.25]


def test_ro03_altseason_seuil_de_75_pourcent():
    def board(returns: dict[str, float]) -> Panneau:
        closes = {s: np.r_[np.full(91, 100.0), 100 * (1 + r)] for s, r in returns.items()}
        closes["NEWUSDT"] = np.r_[np.full(50, np.nan), np.full(42, 100.0)]          # cotée depuis 42 jours
        closes["OUTUSDT"] = np.full(92, 100.0)                                       # non membre
        return panneau(closes, membres={"OUTUSDT": False})

    up = board({"BTCUSDT": 0.10, "AAAUSDT": 0.2, "BBBUSDT": 0.3, "CCCUSDT": 0.4, "DDDUSDT": 0.0})
    out = lancer("RO03_1j", up)
    k = ligne(out, up, 91)
    names = np.array(up.symboles)
    assert out.dans_a[k]
    assert set(names[out.poids[k] > 0]) == {"AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"}
    assert out.poids[k][out.poids[k] > 0] == pytest.approx([0.25] * 4)
    down = board({"BTCUSDT": 0.10, "AAAUSDT": 0.2, "BBBUSDT": 0.3, "CCCUSDT": 0.0, "DDDUSDT": 0.0})
    out = lancer("RO03_1j", down)
    k = ligne(out, down, 91)
    assert not out.dans_a[k] and out.poids[k][list(down.symboles).index("BTCUSDT")] == 1
    assert out.composante_a[k].sum() == pytest.approx(1)                  # A connue même hors de A (référence)


def test_ro04_largeur_strictement_plus_de_la_moitie():
    days = np.arange(57)
    closes = {"AAAUSDT": np.where(days >= 49, 110.0, 100.0), "BBBUSDT": np.where(days >= 49, 110.0, 100.0),
              "CCCUSDT": np.where(days == 49, 110.0, np.where(days > 49, 90.0, 100.0)),
              "DDDUSDT": np.full(57, 100.0)}
    board = panneau(closes)
    out = lancer("RO04_1j", board)
    k49, k56 = ligne(out, board, 49), ligne(out, board, 56)
    assert out.poids[k49] == pytest.approx([0.25] * 4)                    # 3 sur 4 au-dessus
    assert not out.poids[k56].any()                                        # 2 sur 4 : pas plus de la moitié
    assert out.univers[k56].all()
    ref = M.poids_reference(PAR_ID["RO04_1j"], out, exposition=0.4)
    assert ref[k56] == pytest.approx([0.1] * 4)


def test_ro05_correlation_moyenne_a_la_main():
    x = np.array([[1.0, 2.0, 4.0, np.nan], [2.0, np.nan, 3.0, np.nan], [3.0, 6.0, 2.0, 1.0], [4.0, 8.0, 1.0, 2.0]])
    # (1, 2) = 1 ; (1, 3) = −1 ; (2, 3) = −1 ; paires avec la 4e colonne : 2 observations, écartées
    assert R.correlation_moyenne(x) == pytest.approx(-1 / 3)
    assert np.isnan(R.correlation_moyenne(x[:, [0, 3]]))


def test_ro05_regle_sous_la_mediane_des_365_jours_precedents(monkeypatch):
    n = 400 + 3
    start = jour(DEBUT) - 400                         # la ligne 400 tombe sur le dimanche 2019-01-06
    board = panneau({"AAAUSDT": np.full(n, 1.0), "BBBUSDT": np.full(n, 2.0)},
                    debut=str(pd.Timestamp(start, unit="D").date()))
    series = np.full(n, np.nan)
    series[30:] = 0.5
    series[400] = 0.4                                 # sous la médiane (0,5) des 365 précédents
    monkeypatch.setattr(R, "correlations_moyennes", lambda frame, window: series[:len(frame.jours)])
    out = lancer("RO05_1j", board)
    k = ligne(out, board, 400)
    assert out.poids[k] == pytest.approx([0.5, 0.5])
    earlier = ligne(out, board, 393)                  # moins de 365 jours de série avant : USDT
    assert 393 - 365 < 30 <= 400 - 365 and not out.poids[earlier].any()
    series[400] = 0.5                                 # égale à la médiane : USDT
    assert not lancer("RO05_1j", board).poids[k].any()


# --- PF ---------------------------------------------------------------------------------------------------------

def test_pf05_mensuel_premier_lundi_poids_egaux_et_reference_flottante():
    n = 60
    closes = {"AAAUSDT": np.full(n, 1.0), "BBBUSDT": np.full(n, 2.0),
              "CCCUSDT": np.where(np.arange(n) >= 3, 3.0, np.nan), "DDDUSDT": np.full(n, 4.0)}
    board = panneau(closes, membres={"DDDUSDT": np.arange(n) >= 27})        # membre à partir de février
    out = lancer("PF05_1j", board)
    rows = [0, 28, 56]                                # dimanches 6 janvier, 3 février, 3 mars 2019
    assert out.decisions.tolist() == [int(board.disponible[r]) for r in rows]
    assert out.poids[0] == pytest.approx([0.5, 0.5, 0, 0])                 # CCC pas encore cotée, DDD non membre
    assert out.poids[1] == pytest.approx([0.25] * 4)
    assert PAR_ID["PF05_1j"].reference == M.PANIER_FLOTTANT
    assert M.poids_reference(PAR_ID["PF05_1j"], out, 1.0)[1] == pytest.approx([0.25] * 4)


def test_pf06_inverse_de_la_volatilite():
    n = 40
    k = np.arange(1, n)
    a = 100 * np.cumprod(np.r_[1.0, np.where(k % 2 == 1, 1.01, 0.99)])
    b = 100 * np.cumprod(np.r_[1.0, np.where(k % 2 == 1, 1.02, 0.98)])
    board = panneau({"AAAUSDT": a, "BBBUSDT": b, "CCCUSDT": np.r_[np.full(30, np.nan), np.full(10, 5.0)]},
                    debut="2019-01-06")
    # premier lundi de février : dimanche 3 février = ligne 28 ; volatilités 1 % et 2 % → poids 2/3 et 1/3
    out = lancer("PF06_1j", board)
    k28 = ligne(out, board, 28)
    assert out.poids[k28] == pytest.approx([0.0, 0.0, 0.0])                 # 28 rendements < 30 jours de cotation
    board = panneau({"AAAUSDT": np.r_[a, a[-1] * a[1:] / 100][:70], "BBBUSDT": np.r_[b, b[-1] * b[1:] / 100][:70],
                     "CCCUSDT": np.r_[np.full(60, np.nan), np.full(10, 5.0)]}, debut="2018-12-31")
    out = lancer("PF06_1j", board)
    row = 34                                         # dimanche 3 février 2019
    kk = ligne(out, board, row)
    assert out.poids[kk][:2] == pytest.approx([2 / 3, 1 / 3]) and out.poids[kk][2] == 0
    assert not out.univers[kk][2]                                          # CCC pas encore cotée


def _ledoit_wolf_manuel(x: np.ndarray) -> np.ndarray:
    """Formules de l'article (2004) : m = tr(S)/p, d² = ‖S − mI‖²/p, b̄² = Σ_t ‖x_t x_t' − S‖² / (n² p)."""
    n, p = x.shape
    x = x - x.mean(axis=0)
    s = x.T @ x / n
    m = np.trace(s) / p
    d2 = np.sum((s - m * np.eye(p)) ** 2) / p
    b2 = sum(np.sum((np.outer(row, row) - s) ** 2) for row in x) / n ** 2 / p
    return (min(b2, d2) / d2) * m * np.eye(p) + (1 - min(b2, d2) / d2) * s


def test_ledoit_wolf_contre_les_formules_de_l_article():
    rng = np.random.default_rng(5)
    for n, p in ((90, 5), (30, 12), (10, 3)):
        x = rng.normal(0, 0.02, (n, p)) + rng.normal(0, 0.02, (n, 1))
        cov, shrinkage = P.ledoit_wolf(x)
        assert 0 <= shrinkage <= 1
        assert cov == pytest.approx(_ledoit_wolf_manuel(x), rel=1e-10, abs=1e-14)


def _variance_minimale_force_brute(cov: np.ndarray) -> np.ndarray:
    from itertools import combinations
    best, best_w = np.inf, None
    p = len(cov)
    for size in range(1, p + 1):
        for support in combinations(range(p), size):
            s = list(support)
            sol = np.linalg.solve(cov[np.ix_(s, s)], np.ones(size))
            w = np.zeros(p)
            w[s] = sol / sol.sum()
            if (w >= -1e-12).all() and w @ cov @ w < best:
                best, best_w = float(w @ cov @ w), w
    assert best_w is not None
    return best_w


def test_variance_minimale_longue_a_la_main_et_contre_la_force_brute():
    assert P.variance_minimale_longue(np.diag([1.0, 4.0])) == pytest.approx([0.8, 0.2])
    # corrélation 0,9, écarts-types 1 et 2 : le minimum sans contrainte vendrait la 2e (w1 = 2,2 / 1,4) → (1, 0)
    assert P.variance_minimale_longue(np.array([[1.0, 1.8], [1.8, 4.0]])) == pytest.approx([1.0, 0.0])
    rng = np.random.default_rng(9)
    for _ in range(25):
        p = int(rng.integers(2, 7))
        a = rng.normal(0, 1, (p + 3, p)) + rng.normal(0, 1.5, (p + 3, 1))
        cov = a.T @ a / (p + 3) + 1e-3 * np.eye(p)
        w = P.variance_minimale_longue(cov)
        assert w.sum() == pytest.approx(1) and (w >= 0).all()
        assert w @ cov @ w == pytest.approx(float(_variance_minimale_force_brute(cov) @ cov @ _variance_minimale_force_brute(
            cov)), rel=1e-9)


def test_variance_minimale_scipy_si_disponible():
    optimize = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(10)
    a = rng.normal(0, 1, (40, 8)) + rng.normal(0, 2, (40, 1))
    cov = a.T @ a / 40
    res = optimize.minimize(lambda w: w @ cov @ w, np.full(8, 1 / 8), jac=lambda w: 2 * cov @ w, method="SLSQP",
                            bounds=[(0, 1)] * 8, constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                            options={"ftol": 1e-15, "maxiter": 500})
    w = P.variance_minimale_longue(cov)
    assert w @ cov @ w <= res.fun + 1e-12


def test_pf07_journees_manquantes_retirees_et_membres_cotes_depuis_90_jours():
    rng = np.random.default_rng(12)
    n = 130
    returns = rng.normal(0, 0.02, (n, 3)) + rng.normal(0, 0.02, (n, 1))
    closes = 100 * np.cumprod(1 + returns, axis=0)
    closes[100] = np.nan                                         # maintenance commune
    board = panneau({"AAAUSDT": closes[:, 0], "BBBUSDT": closes[:, 1], "CCCUSDT": closes[:, 2],
                     "NEWUSDT": np.r_[np.full(60, np.nan), np.full(n - 60, 7.0)]}, debut="2018-11-04")
    out = lancer("PF07_1j", board)
    row = 119                                                    # dimanche 3 mars 2019
    k = ligne(out, board, row)
    r = F.rendements(board.cloture)[row - 89:row + 1, :3]
    r = r[np.isfinite(r).all(axis=1)]
    assert len(r) == 88                                          # journée manquante et lendemain retirés
    expected = P.variance_minimale_longue(P.ledoit_wolf(r)[0])
    assert out.poids[k][:3] == pytest.approx(expected) and out.poids[k][3] == 0
    assert out.univers[k].tolist() == [True, True, True, True]
    assert M.poids_reference(PAR_ID["PF07_1j"], out, 1.0)[k] == pytest.approx([0.25] * 4)


def test_pf09_60_40_chaque_mois_et_reference_top_40():
    n = 60
    board = panneau({"BTCUSDT": np.full(n, 100.0), "ETHUSDT": np.full(n, 10.0), "AAAUSDT": np.full(n, 1.0)})
    out = lancer("PF09_1j", board)
    assert len(out.decisions) == 3
    assert (out.poids == np.array([0.0, 0.6, 0.4])).all()
    assert M.poids_reference(PAR_ID["PF09_1j"], out, 1.0)[0] == pytest.approx([1 / 3] * 3)


# --- Comptage à blanc -------------------------------------------------------------------------------------------

def test_comptage_a_blanc_xs_ro_pf_sur_le_marche_synthetique():
    from crypto_signal_intelligence.research.zoo import h0
    from crypto_signal_intelligence.research.zoo.data import DECOUVERTE
    from crypto_signal_intelligence.research.zoo.run import compter
    tests = choisir(["XS", "RO", "PF"])
    counts = compter(tests, h0.SourceSynthetique(7, "H0a", n_paires=10), DECOUVERTE)
    assert set(counts) == {t.id for t in tests}
    for test in tests:
        c = counts[test.id]
        assert c["forme"] == "b" and c["blocs"] > 0
    assert counts["XS01_1j"]["echanges"] == 208 and counts["XS24_1j"]["echanges"] == 1461
    assert counts["PF05_1j"]["echanges"] == 48
    assert counts["XS01_1j"]["exposition_moyenne"] == pytest.approx(1.0)
    assert 0 < counts["RO04_1j"]["exposition_moyenne"] < 1
