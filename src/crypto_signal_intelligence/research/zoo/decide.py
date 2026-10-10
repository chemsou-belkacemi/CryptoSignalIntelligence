"""Décisions du zoo (docs/ZOO.md § 4.3 et § 5) : p-valeur et sens, Benjamini-Hochberg (décision) et
Benjamini-Yekutieli (descriptif) en découverte, validation au niveau 1 − 0,05 / m, verdicts.

Tout passe par l'interface commune des mesures (`MesureA`, `MesureB`) : `stat(scénario, delta=, garder=,
sans_paire=, niveau=, min_blocs=, min_transactions=)`, `contributions_annees`, `contributions_paires`,
`masque_sans_annee`, `stat_annee`. `delta` ajoute un effet planté (contrôle de puissance, § 8.3) : `δ` par jour
d'exposition (forme a) ou par semaine (forme b).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ...forward.costs import ADVERSE, CENTRAL
from .measure import NUL

CANDIDATE, NON_CANDIDATE, INSUFFISANT = "CANDIDATE", "NON_CANDIDATE", "INSUFFISANT"
VALIDEE, NON_VALIDEE = "VALIDÉE", "NON_VALIDÉE"
INSUFFISANT_PAR_CONSTRUCTION = "INSUFFISANT_PAR_CONSTRUCTION"
MESURE_NON_VALIDE, INSTRUMENT_AVEUGLE = "MESURE_NON_VALIDE", "INSTRUMENT_AVEUGLE"
INSTRUMENT_NON_VALIDE = "INSTRUMENT_NON_VALIDE"
RIEN = "RIEN"                      # issue globale : aucune CANDIDATE (le zoo s'arrête) ou aucune VALIDÉE
Q_DECOUVERTE, ALPHA = 0.10, 0.05
MIN_BLOCS_SANS_ANNEE = 12
PART_MAX_PAIRE = 0.60
ANNEES_POSITIVES = 2
DECISION = (CENTRAL, ADVERSE)
NULS = (NUL, NUL)


# --- Procédures de rejet ------------------------------------------------------------------------------------------

def benjamini_hochberg(pvalues: Mapping[str, float], q: float = Q_DECOUVERTE) -> set[str]:
    """Tri croissant p(1) ≤ … ≤ p(M) ; k = plus grand rang tel que p(k) ≤ q k / M ; les k premiers sont rejetés."""
    if not pvalues:
        return set()
    names = sorted(pvalues, key=lambda k: (pvalues[k], k))
    m = len(names)
    ranks = [i + 1 for i, name in enumerate(names) if pvalues[name] <= q * (i + 1) / m]
    return set(names[:max(ranks)]) if ranks else set()


def benjamini_yekutieli(pvalues: Mapping[str, float], q: float = Q_DECOUVERTE) -> set[str]:
    """Benjamini-Yekutieli (valide sous toute dépendance) : seuil q / c(M), c(M) = Σ 1/i."""
    m = len(pvalues)
    if m == 0:
        return set()
    return benjamini_hochberg(pvalues, q / sum(1.0 / i for i in range(1, m + 1)))


# --- Découverte ---------------------------------------------------------------------------------------------------

@dataclass
class Decouverte:
    test_id: str
    p: float                      # p du test = max des p des scénarios (1 si sens ou condition jointe en échec)
    insuffisant: bool
    stats: dict[str, dict] = field(default_factory=dict)
    gardes: dict = field(default_factory=dict)
    gardes_ok: bool = False
    statut: str = NON_CANDIDATE


def _stat_dict(s) -> dict:
    return {"stat": s.stat, "se": s.se, "blocs": s.blocs, "t": s.t, "p": s.p, "mu": s.mu, "ic": list(s.ic)}


def p_decision(mesure, scenarios: Sequence[str] = DECISION, *, delta: float = 0.0,
               contre_zero: bool = False) -> tuple[float, bool, dict[str, dict]]:
    """p du test (§ 4.3) : pour chaque scénario, p bilatérale de la statistique ; p = 1 si la statistique est ≤ 0 ou,
    en forme (a), si le rendement net absolu μ ≤ 0 (condition jointe) ; p du test = max des scénarios.
    Rend (p, insuffisant, détails). `contre_zero` (descriptif du contrôle H0) : ancienne statistique « μ contre 0 »."""
    details: dict[str, dict] = {}
    worst = 0.0
    for scenario in dict.fromkeys(scenarios):
        s = mesure.stat(scenario, delta=delta, contre_zero=contre_zero) if contre_zero else \
            mesure.stat(scenario, delta=delta)
        if s is None:
            return 1.0, True, details
        details[scenario] = _stat_dict(s)
        ok = s.stat > 0 and (mesure.forme == "b" or s.mu > 0)
        worst = max(worst, s.p if ok else 1.0)
    return worst, False, details


def _meilleure(contributions: Mapping) -> Any:
    return max(contributions, key=lambda k: (contributions[k], str(k))) if contributions else None


def gardes_decouverte(mesure, scenarios: Sequence[str] = DECISION, *, delta: float = 0.0) -> tuple[bool, dict]:
    """§ 5.1 point 2 : la statistique reste > 0 dans chaque scénario sans la meilleure année civile (contribution
    centrale la plus forte) et, si la garde s'applique, sans la meilleure paire."""
    reference = scenarios[0]
    out: dict = {}
    year = _meilleure(mesure.contributions_annees(reference, delta))
    ok = year is not None
    if year is not None:
        keep = mesure.masque_sans_annee(int(year))
        values = {}
        for scenario in dict.fromkeys(scenarios):
            s = mesure.stat(scenario, delta=delta, garder=keep, min_blocs=2, min_transactions=0)
            values[scenario] = None if s is None else s.stat
            ok = ok and s is not None and s.stat > 0
        out["sans_meilleure_annee"] = {"annee": int(year), "stat": values}
    if mesure.meilleure_paire:
        pair = _meilleure(mesure.contributions_paires(reference, delta))
        if pair is not None:
            values = {}
            for scenario in dict.fromkeys(scenarios):
                s = mesure.stat(scenario, delta=delta, sans_paire=str(pair))
                values[scenario] = None if s is None else s.stat
                ok = ok and s is not None and s.stat > 0
            out["sans_meilleure_paire"] = {"paire": str(pair), "stat": values}
        else:
            ok = False
    else:
        out["sans_meilleure_paire"] = "sans objet (règle de marché à un seul actif ou à l'univers entier)"
    return bool(ok), out


def decouverte(mesures: Mapping[str, object], scenarios: Sequence[str] = DECISION, *, q: float = Q_DECOUVERTE,
               deltas: Mapping[str, float] | None = None) -> tuple[dict[str, Decouverte], dict]:
    """Benjamini-Hochberg à q sur les p des tests mesurés, puis garde-fous ; Benjamini-Yekutieli en descriptif."""
    deltas = deltas or {}
    out: dict[str, Decouverte] = {}
    pvalues: dict[str, float] = {}
    for test_id, mesure in mesures.items():
        p, short, details = p_decision(mesure, scenarios, delta=deltas.get(test_id, 0.0))
        out[test_id] = Decouverte(test_id, p, short, details, statut=INSUFFISANT if short else NON_CANDIDATE)
        if not short:
            pvalues[test_id] = p
    rejected = benjamini_hochberg(pvalues, q)
    for test_id in rejected:
        ok, guards = gardes_decouverte(mesures[test_id], scenarios, delta=deltas.get(test_id, 0.0))
        out[test_id].gardes, out[test_id].gardes_ok = guards, ok
        out[test_id].statut = CANDIDATE if ok else NON_CANDIDATE
    summary = {"q": q, "tests_mesures": len(pvalues), "rejets_bh": sorted(rejected),
               "candidates": sorted(t for t, d in out.items() if d.statut == CANDIDATE),
               "rejets_by_descriptif": sorted(benjamini_yekutieli(pvalues, q))}
    return out, summary


# --- Validation ---------------------------------------------------------------------------------------------------

@dataclass
class Validation:
    test_id: str
    statut: str
    niveau: float
    details: dict = field(default_factory=dict)


def composantes_validation(mesure, scenarios: Sequence[str] = DECISION, *, delta: float = 0.0) -> dict:
    """Ce dont dépend la validation, indépendamment de m : p bilatérales (avec sens et condition jointe) de l'étape
    entière et sans la meilleure année (12 blocs au moins), années positives, concentration. La condition 1 au niveau
    1 − 0,05/m équivaut à : statistique > 0 et p bilatérale < 0,05/m."""
    out: dict = {"insuffisant": False}
    p_full, short, details = p_decision(mesure, scenarios, delta=delta)
    out |= {"p": p_full, "insuffisant": short, "stats": details}
    if short:
        return out
    year = _meilleure(mesure.contributions_annees(scenarios[0], delta))
    p_year = 1.0
    if year is not None:
        keep = mesure.masque_sans_annee(int(year))
        p_year = 0.0
        for scenario in dict.fromkeys(scenarios):
            s = mesure.stat(scenario, delta=delta, garder=keep, min_blocs=MIN_BLOCS_SANS_ANNEE, min_transactions=0)
            ok = s is not None and s.stat > 0 and (mesure.forme == "b" or s.mu > 0)
            p_year = max(p_year, s.p if ok and s is not None else 1.0)
    out["sans_meilleure_annee"] = {"annee": None if year is None else int(year), "p": p_year}
    years = [int(y) for y in mesure.etape.annees]
    positive = 0
    by_year = {}
    for y in years:
        value = mesure.stat_annee(y, scenarios[0]) if delta == 0 else _stat_annee_delta(mesure, y, scenarios[0], delta)
        by_year[str(y)] = value
        positive += int(value is not None and value > 0)
    out["annees"] = {"par_annee": by_year, "positives": positive, "ok": positive >= ANNEES_POSITIVES}
    if mesure.meilleure_paire:
        contributions = mesure.contributions_paires(scenarios[0], delta)
        gains = {k: v for k, v in contributions.items() if v > 0}
        total = sum(gains.values())
        share = max(gains.values()) / total if total > 0 else 1.0
        out["concentration"] = {"part_max": share, "paire": _meilleure(gains), "ok": share <= PART_MAX_PAIRE}
    else:
        out["concentration"] = {"ok": True, "note": "sans objet"}
    return out


def _stat_annee_delta(mesure, annee: int, scenario: str, delta: float) -> float | None:
    keep = mesure.masque_sans_annee(annee)
    only = 1.0 - keep
    s = mesure.stat(scenario, delta=delta, garder=only, min_blocs=1, min_transactions=0)
    return None if s is None else s.stat


def statut_validation(composantes: dict, m: int) -> str:
    if m <= 0:
        raise ValueError("validation sans candidate")
    if composantes.get("insuffisant"):
        return INSUFFISANT
    level = ALPHA / m
    ok = (composantes["p"] < level and composantes["sans_meilleure_annee"]["p"] < level
          and composantes["annees"]["ok"] and composantes["concentration"]["ok"])
    return VALIDEE if ok else NON_VALIDEE


def valider(mesures: Mapping[str, object], scenarios: Sequence[str] = DECISION) -> dict[str, Validation]:
    """Validation des m candidates (m = nombre de mesures passées) au niveau 1 − 0,05 / m."""
    m = len(mesures)
    out = {}
    for test_id, mesure in mesures.items():
        parts = composantes_validation(mesure, scenarios)
        out[test_id] = Validation(test_id, statut_validation(parts, m), 1 - ALPHA / m, parts)
    return out


def valeur_p_finie(p: float) -> float:
    return float(p) if np.isfinite(p) else 1.0
