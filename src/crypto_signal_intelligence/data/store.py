"""Cache local : chandeliers canoniques en Parquet, registre des archives en SQLite."""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .schema import CANONICAL_COLUMNS, PRICE_COLUMNS, VOLUME_COLUMNS

logger = logging.getLogger("csi.store")
# Blocs de ~6 mois de bougies 15m : une lecture filtrée (bougies récentes) ne lit que les derniers blocs.
ROW_GROUP_SIZE = 16_384
# Une archive officielle vérifiée prime sur une ligne REST pour la même bougie.
SOURCE_PRIORITY = {"BINANCE_PUBLIC_DATA": 2, "BINANCE_REST_KLINES": 1}


class CandleStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / "candles" / "binance" / "spot" / symbol / f"{timeframe}.parquet"

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        return pd.read_parquet(path)

    def load_since(self, symbol: str, timeframe: str, since: pd.Timestamp) -> pd.DataFrame:
        """Bougies ouvertes à partir de `since` seulement (filtre à la lecture : pas 5 ans en mémoire)."""
        path = self.path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        table = pq.read_table(path, filters=[("open_time", ">=", pd.Timestamp(since).to_pydatetime())])
        return table.to_pandas().sort_values("open_time").reset_index(drop=True)

    def last_open_time(self, symbol: str, timeframe: str) -> pd.Timestamp | None:
        """Ouverture de la dernière bougie stockée, en ne lisant que cette colonne (~1,6 Mo au lieu de ~50)."""
        path = self.path(symbol, timeframe)
        if not path.exists():
            return None
        column = pq.read_table(path, columns=["open_time"]).column("open_time")
        if len(column) == 0:
            return None
        return pd.Timestamp(pc.max(column).as_py()).tz_convert("UTC")

    def upsert_tail(self, incoming: pd.DataFrame, symbol: str, timeframe: str) -> tuple[pd.Timestamp | None, int]:
        """Ajoute/révise des bougies RÉCENTES sans convertir tout l'historique en pandas.

        Les lignes antérieures à la plus ancienne bougie reçue restent en Arrow (aucun objet Python) ;
        seul le chevauchement est fusionné avec `merge` (mêmes priorités et mêmes comptes de révisions),
        puis le fichier est réécrit atomiquement. Retourne (dernière ouverture, bougies révisées).
        Résultat identique à `save(merge(load(...), incoming))` (vérifié par test).
        """
        path = self.path(symbol, timeframe)
        if incoming.empty:
            return self.last_open_time(symbol, timeframe), 0
        if not path.exists():
            merged, changed = merge(pd.DataFrame(columns=CANONICAL_COLUMNS), incoming)
            self.save(merged, symbol, timeframe)
            return merged["open_time"].max(), changed
        table = pq.read_table(path)
        cutoff = pa.scalar(incoming["open_time"].min().to_pydatetime(), type=table.schema.field("open_time").type)
        recent = pc.greater_equal(table.column("open_time"), cutoff)
        head = table.filter(pc.invert(recent))
        tail = table.filter(recent).to_pandas()
        merged_tail, changed = merge(tail, incoming)
        tail_table = pa.Table.from_pandas(merged_tail[CANONICAL_COLUMNS], preserve_index=False).cast(table.schema)
        combined = pa.concat_tables([head, tail_table])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        pq.write_table(combined, temporary, row_group_size=ROW_GROUP_SIZE)
        os.replace(temporary, path)
        return pd.Timestamp(merged_tail["open_time"].max()), changed

    def save(self, frame: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        path = self.path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        frame[CANONICAL_COLUMNS].to_parquet(temporary, index=False, row_group_size=ROW_GROUP_SIZE)
        os.replace(temporary, path)
        return path

    def quarantine(self, bad: pd.DataFrame, symbol: str, timeframe: str) -> Path | None:
        if bad.empty:
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.data_dir / "quarantine" / symbol / f"{timeframe}-{stamp}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        bad.to_parquet(path, index=False)
        return path


def merge(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fusion idempotente ; renvoie (fusion, nombre de bougies dont les valeurs ont changé).

    Seule la partie de l'historique qui chevauche les nouvelles bougies est fusionnée (priorité,
    doublons, tri) ; le reste est recollé tel quel. Résultat identique à une fusion complète quand
    l'historique existant est trié sans doublon (ce que `save` garantit), pour un coût proportionnel
    au chevauchement au lieu de 5 ans de bougies à chaque cycle.
    """
    if existing.empty:
        return incoming.sort_values("open_time").reset_index(drop=True), 0
    if incoming.empty:
        return existing, 0
    times = existing["open_time"]
    if times.is_monotonic_increasing and not times.duplicated().any():
        cutoff = incoming["open_time"].min()
        split = int(times.searchsorted(cutoff, side="left"))
        if split > 0:
            merged_tail, changed = _merge_full(existing.iloc[split:], incoming)
            merged = pd.concat([existing.iloc[:split], merged_tail], ignore_index=True)
            return merged, changed
    return _merge_full(existing, incoming)


def _merge_full(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if existing.empty:
        return incoming.sort_values("open_time").reset_index(drop=True), 0
    both = pd.concat([existing.assign(_order=0), incoming.assign(_order=1)], ignore_index=True)
    both["_priority"] = both["source"].map(SOURCE_PRIORITY).fillna(0)
    both = both.sort_values(["open_time", "_priority", "_order"])
    compared = PRICE_COLUMNS + VOLUME_COLUMNS
    dup = both[both.duplicated("open_time", keep=False)]
    changed = 0
    if not dup.empty:
        spread = dup.groupby("open_time")[compared].agg(lambda s: s.max() - s.min())
        changed = int((spread.abs() > 1e-12).any(axis=1).sum())
    merged = both.drop_duplicates("open_time", keep="last").drop(columns=["_order", "_priority"])
    return merged.sort_values("open_time").reset_index(drop=True), changed


def content_hash(frame: pd.DataFrame) -> str:
    """Empreinte du contenu (temps + OHLCV), pour tracer la version des données d'une expérience."""
    if frame.empty:
        return "EMPTY"
    columns = ["open_time", *PRICE_COLUMNS, *VOLUME_COLUMNS]
    hashed = pd.util.hash_pandas_object(frame[columns], index=False).to_numpy()
    return hashlib.sha256(hashed.tobytes()).hexdigest()[:16]


class ArchiveRegistry:
    """Archives ingérées : chemin, hash publié, date. Détecte une republication."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS archives (
                path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, unit TEXT NOT NULL,
                rows INTEGER NOT NULL, ingested_at TEXT NOT NULL, revisions INTEGER NOT NULL DEFAULT 0)""")
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, path: str) -> tuple[str, int] | None:
        with self.connect() as db:
            row = db.execute("SELECT sha256, revisions FROM archives WHERE path=?", (path,)).fetchone()
        return (row[0], row[1]) if row else None

    def record(self, path: str, sha256: str, unit: str, rows: int) -> bool:
        """Enregistre ; renvoie True si une version différente existait (republication)."""
        previous = self.get(path)
        revised = previous is not None and previous[0] != sha256
        with self.connect() as db:
            db.execute("""INSERT INTO archives VALUES (?, ?, ?, ?, ?, 0)
                          ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, rows=excluded.rows,
                          ingested_at=excluded.ingested_at,
                          revisions=archives.revisions + (archives.sha256 != excluded.sha256)""",
                       (path, sha256, unit, rows, datetime.now(UTC).isoformat()))
        if revised:
            logger.warning("Archive republiée avec un contenu différent : %s", path)
        return revised

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM archives").fetchone()[0]
