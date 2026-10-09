"""F18_ASSISTANT : l'assistant de marché (docs/ASSISTANT.md) mesuré en direct contre des placebos
(docs/FORWARD_TESTS.md, section F18_ASSISTANT ; règles figées au démarrage, modules `assistant/*` gelés).

À chaque passage horaire de la surveillance : si une clôture 4 h UTC (00/04/08/12/16/20) est passée depuis le
démarrage, pas encore évaluée, et que la bougie 1 h qui la clôture est en magasin (magasin de F15, lecture seule),
l'assistant l'évalue une fois (entrée EVALUATION) ; chaque appel retenu est inscrit (APPEL, avec ses placebos tirés
d'avance) et déposé dans la boîte Telegram de l'assistant. Les appels se résolvent sur les bougies 1 h du même
magasin (RESOLUTION, appel et 20 placebos de même géométrie et même gestion) ; VERDICT à la date d'évaluation une
fois tout résolu, puis CLOTURE. Rien n'est écrit dans `SignalRegistry` ni dans `signals/` : F2 n'est pas touché.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..assistant import evaluate as ev
from ..assistant import outbox, state
from ..assistant import rules as R
from ..backtest.metrics import day_block_ci, day_block_ci95
from ..config import Settings
from ..data.store import CandleStore
from . import f4
from .costs import ADVERSE, CENTRAL, SCENARIOS
from .journal import Journal, utc_iso
from .registry import ForwardTest

TEST_ID = R.TEST_ID
EVALUATION, CALL, RESOLUTION = "EVALUATION", "APPEL", "RESOLUTION"
RESOLVED, GAP = "RESOLU", "TROU"
ALPHA = 0.05
SAMPLES, SEED, BLOCK_DAYS = 10_000, 20261009, 7
GAP_AFTER = pd.Timedelta(days=2)          # données toujours manquantes 2 jours après la fin de l'horizon : trou constaté
MIN_RESOLVED, MIN_DAYS = f4.MIN_RESOLVED, f4.MIN_DAYS


# --- Lecture du journal ------------------------------------------------------------------------------------------------

def calls(journal: Journal) -> dict[str, dict]:
    return {e["data"]["call_id"]: e["data"] for e in journal.entries({CALL})}


def resolutions(journal: Journal) -> dict[str, dict]:
    return {e["data"]["call_id"]: e["data"] for e in journal.entries({RESOLUTION})}


def _bars(store: CandleStore, symbol: str, *, since: pd.Timestamp, until: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h clôturées au plus tard à `until` et connues à `now`, à partir de `since`."""
    frame = store.load_since(symbol, "1h", since)
    if frame.empty:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])
    frame = frame.assign(open_time=pd.to_datetime(frame["open_time"], utc=True))
    if "available_at" in frame.columns:
        frame = frame[pd.to_datetime(frame["available_at"], utc=True) <= now]
    frame = frame[frame["open_time"] + R.HOUR <= until]
    return frame.sort_values("open_time").drop_duplicates("open_time")[["open_time", "open", "high", "low", "close"]].reset_index(drop=True)


def _simulate(call: dict, bars: pd.DataFrame, scenario: str) -> dict:
    return R.simulate(bars, entry_at=pd.Timestamp(call["at"]), entry=call["entry"], stop=call["stop"], tp1=call["tp1"],
                      tp2=call["tp2"], symbol=call["symbol"], scenario=scenario)


def discipline_at(journal: Journal, store: CandleStore, *, at: pd.Timestamp, now: pd.Timestamp) -> dict:
    """Appels actifs à `at` (simulés sur les bougies ≤ at), repos de 48 h après la sortie, appels du jour UTC."""
    done = resolutions(journal)
    active: dict[str, str] = {}
    rest: dict[str, pd.Timestamp] = {}
    today = 0
    for ident, call in calls(journal).items():
        start = pd.Timestamp(call["at"])
        if start.floor("D") == at.floor("D") and start <= at:
            today += 1
        exit_at = None
        if ident in done:
            central = (done[ident].get("results") or {}).get(CENTRAL) or {}
            exit_at = pd.Timestamp(central["exit_at"]) if central.get("exit_at") else start
        elif start < at:
            bars = _bars(store, call["symbol"], since=start, until=at, now=now)
            trade = _simulate(call, bars, CENTRAL)
            if trade["status"] == R.RUNNING:
                active[call["symbol"]] = ident
                continue
            exit_at = pd.Timestamp(trade["exit_at"])
        elif start == at:
            active[call["symbol"]] = ident
            continue
        if exit_at is not None:
            until = exit_at + pd.Timedelta(hours=R.REST_HOURS)
            previous = rest.get(call["symbol"])
            if previous is None or until > previous:
                rest[call["symbol"]] = until
    return {"active": active, "rest_until": {k: utc_iso(v) for k, v in rest.items()}, "calls_today": today}


def active_calls(journal: Journal, store: CandleStore, *, now: pd.Timestamp) -> list[dict]:
    """Appels sans RESOLUTION, avec leur état à la dernière bougie connue (R latent, TP1 atteint ou non)."""
    done = resolutions(journal)
    out = []
    for ident, call in calls(journal).items():
        if ident in done:
            continue
        bars = _bars(store, call["symbol"], since=pd.Timestamp(call["at"]), until=now, now=now)
        trade = _simulate(call, bars, CENTRAL)
        item = {k: call[k] for k in ("call_id", "symbol", "at", "regime", "setup", "entry", "stop", "hard_stop", "tp1", "tp2",
                                     "risk", "score", "explanation", "size")}
        if trade["status"] == R.RUNNING:
            hits = int(not bars.empty and bars["high"].max() >= call["tp1"])
            item |= {"status": R.RUNNING, "hits": hits,
                     "latent_r": R.latent_r(call, float(bars["close"].iloc[-1]), hits) if not bars.empty else None,
                     "last_close_at": utc_iso(bars["open_time"].iloc[-1] + R.HOUR) if not bars.empty else None}
        else:
            item |= {"status": "SORTI_PLACEBOS_EN_COURS", "outcome": trade["outcome"], "latent_r": trade["r"],
                     "exit_at": utc_iso(trade["exit_at"])}
        out.append(item)
    return out


def last_refusals(journal: Journal, limit: int = state.MAX_REFUSALS) -> list[dict]:
    rows: list[dict] = []
    for entry in journal.entries({EVALUATION}):
        rows += [r | {"at": entry["data"]["at"]} for r in entry["data"].get("refusals") or []]
    return rows[-limit:]


def write_state(settings: Settings, journal: Journal, store: CandleStore, *, now: pd.Timestamp) -> dict:
    last = None
    for entry in journal.entries({EVALUATION}):
        last = entry["data"]
    payload = state.build(last, active_calls=active_calls(journal, store, now=now), last_refusals=last_refusals(journal), now=now)
    state.write(settings, payload)
    return payload


# --- Décisions ---------------------------------------------------------------------------------------------------------------

def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime, store: CandleStore | None = None,
                     client=None, book=None) -> dict:
    """Évalue la dernière clôture 4 h UTC passée depuis le démarrage, une seule fois, dès que sa bougie 1 h est en
    magasin ; inscrit EVALUATION puis chaque APPEL, dépose les messages, écrit l'état. Rien après la fin du recueil."""
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    store = store or ev.store_for(settings)
    at = ev.closing_time(moment)
    counts = {"evaluations": 0, "calls": 0}
    if at <= started or moment >= final:
        return counts
    if any(e["data"]["at"] == utc_iso(at) for e in journal.entries({EVALUATION})):
        return counts
    symbols = list(start["halal"]["symbols"])
    probe = ev.MARKET if ev.MARKET in symbols else (symbols[0] if symbols else ev.MARKET)
    if not ev.available(store, probe, at=at, now=moment):
        return counts | {"waiting": f"bougie 1 h de {probe} clôturée à {utc_iso(at)} pas encore en magasin"}
    discipline = discipline_at(journal, store, at=at, now=moment)
    result = ev.run(settings, at=at, now=moment, discipline=discipline, store=store, symbols=symbols, client=client, book=book)
    record = {k: v for k, v in result.items() if k != "calls"} | {"call_ids": [c["call_id"] for c in result["calls"]],
                                                                  "discipline": discipline}
    journal.append(EVALUATION, record, now=moment)
    counts["evaluations"] = 1
    for decision in result["calls"]:
        journal.append(CALL, decision, now=moment)
        outbox.queue(settings, message_id=f"APPEL:{decision['call_id']}", text=outbox.call_message(decision), now=moment)
        counts["calls"] += 1
    write_state(settings, journal, store, now=moment)
    return counts


# --- Résolution ----------------------------------------------------------------------------------------------------------------

def resolve_one(call: dict, bars: pd.DataFrame, *, late: bool) -> dict | None:
    """Appel et placebos simulés sur les bougies 1 h disponibles ; None tant que quelque chose reste EN_COURS et que
    le délai de constat n'est pas passé (`late`) ; TROU pour un appel qu'on ne peut plus résoudre."""
    at = pd.Timestamp(call["at"])
    results: dict = {}
    pending = False
    for scenario in SCENARIOS:
        trade = _simulate(call, bars, scenario)
        if trade["status"] == R.RUNNING:
            pending = True
            results[scenario] = None
            continue
        placebos: list[float | None] = []
        for offset in call["placebo_offsets_h"]:
            p = R.placebo(bars, at=at, offset_h=int(offset), entry=call["entry"], stop=call["stop"], tp1=call["tp1"],
                          tp2=call["tp2"], symbol=call["symbol"], scenario=scenario)
            if p["status"] == R.RUNNING:
                pending = True
            placebos.append(p.get("r") if p["status"] == RESOLVED else None)
        usable = [x for x in placebos if x is not None]
        mean = round(float(np.mean(usable)), 6) if usable else None
        results[scenario] = {"outcome": trade["outcome"], "hits": trade["hits"], "r": trade["r"], "exit_at": utc_iso(trade["exit_at"]),
                             "placebos": placebos, "placebos_resolved": len(usable), "placebo_mean": mean,
                             "excess": round(trade["r"] - mean, 6) if mean is not None else None}
    if pending and not late:
        return None
    base = {"call_id": call["call_id"], "symbol": call["symbol"], "at": call["at"], "regime": call["regime"], "setup": call["setup"]}
    if results[CENTRAL] is None or results[ADVERSE] is None:
        return base | {"status": GAP, "reason": "bougies 1 h manquantes : appel non résolu", "results": None}
    return base | {"status": RESOLVED, "reason": None, "results": results, "late": late}


def resolve(settings: Settings, journal: Journal, *, now: datetime, store: CandleStore | None = None) -> dict:
    """Résout chaque appel (et ses placebos) une fois les bougies disponibles ; trou constaté 2 jours après la fin de
    la fenêtre des placebos si des bougies manquent encore. Un message par RESOLUTION, état réécrit."""
    moment = pd.Timestamp(now)
    store = store or ev.store_for(settings)
    done = resolutions(journal)
    counts: dict[str, int] = {}
    for ident, call in calls(journal).items():
        if ident in done:
            continue
        at = pd.Timestamp(call["at"])
        if moment < at + R.H4:
            continue
        end = pd.Timestamp(call["resolution_end"])
        bars = _bars(store, call["symbol"], since=at - (R.PLACEBO_MAX_H + 1) * R.HOUR, until=min(moment, end + R.HOUR), now=moment)
        out = resolve_one(call, bars, late=moment >= end + GAP_AFTER)
        if out is None:
            continue
        journal.append(RESOLUTION, out, now=moment)
        outbox.queue(settings, message_id=f"RESOLUTION:{ident}", text=outbox.resolution_message(out, call), now=moment)
        counts[out["status"]] = counts.get(out["status"], 0) + 1
    if counts:
        write_state(settings, journal, store, now=moment)
    return counts


# --- Mesures ------------------------------------------------------------------------------------------------------------------

def _measure(rows: list[dict], scenario: str, level: float, *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    if not rows:
        return {"n": 0, "days": 0}
    rows = sorted(rows, key=lambda r: r["at"])
    r = np.array([x["results"][scenario]["r"] for x in rows], float)
    times = pd.to_datetime([x["at"] for x in rows], utc=True).to_numpy()
    excess = np.array([x["results"][scenario]["excess"] if x["results"][scenario]["excess"] is not None else np.nan
                       for x in rows], float)
    ok = np.isfinite(excess)
    ci_r, _ = day_block_ci95(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, min_blocks=8)
    ci_x, _ = (day_block_ci(excess[ok], times[ok], block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level, min_blocks=8)
               if ok.any() else (None, 0))
    hits = np.array([x["results"][scenario]["hits"] for x in rows])
    tp2 = np.array([x["results"][scenario]["outcome"] == R.TP2 for x in rows])
    streak = worst = 0
    for value in r:
        streak = streak + 1 if value < 0 else 0
        worst = max(worst, streak)
    return {"n": int(len(r)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()), "r_mean": round(float(r.mean()), 4),
            "r_ci95": ci_r, "win_share": round(float((r > 0).mean()), 4), "tp1_rate": round(float((hits >= 1).mean()), 4),
            "tp2_rate": round(float(tp2.mean()), 4), "worst_streak": int(worst),
            "placebo_excess": round(float(excess[ok].mean()), 4) if ok.any() else None, "placebo_excess_ci": ci_x}


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    evaluations = [e["data"] for e in journal.entries({EVALUATION})]
    decided = calls(journal)
    done = resolutions(journal)
    resolved = [r for r in done.values() if r["status"] == RESOLVED and r.get("results")]
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in done for k in decided)
    level = 1 - ALPHA / 2
    refusals: dict[str, int] = {}
    regimes: dict[str, int] = {}
    for e in evaluations:
        for reason, n in (e.get("refusals_by_reason") or {}).items():
            refusals[reason] = refusals.get(reason, 0) + n
        for regime, n in (e.get("regimes") or {}).items():
            regimes[regime] = regimes.get(regime, 0) + n
    span_days = max(1.0, (pd.Timestamp(now) - pd.Timestamp(start["started_at"])) / pd.Timedelta(days=1)) if decided else 1.0
    by_regime = {}
    for regime in (R.UP, R.RANGE):
        mine = [r for r in resolved if r["regime"] == regime]
        by_regime[regime] = {"calls": sum(1 for c in decided.values() if c["regime"] == regime), "resolved": len(mine),
                             "central": _measure(mine, CENTRAL, level, samples=2000)}
    scenarios = {s: _measure(resolved, s, level) for s in SCENARIOS}
    return {"evaluations": len(evaluations), "silent": sum(1 for e in evaluations if e.get("silence")),
            "candidates": sum(e.get("candidates", 0) for e in evaluations), "calls": len(decided), "resolved": len(resolved),
            "gaps": sum(1 for r in done.values() if r["status"] == GAP), "pending": sum(1 for k in decided if k not in done),
            "calls_per_week": round(len(decided) / span_days * 7, 2), "refusals_by_reason": refusals,
            "regime_readings": regimes, "by_regime": by_regime, "scenarios": scenarios, "assistant": True,
            "verdict": f4.verdict(scenarios, ended=ended), "ended": bool(ended)}


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "scenarios": result["scenarios"], "calls": result["calls"],
                                 "by_regime": result["by_regime"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Assistant de marché (régime journalier, repli en hausse ou rejet du support en range, filtres de "
                           "liquidité, volatilité, timing et discipline) contre placebos",
    hypothesis=("Un appel d'achat de l'assistant (docs/ASSISTANT.md : feu et BTC favorables, régime HAUSSE ou RANGE, repli puis "
                "reprise ou rejet du support confirmés par le volume, carnet liquide, stop entre 0,75 et 3 fois le mouvement "
                "attendu à 24 h, TP2 ≥ 1,5 R net, hors événements macro et news de risque, 3 appels par jour au plus), géré "
                "avec stop à la clôture 4 h, stop de secours, moitié à +1 R puis l'autre à TP2, rapporte en moyenne plus, en R "
                "net, que 20 entrées placebo de même géométrie sur la même paire à ±84 h. Attendu : NON_DEMONTRE ou INSUFFISANT."),
    params={"evaluation": "chaque clôture 4 h UTC", "history_days": R.HISTORY_DAYS, "min_days": R.MIN_DAYS,
            "ema": [R.EMA_FAST, R.EMA_SLOW, R.SLOPE_DAYS], "range_days": R.RANGE_DAYS, "profile_days": R.PROFILE_DAYS, "merge_atr": R.MERGE_ATR,
            "min_touches": R.MIN_TOUCHES, "range_height_atr_d": R.RANGE_HEIGHT_ATR, "pullback_bars": R.PULLBACK_BARS,
            "touch_bars": R.TOUCH_BARS, "touch_atr": R.TOUCH_ATR, "stop_atr": R.STOP_ATR, "volume_bars": R.VOLUME_BARS,
            "min_resistance_r": R.MIN_RESISTANCE_R, "min_tp2_r_net": R.MIN_TP2_R_NET, "spread_max_pct": R.SPREAD_MAX_PCT,
            "slippage_max_pct": R.SLIPPAGE_MAX_PCT, "buy_size_usdt": R.BUY_SIZE_USDT, "stop_vol": [R.STOP_VOL_MIN, R.STOP_VOL_MAX],
            "realized_days": R.REALIZED_DAYS, "macro_margin_hours": 2, "news_window_hours": 24, "max_calls_per_day": R.MAX_CALLS_PER_DAY,
            "rest_hours": R.REST_HOURS, "hard_stop_factor": R.HARD_STOP_FACTOR, "tp1_share": R.TP1_SHARE, "max_hold_days": 10,
            "placebos": R.PLACEBOS, "placebo_hours": [R.PLACEBO_MIN_H, R.PLACEBO_MAX_H], "min_resolved": MIN_RESOLVED,
            "min_days_resolved": MIN_DAYS, "alpha": ALPHA, "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "gap_after_days": GAP_AFTER.days, "exits": "taker partout ; bougie 1 h touchant objectif et stop de secours : stop",
            "universe": "liste halal figée de F15, magasin forward_figures/data en lecture seule"},
    rule_objects=(), config_keys=("data.rest_base_url", "data.assumed_availability_latency_seconds"),
    frozen_modules=("crypto_signal_intelligence.forward.f18", "crypto_signal_intelligence.assistant.rules",
                    "crypto_signal_intelligence.assistant.evaluate", "crypto_signal_intelligence.assistant.state",
                    "crypto_signal_intelligence.assistant.outbox", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci95"),
                      ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.forward.f4", "verdict"),
                      ("crypto_signal_intelligence.forward.f15", "figure_store"),
                      ("crypto_signal_intelligence.forward.light", "macro_events"),
                      ("crypto_signal_intelligence.forward.liquidity_log", "book_metrics"),
                      ("crypto_signal_intelligence.forward.liquidity_log", "market_buy"),
                      ("crypto_signal_intelligence.forward.liquidity_log", "market_sell"),
                      ("crypto_signal_intelligence.forward.liquidity_log", "_levels"),
                      ("crypto_signal_intelligence.forward.liquidity_log", "band_key"),
                      ("crypto_signal_intelligence.risk.market_light", "decide"),
                      ("crypto_signal_intelligence.risk.market_light", "ema"),
                      ("crypto_signal_intelligence.risk.market_light", "daily_closes"),
                      ("crypto_signal_intelligence.risk.market_light", "current"),
                      ("crypto_signal_intelligence.risk.advice", "advice"),
                      ("crypto_signal_intelligence.news.risk", "risk_items"),
                      ("crypto_signal_intelligence.patterns.indicators", "ema"),
                      ("crypto_signal_intelligence.patterns.primitives", "atr"),
                      ("crypto_signal_intelligence.patterns.primitives", "zigzag"),
                      ("crypto_signal_intelligence.patterns.volume", "volume_profile"),
                      ("crypto_signal_intelligence.technical.analysis", "cluster_levels"),
                      ("crypto_signal_intelligence.technical.analysis", "round_tick"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
