"""C. RANGE_REENTRY — fiche : docs/strategies/RANGE_REENTRY.md

Règle v1 (figée ; toute modification = nouvelle version), sur la bougie 15m clôturée t,
avec les bandes de Bollinger(20) : basse = centre − bb_k × écart-type :
- excursion : close(t−1) < bande basse(t−1) ;
- réintégration : close(t) > bande basse(t) ;
- range assez large : largeur totale de bande >= min_band_width_pct du centre ;
- contexte (désactivable pour l'ablation) : tendance 1h RANGE ;
- filtre BTC (désactivable) : rendement 24 h de BTC (1h, disponible) >= −btc_max_drop_24h_pct ;
- filtre RSI (extension testée séparément, inactif en v1) : RSI14 <= rsi_max ;
- invalidation = plus bas des `extreme_bars` dernières bougies − stop_buffer_atr × ATR14 ;
- cible : centre de bande à la décision (prix, arrondi vers le bas) ; le veto de RR net
  refuse les cibles qui ne couvrent pas assez les coûts et le risque.
"""
from __future__ import annotations

import math

from pydantic import Field

from ..domain.enums import Action, EntryMode, NoTradeReason, TrendRegime
from ..domain.market import EntryIntent, MarketContext, StrategyResult
from ..features.builder import SetupFeatureParams
from .base import Strategy, StrategyParams

EXIT_POLICY_ID = "FIXED_SL_ONE_TP_V1"


class RangeReentryParams(StrategyParams):
    bb_k: float = Field(2.0, gt=0)
    extreme_bars: int = Field(3, ge=2, le=50)
    stop_buffer_atr: float = Field(0.5, ge=0)
    min_band_width_pct: float = Field(0.5, ge=0)
    require_range_context: bool = True
    use_btc_filter: bool = True
    btc_max_drop_24h_pct: float = Field(3.0, gt=0)
    use_rsi_filter: bool = False
    rsi_max: float = Field(35, gt=0, lt=100)
    entry_max_deviation_bps: float = Field(10, ge=0, le=200)
    entry_expiry_bars: int = Field(2, ge=1, le=96)
    min_net_rr: float = Field(0.8, ge=0)


class RangeReentry(Strategy):
    strategy_id = "RANGE_REENTRY"
    params_model = RangeReentryParams
    setup_keys = ("bb_mid", "bb_sigma", "prev_close", "prev_bb_mid", "prev_bb_sigma", "recent_low", "rsi14")
    hypothesis = ("Dans un range 1h stable, une clôture 15m sous la bande de Bollinger basse suivie d'une "
                  "réintégration revient vers le centre de bande plus souvent que ne l'exigent coûts et risque.")
    ablations = {"sans_filtre_contexte": {"require_range_context": False},
                 "sans_filtre_btc": {"use_btc_filter": False}}
    extensions = {"avec_filtre_rsi": {"use_rsi_filter": True}}
    calibration_grid = {"bb_k": (2.0, 2.5), "stop_buffer_atr": (0.25, 0.5, 1.0)}
    params: RangeReentryParams

    @property
    def requires_context(self) -> bool:
        return self.params.require_range_context

    def feature_params(self, gap_block_bars: int) -> SetupFeatureParams:
        return SetupFeatureParams(recent_low_bars=self.params.extreme_bars, gap_block_bars=gap_block_bars)

    def evaluate(self, context: MarketContext) -> StrategyResult:
        p, s = self.params, context.setup
        no_trade = lambda reason, *details: StrategyResult.no_trade(  # noqa: E731
            self.strategy_id, self.version, context.decision_time, context.regime, reason, *details)
        needed = ("close", "atr14", "bb_mid", "bb_sigma", "prev_close", "prev_bb_mid", "prev_bb_sigma", "recent_low")
        if any(math.isnan(s[k]) for k in needed) or s["atr14"] <= 0 or s["bb_mid"] <= 0:
            return no_trade(NoTradeReason.INSUFFICIENT_HISTORY, "indicateurs non disponibles")
        if p.require_range_context and context.regime.trend == TrendRegime.UNKNOWN:
            return no_trade(NoTradeReason.UNKNOWN_REGIME, "tendance 1h inconnue")
        close, atr, mid = s["close"], s["atr14"], s["bb_mid"]
        lower = mid - p.bb_k * s["bb_sigma"]
        previous_lower = s["prev_bb_mid"] - p.bb_k * s["prev_bb_sigma"]
        if not s["prev_close"] < previous_lower:
            return no_trade(NoTradeReason.NO_SETUP, "pas de clôture précédente sous la bande basse")
        if not close > lower:
            return no_trade(NoTradeReason.NO_SETUP, "pas de réintégration dans la bande")
        width_pct = 200 * p.bb_k * s["bb_sigma"] / mid
        if width_pct < p.min_band_width_pct:
            return no_trade(NoTradeReason.NO_SETUP, f"range trop étroit : bande {width_pct:.2f} %")
        if p.require_range_context and context.regime.trend != TrendRegime.RANGE:
            return no_trade(NoTradeReason.NO_SETUP, f"contexte 1h {context.regime.trend.value} (RANGE requis)")
        reasons = [f"clôture précédente {s['prev_close']:.8g} < bande basse {previous_lower:.8g}",
                   f"réintégration : clôture {close:.8g} > bande basse {lower:.8g}", f"bande {width_pct:.2f} %"]
        if p.use_btc_filter:
            btc_return = context.btc.get("ret_24h", math.nan)
            if math.isnan(btc_return):
                return no_trade(NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE, "rendement 24 h de BTC indisponible")
            if 100 * btc_return < -p.btc_max_drop_24h_pct:
                return no_trade(NoTradeReason.NO_SETUP, f"stress BTC : {100 * btc_return:.2f} % sur 24 h")
            reasons.append(f"BTC {100 * btc_return:+.2f} % sur 24 h")
        if p.use_rsi_filter:
            rsi = s["rsi14"]
            if math.isnan(rsi):
                return no_trade(NoTradeReason.INSUFFICIENT_HISTORY, "RSI non disponible")
            if rsi > p.rsi_max:
                return no_trade(NoTradeReason.NO_SETUP, f"RSI {rsi:.1f} > {p.rsi_max}")
            reasons.append(f"RSI {rsi:.1f}")
        if p.require_range_context:
            reasons.append("contexte 1h RANGE")
        if not mid > close:
            return no_trade(NoTradeReason.POOR_NET_PROFILE, "centre de bande déjà atteint")
        stop = s["recent_low"] - p.stop_buffer_atr * atr
        return StrategyResult(
            strategy_id=self.strategy_id, strategy_version=self.version, action=Action.BUY,
            setup_time=context.decision_time, regime=context.regime,
            entry_intent=EntryIntent(EntryMode.LIMIT, close, p.entry_max_deviation_bps, p.entry_expiry_bars),
            invalidation_reference=stop, exit_policy_id=EXIT_POLICY_ID, target_price=mid,
            reasons=tuple(reasons), features_used=needed + ("ctx_trend", "btc_ret_24h"),
            extra={"band_lower": lower, "band_width_pct": width_pct},
        )
