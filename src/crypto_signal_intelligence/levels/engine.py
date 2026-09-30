"""TradeLevelEngine : tous les prix d'un signal long simple, calculés en Python.

risk = ENTRY − STOP (> 0) ; RR_TPi_GROSS = (TP_i − ENTRY) / risk, recalculé
APRÈS arrondi au tickSize. Le RR brut n'est ni une probabilité ni une
espérance. Le RR net intègre un scénario de coûts explicite.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ..config import CostScenario
from ..domain.market import StrategyResult
from ..signals.schema import gross_rr


class LevelError(ValueError):
    pass


def round_to_tick(price: float | Decimal, tick: Decimal, direction: str) -> Decimal:
    """Arrondi au tickSize : « up » pour l'entrée (jamais meilleure que prévu), « down » pour le stop."""
    if tick <= 0:
        raise LevelError("tickSize doit être positif")
    if direction not in ("up", "down"):
        raise LevelError(f"sens d'arrondi inconnu : {direction!r} (up | down)")
    steps = (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_CEILING if direction == "up" else ROUND_FLOOR)
    return (steps * tick).quantize(tick)


@dataclass(frozen=True)
class TradeLevels:
    reference_price: Decimal
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    target_weights: tuple[Decimal, ...]
    rr_gross: tuple[Decimal, ...]
    entry_premium_bps: Decimal        # prime de la limite au-dessus de la référence (paramètre de stratégie)
    tick_size: Decimal

    @property
    def risk(self) -> Decimal:
        return self.entry - self.stop

    @property
    def risk_pct(self) -> float:
        return float(self.risk / self.entry * 100)

    def published_deviation_bps(self, tolerance_bps: int) -> Decimal:
        """MAX_ENTRY_DEVIATION_BPS à publier : écart RÉEL entre la référence et ENTRY_1 après arrondi au tick
        (arrondi vers le haut), plus une tolérance pour le mouvement du prix entre la décision et
        l'acceptation par le consommateur. Sans cela, le consommateur, qui compare le prix courant à
        ENTRY_1 en valeur absolue, refuserait le signal dès sa création. Plafonné à 500 (contrat V3)."""
        gap = ((self.entry / self.reference_price - 1) * Decimal(10_000)).copy_abs()
        return min(gap.to_integral_value(rounding=ROUND_CEILING) + Decimal(tolerance_bps), Decimal(500))

    def net_rr(self, costs: CostScenario, index: int = 0) -> float:
        """RR du TP `index` net de frais (2 remplissages) et du glissement sur le stop."""
        entry, stop, target = float(self.entry), float(self.stop), float(self.targets[index])
        fee = costs.fee_bps / 1e4
        stop_fill = stop * (1 - (costs.slippage_bps + costs.half_spread_bps) / 1e4)
        reward = target * (1 - fee) - entry * (1 + fee)
        loss = entry * (1 + fee) - stop_fill * (1 - fee)
        return reward / loss if loss > 0 else float("-inf")


def compute_levels(result: StrategyResult, tick: Decimal) -> TradeLevels:
    if result.entry_intent is None or result.invalidation_reference is None:
        raise LevelError("résultat BUY incomplet")
    intent = result.entry_intent
    reference = Decimal(str(intent.reference_price))
    deviation = Decimal(str(intent.max_deviation_bps))
    entry = round_to_tick(reference * (1 + deviation / Decimal(10_000)), tick, "up")
    stop = round_to_tick(result.invalidation_reference, tick, "down")
    if stop <= 0 or stop >= entry:
        raise LevelError(f"stop {stop} incohérent avec l'entrée {entry}")
    if result.target_price is not None:
        # Cible en prix (ex. centre de bande) : arrondie vers le bas, jamais embellie.
        target = round_to_tick(result.target_price, tick, "down")
    elif result.target_r is not None:
        target = round_to_tick(entry + Decimal(str(result.target_r)) * (entry - stop), tick, "up")
    else:
        raise LevelError("résultat BUY sans cible")
    if target <= entry:
        raise LevelError(f"objectif {target} pas au-dessus de l'entrée {entry} après arrondi")
    return TradeLevels(reference_price=reference, entry=entry, stop=stop, targets=(target,),
                       target_weights=(Decimal("1.0"),), rr_gross=(gross_rr(entry, stop, target),),
                       entry_premium_bps=deviation, tick_size=tick)
