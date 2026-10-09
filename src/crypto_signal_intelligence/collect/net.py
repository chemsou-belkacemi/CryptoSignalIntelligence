"""Client réseau du collecteur : LISTE FERMÉE d'adresses publiques, lecture seule, aucune clé.

`data/http.py` (bougies, carnet REST, marché à terme REST) n'est pas élargi : les flux WebSocket et les hôtes hors
Binance passent par ce client, qui refuse toute adresse hors de `ALLOWED` AVANT tout appel réseau. Il n'existe
aucune route vers un endpoint privé, un compte ou un ordre ; aucun en-tête d'authentification n'est jamais posé.

Adresses autorisées (préfixes exacts, schéma compris) :
- wss://stream.binance.com:9443/      flux publics Spot (carnet, transactions agrégées) ;
- wss://fstream.binance.com/          flux publics du marché à terme (liquidations) ;
- https://www.deribit.com/api/v2/public/   API publique de Deribit (options, DVOL, indice) ;
- https://www.reddit.com/r/<sub>/new.json  derniers messages publics d'un sous-forum.
Google Trends n'y est pas : NON_DISPONIBLE (pytrends ferait ses appels hors de ce client), voir collect/attention.py.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

USER_AGENT = "crypto-signal-intelligence/0.1 (collecteur shadow, lecture seule, sans cle)"
MAX_BYTES = 4_000_000
ALLOWED_WS = ("wss://stream.binance.com:9443/", "wss://fstream.binance.com/")
ALLOWED_HTTPS = ("https://www.deribit.com/api/v2/public/",)
REDDIT_NEW = re.compile(r"^https://www\.reddit\.com/r/[A-Za-z0-9_]{1,30}/new\.json$")
ALLOWED = (*ALLOWED_WS, *ALLOWED_HTTPS, "https://www.reddit.com/r/<sub>/new.json")
#: Mots interdits dans toute adresse et tout paramètre (sécurité en profondeur : la liste fermée les exclut déjà ;
#: « order » n'y est pas, le flux public des liquidations s'appelle `!forceOrder@arr`).
FORBIDDEN_WORDS = ("private", "account", "signature", "apikey", "api_key", "listenkey", "userdata")


class RefusedUrl(PermissionError):
    pass


class NetError(RuntimeError):
    pass


def check_url(url: str) -> str:
    """Rend `url` si elle est dans la liste fermée ; lève `RefusedUrl` sinon (avant tout appel réseau)."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise RefusedUrl(f"adresse illisible : {url!r}")
    parts = urlsplit(url)
    lowered = url.lower()
    if any(word in lowered for word in FORBIDDEN_WORDS):
        raise RefusedUrl(f"adresse refusée (mot interdit) : {url}")
    if parts.username or parts.password or ".." in parts.path:
        raise RefusedUrl(f"adresse refusée : {url}")
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if parts.scheme == "wss" and base.startswith(ALLOWED_WS):
        return url
    if parts.scheme == "https" and (base.startswith(ALLOWED_HTTPS) or REDDIT_NEW.match(base)):
        return url
    raise RefusedUrl(f"adresse hors liste fermée : {url}")


class CollectHttp:
    """GET JSON borné sur la liste fermée (sans redirection, sans cookie, sans authentification)."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False,
                                    transport=transport, headers={"User-Agent": USER_AGENT})

    def close(self) -> None:
        self._client.close()

    def get_json(self, url: str, params: dict | None = None):
        check_url(url)
        if params and any(word in str(k).lower() for k in params for word in FORBIDDEN_WORDS):
            raise RefusedUrl("paramètre refusé")
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise NetError(f"réseau : {type(exc).__name__}") from None
        if response.status_code != 200:
            raise NetError(f"HTTP {response.status_code} sur {urlsplit(url).netloc}")
        if len(response.content) > MAX_BYTES:
            raise NetError(f"réponse trop volumineuse ({len(response.content)} octets)")
        try:
            return json.loads(response.content)
        except ValueError:
            raise NetError(f"JSON illisible : {url}") from None


@asynccontextmanager
async def ws_messages(url: str, *, ping_interval: float = 20.0, open_timeout: float = 20.0):
    """Flux WebSocket public en lecture seule : rend un itérateur asynchrone de messages JSON décodés.

    `websockets` (extra `collect`) est importé à la demande : les tests remplacent ce flux par des messages fictifs.
    Rien n'est jamais envoyé sur la connexion en dehors des pings de la bibliothèque."""
    check_url(url)
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover - dépendance absente : la source se déclare NON_DISPONIBLE
        raise NetError("bibliothèque websockets absente (pip install -e '.[collect]')") from None
    async with connect(url, ping_interval=ping_interval, ping_timeout=ping_interval, open_timeout=open_timeout,
                       max_size=MAX_BYTES, user_agent_header=USER_AGENT) as socket:
        async def messages() -> AsyncIterator[dict]:
            async for raw in socket:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        yield messages()
