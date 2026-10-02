"""Registre unifié des prédictions et rapport d'information DESCRIPTIF (docs/INFORMATION_REPORT.md, déclaré le
2026-10-03 avant exécution).

Rassemble dans un seul schéma les prédictions hors échantillon déjà enregistrées (décisions ML, trades des
walk-forwards, prévisions de volatilité) et décrit, pour chacune, l'information qu'elle contient : corrélation de
rang avec l'excès sur le même instant, excès par décile, par année, par régime, avant et après frais. Aucun modèle
n'est ajusté, aucun verdict n'est rendu : 0 essai. Une piste lue ici n'est qu'une hypothèse, à pré-inscrire et à
mesurer sur des données jamais vues.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci95
from ..config import Settings
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .positive_control import daily_rank_ic, excess_table, information
from .protocol import development_end

KIND, STRATEGY, PROTOCOL_VERSION, N_TRIALS = "REPORT", "INFORMATION_REPORT", 1, 0
DOC = "docs/INFORMATION_REPORT.md"
ML_SOURCES = {"ML_SWING": ("MLS-20261001T085521Z-a04c71", 168), "ML_INTRADAY": ("MLI-20261001T025936Z-0fab30", 4),
              "ML_SWING_LONG": ("MLL-20261001T194110Z-090770", 168)}
WF_SOURCES = {"A_DONCHIAN": "WF-20260930T143103Z-4b0da8", "B_EMA_PULLBACK": "WF-20260930T150703Z-5eccae",
              "C_RANGE_REENTRY": "WF-20260930T153623Z-7dd6cc"}
VOL_SOURCE, VOL_MODEL = "VOL-20261002T170500Z-c3bda6", "REF_SERVICE"
SCENARIOS = ("central", "adverse", "stress")
UNIFIED = ("source", "family", "symbol", "origin", "horizon_h", "score", "outcome", "outcome_unit", "outcome_gross",
           "excess", "cost_rt_bps", "regime_trend", "regime_vol", "decision")


@dataclass
class Report:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    ml: dict = field(default_factory=dict)
    walk_forward: dict = field(default_factory=dict)
    volatility: dict = field(default_factory=dict)
    cells: int = 0
    expected_false_alarms: float = 0.0
    data_hashes: dict = field(default_factory=dict)


def _ci(values: np.ndarray, times, settings: Settings, block_days: int = 10) -> list[float] | None:
    ci, _ = day_block_ci95(np.asarray(values, float), np.asarray(times), block_days=block_days,
                           samples=settings.protocol.bootstrap_samples, seed=settings.protocol.seed)
    return [round(float(ci[0]), 5), round(float(ci[1]), 5)] if ci is not None else None


# --- Décisions ML ------------------------------------------------------------------------------------------------

def ml_unified(name: str, decisions: pd.DataFrame, horizon_h: int) -> pd.DataFrame:
    table = excess_table(decisions)
    cost = float(decisions["cost_round_trip_bps"].iloc[0]) if len(decisions) else 0.0
    entered = decisions.assign(time=pd.to_datetime(decisions["decision_time"], utc=True))[["time", "symbol", "decision"]]
    table = table.merge(entered, on=["time", "symbol"], how="left")
    return pd.DataFrame({"source": name, "family": "ML", "symbol": table["symbol"], "origin": table["time"], "horizon_h": horizon_h,
                         "score": table["p"], "outcome": table["net"], "outcome_unit": "rendement net", "outcome_gross": table["net"] + cost / 1e4,
                         "excess": table["excess"], "cost_rt_bps": cost, "regime_trend": None, "regime_vol": None,
                         "decision": table["decision"]})


def ml_section(unified: pd.DataFrame, horizon_h: int, settings: Settings) -> dict:
    table = unified.rename(columns={"origin": "time", "score": "p", "outcome": "net"})[["time", "symbol", "p", "net", "excess", "outcome_gross", "decision"]]
    info = information(table, "p", horizon_hours=horizon_h)
    ic = daily_rank_ic(table, "p")
    by_year = {str(y): round(float(v), 5) for y, v in ic.groupby(ic.index.year).mean().items()}
    deciles = pd.qcut(table["p"].rank(method="first"), 10, labels=False)
    top = table[deciles == 9]
    block = max(10, 2 * -(-horizon_h // 24))
    entered = table[table["decision"] == "ENTER"]
    return info | {"rank_ic_by_year": by_year, "rows": int(len(table)),
                   "top_decile": {"n": int(len(top)), "gross_pct": round(float(top["outcome_gross"].mean()) * 100, 4),
                                  "net_pct": round(float(top["net"].mean()) * 100, 4),
                                  "excess_pct": round(float(top["excess"].mean()) * 100, 4),
                                  "excess_ci95_pct": _pct(_ci(top["excess"].to_numpy(), top["time"].to_numpy(), settings, block))},
                   "entered": {"n": int(len(entered)), "net_pct": round(float(entered["net"].mean()) * 100, 4) if len(entered) else None,
                               "excess_pct": round(float(entered["excess"].mean()) * 100, 4) if len(entered) else None}}


def _pct(ci: list[float] | None) -> list[float] | None:
    return None if ci is None else [round(ci[0] * 100, 4), round(ci[1] * 100, 4)]


# --- Walk-forwards -------------------------------------------------------------------------------------------------

def wf_trades(settings: Settings, run_id: str, scenario: str) -> pd.DataFrame:
    trades = pd.read_csv(settings.reports_dir / run_id / f"trades_oos_base_{scenario}.csv")
    trades = trades[np.isfinite(pd.to_numeric(trades["r_multiple"], errors="coerce"))].copy()
    trades["time"] = pd.to_datetime(trades["entry_time"].fillna(trades["setup_time"]), utc=True)
    return trades


def wf_unified(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    hours = (pd.to_datetime(trades["exit_time"], utc=True) - trades["time"]).dt.total_seconds() / 3600
    return pd.DataFrame({"source": name, "family": "WALK_FORWARD", "symbol": trades["symbol"], "origin": trades["time"],
                         "horizon_h": hours.round(2), "score": np.nan, "outcome": trades["r_multiple"].astype(float),
                         "outcome_unit": "R net (central)", "outcome_gross": trades["gross_return"].astype(float), "excess": np.nan,
                         "cost_rt_bps": np.nan, "regime_trend": trades["trend_regime"], "regime_vol": trades["volatility_regime"],
                         "decision": trades["entry_status"]})


def wf_section(settings: Settings, run_id: str) -> tuple[dict, pd.DataFrame]:
    out: dict = {"scenarios": {}}
    central = None
    for scenario in SCENARIOS:
        trades = wf_trades(settings, run_id, scenario)
        out["scenarios"][scenario] = {"trades": int(len(trades)), "r_mean": round(float(trades["r_multiple"].mean()), 4),
                                      "r_ci95": _ci(trades["r_multiple"].to_numpy(), trades["time"].to_numpy(), settings),
                                      "gross_return_pct": round(float(trades["gross_return"].mean()) * 100, 4),
                                      "net_return_pct": round(float(trades["net_return"].mean()) * 100, 4)}
        if scenario == "central":
            central = trades
    assert central is not None
    for column, key in (("trend_regime", "by_trend"), ("volatility_regime", "by_volatility")):
        out[key] = {str(k): {"trades": int(len(g)), "r_mean": round(float(g["r_multiple"].mean()), 4),
                             "r_ci95": _ci(g["r_multiple"].to_numpy(), g["time"].to_numpy(), settings),
                             "gross_return_pct": round(float(g["gross_return"].mean()) * 100, 4)}
                    for k, g in central.groupby(column)}
    out["by_year"] = {str(y): {"trades": int(len(g)), "r_mean": round(float(g["r_multiple"].mean()), 4)}
                      for y, g in central.groupby(central["time"].dt.year)}
    out["mae_mfe_median_r"] = [round(float(central["mae_r"].median()), 3), round(float(central["mfe_r"].median()), 3)]
    return out, central


# --- Volatilité ------------------------------------------------------------------------------------------------------

def vol_section(forecasts: pd.DataFrame) -> dict:
    """Étalonnage de l'ampleur : par décile de la variance prévue, rapport moyen réalisé / prévu ; pente de
    log réalisé sur log prévu (1 = bien étalonné)."""
    out = {}
    for horizon, part in forecasts[forecasts["model"] == VOL_MODEL].groupby("horizon"):
        part = part[(part["forecast"] > 0) & (part["realized"] > 0)]
        deciles = pd.qcut(part["forecast"].rank(method="first"), 10, labels=False)
        ratio = part.groupby(deciles).apply(lambda g: float(g["realized"].mean() / g["forecast"].mean()))
        x, y = np.log(part["forecast"].to_numpy()), np.log(part["realized"].to_numpy())
        slope = float(np.polyfit(x, y, 1)[0])
        out[f"{horizon}d"] = {"rows": int(len(part)), "realized_over_forecast_by_decile": [round(float(v), 3) for v in ratio.to_numpy()],
                              "log_slope": round(slope, 3), "log_correlation": round(float(np.corrcoef(x, y)[0, 1]), 3)}
    return out


# --- Exécution -----------------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        ml_sources: dict | None = None, wf_sources: dict | None = None, vol_source: str | None = VOL_SOURCE) -> Report:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    report = Report(new_run_id("INFO"), end.isoformat())
    parts: list[pd.DataFrame] = []
    for name, (run_id, horizon) in (ml_sources or ML_SOURCES).items():
        say(f"décisions {name}")
        decisions = pd.read_parquet(settings.reports_dir / run_id / "decisions.parquet")
        decisions = decisions[pd.to_datetime(decisions["decision_time"], utc=True) <= end]
        unified = ml_unified(name, decisions, horizon)
        report.ml[name] = ml_section(unified, horizon, settings) | {"run": run_id}
        report.data_hashes[run_id] = str(pd.util.hash_pandas_object(unified[["origin", "symbol", "score", "outcome"]], index=False).sum())
        parts.append(unified)
    for name, run_id in (wf_sources or WF_SOURCES).items():
        say(f"walk-forward {name}")
        section, central = wf_section(settings, run_id)
        report.walk_forward[name] = section | {"run": run_id}
        parts.append(wf_unified(name, central[central["time"] <= end]))
    if vol_source:
        say("volatilité")
        forecasts = pd.read_parquet(settings.reports_dir / vol_source / "forecasts.parquet")
        report.volatility = vol_section(forecasts) | {"run": vol_source}
    report.cells = (len(report.ml) * (10 + 1 + 7) + len(report.walk_forward) * (3 + 6 + 7))
    report.expected_false_alarms = round(report.cells * 0.05, 1)
    report_dir = settings.reports_dir / report.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    unified_all = pd.concat(parts, ignore_index=True)[list(UNIFIED)] if parts else pd.DataFrame(columns=list(UNIFIED))
    unified_all.astype({"regime_trend": "string", "regime_vol": "string", "decision": "string"}).to_parquet(
        report_dir / "predictions.parquet", index=False)
    payload = asdict(report) | {"protocol_version": PROTOCOL_VERSION, "doc": DOC, "schema": list(UNIFIED)}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=report.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="rapport d'information descriptif sur les prédictions hors échantillon déjà enregistrées (aucun verdict)",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"lecture figée ({DOC})",
        params={"ml_sources": {k: v[0] for k, v in (ml_sources or ML_SOURCES).items()}, "wf_sources": wf_sources or WF_SOURCES,
                "vol_source": vol_source, "schema": list(UNIFIED)},
        period_label="DEVELOPMENT", period_start="2017-01-01", period_end=report.period_end, universe=[],
        data_hashes=report.data_hashes, git_commit=state, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="enregistré par chaque source", simulation_rules={"lecture": "descriptive, aucun ajustement"},
        metrics={"n_trials": N_TRIALS, "cells": report.cells, "expected_false_alarms": report.expected_false_alarms},
        status="COMPLETED", report_dir=str(report_dir))
    return report

