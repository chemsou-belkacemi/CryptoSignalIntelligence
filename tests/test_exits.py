"""Politiques de sortie : TP partiels, stop remonté sans rétroactivité, TP au marché, absence de sortie temporelle."""
import pytest

from crypto_signal_intelligence.backtest import exits
from crypto_signal_intelligence.backtest.exits import OpenPosition, exit_policy, pnl_per_unit
from crypto_signal_intelligence.backtest.simulator import simulate

from .test_simulator import FLAT, OneShot, frame_from, rules


def position(policy="FIXED_SL_FOUR_TP_V1", targets=(102.0, 104.0), weights=(0.5, 0.5)):
    return OpenPosition(entry=100.0, initial_stop=98.0, targets=targets, weights=weights, policy=exit_policy(policy))


def bar(pos, o, h, low, c, *, first=False, touched=False, mc=0.0, timeout=False):
    pos.process_bar(o, h, low, c, first_bar=first, touched=touched, market_cost=mc, time_limit_reached=timeout)
    return pos


def test_partial_targets_fill_in_order_and_close_on_the_last():
    pos = bar(position(), 100, 103, 99.5, 102.5, first=True)
    assert not pos.closed and pos.remaining == 0.5 and pos.fills[0].target_index == 1
    bar(pos, 102.5, 105, 102, 104.5)
    assert pos.closed and pos.exit_reason == "TP" and [f.weight for f in pos.fills] == [0.5, 0.5]
    assert pnl_per_unit(pos.fills, 100.0, 0.0) == pytest.approx(3.0)      # 0,5 × 2 + 0,5 × 4
    gap = bar(position(), 105, 106, 104.5, 105)                           # ouverture au-dessus des deux TP
    assert gap.closed and [f.price for f in gap.fills] == [102.0, 104.0]  # au prix du TP, jamais mieux


def test_raised_stop_after_an_intrabar_tp_waits_for_the_next_bar_if_the_low_stays_above():
    pos = bar(position("BREAK_EVEN_AFTER_TP1_V1"), 100, 100.5, 99.5, 100, first=True)
    bar(pos, 101, 103, 100.5, 102.5)                                       # TP1 en cours de bougie, plus bas > 100
    assert not pos.closed and pos.stop == 98.0 and pos.pending_stop == 100.0 and not pos.ambiguous
    bar(pos, 102, 102.5, 99.9, 100)
    assert pos.closed and pos.exit_reason == "SL" and pos.fills[-1].price == 100.0 and pos.stop == 100.0
    assert pnl_per_unit(pos.fills, 100.0, 0.0) == pytest.approx(1.0)      # 0,5 × 2 + 0,5 × 0


def test_raised_stop_reached_in_the_tp_bar_exits_there_pessimistically():
    """Ordre du plus haut et du plus bas inconnu : le retour sous le nouveau stop a pu suivre le TP."""
    pos = bar(position("BREAK_EVEN_AFTER_TP1_V1"), 100, 100.5, 99.5, 100, first=True)
    bar(pos, 101, 103, 99.9, 102.5)
    assert pos.closed and pos.ambiguous and pos.exit_reason == "SL" and pos.fills[-1].price == 100.0
    assert [(f.reason, f.price) for f in pos.optimistic_fills] == [("TP", 102.0), ("TIMEOUT", 102.5)]
    fixed = bar(position("FIXED_SL_FOUR_TP_V1"), 100, 100.5, 99.5, 100, first=True)
    bar(fixed, 101, 103, 99.9, 102.5)                                      # stop fixe : rien ne change
    assert not fixed.closed and not fixed.ambiguous


def test_tp_taken_at_the_open_raises_the_stop_at_once():
    trail = position("TRAIL_PREVIOUS_TP_V1", targets=(102.0, 104.0, 106.0), weights=(1 / 3, 1 / 3, 1 / 3))
    bar(trail, 100, 100.5, 99.5, 100, first=True)
    bar(trail, 104.2, 104.5, 101.5, 102)                                   # TP1 et TP2 à l'ouverture, puis repli
    assert trail.closed and not trail.ambiguous and trail.fills[-1].reason == "SL" and trail.fills[-1].price == 102.0


def test_same_bar_stop_and_target_is_pessimistic_with_optimistic_bound():
    pos = bar(position(), 100, 105, 97, 100, first=True)
    assert pos.closed and pos.ambiguous and pos.exit_reason == "SL" and pos.fills[0].weight == 1.0
    optimistic = [(f.reason, f.weight) for f in pos.optimistic_fills]
    assert optimistic == [("TP", 0.5), ("TP", 0.5)]
    touched = bar(position(), 101, 103, 100.5, 102, first=True, touched=True)
    assert not touched.closed and touched.ambiguous and touched.optimistic_fills[0].reason == "TP"


def test_market_on_trigger_fills_on_contact_with_slippage_and_never_times_out():
    limit = bar(position("FIXED_SL_ONE_TP_V1", targets=(104.0,), weights=(1.0,)), 100, 104.0, 99.5, 103, mc=0.001)
    assert not limit.closed                                               # contact sans dépassement : pas de limite remplie
    market = bar(position("BSM_MARKET_TP_FIXED_SL_V1", targets=(104.0,), weights=(1.0,)), 100, 104.0, 99.5, 103,
                 mc=0.001)
    assert market.closed and market.fills[0].price == pytest.approx(104.0 * 0.999)
    assert not bar(position("BSM_MARKET_TP_FIXED_SL_V1"), *FLAT, timeout=True).closed
    assert bar(position("FIXED_SL_ONE_TP_V1"), *FLAT, timeout=True).exit_reason == "TIMEOUT"


def test_invalid_geometry_and_unknown_policy_are_refused():
    with pytest.raises(ValueError):
        position(targets=(104.0, 102.0))
    with pytest.raises(ValueError):
        position(weights=(0.6, 0.6))
    with pytest.raises(ValueError):
        exit_policy("INCONNUE")
    assert set(exits.EXIT_POLICIES) >= {"FIXED_SL_ONE_TP_V1", "BSM_MARKET_TP_FIXED_SL_V1"}


def test_simulator_honours_a_forced_consumer_profile():
    bars = [FLAT, FLAT, (100, 104.0, 99.8, 103), FLAT, FLAT, FLAT]
    theoretical = simulate(frame_from(bars), "TESTUSDT", OneShot({0}), rules(max_hold=3))
    assert theoretical.trades[0].exit_reason == "TIMEOUT"                 # 104 non dépassé strictement
    consumer = simulate(frame_from(bars), "TESTUSDT", OneShot({0}),
                        rules(max_hold=3, exit_policy_id="BSM_MARKET_TP_FIXED_SL_V1"))
    trade = consumer.trades[0]
    assert trade.exit_reason == "TP" and trade.r_multiple == pytest.approx(2.0) and trade.fills[0]["target"] == 1
    censored = simulate(frame_from([FLAT] * 6), "TESTUSDT", OneShot({0}),
                        rules(max_hold=3, exit_policy_id="BSM_MARKET_TP_FIXED_SL_V1"))
    assert censored.trades[0].exit_reason == "CENSORED"                   # aucune sortie temporelle


def test_policy_hash_covers_rules_but_not_description():
    from dataclasses import replace

    from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES
    policy = EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V1"]
    assert replace(policy, description="autre texte").policy_hash() == policy.policy_hash()
    assert replace(policy, stop_limit_offset_bps=50).policy_hash() != policy.policy_hash()
    assert replace(policy, time_exit=True).policy_hash() != policy.policy_hash()
    assert len({p.policy_hash() for p in EXIT_POLICIES.values()}) == len(EXIT_POLICIES)


def test_policy_hashes_are_frozen():
    """Changer une règle change l'empreinte : il faut alors une NOUVELLE version de politique (…_V2)."""
    from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES
    assert EXIT_POLICIES["FIXED_SL_ONE_TP_V1"].policy_hash() == "dc1c41c29a6f40a1"
    assert EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V1"].policy_hash() == "26367cfb1c063bb4"
    assert EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V2"].policy_hash() == "0c49448ca40d2a8f"
    assert EXIT_POLICIES["BSM_MARKET_TP_BREAK_EVEN_V2"].policy_hash() == "b34dee623d3b61ed"


def test_shared_policy_file_matches_the_registry():
    """config/exit_policies.json est le fichier lu par l'exécuteur : il ne doit pas dériver du code."""
    import json
    from pathlib import Path

    from crypto_signal_intelligence.backtest.exits import policies_document
    path = Path(__file__).resolve().parents[1] / "config" / "exit_policies.json"
    assert json.loads(path.read_text(encoding="utf-8")) == policies_document(), \
        "régénérer : python -m crypto_signal_intelligence exit-policies --write"


def test_bsm_v2_policies_simulate_like_v1_with_a_single_entry():
    """Les écarts V1 → V2 (quantité nette, stop franchi vendu au marché, break-even au prix moyen réel)
    ne changent pas la trajectoire simulée d'une entrée unique : seule la description devient exacte."""
    from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES, OpenPosition
    bars = [(100, 103.2, 99.6, 103), (103, 103.5, 99.9, 100.5), (100.5, 101, 99.0, 99.5)]
    fills = {}
    for pid in ("BSM_MARKET_TP_BREAK_EVEN_V1", "BSM_MARKET_TP_BREAK_EVEN_V2"):
        state = OpenPosition(entry=100.0, initial_stop=98.0, targets=(103.0, 106.0), weights=(0.5, 0.5),
                             policy=EXIT_POLICIES[pid])
        for k, (o, h, low, c) in enumerate(bars):
            state.process_bar(o, h, low, c, first_bar=k == 0, touched=False, market_cost=0.0005,
                              time_limit_reached=False)
        fills[pid] = [(f.reason, round(f.price, 6), f.weight) for f in state.fills]
    assert fills["BSM_MARKET_TP_BREAK_EVEN_V1"] == fills["BSM_MARKET_TP_BREAK_EVEN_V2"]
    assert [r for r, *_ in fills["BSM_MARKET_TP_BREAK_EVEN_V2"]] == ["TP", "SL"]   # stop remonté à l'entrée
