"""Protocole v5 (research/volatility_rv.py, docs/VOLATILITY.md § 20) : volatilité réalisée à 1 et 5 minutes calculée à
la main, journées incomplètes écartées, variables connues à l'origine (falsifier le futur ne change rien), purge de
l'entraînement. Données SYNTHÉTIQUES."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import volatility_rv as vr

D0 = pd.Timestamp("2024-01-01", tz="UTC")


def minutes(days: int, seed: int = 0, drop: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 1440
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    frame = pd.DataFrame({"open_time": pd.date_range(D0, periods=n, freq="min"), "close": close})
    return frame.iloc[drop:].reset_index(drop=True)


def test_daily_rv_by_hand():
    frame = minutes(3)
    rv1 = vr.daily_rv(frame, 1)
    day1 = frame[(frame["open_time"] >= D0 + pd.Timedelta(days=1)) & (frame["open_time"] < D0 + pd.Timedelta(days=2))]
    prev = frame[frame["open_time"] < D0 + pd.Timedelta(days=1)]["close"].iloc[-1]
    r = np.log(np.r_[prev, day1["close"].to_numpy()])
    assert rv1.iloc[1] == pytest.approx(float((np.diff(r) ** 2).sum()))
    rv5 = vr.daily_rv(frame, 5)
    blocks = frame.set_index("open_time")["close"].groupby(lambda t: t.floor("5min")).last()
    r5 = np.log(blocks).diff()
    expected = float((r5[(r5.index >= D0 + pd.Timedelta(days=1)) & (r5.index < D0 + pd.Timedelta(days=2))] ** 2).sum())
    assert rv5.iloc[1] == pytest.approx(expected)


def test_incomplete_days_are_dropped():
    frame = minutes(3)
    frame = frame[~((frame["open_time"] >= D0 + pd.Timedelta(days=1)) & (frame["open_time"] < D0 + pd.Timedelta(days=1, hours=2)))]
    rv = vr.daily_rv(frame, 5)
    assert np.isnan(rv.iloc[1]) and np.isfinite(rv.iloc[2])                    # 2 h manquantes : moins de 95 %


def test_features_at_an_origin_ignore_the_future():
    frame = minutes(60, seed=3)
    origin = D0 + pd.Timedelta(days=40)
    fake = frame.copy()
    future = fake["open_time"] >= origin
    fake.loc[future, "close"] = fake.loc[future, "close"] * np.random.default_rng(1).uniform(0.5, 2, int(future.sum()))
    a = vr.har_features(vr.daily_rv(frame, 5)).loc[origin]
    b = vr.har_features(vr.daily_rv(fake, 5)).loc[origin]
    assert np.allclose(a.to_numpy(float), b.to_numpy(float))
    assert np.isfinite(a.to_numpy(float)).all()
    with_end = vr.har_features(vr.daily_rv(fake, 5, end=origin))
    assert np.allclose(with_end.loc[origin].to_numpy(float), a.to_numpy(float))


def test_training_is_purged(monkeypatch):
    seen = []

    def spy(X, y):
        seen.append(len(y))
        return None

    monkeypatch.setattr(vr.v1, "fit_har", spy)
    origins = pd.date_range("2024-01-01", "2024-03-31", freq="D", tz="UTC")
    part = pd.DataFrame({"origin": origins, "realized": 1e-4, "d": 1e-4, "w": 1e-4, "m": 1e-4})
    vr.candidate_forecasts(part, 7, ["d", "w", "m"])
    # réajustement du 1er mars : seules les origines dont la cible (7 jours) est terminée au 1er mars
    assert seen[2] == int(((origins + pd.Timedelta(days=7)) <= pd.Timestamp("2024-03-01", tz="UTC")).sum())
