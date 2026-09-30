"""Historique prospectif des actualités (SQLite) : traçabilité, corrections, regroupement, santé des sources.

- chaque élément garde source, URL, titre, actifs, date de publication, PREMIÈRE réception (jamais
  réécrite) et dernière observation ;
- un même identifiant dont le titre ou le résumé change est une CORRECTION : nouvelle révision
  conservée, l'ancienne n'est pas effacée ;
- les articles qui reprennent la même information (titres proches, fenêtre de temps, mêmes actifs)
  partagent un identifiant d'événement ;
- l'état de chaque source (dernier essai, dernier succès, erreur, vérification) est tenu à part :
  une source muette n'est jamais lue comme « rien à signaler ».
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .parse import RawItem

STOPWORDS = frozenset(["a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "from", "as", "is", "are", "was", "were", "be", "been", "will", "would", "could", "should", "may", "might", "this", "that", "these", "those", "it", "its", "into", "over", "after", "before", "about", "amid", "new", "says", "said", "report", "reports", "price", "prices", "crypto", "market", "markets", "today", "week"])


def title_tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return frozenset(w for w in words if len(w) >= 3 and w not in STOPWORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class StoredResult:
    item_id: str
    status: str            # NEW | REVISED | UNCHANGED
    event_id: str


class NewsStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS news_items (
                    item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, category TEXT NOT NULL, guid TEXT NOT NULL,
                    url TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, feed_category TEXT,
                    published_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, revision INTEGER NOT NULL, assets TEXT NOT NULL,
                    event_id TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS news_items_seen ON news_items (first_seen_at);
                CREATE TABLE IF NOT EXISTS news_revisions (
                    item_id TEXT NOT NULL, revision INTEGER NOT NULL, seen_at TEXT NOT NULL, title TEXT NOT NULL,
                    summary TEXT NOT NULL, content_sha256 TEXT NOT NULL, PRIMARY KEY (item_id, revision));
                CREATE TABLE IF NOT EXISTS news_events (
                    event_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    title TEXT NOT NULL, tokens TEXT NOT NULL, assets TEXT NOT NULL, sources TEXT NOT NULL,
                    item_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_health (
                    source_id TEXT PRIMARY KEY, last_attempt_at TEXT, last_success_at TEXT, last_error TEXT,
                    items_last INTEGER, verified_at TEXT, verification_detail TEXT);""")
            yield db
            db.commit()
        finally:
            db.close()

    # --- éléments -----------------------------------------------------------------------------
    def upsert(self, *, source_id: str, category: str, item: RawItem, assets: list[str], now: datetime,
               window: timedelta, similarity: float) -> StoredResult:
        item_id = hashlib.sha256(f"{source_id}\x00{item.guid}".encode()).hexdigest()[:20]
        digest = hashlib.sha256(f"{item.title}\x00{item.summary}".encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT * FROM news_items WHERE item_id=?", (item_id,)).fetchone()
            if row is not None:
                if row["content_sha256"] == digest:
                    db.execute("UPDATE news_items SET last_seen_at=? WHERE item_id=?", (now.isoformat(), item_id))
                    return StoredResult(item_id, "UNCHANGED", row["event_id"])
                revision = row["revision"] + 1       # correction : la version précédente reste en historique
                db.execute("""UPDATE news_items SET title=?, summary=?, content_sha256=?, revision=?, last_seen_at=?,
                              assets=? WHERE item_id=?""",
                           (item.title, item.summary, digest, revision, now.isoformat(), json.dumps(assets), item_id))
                db.execute("INSERT INTO news_revisions VALUES (?,?,?,?,?,?)",
                           (item_id, revision, now.isoformat(), item.title, item.summary, digest))
                return StoredResult(item_id, "REVISED", row["event_id"])
            event_id = self._event_for(db, item_id, item.title, assets, source_id, now, window, similarity)
            db.execute("INSERT INTO news_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                item_id, source_id, category, item.guid, item.url, item.title, item.summary, item.category,
                item.published_at.isoformat() if item.published_at else None, now.isoformat(), now.isoformat(),
                digest, 0, json.dumps(assets), event_id))
            db.execute("INSERT INTO news_revisions VALUES (?,?,?,?,?,?)",
                       (item_id, 0, now.isoformat(), item.title, item.summary, digest))
            return StoredResult(item_id, "NEW", event_id)

    @staticmethod
    def _event_for(db: sqlite3.Connection, item_id: str, title: str, assets: list[str], source_id: str,
                   now: datetime, window: timedelta, similarity: float) -> str:
        """Même information reprise ailleurs : titres proches (Jaccard) et mêmes actifs, dans la fenêtre."""
        tokens = title_tokens(title)
        best: tuple[float, sqlite3.Row] | None = None
        for row in db.execute("SELECT * FROM news_events WHERE last_seen_at >= ?", ((now - window).isoformat(),)):
            # Une source ne se « reprend » pas elle-même : deux annonces d'un même émetteur au titre voisin
            # (ex. modèles datés) sont deux informations distinctes.
            if sorted(json.loads(row["assets"])) != sorted(assets) or source_id in json.loads(row["sources"]):
                continue
            score = jaccard(tokens, frozenset(json.loads(row["tokens"])))
            if score >= similarity and (best is None or score > best[0]):
                best = (score, row)
        if best is not None:
            row = best[1]
            sources = sorted(set(json.loads(row["sources"])) | {source_id})
            db.execute("UPDATE news_events SET last_seen_at=?, sources=?, item_count=item_count+1 WHERE event_id=?",
                       (now.isoformat(), json.dumps(sources), row["event_id"]))
            return str(row["event_id"])
        event_id = f"EV-{now:%Y%m%dT%H%M%SZ}-{item_id[:12]}"   # unique : dérivé de l'élément fondateur
        db.execute("INSERT INTO news_events VALUES (?,?,?,?,?,?,?,?)",
                   (event_id, now.isoformat(), now.isoformat(), title, json.dumps(sorted(tokens)),
                    json.dumps(sorted(assets)), json.dumps([source_id]), 1))
        return event_id

    def recent(self, since: datetime, asset: str | None = None, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT i.*, e.sources AS event_sources, e.item_count AS event_items
                                 FROM news_items i JOIN news_events e USING (event_id)
                                 WHERE i.first_seen_at >= ?
                                 ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC LIMIT ?""",
                              (since.isoformat(), limit)).fetchall()
        items = [dict(row) | {"assets": json.loads(row["assets"]), "event_sources": json.loads(row["event_sources"])}
                 for row in rows]
        return [i for i in items if asset is None or asset in i["assets"]]

    def revisions(self, item_id: str) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM news_revisions WHERE item_id=? ORDER BY revision",
                                                (item_id,))]

    # --- santé des sources --------------------------------------------------------------------
    def record_attempt(self, source_id: str, now: datetime, *, error: str | None, items: int | None) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO source_health (source_id) VALUES (?)", (source_id,))
            if error is None:
                db.execute("""UPDATE source_health SET last_attempt_at=?, last_success_at=?, last_error=NULL,
                              items_last=? WHERE source_id=?""", (now.isoformat(), now.isoformat(), items, source_id))
            else:
                db.execute("UPDATE source_health SET last_attempt_at=?, last_error=? WHERE source_id=?",
                           (now.isoformat(), error[:500], source_id))

    def record_verification(self, source_id: str, now: datetime, *, ok: bool, detail: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO source_health (source_id) VALUES (?)", (source_id,))
            db.execute("UPDATE source_health SET verified_at=?, verification_detail=? WHERE source_id=?",
                       (now.isoformat() if ok else None, detail[:500], source_id))

    def health(self) -> dict[str, dict]:
        with self.connect() as db:
            return {row["source_id"]: dict(row) for row in db.execute("SELECT * FROM source_health")}
