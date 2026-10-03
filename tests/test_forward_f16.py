"""F16_TELEGRAM_IMAGES : la file des images devient des décisions au format de F4 ; entrée calée sur l'heure où le
signal est devenu jouable (réception pour SUR, validation pour A_VALIDER) ; images ignorées, refusées ou hors
screening comptées, jamais jouées. Données SYNTHÉTIQUES."""
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


def image(settings, message: str, received: datetime, status: str, text: str | None) -> str:
    ident = iq.add(settings, image=PNG + message.encode(), ext="png", chat="-100", message_id=message,
                   received_at=received.isoformat(), caption="#SOL", now=received)["id"]
    iq.record_read(settings, ident, status=status, text=text, notes=[] if status == iq.SURE else ["x"], code="ocr1",
                   now=received)
    return ident


def test_images_become_f4_decisions_at_the_right_time(settings, tmp_path):
    sure = image(settings, "1", STARTED + timedelta(hours=1), iq.SURE, SOL)
    late = image(settings, "2", STARTED + timedelta(hours=2), iq.TO_VALIDATE, SOL)
    validated_at = STARTED + timedelta(hours=7)
    iq.decide(settings, late, accept=True, text=None, now=validated_at)
    refused = image(settings, "3", STARTED + timedelta(hours=3), iq.TO_VALIDATE, SOL)
    iq.decide(settings, refused, accept=False, text=None, now=STARTED + timedelta(hours=4))
    image(settings, "4", STARTED + timedelta(hours=5), iq.IGNORED, None)
    image(settings, "5", STARTED + timedelta(hours=6), iq.SURE, XYZ)                     # hors liste halal figée
    image(settings, "6", STARTED - timedelta(hours=1), iq.SURE, SOL)                      # avant le démarrage
    journal = Journal(tmp_path / "f16.jsonl")
    assert f16.record_decisions(settings, journal, start(), now=NOW) == {"decisions": 2, "counted": 3}
    assert f16.record_decisions(settings, journal, start(), now=NOW) == {"decisions": 0, "counted": 0}   # une fois
    decisions = {e["data"]["signal_id"]: e["data"] for e in journal.entries({f4.DECISION})}
    first = decisions[f"image:{sure}"]
    assert pd.Timestamp(first["entry_at"]) == f4.entry_time((STARTED + timedelta(hours=1)).isoformat())
    assert pd.Timestamp(first["entry_at"]) == STARTED + timedelta(hours=1, minutes=1)
    validated = decisions[f"image:{late}"]
    assert pd.Timestamp(validated["entry_at"]) >= validated_at + timedelta(seconds=60)   # jamais avant la validation
    assert validated["image_status"] == iq.VALIDATED and validated["symbol"] == "SOLUSDT"
    assert len(validated["placebo_minutes"]) == f4.PLACEBOS and validated["image_sha256"]
    counted = sorted(e["data"]["status"] for e in journal.entries({f4.COUNTED}))
    assert counted == sorted([iq.REFUSED, iq.IGNORED, f4.OUT_OF_SCREEN])


def test_f16_reuses_f4_rules_and_freezes_them():
    assert set(f4.TEST.frozen_modules) <= set(f16.TEST.frozen_modules)
    assert "crypto_signal_intelligence.external.image_queue" in f16.TEST.frozen_modules
    assert f16.TEST.frozen_functions == f4.TEST.frozen_functions and f16.TEST.params["f4_params"] == f4.TEST.params
