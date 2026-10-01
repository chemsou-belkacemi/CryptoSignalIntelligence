"""Intervalles de confiance communs à la recherche (ML swing, criblage du marché à terme).

Un seul calcul, testé à la main et sous l'hypothèse d'un gain nul (tests/test_ml_swing.py), pour que chaque
protocole déclare la même méthode. scipy (quantile de Student) n'est chargé qu'à l'appel : il vient avec
l'extra `ml` du projet, utile seulement sur le poste de recherche.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def calendar_mean_ci(values, times, *, block_days: int, min_blocks: int = 20,
                     level: float = 0.95) -> list[float] | None:
    """IC d'une moyenne par événement (docs/ML_SWING.md, v2) : sommes par blocs de `block_days` jours CALENDAIRES
    consécutifs (jours sans événement compris ; un bloc dure au moins deux fois l'horizon, donc seuls deux blocs
    voisins partagent des positions ouvertes), moyenne = somme / nombre d'événements, variance robuste à un
    retard (la covariance entre blocs voisins s'ajoute quand elle est positive et n'est jamais retranchée),
    quantile de Student au niveau `level` (bilatéral). Aucun IC sous `min_blocks` blocs AVEC événements : le
    critère qui l'exige échoue alors. Sous un gain nul simulé : 2,1 à 2,4 % de bornes basses > 0 pour 2,5 %
    visés (tests/test_ml_swing.py)."""
    from scipy.stats import t as student_t

    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return None
    days = pd.to_datetime(pd.Series(times), utc=True).dt.floor("D")
    block = ((days - days.min()) // pd.Timedelta(days=1)).to_numpy(np.int64) // block_days
    sums = np.bincount(block, weights=values)
    counts = np.bincount(block).astype(float)
    filled = int((counts > 0).sum())
    if filled < max(min_blocks, 2):
        return None
    mean = float(sums.sum() / counts.sum())
    u = sums - mean * counts
    variance = (u @ u + 2 * max(0.0, float(u[1:] @ u[:-1]))) * filled / (filled - 1) / counts.sum() ** 2
    half = float(student_t.ppf(0.5 + level / 2, filled - 1)) * math.sqrt(variance)
    return [round(mean - half, 6), round(mean + half, 6)]
