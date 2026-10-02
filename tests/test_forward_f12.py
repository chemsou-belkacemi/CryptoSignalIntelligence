"""Test en direct F12_VOL_FORWARD (forward/f12.py) : QLIKE et verdict à la main, prévisions journalisées une fois par
jour (modèles de recherche gelés sur un magasin synthétique), résolution après 7 jours, statistiques,
pré-inscription. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward import f12, registry
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.research import volatility as v1

from .conftest import PROJECT, canonical

PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HALAL = HalalList(PAIRS, {}, "a" * 64, "b" * 64)


def test_qlike_and_verdict_by_hand():
    assert f12.qlike(1.0, 1.0) == 0.0 and f12.qlike(2.0, 1.0) == pytest.approx(2 - np.log(2) - 1)
    assert f12.qlike(0.0, 1.0) is None and f12.qlike(1.0, float("nan")) is None
    times = pd.date_range("2026-10-10", periods=84, freq="D").to_numpy()
    assert f12.verdict_of(np.full(84, -0.01), times, ended=False)[0] == f12.RUNNING
    assert f12.verdict_of(np.full(30, -0.01), times[:30], ended=True)[0] == f12.INSUFFICIENT
    rng = np.random.default_rng(1)
    better = -0.05 + rng.normal(0, 0.01, 84)
    assert f12.verdict_of(better, times, ended=True)[0] == f12.CONFIRMED
    assert f12.verdict_of(-better, times, ended=True)[0] == f12.REFUTED
    assert f12.verdict_of(rng.normal(0, 0.01, 84), times, ended=True)[0] == f12.NO_DIFFERENCE
    assert pytest.approx(1 - 0.05 / 7) == f12.LEVEL and len(f12.COMPARISONS) == 7


@pytest.fixture
def live_store(settings, monkeypatch):
    """Magasin de la surveillance synthétique : 1 400 jours de bougies 1 h (assez pour 400 jours d'historique et le
    profil horaire), jusqu'au 2026-10-12 inclus."""
    store = CandleStore(settings.data_dir)
    for index, symbol in enumerate(PAIRS):
        store.save(canonical(24 * 1400, "1h", symbol=symbol, start="2022-12-14", seed=index), symbol, "1h")
    settings.data.symbols = list(PAIRS)
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})
    f12._DAILY_CACHE.clear()
    f12._HOURLY_CACHE.clear()
    return settings


def test_poll_journals_all_forecasts_once_per_day_then_resolves(live_store):
    settings = live_store
    start = registry.start(settings, f12.TEST, now=datetime(2026, 10, 1, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f12.TEST_ID)
    assert f12.poll(settings, journal, start, now=datetime(2026, 10, 3, 0, 5, tzinfo=UTC)) == {"checks": 0}      # avant 00:10
    out = f12.poll(settings, journal, start, now=datetime(2026, 10, 3, 0, 25, tzinfo=UTC))
    assert out == {"checks": 1, "evaluable": 3}
    assert f12.poll(settings, journal, start, now=datetime(2026, 10, 3, 1, 25, tzinfo=UTC)) == {"checks": 0}
    entry = next(journal.entries({f12.FORECAST}))["data"]
    btc = entry["pairs"]["BTCUSDT"]
    assert set(btc) == {"1d", "3d", "7d", "4h", "24h"} and set(btc["3d"]) == set(f12.DAILY_MODELS) and set(btc["24h"]) == set(f12.HOURLY_MODELS)
    assert btc["3d"]["V1_MEAN_M4_M5"] == pytest.approx((btc["3d"]["M4_HAR_POOLED_BTC"] + btc["3d"]["M5_LGBM_POOLED"]) / 2)
    assert all(v > 0 for h in btc.values() for v in h.values())
    assert f12.resolve(settings, journal, now=datetime(2026, 10, 9, 12, tzinfo=UTC)) == {}                      # 7 jours pas passés
    assert f12.resolve(settings, journal, now=datetime(2026, 10, 10, 3, tzinfo=UTC)) == {"RESOLU": 1}
    resolution = next(journal.entries({f12.RESOLUTION}))["data"]
    losses = resolution["pairs"]["BTCUSDT"]
    assert losses["3d"]["realized"] > 0 and set(losses["3d"]["qlike"]) == set(f12.DAILY_MODELS) and losses["24h"]["realized"] > 0
    expected = f12.qlike(losses["3d"]["realized"], btc["3d"]["M5_LGBM_POOLED"])
    assert losses["3d"]["qlike"]["M5_LGBM_POOLED"] == pytest.approx(expected)
    out = f12.stats(journal, start, now=datetime(2026, 10, 10, 4, tzinfo=UTC))
    assert out["resolved"] == 1 and out["pending"] == 0 and len(out["comparisons"]) == 7
    first = out["comparisons"]["3d:V1_MEAN_M4_M5-vs-M5_LGBM_POOLED"]
    assert first["days"] == 1 and first["verdict"] == f12.RUNNING and first["expected"] == "CONFIRME" and journal.verify()["ok"]


def test_f12_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f12.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("V1_MEAN_M4_M5", "H1_HAR_PROFILE", "7 comparaisons", "60 jours", "blocs de 10 jours", "1er du mois", "1er du trimestre",
                  "VOL-20261002T170500Z-c3bda6", "VOL-20261002T211837Z-be55c0"):
        assert value in text, value
    assert ("crypto_signal_intelligence.research.volatility_hourly", "fit_at") in f12.TEST.frozen_functions
