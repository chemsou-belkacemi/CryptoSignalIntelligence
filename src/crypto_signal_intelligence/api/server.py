"""API HTTP locale : l'interface de BinanceSpotManager (ou tout outil local) interroge CSI.

Lecture et évaluation SEULEMENT : aucune route ne crée, modifie ou annule un ordre, aucune ne
demande de clé Binance. Routes (JSON, UTF-8) :

    GET  /health               état de la surveillance (dernier cycle)
    GET  /strategies           dernier walk-forward de chaque stratégie (verdict, E[R], IC95)
    GET  /sources              bilan de chaque groupe Telegram contre le taux de base
    GET  /signals/recent?limit=N   dernières évaluations de signaux externes
    GET  /execution-report     signaux publiés : backtest, prospectif et Demo séparés
    POST /evaluate             {"text": "...", "source": "groupe", "record": true} → verdict expliqué

Sécurité :
- écoute sur 127.0.0.1 par défaut ; dans Docker, le port n'est publié que sur 127.0.0.1 de l'hôte ;
- jeton facultatif `CSI_API_TOKEN` (variable d'environnement, jamais dans le code) : s'il est défini,
  chaque requête doit porter `Authorization: Bearer <jeton>` ;
- corps limité à 16 Ko, JSON uniquement, aucune en-tête CORS (appel de serveur à serveur, pas
  depuis un navigateur).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import Settings

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
MAX_SOURCE_CHARS = 80
TOKEN_ENV = "CSI_API_TOKEN"

VERDICT_TEXT = {
    "REFUSE": "Refusé : le signal ne peut pas être évalué ou est déjà mort (voir le contrôle en échec).",
    "DEFAVORABLE": "Défavorable : un veto est déclenché, ou la même géométrie perd en moyenne dans ce régime.",
    "INDETERMINE": "Indéterminé : pas assez d'éléments pour préférer ce signal au hasard (ce n'est pas du 50/50).",
    "FAVORABLE": "Favorable : la même géométrie a gagné en moyenne dans ce régime (IC95 > 0), hors avantage du groupe.",
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def explain(evaluation: dict) -> str:
    """Résumé en français d'une évaluation, chaque chiffre avec sa définition."""
    verdict = evaluation.get("verdict", "")
    failed = [c for c in evaluation.get("checks", []) if not c.get("ok")]
    if verdict == "DEFAVORABLE" and not failed:
        parts = ["Défavorable : aucun veto, mais la même géométrie perd en moyenne dans ce régime (IC95 ≤ 0)."]
    elif verdict == "DEFAVORABLE":
        parts = ["Défavorable : un veto est déclenché."]
    else:
        parts = [VERDICT_TEXT.get(verdict, verdict)]
    if failed:
        parts.append("Contrôle en échec : " + " ; ".join(f"{c['label']} ({c['detail']})" for c in failed[:3]) + ".")
    rate = evaluation.get("base_rate")
    if rate and rate.get("samples"):
        ci = rate.get("expectancy_r_ci95")
        ci_text = f", IC95 [{ci[0]:+.2f} ; {ci[1]:+.2f}] R" if ci else ", sans intervalle fiable"
        parts.append(
            f"Taux de base : sur {rate['samples']} ordres de même géométrie pris à l'aveugle "
            f"({'même régime' if rate.get('regime_conditioned') else 'tous régimes'}), "
            f"{rate['tp_first'] * 100:.0f} % ont touché le TP1 avant le stop ; espérance "
            f"{rate['expectancy_r']:+.2f} R par ordre rempli{ci_text}. C'est un historique, pas la "
            "probabilité que CE signal réussisse.")
    stats = evaluation.get("source_stats")
    if stats and stats.get("evaluated"):
        parts.append(f"Groupe « {evaluation.get('source')} » : {stats['evaluated']} signal(s) évalué(s), "
                     f"{stats.get('resolved') or 0} résolu(s) ; bilan détaillé dans /sources.")
    return " ".join(parts)


class CsiApi:
    """Logique des routes, indépendante du transport HTTP (testable sans socket)."""

    def __init__(self, settings: Settings, *, now: Callable[[], datetime] | None = None):
        self.settings = settings
        self.now = now or (lambda: datetime.now(UTC))

    # --- lecture ---------------------------------------------------------------------------
    def health(self) -> dict:
        from ..live.health import check
        health = check(self.settings, now=self.now())
        return {"ready": health.ready, "degraded": health.degraded, "detail": health.detail,
                "mode": "shadow", "places_orders": False}

    def strategies(self) -> dict:
        db_path = self.settings.experiments_db
        if not db_path.exists():
            return {"strategies": []}
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as db:
            rows = db.execute("""SELECT r.strategy, r.run_id, r.created_at, r.metrics FROM runs r
                                 WHERE r.kind='WALK_FORWARD' AND r.created_at = (
                                   SELECT MAX(created_at) FROM runs WHERE kind='WALK_FORWARD' AND strategy=r.strategy)
                                 ORDER BY r.strategy""").fetchall()
        out = []
        for strategy, run_id, created_at, metrics_text in rows:
            metrics = json.loads(metrics_text)
            central = metrics.get("oos", {}).get("base/central", {})
            out.append({"strategy": strategy, "run_id": run_id, "created_at": created_at,
                        "verdict": metrics.get("verdict"), "expectancy_r": central.get("expectancy_r"),
                        "expectancy_r_ci95": central.get("expectancy_r_ci95_block_bootstrap"),
                        "trades_closed": central.get("trades_closed"),
                        "program_trials": metrics.get("program_trials")})
        return {"strategies": out,
                "note": "Verdicts du protocole (docs/PROTOCOL.md) ; E[R] hors échantillon en coûts centraux."}

    def sources(self) -> dict:
        from ..external.record import MIN_DAYS, MIN_RESOLVED, source_records
        from ..external.registry import ExternalSignalRegistry
        records = source_records(ExternalSignalRegistry(self.settings.external_db), seed=self.settings.protocol.seed)
        return {"sources": _jsonable(records),
                "rule": f"aucune conclusion avant {MIN_RESOLVED} signaux résolus sur au moins {MIN_DAYS} jours"}

    def recent(self, limit: int) -> dict:
        from ..external.registry import ExternalSignalRegistry
        return {"signals": ExternalSignalRegistry(self.settings.external_db).recent(max(1, min(limit, 200)))}

    def execution_report(self) -> dict:
        from ..feedback.reconcile import execution_report
        return {"rows": _jsonable(execution_report(self.settings))}

    # --- évaluation --------------------------------------------------------------------------
    def evaluate(self, payload: dict) -> dict:
        text = payload.get("text")
        source = payload.get("source")
        record = payload.get("record", True)
        if not isinstance(text, str) or not text.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "champ « text » (texte du signal) requis")
        if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE_CHARS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"champ « source » (nom du groupe, {MAX_SOURCE_CHARS} car. max) requis")
        if not isinstance(record, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "champ « record » : booléen")
        from ..external.evaluate import evaluate
        evaluation = evaluate(self.settings, text, source=source.strip(), now=self.now(), record=record)
        result = _jsonable(evaluation.to_dict())
        result["record_id"] = evaluation.record_id
        result["summary_fr"] = explain(result)
        return result

    # --- aiguillage ------------------------------------------------------------------------------
    def dispatch(self, method: str, path: str, query: dict[str, list[str]], body: dict | None) -> dict:
        if method == "GET":
            routes: dict[str, Callable[[], dict]] = {
                "/health": self.health, "/strategies": self.strategies, "/sources": self.sources,
                "/execution-report": self.execution_report,
                "/signals/recent": lambda: self.recent(_int(query.get("limit", ["20"])[0])),
            }
            if path in routes:
                return routes[path]()
        elif method == "POST" and path == "/evaluate":
            return self.evaluate(body or {})
        raise ApiError(HTTPStatus.NOT_FOUND, f"route inconnue : {method} {path}")


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "limit : entier attendu") from None


def make_handler(api: CsiApi, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CSI-API/1"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:   # journal standard, sans corps ni en-têtes
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: HTTPStatus, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            return secrets.compare_digest(given.encode(), f"Bearer {token}".encode())

        def _handle(self, method: str) -> None:
            try:
                if not self._authorized():
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "jeton absent ou invalide")
                url = urlparse(self.path)
                body = None
                if method == "POST":
                    if "application/json" not in (self.headers.get("Content-Type") or ""):
                        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json requis")
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0 or length > MAX_BODY_BYTES:
                        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > 0 else HTTPStatus.BAD_REQUEST,
                                       f"corps JSON requis, {MAX_BODY_BYTES} octets au plus")
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "JSON invalide") from None
                    if not isinstance(body, dict):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "objet JSON attendu")
                self._send(HTTPStatus.OK, api.dispatch(method, url.path.rstrip("/") or "/", parse_qs(url.query), body))
            except ApiError as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception:   # noqa: BLE001 - jamais de trace interne vers le client
                log.exception("erreur interne sur %s %s", method, self.path)
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "erreur interne (voir le journal de CSI)"})

        def do_GET(self) -> None:   # noqa: N802 - nom imposé par http.server
            self._handle("GET")

        def do_POST(self) -> None:   # noqa: N802
            self._handle("POST")

        def _refuse(self) -> None:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "méthode non autorisée"})

        do_PUT = do_DELETE = do_PATCH = _refuse   # noqa: N815

    return Handler


def serve(settings: Settings, *, host: str = "127.0.0.1", port: int = 8503) -> None:
    token = os.environ.get(TOKEN_ENV) or None
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        log.warning("API exposée sur %s sans %s : réservez-la à un réseau privé (Docker) ou définissez un jeton",
                    host, TOKEN_ENV)
    server = ThreadingHTTPServer((host, port), make_handler(CsiApi(settings), token))
    server.daemon_threads = True
    log.info("API CSI (lecture et évaluation seulement) sur http://%s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
