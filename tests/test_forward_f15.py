"""F15_FIGURES : agrégation des bougies, exécution simulée (ordre limite traversé, annulations, tiers aux objectifs,
stop, échéance, départage d'une minute ambiguë), placebos et mesures. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f15
from crypto_signal_intelligence.forward.costs import costs_for

T0 = pd.Timestamp("2026-10-05 00:00", tz="UTC")


def minutes(rows: list[tuple[float, float, float, float]], start: pd.Timestamp = T0) -> pd.DataFrame:
    return pd.DataFrame([(start + k * f15.MINUTE, *row) for k, row in enumerate(rows)],
                        columns=["open_time", "open", "high", "low", "close"])


def test_aggregate_keeps_complete_buckets_only():
    hours = pd.date_range(T0, periods=10, freq="h")
    h1 = pd.DataFrame({"open_time": hours, "open": np.arange(10.0), "high": np.arange(10.0) + 1, "low": np.arange(10.0) - 1,
                       "close": np.arange(10.0) + 0.5})
    four = f15.aggregate(h1, "4h")
    assert list(four["open_time"]) == [T0, T0 + pd.Timedelta(hours=4)]          # 8:00-9:00 incomplète
    assert four.iloc[0][["open", "high", "low", "close"]].tolist() == [0.0, 4.0, -1.0, 3.5]


def run(rows, **kw):
    params = {"entry": 100.0, "stop": 95.0, "targets": [102.0, 104.0, 106.0], "order_from": T0,
              "order_until": T0 + pd.Timedelta(minutes=5), "hold_minutes": 10, "symbol": "SOLUSDT", "scenario": "central"}
    return f15.simulate(minutes(rows), **(params | kw))


def flat(n, price):
    return [(price, price, price, price)] * n


def test_limit_needs_to_trade_through_and_expires():
    out = run([(101, 101, 100, 100.5)] * 6)                                         # touche 100 sans passer dessous
    assert out == {"status": f15.CANCELLED, "reason": "ordre expiré"}


def test_cancelled_when_a_bar_opens_at_the_stop_before_entry():
    assert run([(101, 101, 100.5, 101), (94, 94, 93, 93.5)] + flat(10, 94))["reason"] == "stop atteint avant l'entrée"


def test_thirds_at_targets_with_maker_fees():
    rows = [(101, 101, 99.5, 100)] + [(100, 102.5, 99.8, 102)] + [(102, 104.5, 101.8, 104)] + [(104, 106.5, 103.9, 106)] + flat(10, 106)
    out = run(rows)
    fee = costs_for("SOLUSDT", "central").fee
    expected = ((102 + 104 + 106) / 3 * (1 - fee) - 100 * (1 + fee)) / 5
    assert out["status"] == f15.EXECUTED and out["hits"] == 3 and out["outcome"] == "TP3"
    assert out["r"] == pytest.approx(expected, abs=1e-6)


def test_stop_after_first_target_and_gap_through_stop():
    rows = [(101, 101, 99.5, 100), (100, 102.5, 99.8, 102), (92, 93, 91, 92)] + flat(10, 92)
    out = run(rows)
    c = costs_for("SOLUSDT", "central")
    expected = (102 / 3 * (1 - c.fee) + 92 * (2 / 3) * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.fee)) / 5
    assert out["outcome"] == "STOP_APRES_TP1" and out["r"] == pytest.approx(expected, abs=1e-6)


def test_time_exit_at_the_close():
    rows = [(101, 101, 99.5, 100)] + flat(12, 101)
    out = run(rows)
    c = costs_for("SOLUSDT", "central")
    assert out["outcome"] == "TEMPS" and out["r"] == pytest.approx((101 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.fee)) / 5, abs=1e-6)


def test_ambiguous_minute_is_decided_by_seconds_else_stop_first():
    rows = [(101, 101, 99.5, 100), (100, 102.5, 94.5, 100)] + flat(10, 100)        # stop et TP1 dans la même minute
    assert run(rows)["outcome"] == "STOP"                                             # sans départage : stop d'abord
    out = run(rows, resolver=lambda minute, stop, target: "target")
    assert out["hits"] >= 1


def test_market_entry_for_placebos():
    out = f15.simulate(minutes([(100, 100, 100, 100)] + flat(12, 103)), entry=100, stop=95, targets=[102, 104, 106],
                       order_from=T0, order_until=None, hold_minutes=10, symbol="SOLUSDT", scenario="central",
                       market_entry=True)
    assert out["status"] == f15.EXECUTED and out["fill_price"] == pytest.approx(100 * (1 + costs_for("SOLUSDT", "central").market))


def test_still_running_when_data_is_missing():
    assert run([(101, 101, 99.5, 100)] + flat(3, 101))["status"] == "EN_COURS"


def test_placebo_offsets_are_fixed_by_the_figure():
    a, b = f15.placebo_offsets("X"), f15.placebo_offsets("X")
    assert a == b and len(set(a)) == f15.PLACEBOS and min(a) >= f15.PLACEBO_MIN_MINUTES and max(a) <= f15.PLACEBO_MAX_MINUTES


def test_measure_and_verdict_shape():
    rows = [{"entry_at": (T0 + pd.Timedelta(days=i)).isoformat(),
             "results": {s: {"r": 0.1, "excess": 0.05, "hits": 1} for s in ("central", "defavorable")}} for i in range(5)]
    out = f15._measure(rows, "central", 0.975, samples=200)
    assert out["n"] == 5 and out["r_mean"] == 0.1 and out["tp_reached"]["TP1"] == 1.0


def synthetic_hours(symbol: str, days: int, seed: int) -> pd.DataFrame:
    from .conftest import canonical
    return canonical(24 * days, "1h", symbol=symbol, start="2025-08-01", seed=seed)


def test_live_detection_records_new_figures_once(settings, monkeypatch):
    from crypto_signal_intelligence.data import pipeline
    from crypto_signal_intelligence.forward.journal import Journal
    store = f15.figure_store(settings)
    for i, symbol in enumerate(("SOLUSDT", "ETHUSDT")):
        store.save(synthetic_hours(symbol, 200, seed=i + 1), symbol, "1h")
    monkeypatch.setattr(pipeline, "download", lambda *a, **k: None)              # aucun réseau
    started = pd.Timestamp("2025-12-01", tz="UTC")
    start = {"started_at": started.isoformat(), "final_at": (started + pd.Timedelta(days=84)).isoformat(),
             "halal": {"symbols": ["SOLUSDT", "ETHUSDT"]}}
    journal = Journal(settings.root / "f15.jsonl")
    now = started + pd.Timedelta(days=30)
    first = f15.record_decisions(settings, journal, start, now=now.to_pydatetime())
    assert first["figures"] > 0
    assert f15.record_decisions(settings, journal, start, now=now.to_pydatetime())["figures"] == 0    # une fois
    figures = [e["data"] for e in journal.entries({f15.FIGURE})]
    assert all(started < pd.Timestamp(f["detected_at"]) <= now for f in figures)
    decisions = [e["data"] for e in journal.entries({f15.DECISION})]
    assert {d["figure_id"] for d in decisions} == {f["figure_id"] for f in figures if f["played"]}
    assert all(not f["played"] for f in figures if f["side"] == "bear")
    assert all(len(d["placebo_minutes"]) == f15.PLACEBOS for d in decisions)
    # Plus tard : les figures déjà inscrites ne changent pas, seules les nouvelles s'ajoutent.
    later = now + pd.Timedelta(days=20)
    f15.record_decisions(settings, journal, start, now=later.to_pydatetime())
    again = [e["data"] for e in journal.entries({f15.FIGURE})]
    assert again[:len(figures)] == figures
