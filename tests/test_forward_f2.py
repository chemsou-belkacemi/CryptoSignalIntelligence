"""Test en direct F2_ECHELLES (forward/f2.py) : échelles calculées à la main, bout en bout depuis le registre shadow,
seuil de décision. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward import f2, registry
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.signals.outbox import SignalRegistry

from .conftest import PROJECT, canonical
from .test_signals import one_tp_signal

STEP = pd.Timedelta(minutes=15)
FIRST = pd.Timestamp("2026-09-29 12:15", tz="UTC")
DECISION = {"symbol": "SOLUSDT", "created_at": "2026-09-29T12:00:05+00:00",
            "entry_expires_at": "2026-09-29T12:30:05+00:00", "entry": 100.0, "stop": 98.0, "target": 104.0}
ETH_ONLY = HalalList(("ETHUSDT",), {}, "a" * 64, "b" * 64)


def bars(rows, start=FIRST):
    return pd.DataFrame([(start + k * STEP, *r) for k, r in enumerate(rows)], columns=["open_time", "open", "high", "low", "close"])


def test_ladder_targets_are_multiples_of_the_risk():
    assert f2.ladder_targets(100, 98, 4) == [102, 104, 106, 108]
    # 12:00:05 → première bougie 12:15 ; expiration 12:30:05 : seule la bougie 12:15 est entièrement valide.
    assert f2.window_bars(DECISION) == 1 and f2.entry_time(DECISION["created_at"]) == FIRST
    real = {"created_at": "2026-10-02T02:00:59+00:00", "entry_expires_at": "2026-10-02T02:30:02+00:00"}
    assert f2.window_bars(real) == 1                         # la bougie 02:30 (2 s de validité) ne compte pas


def test_ladder_by_hand_without_costs(monkeypatch):
    monkeypatch.setattr(f2, "cost_scenario", lambda symbol, scenario: f2.CostScenario(fee_bps=0, slippage_bps=0,
                                                                                       half_spread_bps=0))
    # Rempli à 100 (le plus bas passe sous 100) ; puis 102 et 104 dépassés ; retour à l'entrée → stop à 100.
    rows = [(101, 101, 99.5, 100.5), (100.5, 102.5, 100.2, 102.2), (102.2, 104.5, 102, 104), (104, 104, 99.5, 99.8)]
    rows += [(99.8, 100, 99.5, 99.8)] * 700
    frame = bars(rows)
    two = f2.play(frame, DECISION, "echelle_2", CENTRAL)          # parts 2/3 et 1/3 : +1 R × 2/3 + 2 R × 1/3
    assert two["outcome"] == "TOUS_TP" and two["r"] == pytest.approx(4 / 3, abs=1e-4)
    three = f2.play(frame, DECISION, "echelle_3", CENTRAL)        # parts 3/6, 2/6, 1/6 ; reste revendu à l'entrée
    assert three["outcome"] == "TP2_PUIS_SL" and three["r"] == pytest.approx(0.5 * 1 + 2 / 6 * 2, abs=1e-4)
    origin = f2.play(frame, DECISION, f2.ORIGIN, CENTRAL)         # objectif unique à 104 (dépassé : 104,5)
    assert origin["outcome"] == "TOUS_TP" and origin["r"] == pytest.approx(2.0)
    assert f2.play(bars([(101, 101.5, 100.5, 101)] * 700), DECISION, "echelle_5", CENTRAL)["outcome"] == "NON_REMPLI"
    late = [(101, 101.5, 100.5, 101), (100.5, 101, 99, 100)] + [(100, 100.5, 99.8, 100)] * 700
    assert f2.play(bars(late), DECISION, "echelle_3", CENTRAL)["outcome"] == "NON_REMPLI"   # après l'expiration


def test_costs_follow_the_common_model():
    central, adverse = f2.cost_scenario("SOLUSDT", CENTRAL), f2.cost_scenario("BTCUSDT", ADVERSE)
    assert (central.fee_bps, central.slippage_bps) == pytest.approx((7.5, 5.0))
    assert (adverse.fee_bps, adverse.slippage_bps) == pytest.approx((10.0, 4.0))


def test_f2_end_to_end_from_the_shadow_registry(settings):
    start_at = datetime(2026, 9, 29, 11, tzinfo=UTC)
    start = registry.start(settings, f2.TEST, now=start_at, allow_dirty=True, halal=ETH_ONLY)
    shadow = settings.root / "signals" / "shadow"
    SignalRegistry(settings.signals_db, shadow, root=settings.root).publish(
        one_tp_signal(), datetime(2026, 9, 29, 12, 0, 6, tzinfo=UTC))
    rows = [(2501, 2502, 2499, 2500.5), (2500.5, 2580, 2505, 2576)] + [(2576, 2580, 2575, 2577)] * 800
    candles = canonical(len(rows), "15m", symbol="ETHUSDT", start="2026-09-29 12:15")
    for i, column in enumerate(("open", "high", "low", "close")):
        candles[column] = [r[i] for r in rows]
    CandleStore(settings.data_dir).save(candles, "ETHUSDT", "15m")
    journal = registry.journal_for(settings, f2.TEST_ID)
    now = datetime(2026, 9, 29, 13, tzinfo=UTC)
    assert f2.record_decisions(settings, journal, start, now=now) == {"decisions": 1, "skipped": 0}
    assert f2.record_decisions(settings, journal, start, now=now) == {"decisions": 0, "skipped": 0}
    assert f2.resolve(settings, journal, now=now) == {}                       # échelles encore ouvertes
    later = now + timedelta(days=9)
    counts = f2.resolve(settings, journal, now=later)
    assert sum(counts.values()) == 1 and f2.resolve(settings, journal, now=later) == {}
    result = next(journal.entries({f2.RESOLUTION}))["data"]
    assert set(result["results"][CENTRAL]) == set(f2.VARIANTS) and result["results"][CENTRAL][f2.ORIGIN]["r"] > 1.5
    out = f2.stats(journal, start, now=later)
    assert out["pending"] == 0 and out["scenarios"][CENTRAL][f2.ORIGIN]["filled"] == 1
    assert set(out["verdicts"].values()) == {f2.RUNNING} and journal.verify()["ok"]


@pytest.mark.parametrize(("central", "adverse", "expected"), [
    ({"filled": 150, "diff_ci": (0.01, 0.2)}, {"diff_ci": (0.005, 0.1)}, f2.BETTER),
    ({"filled": 150, "diff_ci": (-0.2, -0.01)}, {"diff_ci": (-0.3, -0.02)}, f2.WORSE),
    ({"filled": 150, "diff_ci": (0.01, 0.2)}, {"diff_ci": (-0.01, 0.1)}, f2.NO_DIFFERENCE),
    ({"filled": 99, "diff_ci": (0.01, 0.2)}, {"diff_ci": (0.01, 0.1)}, f2.INSUFFICIENT),
    ({"filled": 150, "diff_ci": None}, {"diff_ci": (0.01, 0.1)}, f2.INSUFFICIENT),
])
def test_f2_decision_threshold(central, adverse, expected):
    rows = {CENTRAL: {"echelle_3": central}, ADVERSE: {"echelle_3": adverse}}
    assert f2.verdict(rows, ended=True)["echelle_3"] == expected
    assert f2.verdict(rows, ended=False)["echelle_3"] == f2.RUNNING


def test_f2_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f2.TEST_ID)
    assert registry.missing_fields(text) == []
    for value in ("echelle_2 à echelle_7", "100 signaux remplis", "1 − 0,05/6", "graine 20261003", "672 bougies",
                  "84 jours"):
        assert value in text, value


def test_unfilled_signals_are_excluded_from_every_variant():
    """Un signal non rempli l'est pour toutes les variantes : il ne pèse ni sur l'origine ni sur les écarts."""
    table = pd.DataFrame({"created_at": ["2026-09-29T12:00:05+00:00"] * 2 + ["2026-09-30T12:00:05+00:00"],
                          **{v: [1.0, None, 0.5] for v in f2.VARIANTS}})
    origin = f2._measure(table, f2.ORIGIN, 0.99, samples=200, seed=1)
    ladder = f2._measure(table, "echelle_4", 0.99, samples=200, seed=1)
    assert origin["filled"] == 2 and ladder["filled"] == 2 and ladder["diff_mean"] == 0.0
