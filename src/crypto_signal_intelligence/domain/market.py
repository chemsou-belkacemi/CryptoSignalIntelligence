"""Contrats du chemin de décision : contexte immuable en entrée, résultat en sortie."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from .enums import (
    Action,
    EntryMode,
    LiquidityRegime,
    NoTradeReason,
    TransitionState,
    TrendRegime,
    VolatilityRegime,
)


@dataclass(frozen=True)
class MarketRegime:
    trend: TrendRegime = TrendRegime.UNKNOWN
    volatility: VolatilityRegime = VolatilityRegime.UNKNOWN
    liquidity: LiquidityRegime = LiquidityRegime.UNKNOWN
    transition: TransitionState = TransitionState.STABLE

    @property
    def known(self) -> bool:
        return self.trend != TrendRegime.UNKNOWN and self.volatility != VolatilityRegime.UNKNOWN


def frozen_mapping(values: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class MarketContext:
    """Tout ce qu'une stratégie a le droit de voir à l'instant de décision.

    `decision_time` = clôture de la bougie de setup ; `available_at` = moment où
    le système peut en disposer. `setup` ne contient que des valeurs calculées
    sur des bougies clôturées <= decision_time ; `context` et `btc` proviennent
    de bougies 1h dont available_at <= available_at de la décision.
    """
    symbol: str
    setup_timeframe: str
    decision_time: datetime
    available_at: datetime
    setup: Mapping[str, float]
    context: Mapping[str, float]
    btc: Mapping[str, float]
    regime: MarketRegime
    bars_available: int
    data_gap_recent: bool
    context_available_at: datetime | None


@dataclass(frozen=True)
class EntryIntent:
    mode: EntryMode
    reference_price: float
    max_deviation_bps: float
    expires_after_bars: int


@dataclass(frozen=True)
class StrategyResult:
    strategy_id: str
    strategy_version: int
    action: Action
    setup_time: datetime
    regime: MarketRegime
    no_trade_reason: NoTradeReason | None = None
    entry_intent: EntryIntent | None = None
    invalidation_reference: float | None = None
    exit_policy_id: str | None = None
    # Cible de référence : en multiple du risque (target_r) OU en prix (target_price), jamais les deux.
    target_r: float | None = None
    target_price: float | None = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    features_used: tuple[str, ...] = ()
    state_transition: str | None = None
    technical_score: float | None = None
    extra: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.action == Action.BUY:
            if self.entry_intent is None or self.invalidation_reference is None or not self.exit_policy_id:
                raise ValueError("BUY exige entry_intent, invalidation_reference et exit_policy_id")
            if (self.target_r is None) == (self.target_price is None):
                raise ValueError("BUY exige une cible : target_r ou target_price (un seul des deux)")
            if self.no_trade_reason is not None:
                raise ValueError("BUY ne porte pas de motif NO_TRADE")
        elif self.no_trade_reason is None:
            raise ValueError("NO_TRADE exige un code de motif")

    @classmethod
    def no_trade(cls, strategy_id: str, version: int, setup_time: datetime, regime: MarketRegime,
                 reason: NoTradeReason, *details: str) -> StrategyResult:
        return cls(strategy_id=strategy_id, strategy_version=version, action=Action.NO_TRADE,
                   setup_time=setup_time, regime=regime, no_trade_reason=reason, reasons=details)
