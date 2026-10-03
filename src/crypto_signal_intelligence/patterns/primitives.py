"""ATR de Wilder, pivots fractals et ZigZag en ATR (docs/INDICATEURS.md § conventions et § 1)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ATR_PERIOD = 14
FRACTAL_K = 2
ZIGZAG_M = {"1h": 3.0, "4h": 2.5, "1d": 2.0}


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    high, low, close = (np.asarray(a, dtype=float) for a in (high, low, close))
    tr = high - low
    if len(tr) > 1:
        prev = close[:-1]
        tr[1:] = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - prev), np.abs(low[1:] - prev)])
    return tr


def atr(high, low, close, period: int = ATR_PERIOD) -> np.ndarray:
    """ATR de Wilder ; NaN avant l'indice period − 1."""
    tr = true_range(high, low, close)
    out = np.full(len(tr), np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


@dataclass(frozen=True)
class Pivot:
    index: int          # bougie du pivot
    kind: str           # "high" | "low"
    price: float
    known_at: int       # bougie à la clôture de laquelle il est confirmé


def fractal_pivots(high, low, k: int = FRACTAL_K) -> list[Pivot]:
    """Pivots fractals : extrême strict sur k bougies de chaque côté ; connus à j + k."""
    high, low = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    out = []
    for j in range(k, len(high) - k):
        around = np.r_[np.arange(j - k, j), np.arange(j + 1, j + k + 1)]
        if (high[j] > high[around]).all():
            out.append(Pivot(j, "high", float(high[j]), j + k))
        if (low[j] < low[around]).all():
            out.append(Pivot(j, "low", float(low[j]), j + k))
    return out


def zigzag(high, low, atr_values, m: float) -> list[Pivot]:
    """ZigZag : retournement confirmé quand le prix s'éloigne de l'extrême courant d'au moins m × ATR (ATR de la
    bougie de l'extrême). Le premier sens est donné par le premier mouvement de m × ATR depuis le début."""
    high, low, atr_values = (np.asarray(a, dtype=float) for a in (high, low, atr_values))
    out: list[Pivot] = []
    start = next((i for i in range(len(high)) if np.isfinite(atr_values[i])), None)
    if start is None:
        return out
    hi_idx = lo_idx = start
    direction = 0                                        # 0 : inconnu, 1 : montée, −1 : descente
    for i in range(start + 1, len(high)):
        if direction >= 0 and high[i] > high[hi_idx]:
            hi_idx = i
        if direction <= 0 and low[i] < low[lo_idx]:
            lo_idx = i
        if direction == 0:
            if np.isfinite(atr_values[lo_idx]) and high[i] >= low[lo_idx] + m * atr_values[lo_idx] and lo_idx < i:
                out.append(Pivot(lo_idx, "low", float(low[lo_idx]), i))
                direction, hi_idx = 1, i
            elif np.isfinite(atr_values[hi_idx]) and low[i] <= high[hi_idx] - m * atr_values[hi_idx] and hi_idx < i:
                out.append(Pivot(hi_idx, "high", float(high[hi_idx]), i))
                direction, lo_idx = -1, i
            continue
        if direction == 1 and low[i] <= high[hi_idx] - m * atr_values[hi_idx] and hi_idx < i:
            out.append(Pivot(hi_idx, "high", float(high[hi_idx]), i))
            direction, lo_idx = -1, i
        elif direction == -1 and high[i] >= low[lo_idx] + m * atr_values[lo_idx] and lo_idx < i:
            out.append(Pivot(lo_idx, "low", float(low[lo_idx]), i))
            direction, hi_idx = 1, i
    return out


def known(items: list, at: int) -> list:
    """Éléments connus à la clôture de la bougie `at`."""
    return [item for item in items if item.known_at <= at]
