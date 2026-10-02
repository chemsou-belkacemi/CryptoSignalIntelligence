"""Test en direct F11_SELL_PRESSURE_VETO : achat simulé le lendemain d'une journée de pression vendeuse extrême
(docs/FORWARD_TESTS.md, section F11_SELL_PRESSURE_VETO ; règles figées au démarrage).

Piste APRÈS COUP du criblage J (docs/SCREENING.md, SCREEN-20261002T165546Z-0994a2) : la condition J2 (part des
achats au marché ≤ 10e centile) avait un excès NÉGATIF à 1 et 7 jours sans avoir été déclarée comme veto. Ce test
mesure en direct si acheter le lendemain d'une telle journée fait moins bien que des achats placebo : la réponse
attendue est un excès négatif (veto justifié), rien n'est acheté ni évité en pratique.

Chaque jour après 00:10 UTC : part des achats au marché (taker) dans le volume en USDT de la veille (24 bougies 1 h,
au moins 20) ; événement si elle est ≤ au seuil FIGÉ de la paire (10e centile des 365 dernières journées de la
période de développement) → achat simulé au premier prix (bougie 1 min) après le calcul ; sorties à 24 h et
168 h ; placebos à la même heure, 1 à 30 jours avant.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest

TEST_ID = "F11_SELL_PRESSURE_VETO"
#: 10e centile de la part journalière des achats au marché (taker_buy_quote_volume / quote_volume, journées d'au moins
#: 20 bougies 1 h) sur les 365 dernières journées de DEVELOPMENT (2024-07-01 → 2025-06-30), magasin long ; calculé le
#: 2026-10-02 avec la même définition que `research.flow_screen.daily_flow`.
THRESHOLDS: dict[str, float] = {
    "BTCUSDT": 0.4570, "ETHUSDT": 0.4681, "SOLUSDT": 0.4692, "XRPUSDT": 0.4652, "NEARUSDT": 0.4704,
    "AVAXUSDT": 0.4622, "HBARUSDT": 0.4521, "LINKUSDT": 0.4447, "XLMUSDT": 0.4470, "ADAUSDT": 0.4629,
    "TRXUSDT": 0.4548, "FILUSDT": 0.4523, "ALGOUSDT": 0.4669, "DOTUSDT": 0.4536, "ATOMUSDT": 0.4521,
    "ETCUSDT": 0.4313,
}
MIN_HOURS = 20                                   # journée valide : au moins 20 bougies horaires (comme au criblage)
HORIZONS = {"24h": pd.Timedelta(hours=24), "168h": pd.Timedelta(hours=168)}
CHECK_AFTER = pd.Timedelta(minutes=10)
PLACEBOS, PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS = 20, 1, 30
MAX_BAR_DELAY = pd.Timedelta(minutes=10)
MIN_EVENTS = 30
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261011
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
CHECK, EVENT, RESOLUTION = "CONTROLE", "EVENEMENT", "RESOLUTION"
POSITIVE, NEGATIVE, NO_DIFFERENCE, INSUFFICIENT, RUNNING = ("EXCES_POSITIF", "EXCES_NEGATIF",
                                                            "PAS_DE_DIFFERENCE_DEMONTREE", "INSUFFISANT", "EN_COURS")


# --- Règles pures ------------------------------------------------------------------------------------------

def taker_share(candles: pd.DataFrame, day: pd.Timestamp) -> dict | None:
    """Part des achats au marché dans le volume en USDT de la journée qui précède `day` (bougies 1 h ouvertes de
    day − 1 j 00:00 à 23:00) ; None si moins de MIN_HOURS bougies ou volume nul."""
    start = day - pd.Timedelta(days=1)
    times = pd.to_datetime(candles["open_time"], utc=True)
    part = candles[(times >= start) & (times < day)]
    total = float(part["quote_volume"].sum()) if len(part) else 0.0
    if len(part) < MIN_HOURS or total <= 0:
        return None
    return {"share": float(part["taker_buy_quote_volume"].sum()) / total, "hours": int(len(part))}


def triggered(symbol: str, share: float | None) -> bool:
    threshold = THRESHOLDS.get(symbol)
    return bool(threshold is not None and share is not None and share <= threshold + 1e-12)


def placebo_days(event_id: str) -> list[int]:
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{event_id}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS + 1), PLACEBOS))


def net_return(entry_open: float, exit_open: float, symbol: str, scenario: str) -> float:
    costs = costs_for(symbol, scenario)
    return exit_open * (1 - costs.market) * (1 - costs.fee) / (entry_open * (1 + costs.market) * (1 + costs.fee)) - 1


def verdict(scenarios: dict, *, ended: bool) -> dict:
    """EXCES_NEGATIF = veto justifié (la réponse attendue) ; EXCES_POSITIF contredirait le criblage."""
    if not ended:
        return {h: RUNNING for h in HORIZONS}
    out = {}
    for horizon in HORIZONS:
        central, adverse = scenarios.get(CENTRAL, {}).get(horizon, {}), scenarios.get(ADVERSE, {}).get(horizon, {})
        if central.get("n", 0) < MIN_EVENTS or central.get("excess_ci") is None or adverse.get("excess_ci") is None:
            out[horizon] = INSUFFICIENT
        elif central["excess_ci"][1] < 0 and adverse["excess_ci"][1] < 0:
            out[horizon] = NEGATIVE
        elif central["excess_ci"][0] > 0 and adverse["excess_ci"][0] > 0:
            out[horizon] = POSITIVE
        else:
            out[horizon] = NO_DIFFERENCE
    return out


# --- Journal ---------------------------------------------------------------------------------------------

def _first_by(entries, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"][field], entry["data"])
    return out


def hourly_candles(settings: Settings, symbol: str, *, until: pd.Timestamp) -> pd.DataFrame:
    frame = CandleStore(settings.data_dir).load_since(symbol, "1h", until - pd.Timedelta(days=2))
    return frame[frame["open_time"] + pd.Timedelta(hours=1) <= until]


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final or moment < day + CHECK_AFTER:
        return {"checks": 0}
    if key in _first_by(journal.entries({CHECK}), "day"):
        return {"checks": 0}
    symbols = [s for s in THRESHOLDS if s in set(settings.data.symbols) and s in set(start["halal"]["symbols"])]
    values: dict[str, dict] = {}
    events = 0
    for symbol in symbols:
        read = taker_share(hourly_candles(settings, symbol, until=day), day)
        if read is None:
            values[symbol] = {"evaluable": False, "triggered": False}
            continue
        hit = triggered(symbol, read["share"])
        values[symbol] = {"evaluable": True, "share": round(read["share"], 4), "hours": read["hours"], "threshold": THRESHOLDS[symbol],
                          "triggered": hit}
        if hit:
            event_id = hashlib.sha256(f"{TEST_ID}:{symbol}:{key}".encode()).hexdigest()[:16]
            days = placebo_days(event_id)
            journal.append(EVENT, {"event_id": event_id, "symbol": symbol, "day": key, "detected_at": utc_iso(moment),
                                   "entry_at": utc_iso(moment), "share": values[symbol]["share"], "threshold": THRESHOLDS[symbol],
                                   "placebo_days": days, "placebo_entries": [utc_iso(moment - pd.Timedelta(days=d)) for d in days]},
                           now=moment)
            events += 1
    journal.append(CHECK, {"day": key, "evaluable": sum(1 for v in values.values() if v["evaluable"]), "values": values,
                           "events": events}, now=moment)
    return {"checks": 1, "events": events}


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def minute_bar(rest: PublicHttpClient, symbol: str, when: pd.Timestamp) -> list | None:
    rows = rest.get_json("/api/v3/klines", {"symbol": symbol, "interval": "1m", "startTime": int(when.timestamp() * 1000), "limit": 1})
    if not rows:
        return None
    k = rows[0]
    return [utc_iso(pd.Timestamp(int(k[0]), unit="ms", tz="UTC")), float(k[1]), float(k[2]), float(k[3]), float(k[4])]


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest: PublicHttpClient | None = None) -> dict:
    done = set(_first_by(journal.entries({RESOLUTION}), "event_id"))
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    counts: dict[str, int] = {}
    for event in _first_by(journal.entries({EVENT}), "event_id").values():
        if event["event_id"] in done:
            continue
        entry = pd.Timestamp(event["entry_at"])
        if moment < entry + HORIZONS["168h"] + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry + HORIZONS["168h"] + GAP_AFTER
        symbol = event["symbol"]
        times = {"event": entry, **{f"placebo_{i}": pd.Timestamp(t) for i, t in enumerate(event["placebo_entries"])}}
        bars: dict[str, dict] = {}
        gap = unavailable = False
        for name, begin in times.items():
            bars[name] = {}
            for leg, when in {"entree": begin, **{h: begin + d for h, d in HORIZONS.items()}}.items():
                try:
                    bar = minute_bar(client, symbol, when)
                except HttpError:
                    bar, unavailable = None, True
                if bar is None or pd.Timestamp(bar[0]) - when > MAX_BAR_DELAY:
                    gap = True
                bars[name][leg] = bar
        if unavailable and not late:
            continue
        data: dict = {"event_id": event["event_id"], "symbol": symbol, "entry_at": event["entry_at"]}
        if gap:
            data |= {"gap": True, "results": None, "bars": bars}
        else:
            results: dict = {}
            for scenario in SCENARIOS:
                results[scenario] = {}
                for horizon in HORIZONS:
                    event_r = net_return(bars["event"]["entree"][1], bars["event"][horizon][1], symbol, scenario)
                    placebos = [net_return(bars[f"placebo_{i}"]["entree"][1], bars[f"placebo_{i}"][horizon][1], symbol, scenario)
                                for i in range(len(event["placebo_entries"]))]
                    results[scenario][horizon] = {"event_r": round(event_r, 6), "placebo_mean": round(float(np.mean(placebos)), 6),
                                                  "placebos": [round(p, 6) for p in placebos], "excess": round(event_r - float(np.mean(placebos)), 6)}
            data |= {"gap": False, "results": results, "bars": bars,
                     "bars_sha256": hashlib.sha256(canonical(bars).encode("utf-8")).hexdigest()}
        journal.append(RESOLUTION, data, now=moment)
        counts["TROU" if gap else "RESOLU"] = counts.get("TROU" if gap else "RESOLU", 0) + 1
    return counts


def _measure(rows: list[dict], scenario: str, horizon: str, level: float, *, samples: int, seed: int) -> dict:
    if not rows:
        return {"n": 0}
    excess = np.array([r["results"][scenario][horizon]["excess"] for r in rows], float)
    times = pd.to_datetime([r["entry_at"] for r in rows], utc=True).to_numpy()
    ci, _ = day_block_ci(excess, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level, min_blocks=8)
    return {"n": int(len(rows)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()),
            "event_r": round(float(np.mean([r["results"][scenario][horizon]["event_r"] for r in rows])), 6),
            "placebo_r": round(float(np.mean([r["results"][scenario][horizon]["placebo_mean"] for r in rows])), 6),
            "excess": round(float(excess.mean()), 6), "win_share": round(float((excess > 0).mean()), 4), "excess_ci": ci}


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    checks = _first_by(journal.entries({CHECK}), "day")
    events = _first_by(journal.entries({EVENT}), "event_id")
    resolutions = _first_by(journal.entries({RESOLUTION}), "event_id")
    out: dict = {"checks": len(checks), "events": len(events), "decisions": len(events),
                 "by_asset": {}, "pending": sum(1 for k in events if k not in resolutions),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None), "scenarios": {}}
    for event in events.values():
        out["by_asset"][event["symbol"]] = out["by_asset"].get(event["symbol"], 0) + 1
    rows = [r for k, r in resolutions.items() if k in events and r.get("results") is not None]
    level = 1 - ALPHA / len(HORIZONS)
    for scenario in SCENARIOS:
        out["scenarios"][scenario] = {h: _measure(rows, scenario, h, level, samples=samples, seed=seed) for h in HORIZONS}
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
    test_id=TEST_ID, title="Achat le lendemain d'une pression vendeuse extrême (veto J2 en direct)",
    hypothesis=("Quand la part des achats au marché dans le volume d'une journée tombe sous son 10e centile figé, un achat "
                "simulé le lendemain matin fait MOINS bien, net de frais, que 20 achats placebo de la même paire aux mêmes "
                "heures dans les 30 jours précédents, à 24 h et 168 h (excès négatif attendu : veto justifié)."),
    params={"thresholds": THRESHOLDS, "threshold_rule": "10e centile de la part journalière des achats au marché, 365 dernières "
            "journées de DEVELOPMENT (2024-07-01 → 2025-06-30)", "condition": "part de la veille ≤ seuil", "min_hours": MIN_HOURS,
            "horizons": list(HORIZONS), "placebos": PLACEBOS, "placebo_days": [PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS],
            "min_events": MIN_EVENTS, "alpha": ALPHA, "comparisons": len(HORIZONS), "samples": SAMPLES, "seed": SEED,
            "block_days": BLOCK_DAYS, "gap_after_days": GAP_AFTER.days, "max_bar_delay_minutes": 10,
            "screen_run": "SCREEN-20261002T165546Z-0994a2", "expected": NEGATIVE},
    rule_objects=(), config_keys=("data.rest_base_url", "data.symbols"),
    frozen_modules=("crypto_signal_intelligence.forward.f11", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
