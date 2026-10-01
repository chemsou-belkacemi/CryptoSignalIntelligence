"""Importation durable des événements d'exécution : dédoublonnage sur event_id, lignes invalides comptées."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..data.sqlite_schema import add_missing_columns
from .schema import ExecutionEvent, parse_line

COLUMNS = ("event_id", "signal_id", "event_type", "occurred_at", "environment", "producer", "symbol", "quantity",
           "price", "quote_quantity", "fee", "fee_asset", "order_id", "target_index", "reason", "exit_policy_hash",
           "known_signal", "imported_at", "source_file")

@dataclass
class ImportSummary:
    file: str
    lines: int = 0
    imported: int = 0
    duplicates: int = 0
    unknown_signals: list[str] = field(default_factory=list)
    invalid: list[tuple[int, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"file": self.file, "lines": self.lines, "imported": self.imported, "duplicates": self.duplicates,
                "unknown_signals": sorted(set(self.unknown_signals)),
                "invalid": [{"line": n, "error": e} for n, e in self.invalid]}


class FeedbackStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS execution_events (
                event_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, environment TEXT NOT NULL, producer TEXT NOT NULL, symbol TEXT NOT NULL,
                quantity TEXT, price TEXT, quote_quantity TEXT, fee TEXT, fee_asset TEXT, order_id TEXT,
                target_index INTEGER, reason TEXT, known_signal INTEGER NOT NULL, imported_at TEXT NOT NULL,
                source_file TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS execution_events_signal ON execution_events (signal_id, occurred_at)")
            add_missing_columns(db, "execution_events", {"exit_policy_hash": "TEXT"})   # retour d'exécution v2
            yield db
            db.commit()
        finally:
            db.close()

    def import_jsonl(self, path: Path, *, now: datetime, known_signal_ids: set[str] | None = None) -> ImportSummary:
        """Importe un fichier JSONL ; les lignes déjà connues (event_id) ou invalides ne bloquent pas les autres."""
        summary = ImportSummary(file=str(path))
        text = Path(path).read_text(encoding="utf-8")
        with self.connect() as db:
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                summary.lines += 1
                try:
                    event = parse_line(line)
                except ValueError as exc:
                    summary.invalid.append((number, str(exc)))
                    continue
                known = known_signal_ids is None or event.signal_id in known_signal_ids
                if not known:
                    summary.unknown_signals.append(event.signal_id)
                cursor = db.execute(f"INSERT OR IGNORE INTO execution_events ({', '.join(COLUMNS)}) "
                                    f"VALUES ({', '.join('?' * len(COLUMNS))})", (
                    event.event_id, event.signal_id, event.event_type, event.occurred_at.isoformat(),
                    event.environment, event.producer, event.symbol, _text(event.quantity), _text(event.price),
                    _text(event.quote_quantity), _text(event.fee), event.fee_asset, event.order_id,
                    event.target_index, event.reason, event.exit_policy_hash, int(known), now.isoformat(), str(path)))
                if cursor.rowcount:
                    summary.imported += 1
                else:
                    summary.duplicates += 1
        return summary

    def events_for(self, signal_id: str) -> list[ExecutionEvent]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM execution_events WHERE signal_id=? ORDER BY occurred_at, event_id",
                              (signal_id,)).fetchall()
        return [_event(row) for row in rows]

    def signal_ids(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT DISTINCT signal_id FROM execution_events ORDER BY signal_id").fetchall()
        return [row["signal_id"] for row in rows]


def _text(value) -> str | None:
    return None if value is None else str(value)


def _event(row: sqlite3.Row) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=row["event_id"], signal_id=row["signal_id"], event_type=row["event_type"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]), environment=row["environment"],
        producer=row["producer"], symbol=row["symbol"], quantity=row["quantity"], price=row["price"],
        quote_quantity=row["quote_quantity"], fee=row["fee"], fee_asset=row["fee_asset"], order_id=row["order_id"],
        target_index=row["target_index"], reason=row["reason"], exit_policy_hash=row["exit_policy_hash"])
