"""Historique LONG : bougies 1 h depuis la cotation de chaque paire, pour la recherche à basse fréquence.

Demande du propriétaire (2026-10-01) : « télécharger toutes les données possibles, par exemple du Bitcoin du
tout début ». Ces bougies vont dans un magasin SÉPARÉ (`<racine>/long_history/data/…`), au même format et avec
les mêmes contrôles que le magasin courant (archives officielles vérifiées par SHA-256, qualité, quarantaine,
REST pour la fin). Les protocoles déjà exécutés gardent leur magasin, qui commence le 2021-01-01 : ils restent
reproductibles à l'identique.

Biais à déclarer par tout protocole qui s'en sert : les paires sont celles cotées AUJOURD'HUI ; une paire
retirée de la cote depuis n'y figure pas (biais des survivantes). La date de cotation de chaque paire permet
au moins un univers « à la date » (une paire n'existe pas avant sa première bougie).
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

LONG_DIR = "long_history"
TIMEFRAME = "1h"


def long_settings(settings: Settings, start: date | None = None) -> Settings:
    """Réglages du magasin long : même configuration, racine `<racine>/long_history`, début `start`."""
    data = settings.data if start is None else settings.data.model_copy(update={"history_start": start})
    return settings.model_copy(update={"root": settings.root / LONG_DIR, "data": data})


def listing_date(client: PublicHttpClient, symbol: str) -> date:
    """Jour de la première bougie de la paire sur Binance Spot (API publique, aucune clé)."""
    rows = client.get_json("/api/v3/klines", {"symbol": symbol, "interval": "1d", "startTime": 0, "limit": 1})
    if not rows:
        raise ValueError(f"{symbol} : aucune bougie sur Binance Spot")
    return pd.Timestamp(int(rows[0][0]), unit="ms", tz="UTC").date()


def download_long(settings: Settings, symbols: list[str], *, now: datetime | None = None, workers: int = 6,
                  progress: Callable[[str], None] | None = None, rest_client: PublicHttpClient | None = None,
                  archive_client: PublicHttpClient | None = None, downloader: Callable = download) -> list[dict]:
    """Complète le magasin long pour chaque paire, depuis le mois de sa cotation. Une paire en échec est
    rapportée (`error`) sans arrêter les autres ; les fusions restent sérialisées par le pipeline."""
    now = now or datetime.now(UTC)
    say = progress or (lambda _text: None)
    rest = rest_client or PublicHttpClient.rest(settings.data.rest_base_url)

    def one(symbol: str) -> dict:
        try:
            listed = listing_date(rest, symbol)
            target = long_settings(settings, date(listed.year, listed.month, 1))
            summary = downloader(target, symbol, TIMEFRAME, now=now, rest_client=rest, archive_client=archive_client)
            report = summary.report
            say(f"{symbol} : terminé")
            return {"symbol": symbol, "listed": listed.isoformat(), "rows": report.rows if report else 0,
                    "first": report.first_open_time if report else None, "last": report.last_open_time if report else None,
                    "gaps": len(report.gaps) if report else 0, "missing_bars": report.missing_bars if report else 0,
                    "archives": summary.archives_ingested, "archives_missing": len(summary.archives_missing),
                    "quarantined": summary.quarantined}
        except Exception as exc:  # noqa: BLE001 - une paire en échec n'arrête pas les autres
            say(f"{symbol} : ÉCHEC {type(exc).__name__}")
            return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(one, symbols))


def load_long(settings: Settings, symbol: str) -> pd.DataFrame:
    """Bougies 1 h du magasin long (triées). Lève MissingData si la paire n'y est pas."""
    return load_candles(long_settings(settings), symbol, TIMEFRAME)
