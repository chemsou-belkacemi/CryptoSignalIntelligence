"""Gestion « stop suiveur » du propriétaire (external/trailing.py) : chaque cas calculé à la main, puis équivalence
entre le signal réel et les ordres aveugles du taux de base. Bougies SYNTHÉTIQUES."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.external import trailing as tr

FREE = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
COSTS = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)
T0 = pd.Timestamp("2026-03-02", tz="UTC")
W = tr.early_weights(5)                                  # 5/15, 4/15, 3/15, 2/15, 1/15
TARGETS = [102.0, 104.0, 106.0, 108.0, 110.0, 120.0]     # 6 objectifs : seuls les 5 premiers servent


def bars(rows) -> pd.DataFrame:
    return pd.DataFrame([(T0 + k * pd.Timedelta(minutes=15), *r) for k, r in enumerate(rows)],
                        columns=["open_time", "open", "high", "low", "close"])


def flat(price: float, count: int = 1):
    return [(price, price, price, price)] * count


def replay(rows, *, costs=FREE, max_hold: int = 50, entry: float = 100.0, stop: float = 95.0):
    return tr.replay_trailing(bars(rows), entry=entry, stop=stop, targets=TARGETS, entry_window=4, max_hold=max_hold,
                              costs=costs)


FILL = (99.0, 99.5, 98.8, 99.2)                          # ouverture sous la limite 100 : rempli à 99


def with_costs(first_weight: float, tp1: float = 102.0, rest_price: float = 100.0, fee: float = 1e-3) -> float:
    """R à la main : achat à 99 + glissement, TP1 puis le reste au stop remonté (entrée 1). `fee` : 10 pb pour COSTS ;
    les tests qui passent par la configuration donnent ses frais centraux (7,5 pb depuis le 2026-10-03)."""
    m = 3e-4
    buy = 99 * (1 + m)
    sold = (first_weight * tp1 + (1 - first_weight) * rest_price) * (1 - m) * (1 - fee)
    return (sold - buy * (1 + fee)) / 5


def test_weights_and_the_stop_follows_two_targets_behind():
    assert pytest.approx([5 / 15, 4 / 15, 3 / 15, 2 / 15, 1 / 15]) == W and tr.used_targets(TARGETS) == TARGETS[:5]
    entry, targets = np.full(6, 100.0), np.tile(np.array(TARGETS[:5]), (6, 1))
    stops = tr.trail_stop(entry, targets, np.arange(6), np.full(6, 95.0))
    assert stops.tolist() == [95.0, 100.0, 100.0, 102.0, 104.0, 106.0]    # 0, TP1, TP2, TP3→TP1, TP4→TP2, TP5→TP3


def test_tp1_then_back_to_the_entry_is_a_small_win_not_a_loss():
    issue, r = replay([FILL, (99.2, 102.5, 99.2, 101.0), (101.0, 101.0, 98.9, 99.0)])
    # Rempli à 99 (ouverture sous la limite) ; TP1 (5/15 à 102) puis stop remonté à l'ENTRÉE 1 du signal (100) :
    # le reste (10/15) est vendu à 100.
    assert issue == "TP1_PUIS_SL" and r == pytest.approx((5 / 15 * 102 + 10 / 15 * 100 - 99) / 5, abs=1e-4)
    stop_first = replay([FILL, (99.2, 99.5, 94.0, 94.5)])
    assert stop_first == ("SL", pytest.approx((95 - 99) / 5, abs=1e-4))


def test_tp3_moves_the_stop_to_tp1_and_all_targets_sell_everything():
    issue, r = replay([FILL, (99.2, 106.5, 99.2, 106.0), (106.0, 106.0, 101.0, 101.5)])
    # TP1, TP2, TP3 dans la bougie : stop à TP1 (102) dès la suivante ; le plus bas 101 le touche → reste à 102.
    sold = 5 / 15 * 102 + 4 / 15 * 104 + 3 / 15 * 106
    assert issue == "TP3_PUIS_SL" and r == pytest.approx((sold + 3 / 15 * 102 - 99) / 5, abs=1e-4)
    issue, r = replay([FILL, (99.2, 111.0, 99.2, 110.0)])
    everything = 5 / 15 * 102 + 4 / 15 * 104 + 3 / 15 * 106 + 2 / 15 * 108 + 1 / 15 * 110
    assert issue == "TOUS_TP" and r == pytest.approx((everything - 99) / 5, abs=1e-4)


def test_pessimistic_cases():
    # Objectif et stop dans la même bougie : stop d'abord.
    assert replay([FILL, (99.2, 103.0, 94.0, 100.0)]) == ("SL", pytest.approx(-0.8, abs=1e-4))
    # TP1 puis, dans la même bougie, retour sous le nouveau stop (entrée 1 = 100) : on suppose le pire.
    issue, r = replay([FILL, (99.2, 102.5, 98.5, 99.0)])
    assert issue == "TP1_PUIS_SL" and r == pytest.approx((5 / 15 * 102 + 10 / 15 * 100 - 99) / 5, abs=1e-4)
    # Ouverture sous le stop : vendu à l'ouverture, pas au prix du stop.
    assert replay([FILL, (93.0, 93.5, 92.0, 92.5)]) == ("SL", pytest.approx((93 - 99) / 5, abs=1e-4))
    # Remplissage « au contact » (ouverture au-dessus de la limite) : pas d'objectif dans cette bougie.
    touched = replay([(101.0, 103.0, 99.5, 100.5), *flat(100.5, 60)])
    assert touched == ("TEMPS", pytest.approx(0.1, abs=1e-4))         # rempli à 100, vendu à 100,5 au bout du temps
    # Objectifs déjà dépassés à l'ouverture : vendus avant le stop de la bougie.
    issue, r = replay([FILL, (102.5, 102.5, 94.0, 95.0)])
    assert issue == "TP1_PUIS_SL" and r == pytest.approx((5 / 15 * 102 + 10 / 15 * 95 - 99) / 5, abs=1e-4)


def test_unfilled_pending_and_costs():
    assert replay(flat(101.0, 10)) == ("UNFILLED", None)
    assert replay(flat(101.0, 2)) == ("PENDING", None)
    assert replay([FILL, *flat(99.2, 3)]) == ("PENDING", None)                 # ni objectif ni stop, temps non écoulé
    issue, r = replay([FILL, (99.2, 102.5, 99.2, 101.0), (101.0, 101.0, 98.9, 99.0)], costs=COSTS)
    buy, m, fee = 99 * (1 + 3e-4), 3e-4, 1e-3
    expected = (5 / 15 * 102 * (1 - m) * (1 - fee) + 10 / 15 * 100 * (1 - m) * (1 - fee) - buy * (1 + fee)) / 5
    assert issue == "TP1_PUIS_SL" and r == pytest.approx(expected, abs=1e-4)


def test_blind_orders_are_resolved_exactly_like_a_real_signal():
    rng = np.random.default_rng(3)
    n = 3000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    opens = np.r_[100.0, close[:-1]]
    high = np.maximum(opens, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(opens, close) * (1 - rng.uniform(0, 0.004, n))
    frame = pd.DataFrame({"open_time": T0 + pd.to_timedelta(np.arange(n) * 15, "min"), "open": opens, "high": high,
                          "low": low, "close": close, "atr14": close * 0.006})
    frame["decision_time"] = frame["open_time"] + pd.Timedelta(minutes=15)
    rs = [0.3, 0.6, 1.0, 1.5, 2.5]
    blind_r, times = tr.blind_trailing(frame, entry_offset=-0.001, stop_atr=1.5, target_rs=rs, entry_window=8,
                                       horizon=96, costs=COSTS)
    assert len(blind_r) > 2000
    checked = 0
    by_time = dict(zip(pd.DatetimeIndex(times), blind_r, strict=True))
    for i in rng.choice(np.arange(10, n - 200), size=40, replace=False):
        limit = close[i] * (1 - 0.001)
        stop = limit - 1.5 * close[i] * 0.006
        targets = [limit + r * (limit - stop) for r in rs]
        after = frame.iloc[i + 1:].reset_index(drop=True)
        issue, r = tr.replay_trailing(after, entry=limit, stop=stop, targets=targets, entry_window=8, max_hold=96,
                                      costs=COSTS, tp_count=5)
        moment = frame["decision_time"].iloc[i]
        if r is None:
            assert moment not in by_time
            continue
        assert by_time[moment] == pytest.approx(r, abs=1e-4), (i, issue)
        checked += 1
    assert checked > 25


# --- Branchements : avis, suivi des signaux enregistrés, bilan d'historique ------------------------------------------

def test_the_verdict_judges_the_owner_management(settings, monkeypatch):
    from datetime import timedelta

    from crypto_signal_intelligence.data.store import CandleStore
    from crypto_signal_intelligence.external import evaluate as ev
    from crypto_signal_intelligence.features import indicators as ind

    from .conftest import canonical
    store = CandleStore(settings.data_dir)
    setup = canonical(4000, symbol="ETHUSDT", seed=5)
    store.save(setup, "ETHUSDT", "15m")
    store.save(canonical(1000, "1h", symbol="ETHUSDT", seed=6), "ETHUSDT", "1h")
    store.save(canonical(1000, "1h", symbol="BTCUSDT", seed=7), "BTCUSDT", "1h")
    close = float(setup["close"].iloc[-1])
    atr = float(ind.atr(setup["high"], setup["low"], setup["close"]).iloc[-1])
    now = setup["available_at"].iloc[-1].to_pydatetime() + timedelta(minutes=1)
    entry = close * 1.001
    text = (f"PAIR: ETH/USDT\nENTRY 1: {entry:.2f}\nT1: {entry + 0.3 * atr:.2f}\nT2: {entry + 0.6 * atr:.2f}\n"
            f"T3: {entry + 1.2 * atr:.2f}\nSL: {entry - 1.5 * atr:.2f}\nPLATFORM: Binance")
    judged = ev.evaluate(settings, text, source="G", now=now, record=False)
    trail = judged.trailing
    assert judged.managements and len(judged.managements) == 8 and sum(m["owner"] for m in judged.managements) == 1
    # Régime conditionné (cas réel de MOVR) : l'aperçu des gestions filtre aussi par régime, sans erreur.
    from crypto_signal_intelligence.external import trailing as trailing_module
    real_rate = trailing_module.trailing_rate
    monkeypatch.setattr(trailing_module, "trailing_rate", lambda *a, **k: real_rate(*a, **k) | {"regime_conditioned": True})
    conditioned = ev.evaluate(settings, text, source="G", now=now, record=False)
    assert conditioned.trailing["regime_conditioned"] and conditioned.managements
    assert conditioned.managements[0]["samples"] < judged.managements[0]["samples"]      # filtré par régime
    monkeypatch.setattr(trailing_module, "trailing_rate", real_rate)
    assert trail and trail["samples"] > 0 and trail["tp_count"] == 3 and judged.base_rate is not None
    assert "RR TP1 net de coûts" not in {c.label for c in judged.failed}      # TP1 proche : pas un veto ici
    calls = []
    real = ev.ExternalEvaluation

    def with_ci(ci):
        monkeypatch.setattr("crypto_signal_intelligence.external.trailing.trailing_rate",
                            lambda *a, **k: calls.append(k) or {**trail, "expectancy_r_ci95": ci, "samples": 10_000})
        return ev.evaluate(settings, text, source="G", now=now, record=False).verdict

    assert with_ci((0.05, 0.2)) == "FAVORABLE" and with_ci((-0.3, -0.1)) == "DEFAVORABLE"
    assert with_ci((-0.1, 0.1)) == "INDETERMINE" and calls[-1]["horizon"] == settings.external.trail_max_hold_bars
    settings.external.management = "tp1"
    old = ev.evaluate(settings, text, source="G", now=now, record=False)
    assert old.trailing is None and isinstance(old, real)
    settings.external.management = "stop_suiveur"
    recorded = ev.evaluate(settings, text, source="G", now=now)
    from crypto_signal_intelligence.external.registry import ExternalSignalRegistry
    row = ExternalSignalRegistry(settings.external_db).recent()[0]
    assert row["base_expectancy_r"] == recorded.trailing["expectancy_r"]


def test_recorded_signals_are_resolved_with_the_owner_management(settings):
    from datetime import UTC, datetime

    from crypto_signal_intelligence.data.schema import RAW_COLUMNS, normalize
    from crypto_signal_intelligence.data.store import CandleStore
    from crypto_signal_intelligence.external.registry import ExternalSignalRegistry, resolve_pending
    received = datetime(2026, 3, 2, tzinfo=UTC)
    rows = [FILL, (99.2, 102.5, 99.2, 101.0), (101.0, 101.0, 98.9, 99.0)]
    open_ms = [int((T0 + k * pd.Timedelta(minutes=15)).timestamp() * 1000) for k in range(len(rows))]
    raw = pd.DataFrame({"open_time": open_ms, "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                        "low": [r[2] for r in rows], "close": [r[3] for r in rows], "volume": 1.0,
                        "close_time": [m + 899_999 for m in open_ms], "quote_volume": 100.0, "count": 1,
                        "taker_buy_base": 0.5, "taker_buy_quote": 50.0, "ignore": 0})[RAW_COLUMNS]
    CandleStore(settings.data_dir).save(normalize(raw, unit="ms", symbol="ABCUSDT", timeframe="15m", source="SYNTHETIC",
                                                  source_version="test", now=received, latency_seconds=2), "ABCUSDT", "15m")
    registry = ExternalSignalRegistry(settings.external_db)
    registry.record(received_at=received, source="G", content_hash="h", template="structured", symbol="ABCUSDT",
                    entry=100.0, stop=95.0, tp1=102.0, targets=TARGETS, decision_time=None, close=100.5,
                    verdict="INDETERMINE", p_tp1=None, base_expectancy_r=None, evaluation={}, raw_text="x", resolvable=True)
    assert resolve_pending(settings, registry, now=datetime(2026, 4, 1, tzinfo=UTC)) == {"TP1_FIRST": 1}
    row = registry.recent()[0]
    assert row["outcome"] == "TP1_FIRST" and row["outcome_r"] == pytest.approx(with_costs(5 / 15, fee=settings.costs["central"].fee_bps / 1e4), abs=1e-4)


def test_history_audit_measures_the_owner_management_and_proves_with_it(settings):
    from crypto_signal_intelligence.external import audit as au
    text = "#ABC/USDT\nEntry1: 100\nTP1: 102\nTP2: 104\nTP3: 106\nStop: 95"
    candles = bars([*flat(101.0, 40), FILL, (99.2, 102.5, 99.2, 101.0), (101.0, 101.0, 98.9, 99.0), *flat(99.0, 5)])
    item = au.HistoryItem(text=text, received_at=(T0 + pd.Timedelta(hours=10)).to_pydatetime())
    report = au.audit(settings, [item], now=(T0 + pd.Timedelta(days=60)).to_pydatetime(), source="G",
                      bars_for=lambda symbol, start, end: candles)
    outcome = report.rows[0].outcomes[au.TRAILING]
    w = tr.early_weights(3)
    assert outcome["issue"] == "TP1_PUIS_SL" and outcome["r"] == pytest.approx(with_costs(w[0], fee=settings.costs["central"].fee_bps / 1e4), abs=1e-4)
    assert report.summary["G"]["preuve"]["convention"] == au.TRAILING
