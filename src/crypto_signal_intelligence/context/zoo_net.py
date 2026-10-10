"""Client réseau des « données du zoo » (docs/CONTEXTE.md, section « Données du zoo (2026-10) ») : LISTE FERMÉE
d'adresses publiques, lecture seule, sans clé, sans compte, sans cookie, sans redirection.

Liste dédiée, sur le modèle de `collect/net.py` : ni `data/http.py` (Binance seulement) ni la liste blanche du relevé
quotidien (`forward/sources.ALLOWED`) ne sont élargis. Toute adresse hors de `ALLOWED` est refusée AVANT l'appel
réseau. Préfixes exacts (schéma, hôte et début de chemin) :
- FRED (CSV public `fredgraph.csv`, sans clé) et ALFRED (page publique des millésimes d'une série) ;
- Fed : calendrier des réunions du FOMC (page courante et pages historiques annuelles) ;
- alternative.me : indice Fear & Greed ;
- CoinMetrics community : capitalisation de BTC et ETH (métrique gratuite) ;
- BCE : taux de référence (indice dollar recalculé) ;
- Binance : liste publique des annonces (catégorie « New Cryptocurrency Listing »), lecture des titres seulement.

FRED et ALFRED demandent 2 s entre deux requêtes (robots.txt, « Crawl-delay: 2 ») : respecté par `pause_for`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

# FRED refuse certains User-Agent (connexion coupée, constaté le 2026-10-10 : c'est pourquoi `fredgraph.csv`
# paraissait « muet » depuis le 2026-10-02) ; celui-ci, honnête et avec l'adresse du projet, est accepté.
USER_AGENT = ("crypto-signal-intelligence/0.1 (+https://github.com/chemsou-belkacemi/CryptoSignalIntelligence ; "
              "donnees publiques, lecture seule)")
MAX_BYTES = 4_000_000
ALLOWED = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv",
    "https://alfred.stlouisfed.org/series/downloaddata",
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "https://www.federalreserve.gov/monetarypolicy/fomchistorical",
    "https://api.alternative.me/fng/",
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
    "https://data-api.ecb.europa.eu/service/data/EXR/",
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
)
#: Mots interdits dans toute adresse et tout paramètre (sécurité en profondeur, comme `collect/net.py`).
FORBIDDEN_WORDS = ("private", "account", "signature", "apikey", "api_key", "listenkey", "userdata", "order")
#: Pause entre deux requêtes vers un même hôte (secondes) : robots.txt de FRED et d'ALFRED.
CRAWL_DELAY = {"fred.stlouisfed.org": 2.0, "alfred.stlouisfed.org": 2.0}


class RefusedUrl(PermissionError):
    pass


class ZooNetError(RuntimeError):
    pass


def check_url(url: str, params: dict | None = None) -> str:
    """Rend `url` si elle est dans la liste fermée ; lève `RefusedUrl` sinon (avant tout appel réseau)."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise RefusedUrl(f"adresse illisible : {url!r}")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port or ".." in parts.path:
        raise RefusedUrl(f"adresse refusée : {url}")
    lowered = url.lower()
    words = [lowered, *(f"{k}={v}".lower() for k, v in (params or {}).items())]
    if any(word in text for word in FORBIDDEN_WORDS for text in words):
        raise RefusedUrl(f"adresse refusée (mot interdit) : {url}")
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if not base.startswith(ALLOWED):
        raise RefusedUrl(f"adresse hors liste fermée : {url}")
    return url


class ZooHttp:
    """GET borné sur la liste fermée ; `get` rend des octets, `get_json` du JSON décodé."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=False,
                                    transport=transport, headers={"User-Agent": USER_AGENT})
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def pause_for(self, host: str) -> None:
        delay = CRAWL_DELAY.get(host, 0.0)
        last = self._last.get(host)
        if delay and last is not None:
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> bytes:
        check_url(url, params)
        host = urlsplit(url).netloc
        self.pause_for(host)
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ZooNetError(f"réseau : {type(exc).__name__} sur {host}") from None
        if response.status_code != 200:
            raise ZooNetError(f"HTTP {response.status_code} sur {host}")
        if len(response.content) > MAX_BYTES:
            raise ZooNetError(f"réponse trop volumineuse ({len(response.content)} octets)")
        return response.content

    def get_json(self, url: str, params: dict | None = None):
        try:
            return json.loads(self.get(url, params))
        except ValueError:
            raise ZooNetError(f"JSON illisible : {url}") from None
