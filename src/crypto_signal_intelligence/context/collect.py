"""Téléchargement de l'historique (une fois, `csi context-backfill`) et relevé quotidien (surveillance, une fois par
jour après 03:00 UTC) des données de contexte (docs/CONTEXTE.md). Une source en échec n'arrête pas les autres ; le
relevé du jour reprend au passage suivant les seules séries qui ont échoué."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import pandas as pd

from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..forward.sources import PublicSources, SourceError
from . import fetch
from .store import HISTORY, OBSERVED, context_dir, locked, rows, upsert

RECORD_AFTER = pd.Timedelta(hours=3)                 # CoinMetrics publie la veille vers 02-03 h UTC
LISTING_REFRESH = pd.Timedelta(days=7)
HISTORY_START = pd.Timestamp("2017-01-01", tz="UTC")
RATIO_ARCHIVE_START = pd.Timestamp("2020-09-01", tz="UTC")
EXTRA_SPOT = ("ETHBTC", "PAXGUSDT")                  # ratio ETH/BTC et or tokenisé
SOURCES = {"flows": "CoinMetrics community (CC BY-NC, attribution)", "market_global": "CoinGecko (attribution)",
           "market_categories": "CoinGecko (attribution)", "options": "Deribit public", "basis": "Binance futures public",
           "ratios": "Binance futures public", "ratios_archive": "Binance archives publiques",
           "binance_daily": "Binance spot public", "coinbase": "Coinbase Exchange public", "upbit": "Upbit public",
           "ecb": "BCE", "protocols": "DefiLlama", "protocols_tvl": "DefiLlama", "stablecoins": "DefiLlama",
           "fred": "FRED (St. Louis Fed ; SP500 sous licence S&P)", "gold_gld": "Nasdaq (ETF GLD)",
           "macro": "Trésor américain (10 ans), Fed H.6 (M2), Nasdaq (ETF SPY)",
           "wikipedia": "Wikimedia pageviews"}


class Clients:
    def __init__(self, settings: Settings, *, web: PublicSources | None = None, futures: PublicHttpClient | None = None,
                 archives: PublicHttpClient | None = None, spot: PublicHttpClient | None = None):
        self.web = web or PublicSources()
        self.futures = futures or PublicHttpClient.futures_rest(settings.derivatives.rest_base_url)
        self.archives = archives or PublicHttpClient.futures_archives(settings.data.archive_base_url)
        self.spot = spot or PublicHttpClient.rest(settings.data.rest_base_url)


def _state_path(settings: Settings):
    return context_dir(settings) / "_journal.json"


def load_state(settings: Settings) -> dict:
    path = _state_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": {}, "listed": {}}


def save_state(settings: Settings, state: dict) -> None:
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def halal_symbols(settings: Settings) -> list[str]:
    from ..forward.halal import admitted
    return sorted(admitted(settings).symbols)


def _bases(symbols: list[str]) -> list[str]:
    return sorted({s[:-4] for s in symbols if s.endswith("USDT")})


def _write(settings: Settings, series: str, records: list[dict], *, kind: str, now: datetime,
           since: pd.Timestamp | None = None) -> int:
    frame = rows(records, kind=kind, source=SOURCES[series], now=now)
    if since is not None and not frame.empty:
        frame = frame[frame["date"] >= since]
    return upsert(settings, series, frame)


def _per_key(keys: list[str], read: Callable[[str], list[dict]]) -> tuple[list[dict], list[str], dict[str, str]]:
    """Lit une série clé par clé : (enregistrements, clés présentes, erreurs par clé). « HTTP 404 / 400 » = absente."""
    records, present, errors = [], [], {}
    for key in keys:
        try:
            got = read(key)
        except (SourceError, HttpError) as exc:
            text = str(exc)
            if "404" not in text and "400" not in text:
                errors[key] = text[:120]
            continue
        if got:
            records += got
            present.append(key)
    return records, present, errors


def _jobs(settings: Settings, clients: Clients, *, now: datetime, history: bool, listed: dict) -> dict[str, Callable]:
    """Une fonction par série ; `history` : tout l'historique gratuit, sinon les derniers jours seulement."""
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    recent = day - pd.Timedelta(days=10)
    start = HISTORY_START if history else recent
    symbols = halal_symbols(settings)
    bases = _bases(symbols)

    def per_pair(series: str, keys: list[str], read: Callable[[str], list[dict]]) -> dict:
        records, present, errors = _per_key(keys, read)
        listed[series] = {"checked": str(day.date()), "keys": present}
        return {"records": records, "present": len(present), "errors": errors}

    def known(series: str, keys: list[str]) -> list[str]:
        """Au relevé quotidien, seules les clés trouvées au dernier recensement (refait chaque semaine)."""
        info = listed.get(series)
        if history or not info or moment - pd.Timestamp(info["checked"], tz="UTC") > LISTING_REFRESH:
            return keys
        return [k for k in keys if k in set(info["keys"])]

    return {
        "flows": lambda: {"records": fetch.coinmetrics_flows(clients.web, start=str(start.date()))},
        "market_global": lambda: {"records": fetch.coingecko_global(clients.web, day=day)},
        "market_categories": lambda: {"records": fetch.coingecko_categories(clients.web, day=day)},
        "options": lambda: {"records": fetch.deribit_options(clients.web, "BTC", now=now)
                            + fetch.deribit_options(clients.web, "ETH", now=now)},
        "basis": lambda: {"records": [r for p in fetch.BASIS_PAIRS for c in fetch.BASIS_CONTRACTS
                                      for r in fetch.binance_basis(clients.futures, p, c, limit=500 if history else 5)]},
        "ratios": lambda: per_pair("ratios", known("ratios", symbols),
                                   lambda s: fetch.binance_ratios(clients.futures, s, limit=30 if history else 3)),
        "binance_daily": lambda: per_pair("binance_daily", known("binance_daily", [*symbols, *EXTRA_SPOT]),
                                          lambda s: fetch.binance_daily(clients.spot, s, start=start)),
        "coinbase": lambda: per_pair("coinbase", known("coinbase", bases),
                                     lambda b: fetch.coinbase_daily(clients.web, b, start=start, end=day)),
        "upbit": lambda: per_pair("upbit", known("upbit", bases),
                                  lambda b: fetch.upbit_daily(clients.web, b, start=start, end=moment)),
        "ecb": lambda: {"records": fetch.ecb_rates(clients.web, start=str(start.date()))},
        "protocols": lambda: per_pair("protocols", list(fetch.DEFILLAMA),
                                      lambda t: fetch.defillama_revenue(clients.web, t, fetch.DEFILLAMA[t])),
        "protocols_tvl": lambda: per_pair("protocols_tvl", list(fetch.DEFILLAMA),
                                          lambda t: fetch.defillama_tvl(clients.web, t, fetch.DEFILLAMA[t], day=day)),
        "stablecoins": lambda: {"records": fetch.stablecoin_supply(clients.web)},
        # FRED (fredgraph.csv) ne répond plus depuis le 2026-10-02 : remplaçants validés le 2026-10-04.
        "macro": lambda: {"records": [r for y in range(1990 if history else day.year, day.year + 1)
                                      for r in fetch.treasury_10y(clients.web, y)]
                          + fetch.fed_m2(clients.web, last=None if history else 12)
                          + fetch.nasdaq_etf(clients.web, "SPY", start=str((day - pd.Timedelta(days=3650 if history else 10)).date()),
                                             end=str((day - pd.Timedelta(days=1)).date()))},
        "gold_gld": lambda: {"records": fetch.nasdaq_gld(clients.web, start=str((HISTORY_START if history else recent).date()),
                                                         end=str((day - pd.Timedelta(days=1)).date()))},   # Nasdaq : fin = veille
        "wikipedia": lambda: {"records": [r for a in fetch.WIKI_ARTICLES
                                          for r in fetch.wikipedia_views(clients.web, a, start="20150701" if history else recent.strftime("%Y%m%d"),
                                                                         end=day.strftime("%Y%m%d"))]},
    }


SNAPSHOTS = ("market_global", "market_categories", "options", "protocols_tvl")   # aucun historique gratuit


def _run(settings: Settings, names: list[str], *, now: datetime, history: bool, clients: Clients,
         say: Callable[[str], None]) -> dict:
    state = load_state(settings)
    jobs = _jobs(settings, clients, now=now, history=history, listed=state.setdefault("listed", {}))
    recent = pd.Timestamp(now).floor("D") - pd.Timedelta(days=10)
    out: dict = {}
    for name in names:
        say(name)
        try:
            result = jobs[name]()
        except Exception as exc:  # noqa: BLE001 - une source en panne n'arrête pas les autres
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        kind = HISTORY if history and name not in SNAPSHOTS else OBSERVED
        written = _write(settings, name, result["records"], kind=kind, now=now,
                         since=None if kind == HISTORY or name in SNAPSHOTS else recent)
        out[name] = {"rows": written, **{k: v for k, v in result.items() if k != "records"}}
    with locked(settings):                    # relu juste avant d'écrire : ne jamais écraser le journal d'un autre
        fresh = load_state(settings)
        fresh.setdefault("listed", {}).update(state.get("listed", {}))
        save_state(settings, fresh)
    return out


def backfill(settings: Settings, *, now: datetime, only: list[str] | None = None, archives: bool = True,
             clients: Clients | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Télécharge tout l'historique gratuit (lignes HISTORIQUE), plus un premier relevé des séries sans historique,
    et les archives de ratios de BTC et ETH depuis 2020-09 (une archive par jour)."""
    say = progress or (lambda _text: None)
    clients = clients or Clients(settings)
    names = only or list(_jobs(settings, clients, now=now, history=True, listed={}))
    out = _run(settings, names, now=now, history=True, clients=clients, say=say)
    if archives and (only is None or "ratios_archive" in only):
        records: list[dict] = []
        end = pd.Timestamp(now).floor("D") - pd.Timedelta(days=1)
        for symbol in fetch.BASIS_PAIRS:
            for day in pd.date_range(RATIO_ARCHIVE_START, end, freq="D"):
                if day.day == 1:
                    say(f"archives {symbol} {day.date()}")
                try:
                    records += fetch.binance_metrics_archive(clients.archives, symbol, day.date())
                except (HttpError, ValueError, KeyError) as exc:
                    out.setdefault("ratios_archive_errors", []).append(f"{symbol} {day.date()} : {exc}"[:120])
        out["ratios_archive"] = {"rows": _write(settings, "ratios_archive", records, kind=HISTORY, now=now)}
    return out


def record_day(settings: Settings, *, now: datetime, clients: Clients | None = None) -> dict | None:
    """Relevé quotidien (lignes RELEVE : valeurs vues ce jour-là) ; une fois par jour après 03:00 UTC ; seules les
    séries pas encore réussies aujourd'hui sont relues."""
    moment = pd.Timestamp(now)
    if moment - moment.floor("D") < RECORD_AFTER:
        return None
    day = str(moment.date())
    state = load_state(settings)
    done = set(state.get("days", {}).get(day, {}).get("done", []))
    clients = clients or Clients(settings)
    pending = [n for n in _jobs(settings, clients, now=now, history=False, listed={}) if n not in done]
    if not pending:
        return None
    out = _run(settings, pending, now=now, history=False, clients=clients, say=lambda _t: None)
    with locked(settings):
        state = load_state(settings)
        entry = state.setdefault("days", {}).setdefault(day, {"done": [], "errors": {}})
        entry["done"] = sorted(set(entry["done"]) | {n for n, r in out.items() if "error" not in r})
        entry["errors"] = {n: r["error"] for n, r in out.items() if "error" in r}
        state["days"] = dict(sorted(state["days"].items())[-60:])
        save_state(settings, state)
    return {"done": len(entry["done"]), "errors": entry["errors"]}
