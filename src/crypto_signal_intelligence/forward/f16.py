"""F16_TELEGRAM_IMAGES : signaux Telegram publiés en IMAGE, lus par OCR (ou validés par le propriétaire), joués avec
EXACTEMENT les règles de F4 (docs/FORWARD_TESTS.md, section F16_TELEGRAM_IMAGES).

Seule la source diffère : la file `external/image_queue.py` au lieu des messages texte. Un signal devient jouable à sa
LECTURE (`SUR` : max(réception, lecture), ses niveaux n'existant pas avant) ou à la validation du propriétaire
(`VALIDEE`, dans les 2 h qui suivent la lecture) ; c'est cette heure-là qui sert de « réception » aux règles de F4
(entrée 60 s après, jamais avant). Rejeu et verdict : fonctions de `forward/f4.py`, réutilisées sans modification.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from ..config import Settings
from ..external import image_queue
from ..external.parser import group_of, parse
from . import f4
from .journal import Journal, utc_iso
from .registry import ForwardTest
from .telegram_live import LiveSignal

TEST_ID = "F16_TELEGRAM_IMAGES"
SOURCE = "image"
DUPLICATE, CAPTION_SIGNAL, PENDING_AT_END = "DOUBLON", "SIGNAL_TEXTE_DANS_LA_LEGENDE", "EN_SUSPENS_A_LA_FIN"
CLOSED = (image_queue.IGNORED, image_queue.REFUSED, image_queue.EXPIRED)


def provider_of(row: dict) -> str:
    return group_of(row["caption"]) or (f"chat {row['origin_chat']}" if row.get("origin_chat") else f"chat {row['chat']}")


def to_signal(row: dict) -> LiveSignal:
    """Ligne jouable de la file → signal au format de F4 ; « réception » = heure où il est devenu jouable."""
    return LiveSignal(id=f"{SOURCE}:{row['id']}", provider=provider_of(row), received_at=row["playable_at"],
                      text=row["final_text"], source=SOURCE, chat=row["chat"], edited=bool(row.get("edited")))


def caption_is_signal(caption: str) -> bool:
    """La légende se lit déjà comme un signal complet : le relais l'envoie aussi à F4, qui le mesure ; F16 ne le
    rejoue pas (aucun signal compté deux fois)."""
    parsed = parse(caption or "")
    return not parsed.errors and parsed.stop is not None and bool(parsed.targets) and bool(parsed.entries)


def _rows(settings: Settings, statuses: tuple[str, ...], since: str) -> list[dict]:
    marks = ",".join("?" for _ in statuses)
    with image_queue.connect(settings) as db:
        return [dict(r) for r in db.execute(
            f"SELECT * FROM images WHERE status IN ({marks}) AND created_at >= ? ORDER BY created_at, id",  # noqa: S608
            (*statuses, since))]


def _base(row: dict) -> dict:
    return {"signal_id": f"{SOURCE}:{row['id']}", "provider": provider_of(row), "source": SOURCE, "chat": row["chat"],
            "origin_chat": row.get("origin_chat") or "", "forwarded": bool(row.get("origin_chat")),
            "received_at": row["received_at"], "created_at": row["created_at"], "read_at": row["read_at"],
            "decided_at": row["decided_at"], "image_status": row["status"], "image_sha256": row["image_sha256"],
            "file_unique_id": row.get("file_unique_id") or "", "ocr_code": row["ocr_code"],
            "ocr_text_sha256": hashlib.sha256((row["ocr_text"] or "").encode("utf-8")).hexdigest(),
            "edited": bool(row.get("edited"))}


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Inscrit, une fois, chaque image arrivée depuis le démarrage : DECISION (jouable, classée comme dans F4) ou
    COMPTE (ignorée, refusée, expirée, doublon, signal déjà dans la légende, hors règles de F4 ; à la fin du recueil :
    images jamais lues ou jamais décidées)."""
    known = set(f4._first_by(journal.entries({f4.DECISION, f4.COUNTED})))
    seen_images = {d.get("image_sha256") for d in f4._first_by(journal.entries({f4.DECISION})).values()} | \
        {d.get("file_unique_id") for d in f4._first_by(journal.entries({f4.DECISION})).values() if d.get("file_unique_id")}
    frozen = set(start["halal"]["symbols"])
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    since = utc_iso(started)
    counts = {"decisions": 0, "counted": 0}

    def count(row: dict, status: str, extra: dict | None = None) -> None:
        journal.append(f4.COUNTED, _base(row) | {"status": status, "notes": row.get("ocr_notes")} | (extra or {}), now=now)
        known.add(f"{SOURCE}:{row['id']}")
        counts["counted"] += 1

    image_queue.expire(settings, now=now)
    for row in _rows(settings, CLOSED, since):
        if f"{SOURCE}:{row['id']}" not in known:
            count(row, row["status"])
    for row in image_queue.playable(settings, since=since):
        signal = to_signal(row)
        if signal.id in known or pd.Timestamp(row["created_at"]) < started:
            continue
        if row["image_sha256"] in seen_images or (row.get("file_unique_id") and row["file_unique_id"] in seen_images):
            count(row, DUPLICATE)
            continue
        if caption_is_signal(row["caption"]):
            count(row, CAPTION_SIGNAL)
            continue
        base = _base(row) | {"playable_at": row["playable_at"],
                             "text_sha256": hashlib.sha256(signal.text.encode("utf-8")).hexdigest()}
        entry = f4.entry_time(signal.received_at)
        status, levels = f4.classify(signal, frozen)
        if status == f4.DECISION and entry >= final:
            status, levels = f4.OUT_OF_WINDOW, {}
        if status != f4.DECISION:
            journal.append(f4.COUNTED, base | {"status": status} | levels, now=now)
            known.add(signal.id)
            counts["counted"] += 1
            continue
        journal.append(f4.DECISION, base | levels | {"entry_at": utc_iso(entry), "placebo_minutes": f4.placebo_offsets(signal.id),
                                                     "parser_code": f4.parser_code()}, now=now)
        known.add(signal.id)
        seen_images |= {row["image_sha256"], row.get("file_unique_id") or row["image_sha256"]}
        counts["decisions"] += 1
    if pd.Timestamp(now) >= final:
        for row in _rows(settings, (image_queue.RECEIVED, image_queue.TO_VALIDATE), since):
            if f"{SOURCE}:{row['id']}" not in known:
                count(row, PENDING_AT_END)
    return counts


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    return f4.resolve(settings, journal, now=now)


class _View:
    """Vue filtrée du journal (mêmes entrées, sous-ensemble des signaux) pour les statistiques descriptives."""

    def __init__(self, journal: Journal, keep: set[str]):
        self.journal, self.keep = journal, keep

    def entries(self, kinds=None):
        for entry in self.journal.entries(kinds):
            if entry["data"].get("signal_id") in self.keep:
                yield entry


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    """Statistiques de F4 (verdict déclaré sur l'ensemble), plus les issues des images et, à titre descriptif, les
    mêmes mesures sur les seules images SUR, les seules images validées, et par version du code de lecture."""
    out = f4.stats(journal, start, now=now)
    decisions = f4._first_by(journal.entries({f4.DECISION}))
    counted = f4._first_by(journal.entries({f4.COUNTED}))
    statuses: dict[str, int] = {}
    for item in list(decisions.values()) + list(counted.values()):
        key = item.get("status") if item.get("status") in (*CLOSED, DUPLICATE, CAPTION_SIGNAL, PENDING_AT_END) \
            else item.get("image_status")
        statuses[str(key)] = statuses.get(str(key), 0) + 1
    out["images"] = {"by_outcome": statuses, "edited": sum(1 for d in decisions.values() if d.get("edited")),
                     "forwarded": sum(1 for d in decisions.values() if d.get("forwarded")),
                     "ocr_codes": len({d.get("ocr_code") for d in decisions.values()})}
    out["descriptive"] = {}
    for label, keep in (("SUR", {k for k, d in decisions.items() if d.get("image_status") == image_queue.SURE}),
                        ("VALIDEE", {k for k, d in decisions.items() if d.get("image_status") == image_queue.VALIDATED})):
        sub = f4.stats(_View(journal, keep), start, now=now)                  # type: ignore[arg-type]
        out["descriptive"][label] = sub["providers"].get(f4.ALL)
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    return f4.finalize(journal, start, now=now)


TEST = ForwardTest(
    test_id=TEST_ID, title="Signaux Telegram publiés en image, lus par OCR ou validés : règles de F4, contre placebos",
    hypothesis=("Un signal d'achat spot publié en image par un fournisseur Telegram, lu par OCR (SUR) ou validé par le "
                "propriétaire, acheté au premier prix après qu'il est devenu jouable et géré avec le stop suiveur du "
                "propriétaire, rapporte en moyenne plus (en R net) qu'un achat au même moment gardé aussi longtemps et que 20 "
                "achats de même géométrie à des moments tirés au hasard dans les 30 jours précédents."),
    params={"events": "images reçues par le 2e bot après le démarrage ; SUR jouable à max(réception, lecture), A_VALIDER "
                      "à la validation, dans les 2 h qui suivent la lecture (sinon EXPIREE)",
            "rules": "celles de F4 (forward/f4.py réutilisé sans modification)", "f4_params": f4.TEST.params,
            "validation_delay_hours": image_queue.VALIDATION_DELAY_HOURS, "read_attempts": image_queue.MAX_READ_ATTEMPTS,
            "ocr": "RapidOCR local, issues SUR / A_VALIDER / IGNOREE (external/chart_ocr.classify_image) ; code, versions et "
                   "modèles non gelés : empreinte inscrite dans chaque décision",
            "provider": "groupe nommé par la légende, sinon la conversation d'origine d'un transfert, sinon la conversation",
            "exclusions": "doublons (même image ou même fichier Telegram), légende déjà lisible comme signal (mesurée par F4)"},
    rule_objects=(), config_keys=f4.TEST.config_keys,
    frozen_modules=("crypto_signal_intelligence.forward.f16", *f4.TEST.frozen_modules,
                    "crypto_signal_intelligence.external.image_queue"),
    frozen_functions=f4.TEST.frozen_functions,
)
