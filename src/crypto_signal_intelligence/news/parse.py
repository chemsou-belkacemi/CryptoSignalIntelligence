"""Lecture des flux : RSS 2.0, Atom et annonces Binance (JSON public). Aucune exécution de contenu.

Le texte des articles est tronqué et débarrassé de ses balises : il n'est jamais interprété comme
une instruction. Une date absente ou illisible reste `None` (la première réception fait foi).
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

MAX_TITLE = 500
MAX_SUMMARY = 2000
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
ATOM = "{http://www.w3.org/2005/Atom}"


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class RawItem:
    guid: str
    url: str
    title: str
    summary: str
    published_at: datetime | None
    category: str | None = None


def clean_text(value: str | None, limit: int) -> str:
    text = html.unescape(_TAGS.sub(" ", value or ""))
    return _SPACES.sub(" ", text).strip()[:limit]


def parse_date(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)             # RSS : RFC 822
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))   # Atom : ISO 8601
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None                                        # fuseau inconnu : on ne devine pas
    return parsed.astimezone(UTC)


def parse_feed(content: bytes) -> list[RawItem]:
    """RSS 2.0 ou Atom → éléments ; lève FeedError si le document n'est ni l'un ni l'autre."""
    try:
        root = ET.fromstring(content.lstrip(b"\xef\xbb\xbf \r\n\t"))
    except ET.ParseError as exc:
        raise FeedError(f"XML illisible : {exc}") from None
    items: list[RawItem] = []
    if root.tag == "rss" or root.find("channel") is not None:
        for node in root.iter("item"):
            url = (node.findtext("link") or "").strip()
            guid = (node.findtext("guid") or url).strip()
            items.append(RawItem(guid=guid, url=url, title=clean_text(node.findtext("title"), MAX_TITLE),
                                 summary=clean_text(node.findtext("description"), MAX_SUMMARY),
                                 published_at=parse_date(node.findtext("pubDate"))))
    elif root.tag == f"{ATOM}feed":
        for node in root.iter(f"{ATOM}entry"):
            link = node.find(f"{ATOM}link")
            url = (link.get("href") if link is not None else "") or ""
            items.append(RawItem(guid=(node.findtext(f"{ATOM}id") or url).strip(), url=url.strip(),
                                 title=clean_text(node.findtext(f"{ATOM}title"), MAX_TITLE),
                                 summary=clean_text(node.findtext(f"{ATOM}summary"), MAX_SUMMARY),
                                 published_at=parse_date(node.findtext(f"{ATOM}published")
                                                         or node.findtext(f"{ATOM}updated"))))
    else:
        raise FeedError(f"format inconnu (racine {root.tag})")
    return [item for item in items if item.guid and item.title]


def parse_binance_cms(payload: dict) -> list[RawItem]:
    """Annonces officielles Binance (catalogues : listings, retraits, maintenance…)."""
    if not isinstance(payload, dict) or payload.get("code") != "000000":
        raise FeedError("réponse inattendue du service d'annonces")
    items: list[RawItem] = []
    for catalog in (payload.get("data") or {}).get("catalogs") or []:
        for article in catalog.get("articles") or []:
            code, title = str(article.get("code") or ""), clean_text(article.get("title"), MAX_TITLE)
            release = article.get("releaseDate")
            published = datetime.fromtimestamp(release / 1000, UTC) if isinstance(release, int | float) else None
            if code and title:
                items.append(RawItem(guid=code, url=f"https://www.binance.com/en/support/announcement/detail/{code}",
                                     title=title, summary="", published_at=published,
                                     category=clean_text(catalog.get("catalogName"), 100) or None))
    return items
