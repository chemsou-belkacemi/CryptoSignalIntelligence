"""Source ATTENTION : attention publique, une fois par heure (Reddit) et une fois par jour (Google Trends).

Reddit : `https://www.reddit.com/r/<sub>/new.json?limit=100` pour r/CryptoCurrency, r/Bitcoin, r/CryptoMarkets
(User-Agent explicite, une demande toutes les 2 s au plus). Pour chaque sous-forum : nombre de messages créés dans
l'heure close, et nombre de messages de cette heure qui mentionnent chaque base de l'univers (mots entiers,
ticker ou nom, dans le titre et le texte). AUCUN contenu n'est stocké : ni titre, ni texte, ni pseudo, ni lien ;
seuls des comptes, et l'empreinte SHA-256 tronquée des identifiants déjà comptés reste en mémoire pour ne pas
compter deux fois un message vu à deux heures de suite.

Google Trends : via `pytrends` si la bibliothèque est importable (non ajoutée aux dépendances : elle dépend d'une
API non officielle qui casse souvent) ; sinon la source est `NON_DISPONIBLE`, dit dans l'état et dans la doc.
Termes : « bitcoin », « crypto », « ethereum », « altcoin », intérêt relatif des 7 derniers jours (0-100).

Entrées (journal `C_ATTENTION-AAAA-MM.jsonl`) : `ATTENTION_REDDIT_H` (par heure) et `ATTENTION_TRENDS_J` (par jour).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import pandas as pd

from ..config import Settings
from ..forward.journal import utc_iso
from .base import ATTENTION, UNAVAILABLE, Context
from .net import CollectHttp, check_url

log = logging.getLogger("csi.collect.attention")

REDDIT, TRENDS = "ATTENTION_REDDIT_H", "ATTENTION_TRENDS_J"
SUBREDDITS = ("CryptoCurrency", "Bitcoin", "CryptoMarkets")
REQUEST_GAP_SECONDS = 2.0
LIMIT = 100
TRENDS_TERMS = ("bitcoin", "crypto", "ethereum", "altcoin")
TRENDS_URL = "https://trends.google.com/trends/explore"
OFFSET = pd.Timedelta(minutes=2)                     # relevé horaire à hh:02, quotidien à 00:10 UTC
TRENDS_OFFSET = pd.Timedelta(minutes=10)
#: Noms usuels des bases (mots entiers, sans casse) ; les autres bases ne sont cherchées que par leur ticker.
NAMES = {"BTC": ("bitcoin",), "ETH": ("ethereum", "ether"), "SOL": ("solana",), "XRP": ("ripple",), "NEAR": (),
         "AVAX": ("avalanche",), "HBAR": ("hedera",), "LINK": ("chainlink",), "XLM": ("stellar",), "ADA": ("cardano",),
         "TRX": ("tron",), "FIL": ("filecoin",), "ALGO": ("algorand",), "DOT": ("polkadot",), "ATOM": ("cosmos",),
         "ETC": ("ethereum classic",)}


def bases(settings: Settings) -> list[str]:
    return list(dict.fromkeys(s[:-4] for s in settings.data.symbols if s.endswith("USDT")))


def patterns(assets: list[str]) -> dict[str, re.Pattern]:
    out = {}
    for asset in assets:
        words = [asset.lower(), *NAMES.get(asset, ())]
        out[asset] = re.compile(r"(?<![a-z0-9])\$?(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])",
                                re.IGNORECASE)
    return out


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def count_posts(listing: dict, *, hour_start: pd.Timestamp, assets: dict[str, re.Pattern],
                seen: set[str]) -> dict:
    """Comptes d'une page `new.json` pour l'heure [hour_start, hour_start + 1 h) ; rien du contenu n'est gardé.
    `seen` reçoit l'empreinte des identifiants comptés (dédoublonnage entre deux relevés)."""
    children = ((listing.get("data") or {}).get("children") or []) if isinstance(listing, dict) else []
    start, end = hour_start.timestamp(), (hour_start + pd.Timedelta(hours=1)).timestamp()
    counts: dict[str, Any] = {"posts_in_hour": 0, "posts_scanned": len(children), "oldest_in_page": None,
              "mentions": dict.fromkeys(assets, 0)}
    oldest = None
    for child in children:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        created = float(data.get("created_utc") or 0)
        oldest = created if oldest is None else min(oldest, created)
        if not start <= created < end:
            continue
        key = _digest(str(data.get("name") or data.get("id") or f"{created}"))
        if key in seen:
            continue
        seen.add(key)
        counts["posts_in_hour"] += 1
        text = f"{data.get('title') or ''}\n{data.get('selftext') or ''}"
        for asset, pattern in assets.items():
            if pattern.search(text):
                counts["mentions"][asset] += 1
    if oldest is not None:
        counts["oldest_in_page"] = utc_iso(pd.Timestamp(oldest, unit="s", tz="UTC"))
        counts["hour_fully_covered"] = oldest <= start
    return counts


def reddit_hour(http: CollectHttp, settings: Settings, *, hour_start: pd.Timestamp, seen: set[str],
                sleep: Callable[[float], None] = time.sleep) -> dict:
    assets = patterns(bases(settings))
    out: dict = {"hour": utc_iso(hour_start), "subreddits": {}, "mentions": dict.fromkeys(assets, 0),
                 "posts_in_hour": 0, "errors": {}, "content_stored": False}
    for i, sub in enumerate(SUBREDDITS):
        if i:
            sleep(REQUEST_GAP_SECONDS)
        try:
            listing = http.get_json(f"https://www.reddit.com/r/{sub}/new.json", {"limit": LIMIT, "raw_json": 1})
            counts = count_posts(listing, hour_start=hour_start, assets=assets, seen=seen)
        except Exception as exc:  # noqa: BLE001 - un sous-forum en panne n'arrête pas les autres
            out["errors"][sub] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        out["subreddits"][sub] = {k: v for k, v in counts.items() if k != "mentions"} | {"mentions": counts["mentions"]}
        out["posts_in_hour"] += counts["posts_in_hour"]
        for asset, n in counts["mentions"].items():
            out["mentions"][asset] += n
    return out


def trends_available() -> tuple[bool, str]:
    try:
        import pytrends  # noqa: F401
    except ImportError:
        return False, "pytrends absent : Google Trends NON_DISPONIBLE (dépendance non ajoutée, API non officielle instable)"
    return True, "pytrends importable (À VÉRIFIER AU DÉPLOIEMENT)"


def trends_day(*, day: pd.Timestamp) -> dict:
    """Intérêt relatif des 7 derniers jours par terme via pytrends ; `check_url` sur l'hôte avant l'appel."""
    check_url(TRENDS_URL)
    from pytrends.request import TrendReq
    client = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
    client.build_payload(list(TRENDS_TERMS), timeframe="now 7-d")
    frame = client.interest_over_time()
    out: dict = {"day": day.date().isoformat(), "terms": {}, "window": "now 7-d", "scale": "0-100 relatif"}
    for term in TRENDS_TERMS:
        if term in frame.columns and len(frame):
            series = frame[term]
            out["terms"][term] = {"last": int(series.iloc[-1]), "mean_7d": round(float(series.mean()), 2)}
    return out


def next_hour_slot(now) -> pd.Timestamp:
    moment = pd.Timestamp(now)
    slot = moment.floor("h") + OFFSET
    return slot if slot > moment else slot + pd.Timedelta(hours=1)


async def run(ctx: Context) -> None:
    http = ctx.http
    if http is None:
        raise RuntimeError("client HTTP absent")
    available, reason = trends_available()
    seen: set[str] = set()
    last_trends_day: str | None = None
    ctx.state.touch(ATTENTION, detail=f"Reddit {', '.join('r/' + s for s in SUBREDDITS)} chaque heure ; {reason}",
                    trends=UNAVAILABLE if not available else "A_VERIFIER")
    while not ctx.stop.is_set():
        wait = (next_hour_slot(ctx.clock()) - ctx.now()).total_seconds()
        await ctx.sleep(max(0.0, wait))
        if ctx.stop.is_set():
            break
        now = ctx.clock()
        hour_start = pd.Timestamp(now).floor("h") - pd.Timedelta(hours=1)
        data = await asyncio.to_thread(reddit_hour, http, ctx.settings, hour_start=hour_start, seen=seen)
        ctx.state.message(ATTENTION, now=now)
        if data["errors"]:
            ctx.state.error(ATTENTION, "; ".join(f"{k}: {v}" for k, v in data["errors"].items()), status="EN_SERVICE")
        ctx.recorder.append(ATTENTION, REDDIT, data, now=ctx.clock())
        if len(seen) > 5000:            # mémoire bornée : au pire un message compté deux fois après la purge
            seen.clear()
        day = pd.Timestamp(now).floor("D")
        if available and pd.Timestamp(now) >= day + TRENDS_OFFSET and last_trends_day != day.date().isoformat():
            try:
                trends = await asyncio.to_thread(trends_day, day=day)
                ctx.recorder.append(ATTENTION, TRENDS, trends, now=ctx.clock())
            except Exception as exc:  # noqa: BLE001 - Google Trends en panne : noté, Reddit continue
                ctx.state.error(ATTENTION, f"Google Trends : {exc}", status="EN_SERVICE")
            last_trends_day = day.date().isoformat()
        ctx.state.write()
