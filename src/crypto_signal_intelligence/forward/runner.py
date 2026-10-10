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
from .registry import ENDED, NOT_STARTED, RUNNING, check_frozen, journal_for, status
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


F15_ID = "F15_FIGURES"
STORE_READERS = ("F18_ASSISTANT", "F19_PRICE_ACTION")       # lisent le magasin 1 h de F15 (`forward_figures/data`)
STORE_REFRESH = "_magasin_f15"


def refresh_figure_store(settings: Settings, *, now: datetime, rest=None) -> dict | None:
    """Tient à jour le magasin 1 h de F15 (`forward_figures/data`, paires figées au démarrage de F15) quand F15 ne le
    fait plus (après sa date de fin, F15 ne télécharge plus rien) mais que F18 ou F19 en dépend encore (EN_COURS ou en
    résolution). Réutilise les fonctions de F15 et du pipeline en lecture (`f15.figure_settings`,
    `data.pipeline.download`, `rest_only=True`), sans les modifier. None si rien n'est à faire."""
    from ..data.http import PublicHttpClient
    from ..data.pipeline import download
    from . import f15
    from .tests import BY_ID
    figures = status(settings, f15.TEST, now=now)
    if figures["state"] in {NOT_STARTED, RUNNING}:
        return None
    readers = [tid for tid in STORE_READERS if tid in BY_ID and status(settings, BY_ID[tid][0], now=now)["state"] in {RUNNING, ENDED}]
    if not readers:
        return None
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    symbols = list(figures["start"]["halal"]["symbols"])
    errors = 0
    for symbol in symbols:
        try:
            download(f15.figure_settings(settings), symbol, "1h", now=now, rest_client=client, rest_only=True)
        except Exception:  # noqa: BLE001 - une paire en échec n'arrête pas les autres (reprise au passage suivant)
            errors += 1
    return {"pairs": len(symbols), "errors": errors, "readers": readers}


def run_tests(settings: Settings, *, now: datetime) -> dict:
    out = {}
    try:
        refreshed = refresh_figure_store(settings, now=now)
    except Exception as exc:  # noqa: BLE001 - le magasin en échec n'empêche jamais les tests
        log.exception("mise à jour du magasin de F15")
        refreshed = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    if refreshed is not None:
        out[STORE_REFRESH] = refreshed
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
        try:
            out[test.test_id] = out.get(test.test_id, {}) | {
                "recorded": module.record_decisions(settings, journal, state["start"], now=now),
                "resolved": module.resolve(settings, journal, now=now),
                "finalized": module.finalize(journal, state["start"], now=now)}
        except Exception as exc:  # noqa: BLE001 - un test en échec n'arrête pas les suivants (repris au passage suivant)
            log.exception("test %s : passage en échec", test.test_id)
            out[test.test_id] = out.get(test.test_id, {}) | {"error": f"{type(exc).__name__}: {exc}"}
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


def _hour_due(moment: pd.Timestamp) -> bool:
    """Passage complet dès le changement d'heure UTC (et non « 60 min après le précédent », qui dérivait avec les
    cycles de 15 min) : l'évaluation d'une clôture 4 h, son carnet et son message partent dans les minutes qui suivent."""
    return "run" not in _LAST or moment.floor("h") > _LAST["run"].floor("h")


def _pollable(settings: Settings, *, now: datetime) -> bool:
    """Au moins un test EN_COURS détecte des événements en direct (sinon aucun passage intermédiaire)."""
    return any(hasattr(module, "poll") and status(settings, test, now=now)["state"] == RUNNING for test, module in TESTS)


def daily(settings: Settings, *, now: datetime, force: bool = False) -> dict | None:
    """Un passage complet à chaque changement d'heure UTC dans ce processus (sauf `force`), une détection d'événements
    au plus toutes les 10 min quand un test en direct l'exige ; un seul passage à la fois entre processus."""
    moment = pd.Timestamp(now)
    hourly = force or _hour_due(moment)
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
            _read_images(settings, now=now)
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
            try:                                   # données de contexte (docs/CONTEXTE.md), une fois par jour après 03:00 UTC
                from ..context.collect import record_day as record_context
                note = record_context(settings, now=now)
                if note is not None:
                    out["context_extra"] = note
            except HalalNotValidated as exc:
                out["context_extra"] = {"skipped": str(exc)}
            except Exception as exc:  # noqa: BLE001 - jamais bloquant pour les tests en direct
                log.exception("relevé des données de contexte supplémentaires")
                out["context_extra"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            try:                                   # conseil de risque à 24 h en shadow, une fois par prévision F12
                from ..risk.advice import record_day as record_risk
                out["risk_shadow"] = record_risk(settings, now=now)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant pour les tests en direct
                log.exception("conseil de risque en shadow")
                out["risk_shadow"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            # Feu de protection du marché : journal du jour seulement (lecture légère). L'historique du rang, calcul
            # lourd, n'est JAMAIS fait ici (commande `meteo-historique`, conteneur tools) : un manque de mémoire
            # arrêterait F1 à F16.
            try:
                from ..risk import market_light
                out["market_light"] = market_light.record_day(settings, now=now)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant pour les tests en direct
                log.exception("feu de protection du marché")
                out["market_light"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            if moment - moment.floor("D") >= REPORT_AFTER:
                out["report"] = str(report.write(settings, now=now))   # rapport du jour, mis à jour à chaque passage
            return out
    finally:
        _LOCK.release()


_IMAGE_READER: dict = {}


def _read_images(settings: Settings, *, now: datetime) -> dict | None:
    """Lit les signaux reçus en image (OCR local, external/image_queue.py) ; jamais bloquant ; sans l'extra « ocr »,
    les images attendent."""
    try:
        from ..external import chart_ocr, image_queue
        image_queue.expire(settings, now=now)
        if not chart_ocr.available():
            return None
        if "reader" not in _IMAGE_READER:
            _IMAGE_READER["reader"] = chart_ocr.Lecteur()
            _IMAGE_READER["code"] = chart_ocr.reader_fingerprint()
        lecteur = _IMAGE_READER["reader"]

        def read(path, caption):
            return chart_ocr.classify_image(lecteur.analyse(path), caption=caption)

        return image_queue.process(settings, read, code=_IMAGE_READER["code"], now=now)
    except Exception as exc:  # noqa: BLE001 - une lecture en panne n'arrête pas les tests
        log.exception("lecture des signaux en image")
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


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
    if _LOCK.locked() or not (_hour_due(moment) or _due(moment, "poll", POLL_EVERY)):
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
