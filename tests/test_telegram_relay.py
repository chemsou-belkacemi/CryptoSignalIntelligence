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
    assert relay.cycle().items() >= {"updates": 2, "relayed": 2, "waiting": 0}.items()
    assert relay.offset() == 3
    assert relay.cycle().items() >= {"updates": 0, "relayed": 0, "waiting": 0}.items()   # rien de relu
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


PHOTO = [{"file_id": "small", "file_size": 1000, "width": 90, "height": 60},
         {"file_id": "big", "file_size": 200_000, "width": 1280, "height": 720},
         {"file_id": "huge", "file_size": 9_000_000, "width": 4000, "height": 3000}]


class PhotoHttp(FakeHttp):
    def __init__(self, updates, *, api_up=True):
        super().__init__(updates, api_up=api_up)
        self.images = []

    def __call__(self, method, url, body, headers, timeout):
        if url.endswith("/getFile"):
            assert body == {"file_id": "big"}                       # la plus grande sous 4 Mo
            return {"ok": True, "result": {"file_path": "photos/file_1.jpg"}}
        if url.endswith("/telegram/image"):
            if not self.api_up:
                raise ConnectionError("CSI arrêté")
            self.images.append(body)
            return {"id": "x", "new": True}
        return super().__call__(method, url, body, headers, timeout)


def test_photos_are_forwarded_with_caption_and_reception_time(tmp_path):
    http = PhotoHttp([update(5, text=None, photo=PHOTO, caption="#SOL")])
    downloads = []
    relay = tg.Relay(config(tmp_path), http=http, download=lambda url, timeout: downloads.append(url) or b"jpegdata")
    out = relay.cycle()
    assert out["photos_sent"] == 1 and out["relayed"] == 1                          # la légende part aussi vers F4
    [image] = http.images
    assert image["caption"] == "#SOL" and image["chat"] == "-100" and image["message_id"] == "50"
    assert image["received_at"] == at(1790000005) and image["ext"] == "jpg"
    assert downloads == [f"https://api.telegram.org/file/bot{TOKEN}/photos/file_1.jpg"]


def test_photos_wait_when_csi_is_down(tmp_path):
    http = PhotoHttp([update(6, text=None, photo=PHOTO)], api_up=False)
    relay = tg.Relay(config(tmp_path), http=http, download=lambda url, timeout: b"jpegdata")
    out = relay.cycle()
    assert out["photos_waiting"] == 1 and relay.offset() == 7                      # décalage avancé : photo gardée
    http.api_up = True
    assert relay.cycle()["photos_sent"] == 1 and len(http.images) == 1


def test_photo_errors_never_show_the_token(tmp_path, caplog):
    def boom(url, timeout):
        raise ConnectionError(f"échec sur {url}")

    relay = tg.Relay(config(tmp_path), http=PhotoHttp([update(7, text=None, photo=PHOTO)]), download=boom)
    relay.cycle()
    assert TOKEN not in caplog.text and "<jeton>" in caplog.text


def test_job_carries_file_id_and_forward_origin():
    job = tg.photo_job(update(8, text=None, photo=[{"file_id": "f", "file_unique_id": "uniq", "file_size": 10, "width": 9,
                                                    "height": 9}], forward_origin={"type": "channel", "chat": {"id": -1009}}))
    assert job["file_unique_id"] == "uniq" and job["origin_chat"] == "-1009"


@pytest.mark.parametrize(("error", "kept"), [("HTTP 400 : image refusée", False), ("HTTP 413 : trop lourde", False),
                                             ("HTTP 401 : jeton", True), ("HTTP 403 : pas de jeton", True),
                                             ("HTTP 429 : trop de requêtes", True), ("HTTP 502 : panne", True)])
def test_only_content_refusals_drop_a_photo(tmp_path, error, kept):
    class Refusing(PhotoHttp):
        def __call__(self, method, url, body, headers, timeout):
            if url.endswith("/telegram/image"):
                raise tg.RelayError(error)
            return super().__call__(method, url, body, headers, timeout)

    relay = tg.Relay(config(tmp_path), http=Refusing([update(9, text=None, photo=PHOTO)]), download=lambda u, t: b"x")
    out = relay.cycle()
    assert out["photos_sent"] == 0 and out["photos_waiting"] == int(kept)
    assert len(list((tmp_path / "photos_en_attente").glob("*.json"))) == int(kept)
    assert (tmp_path / "photos_refusees.jsonl").exists() is (not kept)


def test_telegram_errors_keep_the_photo_waiting(tmp_path):
    class Busy(PhotoHttp):
        def __call__(self, method, url, body, headers, timeout):
            if url.endswith("/getFile"):
                raise tg.RelayError("HTTP 429 : trop de requêtes")
            return super().__call__(method, url, body, headers, timeout)

    out = tg.Relay(config(tmp_path), http=Busy([update(10, text=None, photo=PHOTO)]), download=lambda u, t: b"x").cycle()
    assert out["photos_waiting"] == 1


def test_real_http_client_logs_never_show_the_token(tmp_path, caplog):
    """httpx journalise chaque requête avec son URL (le jeton) au niveau INFO : quiet_http_logs le coupe."""
    import logging

    import httpx

    def handler(request):
        if "getUpdates" in request.url.path:
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)

    def http(method, url, body, headers, timeout):
        with httpx.Client(transport=transport) as client:
            return client.request(method, url, json=body, headers=headers).json()

    caplog.set_level(logging.INFO)
    tg.quiet_http_logs()
    tg.Relay(config(tmp_path), http=http).cycle()
    assert TOKEN not in caplog.text
