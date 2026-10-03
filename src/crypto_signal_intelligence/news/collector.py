"""Collecte et vérification des sources d'actualités (GET HTTPS publics, sans clé, sans LLM).

Seules les URL configurées sont appelées ; une redirection vers autre chose que HTTPS est refusée ;
la taille des réponses est bornée. Un échec est enregistré dans la santé de la source et n'arrête
pas les autres. L'état d'une source :
- UNVERIFIED : jamais vérifiée (`news-sources --check`) → pas encore déclarée opérationnelle ;
- OPERATIONAL : vérifiée et dernier succès récent ;
- DOWN : vérifiée, le collecteur tourne, mais cette source échoue ou reste muette ;
- UNKNOWN : aucun essai récent sur AUCUNE source (collecteur arrêté) : état inconnu, pas une panne.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from ..config import NewsSourceConfig, Settings
from .assets import base_assets, detect
from .parse import FeedError, RawItem, parse_binance_cms, parse_feed
from .store import NewsStore

log = logging.getLogger("csi.news")
USER_AGENT = "crypto-signal-intelligence/0.1 (collecte RSS publique, lecture seule)"


class SourceError(RuntimeError):
    pass


def fetch(source: NewsSourceConfig, *, timeout: float, max_bytes: int,
          transport: httpx.BaseTransport | None = None) -> list[RawItem]:
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=True, transport=transport,
                      headers={"User-Agent": USER_AGENT}) as client:
        try:
            response = client.get(source.url)
        except httpx.HTTPError as exc:
            raise SourceError(f"réseau : {type(exc).__name__}") from None
    if response.url.scheme != "https":
        raise SourceError(f"redirection non HTTPS refusée : {response.url}")
    if response.status_code != 200:
        raise SourceError(f"HTTP {response.status_code}")
    if len(response.content) > max_bytes:
        raise SourceError(f"réponse trop volumineuse ({len(response.content)} octets)")
    try:
        if source.kind == "binance_cms":
            return parse_binance_cms(json.loads(response.content))
        return parse_feed(response.content)
    except (FeedError, json.JSONDecodeError) as exc:
        raise SourceError(f"format : {exc}") from None


@dataclass
class SourceResult:
    source_id: str
    fetched: int = 0
    new: int = 0
    revised: int = 0
    error: str | None = None


@dataclass
class CollectSummary:
    started_at: datetime
    sources: list[SourceResult] = field(default_factory=list)

    @property
    def new(self) -> int:
        return sum(s.new for s in self.sources)

    @property
    def failed(self) -> list[str]:
        return [s.source_id for s in self.sources if s.error]


Fetcher = Callable[[NewsSourceConfig], list[RawItem]]


def collect(settings: Settings, *, now: datetime, fetcher: Fetcher | None = None) -> CollectSummary:
    cfg = settings.news
    store = NewsStore(settings.news_db)
    universe = base_assets(settings.data.symbols)
    window, summary = timedelta(hours=cfg.cluster_window_hours), CollectSummary(started_at=now)
    fetcher = fetcher or (lambda s: fetch(s, timeout=cfg.timeout_seconds, max_bytes=cfg.max_bytes))
    for source in cfg.sources:
        if not source.enabled:
            continue
        result = SourceResult(source.source_id)
        try:
            items = fetcher(source)
        except SourceError as exc:
            result.error = str(exc)
            store.record_attempt(source.source_id, now, error=result.error, items=None)
            summary.sources.append(result)
            continue
        result.fetched = len(items)
        for item in items:
            stored = store.upsert(source_id=source.source_id, category=source.category, item=item,
                                  assets=detect(f"{item.title} {item.summary}", universe), now=now, window=window,
                                  similarity=cfg.cluster_similarity)
            result.new += stored.status == "NEW"
            result.revised += stored.status == "REVISED"
        store.record_attempt(source.source_id, now, error=None, items=len(items))
        summary.sources.append(result)
    try:                                   # étiquettes de risque par mots-clés, en observation (news/risk.py)
        from .risk import label_pending
        label_pending(settings, now=now)
    except Exception as exc:  # noqa: BLE001 - un échec d'étiquetage n'arrête pas la collecte
        log.warning("étiquetage des risques en échec : %s", exc)
    return summary


def verify_sources(settings: Settings, *, now: datetime, fetcher: Fetcher | None = None) -> dict[str, tuple[bool, str]]:
    """Déclare une source opérationnelle seulement si : HTTPS, HTTP 200, format lisible, au moins un
    élément, et au moins 80 % des éléments datés (sinon l'historique prospectif serait inexploitable)."""
    cfg = settings.news
    store = NewsStore(settings.news_db)
    fetcher = fetcher or (lambda s: fetch(s, timeout=cfg.timeout_seconds, max_bytes=cfg.max_bytes))
    results: dict[str, tuple[bool, str]] = {}
    for source in cfg.sources:
        try:
            items = fetcher(source)
        except SourceError as exc:
            ok, detail = False, str(exc)
        else:
            dated = sum(1 for i in items if i.published_at is not None)
            ok = bool(items) and dated >= 0.8 * len(items)
            detail = f"{len(items)} éléments, {dated} datés" + ("" if ok else " : insuffisant")
        store.record_verification(source.source_id, now, ok=ok, detail=detail)
        results[source.source_id] = (ok, detail)
    return results


def source_states(settings: Settings, *, now: datetime) -> dict[str, str]:
    health = NewsStore(settings.news_db).health()
    stale = timedelta(minutes=settings.news.stale_after_minutes)
    attempts = [datetime.fromisoformat(r["last_attempt_at"]) for r in health.values() if r.get("last_attempt_at")]
    collector_idle = not attempts or now - max(attempts) > stale
    states = {}
    for source in settings.news.sources:
        row = health.get(source.source_id, {})
        if not source.enabled:
            states[source.source_id] = "DISABLED"
        elif not row.get("verified_at"):
            states[source.source_id] = "UNVERIFIED"
        elif collector_idle:
            # Personne n'a interrogé les sources récemment : on ne sait pas si elles répondent.
            states[source.source_id] = "UNKNOWN"
        elif row.get("last_success_at") and now - datetime.fromisoformat(row["last_success_at"]) <= stale \
                and not row.get("last_error"):
            states[source.source_id] = "OPERATIONAL"
        else:
            states[source.source_id] = "DOWN"
    return states
