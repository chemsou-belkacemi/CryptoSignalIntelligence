"""Signal canonique V3 (contrat documenté dans docs/SIGNAL_FORMAT.md).

Le modèle refuse tout signal incohérent : prix non finis ou négatifs, ordre
SL < ENTRY_2 < ENTRY_1 < TP violé, poids ne sommant pas à 1, RR ne correspondant pas aux
prix, horodatages non UTC ou désordonnés, politique de sortie inconnue ou d'empreinte
différente. Les RR sont TOUJOURS recalculés à partir des prix ; un RR fourni différent
est une erreur, pas une valeur.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..backtest.exits import EXIT_POLICIES
from ..domain.enums import EntryMode, IntegrationStatus, TrendRegime, VolatilityRegime

SIGNAL_VERSION = 3
MAX_TP = 4
MAX_ENTRIES = 2
RR_QUANTUM = Decimal("0.001")
ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,160}$"
ValidationStatus = Literal["RESEARCH", "VALIDATED_OOS", "SHADOW", "DEMO_ELIGIBLE", "SCHEMA_EXAMPLE_ONLY"]
NewsStatus = Literal["OFF", "OBSERVE", "GATE_CLEAR"]
RrReference = Literal["ENTRY_1", "WEIGHTED_ENTRY"]
WeightBasis = Literal["BASE_QUANTITY", "QUOTE_BUDGET"]


def gross_rr(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    risk = entry - stop
    if risk <= 0:
        raise ValueError("risque nul ou négatif : STOP_LOSS doit être sous le prix de référence")
    return ((target - entry) / risk).quantize(RR_QUANTUM, rounding=ROUND_HALF_EVEN)


def reference_price(entries: list[Decimal], weights: tuple[Decimal, ...], basis: str, reference: str) -> Decimal:
    """Prix de référence des RR : ENTRY_1, ou prix moyen PRÉVU si toutes les entrées étaient remplies.

    BASE_QUANTITY : poids = parts de quantité → moyenne arithmétique pondérée ;
    QUOTE_BUDGET : poids = parts de budget → moyenne harmonique pondérée.
    """
    if reference == "ENTRY_1":
        return entries[0]
    if basis == "BASE_QUANTITY":
        return sum((w * p for w, p in zip(weights, entries, strict=True)), Decimal(0))
    return 1 / sum((w / p for w, p in zip(weights, entries, strict=True)), Decimal(0))


def new_signal_id(created_at: datetime) -> str:
    return f"CSI-{created_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8].upper()}"


def idempotency_key(*, market_type: str, symbol: str, strategy: str, strategy_version: int,
                    setup_time: datetime, exit_policy_id: str) -> str:
    """Clé logique stable : même setup, même bougie, même profil → même clé."""
    return f"{market_type}:{symbol}:{strategy}:v{strategy_version}:{setup_time:%Y%m%dT%H%M%SZ}:{exit_policy_id}"


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_version: Literal[3] = 3
    signal_id: str = Field(pattern=ID_PATTERN)
    idempotency_key: str = Field(pattern=ID_PATTERN)
    data_as_of: datetime
    decision_at: datetime
    created_at: datetime
    valid_from: datetime
    expires_at: datetime            # fin d'ACCEPTATION du message par le consommateur
    entry_expires_at: datetime      # fin de validité des entrées non remplies
    market_data_source: str = Field(pattern=r"^[A-Z0-9_]{1,60}$")
    environment: Literal["DEMO"]
    market_type: Literal["SPOT"] = "SPOT"
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,20}(USDT|USDC)$")
    action: Literal["BUY"] = "BUY"
    strategy: str = Field(pattern=r"^[A-Z0-9_]{1,60}$")
    strategy_version: int = Field(ge=1)
    timeframe_setup: Literal["5m", "15m", "1h", "4h"]
    entry_mode: EntryMode
    entry_count: int = Field(default=1, ge=1, le=MAX_ENTRIES)
    entry_1: Decimal
    entry_2: Decimal | None = None
    entry_weights: tuple[Decimal, ...]
    weight_basis: WeightBasis = "BASE_QUANTITY"
    stop_loss: Decimal
    tp_count: int = Field(ge=1, le=MAX_TP)
    tp_1: Decimal
    tp_2: Decimal | None = None
    tp_3: Decimal | None = None
    tp_4: Decimal | None = None
    tp_weights: tuple[Decimal, ...]
    exit_policy_id: str = Field(pattern=r"^[A-Z0-9_]{1,60}$")
    exit_policy_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    max_hold_minutes: int | None = Field(default=None, ge=1)
    rr_reference: RrReference = "ENTRY_1"
    rr_tp1_gross: Decimal
    rr_tp2_gross: Decimal | None = None
    rr_tp3_gross: Decimal | None = None
    rr_tp4_gross: Decimal | None = None
    technical_score: Decimal | None = None
    ml_probability: Decimal | None = None
    model_id: str | None = Field(default=None, pattern=ID_PATTERN)
    ml_target_id: str | None = Field(default=None, pattern=ID_PATTERN)
    ml_horizon_minutes: int | None = Field(default=None, ge=1)
    ml_calibration_id: str | None = Field(default=None, pattern=ID_PATTERN)
    trend_regime: TrendRegime
    volatility_regime: VolatilityRegime
    news_status: NewsStatus = "OFF"
    max_entry_deviation_bps: Decimal = Field(ge=0, le=500)
    validation_status: ValidationStatus
    integration_status: IntegrationStatus = IntegrationStatus.INTEGRATION_UNVERIFIED
    status: Literal["NEW"] = "NEW"
    # Hors contrat : prose après ---ANALYSIS---, ignorée par le consommateur.
    analysis: tuple[tuple[str, str], ...] = ()

    @field_validator("data_as_of", "decision_at", "created_at", "valid_from", "expires_at", "entry_expires_at")
    @classmethod
    def _utc_seconds(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise ValueError("horodatage UTC explicite requis")
        if value.microsecond:
            raise ValueError("précision à la seconde requise (format YYYY-MM-DDTHH:MM:SSZ)")
        return value.astimezone(UTC)

    @field_validator("entry_1", "entry_2", "stop_loss", "tp_1", "tp_2", "tp_3", "tp_4")
    @classmethod
    def _price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("prix positif et fini requis")
        return value

    @field_validator("technical_score", "ml_probability", "rr_tp1_gross", "rr_tp2_gross",
                     "rr_tp3_gross", "rr_tp4_gross", "max_entry_deviation_bps")
    @classmethod
    def _finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("valeur finie requise (NaN/Infinity interdits)")
        return value

    @model_validator(mode="after")
    def _coherence(self) -> Signal:
        if not (self.data_as_of <= self.decision_at <= self.created_at <= self.valid_from
                < self.expires_at <= self.entry_expires_at):
            raise ValueError("ordre requis : DATA_AS_OF <= DECISION_AT <= CREATED_AT <= VALID_FROM "
                             "< EXPIRES_AT <= ENTRY_EXPIRES_AT")
        self._check_entries()
        targets = self._check_targets()
        self._check_policy()
        reference = reference_price(self.entries, self.entry_weights, self.weight_basis, self.rr_reference)
        given = [self.rr_tp1_gross, self.rr_tp2_gross, self.rr_tp3_gross, self.rr_tp4_gross]
        for index in range(MAX_TP):
            value = given[index]
            if index < self.tp_count:
                expected = gross_rr(reference, self.stop_loss, targets[index])
                if value is None or abs(value - expected) > RR_QUANTUM / 2:
                    raise ValueError(f"RR_TP{index + 1}_GROSS attendu {expected}, reçu {value}")
            elif value is not None:
                raise ValueError(f"RR_TP{index + 1}_GROSS doit être NONE")
        ml = (self.ml_probability, self.model_id, self.ml_target_id, self.ml_horizon_minutes, self.ml_calibration_id)
        if any(v is not None for v in ml):
            if any(v is None for v in ml):
                raise ValueError("ML_PROBABILITY, MODEL_ID, ML_TARGET_ID, ML_HORIZON_MINUTES et "
                                 "ML_CALIBRATION_ID vont ensemble")
            if self.ml_probability is None or not 0 <= self.ml_probability <= 1:
                raise ValueError("ML_PROBABILITY dans [0, 1] requis")
        return self

    def _check_entries(self) -> None:
        if (self.entry_2 is None) != (self.entry_count == 1):
            raise ValueError("ENTRY_COUNT incohérent avec ENTRY_2 (ENTRY_2=NONE si et seulement si ENTRY_COUNT=1)")
        if len(self.entry_weights) != self.entry_count or any(w <= 0 for w in self.entry_weights):
            raise ValueError("ENTRY_WEIGHTS : un poids positif par entrée")
        if abs(sum(self.entry_weights, Decimal(0)) - 1) > Decimal("1e-9"):
            raise ValueError("ENTRY_WEIGHTS doit sommer à 1")
        if self.entry_2 is not None and not self.stop_loss < self.entry_2 < self.entry_1:
            raise ValueError("achat incohérent : STOP_LOSS < ENTRY_2 < ENTRY_1 requis")
        if self.entry_count == 1 and self.rr_reference != "ENTRY_1":
            raise ValueError("RR_REFERENCE=ENTRY_1 requis avec une seule entrée")

    def _check_targets(self) -> list[Decimal]:
        declared = [self.tp_1, self.tp_2, self.tp_3, self.tp_4][: self.tp_count]
        targets = [t for t in declared if t is not None]
        if len(targets) != self.tp_count:
            raise ValueError("TP_COUNT incohérent avec les TP renseignés (TP_n au-delà de TP_COUNT = NONE)")
        extra = [self.tp_2, self.tp_3, self.tp_4][self.tp_count - 1:]
        if any(t is not None for t in extra):
            raise ValueError("TP renseigné au-delà de TP_COUNT")
        if not self.stop_loss < self.entry_1 < targets[0]:
            raise ValueError("achat incohérent : STOP_LOSS < ENTRY_1 < TP_1 requis")
        if any(b <= a for a, b in zip(targets, targets[1:], strict=False)):
            raise ValueError("TP strictement croissants requis")
        if len(self.tp_weights) != self.tp_count or any(w <= 0 for w in self.tp_weights):
            raise ValueError("TP_WEIGHTS : un poids positif par TP")
        if abs(sum(self.tp_weights, Decimal(0)) - 1) > Decimal("1e-9"):
            raise ValueError("TP_WEIGHTS doit sommer à 1")
        return targets

    def _check_policy(self) -> None:
        policy = EXIT_POLICIES.get(self.exit_policy_id)
        if policy is None:
            raise ValueError(f"EXIT_POLICY_ID inconnue : {self.exit_policy_id}")
        if policy.policy_hash() != self.exit_policy_hash:
            raise ValueError(f"EXIT_POLICY_HASH attendu {policy.policy_hash()} pour {self.exit_policy_id}, "
                             f"reçu {self.exit_policy_hash} (règles différentes)")
        if self.entry_count > policy.max_entries:
            raise ValueError(f"{self.exit_policy_id} gère au plus {policy.max_entries} entrée(s)")
        if policy.time_exit != (self.max_hold_minutes is not None):
            raise ValueError("MAX_HOLD_MINUTES requis si et seulement si la politique a une sortie temporelle")

    @property
    def entries(self) -> list[Decimal]:
        return [e for e in (self.entry_1, self.entry_2)[: self.entry_count] if e is not None]

    @property
    def targets(self) -> list[Decimal]:
        return [t for t in (self.tp_1, self.tp_2, self.tp_3, self.tp_4)[: self.tp_count] if t is not None]
