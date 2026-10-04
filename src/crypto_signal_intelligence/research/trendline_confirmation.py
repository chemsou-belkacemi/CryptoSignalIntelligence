"""Cassures de ligne de tendance en 1 h, confirmation sur 214 paires jamais utilisées (docs/LIGNES_DE_TENDANCE.md,
déclaré le 2026-10-04 avant code et exécution) : `TRENDLINE` 1 h du détecteur de F15, transactions de
`figures_history.play`, placebos tirés uniformément sur toute la période utilisable de la paire. 2 essais."""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from ..forward import f15
from ..forward.costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from . import figures_history as fh
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .universe import RESEARCH_UNIVERSE

KIND = "TRENDLINE_CONFIRMATION"
TEST_ID = "TRENDLINE_CONFIRMATION"
METHOD, TIMEFRAME = "TRENDLINE", "1h"
N_TRIALS = 2
LEVEL = 1 - 0.05 / N_TRIALS
PLACEBOS = 20
GAP_MINUTES = 10
CONFIRMED, INVERSE, NOT_CONFIRMED = "PISTE_CONFIRMEE", "INVERSE", "NON_CONFIRMEE"
GAIN, LOSS, NO_GAIN = "GAIN_DEMONTRE", "PERTE_DEMONTREE", "GAIN_NON_DEMONTRE"
INSUFFICIENT = "INSUFFISANT"


def universe(settings: Settings) -> list[str]:
    """Paires passées au moins une fois par le top 40 à date (recensement filtré de UNIVERSE_PIT.md), hors des 40."""
    from .pit_universe import load_membership
    members = load_membership(settings)
    return sorted(set(members["symbol"]) - set(RESEARCH_UNIVERSE))


def placebo_minutes(key: str, lo_ns: int, hi_ns: int, count: int = PLACEBOS) -> np.ndarray:
    """Minutes tirées uniformément sans remise dans [lo, hi] (graine déduite de l'identifiant)."""
    span = int((hi_ns - lo_ns) // fh.MINUTE_NS)
    if span < count:
        return np.empty(0, np.int64)
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{key}".encode()).hexdigest()[:16], 16))
    return np.asarray(sorted(lo_ns + k * fh.MINUTE_NS for k in rng.sample(range(span + 1), count)), np.int64)


def with_uniform_placebos(row: dict, m: fh.Minutes, *, lo_ns: int, hi_ns: int) -> dict:
    """Ajoute à une transaction exécutée de `figures_history.play` ses 20 placebos tirés sur toute la période
    utilisable de la paire (même géométrie, même sortie, mêmes frais, taker à l'entrée)."""
    if row["status"] != fh.EXECUTED:
        return row
    fill_ns = pd.Timestamp(row["fill_at"]).as_unit("ns").value
    whens = placebo_minutes(row["key"], lo_ns, hi_ns)
    offsets = (fill_ns - whens) // fh.MINUTE_NS                       # f15 : moment = exécution − décalage (négatif : après)
    entry, stop = float(row["entry"]), float(row["stop"])
    third = float(row["tp1"]) - entry
    targets = np.array([entry + k * third for k in (1, 2, 3)])
    hold_ns = f15.HOLD_BARS * f15.TIMEFRAMES[TIMEFRAME].value
    for s in SCENARIOS:
        costs = costs_for(row["symbol"], s)
        raw, hits = fh._placebos(m.o, m.h, m.lo, m.c, m.ns, fill_ns, offsets.astype(np.int64), entry, stop, targets,
                                 hold_ns, costs.market, costs.fee, fh.WEIGHTS, True)
        r = np.array([round(float(x), 6) if np.isfinite(x) else np.nan for x in raw])
        usable = np.isfinite(r)
        handicap = costs.market * (1 + costs.fee) / row["risk_pct"] if row["maker"] else 0.0
        row[f"uplacebo_n_{s}"] = int(usable.sum())
        row[f"uplacebo_mean_{s}"] = round(float(r[usable].mean()), 6) if usable.any() else None
        row[f"uplacebo_tp1_{s}"] = float((hits[usable] >= 1).mean()) if usable.any() else None
        row[f"uplacebo_tp2_{s}"] = float((hits[usable] >= 2).mean()) if usable.any() else None
        row[f"uplacebo_tp3_{s}"] = float((hits[usable] >= 3).mean()) if usable.any() else None
        row[f"uexcess_adj_{s}"] = round(row[f"r_{s}"] - float(r[usable].mean()) - handicap, 6) if usable.any() else None
    return row


def pair_rows(h1: pd.DataFrame, m: fh.Minutes, symbol: str, *, end: pd.Timestamp, latency: pd.Timedelta,
              top_months: set[str]) -> list[dict]:
    """Déclencheurs `TRENDLINE` 1 h d'une paire, joués, avec les deux jeux de placebos."""
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end].reset_index(drop=True)
    if h1.empty or len(m.ns) == 0:
        return []
    first = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).min())
    frame = f15.aggregate(h1, TIMEFRAME)
    setups = [s for s in fh.figure_setups(frame, TIMEFRAME, symbol) if s.method == METHOD
              and fh.in_period(s, first_bar=first, end=end)]
    lo_ns = max(fh.FIRST_DAY, first + fh.WARMUP).as_unit("ns").value
    hold_ns = f15.HOLD_BARS * f15.TIMEFRAMES[TIMEFRAME].value
    hi_ns = min(end.as_unit("ns").value, int(m.ns[-1])) - hold_ns
    last_hour = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).max())
    delisted = bool(last_hour < end - pd.Timedelta(days=2))
    rows = []
    for setup in setups:
        row = with_uniform_placebos(fh.play(setup, m, symbol, latency), m, lo_ns=lo_ns, hi_ns=hi_ns)
        row["top40"] = setup.at.strftime("%Y-%m") in top_months
        row["delisted_pair"] = delisted
        rows.append(row)
    return rows


def _one(args: tuple) -> tuple[str, list[dict] | None, dict]:
    from .derivatives_screen import fingerprint
    from .long_history import load_long
    from .minute_history import load_minutes
    settings, symbol, end, top_months = args
    try:
        h1 = load_long(settings, symbol)
        bars = load_minutes(settings, symbol)
    except MissingData:
        return symbol, None, {}
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end].reset_index(drop=True)
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    rows = pair_rows(h1, m, symbol, end=end, latency=latency, top_months=top_months)
    hashes = {f"1h/{symbol}": fingerprint(h1[["open_time", "open", "high", "low", "close"]]), f"1m/{symbol}": fh.minutes_hash(m)}
    return symbol, rows, hashes


# --- Mesure ---------------------------------------------------------------------------------------------------------

def _stats(done: pd.DataFrame, column: str, level: float, samples: int) -> tuple[float | None, tuple | None]:
    values = done[column].astype(float).to_numpy()
    ok = np.isfinite(values)
    if not ok.any():
        return None, None
    times = pd.to_datetime(done["fill_at"], utc=True).to_numpy()[ok]
    return round(float(values[ok].mean()), 4), fh._ci(values[ok], times, level, samples, fh.SEED)


def measure(part: pd.DataFrame, scenario: str, *, level: float = LEVEL, samples: int = fh.SAMPLES) -> dict:
    done = part[part["status"] == fh.EXECUTED].sort_values("fill_at")
    if done.empty:
        return {"n": 0}
    r_mean, r_ci = _stats(done, f"r_{scenario}", level, samples)
    u_mean, u_ci = _stats(done, f"uexcess_adj_{scenario}", level, samples)
    b_mean, b_ci = _stats(done, f"excess_adj_{scenario}", level, samples)
    hits = done[f"hits_{scenario}"].to_numpy(int)
    return {"n": int(len(done)), "days": int(pd.to_datetime(done["fill_at"], utc=True).dt.floor("D").nunique()),
            "pairs": int(done["symbol"].nunique()), "r_mean": r_mean, "r_ci": r_ci,
            "uexcess_adj_mean": u_mean, "uexcess_adj_ci": u_ci, "backward_excess_adj_mean": b_mean,
            "backward_excess_adj_ci": b_ci,
            "uplacebo_mean": round(float(done[f"uplacebo_mean_{scenario}"].astype(float).mean()), 4),
            "win_share": round(float((done[f"r_{scenario}"] > 0).mean()), 4),
            "tp_reached": {f"TP{k}": round(float((hits >= k).mean()), 4) for k in (1, 2, 3)},
            "uplacebo_tp": {f"TP{k}": round(float(done[f"uplacebo_tp{k}_{scenario}"].astype(float).mean()), 4)
                            for k in (1, 2, 3)},
            "maker_share": round(float(done["maker"].astype(bool).mean()), 4),
            "outcomes": done[f"outcome_{scenario}"].value_counts(normalize=True).round(4).to_dict()}


def decide(central: dict, adverse: dict) -> dict:
    rows = (central, adverse)
    if central.get("n", 0) < fh.MIN_TRADES or any(r.get("r_ci") is None or r.get("uexcess_adj_ci") is None for r in rows):
        return {"piste": INSUFFICIENT, "gain": INSUFFICIENT}
    piste = (CONFIRMED if all(r["uexcess_adj_ci"][0] > 0 for r in rows)
             else INVERSE if all(r["uexcess_adj_ci"][1] < 0 for r in rows) else NOT_CONFIRMED)
    gain = (GAIN if all(r["r_ci"][0] > 0 for r in rows)
            else LOSS if all(r["r_ci"][1] < 0 for r in rows) else NO_GAIN)
    return {"piste": piste, "gain": gain, "scenarios_checked": [CENTRAL, ADVERSE]}


def describe(trades: pd.DataFrame) -> dict:
    done = trades[trades["status"] == fh.EXECUTED]
    out: dict = {"triggers": int(len(trades)), "status": trades["status"].value_counts().to_dict(),
                 "top40": measure(trades[trades["top40"]], CENTRAL, level=0.95, samples=2000),
                 "listed_pairs": measure(trades[~trades["delisted_pair"]], CENTRAL, level=0.95, samples=2000),
                 "delisted_pairs": measure(trades[trades["delisted_pair"]], CENTRAL, level=0.95, samples=2000)}
    if not done.empty:
        years = pd.to_datetime(done["fill_at"], utc=True).dt.year
        out["years"] = {int(y): {"n": int(len(g)), "r_mean": round(float(g[f"r_{CENTRAL}"].mean()), 4),
                                 "uexcess_adj_mean": round(float(g[f"uexcess_adj_{CENTRAL}"].astype(float).mean()), 4)}
                        for y, g in done.groupby(years)}
        for column in (f"r_{CENTRAL}", f"uexcess_adj_{CENTRAL}"):
            values = done[column].astype(float)
            sums = values.groupby(done["symbol"]).sum()
            top = str(sums.idxmax())
            total = float(sums.sum())
            out[f"top_pair_{column}"] = {"pair": top, "share": round(float(sums.max()) / total, 4) if total > 0 else None,
                                         "mean_without": round(float(values[done["symbol"] != top].mean()), 4)}
    return out


def evaluate(trades: pd.DataFrame, *, level: float = LEVEL, samples: int = fh.SAMPLES) -> dict:
    scenarios = {s: measure(trades, s, level=level, samples=samples) for s in SCENARIOS}
    return {"scenarios": scenarios, "decision": decide(scenarios[CENTRAL], scenarios[ADVERSE]), "descriptif": describe(trades)}


# --- Exécution unique -------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        symbols: list[str] | None = None, workers: int = 4) -> dict:
    from .pit_universe import load_membership
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    members = load_membership(settings)
    months = members.assign(m=pd.to_datetime(members["month"], utc=True).dt.strftime("%Y-%m")).groupby("symbol")["m"]
    top = {str(k): set(v) for k, v in months}
    pairs = symbols or universe(settings)
    jobs = [(settings, symbol, end, top.get(symbol, set())) for symbol in pairs]
    rows: list[dict] = []
    hashes: dict[str, str] = {}
    missing: list[str] = []

    def collect(results) -> None:
        for symbol, found, h in results:
            say(symbol)
            if found is None:
                missing.append(symbol)
                continue
            rows.extend(found)
            hashes.update(h)

    if workers <= 1:
        collect(map(_one, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:
            collect(pool.map(_one, jobs))
    trades = pd.DataFrame(rows).sort_values(["at", "key"]).reset_index(drop=True)
    result = evaluate(trades)
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("TRND")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6),
               "result": result, "missing": missing, "pairs": len(pairs), "doc": "docs/LIGNES_DE_TENDANCE.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="les cassures de ligne de tendance en 1 h battent-elles le hasard et gagnent-elles, frais "
                               "compris, sur 214 paires jamais utilisées ?",
                    strategy="TRENDLINE_CONFIRMATION", strategy_version=1, variant="définitions figées (docs/LIGNES_DE_TENDANCE.md)",
                    params={"method": METHOD, "timeframe": TIMEFRAME, "placebos": PLACEBOS, "first_day": str(fh.FIRST_DAY.date()),
                            "warmup_days": fh.WARMUP.days, "block_days": fh.BLOCK_DAYS, "samples": fh.SAMPLES, "seed": fh.SEED},
                    period_label="DEVELOPMENT", period_start=str(fh.FIRST_DAY.date()), period_end=end.isoformat(),
                    universe=sorted(set(trades["symbol"])) if not trades.empty else [], data_hashes=hashes, git_commit=state,
                    dependencies=dependency_versions(), seed=fh.SEED, cost_scenario="central et défavorable (forward/costs.py)",
                    simulation_rules={"execution": "figures_history.play", "placebos": "20 minutes uniformes sur la période de la paire"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "decision": result["decision"]},
                    status="COMPLETED", report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    return payload
