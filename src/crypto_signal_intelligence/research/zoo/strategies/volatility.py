"""Famille Volatilité et range (VO) : 14 tests de forme (a) (docs/ZOO.md § 12.1, docs/ZOO_CATALOGUE.md § 3).

Paramètres standard du recensement, lus dans `ctx.params` (registre `spec.py`) ; fonctions PURES, bougies closes, la
valeur en `t` ne lit que les bougies `≤ t` (vérifié par `causality.py`).

Conventions déclarées (lectures du recensement, aucune n'est un réglage) :
- **journée** : journée UTC de l'heure d'ouverture de la bougie 1 h ; **ouverture du jour** = ouverture de la bougie
  1 h de 00:00 (absente : aucun signal ce jour-là) ;
- **bougies journalières** (VO01, VO02) : `ctx.quotidien` (bougies journalières COMPLÈTES de la paire), jointes à la
  bougie 1 h `t` par leur instant de disponibilité (dernière journée dont l'`available_at` ≤ celui de `t`) ; « la
  veille » doit être exactement la journée `d − 1` (sinon aucun signal) ; Dual Thrust : les `n` journées `d − n` à
  `d − 1` toutes présentes ;
- **« 1re clôture 1 h du jour (de la séance) > niveau »** : la première bougie 1 h de la journée (de la séance) dont la
  clôture dépasse strictement le niveau ; un seul signal par journée (par séance) ;
- **sortie « fin de journée » / « fin de séance »** : la position est vendue à la clôture de la dernière bougie 1 h
  de la journée (de la séance). Le moteur exécute une sortie à la clôture qui SUIT la condition : la condition est
  donc vraie sur l'avant-dernière bougie (exécution à la clôture de la dernière) et sur la dernière (un signal pris
  sur la dernière bougie donne une durée nulle, écartée et comptée par le moteur) ;
- **NR4 / NR7** : journée dont l'amplitude (plus haut − plus bas) est ≤ à celles des `n − 1` journées précédentes
  (bougies journalières consécutives du cadre) ; signal à la première clôture des 2 journées suivantes au-dessus de
  son plus haut ;
- **compression TTM** (Carter, 2005, code public de LazyBear, 2014) : Bollinger SMA 20 ± 2 écarts-types
  (population) ; Keltner SMA 20 ± 1,5 × moyenne simple 20 du vrai range ; compression = Bollinger strictement à
  l'intérieur de Keltner ; momentum = valeur en `t` de la régression linéaire sur 20 bougies de
  `C − ((max H 20 + min L 20) / 2 + SMA 20) / 2` ;
- **cassure de l'ATR** : `C_t > C_{t−1} + k × ATR 14` de `t − 1` (volatilité connue avant la bougie, Wilder) ;
- **momentum intrajournalier** : rendement de l'ouverture de 00:00 à la clôture de la bougie 21:00-22:00.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..data import DAY_H, Bougies, agreger
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({
    "williams_cassure", "dual_thrust", "ouverture_seance", "nr", "squeeze", "cassure_atr",
    "momentum_intrajournalier"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()


# --- Outils de journée (partagés avec mean_reversion) -------------------------------------------------------------

def jour(frame: Bougies) -> np.ndarray:
    """Journée UTC (depuis 1970) de l'ouverture de chaque bougie."""
    return frame.ouverture_h // DAY_H


def heure(frame: Bougies) -> np.ndarray:
    """Heure UTC d'ouverture de chaque bougie (0 à 23)."""
    return frame.ouverture_h % DAY_H


def debut_groupe(groupe: np.ndarray) -> np.ndarray:
    """Indice de la première bougie du groupe consécutif (même journée) de chaque bougie."""
    n = len(groupe)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    starts = np.r_[True, groupe[1:] != groupe[:-1]]
    return np.maximum.accumulate(np.where(starts, np.arange(n), 0))


def cumul_groupe(x: np.ndarray, groupe: np.ndarray) -> np.ndarray:
    """Somme cumulée de `x` à l'intérieur de chaque groupe (NaN dès qu'une valeur du groupe est indéfinie)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x.copy()
    bad = np.isnan(x)
    first = debut_groupe(groupe)
    total = np.cumsum(np.where(bad, 0.0, x))
    base = np.r_[0.0, total[:-1]][first]
    nbad = np.cumsum(bad)
    nbase = np.r_[0, nbad[:-1]][first]
    return np.where(nbad - nbase > 0, np.nan, total - base)


def premier_du_groupe(cond: np.ndarray, groupe: np.ndarray) -> np.ndarray:
    """Vrai à la première bougie du groupe où `cond` est vraie."""
    return cond & (cumul_groupe(cond.astype(np.float64), groupe) == 1)


def ouverture_du_jour(frame: Bougies) -> np.ndarray:
    """Ouverture de la bougie 1 h de 00:00 de la journée de chaque bougie (NaN si cette bougie manque)."""
    first = debut_groupe(jour(frame))
    return np.where(heure(frame)[first] == 0, frame.ouverture[first], np.nan)


def quotidien(frame: Bougies, ctx: Contexte) -> Bougies:
    """Bougies journalières complètes : celles du moteur (`ctx.quotidien`), sinon agrégées du cadre 1 h."""
    return ctx.quotidien if ctx.quotidien is not None else agreger(frame, DAY_H)


def veille(frame: Bougies, daily: Bougies) -> np.ndarray:
    """Indice de la bougie journalière de la VEILLE de chaque bougie 1 h, connue à sa décision (−1 sinon) : dernière
    journée dont l'`available_at` ≤ celui de la bougie 1 h, exigée égale à la journée `d − 1`."""
    if len(daily) == 0:
        return np.full(len(frame), -1, dtype=np.int64)
    j = np.searchsorted(daily.disponible, frame.disponible, side="right") - 1
    ok = j >= 0
    day = np.where(ok, daily.ouverture_h[np.maximum(j, 0)] // DAY_H, -10)
    return np.where(ok & (day == jour(frame) - 1), j, -1).astype(np.int64)


def fin_de_periode(frame: Bougies, fin: int) -> np.ndarray:
    """Condition de sortie « vendre à la clôture de la dernière bougie 1 h qui finit à l'heure `fin` » : vraie sur la
    bougie qui se clôt à `fin − 1` (exécution à `fin`) et sur celle qui se clôt à `fin` (durée nulle si signal)."""
    close_hour = frame.cloture_h % DAY_H
    return (close_hour == (fin - 1) % DAY_H) | (close_hour == fin % DAY_H)


def _lu_par_jour(values: np.ndarray, j: np.ndarray) -> np.ndarray:
    out = np.full(len(j), np.nan)
    ok = j >= 0
    out[ok] = values[j[ok]]
    return out


def _cassure_du_jour(frame: Bougies, ctx: Contexte, amplitude: np.ndarray, daily: Bougies) -> Signaux:
    """1re clôture 1 h du jour > ouverture du jour + k × amplitude de la veille ; vente à la fin de la journée."""
    level = ouverture_du_jour(frame) + ctx.params["k"] * _lu_par_jour(amplitude, veille(frame, daily))
    entry = premier_du_groupe(ind.superieur(frame.cloture, level), jour(frame))
    return Signaux(entry, fin_de_periode(frame, DAY_H))


def williams_cassure(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO01 (exécution 1 h) : 1re clôture 1 h du jour > ouverture du jour + 0,5 × (plus haut − plus bas de la veille) ;
    vente à la clôture de la dernière bougie 1 h de la journée UTC (plafond 24)."""
    daily = quotidien(frame, ctx)
    return _cassure_du_jour(frame, ctx, daily.haut - daily.bas, daily)


def dual_thrust(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO02 (exécution 1 h) : 1re clôture 1 h > ouverture du jour + 0,5 × max(PHH − PCB, PCH − PBB) des N = 4
    journées précédentes ; vente en fin de journée."""
    n = int(ctx.params["n"])
    daily = quotidien(frame, ctx)
    hh = ind.max_glissant(daily.haut, n)
    ll = ind.min_glissant(daily.bas, n)
    hc = ind.max_glissant(daily.cloture, n)
    lc = ind.min_glissant(daily.cloture, n)
    amplitude = np.maximum(hh - lc, hc - ll)
    span = ind.decale(daily.ouverture_h.astype(np.float64), n - 1)
    consecutive = daily.ouverture_h - span == (n - 1) * DAY_H
    return _cassure_du_jour(frame, ctx, np.where(consecutive, amplitude, np.nan), daily)


def ouverture_seance(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO03-VO05 : 1re clôture 1 h de la séance (après sa 1re heure) au-dessus du plus haut de la 1re heure de la
    séance ; vente à la clôture de la dernière bougie 1 h de la séance."""
    start, end = int(ctx.params["debut"]), int(ctx.params["fin"])
    hour, day = heure(frame), jour(frame)
    idx = np.arange(len(frame))
    first = np.maximum.accumulate(np.where(hour == start, idx, -1))
    same = (first >= 0) & (day[np.maximum(first, 0)] == day)
    high = np.where(same, frame.haut[np.maximum(first, 0)], np.nan)
    inside = (hour > start) & (hour < end)
    entry = premier_du_groupe(inside & ind.superieur(frame.cloture, high), day)
    return Signaux(entry, fin_de_periode(frame, end))


def nr(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO06, VO07 : clôture > plus haut d'une journée NR4 (NR7) dans les 2 journées qui la suivent (1re clôture
    au-dessus) ; aucune sortie, tenue de 10 journées."""
    n, window = int(ctx.params["n"]), int(ctx.params["fenetre"])
    span = frame.haut - frame.bas
    narrowest = ind.min_glissant(span, n)
    with np.errstate(invalid="ignore"):
        narrow = ~np.isnan(narrowest) & (span <= narrowest)
    entry = np.zeros(len(frame), dtype=bool)
    for lag in range(1, window + 1):
        if len(frame) <= lag:
            break
        level = ind.decale(np.where(narrow, frame.haut, np.nan), lag)
        hit = ind.superieur(frame.cloture, level)
        for before in range(1, lag):                     # aucune clôture au-dessus entre la journée NR et t
            hit &= ~ind.superieur(ind.decale(frame.cloture, before), level)
        entry |= hit
    return Signaux(entry, None)


def _ecart_type_population(values: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).std(axis=1)
    return out


def _regression_lineaire(values: np.ndarray, n: int) -> np.ndarray:
    """Valeur en `t` de la droite des moindres carrés ajustée sur `t − n + 1 … t` (linreg(x, n, 0))."""
    x = np.asarray(values, dtype=np.float64)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    pos = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    windows = np.lib.stride_tricks.sliding_window_view(x, n)
    slope = windows @ pos / (pos @ pos)
    out[n - 1:] = windows.mean(axis=1) + slope * (n - 1) / 2.0
    return out


def squeeze_composantes(frame: Bougies, bb: int, k_bb: float, kc: int, k_kc: float) -> tuple[np.ndarray, np.ndarray,
                                                                                               np.ndarray]:
    """(compression vraie, compression définie, momentum de Carter)."""
    mid = ind.sma(frame.cloture, bb)
    sd = _ecart_type_population(frame.cloture, bb)
    kc_mid = ind.sma(frame.cloture, kc)
    width = ind.sma(ind.vrai_range(frame.haut, frame.bas, frame.cloture), kc)
    bb_lo, bb_up = mid - k_bb * sd, mid + k_bb * sd
    kc_lo, kc_up = kc_mid - k_kc * width, kc_mid + k_kc * width
    defined = ~(np.isnan(bb_lo) | np.isnan(kc_lo))
    on = ind.superieur(bb_lo, kc_lo) & ind.inferieur(bb_up, kc_up)
    centre = (ind.max_glissant(frame.haut, kc) + ind.min_glissant(frame.bas, kc)) / 2.0
    momentum = _regression_lineaire(frame.cloture - (centre + kc_mid) / 2.0, kc)
    return on, defined, momentum


def squeeze(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO08 : fin de la compression (Bollinger 20/2 sort de Keltner 20/1,5 après au moins une bougie dedans) et
    momentum de Carter > 0 ; sortie après 2 baisses consécutives du momentum (plafond 20)."""
    p = ctx.params
    on, defined, momentum = squeeze_composantes(frame, int(p["bb"]), float(p["k_bb"]), int(p["kc"]),
                                                float(p["k_kc"]))
    released = np.zeros(len(frame), dtype=bool)
    released[1:] = on[:-1] & defined[1:] & ~on[1:]
    entry = released & ind.superieur(momentum, np.zeros(len(frame)))
    previous = ind.decale(momentum, 1)
    exit_flags = ind.inferieur(momentum, previous) & ind.inferieur(previous, ind.decale(momentum, 2))
    return Signaux(entry, exit_flags)


def cassure_atr(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO09 : clôture > clôture précédente + 1 × ATR 14 (de la bougie précédente) ; aucune sortie, tenue de 10."""
    p = ctx.params
    atr_prev = ind.decale(ind.atr(frame.haut, frame.bas, frame.cloture, int(p["n"])), 1)
    level = ind.decale(frame.cloture, 1) + float(p["k"]) * atr_prev
    return Signaux(ind.superieur(frame.cloture, level), None)


def momentum_intrajournalier(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO11 (1 h) : rendement de 00:00 à 22:00 UTC > 0, signal à la clôture de la bougie 21:00-22:00 ; entrée à 23:00,
    sortie à la clôture de 23:00-24:00 (plafond 1)."""
    signal_hour = int(ctx.params["heure_signal"])
    at_signal = frame.cloture_h % DAY_H == signal_hour % DAY_H
    entry = at_signal & ind.superieur(frame.cloture, ouverture_du_jour(frame))
    return Signaux(entry, None)
