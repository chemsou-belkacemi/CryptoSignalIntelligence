"""Fixtures SYNTHÉTIQUES : elles testent le code, pas une performance de marché."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.schema import RAW_COLUMNS, normalize

PROJECT = Path(__file__).resolve().parents[1]
STEP_MS = {"15m": 900_000, "1h": 3_600_000}


def raw_klines(n: int, timeframe: str = "15m", start: str = "2024-01-01", seed: int = 0, unit: str = "ms",
               drift: float = 0.0, vol: float = 0.004) -> pd.DataFrame:
    """Marche aléatoire au format brut Binance (12 colonnes)."""
    rng = np.random.default_rng(seed)
    step = STEP_MS[timeframe]
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    open_ms = start_ms + step * np.arange(n, dtype="int64")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, vol * 0.5, n)) * close
    high, low = np.maximum(open_, close) + wick, np.minimum(open_, close) - wick
    volume = rng.lognormal(5, 0.4, n)
    factor = 1000 if unit == "us" else 1
    return pd.DataFrame({
        "open_time": open_ms * factor, "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "close_time": (open_ms + step - 1) * factor, "quote_volume": volume * close, "count": 100,
        "taker_buy_base": volume * 0.5, "taker_buy_quote": volume * close * 0.5, "ignore": 0,
    })[RAW_COLUMNS]


def canonical(n: int, timeframe: str = "15m", symbol: str = "ETHUSDT", **kwargs) -> pd.DataFrame:
    return normalize(raw_klines(n, timeframe, **kwargs), unit=kwargs.get("unit", "ms"), symbol=symbol,
                     timeframe=timeframe, source="SYNTHETIC_FIXTURE", source_version="test",
                     now=datetime(2030, 1, 1, tzinfo=UTC), latency_seconds=2)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CSI_ROOT", str(tmp_path))
    monkeypatch.setenv("CSI_CONFIG_FILE", str(PROJECT / "config" / "default.toml"))
    from crypto_signal_intelligence.config import load_settings
    return load_settings()
