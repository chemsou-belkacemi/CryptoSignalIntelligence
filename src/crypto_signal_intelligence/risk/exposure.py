"""Limites d'exposition centralisées (docs/ML_INTRADAY.md §5) : un seul registre pour tout le capital.

Toutes les stratégies (intraday, swing plus tard) et toutes les paires passent par le MÊME registre de
positions ouvertes, positions déjà ouvertes comprises (par exemple signalées par BinanceSpotManager) :
une entrée n'est permise que si, taille comprise, elle respecte le nombre de positions, l'exposition
totale, par paire et par stratégie, et si la perte du jour UTC n'a pas atteint la limite.

CSI ne dimensionne aucun ordre réel (BinanceSpotManager le fait) : ce registre sert aux simulations de
portefeuille et pourra refuser la publication d'un signal qui dépasserait ces limites.
Hypothèses déclarées : capital suivi en RÉALISÉ (une position compte pour son montant d'entrée jusqu'à
sa sortie) ; spot sans levier (exposition totale ≤ 100 % du capital).
Les instants sont des entiers en nanosecondes UTC (rapide dans les boucles de simulation) ;
`to_ns` convertit un horodatage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import RiskSection

DAY_NS = 86_400 * 10**9
EPSILON = 1e-12


def to_ns(moment) -> int:
    return int(pd.Timestamp(moment).value)


@dataclass(frozen=True)
class RiskLimits:
    position_fraction: float = 0.10
    max_positions: int = 5
    max_total_exposure: float = 0.50
    max_asset_exposure: float = 0.10
    max_strategy_exposure: float = 0.50
    daily_loss_limit: float = 0.03

    def __post_init__(self) -> None:
        fractions = (self.position_fraction, self.max_total_exposure, self.max_asset_exposure,
                     self.max_strategy_exposure, self.daily_loss_limit)
        if self.max_positions < 1 or not all(0 < f <= 1 for f in fractions):
            raise ValueError("limites incohérentes (fractions dans ]0, 1], au moins une position ; spot sans levier)")
        if self.position_fraction > min(self.max_asset_exposure, self.max_strategy_exposure,
                                        self.max_total_exposure) + EPSILON:
            raise ValueError("une position dépasserait à elle seule une limite d'exposition : aucune entrée possible")

    @classmethod
    def from_settings(cls, section: RiskSection) -> RiskLimits:
        return cls(**section.model_dump())


@dataclass
class Position:
    symbol: str
    strategy: str
    notional: float                 # montant à l'entrée (fraction du capital de départ = 1)
    entry_ns: int
    exit_ns: int | None = None      # sortie prévue ; None : inconnue (position venue du bot)
    net: float | None = None        # rendement net réalisé, connu en simulation
    tag: int = -1                   # rang du candidat d'origine (journal des décisions)


@dataclass
class ExposureBook:
    """Registre unique des positions ouvertes et du capital réalisé."""
    limits: RiskLimits
    equity: float = 1.0
    positions: list[Position] = field(default_factory=list)
    closed: list[Position] = field(default_factory=list)
    _day: int | None = None
    _day_start: float = 1.0

    def exposure(self, *, symbol: str | None = None, strategy: str | None = None) -> float:
        return sum(p.notional for p in self.positions
                   if (symbol is None or p.symbol == symbol) and (strategy is None or p.strategy == strategy))

    def _close(self, before_ns: int, inclusive: bool) -> None:
        keep = []
        for position in self.positions:
            due = position.exit_ns is not None and (position.exit_ns <= before_ns if inclusive
                                                     else position.exit_ns < before_ns)
            if due:
                self.equity += position.notional * (position.net or 0.0)
                self.closed.append(position)
            else:
                keep.append(position)
        self.positions = keep

    def advance(self, moment_ns: int) -> None:
        """Réalise les sorties échues ; au premier instant d'un nouveau jour UTC, le capital de référence
        de la limite de perte journalière est celui de minuit (sorties de la veille réalisées)."""
        day = moment_ns // DAY_NS
        if self._day is None or day > self._day:
            self._close(day * DAY_NS, inclusive=False)
            self._day, self._day_start = day, self.equity
        self._close(moment_ns, inclusive=True)

    def position_size(self) -> float:
        return self.limits.position_fraction * self.equity

    def refusal(self, symbol: str, strategy: str) -> str | None:
        """Raison du refus d'une nouvelle entrée, ou None si elle respecte toutes les limites."""
        limits, equity = self.limits, self.equity
        if equity <= 0 or (equity - self._day_start) / self._day_start <= -limits.daily_loss_limit:
            return "DAILY_LOSS_LIMIT"
        if len(self.positions) >= limits.max_positions:
            return "MAX_POSITIONS"
        size = self.position_size()
        if self.exposure(symbol=symbol) + size > limits.max_asset_exposure * equity + EPSILON:
            return "ASSET_EXPOSURE"
        if self.exposure(strategy=strategy) + size > limits.max_strategy_exposure * equity + EPSILON:
            return "STRATEGY_EXPOSURE"
        if self.exposure() + size > limits.max_total_exposure * equity + EPSILON:
            return "TOTAL_EXPOSURE"
        return None

    def try_open(self, symbol: str, strategy: str, moment_ns: int, exit_ns: int | None, net: float | None = None,
                 tag: int = -1) -> tuple[Position | None, str | None]:
        self.advance(moment_ns)
        reason = self.refusal(symbol, strategy)
        if reason is not None:
            return None, reason
        position = Position(symbol, strategy, self.position_size(), moment_ns, exit_ns, net, tag)
        self.positions.append(position)
        return position, None

    def finish(self) -> None:
        """Fin de simulation : réalise toutes les positions dont la sortie est connue."""
        self._close(2**62, inclusive=True)
