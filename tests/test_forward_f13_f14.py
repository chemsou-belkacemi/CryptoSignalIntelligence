"""Tests en direct F13 (K2 sur les 24 autres paires) et F14 (K2 avec niveaux par la volatilité prévue) : règles à la
main, contrôle quotidien, résolution, pré-inscription. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f10, f13, f14, registry
from crypto_signal_intelligence.forward.costs import CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.research.universe import RESEARCH_UNIVERSE

from .conftest import PROJECT
from .test_forward_f10 import crafted_bars

DAY = pd.Timestamp("2026-10-10", tz="UTC")
HALAL = HalalList(("APTUSDT", "DOGEUSDT", "BTCUSDT", "ETHUSDT"), {}, "a" * 64, "b" * 64)


def test_f13_symbols_are_the_research_universe_minus_the_configuration(settings):
    assert len(f13.SYMBOLS) == 24 and len(set(f13.SYMBOLS)) == 24
    assert set(f13.SYMBOLS) == set(RESEARCH_UNIVERSE) - set(settings.data.symbols)
    assert set(f14.SYMBOLS) == set(RESEARCH_UNIVERSE) and tuple(settings.data.symbols) == f14.CONFIG_SYMBOLS


def test_f13_polls_its_own_pairs_with_the_f10_rules(settings, monkeypatch):
    start = registry.start(settings, f13.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f13.TEST_ID)
    monkeypatch.setattr(f10, "daily_bars", lambda settings_, symbol, until: crafted_bars(breakout=symbol == "APTUSDT", end=until))
    assert f13.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 25, tzinfo=UTC)) == {"checks": 1, "events": 1}
    check = next(journal.entries({f13.CHECK}))["data"]
    assert set(check["values"]) == {"APTUSDT", "DOGEUSDT"}                    # paires F13 admises seulement (pas BTC, ETH)
    event = next(journal.entries({f13.EVENT}))["data"]
    assert event["symbol"] == "APTUSDT" and event["resistance"] == 110.0 and len(event["placebo_entries"]) == 20
    assert f13.stats(journal, start, now=datetime(2026, 10, 10, 1, tzinfo=UTC))["events"] == 1


def test_f14_play_by_hand():
    bars = pd.DataFrame({"open": [100.0, 101.0], "high": [102.0, 116.0], "low": [99.0, 100.5]})
    assert f14.play(100.0, 0.10, bars, 104.0) == ("TP", pytest.approx(115.0))                # objectif 1,5 σ̂ = 115 touché
    falling = pd.DataFrame({"open": [100.0, 95.0], "high": [101.0, 96.0], "low": [96.0, 88.0]})
    assert f14.play(100.0, 0.10, falling, 99.0) == ("SL", pytest.approx(90.0))               # stop 1,0 σ̂ = 90
    both = pd.DataFrame({"open": [100.0], "high": [120.0], "low": [85.0]})
    assert f14.play(100.0, 0.10, both, 99.0) == ("SL", pytest.approx(90.0))                  # les deux : stop d'abord
    above = pd.DataFrame({"open": [120.0], "high": [121.0], "low": [119.0]})
    assert f14.play(100.0, 0.10, above, 99.0) == ("TP", pytest.approx(115.0))   # objectif en gap : prix limite
    gap = pd.DataFrame({"open": [88.0], "high": [89.0], "low": [87.0]})
    assert f14.play(100.0, 0.10, gap, 99.0) == ("SL", 88.0)                   # ouverture sous le stop
    flat = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0]})
    assert f14.play(100.0, 0.10, flat, 103.0) == ("TEMPS", 103.0)
    r = f14.r_of(100.0, 115.0, "TP", 0.10, "BTCUSDT", CENTRAL)
    assert r == pytest.approx((115 * (1 - 0.00075) / (100 * 1.0002 * 1.00075) - 1) / 0.10)
    assert f14.sigma_for({"pairs": {"X": {"available": True, "horizons": {"3": {"move_pct": 4.2}}}}}, "X") == pytest.approx(0.042)
    assert f14.sigma_for({"pairs": {"X": {"available": False}}}, "X") is None


def write_forecast(settings, origin: str, sigma_pct: float = 10.0) -> None:
    path = settings.root / "state" / "volatility.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pairs = {s: {"available": True, "horizons": {"3": {"move_pct": sigma_pct}}} for s in f14.SYMBOLS if s != "DOGEUSDT"}
    path.write_text(json.dumps({"origin": origin, "pairs": pairs}), encoding="utf-8")


def test_f14_waits_for_the_forecast_and_marks_events_without_one(settings, monkeypatch):
    start = registry.start(settings, f14.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f14.TEST_ID)
    monkeypatch.setattr(f10, "daily_bars", lambda settings_, symbol, until: crafted_bars(breakout=symbol in {"APTUSDT", "DOGEUSDT"}, end=until))
    now = datetime(2026, 10, 10, 0, 25, tzinfo=UTC)
    write_forecast(settings, "2026-10-09T00:00:00+00:00")
    assert f14.poll(settings, journal, start, now=now)["checks"] == 0          # prévision de la veille : on attend
    write_forecast(settings, "2026-10-10T00:00:00+00:00")
    assert f14.poll(settings, journal, start, now=now) == {"checks": 1, "events": 2}
    events = {e["data"]["symbol"]: e["data"] for e in journal.entries({f14.EVENT})}
    assert events["APTUSDT"]["playable"] and events["APTUSDT"]["sigma"] == pytest.approx(0.10)
    assert not events["DOGEUSDT"]["playable"]
    out = f14.stats(journal, start, now=now)
    assert out["decisions"] == 1 and out["without_forecast"] == 1 and set(out["verdicts"].values()) == {f14.RUNNING}
    late = datetime(2026, 10, 11, 23, 30, tzinfo=UTC)                         # prévision du jour jamais écrite
    assert f14.poll(settings, journal, start, now=datetime(2026, 10, 11, 12, tzinfo=UTC))["checks"] == 0
    assert f14.poll(settings, journal, start, now=late) == {"checks": 1, "events": 2}
    latest = [e["data"] for e in journal.entries({f14.EVENT}) if e["data"]["day"] == "2026-10-11"]
    assert len(latest) == 2 and not any(e["playable"] for e in latest)


class FakeRest:
    def get_json(self, path, params):
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        return [[int(when.timestamp() * 1000), "100", "100", "100", "100"]]


def test_f14_resolution_compares_to_placebos_and_to_the_timed_exit(settings, monkeypatch):
    start = registry.start(settings, f14.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f14.TEST_ID)
    monkeypatch.setattr(f10, "daily_bars", lambda settings_, symbol, until: crafted_bars(breakout=symbol == "APTUSDT", end=until))
    write_forecast(settings, "2026-10-10T00:00:00+00:00")
    f14.poll(settings, journal, start, now=datetime(2026, 10, 10, 0, 25, tzinfo=UTC))
    event_entry = pd.Timestamp(next(journal.entries({f14.EVENT}))["data"]["entry_at"])

    def hours(settings_, symbol, entry):
        n = int(((entry + f14.HOLD) - entry.ceil("h")) / pd.Timedelta(hours=1))
        index = pd.date_range(entry.ceil("h"), periods=n, freq="h", tz="UTC")
        high = np.full(n, 101.0)
        if entry == event_entry:
            high[10] = 116.0                                                     # l'événement touche son objectif
        return pd.DataFrame({"open_time": index, "open": 100.0, "high": high, "low": 99.0})

    monkeypatch.setattr(f14, "hourly_after", hours)
    full = hours(settings, "APTUSDT", event_entry)
    assert f14.contiguous(full, event_entry) and not f14.contiguous(full.iloc[:-1], event_entry)
    assert not f14.contiguous(full.iloc[1:], event_entry) and not f14.contiguous(full.drop(index=50), event_entry)
    assert f14.resolve(settings, journal, now=datetime(2026, 10, 12, tzinfo=UTC), rest=FakeRest()) == {}
    assert f14.resolve(settings, journal, now=datetime(2026, 10, 17, 1, tzinfo=UTC), rest=FakeRest()) == {"RESOLU": 1}
    result = next(journal.entries({f14.RESOLUTION}))["data"]["results"][CENTRAL]
    assert result["outcome"] == "TP" and result["event_r"] > 1.3 and result["vs_placebos"] > 1.3 and result["vs_sortie_a_date"] > 1.3
    out = f14.stats(journal, start, now=datetime(2026, 10, 17, 2, tzinfo=UTC))
    assert out["scenarios"][CENTRAL]["vs_placebos"]["n"] == 1 and out["pending"] == 0 and journal.verify()["ok"]


@pytest.mark.parametrize("module", [f13, f14])
def test_preregistration_is_complete(module):
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), module.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    assert "30 événements résolus" in text and "1 à 30 jours" in text


def test_f14_section_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f14.TEST_ID)
    for value in ("1,0 σ̂", "1,5 σ̂", "168 h", "graine 20261014", "sans prévision"):
        assert value in text, value
    assert (f14.STOP_SIGMA, f14.TARGET_SIGMA, f14.SEED) == (1.0, 1.5, 20261014)
