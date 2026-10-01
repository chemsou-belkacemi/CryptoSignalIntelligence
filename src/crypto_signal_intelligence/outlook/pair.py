"""Perspective d'une paire à un horizon de 1 heure à 7 jours : situation actuelle, historique comparable,
plan indicatif rejoué sur le passé, avis des stratégies. DESCRIPTIF : jamais une proposition d'entrer,
jamais un verdict du protocole ; aucun ordre.

Règles (relecture leak-auditor du 2026-10-01) :
- historique : seuls les moments dont l'issue est connue au plus tard à la fin de DEVELOPMENT
  (2025-06-30) ; la période suivante est réservée au test final de la recherche (docs/PROTOCOL.md) ;
  la situation ACTUELLE (dernière bougie disponible à l'heure demandée) est décrite normalement ;
- moments comparables : même paire, même régime 1 h (tendance, volatilité) CONNU à chaque instant
  (contexte de moins de 2 h) ; à défaut d'au moins MIN_BLOCKS blocs de jours indépendants, tous
  régimes (signalé) ;
- intervalles : bootstrap par blocs de jours consécutifs, niveau corrigé pour les 6 horizons
  consultés (Bonferroni), affichés seulement avec au moins MIN_BLOCKS blocs ;
- plan indicatif (achat) : entrée à l'ouverture suivante, stop à 1 σ_H, objectif à 1,5 σ_H (σ_H :
  volatilité réalisée des 96 dernières bougies 15 min × √H, connue à la décision), sinon sortie à
  l'horizon ; stop touché au contact, objectif seulement s'il est DÉPASSÉ, stop et objectif dans la
  même bougie → stop, ouverture au-delà d'une barrière → sortie à l'ouverture (stop) ou à l'objectif ;
  coûts du scénario central aux deux remplissages ; R = gain net / risque prévu (entrée − stop) ;
- état « historique positif (non validé) » seulement si l'intervalle corrigé de l'espérance du plan ET
  celui de son écart à la moyenne du même plan sur TOUS les moments (dérive passée) sont au-dessus de 0,
  avec assez de blocs et sans année qui pèse plus de 60 % du résultat.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from ..config import CostScenario, Settings
from ..data.schema import interval
from ..domain.market import MAX_CONTEXT_AGE
from ..external.universe import tick_size_for, universe_symbols
from ..features.builder import SetupFeatureParams, build_decision_frame
from ..features.loader import load_inputs
from ..research.protocol import development_end

HORIZONS: dict[str, tuple[int, str]] = {
    "1h": (4, "1 heure"), "4h": (16, "4 heures"), "12h": (48, "12 heures"),
    "24h": (96, "1 jour"), "3j": (288, "3 jours"), "7j": (672, "7 jours"),
}
STOP_SIGMA, TARGET_SIGMA = 1.0, 1.5
TP, SL, TIMEOUT = 1, -1, 0
MAX_PLAN_SAMPLES_PER_WINDOW = 16       # décisions échantillonnées par fenêtre de H bougies (plan indicatif)
CI_LEVEL = 1 - 0.05 / len(HORIZONS)    # niveau corrigé pour les 6 horizons consultés (Bonferroni)
MIN_BLOCKS = 30                        # blocs de jours indépendants exigés pour un intervalle et un état
MAX_YEAR_SHARE = 0.6
POSITIVE, NO_EDGE, NEGATIVE = "HISTORIQUE_POSITIF_NON_VALIDE", "AUCUN_AVANTAGE_HISTORIQUE", "HISTORIQUE_DEFAVORABLE"
INSUFFICIENT, STALE = "INSUFFISANT", "DONNEES_ANCIENNES"


def definitions(history_end: str) -> dict[str, str]:
    level = f"{CI_LEVEL:.1%}".replace(".", ",")
    return {
        "historique": f"moments passés de la paire dont l'issue était connue au plus tard le {history_end} ; la période "
                      "suivante est réservée au test final de la recherche (protocole)",
        "comparable": "même paire, même régime 1 h (tendance et volatilité) connu à chaque instant ; à défaut d'assez "
                      "de blocs indépendants, tous régimes (indiqué)",
        "p_up": "fréquence de hausse : part des moments comparables où la clôture H plus tard dépassait la clôture "
                "de décision ; une fréquence historique, pas la probabilité du prochain mouvement",
        "p_net_positive": "part des moments comparables où l'achat (ouverture suivante → clôture H plus tard) aurait "
                          "gagné après coûts",
        "mean_net": "gain net moyen de cet achat, coûts du scénario central inclus",
        "fourchette": "médiane et fourchette 10-90 % de la variation brute : la moitié des cas au-dessus de la médiane, "
                      "8 cas sur 10 entre les deux bornes",
        "plan": "achat à l'ouverture suivante, stop à 1 σ_H, objectif à 1,5 σ_H (σ_H : volatilité réalisée récente × "
                "√H), sinon sortie à l'horizon ; objectif compté seulement s'il est dépassé ; stop et objectif dans la "
                "même bougie → stop",
        "issues": "objectif d'abord / stop d'abord / sortie à l'horizon : répartition des issues du plan rejoué sur "
                  "les moments comparables",
        "esperance": "gain net moyen du plan en R (1 R = la perte si le stop est touché), coûts inclus",
        "ecart": "écart entre l'espérance dans les conditions actuelles et celle du même plan sur TOUS les moments "
                 "passés de la paire : sépare l'effet des conditions actuelles de la simple dérive passée du marché",
        "intervalle": f"intervalle de confiance à {level}, corrigé pour les 6 horizons consultés, par bootstrap de "
                      f"blocs de jours consécutifs ; affiché seulement avec au moins {MIN_BLOCKS} blocs indépendants",
        "exemples": "moments évalués et blocs de jours indépendants (seuls les blocs comptent pour la fiabilité)",
        "couts": "coûts aller-retour du scénario central (frais, glissement, demi-spread), aussi exprimés en R",
        "etat": "HISTORIQUE POSITIF (non validé) seulement si les intervalles corrigés de l'espérance ET de l'écart à "
                "« tous moments » sont au-dessus de 0, avec assez de blocs et sans année qui pèse plus de 60 % ; même "
                "alors, ce n'est PAS une proposition d'entrer : statistique en échantillon, non validée par le protocole",
    }


class OutlookError(ValueError):
    """Demande impossible (paire hors univers, horizon inconnu, données absentes)."""


def plan_copy_text(symbol: str, horizon_label: str, entry: object, target: object, stop: object) -> str:
    """Résumé copiable du plan, en PROSE sur une ligne : aucune « ÉTIQUETTE: valeur », pour qu'aucun lecteur de
    signaux (BinanceSpotManager compris, testé dans tests/test_bsm_contract.py) ne le prenne pour un ordre."""
    pair = f"{symbol[:-4]}/{symbol[-4:]}" if symbol.endswith(("USDT", "USDC")) else symbol
    return (f"CSI — plan indicatif, PAS un signal (aucune stratégie validée) : {pair}, horizon {horizon_label}, "
            f"achat au marché vers {entry}, objectif {target}, stop {stop}")


def block_days_for(bars: int, bar_minutes: int = 15) -> int:
    """Blocs du bootstrap : au moins 10 jours, et au moins deux fois la durée d'une fenêtre."""
    return max(10, 2 * math.ceil(bars * bar_minutes / 1440))


def block_interval(values: np.ndarray, times: np.ndarray, *, block_days: int, samples: int, seed: int,
                   level: float = CI_LEVEL, min_blocks: int = MIN_BLOCKS) -> tuple[list[float] | None, int]:
    """(intervalle de la moyenne, nombre de blocs) : tirage avec remise de blocs de `block_days` jours ayant des
    données, consécutifs ; aucun intervalle sous `min_blocks` blocs."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return None, 0
    days = pd.to_datetime(pd.Series(times), utc=True).dt.floor("D")
    frame = pd.DataFrame({"day": days.to_numpy(), "v": values}).groupby("day")["v"].agg(["sum", "count"])
    blocks = math.ceil(len(frame) / block_days)
    if blocks < min_blocks:
        return None, blocks
    pad = blocks * block_days - len(frame)
    sums = np.concatenate([frame["sum"].to_numpy(), np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    counts = np.concatenate([frame["count"].to_numpy(), np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    picks = np.random.default_rng(seed).integers(0, blocks, size=(samples, blocks))
    draws = sums[picks].sum(axis=1) / np.maximum(counts[picks].sum(axis=1), 1)
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(draws, [tail, 100 - tail])
    return [round(float(low), 6), round(float(high), 6)], blocks


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
    rows = rows[(rows + bars < n)]
    rows = rows[np.isfinite(sigma[rows]) & _contiguous(frame["open_time"], bars, step)[rows]]
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
        rules += [(lj <= stop, stop, SL), (hj > target, target, TP)]      # objectif : seulement s'il est dépassé
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


def _number(value, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else round(number, digits)


def _text(value) -> str:
    return value if isinstance(value, str) else "UNKNOWN"


def _fresh_context(last: pd.Series, prefix: str) -> bool:
    seen = last.get(f"{prefix}available_at")
    return seen is not None and not pd.isna(seen) and last["available_at"] - seen <= MAX_CONTEXT_AGE


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
    moment = pd.Timestamp(now)
    # Point dans le temps : seules les bougies disponibles à l'heure demandée.
    data = {name: frame[frame["available_at"] <= moment] for name, frame in data.items()}
    if data["setup"].empty:
        raise OutlookError(f"aucune bougie {settings.data.setup_timeframe} disponible pour {symbol}")
    step = interval(settings.data.setup_timeframe)
    frame = build_decision_frame(data["setup"], data["context"], data["btc"],
                                 setup_timeframe=settings.data.setup_timeframe,
                                 params=SetupFeatureParams(gap_block_bars=settings.data.gap_block_bars),
                                 regimes=settings.regimes)
    last = frame.iloc[-1]
    age = moment - last["available_at"]
    fresh = timedelta(0) <= age <= settings.data.max_staleness_bars * step
    context_fresh = _fresh_context(last, "ctx_")
    trend = _text(last.get("ctx_trend")) if context_fresh else "UNKNOWN"
    volatility = _text(last.get("ctx_volatility")) if context_fresh else "UNKNOWN"
    costs = settings.costs["central"]
    protocol = settings.protocol
    end = pd.Timestamp(development_end(settings))
    history_end = f"{end:%Y-%m-%d}"
    times = frame["decision_time"].dt.tz_convert(None).to_numpy()
    decision = frame["decision_time"]
    usable = ~frame["data_gap_recent"].to_numpy(bool)
    known_regime = ((frame["available_at"] - frame["ctx_available_at"]) <= MAX_CONTEXT_AGE).to_numpy()
    in_regime = (usable & known_regime & (frame["ctx_trend"].to_numpy() == trend)
                 & (frame["ctx_volatility"].to_numpy() == volatility))
    regime_known_now = trend != "UNKNOWN" and volatility != "UNKNOWN"
    bar_minutes = int(step.total_seconds() // 60)
    close = float(last["close"])

    def rate(values: np.ndarray, sample_times: np.ndarray, days: int, seed_offset: int = 0) -> dict:
        ci, blocks = block_interval(values, sample_times, block_days=days, samples=protocol.bootstrap_samples,
                                    seed=protocol.seed + seed_offset)
        return {"value": round(float(np.mean(values)), 6) if len(values) else None, "ci": ci, "blocks": blocks}

    # 1. Vue d'ensemble : fréquences historiques à chaque horizon, dans le régime courant (et en général).
    overview = []
    for key, (bars, label) in HORIZONS.items():
        gross, net = forward_returns(frame, bars, costs, step)
        historical = (decision + bars * step <= end).to_numpy()
        known = np.isfinite(gross) & usable & historical
        days = block_days_for(bars, bar_minutes)
        mask, conditioned = known & in_regime, regime_known_now
        p_up = rate(gross[mask] > 0, times[mask], days) if conditioned else None
        if not conditioned or p_up is None or p_up["ci"] is None:
            mask, conditioned = known, False
            p_up = rate(gross[mask] > 0, times[mask], days)
        sample_gross = gross[mask]
        overview.append({
            "horizon": key, "label": label, "samples": int(mask.sum()), "regime_conditioned": conditioned,
            "p_up": p_up, "p_net_positive": rate(net[mask] > 0, times[mask], days),
            "mean_net": rate(net[mask], times[mask], days),
            "p_up_all_moments": rate(gross[known] > 0, times[known], days),
            "median_gross": _number(np.median(sample_gross)) if len(sample_gross) else None,
            "q10_gross": _number(np.quantile(sample_gross, 0.10)) if len(sample_gross) else None,
            "q90_gross": _number(np.quantile(sample_gross, 0.90)) if len(sample_gross) else None,
            "block_days": days,
        })

    # 2. Plan indicatif pour l'horizon choisi, rejoué sur les moments comparables du passé.
    bars, label = HORIZONS[horizon]
    stride = max(1, bars // MAX_PLAN_SAMPLES_PER_WINDOW)
    historical = (decision + bars * step <= end).to_numpy()
    candidates = np.flatnonzero(usable & historical)
    candidates = candidates[candidates % stride == 0]
    outcomes = plan_outcomes(frame, candidates, bars, costs, step)
    days = block_days_for(bars, bar_minutes)
    all_times = times[outcomes.rows]
    baseline = rate(outcomes.r, all_times, days, 1)
    regime_rows = in_regime[outcomes.rows]
    expectancy = rate(outcomes.r[regime_rows], all_times[regime_rows], days, 2) if regime_known_now else None
    conditioned = expectancy is not None and expectancy["ci"] is not None
    pick = regime_rows if conditioned else np.ones(len(outcomes.rows), dtype=bool)
    if not conditioned:
        expectancy = baseline
    assert expectancy is not None
    sample_r, sample_net, sample_outcome = outcomes.r[pick], outcomes.net[pick], outcomes.outcome[pick]
    excess = (rate(sample_r - (baseline["value"] or 0.0), all_times[pick], days, 3)
              if conditioned else {"value": 0.0, "ci": None, "blocks": expectancy["blocks"]})
    years = pd.Series(sample_r).groupby(pd.to_datetime(all_times[pick]).year).sum() if len(sample_r) else pd.Series()
    total = float(years.sum()) if len(years) else 0.0
    year_share = round(float(years.max() / total), 4) if total > 0 else None

    plan: dict = {"horizon": horizon, "label": label, "samples": int(pick.sum()), "sampling_stride_bars": stride,
                  "regime_conditioned": bool(conditioned), "block_days": days, "blocks": expectancy["blocks"],
                  "history_end": history_end}
    sigma_now = float(last["realized_vol_96"]) * math.sqrt(bars)
    if bool(last["data_gap_recent"]):
        plan["unavailable"] = "trou de données récent : volatilité et indicateurs non fiables"
    elif not (math.isfinite(sigma_now) and sigma_now > 0):
        plan["unavailable"] = "volatilité récente inconnue (historique trop court)"
    else:
        tick = tick_size_for(settings, symbol)
        entry = _round_tick(close, tick, ROUND_FLOOR)
        stop = _round_tick(close * (1 - STOP_SIGMA * sigma_now), tick, ROUND_FLOOR)
        target = _round_tick(close * (1 + TARGET_SIGMA * sigma_now), tick, ROUND_CEILING)
        if not stop < entry < target or stop <= 0:
            plan["unavailable"] = "niveaux trop proches du pas de cotation"
        else:
            risk = (entry - stop) / entry
            round_trip = Decimal(2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps)) / Decimal(10_000)
            plan |= {"entry_reference": str(entry), "stop": str(stop), "target": str(target),
                     "stop_pct": round(float((stop / entry - 1) * 100), 3),
                     "target_pct": round(float((target / entry - 1) * 100), 3),
                     "rr_gross": round(float((target - entry) / (entry - stop)), 3),
                     "costs_in_r": round(float(round_trip / risk), 3)}
            plan["copy_text"] = plan_copy_text(symbol, label, entry, target, stop)
    if len(sample_outcome):
        plan |= {"tp_first": round(float((sample_outcome == TP).mean()), 4),
                 "sl_first": round(float((sample_outcome == SL).mean()), 4),
                 "timeout": round(float((sample_outcome == TIMEOUT).mean()), 4),
                 "expectancy_r": expectancy["value"], "expectancy_r_ci": expectancy["ci"],
                 "baseline_expectancy_r": baseline["value"], "baseline_expectancy_r_ci": baseline["ci"],
                 "excess_r": excess["value"], "excess_r_ci": excess["ci"],
                 "mean_net_pct": round(float(np.mean(sample_net)) * 100, 3), "max_year_share": year_share}
    ci, excess_ci = expectancy["ci"], excess["ci"]
    if not fresh:
        state = STALE
    elif "unavailable" in plan or ci is None:
        state = INSUFFICIENT
    elif ci[1] <= 0:
        state = NEGATIVE
    elif (ci[0] > 0 and conditioned and excess_ci is not None and excess_ci[0] > 0
          and (year_share is None or year_share <= MAX_YEAR_SHARE)):
        state = POSITIVE
    else:
        state = NO_EDGE
    plan["state_history"] = state                      # état d'après l'historique (celui que le suivi enregistre)
    from .tracking import LIVE_PROVEN, live_status
    try:
        live = live_status(settings, horizon, state)
    except Exception:  # noqa: BLE001 - suivi illisible : l'état historique reste affiché
        live = None
    if live is not None:
        plan["live"] = live
        if state == POSITIVE and live["proven"] and fresh:
            state = LIVE_PROVEN                            # les plans de ce type ont gagné EN DIRECT (données jamais vues)
    plan["state"] = state

    btc_fresh = _fresh_context(last, "btc_")
    context = {
        "close": close, "decision_time": last["decision_time"].isoformat(),
        "data_age_minutes": int(age.total_seconds() // 60), "fresh": bool(fresh), "context_fresh": context_fresh,
        "trend_1h": trend, "volatility_1h": volatility,
        "liquidity_1h": _text(last.get("ctx_liquidity")) if context_fresh else "UNKNOWN",
        "transition_1h": _text(last.get("ctx_transition")) if context_fresh else "UNKNOWN",
        "ret_1h_pct": _number(float(np.expm1(last["ret_4"])) * 100, 3) if pd.notna(last["ret_4"]) else None,
        "ret_24h_pct": _number(float(last["ctx_ret_24h"]) * 100, 3) if context_fresh else None,
        "btc_ret_24h_pct": _number(float(last["btc_ret_24h"]) * 100, 3) if btc_fresh else None,
        "atr_pct": _number(float(last["atr14"]) / close * 100, 3),
        "rsi14": _number(last["rsi14"], 1),
        "dist_ema50_pct": _number((close / float(last["ema50"]) - 1) * 100, 3),
        "realized_vol_24h_pct": _number(float(last["realized_vol_96"]) * math.sqrt(96) * 100, 3),
        "data_gap_recent": bool(last["data_gap_recent"]),
        "bars_available": int(last["bars_available"]),
    }
    # Courbe des 7 derniers jours (clôtures horaires, données déjà disponibles) pour l'affichage.
    recent = frame[frame["decision_time"] > last["decision_time"] - pd.Timedelta(days=7)]
    hourly = recent[(recent["decision_time"].dt.minute == 0)]
    spark = {"times": [t.isoformat() for t in hourly["decision_time"]],
             "closes": [round(float(v), 10) for v in hourly["close"]]}
    return {"symbol": symbol, "horizon": horizon, "horizon_label": label, "evaluated_at": moment.isoformat(),
            "spark_7d": spark, "history_end": history_end, "ci_level": round(CI_LEVEL, 4), "min_blocks": MIN_BLOCKS,
            "context": context, "overview": overview, "plan": plan, "definitions": definitions(history_end),
            "costs_round_trip_pct": round(2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps) / 100, 3),
            "warning": f"Statistiques en échantillon (historique jusqu'au {history_end}), non validées par le "
                       "protocole ; consulter plusieurs horizons et plusieurs paires multiplie les essais (intervalles "
                       "corrigés pour les 6 horizons seulement) ; univers choisi aujourd'hui (paires toujours cotées : "
                       "biais du survivant). CSI ne propose jamais d'entrer sur cette base : aucune stratégie n'a "
                       "démontré d'avantage exploitable."}
