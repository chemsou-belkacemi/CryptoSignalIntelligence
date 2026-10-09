"""Boîte Telegram du test F19 (`state/price_action_outbox.json`), SÉPARÉE de celle de l'assistant (F18, gelée) mais au
même format : `GET /assistant/outbox` fusionne les deux boîtes et `POST /assistant/sent` marque chaque message dans la
sienne (identifiants F19 préfixés `pa:`). Un message en attente depuis plus de 6 h passe EXPIRE. Rien ici ne touche à
`SignalRegistry`, à `signals/` ni à `state/assistant*`.

Lecture-modification-écriture sous un verrou de fichier (la surveillance et l'API sont deux processus), écriture
atomique (fichier temporaire, fsync, renommage)."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..forward.journal import utc_iso

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

PREFIX = "pa:"
PENDING, SENT, EXPIRED = "EN_ATTENTE", "ENVOYE", "EXPIRE"
EXPIRE_AFTER = pd.Timedelta(hours=6)
MAX_PENDING_RETURNED = 20
KEEP_MESSAGES = 300
FOOTER = "Shadow : aucun ordre. Test en direct F19, aucun gain démontré."


def path_for(settings: Settings) -> Path:
    return settings.root / "state" / "price_action_outbox.json"


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    messages = data.get("messages") if isinstance(data, dict) else None
    return [m for m in (messages or []) if isinstance(m, dict) and "id" in m]


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _expire(messages: list[dict], now: pd.Timestamp) -> int:
    expired = 0
    for m in messages:
        if m.get("status") == PENDING and now - pd.Timestamp(m["created_at"]) > EXPIRE_AFTER:
            m["status"], expired = EXPIRED, expired + 1
    return expired


def queue(settings: Settings, *, message_id: str, text: str, now: datetime) -> bool:
    """Ajoute un message EN_ATTENTE (une fois par identifiant, préfixé `pa:`) ; False s'il existait déjà."""
    ident = message_id if message_id.startswith(PREFIX) else PREFIX + message_id
    path = path_for(settings)
    moment = pd.Timestamp(now)
    with _locked(path):
        messages = _read(path)
        if any(m["id"] == ident for m in messages):
            return False
        messages.append({"id": ident, "created_at": utc_iso(moment), "text": text, "status": PENDING})
        _expire(messages, moment)
        write_atomic(path, {"messages": messages[-KEEP_MESSAGES:]})
    return True


def pending(settings: Settings, *, now: datetime, limit: int = MAX_PENDING_RETURNED) -> list[dict]:
    """Messages EN_ATTENTE, du plus ancien au plus récent, 20 au plus ; ceux de plus de 6 h passent EXPIRE avant."""
    path = path_for(settings)
    moment = pd.Timestamp(now)
    with _locked(path):
        messages = _read(path)
        if _expire(messages, moment):
            write_atomic(path, {"messages": messages[-KEEP_MESSAGES:]})
    waiting = sorted((m for m in messages if m.get("status") == PENDING), key=lambda m: m["created_at"])
    return [{"id": m["id"], "created_at": m["created_at"], "text": m["text"]} for m in waiting[:limit]]


def mark_sent(settings: Settings, ids: list[str]) -> int:
    """Passe ENVOYE les messages listés (préfixés `pa:`) ; rend le nombre marqués."""
    path = path_for(settings)
    wanted = {str(i) for i in ids if str(i).startswith(PREFIX)}
    if not wanted:
        return 0
    marked = 0
    with _locked(path):
        messages = _read(path)
        for m in messages:
            if m["id"] in wanted and m.get("status") != SENT:
                m["status"], marked = SENT, marked + 1
        if marked:
            write_atomic(path, {"messages": messages})
    return marked


def counts(settings: Settings) -> dict[str, int]:
    out = {PENDING: 0, SENT: 0, EXPIRED: 0}
    for m in _read(path_for(settings)):
        out[m.get("status", PENDING)] = out.get(m.get("status", PENDING), 0) + 1
    return out


# --- Textes ----------------------------------------------------------------------------------------------------------

def _p(value: float) -> str:
    return f"{value:.8g}"


def call_message(d: dict) -> str:
    """Message d'un APPEL : configuration, paire, unité, entrée, stop de clôture, stop de secours, TP1, objectif, R,
    explication (2-3 lignes), puis la mention shadow."""
    from .evaluate import TITLES, UNIT_LABEL
    unit = UNIT_LABEL.get(d["unit"], d["unit"])
    lines = [f"Price action CSI — {d['config']} ({TITLES.get(d['config'], '')}) · {d['symbol']} · {unit}",
             f"Entrée {_p(d['entry'])} (clôture du {d['at'][:16].replace('T', ' ')} UTC)",
             f"Stop de clôture {unit} {_p(d['stop'])} · stop de secours {_p(d['hard_stop'])}",
             f"TP1 {_p(d['tp1'])} (+1 R, moitié) · objectif {_p(d['objective'])} ({d['r_objective']:.2f} R)",
             f"R = {_p(d['risk'])} ({d['stop_pct']:.2f} % de l'entrée)",
             *d.get("explanation", [])[:3], FOOTER]
    return "\n".join(lines)


def resolution_message(res: dict, d: dict) -> str:
    central = (res.get("results") or {}).get("central") or {}
    if res.get("status") != "RESOLU" or not central:
        body = f"Résolution : {res.get('status')} ({res.get('reason') or 'données manquantes'})"
    else:
        excess = central.get("excess")
        adverse = (res["results"].get("defavorable") or {}).get("r")
        body = (f"Issue {central['outcome']} le {str(central['exit_at'])[:16].replace('T', ' ')} UTC · R net {central['r']:+.2f} "
                f"(défavorable {adverse if adverse is None else f'{adverse:+.2f}'}) · placebos "
                f"{central.get('placebo_mean') if central.get('placebo_mean') is not None else '—'} · "
                f"excès {f'{excess:+.2f}' if excess is not None else '—'}")
    return "\n".join([f"Price action CSI — {d['config']} · {d['symbol']} · appel du {d['at'][:16].replace('T', ' ')} UTC résolu",
                      body, FOOTER])
