"""Analyse de la dernière bougie clôturée et publication éventuelle (shadow au lot 1).

Même chemin de décision que le backtest : contexte → vetos → stratégie →
niveaux → vetos → signal. Aucune exécution d'ordre. `publish=False` (tableau de bord) : même
chemin jusqu'aux niveaux et à leurs vetos, puis arrêt AVANT toute publication (rien n'est écrit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..backtest.exits import exit_policy
from ..config import Settings
from ..data.schema import interval
from ..domain.enums import Action, IntegrationStatus, NoTradeReason
from ..domain.market import MarketContext, StrategyResult
from ..features.context import iter_contexts
from ..features.loader import decision_frame, load_inputs
from ..levels.engine import LevelError, TradeLevels, compute_levels
from ..live.backup import publication_suspended
from ..strategies import registry
from ..strategies.base import Strategy
from ..validation import gates
from .outbox import SignalRegistry
from .schema import Signal, idempotency_key, new_signal_id


@dataclass
class AnalysisOutcome:
    symbol: str
    strategy: str
    decision_time: datetime | None
    action: str
    reason_code: str | None
    details: tuple[str, ...]
    signal_id: str | None = None
    signal_path: str | None = None
    publication_status: str | None = None
    integration_status: str = IntegrationStatus.INTEGRATION_UNVERIFIED.value
    levels: dict | None = None          # entrée, stop, objectifs et RR (BUY seulement)


def build_signal(settings: Settings, *, symbol: str, strategy: Strategy, context: MarketContext,
                 decision: StrategyResult, levels: TradeLevels, created: datetime) -> Signal:
    """Signal V3 d'un BUY validé. Deux expirations distinctes (docs/SIGNAL_FORMAT.md) :

    - ENTRY_EXPIRES_AT : fin de la fenêtre d'entrée de la stratégie (même règle que le backtest) ;
    - EXPIRES_AT : fin d'acceptation du message (création + `message_ttl_minutes`, plafonnée).
    Lève ValueError si la fenêtre d'entrée est déjà close.
    """
    assert decision.entry_intent is not None and decision.exit_policy_id is not None
    setup_interval = interval(settings.data.setup_timeframe)
    created = created.replace(microsecond=0)
    entry_expires_at = (context.available_at + decision.entry_intent.expires_after_bars * setup_interval
                        ).replace(microsecond=0)
    if created >= entry_expires_at:
        raise ValueError("la fenêtre d'entrée est déjà close")
    policy = exit_policy(decision.exit_policy_id)
    max_hold_minutes = None
    if policy.time_exit:
        max_hold_minutes = int(settings.simulation.max_hold_bars * setup_interval.total_seconds() // 60)
    decision_at = context.decision_time.replace(microsecond=0)
    return Signal(
        signal_id=new_signal_id(created),
        idempotency_key=idempotency_key(market_type="SPOT", symbol=symbol, strategy=strategy.strategy_id,
                                        strategy_version=strategy.version, setup_time=decision.setup_time,
                                        exit_policy_id=policy.policy_id),
        data_as_of=decision_at, decision_at=decision_at, created_at=created, valid_from=created,
        expires_at=min(created + timedelta(minutes=settings.publication.message_ttl_minutes), entry_expires_at),
        entry_expires_at=entry_expires_at,
        market_data_source=settings.project.market_data_source,
        environment=settings.project.intended_execution_environment, symbol=symbol,
        strategy=strategy.strategy_id, strategy_version=strategy.version,
        timeframe_setup=settings.data.setup_timeframe,  # type: ignore[arg-type]  # validé par pydantic
        entry_mode=decision.entry_intent.mode, entry_count=1, entry_1=levels.entry, entry_weights=(Decimal(1),),
        stop_loss=levels.stop, tp_count=len(levels.targets), tp_1=levels.targets[0],
        tp_2=_nth(levels.targets, 1), tp_3=_nth(levels.targets, 2), tp_4=_nth(levels.targets, 3),
        tp_weights=levels.target_weights, exit_policy_id=policy.policy_id, exit_policy_hash=policy.policy_hash(),
        max_hold_minutes=max_hold_minutes, rr_reference="ENTRY_1", rr_tp1_gross=levels.rr_gross[0],
        rr_tp2_gross=_nth(levels.rr_gross, 1), rr_tp3_gross=_nth(levels.rr_gross, 2),
        rr_tp4_gross=_nth(levels.rr_gross, 3),
        news_status="OBSERVE" if settings.news.mode == "observe" else "OFF",   # observe : aucune influence
        trend_regime=context.regime.trend, volatility_regime=context.regime.volatility,
        max_entry_deviation_bps=levels.published_deviation_bps(settings.publication.entry_tolerance_bps),
        validation_status=strategy.status.value,  # type: ignore[arg-type]
        integration_status=IntegrationStatus.INTEGRATION_UNVERIFIED,
        analysis=(("REASONS", " | ".join(decision.reasons)),
                  ("NET_RR_TP1_CENTRAL", f"{levels.net_rr(settings.costs['central']):.3f}"),
                  ("LIQUIDITY_REGIME", context.regime.liquidity.value),
                  ("PORTFOLIO_STATE", "PORTFOLIO_STATE_UNKNOWN"),
                  ("NOTE", "Signal de recherche ; aucune performance revendiquée")),
    )


def _nth(values, index: int):
    return values[index] if len(values) > index else None


def _levels(levels: TradeLevels, settings: Settings) -> dict:
    return {"entry": str(levels.entry), "stop": str(levels.stop), "targets": [str(t) for t in levels.targets],
            "rr_gross": [str(r) for r in levels.rr_gross],
            "net_rr_tp1_central": round(float(levels.net_rr(settings.costs["central"])), 3)}


def analyze(settings: Settings, symbol: str, strategy_id: str, *, now: datetime,
            inputs: dict | None = None, expected_decision_time: datetime | None = None,
            publish: bool = True) -> AnalysisOutcome:
    """Analyse la dernière bougie clôturée. `inputs` : données déjà chargées (partagées entre stratégies) ;
    `expected_decision_time` : clôture attendue (surveillance continue) — si elle manque encore, NO_TRADE ;
    `publish=False` : simulation (tableau de bord), rien n'est publié ni enregistré."""
    strategy = registry.build(strategy_id, settings.strategies)
    frame = decision_frame(settings, strategy, inputs if inputs is not None else load_inputs(settings, symbol))
    last = frame.tail(1).reset_index(drop=True)
    _, context = next(iter_contexts(last, symbol, settings.data.setup_timeframe, strategy.setup_keys))

    def outcome(reason: NoTradeReason, *details: str) -> AnalysisOutcome:
        return AnalysisOutcome(symbol, strategy_id, context.decision_time, Action.NO_TRADE.value, reason.value, details)

    if expected_decision_time is not None and context.decision_time < expected_decision_time:
        return outcome(NoTradeReason.STALE_DATA, f"bougie clôturée à {expected_decision_time:%H:%M} absente après attente")
    setup_interval = interval(settings.data.setup_timeframe)
    veto = gates.pre_decision(context, now=now, setup_interval=setup_interval,
                              max_staleness_bars=settings.data.max_staleness_bars, warmup_bars=strategy.warmup_bars,
                              requires_context=strategy.requires_context)
    if veto:
        return outcome(*veto)
    decision = strategy.evaluate(context)
    if decision.action != Action.BUY or decision.entry_intent is None or decision.exit_policy_id is None:
        return outcome(decision.no_trade_reason or NoTradeReason.NO_SETUP, *decision.reasons)
    try:
        levels = compute_levels(decision, settings.tick_size(symbol))
    except LevelError as exc:
        return outcome(NoTradeReason.POOR_NET_PROFILE, str(exc))
    veto = gates.post_levels(levels, settings.costs["central"], strategy.params.min_net_rr)
    if veto:
        return outcome(*veto)
    if not publish:
        return AnalysisOutcome(symbol, strategy_id, context.decision_time, Action.BUY.value, None, decision.reasons,
                               publication_status="SIMULATION", levels=_levels(levels, settings))
    veto = gates.publication(strategy.status, settings.publication.mode, exit_policy_id=decision.exit_policy_id,
                             consumer_policies=settings.publication.consumer_policies)
    if veto:
        return outcome(*veto)
    suspended = publication_suspended(settings)
    if suspended:
        return outcome(NoTradeReason.PUBLICATION_SUSPENDED, suspended)
    try:
        signal = build_signal(settings, symbol=symbol, strategy=strategy, context=context, decision=decision,
                              levels=levels, created=now)
    except ValueError as exc:
        return outcome(NoTradeReason.EXPIRED, str(exc))
    signals = SignalRegistry(settings.signals_db, settings.publication_dir())
    if any(row["idempotency_key"] != signal.idempotency_key
           for row in signals.active_for(symbol, strategy_id, signal.created_at)):
        return outcome(NoTradeReason.DUPLICATE, "un signal non expiré existe déjà pour cette paire et stratégie")
    published = signals.publish(signal, now)
    return AnalysisOutcome(symbol, strategy_id, context.decision_time, Action.BUY.value, None, decision.reasons,
                           published.signal_id, str(published.path), published.status,
                           levels=_levels(levels, settings))
