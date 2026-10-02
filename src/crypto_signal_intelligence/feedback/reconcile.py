"""Rapprochement des signaux publiés : backtest, simulation prospective et exécution Demo observée.

Trois mesures, jamais confondues ni additionnées (points 15 et 16 du cahier des charges) :
- backtest : espérance hors échantillon du dernier walk-forward de la stratégie, dans la variante
  qui correspond à la politique du signal (profil_BSM pour une politique BSM_*) ;
- prospectif (théorique) : rejeu du signal sur les bougies stockées APRÈS sa publication, avec
  les règles du simulateur ET la politique de sortie du signal (TP partiels, stop, sortie temporelle
  seulement si la politique en a une) ;
- Demo observée : état reconstruit à partir des événements importés (quantités réellement remplies).

Chaîne d'états : fichier publié → message reçu (RECEIVED) → ordre envoyé (ORDER_PLACED) → ordre
rempli (ENTRY_*). Sans confirmation, l'état est UNKNOWN : ce n'est ni un refus ni une perte. Un
signal shadow n'est pas consommé (NOT_CONSUMED). Un prix qui touche l'entrée n'est pas la preuve
que le consommateur a été rempli.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..backtest.exits import OpenPosition, exit_policy, pnl_per_unit
from ..config import CostScenario, Settings
from ..data.schema import interval
from ..data.store import CandleStore
from ..signals.outbox import SignalRegistry
from ..signals.schema import Signal
from .schema import BUY_EVENTS, SELL_EVENTS, ExecutionEvent
from .store import FeedbackStore

CLOSED_OUTCOMES = ("TP", "SL", "SL_GAP", "TIMEOUT")


@dataclass
class ExecutionState:
    signal_id: str
    # UNKNOWN | RECEIVED | ORDER_PLACED | REJECTED | EXPIRED | CANCELLED | ENTRY_PARTIAL | OPEN | CLOSED
    status: str = "UNKNOWN"
    events: int = 0
    received_at: datetime | None = None
    consumer_policy_hash: str | None = None
    ordered_qty: Decimal = Decimal(0)
    first_entry_at: datetime | None = None
    bought_qty: Decimal = Decimal(0)
    bought_quote: Decimal = Decimal(0)
    sold_qty: Decimal = Decimal(0)
    sold_quote: Decimal = Decimal(0)
    fees_quote: Decimal = Decimal(0)
    fees_other: dict[str, Decimal] = field(default_factory=dict)
    fills_without_fee: int = 0
    tp_fills: int = 0
    stop_filled: bool = False
    market_exits: int = 0
    reason: str | None = None
    realized_pnl_quote: Decimal | None = None
    realized_r: float | None = None

    @property
    def avg_entry_price(self) -> Decimal | None:
        return self.bought_quote / self.bought_qty if self.bought_qty else None

    def to_dict(self) -> dict:
        return asdict(self)


def execution_state(signal: Signal, events: list[ExecutionEvent]) -> ExecutionState:
    """Reconstruit l'état d'exécution à partir des événements (ordre chronologique)."""
    state = ExecutionState(signal_id=signal.signal_id, events=len(events))
    if not events:
        return state
    quote_asset = "USDT" if signal.symbol.endswith("USDT") else "USDC"
    closed = False
    for event in sorted(events, key=lambda e: (e.occurred_at, e.event_id)):
        kind = event.event_type
        if kind == "RECEIVED" and state.received_at is None:
            state.received_at, state.consumer_policy_hash = event.occurred_at, event.exit_policy_hash
        elif kind == "ORDER_PLACED" and event.quantity is not None:
            state.ordered_qty += event.quantity
        elif kind in BUY_EVENTS and event.quantity is not None and event.price is not None:
            state.first_entry_at = state.first_entry_at or event.occurred_at
            state.bought_qty += event.quantity
            state.bought_quote += event.quote_quantity or event.quantity * event.price
        elif kind in SELL_EVENTS and event.quantity is not None and event.price is not None:
            state.sold_qty += event.quantity
            state.sold_quote += event.quote_quantity or event.quantity * event.price
            state.tp_fills += kind == "TP_FILLED"
            state.stop_filled |= kind == "STOP_FILLED"
            state.market_exits += kind == "MARKET_EXIT_FILLED"
        if kind in BUY_EVENTS | SELL_EVENTS:
            if event.fee is None:
                state.fills_without_fee += 1      # frais inconnus : jamais comptés comme nuls
            elif event.fee_asset == quote_asset:
                state.fees_quote += event.fee
            elif event.fee_asset is not None:
                state.fees_other[event.fee_asset] = state.fees_other.get(event.fee_asset, Decimal(0)) + event.fee
        if kind in {"REJECTED", "CANCELLED", "MARKET_EXIT_FILLED"}:
            state.reason = event.reason
        if kind == "CLOSED":
            closed = True
    kinds = {e.event_type for e in events}
    if "REJECTED" in kinds:
        state.status = "REJECTED"
    elif state.bought_qty == 0:
        state.status = next((k for k in ("EXPIRED", "CANCELLED", "ORDER_PLACED", "RECEIVED") if k in kinds), "UNKNOWN")
    elif closed or state.sold_qty >= state.bought_qty:
        state.status = "CLOSED"
    elif "ENTRY_FILLED" in kinds or state.sold_qty > 0:
        state.status = "OPEN"
    else:
        state.status = "ENTRY_PARTIAL"
    if state.status == "CLOSED":
        state.realized_pnl_quote = state.sold_quote - state.bought_quote - state.fees_quote
        planned_risk = state.bought_qty * (signal.entry_1 - signal.stop_loss)
        if planned_risk > 0:
            state.realized_r = round(float(state.realized_pnl_quote / planned_risk), 4)
    return state


@dataclass(frozen=True)
class Prospective:
    outcome: str                   # NO_DATA | PENDING | UNFILLED | OPEN | TP | SL | SL_GAP | TIMEOUT
    r: float | None = None
    fill_at: datetime | None = None
    fill_price: float | None = None
    ambiguous: bool = False


def replay_signal(bars: pd.DataFrame, signal: Signal, costs: CostScenario, step: timedelta) -> Prospective:
    """Rejoue UN signal publié avec les règles du simulateur et SA politique de sortie.

    `bars` : bougies clôturées du timeframe de setup, triées ; seules celles qui ouvrent à partir de
    DECISION_AT comptent. Entrée LIMIT active sur les bougies qui CLÔTURENT au plus tard à
    ENTRY_EXPIRES_AT (pas EXPIRES_AT, qui ne borne que l'acceptation du message) : les mêmes
    `expires_after_bars` bougies que le simulateur. R rapporté au risque prévu ENTRY_1 − STOP_LOSS. Les TP suivent les poids TP_WEIGHTS ; la sortie temporelle n'existe
    que si la politique en prévoit une (MAX_HOLD_MINUTES).
    """
    if signal.entry_count != 1:
        raise ValueError("rejeu à deux entrées non implémenté")
    bars = bars[bars["open_time"] >= pd.Timestamp(signal.decision_at)].reset_index(drop=True)
    if bars.empty:
        return Prospective("NO_DATA")
    fee = costs.fee_bps / 1e4
    market_cost = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    entry, stop = float(signal.entry_1), float(signal.stop_loss)
    opens, highs, lows, closes = (bars[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    times = [t.to_pydatetime() for t in bars["open_time"]]
    window = sum(1 for t in times if t + step <= signal.entry_expires_at)
    fill = None
    for k in range(window):
        if opens[k] <= entry:
            fill = (k, min(opens[k] * (1 + market_cost), entry), False)
            break
        if lows[k] < entry:
            fill = (k, entry, True)
            break
    if fill is None:
        entry_window_over = len(times) > window or times[-1] + 2 * step > signal.entry_expires_at
        return Prospective("UNFILLED" if entry_window_over else "PENDING")
    start, price, touched = fill
    if not touched and opens[start] <= stop:   # ouverture sous le stop (gap) : stop-market immédiat
        exit_price = opens[start] * (1 - market_cost)
        r = (exit_price * (1 - fee) - price * (1 + fee)) / (entry - stop)
        return Prospective("SL_GAP", round(r, 4), times[start], price)
    position = OpenPosition(entry=price, initial_stop=stop, targets=tuple(float(t) for t in signal.targets),
                            weights=tuple(float(w) for w in signal.tp_weights),
                            policy=exit_policy(signal.exit_policy_id))
    max_bars = None
    if signal.max_hold_minutes is not None:
        max_bars = max(1, int(timedelta(minutes=signal.max_hold_minutes) / step))
    for k in range(start, len(bars)):
        held = k - start + 1
        position.process_bar(opens[k], highs[k], lows[k], closes[k], first_bar=k == start, touched=touched and k == start,
                             market_cost=market_cost, time_limit_reached=max_bars is not None and held >= max_bars)
        if position.closed:
            r = pnl_per_unit(position.fills, price, fee) / (entry - stop)
            return Prospective(position.exit_reason or "CLOSED", round(r, 4), times[start], price, position.ambiguous)
    return Prospective("OPEN", None, times[start], price, position.ambiguous)


def backtest_reference(db_path: Path, strategy: str, policy_id: str) -> tuple[float | None, str]:
    """E[R] hors échantillon (coûts centraux) du dernier walk-forward, variante alignée sur la politique."""
    if not Path(db_path).exists():
        return None, "aucun walk-forward"
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute("SELECT run_id, metrics FROM runs WHERE kind='WALK_FORWARD' AND strategy=? "
                         "ORDER BY created_at DESC LIMIT 1", (strategy,)).fetchone()
    if row is None:
        return None, "aucun walk-forward"
    oos = json.loads(row[1]).get("oos", {})
    variant = "profil_BSM/central" if policy_id.startswith("BSM_") else "base/central"
    if variant not in oos:
        return None, f"{row[0]} : variante {variant} absente (profil non aligné)"
    return oos[variant].get("expectancy_r"), f"{row[0]} {variant}"


#: Écart toléré entre le taux de frais réel et l'hypothèse centrale avant de le signaler (arrondis de la bourse).
FEE_TOLERANCE_BPS = 0.5


def fee_rate_bps(state: ExecutionState) -> float | None:
    """Taux de frais réellement payé, en points de base du montant échangé, quand tous les frais sont connus et payés
    dans la devise de cotation ; None sinon (frais inconnus, payés en BNB ou dans l'actif acheté : non convertis ici)."""
    traded = state.bought_quote + state.sold_quote
    if state.fills_without_fee or state.fees_other or not traded or state.fees_quote <= 0:
        return None
    return float(state.fees_quote / traded) * 1e4


def deviations(signal: Signal, channel: str, theory: Prospective, state: ExecutionState, *,
               assumed_fee_bps: float | None = None) -> list[str]:
    """Explique les écarts prospectif / Demo : délai, prix, entrée manquée, taille, politique, frais. `assumed_fee_bps` :
    frais par ordre du scénario central ; un taux réel différent (remise BNB absente en Demo, palier) est signalé."""
    notes: list[str] = []
    if state.consumer_policy_hash and state.consumer_policy_hash != signal.exit_policy_hash:
        notes.append(f"POLITIQUE_DIFFÉRENTE (consommateur {state.consumer_policy_hash})")
    if state.received_at is not None:
        notes.append(f"RÉCEPTION +{(state.received_at - signal.created_at).total_seconds():.0f} s")
    if state.first_entry_at and theory.fill_at:
        notes.append(f"ENTRÉE {(state.first_entry_at - theory.fill_at).total_seconds() / 60:+.0f} min vs théorie")
    avg = state.avg_entry_price
    if avg is not None and theory.fill_price:
        notes.append(f"PRIX_ENTRÉE {(float(avg) / theory.fill_price - 1) * 1e4:+.1f} pb vs théorie")
    theory_filled = theory.fill_at is not None
    if channel == "outbox" and theory_filled and state.bought_qty == 0 and state.status in {"EXPIRED", "CANCELLED",
                                                                                          "REJECTED"}:
        notes.append(f"ENTRÉE_MANQUÉE ({state.status}{': ' + state.reason if state.reason else ''})")
    if theory.outcome == "UNFILLED" and state.bought_qty > 0:
        notes.append("REMPLI_HORS_THÉORIE")
    if state.ordered_qty and state.bought_qty < state.ordered_qty and state.status not in {"ENTRY_PARTIAL"}:
        notes.append(f"TAILLE {float(state.bought_qty / state.ordered_qty):.0%} de la quantité commandée")
    if state.market_exits:
        notes.append(f"SORTIE_MARCHÉ_HORS_POLITIQUE ×{state.market_exits} ({state.reason or 'motif absent'})")
    if state.fills_without_fee:
        notes.append(f"FRAIS_INCONNUS sur {state.fills_without_fee} remplissage(s)")
    rate = fee_rate_bps(state)
    if rate is not None and assumed_fee_bps is not None and abs(rate - assumed_fee_bps) > FEE_TOLERANCE_BPS:
        notes.append(f"FRAIS {rate:.2f} pb par ordre vs {assumed_fee_bps:g} pb supposés")
    for asset, amount in sorted(state.fees_other.items()):
        notes.append(f"FRAIS_EN_{asset} {amount} (non convertis : taux réel à vérifier)")
    if theory.ambiguous:
        notes.append("THÉORIE_AMBIGUË (TP et stop dans la même bougie)")
    return notes


@dataclass
class ReportRow:
    signal_id: str
    symbol: str
    strategy: str
    exit_policy: str
    created_at: str
    expires_at: str
    entry_expires_at: str
    channel: str                    # shadow | outbox
    publication: str                # PUBLISHED | PENDING | CONFLICT
    entry: str
    stop: str
    tp1: str
    backtest_r: float | None
    backtest_source: str
    theoretical_outcome: str
    theoretical_r: float | None
    demo_status: str
    demo_r: float | None
    demo_events: int
    deviations: tuple[str, ...] = ()


def execution_report(settings: Settings) -> list[ReportRow]:
    registry = SignalRegistry(settings.signals_db, settings.publication_dir())
    feedback = FeedbackStore(settings.feedback_db)
    store = CandleStore(settings.data_dir)
    step = interval(settings.data.setup_timeframe)
    shadow_dir = (settings.root / settings.publication.shadow_dir).resolve()
    candles: dict[str, pd.DataFrame] = {}
    rows = []
    for record in registry.rows():
        signal = registry.load(record["signal_id"])
        if signal.symbol not in candles:
            frame = store.load(signal.symbol, settings.data.setup_timeframe)
            candles[signal.symbol] = frame.sort_values("open_time").reset_index(drop=True) if not frame.empty else frame
        frame = candles[signal.symbol]
        theory = Prospective("NO_DATA") if frame.empty else replay_signal(frame, signal, settings.costs["central"], step)
        state = execution_state(signal, feedback.events_for(signal.signal_id))
        channel = "shadow" if registry.directory_of(record).resolve() == shadow_dir else "outbox"
        demo_status = "NOT_CONSUMED" if channel == "shadow" and state.status == "UNKNOWN" else state.status
        backtest_r, source = backtest_reference(settings.experiments_db, signal.strategy, signal.exit_policy_id)
        rows.append(ReportRow(
            signal_id=signal.signal_id, symbol=signal.symbol, strategy=signal.strategy,
            exit_policy=signal.exit_policy_id, created_at=signal.created_at.isoformat(),
            expires_at=signal.expires_at.isoformat(), entry_expires_at=signal.entry_expires_at.isoformat(),
            channel=channel, publication=record["status"], entry=str(signal.entry_1), stop=str(signal.stop_loss),
            tp1=str(signal.tp_1), backtest_r=backtest_r, backtest_source=source,
            theoretical_outcome=theory.outcome, theoretical_r=theory.r, demo_status=demo_status,
            demo_r=state.realized_r, demo_events=state.events,
            deviations=tuple(deviations(signal, channel, theory, state, assumed_fee_bps=settings.costs["central"].fee_bps))))
    return rows
