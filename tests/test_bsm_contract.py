"""Contrat CSI → BinanceSpotManager : le VRAI parseur de BSM doit lire ce que CSI écrit (TXT V3).

Exécuté contre le dépôt BinanceSpotManager voisin, ou celui désigné par `BSM_PATH`
(par exemple le worktree de la branche `feat/csi-v2-drop`). Seul le module de parsing
de BSM est chargé, par chemin, sans importer son paquet : aucun code réseau, aucune clé.

- Toujours vérifié : un parseur BSM qui ne connaît pas le V3 le REFUSE (échec sûr).
- Si BSM expose `parse_csi_signal` : lecture exacte, refus des mêmes corruptions que CSI,
  et refus de toute politique de sortie que BSM n'exécute pas réellement (point 13 du
  cahier des charges : même identifiant ET même empreinte).
Sans `parse_csi_signal`, `integration_verified` ne peut pas passer à `true` (docs/SIGNAL_FORMAT.md).
"""
from __future__ import annotations

import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from crypto_signal_intelligence.backtest.exits import EXIT_POLICIES
from crypto_signal_intelligence.signals.schema import Signal, gross_rr, idempotency_key
from crypto_signal_intelligence.signals.txt import SignalFormatError, parse, serialize
from tests.test_signals import SPEC_EXAMPLE, one_tp_signal

BSM_ROOT = Path(os.environ.get("BSM_PATH", Path(__file__).resolve().parents[2] / "BinanceSpotManager"))
# Politiques que BinanceSpotManager exécute réellement (docs/BSM_PROFILE.md).
BSM_POLICIES = ("BSM_MARKET_TP_FIXED_SL_V2", "BSM_MARKET_TP_BREAK_EVEN_V2")


def load_bsm_parser() -> ModuleType:
    module_path = BSM_ROOT / "binance_spot_manager" / "signal_parser.py"
    if not module_path.exists():
        pytest.skip("BinanceSpotManager introuvable : définir BSM_PATH")
    spec = importlib.util.spec_from_file_location("bsm_signal_parser", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bsm_any() -> ModuleType:
    return load_bsm_parser()


@pytest.fixture(scope="module")
def bsm(bsm_any) -> ModuleType:
    if not hasattr(bsm_any, "parse_csi_signal"):
        pytest.skip("parse_csi_signal absent de BSM : contrat V3 non implémenté côté consommateur")
    return bsm_any


def bsm_signal(policy_id: str = "BSM_MARKET_TP_FIXED_SL_V2", **overrides) -> Signal:
    policy = EXIT_POLICIES[policy_id]
    values = dict(exit_policy_id=policy_id, exit_policy_hash=policy.policy_hash(),
                  max_hold_minutes=1440 if policy.time_exit else None,
                  idempotency_key=f"SPOT:ETHUSDT:DONCHIAN_VOLUME_BREAKOUT:v1:20260929T120005Z:{policy_id}")
    values.update(overrides)
    return one_tp_signal(**values)


def four_tp_signal() -> Signal:
    """Quatre TP pondérés, profil de sortie réel de BSM (TP au marché, SL fixe)."""
    entry, stop = Decimal("2500.10"), Decimal("2462.60")
    targets = [Decimal("2537.60"), Decimal("2575.10"), Decimal("2612.60"), Decimal("2650.10")]
    return bsm_signal(
        signal_id="CSI-TEST-4TP",
        idempotency_key=idempotency_key(market_type="SPOT", symbol="ETHUSDT", strategy="DONCHIAN_VOLUME_BREAKOUT",
                                        strategy_version=1, setup_time=one_tp_signal().created_at,
                                        exit_policy_id="BSM_MARKET_TP_FIXED_SL_V2"),
        entry_1=entry, stop_loss=stop, tp_count=4, tp_1=targets[0], tp_2=targets[1], tp_3=targets[2],
        tp_4=targets[3], tp_weights=(Decimal("0.4"), Decimal("0.3"), Decimal("0.2"), Decimal("0.1")),
        rr_tp1_gross=gross_rr(entry, stop, targets[0]), rr_tp2_gross=gross_rr(entry, stop, targets[1]),
        rr_tp3_gross=gross_rr(entry, stop, targets[2]), rr_tp4_gross=gross_rr(entry, stop, targets[3]),
    )


def test_any_bsm_parser_refuses_v3_it_does_not_understand(bsm_any):
    """Échec sûr : sans lecteur V3, le texte n'est jamais interprété comme un signal exécutable."""
    text = serialize(bsm_signal())
    if hasattr(bsm_any, "parse_csi_signal"):
        pytest.skip("BSM lit le V3 : couvert par les tests suivants")
    assert bsm_any.parse_signal(text).errors
    if hasattr(bsm_any, "parse_signal_v2"):
        assert bsm_any.parse_signal_v2(text).errors


def assert_same_contract(parsed, signal: Signal) -> None:
    assert parsed.errors == []
    assert parsed.signal_version == 3 and parsed.direction == "BUY"
    assert parsed.symbol == signal.symbol
    assert parsed.entries == [float(e) for e in signal.entries]
    assert parsed.stop == float(signal.stop_loss)
    assert parsed.targets == [float(t) for t in signal.targets]
    assert parsed.tp_weights == [float(w) for w in signal.tp_weights]
    assert parsed.signal_id == signal.signal_id
    assert parsed.idempotency_key == signal.idempotency_key
    assert parsed.exit_policy_id == signal.exit_policy_id
    assert parsed.exit_policy_hash == signal.exit_policy_hash
    assert parsed.decision_at == signal.decision_at.timestamp()
    assert parsed.valid_from == signal.valid_from.timestamp()
    assert parsed.expires_at == signal.expires_at.timestamp()
    assert parsed.entry_expires_at == signal.entry_expires_at.timestamp()
    assert parsed.max_entry_deviation_bps == float(signal.max_entry_deviation_bps)
    assert parsed.news_status == signal.news_status


@pytest.mark.parametrize("policy_id", BSM_POLICIES)
def test_bsm_reads_its_own_policies_exactly(bsm, policy_id):
    signal = bsm_signal(policy_id)
    assert_same_contract(bsm.parse_csi_signal(serialize(signal)), signal)


def test_bsm_reads_four_weighted_tps_in_order(bsm):
    signal = four_tp_signal()
    parsed = bsm.parse_csi_signal(serialize(signal))
    assert_same_contract(parsed, signal)
    assert parsed.targets == [2537.60, 2575.10, 2612.60, 2650.10] and parsed.tp_weights == [0.4, 0.3, 0.2, 0.1]


@pytest.mark.parametrize("policy_id", sorted(set(EXIT_POLICIES) - set(BSM_POLICIES)))
def test_bsm_refuses_policies_it_does_not_execute(bsm, policy_id):
    """TP limite, sortie temporelle, stop suiveur… : BSM ne les exécute pas, il doit refuser le signal."""
    parsed = bsm.parse_csi_signal(serialize(bsm_signal(policy_id)))
    assert any("EXIT_POLICY" in error for error in parsed.errors), parsed.errors


def test_bsm_generic_parser_routes_v3_and_never_uses_text_templates(bsm):
    text = serialize(bsm_signal())
    auto = bsm.parse_signal(text)
    assert auto.errors == [] and auto.to_dict() == bsm.parse_csi_signal(text).to_dict()
    for template in ("structured", "abk", "numbered", "simple"):
        assert bsm.parse_signal(text, template=template).errors, template


POLICY_HASH = EXIT_POLICIES["BSM_MARKET_TP_FIXED_SL_V2"].policy_hash()
MUTATIONS = [
    ("RR_TP1_GROSS=2.000", "RR_TP1_GROSS=2.100"),                          # RR falsifié
    ("TP_1=2575.10", "TP_1=2575.10\nTP_1=2575.10"),                        # clé dupliquée
    ("STATUS=NEW", "STATUS=NEW\nFOO=1"),                                   # clé inconnue
    ("SIGNAL_VERSION=3", "SIGNAL_VERSION=4"),                              # version inconnue
    ("SIGNAL_VERSION=3", "SIGNAL_VERSION=2"),                              # ancienne version
    ("STOP_LOSS=2462.60", "STOP_LOSS=2501.00"),                            # stop au-dessus de l'entrée
    ("TP_WEIGHTS=1.0", "TP_WEIGHTS=0.9"),                                  # poids ne sommant pas à 1
    ("SYMBOL=ETHUSDT", "SYMBOL = ETHUSDT"),                                # espaces autour de =
    ("ENTRY_2=NONE", "ENTRY_2=2490.00"),                                   # ENTRY_COUNT=1 contredit
    ("ENTRY_1=2500.10", "ENTRY_1=2500,10"),                                # virgule décimale
    ("ENVIRONMENT=DEMO", "ENVIRONMENT=LIVE"),
    ("ACTION=BUY", "ACTION=SELL"),
    ("EXPIRES_AT=2026-09-29T12:15:05Z", "EXPIRES_AT=2026-09-29T12:00:05Z"),          # ≤ VALID_FROM
    ("ENTRY_EXPIRES_AT=2026-09-29T12:30:05Z", "ENTRY_EXPIRES_AT=2026-09-29T12:10:05Z"),  # < EXPIRES_AT
    (f"EXIT_POLICY_HASH={POLICY_HASH}", "EXIT_POLICY_HASH=0123456789abcdef"),         # règles différentes
    ("MAX_HOLD_MINUTES=NONE", "MAX_HOLD_MINUTES=1440"),                    # BSM n'a pas de sortie temporelle
    ("MAX_ENTRY_DEVIATION_BPS=10.0", "MAX_ENTRY_DEVIATION_BPS=NONE"),      # obligatoire
    ("NEWS_STATUS=OFF", "NEWS_STATUS=NO_BAD_NEWS"),                        # énumération fermée
    ("ML_PROBABILITY=NONE", "ML_PROBABILITY=0.7"),                         # sans cible, horizon, calibration
]


@pytest.mark.parametrize("old, new", MUTATIONS)
def test_bsm_refuses_what_csi_refuses(bsm, old, new):
    text = serialize(bsm_signal())
    assert old in text, old
    corrupted = text.replace(old, new, 1)
    with pytest.raises(SignalFormatError):
        parse(corrupted)
    assert bsm.parse_csi_signal(corrupted).errors, f"BSM accepte un texte que CSI refuse : {new!r}"


def test_bsm_ignores_prose_after_the_analysis_marker(bsm):
    signal = bsm_signal()
    text = serialize(signal)
    assert "---ANALYSIS---" in text
    parsed = bsm.parse_csi_signal(text + "STOP_LOSS=1.0\nSIGNAL_VERSION=9\nEXPIRES_AT=2030-01-01T00:00:00Z\n")
    assert_same_contract(parsed, signal)


def test_bsm_never_treats_the_specification_example_as_executable(bsm):
    assert bsm.parse_csi_signal(SPEC_EXAMPLE).errors, "SCHEMA_EXAMPLE_ONLY doit bloquer l'exécution"


TELEGRAM_FIXTURES = ["BICO", "ABK", "SIMPLE", "GALA"]


@pytest.mark.parametrize("name", TELEGRAM_FIXTURES)
def test_csi_and_bsm_read_the_same_telegram_signal(bsm_any, name):
    """Différentiel : l'avis de CSI doit porter sur le trade que BSM exécuterait (mêmes prix lus)."""
    import tests.test_external as fixtures
    from crypto_signal_intelligence.external.parser import parse as csi_parse
    if not hasattr(fixtures, name):
        pytest.skip(f"fixture {name} absente")
    text = getattr(fixtures, name)
    csi, bsm_parsed = csi_parse(text), bsm_any.parse_signal(text)
    assert csi.ok == (not bsm_parsed.errors), (name, csi.errors, bsm_parsed.errors)
    if csi.ok:
        assert csi.symbol == bsm_parsed.symbol.replace("/", "")
        assert csi.entries == pytest.approx(list(bsm_parsed.entries))
        assert csi.targets == pytest.approx(list(bsm_parsed.targets))
        assert csi.stop == pytest.approx(bsm_parsed.stop)


@pytest.mark.parametrize("horizon", ["1 heure", "7 jours"])
@pytest.mark.parametrize(("symbol", "entry", "target", "stop"), [
    ("ETHUSDT", "2500.10", "2537.60", "2462.60"), ("BTCUSDT", "64012.5", "65900", "62800.1"),
    ("XRPUSDT", "0.5123", "0.5301", "0.5020"), ("PEPEUSDT", "0.0000100", "0.0000110", "0.0000090")])
def test_dashboard_plan_text_is_never_read_as_a_signal_by_bsm(bsm_any, horizon, symbol, entry, target, stop):
    """Le résumé copiable du tableau de bord (« plan indicatif ») ne doit JAMAIS devenir un ordre dans BSM,
    quel que soit son lecteur (relecture contract-guard du 2026-10-01 : l'ancien format « PAIR: … » l'était)."""
    from crypto_signal_intelligence.outlook.pair import plan_copy_text
    text = plan_copy_text(symbol, horizon, entry, target, stop)
    assert bsm_any.parse_signal(text).errors, text
    with pytest.raises(SignalFormatError):
        parse(text)
