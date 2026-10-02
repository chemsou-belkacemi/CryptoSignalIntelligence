"""Issue des signaux shadow de CSI (signals/outcomes.py) : calculée à la main sur des bougies synthétiques, puis de
bout en bout depuis le registre de publication. SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.signals import outcomes as oc
from crypto_signal_intelligence.signals.outbox import SignalRegistry

from .conftest import canonical
from .test_signals import one_tp_signal

STEP = pd.Timedelta(minutes=15)
CREATED = pd.Timestamp("2026-09-29 12:00:05", tz="UTC")
EXPIRES = CREATED + pd.Timedelta(minutes=30)          # 2 bougies de validité : 12:15 et 12:30
FREE = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
NOW = CREATED + pd.Timedelta(days=5)


FIRST = pd.Timestamp("2026-09-29 12:15", tz="UTC")


def bars(rows, start=FIRST):
    return pd.DataFrame([(start + k * STEP, *r) for k, r in enumerate(rows)], columns=["open_time", "open", "high", "low", "close"])


def resolve(rows, *, max_hold=4, now=NOW, start=None):
    frame = bars(rows) if start is None else bars(rows, start)
    return oc.resolve_signal(frame, created_at=CREATED, entry_expires_at=EXPIRES, entry=100.0, stop=98.0, target=104.0,
                             step=STEP, max_hold=max_hold, costs=FREE, now=now)


FLAT = (101, 101.5, 100.5, 101)


def test_outcomes_by_hand():
    # Bougie 12:15 : le plus bas passe sous 100 → rempli à 100 ; bougie suivante dépasse 104 → objectif, +2 R.
    outcome, r, filled = resolve([(101, 101, 99.5, 100.5), (100.5, 104.5, 100, 104)] + [FLAT] * 6)
    assert (outcome, r) == ("TP1_FIRST", pytest.approx(2.0)) and filled == datetime.fromisoformat("2026-09-29T12:15:00+00:00")
    assert resolve([(101, 101, 99.5, 100.5), (100, 100.2, 97.5, 98)] + [FLAT] * 6)[:2] == ("SL_FIRST", pytest.approx(-1.0))
    # Jamais revenu à 100 pendant les 2 bougies de validité : non rempli, même s'il y descend après.
    assert resolve([FLAT, FLAT, (100.5, 100.5, 99, 99.5)] + [FLAT] * 5)[0] == "UNFILLED"
    # Ni objectif ni stop en 4 bougies : sortie à la clôture.
    assert resolve([(101, 101, 99.5, 100.5)] + [(100.5, 101, 100.2, 101)] * 7)[:2] == ("TIMEOUT", pytest.approx(0.5))


def test_waits_for_candles_then_marks_a_gap():
    assert resolve([(101, 101, 99.5, 100.5)])[0] == "PENDING"                   # horizon pas encore écoulé
    late = pd.Timestamp("2026-09-29 13:00", tz="UTC")
    assert resolve([FLAT] * 8, start=late, now=CREATED + pd.Timedelta(hours=6))[0] == "PENDING"
    assert resolve([FLAT] * 8, start=late)[0] == "TROU"                         # bougie d'entrée absente, 5 jours après


def test_end_to_end_from_the_registry(settings):
    shadow = settings.root / "signals" / "shadow"
    SignalRegistry(settings.signals_db, shadow, root=settings.root).publish(one_tp_signal(), CREATED.to_pydatetime())
    rows = [(2501, 2502, 2499, 2500.5), (2500.5, 2580, 2500, 2576)] + [(2576, 2577, 2575, 2576)] * 100
    candles = canonical(len(rows), "15m", symbol="ETHUSDT", start="2026-09-29 12:15")
    for i, column in enumerate(("open", "high", "low", "close")):
        candles[column] = [r[i] for r in rows]
    CandleStore(settings.data_dir).save(candles, "ETHUSDT", "15m")
    assert oc.resolve(settings, now=(CREATED + pd.Timedelta(minutes=20)).to_pydatetime()) == {}  # aucune bougie close
    # Les bougies postérieures à « maintenant » ne sont jamais lues (garde de causalité).
    assert resolve([(101, 101, 99.5, 100.5), (100.5, 104.5, 100, 104)] + [FLAT] * 6,
                   now=FIRST + STEP)[0] == "PENDING"
    counts = oc.resolve(settings, now=NOW.to_pydatetime())
    assert counts == {"TP1_FIRST": 1}
    assert oc.resolve(settings, now=NOW.to_pydatetime()) == {}                                  # une seule fois
    stored = oc.outcomes(settings)["CSI-TEST-1"]
    assert stored["outcome"] == "TP1_FIRST" and stored["r"] > 1.5 and stored["strategy"] == "DONCHIAN_VOLUME_BREAKOUT"
    assert oc.summary(settings) == [{"strategy": "DONCHIAN_VOLUME_BREAKOUT", "resolved": 1, "filled": 1, "unfilled": 0,
                                     "r_mean": stored["r"], "win_share": 1.0}]


def test_no_signal_registry_means_nothing_to_resolve(settings):
    assert oc.resolve(settings, now=NOW.to_pydatetime()) == {} and oc.summary(settings) == []
    assert timedelta(days=5) == NOW - CREATED
