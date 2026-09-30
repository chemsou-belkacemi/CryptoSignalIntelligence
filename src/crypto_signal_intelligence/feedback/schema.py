"""Contrat des événements d'exécution renvoyés par le consommateur (docs/FEEDBACK_FORMAT.md).

Le consommateur (BinanceSpotManager, Binance Demo) écrit un événement par ligne JSON.
Ce projet les importe et les rapproche des signaux publiés ; il ne pilote aucun ordre.
Tout événement incomplet ou ambigu est rejeté à l'importation, jamais complété.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

EventType = Literal["RECEIVED", "REJECTED", "ORDER_PLACED", "ENTRY_PARTIAL", "ENTRY_FILLED", "TP_FILLED",
                    "STOP_FILLED", "MARKET_EXIT_FILLED", "CLOSED", "EXPIRED", "CANCELLED"]
FILL_EVENTS = {"ENTRY_PARTIAL", "ENTRY_FILLED", "TP_FILLED", "STOP_FILLED", "MARKET_EXIT_FILLED"}
BUY_EVENTS = {"ENTRY_PARTIAL", "ENTRY_FILLED"}
SELL_EVENTS = {"TP_FILLED", "STOP_FILLED", "MARKET_EXIT_FILLED"}
FEEDBACK_VERSION = 2
ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,160}$"


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=ID_PATTERN)
    signal_id: str = Field(pattern=ID_PATTERN)
    event_type: EventType
    occurred_at: datetime
    environment: Literal["DEMO"]
    producer: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,60}$")
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,20}(USDT|USDC)$")
    quantity: Decimal | None = None          # quantité de base réellement remplie
    price: Decimal | None = None             # prix moyen du remplissage
    quote_quantity: Decimal | None = None    # montant en devise de cotation, si connu
    fee: Decimal | None = None
    fee_asset: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,20}$")
    order_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:\-]{1,80}$")
    target_index: int | None = Field(default=None, ge=1, le=4)
    reason: str | None = Field(default=None, max_length=500)
    # RECEIVED : empreinte de la politique de sortie que le consommateur a reconnue et appliquera.
    exit_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise ValueError("occurred_at : horodatage UTC explicite requis (…Z)")
        return value.astimezone(UTC)

    @field_validator("quantity", "price", "quote_quantity", "fee")
    @classmethod
    def _finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("valeur finie et positive requise")
        return value

    @model_validator(mode="after")
    def _coherence(self) -> ExecutionEvent:
        if self.event_type in FILL_EVENTS:
            if not self.quantity or not self.price:
                raise ValueError(f"{self.event_type} exige quantity > 0 et price > 0 (remplissage réel)")
            if self.event_type == "TP_FILLED" and self.target_index is None:
                raise ValueError("TP_FILLED exige target_index (1 à 4)")
        if self.event_type == "ORDER_PLACED" and not (self.order_id and self.quantity and self.price):
            raise ValueError("ORDER_PLACED exige order_id, quantity (commandée) et price (limite)")
        if self.event_type in {"REJECTED", "CANCELLED", "MARKET_EXIT_FILLED"} and not self.reason:
            raise ValueError(f"{self.event_type} exige reason")
        if (self.event_type == "RECEIVED") != (self.exit_policy_hash is not None):
            raise ValueError("exit_policy_hash : obligatoire sur RECEIVED, absent ailleurs")
        if (self.fee is None) != (self.fee_asset is None):
            raise ValueError("fee et fee_asset vont ensemble")
        return self


def parse_line(line: str) -> ExecutionEvent:
    """Une ligne JSON → événement validé ; toute erreur remonte (ligne ignorée par l'importateur)."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide : {exc.msg}") from None
    if not isinstance(payload, dict):
        raise ValueError("un objet JSON par ligne est requis")
    try:
        return ExecutionEvent.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValueError(details) from None
