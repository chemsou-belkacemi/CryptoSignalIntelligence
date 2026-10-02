"""Contrôle positif de la mesure (docs/POSITIVE_CONTROL.md, déclaré le 2026-10-03 avant exécution).

Un avantage CONNU est planté dans les vraies données ; on regarde si chaque chaîne de mesure du projet le retrouve
(puissance, biais) et combien de fois elle conclut à tort quand rien n'est planté (fausses alarmes). Aucune
hypothèse de marché n'est testée : 0 essai au programme. Trois chaînes :
1. criblage (D à K) : événements aléatoires, avantage δ planté, mesure `screen._row` ;
2. décisions ML : score planté corrélé à l'excès sur le même instant, corrélation de rang quotidienne ;
3. walk-forward : trades réels recentrés à 0 puis décalés de δ R, IC de l'admission (`day_block_ci95`).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci95
from ..config import Settings
from . import factors as fa
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .flow_screen import events_frame, forward_returns
from .intervals import calendar_mean_ci
from .protocol import development_end
from .screen import _row as screen_row
from .universe import RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION, N_TRIALS = "CONTROL", "POSITIVE_CONTROL", 1, 0
DOC = "docs/POSITIVE_CONTROL.md"
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
SCREEN_DENSITY = 0.10
SCREEN_DELTAS = {1: (0.0, 0.001, 0.0025, 0.005, 0.01), 7: (0.0, 0.005, 0.01, 0.02, 0.04)}
SCREEN_REPS = 100
ML_RUNS = {"swing": ("MLS-20261001T085521Z-a04c71", 168), "intraday": ("MLI-20261001T025936Z-0fab30", 4)}
RHOS = (0.0, 0.02, 0.05, 0.10)
ML_REPS = 50
MIN_PAIRS_PER_INSTANT = 5
WF_RUNS = {"A": "WF-20260930T143103Z-4b0da8", "B": "WF-20260930T150703Z-5eccae", "C": "WF-20260930T153623Z-7dd6cc"}
WF_DELTAS = (0.0, 0.05, 0.10, 0.20, 0.30)
WF_REPS = 100
WF_BLOCK_DAYS = 10
POWER = 0.80


@dataclass
class Result:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    screen: list[dict] = field(default_factory=list)
    ml: list[dict] = field(default_factory=list)
    ml_real: dict = field(default_factory=dict)
    walk_forward: list[dict] = field(default_factory=list)
    minimum_detectable: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)


# --- 1. Criblage -----------------------------------------------------------------------------------------------

def screen_power(panel: fa.Panel, settings: Settings, *, reps: int = SCREEN_REPS, deltas: dict[int, tuple] | None = None,
                 density: float = SCREEN_DENSITY, seed: int = 0, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Taux de détection et de « passe » de la mesure des criblages pour des événements aléatoires à avantage planté."""
    say = progress or (lambda _text: None)
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    out = []
    for horizon, values in (deltas or SCREEN_DELTAS).items():
        fwd = forward_returns(panel, horizon)
        fwd = fwd[fwd.index >= FIRST_DAY]
        valid = fwd.notna().to_numpy()
        for delta in values:
            say(f"criblage {horizon} j, δ = {delta:.2%}")
            detected = passed = 0
            estimates, raw = [], []
            for rep in range(reps):
                rng = np.random.default_rng([seed, horizon, int(delta * 1e6), rep])
                flags = pd.DataFrame((rng.random(fwd.shape) < density) & valid, index=fwd.index, columns=fwd.columns)
                frame = events_frame(flags, fwd)
                frame["ret"] += delta
                frame["excess"] += delta
                row = screen_row("PLANTE", horizon * 24, frame, hurdle_pct, settings)
                ci = row.ci95_excess_pct
                detected += bool(ci is not None and ci[0] > 0)
                passed += bool(row.beats_costs)
                estimates.append(row.mean_excess_pct)
                raw.append(row.mean_return_pct)
            out.append({"horizon_days": horizon, "delta_pct": round(delta * 100, 4), "reps": reps, "events_per_rep": int(len(frame)),
                        "detection_rate": round(detected / reps, 3), "pass_rate": round(passed / reps, 3),
                        "mean_excess_estimate_pct": round(float(np.mean([e for e in estimates if e is not None])), 4),
                        "mean_raw_return_pct": round(float(np.mean([r for r in raw if r is not None])), 4)})
    return out


# --- 2. Décisions ML -------------------------------------------------------------------------------------------

def excess_table(decisions: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par (instant, paire) valide : temps, paire, p, rendement net, excès sur la moyenne du même instant ;
    instants à moins de MIN_PAIRS_PER_INSTANT paires retirés."""
    table = decisions[["decision_time", "symbol", "p", "observed_net"]].rename(columns={"decision_time": "time", "observed_net": "net"})
    table = table[np.isfinite(table["net"].to_numpy(float))].copy()
    table["time"] = pd.to_datetime(table["time"], utc=True)
    grouped = table.groupby("time")["net"]
    table["n_at"] = grouped.transform("size")
    table = table[table["n_at"] >= MIN_PAIRS_PER_INSTANT].copy()
    table["excess"] = table["net"] - table.groupby("time")["net"].transform("mean")
    return table.drop(columns="n_at").sort_values(["time", "symbol"]).reset_index(drop=True)


def daily_rank_ic(table: pd.DataFrame, score: str) -> pd.Series:
    """Corrélation de rang (Spearman) entre `score` et l'excès à chaque instant, moyennée par jour UTC."""
    ranks = table[["time"]].copy()
    ranks["a"] = table.groupby("time")[score].rank()
    ranks["b"] = table.groupby("time")["excess"].rank()
    for column in ("a", "b"):
        ranks[column] = ranks[column] - ranks.groupby("time")[column].transform("mean")
    ranks["ab"], ranks["aa"], ranks["bb"] = ranks["a"] * ranks["b"], ranks["a"] ** 2, ranks["b"] ** 2
    sums = ranks.groupby("time")[["ab", "aa", "bb"]].sum()
    denominator = np.sqrt(sums["aa"] * sums["bb"])
    ic = (sums["ab"] / denominator.where(denominator > 0)).dropna()
    return ic.groupby(ic.index.floor("D")).mean()


def information(table: pd.DataFrame, score: str, *, horizon_hours: int) -> dict:
    """Corrélation de rang quotidienne moyenne et son IC95 ; excès moyen par décile du score et écart haut − bas."""
    ic = daily_rank_ic(table, score)
    block = max(10, 2 * -(-horizon_hours // 24))
    ci = calendar_mean_ci(ic.to_numpy(float), ic.index, block_days=block, level=0.95)
    deciles = pd.qcut(table[score].rank(method="first"), 10, labels=False)
    by_decile = table.groupby(deciles)["excess"].mean()
    return {"days": int(len(ic)), "rank_ic": round(float(ic.mean()), 5), "rank_ic_ci95": ci, "block_days": block,
            "detected": bool(ci is not None and ci[0] > 0),
            "excess_by_decile_pct": [round(float(v) * 100, 4) for v in by_decile.to_numpy()],
            "top_minus_bottom_pct": round(float(by_decile.iloc[-1] - by_decile.iloc[0]) * 100, 4)}


def planted_score(table: pd.DataFrame, rho: float, rng: np.random.Generator) -> np.ndarray:
    grouped = table.groupby("time")["excess"]
    std = grouped.transform("std").replace(0, np.nan)
    z = ((table["excess"] - grouped.transform("mean")) / std).fillna(0.0).to_numpy(float)
    return rho * z + np.sqrt(1 - rho ** 2) * rng.standard_normal(len(table))


def ml_power(table: pd.DataFrame, *, horizon_hours: int, reps: int = ML_REPS, rhos: tuple = RHOS, seed: int = 0,
             label: str = "", progress: Callable[[str], None] | None = None) -> list[dict]:
    say = progress or (lambda _text: None)
    out = []
    for rho in rhos:
        say(f"décisions ML {label}, ρ = {rho}")
        hits, ics = 0, []
        work = table[["time", "excess"]].copy()
        for rep in range(reps):
            rng = np.random.default_rng([seed, int(rho * 1000), rep, horizon_hours])
            work["s"] = planted_score(table, rho, rng)
            ic = daily_rank_ic(work, "s")
            ci = calendar_mean_ci(ic.to_numpy(float), ic.index, block_days=max(10, 2 * -(-horizon_hours // 24)), level=0.95)
            hits += bool(ci is not None and ci[0] > 0)
            ics.append(float(ic.mean()))
        out.append({"system": label, "rho": rho, "reps": reps, "realized_rank_ic": round(float(np.mean(ics)), 5),
                    "detection_rate": round(hits / reps, 3)})
    return out


# --- 3. Walk-forward -------------------------------------------------------------------------------------------

def load_trades(settings: Settings, run_id: str) -> pd.DataFrame:
    path = settings.reports_dir / run_id / "trades_oos_base_central.csv"
    if not path.exists():
        raise FileNotFoundError(f"trades hors échantillon absents : {path}")
    trades = pd.read_csv(path)
    trades = trades[np.isfinite(pd.to_numeric(trades["r_multiple"], errors="coerce"))]
    when = pd.to_datetime(trades["entry_time"].fillna(trades["setup_time"]), utc=True)
    return pd.DataFrame({"time": when.to_numpy(), "r": trades["r_multiple"].astype(float).to_numpy()}).sort_values("time")


def resample_blocks(trades: pd.DataFrame, rng: np.random.Generator, block_days: int = WF_BLOCK_DAYS) -> pd.DataFrame:
    """Nouvel échantillon de même durée : blocs de `block_days` jours de trades réels tirés avec remise et recollés
    bout à bout (dates des blocs réécrites), pour garder la dépendance à l'intérieur d'un bloc."""
    start = trades["time"].min().floor("D")
    block = ((trades["time"] - start) // pd.Timedelta(days=block_days)).to_numpy()
    blocks = int(block.max()) + 1
    parts = []
    for slot, pick in enumerate(rng.integers(0, blocks, size=blocks)):
        part = trades[block == pick]
        if len(part):
            shift = pd.Timedelta(days=block_days) * (slot - int(pick))
            parts.append(part.assign(time=part["time"] + shift))
    return pd.concat(parts, ignore_index=True) if parts else trades.iloc[:0]


def walk_forward_power(trades: pd.DataFrame, settings: Settings, *, label: str, reps: int = WF_REPS,
                       deltas: tuple = WF_DELTAS, seed: int = 0) -> list[dict]:
    centred = trades.assign(r=trades["r"] - trades["r"].mean())
    out = []
    for delta in deltas:
        hits = 0
        for rep in range(reps):
            rng = np.random.default_rng([seed, int(delta * 1000), rep])
            sample = resample_blocks(centred, rng)
            ci, _ = day_block_ci95(sample["r"].to_numpy(float) + delta, sample["time"].to_numpy(), block_days=WF_BLOCK_DAYS,
                                   samples=settings.protocol.bootstrap_samples, seed=settings.protocol.seed)
            hits += bool(ci is not None and ci[0] > 0)
        out.append({"strategy": label, "delta_r": delta, "reps": reps, "trades": int(len(trades)), "detection_rate": round(hits / reps, 3)})
    return out


def minimum_detectable(rows: list[dict], key: str, group: tuple[str, ...]) -> dict:
    """Plus petite taille plantée détectée dans au moins POWER des répétitions, par groupe."""
    out: dict[str, float | None] = {}
    frame = pd.DataFrame(rows)
    for name, part in frame.groupby(list(group)):
        hit = part[(part[key] > 0) & (part["detection_rate"] >= POWER)][key]
        out["/".join(str(v) for v in (name if isinstance(name, tuple) else (name,)))] = float(hit.min()) if len(hit) else None
    return out


# --- Exécution --------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, screen_reps: int = SCREEN_REPS, ml_reps: int = ML_REPS, wf_reps: int = WF_REPS,
        ml_runs: dict | None = None, wf_runs: dict | None = None) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("CTRL"), end.isoformat())
    seed = settings.protocol.seed
    say("données du criblage")
    frames = fa.load_frames(settings, list(symbols or RESEARCH_UNIVERSE), end)
    panel = fa.build_panel(frames)
    result.screen = screen_power(panel, settings, reps=screen_reps, seed=seed, progress=say)
    for label, (run_id, horizon) in (ml_runs or ML_RUNS).items():
        say(f"décisions ML {label}")
        decisions = pd.read_parquet(settings.reports_dir / run_id / "decisions.parquet")
        decisions = decisions[pd.to_datetime(decisions["decision_time"], utc=True) <= end]
        table = excess_table(decisions)
        result.data_hashes[run_id] = str(pd.util.hash_pandas_object(table[["time", "symbol", "p", "net"]], index=False).sum())
        result.ml += ml_power(table, horizon_hours=horizon, reps=ml_reps, seed=seed, label=label, progress=say)
        result.ml_real[label] = information(table, "p", horizon_hours=horizon) | {"run": run_id, "rows": int(len(table))}
    for label, run_id in (wf_runs or WF_RUNS).items():
        say(f"walk-forward {label}")
        trades = load_trades(settings, run_id)
        trades = trades[trades["time"] <= end]
        result.walk_forward += walk_forward_power(trades, settings, label=label, reps=wf_reps, seed=seed)
    result.minimum_detectable = {
        "screen_delta_pct": minimum_detectable(result.screen, "delta_pct", ("horizon_days",)),
        "ml_rho": minimum_detectable(result.ml, "rho", ("system",)),
        "walk_forward_delta_r": minimum_detectable(result.walk_forward, "delta_r", ("strategy",))}
    _record(settings, result, now=now, code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": DOC, "power": POWER}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="contrôle positif : nos chaînes de mesure retrouvent-elles un avantage synthétique planté, à quelle taille, "
                   "et combien de fausses alarmes sans avantage ? (aucune hypothèse de marché)",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"avantages plantés figés ({DOC})",
        params={"screen_deltas": {str(k): list(v) for k, v in SCREEN_DELTAS.items()}, "screen_density": SCREEN_DENSITY,
                "rhos": list(RHOS), "wf_deltas": list(WF_DELTAS), "reps": {"screen": SCREEN_REPS, "ml": ML_REPS, "wf": WF_REPS},
                "ml_runs": {k: v[0] for k, v in ML_RUNS.items()}, "wf_runs": WF_RUNS, "power": POWER},
        period_label="DEVELOPMENT", period_start=str(FIRST_DAY)[:10], period_end=result.period_end, universe=list(RESEARCH_UNIVERSE),
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="central (seuil des criblages)", simulation_rules={"planted": "avantages synthétiques, aucune hypothèse de marché"},
        metrics={"n_trials": N_TRIALS, "minimum_detectable": result.minimum_detectable, "ml_real": result.ml_real},
        status="COMPLETED", report_dir=str(report_dir))


def report_path(settings: Settings, run_id: str) -> Path:
    return settings.reports_dir / run_id / "summary.json"
