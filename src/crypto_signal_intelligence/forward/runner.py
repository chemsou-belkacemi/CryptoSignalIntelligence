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
from . import derivlog, report
from .halal import HalalNotValidated
from .registry import ENDED, RUNNING, check_frozen, journal_for, status
from .tests import TESTS

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("csi.forward")
EVERY = pd.Timedelta(hours=1)
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


def daily(settings: Settings, *, now: datetime, force: bool = False) -> dict | None:
    """Un passage (au plus un par heure dans ce processus, sauf `force`) ; un seul à la fois entre processus."""
    moment = pd.Timestamp(now)
    if not force and "run" in _LAST and moment - _LAST["run"] < EVERY:
        return None
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        with process_lock(settings) as acquired:
            if not acquired:
                return {"skipped": "un autre passage est en cours"}
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
            if moment - moment.floor("D") >= REPORT_AFTER:
                out["report"] = str(report.write(settings, now=now))   # rapport du jour, mis à jour à chaque passage
            return out
    finally:
        _LOCK.release()


def start_background(settings: Settings, *, now: datetime) -> bool:
    if _LOCK.locked() or ("run" in _LAST and pd.Timestamp(now) - _LAST["run"] < EVERY):
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
