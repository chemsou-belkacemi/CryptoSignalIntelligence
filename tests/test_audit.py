"""Bilan mesuré d'un groupe Telegram sur son historique (external/audit.py) : lecture de l'export, statuts,
trois conventions calculées à la main, causalité de l'entrée, seuils de conclusion. Bougies SYNTHÉTIQUES."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.http import HttpError
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.external import audit as au
from crypto_signal_intelligence.external.parser import content_hash, parse
from crypto_signal_intelligence.external.registry import replay

from .conftest import canonical

T0 = pd.Timestamp("2026-03-02 00:00", tz="UTC")
STEP = pd.Timedelta(minutes=15)
NOW = datetime(2026, 6, 1, tzinfo=UTC)
FEE, MARKET = 0.001, 0.0003                      # coûts centraux : frais 10 pb ; glissement 2 pb + demi-écart 1 pb
SIGNAL = """👑 LEGEND TRADING INDICATOR 👑
───────────────────
#ABC/USDT
📍 Entry1: 100
🎯 TP1: 104 (4.0%)
🎯 TP2: 108 (8.0%)
🛑 Stop: 95 (4h) (-5.0%)
"""


def bars(rows: list[tuple[float, float, float, float]], start: pd.Timestamp = T0) -> pd.DataFrame:
    return pd.DataFrame([(start + k * STEP, *row) for k, row in enumerate(rows)],
                        columns=["open_time", "open", "high", "low", "close"])


def flat(count: int, price: float) -> list[tuple[float, float, float, float]]:
    return [(price, price, price, price)] * count


PUBLISHED = T0 + pd.Timedelta(hours=10)


def item(text: str = SIGNAL, at: pd.Timestamp = PUBLISHED, **kwargs) -> au.HistoryItem:
    return au.HistoryItem(text=text, received_at=at.to_pydatetime(), **kwargs)


def run(settings, frame: pd.DataFrame | None, items: list[au.HistoryItem] | None = None, **kwargs) -> au.AuditReport:
    return au.audit(settings, items or [item()], now=NOW, bars_for=lambda symbol, start, end: frame, **kwargs)


def r_of(fills: list[tuple[float, float, bool]], entry: float = 100.0, risk: float = 5.0) -> float:
    """R net à la main : (part, prix, vente au marché) ; frais à l'achat et à chaque vente."""
    return sum(w * (p * (1 - MARKET if market else 1) * (1 - FEE) - entry * (1 + FEE)) for w, p, market in fills) / risk


# --- Lecture ---------------------------------------------------------------------------------------------

def test_telegram_export_is_read_with_its_exact_times_and_groups():
    export = {"name": "Groupe A", "type": "public_channel", "id": 1, "messages": [
        {"id": 1, "type": "service", "date_unixtime": "1772445600", "text": "Le groupe a épinglé un message"},
        {"id": 2, "type": "message", "date": "2026-03-02T11:00:00", "date_unixtime": "1772445600", "text": "bonjour"},
        {"id": 3, "type": "message", "date_unixtime": "1772449200", "edited_unixtime": "1772452800",
         "text": ["#ABC/USDT\n", {"type": "bold", "text": "Entry1: 100"}, "\nTP1: 104\nStop: 95"]},
        {"id": 4, "type": "message", "date_unixtime": "1772449300", "forwarded_from": "Groupe B", "text": "transféré"},
        {"id": 5, "type": "message", "date_unixtime": "1772449400", "text": "   "}]}
    items = au.read_telegram_export(export)
    assert [(i.message_id, i.group, i.edited) for i in items] == [("2", "Groupe A", False), ("3", "Groupe A", True),
                                                                   ("4", "Groupe B", False)]
    assert items[0].received_at == datetime.fromtimestamp(1772445600, UTC)      # l'heure UTC, pas l'heure locale
    assert items[1].text == "#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95" and parse(items[1].text).ok
    everything = au.read_telegram_export({"chats": {"list": [export, {"name": "Autre", "messages": []}]}})
    assert len(everything) == 3
    for broken in ([], {"messages": "x"}, {"chats": {"list": "x"}},
                   {"messages": [{"id": 1, "type": "message", "date": "2026-03-02T11:00:00", "text": "sans heure"}]}):
        with pytest.raises(ValueError):
            au.read_telegram_export(broken)


def test_bsm_inbox_is_read_only_with_the_original_time(tmp_path):
    path = tmp_path / "signals.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE signals (source TEXT, external_id TEXT, received REAL, raw TEXT, source_timestamp REAL)")
        db.executemany("INSERT INTO signals VALUES (?, ?, ?, ?, ?)", [
            ("telegram", "bot:1:7", 2000.0, SIGNAL, 1000.0), ("manual", "", 2500.0, "à la main", 0.0),
            ("telegram", "bot:1:8", 3000.0, "sans date d'origine", 0.0)])
    before = path.read_bytes()
    items = au.read_bsm_inbox(path)
    assert [(i.message_id, i.received_at.timestamp()) for i in items] == [("bot:1:7", 1000.0), ("bot:1:8", 3000.0)]
    assert path.read_bytes() == before


def test_group_name_units_and_weights():
    assert au.group_of(SIGNAL) == "LEGEND TRADING INDICATOR"
    assert au.group_of("📈 Trader/ Suhaib AlMashhadani\n💎 PAIR: WLD/USDT\nENTRY 1: 0.44\nSL: 0.43") == \
        "Trader/ Suhaib AlMashhadani"
    assert au.group_of("PAIR: ETH/USDT\nENTRY 1: 1") == "" and au.group_of("#ABC/USDT\nEntry1: 1") == ""
    assert au.group_of("───────\n\n") == "" and au.group_of("bonjour à tous\nça va ?") == ""
    assert [au.stop_bars(x) for x in ("4h", "1h", "15m", "15min", "30 min", "", "10m", "7m", "1d")] == [16, 4, 1, 1, 2, 0, 0, 0, 0]
    assert au.ladder_weights(5, "early") == pytest.approx((5 / 15, 4 / 15, 3 / 15, 2 / 15, 1 / 15))
    assert au.ladder_weights(4, "equal") == (0.25,) * 4 and sum(au.ladder_weights(7, "early")) == pytest.approx(1)
    with pytest.raises(ValueError):
        au.ladder_weights(3, "late")


def test_pictographs_inside_a_line_do_not_make_a_signal_unreadable():
    harmonic = "PAIR: SAGA/USDT\n✨ENTRY 1 ✅: 0.02542\n1️⃣ T1: 0.025907 📉 (1.92%)\n2️⃣ T2: 0.026407 📉 (3.88%)\n🛑 SL: 0.02446 (15m) (2.59%)"
    plain = "PAIR: SAGA/USDT\nENTRY 1: 0.02542\nT1: 0.025907 (1.92%)\nT2: 0.026407 (3.88%)\nSL: 0.02446 (15m) (2.59%)"
    signal = parse(harmonic)
    assert signal.ok and (signal.symbol, signal.entries, signal.targets, signal.stop, signal.stop_timeframe) == (
        "SAGAUSDT", [0.02542], [0.025907, 0.026407], 0.02446, "15m")
    assert content_hash(harmonic) == content_hash(plain)             # le même signal, décoré après coup : un doublon
    assert parse("Coin: ABC/USDT\nEntry Zone: 100\nTarget 1 → 104\nStop Loss: 95").targets == [104.0]
    assert not parse("PAIR: SAGA/USDT\nENTRY 1: 0.02542\nT1: 2% 📉\nSL: 0.02446").ok   # toujours aucun prix deviné


# --- Les trois conventions, calculées à la main ---------------------------------------------------------------

def scenario(after: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """40 bougies à 101 (00:00 → 10:00), puis `after` à partir de 10:00 (la publication est à 10:00)."""
    return bars(flat(40, 101.0) + after)


WICK = [(101, 101, 99.5, 100.5),          # 10:00 : traverse l'entrée 100 → rempli à 100, au contact
        (100.5, 100.5, 94.0, 99.0),       # 10:15 : mèche sous le stop 95, clôture au-dessus
        *flat(5, 99.0),                   # 10:30 → 11:30
        (99.0, 99.0, 96.0, 97.0),         # 11:45 : clôture de la bougie 4 h (12:00) à 97, au-dessus du stop
        (97.0, 104.5, 97.0, 104.2),       # 12:00 : TP1 104 dépassé
        *flat(3, 104.2)]


def test_a_wick_stops_the_touch_convention_but_not_the_close_rule(settings):
    report = run(settings, scenario(WICK))
    row = report.rows[0]
    assert (row.status, row.group, row.symbol, row.stop_timeframe) == ("OK", "LEGEND TRADING INDICATOR", "ABCUSDT", "4h")
    assert (row.stop_pct, row.tp1_pct) == (5.0, 4.0)
    assert row.outcomes[au.TP1_TOUCH] == {"issue": "SL_FIRST", "r": round(r_of([(1, 95, True)]), 4), "provisoire": False}
    assert row.outcomes[au.TP1_RULE] == {"issue": "TP1_FIRST", "r": round(r_of([(1, 104, False)]), 4), "provisoire": False}
    assert row.outcomes[au.LADDER] == {"issue": "SL", "r": round(r_of([(1, 95, True)]), 4), "provisoire": False}
    assert row.outcomes[au.TP1_TOUCH]["r"] < -1 < 0 < row.outcomes[au.TP1_RULE]["r"]


def test_a_close_under_the_stop_sells_at_that_close_and_can_lose_more_than_one_r(settings):
    closes_low = WICK[:7] + [(99.0, 99.0, 92.0, 93.0), *flat(4, 93.0)]             # la bougie 4 h clôture à 93
    row = run(settings, scenario(closes_low)).rows[0]
    assert row.outcomes[au.TP1_RULE] == {"issue": "SL_FIRST", "r": round(r_of([(1, 93, True)]), 4), "provisoire": False}
    assert row.outcomes[au.TP1_RULE]["r"] < -1.4
    # Une clôture 15 min sous le stop ne compte pas pour un stop « 4h » : seule la clôture du bloc compte.
    dips = WICK[:2] + [(99.0, 99.0, 93.0, 94.0), *flat(4, 99.0)] + WICK[7:]
    assert run(settings, scenario(dips)).rows[0].outcomes[au.TP1_RULE]["issue"] == "TP1_FIRST"
    fifteen = SIGNAL.replace("(4h)", "(15m)")
    assert run(settings, scenario(dips), [item(fifteen)]).rows[0].outcomes[au.TP1_RULE] == {
        "issue": "SL_FIRST", "r": round(r_of([(1, 94, True)]), 4), "provisoire": False}
    # Bougie de remplissage « au contact » : son plus haut a pu précéder l'entrée, il ne vaut pas TP1.
    spike = [(101, 105.0, 99.5, 100.5), *flat(5, 100.5)]
    outcomes = run(settings, scenario(spike)).rows[0].outcomes
    assert outcomes[au.TP1_RULE]["issue"] == outcomes[au.TP1_TOUCH]["issue"] == "PENDING"
    no_unit = SIGNAL.replace(" (4h)", "")
    row = run(settings, scenario(WICK), [item(no_unit)]).rows[0]
    assert row.outcomes[au.TP1_RULE] == row.outcomes[au.TP1_TOUCH]                  # sans unité : stop au contact


def test_default_replay_is_unchanged_by_the_new_option(settings):
    costs = settings.costs["central"]
    after = bars(WICK, T0 + pd.Timedelta(hours=10))
    common = dict(entry=100.0, stop=95.0, target=104.0, entry_window=96, max_hold=672, costs=costs)
    assert replay(after, **common) == replay(after, stop_close_bars=0, **common)
    assert replay(after, **common)[0] == "SL_FIRST" and replay(after, stop_close_bars=16, **common)[0] == "TP1_FIRST"
    assert replay(after.iloc[:0], stop_close_bars=16, **common) == ("PENDING", None, None)


def test_ladder_sells_each_target_then_the_rest_at_the_stop_or_the_last_price(settings):
    fill = (101, 101, 99.5, 100.5)
    tp1 = (100.5, 104.0, 100.5, 103.0)                                # contact avec TP1 104 : vendu au marché
    both = run(settings, scenario([fill, tp1, (103, 108.0, 103, 107), *flat(3, 107.0)])).rows[0].outcomes[au.LADDER]
    assert both == {"issue": "TOUS_TP", "r": round(r_of([(2 / 3, 104, True), (1 / 3, 108, True)]), 4), "provisoire": False}
    then_stop = run(settings, scenario([fill, tp1, (103, 103, 94.0, 96.0), *flat(3, 96.0)])).rows[0].outcomes[au.LADDER]
    assert then_stop == {"issue": "TP1_PUIS_SL", "r": round(r_of([(2 / 3, 104, True), (1 / 3, 95, True)]), 4),
                         "provisoire": False}
    still_open = run(settings, scenario([fill, tp1, *flat(3, 102.0)])).rows[0].outcomes[au.LADDER]
    assert still_open == {"issue": "EN_COURS_TP1", "r": round(r_of([(2 / 3, 104, True), (1 / 3, 102, True)]), 4),
                          "provisoire": True}
    equal = run(settings, scenario([fill, tp1, (103, 108.0, 103, 107), *flat(3, 107.0)]), weights="equal").rows[0]
    assert equal.outcomes[au.LADDER]["r"] == round(r_of([(0.5, 104, True), (0.5, 108, True)]), 4)
    # Suivi borné : après FOLLOW_DAYS, la position ouverte est valorisée au dernier prix, résultat définitif.
    late_rally = [fill, *flat(au.FOLLOW_DAYS * 96 - 1, 101.0), (101, 120.0, 101, 119.0), *flat(5, 119.0)]
    long = run(settings, scenario(late_rally)).rows[0].outcomes[au.LADDER]
    assert long == {"issue": "OUVERT", "r": round(r_of([(1, 101, True)]), 4), "provisoire": False}   # la hausse du 31e jour ne compte pas
    # Ouverture sous l'entrée : acheté à l'ouverture (99, au marché) ; le risque reste celui du signal (100 − 95).
    bought = 99 * (1 + MARKET)
    cheap = run(settings, scenario([(99.0, 99.5, 98.5, 99.0), tp1, (103, 108.0, 103, 107), *flat(3, 107.0)])).rows[0]
    assert cheap.outcomes[au.LADDER]["r"] == round(r_of([(2 / 3, 104, True), (1 / 3, 108, True)], entry=bought), 4)
    unfilled = run(settings, scenario(flat(100, 101.0))).rows[0].outcomes
    assert {o["issue"] for o in unfilled.values()} == {"UNFILLED"} and {o["r"] for o in unfilled.values()} == {None}
    pending = run(settings, scenario(flat(5, 101.0))).rows[0].outcomes
    assert {o["issue"] for o in pending.values()} == {"PENDING"}


def test_a_gap_under_the_stop_is_sold_at_the_open(settings):
    gap = run(settings, scenario([(94.0, 94.5, 93.0, 94.0), *flat(3, 94.0)])).rows[0].outcomes
    expected = round(((94 * (1 - MARKET)) * (1 - FEE) - 94 * (1 + MARKET) * (1 + FEE)) / 5, 4)
    assert gap[au.LADDER] == {"issue": "SL", "r": expected, "provisoire": False}
    assert gap[au.TP1_TOUCH] == {"issue": "SL_FIRST", "r": expected, "provisoire": False}


# --- Statuts et causalité ------------------------------------------------------------------------------------

def test_signals_that_were_dead_stale_duplicated_or_unreadable_are_not_measured(settings):
    def status(price: float, **kwargs) -> tuple[str, dict]:
        row = run(settings, bars(flat(60, price)), **kwargs).rows[0]
        return row.status, row.outcomes

    assert status(94.0) == ("INVALIDE", {}) and status(95.0) == ("INVALIDE", {})        # au stop ou dessous
    assert status(104.0) == ("DEJA_JOUE", {}) and status(105.0) == ("DEJA_JOUE", {})
    assert status(97.0)[0] == "PERIME"                                # entrée 100 à +3,09 % du prix
    assert status(97.1)[0] == "OK"                                    # +2,99 % : encore acceptée
    assert run(settings, None).rows[0].status == "SANS_DONNEES"
    assert run(settings, bars(flat(60, 101.0)).iloc[:0]).rows[0].status == "SANS_DONNEES"
    late = item(at=T0 + pd.Timedelta(hours=20))                      # dernière bougie connue 5 h avant : trop vieux
    assert run(settings, bars(flat(60, 101.0)), [late]).rows[0].status == "SANS_DONNEES"
    assert run(settings, bars(flat(60, 101.0)), [item("bonjour à tous")]).rows[0].status == "ILLISIBLE"
    again = [item(), item(at=T0 + pd.Timedelta(days=3)), item(at=T0 + pd.Timedelta(days=11))]
    assert [r.status for r in run(settings, bars(flat(1300, 101.0)), again).rows] == ["OK", "DOUBLON", "OK"]
    with pytest.raises(ValueError):
        run(settings, bars(flat(60, 101.0)), weights="late")


def test_nothing_before_the_first_candle_that_opens_after_the_message_can_fill_or_stop(settings):
    """Message à 10:07 : la bougie 10:00-10:15, déjà commencée, plonge sous le stop. Elle ne remplit rien, ne
    stoppe rien, et ne sert pas de prix de référence (elle n'est pas clôturée)."""
    crash_in_progress = [(101, 101, 90.0, 101.0), *flat(100, 101.0)]
    at = T0 + pd.Timedelta(hours=10, minutes=7)
    row = run(settings, scenario(crash_in_progress), [item(at=at)]).rows[0]
    assert row.status == "OK" and {o["issue"] for o in row.outcomes.values()} == {"UNFILLED"}
    # Le futur n'entre pas dans la validité : une chute APRÈS la publication ne rend pas le signal « invalide ».
    later_crash = [*flat(2, 101.0), (101, 101, 80.0, 80.0), *flat(3, 80.0)]
    row = run(settings, scenario(later_crash), [item()]).rows[0]
    assert row.status == "OK" and row.outcomes[au.TP1_TOUCH]["issue"] == "SL_FIRST"


def test_each_pair_is_fetched_once_over_the_span_it_needs(settings):
    calls = []

    def fetch(symbol, start, end):
        calls.append((symbol, start, end))
        return bars(flat(3000, 101.0))

    other = SIGNAL.replace("#ABC/USDT", "#XYZ/USDT")
    items = [item(), item(SIGNAL.replace("Entry1: 100", "Entry1: 100.5"), at=T0 + pd.Timedelta(days=4)), item(other)]
    au.audit(settings, items, now=NOW, bars_for=fetch)
    assert [c[0] for c in calls] == ["ABCUSDT", "XYZUSDT"]
    assert calls[0][1] == T0 + pd.Timedelta(hours=4) and calls[0][2] == T0 + pd.Timedelta(days=4 + au.FOLLOW_DAYS + 2)


def test_market_bars_use_the_store_when_it_covers_the_span_and_public_klines_otherwise(settings):
    CandleStore(settings.data_dir).save(canonical(400, "15m", symbol="BTCUSDT", start="2026-03-01", seed=1), "BTCUSDT", "15m")
    requests = []

    class Client:
        def get_json(self, path, params):
            requests.append((path, dict(params)))
            if params["symbol"] == "NOPEUSDT":
                raise HttpError("400")
            first = params["startTime"]
            return [[first + k * 900_000, "1", "2", "0.5", "1.5", "0", 0] for k in range(3)]

    start = pd.Timestamp("2026-03-02 00:00", tz="UTC")
    fetch = au.market_bars(settings, now=start + pd.Timedelta(minutes=40), client=Client())
    stored = au.market_bars(settings, now=NOW, client=Client())("BTCUSDT", start, start + pd.Timedelta(hours=5))
    assert not requests and stored["open_time"].iloc[0] == start and len(stored) == 21
    fresh = fetch("ABCUSDT", start, start + pd.Timedelta(hours=5))
    assert requests[0][0] == "/api/v3/klines" and requests[0][1]["interval"] == "15m"
    assert list(fresh["close"]) == [1.5, 1.5]                         # la 3e bougie (00:30-00:45) n'est pas clôturée à 00:40
    assert fetch("NOPEUSDT", start, start + pd.Timedelta(hours=5)) is None


# --- Bilan ---------------------------------------------------------------------------------------------------

def rows_with(values: list[float], days: int, group: str = "G") -> list[au.AuditRow]:
    out = []
    for k, value in enumerate(values):
        outcome = {"issue": "TP1_FIRST" if value > 0 else "SL_FIRST", "r": value, "provisoire": False}
        out.append(au.AuditRow(received_at=(T0 + pd.Timedelta(days=k % days, minutes=k)).isoformat(), group=group,
                               status="OK", symbol="ABCUSDT", entry=100.0, stop=95.0, targets=[104.0], stop_pct=5.0,
                               tp1_pct=4.0, outcomes={c: dict(outcome) for c in au.CONVENTIONS}))
    return out


def test_no_conclusion_without_twenty_resolved_signals_over_ten_days():
    def conclusion(values: list[float], days: int) -> str:
        return au.summarize(rows_with(values, days), samples=500, seed=1)["G"]["conventions"][au.TP1_TOUCH]["conclusion"]

    assert "trop peu de signaux résolus (19 < 20)" in conclusion([0.5] * 19, 19)
    assert "9 jour(s) < 10" in conclusion([0.5] * 20, 9)
    assert "gain moyen positif" in conclusion([0.5] * 20, 10)
    assert "perte moyenne" in conclusion([-0.5] * 20, 10)
    mixed = np.where(np.random.default_rng(3).random(40) < 0.5, 1.0, -1.0).tolist()
    spread = au.summarize(rows_with(mixed, 20), samples=500, seed=1)["G"]["conventions"][au.TP1_TOUCH]
    assert spread["ic95"][0] < 0 < spread["ic95"][1] and "ni gain ni perte" in spread["conclusion"]
    block = au.summarize(rows_with([0.8] * 12 + [-1.0] * 8, 10), samples=500, seed=1)["G"]
    touch = block["conventions"][au.TP1_TOUCH]
    assert (touch["resolus"], touch["part_gagnants"], touch["r_moyen"], touch["r_total"]) == (20, 0.6, 0.08, 1.6)
    assert touch["ic95"][0] < 0.08 < touch["ic95"][1] and touch["jours"] == 10
    assert block["part_tp1_pour_etre_a_zero"] == round(1 / (1 + 4 / 5), 4)        # TP1 +4 %, stop −5 % : 55,6 %
    assert (block["tp1_pct_moyen"], block["stop_pct_moyen"], block["statuts"]) == (4.0, 5.0, {"OK": 20})


def test_open_positions_are_reported_apart_and_groups_are_summed():
    rows = rows_with([-1.0] * 3, 3, "A") + rows_with([0.5] * 2, 2, "B")
    for row in rows[:2]:
        row.outcomes[au.LADDER] = {"issue": "EN_COURS_TP1", "r": 1.4, "provisoire": True}
    rows.append(au.AuditRow(received_at=T0.isoformat(), group="A", status="ILLISIBLE"))
    summary = au.summarize(rows, samples=200, seed=1)
    assert list(summary) == ["A", "B", au.ALL] and summary["A"]["messages"] == 4
    ladder = summary["A"]["conventions"][au.LADDER]
    assert (ladder["resolus"], ladder["en_cours"], ladder["r_moyen"]) == (1, 2, -1.0)
    assert ladder["r_moyen_avec_ouvertes"] == round((1.4 + 1.4 - 1.0) / 3, 4)
    assert "r_moyen_avec_ouvertes" not in summary["A"]["conventions"][au.TP1_TOUCH]
    assert summary[au.ALL]["conventions"][au.TP1_TOUCH]["resolus"] == 5
    assert summary[au.ALL]["statuts"] == {"OK": 5, "ILLISIBLE": 1}
    assert list(au.summarize(rows_with([0.5], 1), samples=200, seed=1)) == ["G"]      # un seul groupe : pas d'ensemble
    assert au.summarize([], samples=200, seed=1)[au.ALL]["messages"] == 0


def test_report_and_command(settings, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    report = run(settings, scenario(WICK), [item(), item("illisible")])
    directory = au.write_report(settings, report)
    saved = json.loads((directory / "audit.json").read_text(encoding="utf-8"))
    assert saved["summary"]["LEGEND TRADING INDICATOR"]["conventions"][au.TP1_RULE]["r_moyen"] > 0
    table = pd.read_csv(directory / "signaux.csv")
    assert list(table["status"]) == ["OK", "ILLISIBLE"] and table.loc[0, "tp1_contact_issue"] == "SL_FIRST"
    assert np.isnan(table.loc[1, "tp1"]) and table.loc[0, "tp1"] == 104.0
    assert any("supprimé" in note for note in report.notes) and report.to_dict()["conventions"] == au.CONVENTION_LABELS
    export = tmp_path / "result.json"
    export.write_text(json.dumps({"name": "Mon groupe", "messages": [
        {"id": 1, "type": "message", "date_unixtime": str(int((T0 + pd.Timedelta(hours=10)).timestamp())),
         "text": SIGNAL.split("\n", 2)[2]}]}), encoding="utf-8")
    monkeypatch.setattr(au, "market_bars", lambda settings, now: lambda symbol, start, end: scenario(WICK))
    done = CliRunner().invoke(cli.app, ["audit-telegram", "--file", str(export)])
    assert done.exit_code == 0, done.output
    assert "Mon groupe" in done.output and "aucune conclusion" in done.output and "Rapport :" in done.output
    assert CliRunner().invoke(cli.app, ["audit-telegram"]).exit_code == 2
    assert CliRunner().invoke(cli.app, ["audit-telegram", "--file", str(tmp_path / "absent.json")]).exit_code == 2
