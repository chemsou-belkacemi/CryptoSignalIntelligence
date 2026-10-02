"""Test en direct F1_MAKER_TAKER : ordre limite (maker) contre ordre au marché (taker) à l'entrée
(docs/FORWARD_TESTS.md, section F1_MAKER_TAKER ; règles figées au démarrage).

Événements : les plans indicatifs que le suivi en direct (outlook/tracking.py) enregistre chaque jour, sur les
paires de la liste halal figée au démarrage. Un plan n'existe qu'à son heure d'enregistrement : les deux entrées
commencent à la première bougie de 15 minutes qui s'ouvre à ce moment ou après. Chaque plan est inscrit au journal
(DECISION), puis rejoué SANS COÛT une fois son horizon écoulé, sur les bougies arrivées APRÈS (RESOLUTION). Les
coûts sont appliqués ensuite selon quatre lectures :

- « observe » (décision) : frais centraux, AUCUN écart supposé à l'entrée du taker. L'écart restant vient de ce qui
  est observé : décisions non remplies, pire cas dans la bougie du remplissage, prix d'entrée.
- « robuste » (décision, défavorable AU MAKER) : remplissage seulement après traversée de l'écart défavorable ;
  frais et écart de sortie centraux (des frais plus élevés avantageraient le bras qui trade le moins, le maker),
  toujours aucun écart supposé à l'entrée du taker.
- « modele_central » et « modele_defavorable » (information) : le modèle de frais commun, écart payé par le taker.

L'écart d'équilibre est le coût d'entrée du taker (en points de base) à partir duquel le maker devient meilleur
en moyenne, sur la lecture « observe ».
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci95
from ..config import Settings
from ..data.store import CandleStore
from ..outlook import pair as plan_module
from ..outlook import tracking
from .costs import ADVERSE, CENTRAL, MAJORS, costs_for
from .journal import Journal, canonical, utc_iso
from .maker import NOT_FILLED, PENDING, maker_path, net_return, taker_path
from .registry import ForwardTest, code_fingerprint

TEST_ID = "F1_MAKER_TAKER"
TIMEFRAME, STEP = "15m", pd.Timedelta(minutes=15)
HORIZONS = ("24h", "3j", "7j")
HORIZON_BARS = {"24h": 96, "3j": 288, "7j": 672}
PRIMARY = "24h"
VALID_BARS = 4                         # 4 bougies de 15 min : l'ordre limite vit une heure
MIN_DAYS, MIN_DECISIONS = 30, 500
SAMPLES, SEED = 10_000, 20261002
SENSITIVITY_BLOCK_DAYS = 7
GAP_AFTER = pd.Timedelta(days=2)       # bougies toujours absentes ou trouées 2 jours après l'horizon : trou constaté
MAX_ENTRY_COST = 0.05                  # borne de recherche de l'écart d'équilibre (500 points de base)
DECISION, SKIPPED, RESOLUTION = "DECISION", "HORS_SCREENING", "RESOLUTION"
FILLS = ("central", "robuste")
VIEWS: dict[str, dict[str, Any]] = {
    "observe": {"fill": "central", "costs": CENTRAL, "taker_entry_cost": False},
    "robuste": {"fill": "robuste", "costs": CENTRAL, "taker_entry_cost": False},
    "modele_central": {"fill": "central", "costs": CENTRAL, "taker_entry_cost": True},
    "modele_defavorable": {"fill": "robuste", "costs": ADVERSE, "taker_entry_cost": True},
}
DECIDING_VIEWS = ("observe", "robuste")
MAKER_BETTER, TAKER_BETTER, NO_DIFFERENCE = "MAKER_MEILLEUR", "TAKER_MEILLEUR_SANS_ECART", "PAS_DE_DIFFERENCE_DEMONTREE"
INSUFFICIENT, RUNNING = "INSUFFISANT", "EN_COURS"


def limit_price(previous: pd.DataFrame, begin: pd.Timestamp) -> float | None:
    """Règle du prix limite : la clôture de la bougie qui se termine à l'heure d'entrée (le dernier prix connu quand
    l'ordre part). None si cette bougie manque."""
    row = previous[previous["open_time"] == begin - STEP]
    return None if row.empty else float(row["close"].iloc[0])


def entry_time(recorded_at: str) -> pd.Timestamp:
    """Première ouverture de bougie à l'heure où le plan existe, ou après (un ordre ne part pas avant le plan)."""
    return pd.Timestamp(recorded_at).tz_convert("UTC").ceil(STEP)


def through_for(fill: str, symbol: str) -> float:
    """Traversée exigée sous le prix limite : nulle (strictement sous) en central ; en robuste, l'écart et le
    glissement du scénario défavorable (notre ordre est supposé en fin de file)."""
    return 0.0 if fill == "central" else costs_for(symbol, ADVERSE).market


def resolve_one(after: pd.DataFrame, decision: dict, limit: float) -> dict:
    """Chemins sans coût : taker, et maker pour chaque règle de remplissage."""
    common = {"stop_pct": decision["stop_pct"], "target_pct": decision["target_pct"],
              "horizon_bars": decision["bars"], "step": STEP}
    return {"taker": taker_path(after, **common),
            "maker": {fill: maker_path(after, limit=limit, valid_bars=VALID_BARS,
                                       through=through_for(fill, decision["symbol"]), **common) for fill in FILLS}}


def view_r(symbol: str, risk: float, paths: dict, view: str) -> tuple[float, float, bool] | None:
    """(R taker, R maker, rempli) d'une décision dans une lecture ; None si un chemin est un trou."""
    rule = VIEWS[view]
    costs = costs_for(symbol, rule["costs"])
    taker, maker = paths["taker"], paths["maker"][rule["fill"]]
    if taker["gross"] is None or (maker["gross"] is None and maker["outcome"] != NOT_FILLED):
        return None
    entry = costs.market if rule["taker_entry_cost"] else 0.0
    r_taker = net_return(taker["gross"], fee=costs.fee, entry_cost=entry, exit_cost=costs.market) / risk
    if maker["outcome"] == NOT_FILLED:
        return r_taker, 0.0, False
    return r_taker, net_return(maker["gross"], fee=costs.fee, entry_cost=0.0, exit_cost=costs.market) / risk, True


def break_even_bps(rows: list[tuple[str, float, dict]]) -> float | None:
    """Coût d'entrée du taker (pb) qui annule l'écart moyen, lecture « observe » : 0 si le maker est déjà meilleur
    sans écart ; None au-delà de 500 pb."""
    if not rows:
        return None
    gross = np.array([paths["taker"]["gross"] for _, _, paths in rows], float)
    risk = np.array([r for _, r, _ in rows], float)
    fee = np.array([costs_for(s, CENTRAL).fee for s, _, _ in rows], float)
    exit_cost = np.array([costs_for(s, CENTRAL).market for s, _, _ in rows], float)
    maker_mean = float(np.mean([view_r(s, r, p, "observe")[1] for s, r, p in rows]))   # type: ignore[index]

    def gap(cost: float) -> float:
        taker = (gross * (1 - exit_cost) * (1 - fee) / ((1 + cost) * (1 + fee)) - 1) / risk
        return maker_mean - float(taker.mean())

    if gap(0.0) >= 0:
        return 0.0
    if gap(MAX_ENTRY_COST) < 0:
        return None
    low, high = 0.0, MAX_ENTRY_COST
    for _ in range(60):
        middle = (low + high) / 2
        low, high = (middle, high) if gap(middle) < 0 else (low, middle)
    return round(high * 1e4, 2)


def verdict(primary: dict, *, ended: bool) -> str:
    """Seuil de décision (pré-inscrit), horizon principal, lectures « observe » et « robuste »."""
    if not ended:
        return RUNNING
    observed, robust = primary.get("observe", {}), primary.get("robuste", {})
    if observed.get("days", 0) < MIN_DAYS or observed.get("n", 0) < MIN_DECISIONS:
        return INSUFFICIENT
    ci_o, ci_r = observed.get("diff_ci"), robust.get("diff_ci")
    if ci_o is None or ci_r is None:
        return INSUFFICIENT
    if ci_o[0] > 0 and ci_r[0] > 0:
        return MAKER_BETTER
    if ci_o[1] < 0:
        return TAKER_BETTER
    return NO_DIFFERENCE


def plan_code() -> str:
    """Empreinte du code qui FABRIQUE les plans (non gelé, déclaré) : inscrite dans chaque DECISION."""
    return code_fingerprint((plan_module, tracking.record_day))


TEST = ForwardTest(
    test_id=TEST_ID, title="Ordre limite (maker) contre ordre au marché (taker) à l'entrée",
    hypothesis=("Entrer par un ordre limite à la clôture de décision, valable une heure, donne en moyenne un meilleur "
                "résultat net par décision que l'ordre au marché, une fois comptées les décisions non remplies, "
                "même sans compter d'écart à l'entrée du taker."),
    params={"events": "plans quotidiens du suivi en direct (outlook/tracking.py), liste halal figée",
            "entry": "première bougie qui s'ouvre à l'enregistrement du plan ou après",
            "horizons": list(HORIZONS), "horizon_bars": HORIZON_BARS, "primary": PRIMARY,
            "limit": "clôture de la bougie qui se termine à l'heure d'entrée", "valid_bars": VALID_BARS,
            "timeframe": TIMEFRAME,
            "fill": "plus bas strictement sous limite × (1 − traversée)",
            "through": {"central": 0.0, "robuste": "écart et glissement du scénario défavorable"},
            "views": VIEWS, "deciding_views": list(DECIDING_VIEWS), "min_days": MIN_DAYS,
            "min_decisions": MIN_DECISIONS, "samples": SAMPLES, "seed": SEED, "gap_after_days": GAP_AFTER.days,
            "ci": "IC95 par blocs de jours (longueur de l'horizon, au moins 10 blocs), moyenne de l'écart",
            "sensitivity_block_days": SENSITIVITY_BLOCK_DAYS},
    rule_objects=(),          # le code gelé est donné par modules et fonctions (registry.frozen_objects)
    config_keys=("data.setup_timeframe",),
    frozen_modules=("crypto_signal_intelligence.forward.f1", "crypto_signal_intelligence.forward.maker",
                    "crypto_signal_intelligence.forward.costs", "crypto_signal_intelligence.forward.registry",
                    "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci95"),
                      ("crypto_signal_intelligence.outlook.tracking", "replay_plan")),
)


def _raw(window: pd.DataFrame) -> list:
    return [[utc_iso(t), float(o), float(h), float(lo), float(c)] for t, o, h, lo, c in
            window[["open_time", "open", "high", "low", "close"]].itertuples(index=False)]


def _plan_digest(plan: dict) -> str:
    keys = ("id", "day", "symbol", "horizon", "bars", "recorded_at", "decision_time", "close", "stop_pct",
            "target_pct", "state")
    return hashlib.sha256(canonical({k: plan[k] for k in keys}).encode("utf-8")).hexdigest()


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Inscrit les plans apparus depuis le démarrage (une fois chacun) ; hors liste halal : comptés seulement."""
    known = {e["data"]["plan_id"] for e in journal.entries({DECISION, SKIPPED})}
    frozen = set(start["halal"]["symbols"])
    if not tracking.db_path(settings).exists():
        return {"decisions": 0, "skipped": 0}
    with tracking.connect(settings) as db:
        plans = [dict(r) for r in db.execute(
            "SELECT * FROM plans WHERE recorded_at >= ? ORDER BY id", (start["started_at"],))]
    counts = {"decisions": 0, "skipped": 0}
    final = pd.Timestamp(start["final_at"])
    code = plan_code()
    for plan in plans:
        if plan["id"] in known or plan["horizon"] not in HORIZONS:
            continue
        begin = entry_time(plan["recorded_at"])
        if begin >= final:
            continue
        base = {"plan_id": plan["id"], "symbol": plan["symbol"], "horizon": plan["horizon"]}
        if plan["symbol"] not in frozen:
            journal.append(SKIPPED, base | {"reason": "hors de la liste halal figée"}, now=now)
            counts["skipped"] += 1
            continue
        journal.append(DECISION, base | {
            "bars": plan["bars"], "decision_time": plan["decision_time"], "recorded_at": plan["recorded_at"],
            "entry_time": utc_iso(begin), "delay_minutes": round((begin - pd.Timestamp(plan["decision_time"]))
                                                                 .total_seconds() / 60, 2),
            "close": plan["close"], "stop_pct": plan["stop_pct"], "target_pct": plan["target_pct"],
            "state": plan["state"], "plan_sha256": _plan_digest(plan),
            "plan_code": code}, now=now)
        counts["decisions"] += 1
    return counts


def _first_by_plan(entries) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for entry in entries:
        out.setdefault(entry["data"]["plan_id"], entry["data"])
    return out


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    """Rejoue les décisions dont l'horizon est écoulé, sur les bougies stockées après l'entrée."""
    done = set(_first_by_plan(journal.entries({RESOLUTION})))
    moment = pd.Timestamp(now)
    due: dict[str, list[dict]] = {}
    for decision in _first_by_plan(journal.entries({DECISION})).values():
        if decision["plan_id"] in done:
            continue
        if pd.Timestamp(decision["entry_time"]) + (decision["bars"] + 1) * STEP <= moment:
            due.setdefault(decision["symbol"], []).append(decision)
    store = CandleStore(settings.data_dir)
    counts: dict[str, int] = {}
    for symbol, decisions in due.items():
        first = min(pd.Timestamp(d["entry_time"]) for d in decisions)
        try:
            candles = store.load_since(symbol, TIMEFRAME, (first - STEP).floor("D"))
        except FileNotFoundError:
            candles = pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])
        for decision in decisions:
            begin = pd.Timestamp(decision["entry_time"])
            late = moment >= begin + decision["bars"] * STEP + GAP_AFTER
            after = candles[candles["open_time"] >= begin].reset_index(drop=True)
            limit = limit_price(candles, begin)
            base = {"plan_id": decision["plan_id"], "symbol": symbol, "horizon": decision["horizon"]}
            if after.empty or after["open_time"].iloc[0] != begin or limit is None:
                if not late:
                    continue
                data = base | {"gap": "bougie d'entrée ou bougie du prix limite absente", "paths": None}
            else:
                window = after.iloc[:decision["bars"]]
                paths = resolve_one(after, decision, limit)
                outcomes = [paths["taker"]["outcome"], *(m["outcome"] for m in paths["maker"].values())]
                if any(o in {PENDING, tracking.GAP} for o in outcomes) and not late:
                    continue                          # bougies encore incomplètes : on attend qu'elles arrivent
                data = base | {"paths": paths, "limit": limit, "risk": -decision["stop_pct"] / 100,
                               "limit_bar": _raw(candles[candles["open_time"] == begin - STEP]), "n_bars": int(len(window)),
                               "bars_sha256": hashlib.sha256(canonical(_raw(window)).encode()).hexdigest(),
                               "first_bars": _raw(window.iloc[:VALID_BARS]), "last_bar": _raw(window.iloc[-1:])}
            journal.append(RESOLUTION, data, now=now)
            key = "TROU" if data.get("paths") is None else data["paths"]["maker"]["central"]["outcome"]
            counts[key] = counts.get(key, 0) + 1
    return counts


def _round(value) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value), 4)


def _measure(rows: list[tuple[str, float, dict, str]], view: str, block_days: int, *, samples: int, seed: int) -> dict:
    values = [(view_r(symbol, risk, paths, view), when) for symbol, risk, paths, when in rows]
    usable = [(v, when) for v, when in values if v is not None]
    gaps = len(values) - len(usable)
    if not usable:
        return {"n": 0, "gaps": gaps, "days": 0}
    taker = np.array([v[0] for v, _ in usable])
    maker = np.array([v[1] for v, _ in usable])
    filled = np.array([v[2] for v, _ in usable], bool)
    times = pd.to_datetime([when for _, when in usable], utc=True)
    diff = maker - taker
    ci, blocks = day_block_ci95(diff, times.to_numpy(), block_days=block_days, samples=samples, seed=seed,
                                min_blocks=10)
    ci_7d, _ = day_block_ci95(diff, times.to_numpy(), block_days=SENSITIVITY_BLOCK_DAYS, samples=samples, seed=seed,
                              min_blocks=10)
    return {"n": int(len(diff)), "gaps": gaps, "days": int(times.floor("D").nunique()),
            "fill_rate": _round(filled.mean()), "taker_r": _round(taker.mean()), "maker_r": _round(maker.mean()),
            "diff": _round(diff.mean()), "diff_ci": ci, "blocks": blocks, "diff_ci_7d_blocks": ci_7d,
            "maker_minus_taker_filled": _round(diff[filled].mean()) if filled.any() else None,
            "missed_taker_r": _round(taker[~filled].mean()) if (~filled).any() else None}


def stats(journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """Mesures par horizon et par lecture ; verdict sur l'horizon principal à la date d'évaluation, une fois toutes
    ses décisions résolues. Une décision ou une résolution en double (jamais attendu) ne compte qu'une fois."""
    decisions = _first_by_plan(journal.entries({DECISION}))
    resolutions = _first_by_plan(journal.entries({RESOLUTION}))
    out: dict = {"horizons": {}, "skipped": len(_first_by_plan(journal.entries({SKIPPED}))),
                 "pending": sum(1 for p in decisions if p not in resolutions),
                 "pending_primary": sum(1 for p, d in decisions.items() if p not in resolutions and d["horizon"] == PRIMARY),
                 "plan_codes": len({d.get("plan_code") for d in decisions.values()})}
    for horizon in HORIZONS:
        rows, holes = [], 0
        for plan_id, result in resolutions.items():
            decision = decisions.get(plan_id)
            if decision is None or decision["horizon"] != horizon:
                continue
            if result.get("paths") is None:
                holes += 1
                continue
            rows.append((decision["symbol"], result["risk"], result["paths"], decision["entry_time"]))
        block = max(1, round(HORIZON_BARS[horizon] * 15 / 1440))
        measures = {view: _measure(rows, view, block, samples=samples, seed=seed) | {"missing_entry": holes}
                    for view in VIEWS}
        usable = [(s, r, p) for s, r, p, _ in rows if view_r(s, r, p, "observe") is not None]
        measures["observe"]["break_even_bps"] = break_even_bps(usable)
        for group, members in (("BTC_ETH", True), ("autres", False)):
            part = [row for row in rows if (row[0] in MAJORS) == members]
            measures["observe"][f"diff_{group}"] = _measure(part, "observe", block, samples=200, seed=seed)["diff"] \
                if part else None
        out["horizons"][horizon] = measures
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and out["pending_primary"] == 0
    out["verdict"] = verdict(out["horizons"][PRIMARY], ended=ended)
    out["ended"] = bool(ended)
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    """À la date d'évaluation, une fois l'horizon principal résolu : inscrit le VERDICT (une seule fois). Une fois
    toutes les décisions résolues : inscrit la CLOTURE ; plus rien n'est contrôlé ni calculé ensuite."""
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if result["ended"] and journal.first(VERDICT) is None:
        primary = result["horizons"][PRIMARY]
        journal.append(VERDICT, {"verdict": result["verdict"], "observe": primary["observe"],
                                 "robuste": primary["robuste"]}, now=now)
        return VERDICT
    if journal.first(VERDICT) is not None and result["pending"] == 0:
        journal.append(CLOSURE, {"pending": 0}, now=now)
        return CLOSURE
    return None
