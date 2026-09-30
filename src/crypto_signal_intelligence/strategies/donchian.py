"""A. DONCHIAN_VOLUME_BREAKOUT — fiche : docs/strategies/DONCHIAN_VOLUME_BREAKOUT.md

Règle v1 (figée ; toute modification = nouvelle version) :
- niveau = plus haut des `lookback_bars` bougies 15m PRÉCÉDENTES (courante exclue) ;
- setup si close > niveau ;
- filtre volume (désactivable pour l'ablation) : volume > volume_multiple ×
  moyenne des `volume_window_bars` bougies précédentes ;
- filtre de contexte (désactivable) : tendance 1h BULL ;
- entrée tardive refusée si (close − niveau) > max_distance_atr × ATR14 ;
- invalidation = close − stop_atr × ATR14 (règle ATR, pas de stop structurel en v1) ;
- sortie : un TP de référence à target_r × risque (politique FIXED_SL_ONE_TP_V1).
"""
from __future__ import annotations

import math

from pydantic import Field

from ..domain.enums import Action, EntryMode, NoTradeReason, TrendRegime
from ..domain.market import EntryIntent, MarketContext, StrategyResult
from ..features.builder import SetupFeatureParams
from .base import Strategy, StrategyParams

EXIT_POLICY_ID = "FIXED_SL_ONE_TP_V1"


class DonchianParams(StrategyParams):
    lookback_bars: int = Field(20, ge=5, le=500)
    volume_window_bars: int = Field(20, ge=5, le=500)
    volume_multiple: float = Field(1.5, gt=0)
    use_volume_filter: bool = True
    require_bull_context: bool = True
    max_distance_atr: float = Field(0.5, gt=0)
    stop_atr: float = Field(1.5, gt=0)
    target_r: float = Field(2.0, gt=0)
    entry_max_deviation_bps: float = Field(10, ge=0, le=200)
    entry_expiry_bars: int = Field(2, ge=1, le=96)
    min_net_rr: float = Field(1.2, ge=0)


class DonchianVolumeBreakout(Strategy):
    strategy_id = "DONCHIAN_VOLUME_BREAKOUT"
    params_model = DonchianParams
    setup_keys = ("donchian_high", "volume_ratio", "volume_ref")
    hypothesis = ("Une clôture au-dessus du plus haut des 20 bougies 15m précédentes, avec volume élevé et "
                  "contexte 1h haussier, prolonge le mouvement au-delà des coûts.")
    ablations = {"sans_filtre_volume": {"use_volume_filter": False},
                 "sans_filtre_contexte": {"require_bull_context": False}}
    calibration_grid = {"lookback_bars": (20, 40), "stop_atr": (1.0, 1.5, 2.0), "target_r": (1.5, 2.0, 3.0)}
    params: DonchianParams

    @property
    def requires_context(self) -> bool:
        return self.params.require_bull_context

    def feature_params(self, gap_block_bars: int) -> SetupFeatureParams:
        return SetupFeatureParams(donchian_lookback=self.params.lookback_bars,
                                  volume_window=self.params.volume_window_bars, gap_block_bars=gap_block_bars)

    def evaluate(self, context: MarketContext) -> StrategyResult:
        p, s = self.params, context.setup
        no_trade = lambda reason, *details: StrategyResult.no_trade(  # noqa: E731
            self.strategy_id, self.version, context.decision_time, context.regime, reason, *details)
        needed = ("close", "atr14", "donchian_high", "volume_ratio")
        if any(math.isnan(s[k]) for k in needed) or s["atr14"] <= 0:
            return no_trade(NoTradeReason.INSUFFICIENT_HISTORY, "indicateurs non disponibles")
        if p.require_bull_context and context.regime.trend == TrendRegime.UNKNOWN:
            return no_trade(NoTradeReason.UNKNOWN_REGIME, "tendance 1h inconnue")
        level, close, atr = s["donchian_high"], s["close"], s["atr14"]
        if close <= level:
            return no_trade(NoTradeReason.NO_SETUP, "pas de clôture au-dessus du niveau")
        if p.use_volume_filter and not s["volume_ratio"] > p.volume_multiple:
            return no_trade(NoTradeReason.NO_SETUP, f"volume {s['volume_ratio']:.2f}x <= {p.volume_multiple}x")
        if p.require_bull_context and context.regime.trend != TrendRegime.BULL:
            return no_trade(NoTradeReason.NO_SETUP, f"contexte 1h {context.regime.trend.value}")
        distance = (close - level) / atr
        if distance > p.max_distance_atr:
            return no_trade(NoTradeReason.NO_SETUP, f"cassure tardive : {distance:.2f} ATR au-dessus du niveau")
        reasons = [f"clôture {close:.8g} > plus haut {p.lookback_bars} bougies {level:.8g}",
                   f"distance {distance:.2f} ATR"]
        if p.use_volume_filter:
            reasons.append(f"volume {s['volume_ratio']:.2f}x la moyenne précédente")
        if p.require_bull_context:
            reasons.append("contexte 1h BULL")
        return StrategyResult(
            strategy_id=self.strategy_id, strategy_version=self.version, action=Action.BUY,
            setup_time=context.decision_time, regime=context.regime,
            entry_intent=EntryIntent(EntryMode.LIMIT, close, p.entry_max_deviation_bps, p.entry_expiry_bars),
            invalidation_reference=close - p.stop_atr * atr, exit_policy_id=EXIT_POLICY_ID, target_r=p.target_r,
            reasons=tuple(reasons), features_used=needed + ("ctx_trend",),
            extra={"breakout_level": level, "distance_atr": distance},
        )
