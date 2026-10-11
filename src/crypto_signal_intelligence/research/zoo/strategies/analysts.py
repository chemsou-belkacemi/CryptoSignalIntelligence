"""Méthodes d'analystes (AN) : zone OTE (AN05), breaker block (AN06), vague 3 d'Elliott (AN09), swing de Gann (AN13) ;
8 tests de forme (a).

Paramètres standard du recensement (docs/ZOO_CATALOGUE.md § 19), lus dans `ctx.params` ; fonctions PURES, la valeur
en `t` ne lit que les bougies `≤ t`. Pivots ZigZag CONFIRMÉS (`structure.pivots_zigzag`, `INDICATEURS.md` § 1, `m`
par unité) : un pivot n'est connu qu'à la bougie qui le confirme.

Lectures déclarées (lignes du recensement, à relire) :
- AN05 (OTE, `INDICATEURS.md` § 6 et § 10.6) : jambe = dernier pivot haut confirmé `H` et pivot bas précédent `B`,
  zone [H − 0,79 (H − B) ; H − 0,62 (H − B)] ; fenêtre de la jambe comme ST03 (de la confirmation de `H` à celle du
  pivot suivant, exclue) ; « contact » = une bougie de la fenêtre dont la plage [bas ; haut] coupe la zone ; signal à
  la 1re clôture > niveau 62 % à partir du contact (la bougie du contact peut être celle du signal) ; un signal par
  jambe ; sortie : clôture ≥ H ou < B de la jambe du dernier signal ;
- AN06 (breaker, `INDICATEURS.md` § 3 et § 10.6, `patterns.smc.order_blocks` et `patterns.indicators.block_events`,
  copiés en version compilée et testés à l'identique) : order block BAISSIER (dernière bougie haussière avant un
  déplacement baissier de 2 ATR en 3 bougies, ATR de Wilder 14) invalidé par une clôture au-dessus du haut de sa zone →
  breaker HAUSSIER (même zone [bas ; haut]), connu à cette clôture ; « 1er retour » = la 1re bougie suivante dont le
  plus bas ≤ haut de la zone ; signal si ELLE clôture au-dessus du haut de la zone (un essai par breaker) ; sortie :
  clôture sous le bas de la zone du dernier signal ;
- AN09 (Elliott, vague 3) : trois derniers pivots confirmés P0 (bas), P1 (haut), P2 (bas), le dernier connu étant P2 ;
  vague 2 = (P1 − P2) / (P1 − P0) entre 0,382 et 0,786 (bornes incluses) et P2 > P0 ; signal à la 1re clôture > P1
  entre la confirmation de P2 (incluse) et celle du pivot suivant (exclue) ; sortie : clôture ≥ P0 + 1,618 (P1 − P0)
  ou < P2 du dernier signal ;
- AN13 (swing de Gann, 2 bougies) : le tracé monte après 2 plus hauts consécutifs plus hauts (H_t > H_{t−1} >
  H_{t−2}) et descend après 2 plus bas consécutifs plus bas ; au retournement à la baisse en `t`, le sommet de swing
  = plus haut de la montée (depuis le retournement précédent, jusqu'à `t`), connu en `t` ; symétrique pour le creux.
  Tendance haussière (l'état) dès qu'une clôture dépasse le dernier sommet de swing connu, jusqu'à une clôture sous le
  dernier creux de swing connu (la sortie).
"""
from __future__ import annotations

import numpy as np

from ....patterns import smc
from .._jit import njit
from ..data import Bougies
from ..spec import Contexte, Signaux
from .structure import atr_wilder, m_zigzag, pivots_zigzag

IMPLEMENTEES: frozenset[str] = frozenset({"ote", "breaker", "elliott_vague3", "gann_swing"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def _f(values) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


# --- AN05 OTE -----------------------------------------------------------------------------------------------------

@njit
def _ote(high: np.ndarray, low: np.ndarray, close: np.ndarray, sens: np.ndarray, prix: np.ndarray,
         connu: np.ndarray, bas: float, haut: float):
    n = len(close)
    entry = np.zeros(n, dtype=np.bool_)
    out = np.zeros(n, dtype=np.bool_)
    j = 0
    active = False
    touched = False
    upper = np.nan
    lower = np.nan
    top = np.nan
    bottom = np.nan
    exit_top = np.nan
    exit_bottom = np.nan
    for t in range(n):
        while j < len(connu) and connu[j] <= t:
            if sens[j] == 1 and j >= 1:
                top = prix[j]
                bottom = prix[j - 1]
                upper = top - bas * (top - bottom)       # niveau 62 %
                lower = top - haut * (top - bottom)      # niveau 79 %
                active = True
                touched = False
            else:
                active = False
            j += 1
        if active:
            if low[t] <= upper and high[t] >= lower:
                touched = True
            if touched and close[t] > upper:
                entry[t] = True
                active = False
                exit_top = top
                exit_bottom = bottom
        out[t] = close[t] >= exit_top or close[t] < exit_bottom
    return entry, out


def _ote_impl(frame: Bougies, ctx: Contexte, *, extreme: bool = False) -> Signaux:
    piv = pivots_zigzag(frame, m_zigzag(ctx), extreme=extreme)
    entry, out = _ote(_f(frame.haut), _f(frame.bas), _f(frame.cloture), piv.sens, piv.prix, piv.connu,
                      float(ctx.params["bas"]), float(ctx.params["haut"]))
    return Signaux(entry, out)


def ote(frame: Bougies, ctx: Contexte) -> Signaux:
    """AN05 : dans la dernière jambe haussière ZigZag confirmée, contact de la zone 62-79 % puis 1re clôture > 62 % ;
    sortie clôture ≥ sommet de la jambe ou < son creux."""
    return _ote_impl(frame, ctx)


# --- AN06 Breaker block -------------------------------------------------------------------------------------------

@njit
def _order_blocks(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, a: np.ndarray, x: float,
                  window: int):
    n = len(c)
    index = np.empty(n, dtype=np.int64)
    side = np.empty(n, dtype=np.int8)          # 1 : OB haussier (« bull »), −1 : OB baissier (« bear »)
    bottom = np.empty(n)
    top = np.empty(n)
    known = np.empty(n, dtype=np.int64)
    k_out = 0
    for k in range(n):
        if not np.isfinite(a[k]):
            continue
        bearish = c[k] < o[k]
        bullish = c[k] > o[k]
        above = False
        below = False
        cmax = -np.inf
        cmin = np.inf
        same_bear = False
        same_bull = False
        for j in range(k + 1, min(n, k + 1 + window)):
            cj = c[j]
            above = above or cj > h[k]
            below = below or cj < lo[k]
            cmax = max(cmax, cj)
            cmin = min(cmin, cj)
            same_bear = same_bear or cj < o[j]
            same_bull = same_bull or cj > o[j]
            if bearish and above and cmax >= c[k] + x * a[k]:
                if not same_bear:
                    index[k_out], side[k_out], bottom[k_out], top[k_out], known[k_out] = k, 1, lo[k], h[k], j
                    k_out += 1
                break
            if bullish and below and cmin <= c[k] - x * a[k]:
                if not same_bull:
                    index[k_out], side[k_out], bottom[k_out], top[k_out], known[k_out] = k, -1, lo[k], h[k], j
                    k_out += 1
                break
    return index[:k_out], side[:k_out], bottom[:k_out], top[:k_out], known[:k_out]


def order_blocks_rapides(o, h, lo, c, atr, *, x: float = smc.OB_DISPLACEMENT_ATR, window: int = smc.OB_WINDOW):
    """`patterns.smc.order_blocks` à l'identique, compilé : (bougie d'origine, sens 1 haussier / −1 baissier, bas,
    haut, bougie où il est connu)."""
    return _order_blocks(_f(o), _f(h), _f(lo), _f(c), _f(atr), float(x), int(window))


@njit
def _breakers(close: np.ndarray, side: np.ndarray, bottom: np.ndarray, top: np.ndarray, known: np.ndarray):
    """Breakers haussiers : OB baissier invalidé à la 1re clôture > haut de sa zone après sa connaissance."""
    n = len(close)
    k = len(side)
    at = np.full(k, -1, dtype=np.int64)
    for b in range(k):
        if side[b] != -1:
            continue
        for s in range(known[b] + 1, n):
            if close[s] > top[b]:
                at[b] = s
                break
    return at


@njit
def _retours(high: np.ndarray, low: np.ndarray, close: np.ndarray, bottom: np.ndarray, top: np.ndarray,
             at: np.ndarray):
    n = len(close)
    entry = np.zeros(n, dtype=np.bool_)
    zone_low = np.full(n, np.nan)               # bas de zone du signal, à la bougie du signal
    for b in range(len(at)):
        if at[b] < 0:
            continue
        for s in range(at[b] + 1, n):
            if low[s] <= top[b]:
                if close[s] > top[b]:
                    entry[s] = True
                    if np.isnan(zone_low[s]) or bottom[b] > zone_low[s]:
                        zone_low[s] = bottom[b]
                break
    return entry, zone_low


@njit
def _sortie_zone(close: np.ndarray, zone_low: np.ndarray) -> np.ndarray:
    out = np.zeros(len(close), dtype=np.bool_)
    level = np.nan
    for t in range(len(close)):
        if not np.isnan(zone_low[t]):
            level = zone_low[t]
        out[t] = close[t] < level
    return out


def _breaker_impl(frame: Bougies, ctx: Contexte, *, origine: bool = False) -> Signaux:
    atr = atr_wilder(frame.haut, frame.bas, frame.cloture)
    _, side, bottom, top, known = order_blocks_rapides(frame.ouverture, frame.haut, frame.bas, frame.cloture, atr)
    if origine:                                  # MUTATION des contrôles : OB connu dès sa bougie d'origine
        known = order_blocks_rapides(frame.ouverture, frame.haut, frame.bas, frame.cloture, atr)[0]
    close = _f(frame.cloture)
    at = _breakers(close, side, bottom, top, known)
    entry, zone_low = _retours(_f(frame.haut), _f(frame.bas), close, bottom, top, at)
    return Signaux(entry, _sortie_zone(close, zone_low))


def breaker(frame: Bougies, ctx: Contexte) -> Signaux:
    """AN06 : 1er retour dans un breaker haussier, clôturé au-dessus de la zone ; sortie clôture sous la zone."""
    return _breaker_impl(frame, ctx)


# --- AN09 Elliott, vague 3 ------------------------------------------------------------------------------------------

@njit
def _elliott(close: np.ndarray, sens: np.ndarray, prix: np.ndarray, connu: np.ndarray, bas: float, haut: float,
             objectif: float):
    n = len(close)
    entry = np.zeros(n, dtype=np.bool_)
    out = np.zeros(n, dtype=np.bool_)
    j = 0
    active = False
    wave_top = np.nan
    target = np.nan
    stop = np.nan
    exit_target = np.nan
    exit_stop = np.nan
    for t in range(n):
        while j < len(connu) and connu[j] <= t:
            active = False
            if sens[j] == -1 and j >= 2:
                p0 = prix[j - 2]
                p1 = prix[j - 1]
                p2 = prix[j]
                wave1 = p1 - p0
                if wave1 > 0.0 and p2 > p0:
                    ratio = (p1 - p2) / wave1
                    if bas <= ratio <= haut:
                        active = True
                        wave_top = p1
                        target = p0 + objectif * wave1
                        stop = p2
            j += 1
        if active and close[t] > wave_top:
            entry[t] = True
            active = False
            exit_target = target
            exit_stop = stop
        out[t] = close[t] >= exit_target or close[t] < exit_stop
    return entry, out


def _elliott_impl(frame: Bougies, ctx: Contexte, *, extreme: bool = False) -> Signaux:
    p = ctx.params
    piv = pivots_zigzag(frame, m_zigzag(ctx), extreme=extreme)
    entry, out = _elliott(_f(frame.cloture), piv.sens, piv.prix, piv.connu, float(p["bas"]), float(p["haut"]),
                          float(p["objectif"]))
    return Signaux(entry, out)


def elliott_vague3(frame: Bougies, ctx: Contexte) -> Signaux:
    """AN09 : pivots confirmés, clôture > sommet de la vague 1 après une vague 2 de 38,2 à 78,6 % restée au-dessus de
    son départ ; sortie clôture ≥ départ + 1,618 × vague 1, ou < creux de la vague 2."""
    return _elliott_impl(frame, ctx)


# --- AN13 Swing de Gann ---------------------------------------------------------------------------------------------

@njit
def _gann(high: np.ndarray, low: np.ndarray, n_bars: int):
    """Sommets et creux de swing de Gann (n bougies) : (prix du sommet connu à t ou NaN, prix du creux, bougie de
    l'extrême du sommet, bougie de l'extrême du creux)."""
    n = len(high)
    top_at = np.full(n, np.nan)
    bottom_at = np.full(n, np.nan)
    top_idx = np.full(n, -1, dtype=np.int64)
    bottom_idx = np.full(n, -1, dtype=np.int64)
    swing = 0
    anchor = 0
    for t in range(n_bars, n):
        up = True
        down = True
        for k in range(n_bars):
            up = up and high[t - k] > high[t - k - 1]
            down = down and low[t - k] < low[t - k - 1]
        if swing != 1 and up and not (swing == 0 and down):
            if swing == -1 or swing == 0:
                best = anchor
                for s in range(anchor, t + 1):
                    if low[s] < low[best]:
                        best = s
                bottom_at[t] = low[best]
                bottom_idx[t] = best
                anchor = best
            swing = 1
        elif swing != -1 and down and not (swing == 0 and up):
            best = anchor
            for s in range(anchor, t + 1):
                if high[s] > high[best]:
                    best = s
            top_at[t] = high[best]
            top_idx[t] = best
            anchor = best
            swing = -1
    return top_at, bottom_at, top_idx, bottom_idx


@njit
def _gann_tendance(close: np.ndarray, top_at: np.ndarray, bottom_at: np.ndarray):
    n = len(close)
    state = np.zeros(n, dtype=np.bool_)
    out = np.zeros(n, dtype=np.bool_)
    top = np.nan
    bottom = np.nan
    up = False
    for t in range(n):
        if not np.isnan(top_at[t]):
            top = top_at[t]
        if not np.isnan(bottom_at[t]):
            bottom = bottom_at[t]
        if close[t] < bottom:
            up = False
            out[t] = True
        elif close[t] > top:
            up = True
        state[t] = up
    return state, out


def swings_gann(frame: Bougies, n: int, *, extreme: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """(sommet de swing connu à chaque bougie ou NaN, creux de swing connu ou NaN). `extreme=True` : MUTATION des
    contrôles (sommet et creux datés à leur extrême)."""
    top_at, bottom_at, top_idx, bottom_idx = _gann(_f(frame.haut), _f(frame.bas), int(n))
    if not extreme:
        return top_at, bottom_at
    moved_top = np.full(len(frame), np.nan)
    moved_bottom = np.full(len(frame), np.nan)
    ok = top_idx >= 0
    moved_top[top_idx[ok]] = top_at[ok]
    ok = bottom_idx >= 0
    moved_bottom[bottom_idx[ok]] = bottom_at[ok]
    return moved_top, moved_bottom


def _gann_impl(frame: Bougies, ctx: Contexte, *, extreme: bool = False) -> Signaux:
    top_at, bottom_at = swings_gann(frame, ctx.params["n"], extreme=extreme)
    state, out = _gann_tendance(_f(frame.cloture), top_at, bottom_at)
    return Signaux(state, out)


def gann_swing(frame: Bougies, ctx: Contexte) -> Signaux:
    """AN13 (état) : swing de Gann haussier (dernier sommet de swing de 2 bougies dépassé à la clôture) ; sortie
    clôture sous le dernier creux de swing."""
    return _gann_impl(frame, ctx)
