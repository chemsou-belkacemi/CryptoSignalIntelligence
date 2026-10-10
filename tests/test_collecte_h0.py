"""Contrôle sous l'hypothèse nulle des tests en direct F25 à F30 (research/collecte_h0.py). Les tests rapides vérifient
le générateur (déterministe, martingale, grandeurs liées au rendement CONTEMPORAIN) et une réplique de bout en bout ; le
contrôle complet (200 répliques × 6 tests) est marqué `slow` et lancé UNE fois avant le démarrage, chiffres inscrits
dans docs/FORWARD_TESTS.md (sections F25 … F30)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from crypto_signal_intelligence.forward import collecte_events as C
from crypto_signal_intelligence.research import collecte_h0 as H


def test_market_is_deterministic_and_martingale_shaped():
    a = H.market(np.random.default_rng(5), 4, 20)
    b = H.market(np.random.default_rng(5), 4, 20)
    assert all(np.array_equal(x, y) for x, y in zip(a[1:], b[1:], strict=True)) and a[0].equals(b[0])
    times, r, sigma = a
    assert times[0] == H.START + C.STEP and len(times) == 20 * 96 and r.shape == sigma.shape == (20 * 96, 4)
    flat = r.ravel()
    assert abs(flat.mean()) < 4 * flat.std() / np.sqrt(len(flat))


@pytest.mark.parametrize("kind", [C.LIQ, C.IMB, C.BID_DROP, C.WHALE, C.FEAR, C.TREND])
def test_series_are_linked_to_the_contemporaneous_return_and_have_holes(kind):
    rng = np.random.default_rng(11)
    times, r, sigma = H.market(rng, 3, 30)
    values = H.series(kind, rng, times, r, sigma)
    assert values.isna().any().any()                                          # pannes du collecteur
    if kind == C.LIQ:
        bucket = values.diff(1).iloc[:, 0]                                     # pas exact (somme glissante) : signe seulement
        assert np.corrcoef(np.nan_to_num(bucket.to_numpy()[1:]), r[1:, 0])[0, 1] < 0
    if kind == C.IMB:
        hours, rh = H._hourly(times, r)
        ok = values.iloc[:, 0].notna().to_numpy()
        assert np.corrcoef(values.iloc[:, 0].to_numpy()[ok], rh[ok, 0])[0, 1] > 0.1


def test_one_replicate_end_to_end_with_the_live_chain():
    out = H.replicate("F26_MUR_ACHETEURS", np.random.SeedSequence(1), samples=500)
    assert out["events"] > 30 and set(out) >= {"null", "positive", "days", "trou", "windows"}
    assert out["null"]["verdict"] in (C.ABOVE, C.NOT_SHOWN, C.INSUFFICIENT)
    again = H.replicate("F26_MUR_ACHETEURS", np.random.SeedSequence(1), samples=500)
    assert again == out


@pytest.mark.slow
def test_controle_h0_f25_f30_once():
    """Passage UNIQUE (200 répliques indépendantes par test). Fichier dans `CSI_COLLECTE_H0_OUT` s'il est donné."""
    target = os.environ.get("CSI_COLLECTE_H0_OUT")
    report = H.run(out_path=Path(target) if target else None)
    print(json.dumps({t: {k: v for k, v in x.items() if k != "null_verdicts"} for t, x in report["tests"].items()},
                     ensure_ascii=False))
    assert set(report["tests"]) == set(C.TEST_IDS)
    for test_id, result in report["tests"].items():
        assert result["replicates"] == 200
        assert result["passes"], f"{test_id} échoue au contrôle sous H0 : il ne doit pas démarrer"
