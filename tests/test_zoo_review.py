"""Garde d'exécution du zoo (docs/ZOO.md § 7, § 8.4) et registre des 257 tests (§ 12) : l'exécution réelle refuse sans
relecture, configuration, contrôle H0 et comptage inscrits ; mini-contrôle jamais inscriptible."""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from crypto_signal_intelligence.cli import app
from crypto_signal_intelligence.research.factors import DirtyCode
from crypto_signal_intelligence.research.zoo import review as R
from crypto_signal_intelligence.research.zoo import spec as S
from crypto_signal_intelligence.research.zoo.data import Bougies
from crypto_signal_intelligence.research.zoo.spec import Contexte

from .conftest import PROJECT

# --- Registre ---------------------------------------------------------------------------------------------------

def test_registre_257_tests():
    counts = S.verifier_registre()
    assert sum(counts.values()) == 257 and counts["TR"] == 83
    assert sum(t.forme == "a" for t in S.TESTS) == 225 and sum(t.forme == "b" for t in S.TESTS) == 32


def test_parametres_du_registre():
    p = S.PAR_ID
    assert dict(p["TR01_1h"].params) == {"moyenne": "SMA", "rapide": 5, "lente": 20}
    assert p["MR19_1j"].execution == "1h" and p["MR19_1j"].n_max == 24
    assert p["VO11_1h"].reference == S.MEME_HEURE and p["VO11_1h"].n_max == 1
    assert p["EV02_1j"].reference == S.R180 and p["EV02_1j"].univers == "recensement"
    assert p["AM01_1j"].univers == "btc" and not p["AM01_1j"].meilleure_paire
    assert p["XS01_1j"].meilleure_paire and p["XS24_1j"].execution == "1h"
    assert p["RO02_1j"].reference == S.MEME_COMPOSITION and p["TR37_1j"].reference == S.EXPOSITION_EGALE
    assert p["PF05_1j"].reference == S.PANIER_FLOTTANT and p["TR33_1j"].n_max == 10
    assert {t.unite for t in S.TESTS if t.strategie == "TR13"} == {"4h", "1j"}
    assert p["ST15_4h"].n_max == 42 and p["SE01_1j"].n_max == 30


def test_fonctions_codees_ou_non_implementees_explicites():
    import numpy as np
    bars = Bougies(1, *(np.zeros(3) for _ in range(9)))
    for test in S.TESTS:
        fn = test.strategie_fonction()
        if test.famille == "TR":
            assert test.etat_code == S.CODE
            continue
        assert test.etat_code == S.A_CODER
        with pytest.raises(NotImplementedError, match="à coder"):
            fn(bars, Contexte("X", 1, test.params, 0))


# --- Garde ------------------------------------------------------------------------------------------------------

def test_fichier_d_inscription_inerte(tmp_path):
    good = tmp_path / "ok.py"
    good.write_text('"""doc"""\nfrom __future__ import annotations\n\nCODE_REVIEW: str | None = "abc"\n'
                    'CONTROLE_H0: str | None = None\n', encoding="utf-8")
    assert R.fichier_inerte(good)
    bad = tmp_path / "bad.py"
    bad.write_text('"""doc"""\nimport os\nCODE_REVIEW = os.getcwd()\n', encoding="utf-8")
    assert not R.fichier_inerte(bad)
    assert R.fichier_inerte(PROJECT / R.REVIEW_FILE)


def test_relecture_exigee(settings):
    with pytest.raises(DirtyCode):
        R.exiger_relecture("abc+DIRTY")
    with pytest.raises(R.NotReady, match="CODE_REVIEW"):
        R.exiger_relecture("abc", review="")
    with pytest.raises(R.NotReady, match="introuvable"):
        R.exiger_relecture("abc", review="0" * 40, root=PROJECT)
    assert R.exiger_relecture("abc", review="HEAD", root=PROJECT) == "HEAD"
    with pytest.raises(R.NotReady, match="empreinte"):
        R.exiger_relecture("abc", review="HEAD", root=PROJECT, settings=settings, fingerprint="")
    with pytest.raises(R.NotReady, match="configuration"):
        R.exiger_relecture("abc", review="HEAD", root=PROJECT, settings=settings, fingerprint="x" * 64)
    fp = R.config_fingerprint(settings)
    assert R.exiger_relecture("abc", review="HEAD", root=PROJECT, settings=settings, fingerprint=fp) == "HEAD"


def _inscrire(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return f"{path}#{hashlib.sha256(path.read_bytes()).hexdigest()}"


def test_controle_h0_inscrit(tmp_path):
    full = {"study": "ZOO", "control": "H0_ZOO_V1", "mini": False, "tests": [t.id for t in S.TESTS], "repliques": 200,
            "commit": "HEAD", "passe_global": True, "retenus": [], "retires": {}}
    assert R.controle_inscrit(_inscrire(tmp_path / "ok.json", full), root=PROJECT)["repliques"] == 200
    with pytest.raises(R.NotReady, match="mini"):
        R.controle_inscrit(_inscrire(tmp_path / "mini.json", full | {"mini": True}), root=PROJECT)
    with pytest.raises(R.NotReady, match="incomplet"):
        R.controle_inscrit(_inscrire(tmp_path / "part.json", full | {"tests": ["TR01_1h"]}), root=PROJECT)
    with pytest.raises(R.NotReady, match="100"):
        R.controle_inscrit(_inscrire(tmp_path / "few.json", full | {"repliques": 50}), root=PROJECT)
    with pytest.raises(R.NotReady, match="INSTRUMENT_NON_VALIDE"):
        R.controle_inscrit(_inscrire(tmp_path / "fail.json", full | {"passe_global": False}), root=PROJECT)
    inscription = _inscrire(tmp_path / "tamper.json", full)
    (tmp_path / "tamper.json").write_text(json.dumps(full | {"repliques": 201}), encoding="utf-8")
    with pytest.raises(R.NotReady, match="modifié"):
        R.controle_inscrit(inscription, root=PROJECT)
    with pytest.raises(R.NotReady, match="non inscrit"):
        R.controle_inscrit("", root=PROJECT)


def test_comptage_inscrit_et_tests_codes(tmp_path):
    ok = {"study": "ZOO", "etape": "decouverte"}
    assert R.comptage_inscrit(_inscrire(tmp_path / "c.json", ok))["etape"] == "decouverte"
    with pytest.raises(R.NotReady):
        R.comptage_inscrit(_inscrire(tmp_path / "v.json", ok | {"etape": "validation"}))
    R.exiger_codes(["TR01_1h", "TR37_1j"])
    with pytest.raises(R.NotReady, match="non codés"):
        R.exiger_codes(["TR01_1h", "MR01_1j"])


def test_cli_executer_refuse_sans_option_et_sans_inscription(settings, monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["zoo", "executer", "--etape", "decouverte"])
    assert result.exit_code == 2
    result = runner.invoke(app, ["zoo", "executer", "--etape", "decouverte", "--executer"])
    assert result.exit_code == 3 and "Aucun résultat" in result.output
    result = runner.invoke(app, ["zoo", "controle-h0", "--repliques", "200"])
    assert result.exit_code == 3 and "Aucun calcul" in result.output
