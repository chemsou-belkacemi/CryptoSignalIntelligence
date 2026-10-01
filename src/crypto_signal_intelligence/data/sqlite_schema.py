"""Migrations SQLite idempotentes et sûres quand plusieurs connexions ouvrent la base en même temps.

Vérifier puis ajouter une colonne n'est pas atomique : deux connexions (surveillance, API, tableau de bord
qui lance plusieurs requêtes à la fois) peuvent voir la colonne absente au même instant ; la seconde reçoit
alors « duplicate column name », qui signifie simplement que la migration est déjà faite.
"""
from __future__ import annotations

import sqlite3


def add_column(db: sqlite3.Connection, table: str, name: str, kind: str) -> None:
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def add_missing_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, kind in columns.items():
        if name not in existing:
            add_column(db, table, name, kind)
