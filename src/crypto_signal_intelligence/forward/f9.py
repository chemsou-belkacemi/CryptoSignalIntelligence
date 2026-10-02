"""Test en direct F9_OI_FLUSH : achat simulé après une purge de l'intérêt ouvert (docs/FORWARD_TESTS.md, section
F9_OI_FLUSH ; règles figées au démarrage). Étape 5 du plan de travail validé le 2026-10-02.

Le criblage du marché à terme (docs/DERIVATIVES.md, SCREEN-20261001T111500Z-60702b) n'a rien démontré (AUCUNE_PISTE)
mais ses estimations ponctuelles pour OI_FLUSH étaient positives : ce test mesure la condition EN DIRECT, sur les
16 paires de la configuration ayant un perpétuel, avec 20 achats placebo par événement.

Chaque jour, dès que le relevé F0_DERIVES du jour est inscrit : pour chaque paire, variation de l'intérêt ouvert
(nombre de contrats) sur 24 h sous le seuil figé de la paire (10e centile de ses variations journalières sur la
période de développement, 2021 → 2025-06-30) ET clôture Spot journalière en baisse sur 24 h → achat simulé au
premier prix (bougie 1 min) après le calcul ; sorties à 24 h et 72 h ; placebos à la même heure, 1 à 30 jours avant.
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

TEST_ID = "F9_OI_FLUSH"
#: 10e centile de la variation journalière de l'intérêt ouvert (contrats), DEVELOPMENT (≤ 2025-06-30), magasin des
#: dérivés (archives publiques Binance USDⓈ-M, metrics 5 min, dernière valeur de chaque jour) ; calculé le 2026-10-02.
THRESHOLDS: dict[str, float] = {
    "BTCUSDT": -0.0441, "ETHUSDT": -0.0444, "SOLUSDT": -0.0583, "XRPUSDT": -0.0577, "NEARUSDT": -0.0600,
    "AVAXUSDT": -0.0564, "HBARUSDT": -0.0844, "LINKUSDT": -0.0583, "XLMUSDT": -0.0668, "ADAUSDT": -0.0546,
    "TRXUSDT": -0.0735, "FILUSDT": -0.0497, "ALGOUSDT": -0.0654, "DOTUSDT": -0.0397, "ATOMUSDT": -0.0563,
    "ETCUSDT": -0.0682,
}
HORIZONS = {"24h": pd.Timedelta(hours=24), "72h": pd.Timedelta(hours=72)}
PLACEBOS, PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS = 20, 1, 30
MAX_BAR_DELAY = pd.Timedelta(minutes=10)
MIN_EVENTS = 30
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261008
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
CHECK, EVENT, RESOLUTION = "CONTROLE", "EVENEMENT", "RESOLUTION"
POSITIVE, NEGATIVE, NO_DIFFERENCE, INSUFFICIENT, RUNNING = ("EXCES_POSITIF", "EXCES_NEGATIF",
                                                            "PAS_DE_DIFFERENCE_DEMONTREE", "INSUFFISANT", "EN_COURS")


# --- Règles pures ------------------------------------------------------------------------------------------

def oi_change_24h(record: dict, day: pd.Timestamp) -> float | None:
    """Dernière valeur d'intérêt ouvert à 00:00 ou avant, rapportée à celle de 24 h plus tôt (même relevé)."""
    rows = sorted((float(t), float(v)) for t, v, _ in record.get("open_interest", []) if t and v)
    day_ms = day.timestamp() * 1000
    now_oi = next((v for t, v in reversed(rows) if t <= day_ms), None)
    old_oi = next((v for t, v in reversed(rows) if t <= day_ms - 86_400_000), None)
    return now_oi / old_oi - 1 if now_oi and old_oi else None


def spot_change_24h(closes: pd.Series, day: pd.Timestamp) -> float | None:
    last, past = closes.get(day - pd.Timedelta(days=1)), closes.get(day - pd.Timedelta(days=2))
    return float(last) / float(past) - 1 if last and past and float(past) > 0 else None


def triggered(symbol: str, oi: float | None, spot: float | None) -> bool:
    threshold = THRESHOLDS.get(symbol)
    return bool(threshold is not None and oi is not None and spot is not None and oi <= threshold + 1e-9 and spot < 0)


def placebo_days(event_id: str) -> list[int]:
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{event_id}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS + 1), PLACEBOS))


def net_return(entry_open: float, exit_open: float, symbol: str, scenario: str) -> float:
    costs = costs_for(symbol, scenario)
    return exit_open * (1 - costs.market) * (1 - costs.fee) / (entry_open * (1 + costs.market) * (1 + costs.fee)) - 1


def verdict(scenarios: dict, *, ended: bool) -> dict:
    if not ended:
        return {h: RUNNING for h in HORIZONS}
    out = {}
    for horizon in HORIZONS:
        central, adverse = scenarios.get(CENTRAL, {}).get(horizon, {}), scenarios.get(ADVERSE, {}).get(horizon, {})
        if central.get("n", 0) < MIN_EVENTS or central.get("excess_ci") is None or adverse.get("excess_ci") is None:
            out[horizon] = INSUFFICIENT
        elif central["excess_ci"][0] > 0 and adverse["excess_ci"][0] > 0:
            out[horizon] = POSITIVE
        elif central["excess_ci"][1] < 0 and adverse["excess_ci"][1] < 0:
            out[horizon] = NEGATIVE
        else:
            out[horizon] = NO_DIFFERENCE
    return out


# --- Journal ---------------------------------------------------------------------------------------------

def _first_by(entries, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"][field], entry["data"])
    return out


def daily_closes(settings: Settings, symbol: str, *, until: pd.Timestamp) -> pd.Series:
    frame = CandleStore(settings.data_dir).load_since(symbol, "1h", until - pd.Timedelta(days=4))
    frame = frame[(frame["open_time"].dt.hour == 23) & (frame["open_time"] + pd.Timedelta(hours=1) <= until)]
    return pd.Series(frame["close"].to_numpy(float), index=pd.DatetimeIndex(frame["open_time"].dt.floor("D")))


def derivatives_records(settings: Settings, day: str, symbols: list[str]) -> dict[str, dict]:
    from .derivlog import PAIR, journal
    wanted = set(symbols)
    out = {}
    for entry in journal(settings).entries({PAIR}):
        data = entry["data"]
        if data.get("day") == day and data.get("spot") in wanted:
            out[data["spot"]] = data
    return out


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final:
        return {"checks": 0}
    if key in _first_by(journal.entries({CHECK}), "day"):
        return {"checks": 0}
    symbols = [s for s in THRESHOLDS if s in set(start["halal"]["symbols"])]
    records = derivatives_records(settings, key, symbols)
    if len(records) < max(1, len(symbols) // 2):
        return {"checks": 0, "waiting": f"relevé du jour incomplet ({len(records)}/{len(symbols)})"}
    values: dict[str, dict] = {}
    events = 0
    for symbol in symbols:
        record = records.get(symbol)
        oi = oi_change_24h(record, day) if record else None
        spot = spot_change_24h(daily_closes(settings, symbol, until=day), day)
        hit = triggered(symbol, oi, spot)
        values[symbol] = {"oi_change_24h": round(oi, 4) if oi is not None else None,
                          "spot_change_24h": round(spot, 4) if spot is not None else None, "triggered": hit}
        if hit:
            event_id = hashlib.sha256(f"{TEST_ID}:{symbol}:{key}".encode()).hexdigest()[:16]
            days = placebo_days(event_id)
            journal.append(EVENT, {"event_id": event_id, "symbol": symbol, "day": key, "detected_at": utc_iso(moment),
                                   "entry_at": utc_iso(moment), "oi_change_24h": values[symbol]["oi_change_24h"],
                                   "spot_change_24h": values[symbol]["spot_change_24h"], "threshold": THRESHOLDS[symbol],
                                   "placebo_days": days, "placebo_entries": [utc_iso(moment - pd.Timedelta(days=d)) for d in days]},
                           now=moment)
            events += 1
    journal.append(CHECK, {"day": key, "records": len(records), "values": values, "events": events}, now=moment)
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
        if moment < entry + HORIZONS["72h"] + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry + HORIZONS["72h"] + GAP_AFTER
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
    test_id=TEST_ID, title="Achat après une purge de l'intérêt ouvert (OI_FLUSH en direct)",
    hypothesis=("Quand l'intérêt ouvert d'une paire chute en 24 h sous son 10e centile historique et que le Spot baisse le "
                "même jour, un achat simulé le lendemain matin fait mieux, net de frais, que 20 achats placebo de la même "
                "paire aux mêmes heures dans les 30 jours précédents, à 24 h et 72 h."),
    params={"thresholds": THRESHOLDS, "threshold_rule": "10e centile des variations journalières d'OI, 2021 → 2025-06-30",
            "condition": "OI 24 h ≤ seuil ET clôture Spot 24 h < 0", "horizons": list(HORIZONS), "placebos": PLACEBOS,
            "placebo_days": [PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS], "min_events": MIN_EVENTS, "alpha": ALPHA,
            "comparisons": len(HORIZONS), "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "gap_after_days": GAP_AFTER.days, "max_bar_delay_minutes": 10},
    rule_objects=(), config_keys=("data.rest_base_url", "data.symbols"),
    frozen_modules=("crypto_signal_intelligence.forward.f9", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
