"""Prévision de volatilité, protocole v2 (docs/VOLATILITY.md § 14, déclaré le 2026-10-02 avant exécution) :
combiner et enrichir les modèles du lot 7. Étape 7 du plan de travail.

Question : un candidat prévoit-il la variance réalisée à 1, 3 et 7 jours mieux que le modèle EN SERVICE (LightGBM
à 1 et 3 jours, HAR + BTC à 7 jours, réajustés ici sur les mêmes lignes) ? Erreur de prévision seulement : ni
direction, ni rentabilité, ni ordre. 4 candidats × 3 horizons = 12 comparaisons, comptées dans le programme.

Tout le cadre du lot 7 est repris tel quel (`research/volatility.py`, non modifié) : lignes journalières, cibles,
purge, réajustement mensuel, QLIKE, IC calendaires, critères. Ce module n'ajoute que :
- des variables : part négative de la variance (semi-variance / variance) sur 24, 168 et 720 h, log de la variance
  de Parkinson moyenne des bougies 1 h (plus haut / plus bas) sur 24 et 168 h, et l'indice DVOL de Deribit (BTC,
  clôture journalière connue à 00:00 UTC, jamais celle du lendemain) en log de variance horaire implicite et en
  écart à la variance réalisée de la semaine ;
- les candidats V1 (moyenne des deux modèles en service), V2 (HAR + BTC + semi-variances), V3 (LightGBM enrichi),
  V4 (HAR + BTC + DVOL, évalué sur ses seules lignes : DVOL existe depuis le 2021-03-24) ;
- un audit des fuites qui tronque et falsifie aussi la série DVOL, avec une mutation propre (DVOL du lendemain).
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from . import volatility as v1
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .long_history import load_long
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

PROTOCOL_VERSION = 2
KIND, STRATEGY, DOC = v1.KIND, "VOLATILITY_FORECAST_V2", v1.DOC
BETTER, NO_IMPROVEMENT = "MIEUX_QUE_SERVICE", "AUCUNE_AMELIORATION"
HORIZONS = v1.HORIZONS
SERVICE = {1: "M5_LGBM_POOLED", 3: "M5_LGBM_POOLED", 7: "M4_HAR_POOLED_BTC"}   # lot 7 § 12-13
REFERENCE = "REF_SERVICE"
MODELS = (REFERENCE, "V1_MEAN_M4_M5", "V2_HAR_SEMIVAR", "V3_LGBM_ENRICHED", "V4_HAR_DVOL")
CANDIDATES = MODELS[1:]
DVOL_MODEL = "V4_HAR_DVOL"
N_TRIALS = len(CANDIDATES) * len(HORIZONS)              # 12 comparaisons déclarées
LEVEL = 1 - 0.05 / N_TRIALS                             # Bonferroni, bilatéral
DVOL_START, DVOL_CURRENCY = "2021-03-24", "BTC"
DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
DVOL_CHUNK_DAYS = 365
HOURS_PER_YEAR = 365 * 24
READ_COLUMNS = ("open_time", "high", "low", "close", "available_at")
NEG = ("neg_share_d", "neg_share_w", "neg_share_m")
RANGE = ("log_park_d", "log_park_w")
DVOL = ("log_dvol_var", "dvol_spread")
EXTRA = (*NEG, *RANGE)
INPUTS = (*v1.INPUTS, *EXTRA, *DVOL)                    # tout ce que l'audit des fuites compare
COLUMNS = (*v1.COLUMNS, *EXTRA, *DVOL)
V2_FEATURES = (*v1.OWN, *v1.MARKET_FEATURES, *NEG)
V3_FEATURES = (*v1.FEATURES, *NEG, *RANGE)
V4_FEATURES = (*v1.OWN, *v1.MARKET_FEATURES, *DVOL)
MODEL_TEXT = {
    REFERENCE: "référence : le modèle en service à cet horizon (LightGBM commun à 1 et 3 jours, HAR commun + BTC à 7 jours), "
               "mêmes réglages que le lot 7, réajusté sur les mêmes lignes que les candidats",
    "V1_MEAN_M4_M5": "moyenne arithmétique des variances prévues par HAR + BTC et LightGBM (combinaison, sans réglage)",
    "V2_HAR_SEMIVAR": "HAR commun + BTC + part négative de la variance sur 24, 168 et 720 h",
    "V3_LGBM_ENRICHED": "LightGBM commun : variables de M5 + parts négatives + log de la variance de Parkinson sur 24 et 168 h",
    "V4_HAR_DVOL": "HAR commun + BTC + log de la variance horaire implicite DVOL (BTC) + écart DVOL − variance réalisée 168 h ; "
                   "lignes à DVOL connu seulement (depuis le 2021-03-24)",
}
FORECAST_COLUMNS = v1.FORECAST_COLUMNS


@dataclass
class Row(v1.Row):
    years_needed: int = 0


@dataclass
class Result(v1.Result):
    n_trials: int = N_TRIALS


# --- Variables supplémentaires et DVOL -------------------------------------------------------------------------

def _extras(h1: pd.DataFrame) -> pd.DataFrame:
    """Par origine (00:00 UTC, clôture de la bougie de 23:00), sur la même grille horaire que le lot 7 : part
    négative de la variance (moyenne des carrés des rendements négatifs / moyenne des carrés) sur 24, 168 et 720 h
    et log de la variance de Parkinson moyenne, ln(haut/bas)² / (4 ln 2) par bougie, sur 24 et 168 h. Fenêtres
    terminées à la bougie de décision incluse ; couverture d'au moins 95 % des heures, sinon manquant."""
    frame = h1[list(READ_COLUMNS)].dropna(subset=["open_time", "close", "available_at"]).sort_values("open_time")
    if frame.empty:
        return pd.DataFrame(columns=["origin", *EXTRA])
    times = v1._utc(frame["open_time"]).dt.as_unit("ns")
    grid = pd.date_range(times.iloc[0], times.iloc[-1], freq=v1.STEP)
    frame = frame.assign(open_time=times.to_numpy()).set_index("open_time").reindex(grid)
    close = frame["close"].to_numpy(float)
    exists = np.isfinite(close)
    log_close = np.log(np.where(close > 0, close, np.nan))
    diff = np.full(len(frame), np.nan)
    diff[1:] = np.diff(log_close)
    squared = diff ** 2
    negative = np.where(diff < 0, squared, np.where(np.isfinite(diff), 0.0, np.nan))
    high, low = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        park = np.where((high > 0) & (low > 0) & (high >= low), np.log(high / low) ** 2 / (4 * math.log(2)), np.nan)
    closes = pd.DatetimeIndex(grid) + v1.STEP
    rows = np.flatnonzero(np.asarray(closes == closes.floor("D")) & exists)
    out: dict[str, np.ndarray | pd.DatetimeIndex] = {"origin": closes[rows]}
    for name, length in zip(NEG, (v1.DAY_HOURS, v1.WEEK_HOURS, v1.MONTH_HOURS), strict=True):
        var = v1._window_means(squared, rows - length + 1, length)
        neg = v1._window_means(negative, rows - length + 1, length)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[name] = np.where(var > 0, neg / var, np.nan)
    for name, length in zip(RANGE, (v1.DAY_HOURS, v1.WEEK_HOURS), strict=True):
        mean = v1._window_means(park, rows - length + 1, length)
        out[name] = np.log(np.where(mean > 0, mean, np.nan))
    return pd.DataFrame(out)


def _days(index) -> pd.DatetimeIndex:
    """Débuts de journée UTC d'une série DVOL (index naïf ou non)."""
    days = pd.DatetimeIndex(index)
    return days.tz_localize("UTC") if days.tz is None else days.tz_convert("UTC")


def with_dvol(daily: pd.DataFrame, dvol: pd.Series | None, *, leaky: bool = False) -> pd.DataFrame:
    """Joint vers le passé la clôture journalière de DVOL (indexée par le début de sa journée) connue à la décision :
    la clôture du jour, à 00:00 UTC, jamais celle du lendemain. `leaky` : mutation de l'audit, la bougie DVOL de la
    journée qui COMMENCE à l'origine est lue. Variables : log de la variance horaire implicite (DVOL en % annualisé)
    et son écart à la log-variance réalisée des 168 dernières heures."""
    out = daily.copy()
    if dvol is None or dvol.empty:
        out[list(DVOL)] = np.nan
        return out
    known_at = (_days(dvol.index) + pd.Timedelta(days=0 if leaky else 1)).as_unit("ns")
    table = pd.DataFrame({"_dvol_at": known_at, "_dvol": dvol.to_numpy(float)}).sort_values("_dvol_at")
    joined = pd.merge_asof(out.sort_values("decision_available_at"), table, left_on="decision_available_at",
                           right_on="_dvol_at", direction="backward", tolerance=v1.MAX_MARKET_AGE)
    hourly_var = (joined["_dvol"].to_numpy(float) / 100) ** 2 / HOURS_PER_YEAR
    joined["log_dvol_var"] = np.log(np.where(hourly_var > 0, hourly_var, np.nan))
    joined["dvol_spread"] = joined["log_dvol_var"] - joined["log_var_w"]
    return joined.drop(columns=["_dvol_at", "_dvol"]).sort_values("origin").reset_index(drop=True)


def daily_frame(h1: pd.DataFrame, btc_h1: pd.DataFrame | None = None, dvol: pd.Series | None = None, *,
                leaky: bool = False, leaky_dvol: bool = False) -> pd.DataFrame:
    """Lignes journalières du lot 7 (`volatility.daily_frame`, inchangé) + variables v2 + DVOL."""
    base = v1.daily_frame(h1[list(v1.READ_COLUMNS)], None if btc_h1 is None else btc_h1[list(v1.READ_COLUMNS)],
                          leaky=leaky)
    if base.empty:
        return base.assign(**dict.fromkeys((*EXTRA, *DVOL), np.nan))[list(COLUMNS)]
    base = base.assign(origin=base["origin"].dt.as_unit("ns"))
    merged = base.merge(_extras(h1), on="origin", how="left")
    return with_dvol(merged, dvol, leaky=leaky_dvol)[list(COLUMNS)]


def complete_rows(data: pd.DataFrame) -> pd.Series:
    """Lignes complètes du lot 7 dont les variables v2 (hors DVOL) sont aussi connues."""
    return v1.complete_rows(data) & np.isfinite(data[list(EXTRA)].to_numpy(float)).all(axis=1)


def dvol_rows(data: pd.DataFrame) -> np.ndarray:
    return np.isfinite(data[list(DVOL)].to_numpy(float)).all(axis=1)


# --- Modèles (réglages du lot 7, aucun nouveau réglage) ---------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Models:
    har_btc: v1.Har | None          # M4, réajusté ici
    trees: v1.Trees | None          # M5, réajusté ici
    har_semivar: v1.Har | None      # V2
    trees_enriched: v1.Trees | None  # V3
    har_dvol: v1.Har | None         # V4
    rows: int
    rows_dvol: int


def training_rows(data: pd.DataFrame, refit: pd.Timestamp, horizon: int) -> pd.DataFrame:
    """Purge du lot 7 (`volatility.training_rows`) sur les lignes complètes au sens v2."""
    purged = data["origin"] + pd.Timedelta(days=horizon) <= refit
    return data[complete_rows(data) & (data[f"rv2_{horizon}"] > 0) & purged]


def fit_at(data: pd.DataFrame, refit: pd.Timestamp, horizon: int, *, seed: int) -> Models:
    train = training_rows(data, refit, horizon)
    y = np.log(train[f"rv2_{horizon}"].to_numpy(float))
    has = dvol_rows(train)
    return Models(v1.fit_har(train[[*v1.OWN, *v1.MARKET_FEATURES]].to_numpy(float), y),
                  v1.fit_lgbm(train[list(v1.FEATURES)].to_numpy(float), y, seed=seed),
                  v1.fit_har(train[list(V2_FEATURES)].to_numpy(float), y),
                  v1.fit_lgbm(train[list(V3_FEATURES)].to_numpy(float), y, seed=seed),
                  v1.fit_har(train.loc[has, list(V4_FEATURES)].to_numpy(float), y[has]),
                  int(len(train)), int(has.sum()))


def month_forecasts(models: Models, rows: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Variance prévue par la référence et les quatre candidats pour les origines `rows` (NaN si un modèle n'a pas
    pu être ajusté ; V4 : NaN aussi quand DVOL est inconnu à l'origine), avec la variance réalisée."""
    out = rows[["symbol", "origin"]].reset_index(drop=True)
    out["realized"] = rows[f"rv2_{horizon}"].to_numpy(float)
    nan = np.full(len(rows), np.nan)
    m4 = nan if models.har_btc is None else models.har_btc.predict(rows[[*v1.OWN, *v1.MARKET_FEATURES]].to_numpy(float))
    m5 = nan if models.trees is None else models.trees.predict(rows[list(v1.FEATURES)].to_numpy(float))
    out[REFERENCE] = m5 if SERVICE[horizon] == "M5_LGBM_POOLED" else m4
    out["V1_MEAN_M4_M5"] = (m4 + m5) / 2
    out["V2_HAR_SEMIVAR"] = nan if models.har_semivar is None else models.har_semivar.predict(rows[list(V2_FEATURES)].to_numpy(float))
    out["V3_LGBM_ENRICHED"] = (nan if models.trees_enriched is None
                               else models.trees_enriched.predict(rows[list(V3_FEATURES)].to_numpy(float)))
    v4 = nan.copy()
    has = dvol_rows(rows)
    if models.har_dvol is not None and has.any():
        v4[has] = models.har_dvol.predict(rows.loc[has, list(V4_FEATURES)].to_numpy(float))
    out[DVOL_MODEL] = v4
    return out


def walk_forward(frames: dict[str, pd.DataFrame], settings: Settings, *,
                 progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    """Calendrier du lot 7 : réajustement le 1er du mois, origines éligibles (lignes complètes v2, 400 jours
    d'historique, ≥ FIRST_FORECAST, cible dans DEVELOPMENT). Échantillon commun = origines où la référence, V1, V2
    et V3 ont une prévision ; V4 reste NaN là où DVOL manque (il est comparé à la référence sur ses seules lignes)."""
    say = progress or (lambda _text: None)
    empty = pd.DataFrame(columns=list(FORECAST_COLUMNS))
    end = pd.Timestamp(development_end(settings))
    first = pd.Timestamp(v1.FIRST_FORECAST, tz="UTC")
    tables = [frame.assign(symbol=symbol) for symbol, frame in frames.items() if len(frame)]
    if not tables:
        return empty
    data = pd.concat(tables, ignore_index=True)
    origins = data["origin"]
    eligible = complete_rows(data) & (data["history_days"] >= v1.MIN_HISTORY_DAYS) & (origins >= first)
    common = [m for m in MODELS if m != DVOL_MODEL]
    parts: list[pd.DataFrame] = []
    for horizon in HORIZONS:
        say(f"prévisions à {horizon} j")
        last_target_bar = origins + pd.Timedelta(days=horizon) - v1.STEP
        scored = (eligible & (data[f"rv2_{horizon}"] > 0) & (last_target_bar <= end)).to_numpy()
        if not scored.any():
            continue
        for refit in pd.date_range(first.replace(day=1), origins[scored].max(), freq="MS"):
            month = scored & ((origins >= refit) & (origins < refit + pd.offsets.MonthBegin(1))).to_numpy()
            if not month.any():
                continue
            say(f"prévisions à {horizon} j — {refit:%Y-%m}")
            models = fit_at(data, refit, horizon, seed=settings.protocol.seed)
            wide = month_forecasts(models, data[month], horizon)
            values = wide[common].to_numpy(float)
            wide = wide[(np.isfinite(values) & (values > 0)).all(axis=1)]
            long = wide.melt(id_vars=["symbol", "origin", "realized"], value_vars=list(MODELS), var_name="model",
                             value_name="forecast").assign(horizon=horizon)
            parts.append(long[list(FORECAST_COLUMNS)])
    if not parts:
        return empty
    return pd.concat(parts, ignore_index=True).sort_values(["horizon", "origin", "symbol", "model"]).reset_index(drop=True)


# --- Mesures et règle (celles du lot 7, référence = modèle en service) -----------------------------------------

def _row(model: str, horizon: int, table: pd.DataFrame) -> Row:
    """Compare un candidat à la référence pour un horizon ; critère des années = toutes les années couvertes sauf
    au plus une (6 sur 7 pour V1 à V3 ; 4 sur 5 pour V4, dont les lignes commencent en 2021)."""
    criteria = dict.fromkeys(("ci_upper_below_zero", "years", "pairs", "secondary_loss"), False)
    if table.empty:
        return Row(model, horizon, 0, 0, 0, None, None, None, None, {}, 0, None, None, None, None, criteria, False, 0)
    losses = table.assign(diff=table["qlike"] - table["qlike_base"], log_diff=table["log_error"] - table["log_error_base"])
    daily = losses.groupby("origin")[["qlike", "qlike_base", "diff", "log_error", "log_error_base", "log_diff"]].mean().sort_index()
    ci = calendar_mean_ci(daily["diff"].to_numpy(float), daily.index, block_days=v1.block_days_for(horizon),
                          min_blocks=v1.MIN_BLOCKS, level=LEVEL)
    by_year = daily["diff"].groupby(daily.index.year).mean()
    by_pair = losses.groupby("symbol")["diff"].mean()
    covered = [year for year in v1.YEARS if year in by_year.index]
    needed = min(v1.MIN_YEARS_BETTER, max(len(covered) - 1, 1))
    years_better = sum(1 for year in covered if by_year[year] < 0)
    pairs_share = float((by_pair < 0).mean())
    mean = daily.mean()
    criteria = {"ci_upper_below_zero": ci is not None and ci[1] < 0, "years": years_better >= needed,
                "pairs": pairs_share >= v1.MIN_PAIRS_SHARE, "secondary_loss": bool(mean["log_diff"] < 0)}
    return Row(model, horizon, int(len(losses)), int(len(daily)), int(len(by_pair)), v1._rounded(mean["qlike"]),
               v1._rounded(mean["qlike_base"]), v1._rounded(mean["diff"]), ci,
               {str(year): v1._rounded(value) for year, value in by_year.items()}, years_better, round(pairs_share, 4),
               v1._rounded(mean["log_error"]), v1._rounded(mean["log_error_base"]), v1._rounded(mean["log_diff"]),
               criteria, all(criteria.values()), needed)


def evaluate(forecasts: pd.DataFrame) -> list[Row]:
    """Les 12 comparaisons déclarées : chaque candidat contre la référence, à chaque horizon, sur les lignes où les
    deux ont une prévision (toutes pour V1 à V3 ; celles à DVOL connu pour V4)."""
    rows = []
    for horizon in HORIZONS:
        part = forecasts[forecasts["horizon"] == horizon]
        if part.empty:
            rows += [_row(model, horizon, pd.DataFrame()) for model in CANDIDATES]
            continue
        wide = part.pivot(index=["symbol", "origin"], columns="model", values="forecast")
        realized = part.drop_duplicates(["symbol", "origin"]).set_index(["symbol", "origin"])["realized"].reindex(wide.index)
        for model in CANDIDATES:
            if model not in wide.columns or REFERENCE not in wide.columns:
                rows.append(_row(model, horizon, pd.DataFrame()))
                continue
            both = wide[[REFERENCE, model]].assign(realized=realized).dropna().reset_index()
            rows.append(_row(model, horizon, pd.DataFrame({
                "symbol": both["symbol"], "origin": both["origin"],
                "qlike": v1.qlike(both["realized"], both[model]), "qlike_base": v1.qlike(both["realized"], both[REFERENCE]),
                "log_error": v1.log_error(both["realized"], both[model]),
                "log_error_base": v1.log_error(both["realized"], both[REFERENCE])})))
    return rows


def decide(rows: list[Row]) -> tuple[dict[int, str], str]:
    selected = {}
    for horizon in HORIZONS:
        useful = [row for row in rows if row.horizon_days == horizon and row.useful and row.qlike is not None]
        best = min(useful, key=lambda row: (row.qlike or 0.0, MODELS.index(row.model)), default=None)
        selected[horizon] = best.model if best is not None else NO_IMPROVEMENT
    verdict = BETTER if any(model != NO_IMPROVEMENT for model in selected.values()) else NO_IMPROVEMENT
    return selected, verdict


# --- Données : bougies (haut, bas compris) et DVOL ---------------------------------------------------------------

def load_series(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h du magasin long avec plus haut et plus bas, coupées à `end` et jamais au-delà de DEVELOPMENT."""
    frame = load_long(settings, symbol)
    limit = min(pd.Timestamp(end), pd.Timestamp(development_end(settings)))
    return frame.loc[frame["open_time"] <= limit, list(READ_COLUMNS)].reset_index(drop=True)


def dvol_path(settings: Settings) -> Path:
    return settings.data_dir / "options" / f"dvol_{DVOL_CURRENCY.lower()}.parquet"


def fetch_dvol_history(*, start: str = DVOL_START, end: pd.Timestamp | None = None, client=None) -> pd.Series:
    """Clôtures journalières de DVOL (Deribit, API publique en liste blanche), par tranches d'un an ; indexées par le
    début de chaque journée UTC."""
    from ..forward.sources import PublicSources
    source = client or PublicSources()
    begin = pd.Timestamp(start, tz="UTC")
    stop = (pd.Timestamp(end) if end is not None else pd.Timestamp.now(tz="UTC")).floor("D")
    rows: dict[pd.Timestamp, float] = {}
    try:
        while begin < stop:
            chunk_end = min(begin + pd.Timedelta(days=DVOL_CHUNK_DAYS), stop)
            payload = source.get_json(DVOL_URL, {"currency": DVOL_CURRENCY, "start_timestamp": int(begin.timestamp() * 1000),
                                                 "end_timestamp": int(chunk_end.timestamp() * 1000), "resolution": "1D"})
            for item in (payload.get("result") or {}).get("data") or []:
                rows[pd.Timestamp(int(item[0]), unit="ms", tz="UTC").floor("D")] = float(item[4])
            begin = chunk_end
    finally:
        if client is None:
            source.close()
    return pd.Series(rows, dtype=float).sort_index()


def load_dvol(settings: Settings, *, end: pd.Timestamp, fetcher: Callable[..., pd.Series] | None = None) -> pd.Series:
    """Série en cache (data/options), téléchargée une fois si absente ; journées commencées avant `end`."""
    path = dvol_path(settings)
    if path.exists():
        frame = pd.read_parquet(path)
        series = pd.Series(frame["close"].to_numpy(float), index=pd.DatetimeIndex(frame["time"]).tz_convert("UTC"))
    else:
        series = (fetcher or fetch_dvol_history)(end=end)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"time": series.index, "close": series.to_numpy(float)}).to_parquet(path, index=False)
    return series[series.index < pd.Timestamp(end)]


# --- Audit des fuites ---------------------------------------------------------------------------------------------

def causality_violations(h1: pd.DataFrame, btc_h1: pd.DataFrame | None, dvol: pd.Series | None, *,
                         origins: list[pd.Timestamp], seed: int = 0, leaky: bool = False, leaky_dvol: bool = False) -> list[dict]:
    """Comme au lot 7, sur INPUTS v2 : bougies ET série DVOL tronquées à la décision, puis futur falsifié ; tout ce
    qui est lu à l'origine doit égaler le calcul complet. `leaky` / `leaky_dvol` : les deux mutations de l'audit."""
    full = daily_frame(h1, btc_h1, dvol, leaky=leaky, leaky_dvol=leaky_dvol).set_index("origin")
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for origin in origins:
        if origin not in full.index:
            problems.append({"origin": str(origin), "check": "calcul complet", "features": ["(origine absente)"]})
            continue
        known_at = full.loc[origin, "decision_available_at"]

        def cut(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            return frame[v1._utc(frame["available_at"]) <= at]

        def falsify(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            frame = frame.copy()
            future = (v1._utc(frame["available_at"]) > at).to_numpy()
            for column in frame.columns:
                if (column not in ("open_time", "close_time", "available_at", "ingested_at")
                        and pd.api.types.is_numeric_dtype(frame[column]) and not pd.api.types.is_bool_dtype(frame[column])):
                    frame[column] = frame[column].astype(float)
                    frame.loc[future, column] = frame.loc[future, column] * rng.uniform(0.5, 1.5, int(future.sum()))
            return frame

        def cut_dvol(series: pd.Series, at=known_at) -> pd.Series:
            return series[(_days(series.index) + pd.Timedelta(days=1)) <= at]

        def falsify_dvol(series: pd.Series, at=known_at) -> pd.Series:
            series = series.copy()
            future = np.asarray((_days(series.index) + pd.Timedelta(days=1)) > at)
            series[future] = series[future] * rng.uniform(0.5, 1.5, int(future.sum()))
            return series

        expected = full.loc[origin, list(INPUTS)].to_numpy(float)
        for label, change, change_dvol in (("tronqué", cut, cut_dvol), ("futur falsifié", falsify, falsify_dvol)):
            pair = change(h1)
            market = None if btc_h1 is None else pair if btc_h1 is h1 else change(btc_h1)
            series = None if dvol is None else change_dvol(dvol)
            got = daily_frame(pair, market, series, leaky=leaky, leaky_dvol=leaky_dvol).set_index("origin")
            if origin not in got.index:
                problems.append({"origin": str(origin), "check": label, "features": ["(origine absente)"]})
                continue
            values = got.loc[origin, list(INPUTS)].to_numpy(float)
            differs = ~np.isclose(values, expected, rtol=1e-9, atol=0.0, equal_nan=True)
            if differs.any():
                problems.append({"origin": str(origin), "check": label,
                                 "features": [name for name, d in zip(INPUTS, differs, strict=True) if d]})
    return problems


def leak_audit(settings: Settings, end: pd.Timestamp, seed: int, dvol: pd.Series) -> dict:
    """BTC, ETH et SOL exigés ; 3 origines à DVOL connu par paire ; aucune différence tolérée ; les deux mutations
    (fenêtre « jour » avancée d'une heure ; DVOL du lendemain) doivent être détectées sur chaque paire."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(v1.AUDIT_PAIRS, False)
    detected_dvol = dict.fromkeys(v1.AUDIT_PAIRS, False)
    checked: list[str] = []
    origins_checked: dict[str, list[str]] = {}
    try:
        market = load_series(settings, MARKET, end)
    except MissingData:
        market = None
    for symbol in v1.AUDIT_PAIRS:
        try:
            series = market if symbol == MARKET else load_series(settings, symbol, end)
        except MissingData:
            continue
        if series is None or market is None:
            continue
        full, mutated = daily_frame(series, market, dvol), daily_frame(series, market, dvol, leaky=True)
        ready = full.loc[(complete_rows(full) & dvol_rows(full) & mutated["log_var_d"].notna()).to_numpy(), "origin"]
        if len(ready) < v1.AUDIT_ORIGINS:
            continue
        picks = np.sort(rng.choice(len(ready), size=v1.AUDIT_ORIGINS, replace=False))
        origins = [ready.iloc[int(index)] for index in picks]
        violations += [v | {"symbol": symbol} for v in causality_violations(series, market, dvol, origins=origins, seed=seed)]
        found = causality_violations(series, market, dvol, origins=origins, seed=seed, leaky=True)
        detected[symbol] = any("log_var_d" in v["features"] for v in found)
        found = causality_violations(series, market, dvol, origins=origins, seed=seed, leaky_dvol=True)
        detected_dvol[symbol] = any("log_dvol_var" in v["features"] for v in found)
        checked.append(symbol)
        origins_checked[symbol] = [str(origin) for origin in origins]
    complete = checked == list(v1.AUDIT_PAIRS)
    return {"violations": violations, "mutation_detected": detected, "dvol_mutation_detected": detected_dvol,
            "checked_pairs": checked, "origins": origins_checked, "origins_per_pair": v1.AUDIT_ORIGINS,
            "passed": complete and not violations and all(detected.values()) and all(detected_dvol.values())}


# --- Exécution et enregistrement ---------------------------------------------------------------------------------

def _coverage(frames: dict[str, pd.DataFrame], forecasts: pd.DataFrame) -> dict:
    out = {}
    for model in (REFERENCE, DVOL_MODEL):
        part = forecasts[(forecasts["model"] == model) & forecasts["forecast"].notna()]
        counts = part.groupby(["symbol", "horizon"])["origin"].size() if len(part) else None
        out[model] = {symbol: {str(h): int(counts.loc[(symbol, h)]) if counts is not None and (symbol, h) in counts.index else 0
                               for h in HORIZONS} for symbol in frames}
    out["rows"] = {symbol: {"daily_rows": int(len(frame)), "complete_rows": int(complete_rows(frame).sum()),
                            "dvol_rows": int(dvol_rows(frame).sum())} for symbol, frame in frames.items()}
    return out


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, dvol_fetcher: Callable[..., pd.Series] | None = None) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("VOL"), end.isoformat(), round(LEVEL, 6))
    say("couverture des données")
    result.coverage["series"] = v1.check_complete(settings, list(dict.fromkeys([*symbols, MARKET])), end)
    say("DVOL")
    dvol = load_dvol(settings, end=end, fetcher=dvol_fetcher)
    if dvol.empty:
        raise v1.IncompleteData("DVOL absent : aucune journée connue avant la fin de DEVELOPMENT")
    result.coverage["dvol"] = {"first": str(dvol.index.min()), "last": str(dvol.index.max()), "days": int(len(dvol))}
    result.data_hashes["DVOL"] = fingerprint(pd.DataFrame({"time": dvol.index, "close": dvol.to_numpy(float)}))
    say("audit des fuites")
    result.leak_audit = leak_audit(settings, end, settings.protocol.seed, dvol)
    if not result.leak_audit["passed"]:
        raise v1.LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    market = load_series(settings, MARKET, end)
    result.data_hashes[MARKET] = fingerprint(market)
    frames = {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else load_series(settings, symbol, end)
        result.data_hashes[symbol] = fingerprint(series)
        frames[symbol] = daily_frame(series, market, dvol)
    forecasts = walk_forward(frames, settings, progress=say)
    result.coverage["pairs"] = _coverage(frames, forecasts)
    rows = evaluate(forecasts)
    result.rows = list(rows)
    short = [f"{row.model} à {row.horizon_days} j" for row in rows if row.ci_qlike_diff is None]
    if short:
        raise v1.IncompleteData(f"trop peu de jours évaluables pour un intervalle ({v1.MIN_BLOCKS} blocs au moins) : "
                                f"{', '.join(short)} ; rien n'est enregistré")
    result.selected, result.verdict = decide(rows)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, forecasts=forecasts, code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str],
            forecasts: pd.DataFrame | None = None, code: str = "") -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"models": MODEL_TEXT, "service": {str(h): m for h, m in SERVICE.items()},
                                "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if forecasts is not None:
        forecasts.to_parquet(report_dir / "forecasts.parquet", index=False)
    series = result.coverage.get("series", {})
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="prévision de volatilité v2 : la combinaison des modèles en service, les semi-variances, l'étendue des "
                   "bougies ou l'indice DVOL prévoient-ils la variance réalisée à 1, 3 et 7 jours mieux que le modèle en service ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"candidats figés ({DOC} § 14)",
        params={"horizons_days": list(HORIZONS), "models": MODEL_TEXT, "service": {str(h): m for h, m in SERVICE.items()},
                "level": result.level, "first_forecast": v1.FIRST_FORECAST, "min_history_days": v1.MIN_HISTORY_DAYS,
                "lgbm": v1.LGBM_PARAMS | {"num_boost_round": v1.LGBM_ROUNDS}, "min_train_rows": v1.MIN_TRAIN_ROWS,
                "min_blocks": v1.MIN_BLOCKS, "block_days": {str(h): v1.block_days_for(h) for h in HORIZONS},
                "years": list(v1.YEARS), "min_years_better": "années couvertes − 1, au plus 6", "min_pairs_share": v1.MIN_PAIRS_SHARE,
                "dvol": {"currency": DVOL_CURRENCY, "start": DVOL_START, "latency": "clôture du jour connue à 00:00 UTC"},
                "threads": v1.THREADS},
        period_label="DEVELOPMENT",
        period_start=min((entry["first"] for entry in series.values()), default=v1.FIRST_FORECAST),
        period_end=result.period_end, universe=symbols, data_hashes=result.data_hashes,
        git_commit=code or code_state(), dependencies=v1._versions() | dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (erreur de prévision, pas de rentabilité)",
        simulation_rules={"origins": "une par jour et par paire, à 00:00 UTC", "refit": "le 1er de chaque mois",
                          "purge": "cible terminée au plus tard à la date de réajustement",
                          "sample": "origines où référence, V1, V2 et V3 ont une prévision ; V4 sur ses lignes à DVOL connu"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(horizon): model for horizon, model in result.selected.items()},
                 "useful": sum(1 for row in result.rows if row.useful), "rows": [asdict(row) for row in result.rows]},
        status="COMPLETED", report_dir=str(report_dir))
