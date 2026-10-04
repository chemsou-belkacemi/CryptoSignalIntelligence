"""Stop resserré sur le double creux : noyau égal à celui de la gestion A sans resserrement, niveaux des variantes,
stop à la clôture d'une bougie de l'unité de temps (vente à la minute suivante, pas avant), R par unité de risque
prévu, rejeu avec placebos. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward.costs import CENTRAL, SCENARIOS, costs_for
from crypto_signal_intelligence.research import double_management as dm
from crypto_signal_intelligence.research import double_stops as ds
from crypto_signal_intelligence.research import figures_history as fh
from tests.test_figures_history import LATENCY, T0, minute_walk

FEE, MARKET = 0.00075, 0.0005
HOUR = pd.Timedelta(hours=1).value


def bars_of(rows, start=T0) -> fh.Minutes:
    frame = pd.DataFrame([(start + k * pd.Timedelta(minutes=1), *r) for k, r in enumerate(rows)],
                         columns=["open_time", "open", "high", "low", "close"])
    return fh.Minutes.from_frame(frame)


def managed(m, *, touch=90.0, close=ds.NO_LEVEL, fill_i=0, hold=500, targets=(102.0, 104.0, 106.0, 108.0, 110.0)):
    return ds._managed(m.o, m.h, m.lo, m.c, m.ns, fill_i, 100.0, 100.0, touch, close, HOUR, np.asarray(targets, float),
                       ds.WEIGHTS, hold * fh.MINUTE_NS, MARKET, FEE)


def test_without_tightening_the_kernel_equals_the_management_a_kernel():
    rng = np.random.default_rng(8)
    for case in range(300):
        bars = minute_walk(3000, 300 + case, drop=0.05 if case % 2 else 0.0)
        m = fh.Minutes.from_frame(bars)
        k = int(rng.integers(0, 2000))
        entry = float(m.o[k])
        stop = entry * (1 - rng.uniform(0.002, 0.03))
        targets = np.asarray(dm.targets_of(entry, entry * (1 + rng.uniform(0.001, 0.01))), float)
        hold = int(rng.integers(10, 900))
        fill_price = entry * (1 + MARKET)
        a = dm._ladder(m.o, m.h, m.lo, m.c, m.ns, k, fill_price, entry, stop, targets, ds.WEIGHTS, hold * fh.MINUTE_NS,
                       MARKET, FEE, True)
        b = ds._managed(m.o, m.h, m.lo, m.c, m.ns, k, fill_price, entry, stop, ds.NO_LEVEL, HOUR, targets, ds.WEIGHTS,
                        hold * fh.MINUTE_NS, MARKET, FEE)
        assert (a[0], a[2], a[3]) == (b[0], b[2], b[3]) and b[1] == pytest.approx(a[1] * (entry - stop) / entry, abs=1e-12)


def test_variant_levels():
    assert ds.levels("REFERENCE", 100.0, 92.0) == (92.0, ds.NO_LEVEL, 8.0)
    assert ds.levels("MOITIE_TOUCHE", 100.0, 92.0) == (96.0, ds.NO_LEVEL, 4.0)
    assert ds.levels("CLOTURE_0_4", 100.0, 92.0) == pytest.approx((92.0, 96.8, 3.2))
    assert ds.levels("FIXE_3_PCT", 100.0, 92.0) == pytest.approx((97.0, ds.NO_LEVEL, 3.0))
    assert ds.levels("FIXE_3_PCT", 100.0, 98.0) == pytest.approx((98.0, ds.NO_LEVEL, 2.0))   # stop d'origine plus proche


def test_close_stop_waits_for_the_close_of_the_timeframe_candle_and_sells_at_the_next_open():
    """Bougies 1 h : la minute 30 descend sous le niveau (pas de sortie) ; la bougie clôture sous le niveau à la
    minute 59 → vente à l'ouverture de la minute 60."""
    rows = [(100, 100.5, 99.5, 100)] * 30 + [(100, 100, 96, 96.5)] + [(96.5, 97, 96, 96.5)] * 28 + [(96.5, 97, 96, 96.6)] \
        + [(96.4, 97, 96, 96.8)] + [(96.8, 97, 96.5, 96.8)] * 10
    m = bars_of(rows)
    hits, net, exit_i, outcome = managed(m, close=97.0)
    assert outcome == ds.OUT_CLOSE and exit_i == 60
    assert net == pytest.approx((96.4 * (1 - MARKET) * (1 - FEE) - 100 * (1 + FEE)) / 100)
    rows[59] = (96.5, 97.5, 96, 97.2)                   # clôture au-dessus du niveau : aucune sortie
    hits, net, exit_i, outcome = managed(bars_of(rows), close=97.0, hold=70)
    assert outcome == fh.OUT_TIME


def test_close_stop_is_not_checked_after_tp1_and_the_hard_stop_stays():
    rows = [(100, 100.5, 99.5, 100)] * 10 + [(100, 102.5, 100, 102)] + [(102, 102, 99.5, 99.6)] * 60
    hits, net, exit_i, outcome = managed(bars_of(rows), close=99.8)
    assert hits == 1 and outcome == fh.OUT_STOP and exit_i == 11                 # stop à l'entrée, pas la clôture
    rows = [(100, 100.5, 99.5, 100)] * 10 + [(100, 100, 88, 89)]                 # trou sous le stop de sécurité
    hits, net, exit_i, outcome = managed(bars_of(rows), close=97.0)
    assert outcome == fh.OUT_STOP and exit_i == 10


def test_r_is_per_unit_of_planned_risk_and_placebos_use_the_same_levels():
    n = 60 * 24 * 34
    frame = pd.DataFrame({"open_time": T0 + pd.to_timedelta(np.arange(n), unit="min"), "open": 100.0, "high": 100.2,
                          "low": 99.0, "close": 100.0})
    m = fh.Minutes.from_frame(frame)
    at = T0 + pd.Timedelta(days=31)
    setup = fh.Setup("XUSDT:1h:DOUBLE:bull:flat", "DOUBLE", "1h", at, 97.0, 100.0, (101.0, 102.0, 103.0))
    row = fh.play(setup, m, "XUSDT", LATENCY)
    assert row["status"] == fh.EXECUTED
    out = ds.replay(pd.DataFrame([row]).itertuples(index=False).__next__(), m, LATENCY)
    c = costs_for("XUSDT", CENTRAL)
    # stop à mi-distance = 98,5 : jamais touché (plus bas 99) ; sortie au temps à 100 ; R = net / 1,5 %
    net = 100 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.fee)
    assert out["outcome_MOITIE_TOUCHE_central"] == "TEMPS"
    assert out["r_MOITIE_TOUCHE_central"] == pytest.approx(net / 1.5, abs=1e-5)
    # −3 % fixe = 97 (stop d'origine) : même résultat que la référence, R en 3 %
    assert out["r_FIXE_3_PCT_central"] == pytest.approx(out["r_REFERENCE_central"], abs=1e-6)
    # clôture sous 98,8 : jamais (clôtures à 100)
    assert out["outcome_CLOTURE_0_4_central"] == "TEMPS"
    for variant in (ds.REFERENCE, *ds.VARIANTS):
        for s in SCENARIOS:
            assert out[f"placebo_n_{variant}_{s}"] == 20
    placebo = (100 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.market) * (1 + c.fee)) / 1.5
    handicap = c.market * (1 + c.fee) / 0.015
    assert out["excess_adj_MOITIE_TOUCHE_central"] == pytest.approx(net / 1.5 - placebo - handicap, abs=1e-5)


def test_evaluate_reports_every_variant(settings):
    rng = np.random.default_rng(1)
    rows = []
    for k in range(300):
        row = {"key": str(k), "symbol": "XUSDT", "timeframe": "1h", "fill_at": T0 + pd.Timedelta(days=k)}
        for v in (ds.REFERENCE, *ds.VARIANTS):
            row[f"risk_pct_{v}"] = 0.03
            for s in SCENARIOS:
                r = float(rng.normal(0, 1))
                row |= {f"r_{v}_{s}": r, f"pct_{v}_{s}": r * 3, f"hits_{v}_{s}": 1, f"excess_adj_{v}_{s}": r,
                        f"outcome_{v}_{s}": "STOP" if r < -0.5 else "TP1"}
        rows.append(row)
    out = ds.evaluate(pd.DataFrame(rows), samples=200)
    assert set(out) == {ds.REFERENCE, *ds.VARIANTS} and out[ds.REFERENCE]["verdict"] == "REFERENCE"
    c = out["MOITIE_TOUCHE"]["scenarios"][CENTRAL]
    assert c["stopped_before_tp1"] > 0 and c["stopped_loss_pct_mean"] < 0 and c["risk_pct_median"] == 3.0
