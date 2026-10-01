"""Registre des signaux externes évalués : traçabilité, résolution par les données, bilan par source.

Chaque évaluation est conservée avec ses prix absolus et sa bougie de décision. Plus tard,
`resolve_pending` rejoue chaque signal sur les bougies 15m clôturées suivantes, avec les
règles de remplissage du simulateur : ordre LIMIT valable `entry_window_bars`, puis TP1 ou
stop en premier (stop si les deux dans la même bougie), sinon TIMEOUT après `max_hold_bars`.
Le résultat en R rapporte le PnL net au risque prévu (entrée − stop du signal). Un prix qui
touche l'entrée n'est pas la preuve d'un remplissage réel : c'est une simulation.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..config import CostScenario, Settings
from ..data.schema import interval
from ..data.sqlite_schema import add_missing_columns
from ..data.store import CandleStore

OUTCOMES = ("PENDING", "TP1_FIRST", "SL_FIRST", "TIMEOUT", "UNFILLED", "INVALID", "DUPLICATE")
DUPLICATE_WINDOW_DAYS = 7
RESOLVED_WITH_R = ("TP1_FIRST", "SL_FIRST", "TIMEOUT")


def new_external_id(now: datetime) -> str:
    return f"EXT-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6].upper()}"


def replay(bars: pd.DataFrame, *, entry: float, stop: float, target: float, entry_window: int, max_hold: int,
           costs: CostScenario) -> tuple[str, float | None, datetime | None]:
    """(issue, R net, heure de remplissage) sur des bougies clôturées postérieures à la décision."""
    if bars.empty:
        return "PENDING", None, None
    opens, highs, lows, closes = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = [t.to_pydatetime() for t in bars["open_time"]]
    fee = costs.fee_bps / 1e4
    market_cost = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    fill: tuple[int, float, bool] | None = None
    for k in range(min(entry_window, len(bars))):
        if opens[k] <= entry:
            fill = (k, min(opens[k] * (1 + market_cost), entry), False)
            break
        if lows[k] < entry:  # un simple contact ne garantit rien : pénétration stricte requise
            fill = (k, entry, True)
            break
    if fill is None:
        return ("UNFILLED" if len(bars) >= entry_window else "PENDING"), None, None
    start, price, touched = fill
    risk = entry - stop
    outcome: str | None = None
    exit_price: float | None = None
    if not touched and opens[start] <= stop:  # ouverture sous le stop : stop-market aussitôt, au marché
        outcome, exit_price = "SL_FIRST", opens[start] * (1 - market_cost)
    last = min(start + max_hold, len(bars))
    for k in range(start, last if outcome is None else start):
        first = k == start
        if not first and opens[k] <= stop:
            outcome, exit_price = "SL_FIRST", opens[k] * (1 - market_cost)  # gap : jamais « au prix du stop »
            break
        if not first and opens[k] > target:
            outcome, exit_price = "TP1_FIRST", target
            break
        if lows[k] <= stop:
            outcome, exit_price = "SL_FIRST", stop * (1 - market_cost)
            break
        if highs[k] > target and not (first and touched):  # le plus haut a pu précéder l'entrée
            outcome, exit_price = "TP1_FIRST", target
            break
    if outcome is None:
        if len(bars) < start + max_hold:
            return "PENDING", None, times[start]
        outcome, exit_price = "TIMEOUT", closes[last - 1] * (1 - market_cost)
    assert exit_price is not None
    r = (exit_price * (1 - fee) - price * (1 + fee)) / risk
    return outcome, round(float(r), 4), times[start]


class ExternalSignalRegistry:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS external_signals (
                id TEXT PRIMARY KEY, received_at TEXT NOT NULL, source TEXT NOT NULL, content_hash TEXT NOT NULL,
                template TEXT NOT NULL, symbol TEXT NOT NULL, entry REAL, stop REAL, tp1 REAL, targets TEXT,
                decision_time TEXT, close REAL, verdict TEXT NOT NULL, p_tp1 REAL, base_expectancy_r REAL,
                evaluation TEXT NOT NULL, outcome TEXT NOT NULL, outcome_r REAL, filled_at TEXT, resolved_at TEXT,
                raw_text TEXT NOT NULL)""")
            # migration 2026-09-30, sûre en concurrence (tableau de bord : plusieurs requêtes à la fois)
            add_missing_columns(db, "external_signals", {"duplicate_of": "TEXT", "copy_of": "TEXT"})
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, *, received_at: datetime, source: str, content_hash: str, template: str, symbol: str,
               entry: float | None, stop: float | None, tp1: float | None, targets: list[float],
               decision_time: datetime | None, close: float | None, verdict: str, p_tp1: float | None,
               base_expectancy_r: float | None, evaluation: dict, raw_text: str, resolvable: bool) -> str:
        """Enregistre une évaluation. Un même texte (même empreinte) déjà reçu de la MÊME source dans la
        fenêtre de doublon est gardé pour l'historique mais jamais compté (outcome DUPLICATE) ; reçu d'une
        AUTRE source, il est compté pour elle et marqué comme copie de la première réception."""
        signal_id = new_external_id(received_at)
        since = (received_at - timedelta(days=DUPLICATE_WINDOW_DAYS)).isoformat()
        with self.connect() as db:
            first = db.execute("""SELECT id, source FROM external_signals WHERE content_hash=? AND received_at>=?
                                  AND received_at<=? AND outcome NOT IN ('DUPLICATE', 'INVALID')
                                  ORDER BY received_at LIMIT 1""",
                               (content_hash, since, received_at.isoformat())).fetchone()
            duplicate_of = first["id"] if first is not None and first["source"] == source else None
            copy_of = first["id"] if first is not None and first["source"] != source else None
            outcome = "DUPLICATE" if duplicate_of and resolvable else ("PENDING" if resolvable else "INVALID")
            db.execute("""INSERT INTO external_signals (id, received_at, source, content_hash, template, symbol, entry,
                          stop, tp1, targets, decision_time, close, verdict, p_tp1, base_expectancy_r, evaluation,
                          outcome, outcome_r, filled_at, resolved_at, raw_text, duplicate_of, copy_of)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                signal_id, received_at.isoformat(), source, content_hash, template, symbol, entry, stop, tp1,
                json.dumps(targets), decision_time.isoformat() if decision_time else None, close, verdict, p_tp1,
                base_expectancy_r, json.dumps(evaluation, ensure_ascii=False, default=str), outcome, None, None, None,
                raw_text, duplicate_of, copy_of))
        return signal_id

    def previous(self, content_hash: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id, received_at, verdict, outcome FROM external_signals WHERE content_hash=? "
                             "ORDER BY received_at LIMIT 1", (content_hash,)).fetchone()
        return dict(row) if row else None

    def pending(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM external_signals WHERE outcome='PENDING' ORDER BY received_at").fetchall()
        return [dict(row) for row in rows]

    def mark(self, signal_id: str, outcome: str, outcome_r: float | None, filled_at: datetime | None,
             resolved_at: datetime) -> None:
        with self.connect() as db:
            db.execute("UPDATE external_signals SET outcome=?, outcome_r=?, filled_at=?, resolved_at=? WHERE id=?",
                       (outcome, outcome_r, filled_at.isoformat() if filled_at else None, resolved_at.isoformat(),
                        signal_id))

    def recent(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT id, received_at, source, symbol, entry, stop, tp1, verdict, p_tp1,
                                        base_expectancy_r, outcome, outcome_r
                                 FROM external_signals ORDER BY received_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def source_stats(self, source: str | None = None) -> list[dict]:
        """Bilan par source ; les doublons (même texte, même source) ne sont jamais comptés."""
        resolved = "outcome IN ('TP1_FIRST','SL_FIRST','TIMEOUT')"
        query = f"""SELECT source, SUM(outcome!='DUPLICATE') AS evaluated, SUM(outcome='DUPLICATE') AS duplicates,
                           SUM(copy_of IS NOT NULL) AS copies,
                           SUM(verdict='FAVORABLE' AND outcome!='DUPLICATE') AS favorable,
                           SUM(verdict='INDETERMINE' AND outcome!='DUPLICATE') AS indetermine,
                           SUM(verdict='DEFAVORABLE' AND outcome!='DUPLICATE') AS defavorable,
                           SUM(verdict='REFUSE') AS refuse,
                           SUM({resolved}) AS resolved, SUM(outcome='TP1_FIRST') AS tp1_first,
                           SUM(outcome='SL_FIRST') AS sl_first, SUM(outcome='TIMEOUT') AS timeout,
                           SUM(outcome='UNFILLED') AS unfilled, SUM(outcome='PENDING') AS pending,
                           AVG(CASE WHEN {resolved} THEN outcome_r END) AS realized_r,
                           AVG(CASE WHEN {resolved} THEN p_tp1 END) AS base_tp1_rate,
                           AVG(CASE WHEN {resolved} THEN base_expectancy_r END) AS base_expectancy_r
                    FROM external_signals {"WHERE source=?" if source else ""} GROUP BY source ORDER BY evaluated DESC"""
        with self.connect() as db:
            rows = db.execute(query, (source,) if source else ()).fetchall()
        out = []
        for row in rows:
            stats = dict(row)
            stats["realized_tp1_rate"] = round(stats["tp1_first"] / stats["resolved"], 4) if stats["resolved"] else None
            out.append(stats)
        return out


def resolve_pending(settings: Settings, registry: ExternalSignalRegistry, *, now: datetime) -> dict[str, int]:
    """Résout les signaux en attente avec les bougies stockées ; renvoie les issues comptées."""
    store = CandleStore(settings.data_dir)
    cfg, costs = settings.external, settings.costs["central"]
    counts: Counter[str] = Counter()
    candles: dict[str, pd.DataFrame] = {}
    step = interval(settings.data.setup_timeframe)
    pending = registry.pending()
    oldest: dict[str, pd.Timestamp] = {}
    for row in pending:
        received = pd.Timestamp(row["received_at"])
        oldest[row["symbol"]] = min(oldest.get(row["symbol"], received), received)
    for row in pending:
        symbol = row["symbol"]
        if symbol not in candles:            # seulement les bougies utiles, pas 5 ans d'historique
            candles[symbol] = store.load_since(symbol, settings.data.setup_timeframe, oldest[symbol].floor("D"))
        frame = candles[symbol]
        # Suivi à partir de la première bougie qui s'ouvre APRÈS la réception : la bougie en cours au moment
        # de l'avis a commencé avant (jusqu'à 15 min) ; ses plus hauts et plus bas n'étaient pas atteignables.
        start = pd.Timestamp(row["received_at"]).ceil(f"{int(step.total_seconds() // 60)}min")
        bars = frame[frame["open_time"] >= start] if not frame.empty else frame
        outcome, r, filled_at = replay(bars, entry=row["entry"], stop=row["stop"], target=row["tp1"],
                                       entry_window=cfg.entry_window_bars, max_hold=cfg.max_hold_bars, costs=costs)
        if outcome != "PENDING":
            registry.mark(row["id"], outcome, r, filled_at, now)
        counts[outcome] += 1
    return dict(counts)
