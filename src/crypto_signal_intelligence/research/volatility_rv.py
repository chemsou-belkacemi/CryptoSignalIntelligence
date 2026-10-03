"""Prévision de volatilité, protocole v5 : HAR-RV sur la volatilité réalisée des bougies de 1 MINUTE (docs/VOLATILITY.md
§ 20, déclaré le 2026-10-03 avant exécution ; mission, phase 10.1).

Candidats `C5_HAR_RV5` (rendements de 5 minutes) et `C1_HAR_RV1` (rendements de 1 minute) : régression groupée de
log RV²_H sur log d, log w, log m (journée, 7 jours, 30 jours), réajustée chaque mois sur le passé purgé ; référence
= le modèle en service (prévisions du run v2) ; règle et niveau du protocole v4. DEVELOPMENT seulement.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from . import volatility as v1
from . import volatility_v2 as v2
from . import volatility_v4 as v4
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .minute_history import load_minutes
from .protocol import development_end

PROTOCOL_VERSION = 5
STRATEGY = "VOLATILITY_FORECAST_V5"
CANDIDATES = {"C5_HAR_RV5": 5, "C1_HAR_RV1": 1}
N_TRIALS = len(CANDIDATES) * len(v1.HORIZONS)
MIN_DAY_COVERAGE = 0.95
WEEK, WEEK_MIN, MONTH, MONTH_MIN = 7, 6, 30, 27
USEFUL, NONE = "MIEUX_QUE_SERVICE", "AUCUNE_AMELIORATION"


def daily_rv(minutes: pd.DataFrame, step: int, *, end: pd.Timestamp | None = None) -> pd.Series:
    """Volatilité réalisée de chaque journée UTC complète (≥ 95 % des minutes) : somme des carrés des rendements log
    entre clôtures de blocs de `step` minutes ; un rendement appartient à la journée où son bloc s'ouvre. NaN pour une
    journée incomplète. `end` : aucune minute ouverte à `end` ou après n'est lue."""
    times = pd.to_datetime(minutes["open_time"], utc=True)
    keep = times < end if end is not None else np.ones(len(times), dtype=bool)
    times, close = times[keep], minutes["close"].to_numpy(float)[np.asarray(keep)]
    if len(times) == 0:
        return pd.Series(dtype=float)
    day = times.dt.floor("D")
    counts = pd.Series(1, index=day.to_numpy()).groupby(level=0).size()
    block = times.dt.floor(f"{step}min")
    closes = pd.Series(close, index=block.to_numpy()).groupby(level=0).last()
    returns = np.log(closes).diff().dropna()
    squares = (returns ** 2).groupby(pd.DatetimeIndex(returns.index).floor("D")).sum()
    full = pd.date_range(counts.index.min(), counts.index.max(), freq="D", tz="UTC")
    rv = squares.reindex(full)
    complete = counts.reindex(full).fillna(0) >= MIN_DAY_COVERAGE * 1440
    return rv.where(complete.to_numpy())


def har_features(rv: pd.Series) -> pd.DataFrame:
    """Variables à l'origine T (00:00 UTC) : journée T − 1, moyennes des 7 et 30 dernières journées (au moins 6 et 27
    complètes). Index : l'origine."""
    d = rv
    w = rv.rolling(WEEK, min_periods=WEEK_MIN).mean()
    m = rv.rolling(MONTH, min_periods=MONTH_MIN).mean()
    frame = pd.DataFrame({"d": d, "w": w, "m": m})
    frame.index = frame.index + pd.Timedelta(days=1)                 # connue à minuit, origine du jour suivant
    return frame


def candidate_forecasts(part: pd.DataFrame, horizon: int, columns: list[str]) -> np.ndarray:
    """Régression groupée log RV²_H ~ log d, log w, log m, réajustée le 1er de chaque mois sur les lignes dont la cible
    est terminée (origine + H jours ≤ réajustement)."""
    out = np.full(len(part), np.nan)
    origins = part["origin"]
    X_all = np.log(part[columns].to_numpy(float))
    y_all = np.log(part["realized"].to_numpy(float))
    ok = np.isfinite(X_all).all(axis=1) & np.isfinite(y_all)
    for month in pd.date_range(origins.min().replace(day=1), origins.max(), freq="MS"):
        rows = ((origins >= month) & (origins < month + pd.offsets.MonthBegin(1))).to_numpy() & np.isfinite(X_all).all(axis=1)
        train = ok & (origins + pd.Timedelta(days=horizon) <= month).to_numpy()
        if not rows.any():
            continue
        model = v1.fit_har(X_all[train], y_all[train])
        if model is not None:
            out[rows] = model.predict(X_all[rows])
    return out


def decide(rows: list) -> tuple[dict[int, str], str]:
    selected = {}
    for horizon in v1.HORIZONS:
        useful = [r for r in rows if r.horizon_days == horizon and r.useful and r.qlike is not None]
        best = min(useful, key=lambda r: (r.qlike or 0.0, list(CANDIDATES).index(r.model)), default=None)
        selected[horizon] = best.model if best is not None else NONE
    return selected, USEFUL if any(m != NONE for m in selected.values()) else NONE


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        source_run: str = v4.SOURCE_RUN) -> v2.Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    result = v2.Result(new_run_id("VOL"), end.isoformat(), round(v2.LEVEL, 6))
    result.n_trials = N_TRIALS
    table = v4.service_forecasts(settings, source_run)
    features: dict[tuple[str, str], pd.DataFrame] = {}
    missing = []
    for symbol in sorted(table["symbol"].unique()):
        say(f"minutes {symbol}")
        try:
            minutes = load_minutes(settings, symbol)
        except MissingData:
            missing.append(symbol)
            continue
        for name, step in CANDIDATES.items():
            features[(symbol, name)] = har_features(daily_rv(minutes, step, end=end.ceil("D")))
    parts = []
    for horizon in v1.HORIZONS:
        say(f"{horizon} j")
        part = table[(table["horizon"] == horizon) & ~table["symbol"].isin(missing)].copy()
        for name in CANDIDATES:
            cols = [f"{name}_{c}" for c in ("d", "w", "m")]
            merged = []
            for symbol, group in part.groupby("symbol"):
                f = features[(str(symbol), name)].rename(columns=lambda c, n=name: f"{n}_{c}")
                merged.append(group.join(f, on="origin"))
            part = pd.concat(merged).sort_values(["origin", "symbol"]).reset_index(drop=True)
            part[name] = candidate_forecasts(part, horizon, cols)
        parts.append(part)
    wide = pd.concat(parts, ignore_index=True)
    values = wide[["forecast", *CANDIDATES]].to_numpy(float)
    wide = wide[(np.isfinite(values) & (values > 0)).all(axis=1)].rename(columns={"forecast": v4.REFERENCE})
    rows = []
    for horizon in v1.HORIZONS:
        part = wide[wide["horizon"] == horizon]
        for model in CANDIDATES:
            losses = pd.DataFrame({"symbol": part["symbol"], "origin": part["origin"],
                                   "qlike": v1.qlike(part["realized"], part[model]),
                                   "qlike_base": v1.qlike(part["realized"], part[v4.REFERENCE]),
                                   "log_error": v1.log_error(part["realized"], part[model]),
                                   "log_error_base": v1.log_error(part["realized"], part[v4.REFERENCE])})
            rows.append(v2._row(model, horizon, losses))
    result.rows = list(rows)
    result.selected, result.verdict = decide(rows)
    result.coverage = {"source_run": source_run, "missing_minutes": missing,
                       "rows": {str(h): int((wide["horizon"] == h).sum()) for h in v1.HORIZONS},
                       "pairs": int(wide["symbol"].nunique())}
    registry = ExperimentRegistry(settings.experiments_db)
    result.program_trials = registry.program_trials() + N_TRIALS
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": "docs/VOLATILITY.md § 20", "models": list(CANDIDATES)}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    wide.to_parquet(report_dir / "forecasts.parquet", index=False)
    registry.record(
        run_id=result.run_id, created_at=now.isoformat(), kind=v1.KIND,
        hypothesis="volatilité v5 : un HAR sur la volatilité réalisée des bougies de 1 minute (5 min, 1 min) prévoit-il mieux "
                   "que le service ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="candidats figés (docs/VOLATILITY.md § 20)",
        params={"source_run": source_run, "candidates": CANDIDATES, "level": result.level, "min_day_coverage": MIN_DAY_COVERAGE,
                "week": [WEEK, WEEK_MIN], "month": [MONTH, MONTH_MIN]},
        period_label="DEVELOPMENT", period_start=v1.FIRST_FORECAST, period_end=result.period_end,
        universe=sorted(wide["symbol"].unique()), data_hashes={}, git_commit=state,
        dependencies=v1._versions() | dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (erreur de prévision)", simulation_rules={"refit": "le 1er de chaque mois, passé purgé"},
        metrics={"n_trials": N_TRIALS, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(h): m for h, m in result.selected.items()}, "rows": [asdict(r) for r in rows]},
        status="COMPLETED", report_dir=str(report_dir))
    return result
