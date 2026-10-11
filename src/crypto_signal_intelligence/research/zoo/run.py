"""Pipeline du zoo, LE MÊME pour le contrôle sous H0 (marché synthétique) et l'exécution réelle (magasin long) :
signaux → positions (appartenance à date) → mesure par étape. Plus le comptage à blanc (aucun rendement).

Deux passes par source de données :
1. références : grilles de clôtures 1 h, bougies journalières (panneau de la forme b), rendements quotidiens des
   paires et univers → `JoursEtape` de chaque étape (`m(p)`, `U(d)`), calculés après coup, hors de tout signal ;
2. tests : pour chaque paire, signaux de chaque test de forme (a) (unité d'exécution, `Contexte`), positions par
   étape, rendements quotidiens accumulés ; forme (b) : poids sur le panneau, mesure du portefeuille.

Univers d'exception (EV02, `test.univers == "recensement"`) : toutes les paires USDT retenues du recensement, bougies
journalières (clôtures) de `data/pit/daily.parquet` (ou leurs doubles synthétiques), SANS condition d'appartenance au
top 40, référence `R180` calculée sur ces mêmes paires.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np
import pandas as pd

from .data import DAY_H, Bougies, Etape, Grille, Paire, SerieExterne, Univers, mois_du_jour
from .measure import JoursEtape, MesureA, MesureB, jours_etape, m_derniere_heure, mesurer_a, mesurer_b, r180
from .positions import Positions, exposition_jours, simuler, voisins
from .spec import FORME_A, FORME_B, MEME_HEURE, R180, Contexte, Panneau, Signaux, Test

RECENSEMENT = "recensement"


class Source:
    """Source de paires (magasin long coupé, ou marché synthétique)."""
    nom = "source"
    symboles: list[str]
    univers: Univers
    externes: dict[str, SerieExterne]
    evenements: dict[str, pd.DataFrame]

    def paire(self, symbole: str) -> Paire | None:   # pragma: no cover - interface
        raise NotImplementedError

    def quotidien_recensement(self) -> dict[str, Bougies]:
        """Bougies journalières (clôtures) des paires USDT retenues du recensement (EV02) ; vide par défaut."""
        return {}

    def paire_recensement(self, symbole: str) -> Paire | None:
        bars = self.quotidien_recensement().get(symbole)
        return None if bars is None or len(bars) == 0 else Paire(symbole, bars)


@dataclass
class References:
    grilles: dict[str, Grille]
    quotidien: dict[str, Bougies]
    jours: dict[str, JoursEtape]
    univers: Univers
    empreintes: dict[str, str] = field(default_factory=dict)


def r180_etape(etape: Etape, quotidien: dict[str, Bougies]) -> np.ndarray:
    """`R180(d)` de l'étape : rendement brut moyen à poids égaux, le jour `d`, des paires du recensement cotées depuis
    plus de 180 jours (première journée ≤ d − 181), rendement de clôture à clôture de deux journées consécutives."""
    days = etape.jours()
    if not quotidien:
        return np.zeros(len(days))
    symbols = sorted(quotidien)
    first = np.array([int(quotidien[s].ouverture_h[0]) // DAY_H if len(quotidien[s]) else 10**9 for s in symbols])
    returns = np.full((len(days), len(symbols)), np.nan)
    for k, s in enumerate(symbols):
        bars = quotidien[s]
        if len(bars) < 2:
            continue
        d = bars.ouverture_h // DAY_H
        ok = d[1:] - d[:-1] == 1
        r = bars.cloture[1:] / bars.cloture[:-1] - 1.0
        idx = d[1:][ok] - days[0]
        keep = (idx >= 0) & (idx < len(days))
        returns[idx[keep], k] = r[ok][keep]
    return r180(days, symbols, first, returns)


def references(source: Source, etapes: Iterable[Etape], *, avec_vo11: bool = False, avec_r180: bool = False,
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
        if avec_r180:
            je.r180 = r180_etape(etape, source.quotidien_recensement())
        jours[etape.nom] = je
    return References(grids, daily, jours, source.univers, prints)


def contexte(test: Test, paire: Paire, etape: Etape, source: Source) -> Contexte:
    """Contexte d'un test sur une paire. `quotidien` est coupé à la dernière décision du cadre, et `quotidien_connu[t]`
    donne la dernière journée connue à la décision `t` (aucun accès par indice à une journée encore ouverte)."""
    unit = test.execution_heures
    daily, known = None, None
    if unit != DAY_H:
        frame = paire.unite(unit)
        daily = paire.unite(DAY_H)
        if len(frame):
            daily = daily.filtre(daily.disponible <= frame.disponible[-1])
        known = np.searchsorted(daily.disponible, frame.disponible, side="right").astype(np.int64) - 1
    return Contexte(paire.symbole, unit, test.params, etape.debut_ns, quotidien=daily, quotidien_connu=known,
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


def positions_paire(tests: list[Test], paire: Paire, etapes: list[Etape], source: Source, *,
                    tout_membre: bool = False) -> dict[tuple[str, str], Positions]:
    """Positions de chaque test de forme (a) sur la paire, par étape (aucun rendement). `tout_membre` : univers
    d'exception (EV02), aucune condition d'appartenance au top 40."""
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
            members[unit] = np.ones(len(bars), dtype=bool) if tout_membre else \
                source.univers.membre(paire.symbole, bars.disponible)
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
                   recensement: bool = False,
                   progress: Callable[[str], None] | None = None) -> tuple[dict[tuple[str, str], MesureA], dict]:
    """Forme (a) : mesure de chaque test et chaque étape, paire par paire (mémoire bornée). `recensement` : paires de
    l'univers d'exception (EV02), sans condition d'appartenance."""
    say = progress or (lambda _t: None)
    by_id = {t.id: t for t in tests}
    results: dict[tuple[str, str], MesureA] = {}
    kept: dict = {}
    for k, sym in enumerate(symboles):
        if k % 10 == 0:
            say(f"paires {k}")
        pair = source.paire_recensement(sym) if recensement else source.paire(sym)
        if pair is None:
            continue
        grids = {sym: pair.grille}
        for (test_id, etape_nom), pos in positions_paire(tests, pair, etapes, source, tout_membre=recensement).items():
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
    results, refs, _ = mesurer_tout_detail(tests, source, etapes, garder_transactions=garder_transactions,
                                           progress=progress)
    return results, refs


def mesurer_tout_detail(tests: list[Test], source: Source, etapes: list[Etape], *, garder_transactions: bool = True,
                        garder_positions: bool = False,
                        progress: Callable[[str], None] | None = None) -> tuple[dict, References, dict]:
    """Comme `mesurer_tout`, et rend aussi les positions (placebos descriptifs de l'exécution réelle)."""
    refs = references(source, etapes, avec_vo11=any(t.reference == MEME_HEURE for t in tests),
                      avec_r180=any(t.reference == R180 for t in tests), progress=progress)
    form_a = [t for t in tests if t.forme == FORME_A and t.univers != RECENSEMENT]
    census = [t for t in tests if t.forme == FORME_A and t.univers == RECENSEMENT]
    form_b = [t for t in tests if t.forme == FORME_B]
    results: dict = {}
    positions: dict = {}
    if form_a:
        measured, kept = mesurer_paires(form_a, list(refs.grilles), source, refs, etapes, garder_positions=garder_positions,
                                        garder_transactions=garder_transactions, progress=progress)
        results.update(measured)
        positions.update(kept)
    if census:
        measured, kept = mesurer_paires(census, sorted(source.quotidien_recensement()), source, refs, etapes,
                                        garder_positions=garder_positions, garder_transactions=garder_transactions,
                                        recensement=True, progress=progress)
        results.update(measured)
        positions.update(kept)
        for test in census:
            for etape in etapes:
                results.setdefault((test.id, etape.nom), mesurer_a(test, {}, {}, refs.jours[etape.nom]))
    if form_b:
        results.update(mesurer_portefeuilles(form_b, refs, etapes, source))
    return results, refs, positions


# --- Comptage à blanc (aucun rendement) ---------------------------------------------------------------------------

def compter(tests: list[Test], source: Source, etape: Etape, *,
            progress: Callable[[str], None] | None = None) -> dict[str, dict]:
    """Nombres de signaux, de transactions, exposition et blocs avec exposition par test, SANS AUCUN PRIX lu au-delà
    des heures (les positions ne dépendent que des signaux, des heures et de l'appartenance)."""
    from .measure import BLOCS_A, MIN_BLOCS, MIN_TRANSACTIONS
    say = progress or (lambda _t: None)
    form_a = [t for t in tests if t.forme == FORME_A]
    census = [t for t in form_a if t.univers == RECENSEMENT]
    regular = [t for t in form_a if t.univers != RECENSEMENT]
    form_b = [t for t in tests if t.forme == FORME_B]
    counts: dict[str, dict] = {t.id: {"compteurs": {}, "exposition_par_jour": np.zeros(len(etape.jours()))}
                               for t in form_a}

    def add(found: dict) -> None:
        for (test_id, _), pos in found.items():
            entry = counts[test_id]
            for key, value in pos.compteurs.items():
                entry["compteurs"][key] = entry["compteurs"].get(key, 0) + int(value)
            entry["exposition_par_jour"] += exposition_jours([pos], etape)

    daily: dict[str, Bougies] = {}
    for k, sym in enumerate(source.symboles):
        if k % 10 == 0:
            say(f"comptage {k}/{len(source.symboles)}")
        pair = source.paire(sym)
        if pair is None:
            continue
        if form_b:
            daily[sym] = pair.unite(DAY_H)
        add(positions_paire(regular, pair, [etape], source))
    if census:
        for sym in sorted(source.quotidien_recensement()):
            pair = source.paire_recensement(sym)
            if pair is not None:
                add(positions_paire(census, pair, [etape], source, tout_membre=True))
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
            blocks = blocs_exposes(weights.execution_h[keep], exposure, etape) if keep.any() else 0
            out[test.id] = {"forme": "b", "echanges": int(keep.sum()), "echanges_investis": int((exposure > 0).sum()),
                            "exposition_moyenne": round(float(exposure.mean()), 4) if keep.any() else 0.0,
                            "paires_moyennes": round(float((weights.poids[keep] > 0).sum(axis=1).mean()), 2)
                            if keep.any() else 0.0, "blocs": blocks,
                            "insuffisant_par_construction": blocks < MIN_BLOCS}
    return out


def blocs_exposes(execution_h: np.ndarray, exposition: np.ndarray, etape: Etape) -> int:
    """Blocs de 28 jours de l'étape où le portefeuille est investi (exposition > 0) pendant au moins une heure ; une
    exposition court d'un échange au suivant (ou à la dernière clôture de l'étape)."""
    from .measure import BLOCS_B
    order = np.argsort(execution_h, kind="stable")
    starts = execution_h[order].astype(np.int64)
    ends = np.r_[starts[1:], etape.fin_h]
    exposed: set[int] = set()
    origin_h = etape.debut_jour * DAY_H
    for a, b, x in zip(starts, ends, exposition[order], strict=True):
        if x <= 0 or b <= a:
            continue
        first, last = (int(a) - origin_h) // (DAY_H * BLOCS_B), (int(b) - 1 - origin_h) // (DAY_H * BLOCS_B)
        exposed.update(range(max(first, 0), last + 1))
    return len(exposed)


def tests_codes(tests: list[Test]) -> tuple[list[Test], list[Test]]:
    """(codés, à coder) selon l'ensemble `IMPLEMENTEES` de chaque module."""
    done = [t for t in tests if t.etat_code == "code"]
    return done, [t for t in tests if t.etat_code != "code"]
