"""Pipeline du zoo de bout en bout sur le marché SYNTHÉTIQUE (le même code que l'exécution réelle), critères du
contrôle sous H0 (§ 8.3) sur des résumés fabriqués, exécution unique du contrôle."""
from __future__ import annotations

import json

import pytest

from crypto_signal_intelligence.research.zoo import h0
from crypto_signal_intelligence.research.zoo.data import DECOUVERTE, VALIDATION
from crypto_signal_intelligence.research.zoo.measure import MesureA, MesureB
from crypto_signal_intelligence.research.zoo.run import compter, mesurer_tout

IDS = ["TR10_1j", "TR01_4h", "TR37_1j"]


@pytest.fixture(scope="module")
def source():
    return h0.SourceSynthetique(7, "H0a", n_paires=8)


def test_mesures_des_deux_formes_et_des_deux_etapes(source):
    from crypto_signal_intelligence.research.zoo.spec import PAR_ID
    mesures, refs = mesurer_tout([PAR_ID[t] for t in IDS], source, [DECOUVERTE, VALIDATION])
    assert isinstance(mesures[("TR10_1j", "decouverte")], MesureA)
    assert isinstance(mesures[("TR37_1j", "validation")], MesureB)
    a = mesures[("TR01_4h", "decouverte")]
    assert a.n_transactions > 0 and a.e.sum() > 0 and len(a.jours) == 1461
    assert set(a.paires) <= set(refs.grilles)
    b = mesures[("TR37_1j", "decouverte")]
    assert len(b.semaines) >= 200 and 0 < b.exposition < 1


def test_comptage_sans_rendement(source):
    from crypto_signal_intelligence.research.zoo.spec import PAR_ID
    counts = compter([PAR_ID[t] for t in IDS], source, DECOUVERTE)
    assert counts["TR01_4h"]["compteurs"]["transactions"] > 0
    assert counts["TR37_1j"]["echanges"] >= 200
    assert set(counts["TR10_1j"]) >= {"compteurs", "jours_exposition", "blocs_avec_exposition",
                                       "insuffisant_par_construction"}


def test_replique_resume_et_puissance():
    out = h0.replique(1, IDS, ("H0a", "H0b"), n_paires=8)
    entry = out["marches"]["H0a"]["TR01_4h"]
    assert set(entry) >= {"nul", "reel", "transactions", "puissance"}
    assert set(entry["puissance"]) == {str(g) for g in h0.EFFETS_A}
    assert "ancienne_stat_p" in out["marches"]["H0b"]["TR01_4h"]
    assert set(out["marches"]["H0a"]["TR37_1j"]["puissance"]) == {str(g) for g in h0.EFFETS_B}


def _fake(sig: bool, p: float, guards: bool = True, planted_p: float = 1.0) -> dict:
    d = {"p": p, "insuffisant": False, "gardes": guards, "sig05": sig}
    v = {"insuffisant": False, "p": p, "p_sans_annee": p, "annees_ok": True, "concentration_ok": True, "sig05": sig}
    planted = {"d": {"p": planted_p, "insuffisant": False, "gardes": True, "sig05": True}, "v": v}
    return {"nul": {"d": d, "v": v}, "reel": {"d": d | {"p": 1.0}, "v": v}, "ancienne_stat_p": p,
            "puissance": {str(g): planted for g in h0.EFFETS_A}}


def test_criteres_du_controle_h0():
    ids = ["TR01_1h", "TR01_4h", "TR01_1j"]
    resumes = []
    for k in range(40):
        base = {"TR01_1h": _fake(True, 0.01),                  # toujours « significatif » : mesure non valide
                "TR01_4h": _fake(False, 0.5, planted_p=1e-6),   # puissant
                "TR01_1j": _fake(False, 0.5, planted_p=0.5)}    # aveugle
        if k < 2:
            base["TR01_4h"] = _fake(False, 0.001)               # 2 fausses candidates sur 40 : 5 %
        resumes.append({"marches": {m: base for m in ("H0a", "H0b", "H0c")}, "secondes": {}})
    out = h0.criteres(resumes, ids)
    assert out["tests"]["TR01_1h"]["statut"] == "MESURE_NON_VALIDE"
    assert out["tests"]["TR01_4h"]["statut"] == "RETENU"
    assert out["tests"]["TR01_1j"]["statut"] == "INSTRUMENT_AVEUGLE"
    assert out["global"]["H0a"]["part_au_moins_une_candidate"] == pytest.approx(0.05)
    assert out["passe_global"] and out["retenus"] == ["TR01_4h"]
    for k in range(2, 8):
        resumes[k]["marches"]["H0b"] = dict(resumes[k]["marches"]["H0b"]) | {"TR01_4h": _fake(False, 0.001)}
    assert not h0.criteres(resumes, ids)["passe_global"]


def test_controle_execute_une_seule_fois(tmp_path):
    out_dir = tmp_path / "ZOO-H0-MINI"
    report = h0.executer(out_dir, ["TR10_1j"], repliques=1, workers=1, marches=("H0a", "H0b"), n_paires=6,
                         mini=True, commit="abc")
    assert report["mini"] and (out_dir / "criteres.json").exists()
    assert json.loads((out_dir / "criteres.json").read_text(encoding="utf-8"))["tests"]["TR10_1j"]
    with pytest.raises(FileExistsError):
        h0.executer(out_dir, ["TR10_1j"], repliques=1, workers=1, n_paires=6, mini=True, commit="abc")
    other = tmp_path / "AUTRE"
    (other / "repliques").mkdir(parents=True)
    (other / "plan.json").write_text(json.dumps({"autre": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="reprise refusée"):
        h0.executer(other, ["TR10_1j"], repliques=1, workers=1, n_paires=6, mini=True, commit="abc")
