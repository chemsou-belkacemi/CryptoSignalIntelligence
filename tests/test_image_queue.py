"""File des signaux Telegram en image (external/image_queue.py) : dépôt unique, heure future refusée, lecture OCR et
issues (une image SUR jouable à max(réception, lecture)), tentatives bornées, validation du propriétaire dans le délai
(jouable à l'heure de la VALIDATION, paire inchangeable, correction marquée), expiration, refus, routes de l'API."""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_intelligence.external import image_queue as iq

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
TEXT = "#SOL/USDT\nEntry1: 108.4\nTP1: 111.8\nStop: 103.15"


def put(settings, message_id: str, received: datetime = NOW) -> str:
    return iq.add(settings, image=PNG + message_id.encode(), ext="png", chat="-100", message_id=message_id,
                  received_at=received.isoformat(), caption="#SOL", now=NOW)["id"]


def test_add_once_per_message_and_refuse_bad_inputs(settings):
    first = iq.add_b64(settings, {"chat": "-100", "message_id": "7", "received_at": NOW.isoformat(), "caption": "#SOL",
                                  "ext": "png", "image_b64": base64.b64encode(PNG).decode(), "file_unique_id": "u7",
                                  "origin_chat": "-1009"}, now=NOW)
    assert first["new"] is True
    assert iq.add(settings, image=PNG, ext="png", chat="-100", message_id="7", received_at=NOW.isoformat(), caption="",
                  now=NOW) == {"id": first["id"], "new": False}
    with iq.connect(settings) as db:
        row = dict(db.execute("SELECT * FROM images").fetchone())
    assert row["file_unique_id"] == "u7" and row["origin_chat"] == "-1009"
    with pytest.raises(ValueError):
        iq.add(settings, image=PNG, ext="exe", chat="1", message_id="1", received_at=NOW.isoformat(), caption="", now=NOW)
    with pytest.raises(ValueError):
        iq.add_b64(settings, {"chat": "1", "message_id": "2", "received_at": NOW.isoformat(), "image_b64": "%%%"}, now=NOW)
    with pytest.raises(ValueError, match="futur"):
        put(settings, "8", NOW + timedelta(hours=1))


def test_sure_image_is_playable_only_once_read(settings):
    ident = put(settings, "1", NOW)
    read_at = NOW + timedelta(hours=3)
    iq.record_read(settings, ident, status=iq.SURE, text=TEXT, notes=[], code="abc", now=read_at)
    [row] = iq.playable(settings, since=NOW.isoformat())
    assert row["playable_at"] == read_at.isoformat()                 # jamais l'heure de réception si la lecture est plus tardive


def test_reading_outcomes_and_bounded_attempts(settings):
    sure, check, junk, broken = (put(settings, m) for m in ("1", "2", "3", "4"))
    outcomes = {sure: (iq.SURE, TEXT, []), check: (iq.TO_VALIDATE, TEXT, ["paire donnée par la légende seule"]),
                junk: (iq.IGNORED, None, ["DESACCORD_OCR"])}
    calls = []

    def read(path, caption):
        ident = next(k for k in (sure, check, junk, broken) if k not in calls)
        calls.append(ident)
        if ident == broken:
            raise RuntimeError("cv2.error")
        status, text, notes = outcomes[ident]
        return {"status": status, "text": text, "notes": notes}

    assert iq.process(settings, read, code="abc", now=NOW) == {iq.SURE: 1, iq.TO_VALIDATE: 1, iq.IGNORED: 2}
    assert [p["id"] for p in iq.playable(settings, since=(NOW - timedelta(days=1)).isoformat())] == [sure]
    waiting = iq.pending(settings)
    assert [w["id"] for w in waiting] == [check] and waiting[0]["image"].startswith("data:image/png;base64,")
    crash = put(settings, "5")
    with iq.connect(settings) as db:                                  # deux plantages déjà comptés
        db.execute("UPDATE images SET attempts = 2 WHERE id=?", (crash,))
    assert iq.process(settings, lambda *a: pytest.fail("ne doit plus lire"), code="abc", now=NOW) == {iq.IGNORED: 1}


def test_owner_validation_in_time_at_validation_time_same_pair(settings):
    ident = put(settings, "9")
    iq.record_read(settings, ident, status=iq.TO_VALIDATE, text=TEXT, notes=["x"], code="abc", now=NOW)
    later = NOW + timedelta(hours=1)
    with pytest.raises(ValueError, match="illisibles"):
        iq.decide(settings, ident, accept=True, text="n'importe quoi", now=later)
    with pytest.raises(ValueError, match="paire ne peut pas changer"):
        iq.decide(settings, ident, accept=True, text=TEXT.replace("SOL", "LINK"), now=later)
    corrected = TEXT.replace("Stop: 103.15", "Stop: 103.5")
    assert iq.decide(settings, ident, accept=True, text=corrected, now=later)["symbol"] == "SOLUSDT"
    [row] = iq.playable(settings, since=NOW.isoformat())
    assert row["playable_at"] == later.isoformat() and row["final_text"] == corrected and row["edited"] == 1
    with pytest.raises(ValueError, match="déjà traitée"):
        iq.decide(settings, ident, accept=False, text=None, now=later)


def test_validation_after_the_delay_is_refused(settings):
    ident = put(settings, "11")
    iq.record_read(settings, ident, status=iq.TO_VALIDATE, text=TEXT, notes=[], code="abc", now=NOW)
    too_late = NOW + timedelta(hours=iq.VALIDATION_DELAY_HOURS, minutes=1)
    with pytest.raises(ValueError, match="EXPIREE"):
        iq.decide(settings, ident, accept=True, text=None, now=too_late)
    assert iq.pending(settings, now=too_late) == [] and iq.counts(settings) == {iq.EXPIRED: 1}


def test_refusal_is_never_playable(settings):
    ident = put(settings, "10")
    iq.record_read(settings, ident, status=iq.TO_VALIDATE, text=TEXT, notes=[], code="abc", now=NOW)
    assert iq.decide(settings, ident, accept=False, text=None, now=NOW)["status"] == iq.REFUSED
    assert iq.playable(settings, since="2000-01-01") == []


def test_api_routes_require_a_token(settings, monkeypatch):
    from crypto_signal_intelligence.api.server import ApiError, CsiApi
    api = CsiApi(settings, now=lambda: NOW)
    body = {"chat": "-100", "message_id": "1", "received_at": NOW.isoformat(), "caption": "#SOL", "ext": "png",
            "image_b64": base64.b64encode(PNG).decode()}
    monkeypatch.delenv("CSI_API_TOKEN", raising=False)
    with pytest.raises(ApiError, match="jeton"):
        api.dispatch("POST", "/telegram/image", {}, body)
    monkeypatch.setenv("CSI_API_TOKEN", "test")
    out = api.dispatch("POST", "/telegram/image", {}, body)
    iq.record_read(settings, out["id"], status=iq.TO_VALIDATE, text=TEXT, notes=["x"], code="abc", now=NOW)
    listed = api.dispatch("GET", "/images/pending", {}, None)
    assert [p["id"] for p in listed["pending"]] == [out["id"]] and listed["counts"] == {iq.TO_VALIDATE: 1}
    assert api.dispatch("POST", "/images/decide", {}, {"id": out["id"], "accept": True})["status"] == iq.VALIDATED
    with pytest.raises(ApiError):
        api.dispatch("POST", "/images/decide", {}, {"id": out["id"], "accept": "oui"})
