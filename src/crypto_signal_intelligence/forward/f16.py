"""F16_TELEGRAM_IMAGES : signaux Telegram publiés en IMAGE, lus par OCR (ou validés par le propriétaire), joués avec
EXACTEMENT les règles de F4 (docs/FORWARD_TESTS.md, section F16_TELEGRAM_IMAGES).

Seule la source diffère : la file `external/image_queue.py` au lieu des messages texte. Un signal devient jouable à
sa réception (`SUR`) ou à la validation du propriétaire (`VALIDEE`) ; c'est cette heure-là qui sert de « réception »
aux règles de F4 (entrée 60 s après, jamais avant). Rejeu, mesures et verdict : fonctions de `forward/f4.py`,
réutilisées sans modification.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from ..config import Settings
from ..external import image_queue
from ..external.parser import group_of
from . import f4
from .journal import Journal, utc_iso
from .registry import ForwardTest
from .telegram_live import LiveSignal

TEST_ID = "F16_TELEGRAM_IMAGES"
SOURCE = "image"
REFUSED_STATUS, IGNORED_STATUS = "REFUSEE", "IGNOREE"


def to_signal(row: dict) -> LiveSignal:
    """Ligne jouable de la file → signal au format de F4 ; « réception » = heure où il est devenu jouable."""
    provider = group_of(row["caption"]) or f"chat {row['chat']}"
    return LiveSignal(id=f"{SOURCE}:{row['id']}", provider=provider, received_at=row["playable_at"],
                      text=row["final_text"], source=SOURCE, chat=row["chat"])


def _closed(settings: Settings, since: str) -> list[dict]:
    """Images ignorées ou refusées depuis le démarrage (comptées, jamais jouées)."""
    with image_queue.connect(settings) as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM images WHERE status IN (?, ?) AND received_at >= ? ORDER BY received_at",
            (image_queue.IGNORED, image_queue.REFUSED, since))]


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Inscrit chaque image jouable devenue jouable depuis le démarrage (DECISION ou COMPTE selon le classement de
    F4), et chaque image ignorée ou refusée (COMPTE), une fois."""
    known = set(f4._first_by(journal.entries({f4.DECISION, f4.COUNTED})))
    frozen = set(start["halal"]["symbols"])
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    counts = {"decisions": 0, "counted": 0}
    since = utc_iso(started)
    for row in _closed(settings, since):
        ident = f"{SOURCE}:{row['id']}"
        if ident in known:
            continue
        journal.append(f4.COUNTED, {"signal_id": ident, "provider": group_of(row["caption"]) or f"chat {row['chat']}",
                                    "source": SOURCE, "chat": row["chat"], "received_at": row["received_at"],
                                    "image_sha256": row["image_sha256"], "status": row["status"],
                                    "notes": row["ocr_notes"], "ocr_code": row["ocr_code"]}, now=now)
        known.add(ident)
        counts["counted"] += 1
    for row in image_queue.playable(settings, since=since):
        signal = to_signal(row)
        if signal.id in known or pd.Timestamp(row["received_at"]) < started:
            continue
        base = {"signal_id": signal.id, "provider": signal.provider, "source": SOURCE, "chat": signal.chat,
                "received_at": row["received_at"], "playable_at": row["playable_at"], "image_status": row["status"],
                "image_sha256": row["image_sha256"], "ocr_code": row["ocr_code"], "edited": False,
                "text_sha256": hashlib.sha256(signal.text.encode("utf-8")).hexdigest()}
        entry = f4.entry_time(signal.received_at)
        status, levels = f4.classify(signal, frozen)
        if status == f4.DECISION and entry >= final:
            status, levels = f4.OUT_OF_WINDOW, {}
        if status != f4.DECISION:
            journal.append(f4.COUNTED, base | {"status": status} | levels, now=now)
            counts["counted"] += 1
            continue
        journal.append(f4.DECISION, base | levels | {"entry_at": utc_iso(entry), "placebo_minutes": f4.placebo_offsets(signal.id),
                                                     "parser_code": f4.parser_code()}, now=now)
        counts["decisions"] += 1
    return counts


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    return f4.resolve(settings, journal, now=now)


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    return f4.stats(journal, start, now=now)


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    return f4.finalize(journal, start, now=now)


TEST = ForwardTest(
    test_id=TEST_ID, title="Signaux Telegram publiés en image, lus par OCR ou validés : règles de F4, contre placebos",
    hypothesis=("Un signal d'achat spot publié en image par un fournisseur Telegram, lu par OCR (SUR) ou validé par le "
                "propriétaire, acheté au premier prix après qu'il est devenu jouable et géré avec le stop suiveur du "
                "propriétaire, rapporte en moyenne plus (en R net) qu'un achat au même moment gardé aussi longtemps et que 20 "
                "achats de même géométrie à des moments tirés au hasard dans les 30 jours précédents."),
    params={"events": "images reçues par le 2e bot après le démarrage ; SUR jouable à la réception, A_VALIDER à la validation",
            "rules": "celles de F4 (forward/f4.py réutilisé sans modification)", "f4_params": f4.TEST.params,
            "ocr": "RapidOCR local, issues SUR / A_VALIDER / IGNOREE (external/chart_ocr.classify_image), code non gelé : "
                   "empreinte inscrite dans chaque décision",
            "provider": "groupe nommé par la légende, sinon la conversation"},
    rule_objects=(), config_keys=f4.TEST.config_keys,
    frozen_modules=("crypto_signal_intelligence.forward.f16", *f4.TEST.frozen_modules,
                    "crypto_signal_intelligence.external.image_queue"),
    frozen_functions=f4.TEST.frozen_functions,
)
