"""Méta-labeling hors échantillon (lot 5) : le modèle sait-il trier les setups d'une stratégie ?

Protocole déclaré avant toute exécution sur données réelles (docs/ML.md ; version 2 du 2026-10-01,
renforcée après relecture « leak-auditor » et AVANT qu'aucun résultat n'ait été produit) :
- jeu de données : setups de la stratégie (paramètres v1 déclarés, coûts centraux) sur DEVELOPMENT ;
  seuls les trades remplis et CLOS comptent ; étiquette = R net > 0 ;
- variables : uniquement des valeurs du tableau de décisions à l'instant de décision (causal, mêmes
  jointures `available_at` et même masque « BTC périmé » que la stratégie) et la géométrie du trade ;
- validation : fenêtres du walk-forward (ancrées) ; entraînement sur les trades dont le setup ET la
  sortie précèdent le début du test (purge) ; standardisation et taux de base sur l'entraînement seul ;
  une fenêtre n'est jugée que si sa classe minoritaire compte au moins 10 trades par variable ;
- règle SANS réglage : garder un setup si la probabilité prédite dépasse le taux de gain de
  l'entraînement ; un seul essai par stratégie ;
- stratégie filtrée = la stratégie RE-SIMULÉE avec le modèle de chaque fenêtre comme veto, au même
  endroit que la surveillance (un setup refusé libère la paire pour le suivant) ;
- verdict, sur l'agrégat hors échantillon (IC95 par blocs de 10 jours) : USEFUL_OOS seulement si
  1. données intègres et causalité vérifiée (même contrôle que le walk-forward) ;
  2. au moins 300 setups testés et 3 fenêtres jugées ;
  3. score de Brier meilleur que le taux de base (IC de l'écart > 0) ;
  4. les setups gardés font mieux que l'ensemble (IC de l'écart > 0) ;
  5. stratégie filtrée : E[R] > 0 en coûts centraux, IC > 0 ;
  6. stratégie filtrée : E[R] > 0 en coûts défavorables ;
  7. stratégie filtrée : aucune paire ni année > `admission.max_group_pnl_share` du PnL en R.
  Sinon NOT_USEFUL (INSUFFICIENT_DATA si 2 échoue).
Réserve permanente : ces données ont déjà servi à juger ces stratégies (pseudo hors échantillon) ;
un USEFUL_OOS exige une confirmation sur des données non vues avant toute promotion.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import CLOSED, day_block_ci95
from ..backtest.simulator import simulate, trades_frame
from ..config import Settings
from ..domain.market import MAX_CONTEXT_AGE
from ..levels.engine import TradeLevels
from ..research.admission import pnl_shares
from ..research.backtest_run import FrameCache, run_context, simulate_universe, simulation_rules
from ..research.experiments import ExperimentRegistry, new_run_id
from ..research.protocol import period as resolve_period
from ..research.walk_forward import Window, check_integrity, make_windows
from ..strategies import registry
from ..strategies.base import Strategy
from .logistic import LogisticModel, auc, fit_logistic

PROTOCOL_VERSION = 2
FEATURES = (
    "atr_pct", "rsi", "dist_ema20", "dist_ema50", "log_volume_ratio", "ret_1", "ret_4", "realized_vol_96",
    "bb_z", "dist_donchian_high", "ctx_ema50_slope", "ctx_atr_pct", "ctx_ret_24h", "ctx_dist_ema50",
    "btc_ret_24h", "btc_atr_pct", "btc_ema50_slope", "trend_bull", "trend_bear", "vol_high", "vol_low",
    "hour_sin", "hour_cos", "stop_atr", "target_r",
)
BTC_FEATURES = ("btc_ret_24h", "btc_atr_pct", "btc_ema50_slope")
MIN_TEST_TRADES = 300
MIN_WINDOWS = 3
MIN_MINORITY_PER_FEATURE = 10
BLOCK_DAYS = 10
L2 = 1.0
MAX_ITER = 100
FILLED = ("FILLED_OPEN", "FILLED_TOUCH")


def _ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.where(b.abs() > 1e-12) - 1


def features_at_decision(frame: pd.DataFrame) -> pd.DataFrame:
    """Variables sans dimension calculées ligne à ligne sur le tableau de décisions (déjà causal)."""
    close = frame["close"]
    hours = frame["decision_time"].dt.hour + frame["decision_time"].dt.minute / 60
    sigma = frame["bb_sigma"].where(frame["bb_sigma"] > 1e-12)
    out = pd.DataFrame({
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
    if "btc_available_at" in frame and "available_at" in frame:
        # Même règle que la stratégie (features/context.py) : un contexte BTC trop ancien est inconnu.
        stale = (frame["btc_available_at"].isna()
                 | ((frame["available_at"] - frame["btc_available_at"]) > MAX_CONTEXT_AGE)).to_numpy()
        out.loc[stale, list(BTC_FEATURES)] = np.nan
    return out


def _geometry(entry: pd.Series, stop: pd.Series, target: pd.Series, atr_pct: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Stop en ATR et objectif en R (séries) ; division par zéro → valeur absente."""
    risk = entry - stop
    atr = atr_pct * entry
    return risk / atr.where(atr > 0), (target - entry) / risk.where(risk > 0)


def _geometry_one(entry: float, stop: float, target: float, atr_pct: float) -> tuple[float, float]:
    """Même calcul pour un setup isolé (veto du simulateur)."""
    risk, atr = entry - stop, atr_pct * entry
    return (risk / atr if atr > 0 else float("nan")), ((target - entry) / risk if risk > 0 else float("nan"))


def label_trades(trades: pd.DataFrame, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Trades remplis et clos d'une paire + variables à leur instant de DÉCISION ; lignes incomplètes retirées."""
    if trades.empty:
        return pd.DataFrame()
    filled = trades[trades["entry_status"].isin(FILLED) & trades["exit_reason"].isin(CLOSED)]
    if filled.empty:
        return pd.DataFrame()
    filled = filled.assign(setup_time=pd.to_datetime(filled["setup_time"], utc=True),
                           exit_time=pd.to_datetime(filled["exit_time"], utc=True))
    joined = filled.merge(features_at_decision(frame), left_on="setup_time", right_on="decision_time", how="left")
    joined["stop_atr"], joined["target_r"] = _geometry(joined["entry_limit"], joined["stop"], joined["target"],
                                                       joined["atr_pct"])
    joined["symbol"] = symbol
    joined["label"] = (joined["r_multiple"] > 0).astype(float)
    data = joined.replace([np.inf, -np.inf], np.nan).dropna(subset=list(FEATURES) + ["r_multiple"])
    return data[["symbol", "setup_time", "exit_time", "r_multiple", "label", *FEATURES]].reset_index(drop=True)


@dataclass(frozen=True)
class WindowModel:
    window: Window
    model: LogisticModel
    base: float


def evaluate_windows(data: pd.DataFrame, windows: list[Window], settings: Settings) -> tuple[pd.DataFrame, list[dict],
                                                                                              list[WindowModel]]:
    """Prédictions hors échantillon fenêtre par fenêtre (entraînement purgé sur le passé seul)."""
    parts, rows, models = [], [], []
    minimum = MIN_MINORITY_PER_FEATURE * len(FEATURES)
    for w in windows:
        start, end = pd.Timestamp(w.test_start), pd.Timestamp(w.test_end)
        train = data[(data["setup_time"] < start) & (data["exit_time"] < start)]
        test = data[(data["setup_time"] >= start) & (data["setup_time"] <= end)]
        row = {"index": w.index, "test_start": w.test_start.isoformat(), "test_end": w.test_end.isoformat(),
               "train_trades": len(train), "test_trades": len(test)}
        minority = int(min(train["label"].sum(), len(train) - train["label"].sum())) if len(train) else 0
        if minority < minimum or test.empty:
            rows.append(row | {"skipped": f"classe minoritaire {minority} < {minimum} (10 par variable) ou test vide"})
            continue
        model = fit_logistic(train[list(FEATURES)].to_numpy(), train["label"].to_numpy(), FEATURES, l2=L2,
                             max_iter=MAX_ITER)
        base = float(train["label"].mean())
        p = model.predict_proba(test[list(FEATURES)].to_numpy())
        parts.append(test.assign(p=p, base=base, keep=p > base, window=w.index))
        models.append(WindowModel(w, model, base))
        rows.append(row | {"train_win_rate": round(base, 4), "test_auc": auc(test["label"].to_numpy(), p),
                           "kept_share": round(float((p > base).mean()), 4),
                           "converged": model.iterations < MAX_ITER})
    predictions = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return predictions, rows, models


def model_filter(features: pd.DataFrame, wm: WindowModel) -> Callable[[datetime, TradeLevels], bool]:
    """Veto du modèle pour le simulateur : garder si p > taux de base ; variable absente → refus (prudent)."""
    by_time = features.set_index("decision_time")

    def keep(decision_time: datetime, levels: TradeLevels) -> bool:
        stamp = pd.Timestamp(decision_time)
        if stamp not in by_time.index:
            return False
        row = by_time.loc[stamp].copy()
        entry, stop, target = (float(x) for x in (levels.entry, levels.stop, levels.targets[0]))
        row["stop_atr"], row["target_r"] = _geometry_one(entry, stop, target, float(row["atr_pct"]))
        values = row[list(FEATURES)].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            return False
        return bool(wm.model.predict_proba(values[None, :])[0] > wm.base)

    return keep


def filtered_strategy(settings: Settings, strategy: Strategy, frames: FrameCache, models: list[WindowModel],
                      scenario: str, period_end: datetime) -> pd.DataFrame:
    """La stratégie re-simulée, chaque fenêtre de test filtrée par SON modèle (comme en surveillance)."""
    parts = []
    features = {symbol: features_at_decision(frames.get(symbol, strategy)) for symbol in settings.data.symbols}
    for wm in models:
        for symbol in settings.data.symbols:
            sim = simulate(frames.get(symbol, strategy), symbol, strategy,
                           simulation_rules(settings, strategy, symbol, scenario),
                           start=wm.window.test_start, end=period_end, decisions_end=wm.window.test_end,
                           decision_filter=model_filter(features[symbol], wm))
            trades = trades_frame(sim)
            if not trades.empty:
                parts.append(trades.assign(window=wm.window.index))
    if not parts:
        return pd.DataFrame()
    trades = pd.concat(parts, ignore_index=True)
    return trades[trades["entry_status"].isin(FILLED) & trades["exit_reason"].isin(CLOSED)].reset_index(drop=True)


def _ci(values: np.ndarray, times: np.ndarray, settings: Settings) -> tuple[float, float] | None:
    if len(values) == 0:
        return None
    return day_block_ci95(values, times, block_days=BLOCK_DAYS, samples=settings.protocol.bootstrap_samples,
                          seed=settings.protocol.seed)[0]


def judge(predictions: pd.DataFrame, windows: list[dict], settings: Settings, *,
          integrity: tuple[bool, str] = (True, "non contrôlée (tests)"),
          filtered_central: pd.DataFrame | None = None,
          filtered_adverse: pd.DataFrame | None = None) -> tuple[str, list[dict], dict]:
    """Critères déclarés (docstring du module) sur l'agrégat hors échantillon."""
    judged = [w for w in windows if "skipped" not in w and w["test_trades"] > 0]
    n = len(predictions)
    oos: dict = {"setups_tested": n, "windows_judged": len(judged)}
    criteria = [
        {"number": 1, "label": "Données intègres, causalité vérifiée", "passed": bool(integrity[0]),
         "detail": integrity[1]},
        {"number": 2, "label": f">= {MIN_TEST_TRADES} setups testés et >= {MIN_WINDOWS} fenêtres jugées",
         "passed": n >= MIN_TEST_TRADES and len(judged) >= MIN_WINDOWS,
         "detail": f"{n} setups, {len(judged)} fenêtre(s)"},
    ]
    labels = {3: "Brier meilleur que le taux de base (IC95 de l'écart > 0)",
              4: "Les setups gardés font mieux que l'ensemble (IC95 de l'écart > 0)",
              5: "Stratégie filtrée : E[R] > 0 en coûts centraux (IC95 > 0)",
              6: "Stratégie filtrée : E[R] > 0 en coûts défavorables",
              7: f"Stratégie filtrée : aucune paire ni année > {settings.admission.max_group_pnl_share:.0%} du PnL en R"}
    if n == 0:
        criteria += [{"number": k, "label": v, "passed": False, "detail": "aucune prédiction"} for k, v in labels.items()]
        return "INSUFFICIENT_DATA", criteria, oos
    y, p, base = (predictions[c].to_numpy(float) for c in ("label", "p", "base"))
    r, keep = predictions["r_multiple"].to_numpy(float), predictions["keep"].to_numpy(bool)
    times = predictions["setup_time"].to_numpy()
    skill = (y - base) ** 2 - (y - p) ** 2                 # > 0 : le modèle fait mieux que le taux de base
    share = float(keep.mean())
    uplift = r * (keep / share - 1) if share > 0 else np.zeros_like(r)   # moyenne = E[R] gardés − E[R] tous
    skill_ci, uplift_ci = _ci(skill, times, settings), _ci(uplift, times, settings)
    bins = pd.cut(p, bins=np.linspace(0, 1, 11), include_lowest=True)
    calibration = (pd.DataFrame({"bin": bins.astype(str), "p": p, "y": y}).groupby("bin", observed=True)
                   .agg(setups=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).round(4)
                   .reset_index().to_dict("records"))
    oos |= {"auc": auc(y, p), "brier_model": round(float(((y - p) ** 2).mean()), 5),
            "brier_base_rate": round(float(((y - base) ** 2).mean()), 5),
            "brier_skill_mean": round(float(skill.mean()), 6), "brier_skill_ci95": skill_ci,
            "win_rate_all": round(float(y.mean()), 4), "expectancy_r_all": round(float(r.mean()), 4),
            "kept_share": round(share, 4),
            "expectancy_r_kept_subset": round(float(r[keep].mean()), 4) if keep.any() else None,
            "uplift_r": round(float(uplift.mean()), 4), "uplift_r_ci95": uplift_ci, "calibration": calibration}

    central = filtered_central if filtered_central is not None else pd.DataFrame()
    adverse = filtered_adverse if filtered_adverse is not None else pd.DataFrame()
    if central.empty:
        central_r = np.array([])
        central_ci = None
    else:
        central_r = central["r_multiple"].to_numpy(float)
        central_ci = _ci(central_r, pd.to_datetime(central["entry_time"], utc=True).to_numpy(), settings)
    adverse_e = round(float(adverse["r_multiple"].mean()), 4) if not adverse.empty else None
    central_e = round(float(central_r.mean()), 4) if len(central_r) else None
    if central.empty or central_r.sum() <= 0:
        concentration_ok, concentration = False, "PnL total en R <= 0 ou aucun trade : critère non satisfait"
    else:
        grouped = central.assign(year=pd.to_datetime(central["entry_time"], utc=True).dt.year)
        worst = {column: pnl_shares(grouped, column) for column in ("symbol", "year")}
        concentration_ok = all(s.max() <= settings.admission.max_group_pnl_share for s in worst.values())
        concentration = ", ".join(f"{c} max {s.idxmax()} = {s.max():.0%}" for c, s in worst.items())
    oos |= {"filtered_trades": int(len(central_r)), "filtered_expectancy_r": central_e,
            "filtered_expectancy_r_ci95": central_ci, "filtered_expectancy_r_adverse": adverse_e,
            "filtered_by_symbol": (central.groupby("symbol")["r_multiple"].agg(["size", "mean"]).round(4)
                                   .to_dict("index") if not central.empty else {})}
    criteria += [
        {"number": 3, "label": labels[3], "passed": skill_ci is not None and skill_ci[0] > 0,
         "detail": f"écart moyen {oos['brier_skill_mean']}, IC95 {skill_ci}"},
        {"number": 4, "label": labels[4], "passed": uplift_ci is not None and uplift_ci[0] > 0,
         "detail": f"E[R] tous {oos['expectancy_r_all']} → gardés {oos['expectancy_r_kept_subset']} "
                   f"({share:.0%} gardés), écart {oos['uplift_r']}, IC95 {uplift_ci}"},
        {"number": 5, "label": labels[5], "passed": central_ci is not None and central_ci[0] > 0,
         "detail": f"{len(central_r)} trades, E[R] {central_e}, IC95 {central_ci}"},
        {"number": 6, "label": labels[6], "passed": adverse_e is not None and adverse_e > 0,
         "detail": f"E[R] {adverse_e}"},
        {"number": 7, "label": labels[7], "passed": concentration_ok, "detail": concentration},
    ]
    if not criteria[1]["passed"]:
        verdict = "INSUFFICIENT_DATA"
    elif all(c["passed"] for c in criteria):
        verdict = "USEFUL_OOS"
    else:
        verdict = "NOT_USEFUL"
    return verdict, criteria, oos


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
        if not mine.empty:
            raw_rows += int((mine["entry_status"].isin(FILLED) & mine["exit_reason"].isin(CLOSED)).sum())
        labelled = label_trades(mine, frames.get(symbol, strategy), symbol)
        if not labelled.empty:
            parts.append(labelled)
    data = pd.concat(parts, ignore_index=True).sort_values("setup_time") if parts else pd.DataFrame()
    say("validation hors échantillon")
    predictions, window_rows, models = (evaluate_windows(data, windows, settings) if not data.empty
                                        else (pd.DataFrame(), [], []))
    say("stratégie filtrée re-simulée (coûts centraux et défavorables)")
    filtered_central = filtered_strategy(settings, strategy, frames, models, "central", period.end)
    filtered_adverse = filtered_strategy(settings, strategy, frames, models, "adverse", period.end)
    say("contrôle d'intégrité et de causalité")
    integrity = check_integrity(settings, strategy, inputs, period, windows)
    verdict, criteria, oos = judge(predictions, window_rows, settings, integrity=integrity,
                                   filtered_central=filtered_central, filtered_adverse=filtered_adverse)
    run_id = new_run_id("ML")
    result = MetaResult(run_id, strategy_id, verdict, criteria, window_rows, oos, raw_rows - len(data),
                        settings.reports_dir / run_id, predictions, models[-1].model if models else None)
    experiments = ExperimentRegistry(settings.experiments_db)
    program_trials = experiments.program_trials(period.label) + 1
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind="ML_META",
        hypothesis=f"un modèle logistique sur les variables de décision trie les setups de {strategy_id}",
        strategy=strategy_id, strategy_version=strategy.version,
        variant=f"protocole v{PROTOCOL_VERSION} : logistique L2, règle p > taux d'entraînement",
        params={"features": list(FEATURES), "l2": L2, "rule": "p > train win rate",
                "protocol_version": PROTOCOL_VERSION},
        period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
        cost_scenario="central (adverse pour le critère 6)",
        simulation_rules={"walk_forward": config.model_dump(), "purge": "setup et sortie < test",
                          "filtered_strategy": "re-simulée avec le modèle comme veto"},
        metrics={"verdict": verdict, "n_trials": 1, "program_trials": program_trials, "criteria": criteria,
                 "oos": {k: v for k, v in oos.items() if k != "calibration"}},
        status="COMPLETED", report_dir=str(result.report_dir), **common)
    _write(result, program_trials)
    return result


RESERVES = (
    "Pseudo hors échantillon : ces fenêtres ont déjà servi à juger (et rejeter) ces stratégies, et les "
    "variables ont été choisies en connaissant le criblage D–I ; le programme compte plus de 250 essais sur "
    "DEVELOPMENT.",
    "Un USEFUL_OOS ne branche rien : il exige une confirmation sur des données non vues (signaux shadow "
    "prospectifs résolus après la date du modèle, ou une consultation unique et enregistrée du test final), "
    "puis une promotion explicite par le propriétaire.",
    "L'étiquette « R net > 0 » n'est pas l'objectif E[R] : la règle tend à écarter les setups à objectif "
    "lointain (moins de puissance, pas de biais favorable).",
)


def _write(result: MetaResult, program_trials: int) -> None:
    result.report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": result.run_id, "strategy": result.strategy, "protocol_version": PROTOCOL_VERSION,
               "verdict": result.verdict, "criteria": result.criteria, "windows": result.windows,
               "oos": result.oos, "dropped_rows_incomplete_features": result.dropped_rows,
               "program_trials": program_trials, "reserves": list(RESERVES),
               "model_last_window": result.last_model.to_dict() if result.last_model else None}
    (result.report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                                    encoding="utf-8")
    if not result.predictions.empty:
        result.predictions[["symbol", "setup_time", "window", "p", "base", "keep", "label", "r_multiple"]].to_csv(
            result.report_dir / "predictions_oos.csv", index=False)
    lines = [f"# Méta-labeling {result.strategy} — {result.run_id} (protocole v{PROTOCOL_VERSION})", "",
             f"**Verdict : {result.verdict}** (aucune influence sur les signaux sans promotion explicite)", "",
             "| # | Critère | OK | Détail |", "|---|---|---|---|"]
    lines += [f"| {c['number']} | {c['label']} | {'oui' if c['passed'] else 'non'} | {c['detail']} |"
              for c in result.criteria]
    lines += ["", "## Réserves", "", *(f"- {r}" for r in RESERVES), "",
              "La probabilité affichée est la fréquence prédite de « R net > 0 » pour un setup de cette "
              "stratégie, apprise sur le passé ; sa calibration (summary.json) dit si elle est fiable."]
    (result.report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

