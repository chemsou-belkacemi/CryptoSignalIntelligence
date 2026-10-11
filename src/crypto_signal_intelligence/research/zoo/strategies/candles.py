"""Chandeliers japonais (CH) : 13 figures TA-Lib (CH01 à CH05, CH08, CH10 à CH13, CH15 à CH17), Heikin Ashi (CH20)
et Renko (CH21) ; 44 tests de forme (a), tous des événements.

Paramètres standard du recensement (docs/ZOO_CATALOGUE.md § 4), lus dans `ctx.params` (registre `spec.py`). Chaque
fonction est PURE : bougies closes de l'unité du test, `Signaux` en sortie ; la valeur en `t` ne lit que les bougies
`≤ t` (vérifié par `causality.py` et `tests/test_zoo_ch_st_an*.py`).

**Figures : définitions de TA-Lib** (sources C `ta_CDL*.c`, réglages par défaut `TA_CandleDefaultSettings`),
recopiées ici car TA-Lib n'est pas installé (exemples écrits à la main d'après ces sources dans les tests) :
- réglages (type de plage, période, facteur) : BodyLong (corps, 10, 1), BodyShort (corps, 10, 1), BodyDoji
  (haut − bas, 10, 0,1), ShadowLong (corps, 0, 1), ShadowVeryShort (haut − bas, 10, 0,1), Near (haut − bas, 5, 0,2),
  Far (haut − bas, 5, 0,6), Equal (haut − bas, 5, 0,05) ; moyenne d'un réglage à la bougie `j` = facteur × moyenne de
  la plage des `période` bougies `j − période … j − 1` (période 0 : la plage de `j` elle-même ; plage « ombres » :
  moitié de la somme des deux ombres). TA-Lib tient ces sommes par somme glissante ; elles sont ici sommées dans
  l'ordre (écarts d'arrondi seulement) ;
- couleur : blanche si clôture ≥ ouverture (TA_CANDLECOLOR), noire sinon ;
- aucune sortie avant la fenêtre de TA-Lib (`lookback`) ;
- versions avec le cas « 80 » (corps égaux à un bout) pour CDLENGULFING, CDLHARAMI et CDLHARAMICROSS ;
  « > 0 » du recensement = sortie haussière (100 ou 80).
**Tendance préalable** (recensement § 4, Marshall, Young et Rose, 2006) : clôture strictement sous (au-dessus de) la
SMA 10 des clôtures à la **première bougie de la figure** (`t − bougies + 1`).

**Lecture déclarée (à relire)** : CDLINVERTEDHAMMER (écart de corps vers le bas), CDL3INSIDE (2e corps strictement
intérieur), CDL3OUTSIDE (2e bougie ouverte sous la clôture de la 1re), CDLRISEFALL3METHODS (5e bougie ouverte
au-dessus de la clôture précédente) et CDLHOMINGPIGEON (ouverture sous l'ouverture précédente et clôture au-dessus de
la clôture précédente, deux bougies noires) exigent une ouverture DIFFÉRENTE de la clôture précédente : comme CH06,
CH07, CH09, CH14 et CH18 (raison 7 du recensement), elles ne peuvent apparaître sur un marché où l'ouverture est la
clôture précédente (marché synthétique sous H0) ; sur Binance, seulement sur des écarts d'un ou quelques échelons de
prix entre la dernière transaction d'une bougie et la première de la suivante. Codées telles quelles (TA-Lib),
signalées au chantier du registre.

CH20 (Valcu, 2004) : HA_clôture = (O + H + B + C) / 4, HA_ouverture_t = (HA_ouverture_{t−1} + HA_clôture_{t−1}) / 2
(amorce : (O_0 + C_0) / 2 à la première bougie de l'historique), HA_haut = max(H, HA_o, HA_c), HA_bas = min(B, HA_o,
HA_c), récurrence sur le passé seul. Haussière : HA_c > HA_o ; baissière : HA_c < HA_o ; sans mèche basse :
HA_bas = HA_o (soit B ≥ HA_o). Entrée : 1re haussière sans mèche basse depuis la dernière baissière ; sortie : toute
HA baissière.

CH21 (Nison, 1994) : briques sur les clôtures de l'unité d'exécution, boîte du mois = ATR 14 journalier (Wilder,
`indicators.atr`) de la dernière bougie journalière qui précède le 1er du mois (connu au 1er à 00:00) ; mois d'une
bougie = mois de son ouverture. Dernière brique [b ; h] (au départ b = h = première clôture dont la boîte est
connue) : clôture ≥ h + boîte → ⌊(clôture − h) / boîte⌋ briques haussières ; clôture ≤ b − boîte → ⌊(b − clôture) /
boîte⌋ briques baissières (retournement = 2 boîtes depuis l'extrémité opposée de la dernière brique, règle
classique). Entrée : la bougie où se forme la 2e brique haussière consécutive après une brique baissière ; sortie :
toute bougie qui forme une brique baissière. Mois sans boîte connue : aucune brique (la construction reprend de la
dernière brique).
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from .._jit import njit
from ..data import DAY_H, Bougies, agreger
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({"figure", "heikin_ashi", "renko"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

# --- Réglages de TA-Lib (TA_CandleDefaultSettings) ---------------------------------------------------------------

CORPS, HAUT_BAS, OMBRES = "corps", "haut_bas", "ombres"
#: nom → (type de plage, période, facteur)
REGLAGES: dict[str, tuple[str, int, float]] = {
    "BodyLong": (CORPS, 10, 1.0), "BodyShort": (CORPS, 10, 1.0), "BodyDoji": (HAUT_BAS, 10, 0.1),
    "ShadowLong": (CORPS, 0, 1.0), "ShadowVeryShort": (HAUT_BAS, 10, 0.1), "Near": (HAUT_BAS, 5, 0.2),
    "Far": (HAUT_BAS, 5, 0.6), "Equal": (HAUT_BAS, 5, 0.05)}


class _Chandeliers:
    """Grandeurs d'une série de bougies au sens de TA-Lib (macros TA_REALBODY, TA_UPPERSHADOW, …)."""

    def __init__(self, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray):
        self.o, self.h, self.lo, self.c = (np.asarray(v, dtype=np.float64) for v in (o, h, lo, c))
        self.corps = np.abs(self.c - self.o)
        self.haut_corps = np.maximum(self.o, self.c)
        self.bas_corps = np.minimum(self.o, self.c)
        self.ombre_haute = self.h - self.haut_corps
        self.ombre_basse = self.bas_corps - self.lo
        self.couleur = np.where(self.c >= self.o, 1, -1)
        self._cache: dict[str, np.ndarray] = {}

    def __len__(self) -> int:
        return len(self.c)

    def plage(self, kind: str) -> np.ndarray:
        if kind == CORPS:
            return self.corps
        if kind == HAUT_BAS:
            return self.h - self.lo
        return self.ombre_haute + self.ombre_basse

    def moyenne(self, name: str) -> np.ndarray:
        """TA_CANDLEAVERAGE du réglage `name` à chaque bougie (NaN tant que la période n'est pas disponible)."""
        if name not in self._cache:
            kind, period, factor = REGLAGES[name]
            values = self.plage(kind)
            if period == 0:
                avg = factor * values
            else:
                total = np.zeros(len(values))
                for k in range(1, period + 1):          # bougies j − 1 … j − période, sommées dans l'ordre
                    total = total + ind.decale(values, k)
                avg = factor * total / period
            self._cache[name] = avg / 2.0 if kind == OMBRES else avg
        return self._cache[name]

    def d(self, values: np.ndarray, k: int) -> np.ndarray:
        """Valeur de la bougie t − k en t (NaN au début)."""
        return ind.decale(values, k) if k else np.asarray(values, dtype=np.float64)


def _ge(a, b) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.asarray(a >= b) & ~(np.isnan(a) | np.isnan(b))


def _gt(a, b) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.asarray(a > b) & ~(np.isnan(a) | np.isnan(b))


def _blanche(x: _Chandeliers, k: int) -> np.ndarray:
    return x.d(x.couleur.astype(np.float64), k) == 1.0


def _noire(x: _Chandeliers, k: int) -> np.ndarray:
    return x.d(x.couleur.astype(np.float64), k) == -1.0


def _hammer(x: _Chandeliers) -> np.ndarray:
    """CDLHAMMER : petit corps, longue ombre basse (> corps), ombre haute très courte, corps près des plus bas de
    la bougie précédente (bas du corps ≤ plus bas précédent + Near de la bougie précédente)."""
    return (_gt(x.moyenne("BodyShort"), x.corps) & _gt(x.ombre_basse, x.moyenne("ShadowLong"))
            & _gt(x.moyenne("ShadowVeryShort"), x.ombre_haute)
            & _ge(x.d(x.lo, 1) + x.d(x.moyenne("Near"), 1), x.bas_corps))


def _inverted_hammer(x: _Chandeliers) -> np.ndarray:
    """CDLINVERTEDHAMMER : petit corps, longue ombre haute, ombre basse très courte, écart de corps vers le bas
    (haut du corps < bas du corps précédent)."""
    return (_gt(x.moyenne("BodyShort"), x.corps) & _gt(x.ombre_haute, x.moyenne("ShadowLong"))
            & _gt(x.moyenne("ShadowVeryShort"), x.ombre_basse) & _gt(x.d(x.bas_corps, 1), x.haut_corps))


def _engulfing(x: _Chandeliers) -> np.ndarray:
    """CDLENGULFING > 0 : bougie blanche après une noire, corps qui avale le précédent (100), ou égal à un bout (80)."""
    o1, c1 = x.d(x.o, 1), x.d(x.c, 1)
    body = (_ge(x.c, o1) & _gt(c1, x.o)) | (_gt(x.c, o1) & _ge(c1, x.o))
    return _blanche(x, 0) & _noire(x, 1) & body


def _harami_corps(x: _Chandeliers, petit: str) -> np.ndarray:
    """Harami haussier (100 strict, 80 égal à un bout) : 1re bougie noire au corps long, 2e corps court (ou doji)
    contenu dans le premier."""
    return (_gt(x.d(x.corps, 1), x.d(x.moyenne("BodyLong"), 1)) & _ge(x.moyenne(petit), x.corps)
            & _ge(x.d(x.haut_corps, 1), x.haut_corps) & _ge(x.bas_corps, x.d(x.bas_corps, 1)) & _noire(x, 1))


def _harami(x: _Chandeliers) -> np.ndarray:
    """CDLHARAMI > 0."""
    return _harami_corps(x, "BodyShort")


def _harami_cross(x: _Chandeliers) -> np.ndarray:
    """CDLHARAMICROSS > 0 : 2e bougie doji (corps ≤ BodyDoji)."""
    return _harami_corps(x, "BodyDoji")


def _dragonfly(x: _Chandeliers) -> np.ndarray:
    """CDLDRAGONFLYDOJI : corps doji, ombre haute très courte, ombre basse qui ne l'est pas."""
    vs = x.moyenne("ShadowVeryShort")
    return _ge(x.moyenne("BodyDoji"), x.corps) & _gt(vs, x.ombre_haute) & _gt(x.ombre_basse, vs)


def _three_white_soldiers(x: _Chandeliers) -> np.ndarray:
    """CDL3WHITESOLDIERS : trois blanches à ombre haute très courte, clôtures croissantes, chacune ouverte dans le
    corps précédent ou près de lui, corps pas beaucoup plus courts que les précédents, 3e corps pas court."""
    vs, near, far = x.moyenne("ShadowVeryShort"), x.moyenne("Near"), x.moyenne("Far")
    out = np.ones(len(x), dtype=bool)
    for k in (2, 1, 0):
        out &= _blanche(x, k) & _gt(x.d(vs, k), x.d(x.ombre_haute, k))
    c0, c1, c2 = x.c, x.d(x.c, 1), x.d(x.c, 2)
    o0, o1, o2 = x.o, x.d(x.o, 1), x.d(x.o, 2)
    out &= _gt(c0, c1) & _gt(c1, c2)
    out &= _gt(o1, o2) & _ge(c2 + x.d(near, 2), o1) & _gt(o0, o1) & _ge(c1 + x.d(near, 1), o0)
    out &= _gt(x.d(x.corps, 1), x.d(x.corps, 2) - x.d(far, 2)) & _gt(x.corps, x.d(x.corps, 1) - x.d(far, 1))
    return out & _gt(x.corps, x.moyenne("BodyShort"))


def _three_inside(x: _Chandeliers) -> np.ndarray:
    """CDL3INSIDE > 0 : 1re noire au corps long, 2e corps court STRICTEMENT intérieur, 3e blanche qui clôture
    au-dessus de l'ouverture de la 1re."""
    return (_gt(x.d(x.corps, 2), x.d(x.moyenne("BodyLong"), 2))
            & _ge(x.d(x.moyenne("BodyShort"), 1), x.d(x.corps, 1))
            & _gt(x.d(x.haut_corps, 2), x.d(x.haut_corps, 1)) & _gt(x.d(x.bas_corps, 1), x.d(x.bas_corps, 2))
            & _noire(x, 2) & _blanche(x, 0) & _gt(x.c, x.d(x.o, 2)))


def _three_outside(x: _Chandeliers) -> np.ndarray:
    """CDL3OUTSIDE > 0 : noire, puis blanche qui l'avale strictement (ouverte sous sa clôture, close au-dessus de son
    ouverture), puis clôture plus haute."""
    return (_blanche(x, 1) & _noire(x, 2) & _gt(x.d(x.c, 1), x.d(x.o, 2)) & _gt(x.d(x.c, 2), x.d(x.o, 1))
            & _gt(x.c, x.d(x.c, 1)))


def _belt_hold(x: _Chandeliers) -> np.ndarray:
    """CDLBELTHOLD > 0 : longue blanche sans (ou presque) ombre basse."""
    return (_gt(x.corps, x.moyenne("BodyLong")) & _blanche(x, 0)
            & _gt(x.moyenne("ShadowVeryShort"), x.ombre_basse))


def _rise_three_methods(x: _Chandeliers) -> np.ndarray:
    """CDLRISEFALL3METHODS > 0 : longue blanche, trois petites noires en baisse dont le corps touche la plage de la
    1re, puis longue blanche ouverte au-dessus de la clôture précédente et close au-dessus de la clôture de la 1re."""
    long_, short = x.moyenne("BodyLong"), x.moyenne("BodyShort")
    h4, l4 = x.d(x.h, 4), x.d(x.lo, 4)
    out = _gt(x.d(x.corps, 4), x.d(long_, 4)) & _gt(x.corps, long_) & _blanche(x, 4) & _blanche(x, 0)
    for k in (3, 2, 1):
        out &= _gt(x.d(short, k), x.d(x.corps, k)) & _noire(x, k)
        out &= _gt(h4, x.d(x.bas_corps, k)) & _gt(x.d(x.haut_corps, k), l4)
    out &= _gt(x.d(x.c, 3), x.d(x.c, 2)) & _gt(x.d(x.c, 2), x.d(x.c, 1))
    return out & _gt(x.o, x.d(x.c, 1)) & _gt(x.c, x.d(x.c, 4))


def _homing_pigeon(x: _Chandeliers) -> np.ndarray:
    """CDLHOMINGPIGEON : longue noire, puis petite noire ouverte sous son ouverture et close au-dessus de sa clôture."""
    return (_noire(x, 1) & _noire(x, 0) & _gt(x.d(x.corps, 1), x.d(x.moyenne("BodyLong"), 1))
            & _ge(x.moyenne("BodyShort"), x.corps) & _gt(x.d(x.o, 1), x.o) & _gt(x.c, x.d(x.c, 1)))


def _matching_low(x: _Chandeliers) -> np.ndarray:
    """CDLMATCHINGLOW : deux noires de clôtures égales à Equal près (Equal de la 1re bougie)."""
    eq, c1 = x.d(x.moyenne("Equal"), 1), x.d(x.c, 1)
    return _noire(x, 1) & _noire(x, 0) & _ge(c1 + eq, x.c) & _ge(x.c, c1 - eq)


#: fonction TA-Lib → (détecteur haussier, nombre de bougies de la figure, lookback de TA-Lib)
FIGURES = {
    "CDLHAMMER": (_hammer, 1, 11), "CDLINVERTEDHAMMER": (_inverted_hammer, 1, 11),
    "CDLENGULFING": (_engulfing, 2, 2), "CDLHARAMI": (_harami, 2, 11), "CDLHARAMICROSS": (_harami_cross, 2, 11),
    "CDLDRAGONFLYDOJI": (_dragonfly, 1, 10), "CDL3WHITESOLDIERS": (_three_white_soldiers, 3, 12),
    "CDL3INSIDE": (_three_inside, 3, 12), "CDL3OUTSIDE": (_three_outside, 3, 3), "CDLBELTHOLD": (_belt_hold, 1, 10),
    "CDLRISEFALL3METHODS": (_rise_three_methods, 5, 14), "CDLHOMINGPIGEON": (_homing_pigeon, 2, 11),
    "CDLMATCHINGLOW": (_matching_low, 2, 6),
}


def figure_talib(nom: str, ouverture, haut, bas, cloture) -> np.ndarray:
    """Vrai à la bougie `t` où la fonction TA-Lib `nom` rend une valeur haussière (> 0), faux avant son lookback."""
    detector, _, lookback = FIGURES[nom]
    out = detector(_Chandeliers(ouverture, haut, bas, cloture))
    out[:lookback] = False
    return out


def figure(frame: Bougies, ctx: Contexte) -> Signaux:
    """CH01-CH05, CH08, CH10-CH13, CH15-CH17 : figure TA-Lib haussière à `t`, tendance préalable du recensement
    (clôture sous / au-dessus de la SMA 10 à la 1re bougie de la figure) ; aucune sortie (tenue de 10 bougies)."""
    p = ctx.params
    found = figure_talib(p["talib"], frame.ouverture, frame.haut, frame.bas, frame.cloture)
    first = FIGURES[p["talib"]][1] - 1
    average = ind.decale(ind.sma(frame.cloture, p["sma"]), first)
    close_first = ind.decale(frame.cloture, first)
    if p["tendance"] == "baissiere":
        trend = ind.inferieur(close_first, average)
    elif p["tendance"] == "haussiere":
        trend = ind.superieur(close_first, average)
    else:
        raise ValueError(f"tendance inconnue : {p['tendance']}")
    return Signaux(found & trend, None)


# --- CH20 Heikin Ashi -------------------------------------------------------------------------------------------

@njit
def _heikin_ashi(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray):
    n = len(c)
    ha_o = np.empty(n)
    ha_c = np.empty(n)
    ha_h = np.empty(n)
    ha_l = np.empty(n)
    for t in range(n):
        ha_c[t] = (o[t] + h[t] + lo[t] + c[t]) / 4.0
        ha_o[t] = (o[t] + c[t]) / 2.0 if t == 0 else (ha_o[t - 1] + ha_c[t - 1]) / 2.0
        ha_h[t] = max(h[t], max(ha_o[t], ha_c[t]))
        ha_l[t] = min(lo[t], min(ha_o[t], ha_c[t]))
    return ha_o, ha_h, ha_l, ha_c


def bougies_heikin_ashi(ouverture, haut, bas, cloture) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(HA ouverture, HA haut, HA bas, HA clôture), récurrence sur le passé seul."""
    f = np.asarray
    return _heikin_ashi(f(ouverture, dtype=np.float64), f(haut, dtype=np.float64), f(bas, dtype=np.float64),
                        f(cloture, dtype=np.float64))


@njit
def _premieres_apres_baissiere(bull_clean: np.ndarray, bear: np.ndarray) -> np.ndarray:
    out = np.zeros(len(bull_clean), dtype=np.bool_)
    armed = False
    for t in range(len(bull_clean)):
        if bear[t]:
            armed = True
        elif bull_clean[t] and armed:
            out[t] = True
            armed = False
    return out


def heikin_ashi(frame: Bougies, ctx: Contexte) -> Signaux:
    """CH20 : 1re bougie Heikin Ashi haussière sans mèche basse depuis la dernière HA baissière ; sortie : HA
    baissière."""
    ha_o, _, ha_l, ha_c = bougies_heikin_ashi(frame.ouverture, frame.haut, frame.bas, frame.cloture)
    bull_clean = (ha_c > ha_o) & (ha_l == ha_o)
    bear = ha_c < ha_o
    return Signaux(_premieres_apres_baissiere(bull_clean, bear), bear.copy())


# --- CH21 Renko -------------------------------------------------------------------------------------------------

def _premier_jour_du_mois(jours: np.ndarray) -> np.ndarray:
    months = np.asarray(jours, dtype=np.int64).astype("datetime64[D]").astype("datetime64[M]")
    return months.astype("datetime64[D]").astype(np.int64)


def boite_renko(frame: Bougies, quotidien: Bougies, n_atr: int) -> np.ndarray:
    """Boîte de chaque bougie : ATR `n_atr` journalier de la dernière bougie journalière d'avant le 1er du mois de la
    bougie (connu au 1er à 00:00), NaN s'il n'est pas défini."""
    atr = ind.atr(quotidien.haut, quotidien.bas, quotidien.cloture, n_atr)
    daily_days = quotidien.ouverture_h // DAY_H
    first = _premier_jour_du_mois(frame.ouverture_h // DAY_H)
    idx = np.searchsorted(daily_days, first, side="left") - 1   # dernière journée < 1er du mois
    box = np.full(len(frame), np.nan)
    ok = idx >= 0
    box[ok] = atr[idx[ok]]
    return box


@njit
def _renko(close: np.ndarray, box: np.ndarray):
    n = len(close)
    entry = np.zeros(n, dtype=np.bool_)
    down = np.zeros(n, dtype=np.bool_)
    started = False
    low = 0.0
    high = 0.0
    seen_down = False
    ups = 0
    for t in range(n):
        b = box[t]
        if not (b > 0.0):
            continue
        if not started:
            low = close[t]
            high = close[t]
            started = True
            continue
        if close[t] >= high + b:
            k = np.floor((close[t] - high) / b)
            high = high + k * b
            low = high - b
            before = ups
            ups += int(k)
            if seen_down and before < 2 <= ups:
                entry[t] = True
        elif close[t] <= low - b:
            k = np.floor((low - close[t]) / b)
            low = low - k * b
            high = low + b
            down[t] = True
            seen_down = True
            ups = 0
    return entry, down


def briques_renko(cloture, boite) -> tuple[np.ndarray, np.ndarray]:
    """(2e brique haussière consécutive après une baissière, brique baissière) à chaque bougie."""
    return _renko(np.asarray(cloture, dtype=np.float64), np.asarray(boite, dtype=np.float64))


def renko(frame: Bougies, ctx: Contexte) -> Signaux:
    """CH21 : 2e brique Renko haussière après une baissière (boîte ATR 14 journalier connue au 1er du mois, briques
    sur les clôtures du passé seul) ; sortie : 1re brique baissière."""
    daily = frame if frame.unite == DAY_H else (ctx.quotidien if ctx.quotidien is not None else agreger(frame, DAY_H))
    entry, down = briques_renko(frame.cloture, boite_renko(frame, daily, ctx.params["atr"]))
    return Signaux(entry, down)
