"""Confirmation de TR37_1j et XS17_1j (docs/ZOO_CONFIRMATION.md) : garde (relecture, sélection déclarée, exécution
unique), les deux tests exactement, mesure et validation du zoo (m = 2). Aucune donnée réelle lue."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from crypto_signal_intelligence.cli import app
from crypto_signal_intelligence.research.factors import DirtyCode
from crypto_signal_intelligence.research.zoo import confirmation as C
from crypto_signal_intelligence.research.zoo.reel import AlreadyRun
from crypto_signal_intelligence.research.zoo.review import _SRC, NotReady
from crypto_signal_intelligence.research.zoo.spec import EXPOSITION_EGALE, PAR_ID

from .conftest import PROJECT


def _summary(**changes) -> dict:
    tests = {"TR37_1j": {"statut": "NON_CANDIDATE", "stats": {"central": {"t": 2.25}}},
             "XS17_1j": {"statut": "NON_CANDIDATE", "stats": {"central": {"t": 2.06}}}}
    tests.update(changes)
    return {"tests": tests}


def test_les_deux_tests_tels_que_codes_par_le_zoo():
    assert C.TESTS_CONFIRMATION == ("TR37_1j", "XS17_1j")
    assert dict(PAR_ID["TR37_1j"].params) == {"jours": 7} and PAR_ID["TR37_1j"].reference == EXPOSITION_EGALE
    assert dict(PAR_ID["XS17_1j"].params) == {"variable": "proximite_ath", "cote": "haut", "cotation_min_jours": 365}
    assert PAR_ID["XS17_1j"].meilleure_paire and not PAR_ID["TR37_1j"].meilleure_paire


def test_selection_declaree():
    C.selection_declaree(_summary())
    with pytest.raises(NotReady, match="absent"):
        C.selection_declaree({"tests": {"TR37_1j": _summary()["tests"]["TR37_1j"]}})
    with pytest.raises(NotReady, match="sélection"):
        C.selection_declaree(_summary(XS17_1j={"statut": "NON_CANDIDATE", "stats": {"central": {"t": 1.9}}}))
    with pytest.raises(NotReady, match="sélection"):
        C.selection_declaree(_summary(TR37_1j={"statut": "CANDIDATE", "stats": {"central": {"t": 3.0}}}))


class _Registre:
    def __init__(self, deja: int = 0):
        self.deja, self.records = deja, []

    def count_runs(self, strategy=None):
        return self.deja if strategy == C.STRATEGIE else 1

    def get(self, run_id):
        return {"run_id": run_id} if run_id == "ZOO-DECOUVERTE-20261011T055846Z-bcf9e6" else None

    def program_trials(self):
        return 1039

    def record(self, **run):
        self.records.append(run)


def _preparer(monkeypatch, tmp_path, deja: int = 0, summary: dict | None = None) -> dict:
    """Garde simulée : relecture capturée, découverte épinglée sur un résumé écrit ici, registre factice."""
    from crypto_signal_intelligence.research import zoo_review
    text = json.dumps(summary or _summary())
    path = tmp_path / "decouverte.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(C, "DECOUVERTE_ZOO", f"decouverte.json#{hashlib.sha256(text.encode()).hexdigest()}")
    monkeypatch.setattr(zoo_review, "CONFIRMATION_REVIEW", "c" * 40)
    seen: dict = {"registre": _Registre(deja)}

    def relecture(state, **kwargs):
        seen["relecture"] = (state, kwargs)
        return state
    monkeypatch.setattr(C, "code_state", lambda: "abc")
    monkeypatch.setattr(C, "exiger_relecture", relecture)
    monkeypatch.setattr(C, "code_de_mesure_identique", lambda commit, root: seen.setdefault("zoo", commit))
    monkeypatch.setattr(C, "ExperimentRegistry", lambda _db: seen["registre"])
    return seen


def test_refus_sans_relecture_inscrite(settings, monkeypatch):
    from crypto_signal_intelligence.research import zoo_review
    monkeypatch.setattr(zoo_review, "CONFIRMATION_REVIEW", None)
    with pytest.raises(NotReady, match="CONFIRMATION_REVIEW"):
        C.executer_confirmation(settings, now=None)
    monkeypatch.setattr(zoo_review, "CONFIRMATION_REVIEW", "c" * 40)
    monkeypatch.setattr(C, "code_state", lambda: "abc+DIRTY")
    with pytest.raises(DirtyCode):
        C.executer_confirmation(settings, now=None)


def test_relecture_de_la_confirmation_et_configuration_exigees(settings, monkeypatch, tmp_path):
    from crypto_signal_intelligence.research import zoo_review
    seen = _preparer(monkeypatch, tmp_path, deja=1)
    with pytest.raises(AlreadyRun):
        C.executer_confirmation(settings, now=None, root=tmp_path)
    state, kwargs = seen["relecture"]
    assert kwargs["review"] == zoo_review.CONFIRMATION_REVIEW and kwargs["settings"] is settings
    assert seen["zoo"] == zoo_review.CODE_REVIEW


def test_execution_unique_registre_ou_dossier(settings, monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path, deja=1)
    with pytest.raises(AlreadyRun, match="exécutée"):
        C.executer_confirmation(settings, now=None, root=tmp_path)
    _preparer(monkeypatch, tmp_path, deja=0)
    (settings.reports_dir / "ZOO-CONFIRMATION-20261011T000000Z-x").mkdir(parents=True)
    with pytest.raises(AlreadyRun, match="commencée"):
        C.executer_confirmation(settings, now=None, root=tmp_path)


def test_refus_si_la_selection_n_est_pas_celle_declaree(settings, monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path, summary=_summary(TR37_1j={"statut": "NON_CANDIDATE",
                                                               "stats": {"central": {"t": 0.4}}}))
    with pytest.raises(NotReady, match="TR37_1j"):
        C.executer_confirmation(settings, now=None, root=tmp_path)


def test_decouverte_epinglee_par_empreinte(monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path)
    assert C.decouverte_epinglee(_Registre(), tmp_path)["tests"]["TR37_1j"]["statut"] == "NON_CANDIDATE"
    (tmp_path / "decouverte.json").write_text(json.dumps(_summary(XS17_1j={})), encoding="utf-8")
    with pytest.raises(NotReady, match="modifié"):
        C.decouverte_epinglee(_Registre(), tmp_path)
    monkeypatch.setattr(C, "DECOUVERTE_ID", "autre")
    with pytest.raises(NotReady, match="registre"):
        C.decouverte_epinglee(_Registre(), tmp_path)


def _depot(tmp_path):
    root = tmp_path / "depot"
    files = {f"{_SRC}/research/zoo/measure.py": "X = 1\n",
             f"{_SRC}/research/zoo/review.py": "INSCRIPTIONS = ('A',)\nY = 2\n",
             f"{_SRC}/cli.py": "def zoo_execute():\n    return 1\n"}
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "zoo")
    return root, git, git("rev-parse", "HEAD")


def test_code_de_mesure_identique_au_zoo(tmp_path):
    root, git, zoo = _depot(tmp_path)
    (root / f"{_SRC}/research/zoo/review.py").write_text("INSCRIPTIONS = ('A', 'B')\nY = 2\n", encoding="utf-8")
    (root / f"{_SRC}/cli.py").write_text("def zoo_execute():\n    return 1\n\n\ndef zoo_confirm():\n    return 2\n",
                                         encoding="utf-8")
    (root / f"{_SRC}/research/zoo/confirmation.py").write_text("Z = 3\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "confirmation")
    C.code_de_mesure_identique(zoo, root)                     # parties ajoutées seulement : accepté
    (root / f"{_SRC}/research/zoo/review.py").write_text("INSCRIPTIONS = ('A', 'B')\nY = 3\n", encoding="utf-8")
    git("commit", "-qam", "garde changée")
    with pytest.raises(NotReady, match="review.py"):
        C.code_de_mesure_identique(zoo, root)
    git("revert", "--no-edit", "HEAD")
    (root / f"{_SRC}/research/zoo/measure.py").write_text("X = 2\n", encoding="utf-8")
    git("commit", "-qam", "mesure changée")
    with pytest.raises(NotReady, match="code de mesure modifié"):
        C.code_de_mesure_identique(zoo, root)
    with pytest.raises(NotReady, match="non inscrite"):
        C.code_de_mesure_identique("", root)


def test_code_de_mesure_du_depot_identique_au_zoo():
    from crypto_signal_intelligence.research import zoo_review
    C.code_de_mesure_identique(zoo_review.CODE_REVIEW, PROJECT)


@pytest.fixture(scope="module")
def synthetique():
    from crypto_signal_intelligence.research.zoo import h0
    return h0.SourceSynthetique(7, "H0a", n_paires=20)


def test_bout_en_bout_sur_marche_synthetique(settings, monkeypatch, tmp_path, synthetique):
    """Vraie mesure et vraie validation sur un marché synthétique ; registre capturé (2 essais, DEVELOPMENT)."""
    seen = _preparer(monkeypatch, tmp_path)
    monkeypatch.setattr(C, "SourceMagasin", lambda settings, etape, tests: synthetique)
    monkeypatch.setattr(C, "empreintes_donnees", lambda settings, source: {})
    result = C.executer_confirmation(settings, now=datetime(2026, 10, 11, tzinfo=UTC), root=tmp_path)
    assert result["niveau"] == pytest.approx(0.975) and result["n_trials"] == 2
    assert set(result["tests"]) == {"TR37_1j", "XS17_1j"}
    for entry in result["tests"].values():
        assert entry["statut"] in {"VALIDÉE", "NON_VALIDÉE", "INSUFFISANT"}
        for scenario in ("central", "defavorable"):
            decision = entry["intervalle_decision"][scenario]
            assert decision is None or decision["niveau"] == pytest.approx(0.975)
    share = result["tests"]["XS17_1j"]["descriptif"]["part_btc_eth"]
    assert share is None or 0 <= share <= 1
    assert result["tests"]["TR37_1j"]["descriptif"]["part_btc_eth"] is None
    (record,) = seen["registre"].records
    assert record["metrics"] == {"n_trials": 2, "program_trials": 1041, "validees": result["validees"]}
    assert record["period_label"] == "DEVELOPMENT" and record["strategy"] == C.STRATEGIE
    assert record["period_start"].startswith("2023-01-01") and record["period_end"].startswith("2025-06-30")
    run_dir = settings.reports_dir / result["run_id"]
    assert (run_dir / "debut.json").exists() and (run_dir / "summary.json").exists()


def test_cli_exige_executer():
    result = CliRunner().invoke(app, ["zoo", "confirmer"])
    assert result.exit_code == 2
