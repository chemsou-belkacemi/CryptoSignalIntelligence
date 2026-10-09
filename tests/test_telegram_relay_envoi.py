"""Sens retour du relais Telegram (2026-10-09) : le propriétaire se fait connaître par `/start` en privé (ou par
`CSI_TELEGRAM_OWNER_CHAT_ID`), les appels de l'assistant de marché mis en attente par l'API CSI lui sont envoyés
puis marqués. Aucun réseau : un faux client HTTP simule Telegram et le contrat de l'API CSI
(`GET /assistant/outbox` → {"messages": [...]}, `POST /assistant/sent` → {"marked": n})."""
from __future__ import annotations

import json
import logging

import pytest

from crypto_signal_intelligence.relay import telegram as tg

TOKEN = "123456789:" + "B" * 35
SIGNAL = "#SOL/USDT\nEntry1: 100\nTP1: 104\nStop: 95"
OWNER, STRANGER, GROUP = 4242001234, 9999005678, -1001234


def private(update_id: int, chat: int, text: str, username: str = "chamsou") -> dict:
    return {"update_id": update_id, "message": {"message_id": update_id * 10, "date": 1790000000 + update_id, "text": text,
                                                "chat": {"id": chat, "type": "private"},
                                                "from": {"id": chat, "username": username}}}


def group(update_id: int, text: str = SIGNAL) -> dict:
    return {"update_id": update_id, "message": {"message_id": update_id * 10, "date": 1790000000 + update_id, "text": text,
                                                "chat": {"id": GROUP, "type": "supergroup"}}}


def config(tmp_path, **changes) -> tg.RelayConfig:
    return tg.RelayConfig(**({"token": TOKEN, "api_url": "http://csi-api:8503", "api_token": "api", "state_dir": tmp_path}
                             | changes))


class FakeHttp:
    """Telegram (getUpdates, sendMessage) et l'API CSI (/telegram/live, /assistant/outbox, /assistant/sent, /assistant)."""

    def __init__(self, updates: list[dict], *, outbox: list[dict] | None = None, api_up: bool = True,
                 outbox_up: bool = True, mark_up: bool = True, state: dict | None = None):
        self.updates, self.outbox, self.api_up, self.outbox_up, self.mark_up = updates, list(outbox or []), api_up, outbox_up, mark_up
        self.state = state
        self.posted: list[list[dict]] = []
        self.sent: list[dict] = []
        self.marked: list[list[str]] = []
        self.calls: list[str] = []
        self.rate_limits: list[str] = []          # réponses 429 à servir, dans l'ordre, aux prochains sendMessage

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append(f"{method} {url}")
        if url.endswith("/getUpdates"):
            return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= body["offset"]]}
        if url.endswith("/sendMessage"):
            if self.rate_limits:
                raise tg.RelayError(self.rate_limits.pop(0))
            self.sent.append(body)
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        if not self.api_up:
            raise ConnectionError("CSI arrêté")
        assert headers["Authorization"] == "Bearer api"
        if url.endswith("/telegram/live"):
            self.posted.append(body["signals"])
            return {"deposited": len(body["signals"])}
        if url.endswith("/assistant/outbox"):
            if not self.outbox_up:
                raise ConnectionError("route absente")
            assert method == "GET" and body is None
            return {"messages": self.outbox[:20]}
        if url.endswith("/assistant/sent"):
            if not self.mark_up:
                raise ConnectionError("CSI arrêté")
            assert method == "POST"
            self.marked.append(list(body["ids"]))
            self.outbox = [m for m in self.outbox if m["id"] not in body["ids"]]
            return {"marked": len(body["ids"])}
        if url.endswith("/assistant"):
            if self.state is None:
                raise tg.RelayError("HTTP 404 : route inconnue")
            return self.state
        raise AssertionError(f"adresse inattendue : {url}")


def message(ident: str, text: str = "<BTC/USDT> appel : rien à faire, marché > sans direction") -> dict:
    return {"id": ident, "created_at": "2026-10-09T10:00:00+00:00", "text": text}


# --- /start et propriétaire ----------------------------------------------------------------------------------------

def test_first_private_start_registers_the_owner(tmp_path):
    http = FakeHttp([private(1, OWNER, "/start")])
    relay = tg.Relay(config(tmp_path), http=http)
    assert relay.owner() is None
    relay.cycle()
    saved = json.loads((tmp_path / "proprietaire.json").read_text(encoding="utf-8"))
    assert saved["chat_id"] == str(OWNER) and saved["first_seen"].startswith("20")
    assert saved["username_masked"] == "ch…" and "chamsou" not in json.dumps(saved)
    assert relay.owner() == str(OWNER)
    assert [s["chat_id"] for s in http.sent] == [str(OWNER)] and "ordre" in http.sent[0]["text"]


def test_a_second_private_chat_is_refused_once_and_never_registered(tmp_path):
    http = FakeHttp([private(1, OWNER, "/start@CsiBot"), private(2, STRANGER, "/start", username="intrus")])
    relay = tg.Relay(config(tmp_path), http=http)
    relay.cycle()
    assert relay.owner() == str(OWNER)
    assert [(s["chat_id"], s["text"]) for s in http.sent][1] == (str(STRANGER), "Ce bot est privé.")
    http.updates.append(private(3, STRANGER, "/start"))          # insiste : plus aucune réponse
    relay.cycle()
    assert [s["chat_id"] for s in http.sent].count(str(STRANGER)) == 1
    assert json.loads((tmp_path / "proprietaire.json").read_text(encoding="utf-8"))["chat_id"] == str(OWNER)


def test_environment_owner_wins_and_needs_no_start(tmp_path):
    cfg = tg.RelayConfig.from_env({"CSI_TELEGRAM_RELAY_TOKEN": TOKEN, "CSI_TELEGRAM_OWNER_CHAT_ID": f" {OWNER} ",
                                   "CSI_TELEGRAM_RELAY_STATE": str(tmp_path)})
    assert cfg.owner_chat_id == str(OWNER)
    http = FakeHttp([private(1, STRANGER, "/start")], outbox=[message("m1")])
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    out = relay.cycle()
    assert relay.owner() == str(OWNER) and not (tmp_path / "proprietaire.json").exists()
    assert out["assistant_sent"] == 1 and http.sent[-1]["chat_id"] == str(OWNER)      # l'appel part sans /start
    assert [s["text"] for s in http.sent if s["chat_id"] == str(STRANGER)] == ["Ce bot est privé."]


def test_commands_are_never_deposited_in_f4(tmp_path):
    assert tg.to_row(private(1, OWNER, "/start")) is None
    assert tg.to_row(private(1, OWNER, "/start@CsiBot")) is None
    assert tg.to_row(private(1, OWNER, "/etat")) is None
    assert tg.to_row(private(1, OWNER, "#BTC/USDT long")) is not None                  # un signal privé passe
    assert tg.to_row(group(1, "/start")) is not None                                    # un groupe n'a pas de commandes
    http = FakeHttp([private(1, OWNER, "/start"), group(2)])
    tg.Relay(config(tmp_path), http=http).cycle()
    assert [r["raw_text"] for batch in http.posted for r in batch] == [SIGNAL]


# --- envoi des appels de l'assistant --------------------------------------------------------------------------------

def test_calls_are_sent_as_plain_text_then_marked(tmp_path):
    http = FakeHttp([], outbox=[message("m1"), message("m2", "<ETH/USDT> prudence")])
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    out = relay.cycle()
    assert out["assistant_sent"] == 2 and out["assistant_failed"] == 0 and out["owner"] is True
    assert http.sent == [{"chat_id": str(OWNER), "text": message("m1")["text"], "disable_web_page_preview": True},
                         {"chat_id": str(OWNER), "text": "<ETH/USDT> prudence", "disable_web_page_preview": True}]
    assert all("parse_mode" not in s for s in http.sent)
    assert http.marked == [["m1", "m2"]]
    assert http.calls.index("GET http://csi-api:8503/assistant/outbox") < http.calls.index("POST http://csi-api:8503/assistant/sent")
    assert relay.cycle()["assistant_sent"] == 0 and http.marked == [["m1", "m2"]]       # plus rien en attente


def test_never_more_than_twenty_sends_per_cycle(tmp_path):
    http = FakeHttp([], outbox=[message(f"m{i}") for i in range(30)])
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    assert relay.cycle()["assistant_sent"] == 20 and len(http.marked[0]) == 20
    assert relay.cycle()["assistant_sent"] == 10


def test_without_owner_nothing_is_sent_nor_marked_and_the_log_says_so_hourly(tmp_path, caplog):
    now = [1_000.0]
    http = FakeHttp([], outbox=[message("m1")])
    relay = tg.Relay(config(tmp_path), http=http, clock=lambda: now[0])
    with caplog.at_level(logging.INFO, logger="csi.relay.telegram"):
        out = relay.cycle()
        assert out["assistant_sent"] == 0 and out["owner"] is False
        assert http.sent == [] and http.marked == [] and not any("/assistant" in c for c in http.calls)
        now[0] += 1800
        relay.cycle()
        assert caplog.text.count("aucun propriétaire connu") == 1                          # pas avant une heure
        now[0] += 1800
        relay.cycle()
        assert caplog.text.count("aucun propriétaire connu") == 2
    assert TOKEN not in caplog.text and str(OWNER) not in caplog.text


def test_a_429_is_waited_once_then_the_rest_waits_for_the_next_cycle(tmp_path):
    http = FakeHttp([], outbox=[message("m1"), message("m2"), message("m3")])
    http.rate_limits = ['HTTP 429 : {"ok":false,"error_code":429,"description":"Too Many Requests: retry after 7",'
                        '"parameters":{"retry_after":7}}']
    waits: list[float] = []
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http, sleep=waits.append)
    out = relay.cycle()
    assert waits == [7.0] and out["assistant_sent"] == 3 and http.marked == [["m1", "m2", "m3"]]

    http.outbox, http.marked = [message("m4"), message("m5")], []
    http.rate_limits = ["HTTP 429 : retry after 3", "HTTP 429 : retry after 3"]        # Telegram insiste
    out = relay.cycle()
    assert waits == [7.0, 3.0] and out["assistant_sent"] == 0 and out["assistant_failed"] == 1
    assert http.marked == [] and http.outbox == [message("m4"), message("m5")]         # tout attend le passage suivant
    assert relay.cycle()["assistant_sent"] == 2


def test_unreachable_csi_api_never_breaks_the_relay(tmp_path, caplog):
    # Route /assistant absente ou en panne : les signaux sont quand même déposés dans F4.
    http = FakeHttp([group(1)], outbox_up=False)
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    out = relay.cycle()
    assert out["relayed"] == 1 and out["assistant_sent"] == 0 and out["assistant_failed"] == 0 and http.sent == []
    assert relay.offset() == 2
    # API CSI entièrement arrêtée : les signaux attendent en local, le passage se termine sans erreur.
    down = FakeHttp([group(2)], api_up=False)
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=down)
    with caplog.at_level(logging.WARNING):
        out = relay.cycle()
    assert out["waiting"] == 1 and out["assistant_sent"] == 0 and (tmp_path / "en_attente.jsonl").exists()
    assert TOKEN not in caplog.text


def test_marking_failure_keeps_ids_locally_and_never_resends(tmp_path):
    http = FakeHttp([], outbox=[message("m1")], mark_up=False)
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    assert relay.cycle()["assistant_sent"] == 1
    assert json.loads((tmp_path / "a_marquer.json").read_text(encoding="utf-8")) == ["m1"]
    relay.cycle()
    assert len(http.sent) == 1                                                            # m1 n'est pas renvoyé
    http.mark_up = True
    relay.cycle()
    assert http.marked == [["m1"]] and not (tmp_path / "a_marquer.json").exists()


def test_the_token_never_appears_in_logs_even_when_telegram_fails(tmp_path, caplog):
    class Failing(FakeHttp):
        def __call__(self, method, url, body, headers, timeout):
            if url.endswith("/sendMessage"):
                raise ConnectionError(f"impossible de joindre {url}")
            return super().__call__(method, url, body, headers, timeout)

    http = Failing([private(1, OWNER, "/start")], outbox=[message("m1")])
    relay = tg.Relay(config(tmp_path), http=http)
    with caplog.at_level(logging.INFO):
        out = relay.cycle()
    assert out["assistant_failed"] == 1 and out["assistant_sent"] == 0 and http.marked == []
    assert TOKEN not in caplog.text and "<jeton>" in caplog.text
    assert str(OWNER) not in caplog.text and tg.masked_chat(OWNER) in caplog.text       # chat masqué (4 derniers chiffres)


def test_masked_chat_hides_the_last_four_digits():
    assert tg.masked_chat(4242001234) == "424200****" and tg.masked_chat("-100123") == "-10****"
    assert tg.masked_chat("12") == "****" and tg.masked_chat(None) == "****"


# --- /etat -----------------------------------------------------------------------------------------------------------

def test_etat_replies_with_five_lines_from_the_api(tmp_path):
    state = {"feu": "VERT", "derniere_lecture": "2026-10-09T10:00:00+00:00", "appels_en_attente": 0, "details": {"x": 1},
             "a": 1, "b": 2, "c": 3}
    http = FakeHttp([private(1, OWNER, "/etat")], state=state)
    tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http).cycle()
    [reply] = http.sent
    assert reply["chat_id"] == str(OWNER) and reply["text"].splitlines() == [
        "feu : VERT", "derniere_lecture : 2026-10-09T10:00:00+00:00", "appels_en_attente : 0", "a : 1", "b : 2"]
    assert tg.summarize_state({"resume": "l1\nl2\nl3\nl4\nl5\nl6"}) == "l1\nl2\nl3\nl4\nl5"


def test_etat_says_unavailable_without_the_route_and_ignores_strangers(tmp_path):
    http = FakeHttp([private(1, OWNER, "/etat"), private(2, STRANGER, "/etat")])
    tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http).cycle()
    assert [(s["chat_id"], s["text"]) for s in http.sent] == [(str(OWNER), "assistant indisponible")]


def test_retry_after_is_read_from_telegram_errors():
    assert tg._retry_after('HTTP 429 : {"parameters":{"retry_after":12}}') == 12
    assert tg._retry_after("HTTP 429 : Too Many Requests: retry after 4") == 4
    assert tg._retry_after("HTTP 429 : sans délai") == 1


@pytest.mark.parametrize("text", ["/start", "/START", "/start@MonBot", "/etat maintenant"])
def test_command_detection(text):
    assert tg.command_of(private(1, OWNER, text)) is not None
    assert tg.command_of(group(1, text)) is None
    assert tg.command_of(private(1, OWNER, "/inconnue")) is None
