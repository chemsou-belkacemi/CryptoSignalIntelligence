"""Outils communs aux stratégies de forme (b) : calendrier des décisions (§ 4.2).

- hebdomadaire : décision le lundi 00:00 UTC sur les clôtures journalières jusqu'au dimanche (instant de disponibilité
  de la bougie du dimanche), échange à la clôture du lundi (mardi 00:00 UTC) ;
- quotidienne : décision à 00:00 UTC sur la clôture de la veille, échange à la clôture du jour ;
- mensuelle : premier lundi du mois, mêmes heures que l'hebdomadaire.
"""
from __future__ import annotations

import numpy as np

from ..data import DAY_H
from ..spec import Panneau

LUNDI, DIMANCHE = 0, 6


def jour_semaine(jours: np.ndarray) -> np.ndarray:
    """Jour de la semaine (lundi = 0) d'un numéro de journée UTC depuis 1970-01-01 (un jeudi)."""
    return (np.asarray(jours, dtype=np.int64) + 3) % 7


def decisions_hebdomadaires(panneau: Panneau) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(indices des dimanches dans le panneau, instants de décision en ns, heures d'échange)."""
    idx = np.flatnonzero(jour_semaine(panneau.jours) == DIMANCHE)
    return idx, panneau.disponible[idx], (panneau.jours[idx] + 2) * DAY_H


def decisions_quotidiennes(panneau: Panneau) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = np.arange(len(panneau.jours))
    return idx, panneau.disponible[idx], (panneau.jours[idx] + 2) * DAY_H


def decisions_mensuelles(panneau: Panneau) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Premier lundi de chaque mois : décision sur la clôture du dimanche qui le précède."""
    idx, decisions, execution = decisions_hebdomadaires(panneau)
    monday = panneau.jours[idx] + 1
    months = monday.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)
    first = np.ones(len(idx), dtype=bool)
    first[1:] = months[1:] != months[:-1]
    days_in = monday - months.astype("datetime64[M]").astype("datetime64[D]").astype(np.int64)
    first &= days_in < 7
    return idx[first], decisions[first], execution[first]


def rendement_sur(cloture: np.ndarray, idx: np.ndarray, jours: int) -> np.ndarray:
    """Rendement de clôture à clôture sur `jours` journées, aux lignes `idx` (NaN si l'une manque)."""
    out = np.full((len(idx), cloture.shape[1]), np.nan)
    ok = idx >= jours
    with np.errstate(invalid="ignore", divide="ignore"):
        out[ok] = cloture[idx[ok]] / cloture[idx[ok] - jours] - 1.0
    return out
