"""Test en direct F14_PIVOT_BREAK_VOL_LEVELS : les événements K2 (F10 + F13, 40 paires) joués avec un stop et un
objectif placés selon la VOLATILITÉ PRÉVUE au lieu d'une sortie à date fixe (docs/FORWARD_TESTS.md, section
F14_PIVOT_BREAK_VOL_LEVELS ; plan de travail du 2026-10-03, validé par le propriétaire).

C'est l'usage des modèles de volatilité dans une décision : ils ne disent pas le sens, ils disent jusqu'où placer
les niveaux. Règles figées :
- événements : première clôture journalière au-dessus d'un pivot haut confirmé (fonctions gelées de F10) sur les
  40 paires de recherche (16 de la configuration + 24 de F13), contrôle après 00:10 UTC ;
- σ̂ : mouvement typique prévu à 3 jours par la prévision EN SERVICE du jour (state/volatility.json, LightGBM du
  lot 7), lu au moment de la détection ; sans prévision du jour, l'événement est inscrit « sans prévision » et non
  joué ;
- achat au premier prix (bougie 1 min) après la détection ; stop à entrée × (1 − 1,0 σ̂), objectif à entrée ×
  (1 + 1,5 σ̂) (mêmes multiples que le plan du tableau de bord) ; sinon sortie au marché à 168 h ;
- chemin entre l'entrée et 168 h : bougies 1 h du magasin de la surveillance à partir de l'heure pleine qui suit
  l'entrée ; ouverture au-delà d'une barrière → sortie à l'ouverture ; stop et objectif dans la même bougie → stop ;
- R = rendement net / (1,0 σ̂) ; placebos : 20 achats de la même paire aux mêmes heures 1 à 30 jours avant, avec
  le MÊME σ̂ (même géométrie) ; référence appariée : le même événement sorti à date fixe (168 h), en R du même risque.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from . import f10, f13
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest

TEST_ID = "F14_PIVOT_BREAK_VOL_LEVELS"
CONFIG_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "NEARUSDT", "AVAXUSDT", "HBARUSDT", "LINKUSDT", "XLMUSDT", "ADAUSDT",
    "TRXUSDT", "FILUSDT", "ALGOUSDT", "DOTUSDT", "ATOMUSDT", "ETCUSDT",
)
SYMBOLS: tuple[str, ...] = CONFIG_SYMBOLS + f13.SYMBOLS
SIGMA_HORIZON = "3"                      # mouvement typique prévu à 3 jours (prévision en service)
STOP_SIGMA, TARGET_SIGMA = 1.0, 1.5
HOLD = pd.Timedelta(hours=168)
PLACEBOS, PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS = 20, 1, 30
MAX_BAR_DELAY = pd.Timedelta(minutes=10)
MIN_EVENTS = 30
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261014
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
FORECAST_DEADLINE = pd.Timedelta(hours=23)  # sans prévision du jour à 23:00 UTC : contrôle quand même (traçabilité)
CHECK, EVENT, RESOLUTION = "CONTROLE", "EVENEMENT", "RESOLUTION"
COMPARISONS = ("vs_placebos", "vs_sortie_a_date")
POSITIVE, NEGATIVE, NO_DIFFERENCE, INSUFFICIENT, RUNNING = ("EXCES_POSITIF", "EXCES_NEGATIF",
                                                            "PAS_DE_DIFFERENCE_DEMONTREE", "INSUFFISANT", "EN_COURS")


# --- Règles pures ------------------------------------------------------------------------------------------

def sigma_for(forecast: dict | None, symbol: str) -> float | None:
    """σ̂ (fraction) d'une paire dans le fichier de prévisions du jour, ou None."""
    pair = ((forecast or {}).get("pairs") or {}).get(symbol) or {}
    if not pair.get("available"):
        return None
    move = ((pair.get("horizons") or {}).get(SIGMA_HORIZON) or {}).get("move_pct")
    return float(move) / 100 if move and math.isfinite(float(move)) and float(move) > 0 else None


def play(entry: float, sigma: float, bars: pd.DataFrame, exit_open: float) -> tuple[str, float]:
    """Issue et prix de sortie (avant frais) : barrières sur les bougies 1 h `bars` (open, high, low, triées), puis
    sortie à `exit_open` à 168 h. Ouverture au-delà d'une barrière → sortie à l'ouverture ; stop et objectif dans la
    même bougie → stop."""
    stop, target = entry * (1 - STOP_SIGMA * sigma), entry * (1 + TARGET_SIGMA * sigma)
    for o, h, lo in zip(bars["open"].to_numpy(float), bars["high"].to_numpy(float), bars["low"].to_numpy(float), strict=True):
        if o <= stop:
            return "SL", o
        if o >= target:
            return "TP", target                  # ordre limite posé d'avance : rempli à son prix, pas à l'ouverture
        if lo <= stop:
            return "SL", stop
        if h >= target:
            return "TP", target
    return "TEMPS", exit_open


def net_return(entry: float, exit_price: float, symbol: str, scenario: str, *, market_exit: bool) -> float:
    """Achat au marché ; sortie au marché (stop, temps) ou limite (objectif : frais seulement)."""
    costs = costs_for(symbol, scenario)
    sell = (1 - costs.market) if market_exit else 1.0
    return exit_price * sell * (1 - costs.fee) / (entry * (1 + costs.market) * (1 + costs.fee)) - 1


def r_of(entry: float, exit_price: float, outcome: str, sigma: float, symbol: str, scenario: str) -> float:
    return net_return(entry, exit_price, symbol, scenario, market_exit=outcome != "TP") / (STOP_SIGMA * sigma)


def placebo_days(event_id: str) -> list[int]:
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{event_id}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS + 1), PLACEBOS))


def verdict(scenarios: dict, *, ended: bool) -> dict:
    if not ended:
        return dict.fromkeys(COMPARISONS, RUNNING)
    out = {}
    for name in COMPARISONS:
        central, adverse = scenarios.get(CENTRAL, {}).get(name, {}), scenarios.get(ADVERSE, {}).get(name, {})
        if central.get("n", 0) < MIN_EVENTS or central.get("ci") is None or adverse.get("ci") is None:
            out[name] = INSUFFICIENT
        elif central["ci"][0] > 0 and adverse["ci"][0] > 0:
            out[name] = POSITIVE
        elif central["ci"][1] < 0 and adverse["ci"][1] < 0:
            out[name] = NEGATIVE
        else:
            out[name] = NO_DIFFERENCE
    return out


# --- Journal ---------------------------------------------------------------------------------------------

def read_forecast(settings: Settings) -> dict | None:
    path = settings.root / "state" / "volatility.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final or moment < day + f10.CHECK_AFTER:
        return {"checks": 0}
    if key in f10._first_by(journal.entries({CHECK}), "day"):
        return {"checks": 0}
    forecast = read_forecast(settings)
    if forecast is None or str(forecast.get("origin", ""))[:10] != key:
        if moment < day + FORECAST_DEADLINE:
            return {"checks": 0, "waiting": "prévision de volatilité du jour pas encore écrite"}
        forecast = None                          # échéance passée : contrôle quand même, événements « sans prévision »
    symbols = [s for s in SYMBOLS if s in set(start["halal"]["symbols"])]
    values: dict[str, dict] = {}
    events = 0
    for symbol in symbols:
        bars = f10.daily_bars(settings, symbol, until=day)
        read = f10.evaluate(bars, day)
        if read is None:
            values[symbol] = {"evaluable": False, "bars": int(len(bars)), "triggered": False}
            continue
        sigma = sigma_for(forecast, symbol) if forecast is not None else None
        values[symbol] = {"evaluable": True, **read, "sigma": round(sigma, 6) if sigma else None}
        if read["triggered"]:
            event_id = hashlib.sha256(f"{TEST_ID}:{symbol}:{key}".encode()).hexdigest()[:16]
            days = placebo_days(event_id)
            journal.append(EVENT, {"event_id": event_id, "symbol": symbol, "day": key, "detected_at": utc_iso(moment),
                                   "entry_at": utc_iso(moment), "sigma": sigma, "playable": sigma is not None,
                                   "forecast_origin": forecast.get("origin") if forecast else None,
                                   "forecast_source_run": forecast.get("source_run") if forecast else None,
                                   "forecast_models": forecast.get("models") if forecast else None, "resistance": read["resistance"],
                                   "placebo_days": days, "placebo_entries": [utc_iso(moment - pd.Timedelta(days=d)) for d in days]},
                           now=moment)
            events += 1
    journal.append(CHECK, {"day": key, "evaluable": sum(1 for v in values.values() if v["evaluable"]), "values": values,
                           "events": events}, now=moment)
    return {"checks": 1, "events": events}


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def hourly_after(settings: Settings, symbol: str, entry: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h ouvertes de l'heure pleine qui suit l'entrée jusqu'à 168 h après l'entrée (exclue)."""
    first = entry.ceil("h")
    frame = CandleStore(settings.data_dir).load_since(symbol, "1h", first)
    frame = frame[(frame["open_time"] >= first) & (frame["open_time"] + pd.Timedelta(hours=1) <= entry + HOLD)]
    return frame.sort_values("open_time").reset_index(drop=True)


def contiguous(bars: pd.DataFrame, entry: pd.Timestamp) -> bool:
    """Toutes les bougies 1 h attendues : de l'heure pleine qui suit l'entrée à la dernière qui ferme avant 168 h,
    sans trou, ni au début ni à la fin."""
    expected = int(((entry + HOLD) - entry.ceil("h")) / pd.Timedelta(hours=1))
    if expected <= 0 or len(bars) != expected:
        return False
    first, last = pd.Timestamp(bars["open_time"].iloc[0]), pd.Timestamp(bars["open_time"].iloc[-1])
    hour = pd.Timedelta(hours=1)
    return (first == entry.ceil("h") and last + hour <= entry + HOLD < last + 2 * hour
            and bool(bars["open_time"].diff().dropna().eq(hour).all()))


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest: PublicHttpClient | None = None) -> dict:
    done = set(f10._first_by(journal.entries({RESOLUTION}), "event_id"))
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    counts: dict[str, int] = {}
    for event in f10._first_by(journal.entries({EVENT}), "event_id").values():
        if event["event_id"] in done or not event.get("playable"):
            continue
        entry_at = pd.Timestamp(event["entry_at"])
        if moment < entry_at + HOLD + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry_at + HOLD + GAP_AFTER
        symbol, sigma = event["symbol"], float(event["sigma"])
        times = {"event": entry_at, **{f"placebo_{i}": pd.Timestamp(t) for i, t in enumerate(event["placebo_entries"])}}
        legs: dict[str, dict] = {}
        gap = unavailable = False
        for name, begin in times.items():
            legs[name] = {}
            for leg, when in (("entree", begin), ("sortie", begin + HOLD)):
                try:
                    bar = f10.minute_bar(client, symbol, when)
                except HttpError:
                    bar, unavailable = None, True
                if bar is None or pd.Timestamp(bar[0]) - when > MAX_BAR_DELAY:
                    gap = True
                legs[name][leg] = bar
            hours = hourly_after(settings, symbol, begin)
            if not contiguous(hours, begin):
                gap = True
            legs[name]["path"] = hours[["open", "high", "low"]].to_numpy(float).round(10).tolist()
        if unavailable and not late:
            continue
        data: dict = {"event_id": event["event_id"], "symbol": symbol, "entry_at": event["entry_at"], "sigma": sigma}
        if gap:
            data |= {"gap": True, "results": None}
        else:
            results: dict = {}
            outcomes = {}
            for name, recorded in legs.items():
                path = pd.DataFrame(recorded["path"], columns=["open", "high", "low"])
                outcomes[name] = play(recorded["entree"][1], sigma, path, recorded["sortie"][1])
            for scenario in SCENARIOS:
                r = {name: r_of(legs[name]["entree"][1], price, outcome, sigma, symbol, scenario)
                     for name, (outcome, price) in outcomes.items()}
                event_r = r["event"]
                placebos = [r[f"placebo_{i}"] for i in range(len(event["placebo_entries"]))]
                timed = net_return(legs["event"]["entree"][1], legs["event"]["sortie"][1], symbol, scenario, market_exit=True) / (STOP_SIGMA * sigma)
                results[scenario] = {"event_r": round(event_r, 6), "outcome": outcomes["event"][0],
                                     "placebo_mean": round(float(np.mean(placebos)), 6), "placebos": [round(p, 6) for p in placebos],
                                     "timed_r": round(timed, 6), "vs_placebos": round(event_r - float(np.mean(placebos)), 6),
                                     "vs_sortie_a_date": round(event_r - timed, 6)}
            data |= {"gap": False, "results": results, "legs_sha256": hashlib.sha256(canonical(legs).encode("utf-8")).hexdigest()}
        journal.append(RESOLUTION, data, now=moment)
        counts["TROU" if gap else "RESOLU"] = counts.get("TROU" if gap else "RESOLU", 0) + 1
    return counts


def _measure(rows: list[dict], scenario: str, name: str, level: float) -> dict:
    if not rows:
        return {"n": 0}
    values = np.array([r["results"][scenario][name] for r in rows], float)
    times = pd.to_datetime([r["entry_at"] for r in rows], utc=True).to_numpy()
    ci, _ = day_block_ci(values, times, block_days=BLOCK_DAYS, samples=SAMPLES, seed=SEED, level=level, min_blocks=8)
    return {"n": int(len(rows)), "mean": round(float(values.mean()), 6), "win_share": round(float((values > 0).mean()), 4),
            "event_r": round(float(np.mean([r["results"][scenario]["event_r"] for r in rows])), 6), "ci": ci}


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    checks = f10._first_by(journal.entries({CHECK}), "day")
    events = f10._first_by(journal.entries({EVENT}), "event_id")
    resolutions = f10._first_by(journal.entries({RESOLUTION}), "event_id")
    playable = {k: e for k, e in events.items() if e.get("playable")}
    out: dict = {"checks": len(checks), "events": len(events), "decisions": len(playable),
                 "without_forecast": len(events) - len(playable), "by_asset": {},
                 "pending": sum(1 for k in playable if k not in resolutions),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None), "scenarios": {}}
    for event in playable.values():
        out["by_asset"][event["symbol"]] = out["by_asset"].get(event["symbol"], 0) + 1
    rows = [r for k, r in resolutions.items() if k in playable and r.get("results") is not None]
    level = 1 - ALPHA / len(COMPARISONS)
    for scenario in SCENARIOS:
        out["scenarios"][scenario] = {name: _measure(rows, scenario, name, level) for name in COMPARISONS}
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and out["pending"] == 0
    out["verdicts"] = verdict(out["scenarios"], ended=ended)
    out["verdict"] = ", ".join(f"{k}: {v}" for k, v in out["verdicts"].items())
    out["ended"] = bool(ended)
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "verdicts": result["verdicts"], "central": result["scenarios"][CENTRAL],
                                 "events": result["events"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Cassure journalière d'un pivot haut, niveaux placés par la volatilité prévue (40 paires)",
    hypothesis=("Sur les 40 paires de recherche, un achat après une cassure journalière d'un pivot haut confirmé, avec un stop à "
                "1 σ̂ et un objectif à 1,5 σ̂ (σ̂ : mouvement typique prévu à 3 jours par la prévision en service), fait mieux, "
                "net de frais et en R, que 20 achats placebo de même géométrie, et que le même achat sorti à date fixe (168 h)."),
    params={"symbols": list(SYMBOLS), "sigma": "move_pct à 3 jours de state/volatility.json (prévision en service du jour)",
            "stop_sigma": STOP_SIGMA, "target_sigma": TARGET_SIGMA, "hold_hours": 168, "placebos": PLACEBOS,
            "placebo_days": [PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS], "min_events": MIN_EVENTS, "alpha": ALPHA,
            "comparisons": list(COMPARISONS), "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "gap_after_days": GAP_AFTER.days, "max_bar_delay_minutes": 10, "events": "règles de F10 (fonctions gelées)",
            "forecast_deadline_hours": 23, "target_gap_fill": "prix limite"},
    rule_objects=(), config_keys=("data.rest_base_url",),
    frozen_modules=("crypto_signal_intelligence.forward.f14", "crypto_signal_intelligence.forward.f13",
                    "crypto_signal_intelligence.forward.f10", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal",
                    "crypto_signal_intelligence.outlook.volatility"),
    frozen_functions=f10.TEST.frozen_functions + (
        ("crypto_signal_intelligence.research.volatility", "daily_frame"),
        ("crypto_signal_intelligence.research.volatility", "complete_rows"),
        ("crypto_signal_intelligence.research.volatility", "fit_at"),
        ("crypto_signal_intelligence.research.volatility", "month_forecasts")),
)
