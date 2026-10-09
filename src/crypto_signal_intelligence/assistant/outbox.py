"""Boîte Telegram de l'assistant (`state/assistant_outbox.json`) : messages en attente que le service relais lit par
`GET /assistant/outbox` et marque envoyés par `POST /assistant/sent`. Un message en attente depuis plus de 6 h passe
EXPIRE. Rien ici ne touche à `SignalRegistry` ni à `signals/` : l'assistant a sa propre boîte.

Lecture-modification-écriture sous un verrou de fichier (la surveillance et l'API sont deux processus), écriture
atomique (fichier temporaire puis renommage)."""
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

PENDING, SENT, EXPIRED = "EN_ATTENTE", "ENVOYE", "EXPIRE"
EXPIRE_AFTER = pd.Timedelta(hours=6)
MAX_PENDING_RETURNED = 20
KEEP_MESSAGES = 300                     # messages gardés dans le fichier (les plus récents)
FOOTER = "Shadow : aucun ordre. Test en direct F18, aucun gain démontré."


def path_for(settings: Settings) -> Path:
    return settings.root / "state" / "assistant_outbox.json"


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
    """Ajoute un message EN_ATTENTE (une seule fois par identifiant) ; False s'il existait déjà."""
    path = path_for(settings)
    moment = pd.Timestamp(now)
    with _locked(path):
        messages = _read(path)
        if any(m["id"] == message_id for m in messages):
            return False
        messages.append({"id": message_id, "created_at": utc_iso(moment), "text": text, "status": PENDING})
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
    """Passe ENVOYE les messages listés (en attente ou expirés) ; rend le nombre marqués."""
    path = path_for(settings)
    wanted = {str(i) for i in ids}
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


# --- Textes ------------------------------------------------------------------------------------------------------

def _p(value: float) -> str:
    return f"{value:.8g}"


def call_message(d: dict) -> str:
    """Message compact d'un APPEL (français)."""
    setup = "repli puis reprise" if d["setup"] == "REPLI_REPRISE" else "rejet du support"
    size = " · taille réduite (feu ORANGE)" if d.get("size") == "réduite" else ""
    lines = [f"Assistant CSI — {d['symbol']} · {d['regime']} · {setup}{size}",
             f"Entrée {_p(d['entry'])} (clôture 4 h du {d['at'][:16].replace('T', ' ')} UTC)",
             f"Stop de clôture 4 h {_p(d['stop'])} · stop de secours {_p(d['hard_stop'])}",
             f"TP1 {_p(d['tp1'])} (+1 R, moitié) · TP2 {_p(d['tp2'])} ({d['r_tp2']:.2f} R)",
             f"R = {_p(d['risk'])} ({d['volatility']['stop_pct']:.2f} %) · score {d['score']:.0f}/100",
             *d.get("explanation", [])[:3], FOOTER]
    return "\n".join(lines)


def resolution_message(res: dict, d: dict) -> str:
    central = (res.get("results") or {}).get("central") or {}
    if res.get("status") != "RESOLU" or not central:
        body = f"Résolution : {res.get('status')} ({res.get('reason') or 'données manquantes'})"
    else:
        excess = central.get("excess")
        body = (f"Issue {central['outcome']} le {str(central['exit_at'])[:16].replace('T', ' ')} UTC · R net {central['r']:+.2f} "
                f"(défavorable {((res['results'].get('defavorable') or {}).get('r') or 0):+.2f}) · "
                f"placebos {central.get('placebo_mean') if central.get('placebo_mean') is not None else '—'} · "
                f"excès {f'{excess:+.2f}' if excess is not None else '—'}")
    return "\n".join([f"Assistant CSI — {d['symbol']} · appel du {d['at'][:16].replace('T', ' ')} UTC résolu", body, FOOTER])
