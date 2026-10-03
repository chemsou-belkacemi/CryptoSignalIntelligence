"""Contrôle positif de l'AJUSTEMENT (docs/POSITIVE_CONTROL.md § 7, déclaré le 2026-10-03 avant exécution). Point 4 du
plan de travail : le contrôle positif v1 testait la MESURE (un score planté est-il vu ?) ; celui-ci teste l'APPRENTISSAGE
(un modèle, avec nos réglages et notre entraînement glissant, retrouve-t-il une variable faiblement informative cachée
parmi les vraies variables ?).

Données : lignes journalières du lot 7 (`research/volatility.daily_frame`, 40 paires, DEVELOPMENT) ; cible = rendement
log à 7 jours moins la moyenne de toutes les paires au même instant (excès). Variables : les sept du lot 7 + une
variable plantée v = ρ · z(cible) + √(1 − ρ²) · ε (z : cible standardisée à l'instant). Modèles aux réglages déjà
utilisés : LightGBM (15 feuilles, 0,05, 200 lignes par feuille, 300 arbres) et régression linéaire de référence ;
réajustement le 1er de chaque mois sur le passé purgé (cible connue avant la date). Mesure : corrélation de rang
quotidienne hors échantillon entre la prévision et la cible, IC calendaire. 0 essai : avantage synthétique.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import volatility as v1
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .intervals import calendar_mean_ci
from .positive_control import daily_rank_ic
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION, N_TRIALS = "CONTROL", "FIT_CONTROL", 1, 0
HORIZON_DAYS = 7
RHOS = (0.0, 0.02, 0.05, 0.10)
FIRST_FORECAST = pd.Timestamp("2020-01-01", tz="UTC")
MIN_TRAIN = 2000
MODELS = ("lightgbm", "lineaire")
BLOCK_DAYS = 14


@dataclass
class Result:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    rows: list[dict] = field(default_factory=list)


def target_table(frames: dict[str, pd.DataFrame], closes: dict[str, pd.Series], horizon: int = HORIZON_DAYS) -> pd.DataFrame:
    """Lignes complètes du lot 7 + cible : rendement log de la clôture à l'origine à celle H jours plus tard, moins
    la moyenne des paires au même instant ; `target_end` = moment où la cible est connue."""
    parts = []
    for symbol, frame in frames.items():
        rows = frame[v1.complete_rows(frame).to_numpy()][["origin", *v1.FEATURES]].copy()
        close = closes[symbol]
        now_c = close.reindex(rows["origin"]).to_numpy(float)
        later = close.reindex(rows["origin"] + pd.Timedelta(days=horizon)).to_numpy(float)
        rows["ret"] = np.log(later / now_c)
        rows["symbol"] = symbol
        parts.append(rows[np.isfinite(rows["ret"].to_numpy())])
    data = pd.concat(parts, ignore_index=True)
    counts = data.groupby("origin")["ret"].transform("size")
    data = data[counts >= 5].copy()
    data["target"] = data["ret"] - data.groupby("origin")["ret"].transform("mean")
    data["target_end"] = data["origin"] + pd.Timedelta(days=horizon)
    return data.sort_values(["origin", "symbol"]).reset_index(drop=True)


def plant(data: pd.DataFrame, rho: float, rng: np.random.Generator) -> np.ndarray:
    grouped = data.groupby("origin")["target"]
    z = ((data["target"] - grouped.transform("mean")) / grouped.transform("std").replace(0, np.nan)).fillna(0.0).to_numpy(float)
    return rho * z + np.sqrt(1 - rho ** 2) * rng.standard_normal(len(data))


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str], model: str, seed: int) -> np.ndarray:
    X, y = train[features].to_numpy(float), train["target"].to_numpy(float)
    if model == "lineaire":
        design = np.column_stack([np.ones(len(X)), X])
        coef = np.linalg.lstsq(design, y, rcond=None)[0]
        return np.column_stack([np.ones(len(test)), test[features].to_numpy(float)]) @ coef
    import lightgbm
    params = v1.LGBM_PARAMS | {"seed": seed, "num_threads": v1.THREADS, "verbose": -1, "deterministic": True, "force_col_wise": True}
    booster = lightgbm.train(params, lightgbm.Dataset(X, label=y), num_boost_round=v1.LGBM_ROUNDS)
    return np.asarray(booster.predict(test[features].to_numpy(float)), float)


def walk_forward(data: pd.DataFrame, features: list[str], model: str, *, seed: int, end: pd.Timestamp) -> pd.DataFrame:
    """Réajustement mensuel sur les lignes dont la cible est connue avant la date (purge), prévisions du mois."""
    out = []
    months = pd.date_range(FIRST_FORECAST, data["origin"].max(), freq="MS")
    for refit in months:
        train = data[data["target_end"] <= refit]
        test = data[(data["origin"] >= refit) & (data["origin"] < refit + pd.offsets.MonthBegin(1)) & (data["target_end"] <= end)]
        if len(train) < MIN_TRAIN or test.empty:
            continue
        out.append(test[["origin", "symbol", "target"]].assign(prediction=fit_predict(train, test, features, model, seed)))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["origin", "symbol", "target", "prediction"])


def information(predictions: pd.DataFrame) -> dict:
    table = predictions.rename(columns={"origin": "time", "target": "excess"})
    ic = daily_rank_ic(table, "prediction")
    ci = calendar_mean_ci(ic.to_numpy(float), ic.index, block_days=BLOCK_DAYS, level=0.95)
    return {"days": int(len(ic)), "rank_ic": round(float(ic.mean()), 5), "ci95": ci, "detected": bool(ci is not None and ci[0] > 0)}


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, rhos: tuple = RHOS) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("CTRL"), end.isoformat())
    symbols = list(symbols or RESEARCH_UNIVERSE)
    market = v1.load_series(settings, MARKET, end)
    frames, closes = {}, {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else v1.load_series(settings, symbol, end)
        frames[symbol] = v1.daily_frame(series, market)
        stamped = series.assign(origin=pd.to_datetime(series["open_time"], utc=True) + v1.STEP)
        closes[symbol] = stamped.set_index("origin")["close"].astype(float)
        closes[symbol] = closes[symbol][~closes[symbol].index.duplicated()]
    data = target_table(frames, closes)
    features = list(v1.FEATURES)
    for rho in rhos:
        rng = np.random.default_rng([settings.protocol.seed, int(rho * 1000)])
        data["planted"] = plant(data, rho, rng)
        planted_info = information(data[data["origin"] >= FIRST_FORECAST].rename(columns={"planted": "prediction"})[["origin", "symbol", "target", "prediction"]])
        for model in MODELS:
            say(f"ρ = {rho}, {model}")
            predictions = walk_forward(data, [*features, "planted"], model, seed=settings.protocol.seed, end=end)
            result.rows.append({"rho": rho, "model": model, "planted_alone": planted_info, "oos": information(predictions),
                                "predictions": int(len(predictions))})
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(json.dumps(asdict(result) | {"doc": "docs/POSITIVE_CONTROL.md"}, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="contrôle positif de l'ajustement : LightGBM et une régression linéaire retrouvent-ils une variable faiblement informative "
                   "plantée parmi les variables du lot 7 ? (aucune hypothèse de marché)",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="avantage planté figé (§ 7)",
        params={"horizon_days": HORIZON_DAYS, "rhos": list(RHOS), "models": list(MODELS), "lgbm": v1.LGBM_PARAMS | {"rounds": v1.LGBM_ROUNDS},
                "first_forecast": str(FIRST_FORECAST)[:10], "block_days": BLOCK_DAYS},
        period_label="DEVELOPMENT", period_start=str(FIRST_FORECAST)[:10], period_end=result.period_end, universe=symbols, data_hashes={},
        git_commit=state, dependencies=dependency_versions(), seed=settings.protocol.seed, cost_scenario="aucun",
        simulation_rules={"refit": "mensuel, purge sur la fin de la cible"},
        metrics={"n_trials": N_TRIALS, "rows": [{k: v for k, v in r.items()} for r in result.rows]}, status="COMPLETED", report_dir=str(report_dir))
    return result
