"""Comparer les gestions (external/trailing.Management, external/managements.py) : parts vendues, règles du stop
calculées à la main, grille, choix sur les deux premiers tiers puis confirmation sur le dernier. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.external import managements as mg
from crypto_signal_intelligence.external import trailing as tr

FREE = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
T0 = pd.Timestamp("2026-01-01", tz="UTC")
TARGETS = [102.0, 104.0, 106.0, 108.0, 110.0]


def test_shares_sold_at_each_target():
    assert tr.Management(5, None).weights(5) == pytest.approx([5 / 15, 4 / 15, 3 / 15, 2 / 15, 1 / 15])
    assert tr.Management(3, 0.5).weights(7) == pytest.approx([0.5, 0.25, 0.25])
    assert tr.Management(4, 0.7).weights(4) == pytest.approx([0.7, 0.1, 0.1, 0.1])
    assert tr.Management(7, 0.6).weights(2) == pytest.approx([0.6, 0.4])            # le signal n'a que 2 objectifs
    assert tr.Management(1).weights(5) == pytest.approx([1.0])
    for m in tr.management_grid():
        for n in range(1, 8):
            assert m.weights(n).sum() == pytest.approx(1.0) and len(m.weights(n)) == min(m.tp_count, n)


def test_grid_covers_two_to_seven_targets_tp1_shares_and_four_stop_rules():
    grid = tr.management_grid()
    keys = [m.key for m in grid]
    assert len(keys) == len(set(keys)) == 1 + 6 * 4 * 4
    assert tr.OWNER.key in keys and "tp1_early_fixe" in keys and "tp7_70_suiveur1" in keys and "tp2_50_entree" in keys
    assert tr.OWNER.label == "5 objectifs, parts décroissantes, stop à l'entrée 1 après TP1 puis à TP(k−2)"
    assert tr.Management(3, 0.6, tr.ENTRY, 0).label == "3 objectifs, 60 % à TP1 puis parts égales, stop à l'entrée 1 après TP1"


def run(m: tr.Management, rows):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    used = TARGETS[:m.tp_count]
    out = tr.simulate(*(frame[k].to_numpy(float) for k in ("open", "high", "low", "close")), starts=np.array([0]),
                      limit=np.array([100.0]), stop=np.array([95.0]), targets=np.array([used]), entry_window=2,
                      horizon=50, costs=FREE, weights=m.weights(len(used)), management=m)
    return round(float(out["r"][0]), 6) if out["complete"][0] else None


FILL = (99.0, 99.5, 98.8, 99.2)                            # rempli à 99 (ouverture sous la limite 100)
UP_TO = {1: (99.2, 102.5, 99.2, 101.0), 3: (99.2, 106.5, 99.2, 106.0)}


def test_each_stop_rule_by_hand():
    back_to_stop = (101.0, 101.0, 94.0, 94.5)               # après TP1 : tout redescend sous le stop initial
    rows = [FILL, UP_TO[1], back_to_stop]
    assert run(tr.Management(1), rows) == pytest.approx((102 - 99) / 5)                          # tout à TP1
    assert run(tr.Management(2, 0.5, tr.FIXED, 0), rows) == pytest.approx((0.5 * 102 + 0.5 * 95 - 99) / 5)
    assert run(tr.Management(2, 0.5, tr.ENTRY, 0), rows) == pytest.approx((0.5 * 102 + 0.5 * 100 - 99) / 5)
    assert run(tr.Management(2, 0.5, tr.TRAIL, 1), rows) == pytest.approx((0.5 * 102 + 0.5 * 100 - 99) / 5)
    # Après TP3 : suiveur à 1 → stop à TP2 (104) ; à 2 → TP1 (102) ; à l'entrée → 100 ; fixe → 95.
    rows = [FILL, UP_TO[3], (106.0, 106.0, 94.0, 94.5)]
    w = tr.Management(5, 0.6).weights(5)
    sold = w[0] * 102 + w[1] * 104 + w[2] * 106
    rest = 1 - w[:3].sum()
    for rule, lag, stop in ((tr.TRAIL, 1, 104), (tr.TRAIL, 2, 102), (tr.ENTRY, 0, 100), (tr.FIXED, 0, 95)):
        assert run(tr.Management(5, 0.6, rule, lag), rows) == pytest.approx((sold + rest * stop - 99) / 5), rule


def day_of_signal(day: int, after_tp1: str) -> list[tuple]:
    """Un jour de bougies 15 min : signal à 10:00 (entrée 100, stop 95), rempli à 99, TP1 touché, puis soit
    effondrement sous le stop (« crash »), soit montée jusqu'à 111 (« rally »)."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 40
    if after_tp1 == "nofill":                                # le prix ne revient jamais à l'entrée : jamais rempli
        return rows + [(101.0, 101.0, 101.0, 101.0)] * 56
    rows += [FILL, (100.6, 102.5, 100.5, 101.0)]             # TP1 touché, la bougie reste au-dessus de l'entrée 1
    rows += [(101.0, 101.0, 94.0, 94.5)] if after_tp1 == "crash" else [(101.0, 111.0, 101.0, 110.5)]
    rows += [(100.0, 100.0, 100.0, 100.0)] * (96 - len(rows))
    return rows


def group(pattern: list[str]):
    rows, signals = [], []
    for day, kind in enumerate(pattern):
        rows += day_of_signal(day, kind)
        signals.append({"symbol": "ABCUSDT", "received_at": (T0 + pd.Timedelta(days=day, hours=10)).isoformat(),
                        "entry": 100.0, "stop": 95.0, "targets": TARGETS})
    bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    bars.insert(0, "open_time", T0 + pd.to_timedelta(np.arange(len(bars)) * 15, "min"))
    return signals, {"ABCUSDT": bars}


def study(pattern, **kwargs):
    signals, candles = group(pattern)
    return mg.compare(signals, candles, entry_window=4, horizon=40, costs=FREE, samples=300, seed=1, **kwargs)


def test_the_best_management_is_chosen_on_the_past_and_confirmed_on_unseen_signals():
    # Le groupe touche toujours TP1 puis s'effondre : tout vendre à TP1 est meilleur, et cela se confirme.
    out = study(["crash"] * 60)
    assert out["signals"] == 60 and out["variants"] == 97
    assert out["best"]["key"].startswith("tp1") or out["best"]["label"].startswith("tout vendu à TP1")
    assert out["choice_period"][0] == "2026-01-01" and out["confirm_period"][0] == "2026-02-10"   # 40 premiers jours, puis 20
    assert out["best"]["confirm"]["r_mean"] == pytest.approx(0.6, abs=1e-4) and out["best"]["confirm"]["signals"] == 20
    owner = (5 / 15 * 102 + 10 / 15 * 100 - 99) / 5
    assert out["current_confirm"]["r_mean"] == pytest.approx(owner, abs=1e-4)
    assert out["difference_confirm"]["r_mean"] == pytest.approx(0.6 - owner, abs=1e-4)
    assert out["difference_confirm"]["ic95"][0] > 0
    assert "fait mieux que ta gestion" in out["conclusion"]
    assert [row["r_mean_choice"] for row in out["top_on_choice"]] == sorted(
        [row["r_mean_choice"] for row in out["top_on_choice"]], reverse=True)


def test_a_change_of_behaviour_after_the_choice_is_caught_by_the_confirmation():
    # Les deux premiers tiers récompensent « tout à TP1 », le dernier tiers récompense les objectifs lointains.
    out = study(["crash"] * 40 + ["rally"] * 20)
    assert out["best"]["label"].startswith("tout vendu à TP1")
    assert out["difference_confirm"]["ic95"][1] < 0 and "MOINS bien" in out["conclusion"]


def test_no_conclusion_without_enough_unseen_signals_and_current_already_best():
    few = study(["crash"] * 30)
    assert "trop peu de signaux" in few["conclusion"] and "best" not in few
    sparse_signals, candles = group(["rally"] * 45)
    for k, s in enumerate(sparse_signals[30:]):                      # dernier tiers sur 3 jours seulement
        s["received_at"] = (T0 + pd.Timedelta(days=30 + k % 3, hours=10)).isoformat()
    out = mg.compare(sparse_signals, candles, entry_window=4, horizon=40, costs=FREE, samples=300, seed=1)
    assert "confirmation impossible" in out["conclusion"]
    rally = study(["rally"] * 60, current=tr.Management(5, None, tr.TRAIL, 2))
    assert rally["best"]["confirm"]["r_mean"] >= rally["current_confirm"]["r_mean"]


def test_replay_all_matches_the_single_signal_replay_for_the_owner_management():
    signals, candles = group(["crash", "rally"] * 5)
    table = mg.replay_all(signals, candles, [tr.OWNER], entry_window=4, horizon=40, costs=FREE)
    bars = candles["ABCUSDT"]
    for i, s in enumerate(signals):
        after = bars[bars["open_time"] >= pd.Timestamp(s["received_at"])].reset_index(drop=True)
        _, r = tr.replay_trailing(after, entry=100.0, stop=95.0, targets=TARGETS, entry_window=4, max_hold=40, costs=FREE)
        assert table.loc[i, tr.OWNER.key] == pytest.approx(r, abs=1e-4)


def test_an_unfilled_signal_is_left_out_not_counted_as_zero():
    out = study(["crash"] * 30 + ["nofill"] + ["crash"] * 29)
    assert out["signals"] == 59 and out["best"]["confirm"]["r_mean"] == pytest.approx(0.6, abs=1e-4)
    assert out["best"]["choice"]["r_mean"] == pytest.approx(0.6, abs=1e-4)


def test_a_single_signal_shows_a_few_managements_on_the_same_blind_orders():
    rng = np.random.default_rng(2)
    n = 4000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    opens = np.r_[100.0, close[:-1]]
    frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, close) * 1.002, "low": np.minimum(opens, close) * 0.998,
                          "close": close, "atr14": close * 0.006,
                          "decision_time": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")})
    rows = tr.showcase(frame, entry_offset_atr=0.0, stop_atr=1.5, targets_r=[0.3, 0.6, 1.0, 1.5, 2.5, 3.5, 5.0],
                       mask=None, entry_window=8, horizon=200, costs=FREE)
    assert [r["key"] for r in rows] == [m.key for m in tr.SHOWCASE] and sum(r["owner"] for r in rows) == 1
    owner = next(r for r in rows if r["owner"])
    r, _ = tr.blind_trailing(frame, entry_offset=0.0, entry_offset_atr=0.0, stop_atr=1.5, target_rs=[0.3, 0.6, 1.0, 1.5, 2.5],
                             entry_window=8, horizon=200, costs=FREE)
    assert owner["r_mean"] == pytest.approx(float(r.mean()), abs=1e-4) and owner["samples"] == len(r)
    first = rows[0]                                                   # tout à TP1 : gagne plus souvent que la gestion
    assert first["win_share"] > owner["win_share"] * 0.9 and len({r["r_mean"] for r in rows}) > 3
