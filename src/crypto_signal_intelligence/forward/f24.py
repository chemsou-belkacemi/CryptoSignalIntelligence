"""F24_SORTIE_BASE_LONGUE : la configuration « price action » `SORTIE_BASE_LONGUE` (sortie d'une base longue) mesurée SEULE en direct, avec son propre quota
de 5 appels par jour UTC ; toutes les autres règles sont celles de F19_PRICE_ACTION (docs/FORWARD_TESTS.md, section
F24_SORTIE_BASE_LONGUE ; code commun `forward/pa_single.py`, gelé avec ce module)."""
from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..data.store import CandleStore
from . import pa_single as P
from .journal import Journal

TEST_ID = "F24_SORTIE_BASE_LONGUE"
CONFIG = P.CONFIG_OF[TEST_ID]
TEST = P.make_test(TEST_ID)


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime, store: CandleStore | None = None,
                     clock=None) -> dict:
    return P.record_decisions(TEST_ID, settings, journal, start, now=now, store=store, clock=clock)


def resolve(settings: Settings, journal: Journal, *, now: datetime, store: CandleStore | None = None) -> dict:
    return P.resolve(TEST_ID, settings, journal, now=now, store=store)


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    return P.stats(TEST_ID, journal, start, now=now)


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    return P.finalize(TEST_ID, journal, start, now=now)
