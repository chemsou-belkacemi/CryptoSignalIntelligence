"""F19_PRICE_ACTION : les cinq configurations « price action » (docs/PRICE_ACTION.md) mesurées en direct, verdict au R
NET SEUL, placebos descriptifs (§ 11.5 ; docs/FORWARD_TESTS.md, section F19_PRICE_ACTION ; règles figées au démarrage, modules `price_action/*` gelés).

À chaque passage horaire de la surveillance : si une clôture 4 h UTC est passée depuis le démarrage, pas encore
évaluée, et que la bougie 1 h qui la clôture est en magasin (magasin de F15, lecture seule), le détecteur partagé est
appelé une fois sur cette clôture (EVALUATION) — c'est aussi la clôture journalière à 00:00 UTC ; chaque appel retenu
(5 par jour UTC au plus, toutes configurations) est inscrit (APPEL, placebos tirés d'avance) et déposé dans la boîte
Telegram `state/price_action_outbox.json`. Résolution sur les bougies 1 h du même magasin (RESOLUTION : appel et 20
placebos de même géométrie, même gestion, mêmes frais, descriptifs) ; VERDICT par configuration à la date d'évaluation une fois
tout résolu, puis CLOTURE. Rien n'est écrit dans `SignalRegistry`, `signals/` ni `state/assistant*`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci, day_block_ci95
from ..config import Settings
from ..data.store import CandleStore
from ..price_action import detect as D
from ..price_action import evaluate as ev
from ..price_action import manage as M
from ..price_action import outbox, state
from .costs import ADVERSE, CENTRAL, SCENARIOS
from .journal import Journal, utc_iso
from .registry import ForwardTest

TEST_ID = ev.TEST_ID
EVALUATION, CALL, RESOLUTION = "EVALUATION", "APPEL", "RESOLUTION"
RESOLVED, GAP = M.RESOLVED, M.GAP
ALPHA, TESTS = 0.05, 5
LEVEL = 1 - ALPHA / TESTS                    # intervalle de décision du R net : 1 − 0,05/5 (cinq configurations)
SAMPLES, SEED, BLOCK_DAYS = 10_000, 20261010, 7
GAP_AFTER = pd.Timedelta(days=2)
MIN_RESOLVED, MIN_DAYS = 30, 10
ABOVE, BELOW, NOT_SHOWN, INSUFFICIENT, RUNNING = ("SUPERIEUR_A_ZERO", "INFERIEUR_A_ZERO", "NON_DEMONTRE", "INSUFFISANT",
                                                  "EN_COURS")


# --- Journal -----------------------------------------------------------------------------------------------------------------

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
    frame = frame[frame["open_time"] + pd.Timedelta(hours=1) <= until]
    return frame.sort_values("open_time").drop_duplicates("open_time")[["open_time", "open", "high", "low", "close"]].reset_index(drop=True)


def _simulate(call: dict, bars: pd.DataFrame, scenario: str) -> dict:
    return M.simulate(bars, entry_at=pd.Timestamp(call["at"]), entry=call["entry"], stop=call["stop"],
                      objective=call["objective"], symbol=call["symbol"], scenario=scenario, unit=call["unit"],
                      complete=False, tp1=call["tp1"], hard_stop=call["hard_stop"])


def discipline_at(journal: Journal, store: CandleStore, *, at: pd.Timestamp, now: pd.Timestamp) -> dict:
    """Appels actifs à `at` par paire et configuration (rejoués sur les bougies ≤ at), repos de 48 h après la sortie
    centrale, appels du jour UTC."""
    done = resolutions(journal)
    active: dict[str, str] = {}
    rest: dict[str, pd.Timestamp] = {}
    today = 0
    for ident, call in calls(journal).items():
        start = pd.Timestamp(call["at"])
        k = ev.key(call["symbol"], call["config"])
        if start.floor("D") == at.floor("D") and start <= at:
            today += 1
        exit_at = None
        if ident in done:
            central = (done[ident].get("results") or {}).get(CENTRAL) or {}
            exit_at = pd.Timestamp(central["exit_at"]) if central.get("exit_at") else start
        elif start < at:
            trade = _simulate(call, _bars(store, call["symbol"], since=start, until=at, now=now), CENTRAL)
            if trade["status"] == M.RUNNING:
                active[k] = ident
                continue
            exit_at = pd.Timestamp(trade["exit_at"])
        elif start == at:
            active[k] = ident
            continue
        if exit_at is not None:
            until = exit_at + pd.Timedelta(hours=M.REST_HOURS)
            if k not in rest or until > rest[k]:
                rest[k] = until
    return {"active": active, "rest_until": {k: utc_iso(v) for k, v in rest.items()}, "calls_today": today}


def active_calls(journal: Journal, store: CandleStore, *, now: pd.Timestamp) -> list[dict]:
    """Appels sans RESOLUTION avec leur état à la dernière bougie connue (R latent approché, brut de frais)."""
    done = resolutions(journal)
    out = []
    for ident, call in calls(journal).items():
        if ident in done:
            continue
        bars = _bars(store, call["symbol"], since=pd.Timestamp(call["at"]), until=now, now=now)
        trade = _simulate(call, bars, CENTRAL)
        item = {k: call[k] for k in ("call_id", "config", "symbol", "unit", "at", "entry", "stop", "hard_stop", "tp1",
                                     "objective", "risk", "explanation")}
        if trade["status"] == M.RUNNING:
            hits = int(not bars.empty and bars["high"].max() >= call["tp1"])
            latent = None
            if not bars.empty:
                last = float(bars["close"].iloc[-1])
                realized = M.TP1_SHARE * (call["tp1"] - call["entry"]) / call["risk"] if hits else 0.0
                latent = round(realized + (1 - M.TP1_SHARE if hits else 1.0) * (last - call["entry"]) / call["risk"], 2)
            item |= {"status": M.RUNNING, "hits": hits, "latent_r": latent}
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


# --- Décisions -----------------------------------------------------------------------------------------------------------------

def real_clock() -> datetime:
    return datetime.now(UTC)


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime, store: CandleStore | None = None,
                     clock: Callable[[], datetime] | None = None) -> dict:
    """Évalue la dernière clôture 4 h UTC passée depuis le démarrage, une seule fois, dès que sa bougie 1 h est en
    magasin ; inscrit EVALUATION puis chaque APPEL, dépose les messages, écrit l'état. Le retard (`delay_min`, `late`)
    est mesuré sur l'HORLOGE RÉELLE au moment où F19 évalue (`clock`, injectable), pas sur l'heure de début du passage
    horaire (les tests précédents du passage peuvent prendre des minutes)."""
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    store = store or ev.store_for(settings)
    at = ev.closing_time(moment)
    counts = {"evaluations": 0, "calls": 0, "repaired": repair_calls(settings, journal, now=moment)}
    if at <= started or moment >= final:
        return counts
    if any(e["data"]["at"] == utc_iso(at) for e in journal.entries({EVALUATION})):
        return counts
    universe = ev.frozen_universe(settings) or {"symbols": list(start["halal"]["symbols"]), "sha256": start["halal"].get("sha256"),
                                                "source": TEST_ID}
    symbols = universe["symbols"]
    probe = D.FR_MARKET if D.FR_MARKET in symbols else (symbols[0] if symbols else D.FR_MARKET)
    if not ev.available(store, probe, at=at, now=moment):
        return counts | {"waiting": f"bougie 1 h de {probe} clôturée à {utc_iso(at)} pas encore en magasin"}
    discipline = discipline_at(journal, store, at=at, now=moment)
    decided = pd.Timestamp((clock or real_clock)())
    result = ev.run(settings, at=at, now=decided, discipline=discipline, store=store, symbols=symbols)
    result["pass_started_at"] = utc_iso(moment)
    record = result | {"call_ids": [c["call_id"] for c in result["calls"]], "discipline": discipline,
                       "universe": {"source": universe["source"], "pairs": len(symbols), "sha256": universe["sha256"]}}
    journal.append(EVALUATION, record, now=decided)
    counts["evaluations"] = 1
    for decision in result["calls"]:
        journal.append(CALL, decision, now=decided)
        outbox.queue(settings, message_id=f"APPEL:{decision['call_id']}", text=outbox.call_message(decision), now=decided)
        counts["calls"] += 1
    write_state(settings, journal, store, now=decided)
    return counts


def repair_calls(settings: Settings, journal: Journal, *, now: pd.Timestamp) -> int:
    """Arrêt brutal entre EVALUATION et APPEL : l'appel listé est ré-émis depuis l'EVALUATION (marqué `repaired`)."""
    known = set(calls(journal))
    repaired = 0
    for entry in list(journal.entries({EVALUATION})):
        for decision in entry["data"].get("calls") or []:
            if decision["call_id"] in known:
                continue
            journal.append(CALL, decision | {"repaired": True, "repaired_at": utc_iso(now)}, now=now)
            outbox.queue(settings, message_id=f"APPEL:{decision['call_id']}", text=outbox.call_message(decision), now=now)
            known.add(decision["call_id"])
            repaired += 1
    return repaired


# --- Résolution ----------------------------------------------------------------------------------------------------------------

def _jsonable(result: dict) -> dict:
    return {k: (utc_iso(v) if isinstance(v, pd.Timestamp) else v) for k, v in result.items() if k not in ("exit_ns", "pending")}


def resolve_one(call: dict, bars: pd.DataFrame, *, late: bool) -> dict | None:
    """Appel et placebos sur les bougies 1 h disponibles ; None tant que quelque chose est EN_COURS et que le délai de
    constat n'est pas passé ; TROU pour un appel qu'on ne peut plus résoudre."""
    hours = M.Hourly.of(bars) if not bars.empty else None
    results: dict = {}
    pending = False
    for scenario in SCENARIOS:
        out = None if hours is None else M.measure(
            hours, ident=call["call_id"], at=pd.Timestamp(call["at"]), entry=call["entry"], stop=call["stop"],
            objective=call["objective"], symbol=call["symbol"], scenario=scenario, unit=call["unit"], complete=False,
            tp1=call["tp1"], hard_stop=call["hard_stop"])
        if out is None:
            pending = True
            results[scenario] = None
            continue
        pending = pending or out["pending"]
        results[scenario] = _jsonable(out)
    if pending and not late:
        return None
    base = {"call_id": call["call_id"], "config": call["config"], "symbol": call["symbol"], "at": call["at"], "unit": call["unit"]}
    if results[CENTRAL] is None or results[ADVERSE] is None:
        return base | {"status": GAP, "reason": "bougies 1 h manquantes : appel non résolu", "results": None}
    return base | {"status": RESOLVED, "reason": None, "results": results, "late": late}


def resolve(settings: Settings, journal: Journal, *, now: datetime, store: CandleStore | None = None) -> dict:
    """Résout chaque appel (et ses placebos) une fois les bougies disponibles ; trou constaté 2 jours après la fin de la
    fenêtre si des bougies manquent encore. Un message par RESOLUTION, état réécrit."""
    moment = pd.Timestamp(now)
    store = store or ev.store_for(settings)
    done = resolutions(journal)
    counts: dict[str, int] = {}
    for ident, call in calls(journal).items():
        if ident in done:
            continue
        at = pd.Timestamp(call["at"])
        if moment < at + pd.Timedelta(hours=4):
            continue
        end = pd.Timestamp(call["resolution_end"])
        reach = pd.Timedelta(M.placebo_reach_ns(call["unit"]), unit="ns")
        bars = _bars(store, call["symbol"], since=at - reach - pd.Timedelta(hours=1), until=min(moment, end + pd.Timedelta(hours=1)),
                     now=moment)
        out = resolve_one(call, bars, late=moment >= end + GAP_AFTER)
        if out is None:
            continue
        journal.append(RESOLUTION, out, now=moment)
        outbox.queue(settings, message_id=f"RESOLUTION:{ident}", text=outbox.resolution_message(out, call), now=moment)
        counts[out["status"]] = counts.get(out["status"], 0) + 1
    if counts:
        write_state(settings, journal, store, now=moment)
    return counts


# --- Mesures ---------------------------------------------------------------------------------------------------------------------

def _measure(rows: list[dict], scenario: str, *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    if not rows:
        return {"n": 0, "days": 0}
    rows = sorted(rows, key=lambda r: r["at"])
    r = np.array([x["results"][scenario]["r"] for x in rows], float)
    times = pd.to_datetime([x["at"] for x in rows], utc=True).to_numpy()
    excess = np.array([np.nan if x["results"][scenario]["excess"] is None else x["results"][scenario]["excess"] for x in rows], float)
    ok = np.isfinite(excess)
    ci_r, _ = day_block_ci95(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, min_blocks=8)
    ci_d, _ = day_block_ci(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=LEVEL, min_blocks=8)
    ci_x, _ = (day_block_ci(excess[ok], times[ok], block_days=BLOCK_DAYS, samples=samples, seed=seed, level=LEVEL, min_blocks=8)
               if ok.any() else (None, 0))
    hits = np.array([x["results"][scenario]["hits"] for x in rows])
    target = np.array([x["results"][scenario]["outcome"] == M.TARGET for x in rows])
    sides = {}
    for side in ("back", "forward"):
        values = np.array([np.nan if x["results"][scenario].get(f"excess_{side}") is None else x["results"][scenario][f"excess_{side}"]
                           for x in rows], float)
        fine = np.isfinite(values)
        sides[side] = round(float(values[fine].mean()), 4) if fine.any() else None
    streak = worst = 0
    for value in r:
        streak = streak + 1 if value < 0 else 0
        worst = max(worst, streak)
    by_pair: dict[str, float] = {}
    for row, value in zip(rows, r, strict=True):
        by_pair[row["symbol"]] = by_pair.get(row["symbol"], 0.0) + float(value)
    total_abs = sum(abs(v) for v in by_pair.values()) or 1.0
    top = sorted(by_pair.items(), key=lambda kv: -abs(kv[1]))[:5]
    pairs = {"pairs": len(by_pair), "top": [{"symbol": k, "r_sum": round(v, 4), "share_abs": round(abs(v) / total_abs, 4)} for k, v in top],
             "max_share_abs": round(abs(top[0][1]) / total_abs, 4) if top else None}
    return {"n": int(len(r)), "r_by_pair": pairs, "days": int(pd.DatetimeIndex(times).floor("D").nunique()), "r_mean": round(float(r.mean()), 4),
            "r_ci95": ci_r, "r_ci_decision": ci_d, "win_share": round(float((r > 0).mean()), 4), "tp1_rate": round(float((hits >= 1).mean()), 4),
            "target_rate": round(float(target.mean()), 4), "worst_streak": int(worst),
            "placebo_excess": round(float(excess[ok].mean()), 4) if ok.any() else None, "placebo_excess_ci": ci_x,
            "placebo_excess_back": sides["back"], "placebo_excess_forward": sides["forward"]}


def verdict(scenarios: dict, *, ended: bool) -> str:
    """Seuil de décision d'une configuration, sur le R NET SEUL (docs/PRICE_ACTION.md § 11.5) : INSUFFISANT sous 30
    appels résolus ou 10 jours (ou intervalle non calculable) ; SUPERIEUR_A_ZERO si l'intervalle 1 − 0,05/5 du R net
    moyen est > 0 en central ET en défavorable ; INFERIEUR_A_ZERO si l'IC95 est < 0 dans les deux ; sinon
    NON_DEMONTRE. Les placebos ne décident de rien (biais des placebos arrière, contrôle H0 n° 1)."""
    if not ended:
        return RUNNING
    central, adverse = scenarios.get(CENTRAL, {}), scenarios.get(ADVERSE, {})
    if central.get("n", 0) < MIN_RESOLVED or central.get("days", 0) < MIN_DAYS or any(
            x.get(k) is None for x in (central, adverse) for k in ("r_ci95", "r_ci_decision")):
        return INSUFFICIENT
    if central["r_ci_decision"][0] > 0 and adverse["r_ci_decision"][0] > 0:
        return ABOVE
    if central["r_ci95"][1] < 0 and adverse["r_ci95"][1] < 0:
        return BELOW
    return NOT_SHOWN


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    evaluations = [e["data"] for e in journal.entries({EVALUATION})]
    decided = calls(journal)
    done = resolutions(journal)
    resolved = [r for r in done.values() if r["status"] == RESOLVED and r.get("results")]
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in done for k in decided)
    refusals: dict[str, int] = {}
    candidates = dict.fromkeys(D.CONFIGS, 0)
    for e in evaluations:
        for reason, n in (e.get("refusals_by_reason") or {}).items():
            refusals[reason] = refusals.get(reason, 0) + n
        for config, n in (e.get("candidates") or {}).items():
            candidates[config] = candidates.get(config, 0) + n
    span = max(1.0, (pd.Timestamp(now) - pd.Timestamp(start["started_at"])) / pd.Timedelta(days=1))
    configs = {}
    for config in D.CONFIGS:
        mine = [r for r in resolved if r["config"] == config]
        scenarios = {s: _measure(mine, s) for s in SCENARIOS}
        gaps = sum(1 for k, r in done.items() if r["status"] == GAP and decided.get(k, {}).get("config") == config)
        configs[config] = {"calls": sum(1 for c in decided.values() if c["config"] == config), "resolved": len(mine),
                           "gaps": gaps,
                           "candidates": candidates.get(config, 0), "scenarios": scenarios,
                           "verdict": verdict(scenarios, ended=ended)}
    return {"evaluations": len(evaluations), "late": sum(1 for e in evaluations if e.get("late")),
            "candidates": sum(candidates.values()), "calls": len(decided), "resolved": len(resolved),
            "gaps": sum(1 for r in done.values() if r["status"] == GAP), "pending": sum(1 for k in decided if k not in done),
            "calls_per_week": round(len(decided) / span * 7, 2), "refusals_by_reason": refusals, "configs": configs,
            "overall_central": _measure(resolved, CENTRAL, samples=2000), "price_action": True, "ended": bool(ended),
            "verdict": {c: configs[c]["verdict"] for c in D.CONFIGS}}


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "configs": result["configs"], "calls": result["calls"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID,
    title="Price action : base et retest, compression, force relative, journée intérieure, base longue, contre placebos",
    hypothesis=("Un appel d'une des cinq configurations « price action » (docs/PRICE_ACTION.md), géré avec stop de clôture de son "
                "unité, stop de secours à −1,5 R, moitié à +1 R puis l'autre à l'objectif, 10 ou 30 jours au plus, rapporte en "
                "moyenne un R net > 0 après frais (verdict par configuration sur le R net seul ; 20 placebos de même "
                "géométrie rapportés en descriptif). Attendu : NON_DEMONTRE ou INSUFFISANT."),
    params={"evaluation": "chaque clôture 4 h UTC (la clôture journalière est celle de 00:00)",
            "history_days": ev.HISTORY_DAYS, "max_calls_per_day": ev.MAX_CALLS_PER_DAY, "max_delay_minutes": 30,
            "detect": D.params(),
            "manage": {"hard_stop_factor": M.HARD_STOP_FACTOR, "tp1_share": M.TP1_SHARE, "max_hold_days": M.MAX_HOLD_DAYS,
                       "rest_hours": M.REST_HOURS, "placebos": M.PLACEBOS, "placebo_hours": list(M.PLACEBO_HOURS),
                       "placebo_days": list(M.PLACEBO_DAYS), "seed_prefix": M.SEED_PREFIX},
            "min_resolved": MIN_RESOLVED, "min_days_resolved": MIN_DAYS, "alpha": ALPHA, "tests": TESTS, "decision_level": LEVEL,
            "decision": "R net seul (docs/PRICE_ACTION.md § 11.5) ; placebos descriptifs",
            "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS, "gap_after_days": GAP_AFTER.days,
            "exits": "taker partout ; bougie 1 h touchant objectif et stop de secours : stop",
            "universe": "liste halal figée de F15, magasin forward_figures/data en lecture seule"},
    rule_objects=(), config_keys=("data.assumed_availability_latency_seconds",),
    frozen_modules=("crypto_signal_intelligence.forward.f19", "crypto_signal_intelligence.price_action",
                    "crypto_signal_intelligence.price_action.detect", "crypto_signal_intelligence.price_action.manage",
                    "crypto_signal_intelligence.price_action.evaluate", "crypto_signal_intelligence.price_action.state",
                    "crypto_signal_intelligence.price_action.outbox", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci95"),
                      ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.forward.f15", "figure_store"),
                      ("crypto_signal_intelligence.patterns.indicators", "ema"),
                      ("crypto_signal_intelligence.patterns.primitives", "atr"),
                      ("crypto_signal_intelligence.patterns.primitives", "true_range"),
                      ("crypto_signal_intelligence.technical.analysis", "round_tick"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
