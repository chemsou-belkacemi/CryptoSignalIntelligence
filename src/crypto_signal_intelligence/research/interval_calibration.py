"""Calibrage des intervalles sous l'hypothèse nulle (docs/POSITIVE_CONTROL.md § 6, déclaré le 2026-10-03 avant
exécution). Point 1 du plan de travail : comparer la méthode d'intervalle des criblages (blocs de jours à événement,
percentile) à l'intervalle calendaire de Student et au bootstrap stationnaire de `arch`.

Le contrôle positif v1 tirait des événements au hasard jour par jour : sans grappes dans le temps, la composante
commune du marché se moyenne et presque aucun intervalle ne sort de zéro (0 % de fausses alarmes). Ce n'est pas un
calibrage. Ici, les drapeaux sont de VRAIES conditions (groupées dans le temps et entre paires) et le lien avec le
futur est cassé : les rendements à terme sont remplacés par une histoire rééchantillonnée par blocs stationnaires de
journées entières (toutes les paires ensemble), les drapeaux restant à leur place. Sous cette nulle, une méthode bien
calibrée donne environ 2,5 % de bornes basses > 0 (et autant de bornes hautes < 0). Avec un avantage planté, on mesure
la puissance de chaque méthode. 0 essai : aucune hypothèse de marché.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import factors as fa
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .flow_screen import daily_flow, events_frame, flow_flags, forward_returns
from .intervals import calendar_mean_ci, stationary_bootstrap_ci
from .positive_control import daily_rank_ic, excess_table, planted_score
from .protocol import development_end
from .screen import _day_block_ci
from .universe import RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION, N_TRIALS = "CONTROL", "INTERVAL_CALIBRATION", 1, 0
DOC = "docs/POSITIVE_CONTROL.md"
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
HORIZONS = (1, 7)
NULL_REPS, POWER_REPS = 200, 100
PLANTED = {1: 0.0025, 7: 0.01}                 # avantages plantés pour la puissance (≈ 50-80 % au contrôle v1)
METHODS = ("blocs_de_jours", "calendaire_student", "stationnaire_arch")
ML_RUNS = {"swing": ("MLS-20261001T085521Z-a04c71", 168), "intraday": ("MLI-20261001T025936Z-0fab30", 4)}
ML_RHO = 0.02
TARGET, BAND = 0.025, (0.015, 0.04)


@dataclass
class Result:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    screen: list[dict] = field(default_factory=list)
    ml: list[dict] = field(default_factory=list)
    choice: dict = field(default_factory=dict)


# --- Conditions réelles, groupées dans le temps -------------------------------------------------------------------

def real_conditions(panel: fa.Panel, frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Trois drapeaux réels connus à l'instant : J1 et J2 du criblage J (flux d'ordres), et une condition de marché
    (BTC en baisse de plus de 3 % sur la journée, appliquée à toutes les paires : la plus groupée possible)."""
    high, low = flow_flags(daily_flow(frames).reindex(panel.close.index))
    btc = panel.close["BTCUSDT"] if "BTCUSDT" in panel.close else panel.close.iloc[:, 0]
    crash = (btc.pct_change(fill_method=None) < -0.03).to_numpy()
    market = pd.DataFrame(np.repeat(crash[:, None], len(panel.symbols), axis=1), index=panel.close.index, columns=panel.symbols)
    return {"J1_achats_hauts": high.reindex(columns=panel.symbols).fillna(False).astype(bool),
            "J2_achats_bas": low.reindex(columns=panel.symbols).fillna(False).astype(bool),
            "BTC_chute_3pct": market & panel.close.notna()}


def resampled_history(fwd: pd.DataFrame, rng: np.random.Generator, mean_block: float) -> pd.DataFrame:
    """Rendements à terme d'une histoire rééchantillonnée par blocs stationnaires de journées ENTIÈRES (toutes les
    paires ensemble), remis sur le calendrier d'origine : les drapeaux gardent leur place, le lien avec le futur est
    cassé, la dépendance entre paires et dans le temps est conservée."""
    n = len(fwd)
    restart = rng.random(n) < 1.0 / mean_block
    restart[0] = True
    starts = rng.integers(0, n, size=n)
    picks = np.empty(n, dtype=np.int64)
    for i in range(n):                                   # nouveau bloc : départ au hasard ; sinon jour suivant (circulaire)
        picks[i] = starts[i] if restart[i] else (picks[i - 1] + 1) % n
    return pd.DataFrame(fwd.to_numpy()[picks], index=fwd.index, columns=fwd.columns)


def intervals(frame: pd.DataFrame, horizon: int, settings: Settings, seed: int) -> dict[str, list[float] | None]:
    block = max(10, 2 * horizon)
    out: dict[str, list[float] | None] = {}
    ci = _day_block_ci(frame, block, settings.protocol.bootstrap_samples, seed)
    out["blocs_de_jours"] = [ci[0] / 100, ci[1] / 100] if ci is not None else None
    out["calendaire_student"] = calendar_mean_ci(frame["excess"].to_numpy(float), frame["time"], block_days=block)
    out["stationnaire_arch"] = stationary_bootstrap_ci(frame["excess"].to_numpy(float), frame["time"], reps=1000, seed=seed,
                                                       block_days=float(block))[0]
    return out


def screen_calibration(panel: fa.Panel, conditions: dict[str, pd.DataFrame], settings: Settings, *, null_reps: int, power_reps: int,
                       seed: int, progress: Callable[[str], None]) -> list[dict]:
    out = []
    for horizon in HORIZONS:
        fwd_all = forward_returns(panel, horizon)
        fwd_all = fwd_all[fwd_all.index >= FIRST_DAY]
        for name, flags in conditions.items():
            flags = flags[flags.index >= FIRST_DAY]
            for planted, reps in ((0.0, null_reps), (PLANTED[horizon], power_reps)):
                progress(f"criblage {horizon} j, {name}, δ = {planted:.2%}")
                low = dict.fromkeys(METHODS, 0)
                high = dict.fromkeys(METHODS, 0)
                missing = dict.fromkeys(METHODS, 0)
                events = []
                for rep in range(reps):
                    rng = np.random.default_rng([seed, horizon, rep, int(planted * 1e6), len(name)])
                    fwd = resampled_history(fwd_all, rng, mean_block=max(10, 2 * horizon))
                    frame = events_frame(flags, fwd)
                    if frame.empty:
                        continue
                    frame["excess"] += planted
                    events.append(len(frame))
                    for method, ci in intervals(frame, horizon, settings, seed + rep).items():
                        if ci is None:
                            missing[method] += 1
                            continue
                        low[method] += ci[0] > 0
                        high[method] += ci[1] < 0
                for method in METHODS:
                    out.append({"chain": "criblage", "horizon_days": horizon, "condition": name, "planted_pct": round(planted * 100, 3),
                                "method": method, "reps": reps, "events_mean": int(np.mean(events)) if events else 0,
                                "low_above_zero": round(low[method] / reps, 4), "high_below_zero": round(high[method] / reps, 4),
                                "no_interval": missing[method]})
    return out


def ml_calibration(settings: Settings, *, null_reps: int, power_reps: int, seed: int, end: pd.Timestamp,
                   progress: Callable[[str], None], runs: dict | None = None) -> list[dict]:
    """Scores aléatoires (nulle exacte : indépendants de l'histoire) ou plantés (ρ) sur les décisions ML réelles ;
    intervalle de la corrélation de rang quotidienne moyenne par la méthode calendaire et par le bootstrap stationnaire."""
    out = []
    for label, (run_id, horizon) in (runs or ML_RUNS).items():
        decisions = pd.read_parquet(settings.reports_dir / run_id / "decisions.parquet")
        decisions = decisions[pd.to_datetime(decisions["decision_time"], utc=True) <= end]
        table = excess_table(decisions)[["time", "excess"]].copy()
        block = max(10, 2 * -(-horizon // 24))
        for rho, reps in ((0.0, null_reps), (ML_RHO, power_reps)):
            progress(f"décisions ML {label}, ρ = {rho}")
            low = {"calendaire_student": 0, "stationnaire_arch": 0}
            high = dict(low)
            for rep in range(reps):
                rng = np.random.default_rng([seed, rep, int(rho * 1000), horizon])
                table["s"] = planted_score(table.assign(excess=table["excess"]), rho, rng)
                ic = daily_rank_ic(table, "s")
                cis = {"calendaire_student": calendar_mean_ci(ic.to_numpy(float), ic.index, block_days=block),
                       "stationnaire_arch": stationary_bootstrap_ci(ic.to_numpy(float), ic.index, reps=1000, seed=seed + rep,
                                                                    block_days=float(block))[0]}
                for method, ci in cis.items():
                    if ci is not None:
                        low[method] += ci[0] > 0
                        high[method] += ci[1] < 0
            for method in low:
                out.append({"chain": "ML", "system": label, "rho": rho, "method": method, "reps": reps,
                            "low_above_zero": round(low[method] / reps, 4), "high_below_zero": round(high[method] / reps, 4)})
    return out


def choose(rows: list[dict]) -> dict:
    """Règle déclarée : pour chaque chaîne, la méthode retenue pour les protocoles FUTURS est celle dont le taux de
    bornes basses > 0 sous la nulle reste dans [1,5 % ; 4 %] pour toutes les conditions et horizons, et qui a la plus
    grande puissance moyenne ; la méthode actuelle est gardée si aucune autre ne fait mieux sur ces deux points."""
    frame = pd.DataFrame(rows)
    out = {}
    for chain, part in frame.groupby("chain"):
        key = "planted_pct" if chain == "criblage" else "rho"
        null, power = part[part[key] == 0], part[part[key] > 0]
        summary = {}
        for method, rows_m in null.groupby("method"):
            rates = rows_m["low_above_zero"]
            summary[method] = {"null_min": float(rates.min()), "null_max": float(rates.max()),
                               "calibrated": bool(rates.between(*BAND).all()),
                               "power_mean": float(power[power["method"] == method]["low_above_zero"].mean())}
        current = "blocs_de_jours" if chain == "criblage" else "calendaire_student"
        good = {m: s for m, s in summary.items() if s["calibrated"]}
        best = max(good, key=lambda m: good[m]["power_mean"]) if good else current
        out[str(chain)] = {"methods": summary, "current": current, "retained": best}
    return out


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, null_reps: int = NULL_REPS, power_reps: int = POWER_REPS, ml_runs: dict | None = None) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("CAL"), end.isoformat())
    say("données")
    frames = fa.load_frames(settings, list(symbols or RESEARCH_UNIVERSE), end)
    panel = fa.build_panel(frames)
    conditions = real_conditions(panel, frames)
    result.screen = screen_calibration(panel, conditions, settings, null_reps=null_reps, power_reps=power_reps,
                                       seed=settings.protocol.seed, progress=say)
    result.ml = ml_calibration(settings, null_reps=null_reps, power_reps=power_reps, seed=settings.protocol.seed, end=end,
                               progress=say, runs=ml_runs)
    result.choice = choose(result.screen + result.ml)
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps(asdict(result) | {"doc": DOC, "protocol_version": PROTOCOL_VERSION},
                                                        indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="calibrage des intervalles sous la nulle (conditions réelles groupées, histoire rééchantillonnée) et puissance ; "
                   "aucune hypothèse de marché",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"méthodes figées ({DOC} § 6)",
        params={"methods": list(METHODS), "horizons": list(HORIZONS), "null_reps": NULL_REPS, "power_reps": POWER_REPS,
                "planted": {str(k): v for k, v in PLANTED.items()}, "ml_rho": ML_RHO, "band": list(BAND)},
        period_label="DEVELOPMENT", period_start=str(FIRST_DAY)[:10], period_end=result.period_end, universe=list(symbols or RESEARCH_UNIVERSE),
        data_hashes={}, git_commit=state, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun", simulation_rules={"null": "drapeaux réels, histoire rééchantillonnée par blocs stationnaires"},
        metrics={"n_trials": N_TRIALS, "choice": result.choice}, status="COMPLETED", report_dir=str(report_dir))
    return result
