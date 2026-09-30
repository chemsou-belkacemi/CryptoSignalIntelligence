"""Verrou d'instance : une seule surveillance peut publier à la fois (point 6 du cahier des charges).

Verrou exclusif du système d'exploitation sur un fichier (msvcrt sous Windows, fcntl ailleurs) :
il est libéré automatiquement si le processus meurt, donc aucun verrou « fantôme » après un crash.
Le fichier contient le PID et l'heure de démarrage du détenteur, pour le diagnostic.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 - gardé ouvert tant que le verrou vit
        try:
            _lock(handle)
        except OSError:
            try:
                handle.seek(0)
                holder = handle.read().strip() or "détenteur inconnu"
            except OSError:
                holder = "détenteur illisible"
            handle.close()
            raise InstanceAlreadyRunning(f"une autre surveillance tient déjà {self.path} ({holder})") from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} depuis {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            try:
                _unlock(self._handle)
            finally:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    # Octet verrouillé loin du contenu : le PID du détenteur reste lisible par les autres processus.
    _LOCK_OFFSET = 1 << 20

    def _lock(handle: IO[str]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[str]) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
