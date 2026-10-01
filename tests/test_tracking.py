"""Suivi en direct des plans indicatifs (outlook/tracking.py) : rejeu calculé à la main et identique aux règles du plan
historique, enregistrement une fois par jour, résolution sur les bougies postérieures, preuve en direct. SYNTHÉTIQUE."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.outlook import pair as po
from crypto_signal_intelligence.outlook import tracking as tk

from .conftest import canonical

STEP = pd.Timedelta(minutes=15)
T0 = pd.Timestamp("2026-03-02", tz="UTC")


def bars(rows, start=T0) -> pd.DataFrame:
    return pd.DataFrame([(start + k * STEP, *r) for k, r in enumerate(rows)], columns=["open_time", "open", "high", "low", "close"])


def replay(rows, horizon=4, fee=0.0, market=0.0):
    return tk.replay_plan(bars(rows), stop_pct=-2.0, target_pct=3.0, horizon_bars=horizon, step=STEP, fee=fee, market=market)


def test_replay_by_hand():
    # Entrée 100 à l'ouverture de la 1re bougie ; stop 98, objectif 103 ; R = gain / 2 %.
    assert replay([(100, 101, 99, 100.5), (100.5, 103.5, 100, 103)]) == ("TP", pytest.approx(1.5))
    assert replay([(100, 101, 97.5, 98.5), *[(98.5,) * 4] * 3]) == ("SL", pytest.approx(-1.0))
    assert replay([(100, 104, 97.5, 98.5)] * 4) == ("SL", pytest.approx(-1.0))          # les deux : stop d'abord
    assert replay([(100, 100, 100, 100), (97.0, 97.0, 96.0, 96.5)]) == ("SL", pytest.approx(-1.5))   # ouverture sous le stop
    assert replay([(100, 103, 99.5, 101)] * 4) == ("TEMPS", pytest.approx(0.5))          # 103 touché, pas dépassé
    assert replay([(100, 100, 100, 100)] * 3) == ("PENDING", None)
    gap = bars([(100, 100, 100, 100)] * 4).drop(index=2).reset_index(drop=True)
    gap = pd.concat([gap, bars([(100,) * 4], T0 + 10 * STEP)], ignore_index=True)
    assert tk.replay_plan(gap, stop_pct=-2, target_pct=3, horizon_bars=4, step=STEP, fee=0, market=0) == ("TROU", None)
    _, r = replay([(100, 101, 99, 100.5), (100.5, 103.5, 100, 103)], fee=1e-3, market=3e-4)
    assert r == pytest.approx((103 * (1 - 3e-4) * (1 - 1e-3) / (100 * (1 + 3e-4) * (1 + 1e-3)) - 1) / 0.02, abs=1e-4)


def test_live_replay_is_exactly_the_historical_plan_rule():
    """Le plan suivi en direct et le plan rejoué sur l'historique (pair.plan_outcomes) donnent le même R."""
    rng = np.random.default_rng(1)
    n = 2000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    opens = np.r_[100.0, close[:-1]]
    frame = bars(list(zip(opens, np.maximum(opens, close) * 1.002, np.minimum(opens, close) * 0.998, close, strict=True)))
    frame["realized_vol_96"] = 0.004
    costs = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)
    rows = np.arange(100, 1800, 37)
    hist = po.plan_outcomes(frame, rows, 96, costs, STEP)
    sigma = 0.004 * math.sqrt(96)
    for row, r in zip(hist.rows, hist.r, strict=True):
        after = frame.iloc[row + 1:].reset_index(drop=True)
        _, live = tk.replay_plan(after, stop_pct=-sigma * 100, target_pct=1.5 * sigma * 100, horizon_bars=96, step=STEP,
                                 fee=1e-3, market=3e-4)
        assert live == pytest.approx(r, abs=1e-4)


def fake_outlook(state="HISTORIQUE_POSITIF_NON_VALIDE", **plan):
    calls = []

    def outlook(settings, symbol, horizon, *, now, inputs):
        calls.append((symbol, horizon))
        return {"plan": {"state": state, "state_history": state, "stop_pct": -2.0, "target_pct": 3.0,
                         "expectancy_r": 0.1} | plan,
                "context": {"decision_time": (T0 + pd.Timedelta(hours=10)).isoformat(), "close": 100.0}}
    return outlook, calls


def test_plans_are_recorded_once_a_day_and_never_invented(settings):
    outlook, calls = fake_outlook()
    now = datetime(2026, 3, 2, 10, 5, tzinfo=UTC)
    out = tk.record_day(settings, now=now, symbols=["ETHUSDT", "SOLUSDT"], outlook=outlook, loader=lambda s, sym: {})
    assert out == {"recorded": 6, "skipped": 0, "already": 0} and len(calls) == 6
    again = tk.record_day(settings, now=now + timedelta(hours=3), symbols=["ETHUSDT", "SOLUSDT"], outlook=outlook,
                          loader=lambda s, sym: {})
    assert again == {"recorded": 0, "skipped": 0, "already": 6} and len(calls) == 6
    stale, _ = fake_outlook(state="DONNEES_ANCIENNES")
    assert tk.record_day(settings, now=now + timedelta(days=1), symbols=["ETHUSDT"], outlook=stale,
                         loader=lambda s, sym: {})["skipped"] == 3
    no_levels, _ = fake_outlook()
    def missing(settings, symbol, horizon, *, now, inputs):
        result = no_levels(settings, symbol, horizon, now=now, inputs=inputs)
        result["plan"].pop("stop_pct")
        return result
    assert tk.record_day(settings, now=now + timedelta(days=2), symbols=["ETHUSDT"], outlook=missing,
                         loader=lambda s, sym: {})["recorded"] == 0
    def broken(settings, symbol):
        raise FileNotFoundError(symbol)
    assert tk.record_day(settings, now=now + timedelta(days=3), symbols=["ETHUSDT"], outlook=outlook, loader=broken)["skipped"] == 3
    with tk.connect(settings) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM plans")]
    assert len(rows) == 6 and {r["state"] for r in rows} == {"HISTORIQUE_POSITIF_NON_VALIDE"} and rows[0]["day"] == "2026-03-02"


def test_recorded_plans_are_resolved_on_candles_that_come_after(settings):
    candles = canonical(2000, "15m", symbol="ETHUSDT", start="2026-03-01", seed=3)
    CandleStore(settings.data_dir).save(candles, "ETHUSDT", "15m")
    decision = candles["open_time"].iloc[500]
    with tk.connect(settings) as db:
        db.execute("""INSERT INTO plans (day, symbol, horizon, bars, recorded_at, decision_time, close, stop_pct,
                      target_pct, state) VALUES ('2026-03-06', 'ETHUSDT', '24h', 96, ?, ?, 100, -2.0, 3.0, 'X')""",
                   (decision.isoformat(), decision.isoformat()))
    early = decision + 50 * STEP
    assert tk.resolve(settings, now=early.to_pydatetime()) == {}                         # horizon pas écoulé
    later = decision + 200 * STEP
    counts = tk.resolve(settings, now=later.to_pydatetime())
    with tk.connect(settings) as db:
        row = dict(db.execute("SELECT * FROM plans").fetchone())
    after = candles[candles["open_time"] >= decision].reset_index(drop=True)
    costs = settings.costs["central"]
    expected = tk.replay_plan(after, stop_pct=-2.0, target_pct=3.0, horizon_bars=96, step=STEP, fee=costs.fee_bps / 1e4,
                              market=(costs.slippage_bps + costs.half_spread_bps) / 1e4)
    assert (row["outcome"], row["r"]) == expected and counts == {expected[0]: 1}
    assert tk.resolve(settings, now=later.to_pydatetime()) == {}                         # déjà résolu


def insert(settings, *, plans: int, days: int, r: float, state="HISTORIQUE_POSITIF_NON_VALIDE", horizon="24h"):
    with tk.connect(settings) as db:
        for k in range(plans):
            day = T0 + pd.Timedelta(days=k % days)
            value = r + (0.3 if k % 2 else -0.3)
            db.execute("""INSERT INTO plans (day, symbol, horizon, bars, recorded_at, decision_time, close, stop_pct,
                          target_pct, state, outcome, r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (f"{day:%Y-%m-%d}", f"P{k}USDT", horizon, 96, day.isoformat(), day.isoformat(), 1.0, -2.0, 3.0,
                        state, "TP" if value > 0 else "SL", value))


def test_live_proof_needs_fifty_plans_twenty_days_and_a_positive_interval(settings):
    assert tk.summary(settings) == {"groups": [], "recorded": 0}
    insert(settings, plans=50, days=20, r=0.5)
    group = tk.summary(settings, samples=500, seed=1)["groups"][0]
    assert group["proven"] and group["resolved"] == 50 and group["days"] == 20 and group["ic95"][0] > 0
    assert tk.live_status(settings, "24h", "HISTORIQUE_POSITIF_NON_VALIDE")["proven"]
    assert tk.live_status(settings, "3j", "HISTORIQUE_POSITIF_NON_VALIDE") is None


@pytest.mark.parametrize(("plans", "days", "r"), [(49, 20, 0.5), (50, 19, 0.5), (60, 20, 0.0)])
def test_live_proof_refused_when_a_criterion_fails(settings, plans, days, r):
    insert(settings, plans=plans, days=days, r=r)
    assert not tk.summary(settings, samples=500, seed=1)["groups"][0]["proven"]


def test_a_plan_is_shown_favorable_only_when_its_kind_is_proven_live(settings, monkeypatch):
    store = CandleStore(settings.data_dir)
    setup = canonical(6000, symbol="ETHUSDT", seed=5)
    store.save(setup, "ETHUSDT", "15m")
    store.save(canonical(1500, "1h", symbol="ETHUSDT", seed=6), "ETHUSDT", "1h")
    store.save(canonical(1500, "1h", symbol="BTCUSDT", seed=7), "BTCUSDT", "1h")
    now = setup["available_at"].iloc[-1].to_pydatetime() + timedelta(minutes=1)
    base = po.pair_outlook(settings, "ETHUSDT", "24h", now=now)
    assert base["plan"]["state_history"] == base["plan"]["state"]
    proven = {"proven": True, "label": "1 jour", "state": base["plan"]["state"], "resolved": 60, "r_mean": 0.2,
              "ic95": (0.05, 0.3), "win_share": 0.6, "progress": "60/50"}
    monkeypatch.setattr(tk, "live_status", lambda settings, horizon, state: proven | {"state": state})
    shown = po.pair_outlook(settings, "ETHUSDT", "24h", now=now)["plan"]
    expected = tk.LIVE_PROVEN if base["plan"]["state"] == po.POSITIVE else base["plan"]["state"]
    assert shown["state"] == expected and shown["live"]["proven"] and shown["state_history"] == base["plan"]["state"]
    # Le cas positif, forcé : un historique positif dont le type est prouvé en direct devient favorable.
    monkeypatch.setattr(po, "block_interval", lambda *a, **k: ((0.1, 0.4), 40))
    monkeypatch.setattr(po, "MAX_YEAR_SHARE", 1.0)
    forced = po.pair_outlook(settings, "ETHUSDT", "24h", now=now)["plan"]
    assert forced["state_history"] == po.POSITIVE and forced["state"] == tk.LIVE_PROVEN
    monkeypatch.setattr(tk, "live_status", lambda settings, horizon, state: proven | {"state": state, "proven": False})
    assert po.pair_outlook(settings, "ETHUSDT", "24h", now=now)["plan"]["state"] == po.POSITIVE   # pas encore prouvé
