"""Archives officielles Binance Public Data (Spot klines), avec contrôle SHA-256.

URL : {base}/data/spot/{monthly|daily}/klines/{SYMBOL}/{TF}/{SYMBOL}-{TF}-{période}.zip
Chaque archive a un fichier `.CHECKSUM` (« <sha256>  <nom> »). Une archive dont
le hash ne correspond pas est refusée. Les archives brutes sont conservées dans
data/raw avec leur hash : une archive republiée (hash différent) est détectée.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .http import NotFound, PublicHttpClient
from .schema import RAW_COLUMNS
from .timeunits import archive_unit

SOURCE = "BINANCE_PUBLIC_DATA"


class ChecksumError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveRef:
    symbol: str
    timeframe: str
    granularity: str   # monthly | daily
    period: date       # 1er du mois (monthly) ou jour (daily)

    @property
    def label(self) -> str:
        return self.period.strftime("%Y-%m") if self.granularity == "monthly" else self.period.isoformat()

    @property
    def filename(self) -> str:
        return f"{self.symbol}-{self.timeframe}-{self.label}.zip"

    @property
    def path(self) -> str:
        return f"/data/spot/{self.granularity}/klines/{self.symbol}/{self.timeframe}/{self.filename}"

    @property
    def unit(self) -> str:
        return archive_unit(self.period)


def plan(symbol: str, timeframe: str, start: date, today: date) -> list[ArchiveRef]:
    """Mois complets passés en mensuel ; mois courant en quotidien jusqu'à hier."""
    refs = []
    month = date(start.year, start.month, 1)
    current_month = date(today.year, today.month, 1)
    while month < current_month:
        refs.append(ArchiveRef(symbol, timeframe, "monthly", month))
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    day = max(current_month, start)
    while day < today:
        refs.append(ArchiveRef(symbol, timeframe, "daily", day))
        day += timedelta(days=1)
    return refs


def daily_refs_for_month(ref: ArchiveRef, today: date) -> list[ArchiveRef]:
    """Repli quand l'archive mensuelle n'est pas encore publiée."""
    day, refs = ref.period, []
    while day.month == ref.period.month and day < today:
        refs.append(ArchiveRef(ref.symbol, ref.timeframe, "daily", day))
        day += timedelta(days=1)
    return refs


def parse_checksum(text: str, filename: str) -> str:
    match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$", text.strip())
    if not match or match.group(2) != filename:
        raise ChecksumError(f"Fichier CHECKSUM illisible pour {filename}")
    return match.group(1).lower()


def verify(content: bytes, expected_sha256: str, filename: str) -> str:
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_sha256:
        raise ChecksumError(f"SHA-256 invalide pour {filename} : attendu {expected_sha256}, obtenu {actual}")
    return actual


def read_zip_csv(content: bytes) -> pd.DataFrame:
    """CSV de l'archive (sans en-tête en Spot ; un éventuel en-tête est toléré)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Archive inattendue : {archive.namelist()}")
        raw = archive.read(names[0]).decode("utf-8")
    first = raw.split("\n", 1)[0]
    header = 0 if not first[:1].isdigit() else None
    frame = pd.read_csv(io.StringIO(raw), header=header, dtype=str)
    if frame.shape[1] != len(RAW_COLUMNS):
        raise ValueError(f"{frame.shape[1]} colonnes au lieu de {len(RAW_COLUMNS)}")
    frame.columns = RAW_COLUMNS
    frame["open_time"] = frame["open_time"].astype("int64")
    frame["close_time"] = frame["close_time"].astype("int64")
    return frame


@dataclass
class DownloadedArchive:
    ref: ArchiveRef
    sha256: str
    raw: pd.DataFrame
    raw_path: Path


def fetch_archive(client: PublicHttpClient, ref: ArchiveRef, raw_dir: Path) -> DownloadedArchive:
    """Télécharge (ou relit en cache si le hash publié est inchangé) et vérifie."""
    expected = parse_checksum(client.get(ref.path + ".CHECKSUM").text, ref.filename)
    target = raw_dir / ref.symbol / ref.timeframe / ref.granularity / ref.filename
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        content = target.read_bytes()
    else:
        content = client.get_bytes(ref.path)
        verify(content, expected, ref.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    return DownloadedArchive(ref, expected, read_zip_csv(content), target)


__all__ = ["SOURCE", "ArchiveRef", "ChecksumError", "DownloadedArchive", "NotFound", "daily_refs_for_month",
           "fetch_archive", "parse_checksum", "plan", "read_zip_csv", "verify"]
