"""Confirmation de TR37_1j et XS17_1j (docs/ZOO_CONFIRMATION.md) : garde (relecture, sélection déclarée, exécution
unique), les deux tests exactement, mesure et validation du zoo (m = 2). Aucune donnée réelle lue."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from crypto_signal_intelligence.cli import app
from crypto_signal_intelligence.research.factors import DirtyCode
from crypto_signal_intelligence.research.zoo import confirmation as C
from crypto_signal_intelligence.research.zoo.reel import STRATEGIES, AlreadyRun
from crypto_signal_intelligence.research.zoo.review import NotReady
from crypto_signal_intelligence.research.zoo.spec import EXPOSITION_EGALE, PAR_ID


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
    def __init__(self, deja: int, report_dir):
        self.deja, self.report_dir = deja, report_dir

    def count_runs(self, strategy=None):
        return self.deja if strategy == C.STRATEGIE else 1

    def recent(self, _limit):
        return [{"run_id": "ZOO-DECOUVERTE-x", "strategy": STRATEGIES["decouverte"]}]

    def get(self, run_id):
        return {"run_id": run_id, "report_dir": str(self.report_dir)}

    def program_trials(self):
        return 1039


def _preparer(monkeypatch, tmp_path, deja: int = 0, summary: dict | None = None):
    (tmp_path / "summary.json").write_text(json.dumps(summary or _summary()), encoding="utf-8")
    monkeypatch.setattr(C, "code_state", lambda: "abc")
    monkeypatch.setattr(C, "exiger_relecture", lambda *a, **k: "abc")
    monkeypatch.setattr(C, "ExperimentRegistry", lambda _db: _Registre(deja, tmp_path))


def test_refus_sans_relecture_inscrite(settings, monkeypatch):
    from crypto_signal_intelligence.research import zoo_review
    monkeypatch.setattr(C, "code_state", lambda: "abc+DIRTY")
    with pytest.raises(DirtyCode):
        C.executer_confirmation(settings, now=None)
    monkeypatch.setattr(C, "code_state", lambda: "abc")
    monkeypatch.setattr(zoo_review, "CONFIRMATION_REVIEW", None)
    with pytest.raises(NotReady, match="relecture"):
        C.executer_confirmation(settings, now=None)


def test_execution_unique(settings, monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path, deja=1)
    with pytest.raises(AlreadyRun):
        C.executer_confirmation(settings, now=None)


def test_refus_si_la_selection_n_est_pas_celle_declaree(settings, monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path, summary=_summary(TR37_1j={"statut": "NON_CANDIDATE",
                                                               "stats": {"central": {"t": 0.4}}}))
    with pytest.raises(NotReady, match="TR37_1j"):
        C.executer_confirmation(settings, now=None)


def test_mesure_les_deux_tests_sur_la_validation_seulement(settings, monkeypatch, tmp_path):
    _preparer(monkeypatch, tmp_path)
    seen = {}

    class Arret(Exception):
        pass

    def mesurer(tests, source, etapes, **_k):
        seen["tests"] = [t.id for t in tests]
        seen["etapes"] = [e.nom for e in etapes]
        seen["source_etape"] = source.etape.nom if hasattr(source, "etape") else None
        raise Arret

    monkeypatch.setattr(C, "SourceMagasin", lambda settings, etape, tests: type("S", (), {"etape": etape})())
    monkeypatch.setattr(C, "mesurer_tout_detail", mesurer)
    with pytest.raises(Arret):
        C.executer_confirmation(settings, now=None)
    assert seen == {"tests": ["TR37_1j", "XS17_1j"], "etapes": ["validation"], "source_etape": "validation"}


def test_cli_exige_executer():
    result = CliRunner().invoke(app, ["zoo", "confirmer"])
    assert result.exit_code == 2
