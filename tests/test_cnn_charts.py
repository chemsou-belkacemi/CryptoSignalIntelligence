"""CNN sur images de graphiques (docs/CNN.md) : image, causalité, découpage, entraînement minimal (synthétique)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import cnn_charts as cc


def hourly(days: int, *, start: str = "2022-01-01", seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(times))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open_time": times, "open": open_, "high": np.maximum(open_, close) * 1.002,
                         "low": np.minimum(open_, close) * 0.998, "close": close, "quote_volume": rng.uniform(1, 2, len(times))})


def test_daily_bars_tolerate_a_few_missing_hours():
    data = hourly(5)
    data = data.drop(index=[30])                                       # une heure manque le 2e jour : gardé
    data = data.drop(index=list(range(48, 53)))                        # cinq heures manquent le 3e jour : écarté
    days = cc.daily_bars(data, end=pd.Timestamp("2030-01-01", tz="UTC"))
    assert len(days) == 4 and pd.Timestamp("2022-01-03", tz="UTC") not in days.index
    assert pd.Timestamp("2022-01-02", tz="UTC") in days.index
    first = data.iloc[:24]
    assert days.iloc[0]["open"] == first["open"].iloc[0] and days.iloc[0]["close"] == first["close"].iloc[-1]
    assert days.iloc[0]["high"] == first["high"].max() and days.iloc[0]["volume"] == pytest.approx(first["quote_volume"].sum())


def test_chart_image_layout():
    index = pd.date_range("2022-01-01", periods=cc.DAYS, freq="D", tz="UTC")
    price = np.linspace(10, 29, cc.DAYS)
    window = pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price + 0.5,
                           "volume": np.r_[np.zeros(cc.DAYS - 1), 5.0]}, index=index)
    image = cc.chart_image(window)
    assert image.shape == (cc.HEIGHT, cc.WIDTH) and set(np.unique(image)) <= {0, 1}
    assert image[cc.PRICE_ROWS - 1, 1] == 1                            # plus bas des 20 jours : dernière ligne de prix
    assert image[0, 3 * (cc.DAYS - 1) + 1] == 1                        # plus haut : première ligne
    assert not image[cc.PRICE_ROWS].any()                              # ligne vide
    assert image[cc.HEIGHT - cc.VOLUME_ROWS:, 3 * (cc.DAYS - 1) + 1].all()  # plus gros volume : 12 lignes
    assert not image[cc.PRICE_ROWS + 1:, 1].any()                      # volume nul : rien


def test_image_ignores_monday_and_later():
    """Causalité : modifier toute bougie à partir du lundi ne change pas l'image du lundi."""
    data = hourly(60)
    monday = pd.Timestamp("2022-02-07", tz="UTC")
    end = pd.Timestamp("2030-01-01", tz="UTC")
    before = cc.chart_image(cc.image_days(cc.daily_bars(data, end=end), monday))
    future = data.copy()
    later = pd.to_datetime(future["open_time"], utc=True) >= monday
    future.loc[later, ["open", "high", "low", "close"]] *= 50
    future.loc[later, "quote_volume"] *= 1000
    after = cc.chart_image(cc.image_days(cc.daily_bars(future, end=end), monday))
    assert np.array_equal(before, after)
    window = cc.image_days(cc.daily_bars(data, end=end), monday)
    assert window.index[-1] == monday - pd.Timedelta(days=1) and len(window) == cc.DAYS


def test_image_needs_twenty_complete_days():
    data = hourly(30, start="2022-01-10")
    days = cc.daily_bars(data, end=pd.Timestamp("2030-01-01", tz="UTC"))
    assert cc.image_days(days, pd.Timestamp("2022-01-24", tz="UTC")) is None   # 14 journées seulement
    assert cc.image_days(days.drop(index=days.index[20]), pd.Timestamp("2022-02-07", tz="UTC")) is None


def test_samples_label_against_weekly_mean():
    end = pd.Timestamp("2030-01-01", tz="UTC")
    days = {s: cc.daily_bars(hourly(60, seed=i), end=end) for i, s in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT"))}
    mondays = pd.DatetimeIndex([pd.Timestamp("2022-02-07", tz="UTC"), pd.Timestamp("2022-02-14", tz="UTC")])
    returns = pd.DataFrame({"AAAUSDT": [0.03, np.nan], "BBBUSDT": [0.01, np.nan], "CCCUSDT": [-0.01, np.nan]}, index=mondays)
    members = {pd.Period("2022-02", "M"): {"AAAUSDT", "BBBUSDT", "CCCUSDT", "ZZZUSDT"}}
    table, images = cc.build_samples(days, members, mondays, returns)
    assert len(table) == 6 and images.shape == (6, cc.HEIGHT, cc.WIDTH)
    first = table[table["monday"] == mondays[0]].set_index("symbol")["label"]
    assert first.to_dict() == {"AAAUSDT": 1.0, "BBBUSDT": 0.0, "CCCUSDT": 0.0}
    assert table[table["monday"] == mondays[1]]["label"].isna().all()


def _table(mondays: list[str]) -> pd.DataFrame:
    m = pd.to_datetime(pd.Series(mondays), utc=True)
    return pd.DataFrame({"monday": m, "symbol": "AAAUSDT", "ret": 0.01, "label": 1.0, "last_day": m - pd.Timedelta(days=1)})


def test_split_is_disjoint_and_refuses_overlap(monkeypatch):
    end = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
    table = _table(["2022-12-19", "2022-12-26", "2023-01-23", "2023-01-30", "2025-06-23", "2025-06-30"])
    train, validation = cc.split(table, end)
    assert table.loc[train, "monday"].dt.strftime("%Y-%m-%d").tolist() == ["2022-12-19"]
    assert table.loc[validation, "monday"].dt.strftime("%Y-%m-%d").tolist() == ["2023-01-30", "2025-06-23"]   # la journée du 30 juin est dans DEVELOPMENT
    monkeypatch.setattr(cc, "FIRST_VALIDATION", pd.Timestamp("2023-01-02", tz="UTC"))
    with pytest.raises(ValueError, match="chevauchement"):
        cc.split(_table(["2022-12-19", "2023-01-02"]), end)
    late = _table(["2023-02-06"])
    late["last_day"] = late["monday"]
    with pytest.raises(ValueError, match="postérieure"):
        cc.split(late, end)


def test_weights_and_ranking():
    m = pd.Timestamp("2023-02-06", tz="UTC")
    symbols = [f"S{i:02d}USDT" for i in range(10)]
    table = pd.DataFrame({"monday": m, "symbol": symbols, "prob": np.linspace(0.1, 0.9, 10),
                          "ret": np.linspace(-0.05, 0.05, 10), "label": [0.0] * 5 + [1.0] * 5})
    strategy, basket = cc.weights(table, pd.Index(symbols), pd.DatetimeIndex([m]))
    assert strategy.loc[m].sum() == pytest.approx(1.0) and (strategy.loc[m, symbols[:2]] == 0).all()
    assert basket.loc[m].sum() == pytest.approx(1.0) and basket.loc[m].nunique() == 1
    rank = cc.ranking(table)
    assert rank["auc"] == 1.0 and rank["accuracy"] == 1.0 and rank["rank_corr_weekly_mean"] == pytest.approx(1.0)


def test_verdict_needs_lower_bound_above_zero():
    assert cc.verdict({"excess_ci_pct": [0.01, 0.5]}) == cc.PASS
    assert cc.verdict({"excess_ci_pct": [-0.01, 0.5]}) == cc.FAIL
    assert cc.verdict({"excess_ci_pct": None}) == cc.FAIL


def test_model_shape_and_training_is_reproducible():
    torch = pytest.importorskip("torch")
    model = cc.build_model()
    assert model(torch.zeros(3, 1, cc.HEIGHT, cc.WIDTH)).shape == (3, 2)
    rng = np.random.default_rng(0)
    images = (rng.random((40, cc.HEIGHT, cc.WIDTH)) > 0.9).astype(np.uint8)
    labels = rng.integers(0, 2, 40)
    first, history = cc.train(images[:30], labels[:30], images[30:], labels[30:], max_epochs=2)
    second, _ = cc.train(images[:30], labels[:30], images[30:], labels[30:], max_epochs=2)
    assert 1 <= len(history) <= 2
    p1, p2 = cc.predict(first, images[30:]), cc.predict(second, images[30:])
    assert np.allclose(p1, p2) and ((p1 >= 0) & (p1 <= 1)).all()


def test_run_end_to_end_with_real_config(settings, monkeypatch, tmp_path):
    """Relecture : `run()` de bout en bout avec la vraie configuration (fuseau de pydantic), données synthétiques,
    entraînement remplacé par un bouchon ; registre écrit avant le rapport, empreintes des données inscrites."""
    pytest.importorskip("torch")
    from crypto_signal_intelligence.research import long_history, pit_universe
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    symbols = [f"S{i:02d}USDT" for i in range(10)]
    months = pd.date_range("2018-12-01", "2025-06-01", freq="MS", tz="UTC")
    membership = pd.DataFrame([{"month": m, "symbol": s} for m in months for s in symbols])
    (tmp_path / "membership.parquet").write_bytes(b"synthetique")
    frames = {s: hourly(2440, start="2018-11-01", seed=i) for i, s in enumerate(symbols)}
    monkeypatch.setattr(pit_universe, "load_membership", lambda settings: membership)
    monkeypatch.setattr(pit_universe, "pit_dir", lambda settings: tmp_path)
    monkeypatch.setattr(long_history, "load_long", lambda settings, symbol: frames[symbol])
    monkeypatch.setattr(cc, "code_state", lambda: "abc123")

    class Model:
        def state_dict(self):
            return {}
    monkeypatch.setattr(cc, "train", lambda *a, **k: (Model(), [{"epoch": 1, "train_loss": 0.7, "stop_loss": 0.69}]))
    monkeypatch.setattr(cc, "predict", lambda model, images: np.random.default_rng(0).random(len(images)))
    from datetime import UTC, datetime
    payload = cc.run(settings, now=datetime(2026, 10, 3, tzinfo=UTC))
    assert payload["verdict"] in (cc.PASS, cc.FAIL) and payload["n_trials"] == 1
    assert payload["coverage"]["first_validation"] == "2023-01-30" and payload["coverage"]["last_validation"] == "2025-06-23"
    assert payload["coverage"]["weeks_without_images"]["validation"] == []
    assert set(payload["data_hashes"]) == {"membership.parquet", *symbols}
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["data_hashes"]
    assert (settings.reports_dir / payload["run_id"] / "summary.json").exists()
