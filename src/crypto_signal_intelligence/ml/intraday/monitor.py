"""Surveillance d'un modèle intraday en service (docs/ML_INTRADAY.md §8) : fonctions pures, testées.

Décide seulement si de NOUVELLES entrées sont permises. Une suspension ne coupe jamais une position
ouverte : elle va à sa sortie prévue (règle de la cible), et les limites centralisées continuent de
la compter. Aucun modèle intraday n'est en service tant que le protocole n'a rien validé.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from .portfolio import trade_mean_ci

MAX_MODEL_AGE = pd.Timedelta(days=213)       # ≈ 7 mois : réentraînement prévu tous les 6 mois
MAX_STALE_PAIRS = 4
MAX_DATA_DELAY = pd.Timedelta(minutes=30)   # deux bougies 15 min
DEGRADATION_TRADES = 100
DRAWDOWN_MULTIPLE = 1.5
CALIBRATION_SETUPS = 200
MAX_CALIBRATION_GAP = 0.10
EXISTING_POSITIONS = "KEEP_UNTIL_PLANNED_EXIT"


@dataclass
class MonitorDecision:
    allow_new_entries: bool
    reasons: list[str] = field(default_factory=list)
    blocked_pairs: list[str] = field(default_factory=list)
    existing_positions: str = EXISTING_POSITIONS


def model_age_reason(trained_at: datetime, now: datetime, max_age: pd.Timedelta = MAX_MODEL_AGE) -> str | None:
    age = pd.Timestamp(now) - pd.Timestamp(trained_at)
    return f"MODEL_TOO_OLD ({age.days} j > {max_age.days} j)" if age > max_age else None


def stale_pairs(last_available: dict[str, datetime | None], now: datetime, *,
                max_delay: pd.Timedelta = MAX_DATA_DELAY) -> list[str]:
    """Paires dont la dernière bougie 15 min disponible date de plus de `max_delay` (ou absente)."""
    return sorted(symbol for symbol, seen in last_available.items()
                  if seen is None or pd.Timestamp(now) - pd.Timestamp(seen) > max_delay)


def degradation_reason(net: np.ndarray, times, *, current_drawdown: float, reference_drawdown: float | None,
                       samples: int = 2000, seed: int = 0) -> str | None:
    """IC95 du gain moyen des derniers trades entièrement < 0, ou perte au-delà de 1,5 × la référence."""
    net = np.asarray(net, dtype=float)[-DEGRADATION_TRADES:]
    times = list(times)[-DEGRADATION_TRADES:]
    if len(net) >= DEGRADATION_TRADES:
        ci = trade_mean_ci(net, times, block_days=1, samples=samples, seed=seed, min_blocks=10)
        if ci is not None and ci[1] < 0:
            return f"PERFORMANCE_DEGRADED (IC95 du gain moyen {ci} < 0 sur {len(net)} trades)"
    if reference_drawdown is not None and reference_drawdown < 0 and \
            current_drawdown < DRAWDOWN_MULTIPLE * reference_drawdown:
        return f"DRAWDOWN_EXCEEDED ({current_drawdown:.2%} < {DRAWDOWN_MULTIPLE} × {reference_drawdown:.2%})"
    return None


def calibration_reason(p: np.ndarray, outcome: np.ndarray) -> str | None:
    """Écart entre probabilité prédite moyenne et fréquence observée sur les derniers setups."""
    p, outcome = np.asarray(p, dtype=float)[-CALIBRATION_SETUPS:], np.asarray(outcome, dtype=float)[-CALIBRATION_SETUPS:]
    if len(p) < CALIBRATION_SETUPS:
        return None
    gap = abs(float(p.mean()) - float(outcome.mean()))
    return f"CALIBRATION_DRIFT (écart {gap:.1%} > {MAX_CALIBRATION_GAP:.0%})" if gap > MAX_CALIBRATION_GAP else None


def decide(*, trained_at: datetime, now: datetime, last_available: dict[str, datetime | None],
           recent_net: np.ndarray, recent_times, current_drawdown: float, reference_drawdown: float | None,
           recent_p: np.ndarray, recent_outcome: np.ndarray) -> MonitorDecision:
    reasons = [r for r in (model_age_reason(trained_at, now),
                           degradation_reason(recent_net, recent_times, current_drawdown=current_drawdown,
                                              reference_drawdown=reference_drawdown),
                           calibration_reason(recent_p, recent_outcome)) if r]
    stale = stale_pairs(last_available, now)
    if len(stale) > MAX_STALE_PAIRS:
        reasons.append(f"DATA_QUALITY ({len(stale)} paires sans données récentes > {MAX_STALE_PAIRS})")
    return MonitorDecision(allow_new_entries=not reasons, reasons=reasons, blocked_pairs=stale)
