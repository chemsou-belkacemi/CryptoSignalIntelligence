"""Assistant de marché (assistant/, docs/ASSISTANT.md) : régimes et frontières, les deux configurations (cas positif et
négatifs), chaque filtre, score, gestion et résolution calculées à la main, placebos reproductibles, boîte Telegram,
état et résumé, routes de l'API, carte. Données SYNTHÉTIQUES : rien ici ne dit ce que vaut l'assistant sur le marché
(c'est le test en direct F18 qui le mesure)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi, make_handler
from crypto_signal_intelligence.assistant import evaluate as ev
from crypto_signal_intelligence.assistant import outbox, state
from crypto_signal_intelligence.assistant import rules as R
from crypto_signal_intelligence.forward import liquidity_log
from crypto_signal_intelligence.forward.costs import costs_for

T = pd.Timestamp("2026-10-09 08:00", tz="UTC")
NOW = T + pd.Timedelta(minutes=5)
GREEN = {"color": "VERT", "explanation": "feu vert"}
NO_DISCIPLINE = {"active": {}, "rest_until": {}, "calls_today": 0}
GOOD_BOOK = {"bids": [[100.0, 500.0], [99.9, 500.0], [99.5, 500.0]], "asks": [[100.1, 50.0], [100.2, 500.0], [100.9, 200.0]]}


# --- Fabrication de bougies ---------------------------------------------------------------------------------------------

def path4h(segments) -> tuple[np.ndarray, np.ndarray]:
    """Clôtures 4 h linéaires par segment (n barres, de a vers b, volume quote par barre)."""
    closes, vols = [], []
    for n, a, b, v in segments:
        closes += list(np.linspace(a, b, n, endpoint=False) + (b - a) / n)
        vols += [v] * n
    return np.array(closes), np.array(vols)


def hours_from_4h(closes, vols, *, end: pd.Timestamp = T, wick: float = 0.1) -> pd.DataFrame:
    """Bougies 1 h qui composent ces bougies 4 h (interpolation linéaire, mèches ± wick), `available_at` = clôture + 2 s."""
    n = len(closes)
    rows = []
    prev = closes[0]
    for k in range(n):
        t0 = end - (n - k) * R.H4
        for j in range(4):
            o = prev + (closes[k] - prev) * j / 4
            c = prev + (closes[k] - prev) * (j + 1) / 4
            rows.append((t0 + j * R.HOUR, o, max(o, c) + wick, min(o, c) - wick, c, vols[k] / 4))
        prev = closes[k]
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "quote_volume"])
    frame["available_at"] = frame["open_time"] + R.HOUR + pd.Timedelta(seconds=2)
    return frame


def uptrend_pullback(*, pull_low: float = 150.5, last_close: float = 161.0, last_volume: float = 8.0, peak: float = 195.0,
                     pre: float = 151.0):
    """HAUSSE : 10 j plats, montée 100→peak (28 j), chute vers 150 (5 j), consolidation 150↔160 à gros volume (22 j),
    3 barres à 151, repli vers `pull_low` (2 barres) puis clôture `last_close` avec le volume `last_volume`."""
    seg = [(10 * 6, 100, 100, 1.0), (28 * 6, 100, peak, 1.0), (5 * 6, peak, 150, 1.0)]
    for _ in range(11):
        seg += [(6, 150, 160, 3.0), (6, 160, 150, 3.0)]
    seg += [(3, pre, pre, 1.0), (2, pre, pull_low, 1.0), (1, pull_low, last_close, last_volume)]
    return hours_from_4h(*path4h(seg))


def range_rejection(*, dip: float = 100.0, last_close: float = 101.5, last_volume: float = 4.0):
    """RANGE : vague 100↔110 de période 12 jours, 4 jours en haut, chute à `dip`, clôture `last_close`."""
    seg = [(10 * 6, 105, 105, 1.0)]
    for _ in range(4):
        seg += [(36, 100, 110, 1.0), (36, 110, 100, 1.0)]
    seg += [(36, 100, 110, 1.0), (24, 110, 109, 1.0), (11, 109, dip, 1.0), (1, dip, last_close, last_volume)]
    return hours_from_4h(*path4h(seg))


def daily(closes, *, spread: float = 1.0) -> pd.DataFrame:
    c = np.asarray(closes, float)
    times = pd.date_range(T.floor("D") - len(c) * R.DAY, periods=len(c), freq="D")
    return pd.DataFrame({"open_time": times, "open": c, "high": c + spread, "low": c - spread, "close": c, "quote_volume": 1.0})


def run(frames: dict[str, pd.DataFrame], **changes) -> dict:
    forecast = {"available": True, "pairs": {s: {"move_24h_pct": 3.5} for s in frames} | {"RNGUSDT": {"move_24h_pct": 1.0}}}
    params = {"at": T, "now": NOW, "frames": frames, "light": GREEN, "forecast": forecast, "news": [], "macro_events": lambda d: [],
              "book": lambda s: GOOD_BOOK, "discipline": NO_DISCIPLINE, "btc_frame": uptrend_pullback()}
    return ev.evaluate(**(params | changes))


# --- 1. Régimes -------------------------------------------------------------------------------------------------------------

def test_regimes_four_cases_and_boundaries():
    up = R.daily_regime(daily(np.linspace(100, 160, 80)))
    assert up["regime"] == R.UP and up["close"] > up["ema50"] and up["ema20"] > up["ema50"] and up["ema50"] > up["ema50_past"]
    down = R.daily_regime(daily(np.linspace(160, 100, 80)))
    assert down["regime"] == R.DOWN
    flat = R.daily_regime(daily(np.full(80, 100.0)))
    assert flat["regime"] == R.UNDECIDED and flat["reason"] == "ni hausse ni baisse"      # clôture = EMA50 : ni > ni <
    short = R.daily_regime(daily(np.linspace(100, 160, 59)))
    assert short["regime"] == R.UNDECIDED and "59 journée" in short["reason"]
    # Frontière de la pente : montée puis 3 journées de recul qui laissent la clôture > EMA50 et EMA20 > EMA50, mais
    # l'EMA50 d'aujourd'hui ≤ celle d'il y a 3 jours → INDECIS (la règle exige les trois conditions).
    closes = list(np.linspace(100, 160, 77)) + [110.0, 110.0, 110.0]
    read = R.daily_regime(daily(closes))
    assert read["regime"] == R.UNDECIDED or read["ema50"] > read["ema50_past"]
    # Couloir : la lecture complète d'une vague claire donne RANGE, d'une tendance HAUSSE.
    assert ev.read_pair(range_rejection(), T)["regime"] == R.RANGE
    assert ev.read_pair(uptrend_pullback(), T)["regime"] == R.UP
    assert ev.read_pair(hours_from_4h(*path4h([(400, 160, 100, 1.0)])), T)["regime"] == R.DOWN


def test_corridor_needs_two_touches_each_and_four_daily_atr():
    levels = {"supports": [{"price": 100.0, "touches": 2}], "resistances": [{"price": 110.0, "touches": 2}]}
    assert R.corridor(levels, 2.5)["height_atr_d"] == pytest.approx(4.0)
    assert R.corridor(levels, 2.51) is None                                                 # hauteur < 4 ATR
    assert R.corridor({"supports": [{"price": 100.0, "touches": 1}], "resistances": levels["resistances"]}, 1.0) is None
    assert R.corridor({"supports": levels["supports"], "resistances": []}, 1.0) is None
    weak_first = {"supports": [{"price": 104.0, "touches": 1}, {"price": 100.0, "touches": 3}], "resistances": levels["resistances"]}
    assert R.corridor(weak_first, 1.0)["support"]["price"] == 100.0                           # le plus proche touché ≥ 2 fois


def test_unreadable_pairs_are_counted_not_traded():
    h1 = uptrend_pullback()
    assert ev.read_pair(h1[h1["open_time"] < T - R.H4], T)["regime"] == R.UNREADABLE        # bougie 4 h de clôture absente
    assert ev.read_pair(h1.iloc[:0], T)["regime"] == R.UNREADABLE
    out = run({"XUSDT": h1.iloc[:200]})
    assert out["regimes"][R.UNREADABLE] == 1 and out["calls"] == []


# --- 2. Configurations ------------------------------------------------------------------------------------------------------

def test_pullback_setup_positive_and_negative_cases():
    read = ev.read_pair(uptrend_pullback(), T)
    setup = read["setup"]
    assert read["regime"] == R.UP and setup["ok"] and setup["setup"] == R.PULLBACK
    assert setup["zone"][0] <= setup["pullback_low"] <= setup["zone"][1] and setup["entry"] == pytest.approx(161.0)
    assert setup["stop"] == pytest.approx(setup["pullback_low"] - R.STOP_ATR * read["levels"]["atr4"])
    assert setup["volume_multiple"] > 1 and setup["touches"] >= 2
    # Négatifs : repli resté au-dessus de la zone, clôture encore dans la zone, volume faible.
    assert ev.read_pair(uptrend_pullback(pre=157.0, pull_low=157.0), T)["setup"]["reason"] == "aucun repli dans la zone de valeur"
    assert "pas encore au-dessus" in ev.read_pair(uptrend_pullback(last_close=154.0), T)["setup"]["reason"]
    assert "volume" in ev.read_pair(uptrend_pullback(last_volume=0.5), T)["setup"]["reason"]


def test_rejection_setup_positive_and_negative_cases():
    read = ev.read_pair(range_rejection(), T)
    setup = read["setup"]
    assert read["regime"] == R.RANGE and setup["ok"] and setup["setup"] == R.REJECTION
    support = read["corridor"]["support"]["price"]
    assert setup["wick_low"] <= support + R.TOUCH_ATR * read["levels"]["atr4"] and setup["entry"] > support
    assert setup["stop"] == pytest.approx(setup["wick_low"] - R.STOP_ATR * read["levels"]["atr4"])
    assert "non touché" in ev.read_pair(range_rejection(dip=101.0), T)["setup"]["reason"]
    assert "volume" in ev.read_pair(range_rejection(last_volume=0.5), T)["setup"]["reason"]
    # Clôture sous le support : ce support n'est plus sous le prix, le couloir disparaît → INDECIS, aucune configuration.
    below = ev.read_pair(range_rejection(last_close=99.5), T)
    assert below["regime"] == R.UNDECIDED and "setup" not in below
    # Clôture exactement sur le support (fonction pure) : « pas au-dessus ».
    h4 = R.closed(R.aggregate(range_rejection(), "4h"), "4h", T)
    h4.loc[h4.index[-1], "close"] = read["corridor"]["support"]["price"]
    assert "pas au-dessus du support" in R.rejection_setup(h4, read["corridor"], read["levels"] | R.levels_4h(h4))["reason"]


def test_targets_round_to_the_tick_and_refuse_a_near_resistance():
    out = R.targets(100.123, 95.321, 120.0, Decimal("0.01"))
    assert (out["entry"], out["stop"], out["tp1"], out["tp2"]) == (100.12, 95.32, 104.92, 120.0)
    assert out["hard_stop"] == pytest.approx(100.12 - 1.5 * (100.12 - 95.32), abs=0.011) and out["r_tp2"] == pytest.approx((120 - 100.12) / 4.8, abs=0.01)
    assert R.targets(100.0, 95.0, None, None)["tp2"] == 110.0 and R.targets(100.0, 95.0, None, None)["tp2_source"] == "entrée + 2 R"
    near = R.targets(100.0, 95.0, 104.9, None)
    assert not near["ok"] and near["reason"] == R.RESISTANCE_NEAR
    assert not R.targets(100.0, 100.0, None, None)["ok"]


# --- 3. Filtres --------------------------------------------------------------------------------------------------------------

def test_a_call_comes_out_with_all_filters_passed():
    out = run({"RNGUSDT": range_rejection(), "UPUSDT": uptrend_pullback()})
    assert out["silence"] is None and out["size"] == "normale" and out["candidates"] == 2
    assert [c["symbol"] for c in out["calls"]] and out["refusals"] == [], out["refusals"]
    call = next(c for c in out["calls"] if c["symbol"] == "RNGUSDT")
    assert call["regime"] == R.RANGE and call["setup"] == R.REJECTION and call["stop"] < call["entry"] < call["tp1"] < call["tp2"]
    assert call["hard_stop"] == pytest.approx(call["entry"] - 1.5 * call["risk"], abs=1e-6)
    assert call["tp1"] == pytest.approx(call["entry"] + call["risk"], abs=1e-4)   # 8 chiffres significatifs sans pas de cotation
    assert call["plan_net_r"] >= 1.5 and 1 <= call["volatility"]["ratio"] <= 3 and call["volatility"]["source"] == "prévision H24 de F12"
    assert len(call["placebo_offsets_h"]) == 20 and call["score"] == sum(call["score_parts"].values())
    assert "aucun gain démontré" in call["note"] and len(call["explanation"]) == 3


def test_market_gate_red_light_btc_below_ema50_orange_and_unknown():
    frames = {"RNGUSDT": range_rejection()}
    red = run(frames, light={"color": "ROUGE", "explanation": ""})
    assert red["silence"].startswith("feu ROUGE") and red["calls"] == [] and red["candidates"] == 0
    bear = run(frames, btc_frame=hours_from_4h(*path4h([(400, 160, 100, 1.0)])))
    assert "BTC clôture ≤ EMA50" in bear["silence"] and bear["calls"] == []
    unknown_btc = run(frames, btc_frame=range_rejection().iloc[:0])
    assert "BTC inconnu" in unknown_btc["silence"]
    orange = run(frames, light={"color": "ORANGE", "explanation": ""})
    assert orange["size"] == "réduite" and orange["calls"][0]["size"] == "réduite"
    unknown = run(frames, light={"color": "INCONNU", "explanation": ""})
    assert unknown["light_note"] == "feu INCONNU : règle BTC seule" and unknown["calls"]


def test_book_filters_including_unreachable_book():
    frames = {"RNGUSDT": range_rejection()}
    assert run(frames, book=lambda s: None)["refusals"][0]["reason"] == R.BOOK_UNREACHABLE
    wide = {"bids": [[100.0, 100.0]], "asks": [[100.5, 100.0]]}                                # écart 0,5 %
    assert run(frames, book=lambda s: wide)["refusals"][0]["reason"] == R.BOOK_SPREAD
    thin = {"bids": [[100.0, 100.0]], "asks": [[100.1, 0.5], [100.4, 100.0]]}                   # 500 USDT glissent > 0,2 %
    assert run(frames, book=lambda s: thin)["refusals"][0]["reason"] == R.BOOK_SLIPPAGE
    sellers = {"bids": [[100.0, 10.0]], "asks": [[100.1, 10.0], [100.2, 500.0]]}
    assert run(frames, book=lambda s: sellers)["refusals"][0]["reason"] == R.BOOK_IMBALANCE
    assert R.book_check({"bids": [], "asks": []}, liquidity_log.book_metrics)["reason"] == R.BOOK_UNREACHABLE


def test_stop_versus_volatility_bounds_and_realized_fallback():
    assert R.stop_versus_volatility(100.0, 98.0, 2.0)["ok"] and R.stop_versus_volatility(100.0, 94.0, 2.0)["ok"]
    assert R.stop_versus_volatility(100.0, 98.0, 2.01)["reason"] == R.STOP_TIGHT
    assert R.stop_versus_volatility(100.0, 93.9, 2.0)["reason"] == R.STOP_WIDE
    assert R.stop_versus_volatility(100.0, 98.0, None)["reason"] == R.VOL_UNKNOWN
    frames = {"RNGUSDT": range_rejection()}
    tight = run(frames, forecast={"available": True, "pairs": {"RNGUSDT": {"move_24h_pct": 5.0}}})
    assert tight["refusals"][0]["reason"] == R.STOP_TIGHT and "prévision H24" in tight["refusals"][0]["detail"]
    realized = run(frames, forecast={"available": False})                      # sans prévision : volatilité réalisée
    reason = (realized["refusals"][0]["reason"] if realized["refusals"] else realized["calls"][0]["volatility"]["source"])
    assert reason in (R.STOP_TIGHT, R.STOP_WIDE, "volatilité réalisée 7 jours")
    h1 = range_rejection()
    move = R.realized_move_24h_pct(h1)
    expected = float(np.diff(np.log(h1["close"].to_numpy()[-169:])).std(ddof=1) * np.sqrt(24) * 100)
    assert move == pytest.approx(expected)


def test_plan_net_gain_filter_by_hand():
    c = costs_for("XUSDT", "central")
    net = R.plan_net_r(100.0, 95.0, 105.0, 115.0, "XUSDT")
    expected = (0.5 * 105 * (1 - c.market) * (1 - c.fee) + 0.5 * 115 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.market) * (1 + c.fee)) / 5
    assert net == pytest.approx(expected) and 1.9 < net < 2.0
    assert R.plan_net_r(100.0, 95.0, 105.0, 110.0, "XUSDT") < 1.5           # +2 R sans résistance : refusé par le filtre
    assert R.plan_net_r(100.0, 95.0, 105.0, 125.0, "XUSDT") > 1.5
    out = run({"UPUSDT": uptrend_pullback(peak=175.0)})                       # résistance trop proche pour un plan ≥ 1,5 R net
    assert out["refusals"] and out["refusals"][0]["reason"] in (R.GAIN_RISK, R.RESISTANCE_NEAR)


def test_timing_macro_and_news_filters():
    frames = {"RNGUSDT": range_rejection()}
    assert R.macro_blocked(T, lambda d: ["CPI"] if d == "2026-10-09" else []) == ["2026-10-09 CPI"]
    assert R.macro_blocked(pd.Timestamp("2026-10-10 00:00", tz="UTC"), lambda d: ["FED"] if d == "2026-10-09" else []) == ["2026-10-09 FED"]
    assert R.macro_blocked(pd.Timestamp("2026-10-10 04:00", tz="UTC"), lambda d: ["FED"] if d == "2026-10-09" else []) == []
    blocked = run(frames, macro_events=lambda d: ["CPI"] if d == "2026-10-09" else [])
    assert blocked["refusals"][0]["reason"] == R.MACRO and "CPI" in blocked["refusals"][0]["detail"]
    news = [{"title": "Exchange halts RNG withdrawals after exploit", "assets": [], "risk_seen_at": (T - pd.Timedelta(hours=3)).isoformat()},
            {"title": "Unrelated", "assets": ["RNG"], "risk_seen_at": (T - pd.Timedelta(hours=30)).isoformat()},     # trop vieille
            {"title": "Future news on RNG", "assets": ["RNG"], "risk_seen_at": (T + pd.Timedelta(hours=1)).isoformat()}]  # pas encore connue
    assert R.news_hits("RNGUSDT", news, T) == ["Exchange halts RNG withdrawals after exploit"]
    assert R.news_hits("RNGUSDT", news[1:], T) == []
    assert run(frames, news=news)["refusals"][0]["reason"] == R.NEWS
    assert run(frames, news=None)["refusals"][0]["reason"] == R.NEWS_UNREACHABLE


def test_discipline_one_active_per_pair_rest_48h_and_three_per_day():
    frames = {"RNGUSDT": range_rejection()}
    active = run(frames, discipline={"active": {"RNGUSDT": "abc"}, "rest_until": {}, "calls_today": 0})
    assert active["refusals"][0]["reason"] == R.ACTIVE
    resting = run(frames, discipline={"active": {}, "rest_until": {"RNGUSDT": (T + R.HOUR).isoformat()}, "calls_today": 0})
    assert resting["refusals"][0]["reason"] == R.REST
    rested = run(frames, discipline={"active": {}, "rest_until": {"RNGUSDT": T.isoformat()}, "calls_today": 0})
    assert rested["calls"]
    quota = run(frames, discipline={"active": {}, "rest_until": {}, "calls_today": 3})
    assert quota["refusals"][0]["reason"] == R.QUOTA
    # Quatre candidats le même jour : les trois mieux classés sortent, le quatrième est refusé QUOTA_JOUR.
    many = {f"P{i}USDT": range_rejection() for i in range(4)}
    forecast = {"available": True, "pairs": {s: {"move_24h_pct": 1.0 + 0.1 * i} for i, s in enumerate(many)}}
    out = run(many, forecast=forecast)
    assert len(out["calls"]) == 3 and out["refusals"][0]["reason"] == R.QUOTA
    scores = [c["score"] for c in out["calls"]]
    assert scores == sorted(scores, reverse=True) and out["refusals"][0]["symbol"] not in {c["symbol"] for c in out["calls"]}


# --- 4. Score ---------------------------------------------------------------------------------------------------------------

def test_score_formula_by_hand():
    best = R.score(touches=5, imbalance=0.5, volume_multiple=3.0, stop_ratio=3.0, btc_above_ema20=True)
    assert best["total"] == 100.0 and best["parts"] == {"touches": 25.0, "imbalance": 25.0, "volume": 20.0, "stop": 15.0, "btc": 15.0}
    mid = R.score(touches=2, imbalance=0.1, volume_multiple=1.5, stop_ratio=2.0, btc_above_ema20=False)
    assert mid["parts"] == {"touches": 10.0, "imbalance": 5.0, "volume": 5.0, "stop": 7.5, "btc": 0.0} and mid["total"] == 27.5
    assert R.score(touches=9, imbalance=2.0, volume_multiple=9.0, stop_ratio=9.0, btc_above_ema20=True)["total"] == 100.0
    assert R.score(touches=0, imbalance=None, volume_multiple=float("nan"), stop_ratio=0.5, btc_above_ema20=False)["total"] == 0.0


# --- 5. Causalité ------------------------------------------------------------------------------------------------------------

def test_causality_future_candles_change_nothing():
    base = range_rejection()
    future = base.copy()
    extra = pd.DataFrame({"open_time": [T, T + R.HOUR, T + 2 * R.HOUR], "open": [101.5, 50.0, 200.0], "high": [102.0, 60.0, 250.0],
                          "low": [40.0, 45.0, 150.0], "close": [50.0, 55.0, 220.0], "quote_volume": [99.0, 99.0, 99.0]})
    extra["available_at"] = extra["open_time"] + R.HOUR + pd.Timedelta(seconds=2)
    future = pd.concat([future, extra], ignore_index=True)
    before = run({"RNGUSDT": base})
    after = run({"RNGUSDT": future}, now=T + pd.Timedelta(hours=5))
    keep = ("entry", "stop", "hard_stop", "tp1", "tp2", "risk", "score", "regime", "setup", "placebo_offsets_h")
    assert {k: before["calls"][0][k] for k in keep} == {k: after["calls"][0][k] for k in keep}
    assert before["regimes"] == after["regimes"] and before["refusals"] == after["refusals"]
    # Une bougie 1 h dont `available_at` est après `now` n'entre pas (latence) : lecture par le magasin.
    late = base.copy()
    late.loc[late.index[-1], "available_at"] = T + pd.Timedelta(hours=1)
    assert ev.read_pair(late[late["available_at"] <= NOW], T)["regime"] == R.UNREADABLE


# --- 6. Gestion et résolution ---------------------------------------------------------------------------------------------

def bars(rows, start: pd.Timestamp = T) -> pd.DataFrame:
    return pd.DataFrame([(start + k * R.HOUR, *r) for k, r in enumerate(rows)], columns=["open_time", "open", "high", "low", "close"])


def flat(n: int, p: float):
    return [(p, p, p, p)] * n


def sim(rows, **kw):
    params = {"entry_at": T, "entry": 100.0, "stop": 96.0, "tp1": 104.0, "tp2": 110.0, "symbol": "XUSDT", "scenario": "central"}
    return R.simulate(bars(rows), **(params | kw))


def test_close_stop_only_at_4h_closes_not_on_a_touch():
    c = costs_for("XUSDT", "central")
    cost_in = 100 * (1 + c.market) * (1 + c.fee)
    # Touche sous le stop (95,5 > secours 94) sans clôture 4 h dessous : rien ; puis clôture 4 h à 95 (bougie 11:00-12:00).
    rows = [(100, 101, 95.5, 99), (99, 100, 98, 99), (99, 99.5, 98.5, 99), (99, 99.5, 95.5, 95)] + flat(5, 97)
    out = sim(rows)
    assert out["outcome"] == R.STOP_CLOSE and out["exit_at"] == T + 4 * R.HOUR
    assert out["r"] == pytest.approx((95 * (1 - c.market) * (1 - c.fee) - cost_in) / 4)
    # Clôture 1 h à 95 à 10:00 (pas une clôture 4 h) : la position continue.
    rows = [(100, 101, 99, 99), (99, 100, 95, 95), (95, 99.5, 95, 99), (99, 99.5, 98.5, 99)] + flat(3, 99)
    assert sim(rows)["status"] == R.RUNNING


def test_hard_stop_is_intrabar_and_wins_over_a_target_in_the_same_bar():
    c = costs_for("XUSDT", "central")
    cost_in = 100 * (1 + c.market) * (1 + c.fee)
    out = sim([(100, 105, 93.9, 100)] + flat(3, 100))                                       # TP1 et secours dans la même heure
    assert out["outcome"] == R.STOP_HARD and out["hits"] == 0 and out["exit_at"] == T + R.HOUR
    assert out["r"] == pytest.approx((94 * (1 - c.market) * (1 - c.fee) - cost_in) / 4)
    gap = sim([(93, 93.5, 92, 93)] + flat(3, 93))                                            # ouverture déjà sous le secours
    assert gap["r"] == pytest.approx((93 * (1 - c.market) * (1 - c.fee) - cost_in) / 4)


def test_tp1_half_then_stop_to_entry_then_tp2_or_close_stop():
    c = costs_for("XUSDT", "central")
    cost_in = 100 * (1 + c.market) * (1 + c.fee)
    sell = lambda p: p * (1 - c.market) * (1 - c.fee)  # noqa: E731
    # TP1 à 09:00, puis clôture 4 h de 12:00 à 99,5 ≤ entrée : l'autre moitié sort à 99,5.
    rows = [(100, 104.5, 99, 103), (103, 103.5, 102, 103), (103, 103.5, 102, 103), (103, 103.5, 99, 99.5)] + flat(3, 99)
    out = sim(rows)
    assert out["outcome"] == f"{R.STOP_CLOSE}_APRES_TP1" and out["hits"] == 1
    assert out["r"] == pytest.approx((0.5 * sell(104) + 0.5 * sell(99.5) - cost_in) / 4)
    # TP1 puis TP2 : moitié + moitié.
    rows = [(100, 104.5, 99, 103), (103, 110.5, 102, 110)] + flat(3, 110)
    out = sim(rows)
    assert out["outcome"] == R.TP2 and out["r"] == pytest.approx((0.5 * sell(104) + 0.5 * sell(110) - cost_in) / 4)
    # Avant TP1, une clôture 4 h à 99 ne sort pas (stop à 96) ; après TP1 elle sortirait.
    rows = [(100, 101, 99, 99.5)] * 4 + flat(3, 99.5)
    assert sim(rows)["status"] == R.RUNNING


def test_ten_day_limit_and_missing_bars():
    rows = flat(240, 101.0)
    out = sim(rows)
    c = costs_for("XUSDT", "central")
    assert out["outcome"] == R.TIME and out["exit_at"] == T + R.MAX_HOLD
    assert out["r"] == pytest.approx((101 * (1 - c.market) * (1 - c.fee) - 100 * (1 + c.market) * (1 + c.fee)) / 4, abs=1e-6)
    assert sim(flat(239, 101.0))["status"] == R.RUNNING                                     # dixième jour incomplet
    holed = bars(flat(240, 101.0)).drop(index=[5])                                           # trou à 13:00
    assert R.simulate(holed, entry_at=T, entry=100.0, stop=96.0, tp1=104.0, tp2=110.0, symbol="XUSDT", scenario="central")["status"] == R.RUNNING
    assert sim([(100, 101, 99, 100)] * 3, entry_at=T + R.HOUR)["status"] == R.RUNNING       # bougie d'entrée absente → en cours


def test_placebos_are_reproducible_and_keep_the_geometry():
    offsets = R.placebo_offsets("appel-1")
    assert offsets == R.placebo_offsets("appel-1") and offsets != R.placebo_offsets("appel-2")
    assert len(set(offsets)) == 20 and all(5 <= abs(h) <= 84 for h in offsets)
    frame = bars(flat(400, 50.0), start=T - 100 * R.HOUR)
    out = R.placebo(frame, at=T, offset_h=-10, entry=100.0, stop=96.0, tp1=104.0, tp2=110.0, symbol="XUSDT", scenario="central")
    assert out["status"] == "RESOLU" and out["entry"] == 50.0 and out["entry_at"] == T - 10 * R.HOUR and out["outcome"] == R.TIME
    assert R.placebo(frame.iloc[:50], at=T, offset_h=60, entry=100.0, stop=96.0, tp1=104.0, tp2=110.0, symbol="XUSDT",
                     scenario="central")["status"] == R.GAP


# --- 7. Boîte Telegram, état, API, carte --------------------------------------------------------------------------------------

def test_outbox_queue_pending_expiry_and_sent(settings):
    now = datetime(2026, 10, 9, 8, 5, tzinfo=UTC)
    assert outbox.queue(settings, message_id="APPEL:1", text="un", now=now)
    assert not outbox.queue(settings, message_id="APPEL:1", text="un", now=now)              # jamais deux fois
    outbox.queue(settings, message_id="APPEL:2", text="deux", now=now + pd.Timedelta(hours=1))
    assert [m["id"] for m in outbox.pending(settings, now=now + pd.Timedelta(hours=2))] == ["APPEL:1", "APPEL:2"]
    later = now + pd.Timedelta(hours=6, minutes=1)
    assert [m["id"] for m in outbox.pending(settings, now=later)] == ["APPEL:2"]             # > 6 h : EXPIRE
    assert outbox.counts(settings) == {"EN_ATTENTE": 1, "ENVOYE": 0, "EXPIRE": 1}
    assert outbox.mark_sent(settings, ["APPEL:2", "inconnu"]) == 1
    assert outbox.pending(settings, now=later) == [] and outbox.counts(settings)["ENVOYE"] == 1
    data = json.loads(outbox.path_for(settings).read_text(encoding="utf-8"))
    assert {m["status"] for m in data["messages"]} == {"EXPIRE", "ENVOYE"}


def test_messages_are_compact_french_and_honest():
    call = run({"RNGUSDT": range_rejection()})["calls"][0]
    text = outbox.call_message(call)
    assert text.startswith("Assistant CSI — RNGUSDT · RANGE · rejet du support") and text.endswith(outbox.FOOTER)
    assert "Stop de clôture 4 h" in text and "TP2" in text and "score" in text and len(text.splitlines()) <= 10
    res = {"status": "RESOLU", "results": {"central": {"outcome": "TP2", "exit_at": "2026-10-12T08:00:00+00:00", "r": 1.9,
                                                        "placebo_mean": 0.1, "excess": 1.8}, "defavorable": {"r": 1.7}}}
    assert "R net +1.90" in outbox.resolution_message(res, call) and "aucun gain démontré" in outbox.resolution_message(res, call)
    assert "TROU" in outbox.resolution_message({"status": "TROU", "reason": "bougies manquantes"}, call)


def test_state_file_and_five_line_resume(settings):
    evaluation = run({"RNGUSDT": range_rejection(), "UPUSDT": uptrend_pullback(), "DNUSDT": hours_from_4h(*path4h([(400, 160, 100, 1.0)]))})
    active = [{"symbol": "RNGUSDT", "regime": "RANGE", "latent_r": 0.4}, {"symbol": "UPUSDT", "regime": "HAUSSE", "latent_r": None}]
    refusals = [{"symbol": "AUSDT", "regime": "RANGE", "reason": R.BOOK_SPREAD, "detail": ""},
                {"symbol": "BUSDT", "regime": "HAUSSE", "reason": R.STOP_TIGHT, "detail": ""},
                {"symbol": "CUSDT", "regime": "HAUSSE", "reason": R.BOOK_UNREACHABLE, "detail": ""}]
    payload = state.build(evaluation, active_calls=active, last_refusals=refusals, now=NOW)
    lines = payload["resume"].splitlines()
    assert len(lines) == 5 and lines[0] == "Feu VERT · BTC au-dessus de son EMA50"
    assert lines[1] == "3 paires : 1 HAUSSE, 1 RANGE, 1 BAISSE, 0 INDECIS"
    assert lines[2] == "2 appels actifs : RNGUSDT (RANGE, +0,4 R), UPUSDT (HAUSSE, en cours)"
    assert lines[3] == "Derniers refus : liquidité 1, stop hors volatilité 1, carnet injoignable 1"
    assert lines[4] == "Prochaine évaluation : 12:00 UTC" and payload["places_orders"] is False
    state.write(settings, payload)
    assert state.read(settings)["resume"] == payload["resume"] and state.path_for(settings).name == "assistant.json"
    silent = state.build({"at": T.isoformat(), "light": {"color": "ROUGE"}, "btc": {"known": True, "above_ema50": False},
                          "silence": "feu ROUGE : silence total", "regimes": {}}, active_calls=[], last_refusals=[], now=NOW)
    assert silent["resume"].splitlines()[0] == "Feu ROUGE · BTC sous son EMA50 · silence (feu ROUGE : silence total)"
    assert silent["resume"].splitlines()[2] == "Aucun appel actif"


def test_api_routes_and_token(settings, monkeypatch):
    now = datetime(2026, 10, 9, 8, 5, tzinfo=UTC)
    api = CsiApi(settings, now=lambda: now)
    empty = api.dispatch("GET", "/assistant", {}, None)
    assert empty["available"] is False and empty["places_orders"] is False and "resume" in empty
    state.write(settings, state.build(run({"RNGUSDT": range_rejection()}), active_calls=[], last_refusals=[], now=NOW))
    assert api.dispatch("GET", "/assistant", {}, None)["available"] is True
    outbox.queue(settings, message_id="APPEL:x", text="bonjour", now=now)
    assert api.dispatch("GET", "/assistant/outbox", {}, None) == {"messages": [{"id": "APPEL:x", "created_at": pd.Timestamp(now).isoformat(), "text": "bonjour"}]}
    with pytest.raises(ApiError) as forbidden:                                       # écriture sans jeton d'API : refusée
        api.dispatch("POST", "/assistant/sent", {}, {"ids": ["APPEL:x"]})
    assert forbidden.value.status == 403
    monkeypatch.setenv("CSI_API_TOKEN", "jeton-de-test")
    for bad in ({}, {"ids": "APPEL:x"}, {"ids": [1]}):
        with pytest.raises(ApiError):
            api.dispatch("POST", "/assistant/sent", {}, bad)
    assert api.dispatch("POST", "/assistant/sent", {}, {"ids": ["APPEL:x"]}) == {"marked": 1}
    assert api.dispatch("GET", "/assistant/outbox", {}, None) == {"messages": []}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, token="jeton-de-test"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for path in ("/assistant", "/assistant/outbox"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(base + path, timeout=10)
            assert refused.value.code == 401
        request = urllib.request.Request(base + "/assistant/sent", data=b'{"ids": []}', method="POST",
                                         headers={"Authorization": "Bearer jeton-de-test", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert json.loads(response.read()) == {"marked": 0}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_card_is_at_the_top_of_the_market_tab_and_honest():
    from crypto_signal_intelligence.api.server import STATIC_DIR
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    market_tab = page[page.index('id="tab-market"'):]
    assert market_tab.index('id="assistant-result"') < market_tab.index('id="meteo-result"')
    assert 'api("/assistant")' in script and "loadAssistant();" in script and "Assistant de marché (shadow, test F18)" in script
    assert "aucun ordre" in script and "aucun gain démontré" in script.lower() and "Appels actifs" in script


def test_nothing_is_written_in_signals_or_the_registry(settings):
    before = sorted(p.name for p in (settings.root / "signals").glob("*")) if (settings.root / "signals").exists() else []
    out = run({"RNGUSDT": range_rejection()})
    outbox.queue(settings, message_id="APPEL:" + out["calls"][0]["call_id"], text=outbox.call_message(out["calls"][0]), now=NOW)
    state.write(settings, state.build(out, active_calls=[], last_refusals=[], now=NOW))
    after = sorted(p.name for p in (settings.root / "signals").glob("*")) if (settings.root / "signals").exists() else []
    assert before == after and not settings.signals_db.exists() and not (settings.root / "signals" / "shadow").exists()
    assert {p.name for p in (settings.root / "state").glob("assistant*")} >= {"assistant.json", "assistant_outbox.json"}
