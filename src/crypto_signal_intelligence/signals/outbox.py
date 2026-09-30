"""Publication durable des signaux TXT : registre SQLite + fichier atomique.

Séquence : registre PENDING (payload canonique) → écriture `.tmp` dans le
même dossier → flush + fsync → fermeture → renommage atomique en `.txt` →
registre PUBLISHED. Un crash entre deux étapes est réparé par `reconcile()`
avec la même clé et le même contenu. Ceci ne garantit PAS une exécution
unique : le consommateur doit dédoublonner sur SIGNAL_ID / IDEMPOTENCY_KEY.
Le consommateur ne doit lire que les fichiers `.txt`.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .schema import Signal
from .txt import parse, serialize

REPLACE_RETRIES = 8
REPLACE_BACKOFF_SECONDS = 0.15
WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")   # C:\… ou C:/… ou \\serveur\…
SEPARATORS = re.compile(r"[\\/]+")


@dataclass(frozen=True)
class PublishResult:
    signal_id: str
    idempotency_key: str
    path: Path
    status: str          # PUBLISHED | DUPLICATE
    content_sha256: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """Écrit `path` sans jamais exposer de fichier partiel sous son nom final."""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:  # Windows : fichier ouvert par un lecteur/antivirus
            if attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))


class SignalRegistry:
    """Registre des publications. Les dossiers sont enregistrés RELATIVEMENT à la racine du projet
    (par défaut : le dossier parent de `signals/`), pour qu'une sauvegarde restaurée ailleurs (autre
    PC, conteneur Docker, VPS) désigne toujours les bons dossiers. Les anciens enregistrements en
    chemin absolu étranger (ex. chemin Windows lu sous Linux) sont ramenés à leur partie relative."""

    def __init__(self, db_path: Path, directory: Path, root: Path | None = None):
        self.db_path = Path(db_path)
        self.directory = Path(directory)
        self.root = Path(root) if root is not None else self.db_path.parent.parent

    def _stored_directory(self, directory: Path) -> str:
        try:
            return directory.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(directory)          # hors de la racine (ex. dossier de dépôt d'un autre programme)

    def directory_of(self, row: sqlite3.Row) -> Path:
        """Dossier réel d'un enregistrement, quel que soit le système qui l'a écrit.

        - relatif (format actuel) : sous la racine du projet ;
        - absolu du système courant : tel quel (ex. dossier de dépôt d'un autre programme) ;
        - absolu d'un AUTRE système (chemin Windows lu sous Linux, ou l'inverse après restauration) :
          ramené aux derniers composants sous la racine (ex. « signals/shadow »).
        """
        stored = str(row["directory"])
        windows_absolute = bool(WINDOWS_ABSOLUTE.match(stored))
        posix_absolute = stored.startswith("/")
        if not windows_absolute and not posix_absolute:
            return self.root.joinpath(*[part for part in SEPARATORS.split(stored) if part])
        if (windows_absolute and os.name == "nt") or (posix_absolute and os.name != "nt"):
            return Path(stored)
        parts = [part for part in SEPARATORS.split(stored) if part]
        for depth in (2, 1):
            candidate = self.root.joinpath(*parts[-depth:])
            if candidate.resolve() == self.directory.resolve() or candidate.exists():
                return candidate
        return self.root.joinpath(*parts[-2:])

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL, strategy TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PENDING', 'PUBLISHED', 'CONFLICT')),
                directory TEXT NOT NULL, filename TEXT NOT NULL,
                content_sha256 TEXT NOT NULL, payload TEXT NOT NULL,
                published_at TEXT)""")
            yield db
            db.commit()
        finally:
            db.close()

    def publish(self, signal: Signal, now: datetime) -> PublishResult:
        text = serialize(signal)
        digest = sha256_text(text)
        filename = f"{signal.signal_id}.txt"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM signals WHERE idempotency_key=?",
                                  (signal.idempotency_key,)).fetchone()
            if existing is not None:
                db.commit()
                if existing["status"] == "PENDING":
                    self._finish(existing)
                return PublishResult(existing["signal_id"], existing["idempotency_key"],
                                     self.directory_of(existing) / existing["filename"], "DUPLICATE",
                                     existing["content_sha256"])
            db.execute("""INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, NULL)""",
                       (signal.signal_id, signal.idempotency_key, signal.symbol, signal.strategy,
                        # colonne expires_at = fin de validité des ENTRÉES (un signal dont l'ordre peut encore
                        # être rempli reste actif pour le contrôle DUPLICATE), pas la fin d'acceptation.
                        signal.created_at.isoformat(), signal.entry_expires_at.isoformat(),
                        self._stored_directory(self.directory), filename, digest, text))
        row = self._row(signal.signal_id)
        self._finish(row, now=now)
        return PublishResult(signal.signal_id, signal.idempotency_key, self.directory / filename,
                             "PUBLISHED", digest)

    def _row(self, signal_id: str) -> sqlite3.Row:
        with self.connect() as db:
            return db.execute("SELECT * FROM signals WHERE signal_id=?", (signal_id,)).fetchone()

    def _finish(self, row: sqlite3.Row, now: datetime | None = None) -> str:
        """Amène un enregistrement PENDING à PUBLISHED (idempotent)."""
        directory = self.directory_of(row)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / row["filename"]
        status = "PUBLISHED"
        if path.exists():
            if sha256_text(path.read_text(encoding="utf-8")) != row["content_sha256"]:
                status = "CONFLICT"  # jamais d'écrasement silencieux d'un fichier différent
        else:
            atomic_write_text(path, row["payload"])
        with self.connect() as db:
            db.execute("UPDATE signals SET status=?, published_at=? WHERE signal_id=? AND status='PENDING'",
                       (status, (now.isoformat() if now else datetime.now(UTC).isoformat()),
                        row["signal_id"]))
        return status

    def reconcile(self) -> dict[str, int]:
        """Après crash : termine les PENDING, supprime les .tmp orphelins."""
        counts = {"published": 0, "conflicts": 0, "tmp_removed": 0}
        with self.connect() as db:
            pending = db.execute("SELECT * FROM signals WHERE status='PENDING'").fetchall()
        for row in pending:
            if self._finish(row) == "PUBLISHED":
                counts["published"] += 1
            else:
                counts["conflicts"] += 1
        if self.directory.exists():
            for leftover in self.directory.glob("*.txt.tmp"):
                leftover.unlink(missing_ok=True)
                counts["tmp_removed"] += 1
        return counts

    def active_for(self, symbol: str, strategy: str, now: datetime) -> list[sqlite3.Row]:
        """Signaux non expirés d'une paire/stratégie (contrôle DUPLICATE en direct)."""
        with self.connect() as db:
            return db.execute("""SELECT * FROM signals WHERE symbol=? AND strategy=?
                                 AND expires_at > ? AND status != 'CONFLICT'""",
                              (symbol, strategy, now.isoformat())).fetchall()

    def load(self, signal_id: str) -> Signal:
        row = self._row(signal_id)
        if row is None:
            raise KeyError(signal_id)
        return parse(row["payload"])

    def rows(self) -> list[sqlite3.Row]:
        """Tous les enregistrements (PENDING, PUBLISHED, CONFLICT), du plus ancien au plus récent."""
        with self.connect() as db:
            return db.execute("SELECT * FROM signals ORDER BY created_at, signal_id").fetchall()
