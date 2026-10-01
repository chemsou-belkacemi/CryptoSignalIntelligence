"""Variables de positionnement (docs/DERIVATIVES.md) : causalité, mutations, cibles. Données SYNTHÉTIQUES."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.derivatives import features as fx

from .conftest import canonical

START = pd.Timestamp("2024-01-01", tz="UTC")
DAYS = 150


def synthetic(seed: int = 0, *, days: int = DAYS, symbol: str = "ETHUSDT"):
    """Spot 1 h, financement toutes les 8 h, prime 1 h et metrics 5 min, avec leurs `available_at` déclarés."""
    rng = np.random.default_rng(seed)
    spot = canonical(24 * days, "1h", symbol=symbol, start="2024-01-01", seed=seed)
    settle = pd.date_range(START, periods=3 * days, freq="8h") + pd.Timedelta(milliseconds=2)
    funding = pd.DataFrame({"time": settle, "rate": rng.normal(1e-4, 1e-4, len(settle)), "interval_hours": 8.0})
    funding["available_at"] = funding["time"] + pd.Timedelta(seconds=60)
    hours = pd.date_range(START, periods=24 * days, freq="1h")
    premium = pd.DataFrame({"time": hours, "open": 0.0, "high": 0.0, "low": 0.0,
                            "close": rng.normal(0, 5e-4, len(hours))})
    premium["available_at"] = premium["time"] + pd.Timedelta(hours=1, seconds=2)
    stamps = pd.date_range(START, periods=288 * days, freq="5min")
    metrics = pd.DataFrame({"time": stamps, "oi": 1.0,
                            "oi_value": 1e9 * np.exp(np.cumsum(rng.normal(0, 2e-3, len(stamps)))),
                            "top_accounts_ratio": 1.0, "top_positions_ratio": 1.0,
                            "accounts_ratio": 1.5 + np.cumsum(rng.normal(0, 5e-3, len(stamps))), "taker_ratio": 1.0})
    metrics["available_at"] = metrics["time"] + pd.Timedelta(seconds=602)
    return spot, funding, premium, metrics


def test_decisions_every_four_hours_with_contiguous_forward_returns():
    spot, funding, premium, metrics = synthetic()
    frame = fx.decision_frame(spot, funding, premium, metrics)
    assert (frame["decision_time"].dt.hour % 4 == 0).all() and frame["decision_time"].is_monotonic_increasing
    row = frame.index[frame["decision_time"] == pd.Timestamp("2024-03-01 08:00", tz="UTC")][0]
    t = spot.index[spot["open_time"] == frame.loc[row, "open_time"]][0]
    assert frame.loc[row, "ret_24"] == pytest.approx(spot["close"].iloc[t + 24] / spot["open"].iloc[t + 1] - 1)
    assert frame.loc[row, "spot_ret_24h"] == pytest.approx(spot["close"].iloc[t] / spot["close"].iloc[t - 24] - 1)
    holed = spot.drop(index=t + 5).reset_index(drop=True)                 # bougie manquante dans la fenêtre
    again = fx.decision_frame(holed, funding, premium, metrics).set_index("decision_time")
    assert np.isnan(again.loc[pd.Timestamp("2024-03-01 08:00", tz="UTC"), "ret_24"])
    # Seuils : aucun centile avant 60 jours d'historique ; ensuite, toutes les variables sont définies.
    early = frame[frame["decision_time"] < START + pd.Timedelta(days=55)]
    late = frame[frame["decision_time"] > START + pd.Timedelta(days=100)]
    assert early["funding_3d_q10"].isna().all() and late[list(fx.FEATURES)].notna().all().all()


def test_thresholds_never_include_the_current_value():
    _, funding, premium, _ = synthetic()
    base = fx.funding_table(funding)
    shocked = funding.copy()
    shocked.loc[shocked.index[-1], "rate"] = -1.0                         # dernier règlement extrême
    after = fx.funding_table(shocked)
    assert after["funding_3d_q10"].iloc[-1] == base["funding_3d_q10"].iloc[-1]
    assert after["funding_3d"].iloc[-1] < base["funding_3d"].iloc[-1]
    p_base = fx.premium_table(premium)
    p_shock = premium.copy()
    p_shock.loc[p_shock.index[-1], "close"] = -1.0
    assert fx.premium_table(p_shock)["premium_24h_q10"].iloc[-1] == p_base["premium_24h_q10"].iloc[-1]


def test_features_are_causal_and_each_leak_is_caught_by_its_own_family():
    sources = synthetic(seed=3)
    decisions = [START + pd.Timedelta(days=day, hours=hour) for day, hour in ((100, 8), (120, 16), (130, 4))]
    assert fx.causality_violations(*sources, decisions=decisions) == []
    assert set(fx.MUTATIONS) == {"funding", "premium", "metrics"}
    for name, (builder, family) in fx.MUTATIONS.items():
        flagged = {f for v in fx.causality_violations(*sources, decisions=decisions, builders={name: builder})
                   for f in v["features"]}
        assert flagged and flagged <= set(family), (name, flagged)


def test_declared_conditions_need_every_input_and_their_own_rule():
    frame = pd.DataFrame({"funding_3d": [-1.0, 1.0, np.nan], "funding_3d_q10": [0.0, 0.0, 0.0],
                          "premium_24h": [0.0] * 3, "premium_24h_q10": [np.nan] * 3,
                          "oi_change_24h": [-0.2, -0.2, -0.2], "oi_change_24h_q10": [-0.1] * 3,
                          "spot_ret_24h": [-0.01, 0.02, np.nan], "accounts_ratio": [1.0] * 3, "accounts_q10": [1.0] * 3})
    evaluable, events = fx.condition_masks(frame, "FUNDING_LOW")
    assert list(evaluable) == [True, True, False] and list(events) == [True, False, False]
    evaluable, events = fx.condition_masks(frame, "PREMIUM_DISCOUNT")
    assert not evaluable.any() and not events.any()                      # seuil inconnu : rien d'évaluable
    evaluable, events = fx.condition_masks(frame, "OI_FLUSH")
    assert list(events) == [True, False, False]                         # baisse de l'intérêt ouvert ET du prix
    assert not fx.condition_masks(frame, "ACCOUNTS_SHORT")[1].any()    # strictement sous le centile
    assert len(fx.CONDITIONS) == 4


def test_a_zero_open_interest_never_creates_a_flush():
    spot, funding, premium, metrics = synthetic(seed=5)
    hour = START + pd.Timedelta(days=110, hours=8)
    zero = metrics["time"] == hour - pd.Timedelta(minutes=15)          # dernière ligne lue à la décision de 08:00
    metrics.loc[zero, "oi_value"] = 0.0
    grid = fx.metrics_grid(metrics).set_index("available_at")
    assert np.isnan(grid.loc[hour, "oi_change_24h"]) and np.isnan(grid.loc[hour + pd.Timedelta(hours=24), "oi_change_24h"])
    frame = fx.decision_frame(spot, funding, premium, metrics).set_index("decision_time")
    for moment in (hour, hour + pd.Timedelta(hours=24)):
        row = frame.loc[[moment]].reset_index()
        assert not fx.condition_masks(row, "OI_FLUSH")[1].any()


def test_funding_window_holds_nine_settlements_whatever_the_jitter_and_is_per_eight_hours():
    _, funding, _, _ = synthetic(seed=6)
    jittered = funding.assign(time=funding["time"] + pd.to_timedelta(
        np.random.default_rng(1).integers(0, 50, len(funding)), unit="ms"))
    jittered["available_at"] = jittered["time"] + pd.Timedelta(seconds=60)
    exact, noisy = fx.funding_table(funding), fx.funding_table(jittered)
    np.testing.assert_allclose(noisy["funding_3d"].to_numpy(), exact["funding_3d"].to_numpy())
    assert exact["funding_3d"].iloc[-1] == pytest.approx(funding["rate"].iloc[-9:].mean())   # 9 règlements
    four_hours = funding.assign(interval_hours=4.0)                     # même taux par règlement, toutes les 4 h
    assert fx.funding_table(four_hours)["funding_3d"].iloc[-1] == pytest.approx(2 * funding["rate"].iloc[-9:].mean())


def test_metrics_thresholds_never_include_the_current_value():
    _, _, _, metrics = synthetic(seed=7)
    base = fx.metrics_grid(metrics)
    last_hour = base["available_at"].iloc[-1]
    read = metrics.index[metrics["available_at"] <= last_hour][-1]          # ligne lue à la dernière heure
    shocked = metrics.copy()
    shocked.loc[read, "accounts_ratio"] = 0.01                              # valeur extrême
    after = fx.metrics_grid(shocked)
    assert after["accounts_ratio"].iloc[-1] == 0.01                         # la grille la lit bien
    assert after["accounts_q10"].iloc[-1] == base["accounts_q10"].iloc[-1]
    assert after["oi_change_24h_q10"].iloc[-1] == base["oi_change_24h_q10"].iloc[-1]
