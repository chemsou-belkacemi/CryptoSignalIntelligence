"""Test en direct F6_CAPITULATION : achat simulé de BTC ou d'ETH après une capitulation du marché à levier
(docs/FORWARD_TESTS.md, section F6_CAPITULATION ; règles figées au démarrage). Phase 6 de la mission du 2026-10-02.

Chaque jour, dès que le relevé F0_DERIVES du jour est inscrit (après 00:10 UTC), pour BTC et ETH :
- financement moyen des 24 dernières heures strictement négatif,
- ET intérêt ouvert en baisse d'au moins 15 % sur 3 jours,
- ET clôture journalière en baisse d'au moins 10 % sur 3 jours.
Achat simulé au premier prix (bougie 1 min) après le calcul ; sorties à 3 et 7 jours ; pas de nouvel événement sur
le même actif dans les 7 jours ; 20 achats placebo du même actif à des dates tirées au hasard dans les 90 jours
précédents, mêmes durées, mêmes frais. Événements attendus : très peu (≈ 0,2 en 12 semaines sur l'historique).
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

TEST_ID = "F6_CAPITULATION"
ASSETS = ("BTCUSDT", "ETHUSDT")
FUNDING_HOURS = 24
OI_DROP, PRICE_DROP, LOOKBACK_DAYS = 0.15, 0.10, 3
COOLDOWN = pd.Timedelta(days=7)
HORIZONS = {"3j": pd.Timedelta(days=3), "7j": pd.Timedelta(days=7)}
PLACEBOS, PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS = 20, 1, 90
MAX_BAR_DELAY = pd.Timedelta(minutes=10)
MIN_EVENTS = 10
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261006
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
CHECK, EVENT, RESOLUTION = "CONTROLE", "EVENEMENT", "RESOLUTION"
DECIDED, COOLING, OUT_OF_WINDOW = "DECISION", "DELAI_7_JOURS", "HORS_FENETRE"
POSITIVE, NEGATIVE, NO_DIFFERENCE, INSUFFICIENT, RUNNING = ("EXCES_POSITIF", "EXCES_NEGATIF",
                                                            "PAS_DE_DIFFERENCE_DEMONTREE", "INSUFFISANT", "EN_COURS")


# --- Règles pures ------------------------------------------------------------------------------------------

def indicators(record: dict, closes: pd.Series, day: pd.Timestamp) -> dict:
    """Trois indicateurs à partir du relevé F0_DERIVES du jour (`record` : financement et intérêt ouvert) et des
    clôtures journalières (`closes`, indexées par jour UTC, clôture de la bougie de 23:00)."""
    day_ms = day.timestamp() * 1000
    rates = [float(r) for t, r, _ in record.get("funding", []) if t and day_ms - FUNDING_HOURS * 3600 * 1000 < float(t) <= day_ms]
    funding = float(np.mean(rates)) if rates else None
    oi = sorted((float(t), float(v)) for t, v, _ in record.get("open_interest", []) if t and v)
    now_oi = next((v for t, v in reversed(oi) if t <= day_ms), None)
    back = day_ms - LOOKBACK_DAYS * 86400 * 1000
    old_oi = next((v for t, v in reversed(oi) if t <= back), None)
    oi_change = now_oi / old_oi - 1 if now_oi and old_oi else None
    last = closes.get(day - pd.Timedelta(days=1))
    past = closes.get(day - pd.Timedelta(days=1 + LOOKBACK_DAYS))
    price_change = float(last) / float(past) - 1 if last and past and float(past) > 0 else None
    eps = 1e-9                                        # −15,000 % et −10,000 % exacts comptent (arrondi binaire)
    triggered = (funding is not None and funding < 0 and oi_change is not None and oi_change <= -OI_DROP + eps
                 and price_change is not None and price_change <= -PRICE_DROP + eps)
    return {"funding_24h": round(funding, 8) if funding is not None else None, "funding_rates": len(rates),
            "oi_change_3d": round(oi_change, 4) if oi_change is not None else None,
            "price_change_3d": round(price_change, 4) if price_change is not None else None, "triggered": bool(triggered)}


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
    """Clôture de la bougie 1 h de 23:00 de chaque jour (indexée par le jour UTC de la bougie), avant `until`."""
    frame = CandleStore(settings.data_dir).load_since(symbol, "1h", until - pd.Timedelta(days=LOOKBACK_DAYS + 3))
    frame = frame[(frame["open_time"].dt.hour == 23) & (frame["open_time"] + pd.Timedelta(hours=1) <= until)]
    return pd.Series(frame["close"].to_numpy(float), index=pd.DatetimeIndex(frame["open_time"].dt.floor("D")))


def derivatives_records(settings: Settings, day: str) -> dict[str, dict]:
    from .derivlog import PAIR, journal
    out = {}
    for entry in journal(settings).entries({PAIR}):
        data = entry["data"]
        if data.get("day") == day and data.get("spot") in ASSETS:
            out[data["spot"]] = data
    return out


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Dès que le relevé du jour existe pour un actif : contrôle inscrit une fois par jour et par actif, événement
    et décision si les trois conditions sont réunies."""
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    if day < started.floor("D") or day >= final:
        return {"checks": 0}
    frozen = set(start["halal"]["symbols"])
    done = {(c["day"], c["symbol"]) for c in _first_by(journal.entries({CHECK}), "key").values()}
    records = derivatives_records(settings, key)
    events = _first_by(journal.entries({EVENT}), "event_id")
    counts = {"checks": 0, "events": 0, "decisions": 0}
    for symbol in ASSETS:
        if (key, symbol) in done or symbol not in records or symbol not in frozen:
            continue
        closes = daily_closes(settings, symbol, until=day)
        values = indicators(records[symbol], closes, day)
        journal.append(CHECK, {"key": f"{key}:{symbol}", "day": key, "symbol": symbol, **values}, now=moment)
        counts["checks"] += 1
        if not values["triggered"]:
            continue
        recent = [e for e in events.values() if e["symbol"] == symbol and e["status"] == DECIDED
                  and day - pd.Timestamp(e["day"], tz="UTC") < COOLDOWN]
        event_id = hashlib.sha256(f"{TEST_ID}:{symbol}:{key}".encode()).hexdigest()[:16]
        data = {"event_id": event_id, "symbol": symbol, "day": key, "detected_at": utc_iso(moment), **values}
        if recent:
            data["status"] = COOLING
        else:
            days = placebo_days(event_id)
            data |= {"status": DECIDED, "entry_at": utc_iso(moment), "placebo_days": days,
                     "placebo_entries": [utc_iso(moment - pd.Timedelta(days=d)) for d in days]}
            counts["decisions"] += 1
        journal.append(EVENT, data, now=moment)
        events[event_id] = data
        counts["events"] += 1
    return counts


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
        if event["status"] != DECIDED or event["event_id"] in done:
            continue
        entry = pd.Timestamp(event["entry_at"])
        if moment < entry + HORIZONS["7j"] + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry + HORIZONS["7j"] + GAP_AFTER
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
    return {"n": int(len(rows)), "event_r": round(float(np.mean([r["results"][scenario][horizon]["event_r"] for r in rows])), 6),
            "placebo_r": round(float(np.mean([r["results"][scenario][horizon]["placebo_mean"] for r in rows])), 6),
            "excess": round(float(excess.mean()), 6), "win_share": round(float((excess > 0).mean()), 4), "excess_ci": ci}


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    checks = _first_by(journal.entries({CHECK}), "key")
    events = _first_by(journal.entries({EVENT}), "event_id")
    resolutions = _first_by(journal.entries({RESOLUTION}), "event_id")
    decided = {k: e for k, e in events.items() if e["status"] == DECIDED}
    latest = {}
    for check in checks.values():
        latest[check["symbol"]] = {k: check.get(k) for k in ("day", "funding_24h", "oi_change_3d", "price_change_3d")}
    out: dict = {"checks": len(checks), "days": len({c["day"] for c in checks.values()}), "latest": latest,
                 "events": len(events), "decisions": len(decided),
                 "by_status": {s: sum(1 for e in events.values() if e["status"] == s) for s in (DECIDED, COOLING)},
                 "by_asset": {a: sum(1 for e in decided.values() if e["symbol"] == a) for a in ASSETS},
                 "pending": sum(1 for k in decided if k not in resolutions),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None), "scenarios": {}}
    rows = [r for k, r in resolutions.items() if k in decided and r.get("results") is not None]
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
    test_id=TEST_ID, title="Achat de BTC ou d'ETH après une capitulation du marché à levier",
    hypothesis=("Après une liquidation massive des positions à levier (financement 24 h négatif, intérêt ouvert −15 % et prix "
                "−10 % sur 3 jours), les ventes forcées poussent le prix trop bas : un achat simulé après le calcul fait "
                "mieux, net de frais, que 20 achats placebo du même actif dans les 90 jours précédents, à 3 et 7 jours."),
    params={"assets": list(ASSETS), "funding_hours": FUNDING_HOURS, "oi_drop": OI_DROP, "price_drop": PRICE_DROP,
            "lookback_days": LOOKBACK_DAYS, "cooldown_days": COOLDOWN.days, "horizons": list(HORIZONS), "placebos": PLACEBOS,
            "placebo_days": [PLACEBO_MIN_DAYS, PLACEBO_MAX_DAYS], "min_events": MIN_EVENTS, "alpha": ALPHA,
            "comparisons": len(HORIZONS), "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "gap_after_days": GAP_AFTER.days, "max_bar_delay_minutes": 10,
            "inputs": "relevé F0_DERIVES du jour (financement, intérêt ouvert horaire) ; clôtures journalières des bougies 1 h"},
    rule_objects=(), config_keys=("data.rest_base_url",),
    frozen_modules=("crypto_signal_intelligence.forward.f6", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
