"""Concepts ICT/SMC mécaniques (docs/INDICATEURS.md § 2 à 6) : FVG, order blocks, liquidity sweeps, structure
(BOS, CHoCH, MSS), premium / discount."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .primitives import Pivot, fractal_pivots

FVG_MIN_ATR = 0.5
OB_DISPLACEMENT_ATR = 2.0
OB_WINDOW = 3
SWEEP_LOOKBACK = 20


@dataclass(frozen=True)
class Gap:
    index: int              # bougie i (troisième des trois)
    side: str               # "bull" | "bear"
    bottom: float
    top: float
    known_at: int
    filled_at: int | None   # première bougie qui revisite toute la zone


def fair_value_gaps(high, low, atr_values, *, min_atr: float = FVG_MIN_ATR) -> list[Gap]:
    high, low, atr_values = (np.asarray(a, dtype=float) for a in (high, low, atr_values))
    out = []
    for i in range(2, len(high)):
        ref = atr_values[i - 1]
        if not np.isfinite(ref):
            continue
        if low[i] > high[i - 2] and low[i] - high[i - 2] >= min_atr * ref:
            bottom, top = float(high[i - 2]), float(low[i])
            later = np.nonzero(low[i + 1:] <= bottom)[0]
            out.append(Gap(i, "bull", bottom, top, i, int(i + 1 + later[0]) if len(later) else None))
        if high[i] < low[i - 2] and low[i - 2] - high[i] >= min_atr * ref:
            bottom, top = float(high[i]), float(low[i - 2])
            later = np.nonzero(high[i + 1:] >= top)[0]
            out.append(Gap(i, "bear", bottom, top, i, int(i + 1 + later[0]) if len(later) else None))
    return out


def filled_by(gap: Gap, at: int) -> bool:
    """Comblé à la clôture de `at` (jamais d'information postérieure)."""
    return gap.filled_at is not None and gap.filled_at <= at


@dataclass(frozen=True)
class OrderBlock:
    index: int              # bougie d'origine k
    side: str
    bottom: float
    top: float
    known_at: int


def order_blocks(open_, high, low, close, atr_values, *, x: float = OB_DISPLACEMENT_ATR,
                 window: int = OB_WINDOW) -> list[OrderBlock]:
    o, h, lo, c, a = (np.asarray(v, dtype=float) for v in (open_, high, low, close, atr_values))
    out = []
    for k in range(len(c)):
        if not np.isfinite(a[k]):
            continue
        bearish, bullish = c[k] < o[k], c[k] > o[k]
        for j in range(k + 1, min(len(c), k + 1 + window)):
            closes = c[k + 1:j + 1]
            if bearish and (closes > h[k]).any() and closes.max() >= c[k] + x * a[k]:
                if not _later_same_color(o, c, k, j, bearish=True):
                    out.append(OrderBlock(k, "bull", float(lo[k]), float(h[k]), j))
                break
            if bullish and (closes < lo[k]).any() and closes.min() <= c[k] - x * a[k]:
                if not _later_same_color(o, c, k, j, bearish=False):
                    out.append(OrderBlock(k, "bear", float(lo[k]), float(h[k]), j))
                break
    return out


def _later_same_color(o, c, k: int, j: int, *, bearish: bool) -> bool:
    """« Dernière » bougie de sens opposé : aucune autre bougie de même couleur entre k et le déplacement."""
    seg_o, seg_c = o[k + 1:j + 1], c[k + 1:j + 1]
    return bool(((seg_c < seg_o) if bearish else (seg_c > seg_o)).any())


@dataclass(frozen=True)
class Sweep:
    index: int
    side: str               # "bull" (balayage des plus bas) | "bear"
    level: float
    known_at: int


def liquidity_sweeps(high, low, close, *, lookback: int = SWEEP_LOOKBACK) -> list[Sweep]:
    h, lo, c = (np.asarray(v, dtype=float) for v in (high, low, close))
    out = []
    for i in range(lookback, len(c)):
        floor, ceiling = lo[i - lookback:i].min(), h[i - lookback:i].max()
        if lo[i] < floor and c[i] > floor:
            out.append(Sweep(i, "bull", float(floor), i))
        if h[i] > ceiling and c[i] < ceiling:
            out.append(Sweep(i, "bear", float(ceiling), i))
    return out


@dataclass(frozen=True)
class Break:
    index: int              # bougie de cassure (known_at = index)
    side: str               # "bull" | "bear"
    kind: str               # "BOS" | "CHoCH"
    level: float
    pivot_index: int
    mss: bool               # CHoCH avec un FVG du même sens entre le pivot et la cassure
    known_at: int


def structure(high, low, close, atr_values) -> list[Break]:
    h, lo, c = (np.asarray(v, dtype=float) for v in (high, low, close))
    pivots = fractal_pivots(h, lo)
    gaps = fair_value_gaps(h, lo, atr_values)
    out: list[Break] = []
    trend = 0
    used: set[int] = set()
    for i in range(len(c)):
        known_pivots = [p for p in pivots if p.known_at < i]
        last_high = next((p for p in reversed(known_pivots) if p.kind == "high"), None)
        last_low = next((p for p in reversed(known_pivots) if p.kind == "low"), None)
        for pivot, side in ((last_high, "bull"), (last_low, "bear")):
            if pivot is None or pivot.index in used:
                continue
            broke = c[i] > pivot.price if side == "bull" else c[i] < pivot.price
            if not broke:
                continue
            used.add(pivot.index)
            direction = 1 if side == "bull" else -1
            kind = "BOS" if trend == direction else "CHoCH"
            mss = kind == "CHoCH" and any(g.side == side and pivot.index < g.index <= i for g in gaps)
            out.append(Break(i, side, kind, pivot.price, pivot.index, mss, i))
            trend = direction
    return out


def premium_discount(price: float, swing_low: float, swing_high: float) -> dict:
    """Position du prix dans le dernier swing : premium au-dessus du milieu, discount en dessous ; zone OTE à 62-79 %
    de retracement (mesurée depuis le haut pour un swing haussier)."""
    middle = (swing_low + swing_high) / 2
    span = swing_high - swing_low
    return {"middle": middle, "zone": "premium" if price > middle else ("discount" if price < middle else "equilibre"),
            "ote": (swing_high - 0.79 * span, swing_high - 0.62 * span)}


def last_swing(pivots: list[Pivot], at: int) -> tuple[float, float] | None:
    """(bas, haut) du dernier swing terminé parmi les pivots connus à `at`."""
    seen = [p for p in pivots if p.known_at <= at]
    if len(seen) < 2:
        return None
    a, b = seen[-2], seen[-1]
    return (min(a.price, b.price), max(a.price, b.price))
