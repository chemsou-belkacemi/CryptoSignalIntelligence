"""Vetos déterministes, partagés par le backtest et l'analyse en direct.

Un veto ne peut être levé par aucun autre composant (ML ou LLM compris).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config import CostScenario
from ..domain.enums import NoTradeReason, StrategyStatus
from ..domain.market import MAX_CONTEXT_AGE, MarketContext
from ..levels.engine import TradeLevels


def pre_decision(context: MarketContext, *, now: datetime, setup_interval: timedelta, max_staleness_bars: int,
                 warmup_bars: int, requires_context: bool) -> tuple[NoTradeReason, str] | None:
    """Contrôles de données AVANT d'interroger la stratégie."""
    age = now - context.available_at
    if age > max_staleness_bars * setup_interval:
        return NoTradeReason.STALE_DATA, f"dernière bougie disponible depuis {age}"
    if context.bars_available < warmup_bars:
        return NoTradeReason.INSUFFICIENT_HISTORY, f"{context.bars_available} bougies < {warmup_bars}"
    if context.data_gap_recent:
        return NoTradeReason.DATA_GAP, "trou de données dans la fenêtre des indicateurs"
    if requires_context:
        if context.context_available_at is None:
            return NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE, "aucune bougie de contexte disponible"
        if context.available_at - context.context_available_at > MAX_CONTEXT_AGE:
            return NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE, "contexte 1h trop ancien"
    return None


def post_levels(levels: TradeLevels, costs: CostScenario, min_net_rr: float) -> tuple[NoTradeReason, str] | None:
    net = levels.net_rr(costs)
    if net < min_net_rr:
        return NoTradeReason.POOR_NET_PROFILE, f"RR net {net:.2f} < {min_net_rr} (coûts centraux)"
    return None


def publication(status: StrategyStatus, mode: str, *, exit_policy_id: str | None = None,
                consumer_policies: tuple[str, ...] | list[str] = ()) -> tuple[NoTradeReason, str] | None:
    """Hors shadow, seule une stratégie DEMO_ELIGIBLE peut publier, et seulement avec une politique
    de sortie que le consommateur exécute réellement (même identifiant, même empreinte)."""
    if status == StrategyStatus.DISABLED:
        return NoTradeReason.UNVALIDATED_STRATEGY, "stratégie désactivée"
    if mode != "shadow" and status != StrategyStatus.DEMO_ELIGIBLE:
        return NoTradeReason.UNVALIDATED_STRATEGY, f"statut {status.value} : publication shadow uniquement"
    if mode != "shadow" and exit_policy_id not in consumer_policies:
        return (NoTradeReason.EXIT_POLICY_MISMATCH,
                f"politique {exit_policy_id} non exécutée par le consommateur ({', '.join(consumer_policies) or 'aucune'})")
    return None
