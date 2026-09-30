"""Features causales et jointure multi-timeframe vers le passé.

Règle centrale : une ligne de setup (bougie 15m clôturée) ne voit une bougie de
contexte 1h que si `ctx.available_at <= setup.available_at`. À 10h17, la bougie
1h 10h–11h n'est donc jamais visible ; celle de 9h–10h l'est depuis 10h00 + latence.
Les labels et résultats futurs ne sont PAS calculés ici (voir backtest/).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import RegimeSection
from ..data.quality import with_gap_column
from ..data.schema import interval
from ..regimes.classifier import classify
from . import indicators as ind

CONTEXT_COLUMNS = ["close", "ema20", "ema50", "ema50_slope", "atr_pct", "quote_volume_24h", "ret_24h",
                   "trend", "volatility", "liquidity", "transition"]
# Valeurs de la bougie précédente, utiles aux règles « première clôture de retour ».
PREVIOUS_COLUMNS = ("close", "ema20", "atr14", "bb_mid", "bb_sigma")


@dataclass(frozen=True)
class SetupFeatureParams:
    donchian_lookback: int = 20
    volume_window: int = 20
    gap_block_bars: int = 200
    recent_low_bars: int = 3
    bollinger_window: int = 20


def setup_features(candles: pd.DataFrame, timeframe: str, params: SetupFeatureParams) -> pd.DataFrame:
    """Features du timeframe de setup. `candles` : canonique, propre, trié, bougies clôturées."""
    df = with_gap_column(candles.reset_index(drop=True), timeframe)
    close, high, low = df["close"], df["high"], df["low"]
    volume = df["base_volume"]
    out = pd.DataFrame({
        "open_time": df["open_time"],
        "decision_time": df["open_time"] + interval(timeframe),
        "available_at": df["available_at"],
        "open": df["open"], "high": high, "low": low, "close": close, "volume": volume,
    })
    out["atr14"] = ind.atr(high, low, close, 14)
    out["ema20"] = ind.ema(close, 20)
    out["ema50"] = ind.ema(close, 50)
    out["rsi14"] = ind.rsi(close, 14)
    out["donchian_high"] = ind.prior_max(high, params.donchian_lookback)
    out["donchian_low"] = ind.prior_min(low, params.donchian_lookback)
    out["volume_ref"] = ind.prior_mean(volume, params.volume_window)
    out["volume_ratio"] = volume / out["volume_ref"].replace(0, np.nan)
    out["recent_low"] = ind.recent_min(low, params.recent_low_bars)
    out["bb_mid"], out["bb_sigma"] = ind.bollinger(close, params.bollinger_window)
    for column in PREVIOUS_COLUMNS:
        out[f"prev_{column}"] = out[column].shift(1)
    log_ret = np.log(close).diff()
    out["ret_1"] = log_ret
    out["ret_4"] = np.log(close / close.shift(4))
    out["realized_vol_96"] = log_ret.rolling(96, min_periods=96).std()
    out["bars_available"] = np.arange(1, len(df) + 1)
    window = max(1, params.gap_block_bars)
    out["data_gap_recent"] = df["gap_before"].rolling(window, min_periods=1).max().fillna(0) > 0
    return out


def context_features(candles: pd.DataFrame, cfg: RegimeSection) -> pd.DataFrame:
    """Features + régimes du timeframe de contexte (1h), indexés par disponibilité."""
    df = candles.reset_index(drop=True)
    close = df["close"]
    ema50 = ind.ema(close, 50)
    out = pd.DataFrame({
        "open_time": df["open_time"], "available_at": df["available_at"], "close": close,
        "ema20": ind.ema(close, 20), "ema50": ema50, "ema50_slope": ema50 / ema50.shift(3) - 1,
        "atr_pct": ind.atr(df["high"], df["low"], close, 14) / close,
        "quote_volume_24h": df["quote_volume"].rolling(24, min_periods=24).sum(),
        "ret_24h": close / close.shift(24) - 1,
    })
    return pd.concat([out, classify(out, cfg)], axis=1)


def join_past(setup: pd.DataFrame, context: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Jointure vers le passé sur available_at (jamais vers une bougie future)."""
    right = context[["available_at", "open_time", *CONTEXT_COLUMNS]].rename(
        columns={c: f"{prefix}{c}" for c in ["open_time", *CONTEXT_COLUMNS]})
    right = right.assign(**{f"{prefix}available_at": right["available_at"]})
    left = setup.sort_values("available_at")
    joined = pd.merge_asof(left, right.sort_values("available_at"), on="available_at",
                           direction="backward", allow_exact_matches=True)
    return joined.sort_values("open_time").reset_index(drop=True)


def build_decision_frame(setup_candles: pd.DataFrame, context_candles: pd.DataFrame,
                         btc_context_candles: pd.DataFrame, *, setup_timeframe: str,
                         params: SetupFeatureParams, regimes: RegimeSection) -> pd.DataFrame:
    frame = setup_features(setup_candles, setup_timeframe, params)
    frame = join_past(frame, context_features(context_candles, regimes), "ctx_")
    return join_past(frame, context_features(btc_context_candles, regimes), "btc_")
