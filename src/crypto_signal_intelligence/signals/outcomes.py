"""Issue des signaux trouvés par les stratégies de CSI (dossier shadow) : rejoués sur les bougies arrivées APRÈS
leur création, avec les règles de remplissage du simulateur (`external.registry.replay`, testé équivalent).

Règles : ordre limite à l'entrée 1, à partir de la première bougie qui s'ouvre après la création du signal et sur
les seules bougies entièrement avant `entry_expires_at` ; stop au contact (ouverture sous le stop :
sortie à l'ouverture) ; objectif seulement s'il est dépassé ; stop d'abord si les deux sont touchés dans la même
bougie ; sinon sortie à la clôture après `simulation.max_hold_bars` bougies (24 h), comme le backtest qui a jugé
ces stratégies. Coûts du scénario central. Lecture seule des signaux : rien n'est publié ni modifié, aucun ordre.
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..data.schema import interval
from ..data.store import CandleStore
from ..external.registry import replay
from .outbox import SignalRegistry
from .txt import parse

GAP, PENDING = "TROU", "PENDING"
GAP_AFTER = pd.Timedelta(days=3)          # bougies toujours absentes ou trouées 3 jours après : trou constaté


def db_path(settings: Settings) -> Path:
    return settings.root / "signals" / "generated_outcomes.sqlite3"


@contextmanager
def connect(settings: Settings):
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS outcomes (
            signal_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy TEXT NOT NULL, created_at TEXT NOT NULL,
            outcome TEXT NOT NULL, r REAL, filled_at TEXT, resolved_at TEXT NOT NULL)""")
        yield db
        db.commit()


def resolve_signal(bars: pd.DataFrame, *, created_at: pd.Timestamp, entry_expires_at: pd.Timestamp, entry: float,
                   stop: float, target: float, step: pd.Timedelta, max_hold: int, costs, now: pd.Timestamp
                   ) -> tuple[str, float | None, datetime | None]:
    """Issue d'un signal sur les bougies clôturées (`bars`) qui s'ouvrent à sa création ou après."""
    start = created_at.ceil(step)
    window = max(1, math.floor((entry_expires_at - start) / step))     # bougies entièrement dans la validité
    after = bars[(bars["open_time"] >= start) & (bars["open_time"] + step <= now)].reset_index(drop=True)
    late = now >= start + (window + max_hold) * step + GAP_AFTER
    if after.empty or after["open_time"].iloc[0] != start:
        return (GAP, None, None) if late else (PENDING, None, None)
    span = after.iloc[:window + max_hold]
    if not (span["open_time"].diff().iloc[1:] == step).all():
        return (GAP, None, None) if late else (PENDING, None, None)
    return replay(after, entry=entry, stop=stop, target=target, entry_window=window, max_hold=max_hold, costs=costs)


def resolve(settings: Settings, *, now: datetime) -> dict:
    """Résout les signaux shadow pas encore résolus ; renvoie le compte par issue."""
    registry = SignalRegistry(settings.signals_db, settings.publication_dir(), root=settings.root)
    if not settings.signals_db.exists():
        return {}
    with connect(settings) as db:
        done = {r["signal_id"] for r in db.execute("SELECT signal_id FROM outcomes")}
    step = interval(settings.data.setup_timeframe)
    store = CandleStore(settings.data_dir)
    moment = pd.Timestamp(now)
    counts: dict[str, int] = {}
    rows = []
    for row in registry.rows():
        if row["signal_id"] in done:
            continue
        try:
            signal = parse(row["payload"])
        except Exception:  # noqa: BLE001 - enregistrement illisible : ignoré, jamais inventé
            continue
        created = pd.Timestamp(signal.created_at)
        try:
            bars = store.load_since(signal.symbol, settings.data.setup_timeframe, created.floor("D"))
        except FileNotFoundError:
            bars = pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])
        outcome, r, filled = resolve_signal(
            bars, created_at=created, entry_expires_at=pd.Timestamp(signal.entry_expires_at),
            entry=float(signal.entry_1), stop=float(signal.stop_loss), target=float(signal.targets[0]), step=step,
            max_hold=settings.simulation.max_hold_bars, costs=settings.costs["central"], now=moment)
        if outcome == PENDING:
            continue
        rows.append((signal.signal_id, signal.symbol, signal.strategy, created.isoformat(), outcome, r,
                     None if filled is None else pd.Timestamp(filled).isoformat(), moment.isoformat()))
        counts[outcome] = counts.get(outcome, 0) + 1
    with connect(settings) as db:
        db.executemany("INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?,?,?,?)", rows)
    return counts


def outcomes(settings: Settings) -> dict[str, dict]:
    if not db_path(settings).exists():
        return {}
    with connect(settings) as db:
        return {r["signal_id"]: dict(r) for r in db.execute("SELECT * FROM outcomes")}


def summary(settings: Settings) -> list[dict]:
    """Par stratégie : signaux résolus, remplis, R moyen des remplis (net, coûts centraux), part gagnante."""
    rows = pd.DataFrame(list(outcomes(settings).values()))
    if rows.empty:
        return []
    out = []
    for strategy, part in rows.groupby("strategy"):
        filled = part[part["r"].notna()]
        out.append({"strategy": strategy, "resolved": int(len(part)), "filled": int(len(filled)),
                    "unfilled": int((part["outcome"] == "UNFILLED").sum()),
                    "r_mean": round(float(filled["r"].mean()), 4) if len(filled) else None,
                    "win_share": round(float((filled["r"] > 0).mean()), 4) if len(filled) else None})
    return out
