"""État de l'assistant pour le propriétaire (`state/assistant.json`, `GET /assistant`, carte « Assistant ») : dernière
évaluation (heure, feu, BTC, paires par régime), appels actifs avec niveaux, score et explication, 20 derniers refus
avec leur raison, prochaine évaluation, et un `resume` de 5 lignes en français (commande `/etat` du relais).

Écriture atomique par la surveillance (F18) ; lecture seule par l'API. Aucun ordre, aucun gain démontré."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..forward.journal import utc_iso
from . import rules as R
from .outbox import write_atomic

NOTE = ("Assistant de marché en shadow : CSI ne passe aucun ordre. Ses appels sont mesurés en direct par le test "
        "F18_ASSISTANT contre des placebos ; aucun gain n'est démontré.")
MAX_REFUSALS = 20
REASON_LABELS = {
    R.RESISTANCE_NEAR: "résistance proche", R.MACRO: "événement macro", R.NEWS: "news de risque",
    R.NEWS_UNREACHABLE: "news injoignables", R.STOP_TIGHT: "stop hors volatilité", R.STOP_WIDE: "stop hors volatilité",
    R.VOL_UNKNOWN: "volatilité inconnue", R.GAIN_RISK: "gain/risque", R.ACTIVE: "appel déjà actif", R.REST: "repos 48 h",
    R.QUOTA: "quota du jour", R.BOOK_UNREACHABLE: "carnet injoignable", R.BOOK_SPREAD: "liquidité",
    R.BOOK_SLIPPAGE: "liquidité", R.BOOK_IMBALANCE: "liquidité", R.NO_SETUP: "configuration",
}


def path_for(settings: Settings) -> Path:
    return settings.root / "state" / "assistant.json"


def next_evaluation(after: datetime) -> pd.Timestamp:
    return pd.Timestamp(after).floor("4h") + R.H4


def _r(value: float) -> str:
    """« +0,4 R » (virgule décimale, comme dans les messages au propriétaire)."""
    return f"{value:+.1f} R".replace(".", ",")


def resume_lines(payload: dict) -> list[str]:
    """Cinq lignes : feu et BTC · paires par régime · appels actifs · derniers refus · prochaine évaluation."""
    light, btc = payload.get("light") or {}, payload.get("btc") or {}
    btc_text = ("BTC au-dessus de son EMA50" if btc.get("above_ema50") else "BTC sous son EMA50"
                if btc.get("known") else "BTC inconnu")
    silence = payload.get("silence")
    first = f"Feu {light.get('color', 'INCONNU')} · {btc_text}" + (f" · silence ({silence})" if silence else "")
    regimes = payload.get("regimes") or {}
    total = sum(regimes.values())
    second = (f"{total} paires : " + ", ".join(f"{regimes.get(k, 0)} {k}" for k in (R.UP, R.RANGE, R.DOWN, R.UNDECIDED))
              + (f", {regimes[R.UNREADABLE]} non évaluables" if regimes.get(R.UNREADABLE) else ""))
    active = payload.get("active_calls") or []
    if active:
        items = ", ".join(f"{c['symbol']} ({c['regime']}, {_r(c['latent_r']) if c.get('latent_r') is not None else 'en cours'})"
                          for c in active)
        third = f"{len(active)} appel{'s' if len(active) > 1 else ''} actif{'s' if len(active) > 1 else ''} : {items}"
    else:
        third = "Aucun appel actif"
    counts: dict[str, int] = {}
    for r in payload.get("last_refusals") or []:
        label = REASON_LABELS.get(r.get("reason"), str(r.get("reason")).lower())
        counts[label] = counts.get(label, 0) + 1
    fourth = ("Derniers refus : " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
              if counts else "Derniers refus : aucun")
    nxt = payload.get("next_evaluation_at")
    fifth = f"Prochaine évaluation : {str(nxt)[11:16]} UTC" if nxt else "Prochaine évaluation : —"
    return [first, second, third, fourth, fifth]


def build(evaluation: dict | None, *, active_calls: list[dict], last_refusals: list[dict], now: datetime) -> dict:
    """Charge utile de `state/assistant.json`."""
    ev = evaluation or {}
    payload = {"available": evaluation is not None, "evaluated_at": ev.get("evaluated_at"), "at": ev.get("at"),
               "light": ev.get("light"), "btc": ev.get("btc"), "silence": ev.get("silence"), "size": ev.get("size"),
               "regimes": ev.get("regimes") or dict.fromkeys(R.REGIMES, 0), "candidates": ev.get("candidates", 0),
               "calls_made": len(ev.get("calls") or ev.get("call_ids") or []), "active_calls": active_calls,
               "last_refusals": last_refusals[-MAX_REFUSALS:],
               "next_evaluation_at": utc_iso(next_evaluation(pd.Timestamp(ev["at"]) if ev.get("at") else now)),
               "written_at": utc_iso(now), "places_orders": False, "note": NOTE}
    payload["resume"] = "\n".join(resume_lines(payload))
    return payload


def write(settings: Settings, payload: dict) -> Path:
    path = path_for(settings)
    write_atomic(path, payload)
    return path


def read(settings: Settings) -> dict:
    path = path_for(settings)
    if not path.exists():
        return {"available": False, "reason": "aucune évaluation encore (F18 pas démarré, ou première clôture 4 h à venir)",
                "places_orders": False, "note": NOTE, "resume": "Assistant : aucune évaluation encore."}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"état illisible : {type(exc).__name__}", "places_orders": False, "note": NOTE,
                "resume": "Assistant : état illisible."}
