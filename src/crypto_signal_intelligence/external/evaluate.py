"""Évaluation d'un signal externe : vetos déterministes, contexte, taux de base, bilan de la source.

Chemin : lecture stricte → paire dans l'univers → données fraîches → géométrie (écart à
l'entrée, stop en ATR, RR recalculés, RR net) → taux de base de cette géométrie dans ce
régime → avis. Le pourcentage affiché est le taux historique « TP1 avant SL » d'entrées
SANS sélection de même géométrie sur la même paire, après coûts : une référence, pas une
prédiction du signal. Depuis le 2026-09-30, ces entrées sont des ORDRES LIMITES identiques à celui
que la résolution simule (external/base_rate.py) : l'écart entre le bilan d'une source et ce taux
ne mélange plus son apport et l'effet du type d'ordre. L'avantage éventuel de la source se mesure ensuite, signal après
signal, par l'écart entre ses résultats réels et ce taux. Rien n'est exécuté.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from ..config import ExternalSection, Settings
from ..data.http import RETRYABLE_STATUS, HttpError, PublicHttpClient
from ..data.rest import fetch_tick_size
from ..data.schema import interval
from ..features.builder import SetupFeatureParams, build_decision_frame
from ..features.loader import MissingData, load_inputs
from ..levels.engine import TradeLevels
from ..research.protocol import development_end
from ..signals.schema import gross_rr
from .admission import (
    A_DECIDER,
    AJOUTEE,
    DEFAVORABLE,
    FAVORABLE,
    OWNER,
    REFUSEE,
    RULE,
    AdmissionLog,
    hold,
    screening_for,
)
from .audit import latest_history
from .base_rate import BaseRate, base_rate
from .parser import ExternalSignal, group_of, parse
from .record import source_record
from .registry import ExternalSignalRegistry
from .universe import FAILED, REQUESTED, UserUniverse, tick_size_for, universe_symbols

# EN_ATTENTE : paire ajoutée par le propriétaire, historique en cours de téléchargement (non enregistré).
VERDICTS = ("REFUSE", "DEFAVORABLE", "INDETERMINE", "FAVORABLE", "EN_ATTENTE")
REFUSAL, VETO, PENDING = "refus", "veto", "attente"
STOP_CHECK, RR_CHECK = "distance du stop", "RR TP1 net de coûts"
# Vetos de GÉOMÉTRIE : ils jugent le signal « à l'aveugle ». Un groupe prouvé en direct a obtenu ses résultats
# avec ces géométries-là : la preuve mesurée l'emporte sur l'a priori. Les autres contrôles restent bloquants.
GEOMETRY_CHECKS = (STOP_CHECK, RR_CHECK)
BASIS_GROUP, BASIS_GEOMETRY = "groupe", "geometrie"
GENERIC_SOURCE = "telegram"


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str
    kind: str = VETO  # `refus` : non évaluable ; `veto` : évaluable mais défavorable


@dataclass
class ExternalEvaluation:
    source: str
    evaluated_at: datetime
    signal: ExternalSignal
    verdict: str = "REFUSE"
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    base_rate: BaseRate | None = None
    source_stats: dict | None = None
    decision_time: datetime | None = None
    record_id: str | None = None
    volatility: dict | None = None           # TP1 et stop en « mouvements typiques prévus » (information)
    verdict_basis: str = ""                  # « groupe » (preuve en direct) ou « geometrie » (taux de base) si FAVORABLE
    source_proof: dict | None = None         # bilan en direct du groupe : résolus, jours, prouvé ou non

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def to_dict(self) -> dict:
        return {"source": self.source, "evaluated_at": self.evaluated_at.isoformat(), "signal": self.signal.to_dict(),
                "verdict": self.verdict, "checks": [asdict(c) for c in self.checks], "warnings": self.warnings,
                "context": self.context, "geometry": self.geometry,
                "base_rate": self.base_rate.to_dict() if self.base_rate else None,
                "source_stats": self.source_stats, "verdict_basis": self.verdict_basis,
                "volatility": self.volatility,
                "source_proof": self.source_proof,
                "decision_time": self.decision_time.isoformat() if self.decision_time else None}


def _text(value) -> str:
    return value if isinstance(value, str) else "UNKNOWN"


def _num(value) -> float | None:
    number = float(value)
    return None if math.isnan(number) else round(number, 6)


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


def _verdict(evaluation: ExternalEvaluation, cfg: ExternalSection) -> tuple[str, str]:
    """(avis, fondement). Un groupe PROUVÉ EN DIRECT rend l'avis favorable, sauf refus ou signal périmé : ses
    résultats mesurés l'emportent sur les vetos de géométrie et sur le taux de base."""
    failed = evaluation.failed
    if any(c.kind == PENDING for c in failed):
        return "EN_ATTENTE", ""
    if any(c.kind == REFUSAL for c in failed):
        return "REFUSE", ""
    proven = bool(evaluation.source_proof and evaluation.source_proof.get("proven"))
    if [c for c in failed if not (proven and c.label in GEOMETRY_CHECKS)]:
        return "DEFAVORABLE", ""
    if proven and evaluation.geometry:
        return "FAVORABLE", BASIS_GROUP
    rate = evaluation.base_rate
    if rate is None or rate.samples < cfg.min_base_rate_samples or rate.expectancy_r_ci95 is None:
        return "INDETERMINE", ""
    low, high = rate.expectancy_r_ci95
    if high <= 0:
        return "DEFAVORABLE", ""
    return ("FAVORABLE", BASIS_GEOMETRY) if low > 0 else ("INDETERMINE", "")


def _binance_tick_size(settings: Settings, symbol: str) -> Decimal:
    """Pas de prix d'une paire Spot, lu sur l'API publique (aucune clé) ; erreur si la paire n'existe pas."""
    client = PublicHttpClient.rest(settings.data.rest_base_url, retries=2)
    try:
        return fetch_tick_size(client, symbol)
    finally:
        client.close()


def _binance_unreachable(label: str, symbol: str, exc: Exception) -> Check:
    """Binance injoignable pendant la vérification : ce n'est ni un refus de la paire ni un ajout (non enregistré)."""
    return Check(label, False, f"vérification de {symbol} sur Binance impossible pour l'instant ({type(exc).__name__}) : "
                 "paire ni refusée ni ajoutée, redemander l'avis dans un moment", PENDING)


def _universe_check(settings: Settings, symbol: str, *, source: str, now: datetime, user_validated: bool,
                    tick_size_lookup) -> Check:
    """Paire hors univers, selon l'avis de screening halal (règle du propriétaire, external/admission.py) :
    défavorable → refus, même soumise à la main ; reçue automatiquement et douteuse ou inexploitable → « à
    décider », avis EN_ATTENTE (signal non transmis) ; favorable reçue automatiquement → ajout si le mode
    d'ajout automatique est actif ; soumise à la main → décision du propriétaire, qui l'emporte aussi sur un
    refus qu'il avait fait par bouton."""
    label = "paire dans l'univers"
    universe = UserUniverse(settings.external_db)
    entry = universe.get(symbol)
    if entry is not None and entry["status"] == REQUESTED:
        return Check(label, False, f"{symbol} ajoutée à l'univers le {entry['requested_at'][:16]} ; historique en "
                     "cours de téléchargement par la surveillance (quelques minutes) : redemander l'avis ensuite",
                     PENDING)
    screening = screening_for(settings, symbol)
    log = AdmissionLog(settings.external_db)
    decided = log.get(symbol)
    owner = decided if decided and decided["decided_by"] == OWNER else None
    if owner and owner["decision"] == REFUSEE:
        if not user_validated:
            return Check(label, False, f"{symbol} refusée par toi le {owner['decided_at'][:16]} : hors univers",
                         REFUSAL)
        owner = None                         # nouvelle décision du propriétaire : sa soumission manuelle
    if screening.status == DEFAVORABLE and not (owner and owner["decision"] == AJOUTEE):
        if not decided:
            log.record(symbol, screening, REFUSEE, by=RULE, now=now,
                       reason=f"défavorable au screening halal : {screening.explain()}")
        return Check(label, False, f"{symbol} défavorable au screening halal ({screening.explain()}) : refusée",
                     REFUSAL)
    if not user_validated and screening.status != FAVORABLE and not (owner and owner["decision"] == AJOUTEE):
        if not owner:
            log.record(symbol, screening, A_DECIDER, by=RULE, now=now,
                       reason=f"{screening.explain()} ; signal reçu de « {source} »")
        return Check(label, False, f"{symbol} : avis halal {screening.explain()} ; en attente de ta décision "
                     "(tableau de bord, onglet Suivi, « Cryptos à décider ») : signal non transmis", PENDING)
    if not (user_validated or settings.external.auto_add_pairs or (owner and owner["decision"] == AJOUTEE)):
        failure = f" ; téléchargement en échec ({entry['last_error']})" if entry and entry["status"] == FAILED else ""
        return Check(label, False, f"{symbol} hors univers (avis halal : {screening.explain()}), aucune donnée locale"
                     f"{failure}. La soumettre à la main (page Avis CSI) vaut validation et l'ajoute", REFUSAL)
    # Soumission manuelle = validation du propriétaire (ou mode test auto_add_pairs) : ajout (ou relance)
    # après contrôle sur Binance Spot.
    try:
        tick = (tick_size_lookup or _binance_tick_size)(settings, symbol)
    except (StopIteration, KeyError, ValueError) as exc:
        return Check(label, False, f"{symbol} introuvable sur Binance Spot ({type(exc).__name__}) : non ajoutée",
                     REFUSAL)
    except HttpError as exc:
        if exc.status is not None and 400 <= exc.status < 500 and exc.status not in RETRYABLE_STATUS:
            # Binance répond 400 (« Invalid symbol ») pour une paire qui n'existe pas.
            return Check(label, False, f"{symbol} introuvable sur Binance Spot (HTTP {exc.status}) : non ajoutée",
                         REFUSAL)
        return _binance_unreachable(label, symbol, exc)
    except Exception as exc:  # noqa: BLE001 - réseau : ni refus ni ajout, à redemander
        return _binance_unreachable(label, symbol, exc)
    reason = (f"signal soumis à la main (source « {source} » ; avis halal {screening.explain()})" if user_validated
              else f"favorable au screening halal ({screening.explain()}) ; signal reçu de « {source} »")
    universe.request(symbol, tick, reason=reason, now=now)
    if not owner:
        log.record(symbol, screening, AJOUTEE, by=OWNER if user_validated else RULE, reason=reason, now=now)
    how = ("sur ta validation" if user_validated or owner
           else "automatiquement, car favorable au screening halal")
    return Check(label, False, f"{symbol} ajoutée à l'univers {how} (pas de prix {tick}) ; historique 15m et 1h "
                 "en cours de téléchargement par la surveillance (quelques minutes) : redemander l'avis ensuite",
                 PENDING)


def evaluate(settings: Settings, text: str, *, source: str, now: datetime, record: bool = True,
             user_validated: bool = False, tick_size_lookup=None) -> ExternalEvaluation:
    """`user_validated` : signal soumis à la main par le propriétaire (sa validation ajoute une paire
    inconnue à l'univers). Un signal reçu automatiquement n'ajoute jamais rien."""
    signal = parse(text)
    if source.strip().lower().startswith(GENERIC_SOURCE):
        # BinanceSpotManager nomme « telegram <id> » un chat sans nom déclaré ; quand plusieurs groupes y sont
        # transférés, le nom écrit en tête du signal donne à chacun son propre bilan.
        source = group_of(text) or source
    evaluation = ExternalEvaluation(source=source, evaluated_at=now, signal=signal)
    evaluation.warnings.extend(signal.warnings)
    registry = ExternalSignalRegistry(settings.external_db)
    stats = registry.source_stats(source)
    evaluation.source_stats = stats[0] if stats else None
    live = source_record(registry, source, seed=settings.protocol.seed)
    history = latest_history(settings, source, now=now)
    if live is not None and live.proven:
        evaluation.source_proof = {"proven": True, "basis": "direct", "proof": live.proof, "resolved": live.resolved,
                                   "days": live.days, "r_mean": live.r_real, "r_ci95": live.r_ci95}
    elif history is not None and history.get("proven"):
        evaluation.source_proof = {"proven": True, "basis": "historique", "proof": history["text"],
                                   "resolved": history["resolved"], "days": history["days"],
                                   "r_mean": history["r_mean"], "r_ci95": history["r_ci95"],
                                   "generated_at": history.get("generated_at")}
    elif live is not None or history is not None:
        texts = [x for x in (live.proof if live else "", history["text"] if history else "") if x]
        evaluation.source_proof = {"proven": False, "basis": "", "proof": " ; ".join(texts)}
    cfg = settings.external

    def finish() -> ExternalEvaluation:
        evaluation.verdict, evaluation.verdict_basis = _verdict(evaluation, cfg)
        if record and evaluation.verdict != "EN_ATTENTE":   # réévalué et enregistré une fois les données prêtes
            rate = evaluation.base_rate
            evaluation.record_id = registry.record(
                received_at=now, source=source, content_hash=signal.content_hash, template=signal.template,
                symbol=signal.symbol, entry=signal.entries[0] if signal.entries else None, stop=signal.stop,
                tp1=signal.targets[0] if signal.targets else None, targets=signal.targets,
                decision_time=evaluation.decision_time, close=evaluation.context.get("close"),
                verdict=evaluation.verdict, p_tp1=rate.tp_first if rate else None,
                base_expectancy_r=rate.expectancy_r if rate else None, evaluation=evaluation.to_dict(),
                raw_text=text, resolvable=evaluation.verdict != "REFUSE")
        return evaluation

    if signal.errors:
        evaluation.checks.append(Check("lecture du signal", False, " ; ".join(signal.errors), REFUSAL))
        return finish()
    evaluation.checks.append(Check("lecture du signal", True, f"modèle {signal.template}, {len(signal.entries)} "
                                   f"entrée(s), {len(signal.targets)} objectif(s), stop {signal.stop:g}", REFUSAL))
    if signal.symbol not in universe_symbols(settings):
        evaluation.checks.append(_universe_check(settings, signal.symbol, source=source, now=now,
                                                 user_validated=user_validated, tick_size_lookup=tick_size_lookup))
        return finish()
    held = hold(settings, signal.symbol, now=now, source=source)
    if held is not None and not (user_validated and held[0] == "attente"):
        kind, detail = held
        evaluation.checks.append(Check("paire dans l'univers", False, detail, REFUSAL if kind == "refus" else PENDING))
        return finish()
    if held is not None:                     # soumise à la main : la décision du propriétaire, tracée
        log = AdmissionLog(settings.external_db)
        screening = screening_for(settings, signal.symbol)
        log.record(signal.symbol, screening, AJOUTEE, by=OWNER, now=now,
                   reason=f"soumise à la main par le propriétaire (avis {screening.explain()})")
    evaluation.checks.append(Check("paire dans l'univers", True, signal.symbol, REFUSAL))
    previous = registry.previous(signal.content_hash)
    if previous:
        evaluation.warnings.append(f"texte déjà évalué le {previous['received_at'][:16]} ({previous['id']}, "
                                   f"avis {previous['verdict']}, issue {previous['outcome']})")
    try:
        inputs = load_inputs(settings, signal.symbol)
    except MissingData as exc:
        evaluation.checks.append(Check("données locales", False, str(exc), REFUSAL))
        return finish()
    frame = build_decision_frame(inputs["setup"], inputs["context"], inputs["btc"],
                                 setup_timeframe=settings.data.setup_timeframe,
                                 params=SetupFeatureParams(gap_block_bars=settings.data.gap_block_bars),
                                 regimes=settings.regimes)
    last = frame.iloc[-1]
    setup_interval = interval(settings.data.setup_timeframe)
    available_at = last["available_at"].to_pydatetime()
    evaluation.decision_time = last["decision_time"].to_pydatetime()
    age = now - available_at
    fresh = age <= settings.data.max_staleness_bars * setup_interval
    evaluation.checks.append(Check("données fraîches", fresh, f"dernière bougie {settings.data.setup_timeframe} "
                                   f"clôturée {evaluation.decision_time:%Y-%m-%d %H:%M} UTC, il y a {_age(age)}"
                                   + ("" if fresh else " : lancer `download` ou vérifier l'horloge"), REFUSAL))
    if not fresh:
        return finish()
    close, atr = float(last["close"]), float(last["atr14"])
    if not math.isfinite(atr) or atr <= 0 or bool(last["data_gap_recent"]):
        evaluation.checks.append(Check("indicateurs", False, "ATR14 indisponible ou trou de données récent", REFUSAL))
        return finish()
    trend, volatility = _text(last["ctx_trend"]), _text(last["ctx_volatility"])
    evaluation.context = {
        "close": close, "atr14": round(atr, 6), "atr_pct": round(atr / close * 100, 3), "rsi14": _num(last["rsi14"]),
        "trend_1h": trend, "volatility_1h": volatility, "liquidity_1h": _text(last["ctx_liquidity"]),
        "transition_1h": _text(last["ctx_transition"]), "btc_ret_24h_pct": _num(float(last["btc_ret_24h"]) * 100),
        "donchian_high_20": _num(last["donchian_high"]), "bb_mid_20": _num(last["bb_mid"]),
        "bars_available": int(last["bars_available"]),
    }
    assert signal.stop is not None   # garanti par le parseur quand il n'y a pas d'erreur
    entry, stop, targets = signal.entries[0], float(signal.stop), signal.targets
    # Signal déjà « mort » au moment de l'avis : jamais évalué ni compté dans le bilan de la source.
    if close <= stop:
        evaluation.checks.append(Check("signal encore valable", False, f"dernier close {close:g} déjà au niveau du "
                                       f"stop {stop:g} ou dessous : signal invalidé avant réception", REFUSAL))
        return finish()
    if close >= targets[0]:
        evaluation.checks.append(Check("signal encore valable", False, f"dernier close {close:g} déjà au niveau de "
                                       f"TP1 {targets[0]:g} ou au-dessus : signal déjà joué", REFUSAL))
        return finish()
    evaluation.checks.append(Check("signal encore valable", True, f"close {close:g} entre le stop et TP1", REFUSAL))
    deviation = (entry / close - 1) * 100
    not_passed = deviation <= cfg.max_entry_deviation_pct
    evaluation.checks.append(Check("entrée par rapport au dernier prix", not_passed,
                                   f"entrée {entry:g} à {deviation:+.2f} % du dernier close {close:g}"
                                   + ("" if not_passed else " : le prix a déjà dépassé l'entrée, signal périmé")))
    if deviation < -cfg.max_entry_deviation_pct:
        evaluation.warnings.append(f"entrée {abs(deviation):.2f} % sous le prix : l'ordre limite peut ne jamais "
                                   "être rempli")
    # Entrée réellement obtenue : un achat limite AU-DESSUS du marché s'exécute tout de suite, au marché.
    effective = min(entry, close)
    if effective < entry:
        evaluation.warnings.append(f"entrée {entry:g} au-dessus du marché : exécution immédiate vers {close:g}, "
                                   "géométrie calculée sur ce prix")
    stop_atr = (effective - stop) / atr
    evaluation.checks.append(Check(STOP_CHECK, cfg.min_stop_atr <= stop_atr <= cfg.max_stop_atr,
                                   f"{stop_atr:.2f} ATR14 ({(effective - stop) / effective * 100:.2f} % sous l'entrée "
                                   f"obtenue) ; admis {cfg.min_stop_atr:g} à {cfg.max_stop_atr:g} ATR"))
    tick = tick_size_for(settings, signal.symbol)
    off_tick = [p for p in (entry, stop, *targets) if Decimal(str(p)) % tick != 0]
    if off_tick:
        evaluation.warnings.append(f"prix hors pas de cotation {tick} : {', '.join(f'{p:g}' for p in off_tick)}")
    d_entry, d_stop = Decimal(str(effective)), Decimal(str(stop))
    d_targets = tuple(Decimal(str(t)) for t in targets)
    rr = tuple(gross_rr(d_entry, d_stop, t) for t in d_targets)
    weight = (Decimal(1) / len(d_targets)).quantize(Decimal("0.0001"))
    levels = TradeLevels(reference_price=d_entry, entry=d_entry, stop=d_stop, targets=d_targets,
                         target_weights=(weight,) * len(d_targets), rr_gross=rr, entry_premium_bps=Decimal(0),
                         tick_size=tick)
    net = levels.net_rr(settings.costs["central"], 0)
    evaluation.checks.append(Check(RR_CHECK, net >= cfg.min_net_rr,
                                   f"RR brut TP1 {rr[0]} (recalculé sur l'entrée obtenue), net en coûts centraux "
                                   f"{net:.2f} ; minimum {cfg.min_net_rr:g}"))
    evaluation.geometry = {
        "entry": entry, "entry_effective": effective, "entries": signal.entries, "stop": stop, "targets": targets,
        "deviation_pct": round(deviation, 3), "stop_atr": round(stop_atr, 3),
        "stop_pct": round((effective - stop) / effective * 100, 3),
        "tp1_pct": round((targets[0] / effective - 1) * 100, 3),
        "rr_gross": [float(x) for x in rr], "rr_net_tp1_central": round(net, 3),
    }
    from ..outlook.volatility import distances, for_symbol
    evaluation.volatility = distances(for_symbol(settings, signal.symbol), tp1_pct=evaluation.geometry["tp1_pct"],
                                      stop_pct=evaluation.geometry["stop_pct"])
    # Taux de base : le MÊME ordre limite que la résolution (écart au prix, fenêtre, stop et cible fixes
    # par rapport à la limite), rejoué à chaque bougie du passé.
    evaluation.base_rate = base_rate(frame, stop_atr=(entry - stop) / atr, target_r=(targets[0] - entry) / (entry - stop),
                                     horizon=cfg.max_hold_bars, costs=settings.costs["central"], trend=trend,
                                     volatility=volatility, min_samples=cfg.min_base_rate_samples,
                                     seed=settings.protocol.seed, bootstrap_samples=settings.protocol.bootstrap_samples,
                                     entry_offset=entry / close - 1, entry_window=cfg.entry_window_bars,
                                     bar_minutes=int(setup_interval.total_seconds() // 60),
                                     history_end=pd.Timestamp(development_end(settings)))
    return finish()
