"""Confirmation des cassures de ligne de tendance en 1 h : placebos tirés sur tout l'historique (tirage, calcul égal à
`f15.simulate` gelé, excès nul sans mémoire des prix), déclencheurs, drapeaux, décision, exécution de bout en bout.
Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f15
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL, SCENARIOS
from crypto_signal_intelligence.research import figures_history as fh
from crypto_signal_intelligence.research import trendline_confirmation as tc
from tests.test_figures_history import LATENCY, T0, hour_walk, minute_walk

MIN = fh.MINUTE_NS


def test_placebo_minutes_are_uniform_distinct_reproducible_and_inside_the_window():
    lo, hi = T0.as_unit("ns").value, (T0 + pd.Timedelta(days=100)).as_unit("ns").value
    a = tc.placebo_minutes("k1", lo, hi)
    assert len(a) == 20 and len(set(a)) == 20 and (a >= lo).all() and (a <= hi).all() and ((a - lo) % MIN == 0).all()
    assert (a == tc.placebo_minutes("k1", lo, hi)).all() and not (a == tc.placebo_minutes("k2", lo, hi)).all()
    assert len(tc.placebo_minutes("k1", lo, lo + 5 * MIN)) == 0
    many = np.concatenate([tc.placebo_minutes(f"k{i}", lo, hi) for i in range(300)])
    share = (many - lo) / (hi - lo)
    assert 0.45 < share.mean() < 0.55 and (share < 0.1).mean() > 0.07           # pas de tassement près de l'exécution


def test_uniform_placebos_equal_the_frozen_f15_simulate():
    bars = minute_walk(60 * 24 * 40, 4, drop=0.01)
    m = fh.Minutes.from_frame(bars)
    at = T0 + pd.Timedelta(days=31, hours=3)
    price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
    entry, stop = price * 0.998, price * 0.985
    setup = fh.Setup("XUSDT:1h:TRENDLINE:bull:a", "TRENDLINE", "1h", at, stop, entry, (entry * 1.004, entry * 1.008, entry * 1.012))
    row = fh.play(setup, m, "XUSDT", LATENCY)
    assert row["status"] == fh.EXECUTED
    lo, hi = (T0 + pd.Timedelta(days=1)).as_unit("ns").value, (T0 + pd.Timedelta(days=37)).as_unit("ns").value
    row = tc.with_uniform_placebos(row, m, lo_ns=lo, hi_ns=hi)
    for s in SCENARIOS:
        values = []
        for when in tc.placebo_minutes(setup.key, lo, hi):
            start = pd.Timestamp(int(when), tz="UTC")
            ref = bars[bars["open_time"] >= start]
            q0 = float(ref["open"].iloc[0])
            p = f15.simulate(bars, entry=q0, stop=q0 * stop / entry, targets=[q0 * t / entry for t in setup.targets],
                             order_from=start, order_until=None, hold_minutes=60 * 60, symbol="XUSDT", scenario=s,
                             market_entry=True, late=True)
            if p["status"] == fh.EXECUTED:
                values.append(p["r"])
        assert row[f"uplacebo_n_{s}"] == len(values) > 15
        assert row[f"uplacebo_mean_{s}"] == pytest.approx(np.mean(values), abs=1e-6)
        from crypto_signal_intelligence.forward.costs import costs_for
        c = costs_for("XUSDT", s)
        handicap = c.market * (1 + c.fee) / row["risk_pct"] if row["maker"] else 0.0
        assert row[f"uexcess_adj_{s}"] == pytest.approx(row[f"r_{s}"] - np.mean(values) - handicap, abs=2e-6)


def test_uniform_placebo_excess_is_near_zero_on_a_driftless_walk():
    """Instrument sous l'hypothèse nulle : ordres limites tirés au hasard, placebos tirés sur toute la période : excès à
    frais égaux nul à l'erreur près (contrairement aux placebos tirés avant l'exécution pour une figure de tendance)."""
    values, rng = [], np.random.default_rng(42)
    for case in range(16):
        bars = minute_walk(60 * 24 * 40, 2000 + case, vol=0.0008)
        m = fh.Minutes.from_frame(bars)
        lo, hi = (T0 + pd.Timedelta(days=1)).as_unit("ns").value, (T0 + pd.Timedelta(days=37)).as_unit("ns").value
        for k in range(40):
            at = T0 + pd.Timedelta(days=31, hours=int(rng.integers(0, 24 * 5)))
            price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
            entry = price * (1 - rng.uniform(0.001, 0.004))
            stop = entry * (1 - 0.003)
            setup = fh.Setup(f"X:1h:TRENDLINE:bull:{case}-{k}", "TRENDLINE", "1h", at, stop, entry,
                             tuple(entry + j * (entry - stop) for j in (1, 2, 3)))
            row = tc.with_uniform_placebos(fh.play(setup, m, "SOLUSDT", LATENCY), m, lo_ns=lo, hi_ns=hi)
            if row["status"] == fh.EXECUTED and row[f"uexcess_adj_{CENTRAL}"] is not None:
                values.append(row[f"uexcess_adj_{CENTRAL}"])
    x = np.array(values)
    assert len(x) > 300 and abs(x.mean()) < 3 * x.std(ddof=1) / np.sqrt(len(x))


def test_pair_rows_keeps_trendlines_in_1h_only_with_flags():
    h1 = hour_walk(24 * 300, 13)
    h1 = h1.assign(open_time=pd.date_range("2019-01-01", periods=len(h1), freq="h", tz="UTC"))
    rows = h1.loc[h1.index.repeat(60)].reset_index(drop=True)
    rows["open_time"] = h1["open_time"].iloc[0] + pd.to_timedelta(np.arange(len(rows)), unit="min")
    m = fh.Minutes.from_frame(rows)
    end = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
    out = tc.pair_rows(h1, m, "XUSDT", end=end, latency=LATENCY, top_months={"2019-06"})
    assert out and {r["method"] for r in out} == {"TRENDLINE"} and {r["timeframe"] for r in out} == {"1h"}
    assert all(r["at"] >= pd.Timestamp("2019-04-01", tz="UTC") for r in out)      # 90 jours d'échauffement
    assert all(r["top40"] == (r["at"].strftime("%Y-%m") == "2019-06") for r in out)
    assert all(r["delisted_pair"] for r in out)                                     # données arrêtées en 2019
    assert any(r["status"] == fh.EXECUTED and r["uplacebo_n_central"] > 0 for r in out)


def test_decision_rules():
    good = {"n": 100, "r_ci": (0.01, 0.2), "uexcess_adj_ci": (0.02, 0.3)}
    assert tc.decide(good, good)["piste"] == tc.CONFIRMED and tc.decide(good, good)["gain"] == tc.GAIN
    mixed = good | {"r_ci": (-0.05, 0.2)}
    assert tc.decide(good, mixed) == tc.decide(good, mixed) | {"piste": tc.CONFIRMED, "gain": tc.NO_GAIN}
    bad = {"n": 100, "r_ci": (-0.3, -0.01), "uexcess_adj_ci": (-0.3, -0.02)}
    assert tc.decide(bad, bad)["piste"] == tc.INVERSE and tc.decide(bad, bad)["gain"] == tc.LOSS
    assert tc.decide(good | {"n": 10}, good)["piste"] == tc.INSUFFICIENT
    assert tc.decide(good, good | {"uexcess_adj_ci": None})["gain"] == tc.INSUFFICIENT


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research import long_history, minute_history, pit_universe
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    n = 24 * 330
    start = pd.Timestamp("2024-06-01", tz="UTC")

    def fake_long(settings, symbol):
        frame = hour_walk(n, sum(map(ord, symbol)))
        return frame.assign(open_time=start + pd.to_timedelta(np.arange(n), unit="h"))

    def fake_minutes(settings, symbol):
        hours = fake_long(settings, symbol)
        rep = hours.loc[hours.index.repeat(60)].reset_index(drop=True)
        rep["open_time"] = start + pd.to_timedelta(np.arange(len(rep)), unit="min")
        return rep

    members = pd.DataFrame({"month": pd.to_datetime(["2024-10-01", "2024-11-01", "2024-10-01"], utc=True),
                            "symbol": ["AUSDT", "AUSDT", "BTCUSDT"], "rank": [1, 1, 2], "median_quote_volume": [1.0, 1.0, 2.0]})
    monkeypatch.setattr(long_history, "load_long", fake_long)
    monkeypatch.setattr(minute_history, "load_minutes", fake_minutes)
    monkeypatch.setattr(pit_universe, "load_membership", lambda s: members)
    monkeypatch.setattr(tc, "code_state", lambda: "abc123")
    assert tc.universe(settings) == ["AUSDT"]                                       # BTC fait partie des 40
    payload = tc.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), workers=1)
    result = payload["result"]
    assert set(result["decision"]) >= {"piste", "gain"} and result["scenarios"][ADVERSE]["n"] > 0
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 2 and set(entry["data_hashes"]) == {"1h/AUSDT", "1m/AUSDT"}
    trades = pd.read_parquet(settings.reports_dir / payload["run_id"] / "trades.parquet")
    assert set(trades["method"]) == {"TRENDLINE"} and trades["top40"].any()
    monkeypatch.setattr(tc, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(tc.DirtyCode):
        tc.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), workers=1)
