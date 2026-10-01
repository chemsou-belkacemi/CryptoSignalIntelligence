"""Prévision de volatilité (docs/VOLATILITY.md, protocole v1), déclarée avant toute exécution.

Question : un modèle prévoit-il la variance réalisée des 1, 3 et 7 prochains jours mieux que la règle « variance
des 7 derniers jours × horizon » (M0) ? Ce n'est PAS une stratégie : on mesure une erreur de prévision, aucune
rentabilité n'est en jeu. Cinq modèles × trois horizons = 15 comparaisons, comptées dans le programme.
DEVELOPMENT seulement.

Règles :
- bougies 1 h du magasin long (research/long_history.py), univers figé (research/universe.py), coupées à la fin
  de DEVELOPMENT ; rendements log horaires, définis seulement si la bougie de l'heure précédente existe ;
- une ligne par jour et par paire à 00:00 UTC (clôture de la bougie de 23:00) ; variables : variance horaire
  moyenne des 24, 168 et 720 dernières heures (en log), les trois mêmes pour BTC (jointes sur `available_at`),
  jour de la semaine ; fenêtre non contiguë → variable manquante ;
- cible : RV_H² = somme des carrés des 24·H rendements horaires qui SUIVENT l'origine (fenêtre contiguë) ;
- modèles sans réglage : M0 récente 7 j, M1 EWMA (λ = 0,94), M2 HAR par paire, M3 HAR commun, M4 HAR commun +
  BTC, M5 LightGBM commun ; M2 à M5 prévoient log RV_H², puis reviennent à la variance par le facteur « moyenne
  de exp(résidu) » de leur entraînement (sans lui, exp(log prévu) vise la médiane, que le QLIKE pénalise) ;
- réajustement le 1er de chaque mois sur toutes les lignes passées, PURGÉES : la cible d'une ligne d'entraînement
  se termine au plus tard à la date de réajustement ; une origine n'est évaluée qu'après 400 jours d'historique
  de la paire, à partir du 2019-01-01, et seulement si les six modèles ont une prévision (échantillon commun) ;
- mesures : QLIKE (principale) et erreur quadratique de log RV (secondaire) ; différence avec M0 moyennée par
  jour sur les paires, IC de Student par blocs calendaires (max(10, 2·H) jours, au moins 20 blocs), niveau
  corrigé de Bonferroni pour les 15 comparaisons ;
- « PREVISION_UTILE » = borne haute de l'IC < 0 ET mieux que M0 dans 6 années civiles sur 7 ET pour 70 % des
  paires ET sur la perte secondaire ; un modèle utile ne dit rien de la direction ni de la rentabilité ;
- données exigées complètes (sinon refus, aucun essai enregistré) ; audit des fuites sur BTC, ETH et SOL AVANT
  tout résultat (données tronquées, futur falsifié, une mutation qui doit être détectée) : en échec, aucun
  résultat n'est produit ; empreintes des séries lues enregistrées.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib import metadata
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ..config import Settings
from ..features.loader import MissingData
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .long_history import load_long
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY = "VOLATILITY", "VOLATILITY_FORECAST"
PROTOCOL_VERSION = 1
DOC = "docs/VOLATILITY.md"
USEFUL, NO_IMPROVEMENT = "PREVISION_UTILE", "AUCUNE_AMELIORATION"

STEP = pd.Timedelta(hours=1)
HORIZONS = (1, 3, 7)                                   # jours
MODELS = ("M0_RECENT_7D", "M1_EWMA", "M2_HAR_PAIR", "M3_HAR_POOLED", "M4_HAR_POOLED_BTC", "M5_LGBM_POOLED")
BASELINE, CANDIDATES = MODELS[0], MODELS[1:]
N_TRIALS = len(CANDIDATES) * len(HORIZONS)             # 15 comparaisons déclarées
LEVEL = 1 - 0.05 / N_TRIALS                            # Bonferroni, bilatéral
FIRST_FORECAST = "2019-01-01"                          # première prévision hors échantillon
MIN_HISTORY_DAYS = 400                                 # jours depuis la première bougie avant toute origine évaluée
DAY_HOURS, WEEK_HOURS, MONTH_HOURS = 24, 168, 720
EWMA_LAMBDA = 0.94
LGBM_ROUNDS = 300
LGBM_PARAMS: dict[str, Any] = {"objective": "regression", "num_leaves": 15, "learning_rate": 0.05,
                               "min_data_in_leaf": 200}
THREADS = max(1, min(4, (os.cpu_count() or 2) - 2))
MIN_TRAIN_ROWS = 100                                   # sous ce nombre de lignes, un modèle n'est pas ajusté
MIN_BLOCKS = 20
YEARS = tuple(range(2019, 2026))                       # années civiles de l'origine : 2019 à 2025
MIN_YEARS_BETTER = 6
MIN_PAIRS_SHARE = 0.70
AUDIT_PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
AUDIT_ORIGINS = 3
END_TOLERANCE = pd.Timedelta(days=2)
MAX_MARKET_AGE = pd.Timedelta(hours=1)                 # BTC : la ligne du même jour, jamais celle de la veille
MIN_COVERAGE = 0.95                                    # variances passées : au moins 95 % des heures de la fenêtre

READ_COLUMNS = ("open_time", "close", "available_at")  # tout ce que le protocole lit d'une bougie
OWN = ("log_var_d", "log_var_w", "log_var_m")
MARKET_FEATURES = ("btc_log_var_d", "btc_log_var_w", "btc_log_var_m")
FEATURES = (*OWN, *MARKET_FEATURES, "dow")
# Tout ce qu'un modèle ou la sélection des origines lit à l'origine : c'est ce que l'audit des fuites compare.
INPUTS = (*FEATURES, "var_w", "ewma_var", "history_days")
TARGETS = tuple(f"rv2_{horizon}" for horizon in HORIZONS)
COLUMNS = ("origin", "decision_available_at", "history_days", "var_d", "var_w", "var_m", "ewma_var", *FEATURES,
           *TARGETS)
FORECAST_COLUMNS = ("symbol", "origin", "horizon", "model", "forecast", "realized")
MODEL_TEXT = {
    "M0_RECENT_7D": "référence : variance horaire moyenne des 168 dernières heures × 24·H",
    "M1_EWMA": "variance journalière lissée (λ = 0,94) × H",
    "M2_HAR_PAIR": "HAR par paire : log RV_H² sur les log-variances jour, semaine, mois de la paire",
    "M3_HAR_POOLED": "HAR commun : la même régression, toutes paires ensemble",
    "M4_HAR_POOLED_BTC": "HAR commun + les trois log-variances de BTC",
    "M5_LGBM_POOLED": "LightGBM commun : variables de M4 + jour de la semaine (300 arbres, 15 feuilles)",
}


class LeakAuditFailed(RuntimeError):
    pass


class IncompleteData(RuntimeError):
    pass


class DirtyCode(RuntimeError):
    pass


@dataclass
class Row:
    model: str
    horizon_days: int
    forecasts: int                          # (paire, origine) évaluées
    days: int
    pairs: int
    qlike: float | None                     # moyenne des moyennes journalières (un jour = une valeur)
    qlike_baseline: float | None
    qlike_diff: float | None                # modèle − M0 : négatif quand le modèle fait mieux
    ci_qlike_diff: list[float] | None
    qlike_diff_by_year: dict[str, float]
    years_better: int
    pairs_better_share: float | None
    log_error: float | None
    log_error_baseline: float | None
    log_error_diff: float | None
    criteria: dict[str, bool]
    useful: bool


@dataclass
class Result:
    run_id: str
    period_end: str
    level: float
    n_trials: int = N_TRIALS
    program_trials: int = 0
    verdict: str = NO_IMPROVEMENT
    selected: dict[int, str] = field(default_factory=dict)
    leak_audit: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)


def block_days_for(horizon: int) -> int:
    return max(10, 2 * horizon)


# --- Lignes journalières : variables connues à l'origine et cibles futures ------------------------------------

def _window_sums(values: np.ndarray, starts: np.ndarray, length: int) -> np.ndarray:
    """Somme de `length` valeurs consécutives à partir de chaque position de `starts`. NaN si la fenêtre sort de
    la série ou contient un rendement manquant : une fenêtre non contiguë n'a ni variable ni cible."""
    out = np.full(len(starts), np.nan)
    inside = (starts >= 0) & (starts + length <= len(values))
    if inside.any():
        out[inside] = sliding_window_view(values, length)[starts[inside]].sum(axis=1)
    return out


def _window_means(values: np.ndarray, starts: np.ndarray, length: int) -> np.ndarray:
    """Moyenne des valeurs CONNUES de chaque fenêtre de `length` positions, si elles couvrent au moins
    MIN_COVERAGE de la fenêtre ; sinon NaN. Une heure de maintenance de Binance ne retire plus un mois de
    variables (v1 complétée avant exécution) ; la cible, elle, reste exigée contiguë."""
    out = np.full(len(starts), np.nan)
    inside = (starts >= 0) & (starts + length <= len(values))
    if inside.any():
        known = np.isfinite(values)
        windows = sliding_window_view(np.where(known, values, 0.0), length)[starts[inside]]
        counts = sliding_window_view(known.astype(float), length)[starts[inside]].sum(axis=1)
        enough = counts >= math.ceil(MIN_COVERAGE * length)
        out[np.flatnonzero(inside)[enough]] = windows[enough].sum(axis=1) / counts[enough]
    return out


def ewma(values: np.ndarray, decay: float = EWMA_LAMBDA) -> np.ndarray:
    """Lissage exponentiel s_t = λ·s_{t−1} + (1 − λ)·x_t, initialisé à la première valeur connue. Une valeur
    manquante (jour incomplet) laisse l'état inchangé."""
    out, state = np.full(len(values), np.nan), math.nan
    for index, value in enumerate(np.asarray(values, dtype=float).tolist()):
        if math.isfinite(value):
            state = value if math.isnan(state) else decay * state + (1 - decay) * value
        out[index] = state
    return out


def _utc(series: pd.Series) -> pd.Series:
    """Horodatages en UTC. Les colonnes du magasin le sont déjà : aucune relecture valeur par valeur."""
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_convert("UTC")
    return pd.to_datetime(series, utc=True)


def _own(h1: pd.DataFrame, *, leaky: bool = False) -> pd.DataFrame:
    """Lignes journalières d'UNE série : variances passées, EWMA et cibles (sans les variables de BTC)."""
    frame = h1[list(READ_COLUMNS)].dropna().sort_values("open_time").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame(columns=[c for c in COLUMNS if c not in MARKET_FEATURES])
    # Grille horaire complète depuis la première bougie : une position = une heure, une bougie absente = une
    # ligne vide. Les fenêtres comptent ainsi des HEURES, même autour d'une interruption de Binance.
    first = _utc(frame["open_time"]).iloc[0]
    grid = pd.date_range(first, _utc(frame["open_time"]).iloc[-1], freq=STEP)
    frame = frame.assign(open_time=_utc(frame["open_time"])).set_index("open_time").reindex(grid)
    frame.index.name = "open_time"
    frame = frame.reset_index()
    exists = frame["close"].notna().to_numpy()
    times = frame["open_time"]
    close = frame["close"].to_numpy(float)
    log_close = np.log(np.where(close > 0, close, np.nan))
    # Rendement log horaire au carré ; manquant si la bougie de l'heure précédente n'existe pas.
    squared = np.full(len(frame), np.nan)
    squared[1:] = np.diff(log_close) ** 2
    closes = times + STEP
    rows = np.flatnonzero((closes == closes.dt.floor("D")).to_numpy() & exists)   # bougies de 23:00 : clôture à 00:00
    origin = closes.iloc[rows].reset_index(drop=True)
    # Fenêtres PASSÉES : elles se terminent à la bougie de décision (incluse). `leaky` n'existe que pour la
    # MUTATION de l'audit : la fenêtre « jour » avance d'une heure et lit la première heure après l'origine.
    past = {"var_d": _window_means(squared, rows - DAY_HOURS + 1 + int(leaky), DAY_HOURS),
            "var_w": _window_means(squared, rows - WEEK_HOURS + 1, WEEK_HOURS),
            "var_m": _window_means(squared, rows - MONTH_HOURS + 1, MONTH_HOURS)}
    # Une variance nulle n'a pas de logarithme : la variable est manquante.
    logs = {name: np.log(np.where(values > 0, values, np.nan)) for name, values in zip(OWN, past.values(), strict=True)}
    # CIBLES (futur, jamais une variable) : les 24·H rendements horaires qui SUIVENT la bougie de décision.
    targets = {f"rv2_{horizon}": _window_sums(squared, rows + 1, DAY_HOURS * horizon) for horizon in HORIZONS}
    return pd.DataFrame({
        "origin": origin,
        "decision_available_at": _utc(frame["available_at"]).dt.as_unit("ns").iloc[rows].reset_index(drop=True),
        "history_days": ((origin - times.iloc[0]) / pd.Timedelta(days=1)).astype(float),
        **past, "ewma_var": ewma(DAY_HOURS * past["var_d"]), **logs,
        "dow": origin.dt.dayofweek.astype(float),                    # lundi = 0 ; jour qui commence à l'origine
        **targets})


def daily_frame(h1: pd.DataFrame, btc_h1: pd.DataFrame | None = None, *, leaky: bool = False) -> pd.DataFrame:
    """Une ligne par jour à 00:00 UTC (clôture de la bougie 1 h de 23:00) : `origin`, `decision_available_at`,
    ancienneté de la série, variables connues à l'origine et, pour chaque H, `rv2_<H>` = variance réalisée des
    H jours suivants (NaN si la fenêtre n'est pas contiguë). Les variables de BTC sont jointes vers le passé sur
    `available_at` : la ligne BTC du même jour, seulement si elle est disponible à la décision.
    `leaky=True` n'existe que pour la mutation de l'audit des fuites."""
    out = _own(h1, leaky=leaky)
    market = None if btc_h1 is None else _own(btc_h1)
    if out.empty or market is None or market.empty:
        return out.assign(**dict.fromkeys(MARKET_FEATURES, np.nan))[list(COLUMNS)]
    market = market[["decision_available_at", *OWN]].rename(
        columns={"decision_available_at": "_market_at"} | dict(zip(OWN, MARKET_FEATURES, strict=True)))
    joined = pd.merge_asof(out.sort_values("decision_available_at"), market.sort_values("_market_at"),
                           left_on="decision_available_at", right_on="_market_at", direction="backward",
                           tolerance=MAX_MARKET_AGE)
    return joined.sort_values("origin").reset_index(drop=True)[list(COLUMNS)]


def complete_rows(data: pd.DataFrame) -> pd.Series:
    """Lignes dont toutes les variables sont connues (donc aussi les entrées de M0 et de M1, strictement
    positives)."""
    finite = np.isfinite(data[list(INPUTS)].to_numpy(float)).all(axis=1)
    return pd.Series(finite, index=data.index) & (data["var_w"] > 0) & (data["ewma_var"] > 0)


# --- Pertes -----------------------------------------------------------------------------------------------------

def qlike(realized_var, forecast_var):
    """Perte QLIKE entre variance réalisée RV² et variance prévue F : RV²/F − ln(RV²/F) − 1. Nulle quand F = RV²,
    positive sinon ; une sous-estimation coûte plus cher qu'une surestimation du même facteur."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.asarray(realized_var, dtype=float) / np.asarray(forecast_var, dtype=float)
        return ratio - np.log(ratio) - 1


def log_error(realized_var, forecast_var):
    """Erreur quadratique de log RV (RV = racine de la variance) : (ln RV − ln √F)² = (ln RV² − ln F)² / 4."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.log(np.asarray(realized_var, dtype=float)) - np.log(np.asarray(forecast_var, dtype=float))) ** 2 / 4


# --- Modèles (aucun réglage : tout est fixé par le protocole) -------------------------------------------------

def recent_forecast(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    """M0, la référence : variance horaire moyenne des 168 dernières heures × 24·H."""
    return frame["var_w"].to_numpy(float) * DAY_HOURS * horizon


def ewma_forecast(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    """M1 : variance journalière lissée (λ = 0,94) × H."""
    return frame["ewma_var"].to_numpy(float) * horizon


def _design(X) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    return np.column_stack([np.ones(len(X)), X])


def _smearing(residual: np.ndarray) -> float:
    """Facteur de retour du log à la variance : moyenne de exp(résidu) sur l'entraînement (Duan). C'est l'échelle
    qui minimise le QLIKE moyen de l'entraînement ; exp(log prévu) seul viserait la médiane de RV²."""
    return float(np.mean(np.exp(residual)))


@dataclass(frozen=True, eq=False)
class Har:
    """Régression linéaire de log RV_H² : `coef` = constante puis une pente par variable."""
    coef: np.ndarray
    smearing: float
    rows: int

    def predict(self, X) -> np.ndarray:
        """Variance prévue."""
        return np.exp(_design(X) @ self.coef) * self.smearing


@dataclass(frozen=True, eq=False)
class Trees:
    booster: Any
    smearing: float
    rows: int

    def predict(self, X) -> np.ndarray:
        """Variance prévue."""
        return np.exp(np.asarray(self.booster.predict(np.asarray(X, dtype=float)), dtype=float)) * self.smearing


def fit_har(X, y) -> Har | None:
    """Moindres carrés ordinaires de y = log RV_H² sur les variables, avec constante. None sous MIN_TRAIN_ROWS."""
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    if len(y) < MIN_TRAIN_ROWS:
        return None
    design = _design(X)
    coef = np.linalg.lstsq(design, y, rcond=None)[0]
    return Har(coef, _smearing(y - design @ coef), len(y))


def fit_lgbm(X, y, *, seed: int) -> Trees | None:
    """LightGBM (API native) sur y = log RV_H², réglages fixés par le protocole, sans sous-échantillonnage ni
    arrêt précoce. None sous MIN_TRAIN_ROWS."""
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    if len(y) < MIN_TRAIN_ROWS:
        return None
    import lightgbm
    params = LGBM_PARAMS | {"seed": seed, "num_threads": THREADS, "verbose": -1, "deterministic": True,
                            "force_col_wise": True}
    booster = lightgbm.train(params, lightgbm.Dataset(X, label=y), num_boost_round=LGBM_ROUNDS)
    return Trees(booster, _smearing(y - np.asarray(booster.predict(X), dtype=float)), len(y))


@dataclass(frozen=True, eq=False)
class Models:
    """Modèles ajustés à une date de réajustement, pour un horizon."""
    by_pair: dict[str, Har]
    pooled: Har | None
    pooled_btc: Har | None
    trees: Trees | None
    rows: int


def training_rows(data: pd.DataFrame, refit: pd.Timestamp, horizon: int) -> pd.DataFrame:
    """Lignes d'entraînement à la date `refit` : variables complètes, cible connue et PURGE, c'est-à-dire une
    fenêtre cible qui se termine au plus tard à `refit` (origine + H jours ≤ refit : sa dernière bougie clôture
    juste avant, elle est disponible en même temps que la bougie de décision de l'origine `refit`)."""
    purged = data["origin"] + pd.Timedelta(days=horizon) <= refit
    return data[complete_rows(data) & (data[f"rv2_{horizon}"] > 0) & purged]


def fit_at(data: pd.DataFrame, refit: pd.Timestamp, horizon: int, *, seed: int) -> Models:
    """Ajuste M2 à M5 sur la fenêtre croissante purgée connue à `refit`."""
    train = training_rows(data, refit, horizon)
    y = np.log(train[f"rv2_{horizon}"].to_numpy(float))
    own = train[list(OWN)].to_numpy(float)
    by_pair: dict[str, Har] = {}
    for symbol, index in train.groupby("symbol").indices.items():
        model = fit_har(own[index], y[index])
        if model is not None:
            by_pair[str(symbol)] = model
    return Models(by_pair, fit_har(own, y), fit_har(train[[*OWN, *MARKET_FEATURES]].to_numpy(float), y),
                  fit_lgbm(train[list(FEATURES)].to_numpy(float), y, seed=seed), len(train))


def month_forecasts(models: Models, rows: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Variance prévue par les six modèles pour les origines `rows` (NaN quand un modèle n'a pas pu être
    ajusté), avec la variance réalisée."""
    out = rows[["symbol", "origin"]].reset_index(drop=True)
    out["realized"] = rows[f"rv2_{horizon}"].to_numpy(float)
    own = rows[list(OWN)].to_numpy(float)
    by_pair = np.full(len(rows), np.nan)
    for symbol, index in rows.groupby("symbol").indices.items():
        model = models.by_pair.get(str(symbol))
        if model is not None:
            by_pair[index] = model.predict(own[index])
    missing = np.full(len(rows), np.nan)
    out[MODELS[0]] = recent_forecast(rows, horizon)
    out[MODELS[1]] = ewma_forecast(rows, horizon)
    out[MODELS[2]] = by_pair
    out[MODELS[3]] = missing if models.pooled is None else models.pooled.predict(own)
    out[MODELS[4]] = (missing if models.pooled_btc is None
                      else models.pooled_btc.predict(rows[[*OWN, *MARKET_FEATURES]].to_numpy(float)))
    out[MODELS[5]] = missing if models.trees is None else models.trees.predict(rows[list(FEATURES)].to_numpy(float))
    return out


# --- Entraînement glissant causal -----------------------------------------------------------------------------

def walk_forward(frames: dict[str, pd.DataFrame], settings: Settings, *,
                 progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    """Prévisions hors échantillon de tous les modèles (table longue : paire, origine, horizon, modèle, variance
    prévue, variance réalisée). `frames` : lignes journalières (`daily_frame`) par paire.

    Le 1er de chaque mois, M2 à M5 sont réajustés sur toutes les lignes passées purgées (`training_rows`) et
    prévoient toutes les origines du mois. Une origine est évaluée si : variables et cible connues, au moins
    MIN_HISTORY_DAYS jours d'historique de la paire, origine ≥ FIRST_FORECAST, cible entièrement dans DEVELOPMENT
    (sa dernière bougie s'ouvre au plus tard à la fin de DEVELOPMENT), et les six modèles ont une prévision."""
    say = progress or (lambda _text: None)
    empty = pd.DataFrame(columns=list(FORECAST_COLUMNS))
    end = pd.Timestamp(development_end(settings))
    first = pd.Timestamp(FIRST_FORECAST, tz="UTC")
    tables = [frame.assign(symbol=symbol) for symbol, frame in frames.items() if len(frame)]
    if not tables:
        return empty
    data = pd.concat(tables, ignore_index=True)
    origins = data["origin"]
    eligible = complete_rows(data) & (data["history_days"] >= MIN_HISTORY_DAYS) & (origins >= first)
    parts: list[pd.DataFrame] = []
    for horizon in HORIZONS:
        say(f"prévisions à {horizon} j")
        last_target_bar = origins + pd.Timedelta(days=horizon) - STEP
        scored = (eligible & (data[f"rv2_{horizon}"] > 0) & (last_target_bar <= end)).to_numpy()
        if not scored.any():
            continue
        for refit in pd.date_range(first.replace(day=1), origins[scored].max(), freq="MS"):
            month = scored & ((origins >= refit) & (origins < refit + pd.offsets.MonthBegin(1))).to_numpy()
            if not month.any():
                continue
            models = fit_at(data, refit, horizon, seed=settings.protocol.seed)
            wide = month_forecasts(models, data[month], horizon)
            values = wide[list(MODELS)].to_numpy(float)
            wide = wide[(np.isfinite(values) & (values > 0)).all(axis=1)]      # échantillon commun aux six modèles
            long = wide.melt(id_vars=["symbol", "origin", "realized"], value_vars=list(MODELS), var_name="model",
                             value_name="forecast").assign(horizon=horizon)
            parts.append(long[list(FORECAST_COLUMNS)])
    if not parts:
        return empty
    return pd.concat(parts, ignore_index=True).sort_values(["horizon", "origin", "symbol", "model"]).reset_index(
        drop=True)


# --- Mesures et règle -----------------------------------------------------------------------------------------

def _rounded(value: float) -> float:
    return round(float(value), 6)


def _row(model: str, horizon: int, table: pd.DataFrame) -> Row:
    """Compare un modèle à M0 pour un horizon. `table` : une ligne par (paire, origine) évaluée, colonnes
    `symbol`, `origin`, `qlike`, `qlike_base`, `log_error`, `log_error_base`."""
    criteria = dict.fromkeys(("ci_upper_below_zero", "years", "pairs", "secondary_loss"), False)
    if table.empty:
        return Row(model, horizon, 0, 0, 0, None, None, None, None, {}, 0, None, None, None, None, criteria, False)
    losses = table.assign(diff=table["qlike"] - table["qlike_base"],
                          log_diff=table["log_error"] - table["log_error_base"])
    # Une valeur par jour d'origine : la moyenne sur les paires évaluées ce jour-là.
    daily = losses.groupby("origin")[["qlike", "qlike_base", "diff", "log_error", "log_error_base",
                                      "log_diff"]].mean().sort_index()
    ci = calendar_mean_ci(daily["diff"].to_numpy(float), daily.index, block_days=block_days_for(horizon),
                          min_blocks=MIN_BLOCKS, level=LEVEL)
    by_year = daily["diff"].groupby(daily.index.year).mean()
    by_pair = losses.groupby("symbol")["diff"].mean()
    years_better = sum(1 for year in YEARS if year in by_year.index and by_year[year] < 0)
    pairs_share = float((by_pair < 0).mean())
    mean = daily.mean()
    criteria = {"ci_upper_below_zero": ci is not None and ci[1] < 0, "years": years_better >= MIN_YEARS_BETTER,
                "pairs": pairs_share >= MIN_PAIRS_SHARE, "secondary_loss": bool(mean["log_diff"] < 0)}
    return Row(model, horizon, int(len(losses)), int(len(daily)), int(len(by_pair)), _rounded(mean["qlike"]),
               _rounded(mean["qlike_base"]), _rounded(mean["diff"]), ci,
               {str(year): _rounded(value) for year, value in by_year.items()}, years_better,
               round(pairs_share, 4), _rounded(mean["log_error"]), _rounded(mean["log_error_base"]),
               _rounded(mean["log_diff"]), criteria, all(criteria.values()))


def evaluate(forecasts: pd.DataFrame) -> list[Row]:
    """Les 15 comparaisons déclarées : chaque modèle M1…M5 contre M0, à chaque horizon, sur les mêmes lignes."""
    rows = []
    for horizon in HORIZONS:
        part = forecasts[forecasts["horizon"] == horizon]
        wide = part.pivot(index=["symbol", "origin"], columns="model", values="forecast")
        realized = part.drop_duplicates(["symbol", "origin"]).set_index(["symbol", "origin"])["realized"].reindex(
            wide.index)
        for model in CANDIDATES:
            if model not in wide.columns or BASELINE not in wide.columns:
                rows.append(_row(model, horizon, wide.iloc[:0]))
                continue
            both = wide[[BASELINE, model]].assign(realized=realized).dropna().reset_index()
            rows.append(_row(model, horizon, pd.DataFrame({
                "symbol": both["symbol"], "origin": both["origin"],
                "qlike": qlike(both["realized"], both[model]), "qlike_base": qlike(both["realized"], both[BASELINE]),
                "log_error": log_error(both["realized"], both[model]),
                "log_error_base": log_error(both["realized"], both[BASELINE])})))
    return rows


def decide(rows: list[Row]) -> tuple[dict[int, str], str]:
    """Par horizon : le meilleur QLIKE parmi les modèles utiles, sinon AUCUNE_AMELIORATION. Verdict global :
    PREVISION_UTILE dès qu'un horizon a un modèle utile."""
    selected = {}
    for horizon in HORIZONS:
        useful = [row for row in rows if row.horizon_days == horizon and row.useful and row.qlike is not None]
        best = min(useful, key=lambda row: (row.qlike or 0.0, MODELS.index(row.model)), default=None)
        selected[horizon] = best.model if best is not None else NO_IMPROVEMENT
    verdict = USEFUL if any(model != NO_IMPROVEMENT for model in selected.values()) else NO_IMPROVEMENT
    return selected, verdict


# --- Données, audit des fuites --------------------------------------------------------------------------------

def load_series(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h du magasin long, coupées à `end` et jamais au-delà de la fin de DEVELOPMENT ; seules les
    colonnes lues par le protocole sont gardées. Lève MissingData si la paire n'y est pas."""
    frame = load_long(settings, symbol)
    limit = min(pd.Timestamp(end), pd.Timestamp(development_end(settings)))
    return frame.loc[frame["open_time"] <= limit, list(READ_COLUMNS)].reset_index(drop=True)


def check_complete(settings: Settings, symbols: list[str], end: pd.Timestamp) -> dict:
    """Couverture de chaque paire dans le magasin long ; lève IncompleteData si une paire manque ou s'arrête
    plus de 2 jours avant la fin de DEVELOPMENT."""
    report, problems = {}, []
    for symbol in symbols:
        try:
            series = load_series(settings, symbol, end)
        except MissingData:
            problems.append(f"{symbol} : absent du magasin long")
            continue
        if series.empty:
            problems.append(f"{symbol} : aucune bougie avant la fin de DEVELOPMENT")
            continue
        first, last = series["open_time"].iloc[0], series["open_time"].iloc[-1]
        report[symbol] = {"first": str(first), "last": str(last), "rows": int(len(series)),
                          "missing_hours": int((last - first) / STEP) + 1 - int(len(series))}
        if last < end - END_TOLERANCE:
            problems.append(f"{symbol} : s'arrête avant la fin de DEVELOPMENT ({last})")
    if problems:
        raise IncompleteData("données incomplètes : " + " ; ".join(problems))
    return report


def causality_violations(h1: pd.DataFrame, btc_h1: pd.DataFrame | None = None, *, origins: list[pd.Timestamp],
                         seed: int = 0, leaky: bool = False) -> list[dict]:
    """Pour chaque origine, tout ce qui est lu à l'origine (INPUTS), recalculé avec seulement les bougies
    disponibles à la décision (`available_at` ≤ celui de la bougie de 23:00), puis avec un futur falsifié, doit
    égaler le calcul complet. Liste vide : causal. `leaky` : la mutation de l'audit."""
    full = daily_frame(h1, btc_h1, leaky=leaky).set_index("origin")
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for origin in origins:
        if origin not in full.index:
            problems.append({"origin": str(origin), "check": "calcul complet", "features": ["(origine absente)"]})
            continue
        known_at = full.loc[origin, "decision_available_at"]

        def cut(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            return frame[_utc(frame["available_at"]) <= at]

        def falsify(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            frame = frame.copy()
            future = (_utc(frame["available_at"]) > at).to_numpy()
            for column in frame.columns:
                if (column not in ("open_time", "close_time", "available_at", "ingested_at")
                        and pd.api.types.is_numeric_dtype(frame[column])
                        and not pd.api.types.is_bool_dtype(frame[column])):
                    frame[column] = frame[column].astype(float)
                    frame.loc[future, column] = frame.loc[future, column] * rng.uniform(0.5, 1.5, int(future.sum()))
            return frame

        def both(change: Callable[[pd.DataFrame], pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame | None]:
            pair = change(h1)
            return pair, (None if btc_h1 is None else pair if btc_h1 is h1 else change(btc_h1))

        expected = full.loc[origin, list(INPUTS)].to_numpy(float)
        for label, change in (("tronqué", cut), ("futur falsifié", falsify)):
            got = daily_frame(*both(change), leaky=leaky).set_index("origin")
            if origin not in got.index:
                problems.append({"origin": str(origin), "check": label, "features": ["(origine absente)"]})
                continue
            values = got.loc[origin, list(INPUTS)].to_numpy(float)
            differs = ~np.isclose(values, expected, rtol=1e-9, atol=0.0, equal_nan=True)
            if differs.any():
                problems.append({"origin": str(origin), "check": label,
                                 "features": [name for name, d in zip(INPUTS, differs, strict=True) if d]})
    return problems


def leak_audit(settings: Settings, end: pd.Timestamp, seed: int) -> dict:
    """Causalité sur données réelles : pour BTC, ETH et SOL (tous trois exigés), AUDIT_ORIGINS origines tirées
    avec la graine ; aucune différence tolérée, et la mutation (variable « jour » qui lit la première heure après
    l'origine) doit être détectée sur chaque paire."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(AUDIT_PAIRS, False)
    checked: list[str] = []
    origins_checked: dict[str, list[str]] = {}
    try:
        market = load_series(settings, MARKET, end)
    except MissingData:
        market = None
    for symbol in AUDIT_PAIRS:
        try:
            series = market if symbol == MARKET else load_series(settings, symbol, end)
        except MissingData:
            continue
        if series is None or market is None:
            continue
        full, mutated = daily_frame(series, market), daily_frame(series, market, leaky=True)
        # Origines où les variables sont connues et où l'heure suivante existe (la mutation y est observable).
        ready = full.loc[(complete_rows(full) & mutated["log_var_d"].notna()).to_numpy(), "origin"]
        if len(ready) < AUDIT_ORIGINS:
            continue
        picks = np.sort(rng.choice(len(ready), size=AUDIT_ORIGINS, replace=False))
        origins = [ready.iloc[int(index)] for index in picks]
        violations += [v | {"symbol": symbol} for v in causality_violations(series, market, origins=origins,
                                                                             seed=seed)]
        found = causality_violations(series, market, origins=origins, seed=seed, leaky=True)
        detected[symbol] = any("log_var_d" in v["features"] for v in found)
        checked.append(symbol)
        origins_checked[symbol] = [str(origin) for origin in origins]
    complete = checked == list(AUDIT_PAIRS)
    return {"violations": violations, "mutation_detected": detected, "checked_pairs": checked,
            "origins": origins_checked, "origins_per_pair": AUDIT_ORIGINS,
            "passed": complete and not violations and all(detected.values())}


# --- Exécution et enregistrement ------------------------------------------------------------------------------

def _coverage(frames: dict[str, pd.DataFrame], forecasts: pd.DataFrame) -> dict:
    """Ce qui est évalué, par paire : lignes journalières, lignes complètes, origines évaluées par horizon."""
    base = forecasts[forecasts["model"] == BASELINE]
    counts = base.groupby(["symbol", "horizon"])["origin"].agg(["size", "min"]) if len(base) else None
    out = {}
    for symbol, frame in frames.items():
        evaluated, first = {}, None
        for horizon in HORIZONS:
            known = counts is not None and (symbol, horizon) in counts.index
            evaluated[str(horizon)] = int(counts.loc[(symbol, horizon), "size"]) if known and counts is not None else 0
            if known and counts is not None:
                moment = counts.loc[(symbol, horizon), "min"]
                first = moment if first is None or moment < first else first
        out[symbol] = {"daily_rows": int(len(frame)), "complete_rows": int(complete_rows(frame).sum()),
                       "evaluated": evaluated, "first_evaluated": None if first is None else str(first)}
    return out


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None, allow_dirty: bool = False) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée, l'enregistrement ne prouverait pas quel code "
                        "a tourné. Exécuter depuis un arbre propre (git worktree), ou --allow-dirty pour un essai local.")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("VOL"), end.isoformat(), round(LEVEL, 6))
    say("couverture des données")
    result.coverage["series"] = check_complete(settings, list(dict.fromkeys([*symbols, MARKET])), end)
    say("audit des fuites")
    result.leak_audit = leak_audit(settings, end, settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    market = load_series(settings, MARKET, end)
    result.data_hashes[MARKET] = fingerprint(market)
    frames = {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else load_series(settings, symbol, end)
        result.data_hashes[symbol] = fingerprint(series)
        frames[symbol] = daily_frame(series, market)
    forecasts = walk_forward(frames, settings, progress=say)
    result.coverage["pairs"] = _coverage(frames, forecasts)
    result.rows = evaluate(forecasts)
    short = [f"{row.model} à {row.horizon_days} j" for row in result.rows if row.ci_qlike_diff is None]
    if short:
        raise IncompleteData(f"trop peu de jours évaluables pour un intervalle ({MIN_BLOCKS} blocs au moins) : "
                             f"{', '.join(short)} ; rien n'est enregistré")
    result.selected, result.verdict = decide(result.rows)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + result.n_trials
    _record(settings, result, now=now, symbols=symbols, forecasts=forecasts, code=state)
    return result


def _versions() -> dict[str, str]:
    """Versions des dépendances du registre, plus celles dont dépendent M5 (lightgbm) et l'IC (scipy)."""
    versions = dependency_versions()
    for name in ("lightgbm", "scipy"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "absent"
    return versions


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str],
            forecasts: pd.DataFrame | None = None, code: str = "") -> None:
    """Écrit `reports/<run_id>/summary.json` (et les prévisions hors échantillon, pour la vérification
    indépendante), puis l'entrée du registre : les 15 comparaisons comptent dans le programme."""
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"models": MODEL_TEXT, "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    if forecasts is not None:
        forecasts.to_parquet(report_dir / "forecasts.parquet", index=False)
    series = result.coverage.get("series", {})
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="prévision de volatilité : un modèle prévoit-il la variance réalisée à 1, 3 et 7 jours mieux que "
                   "la variance des 7 derniers jours ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"modèles figés ({DOC})",
        params={"horizons_days": list(HORIZONS), "models": MODEL_TEXT, "level": result.level,
                "first_forecast": FIRST_FORECAST, "min_history_days": MIN_HISTORY_DAYS, "ewma_lambda": EWMA_LAMBDA,
                "lgbm": LGBM_PARAMS | {"num_boost_round": LGBM_ROUNDS}, "min_train_rows": MIN_TRAIN_ROWS,
                "min_blocks": MIN_BLOCKS, "block_days": {str(h): block_days_for(h) for h in HORIZONS},
                "years": list(YEARS), "min_years_better": MIN_YEARS_BETTER, "min_pairs_share": MIN_PAIRS_SHARE,
                "min_coverage": MIN_COVERAGE, "threads": THREADS},
        period_label="DEVELOPMENT",
        period_start=min((entry["first"] for entry in series.values()), default=FIRST_FORECAST),
        period_end=result.period_end, universe=symbols, data_hashes=result.data_hashes,
        git_commit=code or code_state(), dependencies=_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (erreur de prévision, pas de rentabilité)",
        simulation_rules={"origins": "une par jour et par paire, à 00:00 UTC", "refit": "le 1er de chaque mois",
                          "purge": "cible terminée au plus tard à la date de réajustement",
                          "sample": "origines où les six modèles ont une prévision"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(horizon): model for horizon, model in result.selected.items()},
                 "useful": sum(1 for row in result.rows if row.useful), "rows": [asdict(row) for row in result.rows]},
        status="COMPLETED", report_dir=str(report_dir))
