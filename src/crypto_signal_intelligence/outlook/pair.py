"""Perspective d'une paire à un horizon de 1 heure à 7 jours : contexte, historique comparable, plan
indicatif évalué sur le passé, avis des stratégies. Descriptif, jamais une promesse ; aucun ordre.

Chaque chiffre a sa définition (rappelée dans la réponse) :
- moments comparables : bougies 15 min clôturées du passé de la MÊME paire, dans le même régime 1 h
  (tendance et volatilité) qu'à présent ; à défaut d'assez d'exemples, tous régimes (signalé) ;
- « hausse à l'horizon » : part de ces moments où la clôture H plus tard dépassait la clôture de
  décision (fréquence historique, pas la probabilité que le prochain mouvement monte) ;
- « gain net » : achat à l'ouverture suivante, vente à la clôture H plus tard, frais, glissement et
  demi-spread du scénario central des deux côtés ;
- plan indicatif (achat seulement) : entrée à l'ouverture suivante, stop à 1 σ_H et objectif à 1,5 σ_H
  sous / au-dessus de l'entrée (σ_H : volatilité réalisée des 96 dernières bougies 15 min × √H, connue à
  la décision), sinon sortie à l'horizon ; conventions défavorables de CSI (stop et objectif dans la
  même bougie → stop ; ouverture sous le stop → sortie à l'ouverture ; au-dessus de l'objectif →
  sortie à l'objectif) ; R = gain net rapporté au risque prévu (entrée − stop) ;
- IC95 : bootstrap par blocs de jours consécutifs (les fenêtres se chevauchent : un intervalle naïf
  serait trop étroit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci95
from ..config import CostScenario, Settings
from ..data.schema import interval
from ..domain.market import MAX_CONTEXT_AGE
from ..external.universe import tick_size_for, universe_symbols
from ..features.builder import SetupFeatureParams, build_decision_frame
from ..features.loader import load_inputs

HORIZONS: dict[str, tuple[int, str]] = {
    "1h": (4, "1 heure"), "4h": (16, "4 heures"), "12h": (48, "12 heures"),
    "24h": (96, "1 jour"), "3j": (288, "3 jours"), "7j": (672, "7 jours"),
}
STOP_SIGMA, TARGET_SIGMA = 1.0, 1.5
TP, SL, TIMEOUT = 1, -1, 0
MAX_PLAN_SAMPLES_PER_WINDOW = 16       # décisions échantillonnées par fenêtre de H bougies (plan indicatif)

DEFINITIONS = {
    "comparable": "moments passés de la même paire dans le même régime 1 h (tendance et volatilité) qu'à "
                  "présent ; à défaut d'assez d'exemples, tous régimes",
    "p_up": "part des moments comparables où la clôture H plus tard dépassait la clôture de décision : une "
            "fréquence historique, pas la probabilité que le prochain mouvement monte",
    "net": "achat à l'ouverture suivante, vente à la clôture H plus tard, coûts du scénario central inclus",
    "plan": "achat à l'ouverture suivante, stop à 1 σ_H, objectif à 1,5 σ_H (σ_H : volatilité réalisée récente × "
            "√H), sinon sortie à l'horizon ; stop et objectif dans la même bougie → stop",
    "ci95": "intervalle de confiance à 95 % par bootstrap de blocs de jours consécutifs",
    "verdict": "FAVORABLE seulement si l'IC95 de l'espérance du plan est entièrement au-dessus de 0 ; "
               "DÉFAVORABLE s'il est entièrement en dessous ; sinon INDÉTERMINÉ (pas d'avantage démontré)",
}


class OutlookError(ValueError):
    """Demande impossible (paire hors univers, horizon inconnu, données absentes)."""


def block_days_for(bars: int, bar_minutes: int = 15) -> int:
    """Blocs du bootstrap : au moins 10 jours, et au moins deux fois la durée d'une fenêtre."""
    return max(10, 2 * math.ceil(bars * bar_minutes / 1440))


def _contiguous(open_time: pd.Series, bars: int, step: pd.Timedelta) -> np.ndarray:
    return ((open_time.shift(-bars) - open_time) == bars * step).to_numpy()


def forward_returns(frame: pd.DataFrame, bars: int, costs: CostScenario,
                    step: pd.Timedelta) -> tuple[np.ndarray, np.ndarray]:
    """(variation brute clôture → clôture H plus tard, gain net achat ouverture suivante → clôture H plus tard)."""
    close, opening = frame["close"], frame["open"]
    ok = _contiguous(frame["open_time"], bars, step)
    market = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    fee = costs.fee_bps / 1e4
    gross = (close.shift(-bars) / close - 1).to_numpy()
    net = (close.shift(-bars) * (1 - market) * (1 - fee) / (opening.shift(-1) * (1 + market) * (1 + fee)) - 1).to_numpy()
    return np.where(ok, gross, np.nan), np.where(ok, net, np.nan)


@dataclass(frozen=True)
class PlanOutcomes:
    rows: np.ndarray        # bougies de décision évaluées
    outcome: np.ndarray     # TP / SL / TIMEOUT
    net: np.ndarray         # gain net (fraction)
    r: np.ndarray           # gain net / risque prévu


def plan_outcomes(frame: pd.DataFrame, rows: np.ndarray, bars: int, costs: CostScenario, step: pd.Timedelta,
                  stop_sigma: float = STOP_SIGMA, target_sigma: float = TARGET_SIGMA) -> PlanOutcomes:
    """Plan indicatif rejoué à chaque décision de `rows` (voir la docstring du module)."""
    o, h, low, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(frame)
    sigma = frame["realized_vol_96"].to_numpy(float) * math.sqrt(bars)
    rows = np.asarray(rows, dtype=np.int64)
    rows = rows[(rows + bars < n) & np.isfinite(sigma[rows])]
    rows = rows[_contiguous(frame["open_time"], bars, step)[rows]]
    entry = o[rows + 1]
    stop, target = entry * (1 - stop_sigma * sigma[rows]), entry * (1 + target_sigma * sigma[rows])
    keep = np.isfinite(entry) & (stop > 0)
    rows, entry, stop, target = rows[keep], entry[keep], stop[keep], target[keep]
    exit_price = np.full(len(rows), np.nan)
    outcome = np.full(len(rows), TIMEOUT, dtype=np.int8)
    active = np.ones(len(rows), dtype=bool)
    for j in range(1, bars + 1):
        k = rows + j
        oj, hj, lj, cj = o[k], h[k], low[k], c[k]
        rules = [] if j == 1 else [(oj <= stop, oj, SL), (oj >= target, target, TP)]
        rules += [(lj <= stop, stop, SL), (hj >= target, target, TP)]
        if j == bars:
            rules.append((np.ones(len(rows), dtype=bool), cj, TIMEOUT))
        for hit, price, label in rules:
            now = active & hit
            exit_price[now], outcome[now] = price[now], label
            active &= ~now
    market = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    fee = costs.fee_bps / 1e4
    net = exit_price * (1 - market) * (1 - fee) / (entry * (1 + market) * (1 + fee)) - 1
    risk = (entry - stop) / entry
    return PlanOutcomes(rows, outcome, net, net / risk)


def _round_tick(value: float, tick: Decimal, rounding: str) -> Decimal:
    return (Decimal(str(value)) / tick).to_integral_value(rounding=rounding) * tick


def _rate(values: np.ndarray, times: np.ndarray, *, block_days: int, settings: Settings) -> dict:
    """Moyenne et IC95 par blocs de jours d'une série d'événements chevauchants."""
    protocol = settings.protocol
    if len(values) == 0:
        return {"value": None, "ci95": None}
    ci, _ = day_block_ci95(values.astype(float), times, block_days=block_days, samples=protocol.bootstrap_samples,
                           seed=protocol.seed)
    return {"value": round(float(np.mean(values)), 4), "ci95": list(ci) if ci else None}


def _regime(frame: pd.DataFrame, last: pd.Series, decision_available: pd.Timestamp) -> tuple[str, str, bool]:
    """Régime 1 h courant ; inconnu si la bougie 1 h de contexte est trop ancienne."""
    seen = last.get("ctx_available_at")
    fresh = seen is not None and not pd.isna(seen) and decision_available - seen <= MAX_CONTEXT_AGE
    trend = last.get("ctx_trend") if fresh else None
    volatility = last.get("ctx_volatility") if fresh else None
    trend = trend if isinstance(trend, str) else "UNKNOWN"
    volatility = volatility if isinstance(volatility, str) else "UNKNOWN"
    return trend, volatility, bool(fresh)


def _number(value, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, digits)


def pair_outlook(settings: Settings, symbol: str, horizon: str, *, now: datetime,
                 inputs: dict | None = None) -> dict:
    symbol = symbol.strip().upper()
    if horizon not in HORIZONS:
        raise OutlookError(f"horizon inconnu : {horizon} (choix : {', '.join(HORIZONS)})")
    if symbol not in universe_symbols(settings):
        raise OutlookError(f"{symbol} hors univers : soumettre un signal à la main sur cette paire l'ajoute "
                           "(onglet Signal), puis attendre le téléchargement de son historique")
    try:
        data = inputs if inputs is not None else load_inputs(settings, symbol)
    except Exception as exc:  # noqa: BLE001 - données absentes ou illisibles : message clair
        raise OutlookError(f"données de {symbol} indisponibles : {exc}") from None
    if data["setup"].empty:
        raise OutlookError(f"aucune bougie {settings.data.setup_timeframe} pour {symbol}")
    step = interval(settings.data.setup_timeframe)
    frame = build_decision_frame(data["setup"], data["context"], data["btc"],
                                 setup_timeframe=settings.data.setup_timeframe,
                                 params=SetupFeatureParams(gap_block_bars=settings.data.gap_block_bars),
                                 regimes=settings.regimes)
    last = frame.iloc[-1]
    decision_time = last["decision_time"]
    age = pd.Timestamp(now) - last["available_at"]
    fresh = age <= settings.data.max_staleness_bars * step
    trend, volatility, context_fresh = _regime(frame, last, last["available_at"])
    costs = settings.costs["central"]
    times = frame["decision_time"].to_numpy()
    usable = ~frame["data_gap_recent"].to_numpy(bool)
    in_regime = usable & (frame["ctx_trend"].to_numpy() == trend) & (frame["ctx_volatility"].to_numpy() == volatility)
    min_samples = settings.external.min_base_rate_samples
    close = float(last["close"])

    # 1. Vue d'ensemble : fréquences historiques à chaque horizon, dans le régime courant.
    overview = []
    for key, (bars, label) in HORIZONS.items():
        gross, net = forward_returns(frame, bars, costs, step)
        known = np.isfinite(gross) & usable
        mask, conditioned = known & in_regime, True
        if trend == "UNKNOWN" or volatility == "UNKNOWN" or mask.sum() < min_samples:
            mask, conditioned = known, False
        sample_gross, sample_net, sample_times = gross[mask], net[mask], times[mask]
        days = block_days_for(bars, int(step.total_seconds() // 60))
        overview.append({
            "horizon": key, "label": label, "samples": int(mask.sum()), "regime_conditioned": conditioned,
            "p_up": _rate(sample_gross > 0, sample_times, block_days=days, settings=settings),
            "p_net_positive": _rate(sample_net > 0, sample_times, block_days=days, settings=settings),
            "mean_net": _rate(sample_net, sample_times, block_days=days, settings=settings),
            "median_gross": _number(np.median(sample_gross)) if len(sample_gross) else None,
            "q10_gross": _number(np.quantile(sample_gross, 0.10)) if len(sample_gross) else None,
            "q90_gross": _number(np.quantile(sample_gross, 0.90)) if len(sample_gross) else None,
            "block_days": days,
        })

    # 2. Plan indicatif pour l'horizon choisi, évalué sur les moments comparables du passé.
    bars, label = HORIZONS[horizon]
    stride = max(1, bars // MAX_PLAN_SAMPLES_PER_WINDOW)
    candidates = np.flatnonzero(usable)
    candidates = candidates[candidates % stride == 0]
    outcomes = plan_outcomes(frame, candidates, bars, costs, step)
    regime_rows = in_regime[outcomes.rows]
    conditioned = trend != "UNKNOWN" and volatility != "UNKNOWN" and regime_rows.sum() * stride >= min_samples
    pick = regime_rows if conditioned else np.ones(len(outcomes.rows), dtype=bool)
    sample_r, sample_net = outcomes.r[pick], outcomes.net[pick]
    sample_outcome, sample_times = outcomes.outcome[pick], times[outcomes.rows[pick]]
    days = block_days_for(bars, int(step.total_seconds() // 60))
    expectancy = _rate(sample_r, sample_times, block_days=days, settings=settings)
    sigma_now = float(last["realized_vol_96"]) * math.sqrt(bars)
    tick = tick_size_for(settings, symbol)
    plan: dict = {"horizon": horizon, "label": label, "samples": int(pick.sum()), "sampling_stride_bars": stride,
                  "regime_conditioned": bool(conditioned), "block_days": days}
    if math.isfinite(sigma_now) and sigma_now > 0:
        entry = _round_tick(close, tick, ROUND_FLOOR)
        stop = _round_tick(close * (1 - STOP_SIGMA * sigma_now), tick, ROUND_FLOOR)
        target = _round_tick(close * (1 + TARGET_SIGMA * sigma_now), tick, ROUND_CEILING)
        risk_fraction = STOP_SIGMA * sigma_now
        cost_r = 2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps) / 1e4 / risk_fraction
        plan |= {"entry_reference": str(entry), "stop": str(stop), "target": str(target),
                 "stop_pct": round(-STOP_SIGMA * sigma_now * 100, 3), "target_pct": round(TARGET_SIGMA * sigma_now * 100, 3),
                 "rr_gross": TARGET_SIGMA / STOP_SIGMA, "costs_in_r": round(cost_r, 3),
                 # Taux de réussite qui annulerait l'espérance (sans sortie au temps) : p×RR − (1−p) − coûts = 0.
                 "breakeven_win_rate": round((1 + cost_r) / (1 + TARGET_SIGMA / STOP_SIGMA), 4)}
    else:
        plan["unavailable"] = "volatilité récente inconnue (historique trop court ou trou de données)"
    if len(sample_outcome):
        plan |= {"tp_first": round(float((sample_outcome == TP).mean()), 4),
                 "sl_first": round(float((sample_outcome == SL).mean()), 4),
                 "timeout": round(float((sample_outcome == TIMEOUT).mean()), 4),
                 "expectancy_r": expectancy["value"], "expectancy_r_ci95": expectancy["ci95"],
                 "mean_net_pct": round(float(np.mean(sample_net)) * 100, 3)}
    if not fresh:
        verdict = "DONNEES_ANCIENNES"
    elif "unavailable" in plan or expectancy["ci95"] is None or plan["samples"] * stride < min_samples:
        verdict = "INSUFFISANT"
    elif expectancy["ci95"][0] > 0:
        verdict = "FAVORABLE"
    elif expectancy["ci95"][1] <= 0:
        verdict = "DEFAVORABLE"
    else:
        verdict = "INDETERMINE"
    plan["verdict"] = verdict

    context = {
        "close": close, "decision_time": decision_time.isoformat(), "data_age_minutes": int(age.total_seconds() // 60),
        "fresh": bool(fresh), "context_fresh": context_fresh, "trend_1h": trend, "volatility_1h": volatility,
        "liquidity_1h": str(last.get("ctx_liquidity")) if context_fresh else "UNKNOWN",
        "transition_1h": str(last.get("ctx_transition")) if context_fresh else "UNKNOWN",
        "ret_1h_pct": _number(float(np.expm1(last["ret_4"])) * 100, 3) if pd.notna(last["ret_4"]) else None,
        "ret_24h_pct": _number(float(last["ctx_ret_24h"]) * 100, 3) if context_fresh else None,
        "btc_ret_24h_pct": _number(float(last["btc_ret_24h"]) * 100, 3),
        "atr_pct": _number(float(last["atr14"]) / close * 100, 3),
        "rsi14": _number(last["rsi14"], 1),
        "dist_ema50_pct": _number((close / float(last["ema50"]) - 1) * 100, 3),
        "realized_vol_24h_pct": _number(float(last["realized_vol_96"]) * math.sqrt(96) * 100, 3),
        "bars_available": int(last["bars_available"]),
    }
    return {"symbol": symbol, "horizon": horizon, "horizon_label": label, "evaluated_at": pd.Timestamp(now).isoformat(),
            "context": context, "overview": overview, "plan": plan, "definitions": DEFINITIONS,
            "costs_round_trip_pct": round(2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps) / 100, 3),
            "warning": "Historique de la paire, pas une prévision garantie : aucune stratégie de CSI n'a démontré "
                       "d'avantage exploitable à ce jour."}
