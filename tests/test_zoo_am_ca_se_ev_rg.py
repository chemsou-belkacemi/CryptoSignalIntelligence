"""Familles AM, CA, SE, EV et RG du zoo (docs/ZOO.md § 12) : définitions calculées à la main, signaux construits,
jointures externes au week-end, fenêtres d'événements en heure d'été et d'hiver, positions du moteur. Données
SYNTHÉTIQUES, aucun réseau, aucun rendement."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.context.zoo import available_at_ecb, available_at_fng, available_at_fred
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, LATENCE_NS, Bougies, SerieExterne
from crypto_signal_intelligence.research.zoo.positions import simuler
from crypto_signal_intelligence.research.zoo.run import panneau
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import calendrier as CA
from crypto_signal_intelligence.research.zoo.strategies import events as EV
from crypto_signal_intelligence.research.zoo.strategies import other_markets as AM
from crypto_signal_intelligence.research.zoo.strategies import regimes as RG
from crypto_signal_intelligence.research.zoo.strategies import sentiment as SE

from .zoo_outils import etape, heure, paire, univers_toujours

FAMILLES = ["AM", "CA", "SE", "EV", "RG"]


def ts(text: str) -> int:
    return int(pd.Timestamp(text, tz="UTC").value)


def serie(nom: str, jours: list[str], valeurs: list[float], regle) -> SerieExterne:
    """Série externe : valeur datée du jour (00:00 UTC), `available_at` donné par la règle du magasin."""
    days = [pd.Timestamp(d, tz="UTC") for d in jours]
    avail = np.array([int(regle(d).value) for d in days], dtype=np.int64)
    dates = np.array([int(d.value) for d in days], dtype=np.int64)
    order = np.argsort(avail, kind="stable")
    return SerieExterne(nom, avail[order], dates[order], np.asarray(valeurs, dtype=float)[order])


def journalieres(closes, debut: str = "2019-01-01", manquantes=()) -> Bougies:
    """Bougies journalières complètes (datées comme l'agrégation du moteur : dernière bougie 1 h + latence)."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    day0 = heure(debut) // DAY_H
    keep = np.ones(n, dtype=bool)
    keep[list(manquantes)] = False
    open_h = (day0 + np.arange(n, dtype=np.int64)) * DAY_H
    opens = np.r_[closes[0], closes[:-1]]
    vol = np.full(n, 1e6)
    return Bougies(DAY_H, open_h[keep], ((open_h + DAY_H) * HOUR_NS + LATENCE_NS)[keep], opens[keep],
                   np.maximum(opens, closes)[keep] * 1.001, np.minimum(opens, closes)[keep] * 0.999, closes[keep],
                   vol[keep], vol[keep] / 2, vol[keep] / 100)


def test_registre_des_cinq_familles_code():
    tests = choisir(FAMILLES)
    assert len(tests) == 7 + 4 + 2 + 1 + 7
    assert all(t.etat_code == "code" for t in tests)
    assert not any(t.strategie == "AM05" for t in tests)          # retiré du zoo


# --- AM : jointures externes ---------------------------------------------------------------------------------------

def test_am_jointure_du_week_end_et_derniere_seance():
    """SP500 (16:00 New York + 2 h) : la séance du vendredi sert le samedi, le dimanche et le lundi 00:00 UTC."""
    days = ["2019-01-02", "2019-01-03", "2019-01-04", "2019-01-07"]       # mercredi, jeudi, vendredi, lundi
    s = serie("zoo_marches:SP500", days, [100.0, 99.0, 101.0, 100.5], lambda d: available_at_fred("SP500", d))
    assert s.disponible[2] == ts("2019-01-04 23:00")                        # 18:00 New York (hiver) = 23:00 UTC
    decisions = np.array([ts(d) + LATENCE_NS for d in ("2019-01-04", "2019-01-05", "2019-01-06", "2019-01-07",
                                                      "2019-01-08")])
    hit, defined = AM.condition(s, decisions, "dernière séance > 0")
    # vendredi 00:00 : jeudi connu (99 < 100) ; samedi, dimanche, lundi 00:00 : vendredi (101 > 99) ; mardi : lundi
    assert defined.all()
    assert hit.tolist() == [False, True, True, True, False]


def test_am_rendement_vingt_seances_a_la_main():
    days = pd.bdate_range("2019-01-01", periods=22).strftime("%Y-%m-%d").tolist()
    values = [100.0] * 21 + [90.0]
    values[0] = 95.0                                       # 20 séances avant la 21e valeur
    s = serie("zoo_marches:NASDAQ100", days, values, lambda d: available_at_fred("NASDAQ100", d))
    decision_21 = s.disponible[20] + 1                     # 21e valeur connue : 100 / 95 − 1 > 0
    decision_20 = s.disponible[19] + 1                     # 20 valeurs seulement : indéfini
    decision_22 = s.disponible[21] + 1                     # 90 / 100 − 1 < 0
    hit, defined = AM.condition(s, np.array([decision_20, decision_21, decision_22]), "rendement 20 séances > 0")
    assert defined.tolist() == [False, True, True]
    assert hit.tolist() == [False, True, False]


def test_am04_dgs10_sur_son_available_at():
    """DGS10 du jour d connu le lendemain 16:15 New York + 2 h : jamais à la décision de d + 1 00:00 UTC."""
    days = pd.bdate_range("2019-03-01", periods=22).strftime("%Y-%m-%d").tolist()
    values = list(np.linspace(3.0, 2.0, 22))
    s = serie("zoo_marches:DGS10", days, values, lambda d: available_at_fred("DGS10", d))
    last = pd.Timestamp(days[-1], tz="UTC")
    assert s.disponible[-1] == ts(str((last + pd.Timedelta(days=1)).date()) + " 22:15")   # heure d'été : 18:15 NY
    next_morning = int((last + pd.Timedelta(days=1)).value) + LATENCE_NS
    after = s.disponible[-1] + 1
    hit, defined = AM.condition(s, np.array([next_morning, after]), "variation 20 séances < 0")
    # le matin du lendemain, la 22e valeur n'est pas connue : la 21e l'est (2,048 − 3,0 < 0), comme la 22e ensuite
    idx = AM.indices_connus(s, np.array([next_morning, after]))
    assert idx.tolist() == [20, 21]
    assert hit.tolist() == [True, True] and defined.all()


def test_am07_dollar_bce_heure_de_francfort():
    s = serie("zoo_dollar_bce:DXY_BCE", ["2019-07-01", "2019-12-02"], [96.0, 97.0], available_at_ecb)
    assert s.disponible.tolist() == [ts("2019-07-01 15:00"), ts("2019-12-02 16:00")]     # 17:00 Francfort


def test_am08_mediane_des_365_jours_precedents():
    days = pd.date_range("2018-01-01", periods=400, freq="D").strftime("%Y-%m-%d").tolist()
    rule = lambda d: available_at_fred("VIXCLS", d)                                       # noqa: E731
    up = serie("zoo_marches:VIXCLS", days, list(range(400)), rule)
    down = serie("zoo_marches:VIXCLS", days, list(range(400, 0, -1)), rule)
    decisions = np.array([up.disponible[k] + 1 for k in (364, 365, 399)])
    hit_up, def_up = AM.condition(up, decisions, "sous sa médiane des 365 jours précédents")
    hit_down, def_down = AM.condition(down, decisions, "sous sa médiane des 365 jours précédents")
    assert def_up.tolist() == [False, True, True] == def_down.tolist()
    assert hit_up.tolist() == [False, False, False]       # valeur k au-dessus de la médiane k − 183
    assert hit_down.tolist() == [False, True, True]


def test_am_btc_seul_et_calendrier_quotidien():
    btc, other = journalieres(np.linspace(100, 120, 40)), journalieres(np.linspace(50, 40, 40))
    board = panneau({"BTCUSDT": btc, "AAAUSDT": other}, univers_toujours(["BTCUSDT", "AAAUSDT"]))
    days = pd.bdate_range("2018-12-01", "2019-02-15").strftime("%Y-%m-%d").tolist()
    s = serie("zoo_marches:SP500", days, np.arange(len(days), dtype=float) + 100, lambda d: available_at_fred(
        "SP500", d))
    test = PAR_ID["AM01_1j"]
    ctx = Contexte("", DAY_H, test.params, 0, externes={"zoo_marches:SP500": s})
    w = AM.btc_si(board, ctx)
    k = board.symboles.index("BTCUSDT")
    assert (w.poids[:, 1 - k] == 0).all() and (w.poids[:, k] == 1.0).all()             # série toujours en hausse
    assert w.univers[:, k].all() and not w.univers[:, 1 - k].any()
    assert (w.execution_h == (board.jours + 2) * DAY_H).all()                         # échange à la clôture du jour
    empty = AM.btc_si(board, Contexte("", DAY_H, test.params, 0, externes={}))
    assert (empty.poids == 0).all()


def test_am_regle_inconnue_refusee():
    with pytest.raises(KeyError):
        AM.condition(None, np.zeros(1, dtype=np.int64), "rendement 21 séances > 0")


# --- SE ------------------------------------------------------------------------------------------------------------

def test_se01_passage_sous_25_connu_le_lendemain():
    days = pd.date_range("2019-01-01", periods=8, freq="D").strftime("%Y-%m-%d").tolist()
    values = [30.0, 26.0, 24.0, 20.0, 30.0, 25.0, 10.0, 40.0]
    s = serie("zoo_fear_greed:fear_greed", days, values, available_at_fng)
    assert s.disponible[0] == ts("2019-01-01 02:00")
    frame = journalieres(np.ones(8) * 100.0)              # journée d décidée à d + 1 00:00:02 : voit la valeur de d
    ctx = Contexte("AAAUSDT", DAY_H, PAR_ID["SE01_1j"].params, 0, externes={SE.CLE: s})
    out = SE.peur_extreme(frame, ctx)
    # valeur du jour d connue à la clôture du jour d : passages sous 25 les jours 2 (24) et 6 (10) ; 25 n'est pas < 25
    assert np.flatnonzero(out.entree).tolist() == [2, 6] and out.sortie is None


def test_se02_avidite_strictement_au_dessus_de_75():
    pairs = {s: journalieres(np.ones(60) * 10.0, debut="2019-01-01") for s in ("AAAUSDT", "BBBUSDT")}
    board = panneau(pairs, univers_toujours(list(pairs)))
    days = pd.date_range("2018-12-20", periods=80, freq="D")
    values = np.where(days < pd.Timestamp("2019-01-20"), 80.0, 75.0)
    s = serie("zoo_fear_greed:fear_greed", days.strftime("%Y-%m-%d").tolist(), values, available_at_fng)
    w = SE.avidite(board, Contexte("", DAY_H, PAR_ID["SE02_1j"].params, 0, externes={SE.CLE: s}))
    decided = pd.to_datetime(w.decisions, utc=True)
    greedy = decided <= pd.Timestamp("2019-01-20 00:00:02", tz="UTC")   # la valeur du 19 (80) sert le 20 à 00:00
    assert (w.poids[greedy] == 0).all()
    assert np.allclose(w.poids[~greedy], 0.5)                           # 75 ne dépasse pas 75


# --- CA : heures de New York, été et hiver -------------------------------------------------------------------------

def macro(rows) -> pd.DataFrame:
    return pd.DataFrame([{"event": e, "key": k, "scheduled": s} for e, k, s in rows])


@pytest.mark.parametrize("evenement,heure_ny,cle,attendu", [
    ("FOMC", "14:00", "2019-01-30", "2019-01-30 19:00"),     # hiver (EST)
    ("FOMC", "14:00", "2019-07-31", "2019-07-31 18:00"),     # été (EDT)
    ("CPI", "08:30", "2019-03-11", "2019-03-11 12:30"),      # lendemain du passage à l'heure d'été (10 mars)
    ("NFP", "08:30", "2019-11-04", "2019-11-04 13:30"),      # lendemain du retour à l'heure d'hiver (3 novembre)
])
def test_ca_publication_new_york_zoneinfo(evenement, heure_ny, cle, attendu):
    assert CA.publications_ns(macro([(evenement, cle, True)]), evenement, heure_ny).tolist() == [ts(attendu)]


def _positions_ca(test_id: str, rows, debut: str, heures: int, manquantes=()):
    test = PAR_ID[test_id]
    p = paire(np.linspace(100, 101, heures), start=debut, manquantes=manquantes)
    ctx = Contexte(p.symbole, 1, test.params, 0, evenements={CA.MACRO: macro(rows)})
    sig = CA.veille_publication(p.h1, ctx)
    stage = etape(debut, str((pd.Timestamp(debut) + pd.Timedelta(hours=heures)).date()))
    member = np.ones(len(p.h1), dtype=bool)
    pos = simuler(p.symbole, sig, p.h1, p.grille, member, stage, test.n_max)
    return sig, pos, p


@pytest.mark.parametrize("test_id,cle,entree,sortie", [
    ("CA09_1h", "2019-01-30", "2019-01-29 19:00", "2019-01-30 19:00"),   # FOMC hiver : 19:00 UTC
    ("CA09_1h", "2019-07-31", "2019-07-30 18:00", "2019-07-31 18:00"),   # FOMC été : 18:00 UTC
    ("CA10_1h", "2019-01-15", "2019-01-14 13:00", "2019-01-15 13:00"),   # CPI hiver : 13:30 → 13:00
    ("CA10_1h", "2019-07-11", "2019-07-10 12:00", "2019-07-11 12:00"),   # CPI été : 12:30 → 12:00
    ("CA11_1h", "2019-03-11", "2019-03-10 12:00", "2019-03-11 12:00"),   # NFP fictif au passage à l'heure d'été
    ("CA11_1h", "2019-11-04", "2019-11-03 13:00", "2019-11-04 13:00"),   # NFP fictif au retour à l'heure d'hiver
])
def test_ca_fenetre_entree_sortie_par_le_moteur(test_id, cle, entree, sortie):
    event = PAR_ID[test_id].params["evenement"]
    debut = str((pd.Timestamp(cle) - pd.Timedelta(days=5)).date())
    _, pos, _ = _positions_ca(test_id, [(event, cle, True), ("FOMC", "2019-02-14", False)], debut, 24 * 12)
    assert len(pos) == 1
    assert pos.entree_h[0] == heure(entree) and pos.sortie_h[0] == heure(sortie)
    publication = CA.publications_ns(macro([(event, cle, True)]), event, PAR_ID[test_id].params["heure"])[0]
    assert pos.entree_h[0] * HOUR_NS <= publication - 24 * HOUR_NS            # au plus tard 24 h avant
    assert pos.sortie_h[0] * HOUR_NS <= publication                            # au plus tard à la publication
    assert (pos.entree_h[0] + 1) * HOUR_NS > publication - 24 * HOUR_NS       # la DERNIÈRE bougie qui le permet


def test_ca_reunion_non_programmee_ignoree_et_trous():
    debut = "2019-01-25"
    sig, pos, p = _positions_ca("CA09_1h", [("FOMC", "2019-01-30", False)], debut, 24 * 10)
    assert not sig.entree.any() and len(pos) == 0
    # bougie qui se clôt à E − 1 h absente : aucun signal (déclaré)
    gap = heure("2019-01-29 17:00") - heure(debut)            # bougie 17:00-18:00 : se clôt à 18:00 = E − 1 h
    sig, pos, _ = _positions_ca("CA09_1h", [("FOMC", "2019-01-30", True)], debut, 24 * 10, manquantes=[gap])
    assert not sig.entree.any() and len(pos) == 0
    # bougie de la sortie voulue absente : sortie à la 1re clôture disponible (règle du moteur)
    gap = heure("2019-01-30 18:00") - heure(debut)            # bougie 18:00-19:00 : se clôt à X = 19:00
    _, pos, _ = _positions_ca("CA09_1h", [("FOMC", "2019-01-30", True)], debut, 24 * 10, manquantes=[gap])
    assert pos.sortie_h.tolist() == [heure("2019-01-30 20:00")]


def test_ca13_cycle_lunaire():
    frame = journalieres(np.ones(40) * 100.0, debut="2019-01-01")
    moons = pd.DataFrame({"event": ["NOUVELLE_LUNE", "PLEINE_LUNE", "NOUVELLE_LUNE"],
                          "event_time": pd.to_datetime(["2019-01-06 01:28", "2019-01-21 05:16", "2019-02-04 21:03"],
                                                       utc=True)})
    ctx = Contexte("AAAUSDT", DAY_H, {}, 0, evenements={CA.CRYPTO: moons})
    sig = CA.cycle_lunaire(frame, ctx)
    assert np.flatnonzero(sig.entree).tolist() == [5, 34]            # journées du 6 janvier et du 4 février
    assert np.flatnonzero(sig.sortie).tolist() == [20]               # journée du 21 janvier
    p = paire(np.ones(24 * 40) * 100.0, start="2019-01-01")
    pos = simuler("AAAUSDT", sig, frame, p.grille, np.ones(40, dtype=bool), etape("2019-01-01", "2019-02-10"), None)
    assert pos.entree_h[0] == heure("2019-01-08 00:00") and pos.sortie_h[0] == heure("2019-01-23 00:00")
    missing = journalieres(np.ones(40) * 100.0, debut="2019-01-01", manquantes=[5])
    assert np.flatnonzero(CA.cycle_lunaire(missing, ctx).entree).tolist() == [33]     # journée absente : rien


# --- EV02 ----------------------------------------------------------------------------------------------------------

def annonces(rows) -> pd.DataFrame:
    return pd.DataFrame([{"category": c, "detail": d, "tickers": t, "event_time": pd.Timestamp(a, tz="UTC")
                          - pd.Timedelta(minutes=10), "available_at": pd.Timestamp(a, tz="UTC")}
                         for c, d, t, a in rows])


def _ev02(rows, symbole: str = "FOOUSDT"):
    p = paire(np.ones(24 * 10) * 2.0, start="2021-05-11 10:00", symbole=symbole)
    frame = p.unite(DAY_H)
    ctx = Contexte(symbole, DAY_H, PAR_ID["EV02_1j"].params, 0, evenements={EV.ANNONCES: annonces(rows)})
    return EV.cotation_binance(frame, ctx), frame


def test_ev02_premiere_journee_complete_annonce_connue():
    sig, frame = _ev02([("COTATION_SPOT", "Binance Will List Foo (FOO)", "FOO", "2021-05-10 09:00")])
    assert frame.ouverture_h[0] == heure("2021-05-12 00:00")            # le 11 (10:00 → 24:00) est incomplet
    assert sig.entree.tolist()[:2] == [True, False] and sig.sortie is None
    assert PAR_ID["EV02_1j"].n_max == 30


@pytest.mark.parametrize("rows", [
    [("COTATION_SPOT", "Binance Will List Foo (FOO)", "FOO", "2021-05-13 00:00:03")],   # connue après la décision
    [("NOUVELLES_PAIRES", "Binance Adds FOO/USDT Trading Pair", "", "2021-05-10 09:00")],  # autre catégorie
    [("COTATION_SPOT", "Binance Will List Bar (BAR)", "BAR", "2021-05-10 09:00")],        # autre paire
    [("COTATION_SPOT", "Foo Trading on Binance", "", "2021-05-10 09:00")],                # aucun mot en capitales
])
def test_ev02_annonce_absente_tardive_ou_autre(rows):
    sig, _ = _ev02(rows)
    assert not sig.entree.any()


def test_ev02_titre_sans_ticker_et_table_synthetique():
    sig, _ = _ev02([("COTATION_SPOT", "FOO Trading on Binance", "", "2021-05-10 09:00")])
    assert sig.entree[0]
    p = paire(np.ones(24 * 10) * 2.0, start="2021-05-11 10:00", symbole="FOOUSDT")
    synth = pd.DataFrame({"symbol": ["FOOUSDT"], "event_time": [pd.Timestamp("2021-05-12 23:00", tz="UTC")],
                          "available_at": [pd.Timestamp("2021-05-12 23:10", tz="UTC")], "reelle": [False]})
    ctx = Contexte("FOOUSDT", DAY_H, PAR_ID["EV02_1j"].params, 0, evenements={EV.ANNONCES: synth})
    assert EV.cotation_binance(p.unite(DAY_H), ctx).entree[0]


# --- RG ------------------------------------------------------------------------------------------------------------

def test_rg04_rs_et_hurst_a_la_main():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    # écarts −1,5 −0,5 0,5 1,5 ; cumuls −1,5 −2 −1,5 0 : R = 2 ; S = √1,25
    assert RG._rs_moyen(x, 4) == pytest.approx(2 / math.sqrt(1.25))
    assert RG._rs_moyen(x, 2) == pytest.approx(1.0)                 # [1, 2] et [3, 4] : R = S = 0,5
    h = RG._hurst_glissant(x, 4, np.array([2, 4]))
    assert np.isnan(h[:3]).all() and h[3] == pytest.approx(math.log(2 / math.sqrt(1.25)) / math.log(2))


def _hurst_reference(r: np.ndarray, sizes=RG.HURST_TAILLES) -> float:
    ly = []
    for n in sizes:
        values = []
        for k in range(len(r) // n):
            seg = r[k * n:(k + 1) * n]
            dev = np.cumsum(seg - seg.mean())
            values.append((dev.max() - min(dev.min(), 0.0)) / seg.std())
        ly.append(np.log(np.mean(values)))
    return float(np.polyfit(np.log(sizes), ly, 1)[0])


def test_rg04_signal_contre_une_reference_independante():
    rng = np.random.default_rng(4)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 300)))
    h = RG.exposant_hurst(close)
    r = np.diff(np.log(close))
    assert np.isnan(h[:128]).all()
    for t in (128, 200, 299):
        assert h[t] == pytest.approx(_hurst_reference(r[t - 128:t]), abs=1e-12)
    frame = journalieres(close)
    sig = RG.hurst(frame, Contexte("A", DAY_H, PAR_ID["RG04_1j"].params, 0))
    ret = close[20:] / close[:-20] - 1
    expected = (h[20:] > 0.5) & (ret > 0)
    assert (sig.entree[20:] == (expected & ~np.isnan(h[20:]))).all()
    assert (sig.sortie[128:] == ~sig.entree[128:]).all() and not sig.sortie[:128].any()


def test_rg05_entropie_de_permutation_a_la_main():
    assert RG.entropie_permutation(np.arange(1.0, 6.0), 3, 5)[-1] == 0.0          # un seul motif
    pe = RG.entropie_permutation(np.array([1.0, 3.0, 2.0, 4.0, 3.0, 5.0]), 3, 6)
    assert np.isnan(pe[:5]).all() and pe[5] == pytest.approx(math.log(2) / math.log(6))
    codes = RG._motifs_ordre3(np.array([1.0, 1.0, 1.0, 3.0, 2.0, 1.0]))
    assert codes[2] == RG._CODES3[0]                                             # égalités : ordre d'apparition
    assert codes[5] == RG._CODES3[5]                                             # 3 > 2 > 1 : décroissant
    with pytest.raises(ValueError):
        RG.entropie_permutation(np.ones(10), 4, 6)


def test_rg05_tiers_bas_des_valeurs_precedentes():
    # précédentes {3, 1, 2} : quantile 1/3 interpolé = 1 + (2/3)(2 − 1) = 1,667
    out = RG._sous_tiers_bas(np.array([3.0, 1.0, 2.0, 1.5]), 3, 1 / 3)
    assert np.isnan(out[:3]).all() and out[3] == 1.0
    assert RG._sous_tiers_bas(np.array([3.0, 1.0, 2.0, 1.7]), 3, 1 / 3)[3] == 0.0


def test_rg06_pente_de_haar_a_la_main():
    close = np.exp(np.arange(16.0))
    slope = RG.pente_haar(close, 3)
    assert np.isnan(slope[:15]).all() and slope[15] == pytest.approx(np.mean(np.arange(8, 16)) - np.mean(np.arange(8)))
    sig = RG.haar(journalieres(np.exp(np.r_[np.arange(16.0), 14.0 - np.arange(20.0)])),
                  Contexte("A", DAY_H, PAR_ID["RG06_1j"].params, 0))
    assert sig.entree[15] and not sig.sortie[15] and sig.sortie[-1] and not sig.entree[-1]


def _kalman_reference(y: np.ndarray, q_eta: float, q_zeta: float) -> tuple[float, np.ndarray]:
    """Filtre de Kalman matriciel (numpy), indépendant de l'implémentation du zoo."""
    T = np.array([[1.0, 1.0], [0.0, 1.0]])
    Q = np.diag([q_eta, q_zeta])
    a = np.array([y[0], 0.0])
    P = np.eye(2) * RG.KALMAN_DIFFUS
    vs, fs, slopes = [], [], []
    for t, obs in enumerate(y):
        v = obs - a[0]
        f = P[0, 0] + 1.0
        K = P[:, 0] / f
        a_f = a + K * v
        P_f = P - np.outer(K, K) * f
        slopes.append(a_f[1])
        if t >= 2:
            vs.append(v)
            fs.append(f)
        a, P = T @ a_f, T @ P_f @ T.T + Q
    vs_a, fs_a = np.array(vs), np.array(fs)
    sigma2 = np.mean(vs_a ** 2 / fs_a)
    return -0.5 * (len(vs_a) * np.log(sigma2) + np.log(fs_a).sum()), np.array(slopes)


def test_rg03_vraisemblance_et_filtre_contre_une_reference():
    rng = np.random.default_rng(8)
    y = np.cumsum(np.cumsum(rng.normal(0, 0.001, 200)) + rng.normal(0, 0.01, 200)) + 4.0
    ll, slopes = _kalman_reference(y, 0.05, 0.001)
    assert RG._kalman_log_vraisemblance(y, math.log(0.05), math.log(0.001)) == pytest.approx(ll, rel=1e-9)
    # estimation : la vraisemblance au point trouvé n'est pas plus basse qu'au départ ni qu'en des points voisins
    e0, e1 = RG._nelder_mead_kalman(y, *RG.KALMAN_DEPART, *RG.KALMAN_BORNES)
    best = RG._kalman_log_vraisemblance(y, e0, e1)
    assert best >= RG._kalman_log_vraisemblance(y, *RG.KALMAN_DEPART)
    for d0, d1 in ((0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5)):
        assert best >= RG._kalman_log_vraisemblance(y, e0 + d0, e1 + d1) - 1e-9
    out = RG._kalman_pente(y, np.array([199]), 200, *RG.KALMAN_DEPART, *RG.KALMAN_BORNES)
    assert np.isnan(out[:199]).all()
    assert out[199] == pytest.approx(_kalman_reference(y, math.exp(e0), math.exp(e1))[1][-1], rel=1e-9)


def test_rg03_pente_sur_une_tendance_et_reestimation_mensuelle():
    frame = journalieres(np.exp(0.002 * np.arange(500) + 3.0), debut="2019-01-01")
    starts = RG.indices_debut_de_mois(frame.disponible)
    dates = pd.to_datetime(frame.disponible[starts], utc=True)
    assert starts[0] == 0 and (dates[1:].day == 1).all() and (dates[1:].hour == 0).all()   # 1re décision du mois
    slope = RG.pente_kalman(frame.cloture, frame.disponible)
    first = starts[starts >= 364][0]
    assert np.isnan(slope[:first]).all() and not np.isnan(slope[first:]).any()
    assert slope[first:] == pytest.approx(0.002, rel=1e-3)
    sig = RG.kalman(journalieres(np.exp(-0.002 * np.arange(500) + 3.0)), Contexte("A", DAY_H, {}, 0))
    assert not sig.entree.any() and sig.sortie[first:].all()


def test_rg02_filtre_avant_contre_une_reference_et_etat_volatil():
    rng = np.random.default_rng(5)
    x = np.r_[rng.normal(0, 0.01, 500), rng.normal(0, 0.06, 150), rng.normal(0, 0.01, 200)]
    start, trans, mean, var = RG._baum_welch(x, *RG.hmm_depart(x, 3), 100, 1e-6)
    filtered, _ = RG._avant(RG._emissions(x, mean, var), start, trans)
    # référence : récurrence avant écrite directement
    p = start.copy()
    for t in range(len(x)):
        if t:
            p = p @ trans
        dens = np.exp(-0.5 * (x[t] - mean) ** 2 / var) / np.sqrt(2 * np.pi * var) + 1e-300
        p = p * dens / (p * dens).sum()
    assert filtered[-1] == pytest.approx(p, abs=1e-12)
    hot = int(np.argmax(var))
    assert filtered[640, hot] > 0.5 and filtered[-1, hot] < 0.5


def test_rg02_poids_hebdomadaires_et_btc_absent():
    rng = np.random.default_rng(6)
    sigma = np.r_[np.full(500, 0.01), np.full(150, 0.06), np.full(200, 0.01)]
    btc = journalieres(100 * np.exp(np.cumsum(rng.normal(0, 1, 850) * sigma)), debut="2017-01-01")
    alt = journalieres(np.ones(850) * 5.0, debut="2017-01-01")
    board = panneau({"BTCUSDT": btc, "AAAUSDT": alt}, univers_toujours(["BTCUSDT", "AAAUSDT"], debut="2016-12-01"))
    w = RG.hmm3(board, Contexte("", DAY_H, PAR_ID["RG02_1j"].params, 0))
    invested = w.poids.sum(axis=1) > 0
    weeks = pd.to_datetime(w.decisions, utc=True)
    # agitation du 2018-05-16 au 2018-10-13 : USDT dès que le réajustement mensuel l'a vue (juin), puis investi
    hot = (weeks > pd.Timestamp("2018-06-01", tz="UTC")) & (weeks < pd.Timestamp("2018-10-10", tz="UTC"))
    assert not invested[hot].any()
    assert invested[weeks < pd.Timestamp("2018-05-20", tz="UTC")].all()
    assert invested[weeks > pd.Timestamp("2018-11-01", tz="UTC")].all()
    assert np.allclose(w.poids[invested], 0.5)
    alone = panneau({"AAAUSDT": alt}, univers_toujours(["AAAUSDT"], debut="2016-12-01"))
    assert np.allclose(RG.hmm3(alone, Contexte("", DAY_H, PAR_ID["RG02_1j"].params, 0)).poids, 1.0)


def test_rg07_sinusoide_d_ehlers_sur_un_cycle_pur():
    t = np.arange(600)
    mid = 100 + 5 * np.sin(2 * np.pi * t / 20)
    frame = Bougies(DAY_H, t * DAY_H, (t + 1) * DAY_H * HOUR_NS + LATENCE_NS, mid, mid + 0.5, mid - 0.5, mid,
                    np.ones(600), np.ones(600), np.ones(600))
    sig = RG.sinusoide_ehlers(frame, Contexte("A", DAY_H, {}, 0))
    entries = np.flatnonzero(sig.entree)
    assert entries[0] >= RG.EHLERS_CHAUFFE
    gaps = np.diff(entries[entries > 200])
    assert np.median(gaps) == pytest.approx(20, abs=1)              # un achat par cycle de 20 bougies
    exits = np.flatnonzero(sig.sortie)
    assert len(exits) == pytest.approx(len(entries), abs=1)
    sine, lead = RG.sinusoide(frame.haut, frame.bas)
    assert np.isnan(sine[:RG.EHLERS_CHAUFFE]).all() and np.isnan(lead[:RG.EHLERS_CHAUFFE]).all()
    assert np.abs(sine[RG.EHLERS_CHAUFFE:]).max() <= 1.0
