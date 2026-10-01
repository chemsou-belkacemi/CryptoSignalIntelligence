"""Simulation événementielle locale de référence (aucun ordre, aucun réseau).

Chronologie par bougie k :
1. un ordre d'entrée en attente peut être rempli pendant k (activé au plus tôt
   à la bougie qui suit la décision, + retard du scénario) ;
2. une position ouverte peut sortir pendant k (règles : backtest/exits.py) ;
3. à la clôture de k, la stratégie décide (même code que l'analyse en direct).

Entrée LIMIT acheteuse : si open <= limite, remplie à l'ouverture (+ glissement et
demi-spread, plafonnée à la limite) ; sinon remplie à la limite seulement si low < limite
STRICTEMENT (un simple contact ne garantit rien) ; expirée sinon.
Sorties : politique de sortie du signal (exits.py), ou politique imposée par les règles
(comparaison profil théorique / profil consommateur). Les cas ambigus sont comptés et une
borne optimiste est conservée. Fin des données : position CENSORED, valorisée au dernier
close, exclue des statistiques de trades clos.
R : PnL net rapporté au risque PRÉVU (limite − stop), comme le signal publié, le taux de base
et le retour d'exécution : un remplissage sous la limite ne gonfle pas le R.
Chaque signal est simulé indépendamment ; un seul setup actif par paire et par stratégie.
Ce n'est PAS une simulation de portefeuille.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from ..config import CostScenario
from ..domain.enums import Action, NoTradeReason
from ..features.context import iter_contexts
from ..levels.engine import LevelError, TradeLevels, compute_levels
from ..strategies.base import Strategy
from ..validation import gates
from .exits import OpenPosition, exit_policy, gross_return, pnl_per_unit


@dataclass(frozen=True)
class SimulationRules:
    costs: CostScenario
    max_hold_bars: int
    min_net_rr: float
    tick_size: Decimal
    setup_interval: timedelta
    max_staleness_bars: int = 2
    requires_context: bool = True
    exit_policy_id: str | None = None   # None : politique déclarée par chaque signal


@dataclass
class Trade:
    symbol: str
    strategy: str
    setup_time: datetime
    trend_regime: str
    volatility_regime: str
    entry_limit: float
    stop: float
    target: float                          # TP de référence (premier objectif)
    exit_policy: str = "FIXED_SL_ONE_TP_V1"
    targets: tuple[float, ...] = ()
    target_weights: tuple[float, ...] = ()
    entry_status: str = "PENDING"         # FILLED_OPEN | FILLED_TOUCH | EXPIRED
    entry_time: datetime | None = None
    entry_price: float | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None        # TP | SL | SL_GAP | TIMEOUT | CENSORED (dernière sortie)
    fills: list[dict] = field(default_factory=list)
    ambiguous: bool = False
    optimistic_exit_reason: str | None = None
    optimistic_exit_price: float | None = None
    bars_held: int = 0
    gross_return: float | None = None
    net_return: float | None = None
    r_multiple: float | None = None
    r_multiple_optimistic: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimulationResult:
    trades: list[Trade] = field(default_factory=list)
    no_trade: Counter = field(default_factory=Counter)
    evaluated_bars: int = 0
    candidates: int = 0
    bars_in_position: int = 0


def simulate(frame: pd.DataFrame, symbol: str, strategy: Strategy, rules: SimulationRules, *,
             start: datetime | None = None, end: datetime | None = None,
             decisions_end: datetime | None = None,
             decision_filter: Callable[[datetime, TradeLevels], bool] | None = None) -> SimulationResult:
    """`frame` : tableau de décisions (features + contexte) d'UNE paire, trié.

    Décisions entre `start` et `decisions_end` (défaut : `end`) ; les prix sont lus
    jusqu'à `end`, pour qu'un trade décidé dans une fenêtre de test puisse se terminer.
    `decision_filter(decision_time, niveaux)` : filtre supplémentaire (modèle du lot 5) appliqué
    après tous les vetos, au même endroit que le ferait la surveillance ; un setup refusé est
    NO_TRADE « ML_FILTER » et laisse la paire libre pour le setup suivant.
    """
    if end is not None:
        # Aucune donnée postérieure à la période : les trades à cheval sont CENSORED.
        frame = frame[frame["decision_time"] <= pd.Timestamp(end)]
    if start is not None:
        # Avant `start`, aucune décision n'est prise, donc aucun ordre ni position :
        # ces bougies ne changent rien au résultat (les features sont déjà calculées).
        frame = frame[frame["decision_time"] >= pd.Timestamp(start)]
    frame = frame.reset_index(drop=True)
    result = SimulationResult()
    if frame.empty:
        return result
    costs = rules.costs
    fee = costs.fee_bps / 1e4
    market_cost = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    opens, highs, lows, closes = (frame[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    times = [t.to_pydatetime() for t in frame["open_time"]]
    last_decision = len(frame) - 1
    if decisions_end is not None:
        last_decision = int((frame["decision_time"] <= pd.Timestamp(decisions_end)).sum()) - 1
    forced_policy = exit_policy(rules.exit_policy_id) if rules.exit_policy_id else None
    pending: dict | None = None
    position: tuple[Trade, OpenPosition, int] | None = None   # (trade, état, bougie d'entrée)

    def finalize(k: int, censor_price: float | None = None) -> None:
        nonlocal position
        assert position is not None
        trade, state, entry_bar = position
        if censor_price is not None:
            state.censor(censor_price)
        entry, risk, exit_price = trade.entry_price, trade.entry_limit - trade.stop, state.exit_price
        assert entry is not None and exit_price is not None
        trade.exit_time, trade.exit_price, trade.exit_reason = times[k], exit_price, state.exit_reason
        trade.bars_held = k - entry_bar + 1
        trade.fills = [f.to_dict() for f in state.fills]
        trade.ambiguous = state.ambiguous
        pnl = pnl_per_unit(state.fills, entry, fee)
        trade.gross_return, trade.net_return, trade.r_multiple = gross_return(state.fills, entry), pnl / entry, pnl / risk
        if state.optimistic_fills is not None:
            trade.optimistic_exit_reason = state.optimistic_fills[-1].reason
            trade.optimistic_exit_price = state.optimistic_fills[-1].price
            trade.r_multiple_optimistic = pnl_per_unit(state.optimistic_fills, entry, fee) / risk
        else:
            trade.r_multiple_optimistic = trade.r_multiple
        trade.mae_r = (entry - min(state.low, exit_price)) / risk
        trade.mfe_r = (max(state.high, exit_price) - entry) / risk
        position = None

    def stopped_at_fill(trade: Trade, k: int) -> None:
        """Ouverture au niveau du stop ou dessous : achat au marché puis stop-market aussitôt, au marché.

        Deux traversées du spread : achat à open × (1 + coût), vente à open × (1 − coût).
        """
        entry = trade.entry_price
        assert entry is not None
        exit_price = opens[k] * (1 - market_cost)
        risk = trade.entry_limit - trade.stop
        trade.exit_time, trade.exit_price, trade.exit_reason, trade.bars_held = times[k], exit_price, "SL_GAP", 1
        trade.fills = [{"reason": "SL_GAP", "price": exit_price, "weight": 1.0, "target": None}]
        pnl = exit_price * (1 - fee) - entry * (1 + fee)
        trade.gross_return, trade.net_return, trade.r_multiple = exit_price / entry - 1, pnl / entry, pnl / risk
        trade.r_multiple_optimistic = trade.r_multiple
        trade.mae_r, trade.mfe_r = (entry - min(lows[k], exit_price)) / risk, max(highs[k] - entry, 0.0) / risk

    def step_position(k: int, *, first_bar: bool, touched: bool) -> None:
        assert position is not None
        _, state, entry_bar = position
        state.process_bar(opens[k], highs[k], lows[k], closes[k], first_bar=first_bar, touched=touched,
                          market_cost=market_cost, time_limit_reached=k - entry_bar + 1 >= rules.max_hold_bars)
        if state.closed:
            finalize(k)

    for k, context in iter_contexts(frame, symbol, _setup_tf(frame), strategy.setup_keys):
        # 1. entrée en attente
        if pending is not None and k >= pending["active_from"]:
            trade = pending["trade"]
            if k > pending["expires_after"]:
                trade.entry_status = "EXPIRED"
                result.trades.append(trade)
                pending = None
            else:
                limit = trade.entry_limit
                fill = None
                if opens[k] <= limit:
                    fill = ("FILLED_OPEN", min(opens[k] * (1 + market_cost), limit), False)
                elif lows[k] < limit:
                    fill = ("FILLED_TOUCH", limit, True)
                if fill:
                    trade.entry_status, trade.entry_price, trade.entry_time = fill[0], fill[1], times[k]
                    result.trades.append(trade)
                    pending = None
                    result.bars_in_position += 1
                    if fill[0] == "FILLED_OPEN" and opens[k] <= trade.stop:
                        stopped_at_fill(trade, k)   # ouverture sous le stop (gap) : stop-market immédiat
                        continue
                    state = OpenPosition(entry=fill[1], initial_stop=trade.stop, targets=trade.targets,
                                         weights=trade.target_weights,
                                         policy=forced_policy or exit_policy(trade.exit_policy))
                    position = (trade, state, k)
                    step_position(k, first_bar=True, touched=fill[2])
        # 2. position ouverte (hors bougie de remplissage, déjà traitée)
        elif position is not None:
            result.bars_in_position += 1
            step_position(k, first_bar=False, touched=False)
        # 3. décision à la clôture
        if k > last_decision:
            if pending is None and position is None:
                break
            continue
        result.evaluated_bars += 1
        veto = gates.pre_decision(context, now=context.available_at, setup_interval=rules.setup_interval,
                                  max_staleness_bars=rules.max_staleness_bars, warmup_bars=strategy.warmup_bars,
                                  requires_context=rules.requires_context)
        if veto:
            result.no_trade[veto[0].value] += 1
            continue
        decision = strategy.evaluate(context)
        if decision.action != Action.BUY or decision.entry_intent is None or decision.exit_policy_id is None:
            result.no_trade[(decision.no_trade_reason or NoTradeReason.NO_SETUP).value] += 1
            continue
        result.candidates += 1
        if pending is not None or position is not None:
            result.no_trade[NoTradeReason.DUPLICATE.value] += 1
            continue
        try:
            levels = compute_levels(decision, rules.tick_size)
        except LevelError:
            result.no_trade[NoTradeReason.POOR_NET_PROFILE.value] += 1
            continue
        veto = gates.post_levels(levels, costs, rules.min_net_rr)
        if veto:
            result.no_trade[veto[0].value] += 1
            continue
        if decision_filter is not None and not decision_filter(context.decision_time, levels):
            result.no_trade["ML_FILTER"] += 1
            continue
        active_from = k + 1 + costs.extra_entry_delay_bars
        pending = {
            "active_from": active_from,
            "expires_after": active_from + decision.entry_intent.expires_after_bars - 1,
            "trade": Trade(symbol=symbol, strategy=strategy.strategy_id, setup_time=decision.setup_time,
                           trend_regime=context.regime.trend.value, volatility_regime=context.regime.volatility.value,
                           entry_limit=float(levels.entry), stop=float(levels.stop), target=float(levels.targets[0]),
                           exit_policy=decision.exit_policy_id, targets=tuple(float(t) for t in levels.targets),
                           target_weights=tuple(float(w) for w in levels.target_weights)),
        }

    last = len(frame) - 1
    if position is not None:
        finalize(last, censor_price=closes[last])
    if pending is not None:
        pending["trade"].entry_status = "EXPIRED"
        result.trades.append(pending["trade"])
    return result


def _setup_tf(frame: pd.DataFrame) -> str:
    step = frame["decision_time"].iat[0] - frame["open_time"].iat[0]
    return {pd.Timedelta(minutes=15): "15m", pd.Timedelta(hours=1): "1h", pd.Timedelta(minutes=5): "5m",
            pd.Timedelta(hours=4): "4h"}.get(step, str(step))


def trades_frame(result: SimulationResult) -> pd.DataFrame:
    return pd.DataFrame([t.to_dict() for t in result.trades])
