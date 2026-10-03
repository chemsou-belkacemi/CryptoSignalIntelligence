"""Contrôle positif de l'ajustement (research/fit_control.py) : cible en excès, variable plantée, entraînement purgé,
un modèle retrouve une variable assez informative. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research import fit_control as fc
from crypto_signal_intelligence.research import volatility as v1


def synthetic(days: int = 900, pairs: int = 8, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    origins = pd.date_range("2019-01-01", periods=days, freq="D", tz="UTC")
    rows = []
    for t in origins:
        target = rng.normal(0, 0.05, pairs)
        target -= target.mean()
        for i in range(pairs):
            rows.append({"origin": t, "symbol": f"P{i}", "target": target[i], **{f: rng.normal() for f in v1.FEATURES}})
    data = pd.DataFrame(rows)
    data["target_end"] = data["origin"] + pd.Timedelta(days=fc.HORIZON_DAYS)
    return data


def test_planted_variable_has_the_requested_correlation_and_training_is_purged(monkeypatch):
    data = synthetic(days=300)
    data["planted"] = fc.plant(data, 0.5, np.random.default_rng(1))
    info = fc.information(data.rename(columns={"planted": "prediction"})[["origin", "symbol", "target", "prediction"]])
    assert 0.35 < info["rank_ic"] < 0.55
    monkeypatch.setattr(fc, "FIRST_FORECAST", pd.Timestamp("2019-06-01", tz="UTC"))
    monkeypatch.setattr(fc, "MIN_TRAIN", 200)
    calls = []
    real = fc.fit_predict

    def spy(train, test, features, model, seed):
        calls.append((train["target_end"].max(), test["origin"].min()))
        return real(train, test, features, model, seed)

    monkeypatch.setattr(fc, "fit_predict", spy)
    fc.walk_forward(data, ["planted"], "lineaire", seed=0, end=pd.Timestamp("2030-01-01", tz="UTC"))
    assert calls and all(train_end <= test_start for train_end, test_start in calls)    # cible connue avant le mois prévu


def test_a_model_recovers_an_informative_planted_variable(monkeypatch):
    data = synthetic()
    data["planted"] = fc.plant(data, 0.3, np.random.default_rng(2))
    monkeypatch.setattr(fc, "FIRST_FORECAST", pd.Timestamp("2019-09-01", tz="UTC"))
    monkeypatch.setattr(fc, "MIN_TRAIN", 500)
    predictions = fc.walk_forward(data, [*v1.FEATURES, "planted"], "lineaire", seed=0, end=pd.Timestamp("2030-01-01", tz="UTC"))
    assert fc.information(predictions)["detected"]
    blind = fc.walk_forward(data, list(v1.FEATURES), "lineaire", seed=0, end=pd.Timestamp("2030-01-01", tz="UTC"))
    assert not fc.information(blind)["detected"]
