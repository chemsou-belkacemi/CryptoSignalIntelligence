"""Garde d'exécution du zoo (docs/ZOO.md § 7 et § 8.4) : l'exécution réelle (`csi zoo executer --executer`) refuse
tant que :
- le code n'est pas commité (jamais « +DIRTY ») ;
- la relecture `leak-auditor` n'est pas inscrite (`zoo_review.CODE_REVIEW`) ou qu'un chemin relu a changé depuis
  (`git diff` sur `REVIEWED_PATHS`) ;
- la configuration effective diffère de celle de la relecture (`CONFIG_FINGERPRINT`) ;
- le contrôle sous H0 n'est pas inscrit (`CONTROLE_H0`) : fichier présent, empreinte identique, contrôle complet (pas
  un mini-contrôle), au moins 100 répliques sur H0-a et H0-b, sur le code relu ;
- le comptage à blanc de la découverte n'est pas inscrit (`COMPTAGE`) ;
- un test retenu n'est pas codé.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

from ...config import Settings
from ..factors import DirtyCode
from .h0 import CONTROL_KIND, STUDY
from .spec import TESTS

_SRC = "src/crypto_signal_intelligence"
REVIEW_FILE = f"{_SRC}/research/zoo_review.py"
REVIEWED_PATHS: tuple[str, ...] = (
    f"{_SRC}/research/zoo",
    *(f"{_SRC}/research/{m}.py" for m in ("pit_universe", "long_history", "protocol", "experiments", "universe",
                                           "factors", "intervals")),
    f"{_SRC}/forward/costs.py", f"{_SRC}/features/loader.py", f"{_SRC}/data/store.py", f"{_SRC}/context/store.py",
    f"{_SRC}/context/zoo.py", f"{_SRC}/config.py", f"{_SRC}/cli.py", "config/default.toml",
)
INSCRIPTIONS = ("CODE_REVIEW", "CONFIG_FINGERPRINT", "CONTROLE_H0", "COMPTAGE")
CONFIG_SECTIONS = ("data", "protocol")
MIN_REPLIQUES = {"H0a": 100, "H0b": 100, "H0c": 50}


class NotReady(RuntimeError):
    """Relecture, configuration, contrôle H0 ou comptage non inscrits (ou différents) : exécution refusée."""


def racine() -> Path:
    return Path(__file__).resolve().parents[4]


def config_fingerprint(settings: Settings) -> str:
    """Empreinte des valeurs EFFECTIVES (fichier et variables `CSI_*`) des sections lues par le calcul."""
    values = settings.model_dump(mode="json", include=set(CONFIG_SECTIONS))
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def fichier_inerte(path: Path) -> bool:
    """Le fichier d'inscription ne contient que sa docstring, l'import `annotations` et les affectations permises."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" \
                and [a.name for a in node.names] == ["annotations"]:
            continue
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets if isinstance(node, ast.Assign) else [])
        value = getattr(node, "value", None)
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id in INSCRIPTIONS \
                and isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, str)):
            continue
        return False
    return True


def _diff_vide(commit: str, root: Path, versus: str = "HEAD") -> None:
    try:
        out = subprocess.run(["git", "diff", "--quiet", commit, versus, "--", *REVIEWED_PATHS], cwd=root,
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotReady(f"comparaison au commit relu impossible : {exc}") from None
    if out.returncode == 1:
        raise NotReady(f"code modifié depuis le commit {commit[:12]} : nouvelle relecture exigée")
    if out.returncode != 0:
        raise NotReady(f"commit {commit[:12]} introuvable ou illisible : {out.stderr.strip()[:200]}")


def exiger_relecture(state: str, *, root: Path | None = None, review: str | None = None,
                     settings: Settings | None = None, fingerprint: str | None = None) -> str:
    """Code commité et identique, sur les chemins relus, au commit inscrit ; configuration identique. Rend le commit."""
    from .. import zoo_review
    if state.endswith("+DIRTY") or state == "NO_GIT_COMMIT":
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    commit = zoo_review.CODE_REVIEW if review is None else review
    if not commit:
        raise NotReady("relecture leak-auditor du moteur non inscrite (CODE_REVIEW) : exécution refusée")
    root = root or racine()
    _diff_vide(commit, root)
    inscription = root / REVIEW_FILE
    if inscription.exists() and not fichier_inerte(inscription):
        raise NotReady("fichier d'inscription modifié au-delà des valeurs permises : exécution refusée")
    if settings is not None:
        expected = zoo_review.CONFIG_FINGERPRINT if fingerprint is None else fingerprint
        if not expected:
            raise NotReady("empreinte de la configuration relue non inscrite (CONFIG_FINGERPRINT) : exécution refusée")
        if config_fingerprint(settings) != expected:
            raise NotReady("configuration effective différente de celle de la relecture : exécution refusée")
    return commit


def _fichier_inscrit(inscription: str | None, nom: str) -> dict:
    if not inscription or "#" not in inscription:
        raise NotReady(f"{nom} non inscrit : exécution refusée")
    path_text, digest = inscription.rsplit("#", 1)
    path = Path(path_text.strip())
    if not path.exists():
        raise NotReady(f"fichier {nom} introuvable : {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest.strip():
        raise NotReady(f"fichier {nom} modifié depuis son inscription : exécution refusée")
    return json.loads(path.read_text(encoding="utf-8"))


def controle_inscrit(inscription: str | None = None, *, root: Path | None = None) -> dict:
    """Critères du contrôle sous H0 inscrits : complet, assez de répliques, tous les tests, code relu, issue globale."""
    from .. import zoo_review
    report = _fichier_inscrit(zoo_review.CONTROLE_H0 if inscription is None else inscription, "CONTROLE_H0")
    if report.get("study") != STUDY or report.get("control") != CONTROL_KIND:
        raise NotReady("fichier du contrôle sous H0 d'une autre étude")
    if report.get("mini"):
        raise NotReady("mini-contrôle : jamais inscriptible (vérification de fonctionnement seulement)")
    if set(report.get("tests", [])) != {t.id for t in TESTS}:
        raise NotReady(f"contrôle sous H0 incomplet : les {len(TESTS)} tests doivent y passer")
    if report.get("repliques", 0) < max(MIN_REPLIQUES["H0a"], MIN_REPLIQUES["H0b"]):
        raise NotReady("contrôle sous H0 sous 100 répliques : refusé (§ 8.4)")
    per_market = report.get("repliques_par_marche") or {}
    for marche, minimum in MIN_REPLIQUES.items():
        if marche not in report.get("marches", []) or int(per_market.get(marche, 0)) < minimum:
            raise NotReady(f"contrôle sous H0 sans {marche} ou sous {minimum} répliques sur ce marché (§ 8.4)")
    commit = str(report.get("commit", ""))
    if not commit or commit.endswith("+DIRTY"):
        raise NotReady("contrôle sous H0 lancé sur du code non commité")
    _diff_vide(commit, root or racine())
    if not report.get("passe_global"):
        raise NotReady("contrôle sous H0 en échec global (INSTRUMENT_NON_VALIDE) : le zoo n'est pas exécuté, 0 essai")
    return report


def comptage_inscrit(inscription: str | None = None, *, root: Path | None = None) -> dict:
    """Comptage à blanc inscrit : découverte du zoo, COMPLET (tous les tests du registre), sur du code commité et
    identique, sur les chemins relus, au code courant."""
    from .. import zoo_review
    report = _fichier_inscrit(zoo_review.COMPTAGE if inscription is None else inscription, "COMPTAGE")
    if report.get("etape") != "decouverte" or report.get("study") != STUDY:
        raise NotReady("comptage inscrit d'une autre étape ou d'une autre étude")
    commit = str(report.get("commit", ""))
    if not commit or commit.endswith("+DIRTY") or commit == "NO_GIT_COMMIT":
        raise NotReady("comptage inscrit fait sur du code non commité : refusé")
    _diff_vide(commit, root or racine())
    if report.get("partiel", True) or report.get("tests_comptes") != len(TESTS) or \
            set(report.get("tests", {})) != {t.id for t in TESTS}:
        raise NotReady(f"comptage inscrit partiel : les {len(TESTS)} tests du registre doivent y être comptés")
    return report


#: Champs d'un test qui doivent être identiques entre le comptage inscrit et le recomptage de l'exécution.
CHAMPS_COMPTAGE = ("compteurs", "blocs_avec_exposition", "insuffisant_par_construction", "echanges",
                   "echanges_investis", "blocs")


def comptages_differents(inscrit: dict, recompte: dict) -> list[str]:
    """Tests dont le recomptage diffère du comptage inscrit (compteurs, blocs, retraits ; tous les tests)."""
    out = []
    for test_id in sorted(set(inscrit) | set(recompte)):
        a, b = inscrit.get(test_id), recompte.get(test_id)
        if a is None or b is None or any(a.get(k) != b.get(k) for k in CHAMPS_COMPTAGE):
            out.append(test_id)
    return out


def exiger_codes(test_ids: list[str]) -> None:
    from .spec import PAR_ID
    missing = sorted(t for t in test_ids if PAR_ID[t].etat_code != "code")
    if missing:
        raise NotReady(f"{len(missing)} tests retenus non codés (ex. {', '.join(missing[:5])}) : exécution refusée")
