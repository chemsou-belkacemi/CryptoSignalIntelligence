"""Protocole temporel fixé AVANT toute optimisation (docs/PROTOCOL.md).

DEVELOPMENT : début de l'historique → development_end.
FINAL_TEST  : final_test_start → maintenant. Réservé : consultation explicite,
              comptée ; après la première, il devient une période de validation.

Les bornes sont figées dans le code (FROZEN_*) : la configuration (fichier ou variable
d'environnement CSI_PROTOCOL__DEVELOPMENT_END) peut terminer DEVELOPMENT plus tôt, jamais plus
tard, sinon une exécution « DEVELOPMENT » lirait en silence des données réservées au test final.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from ..config import Settings

FROZEN_DEVELOPMENT_END = datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC)
FROZEN_FINAL_TEST_START = datetime(2025, 7, 1, tzinfo=UTC)


class FinalTestLocked(PermissionError):
    pass


@dataclass(frozen=True)
class Period:
    label: str
    start: datetime
    end: datetime


def development_end(settings: Settings) -> datetime:
    """Fin de DEVELOPMENT déclarée, refusée si elle déborde sur le test final figé."""
    end = settings.protocol.development_end
    if end > FROZEN_DEVELOPMENT_END:
        raise FinalTestLocked(
            f"development_end {end.isoformat()} dépasse la borne figée {FROZEN_DEVELOPMENT_END.isoformat()} : "
            "ce serait lire le test final sans le déclarer. Utiliser --period final-test "
            "--i-understand-final-test (consultation enregistrée).")
    return end


def clip_to_development(frame: pd.DataFrame, settings: Settings, column: str = "open_time") -> pd.DataFrame:
    """Lignes de DEVELOPMENT seulement (bougies ouvertes au plus tard à development_end)."""
    return frame[frame[column] <= pd.Timestamp(development_end(settings))]


def period(settings: Settings, label: str, *, now: datetime, allow_final_test: bool = False) -> Period:
    protocol = settings.protocol
    if label == "development":
        start = datetime.combine(settings.data.history_start, datetime.min.time(), tzinfo=UTC)
        return Period("DEVELOPMENT", start, development_end(settings))
    if label == "final-test":
        if not allow_final_test:
            raise FinalTestLocked("Test final réservé : ajouter --i-understand-final-test (consultation enregistrée).")
        return Period("FINAL_TEST", max(protocol.final_test_start, FROZEN_FINAL_TEST_START), now)
    raise ValueError(f"Période inconnue : {label} (development | final-test)")
