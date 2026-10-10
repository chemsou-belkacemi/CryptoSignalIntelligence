"""Décisions du zoo (docs/ZOO.md § 4.3 et § 5) : Benjamini-Hochberg et Benjamini-Yekutieli à la main, validation à
1 − 0,05/m, années positives, concentration."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.zoo import decide as D
from crypto_signal_intelligence.research.zoo import measure as M

from .zoo_outils import etape


def test_benjamini_hochberg_a_la_main():
    p = {"a": 0.001, "b": 0.008, "c": 0.039, "d": 0.041, "e": 0.042, "f": 0.06, "g": 0.074, "h": 0.205, "i": 0.212,
         "j": 0.216}
    # M = 10, q = 0,10 : seuils 0,01 0,02 0,03 0,04 0,05 0,06 0,07 0,08 0,09 0,10 → k = 6 (0,06 ≤ 0,06), 7 : 0,074 > 0,07
    assert D.benjamini_hochberg(p, 0.10) == {"a", "b", "c", "d", "e", "f"}
    # q = 0,05 : seuils 0,005 0,01 0,015 0,02 … → k = 2
    assert D.benjamini_hochberg(p, 0.05) == {"a", "b"}
    assert D.benjamini_hochberg({}, 0.1) == set()
    assert D.benjamini_hochberg({"x": 0.5}, 0.1) == set()


def test_benjamini_hochberg_rang_le_plus_grand_meme_si_un_precedent_echoue():
    p = {"a": 0.02, "b": 0.021, "c": 0.022}
    # seuils 0,0333 0,0667 0,1 : tous passent (et 0,02 > 0,1/3 ? non : 0,02 ≤ 0,0333)
    assert D.benjamini_hochberg(p, 0.10) == {"a", "b", "c"}
    p = {"a": 0.04, "b": 0.05, "c": 0.06}
    # p(1) = 0,04 > 0,0333 mais p(3) = 0,06 ≤ 0,1 : k = 3, les trois sont rejetés (procédure par le haut)
    assert D.benjamini_hochberg(p, 0.10) == {"a", "b", "c"}


def test_benjamini_yekutieli_plus_severe():
    p = {"a": 0.001, "b": 0.008, "c": 0.039, "d": 0.041, "e": 0.042, "f": 0.06, "g": 0.074, "h": 0.205, "i": 0.212,
         "j": 0.216}
    c_m = sum(1 / i for i in range(1, 11))          # 2,929
    expected = D.benjamini_hochberg(p, 0.10 / c_m)
    assert D.benjamini_yekutieli(p, 0.10) == expected == {"a"}      # seuils 0,0034 × i : seul p(1) passe


def _mesure(values_by_year: dict[int, float], pairs: dict[str, float], noise: float = 0.0, seed: int = 0):
    stage = etape("2023-01-01", "2025-07-01", "validation")
    stage = stage.__class__(stage.nom, stage.debut, stage.fin, ("2023", "2024", "2025"))
    days = stage.jours()
    years = pd.to_datetime(days, unit="D").year
    rng = np.random.default_rng(seed)
    g = np.zeros(len(days))
    for y, v in values_by_year.items():
        g[years == y] = v
    g = g + rng.normal(0, noise, len(days))
    zero = np.zeros(len(days))
    paires = {s: {"g": v, "e": 100.0, "ref": 0.0, "c_central": 0.0, "c_defavorable": 0.0} for s, v in pairs.items()}
    return M.MesureA("T", stage, days, g, np.ones(len(days)), zero.copy(), zero.copy(),
                     {"central": zero.copy(), "defavorable": zero.copy()}, paires, {"A": np.zeros((500, 0))}, {}, 0.0)


def test_validation_niveau_depend_de_m():
    mesure = _mesure({2023: 0.004, 2024: 0.004, 2025: 0.004}, {"A": 3.0, "B": 2.0}, noise=0.03, seed=1)
    parts = D.composantes_validation(mesure)
    assert parts["annees"]["ok"] and parts["concentration"]["ok"]
    p = parts["p"]
    assert 0 < p < 1
    m_ok = max(1, int(0.05 / p) - 1) if p < 0.05 else None
    if m_ok:
        assert D.statut_validation(parts | {"sans_meilleure_annee": {"p": 0.0}}, m_ok) == D.VALIDEE
    big_m = int(0.05 / p) + 5
    assert D.statut_validation(parts, big_m) == D.NON_VALIDEE


def test_validation_deux_annees_sur_trois_et_concentration():
    one_year = _mesure({2023: 0.02, 2024: -0.001, 2025: -0.001}, {"A": 3.0, "B": 2.0})
    parts = D.composantes_validation(one_year)
    assert parts["annees"]["positives"] == 1 and not parts["annees"]["ok"]
    concentrated = _mesure({2023: 0.01, 2024: 0.01, 2025: 0.01}, {"A": 7.0, "B": 3.0})
    parts = D.composantes_validation(concentrated)
    assert parts["concentration"]["part_max"] == pytest.approx(0.7) and not parts["concentration"]["ok"]
    assert D.statut_validation(parts, 1) == D.NON_VALIDEE


def test_decouverte_garde_fous_et_insuffisant():
    good = _mesure({2023: 0.01, 2024: 0.01, 2025: 0.01}, {"A": 3.0, "B": 2.0}, noise=0.001)
    short = _mesure({2023: 0.01}, {"A": 1.0})
    short.transactions = {"A": np.zeros((5, 0))}
    decisions, summary = D.decouverte({"good": good, "short": short})
    assert decisions["short"].statut == D.INSUFFISANT
    assert decisions["good"].statut == D.CANDIDATE and summary["candidates"] == ["good"]
    with pytest.raises(ValueError):
        D.statut_validation({"insuffisant": False}, 0)
