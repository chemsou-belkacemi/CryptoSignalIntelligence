"""Régimes causaux sur le timeframe de contexte (1h), axes séparés.

trend      : BULL si EMA20 > EMA50, close > EMA50 et pente EMA50 (3 bougies) > 0 ;
             BEAR symétrique ; RANGE sinon ; UNKNOWN sans historique suffisant.
volatility : ATR14/close comparé aux quantiles de SA PROPRE histoire passée
             (fenêtre glissante excluant la bougie courante).
liquidity  : volume en devise de cotation sur 24 h comparé à sa médiane passée
             et à un minimum absolu.
transition : CHANGING si la tendance a changé dans les N dernières bougies.
Pas d'hystérésis en V1 : à ajouter si les changements s'avèrent trop fréquents.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import RegimeSection
from ..domain.enums import LiquidityRegime, TransitionState, TrendRegime, VolatilityRegime

TREND_CODES = {TrendRegime.UNKNOWN: 0, TrendRegime.BULL: 1, TrendRegime.BEAR: 2, TrendRegime.RANGE: 3}


def classify(ctx: pd.DataFrame, cfg: RegimeSection) -> pd.DataFrame:
    """`ctx` doit contenir close, ema20, ema50, ema50_slope, atr_pct, quote_volume_24h."""
    out = pd.DataFrame(index=ctx.index)
    known = ctx[["ema20", "ema50", "ema50_slope"]].notna().all(axis=1)
    bull = (ctx["ema20"] > ctx["ema50"]) & (ctx["close"] > ctx["ema50"]) & (ctx["ema50_slope"] > 0)
    bear = (ctx["ema20"] < ctx["ema50"]) & (ctx["close"] < ctx["ema50"]) & (ctx["ema50_slope"] < 0)
    out["trend"] = np.select([~known, bull, bear], [TrendRegime.UNKNOWN.value, TrendRegime.BULL.value,
                                                    TrendRegime.BEAR.value], TrendRegime.RANGE.value)

    past_vol = ctx["atr_pct"].shift(1).rolling(cfg.volatility_window_bars, min_periods=cfg.volatility_min_bars)
    low_q, high_q = past_vol.quantile(cfg.volatility_low_quantile), past_vol.quantile(cfg.volatility_high_quantile)
    vol_known = low_q.notna() & ctx["atr_pct"].notna()
    out["volatility"] = np.select(
        [~vol_known, ctx["atr_pct"] < low_q, ctx["atr_pct"] > high_q],
        [VolatilityRegime.UNKNOWN.value, VolatilityRegime.LOW.value, VolatilityRegime.HIGH.value],
        VolatilityRegime.NORMAL.value)

    past_liq = ctx["quote_volume_24h"].shift(1).rolling(cfg.liquidity_window_bars,
                                                        min_periods=cfg.liquidity_min_bars).median()
    liq_known = past_liq.notna() & ctx["quote_volume_24h"].notna()
    low_liq = (ctx["quote_volume_24h"] < cfg.liquidity_low_ratio * past_liq) | (
        ctx["quote_volume_24h"] < cfg.liquidity_min_quote_volume_24h)
    out["liquidity"] = np.select([~liq_known, low_liq], [LiquidityRegime.UNKNOWN.value, LiquidityRegime.LOW.value],
                                 LiquidityRegime.ACCEPTABLE.value)

    codes = out["trend"].map({k.value: v for k, v in TREND_CODES.items()})
    window = codes.rolling(cfg.transition_lookback_bars + 1, min_periods=1)
    out["transition"] = np.where(window.max() != window.min(), TransitionState.CHANGING.value,
                                 TransitionState.STABLE.value)
    return out
