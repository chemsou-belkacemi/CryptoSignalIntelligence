"""Détecteur PUR des cinq configurations « price action » (docs/PRICE_ACTION.md § 2), partagé par l'étude historique
(`research/price_action_study.py`), le contrôle sous H0 (`research/price_action_h0.py`) et le test en direct F19
(`forward/f19.py`). Long seulement.

Entrée : bougies 1 h CLÔTURÉES d'une paire (open_time, open, high, low, close, quote_volume) ; 4 h et 1 jour sont
agrégés depuis 00:00 UTC, blocs complets seulement. Sortie : des candidats (configuration, paire, instant de décision
`at` = une clôture, entrée à cette clôture, stop de clôture, objectif) et des refus de géométrie. Un candidat d'instant
`at` n'utilise que des bougies clôturées au plus tard à `at` : couper l'historique à `t`, ou falsifier les bougies
postérieures à `t`, ne change aucun candidat d'instant ≤ t (tests/test_price_action.py). Les paramètres sont déclarés A
PRIORI (tableau de la doc) ; aucun n'a été réglé sur un résultat. Aucune entrée-sortie ici.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..patterns.indicators import ema
from ..patterns.primitives import atr

HOUR_NS = 3_600_000_000_000
H4_NS = 4 * HOUR_NS
DAY_NS = 24 * HOUR_NS

# --- Configurations ---------------------------------------------------------------------------------------------------
BASE_RETEST, SQUEEZE, FORCE_RELATIVE, INSIDE_DAY, LONG_BASE = (
    "BASE_RETEST", "SQUEEZE", "FORCE_RELATIVE", "INSIDE_DAY", "SORTIE_BASE_LONGUE")
CONFIGS = (BASE_RETEST, SQUEEZE, FORCE_RELATIVE, INSIDE_DAY, LONG_BASE)
PAIR_CONFIGS = (BASE_RETEST, SQUEEZE, INSIDE_DAY, LONG_BASE)          # détectées paire par paire
UNIT = {BASE_RETEST: "4h", SQUEEZE: "4h", FORCE_RELATIVE: "4h", INSIDE_DAY: "1d", LONG_BASE: "1d"}

# --- Paramètres déclarés (docs/PRICE_ACTION.md § 2, tableau) -------------------------------------------------------------
ATR_PERIOD = 14                    # ATR de Wilder, 4 h et journalier
EMA_FAST, EMA_SLOW = 20, 50        # tendance haussière journalière : clôture > EMA50 et EMA20 > EMA50
MIN_DAYS = 60                      # journées complètes exigées pour lire la tendance
VOLUME_BARS = 20                   # volume quote comparé à la moyenne des 20 bougies précédentes
VOLUME_MULT = 1.5                  # … et doit la dépasser de 50 %

BR_MIN_BARS, BR_MAX_BARS = 10, 180     # base : au moins 10 bougies 4 h, étendue en arrière jusqu'à 180 (30 jours)
BR_HEIGHT_ATR_D = 1.5                  # hauteur de la base ≤ 1,5 × ATR14 journalier
BR_RETEST_BARS = 10                    # retest et entrée dans les 10 bougies 4 h qui suivent la cassure
BR_RETEST_ATR4 = 0.25                  # retest : plus bas ≤ haut de base + 0,25 × ATR 4 h
BR_STOP_ATR4 = 0.25                    # stop = haut de base − 0,25 × ATR 4 h
BR_MIN_TARGET_R = 1.5                  # objectif = haut de base + hauteur ; refus sous +1,5 R

SQ_PERIOD = 20                         # Bollinger (20, 2 écarts-types) et Keltner (EMA20 ± 1,5 × ATR20)
SQ_BB_K, SQ_KC_K = 2.0, 1.5
SQ_MIN_BARS = 6                        # compression : au moins 6 bougies 4 h
SQ_TARGET_R = 2.0

FR_MARKET = "BTCUSDT"
FR_DROP = -0.05                        # BTC perd ≥ 5 % sur 24 h glissantes (clôtures 1 h)
FR_WINDOW_H = 24
FR_PRE_DAYS = 10                       # « a tenu » : jamais de clôture 4 h sous le plus bas des 10 jours d'avant
FR_MIN_PRE_BARS = 200                  # au moins 200 bougies 1 h sur ces 10 jours (240 possibles)
FR_STOP_ATR4 = 0.25                    # stop = plus bas de la paire pendant la chute − 0,25 × ATR 4 h
FR_TARGET_R = 2.0
FR_MAX_PAIRS = 3                       # au plus 3 paires par événement : les plus faibles baisses
FR_MAX_FALL_DAYS = 30                  # garde technique : événement abandonné sans stabilisation en 30 jours

ID_ENTRY_HOURS = 48                    # entrée : première clôture 4 h au-dessus de la mère dans les 48 h
ID_MAX_STOP_ATR_D = 3.0                # refus si entrée − stop > 3 × ATR14 journalier
ID_TARGET_R = 2.0

LB_MIN_DAYS, LB_MAX_DAYS = 30, 180     # base longue : au moins 30 jours, étendue en arrière jusqu'à 180
LB_MAX_HEIGHT = 0.25                   # hauteur ≤ 25 % de la clôture du dernier jour de la base
LB_HIGH_DAYS = 90                      # clôture au-dessus du plus haut des 90 jours précédents
LB_MIN_TARGET_R = 2.0                  # objectif = haut de base + hauteur, porté à +2 R au moins

# --- Refus de géométrie --------------------------------------------------------------------------------------------------
TARGET_TOO_CLOSE = "OBJECTIF_INSUFFISANT"
STOP_TOO_WIDE = "STOP_TROP_LARGE"
BAD_GEOMETRY = "GEOMETRIE_INVALIDE"

COLUMNS = ["open_time", "open", "high", "low", "close", "quote_volume"]


# --- Bougies ---------------------------------------------------------------------------------------------------------------

def to_ns(values) -> np.ndarray:
    """Horodatages UTC → entiers en nanosecondes depuis l'époque (pandas 3 : résolution forcée en ns)."""
    return pd.DatetimeIndex(pd.to_datetime(values, utc=True)).as_unit("ns").asi8


def stamp(value_ns: int) -> pd.Timestamp:
    return pd.Timestamp(int(value_ns), unit="ns", tz="UTC")


class Frame:
    """Bougies d'une unité en tableaux numpy : ouverture `t` (ns), o, h, l, c, v (volume quote), pas `step` (ns)."""

    __slots__ = ("c", "h", "l", "o", "step", "t", "v")

    def __init__(self, t, o, h, l, c, v, step: int):  # noqa: E741 - l = plus bas, comme o/h/c
        self.t, self.o, self.h, self.l, self.c, self.v, self.step = t, o, h, l, c, v, step

    def __len__(self) -> int:
        return len(self.t)

    @property
    def close_t(self) -> np.ndarray:
        return self.t + self.step


def hourly(h1: pd.DataFrame) -> Frame:
    """Bougies 1 h triées, sans doublon (la première gardée)."""
    if h1 is None or len(h1) == 0:
        empty = np.array([], dtype=float)
        return Frame(np.array([], dtype="int64"), empty, empty, empty, empty, empty, HOUR_NS)
    t = to_ns(h1["open_time"])
    order = np.argsort(t, kind="stable")
    t = t[order]
    keep = np.r_[True, t[1:] != t[:-1]]
    pick = order[keep]

    def col(name: str) -> np.ndarray:
        return np.asarray(h1[name], dtype=float)[pick]
    return Frame(t[keep], col("open"), col("high"), col("low"), col("close"), col("quote_volume"), HOUR_NS)


def aggregate(hours: Frame, step: int) -> Frame:
    """Agrégation 4 h ou 1 jour alignée sur 00:00 UTC ; un bloc n'est gardé que s'il contient toutes ses heures."""
    if len(hours) == 0:
        return Frame(hours.t, hours.o, hours.h, hours.l, hours.c, hours.v, step)
    bucket = hours.t - hours.t % step
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    counts = np.diff(np.r_[starts, len(bucket)])
    ends = starts + counts - 1
    keep = counts == step // HOUR_NS
    return Frame(bucket[starts][keep], hours.o[starts][keep], np.maximum.reduceat(hours.h, starts)[keep],
                 np.minimum.reduceat(hours.l, starts)[keep], hours.c[ends][keep], np.add.reduceat(hours.v, starts)[keep],
                 step)


def prev_mean(values: np.ndarray, n: int) -> np.ndarray:
    """out[i] = moyenne de values[i−n:i] (les n PRÉCÉDENTES, sans la courante) ; NaN avant n."""
    out = np.full(len(values), np.nan)
    if len(values) > n:
        csum = np.r_[0.0, np.cumsum(values)]
        idx = np.arange(n, len(values))
        out[n:] = (csum[idx] - csum[idx - n]) / n
    return out


def prev_extreme(values: np.ndarray, n: int, *, highest: bool) -> np.ndarray:
    """out[i] = max (ou min) de values[i−n:i] ; NaN avant n."""
    out = np.full(len(values), np.nan)
    if len(values) > n:
        windows = np.lib.stride_tricks.sliding_window_view(values[:-1], n)
        out[n:] = windows.max(axis=1) if highest else windows.min(axis=1)
    return out


class Bars:
    """Bougies 1 h, 4 h et journalières d'une paire, avec les indicateurs lus par les détecteurs (calculés une fois,
    chacun causal : la valeur d'indice k ne dépend que des bougies ≤ k)."""

    def __init__(self, h1: pd.DataFrame):
        self.h1 = hourly(h1)
        self.h4 = aggregate(self.h1, H4_NS)
        self.d1 = aggregate(self.h1, DAY_NS)
        d, f = self.d1, self.h4
        self.atr_d = atr(d.h, d.l, d.c, ATR_PERIOD) if len(d) else np.array([])
        self.ema_fast = ema(d.c, EMA_FAST) if len(d) else np.array([])
        self.ema_slow = ema(d.c, EMA_SLOW) if len(d) else np.array([])
        self.atr4 = atr(f.h, f.l, f.c, ATR_PERIOD) if len(f) else np.array([])

    def day_at(self, t_ns) -> np.ndarray | int:
        """Indice de la dernière journée COMPLÈTE clôturée au plus tard à `t` (−1 s'il n'y en a pas)."""
        return np.searchsorted(self.d1.close_t, t_ns, side="right") - 1

    def uptrend(self, day: int) -> bool:
        """Tendance haussière journalière lue sur la journée `day` : au moins 60 journées complètes, clôture > EMA50
        et EMA20 > EMA50."""
        if day < MIN_DAYS - 1:
            return False
        close, fast, slow = self.d1.c[day], self.ema_fast[day], self.ema_slow[day]
        return bool(np.isfinite(fast) and np.isfinite(slow) and close > slow and fast > slow)


def _candidate(config: str, symbol: str, at_ns: int, entry: float, stop: float, objective: float, detail: dict) -> dict:
    return {"config": config, "symbol": symbol, "at": stamp(at_ns), "at_ns": int(at_ns), "unit": UNIT[config],
            "entry": float(entry), "stop": float(stop), "objective": float(objective), "risk": float(entry - stop),
            "detail": detail}


def _refusal(config: str, symbol: str, at_ns: int, reason: str, detail: str) -> dict:
    return {"config": config, "symbol": symbol, "at": stamp(at_ns), "at_ns": int(at_ns), "reason": reason, "detail": detail}


# --- 1. BASE_RETEST (4 h) ----------------------------------------------------------------------------------------------------

def base_retest(bars: Bars, symbol: str) -> tuple[list[dict], list[dict]]:
    """Base d'au moins 10 bougies 4 h tenant dans 1,5 × ATR14 journalier (étendue en arrière tant qu'elle y tient, 180
    bougies au plus) ; cassure = clôture 4 h au-dessus du haut de la base avec un volume > 1,5 × la moyenne des 20
    précédentes ; retest = dans les 10 bougies suivantes, un plus bas ≤ haut de base + 0,25 × ATR 4 h (ATR de la bougie
    de cassure), sans clôture sous le haut de base ; entrée = première clôture qui repart (clôture > ouverture et >
    clôture précédente) à partir de la bougie du retest, dans ces 10 bougies ; stop = haut de base − 0,25 × ATR 4 h ;
    objectif = haut de base + hauteur, refus sous +1,5 R."""
    f = bars.h4
    n = len(f)
    if n < BR_MIN_BARS + 2:
        return [], []
    close_t = f.close_t
    vm = prev_mean(f.v, VOLUME_BARS)
    hmax = prev_extreme(f.h, BR_MIN_BARS, highest=True)
    lmin = prev_extreme(f.l, BR_MIN_BARS, highest=False)
    days = bars.day_at(close_t)
    atr_d = np.where(days >= 0, bars.atr_d[np.clip(days, 0, None)] if len(bars.atr_d) else np.nan, np.nan)
    limit = BR_HEIGHT_ATR_D * atr_d
    with np.errstate(invalid="ignore"):
        hits = (f.c > hmax) & (f.v > VOLUME_MULT * vm) & (hmax - lmin <= limit) & np.isfinite(bars.atr4)
    out, refused, seen = [], [], set()
    for i in (int(x) for x in np.flatnonzero(hits)):
        top, bottom, size = float(hmax[i]), float(lmin[i]), BR_MIN_BARS
        while size < BR_MAX_BARS and i - size - 1 >= 0:
            new_top, new_bottom = max(top, f.h[i - size - 1]), min(bottom, f.l[i - size - 1])
            if new_top - new_bottom > limit[i]:
                break
            top, bottom, size = new_top, new_bottom, size + 1
        if not f.c[i] > top:
            continue
        a4 = float(bars.atr4[i])
        retest: int | None = None
        entry_k: int | None = None
        for k in range(i + 1, min(n, i + BR_RETEST_BARS + 1)):
            if f.c[k] < top:
                break
            if retest is None and f.l[k] <= top + BR_RETEST_ATR4 * a4:
                retest = k
            if retest is not None and f.c[k] > f.o[k] and f.c[k] > f.c[k - 1]:
                entry_k = k
                break
        if entry_k is None or retest is None or entry_k in seen:
            continue
        seen.add(entry_k)
        entry, height = float(f.c[entry_k]), float(top - bottom)
        stop = float(top - BR_STOP_ATR4 * a4)
        objective = float(top + height)
        at = int(close_t[entry_k])
        if not stop < entry:
            refused.append(_refusal(BASE_RETEST, symbol, at, BAD_GEOMETRY, "stop au-dessus de l'entrée"))
            continue
        risk = entry - stop
        if objective - entry < BR_MIN_TARGET_R * risk:
            refused.append(_refusal(BASE_RETEST, symbol, at, TARGET_TOO_CLOSE,
                                    f"objectif à {(objective - entry) / risk:.2f} R (< {BR_MIN_TARGET_R} R)"))
            continue
        detail = {"base_high": float(top), "base_low": float(bottom), "base_bars": int(size), "atr4": a4,
                  "atr_d": float(atr_d[i]), "breakout_at": stamp(int(close_t[i])).isoformat(),
                  "retest_at": stamp(int(close_t[retest])).isoformat(), "volume_multiple": float(f.v[i] / vm[i])}
        out.append(_candidate(BASE_RETEST, symbol, at, entry, stop, objective, detail))
    return out, refused


# --- 2. SQUEEZE (4 h) --------------------------------------------------------------------------------------------------------

def squeeze_flags(f) -> tuple[np.ndarray, np.ndarray]:
    """(compression, Bollinger haute) : Bollinger (SMA20 ± 2 écarts-types de population) entièrement dans Keltner
    (EMA20 ± 1,5 × ATR20 de Wilder)."""
    n = len(f)
    upper = np.full(n, np.nan)
    flags = np.zeros(n, dtype=bool)
    if n < SQ_PERIOD:
        return flags, upper
    windows = np.lib.stride_tricks.sliding_window_view(f.c, SQ_PERIOD)
    mid = np.full(n, np.nan)
    sd = np.full(n, np.nan)
    mid[SQ_PERIOD - 1:] = windows.mean(axis=1)
    sd[SQ_PERIOD - 1:] = windows.std(axis=1)
    upper = mid + SQ_BB_K * sd
    lower = mid - SQ_BB_K * sd
    centre = ema(f.c, SQ_PERIOD)
    width = atr(f.h, f.l, f.c, SQ_PERIOD)
    with np.errstate(invalid="ignore"):
        flags = (upper < centre + SQ_KC_K * width) & (lower > centre - SQ_KC_K * width)
    return flags, upper


def squeeze(bars: Bars, symbol: str) -> tuple[list[dict], list[dict]]:
    """Tendance haussière journalière ; compression d'au moins 6 bougies 4 h ; sortie = PREMIÈRE clôture 4 h au-dessus
    de la Bollinger haute depuis que la compression a atteint 6 bougies (dans la compression ou juste après), avec un
    volume > 1,5 × la moyenne des 20 précédentes (une première sortie sans volume ne donne rien) ; entrée = cette
    clôture ; stop = plus bas des 6 bougies de compression qui la précèdent ; objectif = +2 R."""
    f = bars.h4
    n = len(f)
    if n < SQ_PERIOD + SQ_MIN_BARS:
        return [], []
    flags, upper = squeeze_flags(f)
    run = np.zeros(n, dtype=int)
    for k in range(n):
        run[k] = (run[k - 1] + 1 if k else 1) if flags[k] else 0
    with np.errstate(invalid="ignore"):
        above = f.c > upper
    seen_above = np.r_[0, np.cumsum(above)]                          # seen_above[k] = sorties avant k
    vm = prev_mean(f.v, VOLUME_BARS)
    close_t = f.close_t
    out, refused = [], []
    for e in range(SQ_MIN_BARS, n):
        if run[e - 1] < SQ_MIN_BARS or not above[e]:
            continue
        start = e - run[e - 1]                                        # première bougie de la compression
        if seen_above[e] - seen_above[start + SQ_MIN_BARS] > 0:       # une sortie plus tôt : celle-ci n'est pas la première
            continue
        if not f.v[e] > VOLUME_MULT * vm[e]:
            continue
        if not bars.uptrend(int(bars.day_at(close_t[e]))):
            continue
        entry = float(f.c[e])
        stop = float(f.l[e - SQ_MIN_BARS:e].min())
        at = int(close_t[e])
        if not stop < entry:
            refused.append(_refusal(SQUEEZE, symbol, at, BAD_GEOMETRY, "stop au-dessus de l'entrée"))
            continue
        risk = entry - stop
        detail = {"squeeze_bars": int(run[e - 1]), "bollinger_high": float(upper[e]), "volume_multiple": float(f.v[e] / vm[e])}
        out.append(_candidate(SQUEEZE, symbol, at, entry, stop, entry + SQ_TARGET_R * risk, detail))
    return out, refused


# --- 3. FORCE_RELATIVE (4 h, marché entier) ------------------------------------------------------------------------------

def force_events(btc: Bars) -> list[dict]:
    """Événements BTC : début = première clôture 1 h où BTCUSDT a perdu ≥ 5 % sur 24 h glissantes (la clôture 1 h
    précédente ne l'était pas) ; chute = de 24 h avant ce début jusqu'à la stabilisation ; plus bas de la chute = plus
    bas des bougies 4 h clôturées après le début de la chute ; stabilisation = première clôture 4 h (au début ou après)
    au-dessus du plus haut de la bougie 4 h qui a fait le plus bas, sans nouveau plus bas. Un début pendant une chute
    en cours en fait partie ; sans stabilisation en 30 jours, l'événement est abandonné. Un événement non encore
    stabilisé (fin des données) n'est pas rendu."""
    h, f = btc.h1, btc.h4
    if len(h) < FR_WINDOW_H + 2 or not len(f):
        return []
    back = np.searchsorted(h.t, h.t - FR_WINDOW_H * HOUR_NS)
    valid = (back < len(h)) & (h.t[np.clip(back, 0, len(h) - 1)] == h.t - FR_WINDOW_H * HOUR_NS)
    ret = np.where(valid, h.c / h.c[np.clip(back, 0, len(h) - 1)] - 1, np.nan)
    with np.errstate(invalid="ignore"):
        falling = ret <= FR_DROP
    before = np.r_[False, falling[:-1] & (h.t[1:] - h.t[:-1] == HOUR_NS)]
    onsets = np.flatnonzero(falling & ~before)
    close4 = f.close_t
    events, resume_after = [], -1
    for i in onsets:
        onset = int(h.t[i] + HOUR_NS)                                  # clôture 1 h qui déclenche
        if onset <= resume_after:
            continue
        fall_start = onset - FR_WINDOW_H * HOUR_NS
        cap = onset + FR_MAX_FALL_DAYS * DAY_NS
        low, low_bar, stabilized, abandoned = np.inf, -1, None, False
        for j in range(int(np.searchsorted(close4, fall_start, side="right")), len(f)):
            if close4[j] > cap:
                abandoned = True
                break
            if f.l[j] < low:
                low, low_bar = float(f.l[j]), j
            if close4[j] >= onset and f.c[j] > f.h[low_bar]:
                stabilized = int(close4[j])
                break
        if stabilized is None:
            if abandoned:
                resume_after = cap
                continue
            break                                                      # chute en cours à la fin des données
        events.append({"onset": onset, "fall_start": fall_start, "stabilized": stabilized, "btc_low": low,
                       "low_bar_close": int(close4[low_bar]), "btc_drop_24h": float(ret[i])})
        resume_after = stabilized
    return events


def force_pair(bars: Bars, event: dict) -> dict | None:
    """Lecture d'une paire pendant un événement BTC : None si les données manquent (bougie 1 h de référence au début de
    la chute, moins de 200 bougies 1 h sur les 10 jours d'avant, une bougie 4 h de la chute, ou celle de la
    stabilisation). `held` : aucune clôture 4 h de la chute sous le plus bas des 10 jours d'avant ; `drop` : plus bas
    de la chute / clôture au début de la chute − 1 ; entrée = clôture 4 h à la stabilisation ; stop = plus bas de la
    chute − 0,25 × ATR 4 h ; objectif = +2 R."""
    h, f = bars.h1, bars.h4
    start, end = event["fall_start"], event["stabilized"]
    lo, hi = np.searchsorted(h.t, [start - FR_PRE_DAYS * DAY_NS, start])
    if hi - lo < FR_MIN_PRE_BARS:
        return None
    ref = np.searchsorted(h.t, start - HOUR_NS)
    if ref >= len(h) or h.t[ref] != start - HOUR_NS:
        return None
    pre_low = float(h.l[lo:hi].min())
    a, b = np.searchsorted(h.t, [start, end])
    if b <= a:
        return None
    fall_low = float(h.l[a:b].min())
    first = start - start % H4_NS + H4_NS                               # première clôture 4 h après le début de la chute
    expected = np.arange(first, end + 1, H4_NS)
    p, q = np.searchsorted(f.close_t, [first, end + 1])
    if q - p != len(expected) or not np.array_equal(f.close_t[p:q], expected):
        return None
    last = q - 1
    a4 = float(bars.atr4[last])
    if not np.isfinite(a4):
        return None
    entry = float(f.c[last])
    stop = fall_low - FR_STOP_ATR4 * a4
    if not 0 < stop < entry:
        return None
    return {"held": bool((f.c[p:q] >= pre_low).all()), "drop": fall_low / float(h.c[ref]) - 1, "entry": entry,
            "stop": float(stop), "objective": float(entry + FR_TARGET_R * (entry - stop)), "pre_low": pre_low,
            "fall_low": fall_low, "atr4": a4}


def force_candidate(event: dict, symbol: str, reading: dict, *, rank: int = 0, held_pairs: int = 0) -> dict:
    """Candidat FORCE_RELATIVE d'une paire qui a tenu (entrée à la stabilisation)."""
    detail = {"event_onset": stamp(event["onset"]).isoformat(), "fall_start": stamp(event["fall_start"]).isoformat(),
              "btc_low": event["btc_low"], "btc_drop_24h": event["btc_drop_24h"], "pair_drop": reading["drop"],
              "pre_low": reading["pre_low"], "fall_low": reading["fall_low"], "atr4": reading["atr4"], "rank": rank,
              "held_pairs": held_pairs}
    return _candidate(FORCE_RELATIVE, symbol, event["stabilized"], reading["entry"], reading["stop"], reading["objective"],
                      detail)


def force_select(event: dict, readings: dict[str, dict | None]) -> list[dict]:
    """Au plus 3 paires qui ont tenu, celles dont la baisse pendant la chute est la plus faible (égalité : ordre
    alphabétique) ; BTCUSDT n'est jamais candidate (c'est la référence)."""
    held = [(s, r) for s, r in readings.items() if r is not None and r["held"] and s != FR_MARKET]
    held.sort(key=lambda item: (-item[1]["drop"], item[0]))
    return [force_candidate(event, symbol, r, rank=rank, held_pairs=len(held))
            for rank, (symbol, r) in enumerate(held[:FR_MAX_PAIRS], start=1)]


# --- 4. INSIDE_DAY (1 jour) ----------------------------------------------------------------------------------------------

def inside_day(bars: Bars, symbol: str) -> tuple[list[dict], list[dict]]:
    """Tendance haussière journalière lue sur la journée intérieure ; journée intérieure (plus haut ≤ celui de la veille,
    plus bas ≥ celui de la veille, deux journées consécutives) ; entrée = première clôture 4 h au-dessus du plus haut de
    la mère dans les 48 h qui suivent la clôture de la journée intérieure ; stop = plus bas de la mère (stop de clôture
    journalière) ; refus si entrée − stop > 3 × ATR14 journalier ; objectif = +2 R."""
    d, f = bars.d1, bars.h4
    if len(d) < MIN_DAYS + 1 or not len(f):
        return [], []
    close4 = f.close_t
    out, refused, seen = [], [], set()
    inside = np.r_[False, (d.h[1:] <= d.h[:-1]) & (d.l[1:] >= d.l[:-1]) & (d.t[1:] - d.t[:-1] == DAY_NS)]
    for day in np.flatnonzero(inside):
        if not bars.uptrend(int(day)):
            continue
        mother_high, mother_low = float(d.h[day - 1]), float(d.l[day - 1])
        closed_at = int(d.t[day] + DAY_NS)
        p, q = np.searchsorted(close4, [closed_at, closed_at + ID_ENTRY_HOURS * HOUR_NS], side="right")
        above = np.flatnonzero(f.c[p:q] > mother_high)
        if not len(above):
            continue
        k = p + int(above[0])
        at = int(close4[k])
        if at in seen:
            continue
        seen.add(at)
        entry = float(f.c[k])
        risk = entry - mother_low
        atr_d = float(bars.atr_d[day])
        if not np.isfinite(atr_d) or risk > ID_MAX_STOP_ATR_D * atr_d:
            refused.append(_refusal(INSIDE_DAY, symbol, at, STOP_TOO_WIDE,
                                    f"entrée − stop = {risk / atr_d if atr_d else float('inf'):.2f} ATR journaliers (> 3)"))
            continue
        detail = {"inside_day": stamp(d.t[day]).isoformat(), "mother_high": mother_high, "mother_low": mother_low,
                  "atr_d": atr_d}
        out.append(_candidate(INSIDE_DAY, symbol, at, entry, mother_low, entry + ID_TARGET_R * risk, detail))
    return out, refused


# --- 5. SORTIE_BASE_LONGUE (1 jour) --------------------------------------------------------------------------------------

def long_base(bars: Bars, symbol: str) -> tuple[list[dict], list[dict]]:
    """Base d'au moins 30 journées dont la hauteur (plus haut − plus bas) ≤ 25 % de la clôture de son dernier jour,
    étendue en arrière tant qu'elle y tient (180 jours au plus) ; entrée = clôture journalière au-dessus du plus haut des
    90 journées précédentes ET du haut de la base, avec un volume > 1,5 × la moyenne des 20 précédentes ; stop =
    clôture journalière sous le milieu de la base ; objectif = haut de base + hauteur, porté à entrée + 2 R au moins."""
    d = bars.d1
    n = len(d)
    if n < LB_HIGH_DAYS + 1:
        return [], []
    high90 = prev_extreme(d.h, LB_HIGH_DAYS, highest=True)
    vm = prev_mean(d.v, VOLUME_BARS)
    with np.errstate(invalid="ignore"):
        hits = (d.c > high90) & (d.v > VOLUME_MULT * vm)
    out, refused = [], []
    for b in np.flatnonzero(hits):
        limit = LB_MAX_HEIGHT * float(d.c[b - 1])
        top = float(d.h[b - LB_MIN_DAYS:b].max())
        bottom = float(d.l[b - LB_MIN_DAYS:b].min())
        if top - bottom > limit:
            continue
        size = LB_MIN_DAYS
        while size < LB_MAX_DAYS and b - size - 1 >= 0:
            new_top, new_bottom = max(top, d.h[b - size - 1]), min(bottom, d.l[b - size - 1])
            if new_top - new_bottom > limit:
                break
            top, bottom, size = float(new_top), float(new_bottom), size + 1
        entry = float(d.c[b])
        if not entry > top:
            continue
        middle = (top + bottom) / 2
        at = int(d.t[b] + DAY_NS)
        risk = entry - middle
        if not risk > 0:
            refused.append(_refusal(LONG_BASE, symbol, at, BAD_GEOMETRY, "milieu de base au-dessus de l'entrée"))
            continue
        objective = max(top + (top - bottom), entry + LB_MIN_TARGET_R * risk)
        detail = {"base_high": top, "base_low": bottom, "base_days": int(size), "high_90d": float(high90[b]),
                  "volume_multiple": float(d.v[b] / vm[b]), "measured_target": top + (top - bottom)}
        out.append(_candidate(LONG_BASE, symbol, at, entry, middle, objective, detail))
    return out, refused


DETECTORS = {BASE_RETEST: base_retest, SQUEEZE: squeeze, INSIDE_DAY: inside_day, LONG_BASE: long_base}


def params() -> dict:
    """Paramètres déclarés du détecteur (constantes en majuscules du module, hors unités de temps et fonctions)."""
    return {name: value for name, value in sorted(globals().items())
            if name.isupper() and not name.endswith("_NS") and isinstance(value, int | float | str | tuple)
            or name == "UNIT"}


def scan_pair(bars: Bars, symbol: str, configs=PAIR_CONFIGS) -> tuple[list[dict], list[dict]]:
    """Candidats et refus des configurations paire par paire (toutes sauf FORCE_RELATIVE), triés par instant."""
    candidates, refusals = [], []
    for config in configs:
        found, refused = DETECTORS[config](bars, symbol)
        candidates += found
        refusals += refused
    candidates.sort(key=lambda c: (c["at_ns"], CONFIGS.index(c["config"])))
    return candidates, refusals
