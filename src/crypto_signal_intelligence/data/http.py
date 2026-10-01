"""Client HTTP public unique (httpx), en lecture seule, limité à une liste blanche.

Aucune clé, aucune signature, aucun en-tête d'authentification. Tout chemin
hors liste blanche est refusé AVANT l'appel réseau : il n'existe aucune route
vers les endpoints d'ordres ou de compte, ni en Spot ni sur le marché à terme
(dont CSI ne lit que des données PUBLIQUES de positionnement : docs/DERIVATIVES.md).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("csi.http")

REST_ALLOWED_PATHS = frozenset({"/api/v3/klines", "/api/v3/exchangeInfo", "/api/v3/ping", "/api/v3/time"})
ARCHIVE_ALLOWED_PREFIX = "/data/spot/"
# Marché à terme USDⓈ-M : données publiques de marché seulement (financement, prime, intérêt ouvert, ratios),
# comme information sur le positionnement. Aucun ordre, aucun compte, aucun flux utilisateur : CSI ne négocie
# jamais de contrat à terme.
FUTURES_REST_ALLOWED_PATHS = frozenset({
    "/fapi/v1/premiumIndex", "/fapi/v1/premiumIndexKlines", "/fapi/v1/fundingRate", "/fapi/v1/openInterest",
    "/futures/data/openInterestHist", "/futures/data/globalLongShortAccountRatio",
    "/futures/data/topLongShortPositionRatio", "/futures/data/takerlongshortRatio",
})
FUTURES_ARCHIVE_ALLOWED_PREFIX = "/data/futures/um/"
RETRYABLE_STATUS = {418, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotFound(HttpError):
    pass


class PublicHttpClient:
    def __init__(self, base_url: str, *, allowed_paths: frozenset[str] = frozenset(),
                 allowed_prefix: str | None = None, timeout: float = 20.0, retries: int = 4,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.allowed_paths = allowed_paths
        self.allowed_prefix = allowed_prefix
        self.retries = retries
        self.sleep = sleep
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0),
                                    transport=transport, follow_redirects=False,
                                    headers={"User-Agent": "crypto-signal-intelligence/0.1"})

    @classmethod
    def rest(cls, base_url: str, **kwargs) -> PublicHttpClient:
        return cls(base_url, allowed_paths=REST_ALLOWED_PATHS, **kwargs)

    @classmethod
    def archives(cls, base_url: str, **kwargs) -> PublicHttpClient:
        return cls(base_url, allowed_prefix=ARCHIVE_ALLOWED_PREFIX, **kwargs)

    @classmethod
    def futures_rest(cls, base_url: str, **kwargs) -> PublicHttpClient:
        """Données publiques du marché à terme (lecture seule ; aucun ordre ni compte n'est joignable)."""
        return cls(base_url, allowed_paths=FUTURES_REST_ALLOWED_PATHS, **kwargs)

    @classmethod
    def futures_archives(cls, base_url: str, **kwargs) -> PublicHttpClient:
        """Archives publiques du marché à terme USDⓈ-M (financement, prime, intérêt ouvert et ratios)."""
        return cls(base_url, allowed_prefix=FUTURES_ARCHIVE_ALLOWED_PREFIX, **kwargs)

    def close(self) -> None:
        self._client.close()

    def _check_path(self, path: str) -> None:
        allowed = path in self.allowed_paths or (
            self.allowed_prefix is not None and path.startswith(self.allowed_prefix) and ".." not in path)
        if not allowed:
            raise PermissionError(f"Chemin HTTP non autorisé par la liste blanche : {path}")

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        self._check_path(path)
        last: HttpError | None = None
        for attempt in range(self.retries):
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last = HttpError(f"Erreur réseau ({type(exc).__name__}) sur {path}")
                self.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code == 200:
                return response
            if response.status_code == 404:
                raise NotFound(f"Introuvable : {path}", 404)
            if response.status_code in RETRYABLE_STATUS:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt * 2, 60)
                logger.warning("HTTP %s sur %s, nouvel essai dans %.0f s", response.status_code, path, delay)
                last = HttpError(f"HTTP {response.status_code} sur {path}", response.status_code)
                if response.status_code == 418:  # IP bannie : ne pas insister
                    break
                self.sleep(min(delay, 120))
                continue
            raise HttpError(f"HTTP {response.status_code} sur {path} : {response.text[:200]}", response.status_code)
        raise last or HttpError(f"Échec sur {path}")

    def get_json(self, path: str, params: dict | None = None):
        return self.get(path, params).json()

    def get_bytes(self, path: str) -> bytes:
        return self.get(path).content
