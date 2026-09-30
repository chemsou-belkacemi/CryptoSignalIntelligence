"""B. EMA_PULLBACK_CONTINUATION — fiche : docs/strategies/EMA_PULLBACK_CONTINUATION.md

Règle v1 (figée ; toute modification = nouvelle version), sur la bougie 15m clôturée t :
- zone(t) = EMA20(t) + zone_atr × ATR14(t) : haut de la zone de repli ;
- tendance 15m : EMA20 > EMA50 ;
- reprise : close(t) > zone(t) ET close(t−1) <= zone(t−1) (première clôture de retour au-dessus) ;
- contact : plus bas des `pullback_bars` dernières bougies (t incluse) <= zone(t) ;
- profondeur : ce plus bas >= EMA20(t) − max_depth_atr × ATR14(t) (au-delà : retournement possible) ;
- contexte (désactivable pour l'ablation) : tendance 1h BULL (EMA20 > EMA50, close > EMA50, pente > 0) ;
- invalidation = plus bas du repli − stop_buffer_atr × ATR14 ; refus si le risque dépasse max_risk_atr × ATR14 ;
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


class EmaPullbackParams(StrategyParams):
    pullback_bars: int = Field(4, ge=2, le=50)
    zone_atr: float = Field(0.25, ge=0)
    max_depth_atr: float = Field(1.5, gt=0)
    stop_buffer_atr: float = Field(0.25, ge=0)
    max_risk_atr: float = Field(3.0, gt=0)
    target_r: float = Field(2.0, gt=0)
    require_bull_context: bool = True
    entry_max_deviation_bps: float = Field(10, ge=0, le=200)
    entry_expiry_bars: int = Field(2, ge=1, le=96)
    min_net_rr: float = Field(1.2, ge=0)


class EmaPullbackContinuation(Strategy):
    strategy_id = "EMA_PULLBACK_CONTINUATION"
    params_model = EmaPullbackParams
    setup_keys = ("ema20", "ema50", "recent_low", "prev_close", "prev_ema20", "prev_atr14")
    hypothesis = ("Dans une tendance 1h établie, un repli 15m vers l'EMA20 suivi d'une clôture de reprise "
                  "offre une poursuite avec une invalidation proche, au-delà des coûts.")
    ablations = {"sans_filtre_contexte": {"require_bull_context": False}}
    calibration_grid = {"zone_atr": (0.25, 0.5), "stop_buffer_atr": (0.25, 0.5), "target_r": (1.5, 2.0, 3.0)}
    params: EmaPullbackParams

    @property
    def requires_context(self) -> bool:
        return self.params.require_bull_context

    def feature_params(self, gap_block_bars: int) -> SetupFeatureParams:
        return SetupFeatureParams(recent_low_bars=self.params.pullback_bars, gap_block_bars=gap_block_bars)

    def evaluate(self, context: MarketContext) -> StrategyResult:
        p, s = self.params, context.setup
        no_trade = lambda reason, *details: StrategyResult.no_trade(  # noqa: E731
            self.strategy_id, self.version, context.decision_time, context.regime, reason, *details)
        needed = ("close", "atr14", "ema20", "ema50", "recent_low", "prev_close", "prev_ema20", "prev_atr14")
        if any(math.isnan(s[k]) for k in needed) or s["atr14"] <= 0 or s["prev_atr14"] <= 0:
            return no_trade(NoTradeReason.INSUFFICIENT_HISTORY, "indicateurs non disponibles")
        if p.require_bull_context and context.regime.trend == TrendRegime.UNKNOWN:
            return no_trade(NoTradeReason.UNKNOWN_REGIME, "tendance 1h inconnue")
        close, atr, ema20, low = s["close"], s["atr14"], s["ema20"], s["recent_low"]
        zone = ema20 + p.zone_atr * atr
        if not s["ema20"] > s["ema50"]:
            return no_trade(NoTradeReason.NO_SETUP, "tendance 15m non haussière (EMA20 <= EMA50)")
        if not close > zone:
            return no_trade(NoTradeReason.NO_SETUP, "pas de clôture au-dessus de la zone EMA20")
        if s["prev_close"] > s["prev_ema20"] + p.zone_atr * s["prev_atr14"]:
            return no_trade(NoTradeReason.NO_SETUP, "déjà au-dessus de la zone à la bougie précédente")
        if low > zone:
            return no_trade(NoTradeReason.NO_SETUP, "pas de contact avec la zone EMA20")
        depth = (ema20 - low) / atr
        if depth > p.max_depth_atr:
            return no_trade(NoTradeReason.NO_SETUP, f"repli trop profond : {depth:.2f} ATR sous l'EMA20")
        if p.require_bull_context and context.regime.trend != TrendRegime.BULL:
            return no_trade(NoTradeReason.NO_SETUP, f"contexte 1h {context.regime.trend.value}")
        stop = low - p.stop_buffer_atr * atr
        risk_atr = (close - stop) / atr
        if risk_atr > p.max_risk_atr:
            return no_trade(NoTradeReason.NO_SETUP, f"stop trop éloigné : {risk_atr:.2f} ATR")
        reasons = [f"reprise : clôture {close:.8g} > zone EMA20 {zone:.8g}",
                   f"plus bas du repli {low:.8g} ({depth:.2f} ATR sous l'EMA20)", "EMA20 > EMA50 en 15m"]
        if p.require_bull_context:
            reasons.append("contexte 1h BULL")
        return StrategyResult(
            strategy_id=self.strategy_id, strategy_version=self.version, action=Action.BUY,
            setup_time=context.decision_time, regime=context.regime,
            entry_intent=EntryIntent(EntryMode.LIMIT, close, p.entry_max_deviation_bps, p.entry_expiry_bars),
            invalidation_reference=stop, exit_policy_id=EXIT_POLICY_ID, target_r=p.target_r,
            reasons=tuple(reasons), features_used=needed + ("ctx_trend",),
            extra={"pullback_low": low, "depth_atr": depth, "risk_atr": risk_atr},
        )
