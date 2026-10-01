"""API HTTP locale et tableau de bord interactif de CSI (http://127.0.0.1:8503/), indépendant de BSM.

Lecture et évaluation SEULEMENT : aucune route ne crée, modifie ou annule un ordre, aucune ne
demande de clé Binance. Pages : `/` (application : analyser une paire, évaluer un signal, suivi),
`/app.js`, `/app.css`. Routes (JSON, UTF-8) :

    GET  /health               état de la surveillance (dernier cycle)
    GET  /strategies           dernier walk-forward de chaque stratégie (verdict, E[R], IC95)
    GET  /sources              bilan de chaque groupe Telegram contre le taux de base
    GET  /signals/recent?limit=N   dernières évaluations de signaux externes
    GET  /execution-report     signaux publiés : backtest, prospectif et Demo séparés
    GET  /signals/generated?limit=N  derniers signaux trouvés par les stratégies de CSI (shadow)
    GET  /universe             paires configurées et paires ajoutées par le propriétaire (état)
    GET  /pairs                paires analysables, avec la fraîcheur de leurs données
    GET  /models               verdicts de tous les modèles de CSI (registre des expériences)
    POST /analyze-pair         {"symbol": "ETHUSDT", "horizon": "24h"} → contexte, historique comparable,
                               plan indicatif évalué sur le passé, avis des stratégies (simulation)
    POST /refresh-pair         {"symbol": "ETHUSDT"} → télécharge les bougies publiques manquantes de la paire
                               (et du contexte BTC) : la page reste utilisable sans la surveillance
    POST /evaluate             {"text": "...", "source": "groupe", "record": true, "user_validated": false}
                               → verdict expliqué ; user_validated = signal soumis à la main par le
                               propriétaire (sa validation ajoute une paire inconnue à l'univers)

Sécurité :
- écoute sur 127.0.0.1 par défaut ; dans Docker, le port n'est publié que sur 127.0.0.1 de l'hôte ;
- jeton facultatif `CSI_API_TOKEN` (variable d'environnement, jamais dans le code) : s'il est défini,
  chaque requête doit porter `Authorization: Bearer <jeton>` ;
- corps limité à 16 Ko, JSON uniquement, aucune en-tête CORS : seule la page servie par CSI elle-même
  (même origine) appelle l'API depuis un navigateur ; un autre site ne peut ni lire les réponses ni
  envoyer du JSON (pré-requête refusée) ;
- en-tête Host contrôlé (127.0.0.1, localhost, csi-api, ou `CSI_API_ALLOWED_HOSTS`) : protège contre le
  « DNS rebinding » ;
- la page applique une politique de sécurité de contenu stricte (aucun script externe ni en ligne) et
  n'insère jamais de texte reçu comme du HTML.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
from collections import OrderedDict
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
from ..data.schema import interval

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
MAX_SOURCE_CHARS = 80
TOKEN_ENV = "CSI_API_TOKEN"
ALLOWED_HOSTS_ENV = "CSI_API_ALLOWED_HOSTS"
DEFAULT_HOSTS = ("127.0.0.1", "localhost", "::1", "csi-api")
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
OUTLOOK_CACHE_SIZE = 32
RESEARCH_REGISTRY_ENV = "CSI_RESEARCH_REGISTRY"
MODEL_KINDS = {"WALK_FORWARD": "stratégie (walk-forward)", "ML_META": "méta-labeling (lot 5)",
               "ML_INTRADAY_SELECT": "ML intraday : sélection (lot 5 bis)",
               "ML_INTRADAY_FINAL": "ML intraday : période finale",
               "ML_SWING_SELECT": "ML swing : sélection (lot 5 ter)", "ML_SWING_FINAL": "ML swing : période finale",
               "SCREEN": "criblage de familles"}

VERDICT_TEXT = {
    "REFUSE": "Refusé : le signal ne peut pas être évalué ou est déjà mort (voir le contrôle en échec).",
    "DEFAVORABLE": "Défavorable : un veto est déclenché, ou la même géométrie perd en moyenne dans ce régime.",
    "INDETERMINE": "Indéterminé : pas assez d'éléments pour préférer ce signal au hasard (ce n'est pas du 50/50).",
    "FAVORABLE": "Favorable : la même géométrie a gagné en moyenne dans ce régime (IC95 > 0), hors avantage du groupe.",
    "EN_ATTENTE": "En attente : paire ajoutée à l'univers sur ta validation ; historique en cours de téléchargement, "
                  "redemander l'avis dans quelques minutes.",
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
            f"({'même régime' if rate.get('regime_conditioned') else 'tous régimes'}"
            f"{', historique jusqu’au ' + rate['history_end'] if rate.get('history_end') else ''}), "
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
        # Une analyse de paire à la fois (mémoire du conteneur) ; résultats gardés jusqu'à la bougie suivante.
        self._outlook_lock = threading.Lock()
        self._outlook_cache: OrderedDict[tuple, dict] = OrderedDict()
        self._refresh_lock = threading.Lock()
        self.downloader: Callable[..., object] | None = None     # remplaçable dans les tests (aucun réseau)

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
        return {"sources": _jsonable(records), "min_resolved": MIN_RESOLVED, "min_days": MIN_DAYS,
                "rule": f"aucune conclusion avant {MIN_RESOLVED} signaux résolus sur au moins {MIN_DAYS} jours"}

    def recent(self, limit: int) -> dict:
        from ..external.registry import ExternalSignalRegistry
        return {"signals": ExternalSignalRegistry(self.settings.external_db).recent(max(1, min(limit, 200)))}

    def execution_report(self) -> dict:
        from ..feedback.reconcile import execution_report
        return {"rows": _jsonable(execution_report(self.settings))}

    def generated(self, limit: int) -> dict:
        """Derniers signaux trouvés par les stratégies de CSI (dossier shadow), du plus récent au plus ancien.

        `bsm_text` : le même signal au format texte que BinanceSpotManager lit déjà (PAIR / ENTRY / T / SL),
        pour un test manuel en Demo. Aucune stratégie n'étant validée, chaque signal porte son statut de
        validation et le verdict du dernier walk-forward de sa stratégie.
        """
        from ..signals.outbox import SignalRegistry
        from ..signals.txt import parse
        if not self.settings.signals_db.exists():
            return {"signals": [], "note": "aucun signal trouvé pour l'instant"}
        verdicts = {s["strategy"]: s for s in self.strategies()["strategies"]}
        registry = SignalRegistry(self.settings.signals_db, self.settings.publication_dir(), root=self.settings.root)
        now = self.now()
        out = []
        for row in reversed(registry.rows()[-max(1, min(limit, 100)):]):
            try:
                signal = parse(row["payload"])
            except Exception:  # noqa: BLE001 - un enregistrement illisible n'empêche pas les autres
                continue
            quote = "USDC" if signal.symbol.endswith("USDC") else "USDT"
            pair = f"{signal.symbol[: -len(quote)]}/{quote}"
            targets = [str(t) for t in signal.targets]
            bsm_text = "\n".join([f"PAIR: {pair}", f"ENTRY 1: {signal.entry_1}",
                                  *(f"T{i}: {t}" for i, t in enumerate(targets, 1)),
                                  f"SL: {signal.stop_loss}", "PLATFORM: Binance"])
            walk_forward = verdicts.get(signal.strategy, {})
            out.append({
                "signal_id": signal.signal_id, "symbol": signal.symbol, "strategy": signal.strategy,
                "created_at": signal.created_at.isoformat(), "entry_expires_at": signal.entry_expires_at.isoformat(),
                "expired": signal.entry_expires_at <= now, "entry": str(signal.entry_1),
                "stop_loss": str(signal.stop_loss), "targets": targets, "rr_tp1_gross": str(signal.rr_tp1_gross),
                "trend_regime": _text(signal.trend_regime), "volatility_regime": _text(signal.volatility_regime),
                "validation_status": _text(signal.validation_status),
                "strategy_verdict": walk_forward.get("verdict"), "strategy_expectancy_r": walk_forward.get("expectancy_r"),
                "bsm_text": bsm_text,
            })
        return {"signals": out,
                "note": "stratégies non validées (walk-forward) : signaux à observer ou à tester à la main en Demo, "
                        "jamais une promesse de gain"}

    def universe(self) -> dict:
        from ..external.universe import UserUniverse
        return {"configured": list(self.settings.data.symbols),
                "user_pairs": UserUniverse(self.settings.external_db).all(),
                "rule": "un signal soumis à la main par le propriétaire vaut validation de sa paire : ajoutée "
                        "définitivement (READY une fois l'historique téléchargé) ; un signal reçu automatiquement "
                        "n'ajoute jamais rien"}

    def pairs(self) -> dict:
        """Paires analysables (configuration + ajouts prêts) et âge de leur dernière bougie."""
        from ..data.store import CandleStore
        from ..external.universe import universe_symbols
        store = CandleStore(self.settings.data_dir)
        now = self.now()
        out = []
        for symbol in universe_symbols(self.settings):
            last = store.last_open_time(symbol, self.settings.data.setup_timeframe)
            age = None if last is None else int((now - last.to_pydatetime()).total_seconds() // 60) - 15
            out.append({"symbol": symbol, "last_candle": last.isoformat() if last is not None else None,
                        "age_minutes": age, "configured": symbol in self.settings.data.symbols})
        from ..outlook.pair import HORIZONS
        return {"pairs": out, "horizons": [{"key": k, "label": label} for k, (_, label) in HORIZONS.items()]}

    def models(self) -> dict:
        """Dernier verdict de chaque modèle de CSI, tel qu'enregistré par le protocole (les backtests de
        référence, purement descriptifs, ne portent pas de verdict et ne sont pas listés).

        Deux registres possibles : celui de la surveillance (état Docker) et, s'il est monté en lecture seule,
        le registre de RECHERCHE du PC (`CSI_RESEARCH_REGISTRY`), où tournent les sélections ML."""
        sources = [("surveillance", self.settings.experiments_db, False)]
        research = os.environ.get(RESEARCH_REGISTRY_ENV)
        if research:
            sources.append(("recherche", Path(research), True))
        latest: dict[tuple[str, str], dict] = {}
        trials: dict[str, int] = {}
        for source, db_path, snapshot in sources:
            if not db_path.exists():
                continue
            uri = f"file:{db_path.as_posix()}?mode=ro" + ("&immutable=1" if snapshot else "")
            try:
                with closing(sqlite3.connect(uri, uri=True)) as db:
                    rows = db.execute(f"""SELECT kind, strategy, run_id, created_at, status, metrics FROM runs r
                                          WHERE kind IN ({",".join("?" for _ in MODEL_KINDS)})
                                            AND rowid = (SELECT MAX(rowid) FROM runs WHERE kind = r.kind
                                                         AND strategy = r.strategy)""", tuple(MODEL_KINDS)).fetchall()
                    trials[source] = int(db.execute(
                        """SELECT COALESCE(SUM(COALESCE(json_extract(metrics, '$.n_trials'), 1)), 0)
                           FROM runs WHERE period_label='DEVELOPMENT'""").fetchone()[0])
            except sqlite3.Error:
                log.warning("registre %s illisible (%s)", source, db_path)
                continue
            for kind, strategy, run_id, created_at, status, metrics_text in rows:
                metrics = json.loads(metrics_text or "{}")
                verdict = metrics.get("conclusion") or metrics.get("verdict")
                if kind == "SCREEN":
                    passing = [r for r in metrics.get("rows", []) if r.get("beats_costs")]
                    verdict = (f"{len(passing)} CONDITION(S) AU-DELÀ DES COÛTS" if passing
                               else "AUCUNE_CONDITION_AU_DELA_DES_COUTS")
                if status == "FAILED":
                    verdict = "ÉCHEC D'EXÉCUTION"
                item = {"kind": kind, "label": MODEL_KINDS[kind], "strategy": strategy, "run_id": run_id,
                        "created_at": created_at, "status": status, "verdict": verdict or status, "source": source}
                key = (kind, strategy)
                if key not in latest or created_at > latest[key]["created_at"]:
                    latest[key] = item
        out = sorted(latest.values(), key=lambda m: (m["kind"], m["strategy"]))
        return {"models": out, "program_trials": trials.get("surveillance", 0),
                "research_program_trials": trials.get("recherche"),
                "note": "Aucun modèle n'est validé à ce jour : CSI n'annonce aucune rentabilité."}

    def analyze_pair(self, payload: dict) -> dict:
        from ..outlook.pair import HORIZONS, OutlookError, pair_outlook
        symbol, horizon = payload.get("symbol"), payload.get("horizon", "24h")
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 20 or not symbol.strip().isalnum():
            raise ApiError(HTTPStatus.BAD_REQUEST, "champ « symbol » (ex. ETHUSDT) requis")
        if horizon not in HORIZONS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"horizon : un de {', '.join(HORIZONS)}")
        from ..features.loader import load_inputs
        from ..signals.analyze import analyze
        from ..strategies.registry import STRATEGIES
        symbol = symbol.strip().upper()
        with self._outlook_lock:
            now = self.now()
            try:
                inputs = load_inputs(self.settings, symbol)
            except Exception:  # noqa: BLE001 - message clair produit par pair_outlook ci-dessous
                inputs = None
            setup = inputs["setup"] if inputs is not None else None
            last = setup["open_time"].iloc[-1] if setup is not None and not setup.empty else None
            # La fraîcheur dépend de l'heure : elle fait partie de la clé (sinon des données vieillies
            # resteraient présentées comme fraîches) et l'âge affiché est recalculé à chaque réponse.
            seen = setup["available_at"].iloc[-1] if last is not None and setup is not None else None
            age = (now - seen.to_pydatetime()) if seen is not None else None
            fresh = age is not None and age <= self.settings.data.max_staleness_bars * interval(
                self.settings.data.setup_timeframe)
            key = (symbol, horizon, str(last), fresh)
            if key in self._outlook_cache:
                self._outlook_cache.move_to_end(key)
                cached = self._outlook_cache[key]
                context = dict(cached.get("context", {}))
                if age is not None:
                    context["data_age_minutes"] = int(age.total_seconds() // 60)
                return cached | {"context": context, "cached": True}
            try:
                result = pair_outlook(self.settings, symbol, horizon, now=now, inputs=inputs)
            except OutlookError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from None
            verdicts = {s["strategy"]: s for s in self.strategies()["strategies"]}
            strategies = []
            for strategy_id in STRATEGIES:
                try:
                    out = analyze(self.settings, symbol, strategy_id, now=now, inputs=inputs, publish=False)
                    item = {"strategy": strategy_id, "action": out.action, "reason": out.reason_code,
                            "details": list(out.details), "levels": out.levels}
                except Exception as exc:  # noqa: BLE001 - une stratégie en échec n'empêche pas les autres
                    item = {"strategy": strategy_id, "action": "ERREUR", "reason": type(exc).__name__,
                            "details": [str(exc)], "levels": None}
                walk_forward = verdicts.get(strategy_id, {})
                item |= {"walk_forward_verdict": walk_forward.get("verdict"),
                         "walk_forward_expectancy_r": walk_forward.get("expectancy_r")}
                strategies.append(item)
            result = _jsonable(result | {"strategies": strategies,
                                         "strategies_note": "simulation sur la dernière bougie clôturée : rien n'est "
                                                            "publié et les contrôles propres à la publication (statut "
                                                            "de la stratégie, suspension, expiration, doublon) ne sont "
                                                            "pas appliqués ; stratégies rejetées par le protocole"})
            self._outlook_cache[key] = result
            while len(self._outlook_cache) > OUTLOOK_CACHE_SIZE:
                self._outlook_cache.popitem(last=False)
            return result | {"cached": False}

    def refresh_pair(self, payload: dict) -> dict:
        """Bougies manquantes de la paire (15 min, 1 h) et du contexte BTC, depuis les données PUBLIQUES de
        Binance (client HTTP à liste blanche, aucune clé). Une mise à jour à la fois."""
        from ..data.store import CandleStore
        from ..external.universe import universe_symbols
        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip().isalnum() or len(symbol) > 20:
            raise ApiError(HTTPStatus.BAD_REQUEST, "champ « symbol » (ex. ETHUSDT) requis")
        symbol = symbol.strip().upper()
        if symbol not in universe_symbols(self.settings):
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{symbol} hors univers")
        if not self._refresh_lock.acquire(blocking=False):
            raise ApiError(HTTPStatus.CONFLICT, "une mise à jour est déjà en cours : réessayer dans un instant")
        from ..live.lock import InstanceAlreadyRunning, InstanceLock
        # Même verrou que la surveillance : jamais deux processus qui réécrivent le même fichier de bougies.
        monitor_lock = InstanceLock(self.settings.root / self.settings.live.lock_file)
        try:
            monitor_lock.acquire()
        except InstanceAlreadyRunning:
            self._refresh_lock.release()
            raise ApiError(HTTPStatus.CONFLICT, "la surveillance de CSI tourne : elle met déjà les données à jour toutes "
                                                "les 15 minutes") from None
        try:
            from ..data.pipeline import download
            downloader: Callable[..., object] = self.downloader or download
            data = self.settings.data
            for item, timeframe in ((symbol, data.setup_timeframe), (symbol, data.context_timeframe),
                                    ("BTCUSDT", data.context_timeframe)):
                # rest_only : seules les bougies manquantes depuis la dernière connue (comme la surveillance)
                downloader(self.settings, item, timeframe, now=self.now(), rest_only=True)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - réseau ou Binance : message clair, rien de cassé
            raise ApiError(HTTPStatus.BAD_GATEWAY, f"mise à jour impossible pour l'instant ({type(exc).__name__})") from None
        finally:
            monitor_lock.release()
            self._refresh_lock.release()
        with self._outlook_lock:
            for key in [k for k in self._outlook_cache if k[0] == symbol]:
                del self._outlook_cache[key]
        last = CandleStore(self.settings.data_dir).last_open_time(symbol, self.settings.data.setup_timeframe)
        return {"symbol": symbol, "last_candle": last.isoformat() if last is not None else None}

    # --- évaluation --------------------------------------------------------------------------
    def evaluate(self, payload: dict) -> dict:
        text = payload.get("text")
        source = payload.get("source")
        record = payload.get("record", True)
        user_validated = payload.get("user_validated", False)
        if not isinstance(text, str) or not text.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "champ « text » (texte du signal) requis")
        if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE_CHARS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"champ « source » (nom du groupe, {MAX_SOURCE_CHARS} car. max) requis")
        if not isinstance(record, bool) or not isinstance(user_validated, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "champs « record » et « user_validated » : booléens")
        from ..external.evaluate import evaluate
        evaluation = evaluate(self.settings, text, source=source.strip(), now=self.now(), record=record,
                              user_validated=user_validated)
        result = _jsonable(evaluation.to_dict())
        result["record_id"] = evaluation.record_id
        result["summary_fr"] = explain(result)
        return result

    # --- aiguillage ------------------------------------------------------------------------------
    def dispatch(self, method: str, path: str, query: dict[str, list[str]], body: dict | None) -> dict:
        if method == "GET":
            routes: dict[str, Callable[[], dict]] = {
                "/health": self.health, "/strategies": self.strategies, "/sources": self.sources,
                "/execution-report": self.execution_report, "/universe": self.universe,
                "/signals/recent": lambda: self.recent(_int(query.get("limit", ["20"])[0])),
                "/signals/generated": lambda: self.generated(_int(query.get("limit", ["20"])[0])),
                "/pairs": self.pairs, "/models": self.models,
            }
            if path in routes:
                return routes[path]()
        elif method == "POST" and path == "/evaluate":
            return self.evaluate(body or {})
        elif method == "POST" and path == "/analyze-pair":
            return self.analyze_pair(body or {})
        elif method == "POST" and path == "/refresh-pair":
            return self.refresh_pair(body or {})
        raise ApiError(HTTPStatus.NOT_FOUND, f"route inconnue : {method} {path}")


def _text(value: Any) -> str:
    """Valeur d'énumération ou chaîne, en texte."""
    return str(getattr(value, "value", value))


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "limit : entier attendu") from None


def allowed_hosts() -> set[str]:
    extra = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return {*DEFAULT_HOSTS, *(h.strip().lower() for h in extra.split(",") if h.strip())}


def _host_name(header: str | None) -> str:
    """Nom d'hôte d'un en-tête Host, sans le port (IPv6 entre crochets accepté)."""
    value = (header or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def make_handler(api: CsiApi, token: str | None, hosts: set[str] | None = None) -> type[BaseHTTPRequestHandler]:
    permitted = hosts if hosts is not None else allowed_hosts()

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

        def _send_static(self, path: str) -> None:
            name, content_type = STATIC_FILES[path]
            data = (STATIC_DIR / name).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            return secrets.compare_digest(given.encode(), f"Bearer {token}".encode())

        def _handle(self, method: str) -> None:
            try:
                raw = b""
                length = 0
                if method == "POST":
                    # Corps lu AVANT toute réponse (bornée à 1 Mo) : répondre sans l'avoir lu fait couper la
                    # connexion par le système (Windows), et le client ne reçoit jamais le code d'erreur.
                    try:
                        length = max(0, int(self.headers.get("Content-Length") or 0))
                    except ValueError:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length invalide") from None
                    raw = self.rfile.read(min(length, 1 << 20)) if length else b""
                    self.close_connection = length > len(raw)
                if _host_name(self.headers.get("Host")) not in permitted:
                    raise ApiError(HTTPStatus.MISDIRECTED_REQUEST, "hôte non autorisé (CSI_API_ALLOWED_HOSTS)")
                url = urlparse(self.path)
                if method == "GET" and url.path in STATIC_FILES:   # la page elle-même ne contient aucune donnée
                    self._send_static(url.path)
                    return
                if not self._authorized():
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "jeton absent ou invalide")
                body = None
                if method == "POST":
                    if "application/json" not in (self.headers.get("Content-Type") or ""):
                        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type application/json requis")
                    if length <= 0 or length > MAX_BODY_BYTES:
                        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > 0 else HTTPStatus.BAD_REQUEST,
                                       f"corps JSON requis, {MAX_BODY_BYTES} octets au plus")
                    try:
                        body = json.loads(raw.decode("utf-8"))
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
    log.info("API et tableau de bord CSI (lecture et évaluation seulement) sur http://%s:%s/", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
