"""Journal en AJOUT SEUL des tests en direct (docs/FORWARD_TESTS.md, règle 6 de la mission du 2026-10-02).

Une ligne JSON par entrée : numéro, horodatage UTC, nature, données (entrées brutes et décision) et empreinte
SHA-256 chaînée à l'entrée précédente. Modifier, supprimer ou réordonner une ligne casse la chaîne, et
`verify` dit où. Rien n'est jamais réécrit : une erreur se corrige par une nouvelle entrée qui la signale.

Ligne coupée par un arrêt brutal : l'ajout suivant termine la ligne, puis inscrit une entrée LIGNE_TRONQUEE
(empreinte et longueur de la ligne coupée) chaînée à la dernière entrée valide. La ligne coupée reste dans le
fichier, signalée ; la chaîne continue sans elle.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

try:                                            # verrou entre processus (Linux, Docker) ; absent sous Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

GENESIS = "0" * 64
TRUNCATED = "LIGNE_TRONQUEE"


def clean(value):
    """Valeur sérialisable de façon stable : NaN et infinis deviennent None, horodatages en ISO UTC."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, pd.Timestamp | datetime):
        return utc_iso(value)
    if hasattr(value, "item") and not isinstance(value, str):    # scalaires numpy
        return clean(value.item())
    return value


def canonical(value) -> str:
    return json.dumps(clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def utc_iso(moment) -> str:
    stamp = pd.Timestamp(moment)
    if stamp.tzinfo is None:
        raise ValueError("horodatage sans fuseau : le journal n'accepte que l'UTC explicite")
    return stamp.tz_convert("UTC").isoformat()


def entry_hash(seq: int, at: str, kind: str, data, prev: str) -> str:
    body = canonical({"seq": seq, "at": at, "kind": kind, "data": data, "prev": prev})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _tail(path: Path, count: int = 2) -> list[bytes]:
    """Dernières lignes (brutes, sans le saut final), lues depuis la fin (le journal peut peser des dizaines de Mo)."""
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position, buffer = handle.tell(), b""
        while position > 0:
            step = min(8192, position)
            position -= step
            handle.seek(position)
            buffer = handle.read(step) + buffer
            lines = buffer.rstrip(b"\n").split(b"\n")
            if len(lines) > count or position == 0:
                return [line for line in lines[-count:] if line]
    return []


def _parse(line: bytes | str) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) and {"seq", "at", "kind", "data", "prev", "hash"} <= set(entry) else None


def _ends_with_newline(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return True
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) == b"\n"


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, handle, previous: dict | None, kind: str, data, at: str) -> dict:
        seq = previous["seq"] + 1 if previous else 0
        prev = previous["hash"] if previous else GENESIS
        entry = {"seq": seq, "at": at, "kind": kind, "data": data, "prev": prev,
                 "hash": entry_hash(seq, at, kind, data, prev)}
        handle.write(canonical(entry) + "\n")
        return entry

    def append(self, kind: str, data, *, now) -> dict:
        """Ajoute une entrée (écrite puis synchronisée sur disque avant de rendre la main)."""
        at = utc_iso(now)
        data = clean(data)
        with self._locked():
            unfinished = not _ends_with_newline(self.path)
            tail = _tail(self.path, 2)
            broken = tail[-1] if unfinished and tail and _parse(tail[-1]) is None else None
            valid = [entry for entry in (_parse(line) for line in (tail[:-1] if broken else tail)) if entry]
            previous = valid[-1] if valid else None
            if previous is not None and at < previous["at"]:
                raise ValueError(f"horodatage {at} antérieur à la dernière entrée ({previous['at']})")
            with self.path.open("a", encoding="utf-8") as handle:
                if unfinished:
                    handle.write("\n")             # ligne complète sans saut final, ou ligne coupée
                if broken is not None:
                    previous = self._write(handle, previous, TRUNCATED, {
                        "raw_sha256": hashlib.sha256(broken).hexdigest(), "raw_length": len(broken)}, at)
                entry = self._write(handle, previous, kind, data, at)
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def entries(self, kinds: set[str] | None = None) -> Iterator[dict]:
        """Entrées valides, dans l'ordre (une ligne coupée, signalée par LIGNE_TRONQUEE, est passée)."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entry = _parse(line)
                    if entry is not None and (kinds is None or entry["kind"] in kinds):
                        yield entry

    def first(self, kind: str) -> dict | None:
        return next(self.entries({kind}), None)

    def verify(self) -> dict:
        """Recalcule toute la chaîne : numéros consécutifs, empreintes, liens et horodatages croissants. Une ligne
        illisible n'est admise que si l'entrée suivante est LIGNE_TRONQUEE avec son empreinte exacte."""
        prev, previous_at, count, cut = GENESIS, "", 0, None
        if not self.path.exists():
            return {"ok": True, "entries": 0, "broken_at": None, "reason": None, "truncated": 0}
        truncated = 0
        with self.path.open("rb") as handle:
            for number, raw in enumerate(handle):
                line = raw.rstrip(b"\n")
                entry = _parse(line) if line else None
                if entry is None:
                    if cut is not None or not line:
                        return {"ok": False, "entries": count, "broken_at": number,
                                "reason": "ligne vide ou illisible", "truncated": truncated}
                    cut = hashlib.sha256(line).hexdigest()
                    continue
                expected = entry_hash(entry["seq"], entry["at"], entry["kind"], entry["data"], entry["prev"])
                reason = None
                if cut is not None and (entry["kind"] != TRUNCATED or entry["data"].get("raw_sha256") != cut):
                    reason = "ligne illisible non signalée par LIGNE_TRONQUEE"
                elif entry["seq"] != count:
                    reason = f"numéro {entry['seq']} au lieu de {count}"
                elif entry["prev"] != prev:
                    reason = "lien vers l'entrée précédente rompu"
                elif entry["hash"] != expected:
                    reason = "contenu modifié (empreinte différente)"
                elif entry["at"] < previous_at:
                    reason = "horodatage antérieur à l'entrée précédente"
                if reason:
                    return {"ok": False, "entries": count, "broken_at": number, "reason": reason,
                            "truncated": truncated}
                truncated += cut is not None
                prev, previous_at, count, cut = entry["hash"], entry["at"], count + 1, None
        if cut is not None:
            return {"ok": True, "entries": count, "broken_at": None, "truncated": truncated,
                    "reason": "dernière ligne coupée (elle sera signalée au prochain ajout)"}
        return {"ok": True, "entries": count, "broken_at": None, "reason": None, "truncated": truncated}
