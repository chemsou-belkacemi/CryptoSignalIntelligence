"""Gestion du propriétaire sur le double creux (docs/FIGURES_HISTORIQUE.md, section « Gestion du propriétaire »,
déclaré le 2026-10-04 après la première exécution : piste contaminée) : les mêmes 2 455 transactions `DOUBLE` de
`FIGH-20261004T102820Z-479ac6`, mêmes exécutions et mêmes placebos, rejouées avec 5 objectifs à k × hauteur/3, 60 % vendus
au TP1, stop remonté à l'entrée après TP1 puis au TP précédent. 2 essais (deux répartitions)."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd
from numba import njit

from ..config import Settings
from ..forward import f15
from ..forward.costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from . import figures_history as fh
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end

KIND = "DOUBLE_MANAGEMENT"
SOURCE_RUN = "FIGH-20261004T102820Z-479ac6"
METHOD = "DOUBLE"
SPLITS: dict[str, tuple[float, ...]] = {"A_60_10_10_10_10": (0.60, 0.10, 0.10, 0.10, 0.10),
                                       "B_60_15_10_10_5": (0.60, 0.15, 0.10, 0.10, 0.05)}
N_TARGETS = 5
N_TRIALS = len(SPLITS)
LEVEL = 1 - 0.05 / N_TRIALS
BLOCK_DAYS, SAMPLES, SEED, MIN_BLOCKS = fh.BLOCK_DAYS, fh.SAMPLES, fh.SEED, fh.MIN_BLOCKS
MINUTE_NS = fh.MINUTE_NS


@njit(cache=True)
def _ladder(o, h, lo, c, ns, fill_i, fill_price, entry, stop0, targets, weights, hold_ns, cost_market, fee, move_stop):
    """Après l'exécution (`fill_i`, `fill_price`) : parts vendues aux objectifs (maker), stop remonté à l'entrée après
    TP1 puis à TP(k−1) après TPk, appliqué dès la minute suivante (`move_stop`) ; sinon stop fixe (même calcul que
    `figures_history._simulate` après l'exécution). Renvoie (objectifs atteints, R, indice de sortie, issue)."""
    entry_cost = fill_price * (1 + fee)
    risk = entry - stop0
    remaining, proceeds, hits, stop = 1.0, 0.0, 0, stop0
    count = len(targets)
    horizon = ns[fill_i] + hold_ns
    last = np.searchsorted(ns, horizon)
    exit_i, outcome = -1, -1
    for i in range(fill_i, last):
        if lo[i] <= stop:
            price = min(stop, o[i]) if i > fill_i else stop
            proceeds += remaining * price * (1 - cost_market) * (1 - fee)
            remaining = 0.0
            exit_i, outcome = i, fh.OUT_STOP
            break
        while i > fill_i and hits < count and h[i] > targets[hits]:
            price = max(targets[hits], o[i])
            share = weights[hits] if hits < count - 1 else remaining
            proceeds += share * price * (1 - fee)
            remaining -= share
            hits += 1
            if move_stop:
                stop = entry if hits == 1 else targets[hits - 2]
            if remaining <= 1e-12:
                exit_i, outcome = i, fh.OUT_TP
                break
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        if ns[len(ns) - 1] + MINUTE_NS >= horizon:
            k, outcome = last - 1, fh.OUT_TIME
        else:
            k, outcome = len(ns) - 1, fh.OUT_DELISTED
        proceeds += remaining * c[k] * (1 - cost_market) * (1 - fee)
        exit_i = k
    return hits, (proceeds - entry_cost) / risk, exit_i, outcome


def targets_of(entry: float, tp1: float) -> tuple[float, ...]:
    """TPk = entrée + k × P/3, avec P = 3 × (TP1 − entrée) : TP1 à TP3 sont ceux de la première exécution."""
    third = tp1 - entry
    return tuple(entry + k * third for k in range(1, N_TARGETS + 1))


def replay(row, m: fh.Minutes, latency: pd.Timedelta) -> dict:
    """Une transaction `DOUBLE` de la première exécution rejouée avec chaque répartition, et ses placebos."""
    symbol, timeframe = row.symbol, row.timeframe
    setup = fh.Setup(row.key, METHOD, timeframe, pd.Timestamp(row.at), float(row.stop), float(row.entry),
                     (float(row.tp1), float(row.entry) + 2 * (float(row.tp1) - float(row.entry)),
                      float(row.entry) + 3 * (float(row.tp1) - float(row.entry))))
    start, until, hold = fh.order_window(setup, latency)
    entry, stop0 = float(row.entry), float(row.stop)
    targets = np.asarray(targets_of(entry, float(row.tp1)), float)
    out = {"key": row.key, "symbol": symbol, "timeframe": timeframe, "at": row.at, "fill_at": row.fill_at,
           "entry": entry, "stop": stop0, "risk_pct": float(row.risk_pct), "maker": bool(row.maker),
           "r_thirds_central": float(row.r_central), "r_thirds_defavorable": float(row.r_defavorable)}
    offsets = np.asarray(f15.placebo_offsets(row.key), np.int64)
    hold_ns = hold * MINUTE_NS
    for scenario in SCENARIOS:
        costs = costs_for(symbol, scenario)
        status, fill_i, fill_price, _, _, _, _ = fh._simulate(
            m.o, m.h, m.lo, m.c, m.ns, entry, stop0, targets[:3], start.as_unit("ns").value,
            until.as_unit("ns").value if until is not None else -1, hold_ns, False, costs.market, costs.fee, fh.WEIGHTS, True)
        if status != fh.ST_EXECUTED or pd.Timestamp(int(m.ns[fill_i]), tz="UTC") != pd.Timestamp(row.fill_at):
            raise ValueError(f"{row.key} : exécution différente de la première exécution ({status})")
        fill_ns = int(m.ns[fill_i])
        for name, split in SPLITS.items():
            weights = np.asarray(split, float)
            hits, r, exit_i, outcome = _ladder(m.o, m.h, m.lo, m.c, m.ns, fill_i, fill_price, entry, stop0, targets,
                                               weights, hold_ns, costs.market, costs.fee, True)
            r = round(float(r), 6)
            placebo = []
            for off in offsets:
                when = fill_ns - int(off) * MINUTE_NS
                k = int(np.searchsorted(m.ns, when))
                if k >= len(m.ns) or m.ns[k] - when > 10 * MINUTE_NS:
                    continue
                q0 = float(m.o[k])
                p_fill = q0 * (1 + costs.market)
                p_stop = q0 * stop0 / entry
                if p_fill <= p_stop:
                    continue
                p_targets = np.array([q0 * t / entry for t in targets])
                _, pr, _, _ = _ladder(m.o, m.h, m.lo, m.c, m.ns, k, p_fill, q0, p_stop, p_targets, weights, hold_ns,
                                      costs.market, costs.fee, True)
                placebo.append(round(float(pr), 6))
            handicap = costs.market * (1 + costs.fee) / out["risk_pct"] if out["maker"] else 0.0
            prefix = f"{name}_{scenario}"
            out[f"r_{prefix}"] = r
            out[f"hits_{prefix}"] = int(hits)
            out[f"outcome_{prefix}"] = fh._label(outcome, hits)
            out[f"placebo_n_{prefix}"] = len(placebo)
            out[f"placebo_mean_{prefix}"] = round(float(np.mean(placebo)), 6) if placebo else None
            out[f"excess_adj_{prefix}"] = round(r - float(np.mean(placebo)) - handicap, 6) if placebo else None
    return out


def pair_rows(settings: Settings, symbol: str, part: pd.DataFrame, end: pd.Timestamp) -> list[dict]:
    from .minute_history import load_minutes
    bars = load_minutes(settings, symbol)
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    return [replay(row, m, latency) for row in part.itertuples(index=False)]


def measure(done: pd.DataFrame, name: str, scenario: str, *, level: float = LEVEL, samples: int = SAMPLES) -> dict:
    prefix = f"{name}_{scenario}"
    done = done.sort_values("fill_at")
    r = done[f"r_{prefix}"].to_numpy(float)
    times = pd.to_datetime(done["fill_at"], utc=True).to_numpy()
    excess = done[f"excess_adj_{prefix}"].to_numpy(float)
    ok = np.isfinite(excess)
    diff = r - done[f"r_thirds_{scenario}"].to_numpy(float)
    hits = done[f"hits_{prefix}"].to_numpy(int)
    pct = r * done["risk_pct"].to_numpy(float)
    ci = lambda v, t: fh._ci(v, t, level, samples, SEED)  # noqa: E731
    return {"n": int(len(r)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()),
            "r_mean": round(float(r.mean()), 4), "r_ci": ci(r, times),
            "excess_adj_mean": round(float(excess[ok].mean()), 4) if ok.any() else None,
            "excess_adj_ci": ci(excess[ok], times[ok]) if ok.any() else None,
            "vs_thirds_mean": round(float(diff.mean()), 4), "vs_thirds_ci": ci(diff, times),
            "r_thirds_mean": round(float(done[f"r_thirds_{scenario}"].mean()), 4),
            "win_share": round(float((r > 0).mean()), 4),
            "tp_reached": {f"TP{k}": round(float((hits >= k).mean()), 4) for k in range(1, N_TARGETS + 1)},
            "outcomes": done[f"outcome_{prefix}"].value_counts(normalize=True).round(4).to_dict(),
            "pct_per_trade_mean": round(float(pct.mean()) * 100, 4),
            "by_timeframe": {tf: round(float(g[f"r_{prefix}"].mean()), 4) for tf, g in done.groupby("timeframe")},
            "by_year": {int(y): round(float(g[f"r_{prefix}"].mean()), 4)
                        for y, g in done.groupby(pd.to_datetime(done["fill_at"], utc=True).dt.year)}}


def evaluate(trades: pd.DataFrame, *, level: float = LEVEL, samples: int = SAMPLES) -> dict:
    rows = {}
    for name in SPLITS:
        scenarios = {s: measure(trades, name, s, level=level, samples=samples) for s in SCENARIOS}
        rows[name] = {"scenarios": scenarios, "verdict": fh.verdict(scenarios[CENTRAL], scenarios[ADVERSE])}
    return rows


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        source_run: str = SOURCE_RUN, symbols: list[str] | None = None) -> dict:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    source = settings.reports_dir / source_run / "trades.parquet"
    first = pd.read_parquet(source)
    first = first[(first["method"] == METHOD) & (first["status"] == fh.EXECUTED)]
    if symbols:
        first = first[first["symbol"].isin(symbols)]
    rows: list[dict] = []
    for symbol, part in first.groupby("symbol"):
        say(str(symbol))
        rows.extend(pair_rows(settings, str(symbol), part, end))
    trades = pd.DataFrame(rows).sort_values(["at", "key"]).reset_index(drop=True)
    result = evaluate(trades)
    days = (end - fh.FIRST_DAY).days
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("DBLM")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6),
               "source_run": source_run, "rows": result, "trades": int(len(trades)),
               "trades_per_day": round(len(trades) / days, 3), "doc": "docs/FIGURES_HISTORIQUE.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="la gestion du propriétaire (60 % au TP1, 5 objectifs, stop à l'entrée puis au TP précédent) "
                               "rend-elle le double creux supérieur au hasard, frais compris ?",
                    strategy="DOUBLE_MANAGEMENT", strategy_version=1, variant="définitions figées (docs/FIGURES_HISTORIQUE.md)",
                    params={"splits": SPLITS, "n_targets": N_TARGETS, "source_run": source_run, "block_days": BLOCK_DAYS,
                            "samples": SAMPLES, "seed": SEED},
                    period_label="DEVELOPMENT", period_start=str(fh.FIRST_DAY.date()), period_end=end.isoformat(),
                    universe=sorted(set(trades["symbol"])), data_hashes={"source": f"{source_run}/trades.parquet"},
                    git_commit=state, dependencies=dependency_versions(), seed=SEED,
                    cost_scenario="central et défavorable (forward/costs.py)",
                    simulation_rules={"stop": "entrée après TP1, TP(k−1) après TPk, dès la minute suivante",
                                      "placebos": "ceux de la première exécution, même gestion"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program,
                             "verdicts": {k: v["verdict"] for k, v in result.items()}}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    return payload
