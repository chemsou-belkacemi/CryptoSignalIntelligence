"""Sérialiseur et parseur STRICT du format TXT V3 (clé=valeur, une clé par ligne).

Règles (docs/SIGNAL_FORMAT.md) : UTF-8, point décimal, ISO 8601 UTC
`YYYY-MM-DDTHH:MM:SSZ`, clé unique, aucune espace autour de `=` ni en bord de
valeur, `NONE` pour une absence autorisée, NaN/Infinity interdits, première
ligne `SIGNAL_VERSION=3`, clés inconnues refusées, toute autre version refusée
(la V2 n'a jamais été consommée : elle n'est plus lue). Tout ce qui suit la ligne
`---ANALYSIS---` est de la prose ignorée par le consommateur.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from .schema import SIGNAL_VERSION, Signal

ANALYSIS_MARKER = "---ANALYSIS---"
NONE = "NONE"
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DECIMAL_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")

# Ordre canonique des clés (= ordre d'écriture).
FIELDS: list[tuple[str, str]] = [
    ("SIGNAL_VERSION", "int"), ("SIGNAL_ID", "str"), ("IDEMPOTENCY_KEY", "str"),
    ("DATA_AS_OF", "time"), ("DECISION_AT", "time"), ("CREATED_AT", "time"), ("VALID_FROM", "time"),
    ("EXPIRES_AT", "time"), ("ENTRY_EXPIRES_AT", "time"),
    ("MARKET_DATA_SOURCE", "str"), ("ENVIRONMENT", "str"), ("MARKET_TYPE", "str"),
    ("SYMBOL", "str"), ("ACTION", "str"), ("STRATEGY", "str"), ("STRATEGY_VERSION", "int"),
    ("TIMEFRAME_SETUP", "str"), ("ENTRY_MODE", "str"), ("ENTRY_COUNT", "int"), ("ENTRY_1", "decimal"),
    ("ENTRY_2", "decimal?"), ("ENTRY_WEIGHTS", "decimals"), ("WEIGHT_BASIS", "str"), ("STOP_LOSS", "decimal"),
    ("TP_COUNT", "int"), ("TP_1", "decimal"), ("TP_2", "decimal?"), ("TP_3", "decimal?"), ("TP_4", "decimal?"),
    ("TP_WEIGHTS", "decimals"), ("EXIT_POLICY_ID", "str"), ("EXIT_POLICY_HASH", "str"),
    ("MAX_HOLD_MINUTES", "int?"), ("RR_REFERENCE", "str"),
    ("RR_TP1_GROSS", "decimal"), ("RR_TP2_GROSS", "decimal?"), ("RR_TP3_GROSS", "decimal?"),
    ("RR_TP4_GROSS", "decimal?"), ("TECHNICAL_SCORE", "decimal?"), ("ML_PROBABILITY", "decimal?"),
    ("MODEL_ID", "str?"), ("ML_TARGET_ID", "str?"), ("ML_HORIZON_MINUTES", "int?"), ("ML_CALIBRATION_ID", "str?"),
    ("TREND_REGIME", "str"), ("VOLATILITY_REGIME", "str"), ("NEWS_STATUS", "str"),
    ("MAX_ENTRY_DEVIATION_BPS", "decimal"), ("VALIDATION_STATUS", "str"), ("INTEGRATION_STATUS", "str"),
    ("STATUS", "str"),
]
KINDS = dict(FIELDS)
# Clés facultatives à la lecture (valeur par défaut du modèle si absentes) ;
# le sérialiseur les écrit toujours.
OPTIONAL_KEYS = {"INTEGRATION_STATUS"}


class SignalFormatError(ValueError):
    pass


class UnsupportedSignalVersion(SignalFormatError):
    pass


def _format_value(value, kind: str) -> str:
    if value is None:
        if not kind.endswith("?"):
            raise SignalFormatError("valeur obligatoire absente")
        return NONE
    if kind.startswith("time"):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind.startswith("decimals"):
        return ",".join(_decimal_text(v) for v in value)
    if kind.startswith("decimal"):
        return _decimal_text(value)
    return str(value.value if hasattr(value, "value") else value)


def _decimal_text(value: Decimal) -> str:
    text = format(Decimal(value), "f")
    if "." not in text:
        text += ".0"
    return text


def serialize(signal: Signal) -> str:
    data = signal.model_dump()
    lines = [f"{key}={_format_value(data[key.lower()], kind)}" for key, kind in FIELDS]
    if signal.analysis:
        lines.append(ANALYSIS_MARKER)
        lines += [f"{key}={value}" for key, value in signal.analysis]
    text = "\n".join(lines) + "\n"
    parse(text)  # garde-fou : on n'écrit jamais un texte que notre propre parseur refuse
    return text


def _parse_value(raw: str, kind: str, key: str):
    if raw == NONE:
        if not kind.endswith("?"):
            raise SignalFormatError(f"{key} est obligatoire (NONE interdit)")
        return None
    base = kind.rstrip("?")
    if base == "time":
        if not TIME_PATTERN.fullmatch(raw):
            raise SignalFormatError(f"{key} : format YYYY-MM-DDTHH:MM:SSZ requis")
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if base == "int":
        if not re.fullmatch(r"\d+", raw):
            raise SignalFormatError(f"{key} : entier requis")
        return int(raw)
    if base in {"decimal", "decimals"}:
        parts = raw.split(",") if base == "decimals" else [raw]
        values = []
        for part in parts:
            if not DECIMAL_PATTERN.fullmatch(part):
                raise SignalFormatError(f"{key} : nombre décimal à point requis (reçu {part!r})")
            try:
                values.append(Decimal(part))
            except InvalidOperation:
                raise SignalFormatError(f"{key} : nombre invalide") from None
        return tuple(values) if base == "decimals" else values[0]
    return raw


def parse(text: str) -> Signal:
    if "\r" in text.replace("\r\n", "\n"):
        raise SignalFormatError("fin de ligne invalide")
    lines = text.replace("\r\n", "\n").split("\n")
    contract: dict[str, str] = {}
    analysis: list[tuple[str, str]] = []
    in_analysis = False
    for number, line in enumerate(lines, 1):
        if line == ANALYSIS_MARKER:
            in_analysis = True
            continue
        if not line.strip():
            continue
        key, sep, value = line.partition("=")
        if in_analysis:
            analysis.append((key.strip(), value.strip()))
            continue
        if not sep or not KEY_PATTERN.fullmatch(key):
            raise SignalFormatError(f"ligne {number} : format CLE=VALEUR requis")
        if value != value.strip() or not value:
            raise SignalFormatError(f"ligne {number} : valeur vide ou entourée d'espaces")
        if not contract and key != "SIGNAL_VERSION":
            raise SignalFormatError("SIGNAL_VERSION doit être la première clé")
        if key in contract:
            raise SignalFormatError(f"clé dupliquée : {key}")
        if key not in KINDS:
            raise SignalFormatError(f"clé inconnue pour la version {SIGNAL_VERSION} : {key}")
        contract[key] = value
    if contract.get("SIGNAL_VERSION") != str(SIGNAL_VERSION):
        raise UnsupportedSignalVersion(f"version non prise en charge : {contract.get('SIGNAL_VERSION')}")
    missing = [key for key, _ in FIELDS if key not in contract and key not in OPTIONAL_KEYS]
    if missing:
        raise SignalFormatError(f"clés manquantes : {', '.join(missing)}")
    values = {key.lower(): _parse_value(raw, KINDS[key], key) for key, raw in contract.items()}
    values["analysis"] = tuple(analysis)
    try:
        return Signal(**values)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'signal'}: {e['msg']}" for e in exc.errors())
        raise SignalFormatError(details) from None
