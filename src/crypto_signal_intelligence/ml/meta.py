"""Méta-labeling hors échantillon (lot 5) : le modèle sait-il trier les setups d'une stratégie ?

Protocole, fixé AVANT la première exécution (docs/ML.md) :
- jeu de données : setups de la stratégie (paramètres v1 déclarés, coûts centraux) sur DEVELOPMENT ;
  seuls les trades remplis et CLOS comptent ; étiquette = R net > 0 ;
- variables : uniquement des valeurs du tableau de décisions à l'instant de décision (causal, mêmes
  jointures `available_at` que la stratégie) et la géométrie du trade (stop en ATR, objectif en R) ;
- validation : fenêtres du walk-forward (ancrées) ; entraînement sur les trades dont le setup ET la
  sortie précèdent le début du test (purge : aucune étiquette d'entraînement ne chevauche le test) ;
  standardisation calculée sur l'entraînement seul ;
- règle de filtrage SANS réglage : garder un trade si la probabilité prédite dépasse le taux de gain de
  l'entraînement (« mieux que la moyenne ») : aucun seuil optimisé, un seul essai par stratégie ;
- verdict (agrégat hors échantillon, IC95 par blocs de 10 jours) : USEFUL_OOS seulement si
  1. au moins 300 trades testés et 3 fenêtres ; 2. score de Brier meilleur que le taux de base
  (IC de l'écart > 0) ; 3. E[R] des trades gardés > 0 (IC > 0) ; 4. gain sur l'ensemble des trades
  (IC de l'écart > 0). Sinon NOT_USEFUL (ou INSUFFICIENT_DATA si 1 échoue).
Un verdict USEFUL_OOS ne branche rien : ML_PROBABILITY reste NONE jusqu'à promotion explicite.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import CLOSED, day_block_ci95
from ..config import Settings
from ..research.backtest_run import FrameCache, run_context, simulate_universe
from ..research.experiments import ExperimentRegistry, new_run_id
from ..research.protocol import period as resolve_period
from ..research.walk_forward import Window, make_windows
from ..strategies import registry
from .logistic import LogisticModel, auc, fit_logistic

FEATURES = (
    "atr_pct", "rsi", "dist_ema20", "dist_ema50", "log_volume_ratio", "ret_1", "ret_4", "realized_vol_96",
    "bb_z", "dist_donchian_high", "ctx_ema50_slope", "ctx_atr_pct", "ctx_ret_24h", "ctx_dist_ema50",
    "btc_ret_24h", "btc_atr_pct", "btc_ema50_slope", "trend_bull", "trend_bear", "vol_high", "vol_low",
    "hour_sin", "hour_cos", "stop_atr", "target_r",
)
MIN_TEST_TRADES = 300
MIN_WINDOWS = 3
BLOCK_DAYS = 10
L2 = 1.0


def _ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.where(b.abs() > 1e-12) - 1


def features_at_decision(frame: pd.DataFrame) -> pd.DataFrame:
    """Variables sans dimension calculées ligne à ligne sur le tableau de décisions (déjà causal)."""
    close = frame["close"]
    hours = frame["decision_time"].dt.hour + frame["decision_time"].dt.minute / 60
    sigma = frame["bb_sigma"].where(frame["bb_sigma"] > 1e-12)
    return pd.DataFrame({
        "decision_time": frame["decision_time"],
        "atr_pct": frame["atr14"] / close,
        "rsi": frame["rsi14"] / 100,
        "dist_ema20": _ratio(close, frame["ema20"]),
        "dist_ema50": _ratio(close, frame["ema50"]),
        "log_volume_ratio": np.log1p(frame["volume_ratio"].clip(lower=0)),
        "ret_1": frame["ret_1"], "ret_4": frame["ret_4"], "realized_vol_96": frame["realized_vol_96"],
        "bb_z": (close - frame["bb_mid"]) / sigma,
        "dist_donchian_high": _ratio(close, frame["donchian_high"]),
        "ctx_ema50_slope": frame["ctx_ema50_slope"], "ctx_atr_pct": frame["ctx_atr_pct"],
        "ctx_ret_24h": frame["ctx_ret_24h"], "ctx_dist_ema50": _ratio(frame["ctx_close"], frame["ctx_ema50"]),
        "btc_ret_24h": frame["btc_ret_24h"], "btc_atr_pct": frame["btc_atr_pct"],
        "btc_ema50_slope": frame["btc_ema50_slope"],
        "trend_bull": (frame["ctx_trend"] == "BULL").astype(float),
        "trend_bear": (frame["ctx_trend"] == "BEAR").astype(float),
        "vol_high": (frame["ctx_volatility"] == "HIGH").astype(float),
        "vol_low": (frame["ctx_volatility"] == "LOW").astype(float),
        "hour_sin": np.sin(2 * np.pi * hours / 24), "hour_cos": np.cos(2 * np.pi * hours / 24),
    })


def label_trades(trades: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Trades remplis et clos d'une paire + variables à leur instant de décision ; lignes incomplètes retirées."""
    if trades.empty:
        return pd.DataFrame()
    filled = trades[trades["entry_status"].isin(["FILLED_OPEN", "FILLED_TOUCH"]) & trades["exit_reason"].isin(CLOSED)]
    if filled.empty:
        return pd.DataFrame()
    filled = filled.assign(setup_time=pd.to_datetime(filled["setup_time"], utc=True),
                           exit_time=pd.to_datetime(filled["exit_time"], utc=True))
    joined = filled.merge(features_at_decision(frame), left_on="setup_time", right_on="decision_time", how="left")
    risk = joined["entry_limit"] - joined["stop"]
    atr = joined["atr_pct"] * joined["entry_limit"]
    joined["stop_atr"] = risk / atr.where(atr > 0)
    joined["target_r"] = (joined["target"] - joined["entry_limit"]) / risk.where(risk > 0)
    joined["symbol"] = symbol
    joined["label"] = (joined["r_multiple"] > 0).astype(float)
    data = joined.replace([np.inf, -np.inf], np.nan).dropna(subset=list(FEATURES) + ["r_multiple"])
    return data[["symbol", "setup_time", "exit_time", "r_multiple", "label", *FEATURES]].reset_index(drop=True)


@dataclass
class MetaResult:
    run_id: str
    strategy: str
    verdict: str
    criteria: list[dict]
    windows: list[dict]
    oos: dict
    dropped_rows: int
    report_dir: Path
    predictions: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    last_model: LogisticModel | None = field(repr=False, default=None)


def _ci(values: np.ndarray, times: np.ndarray, settings: Settings) -> tuple[float, float] | None:
    return day_block_ci95(values, times, block_days=BLOCK_DAYS, samples=settings.protocol.bootstrap_samples,
                          seed=settings.protocol.seed)[0]


def evaluate_windows(data: pd.DataFrame, windows: list[Window], settings: Settings) -> tuple[pd.DataFrame, list[dict],
                                                                                              LogisticModel | None]:
    """Prédictions hors échantillon fenêtre par fenêtre (entraînement purgé sur le passé seul)."""
    parts, rows, model = [], [], None
    for w in windows:
        start, end = pd.Timestamp(w.test_start), pd.Timestamp(w.test_end)
        train = data[(data["setup_time"] < start) & (data["exit_time"] < start)]
        test = data[(data["setup_time"] >= start) & (data["setup_time"] <= end)]
        row = {"index": w.index, "test_start": w.test_start.isoformat(), "test_end": w.test_end.isoformat(),
               "train_trades": len(train), "test_trades": len(test)}
        if len(train) < 50 or train["label"].nunique() < 2 or test.empty:
            rows.append(row | {"skipped": "entraînement insuffisant (< 50 trades ou une seule classe) ou test vide"})
            continue
        model = fit_logistic(train[list(FEATURES)].to_numpy(), train["label"].to_numpy(), FEATURES, l2=L2)
        base = float(train["label"].mean())
        p = model.predict_proba(test[list(FEATURES)].to_numpy())
        parts.append(test.assign(p=p, base=base, keep=p > base, window=w.index))
        rows.append(row | {"train_win_rate": round(base, 4), "test_auc": auc(test["label"].to_numpy(), p),
                           "kept_share": round(float((p > base).mean()), 4)})
    predictions = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return predictions, rows, model


def judge(predictions: pd.DataFrame, windows: list[dict], settings: Settings) -> tuple[str, list[dict], dict]:
    """Critères déclarés (docstring du module) sur l'agrégat hors échantillon."""
    tested = [w for w in windows if "skipped" not in w and w["test_trades"] > 0]
    n = len(predictions)
    criteria = [{"number": 1, "label": f">= {MIN_TEST_TRADES} trades testés et >= {MIN_WINDOWS} fenêtres",
                 "passed": n >= MIN_TEST_TRADES and len(tested) >= MIN_WINDOWS,
                 "detail": f"{n} trades, {len(tested)} fenêtre(s)"}]
    oos: dict = {"trades": n, "windows": len(tested)}
    if n == 0:
        for number, label in ((2, "Brier meilleur que le taux de base"), (3, "E[R] des trades gardés > 0"),
                              (4, "Gain sur l'ensemble des trades")):
            criteria.append({"number": number, "label": label, "passed": False, "detail": "aucune prédiction"})
        return "INSUFFICIENT_DATA", criteria, oos
    y, p, base = (predictions[c].to_numpy(float) for c in ("label", "p", "base"))
    r, keep = predictions["r_multiple"].to_numpy(float), predictions["keep"].to_numpy(bool)
    times = predictions["setup_time"].to_numpy()
    skill = (y - base) ** 2 - (y - p) ** 2                 # > 0 : le modèle fait mieux que le taux de base
    share = float(keep.mean())
    uplift = r * (keep / share - 1) if share > 0 else np.zeros_like(r)   # moyenne = E[R] gardés − E[R] tous
    skill_ci, uplift_ci = _ci(skill, times, settings), _ci(uplift, times, settings)
    kept_ci = _ci(r[keep], times[keep], settings) if keep.any() else None
    bins = pd.cut(p, bins=np.linspace(0, 1, 11), include_lowest=True)
    calibration = (pd.DataFrame({"bin": bins.astype(str), "p": p, "y": y}).groupby("bin", observed=True)
                   .agg(trades=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).round(4)
                   .reset_index().to_dict("records"))
    oos |= {
        "auc": auc(y, p), "brier_model": round(float(((y - p) ** 2).mean()), 5),
        "brier_base_rate": round(float(((y - base) ** 2).mean()), 5),
        "brier_skill_mean": round(float(skill.mean()), 6), "brier_skill_ci95": skill_ci,
        "win_rate_all": round(float(y.mean()), 4), "expectancy_r_all": round(float(r.mean()), 4),
        "kept_share": round(share, 4), "expectancy_r_kept": round(float(r[keep].mean()), 4) if keep.any() else None,
        "expectancy_r_kept_ci95": kept_ci, "uplift_r": round(float(uplift.mean()), 4), "uplift_r_ci95": uplift_ci,
        "calibration": calibration,
    }
    criteria += [
        {"number": 2, "label": "Brier meilleur que le taux de base (IC95 de l'écart > 0)",
         "passed": skill_ci is not None and skill_ci[0] > 0,
         "detail": f"écart moyen {oos['brier_skill_mean']}, IC95 {skill_ci}"},
        {"number": 3, "label": "E[R] des trades gardés > 0 (IC95 > 0, coûts centraux)",
         "passed": kept_ci is not None and kept_ci[0] > 0,
         "detail": f"E[R] gardés {oos['expectancy_r_kept']} ({share:.0%} gardés), IC95 {kept_ci}"},
        {"number": 4, "label": "Gain sur l'ensemble des trades (IC95 de l'écart > 0)",
         "passed": uplift_ci is not None and uplift_ci[0] > 0,
         "detail": f"E[R] tous {oos['expectancy_r_all']} → gardés {oos['expectancy_r_kept']}, écart "
                   f"{oos['uplift_r']}, IC95 {uplift_ci}"},
    ]
    if not criteria[0]["passed"]:
        verdict = "INSUFFICIENT_DATA"
    elif all(c["passed"] for c in criteria):
        verdict = "USEFUL_OOS"
    else:
        verdict = "NOT_USEFUL"
    return verdict, criteria, oos


def run(settings: Settings, strategy_id: str, *, now: datetime, progress=None) -> MetaResult:
    say = progress or (lambda _text: None)
    period = resolve_period(settings, "development", now=now)
    config = settings.walk_forward
    windows = make_windows(period.start, period.end, train_months=config.train_min_months,
                           test_months=config.test_months)
    strategy = registry.build(strategy_id, settings.strategies)
    say("données")
    inputs, common = run_context(settings)
    frames = FrameCache(settings, inputs)
    say("simulation des setups (paramètres v1, coûts centraux)")
    universe = simulate_universe(settings, strategy, frames, "central", start=period.start, end=period.end,
                                 progress=lambda s: say(f"simulation {s}"))
    parts, raw_rows = [], 0
    trades = universe.trades
    for symbol in settings.data.symbols:
        mine = trades[trades["symbol"] == symbol] if not trades.empty else trades
        raw_rows += int((mine["entry_status"].isin(["FILLED_OPEN", "FILLED_TOUCH"])
                         & mine["exit_reason"].isin(CLOSED)).sum()) if not mine.empty else 0
        labelled = label_trades(mine, frames.get(symbol, strategy), symbol)
        if not labelled.empty:
            parts.append(labelled)
    data = pd.concat(parts, ignore_index=True).sort_values("setup_time") if parts else pd.DataFrame()
    say("validation hors échantillon")
    predictions, window_rows, model = (evaluate_windows(data, windows, settings) if not data.empty
                                       else (pd.DataFrame(), [], None))
    verdict, criteria, oos = judge(predictions, window_rows, settings)
    run_id = new_run_id("ML")
    result = MetaResult(run_id, strategy_id, verdict, criteria, window_rows, oos, raw_rows - len(data),
                        settings.reports_dir / run_id, predictions, model)
    experiments = ExperimentRegistry(settings.experiments_db)
    program_trials = experiments.program_trials(period.label) + 1
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind="ML_META",
        hypothesis=f"un modèle logistique sur les variables de décision trie les setups de {strategy_id}",
        strategy=strategy_id, strategy_version=strategy.version, variant="logistique L2, règle p > taux d'entraînement",
        params={"features": list(FEATURES), "l2": L2, "rule": "p > train win rate"},
        period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
        cost_scenario="central", simulation_rules={"walk_forward": config.model_dump(), "purge": "setup et sortie < test"},
        metrics={"verdict": verdict, "n_trials": 1, "program_trials": program_trials, "criteria": criteria,
                 "oos": {k: v for k, v in oos.items() if k != "calibration"}},
        status="COMPLETED", report_dir=str(result.report_dir), **common)
    _write(result, program_trials)
    return result


def _write(result: MetaResult, program_trials: int) -> None:
    result.report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": result.run_id, "strategy": result.strategy, "verdict": result.verdict,
               "criteria": result.criteria, "windows": result.windows, "oos": result.oos,
               "dropped_rows_incomplete_features": result.dropped_rows, "program_trials": program_trials,
               "model_last_window": result.last_model.to_dict() if result.last_model else None}
    (result.report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                                    encoding="utf-8")
    if not result.predictions.empty:
        result.predictions[["symbol", "setup_time", "window", "p", "base", "keep", "label", "r_multiple"]].to_csv(
            result.report_dir / "predictions_oos.csv", index=False)
    lines = [f"# Méta-labeling {result.strategy} — {result.run_id}", "",
             f"**Verdict : {result.verdict}** (aucune influence sur les signaux sans promotion explicite)", "",
             "| # | Critère | OK | Détail |", "|---|---|---|---|"]
    lines += [f"| {c['number']} | {c['label']} | {'oui' if c['passed'] else 'non'} | {c['detail']} |"
              for c in result.criteria]
    lines += ["", "La probabilité affichée est la fréquence prédite de « R net > 0 » pour un setup de cette "
              "stratégie, apprise sur le passé ; sa calibration (tableau summary.json) dit si elle est fiable."]
    (result.report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
