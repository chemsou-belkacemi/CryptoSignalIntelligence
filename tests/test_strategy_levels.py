from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.domain.enums import Action, NoTradeReason, TrendRegime, VolatilityRegime
from crypto_signal_intelligence.domain.market import MarketContext, MarketRegime, frozen_mapping
from crypto_signal_intelligence.levels.engine import LevelError, compute_levels, round_to_tick
from crypto_signal_intelligence.signals.schema import gross_rr
from crypto_signal_intelligence.strategies import registry
from crypto_signal_intelligence.validation import gates

T = datetime(2024, 1, 1, 10, 15, tzinfo=UTC)


def context(close=105.0, level=104.8, volume_ratio=2.0, atr=1.0, trend=TrendRegime.BULL, **overrides):
    values = {"open": 104, "high": close, "low": 103.9, "close": close, "volume": 10, "atr14": atr,
              "donchian_high": level, "volume_ratio": volume_ratio, "volume_ref": 5}
    fields = dict(symbol="ETHUSDT", setup_timeframe="15m", decision_time=T, available_at=T,
                  setup=frozen_mapping(values), context=frozen_mapping({}), btc=frozen_mapping({}),
                  regime=MarketRegime(trend=trend, volatility=VolatilityRegime.NORMAL), bars_available=1000,
                  data_gap_recent=False, context_available_at=T)
    fields.update(overrides)
    return MarketContext(**fields)


def test_context_is_immutable():
    ctx = context()
    with pytest.raises(TypeError):
        ctx.setup["close"] = 1  # type: ignore[index]


def test_donchian_buy_and_filters(settings):
    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    buy = strategy.evaluate(context())
    assert buy.action == Action.BUY and buy.invalidation_reference == pytest.approx(105 - 1.5)
    assert strategy.evaluate(context(volume_ratio=1.2)).no_trade_reason == NoTradeReason.NO_SETUP
    assert strategy.evaluate(context(close=104.0)).no_trade_reason == NoTradeReason.NO_SETUP
    assert strategy.evaluate(context(trend=TrendRegime.RANGE)).no_trade_reason == NoTradeReason.NO_SETUP
    assert strategy.evaluate(context(trend=TrendRegime.UNKNOWN)).no_trade_reason == NoTradeReason.UNKNOWN_REGIME
    late = strategy.evaluate(context(close=106.0, level=104.8))
    assert late.no_trade_reason == NoTradeReason.NO_SETUP and "tardive" in late.reasons[0]
    assert strategy.evaluate(context(volume_ratio=float("nan"))).no_trade_reason == NoTradeReason.INSUFFICIENT_HISTORY
    ablated = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies, use_volume_filter=False)
    assert ablated.evaluate(context(volume_ratio=1.0)).action == Action.BUY


def test_gross_rr_examples_from_specification():
    rr = [gross_rr(Decimal(100), Decimal(98), Decimal(t)) for t in (102, 103, 104, 106)]
    assert rr == [Decimal("1.000"), Decimal("1.500"), Decimal("2.000"), Decimal("3.000")]
    assert gross_rr(Decimal("123456.10"), Decimal("122500"), Decimal("124000")) == Decimal("0.569")


def test_levels_are_rounded_then_rr_recomputed(settings):
    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    levels = compute_levels(strategy.evaluate(context(close=105.004)), Decimal("0.01"))
    assert levels.entry == Decimal("105.11")            # 105.004 × 1.001 arrondi au tick supérieur
    assert levels.stop == Decimal("103.50")             # 103.504 arrondi au tick inférieur
    assert levels.rr_gross[0] == gross_rr(levels.entry, levels.stop, levels.targets[0])
    assert levels.targets[0] == levels.entry + 2 * levels.risk
    assert round_to_tick(1.234, Decimal("0.5"), "up") == Decimal("1.5")
    with pytest.raises(LevelError):
        round_to_tick(1, Decimal(0), "up")


def test_net_rr_accounts_for_costs_and_veto(settings):
    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    levels = compute_levels(strategy.evaluate(context(atr=0.05, close=104.82)), Decimal("0.01"))
    central = settings.costs["central"]
    assert levels.net_rr(central) < float(levels.rr_gross[0])
    assert gates.post_levels(levels, central, 1.2)[0] == NoTradeReason.POOR_NET_PROFILE
    assert levels.net_rr(CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)) == pytest.approx(
        float(levels.rr_gross[0]), rel=1e-6)


def test_pre_decision_vetoes():
    from datetime import timedelta
    base = dict(now=T, setup_interval=timedelta(minutes=15), max_staleness_bars=2, warmup_bars=250,
                requires_context=True)
    assert gates.pre_decision(context(), **base) is None
    assert gates.pre_decision(context(), **(base | {"now": T + timedelta(hours=1)}))[0] == NoTradeReason.STALE_DATA
    assert gates.pre_decision(context(bars_available=10), **base)[0] == NoTradeReason.INSUFFICIENT_HISTORY
    assert gates.pre_decision(context(data_gap_recent=True), **base)[0] == NoTradeReason.DATA_GAP
    assert gates.pre_decision(context(context_available_at=None), **base)[0] == \
        NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE


def test_published_band_accepts_the_price_at_publication():
    """Défaut corrigé : avec une prime de 10 pb arrondie au tick supérieur, l'écart RÉEL référence → ENTRY_1
    dépasse 10 pb (ex. 1,239 → 1,241 ≈ 16 pb). Le consommateur compare |prix/ENTRY_1 − 1| à la bande
    publiée : au moment de la publication (prix ≈ référence), le signal doit être accepté."""
    from crypto_signal_intelligence.domain.enums import EntryMode
    from crypto_signal_intelligence.domain.market import EntryIntent, StrategyResult
    cases = [(1.239, "0.001"), (0.27777, "0.0001"), (2769.26, "0.01"), (0.058251, "0.00001"), (63000.0, "0.01")]
    for reference, tick in cases:
        result = StrategyResult(strategy_id="X", strategy_version=1, action=Action.BUY, setup_time=T,
                                regime=MarketRegime(), entry_intent=EntryIntent(EntryMode.LIMIT, reference, 10, 2),
                                invalidation_reference=reference * 0.98, exit_policy_id="FIXED_SL_ONE_TP_V1",
                                target_r=2.0)
        levels = compute_levels(result, Decimal(tick))
        band = levels.published_deviation_bps(25)
        gap = abs(Decimal(str(reference)) / levels.entry - 1) * 10_000
        assert gap <= band - 25, (reference, tick, gap, band)      # la tolérance reste entière pour le marché
        assert band <= 500
    example = compute_levels(StrategyResult(strategy_id="X", strategy_version=1, action=Action.BUY, setup_time=T,
                                            regime=MarketRegime(), entry_intent=EntryIntent(EntryMode.LIMIT, 1.239, 10, 2),
                                            invalidation_reference=1.222, exit_policy_id="FIXED_SL_ONE_TP_V1",
                                            target_r=2.0), Decimal("0.001"))
    assert example.entry == Decimal("1.241") and example.published_deviation_bps(25) == Decimal(42)
