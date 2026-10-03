"""File des signaux Telegram en image (external/image_queue.py) : dépôt unique, lecture OCR et issues, validation
du propriétaire (jouable à l'heure de la VALIDATION, niveaux corrigés relus), refus, signaux jouables."""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_intelligence.external import image_queue as iq

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
TEXT = "#SOL/USDT\nEntry1: 108.4\nTP1: 111.8\nStop: 103.15"


def put(settings, message_id: str, received: datetime = NOW) -> str:
    return iq.add(settings, image=PNG, ext="png", chat="-100", message_id=message_id, received_at=received.isoformat(),
                  caption="#SOL", now=NOW)["id"]


def test_add_once_per_message_and_refuse_bad_files(settings):
    first = iq.add_b64(settings, {"chat": "-100", "message_id": "7", "received_at": NOW.isoformat(), "caption": "#SOL",
                                  "ext": "png", "image_b64": base64.b64encode(PNG).decode()}, now=NOW)
    assert first["new"] is True and put(settings, "7") == first["id"]
    assert iq.counts(settings) == {iq.RECEIVED: 1}
    with pytest.raises(ValueError):
        iq.add(settings, image=PNG, ext="exe", chat="1", message_id="1", received_at=NOW.isoformat(), caption="", now=NOW)
    with pytest.raises(ValueError):
        iq.add_b64(settings, {"chat": "1", "message_id": "2", "received_at": NOW.isoformat(), "image_b64": "%%%"}, now=NOW)


def test_reading_and_playability(settings):
    sure, check, junk, broken = (put(settings, m) for m in ("1", "2", "3", "4"))
    outcomes = {sure: (iq.SURE, TEXT, []), check: (iq.TO_VALIDATE, TEXT, ["paire donnée par la légende seule"]),
                junk: (iq.IGNORED, None, ["DESACCORD_OCR"])}

    order = iter([sure, check, junk, broken])

    def read(path, caption):
        ident = next(order)
        if ident == broken:
            raise RuntimeError("cv2.error")
        status, text, notes = outcomes[ident]
        return {"status": status, "text": text, "notes": notes}

    assert iq.process(settings, read, code="abc", now=NOW) == {iq.SURE: 1, iq.TO_VALIDATE: 1, iq.IGNORED: 2}
    playable = iq.playable(settings, since=(NOW - timedelta(days=1)).isoformat())
    assert [p["id"] for p in playable] == [sure] and playable[0]["playable_at"] == NOW.isoformat()
    waiting = iq.pending(settings)
    assert [w["id"] for w in waiting] == [check] and waiting[0]["image"].startswith("data:image/png;base64,")
    assert waiting[0]["ocr_notes"] == ["paire donnée par la légende seule"]


def test_owner_validation_plays_at_validation_time_and_rereads_corrections(settings):
    ident = put(settings, "9")
    iq.record_read(settings, ident, status=iq.TO_VALIDATE, text=TEXT, notes=["x"], code="abc", now=NOW)
    later = NOW + timedelta(hours=5)
    with pytest.raises(ValueError, match="illisibles"):
        iq.decide(settings, ident, accept=True, text="n'importe quoi", now=later)
    corrected = TEXT.replace("Stop: 103.15", "Stop: 103.5")
    assert iq.decide(settings, ident, accept=True, text=corrected, now=later)["symbol"] == "SOLUSDT"
    [row] = iq.playable(settings, since=NOW.isoformat())
    assert row["playable_at"] == later.isoformat() and row["final_text"] == corrected
    with pytest.raises(ValueError, match="déjà traitée"):
        iq.decide(settings, ident, accept=False, text=None, now=later)


def test_refusal_is_never_playable(settings):
    ident = put(settings, "10")
    iq.record_read(settings, ident, status=iq.TO_VALIDATE, text=TEXT, notes=[], code="abc", now=NOW)
    assert iq.decide(settings, ident, accept=False, text=None, now=NOW)["status"] == iq.REFUSED
    assert iq.playable(settings, since="2000-01-01") == []


def test_api_routes(settings):
    from crypto_signal_intelligence.api.server import CsiApi
    api = CsiApi(settings, now=lambda: NOW)
    out = api.dispatch("POST", "/telegram/image", {}, {"chat": "-100", "message_id": "1", "received_at": NOW.isoformat(),
                                                       "caption": "#SOL", "ext": "png", "image_b64": base64.b64encode(PNG).decode()})
    iq.record_read(settings, out["id"], status=iq.TO_VALIDATE, text=TEXT, notes=["x"], code="abc", now=NOW)
    listed = api.dispatch("GET", "/images/pending", {}, None)
    assert [p["id"] for p in listed["pending"]] == [out["id"]] and listed["counts"] == {iq.TO_VALIDATE: 1}
    assert api.dispatch("POST", "/images/decide", {}, {"id": out["id"], "accept": True})["status"] == iq.VALIDATED
    from crypto_signal_intelligence.api.server import ApiError
    with pytest.raises(ApiError):
        api.dispatch("POST", "/images/decide", {}, {"id": out["id"], "accept": "oui"})
