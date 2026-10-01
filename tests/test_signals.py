from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES
from crypto_signal_intelligence.signals.outbox import SignalRegistry, sha256_text
from crypto_signal_intelligence.signals.schema import Signal, gross_rr, idempotency_key
from crypto_signal_intelligence.signals.txt import (
    SignalFormatError,
    UnsupportedSignalVersion,
    parse,
    serialize,
)

FOUR_TP_HASH = EXIT_POLICIES["FIXED_SL_FOUR_TP_V1"].policy_hash()
ONE_TP_HASH = EXIT_POLICIES["FIXED_SL_ONE_TP_V1"].policy_hash()

# Exemple synthétique de la spécification (non connecté, non signal réel).
SPEC_EXAMPLE = f"""SIGNAL_VERSION=3
SIGNAL_ID=EXAMPLE_ONLY_001
IDEMPOTENCY_KEY=EXAMPLE_ONLY_SETUP_001
DATA_AS_OF=2026-09-29T12:00:00Z
DECISION_AT=2026-09-29T12:00:00Z
CREATED_AT=2026-09-29T12:00:05Z
VALID_FROM=2026-09-29T12:00:05Z
EXPIRES_AT=2026-09-29T12:15:00Z
ENTRY_EXPIRES_AT=2026-09-29T13:00:00Z
MARKET_DATA_SOURCE=SYNTHETIC_FIXTURE
ENVIRONMENT=DEMO
MARKET_TYPE=SPOT
SYMBOL=TESTUSDT
ACTION=BUY
STRATEGY=EMA_PULLBACK_CONTINUATION
STRATEGY_VERSION=1
TIMEFRAME_SETUP=15m
ENTRY_MODE=LIMIT
ENTRY_COUNT=1
ENTRY_1=100.00
ENTRY_2=NONE
ENTRY_WEIGHTS=1.0
WEIGHT_BASIS=BASE_QUANTITY
STOP_LOSS=98.00
TP_COUNT=4
TP_1=102.00
TP_2=103.00
TP_3=104.00
TP_4=106.00
TP_WEIGHTS=0.25,0.25,0.25,0.25
EXIT_POLICY_ID=FIXED_SL_FOUR_TP_V1
EXIT_POLICY_HASH={FOUR_TP_HASH}
MAX_HOLD_MINUTES=1440
RR_REFERENCE=ENTRY_1
RR_TP1_GROSS=1.0
RR_TP2_GROSS=1.5
RR_TP3_GROSS=2.0
RR_TP4_GROSS=3.0
TECHNICAL_SCORE=NONE
ML_PROBABILITY=NONE
MODEL_ID=NONE
ML_TARGET_ID=NONE
ML_HORIZON_MINUTES=NONE
ML_CALIBRATION_ID=NONE
TREND_REGIME=BULL
VOLATILITY_REGIME=NORMAL
NEWS_STATUS=OFF
MAX_ENTRY_DEVIATION_BPS=20
VALIDATION_STATUS=SCHEMA_EXAMPLE_ONLY
STATUS=NEW
---ANALYSIS---
REASONS=EXEMPLE_SYNTHETIQUE_NE_PAS_EXECUTER
"""


def one_tp_signal(**overrides) -> Signal:
    created = datetime(2026, 9, 29, 12, 0, 5, tzinfo=UTC)
    decision = created - timedelta(seconds=5)
    values = dict(signal_id="CSI-TEST-1", data_as_of=decision, decision_at=decision, created_at=created,
                  valid_from=created, expires_at=created + timedelta(minutes=15),
                  entry_expires_at=created + timedelta(minutes=30),
                  idempotency_key=idempotency_key(market_type="SPOT", symbol="ETHUSDT",
                                                  strategy="DONCHIAN_VOLUME_BREAKOUT", strategy_version=1,
                                                  setup_time=created, exit_policy_id="FIXED_SL_ONE_TP_V1"),
                  market_data_source="BINANCE_SPOT_PUBLIC", environment="DEMO", symbol="ETHUSDT",
                  strategy="DONCHIAN_VOLUME_BREAKOUT", strategy_version=1, timeframe_setup="15m", entry_mode="LIMIT",
                  entry_count=1, entry_1=Decimal("2500.10"), entry_weights=(Decimal("1.0"),),
                  stop_loss=Decimal("2462.60"), tp_count=1, tp_1=Decimal("2575.10"), tp_weights=(Decimal("1.0"),),
                  exit_policy_id="FIXED_SL_ONE_TP_V1", exit_policy_hash=ONE_TP_HASH, max_hold_minutes=1440,
                  rr_reference="ENTRY_1", rr_tp1_gross=Decimal("2.000"), trend_regime="BULL",
                  volatility_regime="NORMAL", news_status="OFF", max_entry_deviation_bps=Decimal(10),
                  validation_status="RESEARCH", analysis=(("REASONS", "test"),))
    values.update(overrides)
    return Signal(**values)


def test_specification_example_parses():
    signal = parse(SPEC_EXAMPLE)
    assert signal.tp_count == 4 and signal.targets == [Decimal("102.00"), Decimal("103.00"),
                                                       Decimal("104.00"), Decimal("106.00")]
    assert signal.integration_status.value == "INTEGRATION_UNVERIFIED"
    assert signal.expires_at < signal.entry_expires_at and signal.news_status == "OFF"


def test_round_trip_is_exact():
    signal = one_tp_signal()
    text = serialize(signal)
    assert parse(text) == signal and serialize(parse(text)) == text
    assert "TP_2=NONE" in text and "RR_TP1_GROSS=2.000" in text and text.endswith("\n")
    assert text.startswith("SIGNAL_VERSION=3\n") and f"EXIT_POLICY_HASH={ONE_TP_HASH}\n" in text


@pytest.mark.parametrize("mutation, message", [
    (lambda t: t.replace("TP_1=102.00", "TP_1=102.00\nTP_1=102.00"), "dupliquée"),
    (lambda t: t.replace("STATUS=NEW", "STATUS=NEW\nFOO=1"), "inconnue"),
    (lambda t: t.replace("SIGNAL_VERSION=3", "SIGNAL_VERSION=2"), "version"),
    (lambda t: t.replace("SIGNAL_VERSION=3", "SIGNAL_VERSION=4"), "version"),
    (lambda t: t.replace("ENTRY_1=100.00", "ENTRY_1=NaN"), "décimal"),
    (lambda t: t.replace("ENTRY_1=100.00", "ENTRY_1=Infinity"), "décimal"),
    (lambda t: t.replace("ENTRY_1=100.00", "ENTRY_1=100,00"), "décimal"),
    (lambda t: t.replace("RR_TP1_GROSS=1.0", "RR_TP1_GROSS=1.2"), "RR_TP1_GROSS attendu"),
    (lambda t: t.replace("TP_WEIGHTS=0.25,0.25,0.25,0.25", "TP_WEIGHTS=0.3,0.25,0.25,0.25"), "sommer"),
    (lambda t: t.replace("STOP_LOSS=98.00", "STOP_LOSS=101.00"), "STOP_LOSS"),
    (lambda t: t.replace("TP_3=104.00", "TP_3=102.50"), "croissants"),
    (lambda t: t.replace("CREATED_AT=2026-09-29T12:00:05Z", "CREATED_AT=2026-09-29 12:00:05"), "format"),
    (lambda t: t.replace("EXPIRES_AT=2026-09-29T12:15:00Z", "EXPIRES_AT=2026-09-29T12:00:00Z"), "ordre"),
    # le message ne peut pas rester acceptable après la fin de validité des entrées
    (lambda t: t.replace("ENTRY_EXPIRES_AT=2026-09-29T13:00:00Z", "ENTRY_EXPIRES_AT=2026-09-29T12:10:00Z"), "ordre"),
    (lambda t: t.replace("DECISION_AT=2026-09-29T12:00:00Z", "DECISION_AT=2026-09-29T12:00:06Z"), "ordre"),
    (lambda t: t.replace("SYMBOL=TESTUSDT", "SYMBOL = TESTUSDT"), "CLE=VALEUR"),
    (lambda t: t.replace("SYMBOL=TESTUSDT\n", ""), "manquantes"),
    (lambda t: t.replace("ENTRY_2=NONE", "ENTRY_2=99.00"), "ENTRY_COUNT"),
    (lambda t: t.replace("ACTION=BUY", "ACTION=SELL"), "action"),
    (lambda t: t.replace("ENVIRONMENT=DEMO", "ENVIRONMENT=LIVE"), "environment"),
    (lambda t: t.replace(f"EXIT_POLICY_HASH={FOUR_TP_HASH}", "EXIT_POLICY_HASH=0123456789abcdef"), "EXIT_POLICY_HASH"),
    (lambda t: t.replace("EXIT_POLICY_ID=FIXED_SL_FOUR_TP_V1", "EXIT_POLICY_ID=UNKNOWN_POLICY_V9"), "inconnue"),
    (lambda t: t.replace("MAX_HOLD_MINUTES=1440", "MAX_HOLD_MINUTES=NONE"), "MAX_HOLD_MINUTES"),
    (lambda t: t.replace("RR_REFERENCE=ENTRY_1", "RR_REFERENCE=WEIGHTED_ENTRY"), "RR_REFERENCE"),
    (lambda t: t.replace("ML_PROBABILITY=NONE", "ML_PROBABILITY=0.6"), "vont ensemble"),
    (lambda t: t.replace("NEWS_STATUS=OFF", "NEWS_STATUS=NO_BAD_NEWS"), "news_status"),
    (lambda t: t.replace("INTENDED_EXECUTION_ENVIRONMENT", "X").replace("ENVIRONMENT=DEMO",
                                                                         "INTENDED_EXECUTION_ENVIRONMENT=DEMO"),
     "inconnue"),
])
def test_strict_parser_rejects(mutation, message):
    with pytest.raises((SignalFormatError, UnsupportedSignalVersion), match=message):
        parse(mutation(SPEC_EXAMPLE))


def test_two_entries_use_the_declared_reference_price():
    """ENTRY_2 est lisible et vérifié ; le RR suit RR_REFERENCE (prix moyen PRÉVU si WEIGHTED_ENTRY)."""
    policy = EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V1"]
    entries, weights, stop, tp = [Decimal(100), Decimal(98)], (Decimal("0.5"), Decimal("0.5")), Decimal(96), Decimal(105)
    base = dict(entry_count=2, entry_1=entries[0], entry_2=entries[1], entry_weights=weights, stop_loss=stop,
                tp_1=tp, exit_policy_id=policy.policy_id, exit_policy_hash=policy.policy_hash(), max_hold_minutes=None)
    with pytest.raises(ValueError, match="au plus 1 entrée"):   # aucune politique déclarée ne gère 2 entrées
        one_tp_signal(**base, rr_reference="WEIGHTED_ENTRY", rr_tp1_gross=gross_rr(Decimal(99), stop, tp))
    with pytest.raises(ValueError, match="ENTRY_2 < ENTRY_1"):
        one_tp_signal(**{**base, "entry_2": Decimal(101)}, rr_reference="ENTRY_1",
                      rr_tp1_gross=gross_rr(entries[0], stop, tp))
    from crypto_signal_intelligence.signals.schema import reference_price
    assert reference_price(entries, weights, "BASE_QUANTITY", "WEIGHTED_ENTRY") == Decimal(99)
    harmonic = reference_price(entries, weights, "QUOTE_BUDGET", "WEIGHTED_ENTRY")
    assert Decimal("98.98") < harmonic < Decimal(99)          # budget égal : plus de quantité au prix bas


def test_analysis_section_cannot_change_the_contract():
    text = SPEC_EXAMPLE + "STOP_LOSS=1\nSIGNAL_VERSION=9\n"
    assert parse(text).stop_loss == Decimal("98.00")


def test_publication_is_atomic_idempotent_and_recoverable(tmp_path):
    registry = SignalRegistry(tmp_path / "db.sqlite3", tmp_path / "shadow")
    now = datetime(2026, 9, 29, 12, 0, 6, tzinfo=UTC)
    first = registry.publish(one_tp_signal(), now)
    assert first.status == "PUBLISHED" and first.path.suffix == ".txt"
    assert parse(first.path.read_text(encoding="utf-8")) == one_tp_signal()
    again = registry.publish(one_tp_signal(signal_id="CSI-TEST-2"), now)  # même clé logique
    assert again.status == "DUPLICATE" and again.signal_id == "CSI-TEST-1"
    assert sorted(p.name for p in (tmp_path / "shadow").iterdir()) == ["CSI-TEST-1.txt"]

    # Crash simulé après l'enregistrement PENDING, avant le fichier.
    other = one_tp_signal(signal_id="CSI-TEST-3", idempotency_key="SPOT:ETHUSDT:X:v1:K:P")
    text = serialize(other)
    with registry.connect() as db:
        db.execute("INSERT INTO signals VALUES (?, ?, 'ETHUSDT', 'X', 'c', 'e', 'PENDING', ?, ?, ?, ?, NULL)",
                   (other.signal_id, other.idempotency_key, str(tmp_path / "shadow"), "CSI-TEST-3.txt",
                    sha256_text(text), text))
    (tmp_path / "shadow" / "CSI-ORPHAN.txt.tmp").write_text("partiel", encoding="utf-8")
    counts = registry.reconcile(now=now)
    assert counts == {"published": 1, "conflicts": 0, "tmp_removed": 1, "held": 0, "expired": 0}
    assert parse((tmp_path / "shadow" / "CSI-TEST-3.txt").read_text(encoding="utf-8")) == other
    assert not list((tmp_path / "shadow").glob("*.tmp"))


def test_outbox_publication_refused_until_integration_verified(settings):
    assert settings.publication_dir().name == "shadow"
    locked = settings.model_copy(update={"publication": settings.publication.model_copy(update={"mode": "outbox"})})
    with pytest.raises(PermissionError):
        locked.publication_dir()


def test_build_signal_sets_both_expirations_and_the_policy_fingerprint(settings):
    from crypto_signal_intelligence.levels.engine import compute_levels
    from crypto_signal_intelligence.signals.analyze import build_signal
    from crypto_signal_intelligence.strategies import registry
    from tests.test_strategy_levels import T, context

    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    ctx = context()
    decision = strategy.evaluate(ctx)
    levels = compute_levels(decision, Decimal("0.01"))
    created = T + timedelta(seconds=7, microseconds=5)
    signal = build_signal(settings, symbol="ETHUSDT", strategy=strategy, context=ctx, decision=decision,
                          levels=levels, created=created)
    entry_window = decision.entry_intent.expires_after_bars * timedelta(minutes=15)
    assert signal.decision_at == T and signal.created_at == created.replace(microsecond=0)
    assert signal.entry_expires_at == T + entry_window
    assert signal.expires_at == min(signal.created_at + timedelta(minutes=settings.publication.message_ttl_minutes),
                                    signal.entry_expires_at)
    policy = EXIT_POLICIES[decision.exit_policy_id]
    assert signal.exit_policy_hash == policy.policy_hash()
    assert signal.max_hold_minutes == (settings.simulation.max_hold_bars * 15 if policy.time_exit else None)
    assert parse(serialize(signal)) == signal
    with pytest.raises(ValueError, match="close"):
        build_signal(settings, symbol="ETHUSDT", strategy=strategy, context=ctx, decision=decision, levels=levels,
                     created=T + entry_window)


def test_outbox_publication_requires_a_policy_the_consumer_executes():
    from crypto_signal_intelligence.domain.enums import NoTradeReason, StrategyStatus
    from crypto_signal_intelligence.validation import gates
    consumer = ["BSM_MARKET_TP_FIXED_SL_V1"]
    assert gates.publication(StrategyStatus.RESEARCH, "shadow", exit_policy_id="FIXED_SL_ONE_TP_V1",
                             consumer_policies=consumer) is None           # shadow : observation libre
    veto = gates.publication(StrategyStatus.DEMO_ELIGIBLE, "outbox", exit_policy_id="FIXED_SL_ONE_TP_V1",
                             consumer_policies=consumer)
    assert veto is not None and veto[0] == NoTradeReason.EXIT_POLICY_MISMATCH
    assert gates.publication(StrategyStatus.DEMO_ELIGIBLE, "outbox", exit_policy_id="BSM_MARKET_TP_FIXED_SL_V1",
                             consumer_policies=consumer) is None


def test_registry_paths_survive_a_move_between_machines(tmp_path, monkeypatch):
    """Sauvegarde restaurée ailleurs (Windows ↔ Docker/Linux) : les dossiers restent justes."""
    import os as _os

    from crypto_signal_intelligence.signals import outbox
    root = tmp_path / "csi"
    shadow = root / "signals" / "shadow"
    registry = SignalRegistry(root / "signals" / "registry.sqlite3", shadow)
    registry.publish(one_tp_signal(), datetime(2026, 9, 29, 12, 0, 6, tzinfo=UTC))
    row = registry.rows()[0]
    assert row["directory"] == "signals/shadow"                     # relatif à la racine
    assert registry.directory_of(row) == shadow
    fake = {"directory": r"C:\Users\x\CryptoSignalIntelligence\signals\shadow"}
    posix = {"directory": "/srv/csi/signals/shadow"}
    monkeypatch.setattr(outbox.os, "name", "posix")                 # lu sous Linux
    assert registry.directory_of(fake) == shadow
    assert registry.directory_of(posix) == Path("/srv/csi/signals/shadow")
    monkeypatch.setattr(outbox.os, "name", "nt")                    # lu sous Windows
    assert registry.directory_of(posix) == shadow
    assert registry.directory_of(fake) == Path(fake["directory"])
    assert _os.name in {"nt", "posix"}
