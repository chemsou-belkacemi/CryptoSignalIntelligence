"""Dossier `exports/` : exports de Telegram Desktop copiés par le propriétaire (un sous-dossier par groupe, avec son
`result.json` et ses photos), audités AVEC leurs images depuis le tableau de bord (docs/OCR.md, « Depuis le tableau
de bord »). Un navigateur ne peut pas envoyer des milliers de photos : le dossier est copié sur la machine de CSI
(Docker : `./exports`, monté en lecture seule sur `/srv/csi/exports`) et l'API y lit, jamais ailleurs.

Même lecture, même rejeu, même preuve enregistrée et même rapport que `csi audit-telegram --dir … --ocr` : mesure
d'une source externe, aucun ordre. Un seul audit à la fois, en arrière-plan ; son état (progression, résultat) est
gardé pour le tableau de bord, et les audits terminés sont relus au redémarrage (`reports/exports_audits.json`).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from ..config import Settings
from .audit import ImageReader, read_telegram_export

RESULT_FILE = "result.json"
INDEX_FILE = "exports_audits.json"          # audits terminés (sous reports/), relus au redémarrage de l'API
MAX_FOLDER_CHARS = 120
RUNNING, DONE, FAILED = "EN_COURS", "TERMINE", "ECHEC"


def export_images(payload: dict, folder: Path) -> tuple[int, int]:
    """(images nommées dans l'export, images présentes) : le champ `photo` de chaque message, fichier cherché DANS le
    dossier de l'export (une image hors du dossier n'est jamais lue, donc jamais comptée présente). Même calcul pour
    la commande `audit-telegram --dir` et pour le tableau de bord."""
    chats = payload.get("chats", {}).get("list", []) if isinstance(payload.get("chats"), dict) else [payload]
    named = [m["photo"] for c in chats if isinstance(c, dict) for m in (c.get("messages") or [])
             if isinstance(m, dict) and isinstance(m.get("photo"), str)]
    base = folder.resolve()
    present = 0
    for name in named:
        path = (base / name).resolve()
        present += path.is_relative_to(base) and path.is_file()
    return len(named), present


def folder_name_ok(name: object) -> bool:
    """Nom d'un sous-dossier d'`exports/` tel que le tableau de bord le donne : un seul composant, jamais un chemin
    (« .. », « / », « \\ »), pas de nom caché, 120 caractères au plus."""
    return (isinstance(name, str) and 0 < len(name) <= MAX_FOLDER_CHARS and name == name.strip()
            and name not in (".", "..") and not name.startswith(".") and not any(c in name for c in "/\\\0"))


def export_folder(settings: Settings, name: str) -> Path:
    """Dossier d'un export, SOUS `exports/` (liens symboliques résolus) et contenant `result.json`.
    ValueError si le nom n'est pas un simple nom de dossier, FileNotFoundError s'il n'existe pas."""
    if not folder_name_ok(name):
        raise ValueError("nom de dossier invalide : un sous-dossier d'exports/, sans chemin")
    base = settings.exports_dir.resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_dir():
        raise FileNotFoundError(f"dossier « {name} » absent d'exports/")
    if not (path / RESULT_FILE).is_file():
        raise FileNotFoundError(f"dossier « {name} » sans {RESULT_FILE} (export au format JSON attendu)")
    return path


def list_folders(settings: Settings) -> list[Path]:
    """Sous-dossiers d'`exports/` qui contiennent un `result.json`, par nom."""
    base = settings.exports_dir
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if folder_name_ok(p.name) and p.is_dir() and (p / RESULT_FILE).is_file())


def describe_export(folder: Path) -> dict:
    """Contenu d'un export : messages texte que l'audit lira, images nommées et présentes, nom du ou des chats ;
    `error` si le fichier n'est pas un export JSON de Telegram Desktop. Lecture seule, aucune image lue ici."""
    out: dict[str, Any] = {"folder": folder.name, "chats": [], "messages": 0, "images_named": 0, "images_present": 0,
                           "error": None}
    try:
        with open(folder / RESULT_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
        out["messages"] = len(read_telegram_export(payload))
        out["images_named"], out["images_present"] = export_images(payload, folder)
        chats = payload.get("chats", {}).get("list", []) if isinstance(payload.get("chats"), dict) else [payload]
        out["chats"] = [str(c.get("name") or "") for c in chats if isinstance(c, dict)][:10]
    except (OSError, ValueError) as exc:          # json.JSONDecodeError est un ValueError
        out["error"] = f"pas un export JSON de Telegram Desktop ({exc})"
    out["without_photos"] = bool(out["images_named"]) and not out["images_present"]
    return out


@dataclass
class ExportAudit:
    """État d'un audit lancé depuis le tableau de bord (un seul à la fois), gardé pour `GET /sources/exports`."""
    folder: str
    weights: str
    ocr: bool
    started_at: str
    state: str = RUNNING
    step: str = "lecture de l'export"
    images_named: int = 0
    images_present: int = 0
    images_read: int = 0            # images passées au lecteur (texte du message pas lisible comme signal)
    images_as_signals: int = 0      # images lues comme signaux SÛRS (les seules mesurées)
    finished_at: str | None = None
    error: str | None = None
    result: dict | None = None      # même forme que la réponse de POST /sources/history

    def to_dict(self, *, with_result: bool = True) -> dict:
        out = asdict(self)
        if not with_result:
            out["result"] = None
        out["has_result"] = self.result is not None
        return out

    @classmethod
    def from_dict(cls, data: dict) -> ExportAudit:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def counting_reader(reader: ImageReader, job: ExportAudit) -> ImageReader:
    """Le lecteur d'images, qui compte au passage les images lues et celles lues comme signaux (progression)."""
    def read(path: Path, caption: str) -> tuple[str, str | None]:
        status, text = reader(path, caption)
        job.images_read += 1
        if status == "SUR" and text:
            job.images_as_signals += 1
        return status, text
    return read


def load_index(settings: Settings) -> dict[str, ExportAudit]:
    """Audits terminés, tels qu'enregistrés par `save_index` (un fichier illisible vaut aucun audit)."""
    path = settings.reports_dir / INDEX_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, entry in (data.items() if isinstance(data, dict) else []):
        if isinstance(entry, dict) and folder_name_ok(name):
            try:
                out[name] = ExportAudit.from_dict(entry)
            except TypeError:
                continue
    return out


def save_index(settings: Settings, audits: dict[str, ExportAudit]) -> None:
    """Écrit les audits terminés (jamais celui en cours), par remplacement atomique."""
    path = settings.reports_dir / INDEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {name: audit.to_dict() for name, audit in audits.items() if audit.state != RUNNING}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(done, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
