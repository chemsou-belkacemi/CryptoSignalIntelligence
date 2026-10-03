"""Lecture des signaux externes (groupes Telegram) : la MÊME lecture que BinanceSpotManager.

Depuis le 2026-10-02, `parse` reprend la lecture par étiquettes de BSM (`binance_spot_manager/signal_parser.py`,
commit df56b3a sur main) : paire, entrée, objectif, stop et plateforme sont lus par leurs étiquettes, quel que
soit l'habillage (émojis, filets, numérotation, pourcentages, flèches). CSI juge ainsi exactement les signaux que
le bot exécuterait ; un test compare les deux lectures sur des signaux réels. Tout texte ambigu échoue : aucun
prix n'est deviné, aucune devise n'est ajoutée, les shorts et le levier sont refusés (Spot, long uniquement).
Propre à CSI : l'empreinte `content_hash` (inchangée pour les signaux déjà enregistrés), `group_of`, et un stop
sur clôture évalué comme un stop au toucher (convention pessimiste). Ce lecteur n'exécute rien.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, timezone

NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
# Prix lus dans une valeur : comme NUMBER, plus un entier suivi d'un point sans décimale (« Stop: 223. ») qui vaut
# 223 (même règle que BinanceSpotManager, branche fix/parser-stop-tp) ; « 1.442. », « 223.. », « 1.2.3 » refusés.
PRICE_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+|\d+\.(?![\d.]))"
# Vente à découvert : même règle que BinanceSpotManager (signal_parser.SHORT_SIGNAL). « SELL » en début de ligne,
# après DIRECTION/SIDE/POSITION/TYPE, ou suivi de LIMIT/NOW/MARKET/ZONE ; jamais « T1: 2.9 SELL (1.40%) », qui
# veut dire « vendre à cet objectif » (prise de bénéfice d'un achat).
SHORT_SIGNAL = re.compile(
    r"\b(?:SHORT|LEVERAGE|FUTURES|PERP|PERPETUAL|MARGIN)\b"
    r"|(?:^|\n)[^A-Z0-9\n]*SELL\b|\b(?:DIRECTION|SIDE|POSITION|TYPE)\s*:?\s*SELL\b|\bSELL\s+(?:LIMIT|NOW|MARKET|ZONE)\b")
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
    # Pictogrammes N'IMPORTE OÙ dans la ligne (« T1: 0.0259 📉 (1.92%) », « ENTRY 1 ✅: 0.0254 » ajouté après
    # coup par le groupe) : ils ne portent aucun prix. Les retirer ne devine rien ; les flèches « → » restent.
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Sk") and ch != "\u200d")
    # « *PAIR:* » : gras de Telegram (un ou deux astérisques), jamais dans un prix.
    return text.replace("*", "").replace("️", "")


def content_hash(text: str) -> str:
    """Empreinte du texte normalisé : insensible à la casse, aux espaces (y compris autour des « : ») et aux
    pictogrammes, pour qu'un signal redécoré après coup (« ENTRY 1 ✅: ») reste reconnu comme le même."""
    return hashlib.sha256(re.sub(r"\s*:\s*", ":", " ".join(normalize(text).split())).encode()).hexdigest()


def group_of(text: str) -> str:
    """Nom du groupe écrit en tête d'un signal transféré (première ligne lisible qui n'est pas un champ). Seulement
    pour un texte qui ressemble à un signal : une entrée et un stop ou un objectif ; sinon « » (un simple message
    n'est pas un nom de groupe)."""
    upper_text = normalize(text)
    if not (re.search(r"\bENTRY", upper_text) and re.search(r"\b(?:SL|STOP|TP\s*\d?|T\d|TARGET)", upper_text)):
        return ""
    for line in text.splitlines():
        name = "".join(ch if unicodedata.category(ch)[0] in "LN" or ch in " /'&.-" else " "
                       for ch in unicodedata.normalize("NFKC", line))
        name = " ".join(name.split()).strip(" -/.'&")
        if not name:
            continue
        upper = name.upper()
        if re.match(r"(PAIR|COIN|ENTRY|BUY|SELL|LONG|SHORT|TP|SL|STOP|TARGET|PLATFORM)\b", upper) or \
                re.fullmatch(r"[A-Z0-9]+\s*/\s*(USDT|USDC|USD)", upper):
            return ""
        return name[:60]
    return ""


LABELS = {
    "pair": r"PAIR|COIN|SYMBOL|ASSET|TOKEN|PAIRE",
    "platform": r"PLATFORM|PLATEFORME|EXCHANGE",
    "entry": (r"ENTRY(?:\s+(?:ZONE|PRICES?|RANGE|POINTS?|AREA|LEVELS?))?|ENTRIES|ENTR[EÉ]ES?"
              r"|BUY(?:\s+(?:ZONE|RANGE|PRICE|AREA|AROUND|AT|BETWEEN))?|ACHAT"),
    "target": r"TAKE\s*PROFITS?|TARGETS?|TGTS?|TPS?|T|OBJECTIFS?",
    "stop": r"STOP\s*[-_]?\s*LOSS|STOPLOSS|STOP|SL|S\s*/\s*L|INVALIDATION",
}
LABELLED_LINE = re.compile(
    "^(?:" + "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in LABELS.items()) + r")(?![A-Z])"
    r"\s*(?P<index>\d{1,2})?(?![\d.])\s*(?::|=>|->|→|=|-|–|—|@|\))?\s*(?P<value>.*)$"
)
INLINE_LABEL = re.compile(r"\s(?=(?:" + "|".join(LABELS.values()) + r")(?![A-Z])\s*\d{0,2}\s*[:=])")
QUOTES = r"USDT|USDC|FDUSD|BUSD|USD|BTC|ETH|BNB|EUR|TRY"
SLASH_PAIR = re.compile(rf"(?<![A-Z0-9])([A-Z0-9]{{2,20}})(?:\s*/\s*|[-_])({QUOTES})(?![A-Z0-9])")
JOINED_PAIR = re.compile(r"(?<![A-Z0-9])((?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,20}?)(USDT|USDC)(?![A-Z0-9])")
TIMEFRAME = re.compile(r"(?<![\d.])(\d{1,3})\s*(MINUTES?|MINS?|M|HOURS?|HRS?|H|DAYS?|D|WEEKS?|W)(?![A-Z])")
MEANING_CHANGERS = re.compile(r"\b(?:OR|OU|MARKET|CMP|NOW|CURRENT|ABOVE|BREAKOUT|BREAK|RETEST|DCA|UNTIL)\b")
CANDLE_CLOSE = re.compile(r"\b(?:CLOSES?|CLOSED|CLOSING|CANDLE|DAILY|WEEKLY|CL[OÔ]TURE)\b")
LIST_INDEX = re.compile(r"^\d{1,2}\s*(?:\)|[.:](?=\s))\s*")


def _label_text(raw: str) -> str:
    """Texte lu par étiquettes (BSM : normalize + without_symbols) : majuscules NFKC, pictogrammes remplacés par
    des ESPACES (un espace ne colle jamais deux nombres), gras Telegram « * » retiré."""
    text = unicodedata.normalize("NFKC", raw).upper()
    text = re.sub(r"[0-9]️?⃣", "", text)
    text = text.replace("‎", "").replace("‏", "").replace("‍", "").replace("*", "").replace("️", "")
    return "".join(" " if unicodedata.category(ch) in ("So", "Sk") else ch for ch in text)


def _pairs(text: str) -> set[str]:
    found = {base + quote for base, quote in SLASH_PAIR.findall(text)}
    return found | {base + quote for base, quote in JOINED_PAIR.findall(text)}


def _timeframe(match) -> str:
    return (match[1] + match[2]).lower()


def _read_prices(value: str, kind: str) -> tuple[list[float], str, str]:
    """(prix, unité de clôture du stop, erreur) d'une valeur ; le moindre doute est une erreur, jamais une
    supposition."""
    if MEANING_CHANGERS.search(value):
        return [], "", "condition ou alternative (or, market, above…) non prise en charge"
    timeframe, notes = "", []
    def _note(match: re.Match) -> str:
        notes.append(match[1])
        return " "

    text = re.sub(r"[(\[{]([^)\]}]*)[)\]}]", _note, value)
    for note in notes:
        if "%" in note:
            continue
        found = TIMEFRAME.search(note)
        if found:
            timeframe = timeframe or _timeframe(found)
        elif re.search(r"\d", note):
            return [], "", "chiffre inattendu entre parenthèses"
    text = SLASH_PAIR.sub(" ", JOINED_PAIR.sub(" ", text))
    text = re.sub(r"\b(?:USDT|USDC|USD)\b|\$", " ", text)
    text = re.sub(rf"[+-]?\s*{NUMBER}\s*%", " ", text)
    if kind == "stop":
        found = TIMEFRAME.search(text)
        timeframe = timeframe or (_timeframe(found) if found else "")
        text = TIMEFRAME.sub(" ", text)
        close = CANDLE_CLOSE.search(value)
        if close and not timeframe:
            timeframe = {"DAILY": "1d", "WEEKLY": "1w"}.get(close[0], "bougie")
    else:
        timeframe = ""
    if re.search(r"\d\s*,\d|\d\s*\+|\d[A-Z]|^\s*[-−]\s*\.?\d", text):
        return [], "", "prix ambigu (virgule, +, suffixe ou signe négatif)"
    numbers = re.findall(rf"(?<![\d.]){PRICE_NUMBER}(?![\d.])", text)
    if re.search(r"\d", re.sub(rf"(?<![\d.]){PRICE_NUMBER}(?![\d.])", " ", text)):
        return [], "", "prix mal formé"
    return [float(n) for n in numbers], timeframe, ""


def parse(raw: str) -> ExternalSignal:
    """Lecture par étiquettes, identique à `parse_signal` de BSM (sauf les points propres à CSI, en tête)."""
    result = ExternalSignal()
    if not raw.strip() or len(raw) > MAX_LENGTH:
        result.errors.append("Texte vide ou trop long (20 000 caractères maximum).")
        return result
    result.content_hash = content_hash(raw)
    text = _label_text(raw)
    lines = [re.sub(r"^[^A-Z0-9#]+", "", part.strip())
             for line in text.replace("|", "\n").splitlines() for part in INLINE_LABEL.split(line)]
    clean = "\n".join(lines)
    result.template = ("structured" if re.search(r"^PAIR\s*:", clean, re.M) else
                       "abk" if re.search(r"^COIN\s*:", clean, re.M) else
                       "numbered" if (re.search(r"^#[A-Z0-9]+\s*/\s*(?:USDT|USDC)\s*$", clean, re.M)
                                      and re.search(r"^ENTRY\s*1\s*:", clean, re.M)
                                      and re.search(r"^TP\s*1\s*:", clean, re.M)
                                      and re.search(r"^STOP\s*:", clean, re.M)) else "simple")
    if re.search(r"\b(?:NIFTY|BANKNIFTY|INTRADAY)\b", text):
        result.errors.append("Rapport de marché / indices : pas un signal Spot.")
    # Spot ne peut qu'acheter : BUY est implicite sauf vente annoncée ; un short sans le mot échoue de toute façon
    # sur la géométrie SL < entrées < TP.
    if SHORT_SIGNAL.search(clean):
        result.direction = "SELL"
        result.errors.append("Short, vente initiale et levier refusés : Spot, long uniquement.")
    else:
        result.direction = "BUY"

    keys: dict[str, list[str]] = {"entry": [], "target": []}
    stops: list[float] = []
    pairs, platforms = _pairs(clean), []
    section, list_items = None, 0
    for line in lines:
        if not line:
            continue
        labelled = LABELLED_LINE.match(line)
        if labelled:
            kind = next(k for k in LABELS if labelled[k])
            index, value = labelled["index"], labelled["value"].strip()
            section = None
            if kind == "pair":
                named = re.match(r"[#$]?\s*([A-Z0-9]{2,20})(?:\s*/\s*|[-_\s]?)([A-Z]{3,5})?(?![A-Z0-9])", value)
                if named:
                    pairs.add(named[1] + (named[2] or ""))
                continue
            if kind == "platform":
                platforms.append((value.split() or [""])[0])
                continue
            if index is None and not re.search(r"\d", SLASH_PAIR.sub(" ", JOINED_PAIR.sub(" ", value))):
                if MEANING_CHANGERS.search(value):
                    result.errors.append(f"Valeur non prise en charge : « {line.strip()} ».")
                section = kind
                continue
            if not value:
                result.errors.append(f"Ligne sans prix : « {line.strip()} ».")
                continue
            source = [(index or "single", value)]
        elif section and re.match(r"\.?\d", LIST_INDEX.sub("", line)):
            list_items += 1
            kind, source = section, [(f"list{list_items}", LIST_INDEX.sub("", line))]
        else:
            section = None
            continue
        for key, value in source:
            prices, timeframe, error = _read_prices(value, kind)
            if not error and not prices:
                error = "aucun prix (pourcentage seul ou texte)"
            if not error and len(prices) > 1 and (kind == "stop" or (kind == "target" and key.isdigit())):
                error = "plusieurs prix pour un seul niveau"
            label = {"entry": "Prix d'entrée", "target": "Objectif", "stop": "Stop loss"}[kind]
            if error:
                result.errors.append(f"{label} ambigu ou non pris en charge ({error}) : « {line.strip()} ».")
                continue
            if kind == "stop":
                stops.extend(prices)
                result.stop_timeframe = timeframe
            else:
                keys[kind].append(key)
                (result.entries if kind == "entry" else result.targets).extend(prices)

    if len(pairs) != 1:
        result.errors.append("Une seule paire explicite est requise ; aucune devise n'est ajoutée automatiquement.")
    else:
        result.symbol = pairs.pop()
        if not re.fullmatch(r"[A-Z0-9]{2,20}(?:USDT|USDC)", result.symbol):
            result.errors.append("Seules les paires Spot USDT/USDC sont prises en charge.")
    result.exchange = platforms[0] if platforms else ""
    if any(platform != "BINANCE" for platform in platforms):
        result.errors.append("Plateforme autre que Binance : prix et liquidité non comparables.")
    indexed_entries, indexed_targets = keys["entry"], keys["target"]
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
    hour = re.search(r"(\d{1,2}):(\d{2})\s*(?:GMT|UTC)\s*([+-]\d{1,2})?\b", text)
    if date and hour:
        try:
            stamp = datetime.fromisoformat(date[1]).replace(hour=int(hour[1]), minute=int(hour[2]),
                                                             tzinfo=timezone(timedelta(hours=int(hour[3] or 0))))
            result.published_at = stamp.astimezone(UTC).isoformat()
        except ValueError:
            result.errors.append("Date du signal invalide.")
    else:
        result.warnings.append("Date source absente : fraîcheur vérifiée uniquement sur les données de marché.")
    result.errors = list(dict.fromkeys(result.errors))
    return result
