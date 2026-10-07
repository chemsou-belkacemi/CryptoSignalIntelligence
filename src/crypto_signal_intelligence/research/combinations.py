"""Briques, votes et déclencheurs du programme « combinaisons » (docs/COMBINAISONS.md, déclaré le 2026-10-07 avant
tout code ; relu deux fois par `leak-auditor`).

Fonctions pures sur des bougies 1 h CLÔTURÉES d'une paire (et de BTCUSDT pour l'état `BTC_HAUSSIER`) : ni
téléchargement, ni simulation, ni résultat. Chaque colonne de la table des briques à la ligne `t` ne lit que des
bougies d'indice ≤ `t` (les valeurs « de référence » ne lisent que des bougies d'indice < `t`) ; BTC est jointe vers le
passé sur `available_at`. Les mutations (`mutation=`) n'existent que pour les tests : chacune réintroduit une fuite
déclarée au § 1.8 et doit être détectée.

Conventions de mise en œuvre (inscrites dans l'historique du protocole, aucun résultat vu) :
- `t − 1`, ATR, EMA, pivots et ZigZag se lisent sur la suite des bougies PRÉSENTES (comme F15 et `figures_history`) ;
- les fenêtres de 24, 168 et 720 heures se lisent sur la grille horaire complète depuis la première bougie (une heure
  absente est une bougie absente, comme dans `VOLATILITY.md`) ;
- une brique « événement » vote oui si elle s'est produite dans les 24 dernières heures ; sinon non si elle est
  calculable à `t`, sinon elle est absente.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numba import njit

from ..forward import f15
from ..patterns import volume as pv
from ..patterns.indicators import ema
from ..patterns.primitives import ZIGZAG_M, atr, fractal_pivots, zigzag
from . import figures_history as fh
from .volatility import _window_means

TEST_ID = "COMBINAISONS"
DOC = "docs/COMBINAISONS.md"
NEW_BRICKS = ("VP_POC", "VP_VAL", "AVWAP_RECLAIM", "CVD_DIV", "ABSORPTION")
EVENT_BRICKS = (*NEW_BRICKS, "TRENDLINE")
STATE_BRICKS = ("TENDANCE", "VOL_CALME", "BTC_HAUSSIER")
VOTERS = (*EVENT_BRICKS, *STATE_BRICKS)
HOUR = pd.Timedelta(hours=1)
WINDOW = 720                 # bougies (heures) de la fenêtre de référence : 30 jours
MIN_VALID = 684              # 95 % de 720
PROFILE_DAYS = 30
ABS_MIN_TRADES = 100
ABS_MIN_REFERENCE = 360
ABS_QUANTILE = 0.10
ABS_CLOSE_SHARE = 2 / 3
DIV_MIN, DIV_MAX = 5, 60
EMA_TREND, EMA_BTC = 200, 50
VOL_SHORT, VOL_LONG = 24, 720
VOTE_HOURS = 24
STOP_ATR = 2.0
READ_COLUMNS = ("open_time", "open", "high", "low", "close", "base_volume", "taker_buy_base_volume",
                "number_of_trades", "available_at")
# Colonnes « de référence » : elles ne doivent dépendre d'aucune bougie d'indice ≥ t (contrôle « bougie t falsifiée »).
REFERENCE_COLUMNS = ("s_tilde", "abs_q10", "abs_median", "abs_reference", "poc_high", "val", "profile_bars")
MUTATIONS = ("profil_jour", "cvd_j2", "centrage_t", "absorption_t", "ancre_non_confirmee", "reprise_avant_ancre",
             "btc_open_time")


def _utc(values) -> pd.Series:
    return pd.to_datetime(pd.Series(values), utc=True).dt.as_unit("ns")


def prepare(h1: pd.DataFrame) -> pd.DataFrame:
    """Bougies 1 h triées, sans doublon, colonnes lues par le protocole seulement."""
    frame = h1[list(READ_COLUMNS)].copy()
    frame["open_time"] = _utc(frame["open_time"]).to_numpy()
    frame["available_at"] = _utc(frame["available_at"]).to_numpy()
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
    for column in READ_COLUMNS[1:-1]:
        frame[column] = frame[column].astype(float)
    return frame


@dataclass(frozen=True)
class Grid:
    """Grille horaire complète depuis la première bougie : `pos[i]` = position de la bougie `i` (ligne présente)."""
    start: pd.Timestamp
    size: int
    pos: np.ndarray

    @classmethod
    def of(cls, frame: pd.DataFrame) -> Grid:
        times = frame["open_time"]
        start = pd.Timestamp(times.iloc[0])
        pos = ((times - start) // HOUR).to_numpy(np.int64)
        return cls(start, int(pos[-1]) + 1, pos)

    def spread(self, values: np.ndarray, fill: float = np.nan) -> np.ndarray:
        out = np.full(self.size, fill, dtype=float)
        out[self.pos] = values
        return out


# --- Delta centré (§ 2, correction de la relecture) ------------------------------------------------------------------

def centred_delta(frame: pd.DataFrame, grid: Grid, *, mutation: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(s̃, deltaC) par ligne : s̃_i = Σ TB_j / Σ V_j sur les 720 heures `i − 720 … i − 1` (bougies présentes et V > 0,
    au moins 684), deltaC_i = TB_i − s̃_i · V_i ; NaN si s̃ n'existe pas. Mutation `centrage_t` : fenêtre `i − 719 … i`."""
    v = grid.spread(frame["base_volume"].to_numpy(float), 0.0)
    tb = grid.spread(frame["taker_buy_base_volume"].to_numpy(float), 0.0)
    valid = v > 0
    v, tb = np.where(valid, v, 0.0), np.where(valid, tb, 0.0)
    c_v, c_tb, c_n = (np.concatenate([[0.0], np.cumsum(a)]) for a in (v, tb, valid.astype(float)))
    shift = 1 if mutation == "centrage_t" else 0
    end = grid.pos + shift                                   # fenêtre [end − 720 ; end) en positions de grille
    begin = np.maximum(end - WINDOW, 0)
    count = c_n[end] - c_n[begin]
    total_v = c_v[end] - c_v[begin]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where((count >= MIN_VALID) & (total_v > 0), (c_tb[end] - c_tb[begin]) / total_v, np.nan)
    delta = frame["taker_buy_base_volume"].to_numpy(float) - s * frame["base_volume"].to_numpy(float)
    return s, delta


# --- Profil de volume (§ 2.1) : copie compilée de `patterns.volume.volume_profile`, prouvée identique par test ------

@njit(cache=True)
def _profile_volume(high, low, volume, edges):
    bins = len(edges) - 1
    profile = np.zeros(bins)
    for b in range(len(high)):
        bar_high, bar_low, bar_volume = high[b], low[b], volume[b]
        if bar_high <= bar_low:
            k = np.searchsorted(edges, bar_low, side="right") - 1
            k = min(bins - 1, k)
            profile[max(k, 0)] += bar_volume
            continue
        span = bar_high - bar_low
        for j in range(bins):
            overlap = min(edges[j + 1], bar_high) - max(edges[j], bar_low)
            if overlap < 0:
                overlap = 0.0
            profile[j] += bar_volume * overlap / span
    return profile


def volume_profile_fast(high, low, volume, *, bins: int = pv.PROFILE_BINS, area: float = pv.VALUE_AREA) -> pv.Profile:
    """Même résultat que `patterns.volume.volume_profile` (test d'identité), boucle compilée."""
    h, lo, v = (np.ascontiguousarray(np.asarray(a, dtype=float)) for a in (high, low, volume))
    edges = np.linspace(lo.min(), h.max(), bins + 1)
    profile = _profile_volume(h, lo, v, edges)
    tol = 1e-9 * max(float(profile.max()), 1.0)
    poc = int(np.nonzero(profile >= profile.max() - tol)[0][0])
    low_i = high_i = poc
    total, covered = profile.sum(), profile[poc]
    while covered < area * total - 1e-12 and (low_i > 0 or high_i < bins - 1):
        above = profile[high_i + 1] if high_i < bins - 1 else -1.0
        below = profile[low_i - 1] if low_i > 0 else -1.0
        if above >= below - tol:
            high_i += 1
            covered += above
        else:
            low_i -= 1
            covered += below
    return pv.Profile(edges, profile, (float(edges[poc]), float(edges[poc + 1])), float(edges[low_i]),
                      float(edges[high_i + 1]))


def profile_window(frame: pd.DataFrame, grid: Grid, day: pd.Timestamp, *, shift: int = 0) -> tuple[float, float, int, float]:
    """Profil `P_d` du jour `d` : (borne haute du POC, VAL, bougies présentes, plus grand `available_at` en ns) sur les
    720 heures ouvertes dans `[d − 30 j ; d)` ; NaN si moins de 684 bougies présentes."""
    end = int((pd.Timestamp(day) - grid.start) // HOUR) + shift
    begin = end - PROFILE_DAYS * 24
    lo_i, hi_i = max(begin, 0), max(min(end, grid.size), 0)
    rows = np.arange(np.searchsorted(grid.pos, lo_i), np.searchsorted(grid.pos, hi_i))
    if len(rows) < MIN_VALID:
        return np.nan, np.nan, int(len(rows)), np.nan
    h, lo, v = (frame[k].to_numpy(float)[rows] for k in ("high", "low", "base_volume"))
    p = volume_profile_fast(h, lo, v)
    last = float(frame["available_at"].iloc[rows].astype("int64").max())
    return p.poc[1], p.val, int(len(rows)), last


def daily_profiles(frame: pd.DataFrame, grid: Grid, *, mutation: str | None = None) -> pd.DataFrame:
    """Profil `P_d` de chaque jour UTC `d` qui a des bougies (voir `profile_window`). Mutation `profil_jour` : fenêtre
    décalée d'une heure (elle contient la bougie de 00:00 du jour `d`)."""
    days = pd.DatetimeIndex(frame["open_time"]).floor("D").unique()
    shift = 1 if mutation == "profil_jour" else 0
    rows = [(day, *profile_window(frame, grid, day, shift=shift)) for day in days]
    return pd.DataFrame(rows, columns=["day", "poc_high", "val", "bars", "last_available"])


def _bounce(close: np.ndarray, low: np.ndarray, level: np.ndarray, day: np.ndarray) -> np.ndarray:
    """Rebond sur un niveau : `C_{t−1} > niveau`, `L_t ≤ niveau`, `C_t > niveau` ; le premier du jour seulement."""
    prev = np.r_[np.nan, close[:-1]]
    hit = np.isfinite(level) & (prev > level) & (low <= level) & (close > level)
    out = np.zeros(len(close), dtype=bool)
    seen: set = set()
    for i in np.flatnonzero(hit):
        if day[i] not in seen:
            seen.add(day[i])
            out[i] = True
    return out


# --- VWAP ancré (§ 2.1) ----------------------------------------------------------------------------------------------

def avwap_reclaims(frame: pd.DataFrame, a: np.ndarray, *, mutation: str | None = None) -> dict[str, np.ndarray]:
    """Reprise du VWAP ancré au dernier pivot bas ZigZag (m = 3 ATR) connu. Colonnes : événement, VWAP de l'ancre
    courante, indice de l'ancre, calculable (une ancre est connue)."""
    h, lo, c, v = (frame[k].to_numpy(float) for k in ("high", "low", "close", "base_volume"))
    n = len(c)
    event = np.zeros(n, dtype=bool)
    level = np.full(n, np.nan)
    anchor = np.full(n, -1, dtype=np.int64)
    lows = [p for p in zigzag(h, lo, a, ZIGZAG_M["1h"]) if p.kind == "low"]
    # Mutation `ancre_non_confirmee` (fuite) : l'ancre est le dernier pivot bas par sa DATE, même confirmé plus tard.
    starts = [p.index for p in lows] if mutation == "ancre_non_confirmee" else [p.known_at for p in lows]
    for q, pivot in enumerate(lows):
        begin = starts[q]
        stop = starts[q + 1] if q + 1 < len(lows) else n
        if begin >= stop:
            continue
        vwap = pv.anchored_vwap(h[:stop], lo[:stop], c[:stop], v[:stop], pivot.index)
        level[begin:stop] = vwap[begin:stop]
        anchor[begin:stop] = pivot.index
        first = pivot.index + 1 if mutation == "reprise_avant_ancre" else max(pivot.known_at, begin) + 1
        for t in range(max(first, pivot.index + 1), stop):
            if c[t - 1] <= vwap[t - 1] and c[t] > vwap[t]:
                event[t] = True
                break
    return {"AVWAP_RECLAIM": event, "avwap": level, "avwap_anchor": anchor.astype(float),
            "calc_AVWAP_RECLAIM": anchor >= 0}


# --- Divergence CVD (§ 2.1) ------------------------------------------------------------------------------------------

def cvd_divergences(frame: pd.DataFrame, delta: np.ndarray, *, mutation: str | None = None) -> np.ndarray:
    """Deux pivots bas fractals consécutifs `j1 < j2`, `5 ≤ j2 − j1 ≤ 60`, `L_{j2} < L_{j1}`, Σ deltaC de `j1 + 1` à
    `j2` > 0 (toutes définies) ; connue à `j2 + 2` (mutation `cvd_j2` : datée à `j2`)."""
    h, lo = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
    n = len(lo)
    out = np.zeros(n, dtype=bool)
    lows = [p for p in fractal_pivots(h, lo) if p.kind == "low"]
    for first, second in zip(lows, lows[1:], strict=False):
        gap = second.index - first.index
        if not DIV_MIN <= gap <= DIV_MAX or not second.price < first.price:
            continue
        flow = delta[first.index + 1:second.index + 1]
        if not np.isfinite(flow).all() or not flow.sum() > 0:
            continue
        at = second.index if mutation == "cvd_j2" else second.known_at
        if at < n:
            out[at] = True
    return out


# --- Absorption (§ 2.1) ----------------------------------------------------------------------------------------------

def absorption(frame: pd.DataFrame, grid: Grid, *, mutation: str | None = None, chunk: int = 4096) -> dict[str, np.ndarray]:
    """Forte vente au marché sans baisse. Référence : bougies valides (N ≥ 100, V > 0) parmi les 720 heures
    `t − 720 … t − 1`, au moins 360 (sinon absente). Le centile et la médiane (`numpy.nanquantile` / `nanmedian`,
    méthode par défaut) ne sont calculés qu'aux bougies candidates (N ≥ 100, V > 0, H > L, clôture dans le tiers haut) :
    même résultat, moins de calcul. Mutation `absorption_t` : la référence contient la bougie `t`."""
    h, lo, c, v, tb, trades = (frame[k].to_numpy(float) for k in ("high", "low", "close", "base_volume",
                                                                   "taker_buy_base_volume", "number_of_trades"))
    gv, gtb, gn = grid.spread(v), grid.spread(tb), grid.spread(trades)
    valid = np.isfinite(gv) & (gn >= ABS_MIN_TRADES) & (gv > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(valid, gtb / gv, np.nan)
        own = np.where(v > 0, tb / v, np.nan)
        place = np.where(h > lo, (c - lo) / (h - lo), np.nan)
    volume = np.where(valid, gv, np.nan)
    shift = 1 if mutation == "absorption_t" else 0
    cum = np.concatenate([[0], np.cumsum(valid)])
    end = np.minimum(grid.pos + shift, grid.size)          # fenêtre [end − 720 ; end) en positions de grille
    count = (cum[end] - cum[np.maximum(end - WINDOW, 0)]).astype(float)
    candidate = (count >= ABS_MIN_REFERENCE) & (trades >= ABS_MIN_TRADES) & (v > 0) & (h > lo) & (place >= ABS_CLOSE_SHARE)
    pad = np.full(WINDOW, np.nan)
    share_p, volume_p = np.r_[pad, share, np.nan], np.r_[pad, volume, np.nan]
    q10, med = np.full(len(c), np.nan), np.full(len(c), np.nan)
    rows = np.flatnonzero(candidate)
    for begin in range(0, len(rows), chunk):
        idx = rows[begin:begin + chunk]
        starts = end[idx]                                  # positions dans la série complétée de WINDOW NaN
        windows = np.lib.stride_tricks.sliding_window_view(share_p, WINDOW)[starts]
        vols = np.lib.stride_tricks.sliding_window_view(volume_p, WINDOW)[starts]
        q10[idx] = np.nanquantile(windows, ABS_QUANTILE, axis=1)
        med[idx] = np.nanmedian(vols, axis=1)
    event = candidate & (own <= q10) & (v >= med)
    return {"ABSORPTION": event, "abs_q10": q10, "abs_median": med, "abs_reference": count,
            "calc_ABSORPTION": count >= ABS_MIN_REFERENCE}


# --- États (§ 2.2) ---------------------------------------------------------------------------------------------------

def calm_volatility(frame: pd.DataFrame, grid: Grid) -> np.ndarray:
    """1.0 si la variance horaire moyenne des 24 dernières heures ≤ celle des 720 dernières (95 % couvertes),
    0.0 sinon, NaN si non calculable."""
    close = grid.spread(frame["close"].to_numpy(float))
    log_close = np.log(np.where(close > 0, close, np.nan))
    squared = np.full(grid.size, np.nan)
    squared[1:] = np.diff(log_close) ** 2
    short = _window_means(squared, grid.pos - VOL_SHORT + 1, VOL_SHORT)
    long = _window_means(squared, grid.pos - VOL_LONG + 1, VOL_LONG)
    return np.where(np.isfinite(short) & np.isfinite(long), (short <= long).astype(float), np.nan)


def btc_daily(btc: pd.DataFrame, *, mutation: str | None = None) -> pd.DataFrame:
    """Clôtures journalières de BTC (jours UTC complets de 24 bougies), EMA50 de ces clôtures, état haussier, connu à
    l'`available_at` de la bougie de 23:00 (mutation `btc_open_time` : daté à l'ouverture du jour, fuite)."""
    frame = btc[["open_time", "close", "available_at"]].copy()
    frame["open_time"] = _utc(frame["open_time"]).to_numpy()
    frame["available_at"] = _utc(frame["available_at"]).to_numpy()
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    frame["day"] = frame["open_time"].dt.floor("D")
    days = frame.groupby("day").agg(close=("close", "last"), count=("close", "size"), last=("open_time", "max"),
                                    known=("available_at", "max")).reset_index()
    days = days[(days["count"] == 24) & (days["last"] == days["day"] + pd.Timedelta(hours=23))].reset_index(drop=True)
    days["ema"] = ema(days["close"].to_numpy(float), EMA_BTC)
    days["bull"] = np.where(np.isfinite(days["ema"]), (days["close"] > days["ema"]).astype(float), np.nan)
    days["known_at"] = days["day"] if mutation == "btc_open_time" else days["known"]
    return days[["day", "close", "ema", "bull", "known_at"]]


def join_btc(available_at: pd.Series, daily: pd.DataFrame) -> np.ndarray:
    """État BTC connu à chaque `available_at` (jointure vers le passé, égalité comprise)."""
    left = pd.DataFrame({"at": _utc(available_at).to_numpy(), "row": np.arange(len(available_at))})
    right = daily[["known_at", "bull"]].dropna(subset=["bull"]).copy()
    right["known_at"] = _utc(right["known_at"]).to_numpy()
    if right.empty:
        return np.full(len(left), np.nan)
    merged = pd.merge_asof(left.sort_values("at"), right.sort_values("known_at"), left_on="at", right_on="known_at",
                           direction="backward")
    return merged.sort_values("row")["bull"].to_numpy(float)


def btc_week_up(available_at: pd.Series, btc: pd.DataFrame) -> np.ndarray:
    """Témoin 2 du test « facteur commun » (§ 1.8) : rendement de BTC sur les 168 dernières clôtures 1 h > 0, connu à
    l'`available_at` de la dernière, joint vers le passé."""
    frame = prepare_btc(btc)
    ret = frame["close"].to_numpy(float)
    up = np.full(len(ret), np.nan)
    up[168:] = (ret[168:] > ret[:-168]).astype(float)
    right = pd.DataFrame({"known_at": frame["available_at"].to_numpy(), "up": up}).dropna()
    left = pd.DataFrame({"at": _utc(available_at).to_numpy(), "row": np.arange(len(available_at))})
    if right.empty:
        return np.full(len(left), np.nan)
    merged = pd.merge_asof(left.sort_values("at"), right.sort_values("known_at"), left_on="at", right_on="known_at",
                           direction="backward")
    return merged.sort_values("row")["up"].to_numpy(float)


def prepare_btc(btc: pd.DataFrame) -> pd.DataFrame:
    frame = btc[["open_time", "close", "available_at"]].copy()
    frame["open_time"] = _utc(frame["open_time"]).to_numpy()
    frame["available_at"] = _utc(frame["available_at"]).to_numpy()
    return frame.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)


# --- Table des briques -----------------------------------------------------------------------------------------------

def brick_table(h1: pd.DataFrame, btc: pd.DataFrame | None, *, mutation: str | None = None,
                daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Une ligne par bougie 1 h présente : événements (connus à la clôture de la ligne), calculabilité, états, valeurs
    intermédiaires (pour les contrôles de causalité). `daily` : clôtures journalières de BTC déjà calculées."""
    if mutation is not None and mutation not in MUTATIONS:
        raise ValueError(f"mutation inconnue : {mutation}")
    frame = prepare(h1)
    grid = Grid.of(frame)
    h, lo, c = (frame[k].to_numpy(float) for k in ("high", "low", "close"))
    a = atr(h, lo, c)
    out = pd.DataFrame({"open_time": frame["open_time"], "available_at": frame["available_at"],
                        "at": frame["open_time"] + HOUR, "close": c, "atr": a})
    out["day"] = frame["open_time"].dt.floor("D")
    # Delta centré
    s, delta = centred_delta(frame, grid, mutation=mutation)
    out["s_tilde"], out["delta_c"] = s, delta
    # Profils journaliers ; P_d sert aux bougies du jour d seulement si toutes ses bougies sont disponibles avant elles.
    prof = daily_profiles(frame, grid, mutation=mutation)
    joined = out[["day"]].merge(prof, on="day", how="left")
    usable = joined["last_available"].to_numpy(float) <= frame["available_at"].astype("int64").to_numpy().astype(float)
    out["poc_high"] = np.where(usable, joined["poc_high"], np.nan)
    out["val"] = np.where(usable, joined["val"], np.nan)
    out["profile_bars"] = joined["bars"].to_numpy(float)
    day_key = out["day"].to_numpy()
    out["VP_POC"] = _bounce(c, lo, out["poc_high"].to_numpy(float), day_key)
    out["VP_VAL"] = _bounce(c, lo, out["val"].to_numpy(float), day_key)
    has_prev = np.r_[False, np.ones(len(c) - 1, dtype=bool)] if len(c) else np.zeros(0, dtype=bool)
    out["calc_VP_POC"] = np.isfinite(out["poc_high"].to_numpy(float)) & has_prev
    out["calc_VP_VAL"] = np.isfinite(out["val"].to_numpy(float)) & has_prev
    # VWAP ancré
    for key, values in avwap_reclaims(frame, a, mutation=mutation).items():
        out[key] = values
    # Divergence CVD
    out["CVD_DIV"] = cvd_divergences(frame, delta, mutation=mutation)
    out["calc_CVD_DIV"] = np.isfinite(delta)
    # Absorption
    for key, values in absorption(frame, grid, mutation=mutation).items():
        out[key] = values
    # Ligne de tendance (détecteur de F15, importé sans changement), datée à sa bougie de détection
    trend = np.zeros(len(c), dtype=bool)
    detected = [s_.at for s_ in fh.figure_setups(f15.aggregate(frame, "1h"), "1h", "X") if s_.method == "TRENDLINE"]
    if detected:
        rows = np.searchsorted(out["at"].to_numpy(), pd.DatetimeIndex(detected).as_unit("ns").to_numpy())
        trend[rows[rows < len(c)]] = True
    out["TRENDLINE"] = trend
    out["calc_TRENDLINE"] = np.isfinite(a)
    # États
    e200 = ema(c, EMA_TREND)
    out["ema200"] = e200
    out["TENDANCE"] = np.where(np.isfinite(e200), (c > e200).astype(float), np.nan)
    out["VOL_CALME"] = calm_volatility(frame, grid)
    if daily is None and btc is not None:
        daily = btc_daily(btc, mutation=mutation)
    out["BTC_HAUSSIER"] = (join_btc(frame["available_at"], daily) if daily is not None
                           else np.full(len(c), np.nan))
    return out


def votes(table: pd.DataFrame) -> pd.DataFrame:
    """Votes à chaque ligne (§ 4.1) : 1 / 0 / NaN (absente). Événement : produit dans les heures `t − 23 … t` ;
    état : sa valeur à `t`. `n_votes` = nombre de oui ; `trigger` = au moins un événement à `t`."""
    times = pd.DatetimeIndex(table["open_time"])
    out = pd.DataFrame(index=table.index)
    for brick in EVENT_BRICKS:
        in_window = _window_any(times, table[brick].to_numpy(bool))
        calc = table[f"calc_{brick}"].to_numpy(bool)
        out[brick] = np.where(in_window, 1.0, np.where(calc, 0.0, np.nan))
    for brick in STATE_BRICKS:
        out[brick] = table[brick].to_numpy(float)
    out["n_votes"] = np.nansum(out[list(VOTERS)].to_numpy(float), axis=1)
    out["trigger"] = table[list(EVENT_BRICKS)].to_numpy(bool).any(axis=1)
    return out


def _window_any(times: pd.DatetimeIndex, events: np.ndarray) -> np.ndarray:
    """Vrai si un événement a eu lieu à une heure `h` avec `t − 23 h ≤ h ≤ t` (heures d'ouverture)."""
    ns = times.as_unit("ns").asi8
    event_ns = ns[events]
    if not len(event_ns):
        return np.zeros(len(ns), dtype=bool)
    last = np.searchsorted(event_ns, ns, side="right") - 1          # dernier événement ≤ t
    ok = last >= 0
    out = np.zeros(len(ns), dtype=bool)
    out[ok] = ns[ok] - event_ns[last[ok]] <= (VOTE_HOURS - 1) * HOUR.value
    return out


def setup_for(symbol: str, row: pd.Series | dict, *, method: str = "COMBO") -> fh.Setup:
    """Transaction commune (§ 1.4) d'un déclencheur : achat au marché, stop `C_t − 2 ATR_t` fixe, objectifs à 1, 2 et
    3 R posés à l'exécution. Clé indépendante de la règle : une même heure est la même transaction pour toutes."""
    open_time = pd.Timestamp(row["open_time"])
    key = f"{symbol}:1h:COMBO:bull:{open_time:%Y%m%dT%H%M}"
    close, a = float(row["close"]), float(row["atr"])
    stop = close - STOP_ATR * a if math.isfinite(a) else math.nan
    return fh.Setup(key, method, "1h", open_time + HOUR, stop, valid=bool(math.isfinite(stop)))


def in_period(at: pd.Timestamp, *, first_bar: pd.Timestamp, end: pd.Timestamp) -> bool:
    """`figures_history.in_period` importé tel quel, pour une clôture 1 h."""
    return fh.in_period(fh.Setup("", "", "1h", at, 0.0), first_bar=first_bar, end=end)


def period_mask(table: pd.DataFrame, *, first_bar: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    """Même règle que `in_period`, vectorisée (test d'égalité) : clôture ≥ max(2019-01-01, première bougie + 90 j) et
    clôture + 80 h ≤ fin."""
    at = pd.DatetimeIndex(table["at"])
    low = max(fh.FIRST_DAY, first_bar + fh.WARMUP)
    span = (f15.ORDER_BARS + f15.HOLD_BARS) * HOUR
    return np.asarray((at >= low) & (at + span <= end))


def state_key(table: pd.DataFrame) -> np.ndarray:
    """Case d'états (TENDANCE, VOL_CALME, BTC_HAUSSIER), la valeur absente comptant comme une valeur : « 1-0-A »."""
    parts = []
    for brick in STATE_BRICKS:
        values = table[brick].to_numpy(float)
        parts.append(np.where(np.isnan(values), "A", np.where(values > 0, "1", "0")))
    return np.char.add(np.char.add(np.char.add(parts[0], "-"), np.char.add(parts[1], "-")), parts[2])
