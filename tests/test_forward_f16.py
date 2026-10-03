"""F16_TELEGRAM_IMAGES : la file des images devient des décisions au format de F4 ; entrée calée sur l'heure où le
signal est devenu jouable (lecture pour SUR, validation pour A_VALIDER) ; images ignorées, refusées, expirées,
doublons, légendes déjà lisibles et hors screening comptées, jamais jouées. Données SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from crypto_signal_intelligence.external import image_queue as iq
from crypto_signal_intelligence.forward import f4, f16
from crypto_signal_intelligence.forward.journal import Journal

STARTED = datetime(2026, 10, 3, 18, tzinfo=UTC)
NOW = STARTED + timedelta(days=1)
PNG = b"\x89PNG" + b"y" * 50
SOL = "#SOL/USDT\nEntry1: 108.4\nTP1: 111.8\nTP2: 114.25\nStop: 103.15"
XYZ = SOL.replace("SOL", "XYZ")


def start() -> dict:
    return {"started_at": STARTED.isoformat(), "final_at": (STARTED + timedelta(days=84)).isoformat(),
            "halal": {"symbols": ["SOLUSDT"]}}


def image(settings, message: str, at: datetime, status: str | None, text: str | None, *, content: bytes | None = None,
          caption: str = "#SOL", read_after: timedelta = timedelta(minutes=5)) -> str:
    ident = iq.add(settings, image=content or PNG + message.encode(), ext="png", chat="-100", message_id=message,
                   received_at=at.isoformat(), caption=caption, now=at)["id"]
    if status is not None:
        iq.record_read(settings, ident, status=status, text=text, notes=[] if status == iq.SURE else ["x"], code="ocr1",
                       now=at + read_after)
    return ident


def journal_and_run(settings, tmp_path, now=NOW):
    journal = Journal(tmp_path / "f16.jsonl")
    return journal, f16.record_decisions(settings, journal, start(), now=now)


def test_images_become_f4_decisions_at_the_right_time(settings, tmp_path):
    sure = image(settings, "1", STARTED + timedelta(hours=1), iq.SURE, SOL, read_after=timedelta(hours=3))
    late = image(settings, "2", STARTED + timedelta(hours=2), iq.TO_VALIDATE, SOL)
    validated_at = STARTED + timedelta(hours=3)
    iq.decide(settings, late, accept=True, text=None, now=validated_at)
    refused = image(settings, "3", STARTED + timedelta(hours=3), iq.TO_VALIDATE, SOL)
    iq.decide(settings, refused, accept=False, text=None, now=STARTED + timedelta(hours=4))
    image(settings, "4", STARTED + timedelta(hours=5), iq.IGNORED, None)
    image(settings, "5", STARTED + timedelta(hours=6), iq.SURE, XYZ)                     # hors liste halal figée
    image(settings, "6", STARTED - timedelta(hours=1), iq.SURE, SOL)                      # avant le démarrage
    image(settings, "7", STARTED + timedelta(hours=7), iq.SURE, SOL, content=PNG + b"1")  # même image que « 1 »
    image(settings, "8", STARTED + timedelta(hours=8), iq.SURE, SOL, caption=SOL)         # légende déjà lisible (F4)
    image(settings, "9", STARTED + timedelta(hours=9), iq.TO_VALIDATE, SOL)               # jamais décidée : expirée
    journal, counts = journal_and_run(settings, tmp_path)
    assert counts == {"decisions": 2, "counted": 6}
    assert f16.record_decisions(settings, journal, start(), now=NOW) == {"decisions": 0, "counted": 0}   # une fois
    decisions = {e["data"]["signal_id"]: e["data"] for e in journal.entries({f4.DECISION})}
    first = decisions[f"image:{sure}"]
    read = STARTED + timedelta(hours=4)
    assert first["playable_at"] == read.isoformat()                                       # lecture, pas réception
    assert pd.Timestamp(first["entry_at"]) == f4.entry_time(read.isoformat())
    validated = decisions[f"image:{late}"]
    assert pd.Timestamp(validated["entry_at"]) >= validated_at + timedelta(seconds=60)
    assert validated["image_status"] == iq.VALIDATED and validated["symbol"] == "SOLUSDT" and validated["read_at"]
    assert len(validated["placebo_minutes"]) == f4.PLACEBOS and validated["image_sha256"] and validated["ocr_text_sha256"]
    counted = sorted(e["data"]["status"] for e in journal.entries({f4.COUNTED}))
    assert counted == sorted([iq.REFUSED, iq.IGNORED, iq.EXPIRED, f4.OUT_OF_SCREEN, f16.DUPLICATE, f16.CAPTION_SIGNAL])


def test_images_left_unread_or_undecided_are_counted_at_the_end(settings, tmp_path):
    image(settings, "1", STARTED + timedelta(hours=1), None, None)                       # jamais lue
    end = STARTED + timedelta(days=84, hours=1)
    journal, counts = journal_and_run(settings, tmp_path, now=end)
    assert counts["counted"] == 1 and [e["data"]["status"] for e in journal.entries({f4.COUNTED})] == [f16.PENDING_AT_END]


def test_stats_add_image_outcomes(settings, tmp_path):
    image(settings, "1", STARTED + timedelta(hours=1), iq.SURE, SOL)
    image(settings, "2", STARTED + timedelta(hours=2), iq.IGNORED, None)
    journal, _ = journal_and_run(settings, tmp_path)
    out = f16.stats(journal, start(), now=NOW)
    assert out["images"]["by_outcome"] == {iq.SURE: 1, iq.IGNORED: 1}
    assert out["descriptive"]["SUR"]["decisions"] == 1 and out["descriptive"]["VALIDEE"]["decisions"] == 0
    assert out["providers"][f4.ALL]["signals"] == 2


def test_f16_reuses_f4_rules_and_freezes_them():
    assert set(f4.TEST.frozen_modules) <= set(f16.TEST.frozen_modules)
    assert "crypto_signal_intelligence.external.image_queue" in f16.TEST.frozen_modules
    assert f16.TEST.frozen_functions == f4.TEST.frozen_functions and f16.TEST.params["f4_params"] == f4.TEST.params
