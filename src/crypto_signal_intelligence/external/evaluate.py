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

from ..config import ExternalSection, Settings
from ..data.schema import interval
from ..features.builder import SetupFeatureParams, build_decision_frame
from ..features.loader import MissingData, load_inputs
from ..levels.engine import TradeLevels
from ..signals.schema import gross_rr
from .base_rate import BaseRate, base_rate
from .parser import ExternalSignal, parse
from .registry import ExternalSignalRegistry

VERDICTS = ("REFUSE", "DEFAVORABLE", "INDETERMINE", "FAVORABLE")
REFUSAL, VETO = "refus", "veto"


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

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def to_dict(self) -> dict:
        return {"source": self.source, "evaluated_at": self.evaluated_at.isoformat(), "signal": self.signal.to_dict(),
                "verdict": self.verdict, "checks": [asdict(c) for c in self.checks], "warnings": self.warnings,
                "context": self.context, "geometry": self.geometry,
                "base_rate": self.base_rate.to_dict() if self.base_rate else None,
                "source_stats": self.source_stats,
                "decision_time": self.decision_time.isoformat() if self.decision_time else None}


def _text(value) -> str:
    return value if isinstance(value, str) else "UNKNOWN"


def _num(value) -> float | None:
    number = float(value)
    return None if math.isnan(number) else round(number, 6)


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


def _verdict(evaluation: ExternalEvaluation, cfg: ExternalSection) -> str:
    failed = evaluation.failed
    if any(c.kind == REFUSAL for c in failed):
        return "REFUSE"
    if failed:
        return "DEFAVORABLE"
    rate = evaluation.base_rate
    if rate is None or rate.samples < cfg.min_base_rate_samples or rate.expectancy_r_ci95 is None:
        return "INDETERMINE"
    low, high = rate.expectancy_r_ci95
    if high <= 0:
        return "DEFAVORABLE"
    return "FAVORABLE" if low > 0 else "INDETERMINE"


def evaluate(settings: Settings, text: str, *, source: str, now: datetime, record: bool = True) -> ExternalEvaluation:
    signal = parse(text)
    evaluation = ExternalEvaluation(source=source, evaluated_at=now, signal=signal)
    evaluation.warnings.extend(signal.warnings)
    registry = ExternalSignalRegistry(settings.external_db)
    stats = registry.source_stats(source)
    evaluation.source_stats = stats[0] if stats else None
    cfg = settings.external

    def finish() -> ExternalEvaluation:
        evaluation.verdict = _verdict(evaluation, cfg)
        if record:
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
    if signal.symbol not in settings.data.symbols:
        evaluation.checks.append(Check("paire dans l'univers", False, f"{signal.symbol} hors univers "
                                       "(docs/UNIVERSE.md) : non screenée, aucune donnée locale", REFUSAL))
        return finish()
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
    evaluation.checks.append(Check("distance du stop", cfg.min_stop_atr <= stop_atr <= cfg.max_stop_atr,
                                   f"{stop_atr:.2f} ATR14 ({(effective - stop) / effective * 100:.2f} % sous l'entrée "
                                   f"obtenue) ; admis {cfg.min_stop_atr:g} à {cfg.max_stop_atr:g} ATR"))
    tick = settings.tick_size(signal.symbol)
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
    evaluation.checks.append(Check("RR TP1 net de coûts", net >= cfg.min_net_rr,
                                   f"RR brut TP1 {rr[0]} (recalculé sur l'entrée obtenue), net en coûts centraux "
                                   f"{net:.2f} ; minimum {cfg.min_net_rr:g}"))
    evaluation.geometry = {
        "entry": entry, "entry_effective": effective, "entries": signal.entries, "stop": stop, "targets": targets,
        "deviation_pct": round(deviation, 3), "stop_atr": round(stop_atr, 3),
        "stop_pct": round((effective - stop) / effective * 100, 3),
        "tp1_pct": round((targets[0] / effective - 1) * 100, 3),
        "rr_gross": [float(x) for x in rr], "rr_net_tp1_central": round(net, 3),
    }
    # Taux de base : le MÊME ordre limite que la résolution (écart au prix, fenêtre, stop et cible fixes
    # par rapport à la limite), rejoué à chaque bougie du passé.
    evaluation.base_rate = base_rate(frame, stop_atr=(entry - stop) / atr, target_r=(targets[0] - entry) / (entry - stop),
                                     horizon=cfg.max_hold_bars, costs=settings.costs["central"], trend=trend,
                                     volatility=volatility, min_samples=cfg.min_base_rate_samples,
                                     seed=settings.protocol.seed, bootstrap_samples=settings.protocol.bootstrap_samples,
                                     entry_offset=entry / close - 1, entry_window=cfg.entry_window_bars,
                                     bar_minutes=int(setup_interval.total_seconds() // 60))
    return finish()
