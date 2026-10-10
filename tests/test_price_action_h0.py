"""Contrôle de l'étude « price action » SOUS L'HYPOTHÈSE NULLE (research/price_action_h0.py, docs/PRICE_ACTION.md § 5).

Le contrôle complet (100 paires × 6 ans + BTC, pipeline exact de l'étude) est LENT : lancé seulement avec `--lents`
(`CSI_PA_H0_PAIRS`, `CSI_PA_H0_WORKERS` pour le réduire ou l'accélérer ; déterministe : même code, même graine, mêmes
chiffres). Le passage qui fait foi est celui de `csi price-action controle-h0`, inscrit dans
`research/price_action_review.CONTROLE_H0`. Les autres tests de ce fichier vérifient le générateur, le R brut, la
dérive du contrôle positif et les critères sur un pipeline de 3 paires, sans lancer le contrôle."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.price_action import detect as D
from crypto_signal_intelligence.research import price_action_h0 as H


def test_synthetic_market_is_deterministic_martingale_shaped_and_spans_six_years_of_signals():
    a, b = H.market(3), H.market(3)
    pd.testing.assert_frame_equal(a, b)
    assert a["open_time"].iloc[0] == H.START and a["open_time"].iloc[-1] == H.END - pd.Timedelta(hours=1)
    assert (H.END - H.FIRST_SIGNAL).days >= 6 * 365
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all() and (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    assert (a["low"] > 0).all() and (a["quote_volume"] > 0).all()
    r = a["close"].pct_change().dropna()
    assert abs(r.mean()) < 4 * r.std() / np.sqrt(len(r))                 # aucune dérive détectable
    assert H.symbol_of(H.PAIRS) == "BTCUSDT" and H.symbol_of(7) == "H07USDT"
    assert not H.market(3).equals(H.market(4))


def test_subsets_are_reproducible_samples_of_forty_pairs():
    first, again = H.subsets(), H.subsets()
    assert first == again and len(first) == H.REPLICATES == 200
    assert all(len(x) == 40 and x <= {H.symbol_of(i) for i in range(H.PAIRS)} for x in first)
    assert len({frozenset(x) for x in first}) > 190
    assert pytest.approx(0.02) == H.MAX_FALSE_PISTE and H.MIN_POWER == 0.5 and H.TARGET_NET_R == 0.15


@pytest.fixture(scope="module")
def tiny():
    """Pipeline exact sur 3 paires synthétiques (vérification du code, pas le contrôle)."""
    collected = H.collect(pairs=3, workers=1)
    return collected, H.hours_for(3)


def test_gross_r_and_zero_drift_reproduce_the_pipeline(tiny):
    collected, hours_of = tiny
    rows = collected["rows"][D.INSIDE_DAY][:30]
    assert rows and set(rows[0]["results"]) == {"central", "defavorable"}
    for row in rows:
        for scenario in H.SCENARIOS:
            assert H.drifted(hours_of[row["symbol"]], row, 0.0, scenario) == pytest.approx(row["results"][scenario]["r"], abs=1e-6)
        assert H.gross_r(row, "central") == pytest.approx(H.gross_r(row, "defavorable"), abs=1e-4)   # mêmes prix, frais retirés (R arrondis à 1e-6)
        assert H.gross_r(row, "central") > row["results"]["central"]["r"]


def test_positive_drift_is_calibrated_to_plus_fifteen_hundredths_of_r(tiny):
    collected, hours_of = tiny
    rows = collected["rows"][D.BASE_RETEST]
    m = H.calibrate(rows, hours_of)
    assert m > 0
    mean = np.mean([H.drifted(hours_of[r["symbol"]], r, m, "central") for r in rows])
    assert H.TARGET_NET_R <= mean < H.TARGET_NET_R + 0.02                # au plus près par-dessus (sauts de la moyenne)
    assert np.mean([H.drifted(hours_of[r["symbol"]], r, m, "defavorable") for r in rows]) < mean


def test_criteria_statuses_on_the_tiny_pipeline(tiny):
    collected, hours_of = tiny
    samples = [{H.symbol_of(i) for i in range(3)}] * 4
    out = H.criteria(collected["rows"][D.INSIDE_DAY], hours_of, samples, decision_samples=200)
    assert {"false_piste_rate", "power", "status", "passes", "null", "positive", "excess_forward"} <= set(out)
    assert out["null"]["replicates"] == 4 and out["status"] in (H.VALID, H.FAILED, H.WEAK)
    assert out["passes"] == (out["status"] == H.VALID)
    empty = H.criteria([], hours_of, samples, decision_samples=200)
    assert empty["status"] == H.WEAK and empty["power"] == 0.0 and empty["false_piste_rate"] == 0.0


@pytest.mark.slow
def test_h0_control_full_run(tmp_path):
    pairs = int(os.environ.get("CSI_PA_H0_PAIRS", str(H.PAIRS)))
    workers = int(os.environ.get("CSI_PA_H0_WORKERS", "2"))
    report = H.run(now=pd.Timestamp.now(tz="UTC"), out_dir=tmp_path, workers=workers, pairs=pairs)
    print("\nCONTROLE H0 N° 2 PRICE ACTION :", {c: (r["n"], r["false_piste_rate"], r["power"], r["status"])
                                                for c, r in report["configs"].items()})
    assert report["control"] == "H0_N2_R_NET" and set(report["configs"]) == set(D.CONFIGS)
    for result in report["configs"].values():
        assert {"n", "passes", "reasons", "false_piste_rate", "power", "status"} <= set(result)
