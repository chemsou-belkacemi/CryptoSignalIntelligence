"""Admission des paires selon l'avis de screening halal (règle du propriétaire, 2026-10-01).

- FAVORABLE : ajout direct de la paire USDT, si elle existe et se négocie (TRADING) sur Binance Spot.
- DEFAVORABLE : refus.
- DOUTEUX ou INEXPLOITABLE (crypto absente du screening comprise) : ni ajout ni refus, « à décider » par le
  propriétaire, signalé dans le tableau de bord avec deux boutons. Un signal reçu automatiquement pour une
  crypto à décider n'est pas transmis : son avis reste EN_ATTENTE (BinanceSpotManager ne l'exécute pas).

Ce projet ne certifie rien : les avis viennent de sources publiques relevées à une date
(config/halal_screening.toml, docs/UNIVERSE.md) ; chaque décision est tracée avec son auteur (« règle » ou
« propriétaire »), sa date et son motif, et une décision du propriétaire n'est jamais écrasée par la règle.
Stockage : table `pair_admissions` de signals/external.sqlite3 (avec l'univers ajouté, `user_pairs`).
"""
from __future__ import annotations

import sqlite3
import tomllib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..config import Settings, config_file
from ..data.http import HttpError, PublicHttpClient
from .universe import UserUniverse

FAVORABLE, DEFAVORABLE, DOUTEUX, INEXPLOITABLE = "FAVORABLE", "DEFAVORABLE", "DOUTEUX", "INEXPLOITABLE"
AJOUTEE, REFUSEE, A_DECIDER, INDISPONIBLE = "AJOUTEE", "REFUSEE", "A_DECIDER", "INDISPONIBLE"
RULE, OWNER = "règle", "propriétaire"
QUOTES = ("USDT", "USDC")
SUMMARIES = {"favorable": FAVORABLE, "defavorable": DEFAVORABLE, "défavorable": DEFAVORABLE, "douteux": DOUTEUX,
             "inexploitable": INEXPLOITABLE}
Lookup = Callable[[Settings, str], Decimal | None]


@dataclass(frozen=True)
class Screening:
    base: str
    status: str
    sources: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def explain(self) -> str:
        detail = ", ".join(f"{code} {verdict}" for code, verdict in self.sources.items()) or "avis non détaillé"
        return f"{self.status.lower()} ({detail}{' ; ' + self.note if self.note else ''})"


def classify(entry: dict) -> str:
    """Statut CSI d'une crypto à partir des avis par source (règle de docs/UNIVERSE.md), ou de son résumé."""
    verdicts = [str(v).lower() for k, v in entry.items() if k.isupper()]
    if verdicts:
        if "haram" in verdicts:
            return DEFAVORABLE
        if "douteux" in verdicts:
            return DOUTEUX
        return FAVORABLE if verdicts.count("halal") >= 2 else INEXPLOITABLE
    return SUMMARIES.get(str(entry.get("resume", "")).lower(), INEXPLOITABLE)


def screening_path(settings: Settings) -> Path:
    """À côté du fichier de configuration (Docker : /app/config) : c'est de la configuration, pas un état."""
    return config_file().parent / "halal_screening.toml"


def load_screening(settings: Settings) -> tuple[dict[str, Screening], str]:
    """(avis par crypto, date du relevé)."""
    path = screening_path(settings)
    if not path.exists():
        return {}, ""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = {}
    for base, entry in data.get("assets", {}).items():
        sources = {k: str(v) for k, v in entry.items() if k.isupper()}
        out[base.upper()] = Screening(base.upper(), classify(entry), sources, str(entry.get("note", "")))
    return out, str(data.get("checked_on", ""))


def base_of(symbol: str) -> str:
    symbol = symbol.upper()
    for quote in QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def screening_for(settings: Settings, symbol: str) -> Screening:
    screenings, _ = load_screening(settings)
    base = base_of(symbol)
    return screenings.get(base, Screening(base, INEXPLOITABLE, note="absente du screening consigné"))


def binance_listing(settings: Settings, symbol: str) -> Decimal | None:
    """Pas de prix si la paire existe et se négocie (TRADING) sur Binance Spot ; None si elle n'existe pas ou
    est suspendue. API publique `exchangeInfo`, aucune clé. Une panne réseau lève HttpError."""
    client = PublicHttpClient.rest(settings.data.rest_base_url, retries=2)
    try:
        try:
            data = client.get_json("/api/v3/exchangeInfo", {"symbol": symbol})
        except HttpError as exc:
            if exc.status == 400:                    # « Invalid symbol » : la paire n'existe pas
                return None
            raise
        info = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
        if info is None or info.get("status") != "TRADING":
            return None
        tick = next(f["tickSize"] for f in info["filters"] if f["filterType"] == "PRICE_FILTER")
        return Decimal(tick).normalize()
    finally:
        client.close()


class AdmissionLog:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS pair_admissions (
                symbol TEXT PRIMARY KEY, base TEXT NOT NULL, screening TEXT NOT NULL, decision TEXT NOT NULL,
                decided_by TEXT NOT NULL, reason TEXT NOT NULL, decided_at TEXT NOT NULL)""")
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, symbol: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM pair_admissions WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None

    def all(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM pair_admissions ORDER BY decision, symbol")]

    def pending(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM pair_admissions WHERE decision=? ORDER BY decided_at, symbol", (A_DECIDER,))]

    def record(self, symbol: str, screening: Screening, decision: str, *, by: str, reason: str,
               now: datetime) -> dict:
        with self.connect() as db:
            db.execute("""INSERT INTO pair_admissions VALUES (?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(symbol) DO UPDATE SET screening=excluded.screening,
                          decision=excluded.decision, decided_by=excluded.decided_by, reason=excluded.reason,
                          decided_at=excluded.decided_at""",
                       (symbol, screening.base, screening.status, decision, by, reason[:300], now.isoformat()))
        entry = self.get(symbol)
        assert entry is not None
        return entry


def admit(settings: Settings, symbol: str, *, now: datetime, lookup: Lookup = binance_listing) -> dict:
    """Applique la règle à une paire (hors configuration). Une décision du propriétaire est conservée."""
    symbol = symbol.upper()
    log = AdmissionLog(settings.external_db)
    previous = log.get(symbol)
    if previous and previous["decided_by"] == OWNER:
        return previous
    screening = screening_for(settings, symbol)
    if screening.status == DEFAVORABLE:
        return log.record(symbol, screening, REFUSEE, by=RULE, now=now,
                          reason=f"défavorable au screening halal : {screening.explain()}")
    if screening.status != FAVORABLE:
        return log.record(symbol, screening, A_DECIDER, by=RULE, now=now,
                          reason=f"{screening.explain()} : à décider par le propriétaire")
    tick = lookup(settings, symbol)
    if tick is None:
        return log.record(symbol, screening, INDISPONIBLE, by=RULE, now=now,
                          reason="favorable au screening, mais pas de paire USDT négociable sur Binance Spot")
    UserUniverse(settings.external_db).request(symbol, tick, now=now,
                                               reason=f"screening halal favorable ({screening.explain()})")
    return log.record(symbol, screening, AJOUTEE, by=RULE, now=now,
                      reason=f"favorable au screening halal : {screening.explain()} ; ajout direct")


def admit_all(settings: Settings, *, now: datetime, lookup: Lookup = binance_listing) -> list[dict]:
    """Toutes les cryptos du screening, en paire USDT, hors univers de la configuration."""
    screenings, _ = load_screening(settings)
    configured = set(settings.data.symbols)
    return [admit(settings, f"{base}USDT", now=now, lookup=lookup) for base in sorted(screenings)
            if f"{base}USDT" not in configured]


def decide(settings: Settings, symbol: str, *, add: bool, now: datetime, lookup: Lookup = binance_listing) -> dict:
    """Décision du propriétaire (bouton du tableau de bord) : prévaut sur la règle, tracée comme telle."""
    symbol = symbol.upper()
    screening = screening_for(settings, symbol)
    log = AdmissionLog(settings.external_db)
    if not add:
        UserUniverse(settings.external_db).forget(symbol)
        return log.record(symbol, screening, REFUSEE, by=OWNER, now=now,
                          reason=f"refusée par le propriétaire (avis {screening.explain()})")
    tick = lookup(settings, symbol)
    if tick is None:
        return log.record(symbol, screening, INDISPONIBLE, by=OWNER, now=now,
                          reason="acceptée par le propriétaire, mais pas de paire négociable sur Binance Spot")
    UserUniverse(settings.external_db).request(symbol, tick, now=now,
                                               reason=f"décision du propriétaire (avis {screening.explain()})")
    return log.record(symbol, screening, AJOUTEE, by=OWNER, now=now,
                      reason=f"acceptée par le propriétaire (avis {screening.explain()})")
