"""Test en direct F11_SELL_PRESSURE_VETO (forward/f11.py) : part des achats au marché à la main, seuils figés, contrôle
quotidien, événements, résolution, verdict « veto justifié », pré-inscription. SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f11, registry
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)
DAY = pd.Timestamp("2026-10-10", tz="UTC")


def candles(share: float, *, hours: int = 24, end: pd.Timestamp = DAY) -> pd.DataFrame:
    """`hours` bougies 1 h de la veille de `end` (à partir de 00:00) plus deux bougies de l'avant-veille (ignorées),
    volume 1 000 USDT par heure."""
    yesterday = end - pd.Timedelta(days=1)
    index = pd.date_range(yesterday - pd.Timedelta(hours=2), periods=2, freq="h").append(pd.date_range(yesterday, periods=hours, freq="h"))
    return pd.DataFrame({"open_time": index, "quote_volume": 1000.0, "taker_buy_quote_volume": [0.9 * 1000.0] * 2 + [share * 1000.0] * hours})


def test_rules_by_hand():
    assert f11.taker_share(candles(0.4), DAY) == {"share": pytest.approx(0.4), "hours": 24}
    assert f11.taker_share(candles(0.4, hours=19), DAY) is None                                  # journée incomplète
    assert f11.taker_share(candles(0.4).assign(quote_volume=0.0), DAY) is None                   # volume nul
    assert f11.triggered("BTCUSDT", 0.457) and not f11.triggered("BTCUSDT", 0.4571)                # seuil 0,4570
    assert f11.triggered("ETCUSDT", 0.43) and not f11.triggered("ETCUSDT", 0.44) and not f11.triggered("INCONNUUSDT", 0.1)
    assert not f11.triggered("BTCUSDT", None)
    days = f11.placebo_days("x")
    assert len(set(days)) == 20 and min(days) >= 1 and max(days) <= 30
    assert len(f11.THRESHOLDS) == 16 and all(0.4 < v < 0.5 for v in f11.THRESHOLDS.values())


def test_verdict_reads_a_negative_excess_as_the_expected_answer():
    rows = {h: {"n": 40, "excess_ci": [-0.02, -0.005]} for h in f11.HORIZONS}
    assert f11.verdict({CENTRAL: rows, ADVERSE: rows}, ended=True) == {"24h": f11.NEGATIVE, "168h": f11.NEGATIVE}
    rows = {h: {"n": 40, "excess_ci": [0.001, 0.02]} for h in f11.HORIZONS}
    assert f11.verdict({CENTRAL: rows, ADVERSE: rows}, ended=True) == {"24h": f11.POSITIVE, "168h": f11.POSITIVE}
    rows = {h: {"n": 10, "excess_ci": [-0.02, -0.005]} for h in f11.HORIZONS}
    assert set(f11.verdict({CENTRAL: rows, ADVERSE: rows}, ended=True).values()) == {f11.INSUFFICIENT}
    assert set(f11.verdict({}, ended=False).values()) == {f11.RUNNING}


def test_poll_waits_for_0010_checks_once_per_day_and_records_events(settings, monkeypatch):
    start = registry.start(settings, f11.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f11.TEST_ID)
    shares = {"BTCUSDT": 0.40, "ETHUSDT": 0.52}
    monkeypatch.setattr(f11, "hourly_candles", lambda settings_, symbol, until: candles(shares[symbol], end=until)
                        if symbol in shares else candles(0.3, hours=10, end=until))
    assert f11.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 5, tzinfo=UTC)) == {"checks": 0}      # avant 00:10
    now = datetime(2026, 10, 10, 0, 25, tzinfo=UTC)
    assert f11.poll(settings, journal, start, now=now) == {"checks": 1, "events": 1}
    assert f11.poll(settings, journal, start, now=now) == {"checks": 0}
    event = next(journal.entries({f11.EVENT}))["data"]
    assert event["symbol"] == "BTCUSDT" and event["share"] == 0.4 and event["threshold"] == 0.457 and len(event["placebo_entries"]) == 20
    check = next(journal.entries({f11.CHECK}))["data"]
    assert check["evaluable"] == 2 and check["values"]["SOLUSDT"] == {"evaluable": False, "triggered": False}
    assert check["values"]["ETHUSDT"]["triggered"] is False and check["values"]["ETHUSDT"]["share"] == 0.52
    out = f11.stats(journal, start, now=now)
    assert out["events"] == 1 and out["by_asset"] == {"BTCUSDT": 1} and out["pending"] == 1 and set(out["verdicts"].values()) == {f11.RUNNING}


class FakeRest:
    def get_json(self, path, params):
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        price = 100.0 * (1 + 0.0002 * (when - DAY).total_seconds() / 3600)
        return [[int(when.timestamp() * 1000), str(price), str(price), str(price), str(price)]]


def test_resolution_and_excess(settings, monkeypatch):
    start = registry.start(settings, f11.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f11.TEST_ID)
    monkeypatch.setattr(f11, "hourly_candles", lambda settings_, symbol, until: candles(0.3, end=until))
    f11.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 25, tzinfo=UTC))
    assert f11.resolve(settings, journal, now=datetime(2026, 10, 12, tzinfo=UTC), rest=FakeRest()) == {}
    assert f11.resolve(settings, journal, now=datetime(2026, 10, 17, 0, 30, tzinfo=UTC), rest=FakeRest()) == {"RESOLU": 3}
    result = next(journal.entries({f11.RESOLUTION}))["data"]
    central = result["results"][CENTRAL]["168h"]
    assert central["excess"] == pytest.approx(central["event_r"] - central["placebo_mean"], abs=1e-6) and len(central["placebos"]) == 20
    out = f11.stats(journal, start, now=datetime(2026, 10, 17, 1, tzinfo=UTC))
    assert out["scenarios"][CENTRAL]["168h"]["n"] == 3 and out["pending"] == 0 and journal.verify()["ok"]


def test_f11_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f11.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("10e centile", "BTC 0,4570", "ETC 0,4313", "30 événements", "graine 20261011", "24 h et 168 h", "1 à 30 jours",
                  "SCREEN-20261002T165546Z-0994a2", "après coup", "EXCES_NEGATIF"):
        assert value in text, value
    assert f11.TEST.params["expected"] == "EXCES_NEGATIF" and f11.TEST.params["thresholds"]["ETCUSDT"] == 0.4313
