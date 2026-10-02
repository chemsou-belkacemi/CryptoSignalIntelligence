"""Pré-inscription, démarrage et gel des tests en direct (règles 4 et 5 de la mission du 2026-10-02).

- Un test ne démarre que si sa section existe dans docs/FORWARD_TESTS.md avec tous les champs exigés
  (hypothèse, règles, paramètres, métrique, seuil de décision, date d'évaluation), que le screening halal est
  validé et que le code est commité.
- Au démarrage, trois empreintes sont figées dans le journal du test : celle de sa section du document, celle de
  ses paramètres et celle du CODE de ses règles (source des fonctions qui décident et simulent). La liste halal
  admise est figée aussi.
- Chaque passage recalcule ces empreintes. Une seule différence : le test est ARRÊTÉ (entrée « ARRET » au
  journal) ; il ne reprend jamais. Le refaire, c'est pré-inscrire un NOUVEAU test, sous un nouvel identifiant,
  compté comme un nouvel essai.
- Chaque démarrage est un essai du registre des expériences (période « FORWARD », hors période de
  développement : ces données n'existaient pas quand les règles ont été écrites).
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings, config_file
from ..research.experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .halal import HalalList, admitted
from .journal import Journal, canonical, utc_iso

DOC_NAME = "FORWARD_TESTS.md"
REQUIRED_FIELDS = ("Hypothèse", "Règles", "Paramètres", "Métrique", "Seuil de décision", "Date d'évaluation")
START, STOP = "DEMARRAGE", "ARRET"
COMMON_SECTION = "Cadre"                 # « ## Cadre commun » : couvert par l'empreinte de chaque test
NOT_STARTED, RUNNING, STOPPED, ENDED = "NON_DEMARRE", "EN_COURS", "ARRETE", "TERMINE"
PERIOD_LABEL = "FORWARD"


class NotPreregistered(RuntimeError):
    """Section absente ou incomplète dans docs/FORWARD_TESTS.md."""


class AlreadyStarted(RuntimeError):
    """Un test ne démarre qu'une fois : le refaire, c'est un nouveau test."""


class DirtyCode(RuntimeError):
    """Code non commité : les règles d'un test doivent être celles d'un commit."""


@dataclass(frozen=True)
class ForwardTest:
    test_id: str
    title: str
    hypothesis: str
    params: dict
    rule_objects: tuple = field(default_factory=tuple)   # fonctions et modules dont le source fixe les règles
    config_keys: tuple[str, ...] = ()                    # valeurs de configuration lues par les règles (« data.x »)
    frozen_modules: tuple[str, ...] = ()                 # modules gelés EN ENTIER (constantes comprises)
    frozen_functions: tuple[tuple[str, str], ...] = ()   # (module, fonction) gelées
    interim_days: int = 42
    final_days: int = 84


def doc_path() -> Path:
    """docs/FORWARD_TESTS.md à côté de config/ (Docker : /app/docs, copié dans l'image)."""
    return config_file().parent.parent / "docs" / DOC_NAME


def _heading_id(line: str) -> str | None:
    words = line[3:].split()
    return words[0] if line.startswith("## ") and words else None


def section(text: str, test_id: str) -> str | None:
    """Texte de la section « ## <test_id> … » jusqu'au titre de niveau 2 suivant (exclu). Deux sections du même
    identifiant : pré-inscription ambiguë, refusée."""
    lines, out, inside = text.splitlines(), [], False
    if sum(_heading_id(line) == test_id for line in lines) > 1:
        raise NotPreregistered(f"{test_id} : plusieurs sections portent cet identifiant")
    for line in lines:
        if line.startswith("## "):
            inside = _heading_id(line) == test_id
        if inside:
            out.append(line.rstrip())
    return "\n".join(out).strip() + "\n" if out else None


def missing_fields(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in REQUIRED_FIELDS if name.lower() not in lowered]


def code_fingerprint(objects) -> str:
    digest = hashlib.sha256()
    for obj in objects:
        digest.update(inspect.getsource(obj).encode("utf-8"))
    return digest.hexdigest()


def config_values(test: ForwardTest, settings: Settings | None) -> dict:
    out = {}
    for key in test.config_keys:
        value = settings
        for part in key.split("."):
            value = getattr(value, part)
        out[key] = value
    return out


def frozen_objects(test: ForwardTest) -> list:
    objects = [importlib.import_module(name) for name in test.frozen_modules]
    objects += [getattr(importlib.import_module(module), name) for module, name in test.frozen_functions]
    return objects + list(test.rule_objects)


def fingerprints(test: ForwardTest, doc_text: str, settings: Settings | None = None) -> dict:
    """Empreintes figées : document (« Cadre commun » + section du test), paramètres (+ valeurs de configuration
    lues par les règles), code (modules entiers, fonctions et objets de règles)."""
    text = section(doc_text, test.test_id)
    if text is None:
        raise NotPreregistered(f"{test.test_id} : aucune section « ## {test.test_id} » dans {DOC_NAME}")
    common = section(doc_text, COMMON_SECTION) or ""
    params = {"params": test.params, "config": config_values(test, settings)}
    return {"doc": hashlib.sha256((common + text).encode("utf-8")).hexdigest(),
            "params": hashlib.sha256(canonical(params).encode("utf-8")).hexdigest(),
            "code": code_fingerprint(frozen_objects(test))}


def journal_for(settings: Settings, test_id: str) -> Journal:
    return Journal(settings.root / "forward" / f"{test_id}.jsonl")


def current_commit() -> str:
    """Commit du code exécuté ; dans l'image Docker (sans dépôt git), celui inscrit à la construction."""
    state = code_state()
    if state == "NO_GIT_COMMIT" and os.environ.get("CSI_CODE_COMMIT"):
        return os.environ["CSI_CODE_COMMIT"]
    return state


def status(settings: Settings, test: ForwardTest, *, now: datetime | None = None) -> dict:
    journal = journal_for(settings, test.test_id)
    start = journal.first(START)
    if start is None:
        return {"state": NOT_STARTED, "start": None}
    stop = journal.first(STOP)
    if stop is not None:
        return {"state": STOPPED, "start": start["data"], "stop": stop["data"]}
    if now is not None and pd.Timestamp(now) >= pd.Timestamp(start["data"]["final_at"]):
        return {"state": ENDED, "start": start["data"]}
    return {"state": RUNNING, "start": start["data"]}


def start(settings: Settings, test: ForwardTest, *, now: datetime, allow_dirty: bool = False,
          halal: HalalList | None = None, doc_text: str | None = None) -> dict:
    """Démarre le test : vérifications, essai enregistré, entrée DEMARRAGE au journal (empreintes figées)."""
    doc_text = doc_path().read_text(encoding="utf-8") if doc_text is None else doc_text
    text = section(doc_text, test.test_id)
    if text is None:
        raise NotPreregistered(f"{test.test_id} : aucune section « ## {test.test_id} » dans {DOC_NAME}")
    missing = missing_fields(text)
    if missing:
        raise NotPreregistered(f"{test.test_id} : champs manquants dans la pré-inscription : {', '.join(missing)}")
    journal = journal_for(settings, test.test_id)
    if journal.first(START) is not None:
        raise AlreadyStarted(f"{test.test_id} a déjà démarré : le refaire, c'est pré-inscrire un nouveau test")
    commit = current_commit()
    if not allow_dirty and (commit.endswith("+DIRTY") or commit == "NO_GIT_COMMIT"):
        raise DirtyCode(f"code non commité ({commit}) : démarrage refusé")
    halal = halal or admitted(settings)
    moment = pd.Timestamp(now)
    run_id = new_run_id("FWD")
    marks = fingerprints(test, doc_text, settings)
    data = {"test_id": test.test_id, "run_id": run_id, "started_at": utc_iso(moment),
            "interim_at": utc_iso(moment + pd.Timedelta(days=test.interim_days)),
            "final_at": utc_iso(moment + pd.Timedelta(days=test.final_days)),
            "fingerprints": marks, "params": test.params, "commit": commit,
            "halal": {"symbols": list(halal.symbols), "sha256": halal.sha256, "file_sha256": halal.file_sha256}}
    ExperimentRegistry(settings.experiments_db).record(
        run_id=run_id, created_at=utc_iso(moment), kind="FORWARD_TEST", hypothesis=test.hypothesis,
        strategy=test.test_id, strategy_version=1, variant="direct", params=test.params, period_label=PERIOD_LABEL,
        period_start=data["started_at"], period_end=data["final_at"], universe=list(halal.symbols),
        data_hashes={"halal": halal.sha256, **marks}, git_commit=commit, dependencies=dependency_versions(),
        seed=int(test.params.get("seed", 0)), cost_scenario="central+defavorable",
        simulation_rules={"doc": f"docs/{DOC_NAME}#{test.test_id}"}, metrics={"n_trials": 1}, status="STARTED",
        report_dir=None)
    journal.append(START, data, now=moment)
    return data


def check_frozen(settings: Settings, test: ForwardTest, *, now: datetime, doc_text: str | None = None) -> str | None:
    """Recalcule les empreintes, y compris après la date d'évaluation (tant que le test tourne ou résout) ; à la
    moindre différence, arrête le test (entrée ARRET) et rend le motif. Document introuvable (image mal construite) :
    alerte rendue, test NON arrêté."""
    state = status(settings, test)
    if state["state"] != RUNNING:
        return None
    if doc_text is None:
        if not doc_path().exists():
            return f"ALERTE : {DOC_NAME} introuvable ({doc_path()}) ; test non arrêté, empreintes non vérifiées"
        doc_text = doc_path().read_text(encoding="utf-8")
    reason: str | None
    try:
        marks = fingerprints(test, doc_text, settings)
    except NotPreregistered as exc:
        marks, reason = {}, str(exc)
    else:
        changed = [k for k, v in state["start"]["fingerprints"].items() if marks.get(k) != v]
        reason = f"modifié après le démarrage : {', '.join(changed)}" if changed else None
    if reason is None:
        return None
    journal_for(settings, test.test_id).append(
        STOP, {"reason": reason, "fingerprints": marks,
               "rule": "un test modifié est arrêté ; le refaire = nouveau test pré-inscrit, nouvel essai"}, now=now)
    return reason
