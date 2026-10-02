"""Intervalles de rendement et abstention, protocole v1 (docs/QUANTILES.md, déclaré le 2026-10-02 avant exécution).
Piste 3 de l'étude du 2026-10-02.

Question : à 00:00 UTC, les quantiles 5, 25, 75 et 95 % du rendement log des 1, 3 et 7 prochains jours sont-ils
mieux prévus par un modèle que par la règle Q0 « volatilité en service × quantiles empiriques passés du rendement
standardisé » ? Perte pinball (somme des quatre quantiles) et couverture des intervalles 90 % et 50 % par année.
Ni direction, ni rentabilité, ni ordre : des intervalles servent à dimensionner et à s'abstenir.

Cadre du lot 7 repris (`research/volatility.py`, non modifié) : lignes journalières, variables, purge, réajustement
mensuel, IC calendaires. La volatilité en service σ̂ est la prévision `REF_SERVICE` enregistrée par le protocole v2
(reports/VOL-20261002T170500Z-c3bda6/forecasts.parquet : LightGBM à 1 et 3 jours, HAR + BTC à 7 jours, hors
échantillon, réajustés chaque mois) ; les lignes évaluées sont celles de cet échantillon.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from . import volatility as v1
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION = "QUANTILES", "RETURN_QUANTILES", 1
DOC = "docs/QUANTILES.md"
USEFUL, NO_IMPROVEMENT = "INTERVALLES_UTILES", "AUCUNE_AMELIORATION"
HORIZONS = v1.HORIZONS                                   # 1, 3, 7 jours
QUANTILES = (0.05, 0.25, 0.75, 0.95)
MODELS = ("Q0_SERVICE_EMPIRIQUE", "C1_LGBM_QUANTILE", "C2_CONFORME_ADAPTATIF")
BASELINE, CANDIDATES = MODELS[0], MODELS[1:]
N_TRIALS = len(CANDIDATES) * len(HORIZONS)               # 6 comparaisons déclarées
LEVEL = 1 - 0.05 / N_TRIALS
SERVICE_RUN, SERVICE_MODEL = "VOL-20261002T170500Z-c3bda6", "REF_SERVICE"
GAMMA = 0.005                                            # pas du conforme adaptatif (Gibbs et Candès, 2021)
ALPHA_MIN, ALPHA_MAX = 0.001, 0.999
COVERAGE_TOL = 0.03                                      # couverture dans ± 3 points
MIN_YEARS_COVERED = v1.MIN_YEARS_BETTER                  # 6 années sur 7
FEATURES = (*v1.FEATURES, "log_sigma")
LGBM_PARAMS: dict[str, Any] = {"objective": "quantile", "num_leaves": 15, "learning_rate": 0.05, "min_data_in_leaf": 200}
MODEL_TEXT = {
    "Q0_SERVICE_EMPIRIQUE": "référence : σ̂ en service × quantile empirique du rendement standardisé r/σ̂ sur l'entraînement purgé "
                            "(toutes paires ensemble, réajusté chaque mois)",
    "C1_LGBM_QUANTILE": "LightGBM commun à perte pinball, un modèle par quantile : variables du lot 7 + log σ̂ (300 arbres, 15 feuilles)",
    "C2_CONFORME_ADAPTATIF": "Q0 avec niveau de quantile ajusté en ligne par paire et par quantile (pas 0,005) sur les erreurs de "
                             "couverture des origines déjà résolues",
}


class LeakAuditFailed(RuntimeError):
    pass


@dataclass
class Row:
    model: str
    horizon_days: int
    forecasts: int
    days: int
    pairs: int
    pinball: float | None
    pinball_baseline: float | None
    pinball_diff: float | None
    ci_pinball_diff: list[float] | None
    coverage_90_by_year: dict[str, float]
    coverage_50_by_year: dict[str, float]
    years_covered_90: int
    years_covered_50: int
    pairs_better_share: float | None
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


# --- Données : rendements à terme, σ̂ en service, lignes --------------------------------------------------------

def forward_log_returns(h1: pd.DataFrame) -> pd.DataFrame:
    """Par origine 00:00 UTC (clôture de la bougie de 23:00) : clôture à l'origine et rendement log jusqu'à la clôture
    de 23:00 située H jours plus tard (NaN si l'une des deux bougies manque)."""
    frame = h1[list(v1.READ_COLUMNS)].dropna().sort_values("open_time")
    if frame.empty:
        return pd.DataFrame(columns=["origin", "close", *(f"ret_{h}" for h in HORIZONS)])
    closes = pd.Series(frame["close"].to_numpy(float), index=v1._utc(frame["open_time"]).dt.as_unit("ns") + v1.STEP)
    closes = closes[~closes.index.duplicated()]
    origins = closes.index[closes.index == closes.index.floor("D")]
    out = {"origin": origins, "close": closes.reindex(origins).to_numpy(float)}
    for horizon in HORIZONS:
        later = closes.reindex(origins + pd.Timedelta(days=horizon)).to_numpy(float)
        out[f"ret_{horizon}"] = np.log(later / out["close"])
    return pd.DataFrame(out)


def load_service_sigma(settings: Settings, run_id: str = SERVICE_RUN) -> pd.DataFrame:
    """σ̂ = racine de la variance prévue par le modèle en service, par (paire, origine, horizon), lue dans les
    prévisions hors échantillon enregistrées par le protocole v2."""
    path = settings.reports_dir / run_id / "forecasts.parquet"
    if not path.exists():
        raise v1.IncompleteData(f"prévisions en service absentes : {path}")
    table = pd.read_parquet(path)
    table = table[(table["model"] == SERVICE_MODEL) & table["forecast"].notna() & (table["forecast"] > 0)]
    return pd.DataFrame({"symbol": table["symbol"].to_numpy(), "origin": pd.to_datetime(table["origin"], utc=True).dt.as_unit("ns"),
                         "horizon": table["horizon"].to_numpy(int), "sigma": np.sqrt(table["forecast"].to_numpy(float))})


def build_rows(frames: dict[str, pd.DataFrame], returns: dict[str, pd.DataFrame], sigma: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Par horizon : lignes (paire, origine) avec variables du lot 7, log σ̂, rendement réalisé et rendement
    standardisé z = r / σ̂ ; seulement les origines où σ̂ existe et où les variables sont complètes."""
    out: dict[int, pd.DataFrame] = {}
    for horizon in HORIZONS:
        parts = []
        for symbol, daily in frames.items():
            if daily.empty or symbol not in returns:
                continue
            table = daily.assign(origin=daily["origin"].dt.as_unit("ns"))
            table = table[v1.complete_rows(table).to_numpy()][["origin", *v1.FEATURES]]
            table = table.merge(returns[symbol][["origin", f"ret_{horizon}"]].rename(columns={f"ret_{horizon}": "ret"}), on="origin")
            part = sigma[(sigma["symbol"] == symbol) & (sigma["horizon"] == horizon)][["origin", "sigma"]]
            table = table.merge(part, on="origin").assign(symbol=symbol)
            parts.append(table)
        if not parts:
            out[horizon] = pd.DataFrame(columns=["symbol", "origin", *v1.FEATURES, "ret", "sigma", "log_sigma", "z"])
            continue
        rows = pd.concat(parts, ignore_index=True)
        rows["log_sigma"] = np.log(rows["sigma"].to_numpy(float))
        rows["z"] = rows["ret"].to_numpy(float) / rows["sigma"].to_numpy(float)
        out[horizon] = rows.sort_values(["origin", "symbol"]).reset_index(drop=True)
    return out


# --- Pertes -------------------------------------------------------------------------------------------------------

def pinball(realized: np.ndarray, forecast: np.ndarray, alpha: float) -> np.ndarray:
    diff = np.asarray(realized, float) - np.asarray(forecast, float)
    return np.maximum(alpha * diff, (alpha - 1) * diff)


def total_pinball(realized: np.ndarray, forecasts: dict[float, np.ndarray]) -> np.ndarray:
    return np.sum([pinball(realized, forecasts[alpha], alpha) for alpha in QUANTILES], axis=0)


# --- Modèles -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Models:
    z_quantile: Callable[[np.ndarray], np.ndarray] | None    # fonction quantile empirique de z sur l'entraînement
    boosters: dict[float, Any]
    rows: int


def training_rows(rows: pd.DataFrame, refit: pd.Timestamp, horizon: int) -> pd.DataFrame:
    """Purge du lot 7 : rendement réalisé connu au plus tard à `refit` (origine + H jours ≤ refit)."""
    known = rows["origin"] + pd.Timedelta(days=horizon) <= refit
    return rows[known & np.isfinite(rows["ret"].to_numpy(float))]


def fit_at(rows: pd.DataFrame, refit: pd.Timestamp, horizon: int, *, seed: int) -> Models:
    train = training_rows(rows, refit, horizon)
    if len(train) < v1.MIN_TRAIN_ROWS:
        return Models(None, {}, int(len(train)))
    z = np.sort(train["z"].to_numpy(float))

    def z_quantile(levels: np.ndarray) -> np.ndarray:
        return np.quantile(z, np.clip(np.asarray(levels, float), 0.0, 1.0))

    import lightgbm
    X, y = train[list(FEATURES)].to_numpy(float), train["ret"].to_numpy(float)
    boosters = {}
    for alpha in QUANTILES:
        params = LGBM_PARAMS | {"alpha": alpha, "seed": seed, "num_threads": v1.THREADS, "verbose": -1, "deterministic": True,
                                "force_col_wise": True}
        boosters[alpha] = lightgbm.train(params, lightgbm.Dataset(X, label=y), num_boost_round=v1.LGBM_ROUNDS)
    return Models(z_quantile, boosters, int(len(train)))


def adaptive_sequence(times: pd.DatetimeIndex, z: np.ndarray, qz: Callable[[np.ndarray], np.ndarray], horizon: int, *,
                      gamma: float = GAMMA, lag_days: int | None = None, state: np.ndarray | None = None,
                      pending: list | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Conforme adaptatif d'UNE paire sur des origines croissantes : niveaux effectifs (origines × quantiles) et
    seuils en z, qz(niveaux). À l'origine t, chaque origine s en attente dont le rendement est connu (s + H jours
    ≤ t) met à jour l'état : α_eff ← α_eff + γ (α − 1{z_s ≤ seuil_s}), seuil_s étant celui utilisé pour s ; puis les
    niveaux de t sont fixés et t rejoint l'attente, SANS lire z_t. `state` et `pending` sont modifiés sur place pour
    enchaîner les mois ; `lag_days` = 0 est la mutation de l'audit (lit une origine non encore résolue)."""
    lag = horizon if lag_days is None else lag_days
    state = np.array(QUANTILES, float) if state is None else state
    pending = [] if pending is None else pending
    levels = np.full((len(times), len(QUANTILES)), np.nan)
    thresholds = np.full((len(times), len(QUANTILES)), np.nan)
    targets = np.array(QUANTILES, float)
    for t, moment in enumerate(times):
        while pending and pending[0][0] + pd.Timedelta(days=lag) <= moment:
            _origin, threshold, z_s = pending.pop(0)
            if np.isfinite(z_s):
                hit = (z_s <= threshold).astype(float)
                state[:] = np.clip(state + gamma * (targets - hit), ALPHA_MIN, ALPHA_MAX)
        levels[t] = state
        thresholds[t] = qz(state)
        pending.append((moment, thresholds[t].copy(), float(z[t])))
    return levels, thresholds


def month_forecasts(models: Models, rows: pd.DataFrame, adaptive_thresholds: np.ndarray) -> dict[str, dict[float, np.ndarray]]:
    """Quantiles prévus par les trois modèles pour les origines `rows` (NaN si les modèles n'ont pas pu être ajustés) ;
    `adaptive_thresholds` : seuils en z du conforme adaptatif (lignes × quantiles) alignés sur `rows`."""
    nan = np.full(len(rows), np.nan)
    if models.z_quantile is None:
        return {m: {a: nan for a in QUANTILES} for m in MODELS}
    sigma = rows["sigma"].to_numpy(float)
    X = rows[list(FEATURES)].to_numpy(float)
    out: dict[str, dict[float, np.ndarray]] = {m: {} for m in MODELS}
    base_z = models.z_quantile(np.array(QUANTILES))
    for j, alpha in enumerate(QUANTILES):
        out[BASELINE][alpha] = sigma * base_z[j]
        out["C1_LGBM_QUANTILE"][alpha] = np.asarray(models.boosters[alpha].predict(X), float)
        out["C2_CONFORME_ADAPTATIF"][alpha] = sigma * adaptive_thresholds[:, j]
    return out


def walk_forward(rows_by_horizon: dict[int, pd.DataFrame], settings: Settings, *,
                 progress: Callable[[str], None] | None = None) -> dict[int, pd.DataFrame]:
    """Par horizon, table (paire, origine, rendement, 12 quantiles prévus) des origines évaluables : réajustement
    mensuel, purge, premières origines au FIRST_FORECAST du lot 7, échantillon commun aux trois modèles. Le conforme
    adaptatif enchaîne les mois paire par paire (état et origines en attente conservés)."""
    say = progress or (lambda _text: None)
    end = pd.Timestamp(development_end(settings))
    first = pd.Timestamp(v1.FIRST_FORECAST, tz="UTC")
    out: dict[int, pd.DataFrame] = {}
    for horizon, rows in rows_by_horizon.items():
        columns = [f"{m}_{int(a * 100)}" for m in MODELS for a in QUANTILES]
        if rows.empty:
            out[horizon] = pd.DataFrame(columns=["symbol", "origin", "ret", *columns])
            continue
        origins = rows["origin"]
        scored = ((origins >= first) & (origins + pd.Timedelta(days=horizon) <= end) & np.isfinite(rows["ret"].to_numpy(float))).to_numpy()
        parts: list[pd.DataFrame] = []
        states: dict[str, np.ndarray] = {}
        pendings: dict[str, list] = {}
        if scored.any():
            for refit in pd.date_range(first.replace(day=1), origins[scored].max(), freq="MS"):
                month = scored & ((origins >= refit) & (origins < refit + pd.offsets.MonthBegin(1))).to_numpy()
                if not month.any():
                    continue
                say(f"quantiles à {horizon} j — {refit:%Y-%m}")
                models = fit_at(rows, refit, horizon, seed=settings.protocol.seed)
                picked = rows[month].reset_index(drop=True)
                thresholds = np.full((len(picked), len(QUANTILES)), np.nan)
                if models.z_quantile is not None:
                    for symbol, index in picked.groupby("symbol").indices.items():
                        part = picked.iloc[index]
                        state = states.setdefault(str(symbol), np.array(QUANTILES, float))
                        pending = pendings.setdefault(str(symbol), [])
                        _levels, thresholds[index] = adaptive_sequence(pd.DatetimeIndex(part["origin"]), part["z"].to_numpy(float),
                                                                       models.z_quantile, horizon, state=state, pending=pending)
                forecasts = month_forecasts(models, picked, thresholds)
                wide = picked[["symbol", "origin", "ret"]].copy()
                for model in MODELS:
                    for alpha in QUANTILES:
                        wide[f"{model}_{int(alpha * 100)}"] = forecasts[model][alpha]
                values = wide[columns].to_numpy(float)
                parts.append(wide[np.isfinite(values).all(axis=1)])
        out[horizon] = (pd.concat(parts, ignore_index=True).sort_values(["origin", "symbol"]).reset_index(drop=True) if parts
                        else pd.DataFrame(columns=["symbol", "origin", "ret", *columns]))
    return out


# --- Mesures et règle --------------------------------------------------------------------------------------------

def _quantiles_of(wide: pd.DataFrame, model: str) -> dict[float, np.ndarray]:
    return {alpha: wide[f"{model}_{int(alpha * 100)}"].to_numpy(float) for alpha in QUANTILES}


def _row(model: str, horizon: int, wide: pd.DataFrame) -> Row:
    criteria = dict.fromkeys(("ci_upper_below_zero", "coverage_90", "coverage_50", "pairs"), False)
    if wide.empty:
        return Row(model, horizon, 0, 0, 0, None, None, None, None, {}, {}, 0, 0, None, criteria, False)
    ret = wide["ret"].to_numpy(float)
    mine, base = _quantiles_of(wide, model), _quantiles_of(wide, BASELINE)
    losses = pd.DataFrame({"symbol": wide["symbol"], "day": pd.DatetimeIndex(wide["origin"]).floor("D"),
                           "loss": total_pinball(ret, mine), "loss_base": total_pinball(ret, base),
                           "in90": (ret >= mine[0.05]) & (ret <= mine[0.95]), "in50": (ret >= mine[0.25]) & (ret <= mine[0.75])})
    losses["diff"] = losses["loss"] - losses["loss_base"]
    daily = losses.groupby("day")[["loss", "loss_base", "diff", "in90", "in50"]].mean().sort_index()
    ci = calendar_mean_ci(daily["diff"].to_numpy(float), daily.index, block_days=v1.block_days_for(horizon), min_blocks=v1.MIN_BLOCKS, level=LEVEL)
    by_year_90 = daily["in90"].groupby(daily.index.year).mean()
    by_year_50 = daily["in50"].groupby(daily.index.year).mean()
    years_90 = sum(1 for y in v1.YEARS if y in by_year_90.index and abs(by_year_90[y] - 0.90) <= COVERAGE_TOL)
    years_50 = sum(1 for y in v1.YEARS if y in by_year_50.index and abs(by_year_50[y] - 0.50) <= COVERAGE_TOL)
    by_pair = losses.groupby("symbol")["diff"].mean()
    pairs_share = float((by_pair < 0).mean())
    mean = daily.mean()
    criteria = {"ci_upper_below_zero": ci is not None and ci[1] < 0, "coverage_90": years_90 >= MIN_YEARS_COVERED,
                "coverage_50": years_50 >= MIN_YEARS_COVERED, "pairs": pairs_share >= v1.MIN_PAIRS_SHARE}
    return Row(model, horizon, int(len(losses)), int(len(daily)), int(len(by_pair)), v1._rounded(mean["loss"]),
               v1._rounded(mean["loss_base"]), v1._rounded(mean["diff"]), ci,
               {str(y): round(float(v), 4) for y, v in by_year_90.items()}, {str(y): round(float(v), 4) for y, v in by_year_50.items()},
               years_90, years_50, round(pairs_share, 4), criteria, all(criteria.values()))


def evaluate(forecasts: dict[int, pd.DataFrame]) -> list[Row]:
    return [_row(model, horizon, forecasts.get(horizon, pd.DataFrame())) for horizon in HORIZONS for model in CANDIDATES]


def baseline_rows(forecasts: dict[int, pd.DataFrame]) -> list[Row]:
    """La référence contre elle-même (écart nul) : pour lire ses couvertures par année dans le rapport."""
    return [_row(BASELINE, horizon, forecasts.get(horizon, pd.DataFrame())) for horizon in HORIZONS]


def decide(rows: list[Row]) -> tuple[dict[int, str], str]:
    selected = {}
    for horizon in HORIZONS:
        useful = [row for row in rows if row.horizon_days == horizon and row.useful and row.pinball is not None]
        best = min(useful, key=lambda row: (row.pinball or 0.0, MODELS.index(row.model)), default=None)
        selected[horizon] = best.model if best is not None else NO_IMPROVEMENT
    verdict = USEFUL if any(model != NO_IMPROVEMENT for model in selected.values()) else NO_IMPROVEMENT
    return selected, verdict


# --- Audit des fuites ------------------------------------------------------------------------------------------------

def leak_audit(frames: dict[str, pd.DataFrame], returns: dict[str, pd.DataFrame], rows_by_horizon: dict[int, pd.DataFrame], *,
               seed: int, picks: int = 3) -> dict:
    """(1) Cibles : à `picks` origines tirées, le rendement à terme recalculé sur les bougies tronquées à l'origine est
    inconnu (le futur n'est pas lu), et identique sur les bougies complètes. (2) Niveaux adaptatifs : pour une paire,
    les niveaux à l'origine t calculés sur les seules origines ≤ t sont identiques aux niveaux complets ; la mutation
    (mise à jour avec une origine non encore résolue, lag 0) doit changer des niveaux. Les variables du lot 7 ont leur
    propre audit (lot 7, § 9), non répété ici."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = False
    checked: list[str] = []
    symbols = sorted(s for s in frames if s in returns and len(returns[s]) > 100)
    for symbol in symbols[:3]:
        h1 = frames[symbol]
        table = returns[symbol].set_index("origin")
        candidates = table.index[len(table) // 3:]
        for origin in rng.choice(candidates, size=min(picks, len(candidates)), replace=False):
            origin = pd.Timestamp(origin)
            cut = h1[v1._utc(h1["open_time"]) + v1.STEP <= origin]
            again = forward_log_returns(cut).set_index("origin")
            for horizon in HORIZONS:
                if origin in again.index and np.isfinite(again.loc[origin, f"ret_{horizon}"]):
                    violations.append({"symbol": symbol, "origin": str(origin), "horizon": horizon, "check": "cible lue sur des bougies tronquées"})
            full_again = forward_log_returns(h1).set_index("origin")
            if not np.allclose(full_again.loc[origin, [f"ret_{h}" for h in HORIZONS]].to_numpy(float),
                               table.loc[origin, [f"ret_{h}" for h in HORIZONS]].to_numpy(float), equal_nan=True):
                violations.append({"symbol": symbol, "origin": str(origin), "check": "cible non reproductible"})
        checked.append(symbol)
    for horizon, rows in rows_by_horizon.items():
        if rows.empty:
            continue
        symbol = symbols[0] if symbols else str(rows["symbol"].iloc[0])
        part = rows[rows["symbol"] == symbol]
        if len(part) < 50:
            continue
        times, z = pd.DatetimeIndex(part["origin"]), part["z"].to_numpy(float)
        reference = np.sort(z[np.isfinite(z)])

        def qz(levels: np.ndarray, reference=reference) -> np.ndarray:
            return np.quantile(reference, np.clip(levels, 0.0, 1.0))

        cut_at = int(rng.integers(len(part) // 2, len(part)))
        full, _ = adaptive_sequence(times, z, qz, horizon)
        truncated, _ = adaptive_sequence(times[: cut_at + 1], z[: cut_at + 1], qz, horizon)
        if not np.allclose(full[: cut_at + 1], truncated):
            violations.append({"symbol": symbol, "horizon": horizon, "check": "niveaux adaptatifs dépendant du futur"})
        mutated, _ = adaptive_sequence(times, z, qz, horizon, lag_days=0)
        detected |= not np.allclose(full, mutated)
    return {"violations": violations, "mutation_detected": bool(detected), "checked_pairs": checked,
            "passed": bool(checked) and not violations and bool(detected)}


# --- Exécution ---------------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, service_run: str = SERVICE_RUN) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("QTL"), end.isoformat(), round(LEVEL, 6))
    say("couverture des données")
    result.coverage["series"] = v1.check_complete(settings, list(dict.fromkeys([*symbols, MARKET])), end)
    say("σ̂ en service")
    sigma = load_service_sigma(settings, service_run)
    result.data_hashes["service_sigma"] = fingerprint(sigma)
    result.coverage["service"] = {"run": service_run, "rows": int(len(sigma)), "first": str(sigma["origin"].min()), "last": str(sigma["origin"].max())}
    market = v1.load_series(settings, MARKET, end)
    result.data_hashes[MARKET] = fingerprint(market)
    frames, returns, series_by = {}, {}, {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else v1.load_series(settings, symbol, end)
        result.data_hashes[symbol] = fingerprint(series)
        series_by[symbol] = series
        frames[symbol] = v1.daily_frame(series, market)
        returns[symbol] = forward_log_returns(series)
    rows_by_horizon = build_rows(frames, returns, sigma)
    say("audit des fuites")
    result.leak_audit = leak_audit(series_by, returns, rows_by_horizon, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    forecasts = walk_forward(rows_by_horizon, settings, progress=say)
    result.coverage["pairs"] = {s: {str(h): int((t["symbol"] == s).sum()) if len(t) else 0 for h, t in forecasts.items()} for s in symbols}
    rows = evaluate(forecasts)
    result.rows = list(rows)
    short = [f"{row.model} à {row.horizon_days} j" for row in rows if row.ci_pinball_diff is None]
    if short:
        raise v1.IncompleteData(f"trop peu de jours évaluables pour un intervalle ({v1.MIN_BLOCKS} blocs au moins) : {', '.join(short)} ; rien n'est enregistré")
    result.selected, result.verdict = decide(rows)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, forecasts=forecasts, baseline=baseline_rows(forecasts), code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str], forecasts: dict[int, pd.DataFrame],
            baseline: list[Row], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"models": MODEL_TEXT, "quantiles": list(QUANTILES), "baseline_rows": [asdict(r) for r in baseline],
                                "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    for horizon, table in forecasts.items():
        table.to_parquet(report_dir / f"quantiles_{horizon}d.parquet", index=False)
    series = result.coverage.get("series", {})
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="intervalles de rendement : LightGBM quantile ou un conforme adaptatif prévoient-ils les quantiles 5/25/75/95 % du "
                   "rendement à 1, 3 et 7 jours mieux que « σ̂ en service × quantiles empiriques » ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"modèles figés ({DOC})",
        params={"horizons_days": list(HORIZONS), "quantiles": list(QUANTILES), "models": MODEL_TEXT, "level": result.level,
                "service_run": SERVICE_RUN, "gamma": GAMMA, "coverage_tol": COVERAGE_TOL, "min_years_covered": MIN_YEARS_COVERED,
                "lgbm": LGBM_PARAMS | {"num_boost_round": v1.LGBM_ROUNDS}, "min_train_rows": v1.MIN_TRAIN_ROWS, "min_blocks": v1.MIN_BLOCKS,
                "block_days": {str(h): v1.block_days_for(h) for h in HORIZONS}, "years": list(v1.YEARS), "min_pairs_share": v1.MIN_PAIRS_SHARE},
        period_label="DEVELOPMENT", period_start=min((entry["first"] for entry in series.values()), default=v1.FIRST_FORECAST),
        period_end=result.period_end, universe=symbols, data_hashes=result.data_hashes, git_commit=code or code_state(),
        dependencies=v1._versions() | dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="aucun (perte de prévision, pas de rentabilité)",
        simulation_rules={"origins": "une par jour et par paire, à 00:00 UTC, lignes de l'échantillon v2", "refit": "le 1er de chaque mois",
                          "purge": "rendement connu au plus tard à la date de réajustement", "sample": "origines où les trois modèles prévoient"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "verdict": result.verdict,
                 "selected": {str(h): m for h, m in result.selected.items()}, "useful": sum(1 for r in result.rows if r.useful),
                 "rows": [asdict(r) for r in result.rows], "baseline_rows": [asdict(r) for r in baseline]},
        status="COMPLETED", report_dir=str(report_dir))
