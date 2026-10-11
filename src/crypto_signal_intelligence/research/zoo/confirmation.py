"""Confirmation des deux meilleures du zoo (docs/ZOO_CONFIRMATION.md) : TR37_1j et XS17_1j, mesurés une fois sur
l'étape `VALIDATION` (2023-01 → 2025-06) avec la mesure et la validation du zoo (`m = 2`).

Le seul code nouveau est la garde et le rapport ; la mesure (`run.mesurer_tout_detail`) et la décision
(`decide.valider`) sont celles du zoo. Exécution refusée :
- sans code commité et relu (`zoo_review.CONFIRMATION_REVIEW`) ni configuration identique (`CONFIG_FINGERPRINT`) ;
- si le code de mesure diffère de celui relu pour le zoo (`zoo_review.CODE_REVIEW`) : tous les chemins surveillés,
  sauf ce module, et sauf, dans `zoo/review.py` et `cli.py`, le tuple `INSCRIPTIONS` et la commande `zoo_confirm`
  (comparaison des arbres syntaxiques) ;
- si la découverte épinglée (`DECOUVERTE_ZOO`, empreinte) ne contient pas la sélection déclarée ;
- si la confirmation a déjà été exécutée ou commencée (registre, ou dossier `ZOO-CONFIRMATION-*` existant : le dossier
  est créé AVANT toute mesure).
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

import numpy as np

from ...config import Settings
from ...forward.costs import ADVERSE, CENTRAL
from ..experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .data import DAY_H, VALIDATION
from .decide import ALPHA, NON_CANDIDATE, VALIDEE, valider
from .h0 import STUDY
from .measure import MesureA, MesureB, descriptif_b
from .reel import AlreadyRun, SourceMagasin, empreintes_donnees
from .review import _SRC, REVIEWED_PATHS, NotReady, exiger_codes, exiger_relecture, racine
from .run import References, Source, mesurer_tout_detail, panneau
from .spec import PAR_ID, Contexte

TESTS_CONFIRMATION: tuple[str, ...] = ("TR37_1j", "XS17_1j")
STRATEGIE = "ZOO_CONFIRMATION"
PREFIXE = "ZOO-CONFIRMATION"
T_SELECTION = 2.0
DOC = "docs/ZOO_CONFIRMATION.md"
#: découverte du zoo d'où vient la sélection : identifiant au registre, puis chemin du résumé « # » empreinte SHA-256
DECOUVERTE_ID = "ZOO-DECOUVERTE-20261011T055846Z-bcf9e6"
DECOUVERTE_ZOO = ("reports/ZOO-DECOUVERTE-20261011T055846Z-bcf9e6/summary.json"
                  "#3c596d341350b4c7df4a15b2e1f264895cf6068fc3169ad976c70bf02b65699d")
#: chemins surveillés propres à la confirmation, comparés au commit du zoo hors des seules parties ajoutées
PROPRE = f"{_SRC}/research/zoo/confirmation.py"
PARTIELS = {f"{_SRC}/research/zoo/review.py": ("INSCRIPTIONS",), f"{_SRC}/cli.py": ("zoo_confirm",)}
SEMAINES_PREMIER_BLOC = 4
GRANDES = ("BTCUSDT", "ETHUSDT")


# --- Garde ------------------------------------------------------------------------------------------------------

def _arbre_sans(text: str, noms: tuple[str, ...]) -> str:
    """Arbre syntaxique du module sans les affectations ni les fonctions de ces noms (positions ignorées)."""
    tree = ast.parse(text)

    def garder(node: ast.stmt) -> bool:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name not in noms
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            return not any(isinstance(t, ast.Name) and t.id in noms for t in targets)
        return True

    tree.body = [n for n in tree.body if garder(n)]
    return ast.dump(tree, include_attributes=False)


def _git(args: list[str], root: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotReady(f"git impossible : {exc}") from None


def code_de_mesure_identique(commit_zoo: str, root: Path, versus: str = "HEAD") -> None:
    """Le code de mesure est celui relu pour le zoo : `git diff` vide sur les chemins surveillés (hors ce module et les
    deux fichiers partiels), puis arbres identiques des fichiers partiels hors des parties ajoutées."""
    if not commit_zoo:
        raise NotReady("relecture du zoo non inscrite (CODE_REVIEW) : code de mesure non vérifiable")
    excluded = [f":(exclude){p}" for p in (PROPRE, *PARTIELS)]
    out = _git(["diff", "--quiet", commit_zoo, versus, "--", *REVIEWED_PATHS, *excluded], root)
    if out.returncode == 1:
        raise NotReady(f"code de mesure modifié depuis le zoo ({commit_zoo[:12]}) : confirmation refusée")
    if out.returncode != 0:
        raise NotReady(f"commit {commit_zoo[:12]} introuvable : {out.stderr.strip()[:200]}")
    for path, noms in PARTIELS.items():
        before, after = _git(["show", f"{commit_zoo}:{path}"], root), _git(["show", f"{versus}:{path}"], root)
        if before.returncode or after.returncode:
            raise NotReady(f"{path} illisible à {commit_zoo[:12]} ou {versus}")
        if _arbre_sans(before.stdout, noms) != _arbre_sans(after.stdout, noms):
            raise NotReady(f"{path} modifié depuis le zoo au-delà de {', '.join(noms)} : confirmation refusée")


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


def decouverte_epinglee(registry: ExperimentRegistry, root: Path) -> dict:
    """Résumé de la découverte épinglée : enregistrée au registre, fichier présent et d'empreinte identique."""
    if registry.get(DECOUVERTE_ID) is None:
        raise NotReady(f"découverte {DECOUVERTE_ID} absente du registre : confirmation impossible")
    path_text, digest = DECOUVERTE_ZOO.rsplit("#", 1)
    path = root / path_text
    if not path.exists():
        raise NotReady(f"résumé de la découverte introuvable : {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise NotReady("résumé de la découverte modifié depuis son épinglage : confirmation refusée")
    return json.loads(path.read_text(encoding="utf-8"))


def deja_commencee(registry: ExperimentRegistry, reports_dir: Path) -> None:
    if registry.count_runs(STRATEGIE) > 0:
        raise AlreadyRun("confirmation déjà exécutée : exécution unique")
    started = sorted(p.name for p in reports_dir.glob(f"{PREFIXE}-*")) if reports_dir.exists() else []
    if started:
        raise AlreadyRun(f"confirmation déjà commencée ({started[0]}) : exécution unique")


# --- Mesure et rapport ------------------------------------------------------------------------------------------

def part_des_grandes(test_id: str, refs: References, source: Source) -> float | None:
    """Descriptif : part moyenne de BTC et d'ETH dans les poids du portefeuille, sur les échanges de l'étape."""
    test = PAR_ID[test_id]
    board = panneau(refs.quotidien, refs.univers, jusqu_a_jour=VALIDATION.fin_jour)
    ctx = Contexte("", DAY_H, test.params, VALIDATION.debut_ns, externes=MappingProxyType(source.externes),
                   evenements=MappingProxyType(source.evenements), univers=refs.univers)
    weights = test.strategie_fonction()(board, ctx)
    keep = (weights.decisions >= VALIDATION.debut_ns) & (weights.decisions <= VALIDATION.fin_ns)
    columns = [k for k, s in enumerate(weights.symboles) if s in GRANDES]
    totals = weights.poids[keep].sum(axis=1)
    if not keep.any() or not columns or not (totals > 0).any():
        return None
    share = weights.poids[keep][:, columns].sum(axis=1)[totals > 0] / totals[totals > 0]
    return float(share.mean())


def rapport_test(mesure: MesureA | MesureB, verdict, m: int, part: float | None) -> dict:
    out: dict = {"statut": verdict.statut, "details": verdict.details}
    if not isinstance(mesure, MesureB):
        return out
    level = 1 - ALPHA / m
    first = np.ones(len(mesure.semaines))
    first[:SEMAINES_PREMIER_BLOC] = 0.0
    out["intervalle_decision"] = {}
    out["sans_premier_bloc"] = {}
    for scenario in (CENTRAL, ADVERSE):
        s = mesure.stat(scenario, niveau=level)
        out["intervalle_decision"][scenario] = None if s is None else {"niveau": level, "stat": s.stat,
                                                                      "ic": list(s.ic)}
        s = mesure.stat(scenario, garder=first)
        out["sans_premier_bloc"][scenario] = None if s is None else {"stat": s.stat, "t": s.t}
    out["descriptif"] = descriptif_b(mesure) | {"part_btc_eth": part}
    return out


def mesurer_et_decider(source: Source, *, progress: Callable[[str], None] | None = None) -> tuple[dict, References]:
    """Mesure du zoo sur l'étape VALIDATION pour les deux tests seulement, validation `m = 2`, rapport par test."""
    tests = [PAR_ID[t] for t in TESTS_CONFIRMATION]
    measured, refs, _ = mesurer_tout_detail(tests, source, [VALIDATION], progress=progress)
    mesures: dict[str, MesureA | MesureB] = {t: m for (t, _), m in measured.items()}
    if set(mesures) != set(TESTS_CONFIRMATION):
        raise RuntimeError(f"mesures inattendues : {sorted(mesures)}")
    verdicts = valider(mesures)
    m = len(TESTS_CONFIRMATION)
    parts = {t: part_des_grandes(t, refs, source) if PAR_ID[t].meilleure_paire else None for t in mesures}
    tests_out = {t: rapport_test(mesures[t], v, m, parts[t]) for t, v in verdicts.items()}
    return {"niveau": 1 - ALPHA / m, "tests": tests_out,
            "validees": sorted(t for t, v in verdicts.items() if v.statut == VALIDEE)}, refs


def executer_confirmation(settings: Settings, *, now: datetime,
                          progress: Callable[[str], None] | None = None, root: Path | None = None) -> dict:
    from .. import zoo_review
    root = root or racine()
    state = code_state()
    if not zoo_review.CONFIRMATION_REVIEW:
        raise NotReady("relecture leak-auditor de la confirmation non inscrite (CONFIRMATION_REVIEW) : refusée")
    exiger_relecture(state, settings=settings, review=zoo_review.CONFIRMATION_REVIEW)
    code_de_mesure_identique(zoo_review.CODE_REVIEW or "", root)
    registry = ExperimentRegistry(settings.experiments_db)
    deja_commencee(registry, settings.reports_dir)
    selection_declaree(decouverte_epinglee(registry, root))
    exiger_codes(list(TESTS_CONFIRMATION))
    m = len(TESTS_CONFIRMATION)
    run_id = new_run_id(PREFIXE)
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=False)          # marque « commencée » AVANT toute mesure
    (report_dir / "debut.json").write_text(json.dumps({"run_id": run_id, "commit": state, "date": now.isoformat()},
                                                      ensure_ascii=False), encoding="utf-8")
    source = SourceMagasin(settings, VALIDATION, [PAR_ID[t] for t in TESTS_CONFIRMATION])
    decided, refs = mesurer_et_decider(source, progress=progress)
    program = registry.program_trials() + m
    result = {"run_id": run_id, "study": STUDY, "etape": "confirmation", "doc": DOC, "n_trials": m,
              "program_trials": program, "decouverte": DECOUVERTE_ID, **decided, "commit": state,
              "paires_sans_historique_1h": getattr(source, "manquantes", [])}
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
