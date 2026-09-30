"""Énumérations partagées par tout le moteur (valeurs stables : elles vont dans les signaux)."""
from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    BUY = "BUY"
    NO_TRADE = "NO_TRADE"


class NoTradeReason(StrEnum):
    NO_SETUP = "NO_SETUP"
    STALE_DATA = "STALE_DATA"
    DATA_GAP = "DATA_GAP"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    UNKNOWN_REGIME = "UNKNOWN_REGIME"
    SPREAD_TOO_HIGH = "SPREAD_TOO_HIGH"
    POOR_NET_PROFILE = "POOR_NET_PROFILE"
    UNVALIDATED_STRATEGY = "UNVALIDATED_STRATEGY"
    DUPLICATE = "DUPLICATE"
    EXPIRED = "EXPIRED"
    PRICE_MOVED = "PRICE_MOVED"
    CORRELATED_CANDIDATE = "CORRELATED_CANDIDATE"
    MODEL_STALE = "MODEL_STALE"
    REQUIRED_CONTEXT_UNAVAILABLE = "REQUIRED_CONTEXT_UNAVAILABLE"
    EXIT_POLICY_MISMATCH = "EXIT_POLICY_MISMATCH"   # politique non exécutée à l'identique par le consommateur
    PUBLICATION_SUSPENDED = "PUBLICATION_SUSPENDED"  # après restauration, jusqu'à réconciliation


class TrendRegime(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class VolatilityRegime(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class LiquidityRegime(StrEnum):
    ACCEPTABLE = "ACCEPTABLE"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class TransitionState(StrEnum):
    STABLE = "STABLE"
    CHANGING = "CHANGING"


class StrategyStatus(StrEnum):
    RESEARCH = "RESEARCH"
    VALIDATED_OOS = "VALIDATED_OOS"
    SHADOW = "SHADOW"
    DEMO_ELIGIBLE = "DEMO_ELIGIBLE"
    DISABLED = "DISABLED"


class ValidationVerdict(StrEnum):
    VALIDATED_OOS = "VALIDATED_OOS"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"
    NOT_EVALUATED = "NOT_EVALUATED"


class EntryMode(StrEnum):
    LIMIT = "LIMIT"


class IntegrationStatus(StrEnum):
    INTEGRATION_UNVERIFIED = "INTEGRATION_UNVERIFIED"
    INTEGRATION_VERIFIED = "INTEGRATION_VERIFIED"
