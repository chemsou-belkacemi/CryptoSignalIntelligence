"""Stop resserré sur le double creux (docs/FIGURES_HISTORIQUE.md, section « Stop resserré », déclaré le 2026-10-04
après les deux exécutions précédentes : piste contaminée) : les mêmes 2 455 transactions `DOUBLE` de
`FIGH-20261004T102820Z-479ac6`, mêmes exécutions et placebos, gestion des objectifs de la répartition A ; stop à
mi-distance au toucher, stop à la clôture d'une bougie de l'unité de temps (0,4 de la distance, stop d'origine gardé)
ou −3 % fixe. R par unité de risque prévu. 3 essais."""
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
from . import double_management as dm
from . import figures_history as fh
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end

KIND = "DOUBLE_STOPS"
REFERENCE = "REFERENCE"
VARIANTS = ("MOITIE_TOUCHE", "CLOTURE_0_4", "FIXE_3_PCT")
N_TRIALS = len(VARIANTS)
LEVEL = 1 - 0.05 / N_TRIALS
HALF, CLOSE_SHARE, FIXED = 0.5, 0.4, 0.03
WEIGHTS = np.asarray(dm.SPLITS["A_60_10_10_10_10"], float)
OUT_CLOSE = 4
MINUTE_NS = fh.MINUTE_NS
NO_LEVEL = -1.0


@njit(cache=True)
def _managed(o, h, lo, c, ns, fill_i, fill_price, entry, touch_stop, close_level, step_ns, targets, weights, hold_ns,
             cost_market, fee):
    """Après l'exécution : objectifs (maker) avec stop remonté à l'entrée après TP1 puis au TP précédent (dès la
    minute suivante) ; avant TP1, stop `touch_stop` au toucher et, si `close_level` > 0, sortie à l'ouverture de la
    minute suivante quand une bougie de `step_ns` (alignée sur l'époque, donc sur 00:00 UTC) clôture dessous.
    Renvoie (objectifs atteints, résultat net en part de l'entrée prévue, indice de sortie, issue)."""
    n = len(ns)
    entry_cost = fill_price * (1 + fee)
    remaining, proceeds, hits, stop = 1.0, 0.0, 0, touch_stop
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
            stop = entry if hits == 1 else targets[hits - 2]
            if remaining <= 1e-12:
                exit_i, outcome = i, fh.OUT_TP
                break
        if remaining <= 1e-12:
            break
        if hits == 0 and close_level > 0 and i + 1 < last and ns[i + 1] // step_ns != ns[i] // step_ns \
                and c[i] < close_level:
            proceeds += remaining * o[i + 1] * (1 - cost_market) * (1 - fee)
            remaining = 0.0
            exit_i, outcome = i + 1, OUT_CLOSE
            break
    if remaining > 1e-12:
        if ns[n - 1] + MINUTE_NS >= horizon:
            k, outcome = last - 1, fh.OUT_TIME
        else:
            k, outcome = n - 1, fh.OUT_DELISTED
        proceeds += remaining * c[k] * (1 - cost_market) * (1 - fee)
        exit_i = k
    return hits, (proceeds - entry_cost) / entry, exit_i, outcome


def levels(variant: str, entry: float, stop0: float) -> tuple[float, float, float]:
    """(stop au toucher, niveau de clôture ou NO_LEVEL, risque prévu) d'une variante."""
    distance = entry - stop0
    if variant == REFERENCE:
        return stop0, NO_LEVEL, distance
    if variant == "MOITIE_TOUCHE":
        return entry - HALF * distance, NO_LEVEL, HALF * distance
    if variant == "CLOTURE_0_4":
        return stop0, entry - CLOSE_SHARE * distance, CLOSE_SHARE * distance
    if variant == "FIXE_3_PCT":
        stop = max(stop0, (1 - FIXED) * entry)
        return stop, NO_LEVEL, entry - stop
    raise ValueError(variant)


def label(outcome: int, hits: int) -> str:
    return "STOP_CLOTURE" if outcome == OUT_CLOSE else fh._label(outcome, hits)


def replay(row, m: fh.Minutes, latency: pd.Timedelta) -> dict:
    symbol, timeframe = row.symbol, row.timeframe
    entry, stop0, tp1 = float(row.entry), float(row.stop), float(row.tp1)
    setup = fh.Setup(row.key, dm.METHOD, timeframe, pd.Timestamp(row.at), stop0, entry,
                     (tp1, entry + 2 * (tp1 - entry), entry + 3 * (tp1 - entry)))
    start, until, hold = fh.order_window(setup, latency)
    targets = np.asarray(dm.targets_of(entry, tp1), float)
    step_ns = int(f15.TIMEFRAMES[timeframe].value)
    hold_ns = hold * MINUTE_NS
    offsets = np.asarray(f15.placebo_offsets(row.key), np.int64)
    out = {"key": row.key, "symbol": symbol, "timeframe": timeframe, "at": row.at, "fill_at": row.fill_at, "entry": entry,
           "stop": stop0, "risk_pct": float(row.risk_pct), "maker": bool(row.maker)}
    for scenario in SCENARIOS:
        costs = costs_for(symbol, scenario)
        status, fill_i, fill_price, _, _, _, _ = fh._simulate(
            m.o, m.h, m.lo, m.c, m.ns, entry, stop0, targets[:3], start.as_unit("ns").value,
            until.as_unit("ns").value if until is not None else -1, hold_ns, False, costs.market, costs.fee, fh.WEIGHTS, True)
        if status != fh.ST_EXECUTED or pd.Timestamp(int(m.ns[fill_i]), tz="UTC") != pd.Timestamp(row.fill_at):
            raise ValueError(f"{row.key} : exécution différente de la première exécution ({status})")
        if scenario == CENTRAL and abs(float(fill_price) - float(row.fill_price)) > 1e-9 * float(row.fill_price):
            raise ValueError(f"{row.key} : prix d'exécution différent de la première exécution")
        fill_ns = int(m.ns[fill_i])
        for variant in (REFERENCE, *VARIANTS):
            touch, close_level, risk = levels(variant, entry, stop0)
            risk_pct = risk / entry
            if fill_price <= touch:                         # sortie « au stop » au-dessus du prix d'achat : gain fictif
                raise ValueError(f"{row.key} : exécution sous le stop resserré ({variant})")
            hits, net, exit_i, outcome = _managed(m.o, m.h, m.lo, m.c, m.ns, fill_i, fill_price, entry, touch, close_level,
                                                  step_ns, targets, WEIGHTS, hold_ns, costs.market, costs.fee)
            placebo = []
            for off in offsets:
                when = fill_ns - int(off) * MINUTE_NS
                k = int(np.searchsorted(m.ns, when))
                if k >= len(m.ns) or m.ns[k] - when > 10 * MINUTE_NS:
                    continue
                q0 = float(m.o[k])
                p_fill = q0 * (1 + costs.market)
                p_touch = q0 * touch / entry
                if p_fill <= p_touch:
                    continue
                p_close = q0 * close_level / entry if close_level > 0 else NO_LEVEL
                p_targets = np.array([q0 * t / entry for t in targets])
                _, p_net, _, _ = _managed(m.o, m.h, m.lo, m.c, m.ns, k, p_fill, q0, p_touch, p_close, step_ns, p_targets,
                                          WEIGHTS, hold_ns, costs.market, costs.fee)
                placebo.append(float(p_net) / risk_pct)
            r = round(float(net) / risk_pct, 6)
            handicap = costs.market * (1 + costs.fee) / risk_pct if out["maker"] else 0.0
            prefix = f"{variant}_{scenario}"
            out[f"r_{prefix}"] = r
            out[f"pct_{prefix}"] = round(float(net) * 100, 6)
            out[f"risk_pct_{variant}"] = risk_pct
            out[f"hits_{prefix}"] = int(hits)
            out[f"outcome_{prefix}"] = label(outcome, hits)
            out[f"placebo_n_{prefix}"] = len(placebo)
            out[f"excess_adj_{prefix}"] = round(r - float(np.mean(placebo)) - handicap, 6) if placebo else None
    return out


def pair_rows(settings: Settings, symbol: str, part: pd.DataFrame, end: pd.Timestamp,
              expected_hash: str | None = None) -> list[dict]:
    from .minute_history import load_minutes
    bars = load_minutes(settings, symbol)
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    if expected_hash is not None and fh.minutes_hash(m) != expected_hash:
        raise ValueError(f"{symbol} : bougies 1 minute différentes de celles de la première exécution")
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    return [replay(row, m, latency) for row in part.itertuples(index=False)]


def measure(done: pd.DataFrame, variant: str, scenario: str, *, level: float = LEVEL, samples: int = fh.SAMPLES) -> dict:
    prefix = f"{variant}_{scenario}"
    done = done.sort_values("fill_at")
    times = pd.to_datetime(done["fill_at"], utc=True).to_numpy()
    r = done[f"r_{prefix}"].to_numpy(float)
    excess = done[f"excess_adj_{prefix}"].to_numpy(float)
    ok = np.isfinite(excess)
    diff = r - done[f"r_{REFERENCE}_{scenario}"].to_numpy(float)
    pct = done[f"pct_{prefix}"].to_numpy(float)
    diff_pct = pct - done[f"pct_{REFERENCE}_{scenario}"].to_numpy(float)
    outcome = done[f"outcome_{prefix}"]
    stopped = outcome.isin(["STOP", "STOP_CLOTURE"]).to_numpy()
    hits = done[f"hits_{prefix}"].to_numpy(int)

    def ci(values, when):
        return fh._ci(values, when, level, samples, fh.SEED)

    return {"n": int(len(r)), "r_mean": round(float(r.mean()), 4), "r_ci": ci(r, times),
            "excess_adj_mean": round(float(excess[ok].mean()), 4) if ok.any() else None,
            "excess_adj_ci": ci(excess[ok], times[ok]) if ok.any() else None,
            # Écart en R : à risque PRÉVU égal (taille de position différente) ; écart en % : à position égale.
            "vs_reference_mean": round(float(diff.mean()), 4), "vs_reference_ci": ci(diff, times),
            "vs_reference_pct_mean": round(float(diff_pct.mean()), 4), "vs_reference_pct_ci": ci(diff_pct, times),
            "share_r_below_minus_1": round(float((r < -1).mean()), 4), "r_p01": round(float(np.percentile(r, 1)), 4),
            "r_min": round(float(r.min()), 4), "concentration": _top_pair(done, f"r_{prefix}"),
            "pct_position_mean": round(float(pct.mean()), 4), "pct_capital_at_1pct_risk": round(float(r.mean()), 4),
            "risk_pct_median": round(float(done[f"risk_pct_{variant}"].median()) * 100, 3),
            "stopped_before_tp1": round(float(stopped.mean()), 4),
            "stopped_loss_pct_mean": round(float(pct[stopped].mean()), 4) if stopped.any() else None,
            "win_share": round(float((r > 0).mean()), 4),
            "tp_reached": {f"TP{k}": round(float((hits >= k).mean()), 4) for k in range(1, dm.N_TARGETS + 1)},
            "outcomes": outcome.value_counts(normalize=True).round(4).to_dict(),
            "by_timeframe": {tf: {"n": int(len(g)), "r_mean": round(float(g[f"r_{prefix}"].mean()), 4),
                                  "pct_mean": round(float(g[f"pct_{prefix}"].mean()), 4),
                                  "stopped": round(float(g[f"outcome_{prefix}"].isin(["STOP", "STOP_CLOTURE"]).mean()), 4)}
                             for tf, g in done.groupby("timeframe")},
            "by_year": {int(y): round(float(g[f"r_{prefix}"].mean()), 4)
                        for y, g in done.groupby(pd.to_datetime(done["fill_at"], utc=True).dt.year)}}


def _top_pair(done: pd.DataFrame, column: str) -> dict:
    """Paire qui apporte le plus au total des R (part du total s'il est positif) et R moyen sans elle."""
    sums = done.groupby("symbol")[column].sum()
    top = str(sums.idxmax())
    total = float(sums.sum())
    rest = done.loc[done["symbol"] != top, column]
    return {"pair": top, "share": round(float(sums.max()) / total, 4) if total > 0 else None,
            "r_mean_without": round(float(rest.mean()), 4) if len(rest) else None}


def evaluate(trades: pd.DataFrame, *, level: float = LEVEL, samples: int = fh.SAMPLES) -> dict:
    rows = {}
    for variant in (REFERENCE, *VARIANTS):
        scenarios = {s: measure(trades, variant, s, level=level, samples=samples) for s in SCENARIOS}
        rows[variant] = {"scenarios": scenarios,
                         "verdict": "REFERENCE" if variant == REFERENCE else fh.verdict(scenarios[CENTRAL], scenarios[ADVERSE])}
    return rows


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        source_run: str = dm.SOURCE_RUN) -> dict:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    first = pd.read_parquet(settings.reports_dir / source_run / "trades.parquet")
    first = first[(first["method"] == dm.METHOD) & (first["status"] == fh.EXECUTED)]
    source = ExperimentRegistry(settings.experiments_db).get(source_run)
    hashes = (source or {}).get("data_hashes") or {}
    rows: list[dict] = []
    for symbol, part in first.groupby("symbol"):
        say(str(symbol))
        rows.extend(pair_rows(settings, str(symbol), part, end, hashes.get(f"1m/{symbol}")))
    trades = pd.DataFrame(rows).sort_values(["at", "key"]).reset_index(drop=True)
    result = evaluate(trades)
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("DBLS")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6),
               "source_run": source_run, "rows": result, "trades": int(len(trades)), "doc": "docs/FIGURES_HISTORIQUE.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="un stop resserré (moitié au toucher, clôture à 0,4 de la distance, −3 % fixe) rend-il le "
                               "double creux supérieur au hasard par unité de risque, frais compris ?",
                    strategy="DOUBLE_STOPS", strategy_version=1, variant="définitions figées (docs/FIGURES_HISTORIQUE.md)",
                    params={"variants": list(VARIANTS), "half": HALF, "close_share": CLOSE_SHARE, "fixed": FIXED,
                            "weights": WEIGHTS.tolist(), "source_run": source_run, "block_days": fh.BLOCK_DAYS,
                            "samples": fh.SAMPLES, "seed": fh.SEED},
                    period_label="DEVELOPMENT", period_start=str(fh.FIRST_DAY.date()), period_end=end.isoformat(),
                    universe=sorted(set(trades["symbol"])), data_hashes={"source": f"{source_run}/trades.parquet"},
                    git_commit=state, dependencies=dependency_versions(), seed=fh.SEED,
                    cost_scenario="central et défavorable (forward/costs.py)",
                    simulation_rules={"stop": "variante avant TP1 ; gestion A ensuite", "r": "par unité de risque prévu"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program,
                             "verdicts": {k: v["verdict"] for k, v in result.items()}}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    return payload
