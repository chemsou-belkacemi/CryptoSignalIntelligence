"""Test en direct F4_TELEGRAM (forward/f4.py, forward/telegram_live.py) : lecture des deux sources, classement des
messages, rejeu à la main sur des bougies 1 min simulées, sortie et comparaisons, seuil de décision, route API.
SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward import f4, registry, telegram_live
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL, Costs
from crypto_signal_intelligence.forward.halal import HalalList

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)
T0 = pd.Timestamp("2026-10-05 10:00", tz="UTC")
SIGNAL = "PAIR: SOL/USDT\nENTRY 1: 100\nT1: 102\nT2: 104\nT3: 106\nSL: 98"
SHORT = "PAIR: SOL/USDT\nSHORT\nENTRY: 100\nT1: 95\nSL: 103"
ROBOT_ROW = {"signal_id": "-100:7", "source_chat_id": -100, "raw_text": "👑AL-MAHWASHI VIP👑\n" + SIGNAL,
             "received_at": "2026-10-05T10:00:30Z"}


def live(text=SIGNAL, received="2026-10-05T10:00:30+00:00", ident="robot:x", provider="X"):
    return telegram_live.LiveSignal(id=ident, provider=provider, received_at=received, text=text, source="robot")


# --- Sources ---------------------------------------------------------------------------------------------

def test_robot_file_and_bsm_inbox_are_read_and_deduplicated(settings, tmp_path):
    folder = telegram_live.live_dir(settings)
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(json.dumps([ROBOT_ROW, {"raw_text": "", "received_at": "x"}, {"signal_id": "-100:8", "source_chat_id": -100,
                                                                                            "raw_text": SIGNAL, "received_at": "2026-10-05T11:00:00Z"}]), encoding="utf-8")
    (folder / "b.json").write_text(json.dumps([ROBOT_ROW]), encoding="utf-8")          # doublon : lu une fois
    db = tmp_path / "signals.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE signals (id TEXT, scope TEXT, hash TEXT, source TEXT, external_id TEXT, received REAL, "
                     "raw TEXT, parsed TEXT, payload TEXT, source_timestamp REAL)")
        conn.execute("INSERT INTO signals VALUES ('s1','demo','h','telegram','ab:-100123:5',?,?,?,NULL,?)",
                     (T0.timestamp() + 120, SIGNAL, json.dumps({"errors": ["Message édité : vérifier"]}), T0.timestamp()))
        conn.execute("INSERT INTO signals VALUES ('s2','demo','h2','manual','',?,?,'{}',NULL,0)", (T0.timestamp() + 130, SIGNAL))
        conn.execute("INSERT INTO signals VALUES ('s0','demo','h0','telegram','ab:-1:1',?,?,'{}',NULL,0)", (T0.timestamp() - 86400 * 3, SIGNAL))
    settings.forward.bsm_inbox = str(db)
    items = telegram_live.read_all(settings, since=T0 - pd.Timedelta(days=1))
    assert [s.id for s in items] == ["robot:-100:7", "bsm:s1", "robot:-100:8"]
    assert items[0].provider == "ALMAHWASHI VIP" and items[2].provider == "chat -100"
    assert items[1].provider == "telegram -100123" and items[1].edited is True and items[1].chat == "-100123"
    assert items[1].received_at == (T0 + pd.Timedelta(seconds=120)).isoformat()
    assert telegram_live.read_bsm(tmp_path / "absent.sqlite3", since=T0) == []


def test_api_deposit_writes_a_file_in_the_drop_folder(settings):
    api = CsiApi(settings, now=lambda: datetime(2026, 10, 5, 12, tzinfo=UTC))
    out = api.dispatch("POST", "/telegram/live", {}, {"signals": [ROBOT_ROW]})
    assert out == {"deposited": 1, "readable": 1, "providers": ["ALMAHWASHI VIP"]}
    files = list(telegram_live.live_dir(settings).glob("*.json"))
    assert len(files) == 1 and files[0].name.startswith("20261005T120000Z-")
    with pytest.raises(Exception, match="liste non vide"):
        api.dispatch("POST", "/telegram/live", {}, {"signals": []})


# --- Règles pures ------------------------------------------------------------------------------------------

def test_classification_and_entry_time():
    frozen = {"SOLUSDT"}
    status, levels = f4.classify(live(), frozen)
    assert status == f4.DECISION and levels["stop"] == 98.0 and levels["targets"] == [102.0, 104.0, 106.0]
    assert f4.classify(live(SHORT), frozen)[0] == f4.SHORT
    assert f4.classify(live("bonjour tout le monde"), frozen)[0] == f4.UNREADABLE
    assert f4.classify(live(SIGNAL.replace("SOL", "ETH")), frozen)[0] == f4.OUT_OF_SCREEN
    assert f4.entry_time("2026-10-05T10:00:30+00:00") == pd.Timestamp("2026-10-05 10:02", tz="UTC")
    assert f4.entry_time("2026-10-05T10:00:00+00:00") == pd.Timestamp("2026-10-05 10:01", tz="UTC")


def test_placebo_offsets_are_reproducible_and_bounded():
    offsets = f4.placebo_offsets("robot:x")
    assert offsets == f4.placebo_offsets("robot:x") and len(set(offsets)) == 20
    assert min(offsets) >= f4.PLACEBO_MIN_MINUTES and max(offsets) <= f4.PLACEBO_MAX_MINUTES


ENTRY_BAR = T0 + pd.Timedelta(minutes=2)


def bars(rows, start=ENTRY_BAR):
    return pd.DataFrame([(start + k * f4.STEP, *r) for k, r in enumerate(rows)],
                        columns=["open_time", "open", "high", "low", "close"])


def test_trade_exit_and_same_moment_by_hand(monkeypatch):
    monkeypatch.setattr(f4, "cost_scenario", lambda symbol, scenario: f4.CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0))
    # Entrée 100 à l'ouverture ; TP1 (102) et TP2 (104) touchés, puis retour à l'entrée → stop à 100 (bougie 3).
    rows = [(100, 101, 99.5, 100.5), (100.5, 102.5, 100.2, 102.2), (102.2, 104.5, 102, 104), (104, 104, 99.5, 99.8)]
    rows += [(99.8, 100, 99.5, 99.8)] * 50
    frame = bars(rows)
    full = f4.run_trade(frame, stop=98.0, targets=[102.0, 104.0, 106.0], symbol="SOLUSDT", scenario=CENTRAL)
    assert full["complete"] and full["outcome"] == "TP2_PUIS_SL" and full["entry_price"] == 100.0
    assert full["r"] == pytest.approx((3 / 6 * 1 + 2 / 6 * 2) * 2 / 2, abs=1e-4)      # parts 3/6, 2/6 ; R en risque 2
    assert f4.exit_index(frame, stop=98.0, targets=[102.0, 104.0, 106.0], symbol="SOLUSDT", scenario=CENTRAL, full=full) == 3
    monkeypatch.setattr(f4, "costs_for", lambda symbol, scenario: Costs(fee=0.0, market=0.0))
    assert f4.same_moment_r(100.0, 99.8, 2.0, "SOLUSDT", CENTRAL) == pytest.approx(-0.1)
    flat = bars([(100, 100.5, 99.9, 100)] * 10)
    pending = f4.run_trade(flat, stop=98.0, targets=[102.0], symbol="SOLUSDT", scenario=CENTRAL)
    assert not pending["complete"] and pending["r"] is None


@pytest.mark.parametrize(("central", "adverse", "expected"), [
    ({"n": 40, "days": 12, "r_ci95": (0.1, 0.5), "placebo_excess_ci": (0.05, 0.4)}, {"r_ci95": (0.05, 0.4), "placebo_excess_ci": (0.01, 0.3)}, f4.ABOVE),
    ({"n": 40, "days": 12, "r_ci95": (-0.5, -0.1), "placebo_excess_ci": (-0.4, 0.1)}, {"r_ci95": (-0.6, -0.2), "placebo_excess_ci": (-0.5, 0.0)}, f4.BELOW),
    ({"n": 40, "days": 12, "r_ci95": (0.1, 0.5), "placebo_excess_ci": (-0.05, 0.4)}, {"r_ci95": (0.05, 0.4), "placebo_excess_ci": (0.01, 0.3)}, f4.NOT_SHOWN),
    ({"n": 29, "days": 12, "r_ci95": (0.1, 0.5), "placebo_excess_ci": (0.05, 0.4)}, {"r_ci95": (0.05, 0.4), "placebo_excess_ci": (0.01, 0.3)}, f4.INSUFFICIENT),
    ({"n": 40, "days": 9, "r_ci95": (0.1, 0.5), "placebo_excess_ci": (0.05, 0.4)}, {"r_ci95": (0.05, 0.4), "placebo_excess_ci": (0.01, 0.3)}, f4.INSUFFICIENT),
])
def test_f4_decision_threshold(central, adverse, expected):
    assert f4.verdict({CENTRAL: central, ADVERSE: adverse}, ended=True) == expected
    assert f4.verdict({CENTRAL: central, ADVERSE: adverse}, ended=False) == f4.RUNNING


# --- Bout en bout ------------------------------------------------------------------------------------------

class FakeRest:
    """Bougies 1 min simulées : prix déterministe, +0,5 % par heure à partir de T0 (une tendance : les objectifs
    sont atteints, les placebos antérieurs aussi)."""

    def __init__(self):
        self.calls = 0

    @staticmethod
    def price(when: pd.Timestamp) -> float:
        return 100.0 * (1 + 0.005 * (when - T0).total_seconds() / 3600)

    def get_json(self, path, params):
        self.calls += 1
        assert path == "/api/v3/klines" and params["interval"] == "1m"
        start = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        end = pd.Timestamp(params["endTime"], unit="ms", tz="UTC")
        rows = []
        when = start
        while when <= end and len(rows) < params["limit"]:
            p = self.price(when)
            ms = int(when.timestamp() * 1000)
            rows.append([ms, str(p), str(p * 1.0005), str(p * 0.9995), str(p * 1.0002), "1", ms + 59_999, "100", 10, "0.5", "50", "0"])
            when += pd.Timedelta(minutes=1)
        return rows


def small_horizon(monkeypatch):
    monkeypatch.setattr(f4, "HOLD_BARS", 240)                           # 4 h au lieu de 30 jours
    monkeypatch.setattr(f4, "PLACEBO_MIN_MINUTES", 60)
    monkeypatch.setattr(f4, "PLACEBO_MAX_MINUTES", 600)
    monkeypatch.setattr(f4, "GAP_AFTER", pd.Timedelta(hours=1))


def test_f4_end_to_end(settings, monkeypatch):
    small_horizon(monkeypatch)
    start = registry.start(settings, f4.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    folder = telegram_live.live_dir(settings)
    folder.mkdir(parents=True)
    (folder / "live.json").write_text(json.dumps([ROBOT_ROW, {"signal_id": "-100:9", "source_chat_id": -100, "raw_text": SHORT,
                                                              "received_at": "2026-10-05T10:05:00Z"}]), encoding="utf-8")
    journal = registry.journal_for(settings, f4.TEST_ID)
    now = datetime(2026, 10, 5, 10, 10, tzinfo=UTC)
    assert f4.record_decisions(settings, journal, start, now=now) == {"decisions": 1, "counted": 1}
    assert f4.record_decisions(settings, journal, start, now=now) == {"decisions": 0, "counted": 0}
    decision = next(journal.entries({f4.DECISION}))["data"]
    assert decision["symbol"] == "SOLUSDT" and decision["entry_at"] == "2026-10-05T10:02:00+00:00"
    assert len(decision["placebo_minutes"]) == 20 and decision["parser_code"]
    counted = next(journal.entries({f4.COUNTED}))["data"]
    assert counted["status"] == f4.SHORT
    rest, store = FakeRest(), CandleStore(settings.data_dir)
    assert f4.resolve(settings, journal, now=now, rest=rest, store=store) == {}        # trop tôt
    later = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert f4.resolve(settings, journal, now=later, rest=rest, store=store) == {"RESOLU": 1}
    assert f4.resolve(settings, journal, now=later, rest=rest, store=store) == {}
    result = next(journal.entries({f4.RESOLUTION}))["data"]
    central = result["results"][CENTRAL]
    assert result["status"] == "RESOLU" and central["outcome"].startswith("TP") and central["r"] > 0
    assert len(central["placebos"]) == 20 and central["placebo_mean"] is not None and central["excess"] is not None
    assert central["same_moment_r"] > 0 and central["hold_minutes"] > 0
    assert store.last_open_time("SOLUSDT", "1m") is not None                        # bougies conservées
    out = f4.stats(journal, start, now=later)
    whole = out["providers"][f4.ALL]
    assert out["decisions"] == 1 and out["counted"] == 1 and whole["resolved"] == 1 and whole["halal_share"] == 0.5
    assert out["by_status"][f4.SHORT] == 1 and "ALMAHWASHI VIP" in out["providers"]
    assert whole["verdict"] == f4.RUNNING and f4.finalize(journal, start, now=later) is None
    assert journal.verify()["ok"]


def test_resolution_marks_invalid_or_already_played_entries(settings, monkeypatch):
    small_horizon(monkeypatch)
    start = registry.start(settings, f4.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    folder = telegram_live.live_dir(settings)
    folder.mkdir(parents=True)
    # Niveaux lisibles (97 < 99 < 99,5) mais le prix à l'entrée (≈ 100,2) dépasse déjà le premier objectif.
    played = dict(ROBOT_ROW, signal_id="-100:10", raw_text="PAIR: SOL/USDT\nENTRY 1: 99\nT1: 99.5\nT2: 101\nSL: 97")
    invalid = dict(ROBOT_ROW, signal_id="-100:11", raw_text=SIGNAL.replace("SL: 98", "SL: 100.5").replace("ENTRY 1: 100", "ENTRY 1: 101"))
    (folder / "live.json").write_text(json.dumps([played, invalid]), encoding="utf-8")
    journal = registry.journal_for(settings, f4.TEST_ID)
    now = datetime(2026, 10, 5, 10, 10, tzinfo=UTC)
    assert f4.record_decisions(settings, journal, start, now=now)["decisions"] == 2
    counts = f4.resolve(settings, journal, now=datetime(2026, 10, 5, 15, tzinfo=UTC), rest=FakeRest(), store=CandleStore(settings.data_dir))
    assert counts == {f4.PLAYED: 1, f4.INVALID: 1}
    out = f4.stats(journal, start, now=now)
    assert out["unplayable"][f4.PLAYED] == 1 and out["unplayable"][f4.INVALID] == 1 and out["pending"] == 0


def test_f4_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f4.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("60 s", "43 200 bougies", "20 achats", "30 signaux résolus", "1 − 0,05/2", "graine 20261005",
                  "blocs de 7 jours", "84 jours", "POST /telegram/live", "SHORT_OU_LEVIER"):
        assert value in text, value
    assert f4.TEST.params["seed"] == 20261005 and f4.HOLD_BARS == 43_200 and f4.PLACEBOS == 20
