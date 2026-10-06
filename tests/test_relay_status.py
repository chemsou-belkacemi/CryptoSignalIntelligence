"""État du relais Telegram dans le tableau de bord (2026-10-06) : dernier message, nombre par groupe. Aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.forward import telegram_live

NOW = datetime(2026, 10, 6, 22, tzinfo=UTC)


def signal(ident, chat, header, received):
    text = f"👑 {header}\nPAIR: DOGE/USDT\nENTRY 1: 0.2\nT1: 0.22\nSL: 0.18"
    return {"signal_id": ident, "source_chat_id": chat, "raw_text": text, "received_at": received}


def test_the_relay_card_counts_messages_by_group(settings):
    rows = [signal("c:1", "-1003226951554", "WHALE HUNTING", "2026-10-06T21:00:00+00:00"),
            signal("c:2", "-1003226951554", "WHALE HUNTING", "2026-10-02T10:00:00+00:00"),
            signal("c:3", "-1001909237586", "IN CRYPTO", "2026-10-06T12:00:00+00:00"),
            signal("c:4", "-1001909237586", "IN CRYPTO", "2026-09-20T12:00:00+00:00")]      # plus de 7 jours
    telegram_live.store_drop(settings, rows, now=NOW)
    out = CsiApi(settings, now=lambda: NOW).dispatch("GET", "/telegram/relay", {}, None)
    assert out["week"] == 3 and out["day"] == 2 and out["silent_hours"] == 1.0
    groups = {g["group"]: (g["day"], g["week"]) for g in out["groups"]}
    # Une ligne par conversation, sous le nom réglé (config : chat_names), même pour un message sans nom lisible.
    assert groups == {"WHALE HUNTING": (1, 2), "IN CRYPTO": (1, 1)}


def test_an_unnamed_conversation_shows_its_number(settings):
    rows = [{"signal_id": "c:9", "source_chat_id": "-100555", "raw_text": "TP1 ✅", "received_at": "2026-10-06T21:00:00+00:00"}]
    telegram_live.store_drop(settings, rows, now=NOW)
    out = CsiApi(settings, now=lambda: NOW).dispatch("GET", "/telegram/relay", {}, None)
    assert [g["group"] for g in out["groups"]] == ["conversation -100555"]


def test_no_message_means_no_last_time(settings):
    out = CsiApi(settings, now=lambda: NOW).dispatch("GET", "/telegram/relay", {}, None)
    assert out["last_received_at"] is None and out["week"] == 0 and out["groups"] == []
