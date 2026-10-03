"""Passage périodique des tests en direct, appelé par la surveillance entre deux cycles (fil séparé, jamais
bloquant) : gel vérifié, décisions inscrites, résolutions, relevé quotidien des dérivés, rapport du jour.

Ce module orchestre seulement : il n'est pas gelé (les règles, la mesure et le gel lui-même le sont, dans les
modules de chaque test et dans registry.py). Une fois le VERDICT puis la CLOTURE inscrits, un test n'est plus
contrôlé ni calculé.

Un seul passage à la fois, ENTRE PROCESSUS : la surveillance et `csi forward run` prennent le même verrou de
fichier (`<CSI_ROOT>/forward/.run.lock`) ; l'autre passe son tour. Lire puis ajouter au journal se fait donc
toujours sous ce verrou (aucune décision ni résolution inscrite deux fois).
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime

import pandas as pd

from ..config import Settings
from . import datalog, derivlog, report
from .halal import HalalNotValidated
from .registry import ENDED, RUNNING, check_frozen, journal_for, status
from .tests import TESTS

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("csi.forward")
EVERY = pd.Timedelta(hours=1)
POLL_EVERY = pd.Timedelta(minutes=10)             # détection d'événements en direct (tests qui exposent `poll`)
REPORT_AFTER = pd.Timedelta(hours=1)              # après 01:00 UTC : plans du jour inscrits par le suivi
_LOCK = threading.Lock()
_LAST: dict[str, pd.Timestamp] = {}


@contextmanager
def process_lock(settings: Settings):
    """Verrou exclusif non bloquant entre processus ; rend False s'il est déjà pris."""
    path = settings.root / "forward" / ".run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        if fcntl is None:
            yield True
            return
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_tests(settings: Settings, *, now: datetime) -> dict:
    out = {}
    for test, module in TESTS:
        if status(settings, test, now=now)["state"] in {RUNNING, ENDED}:
            reason = check_frozen(settings, test, now=now)
            if reason:
                log.warning("test %s : %s", test.test_id, reason)
                out[test.test_id] = {"frozen_check": reason}
        state = status(settings, test, now=now)
        if state["state"] not in {RUNNING, ENDED}:
            continue
        journal = journal_for(settings, test.test_id)
        out[test.test_id] = out.get(test.test_id, {}) | {
            "recorded": module.record_decisions(settings, journal, state["start"], now=now),
            "resolved": module.resolve(settings, journal, now=now),
            "finalized": module.finalize(journal, state["start"], now=now)}
    return out


def poll_tests(settings: Settings, *, now: datetime) -> dict:
    """Détection d'événements en direct : les tests EN_COURS qui exposent `poll`, gel vérifié avant."""
    out = {}
    for test, module in TESTS:
        if not hasattr(module, "poll") or status(settings, test, now=now)["state"] != RUNNING:
            continue
        reason = check_frozen(settings, test, now=now)
        if reason:
            log.warning("test %s : %s", test.test_id, reason)
            out[test.test_id] = {"frozen_check": reason}
            continue
        state = status(settings, test, now=now)
        if state["state"] != RUNNING:
            continue
        try:
            out[test.test_id] = module.poll(settings, journal_for(settings, test.test_id), state["start"], now=now)
        except Exception as exc:  # noqa: BLE001 - une source en panne ne doit jamais bloquer les autres tests
            log.exception("détection %s", test.test_id)
            out[test.test_id] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return out


def _due(moment: pd.Timestamp, key: str, every: pd.Timedelta) -> bool:
    return key not in _LAST or moment - _LAST[key] >= every


def _pollable(settings: Settings, *, now: datetime) -> bool:
    """Au moins un test EN_COURS détecte des événements en direct (sinon aucun passage intermédiaire)."""
    return any(hasattr(module, "poll") and status(settings, test, now=now)["state"] == RUNNING for test, module in TESTS)


def daily(settings: Settings, *, now: datetime, force: bool = False) -> dict | None:
    """Un passage complet au plus une fois par heure dans ce processus (sauf `force`), une détection d'événements
    au plus toutes les 10 min quand un test en direct l'exige ; un seul passage à la fois entre processus."""
    moment = pd.Timestamp(now)
    hourly = force or _due(moment, "run", EVERY)
    polling = force or (_due(moment, "poll", POLL_EVERY) and _pollable(settings, now=now))
    if not (hourly or polling):
        return None
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        with process_lock(settings) as acquired:
            if not acquired:
                return {"skipped": "un autre passage est en cours"}
            _LAST["poll"] = moment
            spreads_note = _record_spreads(settings, now=now)
            if not hourly:
                polled: dict = {"poll": poll_tests(settings, now=now)}
                if spreads_note is not None:                     # None : pas de relevé dû (moins de 10 minutes)
                    polled["spreads"] = spreads_note
                return polled
            _LAST["run"] = moment
            out: dict = {"tests": run_tests(settings, now=now)}
            if moment - moment.floor("D") >= derivlog.RECORD_AFTER:
                try:
                    out["derivatives"] = derivlog.record_day(settings, now=now)
                except HalalNotValidated as exc:
                    out["derivatives"] = {"skipped": str(exc)}
                except Exception as exc:  # noqa: BLE001 - le relevé ne doit jamais empêcher le rapport
                    log.exception("relevé des dérivés")
                    out["derivatives"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                try:
                    out["context_data"] = datalog.record_day(settings, now=now)
                except HalalNotValidated as exc:
                    out["context_data"] = {"skipped": str(exc)}
                except Exception as exc:  # noqa: BLE001 - jamais bloquant pour le rapport
                    log.exception("relevé des données de contexte")
                    out["context_data"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            try:                                   # conseil de risque à 24 h en shadow, une fois par prévision F12
                from ..risk.advice import record_day as record_risk
                out["risk_shadow"] = record_risk(settings, now=now)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant pour les tests en direct
                log.exception("conseil de risque en shadow")
                out["risk_shadow"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            if moment - moment.floor("D") >= REPORT_AFTER:
                out["report"] = str(report.write(settings, now=now))   # rapport du jour, mis à jour à chaque passage
            return out
    finally:
        _LOCK.release()


def _record_spreads(settings: Settings, *, now: datetime) -> dict | None:
    """Relevé F0_ECARTS (forward/spreads.py), au plus toutes les 10 minutes ; jamais bloquant."""
    from . import spreads
    try:
        return spreads.maybe_record(settings, now=now)
    except Exception as exc:  # noqa: BLE001 - un relevé en panne n'arrête pas les tests
        log.exception("relevé des écarts entre bourses")
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def start_background(settings: Settings, *, now: datetime) -> bool:
    moment = pd.Timestamp(now)
    if _LOCK.locked() or not (_due(moment, "run", EVERY) or _due(moment, "poll", POLL_EVERY)):
        return False
    threading.Thread(target=_logged, args=(settings, now), name="csi-forward", daemon=True).start()
    return True


def _logged(settings: Settings, now: datetime) -> None:
    try:
        result = daily(settings, now=now)
        if result:
            log.info("tests en direct : %s", result)
    except Exception:  # noqa: BLE001 - jamais bloquant pour la surveillance
        log.exception("tests en direct")
