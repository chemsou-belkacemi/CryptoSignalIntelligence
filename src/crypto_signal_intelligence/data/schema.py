"""Schéma canonique des chandeliers et normalisation depuis les formats bruts Binance."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..config import TIMEFRAMES
from .timeunits import to_utc

RAW_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
               "count", "taker_buy_base", "taker_buy_quote", "ignore"]
CANONICAL_COLUMNS = [
    "exchange", "market_type", "symbol", "timeframe", "open_time", "close_time", "available_at",
    "ingested_at", "open", "high", "low", "close", "base_volume", "quote_volume", "number_of_trades",
    "taker_buy_base_volume", "taker_buy_quote_volume", "is_closed", "source", "source_version",
]
PRICE_COLUMNS = ["open", "high", "low", "close"]
VOLUME_COLUMNS = ["base_volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"]


def interval(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(seconds=TIMEFRAMES[timeframe])


def normalize(raw: pd.DataFrame, *, unit: str, symbol: str, timeframe: str, source: str,
              source_version: str, now: datetime, latency_seconds: float) -> pd.DataFrame:
    """Brut Binance (12 colonnes) → canonique. `now` = heure d'ingestion réelle.

    available_at est une HYPOTHÈSE pour l'historique : fin de bougie + latence
    configurée. L'heure de téléchargement n'est jamais utilisée comme disponibilité.
    """
    step = interval(timeframe)
    open_time = to_utc(raw["open_time"], unit)
    close_raw = to_utc(raw["close_time"], unit)
    frame = pd.DataFrame({
        "exchange": "BINANCE",
        "market_type": "SPOT",
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "close_time": close_raw,
        "available_at": open_time + step + pd.Timedelta(seconds=latency_seconds),
        "ingested_at": pd.Timestamp(now).as_unit("ns"),
        "open": pd.to_numeric(raw["open"], errors="coerce").to_numpy(float),
        "high": pd.to_numeric(raw["high"], errors="coerce").to_numpy(float),
        "low": pd.to_numeric(raw["low"], errors="coerce").to_numpy(float),
        "close": pd.to_numeric(raw["close"], errors="coerce").to_numpy(float),
        "base_volume": pd.to_numeric(raw["volume"], errors="coerce").to_numpy(float),
        "quote_volume": pd.to_numeric(raw["quote_volume"], errors="coerce").to_numpy(float),
        "number_of_trades": pd.to_numeric(raw["count"], errors="coerce").to_numpy(float),
        "taker_buy_base_volume": pd.to_numeric(raw["taker_buy_base"], errors="coerce").to_numpy(float),
        "taker_buy_quote_volume": pd.to_numeric(raw["taker_buy_quote"], errors="coerce").to_numpy(float),
        "is_closed": (open_time + step) <= pd.Timestamp(now),
        "source": source,
        "source_version": source_version,
    })
    return frame[CANONICAL_COLUMNS].reset_index(drop=True)


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in CANONICAL_COLUMNS})
