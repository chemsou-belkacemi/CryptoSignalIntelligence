"""Unités d'horodatage déclarées PAR SOURCE, puis vérifiées par ordre de grandeur.

Binance Public Data (Spot) : millisecondes jusqu'au 31/12/2024, microsecondes
à partir des archives du 1er janvier 2025. REST /api/v3/klines : millisecondes
(par défaut). On ne devine jamais : l'adaptateur déclare l'unité attendue et
une valeur hors de la plage attendue est une erreur (fichier mis en quarantaine).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ARCHIVE_MICROSECONDS_FROM = date(2025, 1, 1)
# Plages plausibles : années 2001 à 2286 environ.
RANGES = {"ms": (1e12, 1e13), "us": (1e15, 1e16)}


class TimestampUnitError(ValueError):
    pass


def archive_unit(period_start: date) -> str:
    return "us" if period_start >= ARCHIVE_MICROSECONDS_FROM else "ms"


def to_utc(values, unit: str) -> pd.DatetimeIndex:
    """Convertit des entiers d'unité `unit` en UTC après contrôle d'ordre de grandeur."""
    if unit not in RANGES:
        raise TimestampUnitError(f"unité inconnue : {unit}")
    array = np.asarray(values, dtype="int64")
    if array.size:
        low, high = RANGES[unit]
        if array.min() < low or array.max() >= high:
            raise TimestampUnitError(
                f"horodatages incompatibles avec l'unité déclarée {unit!r} "
                f"(min={array.min()}, max={array.max()})")
    # Résolution interne fixée à la nanoseconde : jointures et comparaisons homogènes.
    return pd.to_datetime(array, unit=unit, utc=True).as_unit("ns")
