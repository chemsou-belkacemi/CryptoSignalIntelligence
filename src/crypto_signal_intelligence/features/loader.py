"""Chargement des chandeliers stockés et construction du tableau de décisions d'une paire."""
from __future__ import annotations

import pandas as pd

from ..config import Settings
from ..data.schema import interval
from ..data.store import CandleStore, content_hash
from ..strategies.base import Strategy
from .builder import build_decision_frame

MARKET_CONTEXT_SYMBOL = "BTCUSDT"


class MissingData(RuntimeError):
    pass


def load_candles(settings: Settings, symbol: str, timeframe: str, tail: int | None = None) -> pd.DataFrame:
    store = CandleStore(settings.data_dir)
    if tail is None:
        frame = store.load(symbol, timeframe)
    else:
        # Seulement les dernières bougies (+ marge pour les trous) : pas 5 ans convertis en pandas.
        last = store.last_open_time(symbol, timeframe)
        frame = (store.load(symbol, timeframe) if last is None
                 else store.load_since(symbol, timeframe, last - int(tail * 1.25 + 10) * interval(timeframe)))
    if frame.empty:
        raise MissingData(f"Aucune donnée locale pour {symbol} {timeframe} : lancer `download` d'abord.")
    frame = frame.sort_values("open_time")
    if tail is not None:
        frame = frame.tail(tail)
    return frame.reset_index(drop=True)


def load_inputs(settings: Settings, symbol: str, *, setup_tail: int | None = None,
                context_tail: int | None = None) -> dict[str, pd.DataFrame]:
    """Historique complet (recherche) ou seulement les dernières bougies utiles (surveillance continue)."""
    return {
        "setup": load_candles(settings, symbol, settings.data.setup_timeframe, setup_tail),
        "context": load_candles(settings, symbol, settings.data.context_timeframe, context_tail),
        "btc": load_candles(settings, MARKET_CONTEXT_SYMBOL, settings.data.context_timeframe, context_tail),
    }


def decision_frame(settings: Settings, strategy: Strategy, inputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return build_decision_frame(
        inputs["setup"], inputs["context"], inputs["btc"], setup_timeframe=settings.data.setup_timeframe,
        params=strategy.feature_params(settings.data.gap_block_bars), regimes=settings.regimes)


def data_hashes(inputs: dict[str, pd.DataFrame]) -> dict[str, str]:
    return {name: content_hash(frame) for name, frame in inputs.items()}
