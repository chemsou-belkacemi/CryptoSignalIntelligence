"""Retour d'exécution : contrat strict, importation dédoublonnée, état par signal, rapport (synthétique)."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.feedback.reconcile import execution_report, execution_state
from crypto_signal_intelligence.feedback.schema import parse_line
from crypto_signal_intelligence.feedback.store import FeedbackStore
from crypto_signal_intelligence.signals.outbox import SignalRegistry
from crypto_signal_intelligence.signals.txt import parse

from .conftest import canonical
from .test_signals import SPEC_EXAMPLE

NOW = datetime(2026, 9, 30, tzinfo=UTC)


POLICY_HASH = parse(SPEC_EXAMPLE).exit_policy_hash


def event(**overrides) -> str:
    base = {"event_id": "E1", "signal_id": "EXAMPLE_ONLY_001", "event_type": "RECEIVED",
            "occurred_at": "2026-09-29T12:00:10Z", "environment": "DEMO", "producer": "BinanceSpotManager",
            "symbol": "TESTUSDT"}
    if overrides.get("event_type", "RECEIVED") == "RECEIVED":
        base["exit_policy_hash"] = POLICY_HASH
    return json.dumps(base | overrides)


def closing_sequence() -> list[str]:
    """Achat 2 @ 100, TP1 1 @ 102, stop 1 @ 98, clôture : PnL −0,40 USDT de frais, risque prévu 2 × 2 = 4."""
    return [
        event(),
        event(event_id="E2", event_type="ENTRY_FILLED", occurred_at="2026-09-29T12:01:00Z", quantity="2",
              price="100", fee="0.2", fee_asset="USDT"),
        event(event_id="E3", event_type="TP_FILLED", occurred_at="2026-09-29T13:00:00Z", quantity="1", price="102",
              target_index=1, fee="0.102", fee_asset="USDT"),
        event(event_id="E4", event_type="STOP_FILLED", occurred_at="2026-09-29T14:00:00Z", quantity="1", price="98",
              fee="0.098", fee_asset="USDT"),
        event(event_id="E5", event_type="CLOSED", occurred_at="2026-09-29T14:00:01Z"),
    ]


def test_event_contract_is_strict():
    assert parse_line(event()).event_type == "RECEIVED"
    fill = parse_line(event(event_id="E2", event_type="ENTRY_FILLED", quantity="1", price="100", fee="0.1",
                            fee_asset="USDT"))
    assert fill.quantity == Decimal("1") and fill.occurred_at.tzinfo is not None
    for bad in [event(event_type="BOUGHT"), event(event_type="ENTRY_FILLED"),
                event(event_type="TP_FILLED", quantity="1", price="102"), event(event_type="REJECTED"),
                event(environment="LIVE"), event(occurred_at="2026-09-29T12:00:10"), event(extra="x"),
                event(fee="0.1"), event(quantity="NaN", event_type="ENTRY_FILLED", price="1"), "{pas du json", "[]"]:
        with pytest.raises(ValueError):
            parse_line(bad)


def test_import_deduplicates_and_reports_invalid_and_unknown(tmp_path):
    lines = [event(), event(event_id="E2", event_type="ENTRY_FILLED", quantity="1", price="100"), event(), "{cassé",
             event(event_id="E3", signal_id="INCONNU_1")]
    path = tmp_path / "feedback.jsonl"
    path.write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    store = FeedbackStore(tmp_path / "feedback.sqlite3")
    summary = store.import_jsonl(path, now=NOW, known_signal_ids={"EXAMPLE_ONLY_001"})
    assert (summary.lines, summary.imported, summary.duplicates) == (5, 3, 1)
    assert summary.unknown_signals == ["INCONNU_1"] and summary.invalid[0][0] == 4
    again = store.import_jsonl(path, now=NOW, known_signal_ids={"EXAMPLE_ONLY_001"})
    assert (again.imported, again.duplicates) == (0, 4)      # E1 figure deux fois dans le fichier
    assert [e.event_type for e in store.events_for("EXAMPLE_ONLY_001")] == ["RECEIVED", "ENTRY_FILLED"]
    assert store.signal_ids() == ["EXAMPLE_ONLY_001", "INCONNU_1"]


def test_execution_state_reconstructs_pnl_in_quote_and_r():
    signal = parse(SPEC_EXAMPLE)                      # entrée 100, stop 98
    events = [parse_line(line) for line in closing_sequence()]
    state = execution_state(signal, events)
    assert state.status == "CLOSED" and state.tp_fills == 1 and state.stop_filled and state.events == 5
    assert state.realized_pnl_quote == Decimal("-0.4") and state.realized_r == pytest.approx(-0.1)
    assert execution_state(signal, events[:2]).status == "OPEN"
    assert execution_state(signal, events[:1]).status == "RECEIVED"
    assert execution_state(signal, []).status == "UNKNOWN"      # sans confirmation : ni refus ni perte
    rejected = parse_line(event(event_id="R", event_type="REJECTED", reason="expiré à réception"))
    assert execution_state(signal, [rejected]).status == "REJECTED"
    partial = parse_line(event(event_id="P", event_type="ENTRY_PARTIAL", quantity="0.5", price="100"))
    assert execution_state(signal, [partial]).status == "ENTRY_PARTIAL"
    bnb_fee = parse_line(event(event_id="B", event_type="ENTRY_FILLED", quantity="1", price="100", fee="0.001",
                               fee_asset="BNB"))
    assert execution_state(signal, [bnb_fee]).fees_other == {"BNB": Decimal("0.001")}


def test_execution_report_separates_theoretical_shadow_and_demo(settings, tmp_path):
    signal = parse(SPEC_EXAMPLE)
    SignalRegistry(settings.signals_db, settings.publication_dir()).publish(
        signal, datetime(2026, 9, 29, 12, 0, 5, tzinfo=UTC))
    row = execution_report(settings)[0]
    assert (row.publication, row.channel, row.theoretical_outcome, row.demo_status) == ("PUBLISHED", "shadow", "NO_DATA",
                                                                                   "NOT_CONSUMED")
    CandleStore(settings.data_dir).save(canonical(300, symbol="TESTUSDT", start="2026-09-29 12:00"), "TESTUSDT", "15m")
    row = execution_report(settings)[0]
    assert row.theoretical_outcome in {"TP", "SL", "SL_GAP", "TIMEOUT", "UNFILLED", "PENDING", "OPEN"}
    path = tmp_path / "feedback.jsonl"
    path.write_text("\n".join(closing_sequence()), encoding="utf-8")
    FeedbackStore(settings.feedback_db).import_jsonl(path, now=NOW, known_signal_ids={signal.signal_id})
    row = execution_report(settings)[0]
    assert row.demo_status == "CLOSED" and row.demo_r == pytest.approx(-0.1) and row.demo_events == 5


def test_feedback_v2_distinguishes_received_order_and_fill():
    placed = event(event_id="O1", event_type="ORDER_PLACED", occurred_at="2026-09-29T12:00:20Z", quantity="2",
                   price="100", order_id="123")
    for bad in [event(exit_policy_hash=None),                                    # RECEIVED sans empreinte
                event(event_id="O", event_type="ORDER_PLACED", quantity="1", price="100"),   # sans order_id
                event(event_id="M", event_type="MARKET_EXIT_FILLED", quantity="1", price="99"),  # sans motif
                event(event_id="X", event_type="CLOSED", exit_policy_hash=POLICY_HASH)]:
        with pytest.raises(ValueError):
            parse_line(bad)
    signal = parse(SPEC_EXAMPLE)
    events = [parse_line(event()), parse_line(placed)]
    state = execution_state(signal, events)
    assert state.status == "ORDER_PLACED" and state.ordered_qty == 2 and state.consumer_policy_hash == POLICY_HASH
    partial = parse_line(event(event_id="P", event_type="ENTRY_FILLED", occurred_at="2026-09-29T12:05:00Z",
                               quantity="1", price="100"))
    market = parse_line(event(event_id="M", event_type="MARKET_EXIT_FILLED", occurred_at="2026-09-29T13:00:00Z",
                              quantity="1", price="99", reason="stop refusé, vente au marché", fee="0.099",
                              fee_asset="USDT"))
    state = execution_state(signal, [*events, partial, market])
    assert state.status == "CLOSED" and state.market_exits == 1 and state.fills_without_fee == 1


def test_deviations_explain_delay_price_size_policy_and_fees():
    from datetime import timedelta

    from crypto_signal_intelligence.feedback.reconcile import Prospective, deviations
    signal = parse(SPEC_EXAMPLE)
    events = [parse_line(event(exit_policy_hash="0123456789abcdef")),
              parse_line(event(event_id="O1", event_type="ORDER_PLACED", quantity="2", price="100", order_id="1")),
              parse_line(event(event_id="F1", event_type="ENTRY_FILLED", occurred_at="2026-09-29T12:31:00Z",
                               quantity="1", price="100.1"))]
    state = execution_state(signal, events)
    theory = Prospective("TP", 1.0, signal.decision_at + timedelta(minutes=15), 100.0)
    notes = " | ".join(deviations(signal, "outbox", theory, state))
    for expected in ("POLITIQUE_DIFFÉRENTE", "RÉCEPTION +5 s", "ENTRÉE +16 min", "PRIX_ENTRÉE +10.0 pb",
                     "TAILLE 50%", "FRAIS_INCONNUS"):
        assert expected in notes, (expected, notes)
    missed = execution_state(signal, [parse_line(event(event_id="E", event_type="EXPIRED"))])
    assert any("ENTRÉE_MANQUÉE" in n for n in deviations(signal, "outbox", theory, missed))


def test_real_fee_rate_is_compared_to_the_central_assumption():
    from datetime import timedelta

    from crypto_signal_intelligence.feedback.reconcile import Prospective, deviations, fee_rate_bps
    signal = parse(SPEC_EXAMPLE)
    theory = Prospective("TP", 1.0, signal.decision_at + timedelta(minutes=15), 100.0)

    def filled(fee: str, asset: str) -> object:
        return execution_state(signal, [parse_line(event(event_id="F1", event_type="ENTRY_FILLED", quantity="1", price="100",
                                                         fee=fee, fee_asset=asset))])

    with_bnb_rate = filled("0.075", "USDT")                                  # 0,075 USDT sur 100 USDT = 7,5 pb
    assert fee_rate_bps(with_bnb_rate) == pytest.approx(7.5)
    assert not any("FRAIS" in n for n in deviations(signal, "outbox", theory, with_bnb_rate, assumed_fee_bps=7.5))
    without = filled("0.1", "USDT")                                           # 10 pb : remise absente
    assert any("FRAIS 10.00 pb par ordre vs 7.5 pb supposés" in n for n in deviations(signal, "outbox", theory, without, assumed_fee_bps=7.5))
    in_bnb = filled("0.0001", "BNB")
    assert fee_rate_bps(in_bnb) is None
    assert any(n.startswith("FRAIS_EN_BNB 0.0001") for n in deviations(signal, "outbox", theory, in_bnb, assumed_fee_bps=7.5))


def test_prospective_replay_follows_the_signal_policy():
    """Même trajectoire : la politique BSM (sans sortie temporelle) reste ouverte, la théorique sort au temps."""
    from datetime import timedelta

    import pandas as pd

    from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES
    from crypto_signal_intelligence.config import CostScenario
    from crypto_signal_intelligence.feedback.reconcile import replay_signal
    from tests.test_signals import one_tp_signal
    costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=0)
    start = pd.Timestamp("2026-09-29T12:00:00Z")
    n = 200                                                   # 50 h de bougies 15m plates sous le TP
    bars = pd.DataFrame({"open_time": [start + pd.Timedelta(minutes=15 * i) for i in range(n)],
                         "open": [2500.0] * n, "high": [2510.0] * n, "low": [2495.0] * n, "close": [2505.0] * n})
    theoretical = one_tp_signal()                             # FIXED_SL_ONE_TP_V1, MAX_HOLD_MINUTES=1440
    result = replay_signal(bars, theoretical, costs, timedelta(minutes=15))
    assert result.outcome == "TIMEOUT" and result.fill_price == 2500.0
    bsm_policy = EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V1"]
    consumer = one_tp_signal(exit_policy_id=bsm_policy.policy_id, exit_policy_hash=bsm_policy.policy_hash(),
                             max_hold_minutes=None)
    assert replay_signal(bars, consumer, costs, timedelta(minutes=15)).outcome == "OPEN"
    never = bars.assign(open=2600.0, high=2610.0, low=2590.0, close=2600.0)
    assert replay_signal(never, theoretical, costs, timedelta(minutes=15)).outcome == "UNFILLED"


def test_prospective_entry_window_matches_the_simulator_bar_count():
    """Décision 12:00, ENTRY_EXPIRES_AT 12:30:02 : deux bougies d'entrée (12:00 et 12:15), comme le
    simulateur (`expires_after_bars` = 2) ; la bougie 12:30, qui clôture après l'expiration, n'en est pas."""
    from datetime import timedelta

    import pandas as pd

    from crypto_signal_intelligence.config import CostScenario
    from crypto_signal_intelligence.feedback.reconcile import replay_signal
    from tests.test_signals import one_tp_signal
    costs = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0, extra_entry_delay_bars=0)
    decision = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    signal = one_tp_signal(decision_at=decision, data_as_of=decision,
                           entry_expires_at=decision + timedelta(minutes=30, seconds=2))
    start = pd.Timestamp(decision)
    above = {"open": 2600.0, "high": 2610.0, "low": 2590.0, "close": 2600.0}
    rows = [above, above, {"open": 2600.0, "high": 2600.0, "low": 2490.0, "close": 2495.0}] + [above] * 3
    bars = pd.DataFrame([{"open_time": start + pd.Timedelta(minutes=15 * i), **r} for i, r in enumerate(rows)])
    assert replay_signal(bars, signal, costs, timedelta(minutes=15)).outcome == "UNFILLED"
    assert replay_signal(bars.iloc[:2], signal, costs, timedelta(minutes=15)).outcome == "UNFILLED"
    assert replay_signal(bars.iloc[:1], signal, costs, timedelta(minutes=15)).outcome == "PENDING"
    touched = bars.copy()
    touched.loc[1, ["low", "close"]] = [2490.0, 2495.0]
    assert replay_signal(touched, signal, costs, timedelta(minutes=15)).fill_price == 2500.10


def test_prospective_fill_opening_below_the_stop_pays_two_crossings():
    """Ouverture sous le stop : achat à open × (1 + coût), stop-market aussitôt à open × (1 − coût)."""
    from datetime import timedelta

    import pandas as pd

    from crypto_signal_intelligence.config import CostScenario
    from crypto_signal_intelligence.feedback.reconcile import replay_signal
    from tests.test_signals import one_tp_signal
    costs = CostScenario(fee_bps=0, slippage_bps=10, half_spread_bps=0, extra_entry_delay_bars=0)
    signal = one_tp_signal()
    start = pd.Timestamp(signal.decision_at)
    bars = pd.DataFrame([{"open_time": start, "open": 2460.0, "high": 2470.0, "low": 2450.0, "close": 2465.0}])
    result = replay_signal(bars, signal, costs, timedelta(minutes=15))
    fill, exit_ = 2460.0 * 1.001, 2460.0 * 0.999
    assert result.outcome == "SL_GAP" and result.fill_price == pytest.approx(fill)
    assert result.r == pytest.approx(round((exit_ - fill) / (2500.10 - 2462.60), 4))
