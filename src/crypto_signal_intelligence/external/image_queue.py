"""File des signaux Telegram reçus en IMAGE (mission du 2026-10-03, phase 3 ; décision du propriétaire : RapidOCR,
issues SUR / A_VALIDER / IGNOREE).

Chaque image reçue en direct (relais du 2e bot, `POST /telegram/image`) est gardée telle quelle
(`state/telegram_images/<empreinte>.<ext>`) avec sa légende, sa conversation et son heure de réception. La
surveillance la lit (OCR local, `external/chart_ocr.py`) et inscrit son issue :
- `SUR` : jouable dès la réception ;
- `A_VALIDER` : jouable seulement après la validation du propriétaire dans CSI, à l'heure de la VALIDATION (jamais à
  celle de la réception : une validation tardive ne doit pas profiter de ce qui s'est passé entre-temps) ; le
  propriétaire peut corriger les niveaux lus ;
- `IGNOREE` : jamais jouée (comptée).
Aucun ordre ; le test en direct F16 lit les signaux jouables (`playable`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..config import Settings

RECEIVED, SURE, TO_VALIDATE, IGNORED, VALIDATED, REFUSED = "RECUE", "SUR", "A_VALIDER", "IGNOREE", "VALIDEE", "REFUSEE"
EXPIRED = "EXPIREE"
EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024
#: Délai de validation par le propriétaire, compté depuis la lecture (moment où l'image lui est présentée) : au-delà,
#: l'image est EXPIREE (le moment de validation n'est pas un paramètre libre).
VALIDATION_DELAY_HOURS = 2
MAX_READ_ATTEMPTS = 2
MAX_FUTURE_SECONDS = 300


def db_path(settings: Settings) -> Path:
    return settings.root / "state" / "telegram_images.sqlite3"


def image_dir(settings: Settings) -> Path:
    return settings.root / "state" / "telegram_images"


@contextmanager
def connect(settings: Settings):
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, chat TEXT NOT NULL, message_id TEXT NOT NULL, received_at TEXT NOT NULL,
            caption TEXT NOT NULL, file TEXT NOT NULL, image_sha256 TEXT NOT NULL, status TEXT NOT NULL,
            file_unique_id TEXT NOT NULL DEFAULT '', origin_chat TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
            ocr_text TEXT, ocr_notes TEXT, ocr_code TEXT, read_at TEXT,
            final_text TEXT, edited INTEGER NOT NULL DEFAULT 0, decided_at TEXT, playable_at TEXT, created_at TEXT NOT NULL)""")
        yield db
        db.commit()
    finally:
        db.close()


def add(settings: Settings, *, image: bytes, ext: str, chat: str, message_id: str, received_at: str, caption: str,
        now: datetime, file_unique_id: str = "", origin_chat: str = "") -> dict:
    """Garde une image reçue (une seule fois par conversation et message) ; rend {id, new}. Une heure de réception
    dans le futur est refusée ; une heure ancienne est gardée telle quelle (l'image ne devient jouable qu'après sa
    lecture, jamais à sa réception)."""
    ext = ext.lower().lstrip(".")
    if ext not in EXTENSIONS:
        raise ValueError(f"format d'image refusé : {ext}")
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ValueError("image vide ou trop lourde (4 Mo au plus)")
    received_at = _utc_iso(received_at)
    if (_ts(received_at) - _ts(now)).total_seconds() > MAX_FUTURE_SECONDS:
        raise ValueError("heure de réception dans le futur")
    ident = hashlib.sha256(f"{chat}:{message_id}".encode()).hexdigest()[:24]
    digest = hashlib.sha256(image).hexdigest()
    with connect(settings) as db:
        if db.execute("SELECT 1 FROM images WHERE id=?", (ident,)).fetchone():
            return {"id": ident, "new": False}
        folder = image_dir(settings)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{digest}.{ext}"
        if not (folder / name).exists():
            (folder / name).write_bytes(image)
        db.execute("INSERT INTO images (id, chat, message_id, received_at, caption, file, image_sha256, status, created_at, "
                   "file_unique_id, origin_chat) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (ident, str(chat), str(message_id), received_at, caption or "", name, digest, RECEIVED, _utc_iso(now),
                    str(file_unique_id or ""), str(origin_chat or "")))
    return {"id": ident, "new": True}


def _ts(value):
    import pandas as pd
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _utc_iso(value) -> str:
    import pandas as pd
    stamp = pd.Timestamp(value)
    return (stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")).isoformat()


def add_b64(settings: Settings, payload: dict, *, now: datetime) -> dict:
    """Dépôt de l'API : {chat, message_id, received_at, caption, ext, image_b64}."""
    try:
        image = base64.b64decode(str(payload.get("image_b64") or ""), validate=True)
    except ValueError:
        raise ValueError("image_b64 invalide") from None
    for key in ("chat", "message_id", "received_at"):
        if not str(payload.get(key) or "").strip():
            raise ValueError(f"champ « {key} » requis")
    return add(settings, image=image, ext=str(payload.get("ext") or "jpg"), chat=str(payload["chat"]),
               message_id=str(payload["message_id"]), received_at=str(payload["received_at"]),
               caption=str(payload.get("caption") or ""), now=now,
               file_unique_id=str(payload.get("file_unique_id") or ""), origin_chat=str(payload.get("origin_chat") or ""))


def to_read(settings: Settings, limit: int = 20) -> list[dict]:
    with connect(settings) as db:
        return [dict(r) for r in db.execute("SELECT * FROM images WHERE status=? ORDER BY received_at LIMIT ?",
                                            (RECEIVED, limit))]


def record_read(settings: Settings, ident: str, *, status: str, text: str | None, notes: list[str], code: str,
                now: datetime) -> None:
    """Issue de la lecture OCR ; une image SUR devient jouable à la LECTURE (ses niveaux n'existent pas avant), au plus
    tôt à sa réception : max(réception, lecture)."""
    if status not in (SURE, TO_VALIDATE, IGNORED):
        raise ValueError(status)
    with connect(settings) as db:
        row = db.execute("SELECT received_at, status FROM images WHERE id=?", (ident,)).fetchone()
        if row is None or row["status"] != RECEIVED:
            return
        read_at = _utc_iso(now)
        playable = max(row["received_at"], read_at) if status == SURE else None
        db.execute("UPDATE images SET status=?, ocr_text=?, ocr_notes=?, ocr_code=?, read_at=?, final_text=?, playable_at=? "
                   "WHERE id=?", (status, text, json.dumps(notes, ensure_ascii=False), code, read_at,
                                  text if status == SURE else None, playable, ident))


def process(settings: Settings, reader, *, code: str, now: datetime, limit: int = 20) -> dict:
    """Lit les images reçues : `reader(chemin, légende) -> {status, text, notes}` (classify_image après analyse)."""
    counts: dict[str, int] = {}
    for row in to_read(settings, limit):
        with connect(settings) as db:            # compté AVANT la lecture : un plantage ne fait pas boucler la surveillance
            db.execute("UPDATE images SET attempts = attempts + 1 WHERE id=?", (row["id"],))
        out: dict
        if row["attempts"] + 1 > MAX_READ_ATTEMPTS:
            out = {"status": IGNORED, "text": None, "notes": [f"lecture impossible ({MAX_READ_ATTEMPTS} essais)"]}
        else:
            try:
                out = reader(image_dir(settings) / row["file"], row["caption"])
            except Exception as exc:  # noqa: BLE001 - une image illisible est ignorée, jamais devinée
                out = {"status": IGNORED, "text": None, "notes": [f"lecture impossible : {type(exc).__name__}"]}
        record_read(settings, row["id"], status=out["status"], text=out["text"], notes=list(out["notes"]), code=code,
                    now=now)
        counts[out["status"]] = counts.get(out["status"], 0) + 1
    return counts


def decide(settings: Settings, ident: str, *, accept: bool, text: str | None, now: datetime) -> dict:
    """Décision du propriétaire sur une image A_VALIDER, dans les VALIDATION_DELAY_HOURS qui suivent sa lecture :
    validée (niveaux lus, ou corrigés et relus par le parseur ; la paire ne peut pas changer ; une correction est
    marquée) ou refusée. Jouable à l'heure de la VALIDATION."""
    from .parser import parse
    expire(settings, now=now)
    with connect(settings) as db:
        row = db.execute("SELECT * FROM images WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ValueError("image inconnue")
        if row["status"] != TO_VALIDATE:
            raise ValueError(f"image déjà traitée ({row['status']})")
        if not accept:
            db.execute("UPDATE images SET status=?, decided_at=? WHERE id=?", (REFUSED, now.isoformat(), ident))
            return {"id": ident, "status": REFUSED}
        final = (text or row["ocr_text"] or "").strip()
        parsed = parse(final)
        if parsed.errors or parsed.stop is None or not parsed.targets or not parsed.entries:
            raise ValueError("niveaux illisibles : " + " ; ".join(parsed.errors[:2] or ["stop, objectif ou entrée absent"]))
        read_symbol = parse(row["ocr_text"] or "").symbol
        if read_symbol and parsed.symbol != read_symbol:
            raise ValueError(f"la paire ne peut pas changer ({read_symbol} lue sur l'image)")
        edited = int(final != (row["ocr_text"] or "").strip())
        db.execute("UPDATE images SET status=?, final_text=?, edited=?, decided_at=?, playable_at=? WHERE id=?",
                   (VALIDATED, final, edited, _utc_iso(now), _utc_iso(now), ident))
    return {"id": ident, "status": VALIDATED, "symbol": parsed.symbol}


def expire(settings: Settings, *, now: datetime) -> int:
    """Images A_VALIDER non décidées dans le délai : EXPIREE (jamais jouées)."""
    import pandas as pd
    limit = _utc_iso(pd.Timestamp(now) - pd.Timedelta(hours=VALIDATION_DELAY_HOURS))
    with connect(settings) as db:
        cursor = db.execute("UPDATE images SET status=?, decided_at=? WHERE status=? AND read_at < ?",
                            (EXPIRED, _utc_iso(now), TO_VALIDATE, limit))
        return int(cursor.rowcount)


def pending(settings: Settings, *, with_images: bool = True, limit: int = 30, now: datetime | None = None) -> list[dict]:
    """Images à valider (dans le délai), avec l'image en data URL pour la page (aucune route binaire)."""
    if now is not None:
        expire(settings, now=now)
    with connect(settings) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM images WHERE status=? ORDER BY received_at LIMIT ?",
                                            (TO_VALIDATE, limit))]
    for row in rows:
        row["ocr_notes"] = json.loads(row["ocr_notes"] or "[]")
        if with_images:
            path = image_dir(settings) / row["file"]
            ext = row["file"].rsplit(".", 1)[-1].replace("jpg", "jpeg")
            row["image"] = f"data:image/{ext};base64," + base64.b64encode(path.read_bytes()).decode() if path.exists() else None
    return rows


def playable(settings: Settings, *, since: str) -> list[dict]:
    """Signaux jouables (SUR ou VALIDEE) devenus jouables depuis `since`, dans l'ordre."""
    with connect(settings) as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM images WHERE status IN (?, ?) AND playable_at >= ? ORDER BY playable_at, id",
            (SURE, VALIDATED, _utc_iso(since)))]


def counts(settings: Settings) -> dict[str, int]:
    with connect(settings) as db:
        return {r["status"]: int(r["n"]) for r in db.execute("SELECT status, COUNT(*) AS n FROM images GROUP BY status")}
