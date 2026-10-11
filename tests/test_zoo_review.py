"""Garde d'exécution du zoo (docs/ZOO.md § 7, § 8.4) et registre des 239 tests (§ 12) : l'exécution réelle refuse sans
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

def test_registre_239_tests():
    counts = S.verifier_registre()
    assert sum(counts.values()) == 239 and counts["TR"] == 83 and counts["CH"] == 29 and counts["AN"] == 5
    assert sum(t.forme == "a" for t in S.TESTS) == 207 and sum(t.forme == "b" for t in S.TESTS) == 32
    removed = {"CH02", "CH11", "CH12", "CH15", "CH16", "AN05"}
    assert not removed & {t.strategie for t in S.TESTS}
    assert "chaque mois" in S.PAR_ID["PF05_1j"].entree


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
        assert callable(fn)
        if test.etat_code == S.CODE:
            continue
        with pytest.raises(NotImplementedError, match="à coder"):
            fn(bars, Contexte("X", 1, test.params, 0))


def _un_test_non_code(monkeypatch) -> str:
    """Un test non codé : le premier qui l'est déjà, sinon un test codé rendu « à coder » le temps du test."""
    pending = [t.id for t in S.TESTS if t.etat_code != S.CODE]
    if pending:
        return pending[0]
    from crypto_signal_intelligence.research.zoo.strategies import trend
    monkeypatch.setattr(trend, "IMPLEMENTEES", trend.IMPLEMENTEES - {"au_dessus_sma"})
    return "TR10_1j"


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
            "commit": "HEAD", "passe_global": True, "retenus": [], "retires": {}, "marches": ["H0a", "H0b", "H0c"],
            "repliques_par_marche": {"H0a": 200, "H0b": 200, "H0c": 200}}
    assert R.controle_inscrit(_inscrire(tmp_path / "ok.json", full), root=PROJECT)["repliques"] == 200
    with pytest.raises(R.NotReady, match="H0c"):
        R.controle_inscrit(_inscrire(tmp_path / "noc.json", full | {"marches": ["H0a", "H0b"]}), root=PROJECT)
    with pytest.raises(R.NotReady, match="H0c"):
        R.controle_inscrit(_inscrire(tmp_path / "fewc.json", full | {"repliques_par_marche": {
            "H0a": 200, "H0b": 200, "H0c": 40}}), root=PROJECT)
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


def test_comptage_inscrit_et_tests_codes(tmp_path, monkeypatch):
    ok = {"study": "ZOO", "etape": "decouverte", "commit": "HEAD", "partiel": False, "tests_comptes": len(S.TESTS),
          "tests": {t.id: {} for t in S.TESTS}}
    assert R.comptage_inscrit(_inscrire(tmp_path / "c.json", ok), root=PROJECT)["etape"] == "decouverte"
    with pytest.raises(R.NotReady, match="autre étape"):
        R.comptage_inscrit(_inscrire(tmp_path / "v.json", ok | {"etape": "validation"}), root=PROJECT)
    with pytest.raises(R.NotReady, match="non commité"):
        R.comptage_inscrit(_inscrire(tmp_path / "d.json", ok | {"commit": "abc+DIRTY"}), root=PROJECT)
    with pytest.raises(R.NotReady, match="introuvable"):
        R.comptage_inscrit(_inscrire(tmp_path / "x.json", ok | {"commit": "0" * 40}), root=PROJECT)
    with pytest.raises(R.NotReady, match="partiel"):
        R.comptage_inscrit(_inscrire(tmp_path / "p.json", ok | {"partiel": True}), root=PROJECT)
    with pytest.raises(R.NotReady, match="partiel"):
        R.comptage_inscrit(_inscrire(tmp_path / "n.json", ok | {"tests_comptes": 83}), root=PROJECT)
    a = {"TR01_1h": {"compteurs": {"transactions": 5}, "blocs_avec_exposition": 30},
         "TR37_1j": {"echanges": 208, "blocs": 53, "echanges_investis": 190}}
    assert R.comptages_differents(a, a) == []
    assert R.comptages_differents(a, a | {"TR37_1j": a["TR37_1j"] | {"blocs": 52}}) == ["TR37_1j"]
    assert R.comptages_differents(a, {"TR01_1h": a["TR01_1h"]}) == ["TR37_1j"]
    R.exiger_codes(["TR01_1h", "TR37_1j"])
    with pytest.raises(R.NotReady, match="non codés"):
        R.exiger_codes(["TR01_1h", _un_test_non_code(monkeypatch)])


def test_cli_executer_refuse_sans_option_et_sans_inscription(settings, monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["zoo", "executer", "--etape", "decouverte"])
    assert result.exit_code == 2
    result = runner.invoke(app, ["zoo", "executer", "--etape", "decouverte", "--executer"])
    assert result.exit_code == 3 and "Aucun résultat" in result.output
    result = runner.invoke(app, ["zoo", "controle-h0", "--repliques", "200"])
    assert result.exit_code == 3 and "Aucun calcul" in result.output
