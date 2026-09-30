"""Registre des stratégies disponibles et construction depuis la configuration."""
from __future__ import annotations

from typing import Any

from .base import Strategy
from .donchian import DonchianVolumeBreakout
from .ema_pullback import EmaPullbackContinuation
from .range_reentry import RangeReentry

STRATEGIES: dict[str, type[Strategy]] = {
    cls.strategy_id: cls for cls in (DonchianVolumeBreakout, EmaPullbackContinuation, RangeReentry)}


def build(strategy_id: str, config: dict[str, dict[str, Any]], **overrides: Any) -> Strategy:
    if strategy_id not in STRATEGIES:
        raise ValueError(f"Stratégie inconnue : {strategy_id} ; disponibles : {sorted(STRATEGIES)}")
    cls = STRATEGIES[strategy_id]
    if strategy_id not in config:
        raise ValueError(f"Paramètres absents : section [strategies.{strategy_id}] requise")
    return cls(cls.params_model(**(config[strategy_id] | overrides)))
