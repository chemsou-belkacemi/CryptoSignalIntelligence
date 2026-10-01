"""Bilan honnête d'une source Telegram : écart entre ses résultats réels et le taux de base, avec incertitude.

Pour chaque signal résolu (ordre rempli, puis TP1, stop ou sortie temporelle), l'écart vaut
R réalisé − R de base de la même géométrie, calculé avec les MÊMES règles de remplissage et de sortie
(external/base_rate.py, méthode LIMIT_ALIGNED_V2). La moyenne de ces écarts mesure l'apport de
sélection de la source ; son intervalle vient d'un bootstrap par JOURS de réception (les signaux
d'un même jour suivent le même marché et ne sont pas indépendants). Tant que moins de
MIN_RESOLVED signaux, répartis sur au moins MIN_DAYS jours, sont résolus, aucune conclusion n'est
affichée : le chiffre n'est pas une note du groupe et jamais une probabilité de réussite du
prochain signal.

Regards répétés : ce bilan est recalculé à chaque signal. S'arrêter la première fois que l'IC
passe au-dessus de 0 gonfle le risque de faux positif ; une conclusion ne vaut que si elle tient
dans la durée, sur des signaux reçus APRÈS la décision de faire confiance au groupe.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..backtest.metrics import day_block_ci95
from .registry import ExternalSignalRegistry

MIN_RESOLVED = 20
MIN_DAYS = 10
# Preuve en direct d'un groupe (docs/EXTERNAL_SIGNALS.md, « Avis lié au groupe ») : plus exigeante que la simple
# conclusion, parce que le bilan est relu à chaque signal (regards répétés).
PROOF_RESOLVED = 30
PROOF_DAYS = 15
RESOLVED = ("TP1_FIRST", "SL_FIRST", "TIMEOUT")


@dataclass(frozen=True)
class SourceRecord:
    source: str
    evaluated: int
    duplicates: int
    copies: int
    refused: int
    pending: int
    unfilled: int
    resolved: int
    tp1_real: float | None
    tp1_base: float | None
    r_real: float | None
    r_base: float | None
    edge_r: float | None
    edge_ci95: tuple[float, float] | None
    conclusion: str
    r_ci95: tuple[float, float] | None = None      # IC95 du R réalisé (mêmes blocs d'un jour que l'écart)
    days: int = 0                                  # jours de réception distincts des signaux résolus
    proven: bool = False                           # preuve en direct : voir `proof`
    proof: str = ""


def source_records(registry: ExternalSignalRegistry, *, samples: int = 2000, seed: int = 20260930) -> list[SourceRecord]:
    records = []
    rows_by_source: dict[str, list[dict]] = {}
    with registry.connect() as db:
        for row in db.execute(f"""SELECT source, received_at, outcome, outcome_r, p_tp1, base_expectancy_r
                                  FROM external_signals
                                  WHERE outcome IN {RESOLVED} AND outcome_r IS NOT NULL"""):
            rows_by_source.setdefault(row["source"], []).append(dict(row))
    for stats in registry.source_stats():
        rows = rows_by_source.get(stats["source"], [])
        resolved = len(rows)
        with_base = [r for r in rows if r["base_expectancy_r"] is not None]
        edges = np.array([r["outcome_r"] - r["base_expectancy_r"] for r in with_base], dtype=float)
        edge = round(float(edges.mean()), 4) if len(edges) else None
        ci, days = None, 0
        times = np.array([r["received_at"] for r in with_base])
        if len(edges) >= MIN_RESOLVED:
            ci, days = day_block_ci95(edges, times, block_days=1, samples=samples, seed=seed, min_blocks=MIN_DAYS)
        realized = np.array([r["outcome_r"] for r in with_base], dtype=float)
        r_ci, _ = (day_block_ci95(realized, times, block_days=1, samples=samples, seed=seed, min_blocks=MIN_DAYS)
                   if len(realized) >= MIN_RESOLVED else (None, 0))
        distinct_days = len({str(t)[:10] for t in times})
        enough = len(edges) >= PROOF_RESOLVED and distinct_days >= PROOF_DAYS
        proven = bool(enough and ci is not None and ci[0] > 0 and r_ci is not None and r_ci[0] > 0)
        if proven:
            proof = (f"prouvé en direct : {len(edges)} signaux résolus sur {distinct_days} jours, gain moyen et écart "
                     "au taux de base tous deux positifs (IC95 > 0)")
        elif not enough:
            proof = (f"preuve en direct : {len(edges)}/{PROOF_RESOLVED} signaux résolus, "
                     f"{distinct_days}/{PROOF_DAYS} jours")
        else:
            proof = "non prouvé : le gain moyen ou l'écart au taux de base n'est pas positif avec certitude (IC95)"
        if resolved < MIN_RESOLVED:
            conclusion = f"trop peu de signaux résolus ({resolved} < {MIN_RESOLVED}) : aucune conclusion"
        elif len(edges) < MIN_RESOLVED:
            conclusion = "taux de base manquant pour trop de signaux : aucune conclusion"
        elif ci is None:
            conclusion = f"signaux concentrés sur {days} jour(s) < {MIN_DAYS} : aucune conclusion"
        elif ci[0] > 0:
            conclusion = "au-dessus du taux de base (IC95 de l'écart entièrement > 0)"
        elif ci[1] < 0:
            conclusion = "en dessous du taux de base (IC95 de l'écart entièrement < 0)"
        else:
            conclusion = "pas d'écart démontré avec le taux de base (IC95 contient 0)"
        tp1_real = (sum(r["outcome"] == "TP1_FIRST" for r in rows) / resolved) if resolved else None
        base_tp1 = [r["p_tp1"] for r in rows if r["p_tp1"] is not None]
        records.append(SourceRecord(
            source=stats["source"], evaluated=int(stats["evaluated"] or 0), duplicates=int(stats["duplicates"] or 0),
            copies=int(stats["copies"] or 0), refused=int(stats["refuse"] or 0), pending=int(stats["pending"] or 0),
            unfilled=int(stats["unfilled"] or 0), resolved=resolved,
            tp1_real=round(tp1_real, 4) if tp1_real is not None else None,
            tp1_base=round(float(np.mean(base_tp1)), 4) if base_tp1 else None,
            r_real=round(float(np.mean([r["outcome_r"] for r in rows])), 4) if rows else None,
            r_base=round(float(np.mean([r["base_expectancy_r"] for r in with_base])), 4) if with_base else None,
            edge_r=edge, edge_ci95=ci, conclusion=conclusion, r_ci95=r_ci, days=distinct_days, proven=proven,
            proof=proof))
    return records


def source_record(registry: ExternalSignalRegistry, source: str, *, seed: int = 20260930) -> SourceRecord | None:
    """Bilan d'UNE source (None si elle n'a encore rien d'enregistré)."""
    return next((r for r in source_records(registry, seed=seed) if r.source == source), None)
