"""Test en direct F7_LISTINGS : achat simulé après une annonce de listing d'Upbit ou l'apparition d'un produit chez
Coinbase (docs/FORWARD_TESTS.md, section F7_LISTINGS ; règles figées au démarrage). Phase 5 de la mission du
2026-10-02.

Toutes les 10 minutes : annonces publiques d'Upbit (catégorie « 거래 », titres « … (SYMBOLE) 신규 거래지원 안내 ») et
liste publique des produits de Coinbase (nouvelle devise de base en ligne). Pour un actif négociable au comptant
sur Binance ET admis par la liste halal figée : achat simulé au premier prix Binance (bougie 1 min) après la
DÉTECTION (jamais avant ; latence annonce → détection journalisée), sorties à 24 h et 7 jours, frais inclus,
rendements aussi relatifs à BTC acheté et vendu aux mêmes instants. Les autres listings sont comptés.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest
from .sources import PublicSources, SourceError

TEST_ID = "F7_LISTINGS"
MARKET = "BTCUSDT"
UPBIT_URL = "https://api-manager.upbit.com/api/v1/announcements"
COINBASE_URL = "https://api.exchange.coinbase.com/products"
UPBIT_LISTING = re.compile(r"\(([A-Z0-9]{2,10}(?:,\s*[A-Z0-9]{2,10})*)\)\s*신규\s*거래지원")
HORIZONS = {"24h": pd.Timedelta(hours=24), "7j": pd.Timedelta(days=7)}
MAX_BAR_DELAY = pd.Timedelta(minutes=10)
MIN_EVENTS = 10
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261007
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
LISTING, STATE, RESOLUTION, SOURCE_ERROR = "LISTING", "ETAT_COINBASE", "RESOLUTION", "ERREUR_SOURCE"
DECIDED, COUNTED = "DECISION", "COMPTE"
POSITIVE, NEGATIVE, NO_DIFFERENCE, INSUFFICIENT, RUNNING = ("RENDEMENT_POSITIF", "RENDEMENT_NEGATIF",
                                                            "PAS_DE_DIFFERENCE_DEMONTREE", "INSUFFISANT", "EN_COURS")


# --- Règles pures ------------------------------------------------------------------------------------------

def upbit_listings(notices: list[dict]) -> list[dict]:
    """Annonces de nouveau support de trading : (identifiant, actifs, heure d'annonce UTC, titre)."""
    out = []
    for notice in notices:
        title = str(notice.get("title", ""))
        match = UPBIT_LISTING.search(title)
        if not match:
            continue
        try:
            listed = pd.Timestamp(notice.get("listed_at") or notice.get("first_listed_at")).tz_convert("UTC")
        except (TypeError, ValueError):
            continue
        out.append({"id": f"upbit:{notice.get('id')}", "assets": [a.strip() for a in match.group(1).split(",")],
                    "announced_at": utc_iso(listed), "title": title, "source": "upbit"})
    return out


def coinbase_bases(products: list[dict]) -> set[str]:
    return {str(p.get("base_currency", "")).upper() for p in products
            if isinstance(p, dict) and p.get("status") == "online" and p.get("base_currency")}


def net_return(entry_open: float, exit_open: float, symbol: str, scenario: str) -> float:
    costs = costs_for(symbol, scenario)
    return exit_open * (1 - costs.market) * (1 - costs.fee) / (entry_open * (1 + costs.market) * (1 + costs.fee)) - 1


def verdict(scenarios: dict, *, ended: bool) -> dict:
    if not ended:
        return {h: RUNNING for h in HORIZONS}
    out = {}
    for horizon in HORIZONS:
        central, adverse = scenarios.get(CENTRAL, {}).get(horizon, {}), scenarios.get(ADVERSE, {}).get(horizon, {})
        if central.get("n", 0) < MIN_EVENTS or central.get("r_ci") is None or adverse.get("r_ci") is None:
            out[horizon] = INSUFFICIENT
        elif central["r_ci"][0] > 0 and adverse["r_ci"][0] > 0:
            out[horizon] = POSITIVE
        elif central["r_ci"][1] < 0 and adverse["r_ci"][1] < 0:
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


def _log_error(journal: Journal, source: str, error: str, *, now: pd.Timestamp) -> None:
    last = None
    for entry in journal.entries({SOURCE_ERROR}):
        if entry["data"]["source"] == source:
            last = entry
    if last and last["data"]["error"] == error and now - pd.Timestamp(last["at"]) < pd.Timedelta(hours=6):
        return
    journal.append(SOURCE_ERROR, {"source": source, "error": error[:200]}, now=now)


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime, client: PublicSources | None = None) -> dict:
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    frozen = set(start["halal"]["symbols"])
    known = _first_by(journal.entries({LISTING}), "id")
    source = client or PublicSources()
    counts = {"listings": 0, "decisions": 0}
    found: list[dict] = []
    try:
        try:
            reply = source.get_json(UPBIT_URL, {"os": "web", "page": 1, "per_page": 20, "category": "trade"})
            notices = ((reply or {}).get("data") or {}).get("notices") if isinstance(reply, dict) else None
            if not isinstance(notices, list):
                raise SourceError("Upbit : réponse sans « notices »")
            found += upbit_listings(notices)
        except (SourceError, PermissionError) as exc:
            _log_error(journal, "upbit", str(exc), now=moment)
        try:
            products = source.get_json(COINBASE_URL)
            if not isinstance(products, list):
                raise SourceError("Coinbase : liste de produits attendue")
            bases = coinbase_bases(products)
            previous = journal.first(STATE)
            states = list(journal.entries({STATE}))
            last_state = set(states[-1]["data"]["bases"]) if states else None
            if last_state is None:
                journal.append(STATE, {"bases": sorted(bases), "count": len(bases)}, now=moment)
            elif bases - last_state:
                journal.append(STATE, {"bases": sorted(bases), "count": len(bases), "added": sorted(bases - last_state)}, now=moment)
                for asset in sorted(bases - last_state):
                    found.append({"id": f"coinbase:{asset}:{str(moment)[:10]}", "assets": [asset], "announced_at": None,
                                  "title": f"nouveau produit Coinbase {asset}", "source": "coinbase"})
            del previous
        except (SourceError, PermissionError) as exc:
            _log_error(journal, "coinbase", str(exc), now=moment)
    finally:
        if client is None:
            source.close()
    for listing in found:
        if listing["id"] in known:
            continue
        announced = pd.Timestamp(listing["announced_at"]) if listing["announced_at"] else None
        if announced is not None and announced < started:
            continue                                                  # annonce antérieure au démarrage : hors test
        data = dict(listing) | {"detected_at": utc_iso(moment),
                                "latency_min": round((moment - announced).total_seconds() / 60, 2) if announced is not None else None}
        playable = [a for a in listing["assets"] if f"{a}USDT" in frozen]
        if not playable or moment >= final:
            data |= {"status": COUNTED, "reason": "hors fenêtre" if moment >= final else "actif absent de la liste halal figée ou non négociable sur Binance"}
        else:
            data |= {"status": DECIDED, "symbols": [f"{a}USDT" for a in playable], "entry_at": utc_iso(moment)}
            counts["decisions"] += 1
        journal.append(LISTING, data, now=moment)
        known[listing["id"]] = data
        counts["listings"] += 1
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
    done = set(_first_by(journal.entries({RESOLUTION}), "key"))
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    counts: dict[str, int] = {}
    for listing in _first_by(journal.entries({LISTING}), "id").values():
        if listing["status"] != DECIDED:
            continue
        entry = pd.Timestamp(listing["entry_at"])
        if moment < entry + HORIZONS["7j"] + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry + HORIZONS["7j"] + GAP_AFTER
        for symbol in listing["symbols"]:
            key = f"{listing['id']}:{symbol}"
            if key in done:
                continue
            bars: dict[str, dict] = {}
            gap = unavailable = False
            for name, sym in (("asset", symbol), ("btc", MARKET)):
                bars[name] = {}
                for leg, when in {"entree": entry, **{h: entry + d for h, d in HORIZONS.items()}}.items():
                    try:
                        bar = minute_bar(client, sym, when)
                    except HttpError:
                        bar, unavailable = None, True
                    if bar is None or pd.Timestamp(bar[0]) - when > MAX_BAR_DELAY:
                        gap = True
                    bars[name][leg] = bar
            if unavailable and not late:
                continue
            data: dict = {"key": key, "id": listing["id"], "symbol": symbol, "entry_at": listing["entry_at"], "source": listing["source"]}
            if gap:
                data |= {"gap": True, "results": None, "bars": bars}
            else:
                results: dict = {}
                for scenario in SCENARIOS:
                    results[scenario] = {}
                    for horizon in HORIZONS:
                        r = net_return(bars["asset"]["entree"][1], bars["asset"][horizon][1], symbol, scenario)
                        btc = net_return(bars["btc"]["entree"][1], bars["btc"][horizon][1], MARKET, scenario)
                        results[scenario][horizon] = {"r": round(r, 6), "btc_r": round(btc, 6), "relative_r": round(r - btc, 6)}
                data |= {"gap": False, "results": results, "bars": bars,
                         "bars_sha256": hashlib.sha256(canonical(bars).encode("utf-8")).hexdigest()}
            journal.append(RESOLUTION, data, now=moment)
            counts["TROU" if gap else "RESOLU"] = counts.get("TROU" if gap else "RESOLU", 0) + 1
    return counts


def _measure(rows: list[dict], scenario: str, horizon: str, level: float, *, samples: int, seed: int) -> dict:
    if not rows:
        return {"n": 0}
    r = np.array([x["results"][scenario][horizon]["r"] for x in rows], float)
    rel = np.array([x["results"][scenario][horizon]["relative_r"] for x in rows], float)
    times = pd.to_datetime([x["entry_at"] for x in rows], utc=True).to_numpy()
    ci, _ = day_block_ci(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level, min_blocks=8)
    ci_rel, _ = day_block_ci(rel, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level, min_blocks=8)
    return {"n": int(len(r)), "r_mean": round(float(r.mean()), 6), "r_ci": ci, "win_share": round(float((r > 0).mean()), 4),
            "relative_r_mean": round(float(rel.mean()), 6), "relative_r_ci": ci_rel}


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    listings = _first_by(journal.entries({LISTING}), "id")
    resolutions = _first_by(journal.entries({RESOLUTION}), "key")
    decided = {k: e for k, e in listings.items() if e["status"] == DECIDED}
    expected = {f"{k}:{s}" for k, e in decided.items() for s in e["symbols"]}
    latencies = [e["latency_min"] for e in decided.values() if e.get("latency_min") is not None]
    out: dict = {"listings": len(listings), "decisions": len(decided),
                 "by_source": {s: sum(1 for e in listings.values() if e["source"] == s) for s in ("upbit", "coinbase")},
                 "counted": sum(1 for e in listings.values() if e["status"] == COUNTED),
                 "latency_median_min": round(float(np.median(latencies)), 1) if latencies else None,
                 "pending": sum(1 for k in expected if k not in resolutions),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None),
                 "source_errors": sum(1 for _ in journal.entries({SOURCE_ERROR})), "scenarios": {}}
    rows = [r for k, r in resolutions.items() if k in expected and r.get("results") is not None]
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
                                 "listings": result["listings"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Achat après une annonce de listing d'Upbit ou l'apparition d'un produit chez Coinbase",
    hypothesis=("Un actif négociable sur Binance et admis par le screening, acheté au premier prix après la détection d'un "
                "listing chez Upbit ou Coinbase, rapporte en moyenne un rendement net positif à 24 h et à 7 jours, en "
                "absolu et relativement à BTC."),
    params={"sources": {"upbit": UPBIT_URL, "coinbase": COINBASE_URL}, "upbit_pattern": UPBIT_LISTING.pattern,
            "entry": "premier prix Binance (bougie 1 min) après la détection ; latence annonce → détection journalisée",
            "horizons": list(HORIZONS), "relative_to": MARKET, "min_events": MIN_EVENTS, "alpha": ALPHA,
            "comparisons": len(HORIZONS), "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "gap_after_days": GAP_AFTER.days, "max_bar_delay_minutes": 10},
    rule_objects=(), config_keys=("data.rest_base_url",),
    frozen_modules=("crypto_signal_intelligence.forward.f7", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),),
)
