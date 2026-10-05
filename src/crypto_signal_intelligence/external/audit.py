"""Bilan mesuré d'un groupe Telegram sur son historique (docs/EXTERNAL_SIGNALS.md, « Historique d'un groupe »).

Chaque signal passé est rejoué sur les bougies 15 min clôturées qui suivent sa publication : rien de ce qui se
passe après l'entrée n'est connu au moment de la décision. Trois conventions, toujours affichées ensemble :
- `tp1_contact` : la convention du registre de CSI (entrée 1, sortie à TP1 ou au stop touché, 7 jours au plus) ;
- `tp1_regle_du_signal` : la même, mais le stop ne joue qu'à la CLÔTURE d'une bougie de l'unité écrite dans le
  signal (« Stop: 0.0739 (4h) ») ; identique à la première si le signal ne précise rien ;
- `echelle_bsm` : comme BinanceSpotManager l'exécute (entrée 1, tous les objectifs, stop fixe au contact, aucune
  sortie temporelle) : politique `BSM_MARKET_TP_FIXED_SL_V2`.

Ce bilan MESURE une source externe. Ce n'est pas un essai d'une stratégie de CSI : il ne compte pas dans le
programme d'essais, et il ne passe aucun ordre. Limites écrites dans chaque rapport : un historique exporté ne
contient que les messages encore présents (un groupe peut supprimer ses pertes) ; un message modifié après coup
est signalé ; un résultat passé ne dit rien du prochain signal.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.exits import OpenPosition, exit_policy, pnl_per_unit
from ..backtest.metrics import day_block_ci95
from ..config import CostScenario, Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from .parser import ExternalSignal, group_of, parse
from .record import MIN_DAYS, MIN_RESOLVED
from .registry import limit_fill, replay

TP1_TOUCH, TP1_RULE, LADDER, TRAILING = "tp1_contact", "tp1_regle_du_signal", "echelle_bsm", "stop_suiveur"
CONVENTIONS = (TP1_TOUCH, TP1_RULE, LADDER, TRAILING)
CONVENTION_LABELS = {
    TP1_TOUCH: "TP1 ou stop au contact (convention de CSI)",
    TP1_RULE: "TP1 ou stop à la clôture de bougie écrite dans le signal",
    LADDER: "tous les objectifs, stop fixe, comme BinanceSpotManager",
    TRAILING: "ta gestion : stop à l'entrée 1 après TP1, puis à TP(k−2) après TPk",
}
LADDER_POLICY = "BSM_MARKET_TP_FIXED_SL_V2"
OK, UNREADABLE, DUPLICATE, NO_DATA, INVALID, PLAYED, STALE = (
    "OK", "ILLISIBLE", "DOUBLON", "SANS_DONNEES", "INVALIDE", "DEJA_JOUE", "PERIME")
STEP = pd.Timedelta(minutes=15)
FOLLOW_DAYS = 30                       # suivi de l'échelle : au-delà, la position est valorisée au dernier prix
DUPLICATE_DAYS = 7
MAX_REFERENCE_AGE = pd.Timedelta(hours=2)   # dernier prix connu avant la publication : pas plus vieux
ALL = "ensemble"
# Preuve sur historique (docs/EXTERNAL_SIGNALS.md, « Avis lié au groupe ») : mesurée avec la gestion jugée par
# l'avis (réglage external.management) : ta gestion par défaut, sinon l'échelle de BSM.
PROOF_CONVENTION = TRAILING
PROOF_RESOLVED, PROOF_DAYS = 50, 20
MAX_MISSING_SHARE = 0.10            # messages absents de la numérotation de Telegram (supprimés) : 10 % au plus
PROOF_VALID_DAYS = 30               # une preuve sur historique se refait (nouvel export) au-delà
LIVE = "reçus en direct"
Bars = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame | None]


@dataclass(frozen=True)
class HistoryItem:
    text: str
    received_at: datetime
    message_id: str = ""
    edited: bool = False
    group: str = ""
    missing_share: float | None = None     # part de numéros absents dans le chat exporté ; None : reçu en direct
    from_image: bool = False               # niveaux lus sur l'image du message (external/chart_ocr.py)
    image_rejected: bool = False           # image lue mais ignorée (lecture douteuse) : comptée comme illisible
    image_status: str = ""                 # SUR, A_VALIDER ou IGNOREE (seules les SUR sont mesurées)


@dataclass
class AuditRow:
    received_at: str
    group: str
    status: str
    reason: str = ""
    symbol: str = ""
    entry: float | None = None
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    stop_timeframe: str = ""
    stop_pct: float | None = None
    tp1_pct: float | None = None
    edited: bool = False
    message_id: str = ""
    missing_share: float | None = None
    outcomes: dict[str, dict] = field(default_factory=dict)       # convention → {issue, r}
    from_image: bool = False
    image_rejected: bool = False


@dataclass
class AuditReport:
    generated_at: str
    weights: str
    rows: list[AuditRow]
    summary: dict[str, dict]
    notes: list[str]

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "weights": self.weights, "summary": self.summary,
                "notes": self.notes, "conventions": CONVENTION_LABELS, "rows": [asdict(r) for r in self.rows]}


# --- Lecture de l'historique ------------------------------------------------------------------------------

def _flatten(text) -> str:
    """Champ `text` d'un export Telegram : une chaîne, ou une liste de morceaux (texte brut et entités)."""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in text
                       if isinstance(part, (str, dict)))
    return ""


#: Lecteur d'image : (chemin, légende) → (issue, texte) ; issue SUR, A_VALIDER ou IGNOREE (external/chart_ocr.py).
ImageReader = Callable[[Path, str], tuple[str, str | None]]


def chart_reader() -> ImageReader:
    """Lecteur d'images de signaux (OpenCV + RapidOCR, extra « ocr ») : chemin de l'image et légende du message →
    texte de signal standard, ou None au moindre doute. Lève RuntimeError si les bibliothèques manquent."""
    from . import chart_ocr
    if not chart_ocr.available():
        raise RuntimeError("lecture des images impossible : installer l'extra « ocr » (opencv-python-headless, rapidocr, onnxruntime)")
    reader = chart_ocr.Lecteur()

    def read(path: Path, caption: str) -> tuple[str, str | None]:
        try:
            out = chart_ocr.classify_image(reader.analyse(path), caption=caption)
        except Exception:  # noqa: BLE001 - une image qui fait échouer la lecture est ignorée, l'audit continue
            return chart_ocr.IGNORED, None
        return out["status"], out["text"]

    return read


def read_telegram_export(payload, *, images_dir: Path | None = None, image_reader: ImageReader | None = None) -> list[HistoryItem]:
    """Export JSON de Telegram Desktop (« Exporter l'historique », format JSON) : un groupe, ou tous les chats.
    L'heure vient de `date_unixtime` (UTC) ; un message transféré porte le nom de son groupe d'origine.

    Avec `images_dir` (dossier de l'export) et `image_reader`, un message à IMAGE dont le texte n'est pas un signal
    lisible est lu sur son image ; le texte reconstruit remplace la légende et l'élément porte `from_image`. Une image
    douteuse est gardée comme message ILLISIBLE (`image_rejected`), pour que le taux de rejet se voie : jamais de
    niveau deviné. Une RÉPONSE à un message (« TP1 ✅ » avec la capture mise à jour) n'est jamais lue comme un
    nouveau signal ; une image hors du dossier de l'export (chemin absolu ou « .. ») est ignorée."""
    if not isinstance(payload, dict):
        raise ValueError("export Telegram attendu : un objet JSON avec « messages » ou « chats »")
    chats = payload.get("chats", {}).get("list") if isinstance(payload.get("chats"), dict) else [payload]
    if not isinstance(chats, list) or not all(isinstance(c, dict) for c in chats):
        raise ValueError("export Telegram illisible : liste de chats attendue")
    items = []
    for chat in chats:
        name = str(chat.get("name") or f"chat {chat.get('id', '')}").strip()
        messages = chat.get("messages")
        if not isinstance(messages, list):
            raise ValueError(f"export Telegram illisible : « messages » absent ({name})")
        # Telegram numérote les messages d'un chat sans trou ; un numéro absent est un message supprimé (ou exclu
        # de l'export). Un groupe qui efface ses pertes laisse donc des trous.
        ids = sorted({int(m["id"]) for m in messages if isinstance(m, dict) and str(m.get("id", "")).isdigit()})
        missing = (ids[-1] - ids[0] + 1 - len(ids)) / (ids[-1] - ids[0] + 1) if ids else 1.0
        for message in messages:
            if not isinstance(message, dict) or message.get("type") != "message":
                continue
            text = caption = _flatten(message.get("text"))
            from_image = rejected = False
            image_status = ""
            photo = message.get("photo")
            if image_reader is not None and images_dir is not None and isinstance(photo, str) and parse(text).errors \
                    and "reply_to_message_id" not in message:
                path = _inside(Path(images_dir), photo)
                if path is not None:
                    image_status, rebuilt = image_reader(path, text)
                    if image_status == "SUR" and rebuilt:
                        text, from_image = rebuilt, True
                    else:                  # A_VALIDER : jamais simulée sans validation ; IGNOREE : illisible
                        text, rejected = text if text.strip() else "[image non lue]", True
            if not text.strip():
                continue
            try:
                received = datetime.fromtimestamp(int(message["date_unixtime"]), UTC)
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"message {message.get('id')} sans « date_unixtime » : export trop ancien ou "
                                 "modifié, l'heure exacte est nécessaire") from None
            origin = message.get("forwarded_from")
            items.append(HistoryItem(text=text, received_at=received, message_id=str(message.get("id", "")),
                                     edited="edited_unixtime" in message or "edited" in message,
                                     group=group_of(caption) or group_of(text) or (str(origin).strip() if origin else name),
                                     missing_share=round(missing, 4), from_image=from_image, image_rejected=rejected,
                                     image_status=image_status))
    return items


def _inside(root: Path, relative: str) -> Path | None:
    """Fichier image de l'export, seulement s'il est DANS le dossier de l'export."""
    base = root.resolve()
    path = (base / relative).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


def read_bsm_inbox(path: Path) -> list[HistoryItem]:
    """Messages Telegram reçus EN DIRECT par BinanceSpotManager (sa boîte `signals.sqlite3`, lecture seule) :
    texte et heure d'origine enregistrés à la réception, donc sans biais de suppression."""
    with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
        stamp = "source_timestamp" if "source_timestamp" in columns else "0 AS source_timestamp"
        rows = db.execute(f"SELECT raw, received, {stamp}, external_id FROM signals WHERE source='telegram' "
                          "ORDER BY received").fetchall()
    return [HistoryItem(text=row["raw"], message_id=str(row["external_id"]), group=group_of(row["raw"]),
                        received_at=datetime.fromtimestamp(float(row["source_timestamp"] or row["received"]), UTC))
            for row in rows]


# --- Bougies ----------------------------------------------------------------------------------------------

def market_bars(settings: Settings, *, now: datetime, client: PublicHttpClient | None = None) -> Bars:
    """Bougies 15 min clôturées : le magasin local s'il couvre la période, sinon l'API PUBLIQUE de Binance Spot
    (klines : aucune clé, aucun ordre). Une paire inconnue de Binance renvoie None."""
    store = CandleStore(settings.data_dir)
    now_ts = pd.Timestamp(now)
    state: dict[str, PublicHttpClient] = {}

    def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
        end = min(end, now_ts)
        stored = store.load_since(symbol, "15m", start.floor("D"))
        if not stored.empty and stored["open_time"].min() <= start + STEP and stored["open_time"].max() >= end - 2 * STEP:
            return stored[(stored["open_time"] >= start) & (stored["open_time"] <= end)].reset_index(drop=True)
        rest = state.setdefault("rest", client or PublicHttpClient.rest(settings.data.rest_base_url))
        rows: list[list] = []
        since, limit_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        try:
            while since <= limit_ms:
                batch = rest.get_json("/api/v3/klines", {"symbol": symbol, "interval": "15m", "startTime": since,
                                                         "endTime": limit_ms, "limit": 1000})
                if not batch:
                    break
                rows += batch
                since = int(batch[-1][0]) + 1
                if len(batch) < 1000:
                    break
        except HttpError:
            return None
        frame = pd.DataFrame([[pd.Timestamp(int(k[0]), unit="ms", tz="UTC"), float(k[1]), float(k[2]), float(k[3]),
                               float(k[4])] for k in rows], columns=["open_time", "open", "high", "low", "close"])
        return frame[frame["open_time"] + STEP <= now_ts].reset_index(drop=True)     # bougies clôturées seulement

    return fetch


# --- Mesure d'un signal ----------------------------------------------------------------------------------

def stop_bars(timeframe: str) -> int:
    """Unité du stop écrite dans le signal (« 4h », « 15m », « 30 min ») en bougies 15 min ; 0 si absente ou
    non multiple de 15 minutes (le stop est alors compté au contact)."""
    match = re.fullmatch(r"(\d+)\s*(h|min|m)", (timeframe or "").strip().lower())
    if not match:
        return 0
    minutes = int(match[1]) * (60 if match[2] == "h" else 1)
    return minutes // 15 if minutes >= 15 and minutes % 15 == 0 else 0


def ladder_weights(count: int, rule: str) -> tuple[float, ...]:
    """Parts vendues à chaque objectif : `equal` (parts égales) ou `early` (n, n−1, … 1 : davantage au début,
    soit 33 / 27 / 20 / 13 / 7 % pour cinq objectifs)."""
    if rule == "equal":
        return tuple(1 / count for _ in range(count))
    if rule == "early":
        total = count * (count + 1) / 2
        return tuple((count - k) / total for k in range(count))
    raise ValueError(f"répartition inconnue : {rule} (early | equal)")


def replay_ladder(bars: pd.DataFrame, *, entry: float, stop: float, targets: list[float], weights: tuple[float, ...],
                  entry_window: int, costs: CostScenario, follow_bars: int) -> tuple[str, float | None, bool]:
    """(issue, R net, provisoire) : entrée 1 puis tous les objectifs, stop fixe au contact, sans sortie
    temporelle (profil de BSM). Une position encore ouverte est valorisée au dernier prix connu : résultat
    PROVISOIRE (`EN_COURS…`) tant que le suivi n'a pas duré `follow_bars` bougies, définitif ensuite (`OUVERT…`)."""
    if bars.empty:
        return "PENDING", None, False
    opens, highs, lows, closes = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    fee = costs.fee_bps / 1e4
    market_cost = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    fill = limit_fill(opens, lows, entry=entry, entry_window=entry_window, market_cost=market_cost)
    if fill is None:
        return ("UNFILLED" if len(bars) >= entry_window else "PENDING"), None, False
    start, price, touched = fill
    risk = entry - stop
    if price <= stop:                                    # ouverture déjà sous le stop : vendu aussitôt, au marché
        return "SL", round(float((opens[start] * (1 - market_cost) * (1 - fee) - price * (1 + fee)) / risk), 4), False
    position = OpenPosition(entry=price, initial_stop=stop, targets=tuple(targets), weights=weights,
                            policy=exit_policy(LADDER_POLICY))
    last = min(start + follow_bars, len(bars))
    for k in range(start, last):
        first = k == start
        position.process_bar(opens[k], highs[k], lows[k], closes[k], first_bar=first, touched=touched and first,
                             market_cost=market_cost, time_limit_reached=False)
        if position.closed:
            break
    reached = sum(f.reason == "TP" for f in position.fills)
    provisional = False
    if not position.closed:
        provisional = len(bars) < start + follow_bars
        position.censor(closes[last - 1] * (1 - market_cost))
        label = ("EN_COURS" if provisional else "OUVERT") + (f"_TP{reached}" if reached else "")
    elif position.exit_reason == "TP":
        label = "TOUS_TP"
    else:
        label = f"TP{reached}_PUIS_SL" if reached else "SL"
    return label, round(float(pnl_per_unit(position.fills, price, fee) / risk), 4), provisional


def measure(signal: ExternalSignal, bars: pd.DataFrame, received: pd.Timestamp, *, settings: Settings,
            weights: str) -> tuple[str, str, dict[str, dict]]:
    """(statut, raison, issues par convention) d'un signal lisible, avec les bougies de sa paire."""
    cfg, costs = settings.external, settings.costs["central"]
    assert signal.stop is not None
    entry, stop, targets = signal.entries[0], float(signal.stop), signal.targets
    before = bars[bars["open_time"] + STEP <= received]
    if before.empty or received - (before["open_time"].iloc[-1] + STEP) > MAX_REFERENCE_AGE:
        return NO_DATA, "aucune bougie clôturée juste avant la publication", {}
    close = float(before["close"].iloc[-1])
    if close <= stop:
        return INVALID, f"prix {close:g} déjà au stop {stop:g} ou dessous à la publication", {}
    if close >= targets[0]:
        return PLAYED, f"prix {close:g} déjà à TP1 {targets[0]:g} ou au-dessus à la publication", {}
    if (entry / close - 1) * 100 > cfg.max_entry_deviation_pct:
        return STALE, f"entrée {entry:g} à plus de {cfg.max_entry_deviation_pct:g} % au-dessus du prix {close:g}", {}
    after = bars[bars["open_time"] >= received.ceil("15min")].reset_index(drop=True)
    outcomes = {}
    for convention, close_bars in ((TP1_TOUCH, 0), (TP1_RULE, stop_bars(signal.stop_timeframe))):
        issue, r, _ = replay(after, entry=entry, stop=stop, target=targets[0], entry_window=cfg.entry_window_bars,
                             max_hold=cfg.max_hold_bars, costs=costs, stop_close_bars=close_bars)
        outcomes[convention] = {"issue": issue, "r": r, "provisoire": False}
    issue, r, provisional = replay_ladder(after, entry=entry, stop=stop, targets=targets,
                             weights=ladder_weights(len(targets), weights), entry_window=cfg.entry_window_bars,
                             costs=costs, follow_bars=int(pd.Timedelta(days=FOLLOW_DAYS) / STEP))
    outcomes[LADDER] = {"issue": issue, "r": r, "provisoire": provisional}
    from .trailing import replay_trailing
    issue, r = replay_trailing(after, entry=entry, stop=stop, targets=targets, entry_window=cfg.entry_window_bars,
                               max_hold=int(pd.Timedelta(days=FOLLOW_DAYS) / STEP), costs=costs, tp_count=cfg.tp_count)
    outcomes[TRAILING] = {"issue": issue, "r": r, "provisoire": False}
    return OK, "", outcomes


# --- Bilan ------------------------------------------------------------------------------------------------

def _conclusion(count: int, ci: tuple[float, float] | None, days: int) -> str:
    if count < MIN_RESOLVED:
        return f"trop peu de signaux résolus ({count} < {MIN_RESOLVED}) : aucune conclusion"
    if ci is None:
        return f"signaux répartis sur {days} jour(s) < {MIN_DAYS} : aucune conclusion"
    if ci[0] > 0:
        return "gain moyen positif après coûts sur cet historique (IC95 entièrement > 0)"
    if ci[1] < 0:
        return "perte moyenne après coûts sur cet historique (IC95 entièrement < 0)"
    return "ni gain ni perte démontré (IC95 contient 0)"


def summarize(rows: list[AuditRow], *, samples: int, seed: int) -> dict[str, dict]:
    """Bilan par groupe et pour l'ensemble : comptes, puis par convention la part de trades gagnants, le R net
    moyen et son IC95 par jours de publication (les signaux d'un même jour suivent le même marché)."""
    groups = [*dict.fromkeys(r.group for r in rows)]
    out: dict[str, dict] = {}
    for name in [*groups, ALL] if len(groups) > 1 else groups or [ALL]:
        mine = rows if name == ALL else [r for r in rows if r.group == name]
        measured = [r for r in mine if r.status == OK]
        shares = [r.missing_share for r in mine if r.missing_share is not None]
        entry: dict = {"messages": len(mine), "statuts": dict(Counter(r.status for r in mine)),
                       "modifies_apres_coup": sum(r.edited for r in measured),
                       "messages_supprimes_part": max(shares) if shares else None, "conventions": {}}
        # Les signaux lus sur IMAGE (taux d'erreur hors échantillon inconnu) ne comptent ni dans les conventions ni
        # dans la preuve : ils ont leur propre bilan, à comparer à celui des signaux texte.
        images = [r for r in measured if r.from_image]
        measured = [r for r in measured if not r.from_image]
        if any(r.from_image or r.image_rejected for r in mine):
            entry["images"] = {"lues": sum(r.from_image for r in mine), "ignorees": sum(r.image_rejected for r in mine),
                               "mesurees": len(images), "conventions": _conventions(images, samples=samples, seed=seed)}
        if measured:
            ratios = [(r.targets[0] - r.entry) / (r.entry - r.stop) for r in measured
                      if r.entry is not None and r.stop is not None]
            entry["tp1_pct_moyen"] = round(float(np.mean([r.tp1_pct for r in measured])), 2)
            entry["stop_pct_moyen"] = round(float(np.mean([r.stop_pct for r in measured])), 2)
            # Part de TP1 qu'il faut atteindre pour être à zéro, AVANT frais : 1 / (1 + gain/risque).
            entry["part_tp1_pour_etre_a_zero"] = round(float(np.mean([1 / (1 + x) for x in ratios])), 4)
        entry["conventions"] = _conventions(measured, samples=samples, seed=seed)
        entry["preuve"] = history_proof(entry) | {"ocr": "images" in entry,
                                                  "signaux_image_exclus": len(images)}
        out[name] = entry
    return out


def _conventions(measured: list[AuditRow], *, samples: int, seed: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for convention in CONVENTIONS:
        valued = [r.outcomes[convention] for r in measured if r.outcomes[convention]["r"] is not None]
        done = [(r, r.outcomes[convention]) for r in measured
                if r.outcomes[convention]["r"] is not None and not r.outcomes[convention]["provisoire"]]
        values = np.array([o["r"] for _, o in done], dtype=float)
        times = np.array([r.received_at for r, _ in done])
        issues = Counter(r.outcomes[convention]["issue"] for r in measured)
        ci, days = day_block_ci95(values, times, block_days=1, samples=samples, seed=seed, min_blocks=MIN_DAYS) \
            if len(values) >= MIN_RESOLVED else (None, len({t[:10] for t in times}))
        block: dict = {"resolus": len(values), "issues": dict(issues),
                       "en_cours": issues.get("PENDING", 0) + len(valued) - len(values),
                       "non_remplis": issues.get("UNFILLED", 0)}
        if len(valued) > len(values):
            # Les perdants se ferment vite, les gagnants restent ouverts : la moyenne des seuls trades clos
            # est biaisée contre le groupe tant que des positions sont ouvertes. On donne donc aussi la
            # moyenne avec les positions ouvertes valorisées au dernier prix (provisoire).
            block["r_moyen_avec_ouvertes"] = round(float(np.mean([o["r"] for o in valued])), 4)
        if len(values):
            block |= {"part_gagnants": round(float((values > 0).mean()), 4), "r_moyen": round(float(values.mean()), 4),
                      "r_total": round(float(values.sum()), 2), "ic95": ci, "jours": days}
        block["conclusion"] = _conclusion(len(values), ci, days)
        out[convention] = block
    return out


_MANAGEMENT = {"value": "stop_suiveur"}


def proof_convention() -> str:
    """La preuve sur historique se mesure avec la gestion que l'avis juge (fixée par `audit` à chaque bilan)."""
    return TRAILING if _MANAGEMENT["value"] == "stop_suiveur" else LADDER


def history_proof(entry: dict) -> dict:
    """Le groupe est-il prouvé sur son historique ? TOUT doit être vrai (docs/EXTERNAL_SIGNALS.md) :
    au moins PROOF_RESOLVED signaux résolus sur PROOF_DAYS jours, R net moyen avec IC95 entièrement > 0, mesuré
    comme BSM exécute (`PROOF_CONVENTION`), et au plus MAX_MISSING_SHARE de messages supprimés."""
    block = entry["conventions"].get(proof_convention(), {})
    resolved, days, ci = block.get("resolus", 0), block.get("jours", 0), block.get("ic95")
    missing = entry.get("messages_supprimes_part")
    checks = {
        "assez_de_signaux": resolved >= PROOF_RESOLVED,
        "assez_de_jours": days >= PROOF_DAYS,
        "gain_moyen_positif_ic95": bool(ci is not None and ci[0] > 0),
        "peu_de_messages_supprimes": missing is None or missing <= MAX_MISSING_SHARE,
    }
    proven = all(checks.values())
    if proven:
        text = (f"prouvé sur son historique : {resolved} signaux résolus sur {days} jours, gain moyen "
                f"{block.get('r_moyen'):+.2f} R par signal, IC95 {ci}, exécutés comme BinanceSpotManager")
    else:
        reasons = {"assez_de_signaux": f"{resolved}/{PROOF_RESOLVED} signaux résolus",
                   "assez_de_jours": f"{days}/{PROOF_DAYS} jours",
                   "gain_moyen_positif_ic95": "gain moyen pas positif avec certitude (IC95)",
                   "peu_de_messages_supprimes": f"{(missing or 0) * 100:.0f} % de messages supprimés "
                                                f"(au plus {MAX_MISSING_SHARE * 100:.0f} %)"}
        text = "non prouvé sur son historique : " + " ; ".join(reasons[k] for k, ok in checks.items() if not ok)
    return {"proven": proven, "checks": checks, "text": text, "resolved": resolved, "days": days,
            "r_mean": block.get("r_moyen"), "r_ci95": ci, "missing_share": missing, "convention": proof_convention()}


def save_history(settings: Settings, report: AuditReport) -> None:
    """Enregistre la preuve de chaque groupe (table `source_history` du registre des signaux externes)."""
    from .registry import ExternalSignalRegistry
    with ExternalSignalRegistry(settings.external_db).connect() as db:
        _history_table(db)
        for name, entry in report.summary.items():
            if name == ALL:
                continue
            db.execute("INSERT INTO source_history (source, generated_at, proven, proof) VALUES (?, ?, ?, ?)",
                       (name, report.generated_at, int(entry["preuve"]["proven"]),
                        json.dumps(entry["preuve"], ensure_ascii=False, default=str)))


def latest_history(settings: Settings, source: str, *, now: datetime) -> dict | None:
    """Dernière preuve sur historique d'un groupe, si elle a moins de PROOF_VALID_DAYS jours."""
    from .registry import ExternalSignalRegistry
    if not Path(settings.external_db).exists():
        return None
    with ExternalSignalRegistry(settings.external_db).connect() as db:
        _history_table(db)
        row = db.execute("SELECT generated_at, proof FROM source_history WHERE source=? ORDER BY generated_at DESC "
                         "LIMIT 1", (source,)).fetchone()
    if row is None:
        return None
    age = pd.Timestamp(now) - pd.Timestamp(row["generated_at"])
    proof = json.loads(row["proof"]) | {"generated_at": row["generated_at"]}
    if age > pd.Timedelta(days=PROOF_VALID_DAYS):
        return proof | {"proven": False, "text": f"historique importé il y a {age.days} jours : à refaire "
                                                  f"(valable {PROOF_VALID_DAYS} jours)"}
    return proof


def _history_table(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS source_history (source TEXT NOT NULL, generated_at TEXT NOT NULL,
                  proven INTEGER NOT NULL, proof TEXT NOT NULL)""")


def audit(settings: Settings, items: Iterable[HistoryItem], *, now: datetime, source: str = "", weights: str = "early",
          bars_for: Bars | None = None, progress: Callable[[str], None] | None = None) -> AuditReport:
    """Rejoue chaque message de l'historique. `source` nomme le groupe quand ni l'export ni le texte ne le font."""
    say = progress or (lambda _text: None)
    ladder_weights(1, weights)                                      # refuse tout de suite une répartition inconnue
    _MANAGEMENT["value"] = settings.external.management
    fetch = bars_for or market_bars(settings, now=now)
    rows: list[AuditRow] = []
    parsed: list[tuple[AuditRow, ExternalSignal, pd.Timestamp]] = []
    # Doublons, PAR GROUPE (un groupe qui reprend les signaux d'un autre garde les siens, audité seul ou avec lui).
    # Un signal TEXTE ne se compare qu'aux textes antérieurs : la mesure du texte (et la preuve) ne dépend jamais de
    # la lecture des images. Un signal IMAGE se compare aux deux.
    seen_text: dict[tuple, pd.Timestamp] = {}
    seen: dict[tuple, pd.Timestamp] = {}
    for item in sorted(items, key=lambda i: i.received_at):
        received = pd.Timestamp(item.received_at)
        received = received.tz_localize("UTC") if received.tzinfo is None else received.tz_convert("UTC")
        signal = parse(item.text)
        row = AuditRow(received_at=received.isoformat(), group=item.group or group_of(item.text) or source or "inconnu",
                       status=OK, edited=item.edited, message_id=item.message_id, missing_share=item.missing_share,
                       from_image=item.from_image, image_rejected=item.image_rejected)
        rows.append(row)
        if item.image_rejected:
            row.status, row.reason = UNREADABLE, ("image à valider dans CSI : jamais simulée sans validation"
                                                  if item.image_status == "A_VALIDER" else
                                                  "image ignorée : lecture douteuse (external/chart_ocr.py)")
            continue
        if signal.errors:
            row.status, row.reason = UNREADABLE, " ; ".join(signal.errors)[:300]
            continue
        assert signal.stop is not None
        row.symbol, row.entry, row.stop = signal.symbol, signal.entries[0], float(signal.stop)
        row.targets, row.stop_timeframe = list(signal.targets), signal.stop_timeframe
        row.stop_pct = round((row.entry - row.stop) / row.entry * 100, 2)
        row.tp1_pct = round((row.targets[0] / row.entry - 1) * 100, 2)
        key = (row.group, signal.symbol, tuple(signal.entries), row.stop, tuple(signal.targets))
        before = seen if item.from_image else seen_text
        if key in before and received - before[key] <= pd.Timedelta(days=DUPLICATE_DAYS):
            row.status, row.reason = DUPLICATE, f"même signal déjà publié le {before[key]:%Y-%m-%d %H:%M}"
            continue
        # Une capture mise à jour (objectifs atteints effacés, stop déplacé) donne une autre clé exacte : pour un
        # signal lu sur IMAGE, même paire et même stop, ou même paire et même entrée 1, sous 7 jours = doublon.
        loose = ((row.group, signal.symbol, "stop", row.stop), (row.group, signal.symbol, "entree", row.entry))
        earlier = [seen[k] for k in loose if k in seen and received - seen[k] <= pd.Timedelta(days=DUPLICATE_DAYS)]
        if item.from_image and earlier:
            row.status, row.reason = DUPLICATE, f"même paire et même stop ou entrée qu'un signal du {max(earlier):%Y-%m-%d %H:%M}"
            continue
        seen[key] = received
        if not item.from_image:
            seen_text[key] = received
        for k in loose:
            seen[k] = received
        parsed.append((row, signal, received))
    spans: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for _, signal, received in parsed:
        low, high = spans.get(signal.symbol, (received, received))
        spans[signal.symbol] = (min(low, received), max(high, received))
    candles: dict[str, pd.DataFrame | None] = {}
    for symbol, (first, last) in spans.items():
        say(f"bougies {symbol}")
        candles[symbol] = fetch(symbol, first.floor("15min") - pd.Timedelta(hours=6),
                                last + pd.Timedelta(days=FOLLOW_DAYS + 2))
    for row, signal, received in parsed:
        bars = candles[signal.symbol]
        if bars is None or bars.empty:
            row.status, row.reason = NO_DATA, "paire sans bougies publiques sur Binance Spot à cette date"
            continue
        row.status, row.reason, row.outcomes = measure(signal, bars, received, settings=settings, weights=weights)
    notes = [
        "Mesure d'une source externe : ni un essai d'une stratégie de CSI, ni une promesse pour le prochain signal.",
        "Un historique exporté ne contient que les messages encore présents : un groupe peut avoir supprimé des "
        "signaux perdants. Les messages reçus en direct par BinanceSpotManager n'ont pas ce biais.",
        "Entrée simulée sur les bougies 15 min (un contact ne suffit pas, il faut que le prix traverse) ; frais, "
        "glissement et demi-écart du scénario central ; R = gain net rapporté au risque prévu (entrée − stop).",
        f"Échelle : parts « {weights} » ; une position encore ouverte après {FOLLOW_DAYS} jours est valorisée au "
        "dernier prix.",
    ]
    summary = summarize(rows, samples=settings.protocol.bootstrap_samples, seed=settings.protocol.seed)
    say("comparaison des gestions")
    from .managements import compare
    from .trailing import Management
    current = Management(tp_count=settings.external.tp_count)
    for name in summary:
        # Signaux texte seulement, comme le bilan et la preuve : l'étude ne dépend pas de la lecture des images.
        mine = [r for r in rows if r.status == OK and not r.from_image and (name == ALL or r.group == name)]
        if len(mine) < 2:
            continue
        signals = [{"symbol": r.symbol, "received_at": r.received_at, "entry": r.entry, "stop": r.stop,
                    "targets": r.targets} for r in mine]
        summary[name]["gestions"] = compare(
            signals, {s: f for s, f in candles.items() if f is not None}, entry_window=settings.external.entry_window_bars,
            horizon=int(pd.Timedelta(days=FOLLOW_DAYS) / STEP), costs=settings.costs["central"],
            samples=settings.protocol.bootstrap_samples, seed=settings.protocol.seed, current=current)
    return AuditReport(pd.Timestamp(now).isoformat(), weights, rows, summary, notes)


def write_report(settings: Settings, report: AuditReport) -> Path:
    """Écrit `audit.json` et `signaux.csv` dans `reports/AUDIT-<date>/` ; renvoie le dossier."""
    directory = settings.reports_dir / f"AUDIT-{pd.Timestamp(report.generated_at):%Y%m%dT%H%M%SZ}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "audit.json").write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str),
                                          encoding="utf-8")
    flat = []
    for row in report.rows:
        line = {k: v for k, v in asdict(row).items() if k not in ("outcomes", "targets")}
        line["tp1"] = row.targets[0] if row.targets else None
        for convention in CONVENTIONS:
            outcome = row.outcomes.get(convention, {})
            line[f"{convention}_issue"], line[f"{convention}_r"] = outcome.get("issue"), outcome.get("r")
        flat.append(line)
    pd.DataFrame(flat).to_csv(directory / "signaux.csv", index=False)
    return directory
