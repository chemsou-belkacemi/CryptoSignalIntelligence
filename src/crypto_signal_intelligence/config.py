"""Configuration typée : config/default.toml, surchargée par les variables CSI_*.

Priorité : arguments explicites > variables d'environnement (CSI_SECTION__CLE)
> fichier TOML (CSI_CONFIG_FILE, sinon config/default.toml sous la racine).
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
VALIDATED_TIMEFRAMES = {"15m", "1h"}


def project_root() -> Path:
    if os.environ.get("CSI_ROOT"):
        return Path(os.environ["CSI_ROOT"]).resolve()
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "config").is_dir():
            return candidate
    return here


def config_file() -> Path:
    return Path(os.environ.get("CSI_CONFIG_FILE", project_root() / "config" / "default.toml"))


class ProjectSection(BaseModel):
    market_data_source: str = "BINANCE_SPOT_PUBLIC"
    intended_execution_environment: Literal["DEMO"] = "DEMO"


class DataSection(BaseModel):
    symbols: list[str] = ["BTCUSDT", "ETHUSDT"]
    setup_timeframe: str = "15m"
    context_timeframe: str = "1h"
    enabled_timeframes: list[str] = ["15m", "1h"]
    history_start: date = date(2021, 1, 1)
    archive_base_url: str = "https://data.binance.vision"
    rest_base_url: str = "https://data-api.binance.vision"
    assumed_availability_latency_seconds: float = 2
    reconcile_overlap_bars: int = Field(3, ge=1, le=100)
    max_staleness_bars: int = Field(2, ge=1)
    gap_block_bars: int = Field(200, ge=0)
    tick_size: dict[str, Decimal] = {}

    @field_validator("symbols")
    @classmethod
    def _spot_quotes(cls, value: list[str]) -> list[str]:
        value = [s.upper() for s in value]
        if not value or any(not s.endswith(("USDT", "USDC")) for s in value):
            raise ValueError("symboles Spot USDT/USDC uniquement")
        return value

    @model_validator(mode="after")
    def _timeframes(self) -> DataSection:
        for tf in (*self.enabled_timeframes, self.setup_timeframe, self.context_timeframe):
            if tf not in TIMEFRAMES:
                raise ValueError(f"timeframe inconnu : {tf}")
            if tf not in VALIDATED_TIMEFRAMES:
                raise ValueError(f"{tf} prévu mais non activé : jointures temporelles non validées")
        return self


class RegimeSection(BaseModel):
    volatility_window_bars: int = 720
    volatility_min_bars: int = 240
    volatility_low_quantile: float = 0.2
    volatility_high_quantile: float = 0.8
    liquidity_window_bars: int = 720
    liquidity_min_bars: int = 240
    liquidity_low_ratio: float = 0.3
    liquidity_min_quote_volume_24h: float = 5_000_000
    transition_lookback_bars: int = 6


class ProtocolSection(BaseModel):
    development_end: datetime = datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC)
    final_test_start: datetime = datetime(2025, 7, 1, tzinfo=UTC)
    bootstrap_block_days: int = 10   # IC des backtests : blocs de jours consécutifs
    bootstrap_samples: int = 2000
    seed: int = 20260929

    @model_validator(mode="after")
    def _ordered(self) -> ProtocolSection:
        if self.final_test_start <= self.development_end:
            raise ValueError("final_test_start doit suivre development_end")
        return self


class SimulationSection(BaseModel):
    max_hold_bars: int = Field(96, ge=1)
    risk_fraction_per_trade: float = Field(0.005, gt=0, le=0.05)


class WalkForwardSection(BaseModel):
    train_min_months: int = Field(18, ge=1)
    test_months: int = Field(6, ge=1)
    min_train_trades: int = Field(30, ge=1)


class AdmissionSection(BaseModel):
    min_closed_trades: int = Field(150, ge=1)
    min_windows_with_trades: int = Field(3, ge=1)
    max_group_pnl_share: float = Field(0.6, gt=0, le=1)
    max_drawdown_r: float = Field(15, gt=0)


class ExternalSection(BaseModel):
    entry_window_bars: int = Field(96, ge=1)
    max_hold_bars: int = Field(672, ge=1)
    max_entry_deviation_pct: float = Field(3.0, gt=0)
    min_stop_atr: float = Field(0.5, gt=0)
    max_stop_atr: float = Field(8.0, gt=0)
    min_net_rr: float = Field(0.8, ge=0)
    min_base_rate_samples: int = Field(300, ge=1)


class CostScenario(BaseModel):
    fee_bps: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    half_spread_bps: float = Field(ge=0)
    extra_entry_delay_bars: int = Field(0, ge=0)


class PublicationSection(BaseModel):
    mode: Literal["shadow", "outbox"] = "shadow"
    shadow_dir: str = "signals/shadow"
    outbox_dir: str = "signals/outbox"
    outbox_enabled: bool = False
    integration_verified: bool = False
    # EXPIRES_AT = création + ce délai (plafonné à ENTRY_EXPIRES_AT) : au-delà, le consommateur
    # refuse le message ; les entrées déjà placées restent valables jusqu'à ENTRY_EXPIRES_AT.
    message_ttl_minutes: int = Field(15, ge=1, le=1440)
    # MAX_ENTRY_DEVIATION_BPS publié = écart réel référence → ENTRY_1 (arrondi compris) + cette tolérance.
    entry_tolerance_bps: int = Field(25, ge=0, le=400)
    # Politiques de sortie que le consommateur exécute à l'identique (docs/BSM_PROFILE.md) ; hors
    # shadow, un signal portant une autre politique n'est pas publié (EXIT_POLICY_MISMATCH).
    consumer_policies: list[str] = ["BSM_MARKET_TP_FIXED_SL_V2", "BSM_MARKET_TP_BREAK_EVEN_V2"]


class LiveSection(BaseModel):
    """Surveillance continue (`run`) : déclenchement aux clôtures, attente bornée, fenêtres en mémoire."""
    grace_seconds: int = Field(20, ge=0, le=300)          # délai après la clôture avant de demander la bougie
    candle_wait_seconds: int = Field(90, ge=0, le=900)    # attente bornée des bougies attendues (15m et 1h)
    retry_seconds: int = Field(15, ge=1, le=300)
    refresh_workers: int = Field(4, ge=1, le=8)          # séries rafraîchies en parallèle (poids REST faible)
    setup_tail_bars: int = Field(3000, ge=500)            # ≈ 31 jours de 15m : fenêtre d'indicateurs en direct
    context_tail_bars: int = Field(1500, ge=300)          # ≈ 62 jours de 1h
    lock_file: str = "state/run.lock"
    status_file: str = "state/run_status.json"


class NewsSourceConfig(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    kind: Literal["rss", "binance_cms"]
    url: str
    category: Literal["CRYPTO_MEDIA", "EXCHANGE_OFFICIAL", "MACRO_OFFICIAL", "REGULATOR"]
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source d'actualités : HTTPS obligatoire")
        return value


DEFAULT_NEWS_SOURCES = [
    NewsSourceConfig(source_id="coindesk", kind="rss", url="https://www.coindesk.com/arc/outboundfeeds/rss/",
                     category="CRYPTO_MEDIA"),
    NewsSourceConfig(source_id="cointelegraph", kind="rss", url="https://cointelegraph.com/rss", category="CRYPTO_MEDIA"),
    NewsSourceConfig(source_id="decrypt", kind="rss", url="https://decrypt.co/feed", category="CRYPTO_MEDIA"),
    NewsSourceConfig(source_id="theblock", kind="rss", url="https://www.theblock.co/rss.xml", category="CRYPTO_MEDIA"),
    NewsSourceConfig(source_id="binance_announcements", kind="binance_cms",
                     url="https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20",
                     category="EXCHANGE_OFFICIAL"),
    NewsSourceConfig(source_id="federal_reserve", kind="rss", url="https://www.federalreserve.gov/feeds/press_all.xml",
                     category="MACRO_OFFICIAL"),
    NewsSourceConfig(source_id="ecb", kind="rss", url="https://www.ecb.europa.eu/rss/press.html",
                     category="MACRO_OFFICIAL"),
    NewsSourceConfig(source_id="sec", kind="rss", url="https://www.sec.gov/news/pressreleases.rss", category="REGULATOR"),
]


class NewsSection(BaseModel):
    """Collecte d'actualités sans LLM. `observe` : enregistrées, aucune influence sur les signaux."""
    mode: Literal["off", "observe", "gate"] = "observe"
    collect_every_minutes: int = Field(15, ge=5, le=1440)
    timeout_seconds: float = Field(10.0, gt=0, le=60)
    max_bytes: int = Field(3_000_000, ge=10_000)
    cluster_window_hours: int = Field(48, ge=1, le=720)
    cluster_similarity: float = Field(0.5, gt=0, le=1)
    stale_after_minutes: int = Field(90, ge=15)     # sans succès depuis : source DOWN (jamais « rien à signaler »)
    sources: list[NewsSourceConfig] = DEFAULT_NEWS_SOURCES

    @field_validator("mode")
    @classmethod
    def _no_gate_without_rules(cls, value: str) -> str:
        if value == "gate":
            raise ValueError("news.mode = gate refusé : aucune règle de blocage n'a encore été évaluée (point 9)")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CSI_", env_nested_delimiter="__", extra="ignore")

    project: ProjectSection = ProjectSection()
    data: DataSection = DataSection()
    regimes: RegimeSection = RegimeSection()
    protocol: ProtocolSection = ProtocolSection()
    simulation: SimulationSection = SimulationSection()
    walk_forward: WalkForwardSection = WalkForwardSection()
    admission: AdmissionSection = AdmissionSection()
    external: ExternalSection = ExternalSection()
    costs: dict[str, CostScenario] = {}
    publication: PublicationSection = PublicationSection()
    live: LiveSection = LiveSection()
    news: NewsSection = NewsSection()
    strategies: dict[str, dict[str, Any]] = {}
    root: Path = Field(default_factory=project_root)

    @classmethod
    def settings_customise_sources(cls, settings_cls: type[BaseSettings],
                                   init_settings: PydanticBaseSettingsSource,
                                   env_settings: PydanticBaseSettingsSource,
                                   dotenv_settings: PydanticBaseSettingsSource,
                                   file_secret_settings: PydanticBaseSettingsSource):
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls, toml_file=config_file()))

    @model_validator(mode="after")
    def _costs(self) -> Settings:
        if "central" not in self.costs:
            raise ValueError("scénario de coûts 'central' requis")
        return self

    # Chemins dérivés, tous sous la racine du projet.
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def experiments_db(self) -> Path:
        return self.root / "experiments" / "experiments.sqlite3"

    @property
    def signals_db(self) -> Path:
        return self.root / "signals" / "registry.sqlite3"

    @property
    def external_db(self) -> Path:
        return self.root / "signals" / "external.sqlite3"

    @property
    def feedback_db(self) -> Path:
        return self.root / "signals" / "feedback.sqlite3"

    @property
    def news_db(self) -> Path:
        return self.root / "news" / "news.sqlite3"

    def publication_dir(self) -> Path:
        pub = self.publication
        if pub.mode == "outbox":
            if not (pub.outbox_enabled and pub.integration_verified):
                raise PermissionError("Publication outbox refusée : intégration non vérifiée ou non activée.")
            return self.root / pub.outbox_dir
        return self.root / pub.shadow_dir

    def tick_size(self, symbol: str) -> Decimal:
        try:
            return self.data.tick_size[symbol]
        except KeyError:
            raise ValueError(f"tick_size inconnu pour {symbol} : l'ajouter dans [data].tick_size") from None


def load_settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


def utc_now() -> datetime:
    return datetime.now(UTC)
