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


def insert(settings, *, days: int, per_day: int, r: float, state="HISTORIQUE_POSITIF_NON_VALIDE", horizon="24h",
           noise: float = 0.3, seed: int = 0, start=T0):
    """`per_day` plans par jour pendant `days` jours, R = r + bruit ; décision à 00:00 du jour, terminés le lendemain."""
    rng = np.random.default_rng(seed)
    with tk.connect(settings) as db:
        for d in range(days):
            day = start + pd.Timedelta(days=d)
            for k in range(per_day):
                value = float(r + rng.normal(0, noise))
                db.execute("""INSERT INTO plans (day, symbol, horizon, bars, recorded_at, decision_time, close, stop_pct,
                              target_pct, state, outcome, r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (f"{day:%Y-%m-%d}", f"{state[:4]}{k}USDT", horizon, 96, day.isoformat(), day.isoformat(), 1.0,
                            -2.0, 3.0, state, "TP" if value > 0 else "SL", value))


OTHER = "AUCUN_AVANTAGE_HISTORIQUE"
STATE = "HISTORIQUE_POSITIF_NON_VALIDE"


def group(settings, now, state=STATE, horizon="24h"):
    out = tk.summary(settings, samples=500, seed=1, now=now)
    return next(g for g in out["groups"] if g["state"] == state and g["horizon"] == horizon)


def test_a_rising_market_is_not_a_proof(settings):
    """Tous les plans gagnent (marché haussier) : l'ancienne règle aurait conclu ; le témoin dit « rien de plus »."""
    insert(settings, days=40, per_day=3, r=0.5, seed=1)
    insert(settings, days=40, per_day=3, r=0.5, state=OTHER, seed=2)
    g = group(settings, (T0 + pd.Timedelta(days=30)).to_pydatetime())
    assert g["r_mean"] > 0.4 and g["ic95"][0] > 0                          # le R absolu est bien positif
    assert abs(g["excess_mean"]) < 0.1 and not g["proven"]


def test_a_real_state_effect_is_proven_at_the_first_fixed_look(settings):
    insert(settings, days=40, per_day=3, r=0.5, seed=1)
    insert(settings, days=40, per_day=3, r=0.0, state=OTHER, seed=2)
    before = group(settings, (T0 + pd.Timedelta(days=27)).to_pydatetime())
    assert before["checks"] == [] and not before["proven"]                 # aucune date de contrôle encore passée
    after = group(settings, (T0 + pd.Timedelta(days=28, hours=1)).to_pydatetime())
    check = after["checks"][0]
    assert check["look"] == (T0 + pd.Timedelta(days=28)).date().isoformat() and check["level"] == 0.975
    assert check["plans"] == 27 * 3 and check["days"] == 27                 # plans terminés AVANT la date de contrôle
    assert after["proven"] and check["ci"][0] > 0
    assert group(settings, (T0 + pd.Timedelta(days=60)).to_pydatetime())["checks"][1]["level"] == 0.9875


@pytest.mark.parametrize(("days", "per_day"), [(19, 5), (25, 1)])
def test_live_proof_needs_fifty_plans_and_twenty_days(settings, days, per_day):
    insert(settings, days=days, per_day=per_day, r=1.0, seed=1)
    insert(settings, days=days, per_day=per_day, r=0.0, state=OTHER, seed=2)
    assert not group(settings, (T0 + pd.Timedelta(days=60)).to_pydatetime())["proven"]


def test_plans_without_a_control_never_count(settings):
    insert(settings, days=40, per_day=3, r=1.0, seed=1)                    # aucun autre état ces jours-là
    g = group(settings, (T0 + pd.Timedelta(days=60)).to_pydatetime())
    assert g["excess_mean"] is None and not g["proven"]


def test_false_proofs_stay_under_five_percent_across_all_looks(monkeypatch):
    """Sans aucun effet d'état, sur 4 contrôles : part des groupes « prouvés » ≤ 5 % (on simule 200 groupes)."""
    monkeypatch.setattr(tk, "PROOF_SAMPLES", 2000)
    rng = np.random.default_rng(7)
    proven = 0
    for trial in range(200):
        days = pd.date_range(T0, periods=112, freq="D")
        part = pd.DataFrame({"day": [f"{d:%Y-%m-%d}" for d in days for _ in range(2)],
                             "decision_time": [d.isoformat() for d in days for _ in range(2)],
                             "excess": rng.normal(0, 0.8, 224)})
        looks = [T0 + pd.Timedelta(days=28 * k) for k in range(1, 5)]
        if any(tk.proof_at(part, look, k, span=1, bars=96, step=STEP, seed=trial)["proven"]
               for k, look in enumerate(looks, start=1)):
            proven += 1
    assert proven / 200 <= 0.06


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
              "ic95": (0.05, 0.3), "win_share": 0.6, "progress": "60/50", "excess_mean": 0.2, "checks": []}
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
