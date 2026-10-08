"""Relevé de LIQUIDITÉ en shadow (demande du propriétaire du 2026-10-08 ; docs/LIQUIDITE.md).

« La quantité qui veut acheter ou pas, analyser avant d'entrer » : l'historique du carnet d'ordres n'est pas
disponible gratuitement, on l'ENREGISTRE donc à partir de maintenant, pour mesurer plus tard, en direct, s'il
annonce le bon ou le mauvais trade. Ce relevé n'influence AUCUNE décision (ni les tests en direct F4/F16, ni les
avis, ni BinanceSpotManager) : il écrit seulement ses journaux en ajout seul.

Quand :
- à chaque nouveau signal Telegram lisible sur une paire USDT (dossier du relais `POST /telegram/live` et boîte de
  BSM, lus par `forward/telegram_live.py` sans le modifier) : un relevé immédiat, prioritaire ;
- toutes les 15 minutes (5 min après chaque quart d'heure, hors de la rafale du scanner), sur les paires suivies
  (configuration + paires ajoutées prêtes), au plus `MAX_PAIRS` paires.

Quoi, pour chaque relevé (deux demandes PUBLIQUES, liste blanche de `data/http.py`, aucune clé) :
- carnet `/api/v3/depth` (`DEPTH_LIMIT` niveaux) : meilleurs prix, écart, USDT cumulés à ±0,5 / 1 / 2 % du milieu,
  déséquilibre (achats − ventes) / (achats + ventes), courbe de profondeur, glissement estimé d'un achat puis d'une
  vente au marché de 100, 500 et 2 000 USDT ;
- flux récent, avec les klines 1 minute `/api/v3/klines` (aucun autre endpoint) : part des achats « taker » sur 15 et
  60 min, volume relatif à la moyenne des ~15 heures précédentes, nombre de transactions, variation du prix.
  Seules les bougies CLOSES au moment du relevé comptent.

Débit borné : une demande toutes les `REQUEST_INTERVAL` s au plus (30 par minute), poids Binance ≤ 52 par paire ;
un 429/418 suspend le relevé, trois pannes de suite abandonnent le cycle (le trou reste visible au journal).
Un fil séparé de la surveillance l'exécute : le scanner ne l'attend jamais, et une panne ne casse rien d'autre.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from .journal import Journal, utc_iso

log = logging.getLogger("csi.liquidity")

SIGNALS_ID, PERIODIC_ID = "F0_LIQUIDITE_SIGNAUX", "F0_LIQUIDITE"
RECORD, CYCLE, ERROR, MISSED = "RELEVE", "CYCLE", "ERREUR", "MANQUE"
SIGNAL, PERIODIC = "SIGNAL", "PERIODIQUE"
VERSION = 1

DEPTH_PATH, KLINES_PATH = "/api/v3/depth", "/api/v3/klines"
DEPTH_LIMIT = 1000                 # poids Binance 50 (501 à 1 000 niveaux)
KLINES_LIMIT = 1000                # bougies 1 min (poids 2) : 15 et 60 dernières minutes + ~15 h de référence
BANDS = (0.005, 0.01, 0.02)        # ±0,5 %, ±1 %, ±2 % du prix médian
SIZES = (100, 500, 2000)           # tailles d'ordre au marché simulées (USDT)
CURVE = (0.0005, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02)   # courbe de profondeur (distances)
FLOW_WINDOWS = (15, 60)            # minutes

EVERY = pd.Timedelta(minutes=15)
OFFSET = pd.Timedelta(minutes=5)   # relevé périodique à hh:05, :20, :35, :50 (le scanner travaille à la clôture)
MAX_PAIRS = 40                     # paires relevées par cycle périodique
REQUEST_INTERVAL = 2.0             # secondes entre deux demandes : 30 demandes par minute au plus
SIGNAL_CHECK_SECONDS = 5.0         # nouveaux signaux cherchés toutes les 5 s (et entre deux relevés)
IDLE_SECONDS = 2.0
SIGNAL_MAX_AGE = pd.Timedelta(minutes=30)    # un signal plus ancien n'est plus relevé (MANQUE inscrit)
FAILURES_TO_ABANDON = 3
BAN_BACKOFF_SECONDS, OUTAGE_BACKOFF_SECONDS = 600.0, 120.0
HTTP_RETRIES = 2
TAIL_BYTES = 1_000_000             # lecture de la fin du journal périodique (≈ 20 cycles)

# Contrôle « avant d'entrer » (information seulement)
MAX_SLIPPAGE_PCT, MAX_SPREAD_PCT = 0.2, 0.5
DEFAULT_SIZE = 500.0
SUFFICIENT, INSUFFICIENT, UNKNOWN = "SUFFISANTE", "INSUFFISANTE", "INCONNUE"


# --- calculs purs -------------------------------------------------------------------------------------------
def _levels(raw: Iterable, *, descending: bool) -> list[tuple[float, float]]:
    levels = [(float(p), float(q)) for p, q in raw]
    levels = [(p, q) for p, q in levels if p > 0 and q > 0 and math.isfinite(p) and math.isfinite(q)]
    return sorted(levels, key=lambda level: -level[0] if descending else level[0])


def band_key(band: float) -> str:
    """« 0.5 », « 1 », « 2 » : distance en % (clé des journaux)."""
    return f"{band * 100:g}"


def market_buy(asks: list[tuple[float, float]], quote: float) -> dict:
    """Achat au marché de `quote` USDT en remontant les ventes : prix moyen et glissement par rapport au meilleur
    prix de vente (%) ; `filled` faux si le carnet lu n'y suffit pas (glissement alors inconnu)."""
    spent, base = 0.0, 0.0
    for price, qty in asks:
        take = min(quote - spent, price * qty)
        spent += take
        base += take / price
        if spent >= quote * (1 - 1e-12):
            avg = quote / base
            return {"filled": True, "avg": avg, "slip_pct": round((avg / asks[0][0] - 1) * 100, 5)}
    return {"filled": False, "avg": None, "slip_pct": None, "filled_usdt": round(spent, 2)}


def market_sell(bids: list[tuple[float, float]], quantity: float) -> dict:
    """Vente au marché de `quantity` unités en descendant les achats : glissement par rapport au meilleur prix
    d'achat (%)."""
    sold, proceeds = 0.0, 0.0
    for price, qty in bids:
        take = min(quantity - sold, qty)
        sold += take
        proceeds += take * price
        if sold >= quantity * (1 - 1e-12):
            avg = proceeds / quantity
            return {"filled": True, "avg": avg, "slip_pct": round((1 - avg / bids[0][0]) * 100, 5)}
    return {"filled": False, "avg": None, "slip_pct": None, "filled_usdt": round(proceeds, 2)}


def book_metrics(book: dict, *, bands: tuple[float, ...] = BANDS, sizes: tuple[float, ...] = SIZES) -> dict:
    """Mesures d'un carnet Binance (`{"bids": [[prix, qté]…], "asks": […]}`).

    - écart = (meilleure vente − meilleur achat) / milieu, en % ;
    - profondeur à ±b : USDT cumulés des achats à un prix ≥ milieu × (1 − b) et des ventes ≤ milieu × (1 + b) ;
      `truncated` vrai si le carnet lu s'arrête avant la borne (la profondeur est alors un minimum) ;
    - déséquilibre = (achats − ventes) / (achats + ventes), entre −1 (que des ventes) et +1 (que des achats) ;
    - glissement : achat au marché de S USDT (contre le meilleur prix de vente) puis vente au marché de la même
      valeur au prix médian, S / milieu unités (contre le meilleur prix d'achat), en % ; None si le carnet lu n'y
      suffit pas ;
    - courbe : USDT cumulés de chaque côté aux distances `CURVE` du milieu (0,05 % à 2 %).
    """
    bids = _levels(book.get("bids", []), descending=True)
    asks = _levels(book.get("asks", []), descending=False)
    if not bids or not asks:
        raise ValueError("carnet vide")
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_ask <= best_bid:
        raise ValueError("carnet croisé (meilleure vente ≤ meilleur achat)")
    mid = (best_bid + best_ask) / 2

    def side_depth(band: float) -> tuple[float, float]:
        return (sum(p * q for p, q in bids if p >= mid * (1 - band)),
                sum(p * q for p, q in asks if p <= mid * (1 + band)))

    depth = {}
    for band in bands:
        bid_usdt, ask_usdt = side_depth(band)
        total = bid_usdt + ask_usdt
        depth[band_key(band)] = {
            "bid_usdt": round(bid_usdt), "ask_usdt": round(ask_usdt),
            "imbalance": round((bid_usdt - ask_usdt) / total, 4) if total > 0 else None,
            "truncated": bool(bids[-1][0] > mid * (1 - band) or asks[-1][0] < mid * (1 + band))}
    curve = [side_depth(d) for d in CURVE]
    # Glissement en % par taille ; None = le carnet lu ne suffit pas à remplir l'ordre.
    slippage: dict[str, dict] = {"buy": {}, "sell": {}}
    for size in sizes:
        slippage["buy"][f"{size:g}"] = market_buy(asks, size)["slip_pct"]
        slippage["sell"][f"{size:g}"] = market_sell(bids, size / mid)["slip_pct"]
    return {"bid": best_bid, "ask": best_ask, "spread_pct": round((best_ask - best_bid) / mid * 100, 5),
            "levels": [len(bids), len(asks)], "depth": depth,
            "curve_bid_usdt": [round(b) for b, _ in curve], "curve_ask_usdt": [round(a) for _, a in curve],
            "slippage": slippage}


def _ms(moment) -> int:
    return int(pd.Timestamp(moment).timestamp() * 1000)


def flow_metrics(rows: list, *, now) -> dict:
    """Flux récent sur des klines 1 minute brutes de Binance (12 colonnes), bougies CLOSES avant `now` seulement.

    Pour 15 et 60 min : part des achats au marché (taker buy, en USDT) dans le volume, volume en USDT, nombre de
    transactions, variation du prix (%) et volume relatif = volume moyen par minute de la fenêtre / volume moyen
    par minute des bougies qui la précèdent (au moins 60 min de référence ; la fenêtre de 60 min est exclue de la
    référence des deux). `gaps` : minutes manquantes dans la dernière heure."""
    now_ms = _ms(now)
    closed = sorted((r for r in rows if int(r[6]) < now_ms), key=lambda r: int(r[0]))
    out: dict = {"bars": len(closed), "last_closed_open": None, "gaps": None}
    if not closed:
        return out
    out["last_closed_open"] = utc_iso(pd.Timestamp(int(closed[-1][0]), unit="ms", tz="UTC"))
    hour = closed[-60:]
    out["gaps"] = int((int(hour[-1][0]) - int(hour[0][0])) // 60_000 + 1 - len(hour))
    baseline = closed[:-60]
    base_rate = (sum(float(r[7]) for r in baseline) / len(baseline)) if len(baseline) >= 60 else None
    out["baseline_minutes"] = len(baseline)
    for minutes in FLOW_WINDOWS:
        key = f"{minutes}m"
        window = closed[-minutes:]
        if len(window) < minutes:
            out[key] = None
            continue
        quote = sum(float(r[7]) for r in window)
        taker = sum(float(r[10]) for r in window)
        previous_close = float(closed[-minutes - 1][4]) if len(closed) > minutes else float(window[0][1])
        out[key] = {
            "quote_volume": round(quote),
            "taker_buy_share": round(taker / quote, 4) if quote > 0 else None,
            "trades": int(sum(int(r[8]) for r in window)),
            "return_pct": round((float(window[-1][4]) / previous_close - 1) * 100, 4) if previous_close > 0 else None,
            "rel_volume": round(quote / minutes / base_rate, 4) if base_rate else None}
    return out


def entry_check(book: dict, size_usdt: float, *, max_slippage_pct: float = MAX_SLIPPAGE_PCT,
                max_spread_pct: float = MAX_SPREAD_PCT) -> dict:
    """La profondeur suffit-elle pour `size_usdt` ? (INFORMATION, aucune décision n'en dépend.)

    `book` : mesures d'un relevé (`book_metrics`). SUFFISANTE si l'écart est < `max_spread_pct` % et si l'achat ET la
    vente au marché de cette taille glissent de moins de `max_slippage_pct` % (contre le meilleur prix). Le relevé
    connaît le glissement à 100, 500 et 2 000 USDT : on prend la plus petite taille relevée ≥ `size_usdt` (le
    glissement croît avec la taille : estimation prudente) ; au-delà de 2 000 USDT, INCONNUE."""
    if not (isinstance(size_usdt, int | float) and math.isfinite(size_usdt) and size_usdt > 0):
        raise ValueError("taille en USDT strictement positive attendue")
    stored = sorted(float(k) for k in book.get("slippage", {}).get("buy", {}))
    usable = [s for s in stored if s >= size_usdt]
    spread = book.get("spread_pct")
    out: dict = {"size_usdt": size_usdt, "spread_pct": spread, "buy_slip_pct": None, "sell_slip_pct": None,
                 "evaluated_size": None, "reasons": [],
                 "rule": f"écart < {max_spread_pct:g} %, glissement < {max_slippage_pct:g} % à l'achat et à la vente"}
    if not usable:
        largest = f"{stored[-1]:g}" if stored else "aucune"
        return out | {"status": UNKNOWN, "reasons": [f"taille au-delà des tailles relevées (plus grande : {largest} USDT)"]}
    size_key = f"{usable[0]:g}"
    buy, sell = book["slippage"]["buy"][size_key], book["slippage"]["sell"][size_key]
    out |= {"evaluated_size": usable[0], "buy_slip_pct": buy, "sell_slip_pct": sell}
    reasons = []
    if spread is None or spread >= max_spread_pct:
        reasons.append(f"écart {spread} % ≥ {max_spread_pct:g} %")
    for name, slip in (("achat", buy), ("vente", sell)):
        if slip is None:
            reasons.append(f"{name} : profondeur lue insuffisante pour {size_key} USDT")
        elif slip >= max_slippage_pct:
            reasons.append(f"{name} : glissement {slip:.3f} % ≥ {max_slippage_pct:g} %")
    return out | {"status": INSUFFICIENT if reasons else SUFFICIENT, "reasons": reasons}


def entry_check_book(raw_book: dict, size_usdt: float, **limits) -> dict:
    """Même contrôle, exact pour n'importe quelle taille, sur un carnet brut de Binance."""
    return entry_check(book_metrics(raw_book, sizes=(size_usdt,)), size_usdt, **limits)


# --- débit borné --------------------------------------------------------------------------------------------
class RateLimiter:
    """Au plus une demande toutes les `interval` secondes (horloge monotone), pour tout le relevé."""

    def __init__(self, interval: float = REQUEST_INTERVAL, *, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval, self.monotonic, self.sleep = interval, monotonic, sleep
        self._next: float | None = None
        self.calls = 0

    def wait(self) -> None:
        now = self.monotonic()
        if self._next is not None and now < self._next:
            self.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval
        self.calls += 1


# --- journaux -----------------------------------------------------------------------------------------------
def signals_journal(settings: Settings) -> Journal:
    return Journal(settings.root / "forward" / f"{SIGNALS_ID}.jsonl")


def periodic_path(settings: Settings, moment) -> Path:
    """Un journal périodique par mois (≈ 1 Mo par jour pour 40 paires) : `F0_LIQUIDITE-AAAA-MM.jsonl`."""
    return settings.root / "forward" / f"{PERIODIC_ID}-{pd.Timestamp(moment):%Y-%m}.jsonl"


def periodic_journal(settings: Settings, moment) -> Journal:
    return Journal(periodic_path(settings, moment))


def tail_entries(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """Entrées des derniers `max_bytes` d'un journal (lecture seule ; la première ligne coupée est ignorée)."""
    if not path.exists():
        return []
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        start = max(0, size - max_bytes)
        handle.seek(start)
        lines = handle.read().split(b"\n")
    if start > 0:
        lines = lines[1:]
    out = []
    for line in lines:
        try:
            entry = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if isinstance(entry, dict) and "kind" in entry and isinstance(entry.get("data"), dict):
            out.append(entry)
    return out


# --- relevé -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Task:
    symbol: str
    trigger: str
    cycle: str | None = None
    signal: dict = field(default_factory=dict)


def tracked_pairs(settings: Settings) -> list[str]:
    from ..external.universe import universe_symbols
    return universe_symbols(settings)


def due_cycle(now, last: pd.Timestamp | None) -> pd.Timestamp | None:
    """Quart d'heure dont le relevé est dû (5 min après son début), ou None."""
    quarter = (pd.Timestamp(now) - OFFSET).floor(EVERY)
    return quarter if last is None or quarter > last else None


class LiquidityWorker:
    """Relevé de liquidité : une tâche à la fois, signaux d'abord, débit borné. `step` ne lève jamais pour une
    panne de Binance (entrée ERREUR au journal) ; le fil (`run`) rattrape toute autre exception."""

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime], depth: PublicHttpClient | None = None,
                 rest: PublicHttpClient | None = None, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, pairs: Callable[[Settings], list[str]] = tracked_pairs):
        self.settings, self.clock, self.monotonic, self.pairs = settings, clock, monotonic, pairs
        base = settings.data.rest_base_url
        self.depth = depth or PublicHttpClient.depth(base, retries=HTTP_RETRIES)
        self.rest = rest or PublicHttpClient.rest(base, retries=HTTP_RETRIES)
        self.limiter = RateLimiter(monotonic=monotonic, sleep=sleep)
        self.queue: deque[Task] = deque()
        self.seen: set[str] = {e["data"].get("signal_id") for e in signals_journal(settings).entries({RECORD, MISSED})}
        self.seen_files: set[str] = set()
        self.last_cycle = self._last_cycle(clock())
        self.last_signal_check: float | None = None
        self.backoff_until: float | None = None
        self.failures = 0

    def _last_cycle(self, now) -> pd.Timestamp | None:
        cycles = [e["data"]["cycle"] for e in tail_entries(periodic_path(self.settings, now)) if e["kind"] == CYCLE]
        return pd.Timestamp(cycles[-1]) if cycles else None

    # signaux --------------------------------------------------------------------------------------------
    def new_signals(self, now) -> list:
        """Signaux reçus depuis `SIGNAL_MAX_AGE`, jamais vus (dossier du relais : seuls les nouveaux fichiers sont
        lus ; boîte de BSM si configurée). Lecture seule de `forward/telegram_live.py`."""
        from . import telegram_live
        since = pd.Timestamp(now) - SIGNAL_MAX_AGE
        found = []
        folder = telegram_live.live_dir(self.settings)
        if folder.exists():
            for path in sorted(folder.glob("*.json")):
                if path.name in self.seen_files:
                    continue
                modified = path.stat().st_mtime
                if modified < (since - pd.Timedelta(hours=1)).timestamp():
                    self.seen_files.add(path.name)          # dépôt ancien : jamais relu
                    continue
                rows = telegram_live.read_robot_file(path)
                if rows or time.time() - modified > 10:     # fichier vide ou en cours d'écriture : relu plus tard
                    self.seen_files.add(path.name)
                found += rows
        inbox = telegram_live.bsm_inbox(self.settings)
        if inbox:
            found += telegram_live.read_bsm(inbox, since=since)
        out = []
        for signal in sorted(found, key=lambda s: (s.received_at, s.id)):
            if signal.id in self.seen or pd.Timestamp(signal.received_at) < since:
                continue
            self.seen.add(signal.id)
            out.append(signal)
        return out

    def signal_tasks(self, now) -> list[Task]:
        from ..external.parser import parse
        tasks = []
        for signal in self.new_signals(now):
            try:
                parsed = parse(signal.text)
            except Exception:  # noqa: BLE001 - un texte que le parseur ne sait pas lire n'arrête rien
                continue
            if parsed.errors or not parsed.symbol.endswith("USDT"):
                continue
            tasks.append(Task(parsed.symbol, SIGNAL, signal={
                "signal_id": signal.id, "provider": signal.provider, "chat": signal.chat, "source": signal.source,
                "received_at": signal.received_at}))
        return tasks

    # cycle périodique -----------------------------------------------------------------------------------
    def enqueue_cycle(self, cycle: pd.Timestamp, now) -> None:
        stale = [t for t in self.queue if t.trigger == PERIODIC]
        if stale:
            self.queue = deque(t for t in self.queue if t.trigger != PERIODIC)
            periodic_journal(self.settings, now).append(MISSED, {
                "cycle": stale[0].cycle, "pairs": [t.symbol for t in stale],
                "reason": "cycle suivant dû avant la fin de celui-ci"}, now=now)
        try:
            pairs = list(dict.fromkeys(self.pairs(self.settings)))
        except Exception as exc:  # noqa: BLE001 - univers illisible : la configuration seule
            log.warning("univers illisible pour le relevé de liquidité : %s", exc)
            pairs = list(self.settings.data.symbols)
        kept, skipped = pairs[:MAX_PAIRS], pairs[MAX_PAIRS:]
        stamp = utc_iso(cycle)
        periodic_journal(self.settings, now).append(CYCLE, {"cycle": stamp, "pairs": kept, "skipped": skipped,
                                                            "max_pairs": MAX_PAIRS}, now=now)
        self.queue.extend(Task(symbol, PERIODIC, cycle=stamp) for symbol in kept)
        self.last_cycle = cycle

    # mesure ---------------------------------------------------------------------------------------------
    def measure(self, symbol: str) -> dict:
        """Carnet puis klines 1 min de la paire (deux demandes, débit borné)."""
        self.limiter.wait()
        raw = self.depth.get_json(DEPTH_PATH, {"symbol": symbol, "limit": DEPTH_LIMIT})
        at = self.clock()
        book = book_metrics(raw)
        self.limiter.wait()
        rows = self.rest.get_json(KLINES_PATH, {"symbol": symbol, "interval": "1m", "limit": KLINES_LIMIT})
        if not isinstance(rows, list):
            raise ValueError("réponse klines inattendue")
        return {"time": utc_iso(at), "book": book, "flow": flow_metrics(rows, now=at), "depth_limit": DEPTH_LIMIT,
                "version": VERSION}

    def _journal_for(self, task: Task, now) -> Journal:
        return signals_journal(self.settings) if task.trigger == SIGNAL else periodic_journal(self.settings, now)

    def run_task(self, task: Task) -> None:
        now = self.clock()
        base = {"symbol": task.symbol, "trigger": task.trigger, "cycle": task.cycle, **task.signal}
        if task.trigger == SIGNAL and pd.Timestamp(now) - pd.Timestamp(task.signal["received_at"]) > SIGNAL_MAX_AGE:
            signals_journal(self.settings).append(MISSED, base | {"reason": "relevé impossible dans les 30 minutes"},
                                                  now=now)
            return
        try:
            data = self.measure(task.symbol)
        except Exception as exc:  # noqa: BLE001 - Binance en panne, carnet illisible : tracé, jamais bloquant
            self._failed(task, base, exc)
            return
        self.failures = 0
        if task.trigger == SIGNAL:
            data["delay_s"] = round((pd.Timestamp(data["time"]) - pd.Timestamp(task.signal["received_at"]))
                                    .total_seconds(), 1)
        moment = self.clock()
        self._journal_for(task, moment).append(RECORD, base | data, now=moment)

    def _failed(self, task: Task, base: dict, exc: Exception) -> None:
        now = self.clock()
        status = getattr(exc, "status", None)
        self._journal_for(task, now).append(ERROR, base | {"error": f"{type(exc).__name__}: {exc}"[:300],
                                                           "status": status}, now=now)
        if status in (418, 429):                      # limite de Binance : on se retire longtemps
            self.backoff_until = self.monotonic() + BAN_BACKOFF_SECONDS
            self._abandon_cycle(now, f"HTTP {status} : relevé suspendu {BAN_BACKOFF_SECONDS:.0f} s")
            return
        if not isinstance(exc, HttpError) or (status is not None and status < 500):
            return                                    # paire inconnue, carnet vide… : la suite continue
        self.failures += 1
        if self.failures >= FAILURES_TO_ABANDON:
            self.backoff_until = self.monotonic() + OUTAGE_BACKOFF_SECONDS
            self._abandon_cycle(now, f"{self.failures} pannes de suite : Binance injoignable")
            self.failures = 0

    def _abandon_cycle(self, now, reason: str) -> None:
        dropped = [t for t in self.queue if t.trigger == PERIODIC]
        if not dropped:
            return
        self.queue = deque(t for t in self.queue if t.trigger != PERIODIC)
        periodic_journal(self.settings, now).append(MISSED, {"cycle": dropped[0].cycle,
                                                             "pairs": [t.symbol for t in dropped],
                                                             "reason": reason}, now=now)

    # boucle ---------------------------------------------------------------------------------------------
    def step(self) -> bool:
        """Une unité de travail ; vrai si une paire a été traitée."""
        mono = self.monotonic()
        if self.backoff_until is not None and mono < self.backoff_until:
            return False
        now = self.clock()
        if self.last_signal_check is None or mono - self.last_signal_check >= SIGNAL_CHECK_SECONDS:
            self.last_signal_check = mono
            for task in reversed(self.signal_tasks(now)):
                self.queue.appendleft(task)            # signaux d'abord, dans l'ordre de réception
        cycle = due_cycle(now, self.last_cycle)
        if cycle is not None:
            self.enqueue_cycle(cycle, now)
        if not self.queue:
            return False
        self.run_task(self.queue.popleft())
        return True

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                busy = self.step()
            except Exception:  # noqa: BLE001 - disque plein, journal verrouillé… : on réessaie plus tard
                log.exception("relevé de liquidité")
                busy = False
            if not busy:
                stop.wait(IDLE_SECONDS)

    def close(self) -> None:
        self.depth.close()
        self.rest.close()


_STATE: dict = {}
_START_LOCK = threading.Lock()


def start_background(settings: Settings, *, clock: Callable[[], datetime]) -> bool:
    """Démarre le fil du relevé s'il ne tourne pas (appelé par la surveillance toutes les 30 s ; jamais bloquant)."""
    if not settings.forward.liquidity_log:
        return False
    with _START_LOCK:
        thread = _STATE.get("thread")
        if thread is not None and thread.is_alive():
            return False
        worker = LiquidityWorker(settings, clock=clock)
        stop = threading.Event()
        thread = threading.Thread(target=worker.run, args=(stop,), name="csi-liquidite", daemon=True)
        _STATE.update(thread=thread, stop=stop, worker=worker)
        thread.start()
        return True


# --- lecture (API, tableau de bord) -------------------------------------------------------------------------
def describe(data: dict, size_usdt: float) -> dict:
    """Résumé d'un relevé pour l'affichage : écart, profondeur et déséquilibre, flux, contrôle d'entrée."""
    book, flow = data.get("book", {}), data.get("flow", {})
    depth = book.get("depth", {})
    window = {k: (flow.get(k) or {}) for k in ("15m", "60m")}
    return {"symbol": data.get("symbol"), "time": data.get("time"), "signal_id": data.get("signal_id"),
            "provider": data.get("provider"), "received_at": data.get("received_at"), "delay_s": data.get("delay_s"),
            "spread_pct": book.get("spread_pct"),
            "depth": {k: {"bid_usdt": v.get("bid_usdt"), "ask_usdt": v.get("ask_usdt"), "imbalance": v.get("imbalance"),
                          "truncated": v.get("truncated")} for k, v in depth.items()},
            "taker_buy_share": {k: v.get("taker_buy_share") for k, v in window.items()},
            "rel_volume": {k: v.get("rel_volume") for k, v in window.items()},
            "trades": {k: v.get("trades") for k, v in window.items()},
            "check": entry_check(book, size_usdt) if book.get("slippage") else None}


def summary(settings: Settings, *, now, size_usdt: float = DEFAULT_SIZE, limit: int = 20) -> dict:
    """Derniers relevés des signaux (du plus récent au plus ancien) et dernier relevé périodique de chaque paire."""
    journal = signals_journal(settings)
    recent: deque[dict] = deque(maxlen=max(1, limit))
    counts = {"signals": 0, "errors": 0, "missed": 0}
    for entry in journal.entries({RECORD, ERROR, MISSED}):
        if entry["kind"] == RECORD:
            counts["signals"] += 1
            recent.append(entry["data"])
        else:
            counts["errors" if entry["kind"] == ERROR else "missed"] += 1
    moment = pd.Timestamp(now)
    entries = tail_entries(periodic_path(settings, moment))
    if not any(e["kind"] == RECORD for e in entries):
        entries = tail_entries(periodic_path(settings, moment.replace(day=1) - pd.Timedelta(days=1))) + entries
    latest: dict[str, dict] = {}
    last_cycle = None
    for entry in entries:
        if entry["kind"] == RECORD:
            latest[entry["data"]["symbol"]] = entry["data"]
        elif entry["kind"] == CYCLE:
            last_cycle = entry["data"]["cycle"]
    return {"size_usdt": size_usdt, "signals": [describe(d, size_usdt) for d in reversed(recent)],
            "pairs": [describe(latest[s], size_usdt) for s in sorted(latest)], "counts": counts,
            "last_cycle": last_cycle, "enabled": settings.forward.liquidity_log,
            "rule": f"profondeur suffisante si écart < {MAX_SPREAD_PCT:g} % et glissement < {MAX_SLIPPAGE_PCT:g} % "
                    "à l'achat et à la vente au marché de la taille demandée (contre le meilleur prix)",
            "note": "Relevé en shadow (docs/LIQUIDITE.md) : information seulement, aucune influence sur les tests "
                    "en direct, les avis ni BinanceSpotManager. Aucun pouvoir prédictif n'est encore mesuré."}
