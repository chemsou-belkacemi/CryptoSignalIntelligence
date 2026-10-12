"""Scénarios de remplissage construits à la main (valeurs synthétiques)."""
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from crypto_signal_intelligence.backtest.simulator import SimulationRules, simulate
from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.domain.enums import Action, EntryMode, NoTradeReason
from crypto_signal_intelligence.domain.market import EntryIntent, StrategyResult
from crypto_signal_intelligence.features.builder import SetupFeatureParams
from crypto_signal_intelligence.features.context import CTX_NUMERIC
from crypto_signal_intelligence.strategies.base import Strategy, StrategyParams

FREE = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)


class OneShot(Strategy):
    """Achète à la clôture des bougies listées : référence 100, stop 98, cible 2R."""
    strategy_id = "TEST_ONE_SHOT"
    params_model = StrategyParams
    setup_keys = ()

    def __init__(self, buy_at: set[int], expiry: int = 2, deviation_bps: float = 0):
        super().__init__(StrategyParams(version=1))
        self.buy_at, self.expiry, self.deviation = buy_at, expiry, deviation_bps
        self.calls = 0

    @property
    def warmup_bars(self) -> int:
        return 0

    def feature_params(self, gap_block_bars):
        return SetupFeatureParams()

    def evaluate(self, context):
        index = self.calls
        self.calls += 1
        if index in self.buy_at:
            return StrategyResult(strategy_id=self.strategy_id, strategy_version=1, action=Action.BUY,
                                  setup_time=context.decision_time, regime=context.regime,
                                  entry_intent=EntryIntent(EntryMode.LIMIT, 100.0, self.deviation, self.expiry),
                                  invalidation_reference=98.0, exit_policy_id="FIXED_SL_ONE_TP_V1", target_r=2.0)
        return StrategyResult.no_trade(self.strategy_id, 1, context.decision_time, context.regime,
                                       NoTradeReason.NO_SETUP)


def frame_from(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    n = len(bars)
    open_time = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    o, h, low, c = map(list, zip(*bars, strict=True))
    frame = pd.DataFrame({"open_time": open_time, "decision_time": open_time + timedelta(minutes=15),
                          "available_at": open_time + timedelta(minutes=15, seconds=2),
                          "open": o, "high": h, "low": low, "close": c, "volume": 1.0, "atr14": 1.0,
                          "bars_available": range(1, n + 1), "data_gap_recent": False})
    for prefix in ("ctx_", "btc_"):
        for column in CTX_NUMERIC:
            frame[prefix + column] = 1.0
    frame["ctx_available_at"] = frame["available_at"]
    frame["ctx_trend"], frame["ctx_volatility"] = "BULL", "NORMAL"
    frame["ctx_liquidity"], frame["ctx_transition"] = "ACCEPTABLE", "STABLE"
    return frame


def rules(costs=FREE, max_hold=50, exit_policy_id=None):
    return SimulationRules(costs=costs, max_hold_bars=max_hold, min_net_rr=0, tick_size=Decimal("0.01"),
                           setup_interval=timedelta(minutes=15), requires_context=False,
                           exit_policy_id=exit_policy_id)


FLAT = (100, 100.5, 99.5, 100)


def run(bars, buy_at=frozenset({0}), **kwargs):
    strategy_kwargs = {k: kwargs.pop(k) for k in ("expiry", "deviation_bps") if k in kwargs}
    return simulate(frame_from(bars), "TESTUSDT", OneShot(set(buy_at), **strategy_kwargs), rules(**kwargs))


def test_entry_never_on_decision_bar_and_tp_hit():
    # Bougie 0 : décision. Bougie 1 : ouverture 100 <= limite 100 → remplie. Bougie 2 : high 104.5 > TP 104.
    result = run([FLAT, FLAT, (100, 104.5, 99.8, 104)])
    trade = result.trades[0]
    assert trade.entry_status == "FILLED_OPEN" and trade.entry_time == pd.Timestamp("2024-01-01 00:15", tz="UTC")
    assert trade.exit_reason == "TP" and trade.r_multiple == pytest.approx(2.0)


def test_limit_touch_without_penetration_does_not_fill():
    # Ouvertures au-dessus de la limite ; le low touche 100 exactement : pas de remplissage garanti.
    result = run([FLAT, (101, 102, 100, 101), (101, 102, 100, 101), (101, 102, 100.2, 101)], expiry=2)
    assert result.trades[0].entry_status == "EXPIRED"


def test_first_tp_touched_before_any_fill_cancels_the_entry_like_bsm():
    """CANCEL_AT_ENTRY_EXPIRY_OR_FIRST_TP (chasse aux erreurs du 2026-10-12) : TP1 (104) touché avant tout achat
    annule l'entrée, même si le prix revient ensuite à la limite ; une bougie qui touche TP1 ET la limite sans
    ouvrir dessous est aussi une annulation (ordre inconnu) ; ouverture sous la limite : remplie d'abord."""
    up, dip = (101, 101.5, 100.5, 101), (100.5, 100.8, 99.0, 100.2)
    later = run([FLAT, (101, 104.5, 100.5, 103), dip, FLAT], expiry=3).trades[0]
    assert later.entry_status == "CANCELLED_FIRST_TP" and later.entry_time is None
    same_bar = run([FLAT, (101, 105, 99.9, 101), FLAT, (100, 100.2, 97, 97)])
    assert same_bar.trades[0].entry_status == "CANCELLED_FIRST_TP"
    from crypto_signal_intelligence.backtest.metrics import summarize
    from crypto_signal_intelligence.backtest.simulator import trades_frame
    summary = summarize(trades_frame(same_bar), evaluated_bars=4, bars_in_position=0, candidates=1, no_trade={},
                        bootstrap_block_days=1, bootstrap_samples=10, seed=1)
    assert summary["entries_cancelled_first_tp"] == 1 and summary["entries_filled"] == 0
    opened = run([FLAT, (99.9, 104.5, 99.5, 104), up]).trades[0]
    assert opened.entry_status == "FILLED_OPEN"


def test_touch_fill_with_stop_in_same_bar_counts_the_stop():
    trade = run([FLAT, (101, 102, 97, 98.5), FLAT]).trades[0]
    assert trade.entry_status == "FILLED_TOUCH" and trade.exit_reason == "SL" and trade.r_multiple == pytest.approx(-1)


def test_same_bar_tp_and_sl_is_pessimistic_and_flagged():
    result = run([FLAT, FLAT, (100, 105, 97, 100)])
    trade = result.trades[0]
    assert trade.ambiguous and trade.exit_reason == "SL" and trade.optimistic_exit_reason == "TP"


def test_gap_through_stop_fills_at_open_not_at_stop():
    result = run([FLAT, FLAT, (95, 96, 94, 95)], costs=CostScenario(fee_bps=0, slippage_bps=10, half_spread_bps=0))
    trade = result.trades[0]
    assert trade.exit_reason == "SL_GAP" and trade.exit_price == pytest.approx(95 * 0.999)
    assert trade.r_multiple < -2


def test_open_at_or_below_the_stop_exits_at_once_even_if_slippage_lifts_the_fill_above_it():
    """Ouverture 97,95 ≤ stop 98 < remplissage 98,05 : le stop-market part aussitôt, à l'ouverture
    moins le coût, jamais « au prix du stop » (deux traversées du spread)."""
    costs = CostScenario(fee_bps=0, slippage_bps=10, half_spread_bps=0)
    trade = run([FLAT, (97.95, 99, 97.5, 98.5)], costs=costs).trades[0]
    assert trade.entry_price == pytest.approx(97.95 * 1.001) and trade.entry_price > trade.stop
    assert trade.exit_reason == "SL_GAP" and trade.exit_price == pytest.approx(97.95 * 0.999)


def test_fees_and_slippage_are_charged_on_both_fills():
    costs = CostScenario(fee_bps=10, slippage_bps=5, half_spread_bps=0)
    result = run([FLAT, (99, 100, 99, 99.5), (99.5, 104.5, 99.2, 104)], costs=costs, deviation_bps=0)
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(99 * 1.0005)
    expected_pnl = 104 * 0.999 - trade.entry_price * 1.001
    # R rapporté au risque PRÉVU (limite − stop), pas au risque réalisé depuis un remplissage plus bas.
    assert trade.r_multiple == pytest.approx(expected_pnl / (trade.entry_limit - trade.stop))


def test_timeout_and_censoring():
    timeout = run([FLAT] * 6, max_hold=3)
    assert timeout.trades[0].exit_reason == "TIMEOUT" and timeout.trades[0].bars_held == 3
    censored = run([FLAT] * 4)
    assert censored.trades[0].exit_reason == "CENSORED"


def test_extra_entry_delay_and_single_active_setup():
    costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=1)
    result = run([FLAT, (99, 99.5, 98.5, 99), FLAT, FLAT], buy_at={0, 1}, costs=costs)
    trade = result.trades[0]
    assert trade.entry_time == pd.Timestamp("2024-01-01 00:30", tz="UTC")  # bougie 2, pas 1
    assert result.no_trade["DUPLICATE"] == 1 and len(result.trades) == 1


def test_entry_delay_shortens_the_entry_window_instead_of_shifting_it():
    """ENTRY_EXPIRES_AT est fixé à la décision (bougies k+1 à k+N) : avec un retard d'une bougie, il reste N − 1
    bougies, jamais une bougie après l'expiration (chasse aux erreurs du 2026-10-12)."""
    up, dip = (101, 101.5, 100.5, 101), (100.5, 100.8, 99.0, 100.2)
    for delay in (0, 1):
        costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=delay)
        trade = run([up, up, up, dip, up, up], costs=costs, expiry=2).trades[0]
        assert trade.entry_status == "EXPIRED"                     # le creux de la bougie 3 est hors fenêtre
    costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=2)
    assert run([up, dip, dip, up, up], costs=costs, expiry=2).trades[0].entry_status == "EXPIRED"


def test_the_close_of_a_gap_fill_under_the_stop_is_still_evaluated():
    gap = (97, 97.5, 96.5, 97)
    result = run([FLAT, gap, FLAT, FLAT], buy_at={0, 1})
    assert result.trades[0].exit_reason == "SL_GAP" and result.evaluated_bars == 4   # 3 avant la correction
    # la décision de la bougie 1 est évaluée ; son signal serait DUPLICATE en direct (celui de la bougie 0 vit
    # jusqu'à ENTRY_EXPIRES_AT, bougie 0 + 2)
    assert result.no_trade["DUPLICATE"] == 1 and len(result.trades) == 1


def test_a_pair_stays_duplicate_until_entry_expiry_even_after_a_cancellation_like_live():
    """Relecture du 2026-10-12 : en direct (analyze, outbox), la paire reste DUPLICATE jusqu'à ENTRY_EXPIRES_AT,
    même si l'entrée est annulée (TP1 avant l'achat) entre-temps ; ensuite, un nouveau setup est accepté."""
    tp = (101, 104.5, 100.5, 103)
    blocked = run([FLAT, tp, FLAT, FLAT, FLAT], buy_at={0, 1}, expiry=2)
    assert blocked.trades[0].entry_status == "CANCELLED_FIRST_TP" and blocked.no_trade["DUPLICATE"] == 1
    assert len(blocked.trades) == 1
    after = run([FLAT, tp, FLAT, FLAT, FLAT], buy_at={0, 2}, expiry=2)
    assert len(after.trades) == 2 and after.no_trade.get("DUPLICATE", 0) == 0


def test_an_entry_delay_longer_than_the_window_expires_without_blocking_the_pair_beyond_it():
    up = (101, 101.5, 100.5, 101)
    costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=3)
    result = run([up, up, up, up, up, up], buy_at={0, 2}, costs=costs, expiry=2)
    assert [t.entry_status for t in result.trades][:1] == ["EXPIRED"] and result.no_trade.get("DUPLICATE", 0) == 0


def test_period_end_truncates_future_prices():
    bars = [FLAT, FLAT, FLAT, (100, 104.5, 99.8, 104)]
    frame = frame_from(bars)
    end = frame["decision_time"].iloc[2]
    result = simulate(frame, "TESTUSDT", OneShot({0}), rules(), end=end)
    assert result.trades[0].exit_reason == "CENSORED"  # le TP de la bougie 3 est hors période


def test_confidence_interval_uses_day_blocks_and_brackets_the_mean():
    """Trades d'un même jour fortement corrélés : l'IC par blocs de jours est plus large qu'en les
    supposant indépendants, et il encadre la moyenne affichée."""
    import numpy as np

    from crypto_signal_intelligence.backtest.metrics import day_block_ci95
    rng = np.random.default_rng(2)
    days = pd.date_range("2023-01-01", periods=300, freq="D", tz="UTC")
    shocks = rng.normal(0, 1, len(days))
    times = days.repeat(8)
    values = np.repeat(shocks, 8) + rng.normal(0, 0.1, len(times))
    (low, high), blocks = day_block_ci95(values, times.to_numpy(), block_days=10, samples=2000, seed=1)
    assert blocks == 30 and low < values.mean() < high
    naive = 1.96 * values.std() / np.sqrt(len(values))
    assert (high - low) / 2 > 2 * naive


def test_rr_veto_uses_the_live_costs_whatever_the_simulated_scenario():
    """Le veto « RR net » écarte les MÊMES signaux qu'en service (coûts centraux) ; seul le résultat paie les coûts du
    scénario. Avant le 2026-10-02, un scénario cher écartait d'autres signaux et ne mesurait plus les mêmes décisions."""
    from crypto_signal_intelligence.backtest.simulator import SimulationRules, simulate
    from crypto_signal_intelligence.config import CostScenario
    dear = CostScenario(fee_bps=150, slippage_bps=50, half_spread_bps=50)      # RR net sous 1,5 avec ces coûts
    bars = [FLAT, FLAT, (100, 104.5, 99.8, 104)]

    def with_veto(veto_costs):
        rules = SimulationRules(costs=dear, max_hold_bars=50, min_net_rr=1.5, tick_size=Decimal("0.01"),
                                setup_interval=timedelta(minutes=15), requires_context=False, veto_costs=veto_costs)
        return simulate(frame_from(bars), "TESTUSDT", OneShot({0}), rules)

    old = with_veto(None)                     # ancien comportement : veto aux coûts du scénario
    assert not old.trades and sum(old.no_trade.values()) >= 1
    new = with_veto(FREE)                     # veto aux coûts du service
    assert len(new.trades) == 1 and new.trades[0].exit_reason == "TP"
    assert new.trades[0].r_multiple < 2.0     # le résultat, lui, paie bien les coûts du scénario


def test_research_runs_veto_with_central_costs_in_every_scenario(settings):
    from crypto_signal_intelligence.research.backtest_run import simulation_rules
    from crypto_signal_intelligence.strategies import registry
    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    for scenario in settings.costs:
        rules = simulation_rules(settings, strategy, "BTCUSDT", scenario)
        assert rules.costs == settings.costs[scenario] and rules.veto_costs == settings.costs["central"]
