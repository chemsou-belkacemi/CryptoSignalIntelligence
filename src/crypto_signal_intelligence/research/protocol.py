"""Protocole temporel fixé AVANT toute optimisation (docs/PROTOCOL.md).

DEVELOPMENT : début de l'historique → development_end.
FINAL_TEST  : final_test_start → maintenant. Réservé : consultation explicite,
              comptée ; après la première, il devient une période de validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import Settings


class FinalTestLocked(PermissionError):
    pass


@dataclass(frozen=True)
class Period:
    label: str
    start: datetime
    end: datetime


def period(settings: Settings, label: str, *, now: datetime, allow_final_test: bool = False) -> Period:
    protocol = settings.protocol
    if label == "development":
        start = datetime.combine(settings.data.history_start, datetime.min.time(), tzinfo=UTC)
        return Period("DEVELOPMENT", start, protocol.development_end)
    if label == "final-test":
        if not allow_final_test:
            raise FinalTestLocked("Test final réservé : ajouter --i-understand-final-test (consultation enregistrée).")
        return Period("FINAL_TEST", protocol.final_test_start, now)
    raise ValueError(f"Période inconnue : {label} (development | final-test)")
