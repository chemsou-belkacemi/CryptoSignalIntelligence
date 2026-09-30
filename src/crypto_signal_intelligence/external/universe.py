"""Univers étendu par le propriétaire : un signal soumis à la main vaut validation de la paire.

Règle du propriétaire (2026-09-30) : quand IL soumet un signal lui-même (page Avis CSI ou signal collé
sur la page Signaux de BinanceSpotManager, commande `evaluate-signal`, `scripts/evaluer-signal.ps1`),
c'est qu'il a jugé la paire acceptable (halal) : CSI l'ajoute définitivement à l'univers. Les signaux
reçus automatiquement (relève Telegram du worker de BSM) n'ajoutent jamais rien : personne ne les a
validés. Ce projet ne certifie la conformité d'aucun actif : la décision est celle du propriétaire,
tracée avec sa date et son motif.

Cycle d'une paire ajoutée : REQUESTED (pas de prix lu sur Binance Spot, historique à télécharger)
→ READY (bougies 15m et 1h présentes : évaluable) ; FAILED après MAX_ATTEMPTS échecs de
téléchargement, relancée à la prochaine soumission manuelle. Le téléchargement est fait par la
surveillance (`run`), entre deux cycles, une paire à la fois (mémoire), jamais par l'API pendant
une requête : l'avis rendu entre-temps est « EN_ATTENTE » et n'est pas enregistré.

Stockage : table `user_pairs` de signals/external.sqlite3 (même fichier que le registre des
signaux externes : un seul état à sauvegarder).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..config import Settings

REQUESTED, READY, FAILED = "REQUESTED", "READY", "FAILED"
MAX_ATTEMPTS = 3


class UserUniverse:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS user_pairs (
                symbol TEXT PRIMARY KEY, tick_size TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                requested_at TEXT NOT NULL, ready_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, symbol: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM user_pairs WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None

    def all(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM user_pairs ORDER BY requested_at, symbol")]

    def ready_symbols(self) -> list[str]:
        with self.connect() as db:
            return [row["symbol"] for row in db.execute(
                "SELECT symbol FROM user_pairs WHERE status=? ORDER BY symbol", (READY,))]

    def pending(self) -> list[dict]:
        """Paires dont l'historique reste à télécharger, plus ancienne demande d'abord."""
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM user_pairs WHERE status=? ORDER BY requested_at, symbol", (REQUESTED,))]

    def request(self, symbol: str, tick_size: Decimal, *, reason: str, now: datetime) -> dict:
        """Ajoute la paire, ou relance une paire FAILED ; une paire READY ou déjà demandée est inchangée."""
        with self.connect() as db:
            db.execute("""INSERT INTO user_pairs (symbol, tick_size, status, reason, requested_at, attempts)
                          VALUES (?, ?, ?, ?, ?, 0)
                          ON CONFLICT(symbol) DO UPDATE
                          SET status=excluded.status, attempts=0, last_error=NULL, requested_at=excluded.requested_at
                          WHERE user_pairs.status=?""",
                       (symbol, str(tick_size), REQUESTED, reason[:200], now.isoformat(), FAILED))
        entry = self.get(symbol)
        assert entry is not None
        return entry

    def mark_ready(self, symbol: str, *, now: datetime) -> None:
        with self.connect() as db:
            db.execute("UPDATE user_pairs SET status=?, ready_at=?, last_error=NULL WHERE symbol=?",
                       (READY, now.isoformat(), symbol))

    def mark_failed(self, symbol: str, error: str, *, now: datetime) -> dict:
        """Compte l'échec ; FAILED (plus de tentative automatique) à partir de MAX_ATTEMPTS."""
        with self.connect() as db:
            db.execute("""UPDATE user_pairs
                          SET attempts=attempts+1, last_error=?,
                              status=CASE WHEN attempts+1 >= ? THEN ? ELSE ? END
                          WHERE symbol=?""", (f"{now:%Y-%m-%d %H:%M} {error}"[:500], MAX_ATTEMPTS, FAILED, REQUESTED, symbol))
        entry = self.get(symbol)
        assert entry is not None
        return entry

    def forget(self, symbol: str) -> bool:
        """Retire la paire de l'univers du propriétaire (les bougies déjà stockées restent sur disque)."""
        with self.connect() as db:
            return db.execute("DELETE FROM user_pairs WHERE symbol=?", (symbol,)).rowcount == 1


def universe_symbols(settings: Settings) -> list[str]:
    """Paires évaluables : configuration + paires ajoutées par le propriétaire et prêtes."""
    return list(dict.fromkeys([*settings.data.symbols, *UserUniverse(settings.external_db).ready_symbols()]))


def tick_size_for(settings: Settings, symbol: str) -> Decimal:
    """Pas de prix : configuration, sinon celui lu sur Binance à l'ajout de la paire."""
    configured = settings.data.tick_size.get(symbol)
    if configured is not None:
        return configured
    entry = UserUniverse(settings.external_db).get(symbol)
    if entry is None:
        raise ValueError(f"tick_size inconnu pour {symbol} : paire ni configurée ni ajoutée par le propriétaire")
    return Decimal(entry["tick_size"])


def download_pending(settings: Settings, *, now: datetime, downloader=None, limit: int = 1) -> dict:
    """Télécharge l'historique (15m et 1h) des paires demandées, `limit` paire(s) par appel.

    Appelé par la surveillance entre deux cycles. Une paire est READY seulement si des bougies sont
    réellement stockées pour les deux unités de temps ; sinon l'échec est compté (FAILED après
    MAX_ATTEMPTS, relancée par une nouvelle soumission manuelle).
    """
    from ..data.pipeline import download as default_download
    from ..data.store import CandleStore
    downloader = downloader or default_download
    universe = UserUniverse(settings.external_db)
    store = CandleStore(settings.data_dir)
    result: dict = {"processed": [], "ready": [], "failed": []}
    for entry in universe.pending()[:limit]:
        symbol = entry["symbol"]
        result["processed"].append(symbol)
        try:
            for timeframe in (settings.data.setup_timeframe, settings.data.context_timeframe):
                downloader(settings, symbol, timeframe, now=now)
                if store.last_open_time(symbol, timeframe) is None:
                    raise RuntimeError(f"aucune bougie {timeframe} stockée après téléchargement")
            universe.mark_ready(symbol, now=now)
            result["ready"].append(symbol)
        except Exception as exc:  # noqa: BLE001 - l'échec est compté, la surveillance continue
            failed = universe.mark_failed(symbol, f"{type(exc).__name__}: {exc}", now=now)
            result["failed"].append({"symbol": symbol, "status": failed["status"], "attempts": failed["attempts"],
                                     "error": failed["last_error"]})
    return result
