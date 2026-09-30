"""Lecture des signaux externes (groupes Telegram), mêmes modèles que BinanceSpotManager.

Modèles reconnus : structured (PAIR / ENTRY n / Tn / SL — Suhaib, Cleo), abk (Coin / Entry
Zone / Target n → / Stop Loss), numbered (#PAIRE/USDT / Entryn / TPn / Stop — Al-Mahwashi)
et simple (BUY / ENTRY / TP / SL). Tout texte ambigu échoue : aucun prix n'est deviné,
aucune devise n'est ajoutée, les shorts et le levier sont refusés (Spot, long uniquement).
Ce lecteur n'exécute rien.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, timezone

NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
MAX_LENGTH = 20_000


@dataclass
class ExternalSignal:
    template: str = "unknown"
    symbol: str = ""
    direction: str = ""
    exchange: str = ""
    entries: list[float] = field(default_factory=list)
    targets: list[float] = field(default_factory=list)
    stop: float | None = None
    stop_timeframe: str = ""
    published_at: str = ""
    content_hash: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).upper()
    text = re.sub(r"[0-9]️?⃣", "", text)  # émojis « 1️⃣ » devant les objectifs
    text = text.replace("‎", "").replace("‏", "")
    return text.replace("**", "").replace("️", "")


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(normalize(text).split()).encode()).hexdigest()


def parse(raw: str) -> ExternalSignal:
    result = ExternalSignal()
    if not raw.strip() or len(raw) > MAX_LENGTH:
        result.errors.append("Texte vide ou trop long (20 000 caractères maximum).")
        return result
    result.content_hash = content_hash(raw)
    text = normalize(raw)
    lines = [re.sub(r"^[^A-Z0-9#]+", "", line.strip()) for line in text.splitlines()]
    clean = "\n".join(lines)
    result.template = (
        "structured" if re.search(r"^PAIR\s*:", clean, re.M) else
        "abk" if re.search(r"^COIN\s*:", clean, re.M) else
        "numbered" if (re.search(r"^#[A-Z0-9]+\s*/\s*(?:USDT|USDC)\s*$", clean, re.M)
                       and re.search(r"^ENTRY\s*1\s*:", clean, re.M)
                       and re.search(r"^TP\s*1\s*:", clean, re.M)
                       and re.search(r"^STOP\s*:", clean, re.M)) else "simple")
    if re.search(r"\b(?:NIFTY|BANKNIFTY|INTRADAY)\b", text):
        result.errors.append("Rapport de marché / indices : pas un signal Spot.")
    if re.search(r"\b(?:SHORT|SELL|LEVERAGE|FUTURES)\b", text):
        result.direction = "SELL"
        result.errors.append("Short, vente initiale et levier refusés : Spot, long uniquement.")
    elif re.search(r"\b(?:BUY|LONG)\b", text) or result.template in {"structured", "abk", "numbered"}:
        result.direction = "BUY"
    else:
        result.errors.append("Direction d'achat non identifiable.")

    pairs = re.findall(r"(?:^|\n)(?:PAIR|COIN)\s*:\s*([A-Z0-9]+\s*/\s*[A-Z0-9]+|[A-Z0-9]+)", clean)
    if not pairs:
        found = re.findall(r"\b([A-Z0-9]{2,20}/(?:USDT|USDC|USD))\b|#([A-Z0-9]+USDT)\b"
                           r"|(?:BUY|SELL)\s+(?:LIMIT\s+)?([A-Z0-9]+USD[T]?)\b", clean)
        pairs = [next(x for x in match if x) for match in found]
    if len(pairs) != 1:
        result.errors.append("Une seule paire explicite est requise ; aucune devise n'est ajoutée automatiquement.")
    else:
        result.symbol = re.sub(r"[\s/]", "", pairs[0])
        if not re.fullmatch(r"[A-Z0-9]{2,20}(?:USDT|USDC)", result.symbol):
            result.errors.append("Seules les paires Spot USDT/USDC sont prises en charge.")
    platforms = re.findall(r"^PLATFORM\s*:\s*(\w+)", clean, re.M)
    result.exchange = platforms[0] if platforms else ""
    if any(platform != "BINANCE" for platform in platforms):
        result.errors.append("Plateforme autre que Binance : prix et liquidité non comparables.")

    # Préfixe numérique strict : pourcentages seuls, virgules et prix supplémentaires échouent.
    indexed_entries: list[str] = []
    indexed_targets: list[str] = []
    stops: list[float] = []
    for line in lines:
        entry = re.match(r"ENTRY(?:\s+(ZONE|PRICE)|\s*(\d+))?\s*:\s*(.*)$", line)
        target = re.match(r"(?:T(?:P)?\s*(\d+)|TARGET\s*(\d*)|TP)\s*(?::|→|-|\s)\s*(.*)$", line)
        stop = re.match(r"(?:SL|STOP(?:\s*LOSS)?)\s*(?::|-|\s)\s*(.*)$", line)
        if re.match(r"ENTRY\s*\d", line) and not entry:
            result.errors.append("Ligne d'entrée numérotée non reconnue.")
        if re.match(r"(?:TP?\s*\d|TARGET\s*\d)", line) and not target:
            result.errors.append("Ligne d'objectif numéroté non reconnue.")
        if entry and entry[3].strip():
            match = re.fullmatch(rf"({NUMBER})(?:\s*[–—-]\s*({NUMBER}))?\s*", entry[3].strip())
            if not match:
                result.errors.append("Prix d'entrée ambigu ou non pris en charge.")
            else:
                indexed_entries.append(entry[2] or entry[1] or "single")
                result.entries.extend(float(v) for v in match.groups() if v)
        if target:
            indexed_targets.append(target[1] or target[2] or "single")
            match = re.fullmatch(rf"({NUMBER})\s*(?:[\[(][^\]\n)]*[%][\])])?\s*", target[3])
            if match:
                result.targets.append(float(match[1]))
            else:
                result.errors.append("Objectif ambigu (plage, +, pourcentage seul ou texte inattendu).")
        if stop:
            match = re.fullmatch(rf"({NUMBER})\s*(?:\((\d+\s*(?:H|MIN|M))\))?\s*(?:[\[(][^\]\n)]*%[\])])?\s*", stop[1])
            if match:
                stops.append(float(match[1]))
                result.stop_timeframe = (match[2] or "").lower()
            else:
                result.errors.append("Stop loss ambigu ou non pris en charge.")
    if len(set(indexed_entries)) != len(indexed_entries) or len(set(indexed_targets)) != len(indexed_targets):
        result.errors.append("Indices d'entrée/objectif répétés : séparer les signaux.")
    for indices in (indexed_entries, indexed_targets):
        if indices and all(i.isdigit() for i in indices) and [int(i) for i in indices] != list(range(1, len(indices) + 1)):
            result.errors.append("Numérotation discontinue ou désordonnée.")
    if len(stops) == 1:
        result.stop = stops[0]
    else:
        result.errors.append("Un seul stop loss explicite est requis.")
    if not 1 <= len(result.entries) <= 20 or not 1 <= len(result.targets) <= 20:
        result.errors.append("Il faut entre 1 et 20 entrées et objectifs explicites.")
    values = result.entries + result.targets + stops
    if any(not math.isfinite(v) or v <= 0 for v in values):
        result.errors.append("Prix nul, négatif ou non fini.")
    if result.direction == "BUY" and result.entries and result.targets and result.stop is not None:
        if result.stop >= min(result.entries) or min(result.targets) <= max(result.entries):
            result.errors.append("Achat incohérent : SL < toutes les entrées < tous les TP requis.")
        if result.targets != sorted(set(result.targets)):
            result.errors.append("Les TP doivent être strictement croissants.")
    if result.stop_timeframe:
        result.warnings.append(f"SL ({result.stop_timeframe}) : stop sur clôture de bougie possible ; "
                               "évalué ici comme un stop au toucher (convention pessimiste).")
    date = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
    hour = re.search(r"(\d{1,2}):(\d{2})\s*GMT\s*([+-]\d{1,2})\b", text)
    if date and hour:
        try:
            stamp = datetime.fromisoformat(date[1]).replace(hour=int(hour[1]), minute=int(hour[2]),
                                                             tzinfo=timezone(timedelta(hours=int(hour[3]))))
            result.published_at = stamp.astimezone(UTC).isoformat()
        except ValueError:
            result.errors.append("Date du signal invalide.")
    else:
        result.warnings.append("Date source absente : fraîcheur vérifiée uniquement sur les données de marché.")
    return result
