"""Tests en direct F25 à F30 (forward/collecte_events.py, f25.py … f30.py) : événements lus dans les journaux FICTIFS du
collecteur. Détection et frontières des seuils, causalité (futur falsifié dans les journaux et dans les bougies),
rodage de 7 jours, trous du collecteur, un événement par paire et par 24 h, mesure à la main, verdicts positif et
négatif (brut pour les hypothèses négatives), démarrage sur racine temporaire, aucune écriture hors des journaux
F25 à F30, route, carte et rapport. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import dataclasses
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi, make_handler
from crypto_signal_intelligence.forward import collecte_events as C
from crypto_signal_intelligence.forward import f25, f26, f27, f28, f29, f30, registry, report
from crypto_signal_intelligence.forward.costs import costs_for
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.forward.journal import GENESIS, canonical, clean, entry_hash, utc_iso
from crypto_signal_intelligence.forward.tests import BY_ID, TESTS
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .conftest import PROJECT

MODULES = {"F25_LIQ_CASCADE": f25, "F26_MUR_ACHETEURS": f26, "F27_RETRAIT_LIQUIDITE": f27, "F28_BALEINES": f28,
           "F29_PEUR_OPTIONS": f29, "F30_TRENDING": f30}
HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)
S = pd.Timestamp("2026-10-01", tz="UTC")                 # démarrage des tests dans ces scénarios
MIN, Q, H, D = pd.Timedelta(minutes=1), pd.Timedelta(minutes=15), pd.Timedelta(hours=1), pd.Timedelta(days=1)


# --- Aides : journaux fictifs du collecteur, écrits d'un coup (même format que forward/journal.py) -------------------------

def write_collector(root, source: str, rows: list[tuple[pd.Timestamp, str, dict]]) -> None:
    """Entrées (instant d'écriture, nature, données), triées, chaînées comme `Journal.append`, un fichier par mois."""
    months: dict[str, list] = {}
    for at, kind, data in sorted(rows, key=lambda r: r[0]):
        months.setdefault(f"{at:%Y-%m}", []).append((at, kind, data))
    for month, items in months.items():
        path = root / "forward" / f"C_{source}-{month}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev, lines = GENESIS, []
        for seq, (at, kind, data) in enumerate(items):
            stamp, body = utc_iso(at), clean(data)
            digest = entry_hash(seq, stamp, kind, body, prev)
            lines.append(canonical({"seq": seq, "at": stamp, "kind": kind, "data": body, "prev": prev, "hash": digest}))
            prev = digest
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


LIQ_LATE, RESUME_LATE = pd.Timedelta(seconds=63), pd.Timedelta(seconds=4)


def liq_minute(minute: pd.Timestamp, longs: dict[str, float], *, late: pd.Timedelta = LIQ_LATE):
    pairs = {s: [1, v, v, 0] for s, v in longs.items() if v > 0}
    total = sum(longs.values())
    return (minute + late, "LIQ_MINUTE", {"minute": utc_iso(minute), "n": len(pairs), "notional_usdt": total,
                                          "long_liq_usdt": total, "short_liq_usdt": 0, "pairs_count": len(pairs),
                                          "pairs": pairs, "others": {"pairs": 0, "n": 0, "notional_usdt": 0,
                                                                     "long_liq_usdt": 0, "short_liq_usdt": 0}})


def resume(hour: pd.Timestamp, symbol: str, imb: float, bid: float, *, samples: int = 713, late=RESUME_LATE):
    stats = {"imb_1": {"min": imb, "max": imb, "mean": imb}, "bid_1": {"min": bid, "max": bid, "mean": bid}}
    return (hour + H + late, "CARNET_RESUME", {"hour": utc_iso(hour), "symbol": symbol, "samples": samples, "written": 1,
                                               "max_writes_per_hour": 6, "stats": stats})


def start(settings, monkeypatch, test, now=S):
    monkeypatch.setattr(registry, "current_commit", lambda: "abc123def456")
    return registry.start(settings, test, now=now, halal=HALAL)


def run_passes(spec, settings, journal, data, now) -> int:
    total = 0
    for _ in range(100):
        out = C.record_decisions(spec, settings, journal, data, now=now)
        total += out["events"]
        if out["hours"] == 0:
            return total
    raise AssertionError("passages sans fin")


def frame(values: dict[str, list[float]], start: pd.Timestamp, step: pd.Timedelta) -> pd.DataFrame:
    n = len(next(iter(values.values())))
    return pd.DataFrame(values, index=pd.date_range(start, periods=n, freq=step), dtype=float)


def fired(rows: pd.DataFrame) -> list[tuple[pd.Timestamp, str]]:
    return [(pd.Timestamp(r.end), r.symbol) for r in rows[rows["trigger"]].itertuples()]


# --- Démarrage, inscription, gel ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("test_id", sorted(MODULES))
def test_forward_start_each_test_on_a_temporary_root(settings, monkeypatch, test_id):
    module = MODULES[test_id]
    with pytest.raises(registry.DirtyCode):
        monkeypatch.setattr(registry, "current_commit", lambda: "abc123+DIRTY")
        registry.start(settings, module.TEST, now=S, halal=HALAL)
    data = start(settings, monkeypatch, module.TEST)
    assert data["test_id"] == test_id and set(data["fingerprints"]) == {"doc", "params", "code"}
    assert pd.Timestamp(data["final_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=84)
    assert pd.Timestamp(data["interim_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=42)
    assert ExperimentRegistry(settings.experiments_db).program_trials("FORWARD") == 1
    with pytest.raises(registry.AlreadyStarted):
        registry.start(settings, module.TEST, now=S, halal=HALAL)
    assert registry.check_frozen(settings, module.TEST, now=S + D) is None
    params = module.TEST.params
    assert params["family"] == 6 and params["decision_level"] == pytest.approx(1 - 0.05 / 6)
    assert params["min_events"] == 30 and params["min_days"] == 50 and params["rodage_days"] == 7 and params["dedup_hours"] == 24
    assert "collecte_events" in " ".join(module.TEST.frozen_modules)
    collector = [f for f in module.TEST.frozen_functions if ".collect." in f[0]]
    assert collector and not any(name in ("URL", "BASE_URL", "supervise", "run", "serve") for _, name in collector)


def test_tests_are_registered_after_f24_and_preregistered_with_every_field():
    ids = [t.test_id for t, _ in TESTS]
    assert ids[ids.index("F24_SORTIE_BASE_LONGUE") + 1:] == list(MODULES)
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    assert doc.index("## F24_SORTIE_BASE_LONGUE") < doc.index("## F25_LIQ_CASCADE") < doc.index("## F30_TRENDING") \
        < doc.index("## LECTURE_TP_MAHWASHI")
    for test_id in MODULES:
        text = registry.section(doc, test_id)
        assert text and registry.missing_fields(text) == [], test_id
        assert BY_ID[test_id][1] is MODULES[test_id]


def test_changing_a_collector_constant_the_test_depends_on_changes_its_params_fingerprint(monkeypatch):
    from crypto_signal_intelligence.collect import flux, liquidations
    before = C.make_test("F25_LIQ_CASCADE").params
    monkeypatch.setattr(liquidations, "TOP_PAIRS", 5)
    assert C.make_test("F25_LIQ_CASCADE").params != before
    whale = C.make_test("F28_BALEINES").params
    monkeypatch.setattr(flux, "LARGE_USDT_BY_PAIR", {"BTCUSDT": 200_000.0})
    assert C.make_test("F28_BALEINES").params != whale
    monkeypatch.setattr(liquidations, "URL", "wss://fstream.binance.com/market/ws/autre")   # adresse : pas gelée
    monkeypatch.setattr(liquidations, "TOP_PAIRS", 10)
    assert C.make_test("F25_LIQ_CASCADE").params == before


# --- Séries : trous, retards, doublons ------------------------------------------------------------------------------------

def test_silence_of_ten_minutes_makes_the_window_not_evaluable_and_only_up_to_its_end():
    ends = pd.DatetimeIndex([S + 2 * H])
    every = set(pd.date_range(S, S + 3 * H, freq="min", inclusive="left"))
    assert C.silence_ok(every, ends, window=H).tolist() == [True]
    nine = every - set(pd.date_range(S + H + 10 * MIN, periods=9, freq="min"))
    ten = every - set(pd.date_range(S + H + 10 * MIN, periods=10, freq="min"))
    assert C.silence_ok(nine, ends, window=H).tolist() == [True]
    assert C.silence_ok(ten, ends, window=H).tolist() == [False]
    before = every - set(pd.date_range(S + 50 * MIN, periods=12, freq="min"))       # trou à cheval sur le début de fenêtre
    assert C.silence_ok(before, ends, window=H).tolist() == [False]
    after = every - set(pd.date_range(S + 2 * H, periods=30, freq="min"))          # trou APRÈS la fin : sans effet
    assert C.silence_ok(after, ends, window=H).tolist() == [True]
    tail = every - set(pd.date_range(S + 2 * H - 6 * MIN, periods=40, freq="min"))   # 6 min avant la fin, 34 après
    assert C.silence_ok(tail, ends, window=H).tolist() == [True]                     # causal : seulement 6 connues à E


def test_liquidation_window_sums_by_hand_late_and_duplicate_minutes():
    t0 = S + 10 * H
    rows = [{"at": utc_iso(a), "data": d} for a, _, d in
            [liq_minute(t0 - H + k * MIN, {"BTCUSDT": 1_000.0, "XYZUSDT": 9.0}) for k in range(60)]]
    rows.append({"at": utc_iso(t0 - 30 * MIN + 5 * MIN), "data": liq_minute(t0 - 30 * MIN, {"BTCUSDT": 7.0})[2]})  # doublon
    late = liq_minute(t0 - 5 * MIN, {"BTCUSDT": 1e9}, late=pd.Timedelta(minutes=5))      # écrit 5 min après : ignoré
    rows = [r for r in rows if r["data"]["minute"] != utc_iso(t0 - 5 * MIN)] + [{"at": utc_iso(late[0]), "data": late[2]}]
    values, ok = C.liq_values(rows, ["BTCUSDT", "ETHUSDT"], pd.DatetimeIndex([t0]), fields=["n", "notional_usdt",
                                                                                              "long_liq_usdt", "short_liq_usdt"])
    assert ok.tolist() == [True] and values.loc[t0, "BTCUSDT"] == pytest.approx(59 * 1_000.0)   # doublon : la 1re compte
    assert values.loc[t0, "ETHUSDT"] == 0.0 and "XYZUSDT" not in values.columns


def test_carnet_hour_with_too_few_samples_or_written_late_is_not_evaluable():
    hours = pd.DatetimeIndex([S + k * H for k in range(1, 6)])
    rows = [{"at": utc_iso(a), "data": d} for a, _, d in (
        resume(S, "BTCUSDT", 0.1, 100.0, samples=640), resume(S + H, "BTCUSDT", 0.2, 200.0, samples=534),
        resume(S + 2 * H, "BTCUSDT", 0.3, 300.0, samples=533), resume(S + 3 * H, "BTCUSDT", 0.4, 400.0, late=pd.Timedelta(minutes=6)),
        resume(S + 4 * H, "BTCUSDT", 0.5, 500.0, samples=600))]
    out = C.carnet_values(rows, ["BTCUSDT"], hours)
    # heure pleine : 640 échantillons ; 534 ≥ 50/60 × 640 = 533,3 : évaluable ; 533 : non ; écrit 6 min après : non ;
    # le résumé tardif n'entre pas dans le « plein » ; 600 ≥ 533,3 : évaluable
    assert out["imb_1"]["BTCUSDT"].tolist()[0] == 0.1 and out["bid_1"]["BTCUSDT"].isna().tolist() == [False, False, True, True, False]
    first = C.carnet_values([{"at": utc_iso(a), "data": d} for a, _, d in [resume(S, "BTCUSDT", 0.1, 1.0, samples=599)]],
                            ["BTCUSDT"], pd.DatetimeIndex([S + H]))
    assert first["imb_1"]["BTCUSDT"].isna().all()                              # sans historique : 50/60 × 720 = 600


# --- Seuils et frontières -----------------------------------------------------------------------------------------------

def test_threshold_uses_strictly_previous_windows_and_requires_coverage():
    values = frame({"A": [1, 2, 3, 4, 100]}, S, H)
    thr, count = C.thresholds(values, window=4, min_periods=4, quantile=0.5)
    assert np.isnan(thr["A"].iloc[3]) and thr["A"].iloc[4] == pytest.approx(2.5) and count["A"].iloc[4] == 4
    holed = frame({"A": [1, np.nan, 3, 4, 100]}, S, H)
    assert np.isnan(C.thresholds(holed, window=4, min_periods=4, quantile=0.5)[0]["A"].iloc[4])


def test_f25_boundaries_quantile_strict_and_floor_inclusive():
    spec = dataclasses.replace(C.SPECS["F25_LIQ_CASCADE"], window=4, min_periods=4)
    base = [100_000.0] * 4
    rows = C.triggers(spec, frame({"A": [*base, 100_000.0], "B": [*base, 250_000.0], "C": [*base, 249_999.0],
                                   "E": [*base, 260_000.0]}, S, Q))
    assert fired(rows) == [(S + 4 * Q, "B"), (S + 4 * Q, "E")]          # = seuil : non ; ≥ 250 000 : oui ; 249 999 : non


def test_f26_needs_two_consecutive_hours_above_the_quantile():
    spec = dataclasses.replace(C.SPECS["F26_MUR_ACHETEURS"], window=4, min_periods=4)
    rows = C.triggers(spec, frame({"A": [0.1, 0.2, 0.1, 0.2, 0.9, 0.1, 0.1, 0.1, 0.1, 0.9, 0.95]}, S, H))
    assert fired(rows) == [(S + 10 * H, "A")]                             # 0,9 seul : non ; 0,9 puis 0,95 : à la 2e heure


def test_f27_strictly_below_half_of_the_24h_median():
    spec = dataclasses.replace(C.SPECS["F27_RETRAIT_LIQUIDITE"], window=4, min_periods=4)
    rows = C.triggers(spec, frame({"A": [100, 100, 100, 100, 50.0], "B": [100, 100, 100, 100, 49.99]}, S, H))
    assert fired(rows) == [(S + 4 * H, "B")]


def test_f28_needs_a_positive_net_above_the_quantile():
    spec = dataclasses.replace(C.SPECS["F28_BALEINES"], window=4, min_periods=4)
    rows = C.triggers(spec, frame({"A": [-9.0, -8.0, -7.0, -6.0, -1.0], "B": [0.0, 0.0, 0.0, 0.0, 1.0]}, S, Q))
    assert fired(rows) == [(S + 4 * Q, "B")]                              # au-dessus du seuil mais négatif : non


def test_f29_skew_then_declared_dvol_fallback():
    spec = dataclasses.replace(C.SPECS["F29_PEUR_OPTIONS"], window=4, min_periods=4)
    values = frame({"skew": [1, 2, 3, 4, 5, np.nan, 1], "dvol": [40, 41, 42, 43, 40, 60, 40]}, S, Q)
    rows = C.triggers(spec, values)
    assert fired(rows) == [(S + 4 * Q, "BTCUSDT"), (S + 5 * Q, "BTCUSDT")]
    measures = rows.set_index("end")["measure"]
    assert measures[S + 4 * Q] == "ASYMETRIE_25D" and measures[S + 5 * Q] == "DVOL_REPLI"


def test_f30_entry_needs_a_known_previous_hour_where_the_pair_was_absent():
    spec = C.SPECS["F30_TRENDING"]
    values = frame({"A": [0, 1, 1, np.nan, 1, 0, 1], "B": [1, 1, 1, np.nan, 0, 0, 0]}, S, H)
    assert fired(C.triggers(spec, values)) == [(S + H, "A"), (S + 6 * H, "A")]   # après un relevé manquant : non évaluable


# --- Rodage, un événement par 24 h, entrée, placebos --------------------------------------------------------------------------

def test_rodage_window_and_one_event_per_pair_per_24h():
    ends = [S + 7 * D - Q, S + 7 * D, S + 7 * D + 23 * H + 45 * MIN, S + 8 * D, S + 84 * D]
    rows = pd.DataFrame({"end": ends, "symbol": "A", "value": 1.0, "threshold": 0.5, "reference_n": 10, "measure": "X",
                         "evaluable": True, "reference_ok": True, "trigger": True})
    chosen = C.select_events(rows, start_at=S, final_at=S + 84 * D, last_by_symbol={})
    assert [e["end"] for e in chosen] == [S + 7 * D, S + 8 * D]
    again = C.select_events(rows, start_at=S, final_at=S + 84 * D, last_by_symbol={"A": S + 7 * D + 12 * H})
    assert again == []                                                         # tout tombe à moins de 24 h
    later = C.select_events(rows, start_at=S, final_at=S + 84 * D, last_by_symbol={"A": S + 6 * D + 23 * H})
    assert [e["end"] for e in later] == [S + 7 * D + 23 * H + 45 * MIN]


def test_entry_is_the_next_fifteen_minute_close_after_the_event_is_known_and_placebos_come_after():
    assert C.entry_time(S + 10 * H + 2 * MIN) == S + 10 * H + 15 * MIN
    assert C.entry_time(S + 10 * H + 15 * MIN) == S + 10 * H + 30 * MIN
    entry = S + 10 * H + 15 * MIN
    first, again = C.placebo_entries("F25", "x", entry), C.placebo_entries("F25", "x", entry)
    assert first == again and len(set(first)) == 20 and first != C.placebo_entries("F26", "x", entry)
    assert all(entry + H <= p <= entry + 7 * D and (p - entry) % Q == pd.Timedelta(0) for p in first)


# --- Mesure à la main ----------------------------------------------------------------------------------------------------------

def test_measure_by_hand():
    entry = S + 10 * H + 30 * MIN
    placebos = [entry + 2 * H, entry + 3 * H]
    closes = {entry: 100.0, entry + 4 * H: 101.0, entry + D: 102.0, entry + 3 * D: 99.0,
              placebos[0]: 50.0, placebos[0] + 4 * H: 50.0, placebos[0] + D: 51.0, placebos[0] + 3 * D: 50.0,
              placebos[1]: 80.0, placebos[1] + D: 76.0}
    out = C.measure(closes, symbol="BTCUSDT", entry_at=entry, placebos=placebos)
    c = costs_for("BTCUSDT", "central")
    net = 102.0 * (1 - c.market) * (1 - c.fee) / (100.0 * (1 + c.market) * (1 + c.fee)) - 1
    assert out["event"]["24h"]["brut"] == pytest.approx(0.02) and out["event"]["24h"]["central"] == pytest.approx(net)
    assert c.fee == 0.00075 and c.market == 0.0002 and net == pytest.approx(0.0180637, abs=1e-6)
    assert out["event"]["72h"]["brut"] == pytest.approx(-0.01)
    assert out["placebo_mean"]["24h"]["n"] == 2 and out["placebo_mean"]["24h"]["brut"] == pytest.approx((0.02 - 0.05) / 2)
    assert out["placebo_mean"]["4h"]["n"] == 1 and out["excess"]["24h"]["brut"] == pytest.approx(0.02 + 0.015)
    adverse = costs_for("SOLUSDT", "defavorable")
    assert adverse.fee == 0.001 and adverse.market == 0.001
    del closes[entry + D]
    assert C.measure(closes, symbol="BTCUSDT", entry_at=entry, placebos=placebos) is None


# --- Verdict -------------------------------------------------------------------------------------------------------------------

def rows_for(values: list[float], *, days: int, start=S + 8 * D) -> list[dict]:
    out = []
    for i, v in enumerate(values):
        gross = v
        event = {"brut": gross, "central": gross - 0.0019, "defavorable": gross - 0.0024}
        res = {"event": {"4h": None, "24h": event, "72h": None},
               "placebo_mean": {h: {"n": 0, "brut": None, "central": None, "defavorable": None} for h in C.HORIZONS},
               "excess": {h: {m: None for m in C.MEASURES} for h in C.HORIZONS}}
        out.append({"entry_at": utc_iso(start + (i % days) * D + (i // days) * H), "results": res})
    return out


def test_verdict_positive_negative_and_insufficient():
    rng = np.random.default_rng(1)
    good = C.summarize(rows_for(list(0.02 + rng.normal(0, 0.005, 60)), days=60), samples=2000)
    assert C.verdict(good, sign=+1, ended=True) == C.ABOVE and C.verdict(good, sign=+1, ended=False) == C.RUNNING
    assert C.verdict(good, sign=-1, ended=True) == C.NOT_SHOWN
    bad = C.summarize(rows_for(list(-0.02 + rng.normal(0, 0.005, 60)), days=60), samples=2000)
    assert C.verdict(bad, sign=-1, ended=True) == C.BELOW and C.verdict(bad, sign=+1, ended=True) == C.NOT_SHOWN
    zero = C.summarize(rows_for(list(rng.normal(0, 0.0002, 60)), days=60), samples=2000)
    assert zero["central"]["24h"]["ci_decision"][1] < 0                        # le net est < 0 par les seuls frais…
    assert C.verdict(zero, sign=-1, ended=True) == C.NOT_SHOWN                  # … mais le brut ne l'est pas : rien
    few = C.summarize(rows_for(list(0.02 + rng.normal(0, 0.005, 29)), days=29), samples=2000)
    assert C.verdict(few, sign=+1, ended=True) == C.INSUFFICIENT
    clustered = C.summarize(rows_for(list(0.02 + rng.normal(0, 0.005, 120)), days=49), samples=2000)
    assert clustered["central"]["24h"]["n"] == 120 and clustered["central"]["24h"]["days"] == 49
    assert C.verdict(clustered, sign=+1, ended=True) == C.INSUFFICIENT          # 49 jours distincts < 50


# --- Journaux fictifs : détection complète, causalité, rodage, trous, une par 24 h -------------------------------------------

def liq_scenario(*, falsify_future_after: pd.Timestamp | None = None) -> list:
    rows = []
    gap = set(pd.date_range(S + 8 * D + 20 * H, periods=12, freq="min"))
    for minute in pd.date_range(S - D, S + 9 * D, freq="min", inclusive="left"):
        if minute in gap:
            continue
        longs = {"BTCUSDT": 1_000.0, "ETHUSDT": 1_000.0}
        if S + 6 * D + 5 * H <= minute < S + 6 * D + 5 * H + 30 * MIN:          # pic pendant le rodage (référence complète)
            longs["BTCUSDT"] = 20_000.0
        if S + 8 * D + 10 * H <= minute < S + 8 * D + 10 * H + 30 * MIN:        # pic : événement BTC à 10:15
            longs["BTCUSDT"] = 20_000.0
        if S + 8 * D + 20 * H + 12 * MIN <= minute < S + 8 * D + 20 * H + 20 * MIN:   # pic juste après un trou (ETH)
            longs["ETHUSDT"] = 60_000.0
        if minute >= S + 9 * D - 4 * H:                                         # 2e pic BTC moins de 24 h après
            longs["BTCUSDT"] = 30_000.0
        if falsify_future_after is not None and minute >= falsify_future_after:
            longs = {"BTCUSDT": 5e6, "ETHUSDT": 0.0, "SOLUSDT": 9e6}
        rows.append(liq_minute(minute, longs))
    return rows


def test_f25_full_detection_on_fictitious_journals(settings, monkeypatch):
    data = start(settings, monkeypatch, f25.TEST)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario())
    journal = registry.journal_for(settings, f25.TEST_ID)
    now = S + 9 * D + 12 * H
    assert run_passes(f25.SPEC, settings, journal, data, now) == 2
    found = sorted(C.events(journal).values(), key=lambda e: e["window_end"])
    assert [(e["symbol"], e["window_end"]) for e in found] == [("BTCUSDT", utc_iso(S + 8 * D + 10 * H + 15 * MIN)),
                                                              ("ETHUSDT", utc_iso(S + 8 * D + 21 * H + 15 * MIN))]
    btc = found[0]
    assert btc["known_at"] == utc_iso(S + 8 * D + 10 * H + 17 * MIN) and btc["entry_at"] == utc_iso(S + 8 * D + 10 * H + 30 * MIN)
    assert btc["value"] == pytest.approx(15 * 20_000 + 45 * 1_000) and btc["threshold"] == pytest.approx(60_000)
    assert btc["universe_source"] == f25.TEST_ID and len(btc["placebo_entries"]) == 20
    controls = [e["data"] for e in journal.entries({C.CONTROL})]
    assert pd.Timestamp(controls[-1]["until"]) == (now - C.LAG_MINUTE).floor("h")
    rodage = [c for c in controls if c["rodage"]]
    assert sum(c["triggers"] for c in rodage) > 0 and not any(c["events"] for c in rodage)       # pic du rodage : rien
    gap_hour = next(c for c in controls if c["until"] == utc_iso(S + 8 * D + 21 * H))
    assert gap_hour["windows"] == 4 * 3 and gap_hour["trou"] == 4 * 3 and gap_hour["evaluable"] == 0
    assert C.record_decisions(f25.SPEC, settings, journal, data, now=now) == {"hours": 0, "events": 0}
    out = f25.stats(journal, data, now=now)
    assert out["events"] == 2 and out["pending"] == 2 and out["verdict"] == C.RUNNING and out["trou"] >= 12


def test_f25_future_falsified_in_journals_changes_nothing(settings, monkeypatch):
    data = start(settings, monkeypatch, f25.TEST)
    cut = S + 8 * D + 22 * H                                                    # après l'événement ETH (connu à 21:17)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario(falsify_future_after=cut))
    journal = registry.journal_for(settings, f25.TEST_ID)
    end = S + 8 * D + 21 * H + 15 * MIN
    ends = pd.date_range(S + 7 * D + Q, cut, freq=Q)
    honest_root = settings.root / "honnete"
    write_collector(honest_root, "LIQUIDATIONS", liq_scenario())
    falsified = C.detect(f25.SPEC, settings.root, ends=ends, symbols=list(HALAL.symbols), now=S + 20 * D)
    honest = C.detect(f25.SPEC, honest_root, ends=ends, symbols=list(HALAL.symbols), now=S + 20 * D)
    pd.testing.assert_frame_equal(falsified, honest)
    assert (end, "ETHUSDT") in fired(honest)
    assert run_passes(f25.SPEC, settings, journal, data, cut + C.LAG_MINUTE) == 2


def test_late_pass_finds_exactly_the_same_events(settings, monkeypatch):
    data = start(settings, monkeypatch, f25.TEST)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario())
    journal = registry.journal_for(settings, f25.TEST_ID)
    for hour in [*pd.date_range(S + 8 * D, S + 9 * D, freq="7h"), S + 9 * D + 12 * H]:   # passages réguliers, puis un tardif
        run_passes(f25.SPEC, settings, journal, data, hour)
    regular = sorted(C.events(journal))
    late_root = settings.root.parent / "tardif"
    late_root.mkdir()
    monkeypatch.setenv("CSI_ROOT", str(late_root))
    from crypto_signal_intelligence.config import load_settings
    other = load_settings()
    other_data = start(other, monkeypatch, f25.TEST)
    write_collector(other.root, "LIQUIDATIONS", liq_scenario())
    other_journal = registry.journal_for(other, f25.TEST_ID)
    run_passes(f25.SPEC, other, other_journal, other_data, S + 10 * D)
    assert sorted(C.events(other_journal)) == regular and other_data["started_at"] == data["started_at"]


def test_f26_on_fictitious_carnet_with_rodage_and_holes(settings, monkeypatch):
    data = start(settings, monkeypatch, f26.TEST)
    rows = []
    for k, hour in enumerate(pd.date_range(S - D, S + 9 * D, freq="h", inclusive="left")):
        imb = 0.1 + 0.01 * np.sin(k)
        if hour in (S + 8 * D + 5 * H, S + 8 * D + 6 * H):                       # deux heures hautes : événement
            imb = 0.6
        if hour == S + 8 * D + 15 * H:                                         # une seule heure haute : rien
            imb = 0.7
        if hour in (S + 3 * D, S + 3 * D + H):                                 # pendant le rodage : rien
            imb = 0.6
        samples = 500 if hour == S + 8 * D + 20 * H else 713
        rows.append(resume(hour, "ETHUSDT", imb, 1_000_000.0, samples=samples))
        rows.append(resume(hour, "BTCUSDT", 0.1, 2_000_000.0))
    write_collector(settings.root, "CARNET", rows)
    journal = registry.journal_for(settings, f26.TEST_ID)
    assert run_passes(f26.SPEC, settings, journal, data, S + 9 * D) == 1
    event = next(iter(C.events(journal).values()))
    assert event["symbol"] == "ETHUSDT" and event["window_end"] == utc_iso(S + 8 * D + 7 * H)
    assert event["entry_at"] == utc_iso(S + 8 * D + 7 * H + 15 * MIN)
    hole = next(e["data"] for e in journal.entries({C.CONTROL}) if e["data"]["until"] == utc_iso(S + 8 * D + 21 * H))
    assert hole["trou"] >= 1 and hole["pairs"] == 3                             # BTC, ETH et SOL (sans carnet : trou)


# --- Résolution, verdict, clôture ------------------------------------------------------------------------------------------

def price(t: pd.Timestamp) -> float:
    return 100.0 * (1 + 0.001 * ((t - S) / H))


def test_resolution_by_hand_then_gap_then_verdict_and_closure(settings, monkeypatch):
    data = start(settings, monkeypatch, f25.TEST)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario())
    journal = registry.journal_for(settings, f25.TEST_ID)
    run_passes(f25.SPEC, settings, journal, data, S + 9 * D + 12 * H)
    calls = []

    def fetch(symbol, first, last):
        calls.append((symbol, first, last))
        if symbol == "ETHUSDT":
            raise RuntimeError("source muette")
        return {t: price(t) for t in pd.date_range(first, last, freq=Q)}

    btc_entry = S + 8 * D + 10 * H + 30 * MIN
    assert C.resolve(f25.SPEC, settings, journal, now=btc_entry + C.RESOLVE_AFTER - MIN, fetch=fetch) == {}
    assert C.resolve(f25.SPEC, settings, journal, now=btc_entry + C.RESOLVE_AFTER, fetch=fetch) == {C.RESOLVED: 1}
    res = next(iter(C.resolutions(journal).values()))
    expected = price(btc_entry + D) / price(btc_entry) - 1
    assert res["results"]["event"]["24h"]["brut"] == pytest.approx(expected, abs=1e-8)
    assert calls[-1][1] == btc_entry and calls[-1][2] == max(pd.Timestamp(p) for p in next(
        e for e in C.events(journal).values() if e["symbol"] == "BTCUSDT")["placebo_entries"]) + 3 * D
    eth_entry = S + 8 * D + 21 * H + 30 * MIN
    assert C.resolve(f25.SPEC, settings, journal, now=eth_entry + C.RESOLVE_AFTER + D, fetch=fetch) == {}
    assert C.resolve(f25.SPEC, settings, journal, now=eth_entry + C.RESOLVE_AFTER + C.GAP_AFTER, fetch=fetch) == {C.GAP: 1}
    final = pd.Timestamp(data["final_at"])
    assert C.finalize(f25.SPEC, journal, data, now=final) is None                  # recueil pas encore couvert
    journal.append(C.CONTROL, {"until": utc_iso(final.ceil("h")), "windows": 0, "evaluable": 0, "trou": 0, "reference": 0,
                               "triggers": 0, "events": []}, now=final + H)
    assert C.finalize(f25.SPEC, journal, data, now=final + H) == registry.VERDICT
    assert journal.first(registry.VERDICT)["data"]["verdict"] == C.INSUFFICIENT
    assert C.finalize(f25.SPEC, journal, data, now=final + 2 * H) == registry.CLOSURE
    assert C.finalize(f25.SPEC, journal, data, now=final + 3 * H) is None


def test_candles_outside_the_measured_closes_change_nothing():
    entry = S + 10 * H + 30 * MIN
    placebos = C.placebo_entries("F25", "abc", entry)
    closes = {t: price(t) for t in pd.date_range(entry - D, entry + 11 * D, freq=Q)}
    base = C.measure(closes, symbol="BTCUSDT", entry_at=entry, placebos=placebos)
    needed = set(C.needed_closes(entry, placebos))
    falsified = {t: (v if t in needed else v * 7.0) for t, v in closes.items()}
    assert C.measure(falsified, symbol="BTCUSDT", entry_at=entry, placebos=placebos) == base


def test_fetch_closes_asks_one_public_klines_request():
    class Rest:
        def __init__(self):
            self.calls = []

        def get_json(self, path, params):
            self.calls.append((path, params))
            first = pd.Timestamp(params["startTime"], unit="ms", tz="UTC")
            return [[int((first + k * Q).timestamp() * 1000), "1", "1", "1", str(10 + k), "1"] for k in range(5)]

    rest = Rest()
    out = C.fetch_closes(rest, "BTCUSDT", S + Q, S + 3 * Q)
    assert rest.calls[0][0] == "/api/v3/klines" and rest.calls[0][1]["interval"] == "15m" and rest.calls[0][1]["limit"] == 1000
    assert out == {S + Q: 10.0, S + 2 * Q: 11.0, S + 3 * Q: 12.0}


# --- Écritures, route, carte, rapport -------------------------------------------------------------------------------------

def files(root) -> dict:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def test_no_write_outside_the_f25_to_f30_journals(settings, monkeypatch):
    for module in MODULES.values():
        start(settings, monkeypatch, module.TEST)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario())
    before = files(settings.root)
    now = S + 9 * D + 12 * H
    for test_id, module in MODULES.items():
        journal = registry.journal_for(settings, test_id)
        data = registry.status(settings, module.TEST, now=now)["start"]
        run_passes(C.SPECS[test_id], settings, journal, data, now)
        C.resolve(C.SPECS[test_id], settings, journal, now=now, fetch=lambda s, a, b: {})
        module.finalize(journal, data, now=now)
        module.stats(journal, data, now=now)
    changed = {k for k, v in files(settings.root).items() if before.get(k) != v}
    allowed = {f"forward/{t}.jsonl" for t in MODULES} | {f"forward/{t}.jsonl.lock" for t in MODULES}
    assert changed and changed <= allowed
    assert not (settings.root / "signals").exists() and not list((settings.root / "state").glob("assistant*"))


def test_evenements_route_card_and_report(settings, monkeypatch):
    data = start(settings, monkeypatch, f25.TEST)
    write_collector(settings.root, "LIQUIDATIONS", liq_scenario())
    run_passes(f25.SPEC, settings, registry.journal_for(settings, f25.TEST_ID), data, S + 9 * D + 12 * H)
    api = CsiApi(settings, now=lambda: S + 9 * D + 12 * H)
    out = api.dispatch("GET", "/evenements", {}, None)
    rows = {r["test_id"]: r for r in out["tests"]}
    assert list(rows) == list(MODULES) and out["places_orders"] is False and "aucun ordre" in out["note"]
    assert rows["F25_LIQ_CASCADE"]["events"] == 2 and rows["F25_LIQ_CASCADE"]["in_rodage"] is False
    assert rows["F25_LIQ_CASCADE"]["last_events"][0]["symbol"] == "ETHUSDT" and rows["F26_MUR_ACHETEURS"]["state"] == "NON_DEMARRE"
    js = PROJECT.joinpath("src", "crypto_signal_intelligence", "api", "static", "app.js").read_text(encoding="utf-8")
    assert "Événements de marché (collecteur)" in js and 'api("/evenements")' in js
    built = report.build(settings, now=S + 9 * D + 12 * H)
    text = report.markdown(built)
    assert "## F25 à F30 : événements de marché lus dans les journaux du collecteur" in text and "| F25_LIQ_CASCADE | EN_COURS" in text
    handler = make_handler(CsiApi(settings, now=lambda: S), token="jeton-de-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/evenements"
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(urllib.request.Request(url), timeout=10)
        assert refused.value.code == 401
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer jeton-de-test"}),
                                    timeout=10) as response:
            assert response.status == 200 and len(json.loads(response.read())["tests"]) == 6
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_runner_pass_with_the_six_tests_started(settings, monkeypatch):
    from crypto_signal_intelligence.forward import runner
    for module in MODULES.values():
        start(settings, monkeypatch, module.TEST)
    out = runner.run_tests(settings, now=S + 2 * D)
    for test_id in MODULES:
        assert "error" not in out[test_id] and out[test_id]["recorded"]["hours"] > 0
