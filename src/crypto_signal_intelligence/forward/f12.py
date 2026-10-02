"""Test en direct F12_VOL_FORWARD : les prévisions de volatilité retenues sur DEVELOPMENT (lot 7, v2, v3) mesurées en
direct, sans sélection (docs/FORWARD_TESTS.md, section F12_VOL_FORWARD ; règles figées au démarrage).

Chaque jour après 00:10 UTC, pour les paires de la configuration : à l'origine 00:00, les variances prévues par M0
(règle des 7 jours), M4 (HAR + BTC), M5 (LightGBM) et V1 (moyenne de M4 et M5) à 1, 3 et 7 jours (modèles du lot 7,
réajustés le 1er du mois sur le magasin de la surveillance, fonctions gelées), et par R0 (règle des 24 h) et H1 (HAR +
profil heure × jour, réajusté le 1er du trimestre) à 4 h et 24 h. Quand les bougies des H jours suivants existent,
la variance réalisée est lue et la perte QLIKE de chaque prévision inscrite. Aucune décision n'en dépend.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.store import CandleStore
from ..research import volatility as vol
from ..research import volatility_hourly as vh
from ..research.intervals import calendar_mean_ci
from .journal import Journal, utc_iso
from .registry import ForwardTest

TEST_ID = "F12_VOL_FORWARD"
MARKET = "BTCUSDT"
DAILY_HORIZONS = (1, 3, 7)
HOURLY_HORIZONS = (4, 24)
DAILY_MODELS = ("M0_RECENT_7D", "M4_HAR_POOLED_BTC", "M5_LGBM_POOLED", "V1_MEAN_M4_M5")
HOURLY_MODELS = ("R0_RECENT_24H", "H1_HAR_PROFILE", "H2_LGBM_PROFILE")
#: Comparaisons déclarées : (horizon, candidat, référence, attente). « CONFIRME » = le candidat fait mieux (borne haute < 0).
COMPARISONS = (
    ("3d", "V1_MEAN_M4_M5", "M5_LGBM_POOLED", "CONFIRME"),        # v2 : seul horizon utile
    ("1d", "V1_MEAN_M4_M5", "M5_LGBM_POOLED", "PAS_DE_DIFFERENCE"),
    ("7d", "V1_MEAN_M4_M5", "M4_HAR_POOLED_BTC", "PAS_DE_DIFFERENCE"),
    ("24h", "H1_HAR_PROFILE", "R0_RECENT_24H", "CONFIRME"),         # v3 : horizon utile
    ("4h", "H1_HAR_PROFILE", "R0_RECENT_24H", "CONFIRME"),           # v3 : QLIKE net, non retenu par la règle
    ("3d", "M5_LGBM_POOLED", "M0_RECENT_7D", "CONFIRME"),           # lot 7
    ("7d", "M4_HAR_POOLED_BTC", "M0_RECENT_7D", "CONFIRME"),        # lot 7
)
CHECK_AFTER = pd.Timedelta(minutes=10)
MIN_DAYS = 60
MIN_BLOCKS = 6
BLOCK_DAYS = 10
ALPHA = 0.05
LEVEL = 1 - ALPHA / len(COMPARISONS)
FORECAST, RESOLUTION = "PREVISION", "RESOLUTION"
CONFIRMED, REFUTED, NO_DIFFERENCE, INSUFFICIENT, RUNNING = "CONFIRME", "INFIRME", "PAS_DE_DIFFERENCE", "INSUFFISANT", "EN_COURS"
_DAILY_CACHE: dict[tuple[str, int], object] = {}
_HOURLY_CACHE: dict[tuple[str, int], object] = {}


# --- Règles pures -------------------------------------------------------------------------------------------

def qlike(realized: float, forecast: float) -> float | None:
    if not (np.isfinite(realized) and np.isfinite(forecast)) or realized <= 0 or forecast <= 0:
        return None
    ratio = realized / forecast
    return float(ratio - np.log(ratio) - 1)


def verdict_of(diffs: np.ndarray, times: np.ndarray, *, ended: bool) -> tuple[str, list[float] | None, int]:
    """Verdict d'une comparaison : différence de QLIKE (candidat − référence) moyennée par jour sur les paires."""
    days = int(len(diffs))
    if not ended:
        return RUNNING, None, days
    if days < MIN_DAYS:
        return INSUFFICIENT, None, days
    ci = calendar_mean_ci(diffs, pd.DatetimeIndex(times), block_days=BLOCK_DAYS, min_blocks=MIN_BLOCKS, level=LEVEL)
    if ci is None:
        return INSUFFICIENT, None, days
    if ci[1] < 0:
        return CONFIRMED, ci, days
    if ci[0] > 0:
        return REFUTED, ci, days
    return NO_DIFFERENCE, ci, days


# --- Données et modèles (fonctions de recherche gelées) --------------------------------------------------------

def series_until(settings: Settings, symbol: str, until: pd.Timestamp) -> pd.DataFrame:
    frame = CandleStore(settings.data_dir).load(symbol, "1h")
    if frame.empty:
        return frame
    return frame.loc[frame["available_at"] <= until, list(vol.READ_COLUMNS)].reset_index(drop=True)


def daily_forecasts(settings: Settings, symbols: list[str], origin: pd.Timestamp, *, seed: int) -> dict[str, dict]:
    """Prévisions des modèles journaliers à `origin` pour chaque paire évaluable (variables complètes, 400 jours)."""
    market = series_until(settings, MARKET, origin + CHECK_AFTER)
    if market.empty:
        return {}
    frames = []
    for symbol in dict.fromkeys([MARKET, *symbols]):
        own = market if symbol == MARKET else series_until(settings, symbol, origin + CHECK_AFTER)
        if not own.empty:
            frames.append(vol.daily_frame(own, market).assign(symbol=symbol))
    data = pd.concat(frames, ignore_index=True)
    today = data[data["origin"] == origin]
    usable = today[vol.complete_rows(today) & (today["history_days"] >= vol.MIN_HISTORY_DAYS)]
    usable = usable[usable["symbol"].isin(symbols)]
    out: dict[str, dict] = {s: {} for s in usable["symbol"]}
    if usable.empty:
        return out
    refit = origin.replace(day=1)
    for horizon in DAILY_HORIZONS:
        key = (str(refit), horizon)
        if key not in _DAILY_CACHE:
            _DAILY_CACHE.clear() if len(_DAILY_CACHE) > 6 else None
            _DAILY_CACHE[key] = vol.fit_at(data, refit, horizon, seed=seed)
        table = vol.month_forecasts(_DAILY_CACHE[key], usable, horizon)       # type: ignore[arg-type]
        for _, row in table.iterrows():
            m4, m5 = float(row["M4_HAR_POOLED_BTC"]), float(row["M5_LGBM_POOLED"])
            out[str(row["symbol"])][f"{horizon}d"] = {"M0_RECENT_7D": float(row["M0_RECENT_7D"]), "M4_HAR_POOLED_BTC": m4,
                                                      "M5_LGBM_POOLED": m5, "V1_MEAN_M4_M5": (m4 + m5) / 2}
    return out


def hourly_forecasts(settings: Settings, symbols: list[str], origin: pd.Timestamp, *, seed: int) -> dict[str, dict]:
    """Prévisions des modèles horaires à l'origine 00:00 (une par jour ici), réajustés le 1er du trimestre."""
    market = series_until(settings, MARKET, origin + CHECK_AFTER)
    if market.empty:
        return {}
    frames = []
    for symbol in dict.fromkeys([MARKET, *symbols]):
        own = market if symbol == MARKET else series_until(settings, symbol, origin + CHECK_AFTER)
        if not own.empty:
            frames.append(vh.hourly_frame(own, market).assign(symbol=symbol))
    data = pd.concat(frames, ignore_index=True)
    today = data[data["origin"] == origin]
    usable = today[vh.complete_rows(today) & (today["history_days"] >= vh.MIN_HISTORY_DAYS)]
    usable = usable[usable["symbol"].isin(symbols)]
    out: dict[str, dict] = {s: {} for s in usable["symbol"]}
    if usable.empty:
        return out
    refit = origin.tz_convert(None).to_period("Q").start_time.tz_localize("UTC")
    for horizon in HOURLY_HORIZONS:
        key = (str(refit), horizon)
        if key not in _HOURLY_CACHE:
            _HOURLY_CACHE.clear() if len(_HOURLY_CACHE) > 4 else None
            _HOURLY_CACHE[key] = vh.fit_at(data, refit, horizon, seed=seed)
        table = vh.quarter_forecasts(_HOURLY_CACHE[key], usable, horizon)      # type: ignore[arg-type]
        for _, row in table.iterrows():
            out[str(row["symbol"])][f"{horizon}h"] = {m: float(row[m]) for m in HOURLY_MODELS}
    return out


def realized_variances(settings: Settings, symbol: str, origin: pd.Timestamp, until: pd.Timestamp) -> dict[str, float | None]:
    """Variances réalisées à l'origine (lignes de recherche recalculées avec les bougies connues à `until`) : NaN →
    None quand une bougie manque dans la fenêtre (cible contiguë)."""
    own = series_until(settings, symbol, until)
    out: dict[str, float | None] = {}
    if own.empty:
        return out
    daily = vol.daily_frame(own).set_index("origin")
    hourly = vh.hourly_frame(own).set_index("origin")
    for horizon in DAILY_HORIZONS:
        value = float(daily.loc[origin, f"rv2_{horizon}"]) if origin in daily.index else float("nan")
        out[f"{horizon}d"] = value if np.isfinite(value) and value > 0 else None
    for horizon in HOURLY_HORIZONS:
        value = float(hourly.loc[origin, f"rv2_{horizon}"]) if origin in hourly.index else float("nan")
        out[f"{horizon}h"] = value if np.isfinite(value) and value > 0 else None
    return out


# --- Journal ----------------------------------------------------------------------------------------------------

def _first_by(entries, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"][field], entry["data"])
    return out


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final or moment < day + CHECK_AFTER:
        return {"checks": 0}
    if key in _first_by(journal.entries({FORECAST}), "day"):
        return {"checks": 0}
    symbols = [s for s in settings.data.symbols if s in set(start["halal"]["symbols"])]
    daily = daily_forecasts(settings, symbols, day, seed=settings.protocol.seed)
    hourly = hourly_forecasts(settings, symbols, day, seed=settings.protocol.seed)
    pairs = {s: {**daily.get(s, {}), **hourly.get(s, {})} for s in symbols}
    journal.append(FORECAST, {"day": key, "origin": utc_iso(day), "pairs": pairs,
                              "evaluable": {"daily": sum(1 for s in symbols if daily.get(s)), "hourly": sum(1 for s in symbols if hourly.get(s))}},
                   now=moment)
    return {"checks": 1, "evaluable": sum(1 for p in pairs.values() if p)}


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    """Quand les 7 jours qui suivent une origine sont passés (plus 2 h de marge pour la bougie), lit les variances
    réalisées de chaque paire et inscrit les pertes QLIKE de toutes les prévisions."""
    done = set(_first_by(journal.entries({RESOLUTION}), "day"))
    moment = pd.Timestamp(now)
    counts: dict[str, int] = {}
    for key, entry in _first_by(journal.entries({FORECAST}), "day").items():
        if key in done:
            continue
        origin = pd.Timestamp(entry["origin"])
        if moment < origin + pd.Timedelta(days=max(DAILY_HORIZONS)) + pd.Timedelta(hours=2):
            continue
        pairs: dict[str, dict] = {}
        for symbol, forecasts in entry["pairs"].items():
            if not forecasts:
                continue
            realized = realized_variances(settings, symbol, origin, moment)
            losses: dict[str, dict] = {}
            for horizon, models in forecasts.items():
                value = realized.get(horizon)
                losses[horizon] = {"realized": value, "qlike": {m: qlike(value, f) if value is not None else None for m, f in models.items()}}
            pairs[symbol] = losses
        journal.append(RESOLUTION, {"day": key, "origin": entry["origin"], "pairs": pairs}, now=moment)
        counts["RESOLU"] = counts.get("RESOLU", 0) + 1
    return counts


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    forecasts = _first_by(journal.entries({FORECAST}), "day")
    resolutions = _first_by(journal.entries({RESOLUTION}), "day")
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in resolutions for k in forecasts)
    out: dict = {"checks": len(forecasts), "decisions": len(forecasts), "resolved": len(resolutions),
                 "pending": sum(1 for k in forecasts if k not in resolutions), "comparisons": {}, "ended": bool(ended)}
    for horizon, candidate, reference, expected in COMPARISONS:
        diffs, times, mean_c, mean_r = [], [], [], []
        for _key, entry in sorted(resolutions.items()):
            values = []
            for losses in entry["pairs"].values():
                block = losses.get(horizon)
                if not block:
                    continue
                c, r = block["qlike"].get(candidate), block["qlike"].get(reference)
                if c is not None and r is not None:
                    values.append((c, r))
            if values:
                diffs.append(float(np.mean([c - r for c, r in values])))
                mean_c.append(float(np.mean([c for c, _ in values])))
                mean_r.append(float(np.mean([r for _, r in values])))
                times.append(np.datetime64(pd.Timestamp(entry["origin"]).tz_convert("UTC").tz_convert(None)))
        name = f"{horizon}:{candidate}-vs-{reference}"
        verdict, ci, days = verdict_of(np.array(diffs, float), np.array(times, dtype="datetime64[ns]"), ended=ended)
        out["comparisons"][name] = {"horizon": horizon, "candidate": candidate, "reference": reference, "expected": expected,
                                    "days": days, "qlike_candidate": round(float(np.mean(mean_c)), 6) if mean_c else None,
                                    "qlike_reference": round(float(np.mean(mean_r)), 6) if mean_r else None,
                                    "diff": round(float(np.mean(diffs)), 6) if diffs else None, "ci": ci, "verdict": verdict}
    out["verdict"] = ", ".join(f"{k}: {v['verdict']}" for k, v in out["comparisons"].items())
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "comparisons": result["comparisons"], "resolved": result["resolved"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Prévisions de volatilité retenues sur DEVELOPMENT, mesurées en direct (lot 7, v2, v3)",
    hypothesis=("En direct, sur les paires de la configuration : la moyenne de HAR + BTC et LightGBM prévoit la variance réalisée à "
                "3 jours mieux que LightGBM seul (v2) ; HAR + profil heure × jour prévoit la variance des 24 h suivantes mieux que la "
                "règle des 24 dernières heures (v3) ; LightGBM à 3 jours et HAR + BTC à 7 jours font mieux que la règle des 7 jours "
                "(lot 7). Perte QLIKE, différence moyennée par jour, intervalle par blocs de 10 jours."),
    params={"daily_horizons": list(DAILY_HORIZONS), "hourly_horizons": list(HOURLY_HORIZONS), "daily_models": list(DAILY_MODELS),
            "hourly_models": list(HOURLY_MODELS), "comparisons": [list(c) for c in COMPARISONS], "min_days": MIN_DAYS,
            "min_blocks": MIN_BLOCKS, "block_days": BLOCK_DAYS, "alpha": ALPHA, "level": round(LEVEL, 6),
            "daily_refit": "1er du mois", "hourly_refit": "1er du trimestre", "check_after_minutes": 10,
            "source_runs": ["VOL-20261001T194742Z-926ff2", "VOL-20261002T170500Z-c3bda6", "VOL-20261002T211837Z-be55c0"]},
    rule_objects=(), config_keys=("data.symbols",),
    frozen_modules=("crypto_signal_intelligence.forward.f12", "crypto_signal_intelligence.forward.registry",
                    "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.research.volatility", "daily_frame"),
                      ("crypto_signal_intelligence.research.volatility", "complete_rows"),
                      ("crypto_signal_intelligence.research.volatility", "fit_at"),
                      ("crypto_signal_intelligence.research.volatility", "month_forecasts"),
                      ("crypto_signal_intelligence.research.volatility_hourly", "hourly_frame"),
                      ("crypto_signal_intelligence.research.volatility_hourly", "complete_rows"),
                      ("crypto_signal_intelligence.research.volatility_hourly", "fit_at"),
                      ("crypto_signal_intelligence.research.volatility_hourly", "quarter_forecasts"),
                      ("crypto_signal_intelligence.research.intervals", "calendar_mean_ci"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
