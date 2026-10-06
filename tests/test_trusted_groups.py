"""Groupes de confiance halal (décision du propriétaire, 2026-10-06) : une paire USDT publiée par l'un d'eux est
ajoutée à l'univers si elle se négocie sur Binance Spot, jamais contre son refus (pour la crypto, toutes paires
confondues) ni contre un avis défavorable. Reconnus par l'identifiant de leur conversation Telegram, jamais par un
nom écrit dans le message (relecture du 2026-10-06). Aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.external import admission as adm
from crypto_signal_intelligence.external.evaluate import evaluate
from crypto_signal_intelligence.external.universe import REQUESTED, UserUniverse

NOW = datetime(2026, 10, 6, 18, tzinfo=UTC)
LISTED = {"DOGEUSDT", "LTCUSDT", "UNIUSDT", "RAREUSDT", "BNBUSDT", "BNBUSDC", "PEPEUSDC"}
SIGNAL = "👑 WHALE HUNTING\nPAIR: {}/{}\nENTRY 1: 2.000\nT1: 2.100\nT2: 2.200\nSL: 1.900\nPLATFORM: Binance"
WHALE = "-1003226951554"


def listing(settings, symbol):
    return Decimal("0.0001") if symbol in LISTED else None


def row(ident, chat, base, quote="USDT"):
    return {"signal_id": ident, "source_chat_id": chat, "raw_text": SIGNAL.format(base, quote),
            "received_at": NOW.isoformat()}


def test_groups_are_recognised_by_conversation_only(settings):
    assert adm.trusted_group(settings, WHALE) == WHALE
    assert adm.trusted_group(settings, int(WHALE)) == WHALE
    assert adm.trusted_group(settings, "WHALE HUNTING") == ""                         # un nom ne suffit jamais
    assert adm.trusted_group(settings, "-100999") == "" and adm.trusted_group(settings, None) == ""


def test_a_trusted_group_adds_a_usdt_pair_as_the_owner_decision(settings):
    entry = adm.admit_from_trusted_group(settings, "DOGEUSDT", group=WHALE, now=NOW, lookup=listing)
    assert entry["decision"] == adm.AJOUTEE and entry["decided_by"] == adm.OWNER
    assert entry["reason"].startswith(adm.TRUSTED_REASON)
    assert UserUniverse(settings.external_db).get("DOGEUSDT")["status"] == REQUESTED
    assert adm.admit_from_trusted_group(settings, "XTZUSDT", group=WHALE, now=NOW,
                                        lookup=listing)["decision"] == adm.INDISPONIBLE


def test_owner_refusals_hold_for_the_whole_crypto_and_usdc_never_enters(settings):
    """Relecture : un refus de BNBUSDT était contourné par BNBUSDC."""
    adm.decide(settings, "BNBUSDT", add=False, now=NOW, lookup=listing)
    for symbol in ("BNBUSDT", "BNBUSDC"):
        assert adm.admit_from_trusted_group(settings, symbol, group=WHALE, now=NOW,
                                            lookup=listing)["decision"] == adm.REFUSEE
    pepe = adm.admit_from_trusted_group(settings, "PEPEUSDC", group=WHALE, now=NOW, lookup=listing)
    assert pepe["decision"] == adm.INDISPONIBLE                                       # USDT seulement
    universe = UserUniverse(settings.external_db)
    assert universe.get("BNBUSDC") is None and universe.get("PEPEUSDC") is None


def test_haram_opinions_stay_refused(settings):
    uni = adm.admit_from_trusted_group(settings, "UNIUSDT", group=WHALE, now=NOW, lookup=listing)
    assert uni["decision"] == adm.REFUSEE and uni["decided_by"] == adm.RULE
    assert UserUniverse(settings.external_db).get("UNIUSDT") is None


def test_an_owner_decision_is_never_replaced_by_the_rule(settings):
    adm.decide(settings, "XTZUSDT", add=True, now=NOW, lookup=listing)              # acceptée, non négociable
    kept = adm.admit_from_trusted_group(settings, "XTZUSDT", group=WHALE, now=NOW, lookup=listing)
    assert kept["decided_by"] == adm.OWNER


def test_evaluation_never_adds_a_pair_from_a_name_in_the_text(settings):
    """Relecture : « WHALE HUNTING » écrit en tête par un autre canal faisait ajouter la paire."""
    result = evaluate(settings, SIGNAL.format("RARE", "USDT"), source="WHALE HUNTING", now=NOW, tick_size_lookup=listing)
    assert result.verdict == "EN_ATTENTE" and "en attente de ta décision" in result.failed[0].detail
    assert UserUniverse(settings.external_db).get("RAREUSDT") is None


def test_the_relay_admits_by_conversation_with_the_api_token(settings, monkeypatch):
    monkeypatch.setattr(adm, "binance_listing", listing)
    monkeypatch.setenv("CSI_API_TOKEN", "jeton-de-test")
    api = CsiApi(settings, now=lambda: NOW)
    answer = api.telegram_live({"signals": [
        row("c:1", WHALE, "DOGE"), row("c:2", "-100999", "RARE"),               # en-tête WHALE HUNTING usurpée
        row("c:3", "-1003742935429", "UNI"), row("c:4", WHALE, "BNB", "USDC")]})
    assert {a["symbol"]: a["decision"] for a in answer["admissions"]} == {
        "DOGEUSDT": adm.AJOUTEE, "UNIUSDT": adm.REFUSEE, "BNBUSDC": adm.INDISPONIBLE}
    assert answer["deposited"] == 4                                                # le dépôt pour F4 est intact
    assert UserUniverse(settings.external_db).get("RAREUSDT") is None


def test_without_an_api_token_the_relay_adds_nothing(settings, monkeypatch):
    monkeypatch.setattr(adm, "binance_listing", listing)
    monkeypatch.delenv("CSI_API_TOKEN", raising=False)
    answer = CsiApi(settings, now=lambda: NOW).telegram_live({"signals": [row("c:1", WHALE, "DOGE")]})
    assert answer["admissions"] == [] and answer["deposited"] == 1
    assert UserUniverse(settings.external_db).get("DOGEUSDT") is None
