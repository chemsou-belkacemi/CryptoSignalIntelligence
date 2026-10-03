"""Test en direct F13_PIVOT_BREAK_1D_24 : la condition de F10 (première clôture journalière au-dessus d'un pivot haut
confirmé, K2 du criblage K) mesurée sur les 24 AUTRES paires de recherche (docs/FORWARD_TESTS.md, section
F13_PIVOT_BREAK_1D_24 ; plan de travail du 2026-10-03, validé par le propriétaire).

Mêmes règles que F10, sans en changer une seule : les fonctions de F10 (lecture du jour, placebos, rendement net,
résolution, mesures, verdict) sont réutilisées telles quelles et gelées avec ce test ; seule la liste des paires
diffère, figée ici. Ces 24 paires n'ont jamais servi à construire K2 au-delà du criblage (les 40 paires de recherche
y étaient toutes) ; avec F10, la condition est mesurée en direct sur les 40.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from ..config import Settings
from . import f10
from .journal import Journal, utc_iso
from .registry import ForwardTest

TEST_ID = "F13_PIVOT_BREAK_1D_24"
#: Les 40 paires de recherche (research/universe.py, figé le 2026-10-01) moins les 16 de la configuration ;
#: toutes admises par le screening halal au 2026-10-03.
SYMBOLS: tuple[str, ...] = (
    "APTUSDT", "ARBUSDT", "BCHUSDT", "DASHUSDT", "DOGEUSDT", "EGLDUSDT", "FETUSDT", "ICPUSDT", "IOTAUSDT", "LTCUSDT",
    "NEOUSDT", "OPUSDT", "POLUSDT", "QNTUSDT", "RENDERUSDT", "ROSEUSDT", "SEIUSDT", "SUIUSDT", "TAOUSDT", "THETAUSDT",
    "TIAUSDT", "VETUSDT", "XTZUSDT", "ZECUSDT",
)
CHECK, EVENT, RESOLUTION = f10.CHECK, f10.EVENT, f10.RESOLUTION


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Contrôle quotidien de F10 appliqué aux 24 paires figées (admises par la liste halal du démarrage)."""
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final or moment < day + f10.CHECK_AFTER:
        return {"checks": 0}
    if key in f10._first_by(journal.entries({CHECK}), "day"):
        return {"checks": 0}
    symbols = [s for s in SYMBOLS if s in set(start["halal"]["symbols"])]
    values: dict[str, dict] = {}
    events = 0
    for symbol in symbols:
        bars = f10.daily_bars(settings, symbol, until=day)
        read = f10.evaluate(bars, day)
        if read is None:
            values[symbol] = {"evaluable": False, "bars": int(len(bars)), "triggered": False}
            continue
        values[symbol] = {"evaluable": True, **read}
        if read["triggered"]:
            event_id = hashlib.sha256(f"{TEST_ID}:{symbol}:{key}".encode()).hexdigest()[:16]
            days = f10.placebo_days(event_id)
            journal.append(EVENT, {"event_id": event_id, "symbol": symbol, "day": key, "detected_at": utc_iso(moment),
                                   "entry_at": utc_iso(moment), "resistance": read["resistance"], "close": read["close"],
                                   "placebo_days": days, "placebo_entries": [utc_iso(moment - pd.Timedelta(days=d)) for d in days]},
                           now=moment)
            events += 1
    journal.append(CHECK, {"day": key, "evaluable": sum(1 for v in values.values() if v["evaluable"]), "values": values,
                           "events": events}, now=moment)
    return {"checks": 1, "events": events}


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest=None) -> dict:
    return f10.resolve(settings, journal, now=now, rest=rest)


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    return f10.stats(journal, start, now=now)


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    return f10.finalize(journal, start, now=now)


TEST = ForwardTest(
    test_id=TEST_ID, title="Achat après une cassure journalière d'un pivot haut confirmé, 24 autres paires (K2 en direct)",
    hypothesis=("Sur les 24 autres paires de recherche, quand la clôture journalière passe pour la première fois au-dessus du "
                "dernier pivot haut confirmé, un achat simulé le lendemain matin fait mieux, net de frais, que 20 achats "
                "placebo de la même paire aux mêmes heures dans les 30 jours précédents, à 24 h et 168 h (règles de F10)."),
    params=f10.TEST.params | {"symbols": list(SYMBOLS), "rules": "celles de F10, fonctions réutilisées et gelées"},
    rule_objects=(), config_keys=("data.rest_base_url",),
    frozen_modules=("crypto_signal_intelligence.forward.f13", "crypto_signal_intelligence.forward.f10",
                    "crypto_signal_intelligence.forward.costs", "crypto_signal_intelligence.forward.registry",
                    "crypto_signal_intelligence.forward.journal"),
    frozen_functions=f10.TEST.frozen_functions,
)
