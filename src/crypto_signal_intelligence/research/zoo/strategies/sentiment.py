"""Sentiment (SE01 forme a, SE02 forme b) : Fear & Greed d'alternative.me (`zoo_fear_greed`), série déjà coupée.

Lectures déclarées (docs/ZOO.md § 2 et § 12, ZOO_CATALOGUE.md § 11) :
- **valeur connue** à une décision `D` : « la dernière valeur dont l'`available_at` précède la décision »
  (strictement, `data.joindre`) ; l'`available_at` du magasin est le jour `J` 00:00 UTC + 2 h, donc la valeur du jour
  `J` sert pour la première fois à la décision du lendemain 00:00 UTC ;
- **SE01** « passe sous 25 » : valeur connue à `D` < 25 et valeur connue à la décision précédente (`D` − une unité,
  24 h pour le test 1j) ≥ 25 ; ce passage ne dépend pas de la paire (toutes les paires sans trou le voient à la même
  décision) ; une valeur indéfinie (avant 2018-02) ne passe jamais ;
- **SE02** « sauf si la dernière valeur connue dépasse 75 » : strictement > 75 → USDT ; sinon top 40 à poids égaux
  (membres du mois de la décision qui ont une clôture le dimanche) ; valeur indéfinie : la comparaison est fausse,
  l'univers est investi (sans effet sur les étapes, la série commence en 2018-02).
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..data import HOUR_NS, Bougies, SerieExterne, joindre
from ..spec import Contexte, Panneau, Poids, Signaux
from .commun import decisions_hebdomadaires

IMPLEMENTEES: frozenset[str] = frozenset({"avidite", "peur_extreme"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

CLE = "zoo_fear_greed:fear_greed"


def serie_fear_greed(ctx: Contexte) -> SerieExterne | None:
    if CLE in ctx.externes:
        return ctx.externes[CLE]
    return next((s for k, s in ctx.externes.items() if k.split(":")[-1] == "fear_greed"), None)


def peur_extreme(frame: Bougies, ctx: Contexte) -> Signaux:
    """SE01 : la dernière valeur connue du Fear & Greed passe sous 25 ; achat de chaque membre, tenue 30 jours."""
    serie = serie_fear_greed(ctx)
    n = len(frame)
    if serie is None or len(serie) == 0 or n == 0:
        return Signaux(np.zeros(n, dtype=bool), None)
    threshold = np.full(n, float(ctx.params["seuil"]))
    now = joindre(frame.disponible, serie)
    before = joindre(frame.disponible - ctx.unite * HOUR_NS, serie)
    return Signaux(ind.inferieur(now, threshold) & ~ind.inferieur(before, threshold) & ~np.isnan(before), None)


def avidite(frame: Panneau, ctx: Contexte) -> Poids:
    """SE02 (hebdomadaire) : top 40 à poids égaux sauf si la dernière valeur connue du Fear & Greed dépasse 75."""
    idx, decisions, execution = decisions_hebdomadaires(frame)
    eligible = frame.membre[idx] & ~np.isnan(frame.cloture[idx])
    serie = serie_fear_greed(ctx)
    value = joindre(decisions, serie) if serie is not None else np.full(len(idx), np.nan)
    greedy = ind.superieur(value, np.full(len(idx), float(ctx.params["seuil"])))
    count = eligible.sum(axis=1, keepdims=True)
    weights = np.where(eligible & ~greedy[:, None], 1.0 / np.maximum(count, 1), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=eligible)
