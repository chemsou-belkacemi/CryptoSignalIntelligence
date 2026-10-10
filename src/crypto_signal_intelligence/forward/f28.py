"""F28_BALEINES : baleines (solde des gros ordres au marché sur 1 h, journal C_FLUX). Règles, mesure et verdict communs aux tests F25 à F30 : `forward/collecte_events.py`, gelé avec ce
module (docs/FORWARD_TESTS.md, section F28_BALEINES). Mesure seulement : aucun appel à suivre, aucun message, aucun ordre."""
from __future__ import annotations

from datetime import datetime

from ..config import Settings
from . import collecte_events as C
from .journal import Journal

TEST_ID = "F28_BALEINES"
SPEC = C.SPECS[TEST_ID]
TEST = C.make_test(TEST_ID)


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return C.record_decisions(SPEC, settings, journal, start, now=now)


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest=None) -> dict:
    return C.resolve(SPEC, settings, journal, now=now, rest=rest)


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    return C.stats(SPEC, journal, start, now=now)


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    return C.finalize(SPEC, journal, start, now=now)
