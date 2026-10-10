"""Famille Tendance et cassures (TR), modèle des autres familles : 81 tests de forme (a), 2 de forme (b).

Paramètres standard du recensement (docs/ZOO_CATALOGUE.md § 1), lus dans `ctx.params` (registre `spec.py`). Chaque
fonction est PURE : bougies closes de l'unité du test en entrée, `Signaux` (forme a) ou `Poids` (forme b) en sortie ;
la valeur en `t` ne lit que les bougies `≤ t` (vérifié par `causality.py`).

Conventions déclarées : « plus haut / plus bas des n bougies précédentes » = extrêmes des plus hauts / plus bas de
`t − n` à `t − 1` ; « croise au-dessus » = vrai en `t`, faux (et défini) en `t − 1` ; état = entrée tant que la
condition est vraie, sortie par la condition de sortie du § 12 ; Ichimoku : nuage applicable en `t` fait des Senkou
de `t − 26`, Chikou jamais utilisée.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from ..data import Bougies
from ..spec import Contexte, Panneau, Poids, Signaux
from .commun import decisions_hebdomadaires, rendement_sur

IMPLEMENTEES: frozenset[str] = frozenset({
    "croisement_moyennes", "au_dessus_sma", "donchian", "keltner", "supertrend", "sar", "adx_dmi", "ichimoku_nuage",
    "ichimoku_croisement", "macd_signal", "macd_zero", "aroon", "vortex", "trix", "kama", "hull", "filtre_alexander",
    "cassure_plus_haut", "canal_etroit", "point_figure", "momentum_temporel"})
#: Stratégies dont les signaux dépendent du début de l'étape (calculés par étape).
DEPENDENT_ETAPE: frozenset[str] = frozenset({"filtre_alexander"})


def _le(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a ≤ b, faux si l'un est indéfini."""
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a <= b)


def croisement_moyennes(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR01-TR08 : moyenne rapide croise au-dessus de la lente ; sortie au croisement inverse."""
    p = ctx.params
    fast = ind.moyenne(p["moyenne"], frame.cloture, p["rapide"])
    slow = ind.moyenne(p["moyenne"], frame.cloture, p["lente"])
    return Signaux(ind.croise_dessus(fast, slow), ind.croise_dessous(fast, slow))


def au_dessus_sma(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR09, TR10 (état) : clôture > SMA n ; sortie clôture ≤ SMA n."""
    average = ind.sma(frame.cloture, ctx.params["periode"])
    return Signaux(ind.superieur(frame.cloture, average), _le(frame.cloture, average))


def donchian(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR12, TR13 : clôture > plus haut des n bougies précédentes ; sortie clôture < plus bas des m précédentes."""
    p = ctx.params
    high = ind.max_glissant(frame.haut, p["entree"], inclure=False)
    low = ind.min_glissant(frame.bas, p["sortie"], inclure=False)
    return Signaux(ind.superieur(frame.cloture, high), ind.inferieur(frame.cloture, low))


def keltner(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR18 : clôture croise au-dessus de EMA 20 + 2 × ATR 10 ; sortie clôture < EMA 20."""
    p = ctx.params
    center = ind.ema(frame.cloture, p["ema"])
    upper = center + p["k"] * ind.atr(frame.haut, frame.bas, frame.cloture, p["atr"])
    return Signaux(ind.croise_dessus(frame.cloture, upper), ind.inferieur(frame.cloture, center))


def supertrend(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR19 (état) : SuperTrend (10, 3) haussier ; sortie à la bascule baissière."""
    direction = ind.supertrend(frame.haut, frame.bas, frame.cloture, ctx.params["atr"], ctx.params["k"])
    return Signaux(direction == 1, direction == -1)


def sar(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR20 (état) : SAR (0,02 ; 0,20) sous le prix ; sortie quand il passe au-dessus."""
    direction = ind.sar_direction(frame.haut, frame.bas, ctx.params["pas"], ctx.params["maximum"])
    return Signaux(direction == 1, direction == -1)


def adx_dmi(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR21 (état) : ADX 14 > 25 et +DI > −DI ; sortie +DI < −DI."""
    plus, minus, adx = ind.dmi(frame.haut, frame.bas, frame.cloture, ctx.params["n"])
    entry = ind.superieur(adx, np.full(len(adx), ctx.params["seuil"])) & ind.superieur(plus, minus)
    return Signaux(entry, ind.inferieur(plus, minus))


def _nuage(frame: Bougies, ctx: Contexte) -> dict[str, np.ndarray]:
    p = ctx.params
    return ind.ichimoku(frame.haut, frame.bas, frame.cloture, p["tenkan"], p["kijun"], p["senkou"], p["decalage"])


def ichimoku_nuage(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR22 (état) : clôture > haut du nuage applicable (Senkou de t − 26) ; sortie clôture ≤ haut du nuage."""
    cloud = _nuage(frame, ctx)
    return Signaux(ind.superieur(frame.cloture, cloud["nuage_haut"]), _le(frame.cloture, cloud["nuage_haut"]))


def ichimoku_croisement(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR23 : Tenkan croise au-dessus de Kijun et clôture > haut du nuage applicable ; sortie Tenkan < Kijun."""
    cloud = _nuage(frame, ctx)
    entry = ind.croise_dessus(cloud["tenkan"], cloud["kijun"]) & ind.superieur(frame.cloture, cloud["nuage_haut"])
    return Signaux(entry, ind.inferieur(cloud["tenkan"], cloud["kijun"]))


def macd_signal(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR24 : MACD (12, 26) croise au-dessus de son signal 9 ; sortie au croisement inverse."""
    p = ctx.params
    line, signal = ind.macd(frame.cloture, p["rapide"], p["lente"], p["signal"])
    return Signaux(ind.croise_dessus(line, signal), ind.croise_dessous(line, signal))


def macd_zero(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR25 (état) : MACD > 0 ; sortie MACD ≤ 0."""
    p = ctx.params
    line = ind.ema(frame.cloture, p["rapide"]) - ind.ema(frame.cloture, p["lente"])
    zero = np.zeros(len(line))
    return Signaux(ind.superieur(line, zero), _le(line, zero))


def aroon(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR26 : Aroon haut croise au-dessus d'Aroon bas (25) ; sortie au croisement inverse."""
    up, down = ind.aroon(frame.haut, frame.bas, ctx.params["n"])
    return Signaux(ind.croise_dessus(up, down), ind.croise_dessous(up, down))


def vortex(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR27 : VI+ croise au-dessus de VI− (14) ; sortie au croisement inverse."""
    plus, minus = ind.vortex(frame.haut, frame.bas, frame.cloture, ctx.params["n"])
    return Signaux(ind.croise_dessus(plus, minus), ind.croise_dessous(plus, minus))


def trix(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR28 : TRIX 15 croise au-dessus de son signal 9 ; sortie au croisement inverse."""
    line, signal = ind.trix(frame.cloture, ctx.params["n"], ctx.params["signal"])
    return Signaux(ind.croise_dessus(line, signal), ind.croise_dessous(line, signal))


def kama(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR29 : clôture croise au-dessus de KAMA (10, 2, 30) ; sortie clôture < KAMA."""
    p = ctx.params
    average = ind.kama(frame.cloture, p["n"], p["rapide"], p["lente"])
    return Signaux(ind.croise_dessus(frame.cloture, average), ind.inferieur(frame.cloture, average))


def hull(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR30 (état) : HMA 20 en hausse (HMA_t > HMA_t−1) ; sortie HMA en baisse."""
    average = ind.hma(frame.cloture, ctx.params["n"])
    previous = ind.decale(average, 1)
    return Signaux(ind.superieur(average, previous), ind.inferieur(average, previous))


def filtre_alexander(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR31, TR32 (état) : règle de filtre x = 5 % ou 10 % sur les clôtures journalières, machine à deux états
    démarrée à la première décision de l'étape (« depuis la dernière sortie, ou le début de l'étape »)."""
    start = int(np.searchsorted(frame.disponible, ctx.debut_etape_ns, side="left"))
    state = ind.filtre_alexander(frame.cloture, start, ctx.params["x"])
    exit_flags = ~state
    exit_flags[:start] = False
    return Signaux(state, exit_flags)


def cassure_plus_haut(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR33-TR35 : clôture > plus haut des n jours précédents ; aucune sortie (tenue fixe de 10 jours)."""
    high = ind.max_glissant(frame.haut, ctx.params["n"], inclure=False)
    return Signaux(ind.superieur(frame.cloture, high), None)


def canal_etroit(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR36 : (max − min) des 20 jours précédents ≤ 10 % du min, et clôture > ce max ; tenue fixe de 10 jours."""
    p = ctx.params
    high = ind.max_glissant(frame.haut, p["n"], inclure=False)
    low = ind.min_glissant(frame.bas, p["n"], inclure=False)
    narrow = _le(high - low, p["largeur"] * low)
    return Signaux(narrow & ind.superieur(frame.cloture, high), None)


def point_figure(frame: Bougies, ctx: Contexte) -> Signaux:
    """TR48 : point et figure (boîte 1 % log, retournement 3 boîtes, clôtures), passé seul : colonne de X qui dépasse
    la précédente colonne de X ; sortie quand une colonne de O passe sous la précédente colonne de O."""
    buy, sell = ind.point_figure(frame.cloture, ctx.params["boite"], ctx.params["retournement"])
    return Signaux(buy, sell)


def momentum_temporel(frame: Panneau, ctx: Contexte) -> Poids:
    """TR37, TR40 (forme b, hebdomadaire) : chaque membre à 1/n si son rendement sur 7 j (84 j) est > 0, sinon sa
    part en USDT ; n = membres dont le rendement est calculable. Univers de la référence : ces mêmes membres."""
    idx, decisions, execution = decisions_hebdomadaires(frame)
    returns = rendement_sur(frame.cloture, idx, ctx.params["jours"])
    eligible = frame.membre[idx] & ~np.isnan(returns)
    count = eligible.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore"):
        weights = np.where(eligible & (returns > 0), 1.0 / np.maximum(count, 1), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=eligible)
