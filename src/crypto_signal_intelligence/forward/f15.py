"""F15_FIGURES : détecteur automatique de figures sur les paires admises, contre placebos (phase 11 ; docs/FORWARD_TESTS.md,
section F15_FIGURES ; définitions et règles : docs/INDICATEURS.md § 9, écrites avant le code).

À chaque passage horaire : bougies 1 h de chaque paire de la liste halal figée (magasin séparé `forward_figures/`),
agrégées en 4 h et 1 jour (alignées sur 00:00 UTC, bougies complètes seulement) ; détection sur toute l'histoire
depuis HISTORY_START (point de départ fixe : le ZigZag ne dépend pas d'une fenêtre glissante) ; chaque figure
nouvelle est inscrite (FIGURE), et une figure haussière jouable devient un ordre limite simulé (DECISION). Résolution
sur les bougies 1 minute (la seconde départage une minute ambiguë ; sinon stop d'abord), sortie par tiers, stop fixe,
60 bougies au plus ; 20 placebos de même géométrie, mêmes règles d'exécution (départage compris).
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from ..patterns import figures as fg
from ..patterns.primitives import ZIGZAG_M
from . import f4
from .costs import CENTRAL, SCENARIOS, costs_for
from .journal import Journal, utc_iso
from .registry import ForwardTest

TEST_ID = "F15_FIGURES"
FIGURE, DECISION, RESOLUTION = "FIGURE", "DECISION", "RESOLUTION"
TIMEFRAMES = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
HISTORY_START = pd.Timestamp("2025-08-01", tz="UTC")
ORDER_BARS = 20
HOLD_BARS = 60
PLACEBOS = 20
PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES = 1440, 43200
MINUTE = pd.Timedelta(minutes=1)
WEIGHTS = (1 / 3, 1 / 3, 1 / 3)
ALPHA = 0.05
SAMPLES, SEED, BLOCK_DAYS = 10_000, 20261003, 7
GAP_AFTER = pd.Timedelta(days=2)
LATE_AFTER = pd.Timedelta(hours=2)                  # figure inscrite plus tard que cela après sa clôture : non jouée
HEAD_CHUNK = pd.Timedelta(minutes=f4.MAX_PAGES * 1000 - 1000)
EXECUTED, CANCELLED, GAP = "EXECUTE", "ANNULE", "TROU"
DELISTED = "COTATION_ARRETEE"
ALL = "ensemble"


def figure_store(settings: Settings) -> CandleStore:
    return CandleStore(settings.root / "forward_figures" / "data")


def figure_settings(settings: Settings) -> Settings:
    from datetime import date
    data = settings.data.model_copy(update={"history_start": date(HISTORY_START.year, HISTORY_START.month, HISTORY_START.day)})
    return settings.model_copy(update={"root": settings.root / "forward_figures", "data": data})


# --- Bougies ----------------------------------------------------------------------------------------------------

def aggregate(h1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Bougies 4 h ou 1 jour à partir des bougies 1 h (alignées sur 00:00 UTC) ; une bougie n'est gardée que si elle
    contient toutes ses heures."""
    frame = h1[["open_time", "open", "high", "low", "close"]].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    if timeframe == "1h":
        return frame.sort_values("open_time").reset_index(drop=True)
    step = TIMEFRAMES[timeframe]
    hours = int(step / pd.Timedelta(hours=1))
    frame["bucket"] = frame["open_time"].dt.floor(step)
    grouped = frame.sort_values("open_time").groupby("bucket")
    out = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                      count=("open", "size")).reset_index()
    out = out[out["count"] == hours].rename(columns={"bucket": "open_time"})
    return out[["open_time", "open", "high", "low", "close"]].reset_index(drop=True)


def closed(frame: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    return frame[frame["open_time"] + TIMEFRAMES[timeframe] <= now].reset_index(drop=True)


# --- Détection ------------------------------------------------------------------------------------------------

def figure_id(symbol: str, timeframe: str, figure: fg.Figure, times: pd.Series) -> str:
    stamp = "-".join(times.iloc[i].strftime("%Y%m%dT%H%M") for i in figure.anchors)
    return f"{symbol}:{timeframe}:{figure.family}:{figure.side}:{stamp}"


def detect_frame(frame: pd.DataFrame, timeframe: str) -> list[fg.Figure]:
    return fg.detect(frame["open"], frame["high"], frame["low"], frame["close"], m=ZIGZAG_M[timeframe])


def placebo_offsets(figure_key: str) -> list[int]:
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{figure_key}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES + 1), PLACEBOS))


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime,
                     rest: PublicHttpClient | None = None) -> dict:
    """Met à jour les bougies 1 h, détecte, inscrit chaque figure nouvelle (détectée après le démarrage) et chaque
    ordre d'une figure haussière jouable. L'ordre part à la première minute qui suit à la fois la clôture de la bougie
    de détection (plus la latence) et l'inscription ; une figure inscrite plus de 2 h après sa clôture (machine
    éteinte, paire en échec) est inscrite mais jamais jouée (`late`). Aucune détection après la fin du recueil."""
    from ..data.pipeline import download
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    counts = {"figures": 0, "orders": 0, "late": 0, "repaired": 0, "errors": 0}
    figures = [e["data"] for e in journal.entries({FIGURE})]
    known = {f["figure_id"] for f in figures}
    ordered = {e["data"]["figure_id"] for e in journal.entries({DECISION})}
    for f in figures:                                   # arrêt brutal entre les deux inscriptions : décision reprise
        if f.get("played") and f.get("decision") and f["figure_id"] not in ordered:
            journal.append(DECISION, f["decision"], now=now)
            counts["repaired"] += 1
    if moment >= final:
        return counts
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    store = figure_store(settings)
    for symbol in start["halal"]["symbols"]:
        try:
            download(figure_settings(settings), symbol, "1h", now=now, rest_client=client, rest_only=True)
        except Exception:  # noqa: BLE001 - une paire en échec n'arrête pas les autres (bougies reprises au passage suivant)
            counts["errors"] += 1
        h1 = store.load(symbol, "1h")
        if h1.empty:
            continue
        h1 = h1[pd.to_datetime(h1["open_time"], utc=True) >= HISTORY_START]
        for timeframe, step in TIMEFRAMES.items():
            frame = closed(aggregate(h1, timeframe), timeframe, moment)
            if len(frame) < 30:
                continue
            for figure in detect_frame(frame, timeframe):
                at = frame["open_time"].iloc[figure.detected_at] + step       # clôture de la bougie de détection
                if at <= started or at > moment:
                    continue
                key = figure_id(symbol, timeframe, figure, frame["open_time"])
                if key in known:
                    continue
                known.add(key)
                late = moment - at > LATE_AFTER
                played = figure.side == "bull" and figure.valid and at < final and not late
                decision = ({"figure_id": key, "symbol": symbol, "timeframe": timeframe, "family": figure.family,
                             "entry": figure.entry, "stop": figure.stop, "targets": list(figure.targets),
                             "order_from": utc_iso(max(at + latency, moment).ceil("min")),
                             "order_until": utc_iso(at + ORDER_BARS * step), "hold_minutes": int(HOLD_BARS * step / MINUTE),
                             "placebo_minutes": placebo_offsets(key)} if played else None)
                data = {"figure_id": key, "symbol": symbol, "timeframe": timeframe, "family": figure.family,
                        "side": figure.side, "detected_at": utc_iso(at), "anchors": [utc_iso(frame["open_time"].iloc[i]) for i in figure.anchors],
                        "entry": figure.entry, "stop": figure.stop, "targets": list(figure.targets),
                        "zone": list(figure.zone) if figure.zone else None, "notes": figure.notes,
                        "valid": figure.valid, "late": late, "played": played, "decision": decision}
                journal.append(FIGURE, data, now=now)
                counts["figures"] += 1
                counts["late"] += int(late and figure.side == "bull" and figure.valid)
                if decision is not None:
                    journal.append(DECISION, decision, now=now)
                    counts["orders"] += 1
    return counts


# --- Exécution simulée ----------------------------------------------------------------------------------------

def _second_order(settings: Settings, symbol: str, minute: pd.Timestamp, stop: float, target: float) -> str:
    """Départage d'une minute où le stop et un objectif sont touchés : bougies 1 s de la minute ; « stop » si l'ordre
    ne peut pas être établi (prudence)."""
    from ..data import seconds as sec
    try:
        frame = sec.window(settings, symbol, minute, before=pd.Timedelta(0), after=MINUTE)
    except Exception:  # noqa: BLE001 - pas de secondes : prudence
        return "stop"
    for _, row in frame.iterrows():
        hit_stop, hit_target = row["low"] <= stop, row["high"] > target
        if hit_stop:
            return "stop"
        if hit_target:
            return "target"
    return "stop"


def simulate(bars: pd.DataFrame, *, entry: float, stop: float, targets: list[float], order_from: pd.Timestamp,
             order_until: pd.Timestamp | None, hold_minutes: int, symbol: str, scenario: str, market_entry: bool = False,
             resolver=None, late: bool = False) -> dict:
    """Une transaction : ordre limite (exécuté si le prix traverse, annulé si une bougie ouvre au stop ou dessous
    avant l'exécution ; déjà sous la limite à la pose : exécuté à l'ouverture, au marché) ou achat au marché
    (placebo) ; puis tiers aux objectifs (limite, maker), stop fixe et sortie à l'échéance au marché (taker).
    R rapporté au risque prévu (entrée − stop). `resolver(minute, stop, objectif)` départage une minute où le stop et
    un objectif sont touchés : « objectif » ne prend que ce premier objectif, le reste sort au stop dans la minute.
    Échéance en temps (exécution + `hold_minutes`) ; données arrêtées avant l'échéance et constat passé (`late`) :
    le reste est vendu à la dernière clôture (issue COTATION_ARRETEE)."""
    costs = costs_for(symbol, scenario)
    times = pd.to_datetime(bars["open_time"], utc=True)
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ns = times.dt.as_unit("ns").astype("int64").to_numpy()
    start = int(np.searchsorted(ns, pd.Timestamp(order_from).as_unit("ns").value, side="left"))
    fill_i, fill_price = None, None
    if market_entry:
        if start >= len(bars) or times.iloc[start] - order_from > pd.Timedelta(minutes=10):
            return {"status": GAP}
        fill_i, fill_price = start, float(o[start]) * (1 + costs.market)
        if fill_price <= stop:
            return {"status": CANCELLED, "reason": "ouverture au stop"}
    else:
        for i in range(start, len(bars)):
            if order_until is not None and times.iloc[i] >= order_until:
                return {"status": CANCELLED, "reason": "ordre expiré"}
            if o[i] <= stop:
                return {"status": CANCELLED, "reason": "stop atteint avant l'entrée"}
            if i == start and o[i] < entry:                         # exécutable dès la pose : au marché
                fill_i, fill_price = i, float(o[i]) * (1 + costs.market)
                break
            if lo[i] < entry:
                fill_i, fill_price = i, min(entry, float(o[i]))
                break
        if fill_i is None:
            ended = order_until is not None and len(bars) and times.iloc[-1] + MINUTE >= order_until
            return {"status": CANCELLED, "reason": "ordre expiré"} if ended else {"status": "EN_COURS"}
    assert fill_i is not None and fill_price is not None
    entry_cost = fill_price * (1 + costs.fee)
    risk = entry - stop
    remaining, proceeds, hits = 1.0, 0.0, 0
    horizon = ns[fill_i] + hold_minutes * MINUTE.value
    last = int(np.searchsorted(ns, horizon, side="left"))          # bougies ouvertes avant l'échéance : [fill_i, last)
    exit_at, outcome = None, None
    for i in range(fill_i, last):
        stop_hit = lo[i] <= stop
        target_hit = hits < len(targets) and h[i] > targets[hits] and i > fill_i
        if stop_hit and target_hit:
            order = resolver(times.iloc[i], stop, targets[hits]) if resolver else "stop"
            if order != "stop":                                     # objectif d'abord : celui-là seul, puis le stop
                share = WEIGHTS[hits] if hits < len(targets) - 1 else remaining
                proceeds += share * max(targets[hits], float(o[i])) * (1 - costs.fee)
                remaining -= share
                hits += 1
                if remaining <= 1e-12:
                    exit_at, outcome = times.iloc[i], f"TP{hits}"
                    break
        if stop_hit:
            price = min(stop, float(o[i])) if i > fill_i else stop
            proceeds += remaining * price * (1 - costs.market) * (1 - costs.fee)
            remaining, exit_at, outcome = 0.0, times.iloc[i], "STOP" if hits == 0 else f"STOP_APRES_TP{hits}"
            break
        while i > fill_i and hits < len(targets) and h[i] > targets[hits]:
            price = max(targets[hits], float(o[i]))
            share = WEIGHTS[hits] if hits < len(targets) - 1 else remaining
            proceeds += share * price * (1 - costs.fee)
            remaining -= share
            hits += 1
            if remaining <= 1e-12:
                exit_at, outcome = times.iloc[i], f"TP{hits}"
                break
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        if ns[-1] + MINUTE.value >= horizon:
            k, label = last - 1, "TEMPS"
        elif late:
            k, label = len(bars) - 1, DELISTED
        else:
            return {"status": "EN_COURS"}
        proceeds += remaining * float(c[k]) * (1 - costs.market) * (1 - costs.fee)
        exit_at, outcome = times.iloc[k], label if hits == 0 else f"{label}_APRES_TP{hits}"
    r = (proceeds - entry_cost) / risk
    return {"status": EXECUTED, "fill_at": utc_iso(times.iloc[fill_i]), "fill_price": fill_price, "outcome": outcome,
            "hits": hits, "r": round(float(r), 6), "exit_at": utc_iso(exit_at)}


def _fill_head(settings: Settings, store: CandleStore, client: PublicHttpClient, symbol: str, begin: pd.Timestamp, *,
               now: pd.Timestamp) -> None:
    """Comble le début manquant des bougies 1 min à reculons, par tranches que `f4.ensure_minutes` remplit en entier
    (sinon un début de plus de 60 000 minutes laisserait un trou intérieur jamais repris)."""
    for _ in range(20):
        stored = store.load_since(symbol, "1m", begin)
        if stored.empty:
            return                                          # rien en magasin : ensure_minutes part de `begin`
        first = pd.Timestamp(stored["open_time"].min())
        if first <= begin + MINUTE:
            return
        lo = max(begin, first - HEAD_CHUNK)
        f4.ensure_minutes(settings, store, client, symbol, lo, first, now=now)
        after = pd.Timestamp(store.load_since(symbol, "1m", begin)["open_time"].min())
        if after >= first:                                  # rien avant (paire cotée plus tard) : fin
            return


def _horizon_end(d: dict) -> pd.Timestamp:
    return pd.Timestamp(d["order_until"]) + pd.Timedelta(minutes=d["hold_minutes"]) + MINUTE


def resolve_one(settings: Settings, d: dict, bars: pd.DataFrame, *, late: bool) -> dict | None:
    """Résolution d'un ordre sur les bougies 1 min de sa paire (fenêtre : 30 jours avant l'ordre → fin de l'horizon) ;
    None si des données manquent encore et que le délai de constat n'est pas passé."""
    order_from, order_until = pd.Timestamp(d["order_from"]), pd.Timestamp(d["order_until"])

    def resolver(minute, stop, target):
        return _second_order(settings, d["symbol"], minute, stop, target)

    results: dict = {}
    status = None
    for scenario in SCENARIOS:
        trade = simulate(bars, entry=d["entry"], stop=d["stop"], targets=d["targets"], order_from=order_from,
                         order_until=order_until, hold_minutes=d["hold_minutes"], symbol=d["symbol"], scenario=scenario,
                         resolver=resolver, late=late)
        status = trade["status"]
        results[scenario] = trade
    if status == "EN_COURS" and not late:
        return None
    if status in (CANCELLED, "EN_COURS", GAP):
        final_status = CANCELLED if status == CANCELLED else GAP
        return {"figure_id": d["figure_id"], "status": final_status, "reason": results[CENTRAL].get("reason"), "results": None}
    fill_at = pd.Timestamp(results[CENTRAL]["fill_at"])
    pending = False
    times = bars["open_time"]
    for scenario in SCENARIOS:
        placebos: list[float | None] = []
        for offset in d["placebo_minutes"]:
            when = fill_at - pd.Timedelta(minutes=int(offset))
            ref = bars[times >= when]
            if ref.empty:
                placebos.append(None)
                continue
            q0 = float(ref["open"].iloc[0])
            p = simulate(bars, entry=q0, stop=q0 * d["stop"] / d["entry"], targets=[q0 * t / d["entry"] for t in d["targets"]],
                         order_from=when, order_until=None, hold_minutes=d["hold_minutes"], symbol=d["symbol"],
                         scenario=scenario, market_entry=True, resolver=resolver, late=late)
            pending = pending or p["status"] == "EN_COURS"
            placebos.append(p.get("r") if p["status"] == EXECUTED else None)
        usable = [x for x in placebos if x is not None]
        results[scenario]["placebos"] = placebos
        results[scenario]["placebo_mean"] = round(float(np.mean(usable)), 6) if usable else None
        results[scenario]["excess"] = round(results[scenario]["r"] - float(np.mean(usable)), 6) if usable else None
    if pending and not late:
        return None
    return {"figure_id": d["figure_id"], "status": EXECUTED, "symbol": d["symbol"], "timeframe": d["timeframe"],
            "family": d["family"], "entry_at": results[CENTRAL]["fill_at"], "results": results}


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest: PublicHttpClient | None = None) -> dict:
    """Résout chaque ordre une fois son horizon écoulé (ordre + 60 bougies), avec ses placebos ; bougies 1 min chargées
    une fois par paire et par passage ; trou constaté 2 jours après l'horizon si des données manquent."""
    done = {e["data"]["figure_id"] for e in journal.entries({RESOLUTION})}
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    store = figure_store(settings)
    due: dict[str, list[dict]] = {}
    for entry in journal.entries({DECISION}):
        d = entry["data"]
        if d["figure_id"] not in done and moment >= _horizon_end(d):
            due.setdefault(d["symbol"], []).append(d)
    counts: dict[str, int] = {}
    for symbol, decisions in due.items():
        begin = min(pd.Timestamp(d["order_from"]) for d in decisions) - pd.Timedelta(minutes=PLACEBO_MAX_MINUTES)
        stop_at = max(_horizon_end(d) for d in decisions)
        try:
            _fill_head(settings, store, client, symbol, begin, now=moment)
            bars = f4.ensure_minutes(settings, store, client, symbol, begin, stop_at, now=moment)
        except (HttpError, ValueError):
            bars = store.load_since(symbol, "1m", begin)
        bars = bars.assign(open_time=pd.to_datetime(bars["open_time"], utc=True))
        for d in decisions:
            window = bars[(bars["open_time"] >= pd.Timestamp(d["order_from"]) - pd.Timedelta(minutes=PLACEBO_MAX_MINUTES))
                          & (bars["open_time"] < _horizon_end(d))].reset_index(drop=True)
            out = resolve_one(settings, d, window, late=moment >= _horizon_end(d) + GAP_AFTER)
            if out is None:
                continue
            journal.append(RESOLUTION, out, now=moment)
            counts[out["status"]] = counts.get(out["status"], 0) + 1
    return counts


# --- Mesures -------------------------------------------------------------------------------------------------------

def _measure(rows: list[dict], scenario: str, level: float, *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    if not rows:
        return {"n": 0, "days": 0}
    rows = sorted(rows, key=lambda r: r["entry_at"])
    r = np.array([x["results"][scenario]["r"] for x in rows], float)
    times = pd.to_datetime([x["entry_at"] for x in rows], utc=True).to_numpy()
    excess = np.array([x["results"][scenario]["excess"] if x["results"][scenario]["excess"] is not None else np.nan
                       for x in rows], float)
    ok = np.isfinite(excess)
    ci_r, _ = day_block_ci(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=0.95, min_blocks=8)
    ci_x, _ = (day_block_ci(excess[ok], times[ok], block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level,
                            min_blocks=8) if ok.any() else (None, 0))
    hits = np.array([x["results"][scenario]["hits"] for x in rows])
    return {"n": int(len(r)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()), "r_mean": round(float(r.mean()), 4),
            "r_ci95": ci_r, "win_share": round(float((r > 0).mean()), 4),
            "tp_reached": {f"TP{k}": round(float((hits >= k).mean()), 4) for k in (1, 2, 3)},
            "placebo_excess": round(float(excess[ok].mean()), 4) if ok.any() else None, "placebo_excess_ci": ci_x}


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    figures = [e["data"] for e in journal.entries({FIGURE})]
    decisions = {e["data"]["figure_id"]: e["data"] for e in journal.entries({DECISION})}
    resolutions = {e["data"]["figure_id"]: e["data"] for e in journal.entries({RESOLUTION})}
    executed = [r for r in resolutions.values() if r["status"] == EXECUTED and r.get("results")]
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in resolutions for k in decisions)
    level = 1 - ALPHA / 2
    out: dict = {"figures": len(figures), "bull": sum(1 for f in figures if f["side"] == "bull"),
                 "bear": sum(1 for f in figures if f["side"] == "bear"),
                 "invalid": sum(1 for f in figures if f["side"] == "bull" and not f["valid"]),
                 "late": sum(1 for f in figures if f["side"] == "bull" and f["valid"] and f.get("late")),
                 "orders": len(decisions), "executed": len(executed),
                 "cancelled": sum(1 for r in resolutions.values() if r["status"] == CANCELLED),
                 "gaps": sum(1 for r in resolutions.values() if r["status"] == GAP),
                 "pending": sum(1 for k in decisions if k not in resolutions), "ended": bool(ended), "groups": {},
                 # Descriptif : figures différentes sur les mêmes pivots (tête-épaules et triangle, par exemple) donnent
                 # souvent le même ordre ; ordres distincts = (paire, unité, départ de l'ordre, entrée à 0,1 % près).
                 "distinct_orders": len({(d["symbol"], d["timeframe"], d["order_from"], round(math.log(d["entry"]) / 0.001))
                                         for d in decisions.values() if d.get("entry", 0) > 0}),
                 "distinct_executed": len({(r["symbol"], r["timeframe"], r["entry_at"],
                                            round(math.log(decisions[r["figure_id"]]["entry"]) / 0.001))
                                           for r in executed if decisions.get(r["figure_id"], {}).get("entry", 0) > 0})}
    whole = {s: _measure(executed, s, level) for s in SCENARIOS}
    out["overall"] = {"scenarios": whole, "verdict": f4.verdict(whole, ended=ended)}
    for family in fg.FAMILIES:
        for timeframe in TIMEFRAMES:
            mine = [r for r in executed if r["family"] == family and r["timeframe"] == timeframe]
            found = sum(1 for f in figures if f["family"] == family and f["timeframe"] == timeframe and f["side"] == "bull")
            if found or mine:
                out["groups"][f"{family}/{timeframe}"] = {"bull_figures": found, "executed": len(mine),
                                                          "central": _measure(mine, CENTRAL, level, samples=2000)}
    out["verdict"] = out["overall"]["verdict"]
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "overall": result["overall"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


OVERLAP_WINDOW = pd.Timedelta(hours=24)


def analyst_overlap(settings: Settings, journal: Journal, *, window: pd.Timedelta = OVERLAP_WINDOW) -> dict:
    """Comparaison à trois (descriptive) : figures haussières jouées du détecteur et signaux joués des analystes (F4,
    F16) sur la même paire à moins de 24 h d'écart."""
    figures = [e["data"] for e in journal.entries({DECISION})]
    signals = []
    for test_id in ("F4_TELEGRAM", "F16_TELEGRAM_IMAGES"):
        other = Journal(settings.root / "forward" / f"{test_id}.jsonl")
        signals += [e["data"] | {"test": test_id} for e in other.entries({"DECISION"})]
    pairs = []
    for f in figures:
        t = pd.Timestamp(f["order_from"])
        for s in signals:
            if s.get("symbol") == f["symbol"] and s.get("entry_at") and abs(pd.Timestamp(s["entry_at"]) - t) <= window:
                pairs.append({"figure_id": f["figure_id"], "signal_id": s.get("signal_id"), "test": s["test"]})
    return {"figures": len(figures), "analyst_signals": len(signals), "same_pair_24h": pairs}


TEST = ForwardTest(
    test_id=TEST_ID, title="Détecteur automatique de figures (harmoniques, triangles, lignes de tendance, ICT/SMC, figures "
                           "classiques) contre placebos",
    hypothesis=("Une figure haussière détectée mécaniquement (docs/INDICATEURS.md § 9), jouée avec les règles fixes du § 9.5, "
                "rapporte en moyenne plus, en R net, que 20 transactions placebo de même géométrie sur la même paire à des "
                "moments tirés au hasard dans les 30 jours précédents."),
    params={"timeframes": list(TIMEFRAMES), "zigzag_m": ZIGZAG_M, "families": list(fg.FAMILIES), "tolerance": fg.TOLERANCE,
            "stop_atr": fg.STOP_ATR, "order_bars": ORDER_BARS, "hold_bars": HOLD_BARS, "exits": "tiers aux trois objectifs",
            "placebos": PLACEBOS, "placebo_minutes": [PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES],
            "history_start": str(HISTORY_START), "min_resolved": f4.MIN_RESOLVED, "min_days": f4.MIN_DAYS, "alpha": ALPHA,
            "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "execution": "bougies 1 min, la seconde départage une minute ambiguë (objectif d'abord : ce seul objectif, "
                         "puis le stop), sinon stop d'abord ; placebos : mêmes règles",
            "late_after_hours": LATE_AFTER / pd.Timedelta(hours=1), "risk": "entrée prévue − stop"},
    rule_objects=(), config_keys=("data.rest_base_url", "data.assumed_availability_latency_seconds"),
    frozen_modules=("crypto_signal_intelligence.forward.f15", "crypto_signal_intelligence.patterns.figures",
                    "crypto_signal_intelligence.patterns.smc", "crypto_signal_intelligence.patterns.primitives",
                    "crypto_signal_intelligence.data.seconds", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal",
                    "crypto_signal_intelligence.forward.f4"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.data.rest", "fetch_klines"),
                      ("crypto_signal_intelligence.data.schema", "normalize"),
                      ("crypto_signal_intelligence.data.store", "CandleStore"),
                      ("crypto_signal_intelligence.data.pipeline", "download")),
)

