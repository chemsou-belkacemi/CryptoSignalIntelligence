"""Interface de stratégie : fonction pure du contexte.

Une stratégie ne télécharge rien, n'écrit rien, n'appelle aucun LLM et
n'exécute aucun ordre. Elle lit un MarketContext et renvoie un StrategyResult.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import StrategyStatus
from ..domain.market import MarketContext, StrategyResult
from ..features.builder import SetupFeatureParams


class StrategyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    status: StrategyStatus = StrategyStatus.RESEARCH
    # Veto POOR_NET_PROFILE : RR du TP de référence net de coûts centraux minimal.
    min_net_rr: float = Field(1.0, ge=0)


class Strategy(ABC):
    strategy_id: ClassVar[str]
    params_model: ClassVar[type[StrategyParams]]
    # Clés de MarketContext.setup lues par la stratégie (traçabilité + contexte minimal).
    setup_keys: ClassVar[tuple[str, ...]]
    hypothesis: ClassVar[str] = ""
    # Variantes comparées à la version de base, sous coûts centraux :
    # `ablations` retirent un filtre (critère 7 du protocole), `extensions` en ajoutent un.
    ablations: ClassVar[dict[str, dict[str, Any]]] = {}
    extensions: ClassVar[dict[str, dict[str, Any]]] = {}
    # Grille grossière de recalibrage du walk-forward ; budget d'essais = taille du produit.
    calibration_grid: ClassVar[dict[str, tuple[Any, ...]]] = {}

    def __init__(self, params: StrategyParams):
        self.params = params

    @property
    def version(self) -> int:
        return self.params.version

    @property
    def status(self) -> StrategyStatus:
        return self.params.status

    @property
    def requires_context(self) -> bool:
        """Le contexte 1h est-il obligatoire (veto REQUIRED_CONTEXT_UNAVAILABLE s'il manque) ?"""
        return True

    @abstractmethod
    def feature_params(self, gap_block_bars: int) -> SetupFeatureParams: ...

    @abstractmethod
    def evaluate(self, context: MarketContext) -> StrategyResult: ...

    @property
    def warmup_bars(self) -> int:
        """Bougies de setup minimales avant toute décision (convergence des indicateurs)."""
        return 250
