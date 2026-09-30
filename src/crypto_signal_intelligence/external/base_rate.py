"""Taux de base historique d'une géométrie de trade sur une paire (ordres « aveugles »).

Méthode LIMIT_ALIGNED (version 2, 2026-09-30) : on rejoue, à chaque bougie de décision i du passé,
EXACTEMENT l'ordre que la résolution d'un signal réel simule (external/registry.py, `replay`) :
- limite = close(i) × (1 + écart), où l'écart est celui du signal par rapport au dernier prix
  (un ordre au-dessus du marché s'exécute à l'ouverture suivante, comme au marché) ;
- stop = limite − stop_atr × ATR14(i) ; cible = limite + target_r × (limite − stop) ;
- ordre valable `entry_window` bougies : rempli à l'ouverture si elle est sous la limite (plafonné
  à la limite), sinon au prix limite seulement si le plus bas passe STRICTEMENT dessous ;
- sortie : stop ou cible, conventions du simulateur (ouverture sous le stop = gap exécuté à
  l'ouverture ; stop et cible dans la même bougie = stop, compté ambigu ; bougie de remplissage
  « au contact » : pas de cible, le plus haut a pu précéder l'entrée), sinon TIMEOUT au close après
  `horizon` bougies ; ordres non remplis = UNFILLED (0 R, hors statistiques de trades) ;
- R net rapporté au risque prévu (limite − stop), frais et glissement compris.

Ce taux mesure ce que la géométrie, le type d'ordre et le régime donnent SANS aucune sélection. Ce
n'est pas la probabilité qu'un signal précis réussisse : l'apport d'une source se lit dans l'écart
entre ses résultats réels, résolus avec les mêmes règles, et ce taux. Les entrées successives se
chevauchent : l'intervalle vient d'un bootstrap par blocs de jours calendaires consécutifs, sur le
MÊME échantillon que la moyenne affichée.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import CostScenario

TP, SL, TIMEOUT = 1, -1, 0
METHOD = "LIMIT_ALIGNED_V2"
MIN_BLOCKS = 10


@dataclass(frozen=True)
class BaseRate:
    samples: int                       # ordres remplis et résolus (base des statistiques de trade)
    tp_first: float
    sl_first: float
    timeout: float
    ambiguous: float
    expectancy_r: float                # par ordre REMPLI
    expectancy_r_ci95: tuple[float, float] | None
    horizon_bars: int
    regime_conditioned: bool
    regime: str
    emitted: int = 0                   # ordres simulés (remplis + non remplis)
    fill_rate: float = 0.0
    tp_first_ci95: tuple[float, float] | None = None
    entry_window_bars: int = 0
    entry_offset_pct: float = 0.0
    block_days: int = 0
    blocks: int = 0
    method: str = METHOD

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BlindOutcomes:
    outcome: np.ndarray                # TP / SL / TIMEOUT pour les ordres remplis et résolus
    r: np.ndarray
    ambiguous: np.ndarray
    times: np.ndarray                  # instant de décision de chaque ordre rempli et résolu
    emitted: int
    filled: int


def blind_limit_outcomes(frame: pd.DataFrame, *, entry_offset: float, stop_atr: float, target_r: float,
                         entry_window: int, horizon: int, costs: CostScenario,
                         mask: np.ndarray | None = None) -> BlindOutcomes:
    """Ordres limites aveugles, résolus comme `replay` (voir la docstring du module)."""
    opens, highs, lows, closes = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = frame["atr14"].to_numpy(float)
    decision = frame["decision_time"].to_numpy()
    n = len(frame)
    fee = costs.fee_bps / 1e4
    market_cost = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    eligible = np.isfinite(atr) & (atr > 0) & np.isfinite(closes)
    if n:
        eligible[-1] = False                               # pas de bougie suivante pour placer l'ordre
    if mask is not None:
        eligible &= np.asarray(mask, dtype=bool)
    idx = np.flatnonzero(eligible)
    limit = closes[idx] * (1 + entry_offset)
    stop = limit - stop_atr * atr[idx]
    keep = stop > 0
    idx, limit, stop = idx[keep], limit[keep], stop[keep]
    risk = limit - stop
    target = limit + target_r * risk
    count = len(idx)

    # 1. Remplissage : première bougie i+k (k = 1..entry_window) qui exécute la limite.
    fill_bar = np.full(count, -1, dtype=np.int64)
    fill_price = np.full(count, np.nan)
    touched = np.zeros(count, dtype=bool)
    pending = np.arange(count)
    window_complete = np.zeros(count, dtype=bool)
    for k in range(1, entry_window + 1):
        if not len(pending):
            break
        j = idx[pending] + k
        inside = j < n
        pending, j = pending[inside], j[inside]
        if not len(pending):
            break
        at_open = opens[j] <= limit[pending]
        on_touch = ~at_open & (lows[j] < limit[pending])
        filled_now = at_open | on_touch
        hit = pending[filled_now]
        fill_bar[hit] = j[filled_now]
        fill_price[hit] = np.where(at_open[filled_now], np.minimum(opens[j[filled_now]] * (1 + market_cost),
                                                                   limit[hit]), limit[hit])
        touched[hit] = on_touch[filled_now]
        pending = pending[~filled_now]
    last_window_bar = idx + entry_window
    window_complete = last_window_bar < n                     # sinon : peut-être rempli plus tard (censuré)
    filled = fill_bar >= 0
    emitted_mask = filled | window_complete                   # UNFILLED seulement si la fenêtre est finie
    emitted = int(emitted_mask.sum())

    # 2. Sortie depuis la bougie de remplissage, comme `replay`.
    outcome = np.full(count, np.nan)
    exit_price = np.full(count, np.nan)
    ambiguous = np.zeros(count, dtype=bool)
    gap_fill = filled & (fill_price <= stop)                  # rempli sous le stop : stop-market aussitôt
    outcome[gap_fill] = SL
    exit_price[gap_fill] = fill_price[gap_fill] * (1 - market_cost)
    pending = np.flatnonzero(filled & ~gap_fill)
    for step in range(horizon):
        if not len(pending):
            break
        j = fill_bar[pending] + step
        inside = j < n
        pending, j = pending[inside], j[inside]
        if not len(pending):
            break
        first = step == 0
        s, t = stop[pending], target[pending]
        gap = (not first) & (opens[j] <= s)
        open_tp = (not first) & ~gap & (opens[j] > t)
        sl_hit = ~gap & ~open_tp & (lows[j] <= s)
        tp_hit = ~gap & ~open_tp & ~sl_hit & (highs[j] > t) & ~(first & touched[pending])
        done = gap | open_tp | sl_hit | tp_hit
        price = np.select([gap, open_tp, sl_hit, tp_hit],
                          [opens[j] * (1 - market_cost), t, s * (1 - market_cost), t], default=np.nan)
        result = np.select([gap | sl_hit, open_tp | tp_hit], [SL, TP], default=0)
        resolved = pending[done]
        outcome[resolved], exit_price[resolved] = result[done], price[done]
        ambiguous[resolved] = (sl_hit & (highs[j] > t))[done]
        pending = pending[~done]
    last = fill_bar[pending] + horizon - 1
    inside = last < n
    timed_out = pending[inside]
    outcome[timed_out] = TIMEOUT
    exit_price[timed_out] = closes[last[inside]] * (1 - market_cost)

    known = ~np.isnan(outcome)
    r = (exit_price[known] * (1 - fee) - fill_price[known] * (1 + fee)) / risk[known]
    return BlindOutcomes(outcome=outcome[known], r=r, ambiguous=ambiguous[known], times=decision[idx][known],
                         emitted=emitted, filled=int(filled.sum()))


def day_block_ci95(values: np.ndarray, times: np.ndarray, *, block_days: int, samples: int,
                   seed: int) -> tuple[tuple[float, float] | None, int]:
    """IC95 de la moyenne (pondérée par entrée, donc la MÊME que celle affichée) : on tire, avec remise,
    des blocs de `block_days` jours calendaires consécutifs (jours présents dans l'échantillon)."""
    if len(values) == 0:
        return None, 0
    days = pd.to_datetime(times, utc=True).floor("D")
    frame = pd.DataFrame({"day": days, "v": values}).groupby("day")["v"].agg(["sum", "count"])
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    blocks = math.ceil(len(sums) / block_days)
    if blocks < MIN_BLOCKS:
        return None, blocks
    pad = blocks * block_days - len(sums)
    sums = np.concatenate([sums, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    counts = np.concatenate([counts, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, blocks, size=(samples, blocks))
    draws = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return (round(float(low), 4), round(float(high), 4)), blocks


def base_rate(frame: pd.DataFrame, *, stop_atr: float, target_r: float, horizon: int, costs: CostScenario,
              trend: str, volatility: str, min_samples: int, seed: int, bootstrap_samples: int = 2000,
              entry_offset: float = 0.0, entry_window: int = 1, bar_minutes: int = 15) -> BaseRate:
    """Taux de base dans le régime (tendance, volatilité) courant, s'il fournit assez d'ordres remplis
    ET un intervalle ; sinon sur tous les régimes (signalé)."""
    block_days = max(1, math.ceil(horizon * bar_minutes / 1440))

    def run(mask: np.ndarray | None) -> tuple[BlindOutcomes, tuple[float, float] | None, int]:
        blind = blind_limit_outcomes(frame, entry_offset=entry_offset, stop_atr=stop_atr, target_r=target_r,
                                     entry_window=entry_window, horizon=horizon, costs=costs, mask=mask)
        ci, blocks = day_block_ci95(blind.r, blind.times, block_days=block_days, samples=bootstrap_samples,
                                    seed=seed)
        return blind, ci, blocks

    conditioned = trend != "UNKNOWN" and volatility != "UNKNOWN"
    blind, ci, blocks = (None, None, 0)
    if conditioned:
        mask = (frame["ctx_trend"].to_numpy() == trend) & (frame["ctx_volatility"].to_numpy() == volatility)
        blind, ci, blocks = run(mask)
        if len(blind.r) < min_samples or ci is None:
            conditioned = False
    if not conditioned:
        blind, ci, blocks = run(None)
    assert blind is not None
    samples = len(blind.r)
    regime = f"{trend}/{volatility}" if conditioned else "tous régimes"
    fill_rate = round(blind.filled / blind.emitted, 4) if blind.emitted else 0.0
    if samples == 0:
        return BaseRate(0, 0.0, 0.0, 0.0, 0.0, 0.0, None, horizon, conditioned, regime, emitted=blind.emitted,
                        fill_rate=fill_rate, entry_window_bars=entry_window,
                        entry_offset_pct=round(entry_offset * 100, 4), block_days=block_days, blocks=blocks)
    tp_ci, _ = day_block_ci95((blind.outcome == TP).astype(float), blind.times, block_days=block_days,
                              samples=bootstrap_samples, seed=seed)
    return BaseRate(
        samples=int(samples), tp_first=round(float((blind.outcome == TP).mean()), 4),
        sl_first=round(float((blind.outcome == SL).mean()), 4),
        timeout=round(float((blind.outcome == TIMEOUT).mean()), 4),
        ambiguous=round(float(blind.ambiguous.mean()), 4), expectancy_r=round(float(blind.r.mean()), 4),
        expectancy_r_ci95=ci, horizon_bars=horizon, regime_conditioned=conditioned, regime=regime,
        emitted=blind.emitted, fill_rate=fill_rate, tp_first_ci95=tp_ci, entry_window_bars=entry_window,
        entry_offset_pct=round(entry_offset * 100, 4), block_days=block_days, blocks=blocks)
