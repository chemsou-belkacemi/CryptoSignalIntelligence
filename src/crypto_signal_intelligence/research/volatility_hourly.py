"""Prévision de volatilité à toute heure, protocole v3 (docs/VOLATILITY.md § 16, déclaré le 2026-10-02 avant exécution).
Piste 2 de l'étude du 2026-10-02 : la volatilité des 4 h et 24 h qui suivent CHAQUE clôture 1 h, celle qui sert aux
stops et aux objectifs, contre la règle du tableau de bord (volatilité des 24 dernières heures).

Question : à chaque clôture 1 h, un modèle prévoit-il la variance réalisée des 4 et 24 prochaines heures mieux que la
règle R0 « variance horaire moyenne des 24 dernières heures × H » ? Erreur de prévision seulement : ni direction,
ni rentabilité, ni ordre. 2 candidats × 2 horizons = 4 comparaisons, comptées dans le programme.

Cadre du lot 7 repris (`research/volatility.py`, non modifié : fenêtres tolérantes à 5 % d'heures manquantes, cibles
contiguës, HAR et LightGBM aux mêmes réglages, QLIKE, IC calendaires, critères), avec trois différences déclarées :
origines à toutes les heures (pas seulement 00:00), réajustement le 1er de chaque TRIMESTRE sur une fenêtre
glissante de 3 ans (volume de données), et un « profil » heure × jour de semaine appris sur l'entraînement.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from . import volatility as v1
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

PROTOCOL_VERSION = 3
KIND, STRATEGY, DOC = v1.KIND, "VOLATILITY_HOURLY", v1.DOC
USEFUL, NO_IMPROVEMENT = v1.USEFUL, v1.NO_IMPROVEMENT
HORIZONS = (4, 24)                                      # heures
MODELS = ("R0_RECENT_24H", "H1_HAR_PROFILE", "H2_LGBM_PROFILE")
BASELINE, CANDIDATES = MODELS[0], MODELS[1:]
N_TRIALS = len(CANDIDATES) * len(HORIZONS)              # 4 comparaisons déclarées
LEVEL = 1 - 0.05 / N_TRIALS
FIRST_FORECAST = v1.FIRST_FORECAST
MIN_HISTORY_DAYS = v1.MIN_HISTORY_DAYS
TRAIN_WINDOW = pd.Timedelta(days=3 * 365)               # fenêtre glissante d'entraînement
REFIT_FREQ = "QS"                                       # 1er janvier, avril, juillet, octobre
WINDOWS = {"log_var_24": 24, "log_var_168": 168, "log_var_720": 720}
OWN = tuple(WINDOWS)
MARKET_FEATURES = tuple(f"btc_{name}" for name in OWN)
CALENDAR = ("hour", "dow")
PROFILE = "profile"
H1_FEATURES = (*OWN, *MARKET_FEATURES, PROFILE)
H2_FEATURES = (*OWN, *MARKET_FEATURES, PROFILE, *CALENDAR)
INPUTS = (*OWN, *MARKET_FEATURES, *CALENDAR, "var_24", "history_days")
TARGETS = tuple(f"rv2_{h}" for h in HORIZONS)
COLUMNS = ("origin", "decision_available_at", "history_days", "var_24", *OWN, *MARKET_FEATURES, *CALENDAR, *TARGETS)
MODEL_TEXT = {
    "R0_RECENT_24H": "référence : variance horaire moyenne des 24 dernières heures × H (règle du tableau de bord, sur bougies 1 h)",
    "H1_HAR_PROFILE": "HAR commun : log RV_H² sur les log-variances 24 h, 168 h, 720 h de la paire et de BTC + profil heure × jour "
                      "de semaine (moyenne de log RV_H² par case sur l'entraînement)",
    "H2_LGBM_PROFILE": "LightGBM commun : variables de H1 + heure et jour de semaine (300 arbres, 15 feuilles, réglages du lot 7)",
}


@dataclass
class Result(v1.Result):
    n_trials: int = N_TRIALS


def block_days_for(horizon_h: int) -> int:
    return max(10, 2 * -(-horizon_h // 24))


# --- Lignes horaires ---------------------------------------------------------------------------------------------

def hourly_frame(h1: pd.DataFrame, btc_h1: pd.DataFrame | None = None, *, leaky: bool = False) -> pd.DataFrame:
    """Une ligne par bougie 1 h close : `origin` = clôture, variables connues à l'origine (fenêtres terminées à la
    bougie incluse, couverture ≥ 95 %), heure et jour de semaine de l'origine, cibles rv2_4 et rv2_24 (sommes
    CONTIGUËS des carrés des rendements qui suivent). Variables de BTC jointes vers le passé sur `available_at`.
    `leaky` : mutation de l'audit, la fenêtre 24 h avance d'une heure et lit la première heure après l'origine."""
    frame = h1[list(v1.READ_COLUMNS)].dropna().sort_values("open_time").reset_index(drop=True)
    empty = pd.DataFrame(columns=list(COLUMNS))
    if frame.empty:
        return empty
    times = v1._utc(frame["open_time"]).dt.as_unit("ns")
    grid = pd.date_range(times.iloc[0], times.iloc[-1], freq=v1.STEP)
    frame = frame.assign(open_time=times.to_numpy()).set_index("open_time").reindex(grid)
    close = frame["close"].to_numpy(float)
    exists = np.isfinite(close)
    log_close = np.log(np.where(close > 0, close, np.nan))
    squared = np.full(len(frame), np.nan)
    squared[1:] = np.diff(log_close) ** 2
    rows = np.flatnonzero(exists)
    origin = pd.DatetimeIndex(grid)[rows] + v1.STEP
    out: dict = {"origin": origin,
                 "decision_available_at": v1._utc(frame["available_at"]).dt.as_unit("ns").to_numpy()[rows],
                 "history_days": ((origin - grid[0]) / pd.Timedelta(days=1)).to_numpy(float)}
    for name, length in WINDOWS.items():
        shift = int(leaky) if length == 24 else 0
        values = v1._window_means(squared, rows - length + 1 + shift, length)
        if length == 24:
            out["var_24"] = values
        out[name] = np.log(np.where(values > 0, values, np.nan))
    out["hour"], out["dow"] = origin.hour.to_numpy(float), origin.dayofweek.to_numpy(float)
    for horizon in HORIZONS:
        out[f"rv2_{horizon}"] = v1._window_sums(squared, rows + 1, horizon)
    own = pd.DataFrame(out)
    market = None if btc_h1 is None else (own if btc_h1 is h1 else hourly_frame(btc_h1))
    if market is None or market.empty:
        return own.assign(**dict.fromkeys(MARKET_FEATURES, np.nan))[list(COLUMNS)]
    market = market[["decision_available_at", *OWN]].rename(
        columns={"decision_available_at": "_market_at"} | dict(zip(OWN, MARKET_FEATURES, strict=True)))
    joined = pd.merge_asof(own.sort_values("decision_available_at"), market.sort_values("_market_at"),
                           left_on="decision_available_at", right_on="_market_at", direction="backward",
                           tolerance=v1.MAX_MARKET_AGE)
    return joined.sort_values("origin").reset_index(drop=True)[list(COLUMNS)]


def complete_rows(data: pd.DataFrame) -> pd.Series:
    finite = np.isfinite(data[list(INPUTS)].to_numpy(float)).all(axis=1)
    return pd.Series(finite, index=data.index) & (data["var_24"] > 0)


# --- Modèles ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Models:
    har: v1.Har | None
    trees: v1.Trees | None
    profile: pd.Series                      # log RV_H² moyen par (jour de semaine, heure) sur l'entraînement
    profile_default: float
    rows: int


def training_rows(data: pd.DataFrame, refit: pd.Timestamp, horizon: int) -> pd.DataFrame:
    """Purge du lot 7 (cible terminée au plus tard à `refit`) sur une fenêtre glissante de TRAIN_WINDOW."""
    ends = data["origin"] + pd.Timedelta(hours=horizon)
    keep = complete_rows(data) & (data[f"rv2_{horizon}"] > 0) & (ends <= refit) & (data["origin"] > refit - TRAIN_WINDOW)
    return data[keep]


def with_profile(rows: pd.DataFrame, profile: pd.Series, default: float) -> pd.DataFrame:
    key = pd.MultiIndex.from_arrays([rows["dow"].to_numpy(int), rows["hour"].to_numpy(int)])
    values = profile.reindex(key).to_numpy(float)
    return rows.assign(**{PROFILE: np.where(np.isfinite(values), values, default)})


def fit_at(data: pd.DataFrame, refit: pd.Timestamp, horizon: int, *, seed: int) -> Models:
    train = training_rows(data, refit, horizon)
    y = np.log(train[f"rv2_{horizon}"].to_numpy(float))
    if len(train) < v1.MIN_TRAIN_ROWS:
        return Models(None, None, pd.Series(dtype=float), float("nan"), int(len(train)))
    profile = pd.Series(y, index=pd.MultiIndex.from_arrays([train["dow"].to_numpy(int), train["hour"].to_numpy(int)])).groupby(level=[0, 1]).mean()
    default = float(np.mean(y))
    train = with_profile(train, profile, default)
    return Models(v1.fit_har(train[list(H1_FEATURES)].to_numpy(float), y),
                  v1.fit_lgbm(train[list(H2_FEATURES)].to_numpy(float), y, seed=seed), profile, default, int(len(train)))


def quarter_forecasts(models: Models, rows: pd.DataFrame, horizon: int) -> pd.DataFrame:
    out = rows[["symbol", "origin"]].reset_index(drop=True)
    out["realized"] = rows[f"rv2_{horizon}"].to_numpy(float)
    out[BASELINE] = rows["var_24"].to_numpy(float) * horizon
    nan = np.full(len(rows), np.nan)
    if models.har is None or models.trees is None:
        out["H1_HAR_PROFILE"], out["H2_LGBM_PROFILE"] = nan, nan
        return out
    rows = with_profile(rows, models.profile, models.profile_default)
    out["H1_HAR_PROFILE"] = models.har.predict(rows[list(H1_FEATURES)].to_numpy(float))
    out["H2_LGBM_PROFILE"] = models.trees.predict(rows[list(H2_FEATURES)].to_numpy(float))
    return out


def walk_forward(frames: dict[str, pd.DataFrame], settings: Settings, *,
                 progress: Callable[[str], None] | None = None) -> dict[int, pd.DataFrame]:
    """Par horizon, une table large (paire, origine, réalisé, trois prévisions) des origines évaluables : lignes
    complètes, MIN_HISTORY_DAYS, origine ≥ FIRST_FORECAST et ≤ fin de DEVELOPMENT, cible entièrement dans
    DEVELOPMENT, les trois modèles ayant une prévision (échantillon commun). Réajustement trimestriel."""
    say = progress or (lambda _text: None)
    end = pd.Timestamp(development_end(settings))
    first = pd.Timestamp(FIRST_FORECAST, tz="UTC")
    tables = [frame.assign(symbol=symbol) for symbol, frame in frames.items() if len(frame)]
    out: dict[int, pd.DataFrame] = {}
    if not tables:
        return out
    data = pd.concat(tables, ignore_index=True)
    origins = data["origin"]
    eligible = complete_rows(data) & (data["history_days"] >= MIN_HISTORY_DAYS) & (origins >= first) & (origins <= end)
    for horizon in HORIZONS:
        last_target_bar = origins + pd.Timedelta(hours=horizon) - v1.STEP
        scored = (eligible & (data[f"rv2_{horizon}"] > 0) & (last_target_bar <= end)).to_numpy()
        parts: list[pd.DataFrame] = []
        if scored.any():
            for refit in pd.date_range(first.replace(day=1), origins[scored].max(), freq=REFIT_FREQ):
                quarter = scored & ((origins >= refit) & (origins < refit + pd.offsets.QuarterBegin(1, startingMonth=1))).to_numpy()
                if not quarter.any():
                    continue
                say(f"prévisions à {horizon} h — {refit:%Y-%m}")
                models = fit_at(data, refit, horizon, seed=settings.protocol.seed)
                wide = quarter_forecasts(models, data[quarter], horizon)
                values = wide[list(MODELS)].to_numpy(float)
                parts.append(wide[(np.isfinite(values) & (values > 0)).all(axis=1)])
        out[horizon] = (pd.concat(parts, ignore_index=True).sort_values(["origin", "symbol"]).reset_index(drop=True) if parts
                        else pd.DataFrame(columns=["symbol", "origin", "realized", *MODELS]))
    return out


# --- Mesures et règle ---------------------------------------------------------------------------------------------

def _row(model: str, horizon: int, wide: pd.DataFrame) -> v1.Row:
    criteria = dict.fromkeys(("ci_upper_below_zero", "years", "pairs", "secondary_loss"), False)
    if wide.empty:
        return v1.Row(model, horizon, 0, 0, 0, None, None, None, None, {}, 0, None, None, None, None, criteria, False)
    realized = wide["realized"].to_numpy(float)
    losses = pd.DataFrame({"symbol": wide["symbol"], "day": pd.DatetimeIndex(wide["origin"]).floor("D"),
                           "qlike": v1.qlike(realized, wide[model]), "qlike_base": v1.qlike(realized, wide[BASELINE]),
                           "log_error": v1.log_error(realized, wide[model]), "log_error_base": v1.log_error(realized, wide[BASELINE])})
    losses["diff"] = losses["qlike"] - losses["qlike_base"]
    losses["log_diff"] = losses["log_error"] - losses["log_error_base"]
    daily = losses.groupby("day")[["qlike", "qlike_base", "diff", "log_error", "log_error_base", "log_diff"]].mean().sort_index()
    ci = calendar_mean_ci(daily["diff"].to_numpy(float), daily.index, block_days=block_days_for(horizon), min_blocks=v1.MIN_BLOCKS, level=LEVEL)
    by_year = daily["diff"].groupby(daily.index.year).mean()
    by_pair = losses.groupby("symbol")["diff"].mean()
    years_better = sum(1 for year in v1.YEARS if year in by_year.index and by_year[year] < 0)
    pairs_share = float((by_pair < 0).mean())
    mean = daily.mean()
    criteria = {"ci_upper_below_zero": ci is not None and ci[1] < 0, "years": years_better >= v1.MIN_YEARS_BETTER,
                "pairs": pairs_share >= v1.MIN_PAIRS_SHARE, "secondary_loss": bool(mean["log_diff"] < 0)}
    return v1.Row(model, horizon, int(len(losses)), int(len(daily)), int(len(by_pair)), v1._rounded(mean["qlike"]),
                  v1._rounded(mean["qlike_base"]), v1._rounded(mean["diff"]), ci,
                  {str(year): v1._rounded(value) for year, value in by_year.items()}, years_better, round(pairs_share, 4),
                  v1._rounded(mean["log_error"]), v1._rounded(mean["log_error_base"]), v1._rounded(mean["log_diff"]),
                  criteria, all(criteria.values()))


def evaluate(forecasts: dict[int, pd.DataFrame]) -> list[v1.Row]:
    """Les 4 comparaisons déclarées (`horizon_days` du lot 7 porte ici des HEURES)."""
    return [_row(model, horizon, forecasts.get(horizon, pd.DataFrame())) for horizon in HORIZONS for model in CANDIDATES]


def decide(rows: list[v1.Row]) -> tuple[dict[int, str], str]:
    selected = {}
    for horizon in HORIZONS:
        useful = [row for row in rows if row.horizon_days == horizon and row.useful and row.qlike is not None]
        best = min(useful, key=lambda row: (row.qlike or 0.0, MODELS.index(row.model)), default=None)
        selected[horizon] = best.model if best is not None else NO_IMPROVEMENT
    verdict = USEFUL if any(model != NO_IMPROVEMENT for model in selected.values()) else NO_IMPROVEMENT
    return selected, verdict


# --- Audit des fuites ---------------------------------------------------------------------------------------------

def causality_violations(h1: pd.DataFrame, btc_h1: pd.DataFrame | None, *, origins: list[pd.Timestamp], seed: int = 0,
                         leaky: bool = False) -> list[dict]:
    full = hourly_frame(h1, btc_h1, leaky=leaky).set_index("origin")
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

        expected = full.loc[origin, list(INPUTS)].to_numpy(float)
        for label, change in (("tronqué", cut), ("futur falsifié", falsify)):
            pair = change(h1)
            market = None if btc_h1 is None else pair if btc_h1 is h1 else change(btc_h1)
            got = hourly_frame(pair, market, leaky=leaky).set_index("origin")
            if origin not in got.index:
                problems.append({"origin": str(origin), "check": label, "features": ["(origine absente)"]})
                continue
            values = got.loc[origin, list(INPUTS)].to_numpy(float)
            differs = ~np.isclose(values, expected, rtol=1e-9, atol=0.0, equal_nan=True)
            if differs.any():
                problems.append({"origin": str(origin), "check": label, "features": [n for n, d in zip(INPUTS, differs, strict=True) if d]})
    return problems


def leak_audit(settings: Settings, end: pd.Timestamp, seed: int) -> dict:
    """BTC, ETH et SOL exigés ; 3 origines horaires par paire (toutes heures) ; aucune différence tolérée ; mutation
    (fenêtre 24 h avancée d'une heure) détectée sur chaque paire."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(v1.AUDIT_PAIRS, False)
    checked: list[str] = []
    origins_checked: dict[str, list[str]] = {}
    try:
        market = v1.load_series(settings, MARKET, end)
    except MissingData:
        market = None
    for symbol in v1.AUDIT_PAIRS:
        try:
            series = market if symbol == MARKET else v1.load_series(settings, symbol, end)
        except MissingData:
            continue
        if series is None or market is None:
            continue
        full, mutated = hourly_frame(series, market), hourly_frame(series, market, leaky=True)
        ready = full.loc[(complete_rows(full) & mutated["log_var_24"].notna()).to_numpy(), "origin"]
        if len(ready) < v1.AUDIT_ORIGINS:
            continue
        picks = np.sort(rng.choice(len(ready), size=v1.AUDIT_ORIGINS, replace=False))
        origins = [ready.iloc[int(index)] for index in picks]
        violations += [v | {"symbol": symbol} for v in causality_violations(series, market, origins=origins, seed=seed)]
        found = causality_violations(series, market, origins=origins, seed=seed, leaky=True)
        detected[symbol] = any("log_var_24" in v["features"] for v in found)
        checked.append(symbol)
        origins_checked[symbol] = [str(origin) for origin in origins]
    complete = checked == list(v1.AUDIT_PAIRS)
    return {"violations": violations, "mutation_detected": detected, "checked_pairs": checked, "origins": origins_checked,
            "origins_per_pair": v1.AUDIT_ORIGINS, "passed": complete and not violations and all(detected.values())}


# --- Exécution ------------------------------------------------------------------------------------------------------

def _coverage(frames: dict[str, pd.DataFrame], forecasts: dict[int, pd.DataFrame]) -> dict:
    out = {}
    for symbol, frame in frames.items():
        evaluated = {str(h): int((table["symbol"] == symbol).sum()) if len(table) else 0 for h, table in forecasts.items()}
        out[symbol] = {"hourly_rows": int(len(frame)), "complete_rows": int(complete_rows(frame).sum()), "evaluated": evaluated}
    return out


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("VOL"), end.isoformat(), round(LEVEL, 6))
    say("couverture des données")
    result.coverage["series"] = v1.check_complete(settings, list(dict.fromkeys([*symbols, MARKET])), end)
    say("audit des fuites")
    result.leak_audit = leak_audit(settings, end, settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise v1.LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    market = v1.load_series(settings, MARKET, end)
    result.data_hashes[MARKET] = fingerprint(market)
    frames = {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else v1.load_series(settings, symbol, end)
        result.data_hashes[symbol] = fingerprint(series)
        frames[symbol] = hourly_frame(series, market)
    forecasts = walk_forward(frames, settings, progress=say)
    result.coverage["pairs"] = _coverage(frames, forecasts)
    rows = evaluate(forecasts)
    result.rows = list(rows)
    short = [f"{row.model} à {row.horizon_days} h" for row in rows if row.ci_qlike_diff is None]
    if short:
        raise v1.IncompleteData(f"trop peu de jours évaluables pour un intervalle ({v1.MIN_BLOCKS} blocs au moins) : "
                                f"{', '.join(short)} ; rien n'est enregistré")
    result.selected, result.verdict = decide(rows)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, forecasts=forecasts, code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str],
            forecasts: dict[int, pd.DataFrame] | None = None, code: str = "") -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"models": MODEL_TEXT, "horizons_hours": list(HORIZONS), "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    for horizon, table in (forecasts or {}).items():
        table.to_parquet(report_dir / f"forecasts_{horizon}h.parquet", index=False)
    series = result.coverage.get("series", {})
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="volatilité à toute heure : un modèle prévoit-il la variance réalisée des 4 et 24 prochaines heures, à chaque "
                   "clôture 1 h, mieux que la variance des 24 dernières heures ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"modèles figés ({DOC} § 16)",
        params={"horizons_hours": list(HORIZONS), "models": MODEL_TEXT, "level": result.level, "first_forecast": FIRST_FORECAST,
                "min_history_days": MIN_HISTORY_DAYS, "train_window_days": TRAIN_WINDOW.days, "refit": "trimestriel",
                "lgbm": v1.LGBM_PARAMS | {"num_boost_round": v1.LGBM_ROUNDS}, "min_train_rows": v1.MIN_TRAIN_ROWS,
                "min_blocks": v1.MIN_BLOCKS, "block_days": {str(h): block_days_for(h) for h in HORIZONS}, "years": list(v1.YEARS),
                "min_years_better": v1.MIN_YEARS_BETTER, "min_pairs_share": v1.MIN_PAIRS_SHARE, "min_coverage": v1.MIN_COVERAGE,
                "threads": v1.THREADS},
        period_label="DEVELOPMENT",
        period_start=min((entry["first"] for entry in series.values()), default=FIRST_FORECAST),
        period_end=result.period_end, universe=symbols, data_hashes=result.data_hashes,
        git_commit=code or code_state(), dependencies=v1._versions() | dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (erreur de prévision, pas de rentabilité)",
        simulation_rules={"origins": "chaque clôture 1 h, toutes paires", "refit": "le 1er de chaque trimestre, fenêtre de 3 ans",
                          "purge": "cible terminée au plus tard à la date de réajustement", "sample": "origines où les trois modèles ont une prévision"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(horizon): model for horizon, model in result.selected.items()},
                 "useful": sum(1 for row in result.rows if row.useful), "rows": [asdict(row) for row in result.rows]},
        status="COMPLETED", report_dir=str(report_dir))
