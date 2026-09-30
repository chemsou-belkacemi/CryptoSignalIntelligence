"""Orchestration du téléchargement : archives vérifiées, puis REST récent, puis qualité."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd

from ..config import Settings
from . import archives, rest
from .http import NotFound, PublicHttpClient
from .quality import QualityReport, assess, clean
from .schema import interval, normalize
from .store import ArchiveRegistry, CandleStore, merge

logger = logging.getLogger("csi.download")
# Fusions et écritures de séries : une à la fois (mémoire), le réseau restant parallèle.
_HEAVY_SECTION = threading.Lock()


@dataclass
class DownloadSummary:
    symbol: str
    timeframe: str
    archives_ingested: int = 0
    archives_skipped: int = 0
    archives_revised: int = 0
    archives_missing: list[str] = field(default_factory=list)
    rest_rows: int = 0
    reconciled_changes: int = 0
    quarantined: int = 0
    quarantine_file: str | None = None
    report: QualityReport | None = None


def download(settings: Settings, symbol: str, timeframe: str, *, now: datetime | None = None,
             recheck_archives: bool = False, archive_client: PublicHttpClient | None = None,
             rest_client: PublicHttpClient | None = None, progress=None, rest_only: bool = False) -> DownloadSummary:
    """Complète l'historique. `rest_only` (surveillance continue) : aucune archive, seules les bougies
    manquantes depuis la dernière connue (moins un recouvrement contrôlé) sont demandées."""
    now = now or datetime.now(UTC)
    store = CandleStore(settings.data_dir)
    registry = ArchiveRegistry(settings.data_dir / "raw" / "archives.sqlite3")
    raw_dir = settings.data_dir / "raw" / "binance_public_data"
    rest_client = rest_client or PublicHttpClient.rest(settings.data.rest_base_url)
    summary = DownloadSummary(symbol, timeframe)
    latency = settings.data.assumed_availability_latency_seconds
    chunks = []

    queue = [] if rest_only else archives.plan(symbol, timeframe, settings.data.history_start, now.date())
    if queue:
        archive_client = archive_client or PublicHttpClient.archives(settings.data.archive_base_url)
    while queue:
        ref = queue.pop(0)
        if progress:
            progress(ref.filename)
        if not recheck_archives and registry.get(ref.path) is not None:
            summary.archives_skipped += 1
            continue
        assert archive_client is not None
        try:
            fetched = archives.fetch_archive(archive_client, ref, raw_dir)
        except NotFound:
            if ref.granularity == "monthly":
                queue = archives.daily_refs_for_month(ref, now.date()) + queue
            else:
                summary.archives_missing.append(ref.filename)
            continue
        frame = normalize(fetched.raw, unit=ref.unit, symbol=symbol, timeframe=timeframe,
                          source=archives.SOURCE, source_version=f"{ref.filename}#sha256={fetched.sha256[:16]}",
                          now=now, latency_seconds=latency)
        chunks.append(frame)
        summary.archives_revised += registry.record(ref.path, fetched.sha256, ref.unit, len(frame))
        summary.archives_ingested += 1

    rejected = []

    def accept(frame: pd.DataFrame) -> pd.DataFrame:
        good, bad = clean(frame, timeframe)
        # Bougie en cours : attendue côté REST, simplement ignorée (pas une anomalie).
        rejected.append(bad[bad["reason"] != "OPEN_CANDLE"])
        return good

    step = interval(timeframe)

    def rest_start(last_open: pd.Timestamp | None) -> pd.Timestamp:
        """REST : depuis la dernière bougie connue moins un recouvrement contrôlé."""
        if last_open is None:
            return pd.Timestamp(settings.data.history_start, tz="UTC")
        return last_open - settings.data.reconcile_overlap_bars * step

    raw = None
    if not chunks:
        # Cas courant (surveillance) : la date de la dernière bougie suffit, lue sans charger la série ;
        # l'appel réseau se fait hors du verrou, donc en parallèle avec les autres séries.
        raw = rest.fetch_klines(rest_client, symbol, timeframe,
                                int(rest_start(store.last_open_time(symbol, timeframe)).timestamp() * 1000))
    # Fusion et écriture : une série à la fois dans le processus (chaque série complète pèse ~50 Mo en
    # mémoire et une fusion en fait plusieurs copies ; en parallèle, le pic dépassait 1 Go).
    if not chunks:
        # Surveillance : seules les bougies reçues et le chevauchement passent par pandas.
        incoming = pd.DataFrame(columns=[])
        if raw is not None and not raw.empty:
            incoming = accept(normalize(raw, unit=rest.UNIT, symbol=symbol, timeframe=timeframe, source=rest.SOURCE,
                                        source_version="api/v3/klines", now=now, latency_seconds=latency))
        with _HEAVY_SECTION:
            summary.rest_rows = len(incoming)
            if not incoming.empty:
                _, summary.reconciled_changes = store.upsert_tail(incoming, symbol, timeframe)
            bad = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()
            summary.quarantined = len(bad)
            quarantine_path = store.quarantine(bad, symbol, timeframe)
            summary.quarantine_file = str(quarantine_path) if quarantine_path else None
            if not rest_only:     # rapport complet demandé (download sans archive nouvelle)
                summary.report = assess(store.load(symbol, timeframe), symbol, timeframe, now)
        return summary
    with _HEAVY_SECTION:
        merged = store.load(symbol, timeframe)
        if chunks:
            merged, _ = merge(merged, accept(pd.concat(chunks, ignore_index=True)))
            last = None if merged.empty else merged["open_time"].max()
            raw = rest.fetch_klines(rest_client, symbol, timeframe, int(rest_start(last).timestamp() * 1000))
        if raw is not None and not raw.empty:
            incoming = accept(normalize(raw, unit=rest.UNIT, symbol=symbol, timeframe=timeframe, source=rest.SOURCE,
                                        source_version="api/v3/klines", now=now, latency_seconds=latency))
            summary.rest_rows = len(incoming)
            merged, summary.reconciled_changes = merge(merged, incoming)

        bad = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()
        summary.quarantined = len(bad)
        quarantine_path = store.quarantine(bad, symbol, timeframe)
        summary.quarantine_file = str(quarantine_path) if quarantine_path else None
        store.save(merged, symbol, timeframe)
        # Surveillance : pas de rapport de qualité complet (5 ans) à chaque cycle ; les bougies
        # reçues sont nettoyées et mises en quarantaine ci-dessus, et un trou récent bloque la
        # décision (DATA_GAP). Le rapport complet reste produit par `download` et `data-quality`.
        summary.report = None if rest_only else assess(merged, symbol, timeframe, now)
    return summary


def history_start_for(settings: Settings) -> date:
    return settings.data.history_start
