"""Contrôle de l'étude « price action » SOUS L'HYPOTHÈSE NULLE (research/price_action_h0.py, docs/PRICE_ACTION.md § 5).

Le contrôle complet (100 paires × 6 ans + BTC, pipeline exact de l'étude) est LENT : lancé seulement avec `--lents`
(`CSI_PA_H0_PAIRS`, `CSI_PA_H0_WORKERS` pour le réduire ou l'accélérer ; déterministe : même code, même graine, mêmes
chiffres). Le passage qui fait foi est celui de `csi price-action controle-h0`, inscrit dans
`research/price_action_review.CONTROLE_H0`. Les autres tests de ce fichier vérifient le générateur et le calcul des
critères sur des lignes fabriquées, sans lancer le contrôle."""
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
    assert H.symbol_of(H.PAIRS) == "BTCUSDT" and H.symbol_of(7) == "H07USDT" and H.group_of("H47USDT") == 7
    assert H.group_of("H47USDT", 6) == 5 and [H.groups_for(n) for n in (50, 350, 664, 5124)] == [3, 3, 6, 20]
    assert not H.market(3).equals(H.market(4))


def fabricated(values: list[tuple[int, str, float]]) -> list[dict]:
    """(indice de paire, date, excès) → lignes au format de l'étude (central seulement)."""
    rows = []
    for index, date, excess in values:
        at = pd.Timestamp(date, tz="UTC")
        rows.append({"symbol": H.symbol_of(index), "at_ns": int(D.to_ns([at])[0]),
                     "results": {"central": {"r": excess, "excess": excess, "excess_back": excess, "excess_forward": excess}}})
    return rows


def test_criteria_coverage_and_reasons_on_fabricated_rows():
    rng = np.random.default_rng(0)
    days = pd.date_range("2019-01-01", "2024-12-01", freq="3D", tz="UTC")
    unbiased = fabricated([(k % H.PAIRS, str(d), float(rng.normal(0, 1))) for k, d in enumerate(days) for _ in range(1)])
    unbiased += fabricated([((k + 37) % H.PAIRS, str(d), float(rng.normal(0, 1))) for k, d in enumerate(days)])
    out = H.criteria(unbiased, samples=500)
    assert out["n"] == len(unbiased) and out["groups_count"] == H.groups_for(len(unbiased)) == len(out["groups"])
    assert out["groups_defined"] == out["groups_count"] and out["coverage"] is not None
    biased = fabricated([(k % H.PAIRS, str(d), float(rng.normal(0.8, 0.2))) for k, d in enumerate(days)] * 2)
    worse = H.criteria(biased, samples=500)
    assert not worse["passes"] and any("excès" in reason for reason in worse["reasons"]) and worse["coverage"] < 0.9
    few = H.criteria(fabricated([(1, "2020-01-01", 0.0)] * 20), samples=500)
    assert not few["passes"] and any("non jugeable" in reason for reason in few["reasons"])
    assert few["coverage"] == 0.0                                       # groupes sans intervalle : non couverts


@pytest.mark.slow
def test_h0_control_full_run(tmp_path):
    pairs = int(os.environ.get("CSI_PA_H0_PAIRS", str(H.PAIRS)))
    workers = int(os.environ.get("CSI_PA_H0_WORKERS", "2"))
    report = H.run(now=pd.Timestamp.now(tz="UTC"), out_dir=tmp_path, workers=workers, pairs=pairs)
    print("\nCONTROLE H0 PRICE ACTION :", {c: {k: v for k, v in r.items() if k != "groups"} for c, r in report["configs"].items()})
    assert set(report["configs"]) == set(D.CONFIGS) and (tmp_path / "criteres.json").exists()
    for result in report["configs"].values():
        assert {"n", "passes", "reasons"} <= set(result)
