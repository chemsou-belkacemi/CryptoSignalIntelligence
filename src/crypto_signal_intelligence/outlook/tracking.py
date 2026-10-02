"""Suivi EN DIRECT des plans indicatifs (docs/DELIVERY_STATUS.md, « Suivi en direct des plans ») : la seule façon honnête pour un plan de
devenir « favorable ».

Chaque jour (après 00:10 UTC), pour chaque paire de l'univers et chaque horizon suivi (1, 3 et 7 jours), le plan du
moment est ENREGISTRÉ avec ses niveaux relatifs (stop, objectif) et son état historique. Il est ensuite rejoué sur les
bougies qui arrivent APRÈS son enregistrement, avec exactement les règles du plan (`pair.plan_outcomes`) : entrée à
l'ouverture de la bougie suivante, stop au contact, objectif seulement s'il est dépassé, stop d'abord dans la même
bougie, sortie à l'horizon ; coûts du scénario central.

Ces résultats portent sur des données que PERSONNE n'a vues au moment du plan. Par horizon et par état au moment de
l'enregistrement : nombre de plans, R moyen, part gagnante, IC95 par blocs d'un horizon (1, 3 ou 7 jours :
au moins 10 blocs, soit 10, 30 ou 70 jours de suivi). Aucun ordre.

« Prouvé en direct » (règle corrigée le 2026-10-02 après relecture : l'ancienne relisait le R absolu chaque jour,
sans témoin, et aurait déclaré « prouvé » environ un groupe sans avantage sur cinq en 12 semaines) :
- TÉMOIN : chaque plan est comparé aux plans des AUTRES états du même jour et du même horizon (écart de R). La
  hausse ou la baisse générale du marché touche les deux et s'annule : seul l'apport de l'état compte.
- DATES FIXES : le contrôle n'a lieu qu'aux dates de contrôle, tous les LOOK_DAYS jours depuis le premier jour de
  suivi, sur les seuls plans terminés à cette date. Au k-ième contrôle, l'intervalle est au niveau
  1 − 0,05 / 2^k : la somme des risques de fausse preuve sur tous les contrôles reste sous 5 %.
- Au moins PROOF_PLANS plans terminés sur PROOF_DAYS jours avec un témoin. Une preuve acquise à un contrôle reste
  acquise (les plans d'avant cette date ne changent plus).
"""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci, day_block_ci95
from ..config import Settings
from ..data.schema import interval
from ..data.store import CandleStore
from .pair import HORIZONS

log = logging.getLogger("csi.tracking")
TRACKED = ("24h", "3j", "7j")
RECORD_AFTER = timedelta(minutes=10)                 # après 00:00 UTC : la bougie de 23:45 est stockée
PROOF_PLANS, PROOF_DAYS = 50, 20
LOOK_DAYS = 28                                       # contrôles de preuve tous les 28 jours, à dates fixes
PROOF_ALPHA = 0.05
PROOF_SAMPLES = 20_000
LIVE_PROVEN = "FAVORABLE_PROUVE_EN_DIRECT"
TP, SL, TIMEOUT, GAP = "TP", "SL", "TEMPS", "TROU"
_LOCK = threading.Lock()


def db_path(settings: Settings) -> Path:
    return settings.root / "signals" / "plans.sqlite3"


@contextmanager
def connect(settings: Settings):
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, symbol TEXT NOT NULL, horizon TEXT NOT NULL,
            bars INTEGER NOT NULL, recorded_at TEXT NOT NULL, decision_time TEXT NOT NULL, close REAL NOT NULL,
            stop_pct REAL NOT NULL, target_pct REAL NOT NULL, state TEXT NOT NULL, expectancy_r REAL,
            outcome TEXT, r REAL, resolved_at TEXT, UNIQUE(day, symbol, horizon))""")
        yield db
        db.commit()


def record_day(settings: Settings, *, now: datetime, symbols: Iterable[str] | None = None,
               horizons: Iterable[str] = TRACKED, outlook=None, loader=None) -> dict:
    """Enregistre les plans du jour (une seule fois par paire, horizon et jour UTC). Une paire sans données fraîches ou
    sans niveaux (trou, historique trop court) n'est pas enregistrée : rien n'est inventé."""
    from ..external.universe import universe_symbols
    from ..features.loader import load_inputs
    from .pair import STALE, pair_outlook
    outlook = outlook or pair_outlook
    loader = loader or load_inputs
    moment = pd.Timestamp(now)
    day = (moment - RECORD_AFTER).strftime("%Y-%m-%d")
    counts = {"recorded": 0, "skipped": 0, "already": 0}
    with connect(settings) as db:
        done = {(r["symbol"], r["horizon"]) for r in db.execute("SELECT symbol, horizon FROM plans WHERE day=?", (day,))}
    for symbol in symbols or universe_symbols(settings):
        wanted = [h for h in horizons if (symbol, h) not in done]
        counts["already"] += len(list(horizons)) - len(wanted)
        if not wanted:
            continue
        try:
            inputs = loader(settings, symbol)
        except Exception:  # noqa: BLE001 - paire sans données : passée, rien d'enregistré
            counts["skipped"] += len(wanted)
            continue
        rows = []
        for horizon in wanted:
            try:
                result = outlook(settings, symbol, horizon, now=now, inputs=inputs)
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1
                continue
            plan = result["plan"]
            if plan.get("state") == STALE or "stop_pct" not in plan:
                counts["skipped"] += 1
                continue
            rows.append((day, symbol, horizon, HORIZONS[horizon][0], _recorded_at(moment).isoformat(),
                         result["context"]["decision_time"], float(result["context"]["close"]), float(plan["stop_pct"]),
                         float(plan["target_pct"]), plan.get("state_history", plan["state"]), plan.get("expectancy_r")))
        with connect(settings) as db:
            db.executemany("""INSERT OR IGNORE INTO plans (day, symbol, horizon, bars, recorded_at, decision_time, close,
                              stop_pct, target_pct, state, expectancy_r) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
        counts["recorded"] += len(rows)
    return counts


def _recorded_at(moment: pd.Timestamp) -> pd.Timestamp:
    """Heure où le plan existe vraiment : la boucle du jour dure 25 à 35 minutes, l'heure de son début ne suffit pas
    (tests en direct : un ordre ne part pas avant le plan)."""
    return max(moment, pd.Timestamp(datetime.now(UTC)))


def replay_plan(bars: pd.DataFrame, *, stop_pct: float, target_pct: float, horizon_bars: int, step: pd.Timedelta,
                fee: float, market: float) -> tuple[str, float | None]:
    """Règles du plan (pair.plan_outcomes) sur les bougies qui suivent la décision : `bars[0]` est la bougie d'entrée
    (entrée à son ouverture). (issue, R) ; ("TROU", None) si les bougies ne sont pas contiguës."""
    if bars.empty:
        return "PENDING", None
    window = bars.iloc[:horizon_bars]
    if not (window["open_time"].diff().iloc[1:] == step).all():
        return GAP, None
    o, h, low, c = (window[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    entry = o[0]
    stop, target = entry * (1 + stop_pct / 100), entry * (1 + target_pct / 100)
    outcome, exit_price = None, None
    for j in range(len(window)):
        first = j == 0
        if not first and o[j] <= stop:
            outcome, exit_price = SL, o[j]
            break
        if not first and o[j] >= target:
            outcome, exit_price = TP, target
            break
        if low[j] <= stop:
            outcome, exit_price = SL, stop
            break
        if h[j] > target:
            outcome, exit_price = TP, target
            break
    if outcome is None:
        if len(window) < horizon_bars:
            return "PENDING", None                     # ni objectif ni stop, horizon pas encore écoulé
        outcome, exit_price = TIMEOUT, c[-1]
    assert exit_price is not None
    net = exit_price * (1 - market) * (1 - fee) / (entry * (1 + market) * (1 + fee)) - 1
    risk = (entry - stop) / entry
    return outcome, round(float(net / risk), 4)


def resolve(settings: Settings, *, now: datetime) -> dict:
    """Rejoue les plans en attente dont l'horizon est écoulé, sur les bougies stockées après leur décision."""
    costs = settings.costs["central"]
    fee, market = costs.fee_bps / 1e4, (costs.slippage_bps + costs.half_spread_bps) / 1e4
    step = interval(settings.data.setup_timeframe)
    store = CandleStore(settings.data_dir)
    counts: dict[str, int] = {}
    with connect(settings) as db:
        pending = [dict(r) for r in db.execute("SELECT * FROM plans WHERE outcome IS NULL ORDER BY decision_time")]
    by_symbol: dict[str, list[dict]] = {}
    for row in pending:
        if pd.Timestamp(row["decision_time"]) + (row["bars"] + 1) * step <= pd.Timestamp(now):
            by_symbol.setdefault(row["symbol"], []).append(row)
    updates = []
    for symbol, rows in by_symbol.items():
        first = min(pd.Timestamp(r["decision_time"]) for r in rows)
        candles = store.load_since(symbol, settings.data.setup_timeframe, first.floor("D"))
        for row in rows:
            after = candles[candles["open_time"] >= pd.Timestamp(row["decision_time"])].reset_index(drop=True)
            if after.empty or after["open_time"].iloc[0] != pd.Timestamp(row["decision_time"]):
                outcome, r = (GAP, None) if not after.empty else ("PENDING", None)
            else:
                outcome, r = replay_plan(after, stop_pct=row["stop_pct"], target_pct=row["target_pct"],
                                         horizon_bars=row["bars"], step=step, fee=fee, market=market)
            if outcome != "PENDING":
                updates.append((outcome, r, pd.Timestamp(now).isoformat(), row["id"]))
                counts[outcome] = counts.get(outcome, 0) + 1
    with connect(settings) as db:
        db.executemany("UPDATE plans SET outcome=?, r=?, resolved_at=? WHERE id=?", updates)
    return counts


def with_control(rows: pd.DataFrame) -> pd.DataFrame:
    """Ajoute `excess` : R du plan moins le R moyen des plans terminés des AUTRES états, même jour et même horizon
    (NaN s'il n'y en a pas : le plan n'entre pas dans la preuve)."""
    out = rows.copy()
    done = out["r"].notna()
    keys = ["day", "horizon"]
    total = out[done].groupby(keys)["r"].agg(["sum", "count"])
    by_state = out[done].groupby([*keys, "state"])["r"].agg(["sum", "count"])
    excess = np.full(len(out), np.nan)
    for i, (day, horizon, state, r) in enumerate(zip(out["day"], out["horizon"], out["state"], out["r"], strict=True)):
        if pd.isna(r) or (day, horizon) not in total.index:
            continue
        all_sum, all_count = total.loc[(day, horizon)]
        own_sum, own_count = by_state.loc[(day, horizon, state)]
        others = all_count - own_count
        if others > 0:
            excess[i] = r - (all_sum - own_sum) / others
    out["excess"] = excess
    return out


def look_dates(first_day: str, now: datetime) -> list[pd.Timestamp]:
    """Dates de contrôle passées : premier jour de suivi + k × LOOK_DAYS (k ≥ 1), à 00:00 UTC."""
    start = pd.Timestamp(first_day, tz="UTC")
    moment = pd.Timestamp(now)
    out, k = [], 1
    while start + pd.Timedelta(days=k * LOOK_DAYS) <= moment:
        out.append(start + pd.Timedelta(days=k * LOOK_DAYS))
        k += 1
    return out


def proof_at(part: pd.DataFrame, look: pd.Timestamp, k: int, *, span: int, bars: int, step: pd.Timedelta,
             seed: int) -> dict:
    """Contrôle k : plans terminés à la date `look` (décision + horizon ≤ look), écart au témoin."""
    end = pd.to_datetime(part["decision_time"], utc=True) + (bars + 1) * step
    usable = part[(end <= look) & part["excess"].notna()]
    level = 1 - PROOF_ALPHA / 2 ** k
    values = usable["excess"].to_numpy(float)
    days = int(usable["day"].nunique())
    ci = None
    if len(values) >= PROOF_PLANS and days >= PROOF_DAYS:
        times = pd.to_datetime(usable["decision_time"], utc=True).to_numpy()
        ci, _ = day_block_ci(values, times, block_days=span, samples=PROOF_SAMPLES, seed=seed, level=level, min_blocks=10)
    return {"look": look.date().isoformat(), "k": k, "level": round(level, 5), "plans": int(len(values)), "days": days,
            "excess_mean": round(float(values.mean()), 4) if len(values) else None, "ci": ci,
            "proven": bool(ci is not None and ci[0] > 0)}


_CACHE: dict = {}


def summary(settings: Settings, *, samples: int = 2000, seed: int = 20260929, now: datetime | None = None) -> dict:
    """Bilan en direct par horizon et par état au moment de l'enregistrement ; preuve aux dates de contrôle."""
    if not db_path(settings).exists():
        return {"groups": [], "recorded": 0}
    moment = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    key = (str(db_path(settings)), db_path(settings).stat().st_mtime_ns, moment.floor("h"), samples, seed)
    if now is None and _CACHE.get("key") == key:
        return _CACHE["value"]
    with connect(settings) as db:
        rows = pd.DataFrame([dict(r) for r in db.execute("SELECT * FROM plans")])
    if rows.empty:
        return {"groups": [], "recorded": 0}
    rows = with_control(rows)
    step = interval(settings.data.setup_timeframe)
    bar_minutes = int(step.total_seconds() // 60)
    looks = look_dates(str(rows["day"].min()), moment)
    groups = []
    for (horizon, state), part in rows.groupby(["horizon", "state"]):
        done = part[part["r"].notna()]
        values = done["r"].to_numpy(float)
        times = pd.to_datetime(done["decision_time"], utc=True).to_numpy()
        days = int(done["day"].nunique())
        span = max(1, math.ceil(HORIZONS[horizon][0] * bar_minutes / 1440))
        ci = None
        if len(values) >= 20:
            # Plans quotidiens : blocs d'un horizon (1, 3 ou 7 jours), la durée sur laquelle deux plans se chevauchent.
            ci, _ = day_block_ci95(values, times, block_days=span, samples=samples, seed=seed, min_blocks=10)
        excess = done["excess"].dropna().to_numpy(float)
        checks = [proof_at(part, look, k, span=span, bars=HORIZONS[horizon][0], step=step, seed=seed)
                  for k, look in enumerate(looks, start=1)]
        proven = any(c["proven"] for c in checks)
        next_look = pd.Timestamp(str(rows["day"].min()), tz="UTC") + pd.Timedelta(days=(len(looks) + 1) * LOOK_DAYS)
        groups.append({
            "horizon": horizon, "label": HORIZONS[horizon][1], "state": state, "recorded": int(len(part)),
            "resolved": int(len(values)), "pending": int(part["outcome"].isna().sum()),
            "gaps": int((part["outcome"] == GAP).sum()), "days": days,
            "r_mean": round(float(values.mean()), 4) if len(values) else None,
            "win_share": round(float((values > 0).mean()), 4) if len(values) else None, "ic95": ci,
            "excess_mean": round(float(excess.mean()), 4) if len(excess) else None, "checks": checks,
            "proven": proven, "next_look": next_look.date().isoformat(),
            "progress": (f"{len(excess)}/{PROOF_PLANS} plans avec témoin, {days}/{PROOF_DAYS} jours ; "
                         f"prochain contrôle le {next_look.date().isoformat()}")})
    groups.sort(key=lambda g: (list(HORIZONS).index(g["horizon"]), g["state"]))
    value = {"groups": groups, "recorded": int(len(rows)), "first_day": str(rows["day"].min()),
             "rule": (f"« prouvé en direct » : à une date de contrôle fixe (tous les {LOOK_DAYS} jours), au moins "
                      f"{PROOF_PLANS} plans terminés sur {PROOF_DAYS} jours, et l'écart de R face aux plans des autres "
                      f"états du même jour entièrement > 0 (niveau 1 − 0,05/2^k au k-ième contrôle)")}
    if now is None:
        _CACHE.update(key=key, value=value)
    return value


def live_status(settings: Settings, horizon: str, state: str) -> dict | None:
    """Bilan en direct des plans de même horizon et même état (pour l'affichage d'un plan)."""
    for group in summary(settings)["groups"]:
        if group["horizon"] == horizon and group["state"] == state:
            return group
    return None


def start_daily(settings: Settings, *, now: datetime) -> bool:
    """Lance `daily` dans un fil séparé s'il ne tourne pas déjà (l'enregistrement du jour prend ~25 minutes)."""
    if _LOCK.locked():
        return False
    threading.Thread(target=_daily_logged, args=(settings, now), name="csi-plans", daemon=True).start()
    return True


def _daily_logged(settings: Settings, now: datetime) -> None:
    try:
        result = daily(settings, now=now)
        if result and (result.get("recorded", {}).get("recorded") or result.get("resolved")):
            log.info("suivi des plans : %s", result)
    except Exception:  # noqa: BLE001 - jamais bloquant
        log.exception("suivi des plans")


def daily(settings: Settings, *, now: datetime) -> dict | None:
    """Appelé par la surveillance entre deux cycles : résout les plans écoulés, puis enregistre ceux du jour s'ils ne le
    sont pas encore. Un seul passage à la fois ; jamais bloquant pour la surveillance (exceptions tracées)."""
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        resolved = resolve(settings, now=now)
        moment = pd.Timestamp(now)
        if moment - moment.floor("D") < RECORD_AFTER:
            return {"resolved": resolved}
        return {"resolved": resolved, "recorded": record_day(settings, now=now)}
    finally:
        _LOCK.release()

