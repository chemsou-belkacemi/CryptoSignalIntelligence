"""Analyse technique d'une paire (docs/ANALYSE_TECHNIQUE.md) : regroupement des niveaux, arrondi au pas de cotation,
plan d'achat (stop sous le support, objectifs aux résistances, R), bougies clôturées seulement, causalité.
Données SYNTHÉTIQUES."""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.technical import analysis as ta


def hours(n: int, seed: int = 0, start: str = "2026-06-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open_time": pd.date_range(start, periods=n, freq="h", tz="UTC"), "open": open_,
                         "high": np.maximum(open_, close) * 1.003, "low": np.minimum(open_, close) * 0.997, "close": close})


def test_cluster_levels_merges_close_pivots():
    levels = ta.cluster_levels([(100.0, 5), (100.4, 9), (103.0, 2), (99.8, 20)], tolerance=0.5)
    assert [(round(x["price"], 4), x["touches"], x["last_index"]) for x in levels] == [(100.0667, 3, 20), (103.0, 1, 2)]


def test_round_tick():
    assert ta.round_tick(1.23456, Decimal("0.01"), "ROUND_FLOOR") == 1.23
    assert ta.round_tick(1.23456, Decimal("0.01"), "ROUND_CEILING") == 1.24
    assert ta.round_tick(1.23456789123, None, "ROUND_FLOOR") == 1.2345679


def test_buy_plan_by_hand():
    supports = [{"price": 98.0}, {"price": 95.0}]
    resistances = [{"price": 100.1}, {"price": 104.0}, {"price": 108.0}, {"price": 112.0}, {"price": 120.0}]
    plan = ta.buy_plan(100.0, supports, resistances, 2.0, bearish=False, tick=Decimal("0.01"))
    # entrée 100 ; stop 98 − 0,25 × 2 = 97,5 ; 100,1 ignoré (< 0,2 %) ; 3 objectifs au plus
    assert plan["state"] == "PLAN" and plan["entry"] == 100.0 and plan["stop"] == 97.5
    assert [t["price"] for t in plan["targets"]] == [104.0, 108.0, 112.0]
    assert [round(t["r"], 4) for t in plan["targets"]] == [1.6, 3.2, 4.8]
    assert plan["note"] == ""


def test_buy_plan_refuses_bearish_context_and_missing_levels():
    assert ta.buy_plan(100.0, [{"price": 98.0}], [{"price": 104.0}], 2.0, bearish=True, tick=None)["state"] == "NO_TRADE"
    assert "support" in ta.buy_plan(100.0, [], [{"price": 104.0}], 2.0, bearish=False, tick=None)["reason"]
    assert "résistance" in ta.buy_plan(100.0, [{"price": 98.0}], [], 2.0, bearish=False, tick=None)["reason"]
    low_r = ta.buy_plan(100.0, [{"price": 96.0}], [{"price": 101.0}], 2.0, bearish=False, tick=None)
    assert low_r["targets"][0]["r"] < 1 and "faible" in low_r["note"]


def test_aggregate_keeps_closed_complete_bars_only():
    h1 = hours(30)
    now = pd.Timestamp("2026-06-02 05:30", tz="UTC")                   # 29 h 30 après le début
    four = ta.aggregate(h1, "4h", now)
    assert four["open_time"].iloc[-1] == pd.Timestamp("2026-06-02 00:00", tz="UTC")   # 04:00-08:00 pas close
    one = ta.aggregate(h1, "1h", now)
    assert one["open_time"].iloc[-1] == pd.Timestamp("2026-06-02 04:00", tz="UTC")


@pytest.mark.parametrize("timeframe", ["1h", "4h"])
def test_analysis_never_reads_after_now(timeframe):
    h1 = hours(1500, seed=3)
    now = pd.Timestamp(h1["open_time"].iloc[1200]) + pd.Timedelta(hours=1)
    fake = h1.copy()
    later = fake["open_time"] >= now
    fake.loc[later, ["open", "high", "low", "close"]] *= 1.5
    a = ta.analyze(h1, timeframe, now=now, symbol="TEST")
    b = ta.analyze(fake, timeframe, now=now, symbol="TEST")
    assert a == b
    assert all(x["price"] > a["close"] for x in a["resistances"]) and all(x["price"] < a["close"] for x in a["supports"])
    assert pd.Timestamp(a["last_open"]) + ta.TIMEFRAMES[timeframe] <= now


def test_analysis_rejects_short_history_and_unknown_timeframe():
    with pytest.raises(ValueError):
        ta.analyze(hours(30), "1h", now=pd.Timestamp("2030-01-01", tz="UTC"), symbol="TEST")
    with pytest.raises(ValueError):
        ta.analyze(hours(300), "15m", now=pd.Timestamp("2030-01-01", tz="UTC"), symbol="TEST")
