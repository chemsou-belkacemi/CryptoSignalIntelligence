"""Groupes de confiance halal (décision du propriétaire, 2026-10-06) : une paire qu'ils publient est ajoutée à
l'univers si elle se négocie sur Binance Spot, jamais contre son refus ni contre un avis défavorable. Aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.external import admission as adm
from crypto_signal_intelligence.external.evaluate import evaluate
from crypto_signal_intelligence.external.universe import REQUESTED, UserUniverse

NOW = datetime(2026, 10, 6, 18, tzinfo=UTC)
LISTED = {"DOGEUSDT", "LTCUSDT", "UNIUSDT", "GUSDT", "RAREUSDT"}
SIGNAL = "👑 WHALE HUNTING\nPAIR: {}/USDT\nENTRY 1: 2.000\nT1: 2.100\nT2: 2.200\nSL: 1.900\nPLATFORM: Binance"


def listing(settings, symbol):
    return Decimal("0.0001") if symbol in LISTED else None


def test_the_owner_groups_are_recognised_by_name_or_conversation(settings):
    assert adm.trusted_group(settings, "WHALE HUNTING") == "WHALE HUNTING"
    assert adm.trusted_group(settings, "𝗜𝗡 𝗖𝗥𝗬𝗣𝗧𝗢") == "IN CRYPTO"                  # écriture stylisée du groupe
    assert adm.trusted_group(settings, "AL-MAHWASHI VIP") == "AL-MAHWASHI VIP"
    assert adm.trusted_group(settings, "-1003226951554") == "-1003226951554"
    assert adm.trusted_group(settings, "telegram 42", "") == ""
    assert adm.trusted_group(settings, "WHALE") == "" and adm.trusted_group(settings, "IN CRYPTO SIGNALS") == ""


def test_a_trusted_group_adds_a_pair_as_the_owner_decision(settings):
    entry = adm.admit_from_trusted_group(settings, "DOGEUSDT", group="WHALE HUNTING", now=NOW, lookup=listing)
    assert entry["decision"] == adm.AJOUTEE and entry["decided_by"] == adm.OWNER
    assert "groupe de confiance halal « WHALE HUNTING »" in entry["reason"]
    assert UserUniverse(settings.external_db).get("DOGEUSDT")["status"] == REQUESTED
    missing = adm.admit_from_trusted_group(settings, "XTZUSDT", group="WHALE HUNTING", now=NOW, lookup=listing)
    assert missing["decision"] == adm.INDISPONIBLE


def test_owner_refusals_and_haram_opinions_stay_refused(settings):
    adm.decide(settings, "LTCUSDT", add=False, now=NOW, lookup=listing)
    assert adm.admit_from_trusted_group(settings, "LTCUSDT", group="IN CRYPTO", now=NOW,
                                        lookup=listing)["decision"] == adm.REFUSEE
    uni = adm.admit_from_trusted_group(settings, "UNIUSDT", group="IN CRYPTO", now=NOW, lookup=listing)
    assert uni["decision"] == adm.REFUSEE and uni["decided_by"] == adm.RULE
    universe = UserUniverse(settings.external_db)
    assert universe.get("LTCUSDT") is None and universe.get("UNIUSDT") is None


def test_evaluating_a_trusted_group_signal_adds_the_pair_and_others_still_wait(settings):
    trusted = evaluate(settings, SIGNAL.format("DOGE"), source="WHALE HUNTING", now=NOW, tick_size_lookup=listing)
    assert trusted.verdict == "EN_ATTENTE" and "groupe de confiance halal" in trusted.failed[0].detail
    assert adm.AdmissionLog(settings.external_db).get("DOGEUSDT")["decided_by"] == adm.OWNER
    other = evaluate(settings, SIGNAL.format("RARE").replace("👑 WHALE HUNTING\n", "AUTRE GROUPE\n"),
                     source="AUTRE GROUPE", now=NOW, tick_size_lookup=listing)
    assert other.verdict == "EN_ATTENTE" and "en attente de ta décision" in other.failed[0].detail
    assert adm.AdmissionLog(settings.external_db).get("RAREUSDT")["decision"] == adm.A_DECIDER
    assert UserUniverse(settings.external_db).get("RAREUSDT") is None


def test_a_pending_pair_already_present_becomes_the_owner_decision(settings):
    universe = UserUniverse(settings.external_db)
    universe.request("RAREUSDT", Decimal("0.001"), reason="mode test auto_add_pairs (source « g »)", now=NOW)
    universe.mark_ready("RAREUSDT", now=NOW)
    adm.admit_all(settings, now=NOW, lookup=listing)
    assert adm.AdmissionLog(settings.external_db).get("RAREUSDT")["decision"] == adm.A_DECIDER
    result = evaluate(settings, SIGNAL.format("RARE"), source="telegram 1", now=NOW, tick_size_lookup=listing)
    assert result.failed[0].label != "paire dans l'univers"                         # plus retenue
    assert adm.AdmissionLog(settings.external_db).get("RAREUSDT")["decided_by"] == adm.OWNER


def test_a_signal_submitted_by_hand_keeps_its_own_rule(settings):
    manual = evaluate(settings, SIGNAL.format("DOGE"), source="WHALE HUNTING", now=NOW, user_validated=True,
                      tick_size_lookup=listing)
    assert "sur ta validation" in manual.failed[0].detail


def test_the_live_relay_admits_pairs_of_trusted_conversations_only(settings, monkeypatch):
    monkeypatch.setattr(adm, "binance_listing", listing)
    api = CsiApi(settings, now=lambda: NOW)
    rows = [{"signal_id": "c:1", "source_chat_id": "-1003226951554", "raw_text": SIGNAL.format("DOGE"),
             "received_at": NOW.isoformat()},
            {"signal_id": "c:2", "source_chat_id": "-100999", "raw_text": SIGNAL.format("RARE").replace(
                "👑 WHALE HUNTING\n", "AUTRE GROUPE\n"), "received_at": NOW.isoformat()},
            {"signal_id": "c:3", "source_chat_id": "-1003742935429", "raw_text": SIGNAL.format("UNI"),
             "received_at": NOW.isoformat()}]
    answer = api.telegram_live({"signals": rows})
    decisions = {a["symbol"]: a["decision"] for a in answer["admissions"]}
    assert decisions == {"DOGEUSDT": adm.AJOUTEE, "UNIUSDT": adm.REFUSEE}
    assert answer["deposited"] == 3                                                  # le dépôt pour F4 est intact
    assert UserUniverse(settings.external_db).get("RAREUSDT") is None
