"""Historique en bougies de 1 MINUTE pour la volatilité réalisée fine (mission du 2026-10-03, phase 10.1 ; choix du
propriétaire : la minute plutôt que la seconde, 6 Go contre ~190 Go et sans bruit de microstructure à corriger).

Magasin SÉPARÉ (`<racine>/minute_history/data/…`), même pipeline et mêmes contrôles que le magasin long : archives
officielles de data.binance.vision vérifiées par SHA-256, qualité, quarantaine, REST pour la fin. Paires : les 40
paires de recherche, depuis le mois de leur cotation. Lecture des seules bougies de DEVELOPMENT par les protocoles
(coupure faite par chaque protocole, comme pour le magasin long).
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import pandas as pd

from ..config import Settings
from ..data.http import PublicHttpClient
from ..data.pipeline import download
from ..features.loader import load_candles
from .long_history import listing_date

MINUTE_DIR = "minute_history"
TIMEFRAME = "1m"
FIRST_MONTH = date(2018, 1, 1)          # les prévisions évaluées commencent en 2019 ; une année d'avance pour le HAR


def minute_settings(settings: Settings, start: date | None = None) -> Settings:
    data = settings.data if start is None else settings.data.model_copy(update={"history_start": start})
    return settings.model_copy(update={"root": settings.root / MINUTE_DIR, "data": data})


def download_minutes(settings: Settings, symbols: list[str], *, now: datetime | None = None, workers: int = 4,
                     progress: Callable[[str], None] | None = None, rest_client: PublicHttpClient | None = None,
                     downloader: Callable = download) -> list[dict]:
    """Complète le magasin minute pour chaque paire, depuis max(FIRST_MONTH, mois de cotation)."""
    now = now or datetime.now(UTC)
    say = progress or (lambda _text: None)
    rest = rest_client or PublicHttpClient.rest(settings.data.rest_base_url)

    def one(symbol: str) -> dict:
        try:
            listed = listing_date(rest, symbol)
            start = max(FIRST_MONTH, date(listed.year, listed.month, 1))
            summary = downloader(minute_settings(settings, start), symbol, TIMEFRAME, now=now, rest_client=rest)
            report = summary.report
            say(f"{symbol} : terminé")
            return {"symbol": symbol, "start": start.isoformat(), "rows": report.rows if report else 0,
                    "gaps": len(report.gaps) if report else 0, "missing_bars": report.missing_bars if report else 0,
                    "archives": summary.archives_ingested, "archives_missing": len(summary.archives_missing),
                    "quarantined": summary.quarantined}
        except Exception as exc:  # noqa: BLE001 - une paire en échec n'arrête pas les autres
            say(f"{symbol} : ÉCHEC {type(exc).__name__}")
            return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(one, symbols))


def load_minutes(settings: Settings, symbol: str) -> pd.DataFrame:
    """Bougies 1 min du magasin minute (triées). Lève MissingData si la paire n'y est pas."""
    return load_candles(minute_settings(settings), symbol, TIMEFRAME)
