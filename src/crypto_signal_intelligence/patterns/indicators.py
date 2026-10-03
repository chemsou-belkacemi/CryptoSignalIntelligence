"""Indicateurs classiques, flux et ICT/SMC avancé (docs/INDICATEURS.md § 10.4 à 10.6, écrits avant le code).

Bibliothèque seulement : aucun test en cours ne les utilise. Fonctions pures sur des bougies clôturées ; la valeur à
l'indice i ne lit aucune bougie d'indice supérieur (NaN tant qu'elle n'est pas définie)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .primitives import atr, fractal_pivots
from .smc import OrderBlock


def _arr(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def sma(values, n: int) -> np.ndarray:
    v = _arr(values)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        sums = np.convolve(v, np.ones(n), mode="valid")
        out[n - 1:] = sums / n
    return out


def ema(values, n: int) -> np.ndarray:
    """EMA (α = 2/(n+1)) partant de la moyenne simple des n premières valeurs finies consécutives."""
    v = _arr(values)
    out = np.full(len(v), np.nan)
    finite = np.isfinite(v)
    start = next((i for i in range(len(v) - n + 1) if finite[i:i + n].all()), None)
    if start is None:
        return out
    alpha = 2 / (n + 1)
    out[start + n - 1] = v[start:start + n].mean()
    for i in range(start + n, len(v)):
        out[i] = alpha * v[i] + (1 - alpha) * out[i - 1]
    return out


def rolling_max(values, n: int) -> np.ndarray:
    v = _arr(values)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(v, n).max(axis=1)
    return out


def rolling_min(values, n: int) -> np.ndarray:
    v = _arr(values)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(v, n).min(axis=1)
    return out


# --- § 10.4 -------------------------------------------------------------------------------------------------------

def rsi(close, n: int = 14) -> np.ndarray:
    """RSI de Wilder ; première valeur à l'indice n."""
    c = _arr(close)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    diff = np.diff(c)
    gains, losses = np.clip(diff, 0, None), np.clip(-diff, 0, None)
    g, p = gains[:n].mean(), losses[:n].mean()
    for i in range(n, len(c)):
        if i > n:
            g = (g * (n - 1) + gains[i - 1]) / n
            p = (p * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if p == 0 else 100 - 100 / (1 + g / p)
    return out


@dataclass(frozen=True)
class Divergence:
    side: str               # "bull" | "bear"
    first: int
    second: int
    known_at: int


DIV_MIN_BARS, DIV_MAX_BARS = 5, 60


def rsi_divergences(high, low, close, n: int = 14) -> list[Divergence]:
    """Divergences régulières sur deux pivots fractals consécutifs de même type (5 à 60 bougies d'écart)."""
    h, lo = _arr(high), _arr(low)
    r = rsi(close, n)
    pivots = fractal_pivots(h, lo)
    out = []
    for kind, side in (("low", "bull"), ("high", "bear")):
        same = [p for p in pivots if p.kind == kind]
        for a, b in zip(same, same[1:], strict=False):
            gap = b.index - a.index
            if not DIV_MIN_BARS <= gap <= DIV_MAX_BARS or not (np.isfinite(r[a.index]) and np.isfinite(r[b.index])):
                continue
            if side == "bull" and b.price < a.price and r[b.index] > r[a.index]:
                out.append(Divergence("bull", a.index, b.index, b.known_at))
            if side == "bear" and b.price > a.price and r[b.index] < r[a.index]:
                out.append(Divergence("bear", a.index, b.index, b.known_at))
    return sorted(out, key=lambda d: (d.known_at, d.side))


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def stochastic(high, low, close, n: int = 14, smooth: int = 3, d: int = 3) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = rolling_min(low, n), rolling_max(high, n)
    span = hi - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(span > 0, 100 * (_arr(close) - lo) / np.where(span > 0, span, 1), 50.0)
    raw[~np.isfinite(span)] = np.nan
    k = sma(raw, smooth)
    return k, sma(k, d)


def cci(high, low, close, n: int = 20) -> np.ndarray:
    tp = (_arr(high) + _arr(low) + _arr(close)) / 3
    mean = sma(tp, n)
    out = np.full(len(tp), np.nan)
    for i in range(n - 1, len(tp)):
        window = tp[i - n + 1:i + 1]
        dev = np.abs(window - mean[i]).mean()
        out[i] = (tp[i] - mean[i]) / (0.015 * dev) if dev > 0 else 0.0
    return out


def bollinger(close, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c = _arr(close)
    mid = sma(c, n)
    std = np.full(len(c), np.nan)
    if len(c) >= n:
        std[n - 1:] = np.lib.stride_tricks.sliding_window_view(c, n).std(axis=1)
    return mid - k * std, mid, mid + k * std


def keltner(high, low, close, n: int = 20, atr_n: int = 10, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = ema(close, n)
    a = atr(high, low, close, atr_n)
    return mid - k * a, mid, mid + k * a


def ichimoku(high, low, close, tenkan_n: int = 9, kijun_n: int = 26, senkou_n: int = 52, shift: int = 26) -> dict:
    """Nuage APPLICABLE à chaque indice (calculé `shift` bougies plus tôt) ; ligne retardée sous forme causale."""
    h, lo, c = _arr(high), _arr(low), _arr(close)
    tenkan = (rolling_max(h, tenkan_n) + rolling_min(lo, tenkan_n)) / 2
    kijun = (rolling_max(h, kijun_n) + rolling_min(lo, kijun_n)) / 2
    a_raw = (tenkan + kijun) / 2
    b_raw = (rolling_max(h, senkou_n) + rolling_min(lo, senkou_n)) / 2
    span_a, span_b, lag = (np.full(len(c), np.nan) for _ in range(3))
    span_a[shift:], span_b[shift:] = a_raw[:-shift], b_raw[:-shift]
    lag[shift:] = c[shift:] - c[:-shift]
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": span_a, "senkou_b": span_b, "chikou_diff": lag}


def supertrend(high, low, close, n: int = 10, k: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """Ligne et sens (+1 haussier, −1 baissier) ; NaN avant l'ATR."""
    h, lo, c = _arr(high), _arr(low), _arr(close)
    a = atr(h, lo, c, n)
    mid = (h + lo) / 2
    upper, lower = mid + k * a, mid - k * a
    line, trend = np.full(len(c), np.nan), np.full(len(c), np.nan)
    fu = fl = np.nan
    direction = 1
    for i in range(len(c)):
        if not np.isfinite(a[i]):
            continue
        if np.isnan(fu):
            fu, fl, direction = upper[i], lower[i], 1 if c[i] >= mid[i] else -1
        else:
            fu = upper[i] if (upper[i] < fu or c[i - 1] > fu) else fu
            fl = lower[i] if (lower[i] > fl or c[i - 1] < fl) else fl
            if direction == 1 and c[i] < fl:
                direction = -1
            elif direction == -1 and c[i] > fu:
                direction = 1
        line[i] = fl if direction == 1 else fu
        trend[i] = direction
    return line, trend


def pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    p = (prev_high + prev_low + prev_close) / 3
    rng = prev_high - prev_low
    return {"P": p, "R1": 2 * p - prev_low, "S1": 2 * p - prev_high, "R2": p + rng, "S2": p - rng,
            "R3": prev_high + 2 * (p - prev_low), "S3": prev_low - 2 * (prev_high - p)}


# --- § 10.5 -------------------------------------------------------------------------------------------------------

BIG_MULTIPLE, BIG_LOOKBACK = 5.0, 20


def taker_ratio(quote_volume, taker_buy_quote) -> np.ndarray:
    qv, tb = _arr(quote_volume), _arr(taker_buy_quote)
    sell = qv - tb
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sell > 0, tb / np.where(sell > 0, sell, 1), np.nan)


def big_activity(quote_volume, multiple: float = BIG_MULTIPLE, lookback: int = BIG_LOOKBACK) -> np.ndarray:
    """Vrai si le volume de la bougie dépasse `multiple` fois la moyenne des `lookback` bougies précédentes."""
    qv = _arr(quote_volume)
    out = np.zeros(len(qv), dtype=bool)
    previous = sma(qv, lookback)
    out[lookback:] = qv[lookback:] > multiple * previous[lookback - 1:-1]
    return out


# --- § 10.6 -------------------------------------------------------------------------------------------------------

EQUAL_ATR, EQUAL_MIN_BARS = 0.1, 3


@dataclass(frozen=True)
class EqualLevel:
    side: str               # "high" (liquidité au-dessus) | "low"
    first: int
    second: int
    level: float
    known_at: int
    taken_at: int | None


def equal_levels(high, low, close) -> list[EqualLevel]:
    h, lo = _arr(high), _arr(low)
    a = atr(h, lo, close)
    out = []
    for kind in ("high", "low"):
        same = [p for p in fractal_pivots(h, lo) if p.kind == kind]
        for p1, p2 in zip(same, same[1:], strict=False):
            if p2.index - p1.index < EQUAL_MIN_BARS or not np.isfinite(a[p2.index]):
                continue
            if abs(p2.price - p1.price) > EQUAL_ATR * a[p2.index]:
                continue
            level = max(p1.price, p2.price) if kind == "high" else min(p1.price, p2.price)
            later = (np.nonzero(h[p2.known_at + 1:] > level)[0] if kind == "high"
                     else np.nonzero(lo[p2.known_at + 1:] < level)[0])
            taken = int(p2.known_at + 1 + later[0]) if len(later) else None
            out.append(EqualLevel(kind, p1.index, p2.index, level, p2.known_at, taken))
    return sorted(out, key=lambda e: (e.known_at, e.side))


@dataclass(frozen=True)
class BlockEvent:
    block: OrderBlock
    kind: str               # "BREAKER" | "MITIGATION"
    side: str               # sens du breaker (opposé à l'OB) ou de l'OB mitigé
    at: int                 # bougie à la clôture de laquelle l'événement est connu


def block_events(blocks: list[OrderBlock], high, low, close) -> list[BlockEvent]:
    """Pour chaque OB : invalidation (breaker) à la première clôture au-delà de sa zone, et mitigation à la première
    bougie qui revient dans sa zone avant toute invalidation."""
    h, lo, c = _arr(high), _arr(low), _arr(close)
    out = []
    for b in blocks:
        start = b.known_at + 1
        broken = (np.nonzero(c[start:] < b.bottom)[0] if b.side == "bull" else np.nonzero(c[start:] > b.top)[0])
        broken_at = int(start + broken[0]) if len(broken) else None
        touched = (np.nonzero(lo[start:] <= b.top)[0] if b.side == "bull" else np.nonzero(h[start:] >= b.bottom)[0])
        touched_at = int(start + touched[0]) if len(touched) else None
        if touched_at is not None and (broken_at is None or touched_at <= broken_at):
            out.append(BlockEvent(b, "MITIGATION", b.side, touched_at))
        if broken_at is not None:
            out.append(BlockEvent(b, "BREAKER", "bear" if b.side == "bull" else "bull", broken_at))
    return sorted(out, key=lambda e: (e.at, e.kind))
