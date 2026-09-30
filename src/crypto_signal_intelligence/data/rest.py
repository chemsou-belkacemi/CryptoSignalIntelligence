"""Complément REST public /api/v3/klines (données récentes non encore archivées).

Unité : millisecondes. Taille de page propre à CET endpoint : 1000 bougies.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from .http import PublicHttpClient
from .schema import RAW_COLUMNS

SOURCE = "BINANCE_REST_KLINES"
UNIT = "ms"
KLINES_PAGE_LIMIT = 1000


def fetch_klines(client: PublicHttpClient, symbol: str, timeframe: str, start_ms: int,
                 end_ms: int | None = None, max_pages: int = 500) -> pd.DataFrame:
    rows: list[list] = []
    cursor = start_ms
    for _ in range(max_pages):
        params = {"symbol": symbol, "interval": timeframe, "startTime": cursor, "limit": KLINES_PAGE_LIMIT}
        if end_ms is not None:
            params["endTime"] = end_ms
        page = client.get_json("/api/v3/klines", params)
        if not isinstance(page, list):
            raise ValueError("Réponse klines inattendue")
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < KLINES_PAGE_LIMIT:
            break
    frame = pd.DataFrame(rows, columns=RAW_COLUMNS)
    if not frame.empty:
        frame["open_time"] = frame["open_time"].astype("int64")
        frame["close_time"] = frame["close_time"].astype("int64")
    return frame


def fetch_tick_size(client: PublicHttpClient, symbol: str) -> Decimal:
    data = client.get_json("/api/v3/exchangeInfo", {"symbol": symbol})
    info = next(s for s in data["symbols"] if s["symbol"] == symbol)
    tick = next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER")
    return Decimal(tick).normalize()
