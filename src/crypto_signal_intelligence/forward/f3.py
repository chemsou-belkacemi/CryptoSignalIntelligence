"""Test en direct F3_STABLECOINS : achat simulé de BTC après une grosse émission d'USDT ou d'USDC
(docs/FORWARD_TESTS.md, section F3_STABLECOINS ; règles figées au démarrage). Phase 7 de la mission du 2026-10-02.

Événements : créations d'USDT (Tron et Ethereum) et d'USDC (Ethereum), lues sur les blockchains publiques à chaque
passage de la surveillance (TronGrid, nœud Ethereum public ; aucune clé). Une émission est « qualifiante » quand le
cumul des créations du MÊME stablecoin sur 1 heure atteint 100 M$. Plusieurs instants qualifiants à moins de 24 h
d'intervalle forment un seul événement. Achat simulé de BTC au premier prix (bougie 1 min) qui suit DÉTECTION + 5 min,
jamais à l'heure de l'émission ; sorties à 24 h et 72 h ; référence : 20 achats placebo à la même heure, à des dates
tirées au hasard dans les 30 jours précédents, mêmes durées, mêmes frais. Métrique : excès de rendement net de
l'achat sur la moyenne de ses placebos.
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
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest
from .sources import PublicSources, SourceError, eth_block_number, eth_block_timestamp, eth_logs, tron_events

TEST_ID = "F3_STABLECOINS"
SYMBOL = "BTCUSDT"
THRESHOLD_USD = 100_000_000.0
MIN_MINT_USD = 1_000_000.0                  # créations comptées dans le cumul (et inscrites au journal)
WINDOW = pd.Timedelta(hours=1)
SEPARATION = pd.Timedelta(hours=24)
LATENCY = pd.Timedelta(minutes=5)
MAX_LATENCY = pd.Timedelta(minutes=120)     # détectée plus tard : TARDIF, comptée, jamais jouée
LOOKBACK = pd.Timedelta(hours=3)            # fenêtre relue à chaque passage (Tron)
ETH_BLOCKS = 1000                           # ≈ 3,3 h de blocs Ethereum relus à chaque passage
HORIZONS = {"24h": pd.Timedelta(hours=24), "72h": pd.Timedelta(hours=72)}
PLACEBOS, LOOKBACK_DAYS = 20, 30
MAX_BAR_DELAY = pd.Timedelta(minutes=10)    # première bougie 1 min plus de 10 min après l'heure voulue : trou
MIN_EVENTS = 10
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261004
BLOCK_DAYS = 3
GAP_AFTER = pd.Timedelta(days=2)
ERROR_REPEAT = pd.Timedelta(hours=6)        # la même erreur de source n'est réinscrite qu'après ce délai

# Contrats (adresses publiques) et signatures d'événements (keccak-256 des signatures, contrôlées le 2026-10-02).
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
ETH_USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ETH_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
TOPIC_ISSUE = "0xcb8241adb0c3fdb35b70c24ce35c5eb0c17af7431c99f827d44a445ca624176a"      # Issue(uint256), Tether
TOPIC_MINT = "0xab8530f87dc9b59234c4623bf917212bb2536d647574c8e7e5da92c2ede0c9f8"       # Mint(address,address,uint256), Circle
# Émetteurs d'USDC exclus : les ponts CCTP de Circle créent des USDC déjà brûlés sur une autre chaîne (pas d'argent frais).
EXCLUDED_MINTERS = frozenset({"0xc4922d64a24675e16e1586e3e3aa56c06fabe907",     # CCTP v1 TokenMinter
                              "0xfd78ee919681417d192449715b2594ab58f5d002"})    # CCTP v2 TokenMinterV2
DECIMALS = 1e6

MINT, EVENT, RESOLUTION, SOURCE_ERROR = "EMISSION", "EVENEMENT", "RESOLUTION", "ERREUR_SOURCE"
DECIDED, LATE, OUT_OF_WINDOW, SKIPPED = "DECISION", "TARDIF", "HORS_FENETRE", "HORS_SCREENING"
POSITIVE, NEGATIVE, NO_DIFFERENCE = "EXCES_POSITIF", "EXCES_NEGATIF", "PAS_DE_DIFFERENCE_DEMONTREE"
INSUFFICIENT, RUNNING = "INSUFFISANT", "EN_COURS"


# --- Règles pures ------------------------------------------------------------------------------------------

def qualifying_moments(mints: list[dict]) -> list[dict]:
    """Instants où le cumul sur 1 h des créations comptées d'un stablecoin atteint le seuil. Chaque instant porte les
    clés des créations de sa fenêtre et leur total."""
    by_coin: dict[str, list[dict]] = {}
    for mint in sorted(mints, key=lambda m: (m["emitted_at"], m["key"])):
        if mint.get("counted", True):
            by_coin.setdefault(mint["stablecoin"], []).append(mint)
    out = []
    for coin, items in by_coin.items():
        for i, mint in enumerate(items):
            at = pd.Timestamp(mint["emitted_at"])
            window = [x for x in items[:i + 1] if pd.Timestamp(x["emitted_at"]) > at - WINDOW]
            total = sum(float(x["amount_usd"]) for x in window)
            if total >= THRESHOLD_USD:
                out.append({"stablecoin": coin, "at": utc_iso(at), "amount_usd": round(total, 2),
                            "mints": [x["key"] for x in window]})
    return sorted(out, key=lambda q: (q["at"], q["stablecoin"]))


def events_from(mints: list[dict]) -> list[dict]:
    """Un instant qualifiant ouvre un événement s'il est à 24 h ou plus du dernier instant qualifiant (tous
    stablecoins confondus) ; sinon il prolonge l'événement en cours."""
    events: list[dict] = []
    last: pd.Timestamp | None = None
    for moment in qualifying_moments(mints):
        at = pd.Timestamp(moment["at"])
        if last is None or at - last >= SEPARATION:
            raw = f"{TEST_ID}:{moment['stablecoin']}:{moment['mints'][0]}:{moment['at']}"
            events.append({"event_id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], **moment,
                           "moments": 1, "last_at": moment["at"]})
        else:
            events[-1]["moments"] += 1
            events[-1]["last_at"] = moment["at"]
        last = at
    return events


def placebo_days(event_id: str) -> list[int]:
    """20 jours distincts tirés dans les 30 jours précédents, graine déduite de l'événement (tirage reproductible)."""
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{event_id}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(1, LOOKBACK_DAYS + 1), PLACEBOS))


def net_return(entry_open: float, exit_open: float, symbol: str, scenario: str) -> float:
    """Achat au marché à l'ouverture d'entrée, vente au marché à l'ouverture de sortie, modèle de frais commun."""
    costs = costs_for(symbol, scenario)
    return exit_open * (1 - costs.market) * (1 - costs.fee) / (entry_open * (1 + costs.market) * (1 + costs.fee)) - 1


def verdict(scenarios: dict, *, ended: bool) -> dict:
    """Seuil de décision (pré-inscrit), par horizon : excès moyen sur les placebos, central ET défavorable."""
    if not ended:
        return {h: RUNNING for h in HORIZONS}
    out = {}
    for horizon in HORIZONS:
        central = scenarios.get(CENTRAL, {}).get(horizon, {})
        adverse = scenarios.get(ADVERSE, {}).get(horizon, {})
        if central.get("n", 0) < MIN_EVENTS or central.get("excess_ci") is None or adverse.get("excess_ci") is None:
            out[horizon] = INSUFFICIENT
        elif central["excess_ci"][0] > 0 and adverse["excess_ci"][0] > 0:
            out[horizon] = POSITIVE
        elif central["excess_ci"][1] < 0 and adverse["excess_ci"][1] < 0:
            out[horizon] = NEGATIVE
        else:
            out[horizon] = NO_DIFFERENCE
    return out


# --- Lecture des chaînes --------------------------------------------------------------------------------

def parse_tron(events: list[dict], *, detected_at: pd.Timestamp) -> list[dict]:
    out = []
    for item in events:
        try:
            amount = int(item["result"]["amount"]) / DECIMALS
            emitted = pd.Timestamp(int(item["block_timestamp"]), unit="ms", tz="UTC")
            key = f"tron:{item['transaction_id']}:{int(item.get('event_index', 0))}"
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"key": key, "chain": "tron", "stablecoin": "USDT", "amount_usd": round(amount, 2),
                    "emitted_at": utc_iso(emitted), "block": int(item.get("block_number", 0)), "minter": None,
                    "counted": True, "detected_at": utc_iso(detected_at)})
    return out


def parse_eth(logs: list[dict], *, stablecoin: str, timestamps: dict[int, pd.Timestamp],
              detected_at: pd.Timestamp) -> list[dict]:
    out = []
    for log in logs:
        try:
            block = int(log["blockNumber"], 16)
            amount = int(log["data"], 16) / DECIMALS
            key = f"eth:{log['transactionHash']}:{int(log['logIndex'], 16)}"
            minter = ("0x" + log["topics"][1][-40:]).lower() if stablecoin == "USDC" else None
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        counted = minter not in EXCLUDED_MINTERS
        out.append({"key": key, "chain": "ethereum", "stablecoin": stablecoin, "amount_usd": round(amount, 2),
                    "emitted_at": utc_iso(timestamps[block]), "block": block, "minter": minter, "counted": counted,
                    "reason": None if counted else "pont CCTP : USDC déjà brûlés ailleurs",
                    "detected_at": utc_iso(detected_at)})
    return out


def read_mints(client: PublicSources, *, now: pd.Timestamp) -> tuple[list[dict], dict[str, str]]:
    """Créations récentes de chaque source ; une source en échec est rapportée, les autres sont lues."""
    mints: list[dict] = []
    errors: dict[str, str] = {}
    try:
        since = int((now - LOOKBACK).timestamp() * 1000)
        mints += parse_tron(tron_events(client, contract=TRON_USDT, event_name="Issue", since_ms=since), detected_at=now)
    except (SourceError, PermissionError) as exc:
        errors["tron"] = str(exc)[:200]
    try:
        latest = eth_block_number(client)
        first = latest - ETH_BLOCKS
        logs = {"USDT": eth_logs(client, address=ETH_USDT, topic=TOPIC_ISSUE, from_block=first, to_block=latest),
                "USDC": eth_logs(client, address=ETH_USDC, topic=TOPIC_MINT, from_block=first, to_block=latest)}
        blocks = {int(log["blockNumber"], 16) for per in logs.values() for log in per
                  if int(log.get("data", "0x0"), 16) / DECIMALS >= MIN_MINT_USD}
        timestamps = block_times(client, blocks, first=first, latest=latest)
        for coin, per in logs.items():
            kept = [log for log in per if int(log["blockNumber"], 16) in timestamps]
            mints += parse_eth(kept, stablecoin=coin, timestamps=timestamps, detected_at=now)
    except (SourceError, PermissionError, KeyError, ValueError) as exc:
        errors["ethereum"] = f"{type(exc).__name__}: {exc}"[:200]
    return [m for m in mints if m["amount_usd"] >= MIN_MINT_USD], errors


def block_times(client: PublicSources, blocks: set[int], *, first: int, latest: int) -> dict[int, pd.Timestamp]:
    """Heure de chaque bloc demandé : exacte pour les deux bornes de la plage (deux appels), interpolée entre les
    deux pour les autres (créneaux de 12 s ; un créneau manqué décale de 12 s au plus, bien moins que la latence
    de détection). Évite une requête par bloc sur le nœud public."""
    if not blocks:
        return {}
    t_first, t_latest = eth_block_timestamp(client, first), eth_block_timestamp(client, latest)
    span = max(latest - first, 1)
    return {b: t_first + (t_latest - t_first) * ((min(max(b, first), latest) - first) / span) for b in sorted(blocks)}


# --- Journal ---------------------------------------------------------------------------------------------

def _first_by(entries, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"][field], entry["data"])
    return out


def _log_errors(journal: Journal, errors: dict[str, str], *, now: pd.Timestamp) -> int:
    recent: dict[str, dict] = {}
    for entry in journal.entries({SOURCE_ERROR}):
        recent[entry["data"]["source"]] = entry
    written = 0
    for source, error in errors.items():
        previous = recent.get(source)
        if previous and previous["data"]["error"] == error and now - pd.Timestamp(previous["at"]) < ERROR_REPEAT:
            continue
        journal.append(SOURCE_ERROR, {"source": source, "error": error}, now=now)
        written += 1
    return written


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime,
         client: PublicSources | None = None) -> dict:
    """Un passage : créations nouvelles inscrites, événements formés, décisions prises (entrée = détection + 5 min)."""
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    known = set(_first_by(journal.entries({MINT}), "key"))
    source = client or PublicSources()
    try:
        fresh, errors = read_mints(source, now=moment)
    finally:
        if client is None:
            source.close()
    counts = {"mints": 0, "events": 0, "decisions": 0, "errors": _log_errors(journal, errors, now=moment)}
    for mint in sorted(fresh, key=lambda m: (m["emitted_at"], m["key"])):
        if mint["key"] in known or pd.Timestamp(mint["emitted_at"]) < started:
            continue
        latency = round((moment - pd.Timestamp(mint["emitted_at"])).total_seconds() / 60, 2)
        journal.append(MINT, mint | {"latency_min": latency}, now=moment)
        known.add(mint["key"])
        counts["mints"] += 1
    mints = list(_first_by(journal.entries({MINT}), "key").values())
    recorded = _first_by(journal.entries({EVENT}), "event_id")
    recorded_times = [pd.Timestamp(e["at"]) for e in recorded.values()]
    for event in events_from(mints):
        at = pd.Timestamp(event["at"])
        if event["event_id"] in recorded or any(abs(at - t) < SEPARATION for t in recorded_times):
            continue                               # déjà inscrit (ou prolongé par une création lue tardivement)
        latency = round((moment - at).total_seconds() / 60, 2)
        data = {k: event[k] for k in ("event_id", "stablecoin", "at", "amount_usd", "mints", "moments")}
        data |= {"detected_at": utc_iso(moment), "latency_min": latency}
        if at < started or at >= final:
            data["status"] = OUT_OF_WINDOW
        elif SYMBOL not in set(start["halal"]["symbols"]):
            data["status"] = SKIPPED
        elif latency > MAX_LATENCY.total_seconds() / 60:
            data["status"] = LATE
        else:
            entry = moment + LATENCY
            days = placebo_days(event["event_id"])
            data |= {"status": DECIDED, "entry_at": utc_iso(entry), "placebo_days": days,
                     "placebo_entries": [utc_iso(entry - pd.Timedelta(days=d)) for d in days]}
            counts["decisions"] += 1
        journal.append(EVENT, data, now=moment)
        recorded_times.append(at)
        counts["events"] += 1
    return counts


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def minute_bar(rest: PublicHttpClient, when: pd.Timestamp) -> list | None:
    """Première bougie 1 min de BTCUSDT qui s'ouvre à `when` ou après : [ouverture ISO, open, high, low, close]."""
    rows = rest.get_json("/api/v3/klines", {"symbol": SYMBOL, "interval": "1m",
                                            "startTime": int(when.timestamp() * 1000), "limit": 1})
    if not rows:
        return None
    k = rows[0]
    return [utc_iso(pd.Timestamp(int(k[0]), unit="ms", tz="UTC")), float(k[1]), float(k[2]), float(k[3]), float(k[4])]


def _legs(entry: pd.Timestamp) -> dict[str, pd.Timestamp]:
    return {"entree": entry, **{h: entry + delta for h, delta in HORIZONS.items()}}


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest: PublicHttpClient | None = None) -> dict:
    """Une fois 72 h écoulées : prix d'entrée et de sortie de l'achat et de ses 20 placebos (bougies 1 min publiques),
    rendements nets dans les deux scénarios de frais, excès sur la moyenne des placebos."""
    done = set(_first_by(journal.entries({RESOLUTION}), "event_id"))
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    counts: dict[str, int] = {}
    for event in _first_by(journal.entries({EVENT}), "event_id").values():
        if event["status"] != DECIDED or event["event_id"] in done:
            continue
        entry = pd.Timestamp(event["entry_at"])
        if moment < entry + HORIZONS["72h"] + pd.Timedelta(minutes=2):
            continue
        late = moment >= entry + HORIZONS["72h"] + GAP_AFTER
        times = {"event": entry, **{f"placebo_{i}": pd.Timestamp(t) for i, t in enumerate(event["placebo_entries"])}}
        bars: dict[str, dict] = {}
        gap, unavailable = False, False
        for name, begin in times.items():
            bars[name] = {}
            for leg, when in _legs(begin).items():
                try:
                    bar = minute_bar(client, when)
                except HttpError:
                    bar = None
                    unavailable = True
                if bar is None or pd.Timestamp(bar[0]) - when > MAX_BAR_DELAY:
                    gap = True
                bars[name][leg] = bar
        if unavailable and not late:
            continue                                   # source indisponible : nouvel essai au prochain passage
        data: dict = {"event_id": event["event_id"], "stablecoin": event["stablecoin"], "entry_at": event["entry_at"]}
        if gap:
            data |= {"gap": True, "results": None, "bars": bars}
        else:
            results: dict = {}
            for scenario in SCENARIOS:
                results[scenario] = {}
                for horizon in HORIZONS:
                    event_r = net_return(bars["event"]["entree"][1], bars["event"][horizon][1], SYMBOL, scenario)
                    placebos = [net_return(bars[f"placebo_{i}"]["entree"][1], bars[f"placebo_{i}"][horizon][1], SYMBOL, scenario)
                                for i in range(len(event["placebo_entries"]))]
                    results[scenario][horizon] = {"event_r": round(event_r, 6), "placebo_mean": round(float(np.mean(placebos)), 6),
                                                  "placebos": [round(p, 6) for p in placebos],
                                                  "excess": round(event_r - float(np.mean(placebos)), 6)}
            data |= {"gap": False, "results": results, "bars": bars,
                     "bars_sha256": hashlib.sha256(canonical(bars).encode("utf-8")).hexdigest()}
        journal.append(RESOLUTION, data, now=moment)
        key = "TROU" if gap else "RESOLU"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _measure(rows: list[dict], scenario: str, horizon: str, level: float, *, samples: int, seed: int) -> dict:
    if not rows:
        return {"n": 0}
    excess = np.array([r["results"][scenario][horizon]["excess"] for r in rows], float)
    event_r = np.array([r["results"][scenario][horizon]["event_r"] for r in rows], float)
    placebo = np.array([r["results"][scenario][horizon]["placebo_mean"] for r in rows], float)
    times = pd.to_datetime([r["entry_at"] for r in rows], utc=True).to_numpy()
    ci, _ = day_block_ci(excess, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=level, min_blocks=8)
    return {"n": int(len(rows)), "event_r": round(float(event_r.mean()), 6), "placebo_r": round(float(placebo.mean()), 6),
            "excess": round(float(excess.mean()), 6), "win_share": round(float((excess > 0).mean()), 4), "excess_ci": ci}


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    mints = _first_by(journal.entries({MINT}), "key")
    events = _first_by(journal.entries({EVENT}), "event_id")
    resolutions = _first_by(journal.entries({RESOLUTION}), "event_id")
    decided = {k: e for k, e in events.items() if e["status"] == DECIDED}
    latencies = [e["latency_min"] for e in decided.values()]
    out: dict = {"mints": len(mints), "mints_ignored": sum(1 for m in mints.values() if not m.get("counted", True)),
                 "mints_usd": round(sum(m["amount_usd"] for m in mints.values() if m.get("counted", True)) / 1e6, 1),
                 "events": len(events), "decisions": len(decided),
                 "by_status": {s: sum(1 for e in events.values() if e["status"] == s) for s in (DECIDED, LATE, OUT_OF_WINDOW, SKIPPED)},
                 "by_stablecoin": {c: sum(1 for e in decided.values() if e["stablecoin"] == c) for c in ("USDT", "USDC")},
                 "pending": sum(1 for k in decided if k not in resolutions),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None),
                 "latency_median_min": round(float(np.median(latencies)), 1) if latencies else None,
                 "source_errors": sum(1 for _ in journal.entries({SOURCE_ERROR})), "scenarios": {}}
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
    """VERDICT inscrit une seule fois à la fin (toutes les décisions résolues), puis CLOTURE."""
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "verdicts": result["verdicts"],
                                 "central": result["scenarios"][CENTRAL]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Achat de BTC après une grosse émission de stablecoins",
    hypothesis=("Une création d'USDT ou d'USDC d'au moins 100 M$ (en une fois ou cumulée sur 1 h) représente de l'argent "
                "frais qui précède une hausse de BTC : l'achat simulé après détection fait mieux, net de frais, que des "
                "achats placebo aux mêmes heures dans les 30 jours précédents."),
    params={"events": "créations d'USDT (Tron, Ethereum) et d'USDC (Ethereum, hors ponts CCTP) lues toutes les 15 min",
            "threshold_usd": THRESHOLD_USD, "min_mint_usd": MIN_MINT_USD, "window_hours": 1, "separation_hours": 24,
            "latency_minutes": 5, "max_latency_minutes": 120, "symbol": SYMBOL, "bars": "1 min, premier prix après l'heure",
            "horizons": list(HORIZONS), "placebos": PLACEBOS, "lookback_days": LOOKBACK_DAYS,
            "max_bar_delay_minutes": 10, "min_events": MIN_EVENTS, "alpha": ALPHA, "comparisons": len(HORIZONS),
            "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS, "gap_after_days": GAP_AFTER.days,
            "contracts": {"tron_usdt": TRON_USDT, "eth_usdt": ETH_USDT, "eth_usdc": ETH_USDC},
            "topics": {"issue": TOPIC_ISSUE, "mint": TOPIC_MINT}, "excluded_minters": sorted(EXCLUDED_MINTERS)},
    rule_objects=(), config_keys=("data.rest_base_url",),
    frozen_modules=("crypto_signal_intelligence.forward.f3", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.forward.sources", "tron_events"),
                      ("crypto_signal_intelligence.forward.sources", "eth_logs"),
                      ("crypto_signal_intelligence.forward.sources", "eth_block_number"),
                      ("crypto_signal_intelligence.forward.sources", "eth_block_timestamp"),
                      ("crypto_signal_intelligence.forward.sources", "eth_rpc"),
                      ("crypto_signal_intelligence.backtest.metrics", "day_block_ci")),
)
