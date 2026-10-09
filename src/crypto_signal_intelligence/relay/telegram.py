"""Relais Telegram → F4 (docs/TELEGRAM_RELAY.md) : le DEUXIÈME bot du propriétaire, lu en `getUpdates`, dépose chaque
message reçu dans l'entrée en direct de F4_TELEGRAM (`POST /telegram/live` de l'API CSI).

Pourquoi un deuxième bot : un bot Telegram n'accepte qu'un seul lecteur `getUpdates` ; celui de BinanceSpotManager
est déjà pris par son worker. Ce relais n'évalue rien, ne décide rien, ne passe aucun ordre : il recopie le texte
(ou la légende d'une image), l'heure de réception et l'origine, au format que F4 lit déjà
(`forward/telegram_live.read_robot_file`, gelé, non modifié).

Secret : le jeton du bot est lu dans `CSI_TELEGRAM_RELAY_TOKEN` et n'existe que dans ce processus ; il n'est jamais
écrit, ni journalisé (les erreurs sont masquées). Seule adresse appelée hors CSI : https://api.telegram.org, sans
redirection. Le décalage `offset` n'avance qu'après un dépôt réussi ou une mise en attente locale : aucun message
n'est perdu si l'API CSI est arrêtée.

Sens retour (2026-10-09) : le même bot ENVOIE au propriétaire les appels de l'assistant de marché que l'API CSI met
en attente (`GET /assistant/outbox`, puis `POST /assistant/sent` pour les marquer). Le propriétaire n'est jamais un
inconnu : c'est `CSI_TELEGRAM_OWNER_CHAT_ID` s'il est défini, sinon la conversation privée depuis laquelle des messages
TRANSFÉRÉS d'un groupe ou d'un canal sont arrivés (son relais Telethon transfère ses groupes depuis son propre
compte) ; elle est apprise au premier transfert et gardée dans `proprietaire.json`. Un `/start` venu d'ailleurs est
refusé. Rien d'autre ne part : aucun ordre, aucune clé, aucun chiffre inventé ; l'envoi ne bloque jamais le relais.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("csi.relay.telegram")
TELEGRAM = "https://api.telegram.org"
UPDATES = ("message", "edited_message", "channel_post", "edited_channel_post")
POLL_SECONDS = 50
MAX_BATCH = 50                       # l'API CSI limite le corps à 16 Ko
MAX_PHOTO_BYTES = 4 * 1024 * 1024     # plus grande taille de photo Telegram acceptée par CSI
#: Réponses de CSI qui refusent le CONTENU (image invalide, trop lourde, mauvais format) : la photo est écartée et
#: notée ; toute autre erreur (accès 401/403, débit 429, panne) la garde en attente.
DEFINITIVE_REFUSALS = ("HTTP 400", "HTTP 413", "HTTP 415")
MAX_ASSISTANT = 20                   # envois au propriétaire par passage, jamais plus (contrat de l'API CSI)
MAX_TEXT = 4096                      # longueur maximale d'un message Telegram
MAX_RETRY_AFTER = 60.0               # attente maximale sur un 429 Telegram (une seule fois par passage)
OWNER_REMINDER_SECONDS = 3600.0      # « aucun propriétaire connu » : une ligne de journal par heure au plus
COMMANDS = ("start", "etat")         # commandes privées du propriétaire : jamais déposées dans F4
FORWARD_ORIGINS = ("chat", "channel")  # origines de transfert qui désignent un groupe ou un canal (jamais un particulier)
SEND_TIMEOUT = 10.0                  # un envoi Telegram : 20 envois ne retardent jamais le dépôt F4 de plus de ~3 min
PRIVATE_BOT = "Ce bot est privé."
WELCOME = "C'est bien toi : les appels de l'assistant de marché arriveront ici. Aucun ordre ne part d'ici."


class RelayError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelayConfig:
    token: str = field(repr=False)
    api_url: str = "http://csi-api:8503"
    api_token: str = field(default="", repr=False)
    chats: frozenset[str] = frozenset()          # vide : toutes les conversations où le bot reçoit des messages
    state_dir: Path = Path("/srv/relay")
    owner_chat_id: str = ""                      # CSI_TELEGRAM_OWNER_CHAT_ID : fixé sur le VPS, prioritaire sur l'apprentissage

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RelayConfig:
        env = dict(os.environ if env is None else env)
        token = env.get("CSI_TELEGRAM_RELAY_TOKEN", "").strip()
        if not token:
            raise RelayError("CSI_TELEGRAM_RELAY_TOKEN absent : rien à relayer (voir docs/TELEGRAM_RELAY.md)")
        chats = frozenset(c.strip() for c in env.get("CSI_TELEGRAM_RELAY_CHATS", "").split(",") if c.strip())
        return cls(token=token, api_url=env.get("CSI_TELEGRAM_RELAY_API", "http://csi-api:8503").rstrip("/"),
                   api_token=env.get("CSI_API_TOKEN", ""), chats=chats,
                   state_dir=Path(env.get("CSI_TELEGRAM_RELAY_STATE", "/srv/relay")),
                   owner_chat_id=env.get("CSI_TELEGRAM_OWNER_CHAT_ID", "").strip())


def masked(text: str, config: RelayConfig) -> str:
    """Jamais le jeton dans un message d'erreur ou un journal."""
    return text.replace(config.token, "<jeton>") if config.token else text


def masked_chat(chat_id: str | int | None) -> str:
    """Identifiant de conversation jamais en clair dans le journal : les 4 derniers chiffres sont masqués."""
    text = str(chat_id or "")
    return text[:-4] + "****" if len(text) > 4 else "****"


def _utc(seconds: int | None) -> str | None:
    return datetime.fromtimestamp(int(seconds), UTC).isoformat() if seconds else None


def forwarding_private_chat(update: dict) -> dict | None:
    """Le message privé (compte ↔ bot) qui TRANSFÈRE un message d'un groupe ou d'un canal (`forward_from_chat`, ou
    `forward_origin` de type chat/canal), sinon None. C'est ainsi que le relais reconnaît le propriétaire : son relais
    Telethon transfère ses groupes depuis son propre compte ; un simple texte privé n'apprend rien."""
    message = update.get("message")
    if not isinstance(message, dict) or (message.get("chat") or {}).get("type") != "private":
        return None
    origin = message.get("forward_origin") or {}
    forwarded = bool(message.get("forward_from_chat")) or (isinstance(origin, dict) and origin.get("type") in FORWARD_ORIGINS)
    return message if forwarded else None


def command_of(update: dict) -> tuple[str, dict] | None:
    """(commande, message) si la mise à jour est une commande du bot (`/start`, `/start@MonBot`, `/etat`) envoyée en
    conversation PRIVÉE, sinon None. Une commande n'est jamais un signal."""
    message = update.get("message")
    if not isinstance(message, dict) or (message.get("chat") or {}).get("type") != "private":
        return None
    text = str(message.get("text") or "").strip()
    if not text.startswith("/"):
        return None
    word = text.split(maxsplit=1)[0][1:].split("@", 1)[0].lower()
    return (word, message) if word in COMMANDS else None


def to_row(update: dict, *, chats: frozenset[str] = frozenset()) -> dict | None:
    """Une mise à jour Telegram → une ligne F4 (signal_id, source_chat_id, raw_text, received_at, edited), ou None
    (pas de texte, conversation non autorisée). L'heure est celle de l'arrivée du message chez le bot (`date`, ou
    `edit_date` pour une modification) ; un message transféré garde la conversation d'origine comme source."""
    kind = next((k for k in UPDATES if isinstance(update.get(k), dict)), None)
    if kind is None or command_of(update) is not None:
        return None
    message = update[kind]
    chat = str((message.get("chat") or {}).get("id", ""))
    if chats and chat not in chats:
        return None
    text = message.get("text") or message.get("caption") or ""
    if not str(text).strip():
        return None
    edited = kind.startswith("edited_")
    received = _utc(message.get("edit_date") if edited else message.get("date"))
    if received is None:
        return None
    origin = message.get("forward_origin") or {}
    source = (origin.get("chat") or origin.get("sender_chat") or {}).get("id") or (message.get("forward_from_chat") or {}).get("id")
    ident = f"{chat}:{message.get('message_id')}" + (f":edit{message.get('edit_date')}" if edited else "")
    return {"signal_id": ident, "source_chat_id": str(source or chat), "raw_text": str(text), "received_at": received,
            "edited": edited, "relay": "telegram", "relay_chat_id": chat}


Http = Callable[[str, str, dict | None, dict, float], dict]
Download = Callable[[str, float], bytes]


def photo_job(update: dict, *, chats: frozenset[str] = frozenset()) -> dict | None:
    """Photo d'un message (la plus grande sous 4 Mo) à lire par CSI (signaux publiés en image), ou None."""
    kind = next((k for k in UPDATES if isinstance(update.get(k), dict)), None)
    if kind is None or kind.startswith("edited_"):
        return None
    message = update[kind]
    chat = str((message.get("chat") or {}).get("id", ""))
    if chats and chat not in chats:
        return None
    sizes = [p for p in (message.get("photo") or []) if isinstance(p, dict) and (p.get("file_size") or 0) <= MAX_PHOTO_BYTES]
    if not sizes or not message.get("date"):
        return None
    best = max(sizes, key=lambda p: (p.get("width") or 0) * (p.get("height") or 0))
    origin = message.get("forward_origin") or {}
    source = (origin.get("chat") or origin.get("sender_chat") or {}).get("id") or (message.get("forward_from_chat") or {}).get("id")
    return {"file_id": best["file_id"], "file_unique_id": str(best.get("file_unique_id") or ""), "chat": chat,
            "message_id": str(message.get("message_id")), "received_at": _utc(message["date"]),
            "caption": str(message.get("caption") or ""), "origin_chat": str(source or "")}


def _retry_after(error: str) -> int:
    """Délai demandé par un 429 Telegram (`"retry_after": 5` ou « retry after 5 » dans le texte), 1 s à défaut."""
    import re
    found = re.search(r"retry[_ ]after\"?\s*:?\s*(\d+)", error)
    return max(1, int(found.group(1))) if found else 1


def summarize_state(payload: dict) -> str:
    """Cinq lignes au plus pour `/etat` : un résumé fourni par l'API (`resume` ou `summary`), sinon les premières
    valeurs simples de la réponse. Aucun chiffre n'est inventé : ce sont les champs tels que l'API les donne."""
    for key in ("resume", "summary"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return "\n".join(payload[key].strip().splitlines()[:5])
    lines = [f"{key} : {value}" for key, value in payload.items()
             if isinstance(value, (str, int, float, bool)) or value is None]
    return "\n".join(lines[:5]) or "assistant : réponse vide"


def _download(url: str, timeout: float) -> bytes:
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url)
        if response.status_code >= 300:
            raise RelayError(f"HTTP {response.status_code}")
        if len(response.content) > MAX_PHOTO_BYTES:
            raise RelayError("photo trop lourde")
        return response.content


def _http(method: str, url: str, body: dict | None, headers: dict, timeout: float) -> dict:
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.request(method, url, json=body, headers=headers)
        if response.status_code >= 300:
            raise RelayError(f"HTTP {response.status_code} : {response.text[:200]}")
        return response.json()


class Relay:
    def __init__(self, config: RelayConfig, *, http: Http | None = None, clock: Callable[[], float] = time.time,
                 download: Download | None = None, sleep: Callable[[float], None] = time.sleep):
        self.config, self.http, self.clock, self.download = config, http or _http, clock, download or _download
        self.sleep = sleep
        self._owner_reminded_at: float | None = None
        self.config.state_dir.mkdir(parents=True, exist_ok=True)

    def _csi_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        return headers

    # --- état local ------------------------------------------------------------------------------------------
    @property
    def _offset_path(self) -> Path:
        return self.config.state_dir / "offset.json"

    @property
    def _spool(self) -> Path:
        return self.config.state_dir / "en_attente.jsonl"

    def offset(self) -> int:
        try:
            return int(json.loads(self._offset_path.read_text(encoding="utf-8"))["offset"])
        except (OSError, ValueError, KeyError):
            return 0

    def _save_offset(self, value: int) -> None:
        tmp = self._offset_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"offset": value}), encoding="utf-8")
        tmp.replace(self._offset_path)

    # --- échanges -------------------------------------------------------------------------------------------
    def fetch(self, *, timeout: int = POLL_SECONDS) -> list[dict]:
        url = f"{TELEGRAM}/bot{self.config.token}/getUpdates"
        body = {"offset": self.offset(), "timeout": timeout, "allowed_updates": list(UPDATES)}
        try:
            reply = self.http("POST", url, body, {}, timeout + 10)
        except Exception as exc:  # noqa: BLE001 - le message d'erreur peut contenir l'URL, donc le jeton
            raise RelayError(masked(f"{type(exc).__name__}: {exc}", self.config)) from None
        if not reply.get("ok"):
            raise RelayError(masked(f"Telegram : {reply.get('description', 'réponse refusée')}", self.config))
        return list(reply.get("result") or [])

    def deliver(self, rows: list[dict]) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        try:
            for start in range(0, len(rows), MAX_BATCH):
                self.http("POST", f"{self.config.api_url}/telegram/live", {"signals": rows[start:start + MAX_BATCH]}, headers, 30)
        except Exception as exc:  # noqa: BLE001 - API CSI arrêtée : mise en attente, rien n'est perdu
            log.warning("dépôt vers CSI impossible (%s) : %d message(s) mis en attente", type(exc).__name__, len(rows))
            return False
        return True

    @property
    def _photo_spool(self) -> Path:
        return self.config.state_dir / "photos_en_attente"

    def _queue_photos(self, updates: list[dict]) -> int:
        """Photos à transmettre, gardées sur disque AVANT d'avancer le décalage Telegram (rien n'est perdu)."""
        self._photo_spool.mkdir(parents=True, exist_ok=True)
        queued = 0
        for update in updates:
            job = photo_job(update, chats=self.config.chats)
            if job:
                (self._photo_spool / f"{job['chat']}_{job['message_id']}.json").write_text(json.dumps(job), encoding="utf-8")
                queued += 1
        return queued

    def send_photos(self) -> dict:
        """Télécharge chaque photo en attente chez Telegram et la dépose dans CSI (`POST /telegram/image`) ; une photo
        qui échoue reste en attente."""
        import base64
        out = {"sent": 0, "waiting": 0}
        if not self._photo_spool.exists():
            return out
        headers = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        for path in sorted(self._photo_spool.glob("*.json")):
            job = json.loads(path.read_text(encoding="utf-8"))
            try:
                info = self.http("POST", f"{TELEGRAM}/bot{self.config.token}/getFile", {"file_id": job["file_id"]}, {}, 30)
                file_path = str((info.get("result") or {}).get("file_path") or "")
                if not info.get("ok") or not file_path:
                    raise RelayError("getFile refusé")
                image = self.download(f"{TELEGRAM}/file/bot{self.config.token}/{file_path}", 60)
                ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "jpg"
                try:
                    self.http("POST", f"{self.config.api_url}/telegram/image",
                              {k: job.get(k, "") for k in ("chat", "message_id", "received_at", "caption", "file_unique_id",
                                                            "origin_chat")}
                              | {"ext": ext, "image_b64": base64.b64encode(image).decode()}, headers, 60)
                except RelayError as exc:
                    if str(exc).startswith(DEFINITIVE_REFUSALS):     # contenu refusé par CSI : on n'insiste pas
                        log.warning("photo %s refusée par CSI : %s", path.stem, masked(str(exc), self.config))
                        with (self._photo_spool.parent / "photos_refusees.jsonl").open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps({"job": job, "error": masked(str(exc), self.config)}) + "\n")
                        path.unlink(missing_ok=True)
                        continue
                    raise                                        # 401, 403, 429, 5xx : on réessaiera
            except RelayError as exc:
                log.warning("photo %s en attente : %s", path.stem, masked(f"{type(exc).__name__}: {exc}", self.config))
                out["waiting"] += 1
                continue
            except Exception as exc:  # noqa: BLE001 - le message peut contenir l'URL, donc le jeton
                log.warning("photo %s en attente : %s", path.stem, masked(f"{type(exc).__name__}: {exc}", self.config))
                out["waiting"] += 1
                continue
            path.unlink(missing_ok=True)
            out["sent"] += 1
        return out

    # --- propriétaire (sens retour) ---------------------------------------------------------------------------
    @property
    def _owner_path(self) -> Path:
        return self.config.state_dir / "proprietaire.json"

    @property
    def _refused_path(self) -> Path:
        return self.config.state_dir / "prives_refuses.json"

    @property
    def _to_mark_path(self) -> Path:
        return self.config.state_dir / "a_marquer.json"

    def owner(self) -> str | None:
        """Conversation privée du propriétaire : `CSI_TELEGRAM_OWNER_CHAT_ID` d'abord, sinon le `/start` enregistré."""
        if self.config.owner_chat_id:
            return self.config.owner_chat_id
        try:
            chat = str(json.loads(self._owner_path.read_text(encoding="utf-8")).get("chat_id") or "")
        except (OSError, ValueError, AttributeError):
            return None
        return chat or None

    def _read_json_list(self, path: Path) -> list[str]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(v) for v in value] if isinstance(value, list) else []

    def _write_json(self, path: Path, value: object) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def send_text(self, chat_id: str, text: str) -> float | None:
        """Un message Telegram en texte brut (aucun `parse_mode` : les textes contiennent « < » et « > »). Renvoie None
        si l'envoi a réussi, sinon le délai `retry_after` demandé par Telegram (0 pour une autre erreur)."""
        body = {"chat_id": chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}
        try:
            reply = self.http("POST", f"{TELEGRAM}/bot{self.config.token}/sendMessage", body, {}, SEND_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - le message peut contenir l'URL, donc le jeton
            error = masked(f"{type(exc).__name__}: {exc}", self.config)
            log.warning("envoi vers %s refusé : %s", masked_chat(chat_id), error)
            return float(_retry_after(error)) if "429" in error else 0.0
        if not reply.get("ok"):
            log.warning("envoi vers %s refusé : %s", masked_chat(chat_id), masked(str(reply.get("description", "")), self.config))
            return 0.0
        return None

    def learn_owner(self, updates: list[dict]) -> bool:
        """Sans `CSI_TELEGRAM_OWNER_CHAT_ID` ni `proprietaire.json`, la PREMIÈRE conversation privée qui transfère un
        message d'un groupe ou d'un canal devient le propriétaire (le premier apprenant gagne ; un `/start` ou un texte
        privé sans origine de transfert n'apprend rien). Renvoie True si un propriétaire vient d'être enregistré."""
        if self.owner() is not None:
            return False
        for update in updates:
            message = forwarding_private_chat(update)
            if message is None:
                continue
            chat = str((message.get("chat") or {}).get("id", ""))
            user = str((message.get("from") or {}).get("username") or "")
            self._write_json(self._owner_path, {"chat_id": chat, "first_seen": datetime.now(UTC).isoformat(),
                                                "username_masked": (user[:2] + "…") if user else ""})
            log.info("propriétaire reconnu par ses transferts : conversation %s", masked_chat(chat))
            return True
        return False

    def handle_commands(self, updates: list[dict]) -> dict:
        """`/start` en privé : accepté seulement depuis la conversation du propriétaire (variable d'environnement ou
        transferts déjà vus) ; toute autre conversation privée reçoit « Ce bot est privé. » une seule fois et n'est
        jamais enregistrée. `/etat` du propriétaire : cinq lignes de `GET /assistant`. Aucune commande n'est déposée
        dans F4."""
        out = {"welcomed": 0, "refused": 0, "replied": 0}
        for update in updates:
            found = command_of(update)
            if found is None:
                continue
            word, message = found
            chat = str((message.get("chat") or {}).get("id", ""))
            owner = self.owner()
            if word == "start":
                if owner is not None and chat == owner:
                    self.send_text(chat, WELCOME)
                    out["welcomed"] += 1
                else:
                    refused = self._read_json_list(self._refused_path)
                    if chat not in refused:
                        self._write_json(self._refused_path, [*refused, chat])
                        log.info("/start d'une conversation privée inconnue (%s) : refusée", masked_chat(chat))
                        out["refused"] += 1
                        self.send_text(chat, PRIVATE_BOT)
            elif word == "etat" and owner is not None and chat == owner:
                self.send_text(chat, self._state_summary())
                out["replied"] += 1
        return out

    def _state_summary(self) -> str:
        try:
            payload = self.http("GET", f"{self.config.api_url}/assistant", None, self._csi_headers(), 30)
        except Exception:  # noqa: BLE001 - route absente ou API arrêtée : réponse sobre
            return "assistant indisponible"
        return summarize_state(payload) if isinstance(payload, dict) else "assistant indisponible"

    def send_assistant(self) -> dict:
        """Les appels de l'assistant en attente dans l'API CSI (`GET /assistant/outbox`, 20 au plus) partent vers le
        propriétaire, puis sont marqués (`POST /assistant/sent`). Sans propriétaire connu : rien n'est envoyé ni
        marqué, une ligne de journal par heure. API CSI injoignable : silence, passage suivant. Un 429 Telegram est
        respecté une seule fois par passage ; le reste attend le passage suivant."""
        out = {"sent": 0, "failed": 0, "owner": self.owner() is not None}
        to_mark = self._read_json_list(self._to_mark_path)       # envoyés au passage précédent, pas encore marqués
        owner = self.owner()
        if owner is None:
            now = self.clock()
            if self._owner_reminded_at is None or now - self._owner_reminded_at >= OWNER_REMINDER_SECONDS:
                self._owner_reminded_at = now
                log.info("aucun propriétaire connu : transférer un message de groupe au bot depuis son compte, ou fixer "
                         "CSI_TELEGRAM_OWNER_CHAT_ID ; les appels de l'assistant attendent")
        else:
            try:
                reply = self.http("GET", f"{self.config.api_url}/assistant/outbox", None, self._csi_headers(), 30)
            except Exception:  # noqa: BLE001 - API CSI arrêtée ou route absente : passage suivant
                reply = {}
            messages = [m for m in (reply.get("messages") or []) if isinstance(m, dict) and m.get("id")]
            waited = False
            for message in messages[:MAX_ASSISTANT]:
                ident = str(message["id"])
                if ident in to_mark:
                    continue
                delay = self.send_text(owner, str(message.get("text") or ""))
                if delay and not waited:                           # 429 : on attend une fois, puis on réessaie
                    waited = True
                    self.sleep(min(delay, MAX_RETRY_AFTER))
                    delay = self.send_text(owner, str(message.get("text") or ""))
                if delay is None:
                    to_mark.append(ident)
                    out["sent"] += 1
                else:
                    out["failed"] += 1
                    if delay:                                      # encore 429 : le reste attend le passage suivant
                        break
        if to_mark:
            try:
                self.http("POST", f"{self.config.api_url}/assistant/sent", {"ids": to_mark}, self._csi_headers(), 30)
            except Exception:  # noqa: BLE001 - on marquera au passage suivant, sans renvoyer ces messages
                self._write_json(self._to_mark_path, to_mark)
            else:
                self._to_mark_path.unlink(missing_ok=True)
        return out

    def cycle(self) -> dict:
        """Un passage : renvoie d'abord ce qui attend, puis lit les nouveaux messages, les dépose, avance le décalage,
        puis traite les commandes privées et envoie les appels de l'assistant (jamais bloquant pour F4)."""
        pending = self._read_spool()
        if pending and self.deliver(pending):
            self._spool.unlink(missing_ok=True)
            pending = []
        updates = self.fetch()
        rows = [r for r in (to_row(u, chats=self.config.chats) for u in updates) if r]
        delivered = bool(rows) and not pending and self.deliver(rows)
        if rows and not delivered:
            with self._spool.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        photos = self._queue_photos(updates)
        try:
            self.learn_owner(updates)
            self.handle_commands(updates)
        except Exception as exc:  # noqa: BLE001 - une commande ne doit jamais empêcher le décalage d'avancer
            log.warning("commande privée ignorée : %s", masked(f"{type(exc).__name__}: {exc}", self.config))
        if updates:
            self._save_offset(max(int(u["update_id"]) for u in updates) + 1)
        sent = self.send_photos() if photos or self._photo_spool.exists() else {"sent": 0, "waiting": 0}
        try:
            assistant = self.send_assistant()
        except Exception as exc:  # noqa: BLE001 - le sens retour ne casse jamais le relais F4
            log.warning("envoi des appels de l'assistant interrompu : %s", masked(f"{type(exc).__name__}: {exc}", self.config))
            assistant = {"sent": 0, "failed": 1, "owner": self.owner() is not None}
        return {"updates": len(updates), "relayed": len(rows) if delivered else 0,
                "waiting": len(pending) + (len(rows) if rows and not delivered else 0),
                "photos_sent": sent["sent"], "photos_waiting": sent["waiting"],
                "assistant_sent": assistant["sent"], "assistant_failed": assistant["failed"], "owner": assistant["owner"]}

    def _read_spool(self) -> list[dict]:
        if not self._spool.exists():
            return []
        return [json.loads(line) for line in self._spool.read_text(encoding="utf-8").splitlines() if line.strip()]


def quiet_http_logs() -> None:
    """httpx écrit chaque requête (URL complète, donc le jeton du bot) au niveau INFO : jamais dans le journal."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    quiet_http_logs()
    config = RelayConfig.from_env()
    relay = Relay(config)
    owner = relay.owner()
    log.info("relais Telegram démarré (conversations autorisées : %s) → %s ; propriétaire : %s",
             ", ".join(sorted(config.chats)) or "toutes", config.api_url,
             f"conversation {masked_chat(owner)}" if owner else "inconnu (reconnu au premier transfert d'un groupe)")
    pause = 5.0
    while True:
        try:
            out = relay.cycle()
            if out["updates"] or out["photos_sent"] or out["assistant_sent"] or out["assistant_failed"]:
                log.info("%d mise(s) à jour, %d relayée(s), %d en attente ; photos : %d transmise(s), %d en attente ; "
                         "assistant : %d envoi(s), %d échec(s), propriétaire %s",
                         out["updates"], out["relayed"], out["waiting"], out["photos_sent"], out["photos_waiting"],
                         out["assistant_sent"], out["assistant_failed"], "connu" if out["owner"] else "inconnu")
            pause = 5.0
        except RelayError as exc:
            log.warning("%s ; nouvel essai dans %.0f s", exc, pause)
            time.sleep(pause)
            pause = min(pause * 2, 300.0)


if __name__ == "__main__":
    main()
