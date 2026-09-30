"""Construction des MarketContext immuables à partir du tableau de décisions."""
from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd

from ..domain.enums import LiquidityRegime, TransitionState, TrendRegime, VolatilityRegime
from ..domain.market import MAX_CONTEXT_AGE, MarketContext, MarketRegime, frozen_mapping

BASE_SETUP_KEYS = ("open", "high", "low", "close", "volume", "atr14")
CTX_NUMERIC = ("close", "ema20", "ema50", "ema50_slope", "atr_pct", "quote_volume_24h", "ret_24h")


def _enum(enum_type, value, default):
    return enum_type(value) if isinstance(value, str) and value in enum_type.__members__ else default


def regime_from(trend, volatility, liquidity, transition) -> MarketRegime:
    return MarketRegime(
        trend=_enum(TrendRegime, trend, TrendRegime.UNKNOWN),
        volatility=_enum(VolatilityRegime, volatility, VolatilityRegime.UNKNOWN),
        liquidity=_enum(LiquidityRegime, liquidity, LiquidityRegime.UNKNOWN),
        transition=_enum(TransitionState, transition, TransitionState.STABLE),
    )


def iter_contexts(frame: pd.DataFrame, symbol: str, setup_timeframe: str,
                  setup_keys: Sequence[str]) -> Iterator[tuple[int, MarketContext]]:
    """Un contexte par bougie de setup, dans l'ordre chronologique.

    Contexte BTC plus ancien que MAX_CONTEXT_AGE (données BTC arrêtées) : valeurs NaN, donc
    « indisponible » pour la stratégie, jamais une vieille valeur prise pour actuelle.
    """
    keys = tuple(dict.fromkeys((*BASE_SETUP_KEYS, *setup_keys)))
    columns = {
        "setup": [frame[k].to_numpy(float) for k in keys],
        "ctx": [frame[f"ctx_{k}"].to_numpy(float) for k in CTX_NUMERIC],
        "btc": [frame[f"btc_{k}"].to_numpy(float) for k in CTX_NUMERIC],
    }
    if "btc_available_at" in frame:
        stale = (frame["btc_available_at"].isna()
                 | ((frame["available_at"] - frame["btc_available_at"]) > MAX_CONTEXT_AGE)).to_numpy()
        columns["btc"] = [np.where(stale, np.nan, col) for col in columns["btc"]]
    regimes = frame[["ctx_trend", "ctx_volatility", "ctx_liquidity", "ctx_transition"]].to_numpy(object)
    decision = frame["decision_time"].tolist()
    available = frame["available_at"].tolist()
    ctx_available = frame["ctx_available_at"].tolist()
    bars = frame["bars_available"].to_numpy(int)
    gaps = frame["data_gap_recent"].to_numpy(bool)
    cache: dict[tuple, MarketRegime] = {}
    for i in range(len(frame)):
        key = tuple(regimes[i])
        regime = cache.get(key) or cache.setdefault(key, regime_from(*key))
        ctx_time = ctx_available[i]
        yield i, MarketContext(
            symbol=symbol, setup_timeframe=setup_timeframe,
            decision_time=decision[i].to_pydatetime(), available_at=available[i].to_pydatetime(),
            setup=frozen_mapping({k: float(col[i]) for k, col in zip(keys, columns["setup"], strict=True)}),
            context=frozen_mapping({k: float(col[i]) for k, col in zip(CTX_NUMERIC, columns["ctx"], strict=True)}),
            btc=frozen_mapping({k: float(col[i]) for k, col in zip(CTX_NUMERIC, columns["btc"], strict=True)}),
            regime=regime, bars_available=int(bars[i]), data_gap_recent=bool(gaps[i]),
            context_available_at=None if ctx_time is None or pd.isna(ctx_time) else ctx_time.to_pydatetime(),
        )
