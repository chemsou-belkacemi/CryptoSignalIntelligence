"""Bougies d'unité supérieure (4 h…) reconstruites à partir de bougies 1 h CLÔTURÉES, sans fuite.

Une bougie 4 h n'existe que si ses quatre bougies 1 h (00-03, 04-07… UTC) sont toutes présentes et
contiguës ; elle devient disponible à l'`available_at` de sa DERNIÈRE bougie 1 h. Une jointure vers le
passé sur `available_at` ne peut donc jamais montrer une bougie 4 h en cours de formation (testé par
tests/test_ml_intraday.py, y compris une mutation qui joindrait sur `open_time`).
"""
from __future__ import annotations

import pandas as pd

VOLUME_COLUMNS = ("base_volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume",
                  "number_of_trades")


def resample_complete(candles: pd.DataFrame, hours: int = 4, source_hours: int = 1) -> pd.DataFrame:
    """Agrège des bougies de `source_hours` heures en bougies de `hours` heures complètes uniquement."""
    if hours % source_hours:
        raise ValueError("l'unité cible doit être un multiple de l'unité source")
    per_bar = hours // source_hours
    if candles.empty:
        return candles.iloc[0:0][["open_time", "available_at", "open", "high", "low", "close"]].copy()
    df = candles.sort_values("open_time").reset_index(drop=True)
    bucket = df["open_time"].dt.floor(f"{hours}h")
    aggregations: dict = {"open": "first", "high": "max", "low": "min", "close": "last",
                          "available_at": "max", "count": ("open_time", "size"),
                          "first_open": ("open_time", "min"), "last_open": ("open_time", "max")}
    named = {k: (v if isinstance(v, tuple) else (k, v)) for k, v in aggregations.items()}
    for column in VOLUME_COLUMNS:
        if column in df:
            named[column] = (column, "sum")
    grouped = df.groupby(bucket).agg(**named)
    step = pd.Timedelta(hours=source_hours)
    complete = (grouped["count"] == per_bar) & (grouped["last_open"] - grouped["first_open"] == step * (per_bar - 1))
    out = grouped[complete].drop(columns=["count", "first_open", "last_open"]).reset_index(names="open_time")
    return out.reset_index(drop=True)
