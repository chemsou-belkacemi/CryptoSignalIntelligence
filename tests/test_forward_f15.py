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
    assert out["hits"] == 1 and out["outcome"] == "STOP_APRES_TP1"


def test_target_first_takes_only_that_target_then_the_stop():
    """Relecture B1 : la seconde dit « objectif d'abord » sur une minute qui touche les trois objectifs ET le stop :
    seul le premier tiers sort à l'objectif, le reste au stop dans la même minute (jamais TP3 sans stop)."""
    rows = [(101, 101, 99.5, 100), (100, 106.5, 94.5, 100)] + flat(10, 100)
    out = run(rows, resolver=lambda minute, stop, target: "target")
    c = costs_for("SOLUSDT", "central")
    expected = (102 / 3 * (1 - c.fee) + 2 / 3 * 95 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.fee)) / 5
    assert out["hits"] == 1 and out["outcome"] == "STOP_APRES_TP1" and out["r"] == pytest.approx(expected, abs=1e-6)


def test_r_uses_planned_risk_and_marketable_order_pays_taker():
    """Relecture C3 : limite 100, stop 95, première minute ouvrant à 98 : exécution au marché (glissement), R / 5."""
    out = run([(98, 98.5, 97.5, 98)] + flat(12, 98))
    c = costs_for("SOLUSDT", "central")
    fill = 98 * (1 + c.market)
    assert out["fill_price"] == pytest.approx(fill)
    assert out["r"] == pytest.approx((98 * (1 - c.market) * (1 - c.fee) - fill * (1 + c.fee)) / 5, abs=1e-6)


def test_quotes_stopping_after_fill_exit_at_last_close_once_late():
    """Relecture C4 : données coupées après l'exécution (retrait de la cote) : en attente tant que le constat n'est pas
    passé, puis sortie à la dernière clôture, jamais exclue comme un trou."""
    rows = [(101, 101, 99.5, 100)] + flat(3, 97)
    assert run(rows)["status"] == "EN_COURS"
    out = run(rows, late=True)
    c = costs_for("SOLUSDT", "central")
    assert out["status"] == f15.EXECUTED and out["outcome"] == f15.DELISTED
    assert out["r"] == pytest.approx((97 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.fee)) / 5, abs=1e-6)


def test_horizon_is_counted_in_time_not_in_bars():
    """Une minute manquante n'allonge pas la détention : échéance = exécution + 10 minutes."""
    bars = minutes([(101, 101, 99.5, 100)] + flat(14, 101))
    bars = bars.drop(index=[3]).reset_index(drop=True)
    out = f15.simulate(bars, entry=100.0, stop=95.0, targets=[102.0, 104.0, 106.0], order_from=T0,
                       order_until=T0 + pd.Timedelta(minutes=5), hold_minutes=10, symbol="SOLUSDT", scenario="central")
    assert out["outcome"] == "TEMPS" and pd.Timestamp(out["exit_at"]) == T0 + pd.Timedelta(minutes=9)


def test_placebos_use_the_same_second_resolver(monkeypatch):
    """Relecture B2 : les placebos passent par le même départage à la seconde que la figure."""
    n = 32 * 1440
    quiet, ambiguous = (100, 100.5, 99.5, 100), (100, 103, 94, 100)
    start = T0 - pd.Timedelta(days=31)
    bars = minutes([quiet if k % 2 == 0 else ambiguous for k in range(n)], start=start)
    calls = []
    monkeypatch.setattr(f15, "_second_order", lambda settings, symbol, minute, stop, target: calls.append(minute) or "stop")
    order_from = start + pd.Timedelta(days=31)
    d = {"figure_id": "X", "symbol": "SOLUSDT", "timeframe": "1h", "family": "TRIANGLE", "entry": 100.0, "stop": 95.0,
         "targets": [102.0, 104.0, 106.0], "order_from": order_from.isoformat(),
         "order_until": (order_from + pd.Timedelta(minutes=30)).isoformat(), "hold_minutes": 30,
         "placebo_minutes": f15.placebo_offsets("X")}
    out = f15.resolve_one(None, d, bars, late=True)
    assert out["status"] == f15.EXECUTED
    assert any(m < order_from for m in calls)                                        # appels venus des placebos


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


def _live_setup(settings, monkeypatch):
    from crypto_signal_intelligence.data import pipeline
    from crypto_signal_intelligence.forward.journal import Journal
    store = f15.figure_store(settings)
    for i, symbol in enumerate(("SOLUSDT", "ETHUSDT")):
        store.save(synthetic_hours(symbol, 200, seed=i + 1), symbol, "1h")
    monkeypatch.setattr(pipeline, "download", lambda *a, **k: None)              # aucun réseau
    started = pd.Timestamp("2025-12-01", tz="UTC")
    start = {"started_at": started.isoformat(), "final_at": (started + pd.Timedelta(days=84)).isoformat(),
             "halal": {"symbols": ["SOLUSDT", "ETHUSDT"]}}
    return Journal(settings.root / "f15.jsonl"), start, started


def test_live_detection_records_new_figures_once(settings, monkeypatch):
    journal, start, started = _live_setup(settings, monkeypatch)
    monkeypatch.setattr(f15, "LATE_AFTER", pd.Timedelta(days=60))                  # rattrapage accepté pour ce test
    now = started + pd.Timedelta(days=30)
    first = f15.record_decisions(settings, journal, start, now=now.to_pydatetime())
    assert first["figures"] > 0
    assert f15.record_decisions(settings, journal, start, now=now.to_pydatetime())["figures"] == 0    # une fois
    figures = [e["data"] for e in journal.entries({f15.FIGURE})]
    assert all(started < pd.Timestamp(f["detected_at"]) <= now for f in figures)
    decisions = [e["data"] for e in journal.entries({f15.DECISION})]
    assert decisions and {d["figure_id"] for d in decisions} == {f["figure_id"] for f in figures if f["played"]}
    assert all(not f["played"] for f in figures if f["side"] == "bear")
    assert all(len(d["placebo_minutes"]) == f15.PLACEBOS for d in decisions)
    assert all(pd.Timestamp(d["order_from"]) == now.ceil("min") for d in decisions)   # l'ordre part à l'inscription
    # Plus tard : les figures déjà inscrites ne changent pas, seules les nouvelles s'ajoutent.
    later = now + pd.Timedelta(days=20)
    f15.record_decisions(settings, journal, start, now=later.to_pydatetime())
    again = [e["data"] for e in journal.entries({f15.FIGURE})]
    assert again[:len(figures)] == figures


def test_figures_recorded_late_are_never_played(settings, monkeypatch):
    """Relecture C2 : après une panne, les figures de la période ratée sont inscrites mais pas jouées ; l'ordre d'une
    figure à l'heure part après sa clôture plus la latence."""
    journal, start, started = _live_setup(settings, monkeypatch)
    now = started + pd.Timedelta(days=30)
    counts = f15.record_decisions(settings, journal, start, now=now.to_pydatetime())
    figures = [e["data"] for e in journal.entries({f15.FIGURE})]
    assert counts["figures"] > 0 and counts["late"] > 0
    for f in figures:
        late = now - pd.Timestamp(f["detected_at"]) > f15.LATE_AFTER
        assert f["late"] == late and (not late or not f["played"])
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    for d in (e["data"] for e in journal.entries({f15.DECISION})):
        detected = next(f for f in figures if f["figure_id"] == d["figure_id"])["detected_at"]
        assert pd.Timestamp(d["order_from"]) >= pd.Timestamp(detected) + latency


def test_no_detection_after_collection_and_lost_decision_is_repaired(settings, monkeypatch):
    journal, start, started = _live_setup(settings, monkeypatch)
    decision = {"figure_id": "SOLUSDT:1h:TRIANGLE:bull:X", "symbol": "SOLUSDT", "timeframe": "1h", "family": "TRIANGLE",
                "entry": 100.0, "stop": 95.0, "targets": [102.0, 104.0, 106.0], "order_from": started.isoformat(),
                "order_until": started.isoformat(), "hold_minutes": 60, "placebo_minutes": [1440]}
    journal.append(f15.FIGURE, {"figure_id": decision["figure_id"], "side": "bull", "valid": True, "played": True,
                                "decision": decision}, now=started.to_pydatetime())
    after = pd.Timestamp(start["final_at"]) + pd.Timedelta(days=1)
    counts = f15.record_decisions(settings, journal, start, now=after.to_pydatetime())
    assert counts["figures"] == 0 and counts["repaired"] == 1                        # aucune détection après la fin
    assert [e["data"] for e in journal.entries({f15.DECISION})] == [decision]
    assert f15.record_decisions(settings, journal, start, now=after.to_pydatetime())["repaired"] == 0


def test_head_of_minutes_is_filled_backwards_without_interior_hole(settings, monkeypatch):
    """Relecture, mineur 4 : un début manquant de plus de 60 000 minutes est comblé par tranches, sans trou."""
    from .conftest import canonical
    store = f15.figure_store(settings)
    begin = pd.Timestamp("2025-01-01", tz="UTC")
    full = canonical(150_000, "1m", symbol="SOLUSDT", start="2025-01-01", seed=3)
    store.save(full.iloc[100_000:], "SOLUSDT", "1m")

    def fake_ensure(settings, store, client, symbol, lo, hi, *, now):               # au plus 60 000 minutes par appel
        times = pd.to_datetime(full["open_time"], utc=True)
        chunk = full[(times >= lo) & (times <= min(hi, lo + pd.Timedelta(minutes=59_999)))]
        store.upsert_tail(chunk, symbol, "1m")
    monkeypatch.setattr(f15.f4, "ensure_minutes", fake_ensure)
    f15._fill_head(settings, store, None, "SOLUSDT", begin, now=pd.Timestamp("2030-01-01", tz="UTC"))
    stored = store.load_since("SOLUSDT", "1m", begin)
    assert len(stored) == 150_000 and pd.Timestamp(stored["open_time"].min()) == begin
