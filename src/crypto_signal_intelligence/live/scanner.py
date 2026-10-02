"""Cycles d'analyse aux clôtures de bougies, en mode shadow (points 1, 2, 3, 5, 6 et 21).

- `scan_cycle` : UN cycle sur l'univers et les stratégies, pour la clôture 15m la plus récente.
  Les bougies manquantes sont demandées en REST (jamais tout l'historique). Aux clôtures
  simultanées 15m/1h, la bougie 1h attendue (et celle de BTC) est attendue pendant un délai borné ;
  si elle manque encore, la paire est NO_TRADE REQUIRED_CONTEXT_UNAVAILABLE, sans analyse.
- `run_forever` : un cycle à chaque clôture 15m (plus un délai de grâce), jusqu'à l'arrêt ; un seul
  processus à la fois (verrou d'instance). Après un redémarrage, seule la dernière bougie close est
  analysée : les bougies rattrapées reconstruisent l'état des indicateurs sans jamais produire de
  signal périmé (fenêtre d'entrée close → NO_TRADE EXPIRED ; même setup → DUPLICATE).
Aucune exécution d'ordre. Un signal n'est publié que dans le dossier autorisé par la configuration
(shadow tant que l'intégration n'est pas vérifiée).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..data.http import PublicHttpClient
from ..data.pipeline import download
from ..data.schema import interval
from ..data.store import CandleStore
from ..domain.enums import Action, NoTradeReason
from ..features.loader import MARKET_CONTEXT_SYMBOL, MissingData, load_inputs
from ..signals.analyze import AnalysisOutcome, analyze
from ..signals.outbox import SignalRegistry
from ..strategies import registry
from .backup import publication_suspended
from .lock import InstanceLock

log = logging.getLogger(__name__)
Downloader = Callable[..., object]


def last_close(now: datetime, step: timedelta) -> datetime:
    """Clôture la plus récente <= now (les bougies sont alignées sur l'époque Unix, en UTC)."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + ((now - epoch) // step) * step


def next_close(now: datetime, step: timedelta) -> datetime:
    return last_close(now, step) + step


@dataclass
class CycleReport:
    decision_close: datetime
    started_at: datetime
    finished_at: datetime | None = None
    outcomes: list[AnalysisOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing_after_wait: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return dict(Counter(o.action if o.action == Action.BUY.value else f"NO_TRADE:{o.reason_code}"
                            for o in self.outcomes))

    def published(self) -> list[AnalysisOutcome]:
        return [o for o in self.outcomes if o.signal_id]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["counts"] = self.counts()
        return json.loads(json.dumps(data, default=str))


def _latest_close(store: CandleStore, symbol: str, timeframe: str) -> datetime | None:
    last = store.last_open_time(symbol, timeframe)
    return None if last is None else (last + interval(timeframe)).to_pydatetime()


def scan_cycle(settings: Settings, *, now: datetime, decision_close: datetime | None = None,
               symbols: list[str] | None = None, strategies: list[str] | None = None,
               downloader: Downloader = download, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
               sleep: Callable[[float], None] = time.sleep, refresh: bool = True) -> CycleReport:
    # Paires ajoutées par le propriétaire (prêtes) : rafraîchies à chaque cycle pour l'évaluation et la
    # résolution des signaux externes, mais ni attendues ni analysées par les stratégies.
    passengers: list[str] = []
    if symbols is None:
        from ..external.universe import UserUniverse
        passengers = [s for s in UserUniverse(settings.external_db).ready_symbols() if s not in settings.data.symbols]
    symbols = symbols or list(settings.data.symbols)
    strategies = strategies or list(registry.STRATEGIES)
    setup_tf, context_tf = settings.data.setup_timeframe, settings.data.context_timeframe
    decision_close = decision_close or last_close(now, interval(setup_tf))
    context_close = last_close(decision_close, interval(context_tf))
    report = CycleReport(decision_close=decision_close, started_at=now)
    store = CandleStore(settings.data_dir)
    expected = {(s, setup_tf): decision_close for s in symbols}
    expected |= {(s, context_tf): context_close for s in [*symbols, MARKET_CONTEXT_SYMBOL]}

    def missing() -> list[tuple[str, str]]:
        return [(s, tf) for (s, tf), close in expected.items()
                if (latest := _latest_close(store, s, tf)) is None or latest < close]

    # 1. Données : seulement les bougies manquantes, attente bornée des clôtures attendues.
    #    Une connexion HTTP réutilisée (pas de poignée TLS par série) et quelques fils en parallèle.
    started = time.perf_counter()
    todo = sorted(expected) + [(s, tf) for s in passengers for tf in (setup_tf, context_tf)]
    deadline = clock() + timedelta(seconds=settings.live.candle_wait_seconds)
    client = PublicHttpClient.rest(settings.data.rest_base_url) if refresh and downloader is download else None

    def fetch_one(item: tuple[str, str]) -> str | None:
        symbol, timeframe = item
        try:
            extra = {"rest_client": client} if client is not None else {}
            downloader(settings, symbol, timeframe, now=clock(), rest_only=True, **extra)
            return None
        except Exception as exc:  # noqa: BLE001 - une paire en échec ne bloque pas les autres
            return f"{symbol} {timeframe} : {type(exc).__name__}: {exc}"

    try:
        with ThreadPoolExecutor(max_workers=settings.live.refresh_workers) as pool:
            while refresh and todo:
                report.errors.extend(e for e in pool.map(fetch_one, todo) if e)
                todo = missing()
                if not todo or clock() >= deadline:
                    break
                sleep(settings.live.retry_seconds)
    finally:
        if client is not None:
            client.close()
    report.timings["data_s"] = round(time.perf_counter() - started, 3)
    absent = set(missing())
    report.missing_after_wait = [f"{s} {tf}" for s, tf in sorted(absent)]
    btc_context_missing = (MARKET_CONTEXT_SYMBOL, context_tf) in absent

    # 2. Analyse : données chargées une fois par paire, partagées entre stratégies.
    started = time.perf_counter()
    for symbol in symbols:
        if btc_context_missing or (symbol, context_tf) in absent:
            which = "BTC" if btc_context_missing else symbol
            for strategy_id in strategies:
                report.outcomes.append(AnalysisOutcome(
                    symbol, strategy_id, None, Action.NO_TRADE.value, NoTradeReason.REQUIRED_CONTEXT_UNAVAILABLE.value,
                    (f"bougie 1h {which} clôturée à {context_close:%H:%M} absente après attente",)))
            continue
        try:
            inputs = load_inputs(settings, symbol, setup_tail=settings.live.setup_tail_bars,
                                 context_tail=settings.live.context_tail_bars)
        except MissingData as exc:
            report.errors.append(str(exc))
            continue
        for strategy_id in strategies:
            try:
                report.outcomes.append(analyze(settings, symbol, strategy_id, now=clock(), inputs=inputs,
                                               expected_decision_time=decision_close))
            except Exception as exc:  # noqa: BLE001 - erreur isolée, jamais silencieuse
                log.exception("analyse %s %s", symbol, strategy_id)
                report.errors.append(f"{symbol} {strategy_id} : {type(exc).__name__}: {exc}")
    report.timings["analysis_s"] = round(time.perf_counter() - started, 3)
    report.finished_at = clock()
    report.timings["publication_delay_s"] = round((report.finished_at - decision_close).total_seconds(), 1)
    return report


def _resolve_external_signals(settings: Settings, *, now: datetime) -> dict:
    from ..external.registry import ExternalSignalRegistry, resolve_pending
    return resolve_pending(settings, ExternalSignalRegistry(settings.external_db), now=now)


class UserPairWorker:
    """Télécharge l'historique des paires ajoutées par le propriétaire dans un fil SÉPARÉ (point 4).

    Le téléchargement complet d'une paire (archives 15m + 1h) dure plusieurs minutes : fait dans la
    boucle de surveillance, il lui ferait manquer des clôtures. Ici, la boucle n'attend jamais ; les
    fusions de séries restent sérialisées par le verrou du pipeline (une à la fois, mémoire).
    """

    def __init__(self, settings: Settings, *, downloader: Downloader, clock: Callable[[], datetime],
                 interval_seconds: float = 30.0):
        self.settings, self.downloader, self.clock = settings, downloader, clock
        self.interval_seconds = interval_seconds
        self.last: object = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> object:
        from ..external.universe import download_pending
        try:
            result: object = download_pending(self.settings, now=self.clock(), downloader=self.downloader)
        except Exception as exc:  # noqa: BLE001 - jamais bloquant, toujours tracé
            log.exception("téléchargement des paires ajoutées")
            result = {"error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict) or result.get("processed") or result.get("error"):
            self.last = result                       # on garde le dernier téléchargement réel, pas les tours à vide
        try:                                         # volatilité prévue du jour (information, jamais bloquante)
            from ..outlook.volatility import ensure
            ensure(self.settings, now=self.clock())
        except Exception:  # noqa: BLE001
            log.exception("volatilité prévue")
        try:                                         # suivi en direct des plans (fil séparé, une fois par jour)
            from ..outlook.tracking import start_daily
            start_daily(self.settings, now=self.clock())
        except Exception:  # noqa: BLE001
            log.exception("suivi des plans")
        try:                                         # issue des signaux shadow de CSI (rejeu sur les bougies stockées)
            from ..signals.outcomes import resolve as resolve_generated
            resolve_generated(self.settings, now=self.clock())
        except Exception:  # noqa: BLE001
            log.exception("issue des signaux shadow")
        try:                                         # tests en direct pré-inscrits (fil séparé, au plus une fois par heure)
            from ..forward.runner import start_background
            start_background(self.settings, now=self.clock())
        except Exception:  # noqa: BLE001
            log.exception("tests en direct")
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="csi-user-pairs", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)


def write_status(path: Path, report: CycleReport, *, cycles: int, started_at: datetime,
                 news: dict | None = None, external: object = None, universe: object = None) -> None:
    """État de santé lisible (dernière analyse, décomptes, publications, erreurs, durées, news, résolution)."""
    status = {"pid_started_at": started_at.isoformat(), "cycles": cycles, "last_cycle": report.to_dict(),
              "last_news_collection": news, "last_external_resolution": external,
              "last_universe_download": universe,
              "published": [{"symbol": o.symbol, "strategy": o.strategy, "signal_id": o.signal_id,
                             "path": o.signal_path} for o in report.published()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _refresh_dashboard(settings: Settings, now: datetime) -> None:
    """Tableau de bord HTML ; une erreur d'affichage ne bloque jamais la surveillance."""
    try:
        from ..reporting.dashboard import write_dashboard
        write_dashboard(settings, now=now)
    except Exception:  # noqa: BLE001
        log.exception("tableau de bord")


def run_forever(settings: Settings, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                sleep: Callable[[float], None] = time.sleep, should_stop: Callable[[], bool] = lambda: False,
                max_cycles: int | None = None, on_cycle: Callable[[CycleReport], None] | None = None,
                downloader: Downloader = download,
                news_collector: Callable[..., object] | None = None,
                signal_resolver: Callable[..., object] | None = None,
                user_pair_worker: UserPairWorker | None = None) -> int:
    """Surveillance jusqu'à l'arrêt (Ctrl+C) ou `max_cycles`. Retourne le nombre de cycles effectués.

    Les actualités (mode observe) sont collectées APRÈS l'analyse de chaque cycle, quand l'intervalle
    configuré est écoulé : une source lente ou en panne ne retarde jamais l'analyse des bougies.
    Les paires ajoutées par le propriétaire (external/universe.py) sont téléchargées par un fil séparé
    (UserPairWorker) : la boucle ne l'attend jamais.
    """
    worker = user_pair_worker or UserPairWorker(settings, downloader=downloader, clock=clock)
    if news_collector is None and settings.news.mode != "off":
        from ..news.collector import collect as news_collector
    if signal_resolver is None:
        signal_resolver = _resolve_external_signals
    started_at = clock()
    with InstanceLock(settings.root / settings.live.lock_file):
        # Reprise : publications interrompues terminées, .tmp orphelins supprimés, registre conservé.
        counts = SignalRegistry(settings.signals_db, settings.publication_dir()).reconcile(
            publish=publication_suspended(settings) is None, now=clock())
        log.info("démarrage : réconciliation du registre %s", counts)
        _refresh_dashboard(settings, clock())       # visible dès le démarrage (« pas prêt » avant le 1er cycle)
        worker.start()
        try:
            return _cycles(settings, clock=clock, sleep=sleep, should_stop=should_stop, max_cycles=max_cycles,
                           on_cycle=on_cycle, downloader=downloader, news_collector=news_collector,
                           signal_resolver=signal_resolver, worker=worker, started_at=started_at)
        finally:
            worker.stop()


def _cycles(settings: Settings, *, clock: Callable[[], datetime], sleep: Callable[[float], None],
            should_stop: Callable[[], bool], max_cycles: int | None, on_cycle: Callable[[CycleReport], None] | None,
            downloader: Downloader, news_collector: Callable[..., object] | None,
            signal_resolver: Callable[..., object], worker: UserPairWorker, started_at: datetime) -> int:
    last_news: datetime | None = None
    step = interval(settings.data.setup_timeframe)
    grace = timedelta(seconds=settings.live.grace_seconds)
    cycles = 0
    while not should_stop():
        target = next_close(clock() - grace, step) + grace
        while (remaining := (target - clock()).total_seconds()) > 0:
            if should_stop():
                return cycles
            sleep(min(remaining, 1.0))
        try:      # publication restée PENDING (fichier verrouillé…) : terminée sans attendre un redémarrage
            SignalRegistry(settings.signals_db, settings.publication_dir()).reconcile(
                publish=publication_suspended(settings) is None, now=clock())
        except Exception:  # noqa: BLE001 - la réconciliation n'arrête jamais la surveillance
            log.exception("réconciliation du registre")
        report = scan_cycle(settings, now=clock(), decision_close=target - grace, downloader=downloader,
                            clock=clock, sleep=sleep)
        cycles += 1
        news_note = None
        if news_collector is not None and (last_news is None or clock() - last_news
                                           >= timedelta(minutes=settings.news.collect_every_minutes)):
            try:
                collected = news_collector(settings, now=clock())
                news_note = {"new": getattr(collected, "new", None), "failed": getattr(collected, "failed", None)}
            except Exception as exc:  # noqa: BLE001 - les news n'arrêtent jamais la surveillance
                log.exception("collecte des actualités")
                news_note = {"error": f"{type(exc).__name__}: {exc}"}
            last_news = clock()
        # Signaux Telegram évalués : résolus avec les bougies déjà stockées (aucun appel réseau).
        try:
            resolution = signal_resolver(settings, now=clock())
        except Exception as exc:  # noqa: BLE001 - la résolution n'arrête jamais la surveillance
            log.exception("résolution des signaux externes")
            resolution = {"error": f"{type(exc).__name__}: {exc}"}
        write_status(settings.root / settings.live.status_file, report, cycles=cycles, started_at=started_at,
                     news=news_note, external=resolution, universe=worker.last)
        log.info("cycle %s : %s ; publiés %s ; erreurs %s ; durées %s", report.decision_close.isoformat(),
                 report.counts(), len(report.published()), len(report.errors), report.timings)
        _refresh_dashboard(settings, clock())
        if on_cycle:
            on_cycle(report)
        if max_cycles is not None and cycles >= max_cycles:
            break
    return cycles

