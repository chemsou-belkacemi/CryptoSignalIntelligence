"""Confirmation des deux meilleures du zoo (docs/ZOO_CONFIRMATION.md) : TR37_1j et XS17_1j, mesurés une fois sur
l'étape `VALIDATION` (2023-01 → 2025-06) avec la mesure et la validation du zoo (`m = 2`).

Le seul code nouveau est la garde et le rapport ; la mesure (`run.mesurer_tout_detail`) et la décision
(`decide.valider`) sont celles du zoo, inchangées. Exécution refusée sans code commité et relu
(`zoo_review.CONFIRMATION_REVIEW`), sans configuration identique, sans la sélection déclarée dans la découverte
enregistrée, ou si la confirmation a déjà été exécutée.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from ...config import Settings
from ..experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .data import VALIDATION
from .decide import NON_CANDIDATE, VALIDEE, valider
from .h0 import STUDY
from .measure import MesureA, MesureB, descriptif_b
from .reel import STRATEGIES, AlreadyRun, SourceMagasin, empreintes_donnees
from .review import NotReady, exiger_codes, exiger_relecture
from .run import mesurer_tout_detail
from .spec import PAR_ID

TESTS_CONFIRMATION: tuple[str, ...] = ("TR37_1j", "XS17_1j")
STRATEGIE = "ZOO_CONFIRMATION"
T_SELECTION = 2.0
DOC = "docs/ZOO_CONFIRMATION.md"


def selection_declaree(summary: Mapping) -> None:
    """La découverte enregistrée doit contenir la sélection déclarée : chaque test `NON_CANDIDATE`, t central > 2."""
    tests = summary.get("tests", {})
    for test_id in TESTS_CONFIRMATION:
        entry = tests.get(test_id)
        if entry is None:
            raise NotReady(f"{test_id} absent de la découverte enregistrée : sélection non conforme")
        t = entry.get("stats", {}).get("central", {}).get("t")
        if entry.get("statut") != NON_CANDIDATE or t is None or not t > T_SELECTION:
            raise NotReady(f"{test_id} : statut {entry.get('statut')}, t = {t} ; sélection déclarée non retrouvée")


def decouverte_enregistree(registry: ExperimentRegistry) -> tuple[str, dict]:
    runs = [r for r in registry.recent(1000) if r["strategy"] == STRATEGIES["decouverte"]]
    if not runs:
        raise NotReady("aucune découverte du zoo enregistrée : confirmation impossible")
    run = registry.get(runs[0]["run_id"])
    if run is None:
        raise NotReady("découverte enregistrée illisible")
    summary = json.loads((Path(run["report_dir"]) / "summary.json").read_text(encoding="utf-8"))
    return run["run_id"], summary


def _descriptif(mesure: MesureA | MesureB) -> dict:
    return descriptif_b(mesure) if isinstance(mesure, MesureB) else {}


def executer_confirmation(settings: Settings, *, now: datetime,
                          progress: Callable[[str], None] | None = None) -> dict:
    from .. import zoo_review
    say = progress or (lambda _t: None)
    state = code_state()
    exiger_relecture(state, settings=settings, review=zoo_review.CONFIRMATION_REVIEW or "")
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.count_runs(STRATEGIE) > 0:
        raise AlreadyRun("confirmation déjà exécutée : exécution unique")
    discovery_id, discovery = decouverte_enregistree(registry)
    selection_declaree(discovery)
    exiger_codes(list(TESTS_CONFIRMATION))
    tests = [PAR_ID[t] for t in TESTS_CONFIRMATION]
    source = SourceMagasin(settings, VALIDATION, tests)
    measured, refs, _ = mesurer_tout_detail(tests, source, [VALIDATION], progress=say)
    mesures: dict[str, MesureA | MesureB] = {t: m for (t, _), m in measured.items()}
    if set(mesures) != set(TESTS_CONFIRMATION):
        raise RuntimeError(f"mesures inattendues : {sorted(mesures)}")
    verdicts = valider(mesures)
    m = len(TESTS_CONFIRMATION)
    run_id = new_run_id("ZOO-CONFIRMATION")
    program = registry.program_trials() + m
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run_id, "study": STUDY, "etape": "confirmation", "doc": DOC, "n_trials": m,
              "program_trials": program, "decouverte": discovery_id, "niveau": 1 - 0.05 / m,
              "tests": {t: {"statut": v.statut, "details": v.details, "descriptif": _descriptif(mesures[t])}
                        for t, v in verdicts.items()},
              "validees": sorted(t for t, v in verdicts.items() if v.statut == VALIDEE), "commit": state,
              "paires_sans_historique_1h": source.manquantes}
    (report_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind="ZOO",
                    hypothesis="confirmation des deux meilleures du zoo sur 2023-01 → 2025-06 (docs/ZOO_CONFIRMATION.md)",
                    strategy=STRATEGIE, strategy_version=1, variant="exécution unique",
                    params={"tests": list(TESTS_CONFIRMATION)}, period_label="DEVELOPMENT",
                    period_start=str(VALIDATION.debut), period_end=str(VALIDATION.fin), universe=source.symboles,
                    data_hashes=refs.empreintes | empreintes_donnees(settings, source), git_commit=state,
                    dependencies=dependency_versions(), seed=20261012, cost_scenario="central+defavorable",
                    simulation_rules={"doc": f"{DOC} § 2-3 ; docs/ZOO.md § 4.2, § 5.2"},
                    metrics={"n_trials": m, "program_trials": program, "validees": result["validees"]},
                    status="COMPLETED", report_dir=str(report_dir))
    return result
