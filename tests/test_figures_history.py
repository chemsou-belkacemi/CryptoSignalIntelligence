"""Figures et méthodes des analystes sur l'historique (docs/FIGURES_HISTORIQUE.md) : copie compilée de la simulation
de F15 identique à la fonction gelée (transaction et placebos), niveaux des éléments ICT/SMC pris seuls, causalité,
période, verdict, exécution de bout en bout. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f15
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL, SCENARIOS
from crypto_signal_intelligence.patterns.primitives import atr
from crypto_signal_intelligence.research import figures_history as fh

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
LATENCY = pd.Timedelta(seconds=2)


def minute_walk(n: int, seed: int, *, drop: float = 0.0, vol: float = 0.0015) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.r_[100.0, c[:-1]] * np.exp(rng.normal(0, vol / 3, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, vol / 2, n)))
    frame = pd.DataFrame({"open_time": T0 + pd.to_timedelta(np.arange(n), unit="min"), "open": o, "high": h, "low": lo,
                          "close": c})
    if drop:
        keep = rng.random(n) > drop
        keep[:5] = True
        frame = frame[keep].reset_index(drop=True)
    return frame


def hour_walk(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[100.0, c[:-1]]
    return pd.DataFrame({"open_time": pd.date_range("2018-01-01", periods=n, freq="h", tz="UTC"), "open": o,
                         "high": np.maximum(o, c) * (1 + rng.uniform(0, 0.008, n)),
                         "low": np.minimum(o, c) * (1 - rng.uniform(0, 0.008, n)), "close": c})


# --- Copie compilée de f15.simulate ------------------------------------------------------------------------------

def test_compiled_simulation_matches_the_frozen_f15_simulate():
    """Mille cas tirés au hasard : ordres limites et au marché, validité ou non, trous dans les minutes, 1 à 3
    objectifs, données arrêtées avant l'échéance, deux scénarios de frais : mêmes champs, au bit près."""
    rng = np.random.default_rng(20261004)
    seen = set()
    for case in range(1000):
        bars = minute_walk(3000, case, drop=0.05 if case % 3 == 0 else 0.0)
        m = fh.Minutes.from_frame(bars)
        k = int(rng.integers(0, 2500))
        order_from = bars["open_time"].iloc[k] + pd.Timedelta(seconds=int(rng.integers(0, 120)))
        price = float(bars["close"].iloc[k])
        market = bool(rng.random() < 0.3)
        entry = price * (1 - rng.uniform(-0.002, 0.01))
        stop = entry * (1 - rng.uniform(0.002, 0.03))
        count = int(rng.integers(1, 4))
        targets = list(entry * (1 + np.sort(rng.uniform(0.001, 0.04, count))))
        until = None if market or rng.random() < 0.2 else order_from + pd.Timedelta(minutes=int(rng.integers(5, 400)))
        hold = int(rng.integers(10, 1500))
        late = bool(rng.random() < 0.5)
        symbol = "BTCUSDT" if case % 2 else "SOLUSDT"
        for scenario in SCENARIOS:
            params = {"entry": entry, "stop": stop, "targets": targets, "order_from": order_from, "order_until": until,
                      "hold_minutes": hold, "symbol": symbol, "scenario": scenario, "market_entry": market, "late": late}
            frozen = f15.simulate(bars, resolver=None, **params)
            fast = fh.simulate_fast(m, **params)
            assert fast == frozen, (case, scenario)
            seen.add(frozen["status"] + ":" + str(frozen.get("outcome", frozen.get("reason"))).split("_APRES")[0])
    for expected in ("EXECUTE:STOP", "EXECUTE:TP1", "EXECUTE:TP2", "EXECUTE:TP3", "EXECUTE:TEMPS",
                     "EXECUTE:COTATION_ARRETEE", "ANNULE:ordre expiré", "ANNULE:stop atteint avant l'entrée", "EN_COURS:None"):
        assert expected in seen, expected                   # chaque branche a bien été comparée


def test_market_order_after_a_hole_in_the_minutes_like_f15():
    """Achat au marché : première minute à plus de 10 minutes du départ → TROU ; à 10 minutes ou moins → exécuté."""
    bars = minute_walk(400, 5)
    bars = bars[(bars["open_time"] < T0 + pd.Timedelta(minutes=100)) | (bars["open_time"] >= T0 + pd.Timedelta(minutes=111))]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    for seconds, expected in ((30, "TROU"), (60, "EXECUTE"), (90, "EXECUTE")):
        start = T0 + pd.Timedelta(minutes=100, seconds=seconds)
        params = {"entry": 100.0, "stop": 50.0, "targets": [200.0], "order_from": start, "order_until": None,
                  "hold_minutes": 30, "symbol": "XUSDT", "scenario": CENTRAL, "market_entry": True, "late": True}
        frozen = f15.simulate(bars.reset_index(drop=True), resolver=None, **params)
        assert frozen["status"] == expected and fh.simulate_fast(m, **params) == frozen


def test_play_matches_f15_resolve_one_with_its_placebos(settings, monkeypatch):
    """Transaction ET 20 placebos : mêmes R, mêmes excès que `f15.resolve_one` (départage à la seconde remplacé par la
    règle de prudence « stop d'abord », comme déclaré)."""
    monkeypatch.setattr(f15, "_second_order", lambda *a, **k: "stop")
    bars = minute_walk(60 * 24 * 40, 7, drop=0.01)
    hole = (bars["open_time"] >= T0 + pd.Timedelta(days=12)) & (bars["open_time"] < T0 + pd.Timedelta(days=12, hours=9))
    bars = bars[~hole].reset_index(drop=True)                # grand trou : placebos sans données
    m = fh.Minutes.from_frame(bars)
    checked = 0
    cases = [("1h", 31, 0.998, 0.985), ("1h", 33, 0.998, 0.985), ("1h", 35, 0.999, 0.99), ("4h", 25, 0.999, 0.98),
             ("4h", 26, 0.997, 0.97), ("1h", 36, 1.004, 0.99)]
    for k, (tf, day, below, stop_at) in enumerate(cases):
        at = T0 + pd.Timedelta(days=day, hours=k)
        price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
        entry, stop = price * below, price * stop_at
        setup = fh.Setup(f"XUSDT:{tf}:TRIANGLE:bull:{k}", "TRIANGLE", tf, at, stop, entry,
                         (entry * 1.004, entry * 1.008, entry * 1.012))
        row = fh.play(setup, m, "XUSDT", LATENCY)
        start, until, hold = fh.order_window(setup, LATENCY)
        d = {"figure_id": setup.key, "symbol": "XUSDT", "timeframe": tf, "family": "TRIANGLE", "entry": entry,
             "stop": stop, "targets": list(setup.targets), "order_from": start.isoformat(), "order_until": until.isoformat(),
             "hold_minutes": hold, "placebo_minutes": f15.placebo_offsets(setup.key)}
        frozen = f15.resolve_one(settings, d, bars, late=True)
        assert frozen["status"] == row["status"]
        if row["status"] != fh.EXECUTED:
            continue
        checked += 1
        for s in SCENARIOS:
            assert row[f"r_{s}"] == frozen["results"][s]["r"]
            assert row[f"placebo_mean_{s}"] == frozen["results"][s]["placebo_mean"]
            assert row[f"excess_{s}"] == frozen["results"][s]["excess"]
            assert row[f"placebo_n_{s}"] == sum(x is not None for x in frozen["results"][s]["placebos"])
    assert checked >= 3


def test_equal_entry_costs_remove_exactly_the_market_cost_of_the_placebos():
    """Excès à frais égaux = excès − market × (1 + frais) / risque relatif pour une entrée maker ; inchangé pour un
    achat au marché ou un ordre exécuté à l'ouverture de la première minute (taker)."""
    from crypto_signal_intelligence.forward.costs import costs_for
    bars = minute_walk(60 * 24 * 40, 21)
    m = fh.Minutes.from_frame(bars)
    kinds = set()
    for k in range(40):
        at = T0 + pd.Timedelta(days=31, hours=3 * k)
        price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
        if k % 3 == 2:
            setup = fh.Setup(f"X:1h:SWEEP:bull:{k}", "SWEEP", "1h", at, price * 0.99)
        else:
            entry = price * (0.997 if k % 3 == 0 else 1.01)                 # 1,01 : exécutable dès la pose
            setup = fh.Setup(f"X:1h:FVG:bull:{k}", "FVG", "1h", at, entry * 0.99, entry,
                             tuple(entry * (1 + 0.01 * j) for j in (1, 2, 3)))
        row = fh.play(setup, m, "XUSDT", LATENCY)
        if row["status"] != fh.EXECUTED:
            continue
        kinds.add((setup.method, row["maker"]))
        for s in SCENARIOS:
            c = costs_for("XUSDT", s)
            gap = row[f"excess_{s}"] - row[f"excess_adj_{s}"]
            assert gap == pytest.approx(c.market * (1 + c.fee) / row["risk_pct"] if row["maker"] else 0.0, abs=2e-6)
    assert kinds == {("FVG", True), ("FVG", False), ("SWEEP", False)}


def test_equal_entry_cost_excess_is_near_zero_on_a_driftless_walk():
    """Marche sans dérive, ordres limites seulement : l'excès corrigé est nul à l'erreur près ; l'excès brut porte le
    biais prévu (relecture avant exécution)."""
    raw, adj, rng = [], [], np.random.default_rng(99)
    for case in range(16):
        bars = minute_walk(60 * 24 * 40, 1000 + case, vol=0.0008)
        m = fh.Minutes.from_frame(bars)
        for k in range(40):
            at = T0 + pd.Timedelta(days=31, hours=int(rng.integers(0, 24 * 8)))
            price = float(bars.loc[bars["open_time"] <= at, "close"].iloc[-1])
            entry = price * (1 - rng.uniform(0.001, 0.004))
            stop = entry * (1 - 0.003)
            setup = fh.Setup(f"X:1h:FVG:bull:{case}-{k}", "FVG", "1h", at, stop, entry,
                             tuple(entry + j * (entry - stop) for j in (1, 2, 3)))
            row = fh.play(setup, m, "SOLUSDT", LATENCY)
            if row["status"] == fh.EXECUTED and row["maker"]:
                raw.append(row[f"excess_{CENTRAL}"])
                adj.append(row[f"excess_adj_{CENTRAL}"])
    raw_a, adj_a = np.array(raw), np.array(adj)
    se = adj_a.std(ddof=1) / np.sqrt(len(adj_a))
    assert len(adj_a) > 300 and abs(adj_a.mean()) < 3 * se
    assert raw_a.mean() - adj_a.mean() > 3 * se                  # sans correction, le hasard paraîtrait battu


def test_order_window_is_the_one_of_f15():
    at = pd.Timestamp("2024-05-01 13:00", tz="UTC")
    limit = fh.Setup("k", "FVG", "4h", at, 90.0, 100.0, (110.0, 120.0, 130.0))
    start, until, hold = fh.order_window(limit, LATENCY)
    assert start == pd.Timestamp("2024-05-01 13:01", tz="UTC")             # première minute après clôture + 2 s
    assert until == at + 20 * pd.Timedelta(hours=4) and hold == 60 * 4 * 60
    market = fh.Setup("k", "SWEEP", "1d", at, 90.0)
    assert fh.order_window(market, LATENCY)[1] is None and fh.order_window(market, LATENCY)[2] == 60 * 24 * 60


def test_market_setup_enters_at_the_first_minute_with_r_targets():
    bars = minute_walk(5000, 3)
    m = fh.Minutes.from_frame(bars)
    at = T0 + pd.Timedelta(hours=40)
    price = float(bars.loc[bars["open_time"] < at, "close"].iloc[-1])
    setup = fh.Setup("XUSDT:1h:SWEEP:bull:a", "SWEEP", "1h", at, price * 0.99)
    row = fh.play(setup, m, "XUSDT", LATENCY)
    first = bars[bars["open_time"] >= at + pd.Timedelta(minutes=1)].iloc[0]
    assert row["entry"] == first["open"]
    risk = row["entry"] - row["stop"]
    assert row["tp1"] == pytest.approx(row["entry"] + risk)
    assert row["status"] == fh.EXECUTED and row["fill_at"] == first["open_time"]
    high = fh.Setup("XUSDT:1h:SWEEP:bull:b", "SWEEP", "1h", at, price * 1.5)       # stop au-dessus de l'ouverture
    assert fh.play(high, m, "XUSDT", LATENCY)["status"] == fh.INVALID


# --- Niveaux des éléments ICT/SMC pris seuls ------------------------------------------------------------------------

def frame_of(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([(T0 + k * pd.Timedelta(hours=1), *r) for k, r in enumerate(rows)],
                        columns=["open_time", "open", "high", "low", "close"])


def flat(n: int, price: float = 10.0) -> list[tuple[float, float, float, float]]:
    return [(price, price + 0.5, price - 0.5, price)] * n


def test_fvg_setup_levels():
    rows = flat(20) + [(10.0, 10.2, 9.8, 10.1), (10.1, 12.0, 10.0, 11.9), (11.9, 13.0, 11.5, 12.8)]
    frame = frame_of(rows)
    a = atr(*(frame[k].to_numpy(float) for k in ("high", "low", "close")))
    fvg = [s for s in fh.smc_setups(frame, "1h", "XUSDT") if s.method == "FVG"]
    assert len(fvg) == 1
    s = fvg[0]
    i = len(rows) - 1
    assert s.entry == 11.5 and s.stop == pytest.approx(10.2 - 0.25 * a[i])     # haut et bas de la zone
    risk = s.entry - s.stop
    assert s.targets == pytest.approx((11.5 + risk, 11.5 + 2 * risk, 11.5 + 3 * risk))
    assert s.at == frame["open_time"].iloc[i] + pd.Timedelta(hours=1) and s.valid and not s.market


def test_order_block_setup_levels():
    rows = flat(20) + [(10.0, 10.2, 8.8, 9.0), (9.0, 10.5, 9.0, 10.4), (10.4, 12.0, 10.3, 11.8)]
    frame = frame_of(rows)
    a = atr(*(frame[k].to_numpy(float) for k in ("high", "low", "close")))
    obs = [s for s in fh.smc_setups(frame, "1h", "XUSDT") if s.method == "OB"]
    assert len(obs) == 1
    j = len(rows) - 1                                       # 11,8 ≥ 9 + 2 × ATR et > 10,2 : connu ici
    assert obs[0].entry == 10.2 and obs[0].stop == pytest.approx(8.8 - 0.25 * a[j])
    assert obs[0].at == frame["open_time"].iloc[j] + pd.Timedelta(hours=1)


def test_sweep_and_rsi_divergence_are_market_orders():
    rows = flat(25) + [(10.0, 10.3, 9.0, 10.2)]           # plus bas des 20 dernières balayé, clôture au-dessus
    frame = frame_of(rows)
    a = atr(*(frame[k].to_numpy(float) for k in ("high", "low", "close")))
    sweeps = [s for s in fh.smc_setups(frame, "1h", "XUSDT") if s.method == "SWEEP"]
    assert len(sweeps) == 1 and sweeps[0].market and sweeps[0].stop == pytest.approx(9.0 - 0.25 * a[-1])
    walk = hour_walk(3000, 2)
    h, lo, c = (walk[k].to_numpy(float) for k in ("high", "low", "close"))
    a = atr(h, lo, c)
    from crypto_signal_intelligence.patterns import indicators
    expected = [(walk["open_time"].iloc[d.known_at] + pd.Timedelta(hours=1), lo[d.second] - 0.25 * a[d.known_at])
                for d in indicators.rsi_divergences(h, lo, c) if d.side == "bull"]
    divs = [s for s in fh.smc_setups(walk, "1h", "XUSDT") if s.method == "RSI_DIV"]
    assert len(divs) == len(expected) > 5 and all(s.market and s.targets == () for s in divs)
    assert [(s.at, s.stop) for s in divs] == expected        # stop sous le second creux, moins 0,25 ATR


def test_structure_break_stop_is_the_last_fractal_low_known_before_the_break():
    frame = hour_walk(3000, 4)
    h, lo, c = (frame[k].to_numpy(float) for k in ("high", "low", "close"))
    a = atr(h, lo, c)
    from crypto_signal_intelligence.patterns import smc
    from crypto_signal_intelligence.patterns.primitives import fractal_pivots
    breaks = {(b.pivot_index, b.index): b for b in smc.structure(h, lo, c, a) if b.side == "bull"}
    lows = [p for p in fractal_pivots(h, lo) if p.kind == "low"]
    setups = [s for s in fh.smc_setups(frame, "1h", "XUSDT") if s.method in ("BOS", "CHOCH")]
    assert len(setups) == len(breaks) and {s.method for s in setups} == {"BOS", "CHOCH"}
    for s in setups[:200]:
        if np.isnan(s.entry):                               # aucun pivot bas encore connu : géométrie invalide
            continue
        i = int((s.at - pd.Timedelta(hours=1) - frame["open_time"].iloc[0]) / pd.Timedelta(hours=1))
        brk = next(b for (p, j), b in breaks.items() if j == i)
        known = max((p for p in lows if p.known_at < i), key=lambda p: (p.known_at, p.index))
        assert s.entry == brk.level and s.stop == pytest.approx(known.price - 0.25 * a[i])


@pytest.mark.parametrize("cut", [24 * 230 + 5, 24 * 330 + 17])
def test_setups_on_the_full_history_equal_setups_on_the_known_prefix(cut):
    """Détection une fois sur toute l'histoire = détection en direct sur les seules bougies connues (troncature),
    pour toutes les méthodes et les trois unités de temps."""
    h1 = hour_walk(24 * 400, 12)
    prefix = h1.iloc[:cut]
    seen = set()
    for tf, step in f15.TIMEFRAMES.items():
        part = f15.aggregate(prefix, tf)
        limit = part["open_time"].iloc[-1] + step

        def setups(frame, tf=tf):
            return fh.figure_setups(frame, tf, "X") + fh.smc_setups(frame, tf, "X")

        def norm(items):                                    # NaN (géométries invalides sans ATR) comparés comme égaux
            return sorted((repr(s), s.key) for s in items)

        full = [s for s in setups(f15.aggregate(h1, tf)) if s.at <= limit]
        assert norm(full) == norm(setups(part)), tf
        seen |= {s.method for s in full}
    assert len(seen) >= 14


@pytest.mark.parametrize("cut", [1500, 2200])
def test_setups_already_detected_never_change_when_the_future_is_falsified(cut):
    frame = hour_walk(3000, 9)
    fake = frame.copy()
    rng = np.random.default_rng(1)
    scale = rng.uniform(0.7, 1.3, len(frame) - cut - 1)
    for col in ("open", "high", "low", "close"):
        fake.loc[cut + 1:, col] *= scale
    fake.loc[cut + 1:, "high"] = fake.loc[cut + 1:, ["open", "high", "close"]].max(axis=1)
    fake.loc[cut + 1:, "low"] = fake.loc[cut + 1:, ["open", "low", "close"]].min(axis=1)
    limit = frame["open_time"].iloc[cut] + pd.Timedelta(hours=1)

    def known(f):
        return sorted((s for s in fh.figure_setups(f, "1h", "X") + fh.smc_setups(f, "1h", "X") if s.at <= limit),
                      key=lambda s: s.key)

    true = known(frame)
    assert len({s.method for s in true}) >= 6 and known(fake) == true


def test_period_needs_warmup_and_the_whole_horizon_in_development():
    end = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
    first = pd.Timestamp("2018-12-20", tz="UTC")

    def ok(at, tf="1h"):
        return fh.in_period(fh.Setup("k", "FVG", tf, pd.Timestamp(at, tz="UTC"), 1.0, 2.0, (3.0,)), first_bar=first, end=end)

    assert not ok("2018-12-31 23:00") and not ok("2019-03-19") and ok("2019-03-21")       # 2019-01-01 et 90 jours
    assert ok("2025-06-27 09:00") and not ok("2025-06-27 09:00", "4h")                     # 80 bougies dans DEVELOPMENT
    assert not ok("2025-04-20", "1d") and ok("2025-04-10", "1d")


# --- Mesure, verdict, exécution ----------------------------------------------------------------------------------

def test_verdict_needs_both_intervals_above_zero_in_both_scenarios():
    good = {"n": 100, "r_ci": (0.01, 0.2), "excess_adj_ci": (0.02, 0.3), "excess_ci": (0.05, 0.4)}
    assert fh.verdict(good, good) == fh.ABOVE
    assert fh.verdict(good, good | {"excess_adj_ci": (-0.01, 0.3)}) == fh.NOT_SHOWN       # l'excès brut ne compte pas
    assert fh.verdict(good | {"r_ci": (-0.3, -0.01)}, good | {"r_ci": (-0.4, -0.02)}) == fh.BELOW
    assert fh.verdict(good | {"n": 29}, good) == fh.INSUFFICIENT
    assert fh.verdict(good, good | {"r_ci": None}) == fh.INSUFFICIENT


def test_evaluate_pools_only_the_f15_families_in_the_ensemble():
    rng = np.random.default_rng(3)
    rows = []
    for k in range(400):
        method = fh.METHODS[k % len(fh.METHODS)]
        r = float(rng.normal(0, 1))
        rows.append({"key": str(k), "symbol": "XUSDT", "timeframe": "1h", "method": method, "status": fh.EXECUTED,
                     "reason": None, "order_from": T0 + pd.Timedelta(days=k), "fill_at": T0 + pd.Timedelta(days=k),
                     "entry": 100.0, "stop": 98.0, "tp1": 102.0}
                    | {f"r_{s}": r for s in SCENARIOS} | {f"hits_{s}": 1 for s in SCENARIOS}
                    | {f"excess_{s}": r - 0.1 for s in SCENARIOS} | {f"excess_adj_{s}": r - 0.2 for s in SCENARIOS}
                    | {f"placebo_mean_{s}": 0.1 for s in SCENARIOS} | {"maker": k % 2 == 0}
                    | {f"placebo_tp1_{s}": 0.4 for s in SCENARIOS})
    trades = pd.DataFrame(rows)
    out = fh.evaluate(trades, samples=200)
    assert set(out) == {*fh.METHODS, fh.ENSEMBLE} and len(out) == fh.N_TRIALS == 19
    ensemble = out[fh.ENSEMBLE]["scenarios"][CENTRAL]["n"]
    assert ensemble == int(trades["method"].isin(f15.fg.FAMILIES).sum())
    assert out["FVG"]["scenarios"][ADVERSE]["tp1_break_even_simple"] == 0.5
    fvg = out["FVG"]["scenarios"][CENTRAL]
    assert fvg["excess_adj_mean"] == pytest.approx(fvg["excess_mean"] - 0.1, abs=1e-3)
    concentration = out[fh.ENSEMBLE]["descriptif"][f"concentration_r_{CENTRAL}"]
    assert concentration["pair"] is None or concentration["pair"]["share"] == 1.0          # une seule paire


def test_run_end_to_end(settings, monkeypatch):
    from crypto_signal_intelligence.research import long_history, minute_history
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    n_hours = 24 * 200
    start = pd.Timestamp("2025-01-01", tz="UTC") - pd.Timedelta(days=95)

    def fake_long(settings, symbol):
        frame = hour_walk(n_hours, sum(map(ord, symbol)))
        return frame.assign(open_time=start + pd.to_timedelta(np.arange(n_hours), unit="h"))

    def fake_minutes(settings, symbol):
        hours = fake_long(settings, symbol)
        rep = hours.loc[hours.index.repeat(60)].reset_index(drop=True)
        rep["open_time"] = start + pd.to_timedelta(np.arange(len(rep)), unit="min")
        rep["high"] = np.maximum(rep["high"], rep[["open", "close"]].max(axis=1))
        return rep

    monkeypatch.setattr(long_history, "load_long", fake_long)
    monkeypatch.setattr(minute_history, "load_minutes", fake_minutes)
    monkeypatch.setattr(fh, "code_state", lambda: "abc123")
    payload = fh.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), symbols=["AUSDT", "BUSDT"], workers=1)
    assert set(payload["rows"]) == {*fh.METHODS, fh.ENSEMBLE}
    entry = ExperimentRegistry(settings.experiments_db).get(payload["run_id"])
    assert entry is not None and entry["metrics"]["n_trials"] == 19 and entry["period_end"].startswith("2025-06-30")
    assert set(entry["data_hashes"]) == {"1h/AUSDT", "1m/AUSDT", "1h/BUSDT", "1m/BUSDT"}
    trades = pd.read_parquet(settings.reports_dir / payload["run_id"] / "trades.parquet")
    assert (pd.to_datetime(trades["at"], utc=True) >= start + pd.Timedelta(days=90)).all() and len(trades) > 100
    monkeypatch.setattr(fh, "code_state", lambda: "abc123+DIRTY")
    with pytest.raises(fh.DirtyCode):
        fh.run(settings, now=datetime(2026, 10, 4, tzinfo=UTC), symbols=["AUSDT"], workers=1)
