"""Relevé des écarts entre bourses (forward/spreads.py) : écart brut et net à la main, cadence, résumé. SYNTHÉTIQUE,
sans réseau."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crypto_signal_intelligence.forward import spreads


def test_gap_by_hand():
    venues = {"binance": {"BTC": {"bid": 99.9, "ask": 100.0}}, "kraken": {"BTC": {"bid": 100.5, "ask": 100.6}},
              "okx": {"BTC": {"bid": None, "ask": 100.0}}}
    out = spreads.gaps(venues, asset="BTC")
    assert set(out) == {"kraken"}                                              # OKX incomplet : ignoré
    assert out["kraken"]["gross_bps"] == pytest.approx(50.0) and out["kraken"]["direction"] == "achat Binance"
    assert out["kraken"]["net_bps"] == pytest.approx(50.0 - (7.5 + 40.0))


def test_record_cadence_and_summary(settings, monkeypatch):
    fake = {"time": "", "usdt_usd": 1.0, "venues": {}, "korea_premium_bps": {"BTC": 10.0},
            "gaps": {"BTC": {"kraken": {"gross_bps": 1.0, "net_bps": -46.5, "direction": "achat Binance"}}}, "errors": {}}
    monkeypatch.setattr(spreads, "snapshot", lambda settings_, now: fake | {"time": now.isoformat()})
    assert spreads.maybe_record(settings, now=datetime(2026, 10, 3, 10, 0, tzinfo=UTC)) == {"errors": 0}
    assert spreads.maybe_record(settings, now=datetime(2026, 10, 3, 10, 5, tzinfo=UTC)) is None    # moins de 10 min
    assert spreads.maybe_record(settings, now=datetime(2026, 10, 3, 10, 11, tzinfo=UTC)) == {"errors": 0}
    out = spreads.summary(settings)
    assert out["snapshots"] == 2 and out["pairs"]["kraken/BTC"]["net_positive_share"] == 0.0 and out["korea"]["BTC"]["mean_bps"] == 10.0
