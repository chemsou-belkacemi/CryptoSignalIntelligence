"""Test en direct F4_TELEGRAM : les signaux Telegram reçus EN DIRECT, achetés au premier prix après réception, joués
avec la gestion « stop suiveur » du propriétaire, comparés à un achat au même moment et à 20 achats placebo
(docs/FORWARD_TESTS.md, section F4_TELEGRAM ; règles figées au démarrage). Phase 3 de la mission du 2026-10-02.

Événements : chaque message reçu par la boîte de BinanceSpotManager ou déposé par le robot du propriétaire
(`forward/telegram_live.py`) après le démarrage. Seuls les signaux d'ACHAT spot lisibles, sur une paire de la liste
halal figée, sont joués ; les autres sont comptés avec leur raison (illisible, short ou levier, hors screening).
Bougies de 1 minute publiques ; entrée au marché à l'ouverture de la première bougie qui s'ouvre 60 s ou plus après
la réception ; stop et objectifs du signal (5 au plus), stop à l'entrée après TP1 puis à TP(k−2) ; 30 jours au plus.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import CostScenario, Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.rest import fetch_klines
from ..data.schema import normalize
from ..data.store import CandleStore
from ..external import parser as parser_module
from ..external.parser import parse
from ..external.trailing import TRAIL, Management, simulate
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest, code_fingerprint
from .telegram_live import LiveSignal, read_all

TEST_ID = "F4_TELEGRAM"
TIMEFRAME, STEP = "1m", pd.Timedelta(minutes=1)
DELAY = pd.Timedelta(seconds=60)                 # entrée : première bougie ouverte 60 s ou plus après la réception
HOLD_BARS = 30 * 1440                            # 30 jours de bougies 1 min
TP_COUNT = 5
MANAGEMENT = Management(tp_count=TP_COUNT, first_share=None, stop_rule=TRAIL, lag=2)
PLACEBOS = 20
PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES = 1440, 30 * 1440      # dans les 30 jours précédents, au moins 1 jour avant
MAX_BAR_DELAY = pd.Timedelta(minutes=10)         # bougie d'entrée plus de 10 min après l'heure voulue : trou
MIN_RESOLVED, MIN_DAYS = 30, 10
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261005
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
MAX_PAGES = 60                                   # bougies 1 min téléchargées par paire et par passage (60 000)
DECISION, COUNTED, RESOLUTION = "DECISION", "COMPTE", "RESOLUTION"
UNREADABLE, SHORT, OUT_OF_SCREEN, OUT_OF_WINDOW = "ILLISIBLE", "SHORT_OU_LEVIER", "HORS_SCREENING", "HORS_FENETRE"
PLAYED, INVALID, GAP = "DEJA_JOUE", "INVALIDE", "TROU"
ABOVE, BELOW, NOT_SHOWN, INSUFFICIENT, RUNNING = ("SUPERIEUR_AU_HASARD", "INFERIEUR_AU_HASARD",
                                                  "NON_DEMONTRE", "INSUFFISANT", "EN_COURS")
ALL = "ensemble"
TIMEOUT_OUTCOMES = {"TIMEOUT"}


def cost_scenario(symbol: str, scenario: str) -> CostScenario:
    """Modèle de frais commun au format du moteur de rejeu (écart compté en glissement)."""
    costs = costs_for(symbol, scenario)
    return CostScenario(fee_bps=costs.fee * 1e4, slippage_bps=costs.market * 1e4, half_spread_bps=0.0)


def entry_time(received_at: str) -> pd.Timestamp:
    """Première ouverture de bougie 1 min à réception + 60 s ou après."""
    return (pd.Timestamp(received_at).tz_convert("UTC") + DELAY).ceil(STEP)


def classify(signal: LiveSignal, frozen: set[str]) -> tuple[str, dict]:
    """(statut, niveaux) d'un message : DECISION ou la raison du comptage."""
    parsed = parse(signal.text)
    if parsed.errors:
        reason = SHORT if any("short" in e.lower() or "levier" in e.lower() for e in parsed.errors) else UNREADABLE
        return reason, {"errors": parsed.errors[:3]}
    if parsed.stop is None or not parsed.targets or not parsed.entries:
        return UNREADABLE, {"errors": ["stop, objectif ou entrée absent"]}
    if parsed.symbol not in frozen:
        return OUT_OF_SCREEN, {"symbol": parsed.symbol}
    return DECISION, {"symbol": parsed.symbol, "entry_ref": float(parsed.entries[0]), "stop": float(parsed.stop),
                      "targets": [float(t) for t in parsed.targets[:TP_COUNT]], "stop_timeframe": parsed.stop_timeframe,
                      "n_targets": len(parsed.targets), "n_entries": len(parsed.entries)}


def placebo_offsets(signal_id: str) -> list[int]:
    """20 décalages distincts, en minutes, dans les 30 jours précédents (graine déduite du signal)."""
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{signal_id}".encode()).hexdigest()[:16], 16))
    return sorted(rng.sample(range(PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES + 1), PLACEBOS))


def parser_code() -> str:
    """Empreinte du parseur (non gelé, déclaré) : inscrite dans chaque DECISION."""
    return code_fingerprint((parser_module,))


# --- Rejeu ------------------------------------------------------------------------------------------------

def _arrays(bars: pd.DataFrame):
    return tuple(bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))


def run_trade(bars: pd.DataFrame, *, stop: float, targets: list[float], symbol: str, scenario: str,
              horizon: int | None = None) -> dict:
    """Achat au marché à l'ouverture de `bars[0]`, puis la gestion du propriétaire sur les bougies suivantes."""
    horizon = HOLD_BARS if horizon is None else horizon
    costs = cost_scenario(symbol, scenario)
    market = costs.slippage_bps / 1e4
    o, h, low, c = _arrays(bars)
    limit = float(o[0]) * (1 + market)                       # prix d'entrée effectif (écart et glissement payés)
    tgt = np.array([targets[:TP_COUNT]], dtype=float)
    out = simulate(o, h, low, c, starts=np.array([0]), limit=np.array([limit]), stop=np.array([float(stop)]),
                   targets=tgt, entry_window=1, horizon=horizon, costs=costs, weights=MANAGEMENT.weights(tgt.shape[1]),
                   management=MANAGEMENT)
    result = {"complete": bool(out["complete"][0]), "outcome": str(out["outcome"][0]), "hits": int(out["hits"][0]),
              "entry_price": limit, "risk": limit - float(stop)}
    result["r"] = round(float(out["r"][0]), 6) if result["complete"] else None
    return result


def exit_index(bars: pd.DataFrame, *, stop: float, targets: list[float], symbol: str, scenario: str,
               full: dict) -> int:
    """Indice de la bougie de sortie d'une transaction terminée : plus petit horizon qui reproduit son résultat
    (une sortie par le temps est à la dernière bougie de l'horizon)."""
    n = min(len(bars), HOLD_BARS)
    if full["outcome"] in TIMEOUT_OUTCOMES or full["outcome"].endswith("_TEMPS"):
        return n - 1
    low, high = 1, n
    while low < high:
        middle = (low + high) // 2
        probe = run_trade(bars.iloc[:middle], stop=stop, targets=targets, symbol=symbol, scenario=scenario, horizon=middle)
        if probe["complete"] and probe["outcome"] == full["outcome"] and probe["r"] is not None \
                and abs(probe["r"] - full["r"]) < 1e-9:
            high = middle
        else:
            low = middle + 1
    return low - 1


def same_moment_r(entry_price: float, exit_close: float, risk: float, symbol: str, scenario: str) -> float:
    """Achat au même instant et au même prix, vendu au marché à la clôture de la bougie de sortie, en R."""
    costs = costs_for(symbol, scenario)
    proceeds = exit_close * (1 - costs.market) * (1 - costs.fee)
    return round((proceeds - entry_price * (1 + costs.fee)) / risk, 6)


def ensure_minutes(settings: Settings, store: CandleStore, rest: PublicHttpClient, symbol: str, start: pd.Timestamp,
                   end: pd.Timestamp, *, now: datetime, max_pages: int = MAX_PAGES) -> pd.DataFrame:
    """Bougies 1 min CLÔTURÉES de `symbol` entre `start` et `end`, complétées depuis l'API publique (au plus
    `max_pages` pages par appel : le reste arrive au passage suivant) et conservées dans le magasin."""
    moment = pd.Timestamp(now)
    end = min(pd.Timestamp(end), moment)
    stored = store.load_since(symbol, TIMEFRAME, start)
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if stored.empty:
        ranges.append((start, end))
    else:
        first, last = pd.Timestamp(stored["open_time"].min()), pd.Timestamp(stored["open_time"].max())
        if first > start + STEP:
            ranges.append((start, first - STEP))
        if last + STEP < end:
            ranges.append((last + STEP, end))
    for lo, hi in ranges:
        raw = fetch_klines(rest, symbol, TIMEFRAME, int(lo.timestamp() * 1000), int(hi.timestamp() * 1000), max_pages=max_pages)
        if raw.empty:
            continue
        frame = normalize(raw, unit="ms", symbol=symbol, timeframe=TIMEFRAME, source="BINANCE_REST_KLINES", source_version="v3",
                          now=moment, latency_seconds=settings.data.assumed_availability_latency_seconds)
        frame = frame[frame["is_closed"]]
        if not frame.empty:
            store.upsert_tail(frame, symbol, TIMEFRAME)
    bars = store.load_since(symbol, TIMEFRAME, start)
    bars = bars[(bars["open_time"] >= start) & (bars["open_time"] <= end) & (bars["open_time"] + STEP <= moment)]
    return bars.sort_values("open_time").reset_index(drop=True)


def _window(bars: pd.DataFrame, begin: pd.Timestamp) -> pd.DataFrame | None:
    """Bougies à partir de la première qui s'ouvre à `begin` ou après ; None si elle manque ou arrive trop tard."""
    after = bars[bars["open_time"] >= begin]
    if after.empty or pd.Timestamp(after["open_time"].iloc[0]) - begin > MAX_BAR_DELAY:
        return None
    return after.reset_index(drop=True)


def _raw(bar: pd.Series) -> list:
    return [utc_iso(bar["open_time"]), float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])]


# --- Journal ---------------------------------------------------------------------------------------------

def _first_by(entries, field: str = "signal_id") -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"][field], entry["data"])
    return out


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Inscrit chaque message reçu depuis le démarrage, une fois : DECISION (achat joué) ou COMPTE (avec sa raison)."""
    known = set(_first_by(journal.entries({DECISION, COUNTED})))
    frozen = set(start["halal"]["symbols"])
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    counts = {"decisions": 0, "counted": 0}
    code = parser_code()
    for signal in read_all(settings, since=started - pd.Timedelta(days=1)):
        received = pd.Timestamp(signal.received_at)
        if signal.id in known or received < started:
            continue
        base = {"signal_id": signal.id, "provider": signal.provider, "source": signal.source, "chat": signal.chat,
                "received_at": signal.received_at, "edited": signal.edited,
                "text_sha256": hashlib.sha256(signal.text.encode("utf-8")).hexdigest()}
        entry = entry_time(signal.received_at)
        status, levels = classify(signal, frozen)
        if status == DECISION and entry >= final:
            status, levels = OUT_OF_WINDOW, {}
        if status != DECISION:
            journal.append(COUNTED, base | {"status": status} | levels, now=now)
            counts["counted"] += 1
            continue
        journal.append(DECISION, base | levels | {"entry_at": utc_iso(entry), "placebo_minutes": placebo_offsets(signal.id),
                                                  "parser_code": code}, now=now)
        counts["decisions"] += 1
    return counts


def resolve(settings: Settings, journal: Journal, *, now: datetime, rest: PublicHttpClient | None = None,
            store: CandleStore | None = None) -> dict:
    """Rejoue les décisions sur les bougies 1 min arrivées après l'entrée ; les placebos sur les 30 jours
    précédents ; résolution quand tout est terminé (ou trou constaté)."""
    done = set(_first_by(journal.entries({RESOLUTION})))
    moment = pd.Timestamp(now)
    client = rest or PublicHttpClient.rest(settings.data.rest_base_url)
    candles = store or CandleStore(settings.data_dir)
    counts: dict[str, int] = {}
    for decision in _first_by(journal.entries({DECISION})).values():
        if decision["signal_id"] in done:
            continue
        entry = pd.Timestamp(decision["entry_at"])
        if moment < entry + 2 * STEP:
            continue
        late = moment >= entry + HOLD_BARS * STEP + GAP_AFTER
        symbol = decision["symbol"]
        begin = entry - pd.Timedelta(minutes=PLACEBO_MAX_MINUTES)
        try:
            bars = ensure_minutes(settings, candles, client, symbol, begin, entry + (HOLD_BARS + 1) * STEP, now=moment)
        except (HttpError, ValueError):
            if not late:
                continue
            bars = candles.load_since(symbol, TIMEFRAME, begin)
        window = _window(bars, entry)
        base = {"signal_id": decision["signal_id"], "provider": decision["provider"], "symbol": symbol,
                "entry_at": decision["entry_at"]}
        if window is None:
            if not late:
                continue
            journal.append(RESOLUTION, base | {"status": GAP, "results": None}, now=moment)
            counts[GAP] = counts.get(GAP, 0) + 1
            continue
        p0 = float(window["open"].iloc[0])
        stop, targets = float(decision["stop"]), [float(t) for t in decision["targets"]]
        if p0 <= stop or p0 >= targets[0]:
            status = INVALID if p0 <= stop else PLAYED
            journal.append(RESOLUTION, base | {"status": status, "results": None, "entry_open": p0,
                                               "entry_bar": _raw(window.iloc[0])}, now=moment)
            counts[status] = counts.get(status, 0) + 1
            continue
        trades = {s: run_trade(window, stop=stop, targets=targets, symbol=symbol, scenario=s) for s in SCENARIOS}
        if not all(t["complete"] for t in trades.values()):
            if not late:
                continue
            journal.append(RESOLUTION, base | {"status": GAP, "results": None, "entry_open": p0}, now=moment)
            counts[GAP] = counts.get(GAP, 0) + 1
            continue
        placebo_windows = []
        for offset in decision["placebo_minutes"]:
            pw = _window(bars, entry - pd.Timedelta(minutes=int(offset)))
            placebo_windows.append(pw)
        results: dict = {}
        pending = False
        for scenario, trade in trades.items():
            k = exit_index(window, stop=stop, targets=targets, symbol=symbol, scenario=scenario, full=trade)
            exit_bar = window.iloc[k]
            same = same_moment_r(trade["entry_price"], float(exit_bar["close"]), trade["risk"], symbol, scenario)
            placebos: list[float | None] = []
            for pw in placebo_windows:
                if pw is None:
                    placebos.append(None)
                    continue
                q0 = float(pw["open"].iloc[0])
                p_trade = run_trade(pw, stop=q0 * stop / p0, targets=[q0 * t / p0 for t in targets], symbol=symbol,
                                    scenario=scenario)
                placebos.append(p_trade["r"] if p_trade["complete"] else None)
                pending = pending or not p_trade["complete"]
            usable = [p for p in placebos if p is not None]
            results[scenario] = {"outcome": trade["outcome"], "hits": trade["hits"], "r": trade["r"],
                                 "exit_at": utc_iso(exit_bar["open_time"]), "hold_minutes": int(k),
                                 "same_moment_r": same, "same_moment_diff": round(trade["r"] - same, 6),
                                 "placebos": placebos, "placebo_mean": round(float(np.mean(usable)), 6) if usable else None,
                                 "excess": round(trade["r"] - float(np.mean(usable)), 6) if usable else None}
        if pending and not late:
            continue
        used = window.iloc[:max(r["hold_minutes"] for r in results.values()) + 1]
        journal.append(RESOLUTION, base | {
            "status": "RESOLU", "entry_open": p0, "entry_bar": _raw(window.iloc[0]), "results": results,
            "n_bars": int(len(used)),
            "bars_sha256": hashlib.sha256(canonical([_raw(row) for _, row in used.iterrows()]).encode()).hexdigest()},
            now=moment)
        counts["RESOLU"] = counts.get("RESOLU", 0) + 1
    return counts


# --- Mesures ---------------------------------------------------------------------------------------------

def _streak(values: np.ndarray) -> int:
    worst = current = 0
    for v in values:
        current = current + 1 if v < 0 else 0
        worst = max(worst, current)
    return int(worst)


def _measure(rows: list[dict], scenario: str, level: float, *, samples: int, seed: int) -> dict:
    if not rows:
        return {"n": 0, "days": 0}
    rows = sorted(rows, key=lambda r: r["entry_at"])
    r = np.array([x["results"][scenario]["r"] for x in rows], float)
    times = pd.to_datetime([x["entry_at"] for x in rows], utc=True)
    same = np.array([x["results"][scenario]["same_moment_diff"] for x in rows], float)
    excess = np.array([x["results"][scenario]["excess"] if x["results"][scenario]["excess"] is not None else np.nan for x in rows], float)
    ok = np.isfinite(excess)
    ci_r, _ = day_block_ci(r, times.to_numpy(), block_days=BLOCK_DAYS, samples=samples, seed=seed, level=0.95, min_blocks=8)
    ci_excess, _ = (day_block_ci(excess[ok], times.to_numpy()[ok], block_days=BLOCK_DAYS, samples=samples, seed=seed,
                                 level=level, min_blocks=8) if ok.any() else (None, 0))
    return {"n": int(len(r)), "days": int(times.floor("D").nunique()), "r_mean": round(float(r.mean()), 4), "r_ci95": ci_r,
            "win_share": round(float((r > 0).mean()), 4), "worst_streak": _streak(r),
            "hold_hours_mean": round(float(np.mean([x["results"][scenario]["hold_minutes"] for x in rows]) / 60), 1),
            "same_moment_diff": round(float(same.mean()), 4), "placebo_excess": round(float(excess[ok].mean()), 4) if ok.any() else None,
            "placebo_excess_ci": ci_excess, "placebo_beaten": round(float((excess[ok] > 0).mean()), 4) if ok.any() else None}


def verdict(scenarios: dict, *, ended: bool) -> str:
    """Seuil de décision (pré-inscrit) d'un fournisseur : R moyen ET excès sur les placebos, central et défavorable."""
    if not ended:
        return RUNNING
    central, adverse = scenarios.get(CENTRAL, {}), scenarios.get(ADVERSE, {})
    if central.get("n", 0) < MIN_RESOLVED or central.get("days", 0) < MIN_DAYS or central.get("r_ci95") is None \
            or adverse.get("r_ci95") is None or central.get("placebo_excess_ci") is None or adverse.get("placebo_excess_ci") is None:
        return INSUFFICIENT
    if central["r_ci95"][0] > 0 and adverse["r_ci95"][0] > 0 and central["placebo_excess_ci"][0] > 0 and adverse["placebo_excess_ci"][0] > 0:
        return ABOVE
    if central["r_ci95"][1] < 0 and adverse["r_ci95"][1] < 0:
        return BELOW
    return NOT_SHOWN


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    decisions = _first_by(journal.entries({DECISION}))
    counted = _first_by(journal.entries({COUNTED}))
    resolutions = _first_by(journal.entries({RESOLUTION}))
    resolved = {k: r for k, r in resolutions.items() if r.get("results") is not None and k in decisions}
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in resolutions for k in decisions)
    level = 1 - ALPHA / 2
    providers = sorted({d["provider"] for d in decisions.values()} | {c["provider"] for c in counted.values()})
    out: dict = {"signals": len(decisions) + len(counted), "decisions": len(decisions), "counted": len(counted),
                 "by_status": {}, "pending": sum(1 for k in decisions if k not in resolutions),
                 "unplayable": {s: sum(1 for r in resolutions.values() if r.get("status") == s) for s in (GAP, INVALID, PLAYED)},
                 "edited": sum(1 for d in list(decisions.values()) + list(counted.values()) if d.get("edited")),
                 "parser_codes": len({d.get("parser_code") for d in decisions.values()}), "providers": {}, "ended": bool(ended)}
    for status in (UNREADABLE, SHORT, OUT_OF_SCREEN, OUT_OF_WINDOW):
        out["by_status"][status] = sum(1 for c in counted.values() if c["status"] == status)
    for name in [ALL, *providers]:
        mine = [r for r in resolved.values() if name == ALL or r["provider"] == name]
        total = sum(1 for d in list(decisions.values()) + list(counted.values()) if name == ALL or d["provider"] == name)
        played = sum(1 for d in decisions.values() if name == ALL or d["provider"] == name)
        block: dict = {"signals": total, "decisions": played, "halal_share": round(played / total, 4) if total else None,
                 "resolved": len(mine), "scenarios": {s: _measure(mine, s, level, samples=samples, seed=seed) for s in SCENARIOS}}
        block["verdict"] = verdict(block["scenarios"], ended=ended)
        out["providers"][name] = block
    out["verdict"] = ", ".join(f"{k}: {v['verdict']}" for k, v in out["providers"].items() if k != ALL) or \
        f"{ALL}: {out['providers'][ALL]['verdict']}"
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
        journal.append(VERDICT, {"verdict": result["verdict"],
                                 "providers": {k: {"verdict": v["verdict"], "resolved": v["resolved"], "central": v["scenarios"][CENTRAL]}
                                               for k, v in result["providers"].items()}}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Signaux Telegram reçus en direct : achat au premier prix, gestion du propriétaire, contre placebos",
    hypothesis=("Un signal d'achat spot d'un fournisseur Telegram, acheté au premier prix après sa réception et géré avec le "
                "stop suiveur du propriétaire, rapporte en moyenne plus (en R net) qu'un achat au même moment gardé aussi "
                "longtemps et que 20 achats de même géométrie à des moments tirés au hasard dans les 30 jours précédents."),
    params={"events": "messages reçus en direct (boîte de BinanceSpotManager, dépôt du robot) après le démarrage, liste halal figée",
            "entry": "marché, ouverture de la première bougie 1 min à réception + 60 s ou après", "timeframe": TIMEFRAME,
            "management": "stop suiveur : 5 objectifs au plus, parts décroissantes, stop à l'entrée après TP1 puis TP(k−2)",
            "hold_bars": HOLD_BARS, "placebos": PLACEBOS, "placebo_minutes": [PLACEBO_MIN_MINUTES, PLACEBO_MAX_MINUTES],
            "placebo_levels": "stop et objectifs au même rapport au prix d'entrée que le signal", "max_bar_delay_minutes": 10,
            "min_resolved": MIN_RESOLVED, "min_days": MIN_DAYS, "alpha": ALPHA, "comparisons": 2, "samples": SAMPLES,
            "seed": SEED, "block_days": BLOCK_DAYS, "gap_after_days": GAP_AFTER.days, "max_pages": MAX_PAGES,
            "parser": "non gelé : empreinte inscrite dans chaque décision"},
    rule_objects=(), config_keys=("data.rest_base_url", "data.assumed_availability_latency_seconds"),
    frozen_modules=("crypto_signal_intelligence.forward.f4", "crypto_signal_intelligence.forward.telegram_live",
                    "crypto_signal_intelligence.forward.costs", "crypto_signal_intelligence.forward.registry",
                    "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.external.trailing", "simulate"),
                      ("crypto_signal_intelligence.external.trailing", "trail_stop"),
                      ("crypto_signal_intelligence.external.trailing", "early_weights"),
                      ("crypto_signal_intelligence.external.trailing", "Management"),
                      ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.data.rest", "fetch_klines"),
                      ("crypto_signal_intelligence.data.schema", "normalize"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)
