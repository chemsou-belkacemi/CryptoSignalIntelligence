"""Prévision de volatilité, protocole v4 (docs/VOLATILITY.md § 17, déclaré le 2026-10-03 avant exécution). Point 5 du
plan de travail : le rapport d'information a montré que les prévisions en service sont bien classées mais mal
étalonnées aux extrêmes (déciles bas sous-estimés à 1-3 jours, 7 jours surestimé d'environ 10 %).

Référence : `REF_SERVICE` (prévisions hors échantillon du protocole v2, VOL-20261002T170500Z-c3bda6). Candidats :
- `C1_REETALONNE` : log RV² = a + b · log F_service, a et b estimés chaque mois sur le passé purgé (origine + H jours
  ≤ date), puis retour à la variance par la moyenne de exp(résidu) (Duan) ;
- `C2_GARCH` : GARCH(1,1) à moyenne nulle (bibliothèque `arch`) sur les rendements log JOURNALIERS de la paire
  (clôtures de 00:00), réestimé le 1er de chaque mois sur les seuls rendements connus, prévision de la somme des
  variances journalières sur H jours, ramenée à l'échelle de RV² par le rapport moyen RV² / prévision du passé purgé.
3 horizons × 2 candidats = 6 comparaisons, comptées ; règle du § 14 (référence = service, années couvertes − 1).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import volatility as v1
from . import volatility_v2 as v2
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .protocol import development_end

PROTOCOL_VERSION = 4
STRATEGY, DOC = "VOLATILITY_FORECAST_V4", v1.DOC
SOURCE_RUN, REFERENCE = "VOL-20261002T170500Z-c3bda6", "REF_SERVICE"
CANDIDATES = ("C1_REETALONNE", "C2_GARCH")
MODELS = (REFERENCE, *CANDIDATES)
N_TRIALS = len(CANDIDATES) * len(v1.HORIZONS)
LEVEL = 1 - 0.05 / N_TRIALS
MIN_FIT_ROWS = 500
GARCH_MIN_DAYS = 365


def service_forecasts(settings: Settings, run_id: str = SOURCE_RUN) -> pd.DataFrame:
    path = settings.reports_dir / run_id / "forecasts.parquet"
    table = pd.read_parquet(path)
    table = table[(table["model"] == REFERENCE) & (table["forecast"] > 0) & (table["realized"] > 0)]
    return table.assign(origin=pd.to_datetime(table["origin"], utc=True))[["symbol", "origin", "horizon", "forecast", "realized"]]


def recalibrated(part: pd.DataFrame, horizon: int) -> np.ndarray:
    """C1 : régression log RV² sur log F, réestimée le 1er de chaque mois sur les lignes dont la cible est connue."""
    out = np.full(len(part), np.nan)
    origins = part["origin"]
    for month in pd.date_range(origins.min().replace(day=1), origins.max(), freq="MS"):
        rows = ((origins >= month) & (origins < month + pd.offsets.MonthBegin(1))).to_numpy()
        train = part[(origins + pd.Timedelta(days=horizon) <= month).to_numpy()]
        if len(train) < MIN_FIT_ROWS or not rows.any():
            continue
        x, y = np.log(train["forecast"].to_numpy(float)), np.log(train["realized"].to_numpy(float))
        b, a = np.polyfit(x, y, 1)
        smear = float(np.mean(np.exp(y - (a + b * x))))
        out[rows] = np.exp(a + b * np.log(part.loc[rows, "forecast"].to_numpy(float))) * smear
    return out


def daily_returns(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.Series:
    """Rendements log journaliers de clôture à clôture (bougie 1 h de 23:00), indexés par l'origine (00:00 suivante)."""
    series = v1.load_series(settings, symbol, end)
    closes = series.assign(origin=pd.to_datetime(series["open_time"], utc=True) + v1.STEP).set_index("origin")["close"]
    closes = closes[closes.index == closes.index.floor("D")]
    closes = closes[~closes.index.duplicated()].astype(float)
    full = closes.reindex(pd.date_range(closes.index.min(), closes.index.max(), freq="D"))
    return np.log(full / full.shift(1)).dropna()


def garch_forecasts(returns: pd.Series, origins: pd.DatetimeIndex, horizon: int) -> pd.Series:
    """GARCH(1,1) à moyenne nulle, réestimé chaque mois sur les rendements connus avant le 1er du mois ; à chaque
    origine du mois, filtre jusqu'à l'origine (paramètres fixés) et prévoit la somme des variances des H jours."""
    from arch import arch_model
    out = pd.Series(np.nan, index=origins)
    scaled = returns * 100
    for month in pd.date_range(origins.min().replace(day=1), origins.max(), freq="MS"):
        in_month = origins[(origins >= month) & (origins < month + pd.offsets.MonthBegin(1))]
        history = scaled[scaled.index < month]
        if len(in_month) == 0 or len(history) < GARCH_MIN_DAYS:
            continue
        try:
            fitted = arch_model(history, mean="Zero", vol="GARCH", p=1, q=1, rescale=False).fit(disp="off")
            model = arch_model(scaled[scaled.index <= in_month.max()], mean="Zero", vol="GARCH", p=1, q=1, rescale=False)
            forecast = model.fix(fitted.params).forecast(horizon=horizon, start=in_month.min(), reindex=False)
        except Exception:  # noqa: BLE001 - un mois sans ajustement : pas de prévision (échantillon commun)
            continue
        total = forecast.variance.sum(axis=1) / 1e4
        total.index = pd.DatetimeIndex(total.index)
        out.loc[in_month] = total.reindex(in_month).to_numpy(float)
    return out


def scaled_to_rv(part: pd.DataFrame, raw: np.ndarray, horizon: int) -> np.ndarray:
    """Ramène une prévision à l'échelle de RV² : × moyenne de RV² / prévision sur le passé purgé (réestimée chaque mois)."""
    out = np.full(len(part), np.nan)
    origins = part["origin"]
    ratio_all = part["realized"].to_numpy(float) / raw
    for month in pd.date_range(origins.min().replace(day=1), origins.max(), freq="MS"):
        rows = ((origins >= month) & (origins < month + pd.offsets.MonthBegin(1))).to_numpy()
        known = ((origins + pd.Timedelta(days=horizon) <= month).to_numpy()) & np.isfinite(ratio_all)
        if known.sum() < MIN_FIT_ROWS or not rows.any():
            continue
        out[rows] = raw[rows] * float(np.mean(ratio_all[known]))
    return out


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        source_run: str = SOURCE_RUN) -> v2.Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    result = v2.Result(new_run_id("VOL"), end.isoformat(), round(LEVEL, 6))
    result.n_trials = N_TRIALS
    table = service_forecasts(settings, source_run)
    parts = []
    for horizon in v1.HORIZONS:
        for symbol, part in table[table["horizon"] == horizon].groupby("symbol"):
            say(f"{horizon} j — {symbol}")
            part = part.sort_values("origin").reset_index(drop=True)
            c1 = recalibrated(part, horizon)
            returns = daily_returns(settings, str(symbol), end)
            garch_raw = garch_forecasts(returns, pd.DatetimeIndex(part["origin"]), horizon).to_numpy(float)
            c2 = scaled_to_rv(part, garch_raw, horizon)
            parts.append(part.assign(C1_REETALONNE=c1, C2_GARCH=c2))
    wide = pd.concat(parts, ignore_index=True)
    values = wide[["forecast", *CANDIDATES]].to_numpy(float)
    wide = wide[(np.isfinite(values) & (values > 0)).all(axis=1)].rename(columns={"forecast": REFERENCE})
    rows = []
    for horizon in v1.HORIZONS:
        part = wide[wide["horizon"] == horizon]
        for model in CANDIDATES:
            losses = pd.DataFrame({"symbol": part["symbol"], "origin": part["origin"],
                                   "qlike": v1.qlike(part["realized"], part[model]), "qlike_base": v1.qlike(part["realized"], part[REFERENCE]),
                                   "log_error": v1.log_error(part["realized"], part[model]),
                                   "log_error_base": v1.log_error(part["realized"], part[REFERENCE])})
            row = v2._row(model, horizon, losses)
            rows.append(row)
    result.rows = list(rows)
    result.selected, result.verdict = v2.decide(rows)
    result.coverage = {"source_run": source_run, "rows": {str(h): int((wide["horizon"] == h).sum()) for h in v1.HORIZONS},
                       "pairs": int(wide["symbol"].nunique())}
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": DOC, "models": list(MODELS)}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    wide.to_parquet(report_dir / "forecasts.parquet", index=False)
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=v1.KIND,
        hypothesis="volatilité v4 : un réétalonnage des prévisions en service, ou un GARCH(1,1), prévoit-il mieux que le service ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"candidats figés ({DOC} § 17)",
        params={"source_run": source_run, "candidates": list(CANDIDATES), "level": result.level, "min_fit_rows": MIN_FIT_ROWS,
                "garch_min_days": GARCH_MIN_DAYS},
        period_label="DEVELOPMENT", period_start=v1.FIRST_FORECAST, period_end=result.period_end, universe=sorted(wide["symbol"].unique()),
        data_hashes={}, git_commit=state, dependencies=v1._versions() | dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (erreur de prévision)", simulation_rules={"refit": "le 1er de chaque mois, passé purgé"},
        metrics={"n_trials": N_TRIALS, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(h): m for h, m in result.selected.items()}, "rows": [asdict(r) for r in rows]},
        status="COMPLETED", report_dir=str(report_dir))
    return result
