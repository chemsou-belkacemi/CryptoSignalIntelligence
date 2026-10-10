"""Pipeline du zoo, LE MÊME pour le contrôle sous H0 (marché synthétique) et l'exécution réelle (magasin long) :
signaux → positions (appartenance à date) → mesure par étape. Plus le comptage à blanc (aucun rendement).

Deux passes par source de données :
1. références : grilles de clôtures 1 h, bougies journalières (panneau de la forme b), rendements quotidiens des
   paires et univers → `JoursEtape` de chaque étape (`m(p)`, `U(d)`), calculés après coup, hors de tout signal ;
2. tests : pour chaque paire, signaux de chaque test de forme (a) (unité d'exécution, `Contexte`), positions par
   étape, rendements quotidiens accumulés ; forme (b) : poids sur le panneau, mesure du portefeuille.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np
import pandas as pd

from .data import DAY_H, Bougies, Etape, Grille, Paire, SerieExterne, Univers, mois_du_jour
from .measure import JoursEtape, MesureA, MesureB, jours_etape, m_derniere_heure, mesurer_a, mesurer_b
from .positions import Positions, exposition_jours, simuler, voisins
from .spec import FORME_A, FORME_B, MEME_HEURE, Contexte, Panneau, Signaux, Test


class Source:
    """Source de paires (magasin long coupé, ou marché synthétique)."""
    nom = "source"
    symboles: list[str]
    univers: Univers
    externes: dict[str, SerieExterne]
    evenements: dict[str, pd.DataFrame]

    def paire(self, symbole: str) -> Paire | None:   # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class References:
    grilles: dict[str, Grille]
    quotidien: dict[str, Bougies]
    jours: dict[str, JoursEtape]
    univers: Univers
    empreintes: dict[str, str] = field(default_factory=dict)


def references(source: Source, etapes: Iterable[Etape], *, avec_vo11: bool = False,
               progress: Callable[[str], None] | None = None) -> References:
    say = progress or (lambda _t: None)
    grids, daily, prints = {}, {}, {}
    prepared = getattr(source, "pre_references", None)
    if prepared is not None:
        grids, daily, prints = prepared()
    for k, sym in enumerate([] if prepared is not None else source.symboles):
        if k % 25 == 0:
            say(f"références {k}/{len(source.symboles)}")
        pair = source.paire(sym)
        if pair is None:
            continue
        grids[sym] = pair.grille
        daily[sym] = pair.unite(DAY_H)
        prints[sym] = pair.empreinte()
    jours = {}
    for etape in etapes:
        je = jours_etape(etape, grids, source.univers)
        if avec_vo11:
            je.m_heure = m_derniere_heure(je, grids)
        jours[etape.nom] = je
    return References(grids, daily, jours, source.univers, prints)


def contexte(test: Test, paire: Paire, etape: Etape, source: Source) -> Contexte:
    unit = test.execution_heures
    return Contexte(paire.symbole, unit, test.params, etape.debut_ns,
                    quotidien=paire.unite(DAY_H) if unit != DAY_H else None,
                    externes=MappingProxyType(source.externes), evenements=MappingProxyType(source.evenements),
                    univers=source.univers)


def _depend_etape(test: Test) -> bool:
    import importlib
    module, name = test.fonction.split(":")
    mod = importlib.import_module(f"{__package__}.strategies.{module}")
    return name in getattr(mod, "DEPENDENT_ETAPE", frozenset())


def signaux(test: Test, paire: Paire, etape: Etape, source: Source) -> Signaux:
    out = test.strategie_fonction()(paire.unite(test.execution_heures), contexte(test, paire, etape, source))
    if not isinstance(out, Signaux):
        raise TypeError(f"{test.id} : la stratégie doit rendre des Signaux (forme a)")
    if len(out.entree) != len(paire.unite(test.execution_heures)):
        raise ValueError(f"{test.id} : signaux non alignés sur les bougies")
    return out


def positions_paire(tests: list[Test], paire: Paire, etapes: list[Etape], source: Source) -> dict[tuple[str, str], Positions]:
    """Positions de chaque test de forme (a) sur la paire, par étape (aucun rendement)."""
    out: dict[tuple[str, str], Positions] = {}
    neighbours = voisins(paire.grille)
    members: dict[int, np.ndarray] = {}
    for test in tests:
        unit = test.execution_heures
        bars = paire.unite(unit)
        if len(bars) == 0:
            for etape in etapes:
                out[(test.id, etape.nom)] = Positions.vide(paire.symbole)
            continue
        if unit not in members:
            members[unit] = source.univers.membre(paire.symbole, bars.disponible)
        shared = None if _depend_etape(test) else signaux(test, paire, etapes[0], source)
        for etape in etapes:
            sig = shared if shared is not None else signaux(test, paire, etape, source)
            out[(test.id, etape.nom)] = simuler(paire.symbole, sig, bars, paire.grille, members[unit], etape,
                                                test.n_max, neighbours)
    return out


def fusionner(a: MesureA | None, b: MesureA) -> MesureA:
    if a is None:
        return b
    for key, value in b.compteurs.items():
        a.compteurs[key] = a.compteurs.get(key, 0) + value
    a.g += b.g
    a.e += b.e
    a.ref += b.ref
    a.eu += b.eu
    for scenario in a.c:
        a.c[scenario] += b.c[scenario]
    a.paires.update(b.paires)
    a.transactions.update(b.transactions)
    return a


def mesurer_paires(tests: list[Test], symboles: Iterable[str], source: Source, refs: References,
                   etapes: list[Etape], *, garder_positions: bool = False, garder_transactions: bool = True,
                   progress: Callable[[str], None] | None = None) -> tuple[dict[tuple[str, str], MesureA], dict]:
    """Forme (a) : mesure de chaque test et chaque étape, paire par paire (mémoire bornée)."""
    say = progress or (lambda _t: None)
    by_id = {t.id: t for t in tests}
    results: dict[tuple[str, str], MesureA] = {}
    kept: dict = {}
    for k, sym in enumerate(symboles):
        if k % 10 == 0:
            say(f"paires {k}")
        pair = source.paire(sym)
        if pair is None:
            continue
        grids = {sym: pair.grille}
        for (test_id, etape_nom), pos in positions_paire(tests, pair, etapes, source).items():
            partial = mesurer_a(by_id[test_id], {sym: pos}, grids, refs.jours[etape_nom],
                                garder_transactions=garder_transactions)
            results[(test_id, etape_nom)] = fusionner(results.get((test_id, etape_nom)), partial)
            if garder_positions:
                kept.setdefault((test_id, etape_nom), {})[sym] = pos
    return results, kept


# --- Forme (b) ----------------------------------------------------------------------------------------------------

def panneau(quotidien: dict[str, Bougies], univers: Univers, jusqu_a_jour: int | None = None) -> Panneau:
    """Panneau journalier (jours × paires) ; `membre[j]` = top 40 du mois de la décision prise à la clôture du jour j
    (mois du jour j + 1)."""
    symbols = tuple(sorted(quotidien))
    first = min(int(b.ouverture_h[0]) // DAY_H for b in quotidien.values() if len(b))
    last = max(int(b.ouverture_h[-1]) // DAY_H for b in quotidien.values() if len(b))
    if jusqu_a_jour is not None:
        last = min(last, jusqu_a_jour)
    days = np.arange(first, last + 1, dtype=np.int64)
    shape = (len(days), len(symbols))
    fields = {f: np.full(shape, np.nan) for f in ("ouverture", "haut", "bas", "cloture", "volume", "achat")}
    for k, sym in enumerate(symbols):
        bars = quotidien[sym]
        idx = bars.ouverture_h // DAY_H - first
        ok = (idx >= 0) & (idx < len(days))
        for f in fields:
            fields[f][idx[ok], k] = getattr(bars, f)[ok]
    months = mois_du_jour(days + 1)
    member = np.zeros(shape, dtype=bool)
    for j, month in enumerate(months):
        chosen = univers.membres_du_mois(int(month))
        if chosen:
            member[j] = [s in chosen for s in symbols]
    return Panneau(days, symbols, fields["ouverture"], fields["haut"], fields["bas"], fields["cloture"],
                   fields["volume"], fields["achat"], member)


def mesurer_portefeuilles(tests: list[Test], refs: References, etapes: list[Etape],
                          source: Source) -> dict[tuple[str, str], MesureB]:
    out = {}
    for etape in etapes:
        board = panneau(refs.quotidien, refs.univers, jusqu_a_jour=etape.fin_jour)
        for test in tests:
            ctx = Contexte("", DAY_H, test.params, etape.debut_ns, externes=MappingProxyType(source.externes),
                           evenements=MappingProxyType(source.evenements), univers=refs.univers)
            weights = test.strategie_fonction()(board, ctx)
            out[(test.id, etape.nom)] = mesurer_b(test, weights, refs.grilles, etape)
    return out


def mesurer_tout(tests: list[Test], source: Source, etapes: list[Etape], *, garder_transactions: bool = True,
                 progress: Callable[[str], None] | None = None) -> tuple[dict, References]:
    """Tous les tests (formes a et b) sur une source, toutes les étapes : {(test, étape) : mesure}."""
    refs = references(source, etapes, avec_vo11=any(t.reference == MEME_HEURE for t in tests), progress=progress)
    form_a = [t for t in tests if t.forme == FORME_A]
    form_b = [t for t in tests if t.forme == FORME_B]
    results: dict = {}
    if form_a:
        measured, _ = mesurer_paires(form_a, list(refs.grilles), source, refs, etapes,
                                     garder_transactions=garder_transactions, progress=progress)
        results.update(measured)
    if form_b:
        results.update(mesurer_portefeuilles(form_b, refs, etapes, source))
    return results, refs


# --- Comptage à blanc (aucun rendement) ---------------------------------------------------------------------------

def compter(tests: list[Test], source: Source, etape: Etape, *,
            progress: Callable[[str], None] | None = None) -> dict[str, dict]:
    """Nombres de signaux, de transactions, exposition et blocs avec exposition par test, SANS AUCUN PRIX lu au-delà
    des heures (les positions ne dépendent que des signaux, des heures et de l'appartenance)."""
    from .measure import BLOCS_A, BLOCS_B, MIN_BLOCS, MIN_TRANSACTIONS
    say = progress or (lambda _t: None)
    form_a = [t for t in tests if t.forme == FORME_A]
    form_b = [t for t in tests if t.forme == FORME_B]
    counts: dict[str, dict] = {t.id: {"compteurs": {}, "exposition_par_jour": np.zeros(len(etape.jours()))}
                               for t in form_a}
    daily: dict[str, Bougies] = {}
    for k, sym in enumerate(source.symboles):
        if k % 10 == 0:
            say(f"comptage {k}/{len(source.symboles)}")
        pair = source.paire(sym)
        if pair is None:
            continue
        if form_b:
            daily[sym] = pair.unite(DAY_H)
        for (test_id, _), pos in positions_paire(form_a, pair, [etape], source).items():
            entry = counts[test_id]
            for key, value in pos.compteurs.items():
                entry["compteurs"][key] = entry["compteurs"].get(key, 0) + int(value)
            entry["exposition_par_jour"] += exposition_jours([pos], etape)
    out = {}
    origin = etape.debut_jour
    days = etape.jours()
    for test in form_a:
        expo = counts[test.id].pop("exposition_par_jour")
        blocks = int((np.bincount((days - origin) // BLOCS_A, weights=expo) > 0).sum())
        n = counts[test.id]["compteurs"].get("transactions", 0)
        out[test.id] = {"forme": "a", **counts[test.id], "jours_exposition": round(float(expo.sum()), 2),
                        "blocs_avec_exposition": blocks,
                        "insuffisant_par_construction": n < MIN_TRANSACTIONS or blocks < MIN_BLOCS}
    if form_b:
        board = panneau(daily, source.univers, jusqu_a_jour=etape.fin_jour)
        for test in form_b:
            ctx = Contexte("", DAY_H, test.params, etape.debut_ns, externes=MappingProxyType(source.externes),
                           evenements=MappingProxyType(source.evenements), univers=source.univers)
            weights = test.strategie_fonction()(board, ctx)
            keep = (weights.decisions >= etape.debut_ns) & (weights.decisions <= etape.fin_ns) & \
                (weights.execution_h <= etape.fin_h)
            exposure = weights.poids[keep].sum(axis=1)
            start_days = weights.execution_h[keep] // DAY_H
            blocks = int((np.bincount((start_days - origin) // BLOCS_B, weights=exposure + 1e-12) > 0).sum()) \
                if keep.any() else 0
            out[test.id] = {"forme": "b", "echanges": int(keep.sum()), "echanges_investis": int((exposure > 0).sum()),
                            "exposition_moyenne": round(float(exposure.mean()), 4) if keep.any() else 0.0,
                            "paires_moyennes": round(float((weights.poids[keep] > 0).sum(axis=1).mean()), 2)
                            if keep.any() else 0.0, "blocs": blocks,
                            "insuffisant_par_construction": blocks < MIN_BLOCS}
    return out


def tests_codes(tests: list[Test]) -> tuple[list[Test], list[Test]]:
    """(codés, à coder) selon l'ensemble `IMPLEMENTEES` de chaque module."""
    done = [t for t in tests if t.etat_code == "code"]
    return done, [t for t in tests if t.etat_code != "code"]
