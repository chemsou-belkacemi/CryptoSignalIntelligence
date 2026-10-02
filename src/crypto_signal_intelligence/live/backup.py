"""Sauvegarde, restauration et reprise (point 20 du cahier des charges).

Sauvegardé : registres SQLite (signaux publiés, retours d'exécution, signaux externes, expériences,
actualités, archives ingérées) copiés de façon cohérente par l'API de sauvegarde SQLite, fichiers
de signaux (shadow, outbox), configuration et modèles actifs (`models/`). Les bougies (`data/`) sont
re-téléchargeables et vérifiables par SHA-256 : incluses seulement sur demande.

Restauration : l'archive est entièrement vérifiée (empreintes du manifeste) avant toute écriture,
l'état courant est d'abord sauvegardé, une surveillance en cours bloque l'opération, et la
publication est SUSPENDUE ensuite : un état restauré peut être en retard sur le consommateur, qui a
peut-être déjà reçu des signaux absents de la sauvegarde. La reprise est explicite
(`publication-resume`) après réconciliation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import Settings
from .lock import InstanceLock

SQLITE_FILES = ("signals/registry.sqlite3", "signals/feedback.sqlite3", "signals/external.sqlite3",
                "experiments/experiments.sqlite3", "news/news.sqlite3", "data/raw/archives.sqlite3",
                "signals/plans.sqlite3", "signals/generated_outcomes.sqlite3")
PLAIN_DIRS = ("signals/shadow", "signals/outbox", "config", "models")
SUSPENSION_FILE = "state/PUBLICATION_SUSPENDED"
MANIFEST = "manifest.json"


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_copy(source: Path, target: Path) -> None:
    """Copie cohérente même si une autre connexion écrit (API de sauvegarde SQLite)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:              # `with sqlite3.connect()` ne ferme PAS la connexion (verrou de fichier sous Windows)
        dst.close()
        src.close()


def create_backup(settings: Settings, destination: Path, *, now: datetime, with_data: bool = False) -> Path:
    root = settings.root
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"csi-backup-{now:%Y%m%dT%H%M%SZ}.zip"
    files: dict[str, Path] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for relative in SQLITE_FILES:
            if (root / relative).exists():
                copy = Path(tmp) / relative
                _sqlite_copy(root / relative, copy)
                files[relative] = copy
        folders = [*PLAIN_DIRS, *(["data"] if with_data else [])]
        for folder in folders:
            for path in sorted((root / folder).rglob("*")) if (root / folder).exists() else []:
                relative = path.relative_to(root).as_posix()
                if path.is_file() and relative not in files and not relative.endswith((".tmp", "-journal", "-wal")):
                    files[relative] = path
        manifest = {"created_at": now.isoformat(), "root": str(root), "with_data": with_data,
                    "files": {name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
                              for name, path in sorted(files.items())}}
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, path in sorted(files.items()):
                zf.write(path, name)
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
    return archive


def verify_backup(archive: Path) -> dict:
    """Vérifie toutes les empreintes ; lève BackupError au premier écart (rien n'est écrit)."""
    with zipfile.ZipFile(archive) as zf:
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except KeyError:
            raise BackupError("manifeste absent : ce n'est pas une sauvegarde CSI") from None
        names = set(zf.namelist()) - {MANIFEST}
        if names != set(manifest["files"]):
            raise BackupError("contenu différent du manifeste")
        for name, meta in manifest["files"].items():
            if ".." in Path(name).parts or Path(name).is_absolute():
                raise BackupError(f"chemin refusé : {name}")
            if hashlib.sha256(zf.read(name)).hexdigest() != meta["sha256"]:
                raise BackupError(f"empreinte incorrecte : {name}")
    return manifest


@dataclass(frozen=True)
class RestoreResult:
    restored_files: int
    safety_backup: Path
    suspension_file: Path


def restore_backup(settings: Settings, archive: Path, *, now: datetime) -> RestoreResult:
    manifest = verify_backup(archive)
    root = settings.root
    # Une surveillance active écrirait pendant la restauration : refus.
    with InstanceLock(root / settings.live.lock_file):
        safety = create_backup(settings, root / "backups" / "avant-restauration", now=now,
                               with_data=manifest.get("with_data", False))
        with zipfile.ZipFile(archive) as zf:
            for name in manifest["files"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
        suspension = root / SUSPENSION_FILE
        suspension.parent.mkdir(parents=True, exist_ok=True)
        suspension.write_text(f"restauration de {archive.name} le {now.isoformat()} ; reprise : publication-resume\n",
                              encoding="utf-8")
    return RestoreResult(len(manifest["files"]), safety, suspension)


def publication_suspended(settings: Settings) -> str | None:
    path = settings.root / SUSPENSION_FILE
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def resume_publication(settings: Settings) -> bool:
    path = settings.root / SUSPENSION_FILE
    if not path.exists():
        return False
    path.unlink()
    return True
