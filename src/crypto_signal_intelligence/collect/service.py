"""Service `csi collecteur` : les cinq sources dans des tâches asyncio, sous un verrou d'instance, à basse priorité.

Chaque source tourne sous un superviseur : une coupure (flux fermé, panne réseau, exception) est inscrite dans
l'état (`C_ETAT.json`, sans secret : CSI n'en a aucun) puis la source est relancée après une attente exponentielle
de 1 s à 60 s, remise à 1 s après `STABLE_SECONDS` de fonctionnement. Une source qui ne peut pas démarrer (dépendance
absente, URL refusée) ne casse pas les autres : elle est marquée `NON_DISPONIBLE`.

Arrêt : SIGTERM/SIGINT → `stop` posé, tâches annulées, état écrit une dernière fois (`ARRETE`), verrou libéré.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from ..config import Settings
from ..forward.journal import utc_iso
from ..live.lock import InstanceLock
from ..live.priority import lower_priority
from . import attention, carnet, flux, liquidations, options
from .base import (
    ATTENTION,
    CARNET,
    FLUX,
    LIQUIDATIONS,
    LOCK_FILE,
    MUTE,
    OPTIONS,
    RECONNECTING,
    RELOADING,
    STOPPED,
    UNAVAILABLE,
    Context,
    Recorder,
    Reload,
    Silent,
    State,
)
from .net import CollectHttp, NetError, RefusedUrl, ws_messages

log = logging.getLogger("csi.collect.service")

BACKOFF_FIRST, BACKOFF_MAX = 1.0, 60.0
STABLE_SECONDS = 300.0
STATE_WRITE_SECONDS = 30.0
SILENCE_RETRY_SECONDS = 3600.0
RUNNERS: Mapping[str, Callable] = {LIQUIDATIONS: liquidations.run, CARNET: carnet.run, FLUX: flux.run,
                                   OPTIONS: options.run, ATTENTION: attention.run}


def backoff_delays(first: float | None = None, maximum: float | None = None):
    """1, 2, 4, … plafonné à 60 s (générateur ; `send(True)` remet à la première valeur)."""
    first = BACKOFF_FIRST if first is None else first
    maximum = BACKOFF_MAX if maximum is None else maximum
    delay = first
    while True:
        reset = yield delay
        delay = first if reset else min(delay * 2, maximum)


async def supervise(name: str, ctx: Context, runner: Callable, *, max_runs: int | None = None) -> None:
    """Relance `runner(ctx)` jusqu'à l'arrêt ; attente exponentielle entre deux essais."""
    delays = backoff_delays()
    delay = next(delays)
    runs = 0
    while not ctx.stop.is_set():
        if max_runs is not None and runs >= max_runs:
            return
        runs += 1
        started = ctx.monotonic()
        try:
            await runner(ctx)
            if ctx.stop.is_set():
                break
            ctx.state.error(name, "flux terminé, reconnexion", status=RECONNECTING)
        except asyncio.CancelledError:                      # arrêt demandé : l'annulation interrompt `async for`
            ctx.state.touch(name, status=STOPPED)
            raise
        except Reload as exc:                               # pas une erreur : relance immédiate
            ctx.state.touch(name, status=RELOADING, detail=str(exc))
            ctx.state.write()
            continue
        except Silent as exc:                               # pas une panne : MUET, réessai une fois par heure
            source = ctx.state.sources[name]
            source["silences"] = source.get("silences", 0) + 1
            ctx.state.touch(name, status=MUTE, last_silence_at=utc_iso(ctx.clock()), next_retry_s=SILENCE_RETRY_SECONDS,
                            detail=f"{exc} : flux dérivés probablement bloqués depuis ce réseau (à vérifier sur le VPS) ; "
                                   f"réessai toutes les heures")
            ctx.state.write(force=True)
            await ctx.pause(SILENCE_RETRY_SECONDS)
            continue
        except (RefusedUrl, ImportError) as exc:            # ne se répare pas tout seul : on n'insiste pas
            ctx.state.error(name, exc, status=UNAVAILABLE)
            ctx.state.write(force=True)
            return
        except NetError as exc:
            ctx.state.error(name, exc, status=RECONNECTING)
        except Exception as exc:  # noqa: BLE001 - une source ne doit jamais faire tomber les autres
            log.exception("collecteur %s", name)
            ctx.state.error(name, exc, status=RECONNECTING)
        if ctx.monotonic() - started >= STABLE_SECONDS:
            delay = delays.send(True)
        ctx.state.sources[name]["reconnections"] += 1
        ctx.state.sources[name]["next_retry_s"] = delay
        ctx.state.write()
        try:
            await asyncio.wait_for(ctx.stop.wait(), timeout=delay)
            break
        except TimeoutError:
            pass
        delay = delays.send(False)


async def state_writer(ctx: Context) -> None:
    """Réécrit l'état au moins toutes les `STATE_WRITE_SECONDS` (le contrôle de santé regarde sa fraîcheur)."""
    while not ctx.stop.is_set():
        ctx.state.write(force=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.stop.wait(), timeout=STATE_WRITE_SECONDS)


def build_context(settings: Settings, *, clock: Callable[[], datetime] | None = None, http: CollectHttp | None = None,
                  stream: Callable | None = None, sources: tuple[str, ...] | None = None) -> Context:
    clock = clock or (lambda: datetime.now(UTC))
    state = State(settings, clock=clock)
    ctx = Context(settings, Recorder(settings, state), state, clock=clock, http=http or CollectHttp(),
                  stream=stream or ws_messages)
    for name in set(RUNNERS) - set(sources or RUNNERS):
        state.touch(name, status=UNAVAILABLE, detail="source non lancée (sélection)")
    return ctx


async def run_all(ctx: Context, *, sources: tuple[str, ...] | None = None, max_runs: int | None = None) -> None:
    """Toutes les sources jusqu'à `stop` (ou jusqu'à leur fin avec `max_runs`) ; l'arrêt annule les tâches, qui
    interrompent leurs flux et leurs attentes, puis l'état est écrit `ARRETE`."""
    names = sources or tuple(RUNNERS)
    tasks = [asyncio.create_task(supervise(n, ctx, RUNNERS[n], max_runs=max_runs), name=f"collect-{n}") for n in names]
    tasks.append(asyncio.create_task(state_writer(ctx), name="collect-etat"))
    work: asyncio.Future = asyncio.ensure_future(asyncio.gather(*tasks[:-1]))
    stopper: asyncio.Future = asyncio.create_task(ctx.stop.wait(), name="collect-stop")
    try:
        await asyncio.wait({work, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        ctx.stop.set()
        for task in [*tasks, stopper]:
            task.cancel()
        await asyncio.gather(*tasks, stopper, work, return_exceptions=True)
        for name in names:
            ctx.state.touch(name, status=STOPPED)
        ctx.state.write(force=True)


def serve(settings: Settings, *, sources: tuple[str, ...] | None = None) -> dict:
    """Point d'entrée du CLI : verrou, basse priorité, boucle asyncio jusqu'à SIGTERM/SIGINT."""
    with InstanceLock(settings.root / LOCK_FILE):
        lowered = lower_priority()
        ctx = build_context(settings, sources=sources)
        ctx.state.info["priority_lowered"] = lowered

        async def main() -> None:
            loop = asyncio.get_running_loop()
            for name in ("SIGTERM", "SIGINT"):
                if hasattr(signal, name):
                    with contextlib.suppress(NotImplementedError, RuntimeError):
                        loop.add_signal_handler(getattr(signal, name), ctx.stop.set)
            await run_all(ctx, sources=sources)

        try:
            asyncio.run(main())
        finally:
            if ctx.http is not None:
                ctx.http.close()
        return ctx.state.payload()
