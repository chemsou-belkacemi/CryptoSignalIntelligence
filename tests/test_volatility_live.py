"""Volatilité prévue en service (outlook/volatility.py) : mêmes modèles que le protocole, une prévision par jour,
causale, mise en cache ; route de l'API ; distances d'un signal. Données SYNTHÉTIQUES."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi, explain
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.outlook import volatility as live
from crypto_signal_intelligence.research import volatility as vol

from .conftest import canonical

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
NOW = datetime(2025, 3, 11, 0, 30, tzinfo=UTC)          # 30 min après l'origine du 2025-03-11
ORIGIN = pd.Timestamp("2025-03-11", tz="UTC")


@pytest.fixture
def market(settings, monkeypatch):
    store = CandleStore(settings.data_dir)
    for index, symbol in enumerate(PAIRS):                # 2024-01-01 → 2025-03-14 : il y a du « futur » après NOW
        store.save(canonical(24 * 438, "1h", symbol=symbol, start="2024-01-01", seed=index), symbol, "1h")
    store.save(canonical(24 * 60, "1h", symbol="ADAUSDT", start="2025-01-13", seed=9), "ADAUSDT", "1h")   # trop récente
    settings.data.symbols = [*PAIRS, "ADAUSDT"]
    monkeypatch.setattr(vol, "MIN_HISTORY_DAYS", 100)
    monkeypatch.setattr(vol, "LGBM_ROUNDS", 20)
    monkeypatch.setattr(vol, "LGBM_PARAMS", vol.LGBM_PARAMS | {"min_data_in_leaf": 20})
    live._ATTEMPT.clear()
    return settings


def test_forecast_uses_the_protocol_models_on_what_is_known_now(market):
    out = live.compute(market, now=NOW)
    assert out["origin"] == ORIGIN.isoformat() and out["source_run"].startswith("VOL-")
    assert {h: m["model"] for h, m in out["models"].items()} == {"1": "M5_LGBM_POOLED", "3": "M5_LGBM_POOLED",
                                                                 "7": "M4_HAR_POOLED_BTC"}
    assert all(m["refit"].startswith("2025-03-01") for m in out["models"].values())      # modèle du 1er du mois
    eth = out["pairs"]["ETHUSDT"]
    assert eth["available"] and set(eth["horizons"]) == {"1", "3", "7"}
    # Recalcul indépendant avec le code du protocole : lignes journalières, purge, modèle du mois.
    store = CandleStore(market.data_dir)
    frames = []
    for symbol in PAIRS:
        h1 = store.load(symbol, "1h")
        btc = store.load("BTCUSDT", "1h")
        cut = lambda f: f.loc[f["available_at"] <= pd.Timestamp(NOW), list(vol.READ_COLUMNS)]   # noqa: E731
        frames.append(vol.daily_frame(cut(h1), cut(btc)).assign(symbol=symbol))
    data = pd.concat(frames, ignore_index=True)
    row = data[(data["origin"] == ORIGIN) & (data["symbol"] == "ETHUSDT")]
    fitted = vol.fit_at(data, pd.Timestamp("2025-03-01", tz="UTC"), 7, seed=market.protocol.seed)
    expected = vol.month_forecasts(fitted, row, 7)
    assert eth["horizons"]["7"]["move_pct"] == round(math.sqrt(expected["M4_HAR_POOLED_BTC"].iloc[0]) * 100, 2)
    assert eth["horizons"]["7"]["recent_move_pct"] == round(math.sqrt(row["var_w"].iloc[0] * 168) * 100, 2)
    assert eth["horizons"]["7"]["ratio"] == round(eth["horizons"]["7"]["move_pct"] / eth["horizons"]["7"]["recent_move_pct"], 2)
    assert eth["horizons"]["1"]["move_pct"] < eth["horizons"]["3"]["move_pct"] < eth["horizons"]["7"]["move_pct"]
    assert out["pairs"]["ADAUSDT"] == {"available": False, "reason": "moins de 100 jours d'historique"}


def test_forecast_never_reads_candles_after_now(market):
    before = live.compute(market, now=NOW)
    store = CandleStore(market.data_dir)
    rng = np.random.default_rng(1)
    for symbol in PAIRS:
        frame = store.load(symbol, "1h")
        later = (frame["available_at"] > pd.Timestamp(NOW)).to_numpy()
        assert later.sum() > 60
        frame.loc[later, "close"] = frame.loc[later, "close"] * rng.uniform(0.5, 1.5, int(later.sum()))
        store.save(frame, symbol, "1h")
    after = live.compute(market, now=NOW)
    assert after["pairs"] == before["pairs"] and after["models"] == before["models"]


def test_a_gap_or_a_missing_day_gives_no_forecast_instead_of_a_guess(market):
    store = CandleStore(market.data_dir)
    frame = store.load("SOLUSDT", "1h")
    store.save(frame[frame["open_time"] != ORIGIN - pd.Timedelta(hours=1)], "SOLUSDT", "1h")     # bougie de 23:00 absente
    eth = store.load("ETHUSDT", "1h")
    hole = eth["open_time"].between(ORIGIN - pd.Timedelta(hours=20), ORIGIN - pd.Timedelta(hours=10))
    store.save(eth[~hole], "ETHUSDT", "1h")                                                    # 11 heures manquantes hier
    out = live.compute(market, now=NOW)
    assert out["pairs"]["SOLUSDT"] == {"available": False, "reason": "pas de bougie de 23:00 pour ce jour"}
    assert out["pairs"]["ETHUSDT"] == {"available": False, "reason": "variables incomplètes (trou de données récent)"}
    assert out["pairs"]["BTCUSDT"]["available"]
    CandleStore(market.data_dir).path("BTCUSDT", "1h").unlink()
    with pytest.raises(RuntimeError, match="BTCUSDT"):
        live.compute(market, now=NOW)


def test_daily_cache_is_reused_then_refreshed_for_a_new_day_or_a_new_pair(market, monkeypatch):
    calls = []
    honest = live.compute
    monkeypatch.setattr(live, "compute", lambda settings, *, now, symbols=None: calls.append(now) or honest(settings, now=now))
    first = live.ensure(market, now=NOW)
    assert first["origin"] == ORIGIN.isoformat() and len(calls) == 1
    assert json.loads(live.state_path(market).read_text(encoding="utf-8")) == first
    assert live.ensure(market, now=NOW + timedelta(hours=5)) == first and len(calls) == 1      # même jour : relu
    assert live.for_symbol(market, "ETHUSDT")["origin"] == ORIGIN.isoformat() and live.for_symbol(market, "XUSDT") is None
    next_day = live.ensure(market, now=NOW + timedelta(days=1))
    assert next_day["origin"] == (ORIGIN + pd.Timedelta(days=1)).isoformat() and len(calls) == 2
    # Juste après minuit, les bougies du jour ne sont pas encore là : on attend 10 minutes avant de changer de jour.
    assert live.expected_origin(datetime(2025, 3, 12, 0, 5, tzinfo=UTC)) == ORIGIN
    assert live.expected_origin(datetime(2025, 3, 12, 0, 10, tzinfo=UTC)) == ORIGIN + pd.Timedelta(days=1)
    # Une paire ajoutée à l'univers manque au fichier : recalcul, mais pas plus d'une tentative par quart d'heure.
    market.data.symbols = [*market.data.symbols, "NEWUSDT"]
    moment = NOW + timedelta(days=1, minutes=1)
    assert live.ensure(market, now=moment) == next_day and len(calls) == 2                      # tentative trop récente
    refreshed = live.ensure(market, now=moment + timedelta(minutes=20))
    assert len(calls) == 3 and refreshed["pairs"]["NEWUSDT"] == {"available": False, "reason": "aucune bougie 1 h"}
    assert live.ensure(market, now=moment + timedelta(minutes=21), force=True) and len(calls) == 4


def test_signal_distances_are_expressed_in_expected_moves():
    forecast = {"available": True, "horizons": {"1": {"move_pct": 2.0}, "3": {"move_pct": 4.0}, "7": {"move_pct": None}}}
    assert live.distances(forecast, tp1_pct=3.0, stop_pct=8.0) == {
        "1": {"move_pct": 2.0, "tp1_moves": 1.5, "stop_moves": 4.0}, "3": {"move_pct": 4.0, "tp1_moves": 0.75, "stop_moves": 2.0}}
    assert live.distances(None, tp1_pct=3, stop_pct=8) is None
    assert live.distances({"available": False, "reason": "x"}, tp1_pct=3, stop_pct=8) is None
    text = explain({"verdict": "INDETERMINE", "checks": [], "source": "g",
                    "volatility": {"3": {"move_pct": 4.0, "tp1_moves": 0.75, "stop_moves": 2.0}}})
    assert "±4.0 %" in text and "0.75 fois" in text and "sans direction" in text


def test_api_route_serves_the_forecast_and_says_when_it_is_missing(market, settings):
    api = CsiApi(market, now=lambda: NOW)
    everything = api.dispatch("GET", "/volatility", {}, None)
    assert everything["available"] and everything["origin"] == ORIGIN.isoformat() and "jamais le sens" in everything["note"]
    one = api.dispatch("GET", "/volatility", {"symbol": ["ethusdt"]}, None)
    assert one["symbol"] == "ETHUSDT" and one["forecast"]["available"] and set(one["model_names"]) == {"1", "3", "7"}
    with pytest.raises(ApiError):
        api.dispatch("GET", "/volatility", {"symbol": ["NOPEUSDT"]}, None)
    for symbol in ("BTCUSDT",):
        CandleStore(market.data_dir).path(symbol, "1h").unlink()
    live.state_path(market).unlink()
    live._ATTEMPT.clear()
    missing = api.dispatch("GET", "/volatility", {}, None)
    assert missing == {"available": False, "reason": "prévision pas encore calculée (données ou modèle indisponibles)"}


def test_the_page_shows_the_forecast_in_the_pair_market_and_signal_views():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "src" / "crypto_signal_intelligence" / "api" / "static"
    script, page = (static / "app.js").read_text(encoding="utf-8"), (static / "index.html").read_text(encoding="utf-8")
    assert "volatilityCard(r.symbol)" in script and 'api("/volatility")' in script and "e.volatility" in script
    assert 'id="volatility-result"' in page and "Mouvement typique attendu" in script
