"""Famille Retour à la moyenne (MR) : 23 tests de forme (a) (docs/ZOO.md § 12.1, docs/ZOO_CATALOGUE.md § 2).

Paramètres standard du recensement, lus dans `ctx.params` (registre `spec.py`) ; fonctions PURES, bougies closes, la
valeur en `t` ne lit que les bougies `≤ t` (vérifié par `causality.py`).

Conventions déclarées (définitions de docs/INDICATEURS.md § 10.4 quand elles y sont) :
- **RSI** de Wilder : gains et pertes lissés par RMA, première valeur en `n` ; 100 si la perte moyenne est nulle ;
- **Bollinger** : SMA ± k écarts-types de POPULATION des n dernières clôtures ; réintégration (MR03) stricte des deux
  côtés : `C_{t−1} < bande basse_{t−1}` et `C_t > bande basse_t` ;
- **stochastique lente** (14, 3, 3) : %K brut = 100 (C − min L 14) / (max H 14 − min L 14) (50 si l'écart est nul),
  %K = SMA 3 du brut, %D = SMA 3 de %K ; **Williams %R** = −100 (max H 14 − C) / (max H 14 − min L 14) (−50 si
  l'écart est nul) ; **CCI** = (TP − SMA 20 de TP) / (0,015 × écart absolu moyen à cette SMA), indéfini si l'écart est
  nul ;
- **VWAP du jour** (MR09) : ancrée à 00:00 UTC, Σ TP × V / Σ V depuis la 1re bougie de la journée jusqu'à `t` inclus,
  TP = (H + L + C) / 3, V = volume en USDT (seul volume du zoo, docs/ZOO.md § 2) ; écart relatif `C / VWAP − 1` divisé
  par l'écart-type (échantillon, n − 1) de cet écart sur les 168 bougies précédentes (`t` exclue) ; vente à la
  clôture de la dernière bougie de la journée UTC si la clôture n'est pas revenue à la VWAP avant ;
- **z-score du rendement** (MR11) : rendement simple de `t` contre l'écart-type d'échantillon (n − 1) des 20
  rendements précédents (`t` exclu), sans retrait de la moyenne ;
- **règles d'événement de Connors** (MR01, MR12, MR20) : le déclencheur est la condition vraie à la clôture (un signal
  pendant une position est ignoré par le moteur) ; « plus bas / plus haut des 7 dernières » = des 7 dernières
  CLÔTURES, `t` comprise ;
- **IBS** (MR19, exécution 1 h) : IBS de la journée `d − 1` (bougie journalière complète, connue à 00:00:02), signal
  marqué sur la bougie 1 h de 00:00-01:00 de la journée `d` (README du zoo) ; H = L : IBS indéfini, aucun signal.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..data import DAY_H, Bougies
from ..spec import Contexte, Signaux
from .volatility import cumul_groupe, fin_de_periode, heure, jour, quotidien, veille

IMPLEMENTEES: frozenset[str] = frozenset({
    "rsi2_connors", "rsi_30_70", "bollinger_reintegration", "stochastique", "williams_r", "cci", "vwap_ecart",
    "choc_rendement", "trois_baisses", "ibs", "double_sept"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()
_BLOC = 4096


def _pleins(n: int, value: float) -> np.ndarray:
    return np.full(n, float(value))


def _ge(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a ≥ b, faux si l'un est indéfini."""
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a >= b)


def _egal(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a == b)


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    """RSI de Wilder (première valeur en `n`, 100 si la perte moyenne est nulle)."""
    c = np.asarray(close, dtype=np.float64)
    delta = np.diff(c, prepend=np.nan)
    with np.errstate(invalid="ignore"):
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
    gain[np.isnan(delta)] = np.nan
    loss[np.isnan(delta)] = np.nan
    g, p = ind.rma(gain, n), ind.rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(p == 0, 100.0, 100.0 - 100.0 / (1.0 + g / p))
    return np.where(np.isnan(g) | np.isnan(p), np.nan, out)


def ecart_type_glissant(values: np.ndarray, n: int, *, ddof: int, inclure: bool = True) -> np.ndarray:
    """Écart-type des n dernières valeurs (`inclure=False` : de t − n à t − 1) ; NaN si une valeur manque."""
    x = np.asarray(values, dtype=np.float64)
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(x, n)
    sd = np.empty(len(windows))
    for lo in range(0, len(windows), _BLOC):           # par blocs : mémoire bornée sur 70 000 bougies × 168
        sd[lo:lo + _BLOC] = windows[lo:lo + _BLOC].std(axis=1, ddof=ddof)
    if inclure:
        out[n - 1:] = sd
    else:
        out[n:] = sd[:-1]
    return out


def rsi2_connors(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR01 : RSI 2 < 10 et clôture > SMA 200 ; sortie clôture > SMA 5 (plafond 20)."""
    p = ctx.params
    n = len(frame)
    entry = ind.inferieur(rsi(frame.cloture, int(p["rsi"])), _pleins(n, p["seuil"])) & ind.superieur(
        frame.cloture, ind.sma(frame.cloture, int(p["sma"])))
    return Signaux(entry, ind.superieur(frame.cloture, ind.sma(frame.cloture, int(p["sortie_sma"]))))


def rsi_30_70(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR02 : RSI 14 croise au-dessus de 30 ; sortie RSI 14 > 70 (plafond 20)."""
    p = ctx.params
    value = rsi(frame.cloture, int(p["n"]))
    n = len(frame)
    return Signaux(ind.croise_dessus(value, _pleins(n, p["bas"])), ind.superieur(value, _pleins(n, p["haut"])))


def bandes_bollinger(close: np.ndarray, n: int, k: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(basse, médiane, haute) : SMA n ± k écarts-types de population."""
    mid = ind.sma(close, n)
    sd = ecart_type_glissant(close, n, ddof=0)
    return mid - k * sd, mid, mid + k * sd


def bollinger_reintegration(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR03 : clôture t − 1 < bande basse, clôture t > bande basse (BB 20, 2) ; sortie clôture ≥ bande médiane."""
    lower, mid, _ = bandes_bollinger(frame.cloture, int(ctx.params["n"]), float(ctx.params["k"]))
    below = ind.inferieur(frame.cloture, lower)
    entry = np.zeros(len(frame), dtype=bool)
    entry[1:] = below[:-1] & ind.superieur(frame.cloture, lower)[1:]
    return Signaux(entry, _ge(frame.cloture, mid))


def stochastique_lente(frame: Bougies, k: int, smooth: int, d: int) -> tuple[np.ndarray, np.ndarray]:
    """(%K lent, %D) : brut sur k bougies (50 si l'écart est nul), lissé par SMA `smooth`, %D = SMA `d` de %K."""
    high, low = ind.max_glissant(frame.haut, k), ind.min_glissant(frame.bas, k)
    span = high - low
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(span == 0, 50.0, 100.0 * (frame.cloture - low) / span)
    raw = np.where(np.isnan(span), np.nan, raw)
    slow = ind.sma(raw, smooth)
    return slow, ind.sma(slow, d)


def stochastique(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR05 : %K croise au-dessus de %D avec %K < 20 (14, 3, 3) ; sortie %K > 80 (plafond 20)."""
    p = ctx.params
    k_line, d_line = stochastique_lente(frame, int(p["k"]), int(p["lissage"]), int(p["d"]))
    n = len(frame)
    entry = ind.croise_dessus(k_line, d_line) & ind.inferieur(k_line, _pleins(n, p["bas"]))
    return Signaux(entry, ind.superieur(k_line, _pleins(n, p["haut"])))


def williams_pct_r(frame: Bougies, n: int) -> np.ndarray:
    high, low = ind.max_glissant(frame.haut, n), ind.min_glissant(frame.bas, n)
    span = high - low
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(span == 0, -50.0, -100.0 * (high - frame.cloture) / span)
    return np.where(np.isnan(span), np.nan, out)


def williams_r(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR06 : %R 14 croise au-dessus de −80 ; sortie %R > −20 (plafond 20)."""
    p = ctx.params
    value = williams_pct_r(frame, int(p["n"]))
    n = len(frame)
    return Signaux(ind.croise_dessus(value, _pleins(n, p["bas"])), ind.superieur(value, _pleins(n, p["haut"])))


def cci_valeurs(frame: Bougies, n: int) -> np.ndarray:
    """CCI de Lambert : (TP − SMA n) / (0,015 × écart absolu moyen à la SMA n de la bougie), NaN si l'écart est nul."""
    tp = (frame.haut + frame.bas + frame.cloture) / 3.0
    out = np.full(len(tp), np.nan)
    if len(tp) < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(tp, n)
    mean = windows.mean(axis=1)
    deviation = np.abs(windows - mean[:, None]).mean(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[n - 1:] = np.where(deviation > 0, (tp[n - 1:] - mean) / (0.015 * deviation), np.nan)
    return out


def cci(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR07 : CCI 20 croise au-dessus de −100 ; sortie CCI > +100 (plafond 20)."""
    p = ctx.params
    value = cci_valeurs(frame, int(p["n"]))
    n = len(frame)
    return Signaux(ind.croise_dessus(value, _pleins(n, p["bas"])), ind.superieur(value, _pleins(n, p["haut"])))


def vwap_du_jour(frame: Bougies) -> np.ndarray:
    """VWAP ancrée à 00:00 UTC (bougie `t` comprise), prix typique, volume en USDT ; NaN si le volume cumulé est nul."""
    day = jour(frame)
    tp = (frame.haut + frame.bas + frame.cloture) / 3.0
    volume = cumul_groupe(frame.volume, day)
    weighted = cumul_groupe(tp * frame.volume, day)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(volume > 0, weighted / volume, np.nan)


def vwap_ecart(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR09 (1 h) : (C / VWAP du jour − 1) / écart-type de cet écart sur les 168 bougies précédentes < −2 ; sortie
    clôture ≥ VWAP du jour, sinon à la clôture de la dernière bougie de la journée UTC (plafond 24)."""
    p = ctx.params
    vwap = vwap_du_jour(frame)
    gap = frame.cloture / vwap - 1.0
    sd = ecart_type_glissant(gap, int(p["fenetre"]), ddof=1, inclure=False)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(sd > 0, gap / sd, np.nan)
    entry = ind.inferieur(z, _pleins(len(frame), p["z"]))
    return Signaux(entry, _ge(frame.cloture, vwap) | fin_de_periode(frame, DAY_H))


def choc_rendement(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR11 : rendement de t < −2 × écart-type des 20 rendements précédents ; aucune sortie, tenue de 10."""
    p = ctx.params
    ret = frame.cloture / ind.decale(frame.cloture, 1) - 1.0
    sd = ecart_type_glissant(ret, int(p["n"]), ddof=1, inclure=False)
    return Signaux(ind.inferieur(ret, float(p["z"]) * sd), None)


def trois_baisses(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR12 : 3 clôtures consécutives en baisse et clôture > SMA 200 ; sortie clôture > SMA 5 (plafond 20)."""
    p = ctx.params
    down = ind.inferieur(frame.cloture, ind.decale(frame.cloture, 1))
    entry = down.copy()
    for lag in range(1, int(p["baisses"])):
        entry[:lag] = False
        entry[lag:] &= down[:-lag]
    entry &= ind.superieur(frame.cloture, ind.sma(frame.cloture, int(p["sma"])))
    return Signaux(entry, ind.superieur(frame.cloture, ind.sma(frame.cloture, int(p["sortie_sma"]))))


def ibs(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR19 (signal journalier, exécution 1 h) : IBS = (C − L) / (H − L) de la journée d − 1 < 0,2, marqué sur la
    bougie 1 h de 00:00-01:00 de la journée d ; entrée à 02:00, sortie 24 h plus tard (plafond 24, aucune règle)."""
    daily = quotidien(frame, ctx)
    span = daily.haut - daily.bas
    with np.errstate(invalid="ignore", divide="ignore"):
        value = np.where(span > 0, (daily.cloture - daily.bas) / span, np.nan)
    j = veille(frame, daily)
    level = np.full(len(frame), np.nan)
    ok = (j >= 0) & (heure(frame) == 0)
    level[ok] = value[j[ok]]
    return Signaux(ind.inferieur(level, _pleins(len(frame), ctx.params["seuil"])), None)


def double_sept(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR20 : clôture > SMA 200 et clôture = plus bas des 7 dernières clôtures ; sortie clôture = plus haut des 7
    dernières (plafond 20)."""
    p = ctx.params
    n = int(p["n"])
    entry = _egal(frame.cloture, ind.min_glissant(frame.cloture, n)) & ind.superieur(
        frame.cloture, ind.sma(frame.cloture, int(p["sma"])))
    return Signaux(entry, _egal(frame.cloture, ind.max_glissant(frame.cloture, n)))
