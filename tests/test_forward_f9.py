"""Test en direct F9_OI_FLUSH (forward/f9.py) : règles à la main, contrôle quotidien, événements, résolution,
pré-inscription. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f9, registry
from crypto_signal_intelligence.forward.costs import CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)
DAY = pd.Timestamp("2026-10-10", tz="UTC")


def record(now_oi, old_oi):
    ms = DAY.timestamp() * 1000
    return {"open_interest": [[ms - 25 * 3600 * 1000, str(old_oi), "0"], [ms - 24 * 3600 * 1000, str(old_oi), "0"],
                              [ms - 3600 * 1000, str(now_oi), "0"], [ms + 3600 * 1000, "1", "0"]]}   # valeur future ignorée


def closes(last, past):
    return pd.Series({DAY - pd.Timedelta(days=1): last, DAY - pd.Timedelta(days=2): past})


def test_rules_by_hand():
    assert f9.oi_change_24h(record(95.0, 100.0), DAY) == pytest.approx(-0.05)
    assert f9.oi_change_24h({"open_interest": []}, DAY) is None
    assert f9.spot_change_24h(closes(99.0, 100.0), DAY) == pytest.approx(-0.01)
    assert f9.triggered("BTCUSDT", -0.0441, -0.01) and not f9.triggered("BTCUSDT", -0.044, -0.01)      # seuil −4,41 %
    assert not f9.triggered("BTCUSDT", -0.05, 0.0) and not f9.triggered("INCONNUUSDT", -0.5, -0.1)
    assert f9.triggered("HBARUSDT", -0.09, -0.001) and not f9.triggered("HBARUSDT", -0.08, -0.001)
    days = f9.placebo_days("x")
    assert len(set(days)) == 20 and min(days) >= 1 and max(days) <= 30
    assert len(f9.THRESHOLDS) == 16 and all(-0.1 < v < 0 for v in f9.THRESHOLDS.values())


def test_poll_checks_once_per_day_and_records_events(settings, monkeypatch):
    start = registry.start(settings, f9.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f9.TEST_ID)
    records = {"BTCUSDT": record(95.0, 100.0), "ETHUSDT": record(99.0, 100.0), "SOLUSDT": record(90.0, 100.0)}
    monkeypatch.setattr(f9, "derivatives_records", lambda settings_, day, symbols: {s: r for s, r in records.items() if s in symbols})
    monkeypatch.setattr(f9, "daily_closes", lambda settings_, symbol, until: closes(99.0, 100.0) if symbol != "SOLUSDT" else closes(101.0, 100.0))
    now = datetime(2026, 10, 10, 0, 25, tzinfo=UTC)
    assert f9.poll(settings, journal, start, now=now) == {"checks": 1, "events": 1}           # BTC : OI −5 % et Spot −1 % ; SOL : Spot en hausse
    assert f9.poll(settings, journal, start, now=now) == {"checks": 0}
    event = next(journal.entries({f9.EVENT}))["data"]
    assert event["symbol"] == "BTCUSDT" and event["threshold"] == -0.0441 and len(event["placebo_entries"]) == 20
    check = next(journal.entries({f9.CHECK}))["data"]
    assert check["values"]["SOLUSDT"]["triggered"] is False and check["values"]["ETHUSDT"]["oi_change_24h"] == pytest.approx(-0.01)
    monkeypatch.setattr(f9, "derivatives_records", lambda settings_, day, symbols: {})
    assert f9.poll(settings, journal, start, now=datetime(2026, 10, 11, 0, 25, tzinfo=UTC))["waiting"].startswith("relevé")
    out = f9.stats(journal, start, now=now)
    assert out["events"] == 1 and out["by_asset"] == {"BTCUSDT": 1} and out["pending"] == 1 and set(out["verdicts"].values()) == {f9.RUNNING}


class FakeRest:
    def get_json(self, path, params):
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        price = 100.0 * (1 + 0.0002 * (when - DAY).total_seconds() / 3600)
        return [[int(when.timestamp() * 1000), str(price), str(price), str(price), str(price)]]


def test_resolution_and_excess(settings, monkeypatch):
    start = registry.start(settings, f9.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f9.TEST_ID)
    monkeypatch.setattr(f9, "derivatives_records", lambda settings_, day, symbols: {"BTCUSDT": record(90.0, 100.0), "ETHUSDT": record(90.0, 100.0)})
    monkeypatch.setattr(f9, "daily_closes", lambda settings_, symbol, until: closes(99.0, 100.0))
    f9.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 25, tzinfo=UTC))
    assert f9.resolve(settings, journal, now=datetime(2026, 10, 11, tzinfo=UTC), rest=FakeRest()) == {}
    assert f9.resolve(settings, journal, now=datetime(2026, 10, 13, 0, 30, tzinfo=UTC), rest=FakeRest()) == {"RESOLU": 2}
    result = next(journal.entries({f9.RESOLUTION}))["data"]
    central = result["results"][CENTRAL]["24h"]
    assert central["excess"] == pytest.approx(central["event_r"] - central["placebo_mean"], abs=1e-6) and len(central["placebos"]) == 20
    out = f9.stats(journal, start, now=datetime(2026, 10, 13, 1, tzinfo=UTC))
    assert out["scenarios"][CENTRAL]["72h"]["n"] == 2 and out["pending"] == 0 and journal.verify()["ok"]


def test_f9_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f9.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("10e centile", "BTC −4,41 %", "HBAR −8,44 %", "30 événements", "graine 20261008", "24 h et 72 h", "1 à 30 jours"):
        assert value in text, value
    assert f9.MIN_EVENTS == 30 and f9.TEST.params["thresholds"]["DOTUSDT"] == -0.0397
