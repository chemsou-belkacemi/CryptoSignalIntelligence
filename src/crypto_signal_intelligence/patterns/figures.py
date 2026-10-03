"""Détecteur de figures (docs/INDICATEURS.md § 9, phase 11, test F15) : harmoniques (Gartley, Bat, Butterfly, Crab),
ABCD, triangles et biseaux, cassures de lignes de tendance, configuration ICT/SMC, figures chartistes classiques
(tête-épaules, double creux/sommet, drapeau/fanion, coupe avec anse, § 9.6). Fonctions pures sur des bougies
clôturées d'une unité de temps ; chaque figure porte `detected_at`, la bougie à la clôture de laquelle elle est
détectée (rien de postérieur n'est lu), et, si elle est haussière, ses niveaux de transaction (§ 9.5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import smc
from .primitives import Pivot, atr, zigzag

TOLERANCE = 0.05
STOP_ATR = 0.25
TRENDLINE_ATR = 0.5
TRIANGLE_FLAT = 0.10                 # descriptif : ligne « plate » (triangle ascendant ou descendant)
ICT_WINDOW = 10
MIN_STOP_DISTANCE = 0.001

#: Figure → (B en part de XA, C en part de AB, D en retracement de XA, D en extension de BC) ; une valeur seule ou
#: une plage (a, b).
HARMONICS: dict[str, tuple] = {
    "GARTLEY": (0.618, (0.382, 0.886), 0.786, (1.272, 1.618)),
    "BAT": ((0.382, 0.50), (0.382, 0.886), 0.886, (1.618, 2.618)),
    "BUTTERFLY": (0.786, (0.382, 0.886), 1.272, (1.618, 2.24)),
    "CRAB": ((0.382, 0.618), (0.382, 0.886), 1.618, (2.24, 3.618)),
}
CLASSICS = ("HEAD_SHOULDERS", "DOUBLE", "FLAG", "CUP_HANDLE")
FAMILIES = (*HARMONICS, "ABCD", "TRIANGLE", "TRENDLINE", "ICT", *CLASSICS)
HS_SHOULDERS = 0.25                  # |L1 − L3| ≤ 0,25 × profondeur
HS_SYMMETRY = (0.5, 2.0)             # (L3 − L2) / (L2 − L1) en bougies
DOUBLE_EQUAL = 0.10                  # |L1 − L2| ≤ 0,10 × hauteur
DOUBLE_MIN_BARS = 5
FLAG_POLE_M = 2.0                    # mât d'au moins 2 m × ATR
FLAG_POLE_BARS = 10
FLAG_RETRACE = 0.50
FLAG_DURATION = 2.0
CUP_RIMS = 0.10
CUP_CENTER = 0.60                    # fond dans les 60 % centraux de la coupe
CUP_HANDLE_DEPTH = 0.50              # anse dans la moitié haute
CUP_LENGTH = 3.0                     # coupe ≥ 3 × anse


@dataclass(frozen=True)
class Figure:
    family: str
    side: str                       # "bull" | "bear"
    detected_at: int
    anchors: tuple[int, ...]        # indices des pivots ou bougies qui définissent la figure (identité)
    entry: float | None = None      # niveaux de transaction (figures haussières seulement)
    stop: float | None = None
    targets: tuple[float, ...] = ()
    zone: tuple[float, float] | None = None
    notes: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.family}:{self.side}:{'-'.join(map(str, self.anchors))}"

    @property
    def valid(self) -> bool:
        """Géométrie jouable : stop sous l'entrée d'au moins 0,1 %, premier objectif au-dessus de l'entrée."""
        return (self.entry is not None and self.stop is not None and bool(self.targets)
                and self.stop < self.entry * (1 - MIN_STOP_DISTANCE) and self.targets[0] > self.entry)


def within(ratio: float, spec) -> bool:
    lo, hi = (spec, spec) if not isinstance(spec, tuple) else spec
    return (1 - TOLERANCE) * lo <= ratio <= (1 + TOLERANCE) * hi


def _span(spec) -> tuple[float, float]:
    lo, hi = (spec, spec) if not isinstance(spec, tuple) else spec
    return (1 - TOLERANCE) * lo, (1 + TOLERANCE) * hi


# --- Harmoniques et ABCD (§ 9.1) ---------------------------------------------------------------------------------

def _prz_bull(x: float, a: float, b: float, c: float, d_ret, bc_ext) -> tuple[float, float] | None:
    """Intersection des prix de D : retracement de XA (± 5 %) et extension de BC (plage ± 5 %)."""
    xa, bc = a - x, c - b
    r_lo, r_hi = _span(d_ret)
    e_lo, e_hi = _span(bc_ext)
    one = (a - r_hi * xa, a - r_lo * xa)
    two = (c - e_hi * bc, c - e_lo * bc)
    lo, hi = max(one[0], two[0]), min(one[1], two[1])
    return (lo, hi) if lo <= hi else None


def harmonics(pivots: list[Pivot], close: np.ndarray, atr_values: np.ndarray) -> list[Figure]:
    """Harmoniques sur chaque suite de 4 pivots consécutifs X, A, B, C ; détectées quand C est connu."""
    out: list[Figure] = []
    for k in range(3, len(pivots)):
        x, a, b, c = pivots[k - 3:k + 1]
        at = c.known_at
        if not np.isfinite(atr_values[at]):
            continue
        bull = (x.kind, a.kind, b.kind, c.kind) == ("low", "high", "low", "high")
        bear = (x.kind, a.kind, b.kind, c.kind) == ("high", "low", "high", "low")
        if not (bull or bear):
            continue
        sign = 1.0 if bull else -1.0
        xp, ap, bp, cp = (sign * p.price for p in (x, a, b, c))     # miroir : une figure baissière devient haussière
        xa, ab, bc = ap - xp, ap - bp, cp - bp
        if xa <= 0 or ab <= 0 or bc <= 0:
            continue
        for name, (b_spec, c_spec, d_ret, bc_ext) in HARMONICS.items():
            if not (within(ab / xa, b_spec) and within(bc / ab, c_spec)):
                continue
            prz = _prz_bull(xp, ap, bp, cp, d_ret, bc_ext)
            if prz is None:
                continue
            lo, hi = prz
            if sign * close[at] < lo:                               # déjà au-delà de la PRZ : écartée
                continue
            out.append(_harmonic_figure(name, bull, at, (x.index, a.index, b.index, c.index), prz, sign, xp, ap,
                                        atr_values[at]))
    return out


def _harmonic_figure(name: str, bull: bool, at: int, anchors: tuple[int, ...], prz: tuple[float, float], sign: float,
                     xp: float, ap: float, atr_now: float) -> Figure:
    lo, hi = prz
    zone = (lo, hi) if bull else (-hi, -lo)
    if not bull:
        return Figure(name, "bear", at, anchors, zone=zone)
    entry = hi
    stop = min(xp, lo) - STOP_ATR * atr_now
    ad = ap - entry
    return Figure(name, "bull", at, anchors, entry=entry, stop=stop,
                  targets=(entry + 0.382 * ad, entry + 0.618 * ad, ap), zone=zone)


def abcd(pivots: list[Pivot], close: np.ndarray, atr_values: np.ndarray) -> list[Figure]:
    out: list[Figure] = []
    for k in range(2, len(pivots)):
        a, b, c = pivots[k - 2:k + 1]
        at = c.known_at
        if not np.isfinite(atr_values[at]):
            continue
        bull = (a.kind, b.kind, c.kind) == ("high", "low", "high")
        bear = (a.kind, b.kind, c.kind) == ("low", "high", "low")
        if not (bull or bear):
            continue
        sign = 1.0 if bull else -1.0
        ap, bp, cp = (sign * p.price for p in (a, b, c))
        ab, bc = ap - bp, cp - bp
        if ab <= 0 or bc <= 0 or not within(bc / ab, (0.382, 0.886)):
            continue
        one = (cp - 1.05 * ab, cp - 0.95 * ab)
        e_lo, e_hi = _span((1.272, 1.618))
        two = (cp - e_hi * bc, cp - e_lo * bc)
        lo, hi = max(one[0], two[0]), min(one[1], two[1])
        if lo > hi or sign * close[at] < lo:
            continue
        anchors = (a.index, b.index, c.index)
        if not bull:
            out.append(Figure("ABCD", "bear", at, anchors, zone=(-hi, -lo)))
            continue
        entry = hi
        stop = lo - STOP_ATR * atr_values[at]
        ad = ap - entry
        out.append(Figure("ABCD", "bull", at, anchors, entry=entry, stop=stop,
                          targets=(entry + 0.382 * ad, entry + 0.618 * ad, ap), zone=(lo, hi)))
    return out


# --- Triangles et biseaux (§ 9.2) -------------------------------------------------------------------------------

def _line(p: Pivot, q: Pivot):
    slope = (q.price - p.price) / (q.index - p.index)
    return slope, (lambda i: p.price + slope * (i - p.index))


def triangles(pivots: list[Pivot], high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[Figure]:
    """Lignes par les deux derniers pivots hauts et les deux derniers pivots bas (4 pivots alternés), convergentes ;
    cassure = première clôture au-delà d'une ligne à partir de la bougie où le 4e pivot est connu (incluse), avant
    leur croisement."""
    out: list[Figure] = []
    done: set[tuple[int, ...]] = set()
    for k in range(3, len(pivots)):
        four = pivots[k - 3:k + 1]
        if any(four[j].kind == four[j + 1].kind for j in range(3)):
            continue
        highs = [p for p in four if p.kind == "high"]
        lows = [p for p in four if p.kind == "low"]
        su, upper = _line(highs[0], highs[1])
        sl, lower = _line(lows[0], lows[1])
        if su >= sl:                                               # non convergentes
            continue
        first = four[0].index
        cross = first + (upper(first) - lower(first)) / (sl - su)
        known = four[-1].known_at
        span = np.arange(first, known + 1)
        if (np.array([upper(i) for i in span]) <= np.array([lower(i) for i in span])).any():
            continue
        anchors = tuple(p.index for p in four)
        if anchors in done:
            continue
        height = upper(first) - lower(first)
        next_known = pivots[k + 1].known_at if k + 1 < len(pivots) else len(close)
        length = known - first
        for i in range(known, min(len(close), int(np.floor(cross)), next_known + 1)):
            if close[i] > upper(i):
                entry, stop = upper(i), lower(i)
                out.append(Figure("TRIANGLE", "bull", i, anchors, entry=entry, stop=stop,
                                  targets=(entry + height / 3, entry + 2 * height / 3, entry + height),
                                  notes={"kind": _triangle_kind(su, sl, length, height), "height": height}))
                done.add(anchors)
                break
            if close[i] < lower(i):
                out.append(Figure("TRIANGLE", "bear", i, anchors, notes={"kind": _triangle_kind(su, sl, length, height), "height": height}))
                done.add(anchors)
                break
    return out


def _triangle_kind(su: float, sl: float, length: float, height: float) -> str:
    """Descriptif : une ligne est « plate » si elle varie de moins de 10 % de la hauteur sur la figure."""
    flat = TRIANGLE_FLAT * height
    if abs(su) * length <= flat:
        return "ascendant"
    if abs(sl) * length <= flat:
        return "descendant"
    if su < 0 < sl:
        return "symetrique"
    return "biseau_montant" if su > 0 else "biseau_descendant"


# --- Lignes de tendance (§ 9.3) ---------------------------------------------------------------------------------

def trendlines(pivots: list[Pivot], high: np.ndarray, low: np.ndarray, close: np.ndarray,
               atr_values: np.ndarray) -> list[Figure]:
    """Résistance descendante par trois pivots hauts consécutifs (P2 à moins de 0,5 ATR de la ligne P1-P3, aucun plus
    haut au-dessus de plus de 0,5 ATR) ; cassure = première clôture au-dessus à partir de la bougie où P3 est connu
    (incluse). Support montant : baissière, pour information."""
    out: list[Figure] = []
    for kind, side in (("high", "bull"), ("low", "bear")):
        same = [p for p in pivots if p.kind == kind]
        for k in range(2, len(same)):
            p1, p2, p3 = same[k - 2:k + 1]
            descending = p1.price > p2.price > p3.price if kind == "high" else p1.price < p2.price < p3.price
            if not descending:
                continue
            _, line = _line(p1, p3)
            tol = TRENDLINE_ATR * atr_values[p2.index]
            if not np.isfinite(tol) or abs(p2.price - line(p2.index)) > tol:
                continue
            seg = np.arange(p1.index, p3.index + 1)
            if kind == "high" and (high[seg] > np.array([line(i) for i in seg]) + TRENDLINE_ATR * atr_values[seg]).any():
                continue
            if kind == "low" and (low[seg] < np.array([line(i) for i in seg]) - TRENDLINE_ATR * atr_values[seg]).any():
                continue
            anchors = (p1.index, p2.index, p3.index)
            nxt = same[k + 1].known_at if k + 1 < len(same) else len(close)
            for i in range(p3.known_at, min(len(close), nxt + 1)):
                broke = close[i] > line(i) if kind == "high" else close[i] < line(i)
                if not broke:
                    continue
                if side == "bear":
                    out.append(Figure("TRENDLINE", "bear", i, anchors))
                    break
                after = np.arange(p3.index, i + 1)
                entry = line(i)
                stop = float(low[after].min()) - STOP_ATR * atr_values[i]
                lowest = p1.index + int(np.argmin(low[p1.index:i + 1]))
                height = line(lowest) - float(low[lowest])
                out.append(Figure("TRENDLINE", "bull", i, anchors, entry=entry, stop=stop,
                                  targets=(entry + height / 3, entry + 2 * height / 3, entry + height),
                                  notes={"height": height}))
                break
    return out


# --- ICT/SMC (§ 9.4) --------------------------------------------------------------------------------------------

def ict_setups(open_, high, low, close, atr_values) -> list[Figure]:
    """Sweep haussier puis MSS haussier dans les 10 bougies ; entrée au haut du FVG du MSS. Symétrique : baissier."""
    h, lo, c = (np.asarray(a, dtype=float) for a in (high, low, close))
    sweeps = smc.liquidity_sweeps(h, lo, c)
    breaks = [b for b in smc.structure(h, lo, c, atr_values) if b.mss]
    gaps = smc.fair_value_gaps(h, lo, atr_values)
    out: list[Figure] = []
    used: set[int] = set()
    for brk in breaks:
        sweep = next((s for s in reversed(sweeps) if s.side == brk.side and brk.index - ICT_WINDOW <= s.index < brk.index), None)
        if sweep is None or brk.index in used:
            continue
        gap = next((g for g in reversed(gaps) if g.side == brk.side and brk.pivot_index < g.index <= brk.index), None)
        if gap is None:
            continue
        used.add(brk.index)
        anchors = (sweep.index, brk.pivot_index, gap.index, brk.index)
        if brk.side == "bear":
            out.append(Figure("ICT", "bear", brk.index, anchors, zone=(gap.bottom, gap.top)))
            continue
        entry = gap.top
        stop = float(lo[sweep.index]) - STOP_ATR * atr_values[brk.index]
        risk = entry - stop
        out.append(Figure("ICT", "bull", brk.index, anchors, entry=entry, stop=stop,
                          targets=(entry + risk, entry + 2 * risk, entry + 3 * risk), zone=(gap.bottom, gap.top)))
    return out


# --- Ensemble ---------------------------------------------------------------------------------------------------

def detect(open_, high, low, close, *, m: float) -> list[Figure]:
    """Toutes les familles sur une unité de temps (ZigZag de seuil m × ATR) ; triées par bougie de détection."""
    o, h, lo, c = (np.asarray(a, dtype=float) for a in (open_, high, low, close))
    a = atr(h, lo, c)
    pivots = zigzag(h, lo, a, m)
    figures = (harmonics(pivots, c, a) + abcd(pivots, c, a) + triangles(pivots, h, lo, c)
               + trendlines(pivots, h, lo, c, a) + ict_setups(o, h, lo, c, a) + classics(pivots, c, a, m=m))
    return sorted(figures, key=lambda f: (f.detected_at, f.family, f.anchors))


# --- Figures chartistes classiques (§ 9.6) ------------------------------------------------------------------------

def _breakout(signed_close: np.ndarray, level, start: int, stop: int) -> int | None:
    """Première bougie de [start ; stop] dont la clôture (dans le sens de la figure) passe au-dessus de `level`
    (constante, ou fonction de l'indice pour une ligne oblique)."""
    for i in range(start, min(len(signed_close), stop + 1)):
        if signed_close[i] > (level(i) if callable(level) else level):
            return i
    return None


def _v(p: Pivot, sign: int) -> float:
    """Prix dans le sens de la figure (changé de signe pour la version baissière)."""
    return sign * p.price


def _classic(family: str, sign: int, i: int, anchors: tuple[int, ...], entry: float, stop: float, height: float,
             notes: dict) -> Figure:
    if sign < 0:
        return Figure(family, "bear", i, anchors, notes=notes)
    return Figure(family, "bull", i, anchors, entry=entry, stop=stop,
                  targets=(entry + height / 3, entry + 2 * height / 3, entry + height), notes=notes)


def classics(pivots: list[Pivot], close: np.ndarray, atr_values: np.ndarray, *, m: float) -> list[Figure]:
    """Tête-épaules, double creux, drapeau/fanion et coupe avec anse sur des pivots ZigZag consécutifs ; version
    baissière en miroir (prix changés de signe), inscrite sans niveaux."""
    close = np.asarray(close, dtype=float)
    out: list[Figure] = []
    n = len(pivots)
    alternating = [k == 0 or pivots[k].kind != pivots[k - 1].kind for k in range(n)]
    for k in range(n):
        last = pivots[k]
        sign = 1 if last.kind == "low" else -1       # haussière : la figure finit sur un creux (anse, épaule, repli)
        if not all(alternating[max(1, k - 4):k + 1]):
            continue                                  # pivots non alternés (jamais avec le ZigZag) : aucune figure
        next_known = pivots[k + 1].known_at if k + 1 < n else len(close)
        known = last.known_at
        atr_last = float(atr_values[last.index]) if np.isfinite(atr_values[last.index]) else np.nan
        signed = sign * close

        # Tête-épaules (inverse en haussier) : L1 H1 L2 H2 L3
        if k >= 4 and np.isfinite(atr_last):
            l1, h1, l2, h2, l3 = pivots[k - 4:k + 1]
            slope = (_v(h2, sign) - _v(h1, sign)) / (h2.index - h1.index)
            neck_base = _v(h1, sign)

            def neck(i, base=neck_base, at=h1.index, slope=slope):
                return base + slope * (i - at)
            depth = neck(l2.index) - _v(l2, sign)
            left, right = l2.index - l1.index, l3.index - l2.index
            head, shoulders = _v(l2, sign), (_v(l1, sign), _v(l3, sign))
            if (head < min(shoulders) and depth > 0 and abs(shoulders[0] - shoulders[1]) <= HS_SHOULDERS * depth
                    and left > 0 and HS_SYMMETRY[0] <= right / left <= HS_SYMMETRY[1]):
                i = _breakout(signed, neck, known, next_known)
                if i is not None:
                    entry = sign * neck(i)
                    out.append(_classic("HEAD_SHOULDERS", sign, i, (l1.index, h1.index, l2.index, h2.index, l3.index),
                                        entry, l3.price - STOP_ATR * atr_last, depth, {"depth": depth}))
        # Double creux / sommet : L1 H L2
        if k >= 2 and np.isfinite(atr_last):
            l1, top, l2 = pivots[k - 2:k + 1]
            height = _v(top, sign) - min(_v(l1, sign), _v(l2, sign))
            if (height > 0 and abs(_v(l1, sign) - _v(l2, sign)) <= DOUBLE_EQUAL * height
                    and l2.index - l1.index >= DOUBLE_MIN_BARS):
                i = _breakout(signed, _v(top, sign), known, next_known)
                if i is not None:
                    low = min(l1.price, l2.price) if sign > 0 else max(l1.price, l2.price)
                    out.append(_classic("DOUBLE", sign, i, (l1.index, top.index, l2.index), top.price,
                                        low - STOP_ATR * atr_last, height, {"height": height}))
        # Drapeau / fanion : P0 P1 P2 (mât P0 → P1, repli P1 → P2)
        if k >= 2 and np.isfinite(atr_last):
            p0, p1, p2 = pivots[k - 2:k + 1]
            pole = _v(p1, sign) - _v(p0, sign)
            pole_bars = p1.index - p0.index
            if (pole >= FLAG_POLE_M * m * atr_last and 0 < pole_bars <= FLAG_POLE_BARS
                    and _v(p1, sign) - _v(p2, sign) <= FLAG_RETRACE * pole and p2.index - p1.index <= FLAG_DURATION * pole_bars):
                i = _breakout(signed, _v(p1, sign), known, next_known)
                if i is not None:
                    out.append(_classic("FLAG", sign, i, (p0.index, p1.index, p2.index), p1.price,
                                        p2.price - STOP_ATR * atr_last, pole, {"pole": pole, "pole_bars": pole_bars}))
        # Coupe avec anse : H1 L1 H2 L2
        if k >= 3 and np.isfinite(atr_last):
            h1, l1, h2, l2 = pivots[k - 3:k + 1]
            depth = min(_v(h1, sign), _v(h2, sign)) - _v(l1, sign)
            width = h2.index - h1.index
            margin = (1 - CUP_CENTER) / 2 * width
            if (depth > 0 and abs(_v(h1, sign) - _v(h2, sign)) <= CUP_RIMS * depth
                    and h1.index + margin <= l1.index <= h2.index - margin
                    and _v(l2, sign) >= _v(l1, sign) + CUP_HANDLE_DEPTH * depth and width >= CUP_LENGTH * (l2.index - h2.index)):
                rim = max(_v(h1, sign), _v(h2, sign))
                i = _breakout(signed, rim, known, next_known)
                if i is not None:
                    out.append(_classic("CUP_HANDLE", sign, i, (h1.index, l1.index, h2.index, l2.index), sign * rim,
                                        l2.price - STOP_ATR * atr_last, depth, {"depth": depth}))
    return out

