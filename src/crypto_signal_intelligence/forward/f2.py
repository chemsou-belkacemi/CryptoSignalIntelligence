"""Test en direct F2_ECHELLES : les signaux shadow des stratégies de CSI, joués avec 1 objectif (la règle testée)
ou avec une échelle de 2 à 7 objectifs et la gestion « stop suiveur » du propriétaire (docs/FORWARD_TESTS.md,
section F2_ECHELLES ; règles figées au démarrage).

Événements : chaque signal du registre shadow (signals/registry.sqlite3) créé après le démarrage, sur une paire de
la liste halal figée. Les MÊMES entrées pour toutes les variantes : seule la sortie change.
- `origine` : l'objectif unique du signal, stop fixe, sortie à 24 h (la règle avec laquelle la stratégie a été jugée).
- `echelle_k` (k = 2 à 7) : objectifs à 1, 2, …, k fois le risque au-dessus de l'entrée ; parts décroissantes
  (k, k−1, …, 1) ; stop à l'entrée après TP1, puis à TP(k−2) après TPk ; sortie au plus tard à 7 jours.
Rejeu sans aucune information postérieure à la décision dans la décision ; résolution sur les bougies arrivées après.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import CostScenario, Settings
from ..data.store import CandleStore
from ..external.trailing import FIXED, TRAIL, Management, simulate
from ..signals.outbox import SignalRegistry
from ..signals.txt import parse
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, canonical, utc_iso
from .registry import ForwardTest

TEST_ID = "F2_ECHELLES"
TIMEFRAME, STEP = "15m", pd.Timedelta(minutes=15)
ORIGIN = "origine"
LADDERS = tuple(range(2, 8))
VARIANTS = (ORIGIN, *(f"echelle_{k}" for k in LADDERS))
ORIGIN_HOLD, LADDER_HOLD = 96, 672           # 24 h (règle testée) ; 7 jours pour les échelles
MIN_FILLED = 100
ALPHA = 0.05
SAMPLES, SEED = 10_000, 20261003
BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)
DECISION, SKIPPED, RESOLUTION = "DECISION", "HORS_SCREENING", "RESOLUTION"
BETTER, WORSE, NO_DIFFERENCE = "MEILLEURE", "MOINS_BONNE", "PAS_DE_DIFFERENCE_DEMONTREE"
INSUFFICIENT, RUNNING = "INSUFFISANT", "EN_COURS"


def ladder_targets(entry: float, stop: float, count: int) -> list[float]:
    """Objectifs à 1, 2, …, count fois le risque au-dessus de l'entrée."""
    risk = entry - stop
    return [entry + i * risk for i in range(1, count + 1)]


def management_for(variant: str) -> tuple[Management, int]:
    if variant == ORIGIN:
        return Management(tp_count=1, first_share=None, stop_rule=FIXED, lag=0), ORIGIN_HOLD
    return Management(tp_count=int(variant.split("_")[1]), first_share=None, stop_rule=TRAIL, lag=2), LADDER_HOLD


def cost_scenario(symbol: str, scenario: str) -> CostScenario:
    """Modèle de frais commun des tests en direct, au format du moteur de rejeu (écart compté en glissement)."""
    costs = costs_for(symbol, scenario)
    return CostScenario(fee_bps=costs.fee * 1e4, slippage_bps=costs.market * 1e4, half_spread_bps=0.0)


def entry_time(created_at: str) -> pd.Timestamp:
    """Première bougie qui s'ouvre à la création du signal ou après."""
    return pd.Timestamp(created_at).tz_convert("UTC").ceil(STEP)


def window_bars(decision: dict) -> int:
    """Bougies ENTIÈREMENT dans la validité de l'ordre (de la première bougie après la création jusqu'à
    `ENTRY_EXPIRES_AT`) : une bougie qui s'ouvre quelques secondes avant l'expiration ne compte pas."""
    return max(1, math.floor((pd.Timestamp(decision["entry_expires_at"]) - entry_time(decision["created_at"])) / STEP))


def play(bars: pd.DataFrame, decision: dict, variant: str, scenario: str) -> dict:
    """Une variante sur les bougies qui suivent l'entrée (`bars[0]` = bougie d'entrée)."""
    management, hold = management_for(variant)
    entry, stop = decision["entry"], decision["stop"]
    targets = [decision["target"]] if variant == ORIGIN else ladder_targets(entry, stop, management.tp_count)
    o, h, low, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    out = simulate(o, h, low, c, starts=np.array([0]), limit=np.array([entry]), stop=np.array([stop]),
                   targets=np.array([targets], dtype=float), entry_window=window_bars(decision), horizon=hold,
                   costs=cost_scenario(decision["symbol"], scenario), weights=management.weights(len(targets)),
                   management=management)
    if out["complete"][0]:
        return {"outcome": str(out["outcome"][0]), "r": round(float(out["r"][0]), 4), "hits": int(out["hits"][0])}
    if out["unfilled"][0]:
        return {"outcome": "NON_REMPLI", "r": None, "hits": 0}
    return {"outcome": "PENDING", "r": None, "hits": int(out["hits"][0])}


def verdict(rows: dict, *, ended: bool) -> dict:
    """Seuil de décision (pré-inscrit) : chaque échelle face à l'origine, écart apparié, central ET défavorable."""
    if not ended:
        return {v: RUNNING for v in VARIANTS if v != ORIGIN}
    out = {}
    for variant in VARIANTS:
        if variant == ORIGIN:
            continue
        central, adverse = rows.get(CENTRAL, {}).get(variant, {}), rows.get(ADVERSE, {}).get(variant, {})
        if central.get("filled", 0) < MIN_FILLED or central.get("diff_ci") is None or adverse.get("diff_ci") is None:
            out[variant] = INSUFFICIENT
        elif central["diff_ci"][0] > 0 and adverse["diff_ci"][0] > 0:
            out[variant] = BETTER
        elif central["diff_ci"][1] < 0 and adverse["diff_ci"][1] < 0:
            out[variant] = WORSE
        else:
            out[variant] = NO_DIFFERENCE
    return out


TEST = ForwardTest(
    test_id=TEST_ID, title="Signaux de CSI : 1 objectif contre échelles de 2 à 7 objectifs",
    hypothesis=("Sur les mêmes entrées (signaux shadow des stratégies de CSI), une échelle de k objectifs (k = 2 à 7) "
                "avec la gestion « stop suiveur » donne un R net moyen différent de la règle testée à un objectif."),
    params={"events": "signaux du registre shadow de CSI créés après le démarrage, liste halal figée",
            "entry": "limite à l'entrée 1, valable jusqu'à l'expiration de l'entrée du signal",
            "variants": list(VARIANTS), "ladder_targets": "1, 2, …, k fois le risque au-dessus de l'entrée",
            "ladder_weights": "décroissantes k, k−1, …, 1", "ladder_stop": "entrée après TP1, puis TP(k−2) après TPk",
            "origin_hold_bars": ORIGIN_HOLD, "ladder_hold_bars": LADDER_HOLD, "timeframe": TIMEFRAME,
            "min_filled": MIN_FILLED, "alpha": ALPHA, "comparisons": len(LADDERS), "samples": SAMPLES, "seed": SEED,
            "block_days": BLOCK_DAYS, "gap_after_days": GAP_AFTER.days},
    rule_objects=(), config_keys=("data.setup_timeframe", "strategies"),
    frozen_modules=("crypto_signal_intelligence.forward.f2", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.external.trailing", "simulate"),
                      ("crypto_signal_intelligence.external.trailing", "trail_stop"),
                      ("crypto_signal_intelligence.external.trailing", "early_weights"),
                      ("crypto_signal_intelligence.external.trailing", "Management"),
                      ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                      ("crypto_signal_intelligence.signals.txt", "parse"),
                      ("crypto_signal_intelligence.signals.analyze", "build_signal"),
                      ("crypto_signal_intelligence.data.store", "CandleStore")),
)


def _raw(window: pd.DataFrame) -> list:
    return [[utc_iso(t), float(o), float(h), float(lo), float(c)] for t, o, h, lo, c in
            window[["open_time", "open", "high", "low", "close"]].itertuples(index=False)]


def _first_by_signal(entries) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"]["signal_id"], entry["data"])
    return out


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    known = set(_first_by_signal(journal.entries({DECISION, SKIPPED})))
    frozen = set(start["halal"]["symbols"])
    counts = {"decisions": 0, "skipped": 0}
    if not settings.signals_db.exists():
        return counts
    registry = SignalRegistry(settings.signals_db, settings.publication_dir(), root=settings.root)
    final = pd.Timestamp(start["final_at"])
    for row in registry.rows():
        if row["signal_id"] in known:
            continue
        try:
            signal = parse(row["payload"])
        except Exception:  # noqa: BLE001 - enregistrement illisible : jamais inventé
            continue
        created = pd.Timestamp(signal.created_at)
        if created < pd.Timestamp(start["started_at"]) or entry_time(created.isoformat()) >= final:
            continue
        base = {"signal_id": signal.signal_id, "symbol": signal.symbol, "strategy": signal.strategy}
        if signal.symbol not in frozen:
            journal.append(SKIPPED, base | {"reason": "hors de la liste halal figée"}, now=now)
            counts["skipped"] += 1
            continue
        journal.append(DECISION, base | {
            "created_at": utc_iso(created), "entry_expires_at": utc_iso(pd.Timestamp(signal.entry_expires_at)),
            "entry": float(signal.entry_1), "stop": float(signal.stop_loss), "target": float(signal.targets[0]),
            "payload_sha256": hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()}, now=now)
        counts["decisions"] += 1
    return counts


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    done = set(_first_by_signal(journal.entries({RESOLUTION})))
    moment = pd.Timestamp(now)
    store = CandleStore(settings.data_dir)
    counts: dict[str, int] = {}
    for decision in _first_by_signal(journal.entries({DECISION})).values():
        if decision["signal_id"] in done:
            continue
        begin = entry_time(decision["created_at"])
        horizon = window_bars(decision) + LADDER_HOLD
        late = moment >= begin + horizon * STEP + GAP_AFTER
        try:
            candles = store.load_since(decision["symbol"], TIMEFRAME, begin.floor("D"))
        except FileNotFoundError:
            candles = pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])
        after = candles[(candles["open_time"] >= begin) & (candles["open_time"] + STEP <= moment)].reset_index(drop=True)
        window = after.iloc[:horizon]
        contiguous = not after.empty and after["open_time"].iloc[0] == begin and \
            bool((window["open_time"].diff().iloc[1:] == STEP).all())
        if not contiguous:
            if not late:
                continue
            data = {"signal_id": decision["signal_id"], "symbol": decision["symbol"], "gap": True, "results": None}
        else:
            results = {s: {v: play(window, decision, v, s) for v in VARIANTS} for s in SCENARIOS}
            if any(r["outcome"] == "PENDING" for per in results.values() for r in per.values()):
                continue
            data = {"signal_id": decision["signal_id"], "symbol": decision["symbol"], "strategy": decision["strategy"],
                    "risk_pct": round((decision["entry"] - decision["stop"]) / decision["entry"] * 100, 4),
                    "results": results, "n_bars": int(len(window)),
                    "bars_sha256": hashlib.sha256(canonical(_raw(window)).encode()).hexdigest()}
        journal.append(RESOLUTION, data, now=now)
        key = "TROU" if data.get("results") is None else data["results"][CENTRAL][ORIGIN]["outcome"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def _measure(rows: pd.DataFrame, variant: str, level: float, *, samples: int, seed: int) -> dict:
    part = rows[rows[variant].notna()]
    if part.empty:
        return {"filled": 0}
    values = part[variant].to_numpy(float)
    times = pd.to_datetime(part["created_at"], utc=True).to_numpy()
    out: dict = {"filled": int(len(part)), "r_mean": round(float(values.mean()), 4),
           "win_share": round(float((values > 0).mean()), 4)}
    if variant != ORIGIN:
        paired = part[part[ORIGIN].notna()]
        diff = (paired[variant] - paired[ORIGIN]).to_numpy(float)
        ci, _ = day_block_ci(diff, pd.to_datetime(paired["created_at"], utc=True).to_numpy(), block_days=BLOCK_DAYS,
                             samples=samples, seed=seed, level=level, min_blocks=8)
        out |= {"diff_mean": round(float(diff.mean()), 4) if len(diff) else None, "diff_ci": ci}
    else:
        ci, _ = day_block_ci(values, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=0.95, min_blocks=8)
        out["ci95"] = ci
    return out


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    decisions = _first_by_signal(journal.entries({DECISION}))
    resolutions = _first_by_signal(journal.entries({RESOLUTION}))
    level = 1 - ALPHA / len(LADDERS)
    out: dict = {"decisions": len(decisions), "pending": sum(1 for s in decisions if s not in resolutions),
                 "skipped": len(_first_by_signal(journal.entries({SKIPPED}))),
                 "gaps": sum(1 for r in resolutions.values() if r.get("results") is None), "scenarios": {}}
    for scenario in SCENARIOS:
        table = pd.DataFrame([{"created_at": decisions[s]["created_at"],
                               **{v: r["results"][scenario][v]["r"] for v in VARIANTS}}
                              for s, r in resolutions.items() if r.get("results") is not None and s in decisions])
        out["scenarios"][scenario] = {v: (_measure(table, v, level, samples=samples, seed=seed) if len(table)
                                          else {"filled": 0}) for v in VARIANTS}
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
