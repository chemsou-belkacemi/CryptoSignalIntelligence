"""Conseil de risque à 24 h, en SHADOW (plan de travail § 2 bis, point 2 ; docs/RISK_SHADOW.md).

Seule prévision de volatilité CONFIRMÉE hors échantillon (docs/VOLATILITY.md § 19, lecture unique de la période
finale) : HAR + profil heure × jour, variance des 24 h suivantes. Le test en direct F12 la calcule déjà chaque jour à
00:00 UTC pour les paires du service (fonctions gelées) ; ce module LIT son journal, sans rien y écrire ni rien
recalculer, et en tire pour chaque paire :
- `move_24h_pct` : ampleur typique sur 24 h, √variance prévue (un écart-type de rendement, en %) ;
- `relative_size` : taille relative à risque égal, médiane des paires / ampleur de la paire, bornée à [0,25 ; 2] ;
  une paire deux fois plus agitée que la médiane reçoit la moitié de la taille ;
- `stop_floor_pct` : un stop plus serré que cette ampleur se trouve à l'intérieur du mouvement ordinaire d'une
  journée (ce n'est pas une probabilité d'être touché : la forme des rendements n'est pas supposée).

Aucun de ces chiffres n'est une prévision de sens ni de gain. Les conseils du jour sont journalisés
(`state/risk_shadow.jsonl`) pour être comparés plus tard à ce qui s'est passé.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings

SOURCE_TEST = "F12_VOL_FORWARD"
MODEL = "H1_HAR_PROFILE"
SIZE_BOUNDS = (0.25, 2.0)
MAX_AGE = pd.Timedelta(hours=36)            # au-delà, la prévision est périmée : pas de conseil
NOTE = ("Ampleur typique des 24 prochaines heures (un écart-type), prévue par le seul modèle de volatilité confirmé "
        "hors échantillon (HAR + profil heure × jour, VOLATILITY.md § 19). Taille relative : à risque égal entre paires. "
        "Information en shadow : aucune influence sur les signaux ni sur BinanceSpotManager ; aucune prévision de sens.")


def journal_path(settings: Settings) -> Path:
    return settings.root / "forward" / f"{SOURCE_TEST}.jsonl"


def latest_forecast(settings: Settings) -> dict | None:
    """Dernière entrée PREVISION de F12 (lecture seule du journal)."""
    from ..forward.journal import Journal
    last = None
    for entry in Journal(journal_path(settings)).entries({"PREVISION"}):
        last = entry
    return None if last is None else last["data"]


def advice(forecast: dict | None, *, now: datetime) -> dict:
    if not forecast:
        return {"available": False, "reason": "aucune prévision à 24 h enregistrée par F12 pour l'instant", "note": NOTE}
    origin = pd.Timestamp(forecast["origin"])
    if pd.Timestamp(now) - origin > MAX_AGE:
        return {"available": False, "reason": f"prévision du {origin:%Y-%m-%d %H:%M} UTC périmée", "note": NOTE}
    moves = {}
    for symbol, horizons in (forecast.get("pairs") or {}).items():
        variance = ((horizons or {}).get("24h") or {}).get(MODEL)
        if isinstance(variance, int | float) and variance > 0 and math.isfinite(variance):
            moves[symbol] = math.sqrt(variance)
    if not moves:
        return {"available": False, "reason": "prévision à 24 h vide", "note": NOTE}
    median = float(pd.Series(moves).median())
    low, high = SIZE_BOUNDS
    pairs = {s: {"move_24h_pct": round(m * 100, 2), "stop_floor_pct": round(m * 100, 2),
                 "relative_size": round(min(high, max(low, median / m)), 2)} for s, m in moves.items()}
    return {"available": True, "origin": origin.isoformat(), "model": MODEL, "median_move_24h_pct": round(median * 100, 2),
            "pairs": dict(sorted(pairs.items(), key=lambda kv: -kv[1]["move_24h_pct"])), "note": NOTE}


def current(settings: Settings, *, now: datetime) -> dict:
    return advice(latest_forecast(settings), now=now)


def record_day(settings: Settings, *, now: datetime) -> dict | None:
    """Journalise le conseil du jour une seule fois par prévision (ajout seul) ; None si rien de nouveau."""
    out = current(settings, now=now)
    if not out.get("available"):
        return None
    path = settings.root / "state" / "risk_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("origin") == out["origin"]:
                return None
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"recorded_at": pd.Timestamp(now).isoformat(), **out}, ensure_ascii=False) + "\n")
    return {"origin": out["origin"], "pairs": len(out["pairs"])}
