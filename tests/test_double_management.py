"""Gestion du propriétaire sur le double creux : noyau à stop remonté (égal au noyau à stop fixe quand le stop ne
bouge pas ; stop à l'entrée après TP1, au TP précédent ensuite, appliqué dès la minute suivante), objectifs,
rejeu d'une transaction avec ses placebos, exécution de bout en bout. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward.costs import CENTRAL, SCENARIOS, costs_for
from crypto_signal_intelligence.research import double_management as dm
from crypto_signal_intelligence.research import figures_history as fh
from tests.test_figures_history import LATENCY, T0, minute_walk

FEE, MARKET = 0.00075, 0.0005


def bars_of(rows: list[tuple[float, float, float, float]]) -> fh.Minutes:
    frame = pd.DataFrame([(T0 + k * pd.Timedelta(minutes=1), *r) for k, r in enumerate(rows)],
                         columns=["open_time", "open", "high", "low", "close"])
    return fh.Minutes.from_frame(frame)


def ladder(m, *, fill_i=0, fill_price=100.0, entry=100.0, stop=90.0, targets=(102.0, 104.0, 106.0, 108.0, 110.0),
           weights=(0.6, 0.1, 0.1, 0.1, 0.1), hold=100, move=True):
    hits, r, exit_i, outcome = dm._ladder(m.o, m.h, m.lo, m.c, m.ns, fill_i, fill_price, entry, stop,
                                          np.asarray(targets, float), np.asarray(weights, float), hold * dm.MINUTE_NS,
                                          MARKET, FEE, move)
    return hits, r, exit_i, outcome


def test_splits_sum_to_one_and_targets_are_thirds_of_the_height():
    for split in dm.SPLITS.values():
        assert len(split) == dm.N_TARGETS and sum(split) == pytest.approx(1.0)
    assert dm.targets_of(100.0, 102.0) == pytest.approx((102.0, 104.0, 106.0, 108.0, 110.0))


def test_fixed_stop_ladder_equals_the_frozen_kernel_after_the_fill():
    """Sans bascule et avec les tiers, même R que `figures_history._simulate` (lui-même égal à f15.simulate)."""
    rng = np.random.default_rng(5)
    for case in range(300):
        bars = minute_walk(2000, 100 + case, drop=0.05 if case % 2 else 0.0)
        m = fh.Minutes.from_frame(bars)
        k = int(rng.integers(0, 1500))
        when = int(m.ns[k]) - int(rng.integers(0, 60)) * 10**9
        q0 = float(m.o[k])
        stop = q0 * (1 - rng.uniform(0.002, 0.03))
        targets = np.array(q0 * (1 + np.sort(rng.uniform(0.001, 0.04, 3))))
        hold = int(rng.integers(10, 800))
        for scenario in SCENARIOS:
            c = costs_for("SOLUSDT", scenario)
            status, fill_i, fill_price, hits, r, exit_i, outcome = fh._simulate(
                m.o, m.h, m.lo, m.c, m.ns, q0, stop, targets, when, -1, hold * dm.MINUTE_NS, True, c.market, c.fee, fh.WEIGHTS, True)
            if status != fh.ST_EXECUTED:
                continue
            got = dm._ladder(m.o, m.h, m.lo, m.c, m.ns, fill_i, fill_price, q0, stop, targets, fh.WEIGHTS,
                             hold * dm.MINUTE_NS, c.market, c.fee, False)
            assert got == (hits, r, exit_i, outcome), case


def test_stop_moves_to_entry_after_tp1_and_to_previous_target_afterwards():
    rows = [(100, 100.5, 99.5, 100), (100, 102.5, 100, 102), (102, 102.2, 100.2, 101), (101, 101.5, 100.1, 100.4),
            (100.4, 101, 99, 99.5)]                    # TP1 à la minute 1 ; minute 4 : ouvre au-dessus, touche l'entrée
    m = bars_of(rows)
    hits, r, exit_i, outcome = ladder(m)
    assert hits == 1 and exit_i == 4 and outcome == fh.OUT_STOP
    expected = 0.6 * 102 * (1 - FEE) + 0.4 * 100 * (1 - MARKET) * (1 - FEE) - 100 * (1 + FEE)
    assert r == pytest.approx(expected / 10)
    rows = [(100, 100.5, 99.5, 100), (100, 104.5, 100, 104), (104, 104.2, 102.5, 103), (103, 103.5, 102, 102.2),
            (102.2, 102.4, 100.5, 101)]                # TP1 et TP2 à la minute 1 ; stop = TP1 = 102 touché minute 3
    hits, r, exit_i, outcome = ladder(bars_of(rows))
    assert hits == 2 and exit_i == 3 and outcome == fh.OUT_STOP
    expected = 0.6 * 102 * (1 - FEE) + 0.1 * 104 * (1 - FEE) + 0.3 * 102 * (1 - MARKET) * (1 - FEE) - 100 * (1 + FEE)
    assert r == pytest.approx(expected / 10)


def test_new_stop_applies_from_the_next_minute_only():
    """Minute où TP1 est touché puis le prix revient sous l'entrée : le stop du début de minute (90) reste en vigueur."""
    rows = [(100, 100.5, 99.5, 100), (100, 102.5, 99.0, 99.2), (100.5, 103, 100.2, 102.8)]
    hits, r, exit_i, outcome = ladder(bars_of(rows), hold=3)
    assert hits == 1 and exit_i == 2 and outcome == fh.OUT_TIME                  # pas de stop à l'entrée minute 1
    rows = [(100, 100.5, 99.5, 100), (100, 102.5, 99.0, 99.2), (99.2, 99.5, 98, 98.5)]
    hits, r, exit_i, outcome = ladder(bars_of(rows), hold=3)
    assert hits == 1 and exit_i == 2 and outcome == fh.OUT_STOP                  # minute 2 : stop = entrée, ouverture 99,2
    assert r == pytest.approx((0.6 * 102 * (1 - FEE) + 0.4 * 99.2 * (1 - MARKET) * (1 - FEE) - 100 * (1 + FEE)) / 10)


def test_gap_below_the_moved_stop_exits_at_the_open():
    rows = [(100, 100.5, 99.5, 100), (100, 102.5, 100, 102), (97, 98, 96, 97.5)]
    hits, r, exit_i, outcome = ladder(bars_of(rows))
    assert exit_i == 2 and outcome == fh.OUT_STOP
    assert r == pytest.approx((0.6 * 102 * (1 - FEE) + 0.4 * 97 * (1 - MARKET) * (1 - FEE) - 100 * (1 + FEE)) / 10)


def test_full_ladder_sells_the_remainder_at_tp5():
    rows = [(100, 100.5, 99.5, 100)] + [(100 + 2 * k, 100.5 + 2 * k, 99.5 + 2 * k, 100 + 2 * k) for k in range(1, 7)]
    hits, r, exit_i, outcome = ladder(bars_of(rows), weights=(0.6, 0.15, 0.1, 0.1, 0.05))
    assert hits == 5 and outcome == fh.OUT_TP
    expected = sum(w * t for w, t in zip((0.6, 0.15, 0.1, 0.1, 0.05), (102, 104, 106, 108, 110), strict=True)) * (1 - FEE) \
        - 100 * (1 + FEE)
    assert r == pytest.approx(expected / 10)


def first_run_row(m: fh.Minutes, key: str, at: pd.Timestamp, entry: float, stop: float, tp1: float):
    """Une ligne de `trades.parquet` de la première exécution, produite par le même code (`figures_history.play`)."""
    setup = fh.Setup(key, "DOUBLE", "1h", at, stop, entry, (tp1, entry + 2 * (tp1 - entry), entry + 3 * (tp1 - entry)))
    row = fh.play(setup, m, "XUSDT", LATENCY)
    return row


def test_replay_matches_the_first_execution_and_the_thirds_rule():
    bars = minute_walk(60 * 24 * 40, 7, drop=0.01)
    m = fh.Minutes.from_frame(bars)
    checked = 0
    for k, day in enumerate((31, 33, 35, 37)):
        at = T0 + pd.Timedelta(days=day, hours=k)
        price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
        row = first_run_row(m, f"XUSDT:1h:DOUBLE:bull:{k}", at, price * 0.998, price * 0.97, price * 1.008)
        if row["status"] != fh.EXECUTED:
            continue
        checked += 1
        out = dm.replay(pd.DataFrame([row]).itertuples(index=False).__next__(), m, LATENCY)
        assert out["r_thirds_central"] == row["r_central"]
        for name in dm.SPLITS:
            assert out[f"placebo_n_{name}_central"] == row["placebo_n_central"]        # mêmes placebos
            assert out[f"hits_{name}_central"] <= dm.N_TARGETS
            assert out[f"excess_adj_{name}_central"] is not None
    assert checked >= 2


def test_replay_refuses_a_different_fill():
    bars = minute_walk(60 * 24 * 40, 8)
    m = fh.Minutes.from_frame(bars)
    at = T0 + pd.Timedelta(days=31)
    price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
    row = first_run_row(m, "XUSDT:1h:DOUBLE:bull:z", at, price * 0.998, price * 0.97, price * 1.008)
    assert row["status"] == fh.EXECUTED
    row["fill_at"] = row["fill_at"] + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError):
        dm.replay(pd.DataFrame([row]).itertuples(index=False).__next__(), m, LATENCY)


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research import minute_history
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    n = 60 * 24 * 420
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rng = np.random.default_rng(3)

    def walk(symbol):
        r = np.random.default_rng(sum(map(ord, symbol)))
        c = 100 * np.exp(np.cumsum(r.normal(0, 0.001, n)))
        o = np.r_[100.0, c[:-1]]
        return pd.DataFrame({"open_time": start + pd.to_timedelta(np.arange(n), unit="min"), "open": o,
                             "high": np.maximum(o, c) * 1.0004, "low": np.minimum(o, c) * 0.9996, "close": c})

    monkeypatch.setattr(minute_history, "load_minutes", lambda s, symbol: walk(symbol))
    rows = []
    for symbol in ("AUSDT", "BUSDT"):
        m = fh.Minutes.from_frame(walk(symbol))
        for k in range(90):
            at = start + pd.Timedelta(days=31 + 4 * k, hours=int(rng.integers(0, 20)))
            price = float(m.c[np.searchsorted(m.ns, at.as_unit("ns").value) - 1])
            setup = fh.Setup(f"{symbol}:1h:DOUBLE:bull:{k}", "DOUBLE", "1h", at, price * 0.97, price * 0.999,
                             (price * 1.009, price * 1.019, price * 1.029))
            row = fh.play(setup, m, symbol, LATENCY)
            if row["status"] == fh.EXECUTED:
                rows.append(row)
    source = settings.reports_dir / "FIGH-TEST" / "trades.parquet"
    source.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(source, index=False)
    monkeypatch.setattr(dm, "code_state", lambda: "abc123")
    payload = dm.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), source_run="FIGH-TEST")
    assert set(payload["rows"]) == set(dm.SPLITS) and payload["trades"] == len(rows) > 50
    central = payload["rows"]["A_60_10_10_10_10"]["scenarios"][CENTRAL]
    assert central["n"] == len(rows) and "vs_thirds_ci" in central and "TP5" in central["tp_reached"]
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 2
    monkeypatch.setattr(dm, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(dm.DirtyCode):
        dm.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), source_run="FIGH-TEST")


def test_placebos_follow_the_same_five_target_ladder():
    """Minutes plates à 100 avec un plus haut à 112 chaque minute : la figure et chacun de ses 20 placebos touchent
    les 5 objectifs ; R exacts, les placebos payant l'écart à l'entrée."""
    n = 60 * 24 * 34
    frame = pd.DataFrame({"open_time": T0 + pd.to_timedelta(np.arange(n), unit="min"), "open": 100.0, "high": 112.0,
                          "low": 99.9, "close": 100.0})
    m = fh.Minutes.from_frame(frame)
    at = T0 + pd.Timedelta(days=31)
    row = first_run_row(m, "XUSDT:1h:DOUBLE:bull:flat", at, 100.0, 97.0, 102.0)
    assert row["status"] == fh.EXECUTED and row["placebo_n_central"] == 20
    out = dm.replay(pd.DataFrame([row]).itertuples(index=False).__next__(), m, LATENCY)
    c = costs_for("XUSDT", CENTRAL)
    for name, split in dm.SPLITS.items():
        sold = sum(w * t for w, t in zip(split, (102, 104, 106, 108, 110), strict=True)) * (1 - c.fee)
        assert out[f"r_{name}_central"] == pytest.approx((sold - 100 * (1 + c.fee)) / 3, abs=2e-6)
        assert out[f"hits_{name}_central"] == 5
        assert out[f"placebo_mean_{name}_central"] == pytest.approx((sold - 100 * (1 + c.market) * (1 + c.fee)) / 3, abs=2e-6)
