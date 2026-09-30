"""Stratégies B et C, cibles en prix et nouvelles features (valeurs synthétiques)."""
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.domain.enums import (
    Action,
    EntryMode,
    NoTradeReason,
    TrendRegime,
    VolatilityRegime,
)
from crypto_signal_intelligence.domain.market import (
    EntryIntent,
    MarketContext,
    MarketRegime,
    StrategyResult,
    frozen_mapping,
)
from crypto_signal_intelligence.features import indicators as ind
from crypto_signal_intelligence.features.builder import SetupFeatureParams, context_features, setup_features
from crypto_signal_intelligence.levels.engine import LevelError, compute_levels
from crypto_signal_intelligence.strategies import registry
from crypto_signal_intelligence.validation.causality import check

from .conftest import canonical

T = datetime(2024, 1, 1, 10, 15, tzinfo=UTC)


def make_context(setup: dict, trend=TrendRegime.BULL, btc=None) -> MarketContext:
    base = {"open": 100.0, "high": 101.0, "low": 99.0, "volume": 10.0, "atr14": 1.0}
    return MarketContext(symbol="ETHUSDT", setup_timeframe="15m", decision_time=T, available_at=T,
                         setup=frozen_mapping(base | setup), context=frozen_mapping({}),
                         btc=frozen_mapping(btc if btc is not None else {"ret_24h": -0.01}),
                         regime=MarketRegime(trend=trend, volatility=VolatilityRegime.NORMAL), bars_available=1000,
                         data_gap_recent=False, context_available_at=T)


# --- B. EMA_PULLBACK_CONTINUATION -------------------------------------------------------------

PULLBACK = {"close": 100.6, "ema20": 100.0, "ema50": 98.0, "recent_low": 99.5, "prev_close": 100.1,
            "prev_ema20": 100.0, "prev_atr14": 1.0}


def pullback(trend=TrendRegime.BULL, **changes):
    return make_context(PULLBACK | changes, trend=trend)


def test_ema_pullback_buy_levels(settings):
    strategy = registry.build("EMA_PULLBACK_CONTINUATION", settings.strategies)
    result = strategy.evaluate(pullback())
    assert result.action == Action.BUY
    assert result.invalidation_reference == pytest.approx(99.5 - 0.25)   # plus bas du repli − 0,25 ATR
    assert result.target_r == 2.0 and result.target_price is None


@pytest.mark.parametrize(("changes", "fragment"), [
    ({"ema50": 100.5}, "tendance 15m"),
    ({"close": 100.2}, "pas de clôture au-dessus"),
    ({"prev_close": 100.5}, "déjà au-dessus"),
    ({"recent_low": 100.3}, "pas de contact"),
    ({"recent_low": 98.0}, "trop profond"),
    ({"close": 103.5}, "stop trop éloigné"),
])
def test_ema_pullback_rejections(settings, changes, fragment):
    strategy = registry.build("EMA_PULLBACK_CONTINUATION", settings.strategies)
    result = strategy.evaluate(pullback(**changes))
    assert result.no_trade_reason == NoTradeReason.NO_SETUP and fragment in result.reasons[0]


def test_ema_pullback_context_filter_and_ablation(settings):
    strategy = registry.build("EMA_PULLBACK_CONTINUATION", settings.strategies)
    assert strategy.evaluate(pullback(trend=TrendRegime.RANGE)).no_trade_reason == NoTradeReason.NO_SETUP
    assert strategy.evaluate(pullback(trend=TrendRegime.UNKNOWN)).no_trade_reason == NoTradeReason.UNKNOWN_REGIME
    assert strategy.evaluate(pullback(ema20=float("nan"))).no_trade_reason == NoTradeReason.INSUFFICIENT_HISTORY
    ablated = registry.build("EMA_PULLBACK_CONTINUATION", settings.strategies, require_bull_context=False)
    assert not ablated.requires_context
    assert ablated.evaluate(pullback(trend=TrendRegime.RANGE)).action == Action.BUY


# --- C. RANGE_REENTRY ----------------------------------------------------------------------------

REENTRY = {"close": 98.4, "bb_mid": 100.0, "bb_sigma": 1.0, "prev_close": 97.5, "prev_bb_mid": 100.0,
           "prev_bb_sigma": 1.0, "recent_low": 97.0, "rsi14": 30.0}


def reentry(trend=TrendRegime.RANGE, btc=None, **changes):
    return make_context(REENTRY | changes, trend=trend, btc=btc)


def test_range_reentry_buy_targets_band_center(settings):
    strategy = registry.build("RANGE_REENTRY", settings.strategies)
    result = strategy.evaluate(reentry())
    assert result.action == Action.BUY
    assert result.target_price == 100.0 and result.target_r is None
    assert result.invalidation_reference == pytest.approx(97.0 - 0.5)
    levels = compute_levels(result, Decimal("0.01"))
    assert levels.entry == Decimal("98.50") and levels.stop == Decimal("96.50")
    assert levels.targets == (Decimal("100.00"),) and levels.rr_gross == (Decimal("0.750"),)


@pytest.mark.parametrize(("changes", "fragment"), [
    ({"prev_close": 98.2}, "pas de clôture précédente"),
    ({"close": 97.9}, "pas de réintégration"),
    ({"bb_sigma": 0.1, "prev_bb_sigma": 0.1, "prev_close": 99.7, "close": 99.85}, "trop étroit"),
])
def test_range_reentry_rejections(settings, changes, fragment):
    strategy = registry.build("RANGE_REENTRY", settings.strategies)
    result = strategy.evaluate(reentry(**changes))
    assert result.no_trade_reason == NoTradeReason.NO_SETUP and fragment in result.reasons[0]


def test_range_reentry_context_btc_and_rsi_filters(settings):
    strategy = registry.build("RANGE_REENTRY", settings.strategies)
    assert strategy.evaluate(reentry(trend=TrendRegime.BULL)).no_trade_reason == NoTradeReason.NO_SETUP
    stressed = strategy.evaluate(reentry(btc={"ret_24h": -0.05}))
    assert stressed.no_trade_reason == NoTradeReason.NO_SETUP and "stress BTC" in stressed.reasons[0]
    missing = strategy.evaluate(reentry(btc={"ret_24h": float("nan")}))
    assert missing.no_trade_reason == NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE
    without_btc = registry.build("RANGE_REENTRY", settings.strategies, use_btc_filter=False)
    assert without_btc.evaluate(reentry(btc={"ret_24h": float("nan")})).action == Action.BUY
    without_context = registry.build("RANGE_REENTRY", settings.strategies, require_range_context=False)
    assert without_context.evaluate(reentry(trend=TrendRegime.BULL)).action == Action.BUY
    with_rsi = registry.build("RANGE_REENTRY", settings.strategies, use_rsi_filter=True)
    assert with_rsi.evaluate(reentry(rsi14=40.0)).no_trade_reason == NoTradeReason.NO_SETUP
    assert with_rsi.evaluate(reentry(rsi14=30.0)).action == Action.BUY


# --- Contrats communs ----------------------------------------------------------------------------

def buy(**target) -> StrategyResult:
    return StrategyResult(strategy_id="X", strategy_version=1, action=Action.BUY, setup_time=T,
                          regime=MarketRegime(), entry_intent=EntryIntent(EntryMode.LIMIT, 100.0, 0, 2),
                          invalidation_reference=98.0, exit_policy_id="FIXED_SL_ONE_TP_V1", **target)


def test_buy_requires_exactly_one_target():
    with pytest.raises(ValueError, match="cible"):
        buy()
    with pytest.raises(ValueError, match="cible"):
        buy(target_r=2.0, target_price=104.0)


def test_price_target_is_rounded_down_and_must_exceed_entry():
    assert compute_levels(buy(target_price=103.019), Decimal("0.01")).targets == (Decimal("103.01"),)
    with pytest.raises(LevelError):
        compute_levels(buy(target_price=100.004), Decimal("0.01"))


def test_bollinger_convention_and_recent_min():
    mid, sigma = ind.bollinger(pd.Series([1.0, 2, 3, 4]), 4)
    assert mid.iloc[-1] == 2.5 and sigma.iloc[-1] == pytest.approx(np.sqrt(1.25))  # écart-type de population
    assert mid.iloc[:3].isna().all()
    assert ind.recent_min(pd.Series([5.0, 4, 6, 7]), 2).tolist()[1:] == [4.0, 4.0, 6.0]  # courante incluse


def test_previous_columns_and_btc_return_are_shifted_not_future():
    frame = setup_features(canonical(300), "15m", SetupFeatureParams())
    pd.testing.assert_series_equal(frame["prev_close"].iloc[1:], frame["close"].shift(1).iloc[1:],
                                   check_names=False)
    assert np.isnan(frame["prev_close"].iloc[0])
    from crypto_signal_intelligence.config import RegimeSection
    ctx = context_features(canonical(100, "1h"), RegimeSection())
    assert ctx["ret_24h"].iloc[:24].isna().all()
    assert ctx["ret_24h"].iloc[30] == pytest.approx(ctx["close"].iloc[30] / ctx["close"].iloc[6] - 1)


@pytest.mark.parametrize("strategy_id", ["EMA_PULLBACK_CONTINUATION", "RANGE_REENTRY"])
def test_new_strategies_are_causal(settings, strategy_id):
    inputs = {"setup": canonical(4000, seed=1), "context": canonical(1000, "1h", seed=2),
              "btc": canonical(1000, "1h", seed=3, symbol="BTCUSDT")}
    strategy = registry.build(strategy_id, settings.strategies)
    cuts = [inputs["setup"]["open_time"].iloc[i] for i in (1500, 2600, 3800)]
    results = check(settings, strategy, inputs, "ETHUSDT", cuts)
    assert all(r.ok for r in results), [(r.cut, r.feature_mismatches, r.decision_mismatches) for r in results]
