"""Relais Telegram → F4 (relay/telegram.py) : conversion des messages au format que F4 lit déjà, conversations
autorisées, modifications et transferts, jeton jamais divulgué, aucun message perdu quand l'API CSI est arrêtée.
Aucun réseau : un faux client HTTP."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from crypto_signal_intelligence.forward.telegram_live import read_robot_file
from crypto_signal_intelligence.relay import telegram as tg

TOKEN = "123456789:" + "A" * 35
def at(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


SIGNAL = "#SOL/USDT\nEntry1: 100\nTP1: 104\nStop: 95"


def update(update_id: int, *, chat: int = -100, text: str | None = SIGNAL, kind: str = "message", **extra) -> dict:
    message = {"message_id": update_id * 10, "date": 1790000000 + update_id, "chat": {"id": chat}} | extra
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, kind: message}


def config(tmp_path, **changes) -> tg.RelayConfig:
    return tg.RelayConfig(token=TOKEN, api_url="http://csi-api:8503", api_token="api", state_dir=tmp_path) if not changes \
        else tg.RelayConfig(**({"token": TOKEN, "api_url": "http://csi-api:8503", "api_token": "api", "state_dir": tmp_path} | changes))


class FakeHttp:
    def __init__(self, updates: list[dict], *, api_up: bool = True):
        self.updates, self.api_up, self.posted, self.calls = updates, api_up, [], []

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append(url)
        if "api.telegram.org" in url:
            offset = body["offset"]
            return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}
        if not self.api_up:
            raise ConnectionError("CSI arrêté")
        assert headers["Authorization"] == "Bearer api"
        self.posted.append(body["signals"])
        return {"deposited": len(body["signals"])}


def test_rows_are_in_the_format_f4_already_reads(tmp_path):
    rows = [tg.to_row(update(1)), tg.to_row(update(2, text=None, caption="#ETH/USDT long"))]
    path = tmp_path / "drop.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    signals = read_robot_file(path)
    assert [s.text for s in signals] == [SIGNAL, "#ETH/USDT long"]
    assert signals[0].received_at == at(1790000001) and signals[0].chat == "-100"


def test_messages_without_text_or_outside_allowed_chats_are_ignored():
    assert tg.to_row(update(1, text=None)) is None
    assert tg.to_row(update(1, text="   ")) is None
    assert tg.to_row(update(1, chat=-200), chats=frozenset({"-100"})) is None
    assert tg.to_row(update(1, chat=-100), chats=frozenset({"-100"})) is not None
    assert tg.to_row({"update_id": 1, "callback_query": {}}) is None


def test_edits_and_forwards():
    edited = tg.to_row(update(3, kind="edited_message", edit_date=1790000999))
    assert edited["edited"] is True and edited["signal_id"].endswith(":edit1790000999")
    assert edited["received_at"] == at(1790000999)                   # l'heure de la modification
    forwarded = tg.to_row(update(4, chat=555, forward_origin={"type": "channel", "chat": {"id": -1009}, "date": 1}))
    assert forwarded["source_chat_id"] == "-1009" and forwarded["relay_chat_id"] == "555"
    assert forwarded["received_at"] == at(1790000004)               # arrivée chez le bot, pas l'original


def test_token_is_required_and_never_shown(tmp_path):
    with pytest.raises(tg.RelayError, match="CSI_TELEGRAM_RELAY_TOKEN"):
        tg.RelayConfig.from_env({})
    cfg = tg.RelayConfig.from_env({"CSI_TELEGRAM_RELAY_TOKEN": TOKEN, "CSI_TELEGRAM_RELAY_CHATS": "-100, -200",
                                   "CSI_TELEGRAM_RELAY_STATE": str(tmp_path)})
    assert cfg.chats == frozenset({"-100", "-200"}) and TOKEN not in repr(cfg)

    def failing(method, url, body, headers, timeout):
        raise ConnectionError(f"impossible de joindre {url}")

    relay = tg.Relay(cfg, http=failing)
    with pytest.raises(tg.RelayError) as error:
        relay.fetch()
    assert TOKEN not in str(error.value) and "<jeton>" in str(error.value)


def test_only_telegram_and_csi_are_called(tmp_path):
    http = FakeHttp([update(1)])
    tg.Relay(config(tmp_path), http=http).cycle()
    assert all(url.startswith(("https://api.telegram.org/bot", "http://csi-api:8503/")) for url in http.calls)


def test_relay_delivers_then_advances_the_offset(tmp_path):
    http = FakeHttp([update(1), update(2)])
    relay = tg.Relay(config(tmp_path), http=http)
    assert relay.cycle() == {"updates": 2, "relayed": 2, "waiting": 0}
    assert relay.offset() == 3
    assert relay.cycle() == {"updates": 0, "relayed": 0, "waiting": 0}            # rien de relu
    assert [r["signal_id"] for r in http.posted[0]] == ["-100:10", "-100:20"]


def test_nothing_is_lost_when_csi_is_down(tmp_path):
    http = FakeHttp([update(1)], api_up=False)
    relay = tg.Relay(config(tmp_path), http=http)
    assert relay.cycle()["waiting"] == 1
    assert relay.offset() == 2                                                    # Telegram ne le renverra plus…
    http.updates.append(update(2))
    assert relay.cycle()["waiting"] == 2                                          # …mais il attend en local
    http.api_up = True
    out = relay.cycle()
    assert [r["signal_id"] for batch in http.posted for r in batch] == ["-100:10", "-100:20"]
    assert out["waiting"] == 0 and not (tmp_path / "en_attente.jsonl").exists()
