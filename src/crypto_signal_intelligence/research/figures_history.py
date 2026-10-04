"""Figures et méthodes des analystes rejouées sur DEVELOPMENT, contre placebos (docs/FIGURES_HISTORIQUE.md, déclaré
le 2026-10-04 avant code et exécution) : les 12 familles du détecteur de F15 (importé, jamais modifié), les éléments
ICT/SMC pris seuls et les divergences RSI ; règles de transaction de F15 (§ 9.5 de INDICATEURS.md), 20 placebos de
même géométrie ; 40 paires de recherche, 1 h / 4 h / 1 jour, exécution sur les bougies 1 minute. 19 comparaisons."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from numba import njit

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..features.loader import MissingData
from ..forward import f15
from ..forward.costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from ..forward.journal import utc_iso
from ..patterns import figures as fg
from ..patterns import indicators, smc
from ..patterns.primitives import ZIGZAG_M, atr, fractal_pivots
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .universe import RESEARCH_UNIVERSE

KIND = "FIGURES_HISTORY"
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
WARMUP = pd.Timedelta(days=90)
STOP_ATR = 0.25
R_MULTIPLES = (1.0, 2.0, 3.0)
SMC_METHODS = ("FVG", "OB", "SWEEP", "BOS", "CHOCH", "RSI_DIV")
MARKET_METHODS = frozenset({"SWEEP", "RSI_DIV"})
METHODS = (*fg.FAMILIES, *SMC_METHODS)
ENSEMBLE = "F15_ENSEMBLE"
N_TRIALS = len(METHODS) + 1
LEVEL = 1 - 0.05 / N_TRIALS
BLOCK_DAYS, SAMPLES, SEED, MIN_BLOCKS = 28, 10_000, 20261004, 10
MIN_TRADES = 30
MINUTE_NS = 60_000_000_000
EXECUTED, CANCELLED, GAP, INVALID = f15.EXECUTED, f15.CANCELLED, f15.GAP, "INVALIDE"
ABOVE, BELOW, NOT_SHOWN, INSUFFICIENT = "SUPERIEUR_AU_HASARD", "INFERIEUR_AU_HASARD", "NON_DEMONTRE", "INSUFFISANT"


# --- Déclencheurs ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Setup:
    key: str
    method: str
    timeframe: str
    at: pd.Timestamp                    # clôture de la bougie de détection
    stop: float
    entry: float | None = None          # None : achat au marché à la première minute (entrée = son ouverture)
    targets: tuple[float, ...] = ()     # vide : objectifs à 1, 2 et 3 R posés à l'exécution
    valid: bool = True

    @property
    def market(self) -> bool:
        return self.entry is None


def _key(symbol: str, timeframe: str, method: str, anchors, times: pd.Series) -> str:
    stamp = "-".join(times.iloc[i].strftime("%Y%m%dT%H%M") for i in anchors)
    return f"{symbol}:{timeframe}:{method}:bull:{stamp}"


def _limit(key: str, method: str, timeframe: str, at: pd.Timestamp, entry: float, stop: float) -> Setup:
    risk = entry - stop
    targets = tuple(entry + k * risk for k in R_MULTIPLES)
    valid = bool(np.isfinite(stop) and stop < entry * (1 - fg.MIN_STOP_DISTANCE) and targets[0] > entry)
    return Setup(key, method, timeframe, at, stop, entry, targets, valid)


def figure_setups(frame: pd.DataFrame, timeframe: str, symbol: str) -> list[Setup]:
    """Figures haussières du détecteur de F15, identité et niveaux de F15 (une figure n'est jouée qu'une fois)."""
    times, step = frame["open_time"], f15.TIMEFRAMES[timeframe]
    out: dict[str, Setup] = {}
    for f in f15.detect_frame(frame, timeframe):
        if f.side != "bull":
            continue
        key = f15.figure_id(symbol, timeframe, f, times)
        if key in out:
            continue
        at = times.iloc[f.detected_at] + step
        if f.valid and f.entry is not None and f.stop is not None:
            out[key] = Setup(key, f.family, timeframe, at, float(f.stop), float(f.entry), tuple(float(t) for t in f.targets))
        else:
            out[key] = Setup(key, f.family, timeframe, at, math.nan, math.nan, (), False)
    return list(out.values())


def smc_setups(frame: pd.DataFrame, timeframe: str, symbol: str) -> list[Setup]:
    """Éléments ICT/SMC pris seuls et divergences RSI (règles de docs/FIGURES_HISTORIQUE.md, partie B)."""
    times, step = frame["open_time"], f15.TIMEFRAMES[timeframe]
    o, h, lo, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(h, lo, c)
    out: list[Setup] = []

    def close_of(i: int) -> pd.Timestamp:
        return times.iloc[i] + step

    for g in smc.fair_value_gaps(h, lo, a):
        if g.side == "bull":
            i = g.known_at
            out.append(_limit(_key(symbol, timeframe, "FVG", (i,), times), "FVG", timeframe, close_of(i),
                              g.top, g.bottom - STOP_ATR * a[i]))
    for b in smc.order_blocks(o, h, lo, c, a):
        if b.side == "bull":
            j = b.known_at
            out.append(_limit(_key(symbol, timeframe, "OB", (b.index, j), times), "OB", timeframe, close_of(j),
                              b.top, b.bottom - STOP_ATR * a[j]))
    for s in smc.liquidity_sweeps(h, lo, c):
        if s.side == "bull":
            i = s.known_at
            stop = float(lo[i] - STOP_ATR * a[i])
            out.append(Setup(_key(symbol, timeframe, "SWEEP", (i,), times), "SWEEP", timeframe, close_of(i), stop,
                             valid=bool(np.isfinite(stop))))
    lows = sorted((p for p in fractal_pivots(h, lo) if p.kind == "low"), key=lambda p: (p.known_at, p.index))
    known = [p.known_at for p in lows]
    for brk in smc.structure(h, lo, c, a):
        if brk.side != "bull":
            continue
        i = brk.index
        k = int(np.searchsorted(known, i, side="left")) - 1       # dernier pivot bas connu avant i
        method = "BOS" if brk.kind == "BOS" else "CHOCH"
        key = _key(symbol, timeframe, method, (brk.pivot_index, i), times)
        if k < 0:
            out.append(Setup(key, method, timeframe, close_of(i), math.nan, math.nan, (), False))
            continue
        out.append(_limit(key, method, timeframe, close_of(i), brk.level, lows[k].price - STOP_ATR * a[i]))
    for d in indicators.rsi_divergences(h, lo, c):
        if d.side == "bull":
            j = d.known_at
            stop = float(lo[d.second] - STOP_ATR * a[j])
            out.append(Setup(_key(symbol, timeframe, "RSI_DIV", (d.first, d.second), times), "RSI_DIV", timeframe,
                             close_of(j), stop, valid=bool(np.isfinite(stop))))
    return out


# --- Exécution : copie compilée de f15.simulate (resolver=None), prouvée identique par test --------------------------

ST_EXECUTED, ST_CANCELLED_EXPIRED, ST_CANCELLED_STOP, ST_CANCELLED_OPEN, ST_GAP, ST_RUNNING = 0, 1, 2, 3, 4, 5
OUT_STOP, OUT_TP, OUT_TIME, OUT_DELISTED = 0, 1, 2, 3
STATUS = {ST_EXECUTED: EXECUTED, ST_CANCELLED_EXPIRED: CANCELLED, ST_CANCELLED_STOP: CANCELLED,
          ST_CANCELLED_OPEN: CANCELLED, ST_GAP: GAP, ST_RUNNING: "EN_COURS"}
REASON = {ST_CANCELLED_EXPIRED: "ordre expiré", ST_CANCELLED_STOP: "stop atteint avant l'entrée",
          ST_CANCELLED_OPEN: "ouverture au stop"}


@njit(cache=True)
def _simulate(o, h, lo, c, ns, entry, stop, targets, order_from, order_until, hold_ns, market_entry, cost_market, fee,
              weights, late):
    """Même calcul que `f15.simulate` sans départage (minute ambiguë : stop d'abord). `order_until` < 0 : aucun.
    Renvoie (statut, indice d'exécution, prix d'exécution, objectifs atteints, R, indice de sortie, issue)."""
    n = len(ns)
    start = np.searchsorted(ns, order_from)
    fill_i = -1
    fill_price = 0.0
    if market_entry:
        if start >= n or ns[start] - order_from > 10 * MINUTE_NS:
            return ST_GAP, -1, 0.0, 0, 0.0, -1, -1
        fill_i = start
        fill_price = o[start] * (1 + cost_market)
        if fill_price <= stop:
            return ST_CANCELLED_OPEN, -1, 0.0, 0, 0.0, -1, -1
    else:
        for i in range(start, n):
            if order_until >= 0 and ns[i] >= order_until:
                return ST_CANCELLED_EXPIRED, -1, 0.0, 0, 0.0, -1, -1
            if o[i] <= stop:
                return ST_CANCELLED_STOP, -1, 0.0, 0, 0.0, -1, -1
            if i == start and o[i] < entry:
                fill_i = i
                fill_price = o[i] * (1 + cost_market)
                break
            if lo[i] < entry:
                fill_i = i
                fill_price = min(entry, o[i])
                break
        if fill_i < 0:
            if order_until >= 0 and n > 0 and ns[n - 1] + MINUTE_NS >= order_until:
                return ST_CANCELLED_EXPIRED, -1, 0.0, 0, 0.0, -1, -1
            return ST_RUNNING, -1, 0.0, 0, 0.0, -1, -1
    entry_cost = fill_price * (1 + fee)
    risk = entry - stop
    remaining, proceeds, hits = 1.0, 0.0, 0
    count = len(targets)
    horizon = ns[fill_i] + hold_ns
    last = np.searchsorted(ns, horizon)
    exit_i, outcome = -1, -1
    for i in range(fill_i, last):
        if lo[i] <= stop:
            price = min(stop, o[i]) if i > fill_i else stop
            proceeds += remaining * price * (1 - cost_market) * (1 - fee)
            remaining = 0.0
            exit_i, outcome = i, OUT_STOP
            break
        while i > fill_i and hits < count and h[i] > targets[hits]:
            price = max(targets[hits], o[i])
            share = weights[hits] if hits < count - 1 else remaining
            proceeds += share * price * (1 - fee)
            remaining -= share
            hits += 1
            if remaining <= 1e-12:
                exit_i, outcome = i, OUT_TP
                break
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        if ns[n - 1] + MINUTE_NS >= horizon:
            k, outcome = last - 1, OUT_TIME
        elif late:
            k, outcome = n - 1, OUT_DELISTED
        else:
            return ST_RUNNING, -1, 0.0, 0, 0.0, -1, -1
        proceeds += remaining * c[k] * (1 - cost_market) * (1 - fee)
        exit_i = k
    return ST_EXECUTED, fill_i, fill_price, hits, (proceeds - entry_cost) / risk, exit_i, outcome


@njit(cache=True)
def _placebos(o, h, lo, c, ns, fill_ns, offsets, entry, stop, targets, hold_ns, cost_market, fee, weights, late):
    """R et objectifs atteints des placebos (NaN et −1 : inutilisable), comme `f15.resolve_one` (mêmes opérations :
    `q0 · stop / entrée`, `q0 · objectif / entrée`)."""
    m = len(offsets)
    r = np.full(m, np.nan)
    hits = np.full(m, -1)
    scaled = np.empty(len(targets))
    for k in range(m):
        when = fill_ns - offsets[k] * MINUTE_NS
        start = np.searchsorted(ns, when)
        if start >= len(ns):
            continue
        q0 = o[start]
        for j in range(len(targets)):
            scaled[j] = q0 * targets[j] / entry
        status, _, _, got, value, _, _ = _simulate(o, h, lo, c, ns, q0, q0 * stop / entry, scaled, when, -1,
                                                   hold_ns, True, cost_market, fee, weights, late)
        if status == ST_EXECUTED:
            r[k] = value
            hits[k] = got
    return r, hits


WEIGHTS = np.asarray(f15.WEIGHTS, float)


def _label(outcome: int, hits: int) -> str:
    base = {OUT_STOP: "STOP", OUT_TIME: "TEMPS", OUT_DELISTED: f15.DELISTED}.get(outcome)
    if outcome == OUT_TP:
        return f"TP{hits}"
    assert base is not None
    return base if hits == 0 else f"{base}_APRES_TP{hits}"


@dataclass
class Minutes:
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    ns: np.ndarray

    @classmethod
    def from_frame(cls, bars: pd.DataFrame) -> Minutes:
        times = pd.to_datetime(bars["open_time"], utc=True)
        o, h, lo, c = (np.ascontiguousarray(bars[k].to_numpy(float)) for k in ("open", "high", "low", "close"))
        return cls(o, h, lo, c, np.ascontiguousarray(times.dt.as_unit("ns").astype("int64").to_numpy()))


def simulate_fast(m: Minutes, *, entry: float, stop: float, targets, order_from: pd.Timestamp,
                  order_until: pd.Timestamp | None, hold_minutes: int, symbol: str, scenario: str,
                  market_entry: bool = False, late: bool = True) -> dict:
    """Interface de `f15.simulate` (resolver=None) sur la copie compilée ; mêmes champs."""
    costs = costs_for(symbol, scenario)
    status, fill_i, fill_price, hits, r, exit_i, outcome = _simulate(
        m.o, m.h, m.lo, m.c, m.ns, float(entry), float(stop), np.asarray(targets, float),
        pd.Timestamp(order_from).as_unit("ns").value, -1 if order_until is None else pd.Timestamp(order_until).as_unit("ns").value,
        int(hold_minutes) * MINUTE_NS, bool(market_entry), costs.market, costs.fee, WEIGHTS, bool(late))
    if status != ST_EXECUTED:
        out = {"status": STATUS[status]}
        if status in REASON:
            out["reason"] = REASON[status]
        return out
    return {"status": EXECUTED, "fill_at": _iso(m.ns[fill_i]), "fill_price": float(fill_price), "outcome": _label(outcome, hits),
            "hits": int(hits), "r": round(float(r), 6), "exit_at": _iso(m.ns[exit_i])}


def _iso(value: int) -> str:
    return utc_iso(pd.Timestamp(int(value), tz="UTC"))


# --- Une transaction et ses placebos ------------------------------------------------------------------------------

def order_window(setup: Setup, latency: pd.Timedelta) -> tuple[pd.Timestamp, pd.Timestamp | None, int]:
    """Départ de l'ordre (première minute après la clôture plus la latence, comme F15), fin de validité (20 bougies
    après la clôture ; aucune pour un achat au marché) et durée maximale en minutes (60 bougies)."""
    step = f15.TIMEFRAMES[setup.timeframe]
    start = (setup.at + latency).ceil("min")
    until = None if setup.market else setup.at + f15.ORDER_BARS * step
    return start, until, int(f15.HOLD_BARS * step / pd.Timedelta(minutes=1))


def play(setup: Setup, m: Minutes, symbol: str, latency: pd.Timedelta) -> dict:
    """Résultat d'un déclencheur dans les deux scénarios de frais, avec ses 20 placebos (ceux de F15)."""
    row = {"key": setup.key, "symbol": symbol, "timeframe": setup.timeframe, "method": setup.method,
           "at": setup.at, "status": INVALID, "reason": None}
    if not setup.valid:
        return row
    start, until, hold = order_window(setup, latency)
    row["order_from"] = start
    entry, stop, targets = setup.entry, setup.stop, setup.targets
    if setup.market:
        first = int(np.searchsorted(m.ns, start.as_unit("ns").value))
        if first >= len(m.ns) or m.ns[first] - start.as_unit("ns").value > 10 * MINUTE_NS:
            row["status"] = GAP
            return row
        entry = float(m.o[first])
        if not stop < entry * (1 - fg.MIN_STOP_DISTANCE):
            row["reason"] = "stop au-dessus de l'ouverture"
            return row
        targets = tuple(entry + k * (entry - stop) for k in R_MULTIPLES)
    assert entry is not None
    row.update({"entry": entry, "stop": stop, "tp1": targets[0], "risk_pct": (entry - stop) / entry})
    results = {s: simulate_fast(m, entry=entry, stop=stop, targets=targets, order_from=start, order_until=until,
                                hold_minutes=hold, symbol=symbol, scenario=s, market_entry=setup.market)
               for s in SCENARIOS}
    status = results[ADVERSE]["status"]                     # comme f15.resolve_one : le dernier scénario décide
    if status != EXECUTED:
        row["status"] = CANCELLED if status == CANCELLED else GAP
        row["reason"] = results[CENTRAL].get("reason")
        return row
    row["status"] = EXECUTED
    fill_at = pd.Timestamp(results[CENTRAL]["fill_at"])
    row["fill_at"] = fill_at
    row["fill_price"] = results[CENTRAL]["fill_price"]
    # Exécution « maker » : ordre limite traversé (ni achat au marché, ni première minute ouverte sous la limite).
    first = int(np.searchsorted(m.ns, start.as_unit("ns").value))
    fill_i = int(np.searchsorted(m.ns, fill_at.as_unit("ns").value))
    maker = not setup.market and not (fill_i == first and m.o[first] < entry)
    row["maker"] = maker
    offsets = np.asarray(f15.placebo_offsets(setup.key), np.int64)
    for s in SCENARIOS:
        costs = costs_for(symbol, s)
        raw, hits = _placebos(m.o, m.h, m.lo, m.c, m.ns, fill_at.as_unit("ns").value, offsets, float(entry), float(stop),
                              np.asarray(targets, float), hold * MINUTE_NS, costs.market, costs.fee, WEIGHTS, True)
        r = np.array([round(float(x), 6) if np.isfinite(x) else np.nan for x in raw])     # arrondi de f15.simulate
        usable = np.isfinite(r)
        own = results[s]
        row[f"r_{s}"] = own["r"]
        row[f"hits_{s}"] = own["hits"]
        row[f"outcome_{s}"] = own["outcome"]
        row[f"placebo_n_{s}"] = int(usable.sum())
        row[f"placebo_mean_{s}"] = round(float(r[usable].mean()), 6) if usable.any() else None
        row[f"placebo_tp1_{s}"] = float((hits[usable] >= 1).mean()) if usable.any() else None
        row[f"excess_{s}"] = round(own["r"] - float(r[usable].mean()), 6) if usable.any() else None
        # Excès à frais d'entrée égaux (relecture avant exécution) : un placebo paie à l'entrée l'écart et le glissement
        # d'un achat au marché, soit exactement market × (1 + frais) / risque relatif en R de plus qu'une entrée maker
        # au même prix (les sorties ne dépendent pas du prix d'exécution) ; retiré quand le déclencheur entre en maker.
        handicap = costs.market * (1 + costs.fee) / row["risk_pct"] if maker else 0.0
        row[f"excess_adj_{s}"] = round(row[f"excess_{s}"] - handicap, 6) if usable.any() else None
    return row


# --- Une paire -------------------------------------------------------------------------------------------------

def in_period(setup: Setup, *, first_bar: pd.Timestamp, end: pd.Timestamp) -> bool:
    step = f15.TIMEFRAMES[setup.timeframe]
    return (setup.at >= max(FIRST_DAY, first_bar + WARMUP)
            and setup.at + (f15.ORDER_BARS + f15.HOLD_BARS) * step <= end)


def pair_setups(h1: pd.DataFrame, symbol: str, end: pd.Timestamp) -> list[Setup]:
    first = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).min())
    out: list[Setup] = []
    for timeframe in f15.TIMEFRAMES:
        frame = f15.aggregate(h1, timeframe)
        if len(frame) < 30:
            continue
        for s in figure_setups(frame, timeframe, symbol) + smc_setups(frame, timeframe, symbol):
            if in_period(s, first_bar=first, end=end):
                out.append(s)
    return out


def minutes_hash(m: Minutes) -> str:
    digest = hashlib.sha256()
    for a in (m.ns, m.o, m.h, m.lo, m.c):
        digest.update(a.tobytes())
    return digest.hexdigest()[:16]


def pair_trades(settings: Settings, symbol: str, end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Tous les déclencheurs d'une paire joués sur ses minutes ; (lignes, empreintes des données lues)."""
    from .derivatives_screen import fingerprint
    from .long_history import load_long
    from .minute_history import load_minutes
    h1 = load_long(settings, symbol)
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end].reset_index(drop=True)
    setups = pair_setups(h1, symbol, end)
    bars = load_minutes(settings, symbol)
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
    m = Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    rows = [play(s, m, symbol, latency) for s in setups]
    hashes = {f"1h/{symbol}": fingerprint(h1[["open_time", "open", "high", "low", "close"]]), f"1m/{symbol}": minutes_hash(m)}
    return pd.DataFrame(rows), hashes


# --- Mesure --------------------------------------------------------------------------------------------------------

def _ci(values: np.ndarray, times: np.ndarray, level: float, samples: int, seed: int):
    ci, _ = day_block_ci(values, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level,
                         min_blocks=MIN_BLOCKS)
    return ci


def measure(part: pd.DataFrame, scenario: str, *, level: float = LEVEL, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """R moyen, excès sur les placebos et leurs intervalles (blocs de 28 jours), et descriptif d'un groupe."""
    done = part[part["status"] == EXECUTED].sort_values("fill_at")
    if done.empty:
        return {"n": 0}
    r = done[f"r_{scenario}"].to_numpy(float)
    times = pd.to_datetime(done["fill_at"], utc=True).to_numpy()
    excess = done[f"excess_{scenario}"].to_numpy(float)
    ok = np.isfinite(excess)
    adjusted = done[f"excess_adj_{scenario}"].to_numpy(float)
    hits = done[f"hits_{scenario}"].to_numpy(int)
    gain = done["tp1"].to_numpy(float) - done["entry"].to_numpy(float)
    risk = done["entry"].to_numpy(float) - done["stop"].to_numpy(float)
    return {"n": int(len(r)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()),
            "r_mean": round(float(r.mean()), 4), "r_ci": _ci(r, times, level, samples, seed),
            "excess_mean": round(float(excess[ok].mean()), 4) if ok.any() else None,
            "excess_ci": _ci(excess[ok], times[ok], level, samples, seed) if ok.any() else None,
            "excess_adj_mean": round(float(adjusted[ok].mean()), 4) if ok.any() else None,
            "excess_adj_ci": _ci(adjusted[ok], times[ok], level, samples, seed) if ok.any() else None,
            "maker_share": round(float(done["maker"].astype(bool).mean()), 4),
            "placebo_mean": round(float(done[f"placebo_mean_{scenario}"].astype(float).mean()), 4),
            "win_share": round(float((r > 0).mean()), 4),
            "tp_reached": {f"TP{k}": round(float((hits >= k).mean()), 4) for k in (1, 2, 3)},
            "placebo_tp1": round(float(done[f"placebo_tp1_{scenario}"].astype(float).mean()), 4),
            # Seuil simplifié, défini : part de TP1 qui équilibrerait une sortie UNIQUE au premier objectif ou au stop,
            # sans frais (risque / (risque + gain au TP1)) ; la vraie sortie se fait par tiers, avec frais.
            "tp1_break_even_simple": round(float(np.mean(risk / (risk + gain))), 4)}


def verdict(central: dict, adverse: dict) -> str:
    """Seuil de F15 (`f4.verdict`), au niveau de Bonferroni de l'étude, sans condition de durée, sur l'excès à frais
    d'entrée égaux."""
    if central.get("n", 0) < MIN_TRADES or central.get("r_ci") is None or adverse.get("r_ci") is None \
            or central.get("excess_adj_ci") is None or adverse.get("excess_adj_ci") is None:
        return INSUFFICIENT
    if central["r_ci"][0] > 0 and adverse["r_ci"][0] > 0 and central["excess_adj_ci"][0] > 0 \
            and adverse["excess_adj_ci"][0] > 0:
        return ABOVE
    if central["r_ci"][1] < 0 and adverse["r_ci"][1] < 0:
        return BELOW
    return NOT_SHOWN


def describe(part: pd.DataFrame) -> dict:
    """Descriptif hors verdict : unités de temps, années, statuts, ordres distincts, paire la plus influente."""
    done = part[part["status"] == EXECUTED]
    out: dict = {"triggers": int(len(part)), "status": part["status"].value_counts().to_dict(),
                 "cancel_reasons": part.loc[part["status"] == CANCELLED, "reason"].value_counts().to_dict()}
    usable = part[part["status"] != INVALID]
    out["distinct_orders"] = len({(r.symbol, r.timeframe, r.order_from, round(math.log(r.entry) / 0.001))
                                  for r in usable.itertuples() if isinstance(r.entry, float) and r.entry > 0})
    out["timeframes"] = {tf: measure(part[part["timeframe"] == tf], CENTRAL, level=0.95, samples=2000)
                         for tf in f15.TIMEFRAMES if (part["timeframe"] == tf).any()}
    if not done.empty:
        years = pd.to_datetime(done["fill_at"], utc=True).dt.year
        out["years"] = {int(y): {"n": int(len(g)), "r_mean": round(float(g[f"r_{CENTRAL}"].mean()), 4),
                                 "excess_adj_mean": round(float(g[f"excess_adj_{CENTRAL}"].astype(float).mean()), 4)}
                        for y, g in done.groupby(years)}
        for column in (f"r_{CENTRAL}", f"excess_adj_{CENTRAL}"):
            values = done[column].astype(float)
            out[f"concentration_{column}"] = {"pair": _largest_share(values, done["symbol"]),
                                              "year": _largest_share(values, years)}
            by_pair = values.groupby(done["symbol"]).sum()
            top = str(by_pair.idxmax())
            rest = values[done["symbol"] != top]
            out[f"without_top_pair_{column}"] = {"pair": top, "mean": round(float(rest.mean()), 4) if rest.notna().any() else None}
    return out


def _largest_share(values: pd.Series, groups: pd.Series) -> dict | None:
    """Part du total apportée par le groupe (paire ou année) qui apporte le plus ; None si le total n'est pas positif."""
    sums = values.groupby(groups).sum()
    total = float(sums.sum())
    if total <= 0:
        return None
    return {"group": str(sums.idxmax()), "share": round(float(sums.max()) / total, 4)}


def evaluate(trades: pd.DataFrame, *, level: float = LEVEL, samples: int = SAMPLES) -> dict:
    groups = {name: trades[trades["method"] == name] for name in METHODS}
    groups[ENSEMBLE] = trades[trades["method"].isin(fg.FAMILIES)]
    rows = {}
    for name, part in groups.items():
        scenarios = {s: measure(part, s, level=level, samples=samples) for s in SCENARIOS}
        rows[name] = {"scenarios": scenarios, "verdict": verdict(scenarios[CENTRAL], scenarios[ADVERSE]),
                      "descriptif": describe(part)}
    return rows


# --- Exécution unique -------------------------------------------------------------------------------------------

def _one(args: tuple) -> tuple[str, pd.DataFrame | None, dict]:
    settings, symbol, end = args
    try:
        frame, hashes = pair_trades(settings, symbol, end)
    except MissingData:
        return symbol, None, {}
    return symbol, frame, hashes


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        symbols: list[str] | None = None, workers: int = 4) -> dict:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    universe = symbols or list(RESEARCH_UNIVERSE)
    frames, hashes, missing = [], {}, []
    jobs = [(settings, symbol, end) for symbol in universe]

    def collect(results) -> None:
        for symbol, frame, h in results:
            say(symbol)
            if frame is None:
                missing.append(symbol)
                continue
            frames.append(frame)
            hashes.update(h)

    if workers <= 1:
        collect(map(_one, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:          # une paire par processus ; résultat indépendant de l'ordre
            collect(pool.map(_one, jobs))
    trades = pd.concat(frames, ignore_index=True).sort_values(["at", "key"]).reset_index(drop=True)
    rows = evaluate(trades)
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("FIGH")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6),
               "rows": rows, "missing": missing, "doc": "docs/FIGURES_HISTORIQUE.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="les figures du détecteur de F15, les éléments ICT/SMC pris seuls et les divergences RSI, "
                               "joués mécaniquement, font-ils mieux qu'un achat au hasard de même géométrie, frais compris ?",
                    strategy="FIGURES_HISTORY", strategy_version=1, variant="définitions figées (docs/FIGURES_HISTORIQUE.md)",
                    params={"methods": list(METHODS), "ensemble": ENSEMBLE, "zigzag_m": ZIGZAG_M, "stop_atr": STOP_ATR,
                            "r_multiples": R_MULTIPLES, "order_bars": f15.ORDER_BARS, "hold_bars": f15.HOLD_BARS,
                            "placebos": f15.PLACEBOS, "first_day": str(FIRST_DAY.date()), "warmup_days": WARMUP.days,
                            "block_days": BLOCK_DAYS, "samples": SAMPLES, "seed": SEED, "min_trades": MIN_TRADES},
                    period_label="DEVELOPMENT", period_start=str(FIRST_DAY.date()), period_end=end.isoformat(),
                    universe=sorted(set(trades["symbol"])), data_hashes=hashes, git_commit=state,
                    dependencies=dependency_versions(), seed=SEED, cost_scenario="central et défavorable (forward/costs.py)",
                    simulation_rules={"execution": "f15.simulate (copie compilée), minute ambiguë : stop d'abord",
                                      "placebos": "20 achats au marché, 1 à 30 jours avant, même géométrie"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program,
                             "verdicts": {k: v["verdict"] for k, v in rows.items()}}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    return payload
