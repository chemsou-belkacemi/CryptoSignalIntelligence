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
    vr.candidate_forecasts(part, part, 7, ["d", "w", "m"])
    # réajustement du 1er mars : seules les origines dont la cible (7 jours) est terminée au 1er mars
    assert seen[2] == int(((origins + pd.Timedelta(days=7)) <= pd.Timestamp("2024-03-01", tz="UTC")).sum())


def test_training_uses_history_before_the_evaluated_rows(monkeypatch):
    """Relecture B1 : les lignes évaluées (celles du service) commencent au mois M, l'entraînement a tout le passé
    purgé depuis le début des minutes : le réajustement du 1er M a déjà des lignes."""
    seen = []
    monkeypatch.setattr(vr.v1, "fit_har", lambda X, y: seen.append(len(y)))
    train_origins = pd.date_range("2023-01-01", "2024-03-31", freq="D", tz="UTC")
    train = pd.DataFrame({"origin": train_origins, "realized": 1e-4, "d": 1e-4, "w": 1e-4, "m": 1e-4})
    rows = train[train["origin"] >= pd.Timestamp("2024-01-01", tz="UTC")].reset_index(drop=True)
    vr.candidate_forecasts(train, rows, 3, ["d", "w", "m"])
    assert seen[0] == int(((train_origins + pd.Timedelta(days=3)) <= pd.Timestamp("2024-01-01", tz="UTC")).sum()) > 300


def test_missing_day_counts_in_calendar_windows():
    """Relecture M6 : une journée entièrement absente reste un jour du calendrier (NaN) dans les moyennes."""
    frame = minutes(40, seed=2)
    gone = (frame["open_time"] >= D0 + pd.Timedelta(days=20)) & (frame["open_time"] < D0 + pd.Timedelta(days=21))
    rv = vr.daily_rv(frame[~gone].reset_index(drop=True), 5)
    assert len(rv) == 40 and np.isnan(rv.iloc[20])
    f = vr.har_features(rv)
    origin = D0 + pd.Timedelta(days=27)                                   # fenêtre de 7 jours : jours 20 à 26
    assert f.loc[origin, "w"] == pytest.approx(rv.iloc[20:27].mean())     # moyenne sur 6 journées présentes
    assert np.isnan(f.loc[D0 + pd.Timedelta(days=21), "d"])


def _hourly(mins: pd.DataFrame) -> pd.DataFrame:
    hours = pd.Series(mins["close"].to_numpy(), index=pd.DatetimeIndex(mins["open_time"].dt.floor("h"))).groupby(level=0).last()
    times = pd.DatetimeIndex(hours.index)
    return pd.DataFrame({"open_time": times, "close": hours.to_numpy(),
                         "available_at": times + pd.Timedelta(hours=1, milliseconds=1)})


def test_run_end_to_end(settings, monkeypatch):
    """Relecture C3 : `run()` de bout en bout avec la vraie configuration ; registre (6 comparaisons) avant le rapport,
    empreintes ; falsifier les minutes après la fin de DEVELOPMENT ne change aucune prévision ; code non commité et
    cible incohérente refusés sans rien compter."""
    from datetime import UTC, datetime

    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    start = pd.Timestamp("2024-11-01", tz="UTC")
    n = 270 * 1440
    data = {}
    for i, symbol in enumerate(("AAAUSDT", "BBBUSDT")):
        rng = np.random.default_rng(i)
        vol = 0.0006 * np.exp(np.repeat(rng.normal(0, 0.4, 270), 1440))
        close = 100 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))
        data[symbol] = pd.DataFrame({"open_time": pd.date_range(start, periods=n, freq="min"), "close": close})
    end = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
    rows = []
    for symbol, mins in data.items():
        frame = vr.v1.daily_frame(_hourly(mins[mins["open_time"] <= end]))
        for h in vr.v1.HORIZONS:
            ok = frame[(frame["origin"] >= pd.Timestamp("2025-03-01", tz="UTC")) & (frame[f"rv2_{h}"] > 0)]
            rows.append(pd.DataFrame({"symbol": symbol, "origin": ok["origin"], "horizon": h, "model": vr.v4.REFERENCE,
                                      "forecast": ok[f"rv2_{h}"] * 1.1, "realized": ok[f"rv2_{h}"]}))
    source = settings.reports_dir / vr.v4.SOURCE_RUN
    source.mkdir(parents=True)
    pd.concat(rows).to_parquet(source / "forecasts.parquet", index=False)
    store = {"minutes": data}
    monkeypatch.setattr(vr, "load_minutes", lambda settings, symbol: store["minutes"][symbol])
    monkeypatch.setattr(vr.v1, "load_long", lambda settings, symbol: _hourly(store["minutes"][symbol]))
    monkeypatch.setattr(vr, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(vr.v1.DirtyCode):
        vr.run(settings, now=datetime(2026, 10, 3, tzinfo=UTC))
    monkeypatch.setattr(vr, "code_state", lambda: "abc123")
    first = vr.run(settings, now=datetime(2026, 10, 3, tzinfo=UTC))
    registry = ExperimentRegistry(settings.experiments_db)
    entry = registry.get(first.run_id)
    assert entry is not None and entry["metrics"]["n_trials"] == 6 and entry["period_end"].startswith("2025-06-30")
    assert {"AAAUSDT", "BBBUSDT"} <= {k.split("/")[-1] for k in entry["data_hashes"]}
    a = pd.read_parquet(settings.reports_dir / first.run_id / "forecasts.parquet")
    assert a["origin"].min() == pd.Timestamp("2025-03-01", tz="UTC")                       # janvier-mars : déjà un modèle
    fake = {}
    for symbol, mins in data.items():
        m = mins.copy()
        future = m["open_time"] > end
        m.loc[future, "close"] = m.loc[future, "close"] * np.random.default_rng(9).uniform(0.5, 2, int(future.sum()))
        fake[symbol] = m
    store["minutes"] = fake
    second = vr.run(settings, now=datetime(2026, 10, 3, tzinfo=UTC))
    b = pd.read_parquet(settings.reports_dir / second.run_id / "forecasts.parquet")
    pd.testing.assert_frame_equal(a[list(vr.CANDIDATES)], b[list(vr.CANDIDATES)])
    before = registry.program_trials()
    table = pd.read_parquet(source / "forecasts.parquet")
    table.loc[0, "realized"] *= 1.0001
    table.to_parquet(source / "forecasts.parquet", index=False)
    with pytest.raises(vr.IncompleteMinutes, match="cible recalculée"):
        vr.run(settings, now=datetime(2026, 10, 3, tzinfo=UTC))
    assert registry.program_trials() == before
