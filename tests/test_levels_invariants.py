"""Calculs critiques des niveaux (entrée, stop, TP, RR, bande d'écart) : invariants et cas d'erreur.

Les prix publiés doivent être sur la grille du tickSize Binance, jamais plus favorables que la règle
(entrée arrondie vers le haut, stop vers le bas), et le RR publié doit être recalculé après arrondi.
Quantité (stepSize) et minNotional : appliqués par BinanceSpotManager, pas ici.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.domain.enums import Action, EntryMode
from crypto_signal_intelligence.domain.market import EntryIntent, MarketRegime, StrategyResult
from crypto_signal_intelligence.levels.engine import LevelError, compute_levels, round_to_tick
from crypto_signal_intelligence.signals.schema import gross_rr

T = datetime(2024, 1, 1, 10, 15, tzinfo=UTC)
# Ordres de grandeur réels de l'univers : de 0,05 USDT (HBAR, TRX…) à 100 000 USDT (BTC).
TICKS = ["0.00001", "0.0001", "0.001", "0.01", "0.1", "0.01000000"]


def buy(reference: float, invalidation: float, *, premium_bps: float = 10, target_r: float | None = 2.0,
        target_price: float | None = None, with_intent: bool = True) -> StrategyResult:
    return StrategyResult(strategy_id="X", strategy_version=1, action=Action.BUY, setup_time=T,
                          regime=MarketRegime(),
                          entry_intent=EntryIntent(EntryMode.LIMIT, reference, premium_bps, 2) if with_intent else None,
                          invalidation_reference=invalidation, exit_policy_id="FIXED_SL_ONE_TP_V1",
                          target_r=target_r, target_price=target_price)


def on_grid(price: Decimal, tick: Decimal) -> bool:
    return price % tick == 0


def test_levels_invariants_over_many_prices_and_ticks():
    rng = np.random.default_rng(20260930)
    checked = 0
    for tick_text in TICKS:
        tick = Decimal(tick_text)
        for _ in range(400):
            reference = float(rng.uniform(1000, 100_000) * float(tick))       # 1 000 à 100 000 ticks
            stop_pct = float(rng.uniform(0.3, 8.0)) / 100
            premium = float(rng.choice([0, 5, 10, 25]))
            target_r = float(rng.choice([1.0, 1.5, 2.0, 3.0]))
            invalidation = reference * (1 - stop_pct)
            try:
                levels = compute_levels(buy(reference, invalidation, premium_bps=premium, target_r=target_r), tick)
            except LevelError:
                continue                                                      # refus explicite : jamais de niveau faux
            checked += 1
            ideal_entry = Decimal(str(reference)) * (1 + Decimal(str(premium)) / Decimal(10_000))
            entry, stop, target = levels.entry, levels.stop, levels.targets[0]
            assert all(on_grid(p, tick) for p in (entry, stop, target))
            assert ideal_entry <= entry < ideal_entry + tick                  # arrondi vers le haut, un tick au plus
            assert Decimal(str(invalidation)) - tick < stop <= Decimal(str(invalidation))   # vers le bas
            assert 0 < stop < entry < target
            assert target >= entry + Decimal(str(target_r)) * levels.risk     # jamais moins que le R déclaré
            assert levels.rr_gross[0] == gross_rr(entry, stop, target)        # recalculé APRÈS arrondi
            assert levels.risk_pct > 0 and levels.tick_size == tick
    assert checked > 2000


def test_round_to_tick_keeps_exact_multiples_and_rejects_bad_inputs():
    tick = Decimal("0.01")
    for price in (105.11, 0.07, 63000.0, Decimal("2769.26")):
        assert round_to_tick(price, tick, "up") == round_to_tick(price, tick, "down") == Decimal(str(price)).quantize(tick)
    assert round_to_tick(1.234, Decimal("0.5"), "down") == Decimal("1.0")
    assert round_to_tick(0.000123456, Decimal("0.00001000"), "up") == Decimal("0.00013000")
    for bad_tick in (Decimal(0), Decimal("-0.01")):
        with pytest.raises(LevelError):
            round_to_tick(1.0, bad_tick, "up")
    with pytest.raises(LevelError):
        round_to_tick(1.0, tick, "UP")                                        # faute de frappe : refus, pas un arrondi silencieux


@pytest.mark.parametrize("result", [
    pytest.param(buy(100.0, 100.5), id="stop au-dessus de l'entrée"),
    pytest.param(buy(100.0, 100.0, premium_bps=0), id="stop égal à l'entrée"),
    pytest.param(buy(0.05, 0.004), id="stop nul après arrondi"),
    pytest.param(buy(100.0, 98.0, target_r=None, target_price=99.0), id="cible en prix sous l'entrée"),
    pytest.param(buy(100.0, 98.0, target_r=None, target_price=100.1), id="cible égale à l'entrée après arrondi"),
])
def test_incoherent_levels_are_refused(result):
    with pytest.raises(LevelError):
        compute_levels(result, Decimal("0.01"))


@pytest.mark.parametrize("kwargs", [
    pytest.param({"target_r": None}, id="aucune cible"),
    pytest.param({"target_price": 104.0}, id="deux cibles"),
    pytest.param({"with_intent": False}, id="aucune intention d'entrée"),
])
def test_incomplete_buy_is_refused_before_any_level(kwargs):
    with pytest.raises((ValueError, LevelError)):
        compute_levels(buy(100.0, 98.0, **kwargs), Decimal("0.01"))


def test_price_target_is_rounded_down_never_embellished():
    levels = compute_levels(buy(100.0, 98.0, target_r=None, target_price=104.567), Decimal("0.01"))
    assert levels.targets[0] == Decimal("104.56")


def test_net_rr_falls_as_costs_rise_and_equals_gross_without_costs():
    levels = compute_levels(buy(100.0, 98.0), Decimal("0.01"))
    free = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
    ladder = [CostScenario(fee_bps=f, slippage_bps=s, half_spread_bps=h) for f, s, h in ((10, 2, 1), (10, 5, 3), (15, 10, 5))]
    assert levels.net_rr(free) == pytest.approx(float((levels.targets[0] - levels.entry) / levels.risk))
    values = [levels.net_rr(c) for c in ladder]
    assert values == sorted(values, reverse=True) and values[0] < levels.net_rr(free)


def test_published_deviation_covers_the_real_premium_and_is_capped():
    for reference, tick in ((1.239, "0.001"), (0.058251, "0.00001"), (63000.0, "0.01")):
        levels = compute_levels(buy(reference, reference * 0.98), Decimal(tick))
        real_bps = (levels.entry / levels.reference_price - 1) * 10_000
        band = levels.published_deviation_bps(25)
        assert real_bps + 25 <= band <= 500
    wide = compute_levels(buy(100.0, 90.0, premium_bps=480), Decimal("0.01"))
    assert wide.published_deviation_bps(25) == Decimal(500)
