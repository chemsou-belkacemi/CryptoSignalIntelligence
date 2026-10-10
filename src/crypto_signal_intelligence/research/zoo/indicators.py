"""Indicateurs du zoo : fonctions PURES sur tableaux numpy, causales (la valeur en `t` ne lit que `≤ t`), NaN tant
que la fenêtre n'est pas entièrement disponible (« un signal n'existe que si la fenêtre de l'indicateur est
entièrement disponible », docs/ZOO.md § 2).

Conventions déclarées (définitions de Wilder et de TA-Lib, testées à la main dans tests/test_zoo_indicators.py) :
- EMA de période n : α = 2 / (n + 1), amorcée par la moyenne simple des n premières valeurs (première valeur en
  n − 1) ; RMA (lissage de Wilder) : α = 1 / n, même amorçage ;
- vrai range : max(H − L, |H − C₋₁|, |L − C₋₁|), indéfini sur la première bougie ; ATR n = RMA du vrai range ;
- « plus haut des n bougies précédentes » : maximum des plus hauts de t − n à t − 1 (la bougie t exclue) ;
- croisement au-dessus : condition vraie en t et FAUSSE (définie) en t − 1.
"""
from __future__ import annotations

import numpy as np

from ._jit import njit


def _f(values) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def premier_valide(x: np.ndarray) -> int:
    ok = np.flatnonzero(~np.isnan(x))
    return int(ok[0]) if len(ok) else len(x)


def sma(values, n: int) -> np.ndarray:
    """Moyenne simple des n dernières valeurs ; les valeurs indéfinies de tête (chauffe d'un autre indicateur) sont
    sautées : première moyenne quand n valeurs définies sont disponibles."""
    x = _f(values)
    out = np.full(len(x), np.nan)
    start = premier_valide(x)
    if n <= 0 or len(x) - start < n:
        return out
    csum = np.cumsum(np.r_[0.0, x[start:]])
    out[start + n - 1:] = (csum[n:] - csum[:-n]) / n
    return out


@njit
def _lisse(x: np.ndarray, n: int, alpha: float) -> np.ndarray:
    out = np.full(len(x), np.nan)
    start = 0
    while start < len(x) and np.isnan(x[start]):
        start += 1
    if start + n > len(x):
        return out
    acc = 0.0
    for i in range(start, start + n):
        acc += x[i]
    value = acc / n
    out[start + n - 1] = value
    for i in range(start + n, len(x)):
        value = value + alpha * (x[i] - value)
        out[i] = value
    return out


def ema(values, n: int) -> np.ndarray:
    """EMA amorcée par la SMA des n premières valeurs définies (TA-Lib)."""
    return _lisse(_f(values), int(n), 2.0 / (n + 1.0))


def rma(values, n: int) -> np.ndarray:
    """Lissage de Wilder (α = 1/n), amorcé par la SMA des n premières valeurs définies."""
    return _lisse(_f(values), int(n), 1.0 / n)


def wma(values, n: int) -> np.ndarray:
    """Moyenne pondérée linéaire (poids 1 … n, le plus fort sur la valeur la plus récente)."""
    x = _f(values)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    weights = np.arange(1, n + 1, dtype=np.float64)
    windows = np.lib.stride_tricks.sliding_window_view(x, n)
    out[n - 1:] = windows @ weights / weights.sum()
    return out


def vrai_range(high, low, close) -> np.ndarray:
    h, lo, c = _f(high), _f(low), _f(close)
    out = np.full(len(c), np.nan)
    if len(c) > 1:
        prev = c[:-1]
        out[1:] = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - prev), np.abs(lo[1:] - prev)))
    return out


def atr(high, low, close, n: int) -> np.ndarray:
    return rma(vrai_range(high, low, close), n)


def max_glissant(values, n: int, *, inclure: bool = True) -> np.ndarray:
    """Maximum des n dernières valeurs (`inclure=True` : t − n + 1 … t ; sinon t − n … t − 1)."""
    x = _f(values)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    win = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)
    if inclure:
        out[n - 1:] = win
    else:
        out[n:] = win[:-1]
    return out


def min_glissant(values, n: int, *, inclure: bool = True) -> np.ndarray:
    return -max_glissant(-_f(values), n, inclure=inclure)


def decale(values, k: int) -> np.ndarray:
    """Valeur de t − k en t (NaN au début)."""
    x = _f(values)
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def croise_dessus(a, b) -> np.ndarray:
    """Vrai en t si a > b en t et a ≤ b en t − 1, les deux définis aux deux instants."""
    a, b = _f(a), _f(b)
    valid = ~(np.isnan(a) | np.isnan(b))
    above = np.where(valid, a > b, False)
    out = np.zeros(len(a), dtype=bool)
    out[1:] = above[1:] & valid[:-1] & ~above[:-1]
    return out


def croise_dessous(a, b) -> np.ndarray:
    return croise_dessus(-_f(a), -_f(b))


def superieur(a, b) -> np.ndarray:
    """a > b, faux si l'un est indéfini."""
    a, b = _f(a), _f(b)
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a > b)


def inferieur(a, b) -> np.ndarray:
    return superieur(b, a)


# --- Indicateurs de tendance ----------------------------------------------------------------------------------------

def moyenne(kind: str, values, n: int) -> np.ndarray:
    if kind == "SMA":
        return sma(values, n)
    if kind == "EMA":
        return ema(values, n)
    raise ValueError(f"moyenne inconnue : {kind}")


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    return line, ema(line, signal)


@njit
def _supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr_: np.ndarray,
                k: float) -> np.ndarray:
    n = len(close)
    direction = np.zeros(n, dtype=np.int8)       # 1 haussier, -1 baissier, 0 indéfini
    upper = np.nan
    lower = np.nan
    trend = 0
    for t in range(n):
        if np.isnan(atr_[t]):
            continue
        mid = (high[t] + low[t]) / 2.0
        basic_up = mid + k * atr_[t]
        basic_low = mid - k * atr_[t]
        if trend == 0:
            upper, lower = basic_up, basic_low
            trend = 1 if close[t] > mid else -1
            direction[t] = trend
            continue
        prev_close = close[t - 1]
        upper = basic_up if (basic_up < upper or prev_close > upper) else upper
        lower = basic_low if (basic_low > lower or prev_close < lower) else lower
        if trend == 1 and close[t] < lower:
            trend = -1
        elif trend == -1 and close[t] > upper:
            trend = 1
        direction[t] = trend
    return direction


def supertrend(high, low, close, n: int = 10, k: float = 3.0) -> np.ndarray:
    """Direction du SuperTrend (1 haussier, −1 baissier, 0 indéfini) : bandes (H + L)/2 ± k × ATR n (Wilder),
    bandes finales qui ne reculent que si le prix les a franchies, bascule quand la clôture traverse la bande opposée."""
    return _supertrend(_f(high), _f(low), _f(close), atr(high, low, close, n), float(k))


@njit
def _sar(high: np.ndarray, low: np.ndarray, step: float, maximum: float) -> np.ndarray:
    n = len(high)
    direction = np.zeros(n, dtype=np.int8)
    if n < 2:
        return direction
    up_move = high[1] - high[0]
    down_move = low[0] - low[1]
    long = not (down_move > 0.0 and down_move > up_move)
    sar = low[0] if long else high[0]
    ep = high[1] if long else low[1]
    af = step
    direction[1] = 1 if long else -1
    for t in range(2, n):
        sar = sar + af * (ep - sar)
        if long:
            sar = min(sar, low[t - 1], low[t - 2])
            if low[t] < sar:
                long = False
                sar = ep
                ep = low[t]
                af = step
            elif high[t] > ep:
                ep = high[t]
                af = min(af + step, maximum)
        else:
            sar = max(sar, high[t - 1], high[t - 2])
            if high[t] > sar:
                long = True
                sar = ep
                ep = high[t]
                af = step
            elif low[t] < ep:
                ep = low[t]
                af = min(af + step, maximum)
        direction[t] = 1 if long else -1
    return direction


def sar_direction(high, low, step: float = 0.02, maximum: float = 0.20) -> np.ndarray:
    """Parabolic SAR de Wilder : 1 si le SAR est sous le prix (position acheteuse), −1 sinon, 0 sur la 1re bougie.
    Sens initial par le mouvement directionnel de la 2e bougie (TA-Lib)."""
    return _sar(_f(high), _f(low), float(step), float(maximum))


def dmi(high, low, close, n: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(+DI, −DI, ADX) de Wilder : mouvements directionnels et vrai range lissés par RMA, ADX = RMA du DX."""
    h, lo = _f(high), _f(low)
    up = np.full(len(h), np.nan)
    down = np.full(len(h), np.nan)
    if len(h) > 1:
        u, d = h[1:] - h[:-1], lo[:-1] - lo[1:]
        up[1:] = np.where((u > d) & (u > 0), u, 0.0)
        down[1:] = np.where((d > u) & (d > 0), d, 0.0)
    tr = rma(vrai_range(high, low, close), n)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus = 100 * rma(up, n) / tr
        minus = 100 * rma(down, n) / tr
        total = plus + minus
        dx = np.where(total > 0, 100 * np.abs(plus - minus) / np.where(total > 0, total, 1.0), 0.0)
    dx[np.isnan(plus) | np.isnan(minus)] = np.nan
    return plus, minus, rma(dx, n)


def ichimoku(high, low, close, tenkan: int = 9, kijun: int = 26, senkou: int = 52,
             shift: int = 26) -> dict[str, np.ndarray]:
    """Tenkan, Kijun, et le NUAGE APPLICABLE en t : Senkou A et B calculées en t − `shift` (jamais la Chikou)."""
    h, lo = _f(high), _f(low)
    tk = (max_glissant(h, tenkan) + min_glissant(lo, tenkan)) / 2
    kj = (max_glissant(h, kijun) + min_glissant(lo, kijun)) / 2
    span_a = (tk + kj) / 2
    span_b = (max_glissant(h, senkou) + min_glissant(lo, senkou)) / 2
    a, b = decale(span_a, shift), decale(span_b, shift)
    return {"tenkan": tk, "kijun": kj, "nuage_a": a, "nuage_b": b, "nuage_haut": np.maximum(a, b),
            "nuage_defini": ~(np.isnan(a) | np.isnan(b))}


def aroon(high, low, n: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """Aroon haut et bas sur n + 1 bougies (t − n … t) : 100 × (n − bougies depuis l'extrême) / n ; en cas
    d'égalité, l'extrême le plus récent (TA-Lib)."""
    h, lo = _f(high), _f(low)
    up = np.full(len(h), np.nan)
    down = np.full(len(h), np.nan)
    if len(h) > n:
        wh = np.lib.stride_tricks.sliding_window_view(h, n + 1)
        wl = np.lib.stride_tricks.sliding_window_view(lo, n + 1)
        since_high = np.argmax(wh[:, ::-1], axis=1)       # bougies depuis le plus récent maximum
        since_low = np.argmin(wl[:, ::-1], axis=1)
        up[n:] = 100.0 * (n - since_high) / n
        down[n:] = 100.0 * (n - since_low) / n
    return up, down


def vortex(high, low, close, n: int = 14) -> tuple[np.ndarray, np.ndarray]:
    h, lo = _f(high), _f(low)
    plus = np.full(len(h), np.nan)
    minus = np.full(len(h), np.nan)
    if len(h) > 1:
        plus[1:] = np.abs(h[1:] - lo[:-1])
        minus[1:] = np.abs(lo[1:] - h[:-1])
    tr = vrai_range(high, low, close)
    sum_tr = sma(tr, n) * n
    with np.errstate(invalid="ignore", divide="ignore"):
        return sma(plus, n) * n / sum_tr, sma(minus, n) * n / sum_tr


def trix(close, n: int = 15, signal: int = 9) -> tuple[np.ndarray, np.ndarray]:
    triple = ema(ema(ema(close, n), n), n)
    out = np.full(len(triple), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[1:] = 100.0 * (triple[1:] / triple[:-1] - 1.0)
    return out, ema(out, signal)


@njit
def _kama(close: np.ndarray, n: int, fast: float, slow: float) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    value = close[n - 1]
    for t in range(n, len(close)):
        change = abs(close[t] - close[t - n])
        volatility = 0.0
        for i in range(t - n + 1, t + 1):
            volatility += abs(close[i] - close[i - 1])
        er = change / volatility if volatility > 0 else 0.0
        sc = (er * (fast - slow) + slow) ** 2
        value = value + sc * (close[t] - value)
        out[t] = value
    return out


def kama(close, n: int = 10, fast: int = 2, slow: int = 30) -> np.ndarray:
    """KAMA de Kaufman : ER sur n, constantes 2/(fast + 1) et 2/(slow + 1), amorcée par la clôture n − 1."""
    return _kama(_f(close), int(n), 2.0 / (fast + 1.0), 2.0 / (slow + 1.0))


def hma(close, n: int = 20) -> np.ndarray:
    """Moyenne de Hull : WMA(2 × WMA(n/2) − WMA(n), ⌊√n⌋)."""
    raw = 2 * wma(close, n // 2) - wma(close, n)
    first = premier_valide(raw)
    out = np.full(len(raw), np.nan)
    m = int(np.sqrt(n))
    if first < len(raw):
        out[first:] = wma(raw[first:], m)
    return out


@njit
def _filtre(close: np.ndarray, start: int, x: float) -> np.ndarray:
    state = np.zeros(len(close), dtype=np.bool_)
    if start >= len(close):
        return state
    invested = False
    low = close[start]
    high = close[start]
    for t in range(start, len(close)):
        c = close[t]
        if invested:
            if c > high:
                high = c
            if c <= (1.0 - x) * high:
                invested = False
                low = c
        else:
            if c < low:
                low = c
            if c >= (1.0 + x) * low:
                invested = True
                high = c
        state[t] = invested
    return state


def filtre_alexander(close, start: int, x: float) -> np.ndarray:
    """Règle de filtre d'Alexander, machine à deux états sur les clôtures à partir de l'indice `start` (début de
    l'étape) : hors marché, achat quand la clôture ≥ (1 + x) × plus bas des clôtures depuis la dernière sortie (ou
    `start`) ; investi, sortie quand la clôture ≤ (1 − x) × plus haut atteint depuis l'entrée."""
    return _filtre(_f(close), int(start), float(x))


@njit
def _point_figure(close: np.ndarray, box: float, reversal: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    buy = np.zeros(n, dtype=np.bool_)
    sell = np.zeros(n, dtype=np.bool_)
    if n == 0:
        return buy, sell
    step = np.log(1.0 + box)
    level0 = np.log(close[0]) / step
    direction = 0                # 1 colonne de X, -1 colonne de O, 0 indéfini
    top = np.floor(level0)
    bottom = np.ceil(level0)
    prev_x_top = np.nan
    prev_o_bottom = np.nan
    fired = False
    for t in range(1, n):
        level = np.log(close[t]) / step
        up_box = np.floor(level)
        down_box = np.ceil(level)
        if direction == 0:
            if up_box >= top + 1:
                direction = 1
                top = up_box
                fired = False
            elif down_box <= bottom - 1:
                direction = -1
                bottom = down_box
                fired = False
            continue
        if direction == 1:
            if up_box > top:
                top = up_box
            elif down_box <= top - reversal:
                prev_x_top = top
                direction = -1
                bottom = down_box
                fired = False
            if direction == 1 and not fired and not np.isnan(prev_x_top) and top > prev_x_top:
                buy[t] = True
                fired = True
        else:
            if down_box < bottom:
                bottom = down_box
            elif up_box >= bottom + reversal:
                prev_o_bottom = bottom
                direction = 1
                top = up_box
                fired = False
            if direction == -1 and not fired and not np.isnan(prev_o_bottom) and bottom < prev_o_bottom:
                sell[t] = True
                fired = True
    return buy, sell


def point_figure(close, box: float = 0.01, reversal: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Point et figure sur les clôtures (boîte `box` en échelle logarithmique, retournement de `reversal` boîtes),
    construit sur le passé seul, jamais révisé. Rend (achat, vente) : la colonne de X en cours dépasse le sommet de la
    colonne de X précédente (double sommet cassé) ; la colonne de O en cours passe sous le creux de la colonne de O
    précédente. Un signal par colonne, à la bougie où il apparaît."""
    return _point_figure(_f(close), float(box), int(reversal))
