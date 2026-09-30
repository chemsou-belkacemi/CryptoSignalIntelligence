"""Santé de la surveillance (point 19) : « processus démarré » n'est pas « service prêt ».

Prêt = au moins un cycle terminé, et le dernier date de moins d'une période de bougie plus les
délais d'attente (plus une marge). Un cycle terminé avec des paires sans données reste « prêt »
mais « dégradé » : la santé n'invente jamais de données manquantes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import Settings
from ..data.schema import interval

MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Health:
    ready: bool
    degraded: bool
    detail: str


def check(settings: Settings, *, now: datetime) -> Health:
    path = settings.root / settings.live.status_file
    if not path.exists():
        return Health(False, False, "aucun cycle terminé : processus peut-être démarré, service pas encore prêt")
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        finished = datetime.fromisoformat(str(status["last_cycle"]["finished_at"]).replace(" ", "T"))
    except (ValueError, KeyError, TypeError) as exc:
        return Health(False, False, f"état illisible : {exc}")
    max_age = (interval(settings.data.setup_timeframe) + timedelta(seconds=settings.live.grace_seconds)
               + timedelta(seconds=settings.live.candle_wait_seconds) + MARGIN)
    age = now - finished
    if age > max_age:
        return Health(False, False, f"dernier cycle terminé il y a {age.total_seconds() / 60:.0f} min (> {max_age})")
    cycle = status["last_cycle"]
    degraded = bool(cycle.get("errors") or cycle.get("missing_after_wait"))
    detail = (f"prêt : cycle {cycle.get('decision_close')} terminé il y a {age.total_seconds():.0f} s, "
              f"{sum(cycle.get('counts', {}).values())} analyses, {len(status.get('published', []))} publication(s)")
    if degraded:
        detail += f" ; DÉGRADÉ : {len(cycle.get('errors', []))} erreur(s), absents {cycle.get('missing_after_wait')}"
    return Health(True, degraded, detail)
