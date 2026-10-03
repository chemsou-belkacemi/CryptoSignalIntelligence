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


class RelayError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelayConfig:
    token: str = field(repr=False)
    api_url: str = "http://csi-api:8503"
    api_token: str = field(default="", repr=False)
    chats: frozenset[str] = frozenset()          # vide : toutes les conversations où le bot reçoit des messages
    state_dir: Path = Path("/srv/relay")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RelayConfig:
        env = dict(os.environ if env is None else env)
        token = env.get("CSI_TELEGRAM_RELAY_TOKEN", "").strip()
        if not token:
            raise RelayError("CSI_TELEGRAM_RELAY_TOKEN absent : rien à relayer (voir docs/TELEGRAM_RELAY.md)")
        chats = frozenset(c.strip() for c in env.get("CSI_TELEGRAM_RELAY_CHATS", "").split(",") if c.strip())
        return cls(token=token, api_url=env.get("CSI_TELEGRAM_RELAY_API", "http://csi-api:8503").rstrip("/"),
                   api_token=env.get("CSI_API_TOKEN", ""), chats=chats,
                   state_dir=Path(env.get("CSI_TELEGRAM_RELAY_STATE", "/srv/relay")))


def masked(text: str, config: RelayConfig) -> str:
    """Jamais le jeton dans un message d'erreur ou un journal."""
    return text.replace(config.token, "<jeton>") if config.token else text


def _utc(seconds: int | None) -> str | None:
    return datetime.fromtimestamp(int(seconds), UTC).isoformat() if seconds else None


def to_row(update: dict, *, chats: frozenset[str] = frozenset()) -> dict | None:
    """Une mise à jour Telegram → une ligne F4 (signal_id, source_chat_id, raw_text, received_at, edited), ou None
    (pas de texte, conversation non autorisée). L'heure est celle de l'arrivée du message chez le bot (`date`, ou
    `edit_date` pour une modification) ; un message transféré garde la conversation d'origine comme source."""
    kind = next((k for k in UPDATES if isinstance(update.get(k), dict)), None)
    if kind is None:
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
    return {"file_id": best["file_id"], "chat": chat, "message_id": str(message.get("message_id")),
            "received_at": _utc(message["date"]), "caption": str(message.get("caption") or "")}


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
                 download: Download | None = None):
        self.config, self.http, self.clock, self.download = config, http or _http, clock, download or _download
        self.config.state_dir.mkdir(parents=True, exist_ok=True)

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
                self.http("POST", f"{self.config.api_url}/telegram/image",
                          {k: job[k] for k in ("chat", "message_id", "received_at", "caption")}
                          | {"ext": ext, "image_b64": base64.b64encode(image).decode()}, headers, 60)
            except Exception as exc:  # noqa: BLE001 - le message peut contenir l'URL, donc le jeton
                log.warning("photo %s en attente : %s", path.stem, masked(f"{type(exc).__name__}: {exc}", self.config))
                out["waiting"] += 1
                continue
            path.unlink(missing_ok=True)
            out["sent"] += 1
        return out

    def cycle(self) -> dict:
        """Un passage : renvoie d'abord ce qui attend, puis lit les nouveaux messages, les dépose, avance le décalage."""
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
        if updates:
            self._save_offset(max(int(u["update_id"]) for u in updates) + 1)
        sent = self.send_photos() if photos or self._photo_spool.exists() else {"sent": 0, "waiting": 0}
        return {"updates": len(updates), "relayed": len(rows) if delivered else 0,
                "waiting": len(pending) + (len(rows) if rows and not delivered else 0),
                "photos_sent": sent["sent"], "photos_waiting": sent["waiting"]}

    def _read_spool(self) -> list[dict]:
        if not self._spool.exists():
            return []
        return [json.loads(line) for line in self._spool.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    config = RelayConfig.from_env()
    relay = Relay(config)
    log.info("relais Telegram démarré (conversations autorisées : %s) → %s",
             ", ".join(sorted(config.chats)) or "toutes", config.api_url)
    pause = 5.0
    while True:
        try:
            out = relay.cycle()
            if out["updates"] or out["photos_sent"]:
                log.info("%d mise(s) à jour, %d relayée(s), %d en attente ; photos : %d transmise(s), %d en attente",
                         out["updates"], out["relayed"], out["waiting"], out["photos_sent"], out["photos_waiting"])
            pause = 5.0
        except RelayError as exc:
            log.warning("%s ; nouvel essai dans %.0f s", exc, pause)
            time.sleep(pause)
            pause = min(pause * 2, 300.0)


if __name__ == "__main__":
    main()
