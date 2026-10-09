"""Socle du collecteur : journaux mensuels en ajout seul, état `C_ETAT.json`, contexte partagé des sources.

Journaux : `<root>/forward/C_<SOURCE>-AAAA-MM.jsonl`, écrits par `forward.journal.Journal` (empreintes chaînées,
ligne coupée signalée). Rotation mensuelle par le nom du fichier : le mois de l'horodatage de l'entrée choisit le
fichier. Jamais dans les journaux `F*.jsonl` des tests en direct.

État : `<root>/state/C_ETAT.json`, réécrit atomiquement (dernier message et dernière entrée par source, compteurs,
dernière erreur sans secret, octets écrits ce mois). Lu par `GET /collecte` et par le contrôle de santé du conteneur.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings
from ..forward.journal import Journal, _parse, _tail, canonical, utc_iso
from .net import CollectHttp

log = logging.getLogger("csi.collect")

LIQUIDATIONS, CARNET, FLUX, OPTIONS, ATTENTION = "LIQUIDATIONS", "CARNET", "FLUX", "OPTIONS", "ATTENTION"
SOURCES = (LIQUIDATIONS, CARNET, FLUX, OPTIONS, ATTENTION)
PREFIX = "C_"
STATE_FILE = "state/C_ETAT.json"
LOCK_FILE = "state/collecteur.lock"
IN_SERVICE, RECONNECTING, UNAVAILABLE, STOPPED, STARTING = "EN_SERVICE", "RECONNEXION", "NON_DISPONIBLE", "ARRETE", "DEMARRAGE"
RELOADING, MUTE = "RECHARGEMENT", "MUET"
NOTE = ("Relevé en shadow (docs/COLLECTE.md) : aucune influence sur les tests en direct, les avis ou "
        "BinanceSpotManager ; aucun pouvoir prédictif revendiqué.")
VERSION = 1


def journal_path(settings: Settings, source: str, moment) -> Path:
    if source not in SOURCES:
        raise ValueError(f"source inconnue : {source}")
    return settings.root / "forward" / f"{PREFIX}{source}-{pd.Timestamp(moment):%Y-%m}.jsonl"


def journal(settings: Settings, source: str, moment) -> Journal:
    return Journal(journal_path(settings, source, moment))


def state_path(settings: Settings) -> Path:
    return settings.root / STATE_FILE


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_state(settings: Settings) -> dict:
    """Contenu de `C_ETAT.json` (`available` faux si le collecteur n'a jamais écrit)."""
    path = state_path(settings)
    if not path.exists():
        return {"available": False, "sources": {}, "note": NOTE, "places_orders": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"available": False, "error": f"état illisible : {exc}", "sources": {}, "note": NOTE,
                "places_orders": False}
    return {"available": True, **payload}


def _source_state() -> dict:
    return {"status": STARTING, "last_message_at": None, "last_entry_at": None, "messages": 0, "entries": 0,
            "errors": 0, "last_error": None, "last_error_at": None, "reconnections": 0, "bytes_month": 0,
            "detail": None}


class State:
    """Compteurs par source, réécrits sur disque au plus toutes les `min_interval` secondes."""

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime], min_interval: float = 10.0,
                 monotonic: Callable[[], float] = time.monotonic):
        self.settings, self.clock, self.monotonic, self.min_interval = settings, clock, monotonic, min_interval
        self.sources: dict[str, dict] = {s: _source_state() for s in SOURCES}
        self.started_at = utc_iso(clock())
        self.info: dict = {}
        self._last_write: float | None = None
        self._dirty = True

    def touch(self, source: str, **fields) -> None:
        self.sources[source].update(fields)
        self._dirty = True

    def message(self, source: str, *, now: datetime | None = None) -> None:
        entry = self.sources[source]
        entry["messages"] += 1
        entry["last_message_at"] = utc_iso(now or self.clock())
        entry["status"] = IN_SERVICE
        self._dirty = True

    def error(self, source: str, exc: BaseException | str, *, status: str = RECONNECTING) -> None:
        text = (f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc))[:300]
        entry = self.sources[source]
        entry.update(errors=entry["errors"] + 1, last_error=text, last_error_at=utc_iso(self.clock()), status=status)
        self._dirty = True
        log.warning("collecteur %s : %s", source, text)

    def payload(self) -> dict:
        now = self.clock()
        return {"written_at": utc_iso(now), "started_at": self.started_at, "version": VERSION, "pid": os.getpid(),
                "sources": {k: dict(v) for k, v in self.sources.items()},
                "journal_prefix": PREFIX, "month": f"{pd.Timestamp(now):%Y-%m}",
                "places_orders": False, "note": NOTE, **self.info}

    def write(self, *, force: bool = False) -> bool:
        mono = self.monotonic()
        if not force and (not self._dirty or (self._last_write is not None and mono - self._last_write < self.min_interval)):
            return False
        write_atomic(state_path(self.settings), self.payload())
        self._last_write, self._dirty = mono, False
        return True


class Reload(Exception):
    """Levée par une source qui veut être relancée tout de suite (liste de paires changée) : pas une erreur."""


class Recorder:
    """Ajoute une entrée au journal mensuel de la source (verrouillé par `Journal`) et met l'état à jour.

    Horloge en recul (NTP, machine réveillée) : `Journal` refuse un horodatage antérieur à sa dernière entrée ; on
    ré-horodate alors à `dernière entrée + 1 ms` avec `clock_adjusted: true` dans les données, sans arrêter la source."""

    def __init__(self, settings: Settings, state: State):
        self.settings, self.state = settings, state

    def append(self, source: str, kind: str, data: dict, *, now: datetime) -> dict:
        at = pd.Timestamp(now)
        log_ = journal(self.settings, source, at)
        try:
            entry = log_.append(kind, data, now=at)
        except ValueError as exc:
            if "antérieur" not in str(exc):
                raise
            previous = next((e for e in (_parse(line) for line in reversed(_tail(log_.path, 2))) if e), None)
            if previous is None:
                raise
            adjusted = pd.Timestamp(previous["at"]) + pd.Timedelta(milliseconds=1)
            log.warning("collecteur %s : horloge en recul (%s < %s), entrée ré-horodatée", source, utc_iso(at), previous["at"])
            entry = log_.append(kind, {**data, "clock_adjusted": True, "clock_at": utc_iso(at)}, now=adjusted)
        status = self.state.sources[source]
        status["entries"] += 1
        status["last_entry_at"] = entry["at"]
        status["bytes_month"] += len(canonical(entry).encode("utf-8")) + 1
        self.state._dirty = True
        return entry


@dataclass
class Context:
    """Ce qu'une source reçoit : réglages, horloges (remplaçables en test), journaux, état, clients réseau."""
    settings: Settings
    recorder: Recorder
    state: State
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[Any]] | None = None     # test : coroutine qui avance une horloge fictive
    http: CollectHttp | None = None
    stream: Callable | None = None                         # ws_messages(url) ou flux fictif en test
    stop: asyncio.Event = field(default_factory=asyncio.Event)

    def now(self) -> pd.Timestamp:
        return pd.Timestamp(self.clock())

    async def pause(self, seconds: float) -> None:
        """Attente ANNULABLE : revient dès que `stop` est posé (ou tout de suite si `seconds` ≤ 0)."""
        if self.sleep is not None:
            await self.sleep(max(0.0, seconds))
            return
        if seconds <= 0 or self.stop.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)


class Silent(Exception):
    """Levée par une source connectée qui n'a rien reçu depuis `timeout` s : flux probablement bloqué depuis ce
    réseau (constaté sur les flux dérivés de Binance le 2026-10-09). Pas une panne : réessai une fois par heure."""


async def first_or_silence(ctx: Context, messages, *, timeout: float):
    """Premier message d'un flux, ou `Silent` si rien n'arrive en `timeout` s (attente annulable : `ctx.pause`, donc
    pilotable par l'horloge fictive des tests) ; None si l'arrêt a été demandé entre-temps."""
    first = asyncio.ensure_future(anext(messages))
    pause = asyncio.ensure_future(ctx.pause(timeout))
    done, _ = await asyncio.wait({first, pause}, return_when=asyncio.FIRST_COMPLETED)
    if first in done:
        pause.cancel()
        await asyncio.gather(pause, return_exceptions=True)
        try:
            return first.result()
        except StopAsyncIteration:
            return None
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    if ctx.stop.is_set():
        return None
    raise Silent(f"connecté, aucune donnée en {timeout / 60:.0f} min")



def minute_floor(stamp_ms: int) -> int:
    return stamp_ms - stamp_ms % 60_000


def iso_ms(stamp_ms: int) -> str:
    return utc_iso(pd.Timestamp(int(stamp_ms), unit="ms", tz="UTC"))


def month_bytes(settings: Settings, source: str, moment) -> int:
    path = journal_path(settings, source, moment)
    return path.stat().st_size if path.exists() else 0
