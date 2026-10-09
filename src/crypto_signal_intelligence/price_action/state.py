"""État « price action » pour le propriétaire (`state/price_action.json`, `GET /price-action`, carte « Price action ») :
dernière évaluation (heure, candidats par configuration, refus), appels actifs avec niveaux et explication, 20 derniers
refus, prochaine évaluation, et un `resume` de 4 lignes en français. Écriture atomique par la surveillance (F19) ;
lecture seule par l'API. Aucun ordre, aucun gain démontré."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..forward.journal import utc_iso
from . import detect as D
from .outbox import write_atomic

NOTE = ("Price action en shadow : CSI ne passe aucun ordre. Les appels sont mesurés en direct par le test "
        "F19_PRICE_ACTION contre des placebos ; aucun gain n'est démontré.")
MAX_REFUSALS = 20


def path_for(settings: Settings) -> Path:
    return settings.root / "state" / "price_action.json"


def next_evaluation(after) -> pd.Timestamp:
    return pd.Timestamp(after).floor("4h") + pd.Timedelta(hours=4)


def resume_lines(payload: dict) -> list[str]:
    """Quatre lignes : dernière clôture et candidats · appels actifs · derniers refus · prochaine évaluation."""
    cands = payload.get("candidates") or {}
    total = sum(cands.values())
    first = (f"Clôture du {str(payload.get('at') or '—')[:16].replace('T', ' ')} UTC : {total} candidat(s)"
             + (" (" + ", ".join(f"{k} {v}" for k, v in cands.items() if v) + ")" if total else "")
             + (" · évaluation tardive" if payload.get("late") else ""))
    active = payload.get("active_calls") or []
    if active:
        second = f"{len(active)} appel(s) actif(s) : " + ", ".join(
            f"{c['symbol']} ({c['config']}" + (f", {c['latent_r']:+.1f} R".replace(".", ",") if c.get("latent_r") is not None else "")
            + ")" for c in active)
    else:
        second = "Aucun appel actif"
    counts: dict[str, int] = {}
    for r in payload.get("last_refusals") or []:
        counts[r.get("reason", "?")] = counts.get(r.get("reason", "?"), 0) + 1
    third = ("Derniers refus : " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
             if counts else "Derniers refus : aucun")
    nxt = payload.get("next_evaluation_at")
    fourth = f"Prochaine évaluation : {str(nxt)[11:16]} UTC" if nxt else "Prochaine évaluation : —"
    return [first, second, third, fourth]


def build(evaluation: dict | None, *, active_calls: list[dict], last_refusals: list[dict], now: datetime) -> dict:
    ev = evaluation or {}
    payload = {"available": evaluation is not None, "at": ev.get("at"), "evaluated_at": ev.get("evaluated_at"),
               "late": ev.get("late"), "candidates": ev.get("candidates") or dict.fromkeys(D.CONFIGS, 0),
               "calls_made": len(ev.get("calls") or ev.get("call_ids") or []), "refusals_by_reason": ev.get("refusals_by_reason") or {},
               "active_calls": active_calls, "last_refusals": last_refusals[-MAX_REFUSALS:],
               "next_evaluation_at": utc_iso(next_evaluation(pd.Timestamp(ev["at"]) if ev.get("at") else now)),
               "written_at": utc_iso(now), "configs": list(D.CONFIGS), "places_orders": False, "note": NOTE}
    payload["resume"] = "\n".join(resume_lines(payload))
    return payload


def write(settings: Settings, payload: dict) -> Path:
    path = path_for(settings)
    write_atomic(path, payload)
    return path


def read(settings: Settings) -> dict:
    path = path_for(settings)
    if not path.exists():
        return {"available": False, "reason": "aucune évaluation encore (F19 pas démarré, ou première clôture 4 h à venir)",
                "places_orders": False, "note": NOTE, "resume": "Price action : aucune évaluation encore."}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"état illisible : {type(exc).__name__}", "places_orders": False, "note": NOTE,
                "resume": "Price action : état illisible."}
