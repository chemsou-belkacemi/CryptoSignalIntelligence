"""Portefeuilles hebdomadaires entre cryptos sur l'univers À DATE (point 7 du plan de travail ; docs/XSECTION.md,
déclaré le 2026-10-03 avant exécution) : momentum, double momentum et « paires calmes », contre la moyenne de
l'univers, sur les bougies journalières de TOUTES les paires USDT (retirées comprises, docs/UNIVERSE_PIT.md).

Chaque lundi, l'univers est le top 40 du mois ; signaux sur les clôtures jusqu'au dimanche ; achat avec un jour de
retard (clôture du lundi) et détention jusqu'à la clôture du lundi suivant ; poids égaux ; coûts sur la rotation.
Une paire retirée en cours de semaine est vendue à sa dernière clôture connue (déclaré : peut être optimiste).
Même mesure sur les 40 paires de recherche (survivantes) : l'écart entre les deux lectures est le biais de survivance.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .intervals import calendar_mean_ci
from .protocol import development_end
from .universe import RESEARCH_UNIVERSE

FIRST_WEEK = pd.Timestamp("2019-01-07", tz="UTC")          # premier lundi de 2019
LOOKBACK, VOL_DAYS, PICK = 28, 28, 8
COST_PER_SIDE = 0.00075 + 0.0005                           # frais + glissement « autres paires » du modèle commun
PORTFOLIOS = ("MOM_4S", "DOUBLE_MOM_4S", "CALMES")
UNIVERSES = ("A_DATE", "SURVIVANTES")
N_TRIALS = len(PORTFOLIOS) * len(UNIVERSES)
BLOCK_DAYS = 56


def weekly_weights(closes: pd.DataFrame, members: dict[pd.Period, set[str]], mondays: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Poids cibles décidés à chaque lundi (sur les clôtures jusqu'au dimanche inclus)."""
    returns = closes.pct_change(fill_method=None)
    out = {name: pd.DataFrame(0.0, index=mondays, columns=closes.columns) for name in (*PORTFOLIOS, "MOYENNE")}
    for monday in mondays:
        sunday = monday - pd.Timedelta(days=1)
        if sunday not in closes.index:
            continue
        universe = [s for s in members.get(monday.tz_convert(None).to_period("M"), set()) if s in closes.columns]
        past = closes.loc[:sunday]
        last = past.iloc[-1][universe]
        ago = past.iloc[-1 - LOOKBACK][universe] if len(past) > LOOKBACK else pd.Series(np.nan, index=universe)
        momentum = (last / ago - 1).dropna()
        vol = returns.loc[:sunday].iloc[-VOL_DAYS:][universe].std().dropna()
        alive = last.dropna().index
        if len(alive):
            out["MOYENNE"].loc[monday, alive] = 1.0 / len(alive)
        top = momentum.sort_values(ascending=False).head(PICK)
        if len(top):
            out["MOM_4S"].loc[monday, top.index] = 1.0 / PICK
            positive = top[top > 0]
            out["DOUBLE_MOM_4S"].loc[monday, positive.index] = 1.0 / PICK          # le reste en liquidités
        calm = vol.sort_values().head(PICK)
        if len(calm):
            out["CALMES"].loc[monday, calm.index] = 1.0 / PICK
    return out


def holding_returns(closes: pd.DataFrame, mondays: pd.DatetimeIndex) -> pd.DataFrame:
    """Rendement de la clôture du lundi à celle du lundi suivant (un jour de retard sur la décision) ; une paire sans
    clôture à la fin est valorisée à sa dernière clôture connue de la semaine."""
    out = pd.DataFrame(np.nan, index=mondays, columns=closes.columns)
    for i, monday in enumerate(mondays[:-1]):
        nxt = mondays[i + 1]
        if monday not in closes.index:
            continue
        start = closes.loc[monday]
        window = closes.loc[monday:nxt]
        end = window.ffill().iloc[-1]
        out.loc[monday] = end / start - 1
    return out


def portfolio_series(weights: pd.DataFrame, returns: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Rendement net hebdomadaire et rotation (somme des |Δ poids|)."""
    gross = (weights * returns.fillna(0.0)).sum(axis=1)
    turnover = weights.diff().fillna(weights).abs().sum(axis=1)                 # première semaine : achat complet
    return gross - turnover * COST_PER_SIDE, turnover


def describe(net: pd.Series, reference: pd.Series) -> dict:
    net, reference = net.dropna(), reference.reindex(net.index)
    excess = (net - reference).dropna()
    ci = calendar_mean_ci(excess.to_numpy(float), excess.index, block_days=BLOCK_DAYS, min_blocks=10)
    value = (1 + net).cumprod()
    drawdown = float((value / value.cummax() - 1).min()) if len(value) else None
    sharpe = float(net.mean() / net.std() * np.sqrt(52)) if net.std() > 0 else None
    by_year = excess.groupby(excess.index.year).mean()
    return {"weeks": int(len(net)), "weekly_mean_pct": round(float(net.mean()) * 100, 3), "sharpe": round(sharpe, 3) if sharpe else None,
            "max_drawdown_pct": round(drawdown * 100, 1) if drawdown is not None else None,
            "excess_weekly_pct": round(float(excess.mean()) * 100, 3),
            "excess_ci_pct": [round(c * 100, 3) for c in ci] if ci else None,
            "years_positive": f"{int((by_year > 0).sum())}/{len(by_year)}"}


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False) -> dict:
    from .pit_universe import load_membership, pit_dir
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    daily = pd.read_parquet(pit_dir(settings) / "daily.parquet")
    closes = daily.pivot_table(index="day", columns="symbol", values="close").sort_index()
    closes = closes[closes.index <= end]
    table = load_membership(settings)
    members_pit = table.groupby(table["month"].dt.tz_convert(None).dt.to_period("M"))["symbol"].apply(set).to_dict()
    survivors = set(RESEARCH_UNIVERSE)
    members_surv = {m: {s for s in survivors if s in closes.columns} for m in members_pit}
    mondays = pd.date_range(FIRST_WEEK, closes.index.max(), freq="W-MON")
    returns = holding_returns(closes, mondays)
    out: dict = {"weeks": int(len(mondays)), "rows": {}}
    for universe, members in (("A_DATE", members_pit), ("SURVIVANTES", members_surv)):
        say(universe)
        weights = weekly_weights(closes, members, mondays)
        reference, _ = portfolio_series(weights["MOYENNE"], returns)
        out["rows"][universe] = {"MOYENNE": describe(reference, reference * 0)}
        for name in PORTFOLIOS:
            net, turnover = portfolio_series(weights[name], returns)
            out["rows"][universe][name] = describe(net, reference) | {"turnover_weekly": round(float(turnover.mean()), 3)}
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("XSEC")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, **out, "doc": "docs/XSECTION.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind="XSECTION",
                    hypothesis="portefeuilles hebdomadaires (momentum, double momentum, paires calmes) contre la moyenne de l'univers, à date et "
                               "sur les survivantes",
                    strategy="XSECTION_WEEKLY", strategy_version=1, variant="portefeuilles figés (docs/XSECTION.md)",
                    params={"lookback": LOOKBACK, "vol_days": VOL_DAYS, "pick": PICK, "cost_per_side": COST_PER_SIDE, "block_days": BLOCK_DAYS},
                    period_label="DEVELOPMENT", period_start=str(FIRST_WEEK)[:10], period_end=end.isoformat(), universe=sorted(closes.columns),
                    data_hashes={}, git_commit=state, dependencies=dependency_versions(), seed=settings.protocol.seed,
                    cost_scenario="frais 7,5 pb + glissement 5 pb par côté sur la rotation",
                    simulation_rules={"entry": "clôture du lundi (décision sur le dimanche)", "exit": "clôture du lundi suivant"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "rows": out["rows"]}, status="COMPLETED", report_dir=str(report_dir))
    return payload
