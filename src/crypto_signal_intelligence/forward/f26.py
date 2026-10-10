"""F26_MUR_ACHETEURS : mur d'acheteurs (déséquilibre du carnet à ±1 %, journal C_CARNET). Règles, mesure et verdict communs aux tests F25 à F30 : `forward/collecte_events.py`, gelé avec ce
module (docs/FORWARD_TESTS.md, section F26_MUR_ACHETEURS). Mesure seulement : aucun appel à suivre, aucun message, aucun ordre."""
from __future__ import annotations

from datetime import datetime

from ..config import Settings
from . import collecte_events as C
from .journal import Journal

TEST_ID = "F26_MUR_ACHETEURS"
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
