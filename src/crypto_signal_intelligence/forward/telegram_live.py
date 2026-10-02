"""Signaux Telegram reçus EN DIRECT, pour le test F4_TELEGRAM (phase 3 de la mission du 2026-10-02).

Deux sources, aucune clé chez CSI :
- la boîte de réception de BinanceSpotManager (`signals.sqlite3`, lecture seule) : les messages des conversations
  autorisées, enregistrés par son bot à la réception, donc sans biais de suppression ;
- un dossier de dépôt (`<CSI_ROOT>/imports/telegram/live/*.json`) : listes de signaux produites par le robot du
  propriétaire (champs `signal_id`, `source_chat_id`, `raw_text`, `received_at`), déposées à la main ou par la route
  `POST /telegram/live` de l'API.

Chaque signal porte l'heure à laquelle le bot l'a reçu (`received_at`), le texte brut et le nom de son fournisseur
(lu en tête du message, sinon l'identifiant de la conversation). Les messages modifiés sont signalés quand la source
le permet ; les suppressions ne sont pas détectables ici.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..external.parser import group_of

BSM_SOURCE, ROBOT_SOURCE = "bsm", "robot"


@dataclass(frozen=True)
class LiveSignal:
    id: str
    provider: str
    received_at: str          # ISO UTC : heure de réception par le bot
    text: str
    source: str               # bsm | robot
    chat: str = ""
    edited: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), UTC).isoformat()


def _chat_of(external_id: str) -> str:
    """Identifiant externe de BSM : `<empreinte du bot>:<conversation>:<message>`."""
    parts = str(external_id or "").split(":")
    return parts[1] if len(parts) >= 3 else ""


def read_bsm(path: Path, *, since: pd.Timestamp) -> list[LiveSignal]:
    """Messages Telegram de la boîte de BSM reçus à partir de `since` (lecture seule, fichier absent = aucun)."""
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
        if not {"raw", "received", "external_id", "source"} <= columns:
            return []
        parsed_col = "parsed" if "parsed" in columns else "'{}' AS parsed"
        rows = db.execute(f"SELECT id, raw, received, external_id, {parsed_col} FROM signals "
                          "WHERE source='telegram' AND received >= ? ORDER BY received, id",
                          (float(pd.Timestamp(since).timestamp()),)).fetchall()
    out = []
    for row in rows:
        try:
            parsed = json.loads(row["parsed"] or "{}")
        except ValueError:
            parsed = {}
        errors = parsed.get("errors") if isinstance(parsed, dict) else None
        edited = any("édité" in str(e).lower() for e in (errors or []))
        chat = _chat_of(row["external_id"])
        out.append(LiveSignal(id=f"{BSM_SOURCE}:{row['id']}", provider=group_of(row["raw"]) or (f"telegram {chat}" if chat else "telegram"),
                              received_at=_iso(row["received"]), text=str(row["raw"]), source=BSM_SOURCE, chat=chat,
                              edited=edited))
    return out


def read_robot_file(path: Path) -> list[LiveSignal]:
    """Un fichier du robot : liste JSON de signaux (format du 2026-10-02) ; ligne illisible ignorée."""
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = rows.get("signals", [])
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("raw_text") or not row.get("received_at"):
            continue
        try:
            received = pd.Timestamp(row["received_at"])
            received = received.tz_localize("UTC") if received.tzinfo is None else received.tz_convert("UTC")
        except (TypeError, ValueError):
            continue
        chat = str(row.get("source_chat_id", ""))
        ident = str(row.get("signal_id") or hashlib.sha256(f"{chat}:{received.isoformat()}:{row['raw_text']}".encode()).hexdigest()[:16])
        out.append(LiveSignal(id=f"{ROBOT_SOURCE}:{ident}", provider=group_of(row["raw_text"]) or (f"chat {chat}" if chat else "robot"),
                              received_at=received.isoformat(), text=str(row["raw_text"]), source=ROBOT_SOURCE, chat=chat,
                              edited=bool(row.get("edited", False))))
    return out


def read_robot_dir(folder: Path, *, since: pd.Timestamp) -> list[LiveSignal]:
    folder = Path(folder)
    if not folder.exists():
        return []
    out: list[LiveSignal] = []
    for path in sorted(folder.glob("*.json")):
        out += [s for s in read_robot_file(path) if pd.Timestamp(s.received_at) >= since]
    return out


def live_dir(settings: Settings) -> Path:
    return settings.root / settings.forward.telegram_live_dir


def bsm_inbox(settings: Settings) -> Path | None:
    return Path(settings.forward.bsm_inbox) if settings.forward.bsm_inbox else None


def read_all(settings: Settings, *, since: pd.Timestamp) -> list[LiveSignal]:
    """Toutes les sources, dédoublonnées sur l'identifiant, dans l'ordre de réception."""
    items: dict[str, LiveSignal] = {}
    path = bsm_inbox(settings)
    for signal in (read_bsm(path, since=since) if path else []) + read_robot_dir(live_dir(settings), since=since):
        items.setdefault(signal.id, signal)
    return sorted(items.values(), key=lambda s: (s.received_at, s.id))


def store_drop(settings: Settings, rows: list[dict], *, now: datetime) -> Path:
    """Enregistre une liste reçue par l'API dans le dossier de dépôt (un fichier horodaté, jamais écrasé)."""
    folder = live_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    path = folder / f"{pd.Timestamp(now).strftime('%Y%m%dT%H%M%SZ')}-{digest}.json"
    if not path.exists():
        path.write_text(payload, encoding="utf-8")
    return path
