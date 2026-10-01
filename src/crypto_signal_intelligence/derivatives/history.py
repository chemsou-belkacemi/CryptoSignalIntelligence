"""Historique des données publiques du marché à terme USDⓈ-M (docs/DERIVATIVES.md).

Archives officielles `data.binance.vision/data/futures/um/…`, vérifiées par SHA-256 (fichier `.CHECKSUM`),
une série Parquet par jeu de données et par paire, registre des archives ingérées (une archive republiée avec
un contenu différent est signalée). Chaque ligne porte `available_at`, HYPOTHÈSE prudente déclarée, jamais
l'heure de téléchargement :
- financement : connu `funding_latency_seconds` après son règlement ;
- prime (bougies 1 h) : fin de l'heure + latence des bougies Spot ;
- metrics (5 min) : l'archive date chaque valeur 5 min AVANT l'API publique (vérifié le 2026-10-01) ; connue
  `metrics_latency_seconds` (10 min + 2 s) après son horodatage d'archive.
Aucune donnée n'est inventée : une archive absente est comptée, jamais comblée.
"""
from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.archives import ChecksumError, parse_checksum, verify
from ..data.http import NotFound, PublicHttpClient
from ..data.store import ArchiveRegistry

logger = logging.getLogger("csi.derivatives")
PREFIX = "/data/futures/um"
WORKERS = 8
STEPS = {"premium": pd.Timedelta(hours=1), "metrics": pd.Timedelta(minutes=5)}
METRICS_COLUMNS = {"sum_open_interest": "oi", "sum_open_interest_value": "oi_value",
                   "count_toptrader_long_short_ratio": "top_accounts_ratio",
                   "sum_toptrader_long_short_ratio": "top_positions_ratio",
                   "count_long_short_ratio": "accounts_ratio", "sum_taker_long_short_vol_ratio": "taker_ratio"}


@dataclass(frozen=True)
class Dataset:
    name: str
    folder: str                       # nom du dossier d'archives Binance
    granularities: tuple[str, ...]    # monthly et/ou daily

    def path(self, symbol: str, granularity: str, label: str) -> str:
        if self.name == "premium":
            return f"{PREFIX}/{granularity}/{self.folder}/{symbol}/1h/{symbol}-1h-{label}.zip"
        return f"{PREFIX}/{granularity}/{self.folder}/{symbol}/{symbol}-{self.folder}-{label}.zip"


DATASETS = {
    "funding": Dataset("funding", "fundingRate", ("monthly",)),
    "premium": Dataset("premium", "premiumIndexKlines", ("monthly", "daily")),
    "metrics": Dataset("metrics", "metrics", ("daily",)),
}


@dataclass(frozen=True)
class Ref:
    dataset: str
    symbol: str
    granularity: str
    period: date

    @property
    def label(self) -> str:
        return self.period.strftime("%Y-%m") if self.granularity == "monthly" else self.period.isoformat()

    @property
    def path(self) -> str:
        return DATASETS[self.dataset].path(self.symbol, self.granularity, self.label)

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[1]


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def plan(dataset: str, symbol: str, start: date, today: date) -> list[Ref]:
    """Mois complets en mensuel (si le jeu en a) ; sinon, ou pour le mois en cours, jours complets."""
    spec = DATASETS[dataset]
    refs: list[Ref] = []
    current_month = date(today.year, today.month, 1)
    day = start
    if "monthly" in spec.granularities:
        month = date(start.year, start.month, 1)
        while month < current_month:
            refs.append(Ref(dataset, symbol, "monthly", month))
            month = _next_month(month)
        day = max(current_month, start)
    if "daily" in spec.granularities:
        while day < today:
            refs.append(Ref(dataset, symbol, "daily", day))
            day += timedelta(days=1)
    return refs


def daily_of(ref: Ref, today: date) -> list[Ref]:
    """Jours (passés) du mois d'une archive mensuelle pas encore publiée."""
    day, refs = ref.period, []
    while day.month == ref.period.month and day < today:
        refs.append(Ref(ref.dataset, ref.symbol, "daily", day))
        day += timedelta(days=1)
    return refs


def first_available(client: PublicHttpClient, dataset: str, symbol: str, start: date, today: date) -> date | None:
    """Premier mois publié pour la paire (contrat coté plus tard que `start`) : évite des milliers de 404."""
    spec = DATASETS[dataset]
    granularity = "monthly" if "monthly" in spec.granularities else "daily"
    month = date(start.year, start.month, 1)
    while month < today:
        label = month.strftime("%Y-%m") if granularity == "monthly" else month.isoformat()
        try:
            client.get(spec.path(symbol, granularity, label) + ".CHECKSUM")
            return max(month, start)
        except NotFound:
            month = _next_month(month)
    return None


def read_csv(content: bytes) -> pd.DataFrame:
    """CSV unique de l'archive, en texte ; un en-tête éventuel est reconnu (certaines archives n'en ont pas)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"archive inattendue : {archive.namelist()}")
        raw = archive.read(names[0]).decode("utf-8")
    header = None if raw[:1].isdigit() else 0
    return pd.read_csv(io.StringIO(raw), header=header, dtype=str)


def epoch_to_utc(values) -> pd.Series:
    """Horodatages entiers en millisecondes ou microsecondes (reconnues à leur ordre de grandeur)."""
    numbers = pd.to_numeric(pd.Series(values), errors="raise").astype("int64")
    if len(numbers) and (numbers.min() < 10**12 or numbers.max() >= 10**17):
        raise ValueError(f"horodatages hors d'échelle (min={numbers.min()}, max={numbers.max()})")
    unit = "us" if len(numbers) and numbers.max() >= 10**14 else "ms"
    return pd.Series(pd.to_datetime(numbers.to_numpy(), unit=unit, utc=True).as_unit("ns"))


def _number(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(float)


def parse(dataset: str, content: bytes, settings: Settings) -> pd.DataFrame:
    """Archive → lignes datées (`time`) avec `available_at` selon l'hypothèse déclarée du jeu de données."""
    raw = read_csv(content)
    if dataset == "funding":
        time = epoch_to_utc(raw.iloc[:, 0])
        frame = pd.DataFrame({"time": time, "rate": _number(raw.iloc[:, 2]), "interval_hours": _number(raw.iloc[:, 1])})
        latency = pd.Timedelta(seconds=settings.derivatives.funding_latency_seconds)
    elif dataset == "premium":
        time = epoch_to_utc(raw.iloc[:, 0])
        frame = pd.DataFrame({"time": time, **{k: _number(raw.iloc[:, i]) for i, k in
                                               enumerate(("open", "high", "low", "close"), start=1)}})
        latency = STEPS["premium"] + pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    elif dataset == "metrics":
        missing = set(METRICS_COLUMNS) - set(raw.columns)
        if missing:
            raise ValueError(f"colonnes metrics absentes : {sorted(missing)}")
        time = pd.Series(pd.to_datetime(raw["create_time"], utc=True).dt.as_unit("ns"))
        frame = pd.DataFrame({"time": time, **{name: _number(raw[col]) for col, name in METRICS_COLUMNS.items()}})
        latency = pd.Timedelta(seconds=settings.derivatives.metrics_latency_seconds)
    else:
        raise ValueError(f"jeu de données inconnu : {dataset}")
    frame["available_at"] = frame["time"] + latency
    return frame


class DerivativesStore:
    """Une série Parquet par jeu de données et par paire ; fusion idempotente sur `time`."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "derivatives" / "binance_um"

    def path(self, dataset: str, symbol: str) -> Path:
        return self.root / dataset / f"{symbol}.parquet"

    def load(self, dataset: str, symbol: str) -> pd.DataFrame:
        path = self.path(dataset, symbol)
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    def merge(self, dataset: str, symbol: str, incoming: pd.DataFrame) -> pd.DataFrame:
        frame = pd.concat([self.load(dataset, symbol), incoming], ignore_index=True)
        frame = frame.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
        path = self.path(dataset, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        frame.to_parquet(temporary, index=False, compression="zstd")
        temporary.replace(path)
        return frame


def quality(dataset: str, frame: pd.DataFrame) -> dict:
    """Lignes, période, trous (aucun comblement) et valeurs manquantes."""
    if frame.empty:
        return {"rows": 0, "first": None, "last": None, "gaps": 0, "largest_gap_hours": None, "missing_values": 0}
    times = frame["time"].reset_index(drop=True)
    spacing = times.diff()
    if dataset == "funding":
        expected = pd.to_timedelta(frame["interval_hours"].fillna(8).to_numpy(), unit="h")
        gaps = spacing.to_numpy()[1:] > (expected[1:] * 1.5)
    else:
        gaps = (spacing.iloc[1:] > STEPS[dataset] * 1.5).to_numpy()
    values = frame.drop(columns=["time", "available_at"])
    return {"rows": int(len(frame)), "first": times.iloc[0].isoformat(), "last": times.iloc[-1].isoformat(),
            "gaps": int(gaps.sum()), "largest_gap_hours": round(float(spacing.max() / pd.Timedelta(hours=1)), 2),
            "missing_values": int(values.isna().any(axis=1).sum())}


@dataclass
class Summary:
    dataset: str
    symbol: str
    start: str | None = None
    ingested: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    revised: int = 0
    quality: dict = field(default_factory=dict)


def _fetch(client: PublicHttpClient, ref: Ref) -> tuple[Ref, str | None, bytes | None, str | None]:
    """(référence, sha256, contenu, motif de rejet) ; absente : sha256 None sans motif."""
    try:
        expected = parse_checksum(client.get(ref.path + ".CHECKSUM").text, ref.filename)
        content = client.get_bytes(ref.path)
        verify(content, expected, ref.filename)
    except NotFound:
        return ref, None, None, None
    except ChecksumError as exc:
        return ref, None, None, str(exc)
    return ref, expected, content, None


def download(settings: Settings, *, datasets: list[str] | None = None, symbols: list[str] | None = None,
             now: datetime | None = None, client: PublicHttpClient | None = None,
             progress: Callable[[str], None] | None = None) -> list[Summary]:
    """Complète l'historique de chaque (jeu de données, paire). Les archives déjà ingérées ne sont pas
    retéléchargées ; elles ne sont inscrites au registre qu'APRÈS l'écriture de la série (reprise sûre)."""
    now = now or datetime.now(UTC)
    today = now.date()
    say = progress or (lambda _text: None)
    client = client or PublicHttpClient.futures_archives(settings.data.archive_base_url)
    registry = ArchiveRegistry(settings.data_dir / "raw" / "derivatives_archives.sqlite3")
    store = DerivativesStore(settings.data_dir)
    out = []
    for dataset in datasets or list(DATASETS):
        if dataset not in DATASETS:
            raise ValueError(f"jeu de données inconnu : {dataset} (un de {', '.join(DATASETS)})")
        for symbol in symbols or list(settings.data.symbols):
            summary = Summary(dataset, symbol)
            say(f"{dataset} {symbol} : premier mois publié")
            start = first_available(client, dataset, symbol, settings.data.history_start, today)
            if start is None:
                summary.quality = quality(dataset, store.load(dataset, symbol))
                out.append(summary)
                continue
            summary.start = start.isoformat()
            refs = plan(dataset, symbol, start, today)
            todo = [ref for ref in refs if registry.get(ref.path) is None]
            summary.skipped = len(refs) - len(todo)
            frames, ingested = [], []
            pending = todo
            while pending:
                fallback: list[Ref] = []
                with ThreadPoolExecutor(WORKERS) as pool:
                    results = pool.map(lambda r: _fetch(client, r), pending)
                    for done, (ref, sha, content, reason) in enumerate(results, 1):
                        if done % 50 == 0 or done == len(pending):
                            say(f"{dataset} {symbol} : {done}/{len(pending)} archives")
                        if reason:
                            summary.rejected.append(f"{ref.filename} ({reason})")
                            continue
                        if sha is None or content is None:
                            if ref.granularity == "monthly" and "daily" in DATASETS[dataset].granularities:
                                fallback += [d for d in daily_of(ref, today) if registry.get(d.path) is None]
                            else:
                                summary.missing.append(ref.filename)
                            continue
                        frame = parse(dataset, content, settings)
                        frames.append(frame)
                        ingested.append((ref.path, sha, len(frame)))
                pending = fallback
            merged = (store.merge(dataset, symbol, pd.concat(frames, ignore_index=True)) if frames
                      else store.load(dataset, symbol))
            for path, sha, rows in ingested:
                summary.revised += registry.record(path, sha, "auto", rows)
            summary.ingested = len(ingested)
            summary.quality = quality(dataset, merged)
            out.append(summary)
    return out
