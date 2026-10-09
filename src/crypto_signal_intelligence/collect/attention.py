"""Source ATTENTION : attention publique, sans clé, sans contenu stocké (comptes et symboles seulement).

- **Wikipédia** (API REST publique de Wikimedia, pages vues par les lecteurs humains), une fois par **jour** après
  06:00 UTC pour la veille : une page par base de la configuration quand elle existe (table figée `PAGES`, une page
  inconnue de Wikipédia est ignorée et listée dans `missing`), plus `Cryptocurrency`. User-Agent descriptif
  (exigé par Wikimedia), une demande par page, espacées de `REQUEST_GAP_SECONDS` (5 s), une reprise après 90 s sur « HTTP 429 ».
  Entrée `ATTENTION_WIKI_J` : `{day, views: {base: n}, total, missing: [...], errors: {...}}`.
- **CoinGecko « trending »** (`/api/v3/search/trending`, public, sans clé, ≈ 10-30 demandes/min autorisées : une
  par **heure** ici ; rien d'autre de CoinGecko). Entrée `ATTENTION_TRENDING_H` : `{hour, coins: [symbole, …]
  (15 au plus, dans l'ordre de CoinGecko), in_universe: [...]}`.
- **Reddit : NON_DISPONIBLE** (connexion exigée depuis 2026 : `www`, `api` et `old.reddit.com` répondent 403 ou
  renvoient vers la page de connexion ; constaté depuis cette machine le 2026-10-09) ; retiré de la liste fermée.
- **Google Trends : NON_DISPONIBLE** (pytrends : API non officielle instable, appels hors du client à liste fermée).

Entrées du journal `C_ATTENTION-AAAA-MM.jsonl` : `ATTENTION_WIKI_J`, `ATTENTION_TRENDING_H`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import pandas as pd

from ..config import Settings
from ..forward.journal import utc_iso
from .base import ATTENTION, UNAVAILABLE, Context, journal
from .net import CollectHttp

log = logging.getLogger("csi.collect.attention")

WIKI, TRENDING = "ATTENTION_WIKI_J", "ATTENTION_TRENDING_H"
WIKI_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
REQUEST_GAP_SECONDS = 5.0                # Wikimedia limite les clients anonymes : 17 pages en ≈ 1,5 min
RETRY_429_SECONDS = 90.0                 # une seule reprise par page après « HTTP 429 »
WIKI_AFTER = pd.Timedelta(hours=6)                   # relevé de la veille après 06:00 UTC (données du jour complètes)
OFFSET = pd.Timedelta(minutes=2)                     # passage horaire à hh:02
MAX_TRENDING = 15
REDDIT_REASON = "Reddit NON_DISPONIBLE (connexion exigée depuis 2026)"
TRENDS_REASON = "Google Trends NON_DISPONIBLE (pytrends : API non officielle, appels hors du client à liste fermée)"
#: Table FIGÉE base ↔ page Wikipédia (en.wikipedia) ; une page absente est ignorée (404 → `missing`).
PAGES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana_(blockchain_platform)", "XRP": "XRP_Ledger",
         "NEAR": "NEAR_Protocol", "AVAX": "Avalanche_(blockchain_platform)", "HBAR": "Hedera_(distributed_ledger)",
         "LINK": "Chainlink_(blockchain)", "XLM": "Stellar_(payment_network)", "ADA": "Cardano_(blockchain_platform)",
         "TRX": "Tron_(blockchain)", "FIL": "Filecoin", "ALGO": "Algorand_(blockchain_platform)",
         "DOT": "Polkadot_(cryptocurrency)", "ATOM": "Cosmos_(blockchain)", "ETC": "Ethereum_Classic",
         "DOGE": "Dogecoin", "CRYPTO": "Cryptocurrency"}


def bases(settings: Settings) -> list[str]:
    return list(dict.fromkeys(s[:-4] for s in settings.data.symbols if s.endswith("USDT")))


def wiki_pages(settings: Settings) -> dict[str, str]:
    """Pages à relever : les bases de la configuration présentes dans `PAGES`, plus `CRYPTO` (Cryptocurrency)."""
    return {b: PAGES[b] for b in [*bases(settings), "CRYPTO"] if b in PAGES}


def parse_views(payload) -> int:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("réponse Wikimedia sans « items »")
    return int(items[-1]["views"])


def wiki_day(http: CollectHttp, settings: Settings, *, day: pd.Timestamp,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Pages vues de la journée `day` (UTC) pour chaque page de la table ; une page absente (404) va dans `missing`."""
    stamp = f"{pd.Timestamp(day):%Y%m%d}"
    out: dict = {"day": pd.Timestamp(day).date().isoformat(), "views": {}, "total": 0, "missing": [], "errors": {},
                 "source": "wikimedia pageviews en.wikipedia all-access user"}
    for i, (base, page) in enumerate(wiki_pages(settings).items()):
        if i:
            sleep(REQUEST_GAP_SECONDS)
        try:
            try:
                views = parse_views(http.get_json(f"{WIKI_BASE}{page}/daily/{stamp}/{stamp}"))
            except Exception as first:  # noqa: BLE001 - limite de débit : une seule reprise après une attente
                if "HTTP 429" not in str(first):
                    raise
                sleep(RETRY_429_SECONDS)
                views = parse_views(http.get_json(f"{WIKI_BASE}{page}/daily/{stamp}/{stamp}"))
        except Exception as exc:  # noqa: BLE001 - une page en panne n'arrête pas les autres
            text = f"{type(exc).__name__}: {exc}"[:200]
            if "HTTP 404" in text:
                out["missing"].append(base)
            else:
                out["errors"][base] = text
            continue
        out["views"][base] = views
        out["total"] += views
    return out


def parse_trending(payload, universe: list[str]) -> dict:
    coins = payload.get("coins") if isinstance(payload, dict) else None
    if not isinstance(coins, list):
        raise ValueError("réponse CoinGecko sans « coins »")
    symbols: list[str] = []
    for coin in coins:
        item = coin.get("item") if isinstance(coin, dict) else None
        symbol = str((item or {}).get("symbol") or "").upper()[:12]
        if symbol and len(symbols) < MAX_TRENDING:
            symbols.append(symbol)
    return {"coins": symbols, "in_universe": [s for s in symbols if s in universe]}


def trending_hour(http: CollectHttp, settings: Settings, *, hour: pd.Timestamp) -> dict:
    return {"hour": utc_iso(hour), **parse_trending(http.get_json(TRENDING_URL), bases(settings))}


def next_hour_slot(now) -> pd.Timestamp:
    moment = pd.Timestamp(now)
    slot = moment.floor("h") + OFFSET
    return slot if slot > moment else slot + pd.Timedelta(hours=1)


def last_wiki_day(settings: Settings, now) -> str | None:
    """Dernier jour déjà relevé (journal du mois, puis du mois précédent) : un redémarrage ne refait pas la veille."""
    moment = pd.Timestamp(now)
    for month in (moment, moment.replace(day=1) - pd.Timedelta(days=1)):
        days = [e["data"].get("day") for e in journal(settings, ATTENTION, month).entries({WIKI})]
        if days:
            return max(days)
    return None


async def run(ctx: Context) -> None:
    http = ctx.http
    if http is None:
        raise RuntimeError("client HTTP absent")
    done_day = last_wiki_day(ctx.settings, ctx.clock())
    ctx.state.touch(ATTENTION, detail=f"Wikipédia (pages vues, 1/jour après 06:00 UTC, {len(wiki_pages(ctx.settings))} pages) ; "
                                      f"CoinGecko trending (1/heure) ; {REDDIT_REASON} ; {TRENDS_REASON}",
                    reddit=UNAVAILABLE, trends=UNAVAILABLE)
    while not ctx.stop.is_set():
        wait = (next_hour_slot(ctx.clock()) - ctx.now()).total_seconds()
        await ctx.pause(wait)
        if ctx.stop.is_set():
            break
        now = ctx.clock()
        hour = pd.Timestamp(now).floor("h")
        errors = []
        try:
            data = await asyncio.to_thread(trending_hour, http, ctx.settings, hour=hour)
            ctx.recorder.append(ATTENTION, TRENDING, data, now=ctx.clock())
        except Exception as exc:  # noqa: BLE001 - CoinGecko en panne : noté, Wikipédia continue
            errors.append(f"trending : {type(exc).__name__}: {exc}"[:200])
        yesterday = (pd.Timestamp(now).floor("D") - pd.Timedelta(days=1)).date().isoformat()
        if pd.Timestamp(now) >= pd.Timestamp(now).floor("D") + WIKI_AFTER and done_day != yesterday:
            data = await asyncio.to_thread(wiki_day, http, ctx.settings, day=pd.Timestamp(yesterday, tz="UTC"))
            ctx.recorder.append(ATTENTION, WIKI, data, now=ctx.clock())
            done_day = yesterday
            if data["errors"]:
                errors.append("wikipédia : " + "; ".join(f"{k}: {v}" for k, v in data["errors"].items()))
        ctx.state.message(ATTENTION, now=now)
        if errors:
            ctx.state.error(ATTENTION, " | ".join(errors), status="EN_SERVICE")
        ctx.state.write()
