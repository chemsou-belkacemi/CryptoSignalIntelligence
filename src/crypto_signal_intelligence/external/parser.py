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


# Nom du trader écrit en tête d'un signal : même lecture et même forme canonique que BinanceSpotManager
# (`trader_name.trader_of` et `name_key`, 2026-10-05 ; comparées par tests/test_group_name.py). Sur les exports du
# propriétaire (LEGEND TRADING, AL-MAHWASHI CRYPTO, IN CRYPTO, fichiers de son robot), un nom est lu pour 867
# signaux lisibles sur 874 ; l'ancienne règle (première ligne lisible) en rangeait une partie sous une ligne
# d'événement (« HARMONIC TRADE DETECTED » pour Suhaib AlMashhadani, « Harmonic Pattern Detected » pour Al-Afify
# comme pour Apex). Deux relectures leak-auditor le 2026-10-05.
NAME_FORMULAS = ("بسم الله", "بيم الله", "توكلت على الله", "توكلنا على الله", "الرحمن الرحيم", "الحمد لله",
                 "سبحان الله", "شاء الله", "استغفر الله", "صلى الله", "BISMILLAH", "INSHALLAH", "IN SHAA ALLAH")
NAME_GENERIC = frozenset({
    "HARMONIC", "PATTERN", "PATTERNS", "DETECTED", "TRADE", "TRADES", "TIME", "BASED", "TIME-BASED", "CYCLE",
    "ANALYSIS", "INDICATOR", "INDICATORS", "ULTRA", "SIGNAL", "SIGNALS", "ALERT", "ALERTS", "NEW", "SPOT",
    "LONG", "BUY", "ICT", "PREVIEW", "SETUP", "SWING", "SCALP", "SCALPING", "TERM", "SHORT", "MID", "UPDATE",
    "SPECIAL", "TP", "TRACKING", "HOLD",
    "معاينة", "الصفقة", "صفقة", "جديدة", "توصية", "اشارة", "إشارة", "شراء", "سبوت",
})
# Première mot d'une donnée (« Type: Spot », « Risk Level - High », « Market = Spot ») : jamais un nom.
NAME_METADATA_KEYS = frozenset({"TYPE", "MARKET", "RISK", "LEVEL", "POSITION", "DIRECTION", "SIDE", "TIMEFRAME",
                                "TF", "DURATION", "STRATEGY", "EXCHANGE", "LEVERAGE", "DATE", "TIME", "STATUS",
                                "CATEGORY", "MODE", "ORDER", "PLATFORM", "NOTE", "INFO",
                      "ATTENTION", "WARNING", "REMINDER", "DISCLAIMER"})
# Noms faits seulement de ces mots : personne n'est nommé (aucun nom plutôt qu'un nom partagé par des canaux).
NAME_BANAL = frozenset({"VIP", "KING", "PRO", "PREMIUM", "FREE", "BINANCE", "SPOT", "CRYPTO", "TRADING", "TRADER",
                        "TRADERS", "SIGNAL", "SIGNALS", "IN", "THE", "BEST", "TOP", "GOLD", "MASTER", "EXPERT",
                        "ELITE", "TEAM", "CHANNEL", "GROUP", "CLUB", "ACADEMY",
                        "GOOD", "MORNING", "EVENING", "NIGHT", "HELLO", "HI", "DEAR", "FRIENDS", "GUYS",
                        "EVERYONE", "ALL",
                        "BTC", "ETH", "BNB", "SOL", "USDT", "USDC", "XRP",
                        "توصيات", "كريبتو", "تداول", "اشارات", "إشارات", "قناة", "مجموعة", "صباح", "مساء", "الخير",
                        "اخواني", "إخواني"})
NAME_PREFIX = re.compile(r"^(?:(?:TRADER|ANALYST)\s*[/:]\s*|(?:TRADER|ANALYST|BY|FROM|PH\.?)\s+"
                         r"|(?:المحلل|المتداول)\s*[/:]\s*)", re.IGNORECASE)
NAME_SEPARATED = re.compile(r"^(?P<key>[^:=|→]{1,40}?)\s*[:=|→]\s*(?P<value>\S.*)$")
_MONTHS = (r"(?:JAN(?:UARY|VIER)?|FEB(?:RUARY)?|F[EÉ]V(?:RIER)?|MAR(?:CH|S)?|APR(?:IL)?|AVR(?:IL)?|MAY|MAI"
           r"|JUN(?:E)?|JUIN|JUL(?:Y)?|JUIL(?:LET)?|AUG(?:UST)?|AO[UÛ]T|SEP(?:T(?:EMBER|EMBRE)?)?"
           r"|OCT(?:OBER|OBRE)?|NOV(?:EMBER|EMBRE)?|DEC(?:EMBER)?|D[EÉ]C(?:EMBRE)?)")
NAME_DATE_OR_TIME = re.compile(
    r"\d{1,4}\s*[/.-]\s*\d{1,2}(?:\s*[/.-]\s*\d{1,4})?|\b\d{1,2}\s*[:hH]\s*\d{2}\b"
    r"|\b\d{1,2}\s*(?:AM|PM)\b|\b(?:UTC|GMT)\b"
    r"|\b(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY|LUNDI|MARDI|MERCREDI|JEUDI|VENDREDI|SAMEDI"
    r"|DIMANCHE)\b"
    rf"|\b\d{{1,2}}\s+{_MONTHS}\b|\b{_MONTHS}\s+\d{{1,2}}\b",
    re.IGNORECASE)
NAME_PARTICLE_DASH = re.compile(r"\b(AL|EL|ABD|ABU|ABO|BEN|BIN|IBN)\s*-\s*([^\W_]+)", re.IGNORECASE)
# Forme canonique, volontairement stricte (un nom peut lever des vetos par la preuve de son groupe) : majuscules
# sans accents ni signes diacritiques, particules collées au mot suivant (« Al-Mashhadani » = « AlMashhadani »,
# « ABD ELOUADOUD » = « ABDELOUADOUD »). Aucun mot retiré : « CRYPTO LEGEND » reste distinct de « LEGEND TRADING ».
# Les variantes d'un même nom ne sont réunies que par la liste NAME_ALIASES (variantes vues dans ses exports).
NAME_PARTICLES = frozenset({"AL", "EL", "ABD", "ABU", "ABO", "BEN", "BIN", "IBN"})
NAME_ALIASES = {
    "ALMAHWASHI CRYPTO TRADING": "ALMAHWASHI CRYPTO",
    "ALMAHWASHI TRADING CRYPTO": "ALMAHWASHI CRYPTO",
    "ALAFIFY TRADING": "ALAFIFY",
}


def _name_words(text: str) -> list[str]:
    return [word.upper().strip(".:") for word in text.split()]


#: Mots d'une description placée après « - » ou « : » (« Bat Pattern Detected », « Daily Chart », « Gartley »).
NAME_DESCRIPTION = frozenset({"PATTERN", "PATTERNS", "DETECTED", "CHART", "DAILY", "WEEKLY", "GARTLEY", "BAT",
                                "BUTTERFLY", "CRAB", "SHARK", "CYPHER", "ABCD"})


def _generic(word: str) -> bool:
    """Mot générique, y compris composé (« MID-TERM », « TIME-BASED »)."""
    word = word.upper().strip(".:")
    return word in NAME_GENERIC or ("-" in word and all(part in NAME_GENERIC for part in word.split("-") if part))


def _without_description(text: str) -> str:
    """« NOM - Bat Pattern Detected » → « NOM » ; « AL-MAHWASHI CRYPTO - VIP » reste entier (VIP distingue)."""
    left, separator, right = text.partition(" - ")
    words = _name_words(right)
    if separator and words and (all(_generic(word) for word in words) or NAME_DESCRIPTION & set(words)):
        return left.strip()
    return text


def _name_from_line(line: str) -> str:
    """Nom porté par une ligne d'en-tête, ou « » (formule, mot-dièse, donnée, date, ligne d'événement ou
    générique, phrase, mots banals)."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKC", line)
                    if unicodedata.category(ch) != "Mn" and ch != "\u0640")       # diacritiques, tatouil
    bare = "".join(" " if unicodedata.category(ch) in ("So", "Sk") else ch for ch in plain)
    head = re.sub(r"^\d{1,2}[.)]\s*", "", bare.strip().lstrip("*•·▪▫-–—_( "))
    if head.startswith(("#", "$")) or re.search(r"t\.me/|https?:|www\.", bare, re.IGNORECASE):
        return ""
    text = "".join(ch if unicodedata.category(ch)[0] in "LNZ" or ch in "/&'.-:=|→" else " "
                   for ch in plain.replace("*", " ").replace("_", " "))
    text = " ".join(text.split()).strip(" -/.:&'=|→")
    if not text or any(formula in text.upper() for formula in NAME_FORMULAS) or NAME_DATE_OR_TIME.search(text):
        return ""
    text = NAME_PREFIX.sub("", text).strip(" -/.:")
    if _name_words(text) and _name_words(text)[0] in NAME_METADATA_KEYS:
        return ""
    separated = NAME_SEPARATED.match(text)
    if separated:
        # « LEGEND TRADING: NEW SIGNAL » → « LEGEND TRADING » ; toute autre « clé : valeur » est une donnée.
        value = _name_words(separated.group("value"))
        if not (all(_generic(word) for word in value) or NAME_DESCRIPTION & set(value)):
            return ""
        text = separated.group("key").strip()
    text = NAME_PARTICLE_DASH.sub(lambda match: match.group(0) if match.group(2).upper() in NAME_GENERIC
                                  else f"{match.group(1)}-{match.group(2)}", text)
    words = _without_description(text).strip(" -/.:").split()
    while words and _generic(words[-1]):
        words.pop()
    while words and _generic(words[0]):
        words.pop(0)
    name = " ".join(words).strip(" -/.:")
    if not name or len(name) > 48 or len(words) > 6:
        return ""
    if all(word in NAME_BANAL for word in re.findall(r"[^\W_]+", name.upper())):
        return ""
    return name


def _starts_the_signal(line: str) -> bool:
    """Ligne de paire ou d'étiquette (entrée, objectif, stop) : la fin de l'en-tête."""
    text = _label_text(line).strip().lstrip("#").strip()
    return bool(LABELLED_LINE.match(text) or SLASH_PAIR.search(text) or JOINED_PAIR.search(text))


def canonical_name(name: str) -> str:
    """Forme unique des variantes d'écriture d'un nom : majuscules sans accents ni diacritiques, particules collées
    au mot suivant, puis variantes déclarées (NAME_ALIASES)."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch) and ch != "\u0640").upper()
    words: list[str] = []
    for word in re.findall(r"[^\W_]+", text):
        if words and words[-1] in NAME_PARTICLES:
            words[-1] += word
        else:
            words.append(word)
    canonical = " ".join(words)[:60]
    return NAME_ALIASES.get(canonical, canonical)


def group_of(text: str) -> str:
    """Nom du trader ou du groupe écrit en tête d'un signal, sous sa forme canonique (« SUHAIB ALMASHHADANI »,
    « ALMAHWASHI VIP », « ABK »). Seulement pour un texte qui ressemble à un signal : une entrée et un stop ou
    un objectif ; sinon « » (un simple message n'est pas un nom de groupe).

    Lignes placées avant la paire ou la première étiquette ; formules (« بسم الله … »), mots-dièses, données
    « clé : valeur », dates et lignes d'événement (« Harmonic Pattern Detected ») ignorés ; préfixes (« Trader/ »,
    « Ph. ») et suffixes (« Harmonic Indicator Ultra », « SIGNAL ALERT ») retirés ; la dernière ligne restante
    est le nom. Des mots banals seuls (« VIP », « CRYPTO VIP ») ne nomment personne. Aucun nom n'est inventé."""
    upper_text = normalize(text)
    if not (re.search(r"\bENTRY", upper_text) and re.search(r"\b(?:SL|STOP|TP\s*\d?|T\d|TARGET)", upper_text)):
        return ""
    names = []
    for line in text.splitlines():
        if _starts_the_signal(line):
            break
        name = _name_from_line(line)
        if name:
            names.append(name)
    return canonical_name(names[-1]) if names else ""


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
