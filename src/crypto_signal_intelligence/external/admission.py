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
INJOIGNABLE = "INJOIGNABLE"            # Binance injoignable pendant la vérification : rien n'est enregistré
STATUS_LABELS = {FAVORABLE: "favorable", DEFAVORABLE: "défavorable", DOUTEUX: "douteux",
                 INEXPLOITABLE: "inexploitable"}
# Motif d'ajout d'une paire soumise à la main par le propriétaire (external/evaluate.py) : sa décision.
MANUAL_REASON = "signal soumis à la main"
# Motif d'ajout d'une paire publiée par un groupe de confiance halal (règle du propriétaire du 2026-10-06).
TRUSTED_REASON = "groupe de confiance halal"


class DefavorableRefused(ValueError):
    """Ajout demandé pour une crypto défavorable : seul le fichier des avis peut lever ce refus."""
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
        return f"{STATUS_LABELS.get(self.status, self.status.lower())} ({detail}{' ; ' + self.note if self.note else ''})"


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
    return load_screening_file(screening_path(settings))


def load_screening_file(path: Path) -> tuple[dict[str, Screening], str]:
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


def _added_by_owner(entry: dict | None) -> bool:
    """Paire de l'univers ajouté soumise à la main par le propriétaire (sa décision, motif enregistré)."""
    return entry is not None and str(entry.get("reason", "")).startswith(MANUAL_REASON)


def admit(settings: Settings, symbol: str, *, now: datetime, lookup: Lookup = binance_listing) -> dict:
    """Applique la règle à une paire hors configuration ; une décision du propriétaire est conservée.

    S'applique aussi, rétroactivement, aux paires ajoutées AVANT la règle par l'ancien mode test : une paire
    soumise à la main compte comme décision du propriétaire ; une autre, non favorable, repasse « à décider »
    (ses signaux restent EN_ATTENTE, external/evaluate.py), ou refusée si elle est défavorable. Une crypto qui
    ne se négocie pas sur Binance Spot est « indisponible » : aucune décision à prendre."""
    symbol = symbol.upper()
    log = AdmissionLog(settings.external_db)
    previous = log.get(symbol)
    if previous and previous["decided_by"] == OWNER:
        return previous
    screening = screening_for(settings, symbol)
    present = UserUniverse(settings.external_db).get(symbol)
    if _added_by_owner(present) and screening.status != DEFAVORABLE:
        return log.record(symbol, screening, AJOUTEE, by=OWNER, now=now,
                          reason=f"soumise à la main par le propriétaire (avis {screening.explain()})")
    if screening.status == DEFAVORABLE:
        return log.record(symbol, screening, REFUSEE, by=RULE, now=now,
                          reason=f"défavorable au screening halal : {screening.explain()}")
    tick: Decimal | None = None
    if present is None:
        try:
            tick = lookup(settings, symbol)
        except HttpError as exc:
            return {"symbol": symbol, "base": screening.base, "screening": screening.status, "decision": INJOIGNABLE,
                    "decided_by": RULE, "reason": f"Binance injoignable ({exc}) : rien n'est enregistré, à relancer",
                    "decided_at": now.isoformat()}
        if tick is None:
            return log.record(symbol, screening, INDISPONIBLE, by=RULE, now=now,
                              reason=f"{screening.explain()} ; pas de paire négociable sur Binance Spot")
    if screening.status != FAVORABLE:
        before = " ; ajoutée avant la règle, ses signaux restent en attente" if present else ""
        return log.record(symbol, screening, A_DECIDER, by=RULE, now=now,
                          reason=f"{screening.explain()} : à décider par le propriétaire{before}")
    if present is None and tick is not None:
        UserUniverse(settings.external_db).request(symbol, tick, now=now,
                                                   reason=f"screening halal favorable ({screening.explain()})")
    return log.record(symbol, screening, AJOUTEE, by=RULE, now=now,
                      reason=f"favorable au screening halal : {screening.explain()} ; ajout direct")


def admit_all(settings: Settings, *, now: datetime, lookup: Lookup = binance_listing) -> list[dict]:
    """Toutes les cryptos du screening en paire USDT, plus les paires déjà ajoutées, hors configuration."""
    screenings, _ = load_screening(settings)
    configured = set(settings.data.symbols)
    symbols = {f"{base}USDT" for base in screenings} | {e["symbol"] for e in UserUniverse(settings.external_db).all()}
    return [admit(settings, symbol, now=now, lookup=lookup) for symbol in sorted(symbols - configured)]


def hold(settings: Settings, symbol: str, *, now: datetime, source: str) -> tuple[str, str] | None:
    """Pour une paire DÉJÀ dans l'univers ajouté : ('refus' | 'attente', motif) si ses signaux ne doivent pas
    être transmis, None sinon. Rattrape les paires ajoutées avant la règle par l'ancien mode test."""
    if symbol in settings.data.symbols:
        return None
    log = AdmissionLog(settings.external_db)
    decided = log.get(symbol)
    if decided and decided["decided_by"] == OWNER:
        return ("refus", f"{symbol} refusée par toi le {decided['decided_at'][:16]}") \
            if decided["decision"] == REFUSEE else None
    screening = screening_for(settings, symbol)
    if screening.status == DEFAVORABLE:
        if not decided:
            log.record(symbol, screening, REFUSEE, by=RULE, now=now,
                       reason=f"défavorable au screening halal : {screening.explain()}")
        return "refus", f"{symbol} défavorable au screening halal ({screening.explain()}) : refusée"
    if screening.status == FAVORABLE or _added_by_owner(UserUniverse(settings.external_db).get(symbol)):
        return None
    if not decided or decided["decision"] != A_DECIDER:
        log.record(symbol, screening, A_DECIDER, by=RULE, now=now,
                   reason=f"{screening.explain()} : ajoutée avant la règle ; signal reçu de « {source} »")
    return "attente", (f"{symbol} : avis halal {screening.explain()} ; ajoutée avant la règle, en attente de ta "
                       "décision (tableau de bord, onglet Suivi, « Cryptos à décider ») : signal non transmis")


def decide(settings: Settings, symbol: str, *, add: bool, now: datetime, lookup: Lookup = binance_listing) -> dict:
    """Décision du propriétaire (bouton du tableau de bord) : prévaut sur la règle, tracée comme telle."""
    symbol = symbol.upper()
    screening = screening_for(settings, symbol)
    log = AdmissionLog(settings.external_db)
    if not add:
        UserUniverse(settings.external_db).forget(symbol)
        return log.record(symbol, screening, REFUSEE, by=OWNER, now=now,
                          reason=f"refusée par le propriétaire (avis {screening.explain()})")
    if screening.status == DEFAVORABLE:
        raise DefavorableRefused(f"{symbol} défavorable au screening halal ({screening.explain()}) : le refus ne se "
                                 "lève qu'en modifiant config/halal_screening.toml")
    present = UserUniverse(settings.external_db).get(symbol)
    if present is None:
        tick = lookup(settings, symbol)
        if tick is None:
            return log.record(symbol, screening, INDISPONIBLE, by=OWNER, now=now,
                              reason="acceptée par le propriétaire, mais pas de paire négociable sur Binance Spot")
        UserUniverse(settings.external_db).request(symbol, tick, now=now,
                                                   reason=f"décision du propriétaire (avis {screening.explain()})")
    return log.record(symbol, screening, AJOUTEE, by=OWNER, now=now,
                      reason=f"acceptée par le propriétaire (avis {screening.explain()})")


def decide_all_pending(settings: Settings, *, now: datetime, lookup: Lookup = binance_listing,
                       symbols: list[str] | None = None) -> list[dict]:
    """« Tout ajouter » : chaque crypto à décider (ou seulement celles de `symbols`) est ajoutée, comme décision
    du propriétaire. Une paire qui n'est pas « à décider » n'est jamais touchée."""
    wanted = None if symbols is None else {s.upper() for s in symbols}
    return [decide(settings, entry["symbol"], add=True, now=now, lookup=lookup)
            for entry in AdmissionLog(settings.external_db).pending()
            if wanted is None or entry["symbol"] in wanted]


# Groupes d'affichage des cryptos à décider, du plus étayé au moins étayé.
GROUP_ONE_SOURCE, GROUP_DOUBTFUL, GROUP_NO_SOURCE = "une_source", "douteux", "aucune_source"


def pending_group(screening: Screening) -> str:
    """une_source : une seule source dit halal, aucune réserve ; douteux : zone grise pour une source au
    moins ; aucune_source : aucune source ne la liste."""
    if screening.status == DOUTEUX:
        return GROUP_DOUBTFUL
    return GROUP_ONE_SOURCE if "halal" in screening.sources.values() else GROUP_NO_SOURCE


# ---------------------------------------------------------------------------------------------------------------
# Groupes de confiance halal (décision du propriétaire, 2026-10-06)
# ---------------------------------------------------------------------------------------------------------------

def trusted_group(settings: Settings, chat_id: object) -> str:
    """Groupe de confiance désigné par l'identifiant de la conversation Telegram d'origine (métadonnée posée par
    Telegram et transmise par le relais), sinon "". Jamais par un nom écrit dans le message : n'importe quel canal
    peut écrire « WHALE HUNTING » en tête (relecture du 2026-10-06)."""
    chat = str(chat_id or "").strip()
    if not chat.lstrip("-").isdigit():
        return ""
    for group in settings.external.halal_trusted_groups:
        if str(group).strip() == chat:
            return chat
    return ""


def trusted_reason(group: str, screening: Screening) -> str:
    return f"{TRUSTED_REASON} « {group} » (règle du propriétaire du 2026-10-06 ; avis {screening.explain()})"


def admit_from_trusted_group(settings: Settings, symbol: str, *, group: str, now: datetime,
                             lookup: Lookup | None = None) -> dict:
    """Paire publiée par un groupe de confiance halal : ajoutée comme décision du propriétaire si elle se négocie
    sur Binance Spot. Jamais contre un refus du propriétaire ni contre un avis défavorable (sa règle) ; une paire
    déjà ajoutée reste telle quelle. Binance injoignable : rien n'est enregistré (INJOIGNABLE), à relancer."""
    symbol = symbol.upper()
    log = AdmissionLog(settings.external_db)
    decided = log.get(symbol)
    if decided and decided["decided_by"] == OWNER and decided["decision"] in {REFUSEE, AJOUTEE}:
        return decided
    screening = screening_for(settings, symbol)
    refused = next((e for e in log.all() if e["decided_by"] == OWNER and e["decision"] == REFUSEE
                    and base_of(e["symbol"]) == screening.base), None)
    if refused is not None:
        # Le refus du propriétaire vaut pour la crypto, quelle que soit la paire (BNBUSDC refusée → BNBUSDT aussi).
        return refused
    if not symbol.endswith("USDT"):
        # Règle du propriétaire : paire USDT seulement (une paire USDC ne passe jamais par ce chemin).
        return {"symbol": symbol, "base": screening.base, "screening": screening.status, "decision": INDISPONIBLE,
                "decided_by": RULE, "reason": "groupe de confiance : seules les paires USDT sont ajoutées",
                "decided_at": now.isoformat()}
    if screening.status == DEFAVORABLE:
        if decided and decided["decision"] == REFUSEE:
            return decided
        return log.record(symbol, screening, REFUSEE, by=RULE, now=now,
                          reason=f"défavorable au screening halal : {screening.explain()} (reçue de « {group} »)")
    universe = UserUniverse(settings.external_db)
    if universe.get(symbol) is None and symbol not in settings.data.symbols:
        try:
            tick = (lookup or binance_listing)(settings, symbol)
        except HttpError as exc:
            return {"symbol": symbol, "base": screening.base, "screening": screening.status, "decision": INJOIGNABLE,
                    "decided_by": RULE, "reason": f"Binance injoignable ({exc}) : rien n'est enregistré, à relancer",
                    "decided_at": now.isoformat()}
        if tick is None:
            if decided and decided["decided_by"] == OWNER:
                return decided                 # sa décision n'est jamais remplacée par la règle
            return log.record(symbol, screening, INDISPONIBLE, by=RULE, now=now,
                              reason=f"reçue de « {group} » ; pas de paire négociable sur Binance Spot")
        universe.request(symbol, tick, now=now, reason=trusted_reason(group, screening))
    return log.record(symbol, screening, AJOUTEE, by=OWNER, now=now, reason=trusted_reason(group, screening))
