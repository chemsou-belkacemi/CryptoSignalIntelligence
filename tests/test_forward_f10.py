"""Test en direct F10_PIVOT_BREAK_1D (forward/f10.py) : lecture du jour à la main (fonctions gelées du criblage K),
contrôle quotidien, événements, résolution, pré-inscription. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f10, registry
from crypto_signal_intelligence.forward.costs import CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)
DAY = pd.Timestamp("2026-10-10", tz="UTC")


def crafted_bars(days: int = 120, *, breakout: bool = True, end: pd.Timestamp = DAY) -> pd.DataFrame:
    """Journées plates (clôture 100, plus haut 101) ; pivot haut à 110 en position days − 20 ; dernière journée (la veille
    de `end`) qui clôture à 111 au-dessus de ce pivot (ou à 100 sans cassure)."""
    index = pd.date_range(end - pd.Timedelta(days=days), periods=days, freq="D", tz="UTC")
    close = np.full(days, 100.0)
    high, low = np.full(days, 101.0), np.full(days, 99.0)
    high[days - 20] = 110.0
    if breakout:
        close[-1], high[-1] = 111.0, 112.0
    return pd.DataFrame({"open_time": index, "open": 100.0, "high": high, "low": low, "close": close,
                         "available_at": index + pd.Timedelta(days=1)})


def test_rules_by_hand():
    read = f10.evaluate(crafted_bars(), DAY)
    assert read == {"triggered": True, "resistance": 110.0, "close": 111.0, "bars": 120}
    assert f10.evaluate(crafted_bars(breakout=False), DAY)["triggered"] is False
    assert f10.evaluate(crafted_bars(days=100), DAY) is None                                  # moins de 110 journées
    assert f10.evaluate(crafted_bars(), DAY + pd.Timedelta(days=1)) is None                  # la veille manque
    days = f10.placebo_days("x")
    assert len(set(days)) == 20 and min(days) >= 1 and max(days) <= 30
    assert f10.MIN_EVENTS == 30 and f10.HORIZONS["168h"] == pd.Timedelta(days=7)


def test_poll_waits_for_0010_checks_once_per_day_and_records_events(settings, monkeypatch):
    start = registry.start(settings, f10.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f10.TEST_ID)
    monkeypatch.setattr(f10, "daily_bars", lambda settings_, symbol, until: crafted_bars(breakout=symbol == "BTCUSDT", end=until)
                        if symbol != "SOLUSDT" else crafted_bars(days=50, end=until))
    assert f10.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 5, tzinfo=UTC)) == {"checks": 0}      # avant 00:10
    now = datetime(2026, 10, 10, 0, 25, tzinfo=UTC)
    assert f10.poll(settings, journal, start, now=now) == {"checks": 1, "events": 1}
    assert f10.poll(settings, journal, start, now=now) == {"checks": 0}
    event = next(journal.entries({f10.EVENT}))["data"]
    assert event["symbol"] == "BTCUSDT" and event["resistance"] == 110.0 and len(event["placebo_entries"]) == 20
    check = next(journal.entries({f10.CHECK}))["data"]
    assert check["evaluable"] == 2 and check["values"]["SOLUSDT"] == {"evaluable": False, "bars": 50, "triggered": False}
    assert check["values"]["ETHUSDT"]["triggered"] is False
    out = f10.stats(journal, start, now=now)
    assert out["events"] == 1 and out["by_asset"] == {"BTCUSDT": 1} and out["pending"] == 1 and set(out["verdicts"].values()) == {f10.RUNNING}


class FakeRest:
    def get_json(self, path, params):
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        price = 100.0 * (1 + 0.0002 * (when - DAY).total_seconds() / 3600)
        return [[int(when.timestamp() * 1000), str(price), str(price), str(price), str(price)]]


def test_resolution_and_excess(settings, monkeypatch):
    start = registry.start(settings, f10.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f10.TEST_ID)
    monkeypatch.setattr(f10, "daily_bars", lambda settings_, symbol, until: crafted_bars(end=until))
    f10.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 25, tzinfo=UTC))
    assert f10.resolve(settings, journal, now=datetime(2026, 10, 12, tzinfo=UTC), rest=FakeRest()) == {}
    assert f10.resolve(settings, journal, now=datetime(2026, 10, 17, 0, 30, tzinfo=UTC), rest=FakeRest()) == {"RESOLU": 3}
    result = next(journal.entries({f10.RESOLUTION}))["data"]
    central = result["results"][CENTRAL]["168h"]
    assert central["excess"] == pytest.approx(central["event_r"] - central["placebo_mean"], abs=1e-6) and len(central["placebos"]) == 20
    out = f10.stats(journal, start, now=datetime(2026, 10, 17, 1, tzinfo=UTC))
    assert out["scenarios"][CENTRAL]["168h"]["n"] == 3 and out["pending"] == 0 and journal.verify()["ok"]


def test_f10_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f10.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("k = 3", "110 journées", "30 événements", "graine 20261010", "24 h et 168 h", "1 à 30 jours",
                  "SCREEN-20261002T170108Z-e89978", "un seul événement par niveau"):
        assert value in text, value
    assert f10.TEST.params["screen_run"] == "SCREEN-20261002T170108Z-e89978"
    assert ("crypto_signal_intelligence.research.pivot_screen", "events_of") in f10.TEST.frozen_functions
