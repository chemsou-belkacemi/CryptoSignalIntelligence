"""Positions du zoo (docs/ZOO.md § 3 et § 4.1) : règles de bord écrites à la main, AUCUN rendement."""
from __future__ import annotations

import numpy as np

from crypto_signal_intelligence.research.zoo.data import HOUR_NS
from crypto_signal_intelligence.research.zoo.positions import (
    COTATION_ARRETEE,
    FIN_ETAPE,
    PLAFOND,
    REGLE,
    exposition_jours,
    simuler,
)
from crypto_signal_intelligence.research.zoo.spec import Signaux

from .zoo_outils import etape, heure, paire, univers_mois, univers_toujours

STAGE = etape("2019-01-01", "2019-01-11")


def flags(n: int, on=()) -> np.ndarray:
    out = np.zeros(n, dtype=bool)
    out[list(on)] = True
    return out


def run(p, entree, sortie=None, n_max=None, stage=STAGE, member=None):
    bars = p.unite(1)
    members = np.ones(len(bars), dtype=bool) if member is None else member
    return simuler(p.symbole, Signaux(entree, sortie), bars, p.grille, members, stage, n_max)


def test_entree_a_la_cloture_suivante_et_sortie_a_la_cloture_apres_la_regle():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5]), flags(100, [9]))
    t0 = heure("2019-01-01")
    assert len(pos) == 1
    assert pos.entree_h[0] == t0 + 7          # signal à la clôture de la bougie 5 (heure 6) → entrée à la suivante
    assert pos.sortie_h[0] == t0 + 11         # sortie décidée à la clôture 9 (heure 10) → exécutée à la suivante
    assert pos.raison[0] == REGLE


def test_plafond_n_bougies_apres_l_entree():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5]), None, n_max=10)
    assert pos.sortie_h[0] - pos.entree_h[0] == 10 and pos.raison[0] == PLAFOND


def test_regle_apres_le_plafond_ignoree():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5]), flags(100, [40]), n_max=10)
    assert pos.raison[0] == PLAFOND


def test_signal_pendant_une_position_ignore_et_compte():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5, 6, 7, 30]), flags(100, [9]))
    assert len(pos) == 2
    assert pos.compteurs["ignores_position_ouverte"] == 2
    assert pos.compteurs["signaux"] == 4


def test_nouveau_signal_permis_a_la_cloture_de_sortie():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5, 10]), flags(100, [9]))
    # sortie à la clôture de la bougie 10 ; le signal de la bougie 10 (décidé après cette clôture) ouvre une position
    assert len(pos) == 2 and pos.entree_h[1] == pos.sortie_h[0] + 1


def test_non_membre_ecarte_et_position_ouverte_continue_hors_du_top():
    t0 = "2019-01-31 20:00"
    p = paire(np.linspace(100, 110, 100), start=t0)
    members = univers_mois({"2019-01": [p.symbole]})
    bars = p.unite(1)
    member = members.membre(p.symbole, bars.disponible)
    stage = etape("2019-01-01", "2019-03-01")
    pos = simuler(p.symbole, Signaux(flags(100, [0, 30]), None), bars, p.grille, member, stage, 20)
    assert len(pos) == 1 and pos.compteurs["non_membre"] == 1
    assert pos.sortie_h[0] - pos.entree_h[0] == 20    # la position va jusqu'à sa sortie en février


def test_entree_dans_un_trou_ecartee_et_sortie_dans_un_trou_a_la_premiere_cloture():
    p = paire(np.linspace(100, 110, 100), manquantes=[6, 20, 21, 22])
    bars = p.unite(1)
    # signal à la bougie d'indice 5 : la bougie suivante (heure 6) manque → entrée écartée
    pos = run(p, flags(len(bars), [5]), None, n_max=3)
    assert len(pos) == 0 and pos.compteurs["entree_dans_un_trou"] == 1
    # entrée à l'heure 17 (signal sur la bougie 16 → indice 15), plafond à 21 (trou) → première clôture disponible : 24
    t0 = heure("2019-01-01")
    pos = run(p, flags(len(bars), [15]), None, n_max=4)
    assert pos.entree_h[0] == t0 + 18 and pos.sortie_h[0] == t0 + 24


def test_fin_d_etape_et_entree_apres_la_fin():
    p = paire(np.linspace(100, 110, 400))
    stage = etape("2019-01-01", "2019-01-05")
    t_end = heure("2019-01-05")
    bars = p.unite(1)
    n = len(bars)
    pos = run(p, flags(n, [10]), None, None, stage=stage)
    assert pos.sortie_h[0] == t_end and pos.raison[0] == FIN_ETAPE
    last = int(np.flatnonzero(bars.cloture_h == t_end)[0])
    pos = run(p, flags(n, [last - 1]), None, None, stage=stage)
    assert len(pos) == 0 and pos.compteurs["duree_nulle"] == 1     # entrée à la dernière clôture = sortie
    pos = run(p, flags(n, [last]), None, None, stage=stage)
    assert pos.compteurs["signaux"] == 0                             # décision après la dernière décision de l'étape


def test_retrait_de_la_cote_vente_a_la_derniere_cloture():
    p = paire(np.linspace(100, 50, 60))
    pos = run(p, flags(60, [10]), None, None)
    assert pos.raison[0] == COTATION_ARRETEE and pos.sortie_h[0] == p.grille.fin_h


def test_duree_nulle_ecartee():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5]), flags(100, [5]))
    assert len(pos) == 0 and pos.compteurs["duree_nulle"] == 1


def test_etat_ouvre_quand_la_paire_entre_dans_le_top_40():
    p = paire(np.linspace(100, 110, 24 * 40), start="2019-01-20")
    members = univers_mois({"2019-02": [p.symbole]})
    bars = p.unite(24)
    state = np.ones(len(bars), dtype=bool)
    stage = etape("2019-01-01", "2019-03-31")
    pos = simuler(p.symbole, Signaux(state, ~state), bars, p.grille, members.membre(p.symbole, bars.disponible),
                  stage, None)
    # première décision de février : clôture du 31 janvier (disponible le 1er février 00:00:02) → entrée le 1er à 24:00
    assert len(pos) == 1
    assert pos.entree_h[0] == heure("2019-02-02")


def test_exposition_jours_comptage_a_blanc():
    p = paire(np.linspace(100, 110, 100))
    pos = run(p, flags(100, [5]), flags(100, [9]))
    expo = exposition_jours([pos], STAGE)
    assert np.isclose(expo.sum(), 4 / 24)
    assert HOUR_NS == 3_600_000_000_000
    _ = univers_toujours(["X"])
