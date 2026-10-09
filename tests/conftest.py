"""Fixtures SYNTHÉTIQUES : elles testent le code, pas une performance de marché."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.schema import RAW_COLUMNS, normalize

PROJECT = Path(__file__).resolve().parents[1]
STEP_MS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000}


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


def pytest_addoption(parser):
    parser.addoption("--lents", action="store_true", default=False,
                     help="lance aussi les tests marqués « slow » (contrôles longs, par exemple sous l'hypothèse nulle)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--lents"):
        return
    skip = pytest.mark.skip(reason="test lent : lancer avec --lents")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def fixed_halal_screening(monkeypatch):
    """Le mécanisme d'admission est testé sur un fichier d'avis FIGÉ (tests/data), pas sur le relevé courant."""
    from crypto_signal_intelligence.external import admission
    monkeypatch.setattr(admission, "screening_path", lambda settings: PROJECT / "tests" / "data" / "halal_screening.toml")


@pytest.fixture(autouse=True)
def no_spreads_network(monkeypatch):
    """Le relevé F0_ECARTS (forward/spreads.py) interroge des bourses à chaque passage des tests en direct : coupé
    ici pour qu'aucun test ne touche au réseau. `tests/test_spreads.py` le teste avec des sources factices."""
    from crypto_signal_intelligence.forward import runner
    monkeypatch.setattr(runner, "_record_spreads", lambda settings, *, now: None)
    # Lecture OCR des signaux en image : modèles lourds, testée à part (tests/test_image_queue.py, test_chart_ocr.py).
    monkeypatch.setattr(runner, "_read_images", lambda settings, *, now: None)
    # Données de contexte (docs/CONTEXTE.md) : sources publiques, testées à part avec des clients factices
    # (tests/test_context.py).
    from crypto_signal_intelligence.context import collect
    monkeypatch.setattr(collect, "record_day", lambda settings, *, now, clients=None: None)
    # Relevé de liquidité (forward/liquidity_log.py) : fil qui interroge Binance, lancé par la surveillance ; testé
    # à part avec des clients factices (tests/test_liquidity_log.py).
    from crypto_signal_intelligence.forward import liquidity_log
    monkeypatch.setattr(liquidity_log, "start_background", lambda settings, *, clock: False)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CSI_ROOT", str(tmp_path))
    monkeypatch.setenv("CSI_CONFIG_FILE", str(PROJECT / "config" / "default.toml"))
    from crypto_signal_intelligence.config import load_settings
    return load_settings()
