"""Structure (ST) : Dow (ST01), retracements de Fibonacci (ST03-ST05), points pivots (ST06-ST10), plus hauts annuel
et historique (ST11, ST12), chiffres ronds (ST13), plus haut de la veille et de la semaine (ST14, ST15) ; 22 tests de
forme (a).

Paramètres standard du recensement (docs/ZOO_CATALOGUE.md § 5), lus dans `ctx.params` ; fonctions PURES, la valeur en
`t` ne lit que les bougies `≤ t`.

**Pivots ZigZag** (`INDICATEURS.md` § 1, `patterns/primitives.zigzag`, module gelé, appelé et non modifié) :
retournement confirmé quand le prix s'éloigne de l'extrême courant d'au moins `m × ATR` (ATR de Wilder 14 de la
bougie de l'extrême, `primitives.atr`), `m` = 3,0 en 1 h, 2,5 en 4 h, 2,0 en 1 j (`primitives.ZIGZAG_M`). Un pivot
n'est connu qu'à la bougie qui le **confirme** (`known_at`), jamais à son extrême. `zigzag_rapide` en est une copie
compilée (numba), identique à `primitives.zigzag` (test d'égalité exacte) : la version gelée, en Python pur, est trop
lente pour 200 répliques × 100 paires en 1 h.

Lectures déclarées (lignes du recensement, à relire) :
- ST01 : état vrai quand le dernier pivot haut confirmé > le précédent ET le dernier pivot bas confirmé > le
  précédent ; sortie à la bougie qui confirme un pivot bas plus bas que le précédent. Les paramètres du registre
  `m_haut = 2,5` et `m_bas = 2,0` sont lus comme les `m` du § 1 pour 4 h et 1 j (« m = 2,5 / 2,0 ATR » du recensement,
  unités 4h, 1j) : `m` = `m_haut` en 4 h, `m_bas` en 1 j ;
- ST03-ST05 : jambe = dernier pivot haut confirmé `H` et le pivot bas qui le précède `B` ; niveau = H − r (H − B) ;
  fenêtre de la jambe : de la bougie qui confirme `H` (incluse) à celle qui confirme le pivot suivant (exclue) ;
  « contact » = une bougie de la fenêtre dont le plus bas ≤ niveau (la bougie du signal peut être celle du contact :
  mèche sous le niveau et clôture au-dessus) ; signal à la 1re clôture > niveau à partir du contact ; un signal par
  jambe. Sortie : clôture ≥ H ou < B **de la jambe du dernier signal** (l'interface ne porte qu'un tableau de
  sortie par paire : si une nouvelle jambe signale pendant une position, ignorée par le moteur, la sortie suit les
  niveaux de la nouvelle jambe ; cas rare, signalé au chantier du moteur) ;
- ST06-ST10 : pivots journaliers (`INDICATEURS.md` § 10.4) du jour UTC précédent COMPLET (bougie journalière de
  `ctx.quotidien`, sinon aucun niveau) ; classiques P = (H + B + C) / 3, R1 = 2P − B, S1 = 2P − H, R2 = P + (H − B) ;
  Camarilla L3 = C − 1,1 (H − B) / 4, H3 = C + 1,1 (H − B) / 4, H4 = C + 1,1 (H − B) / 2 ; Woodie P = (H + B + 2C) / 4,
  S1 = 2P − H. « Rebond » : plus bas ≤ niveau et clôture > niveau ; « cassure » (« 1re clôture du jour > R1 ») : la
  1re clôture du jour au-dessus du niveau ; un seul signal par jour ;
- « fin de journée UTC » : la sortie s'exécute à la clôture de la dernière bougie 1 h du jour (24:00) ; le moteur
  sortant à la clôture `u + 1`, la condition est vraie sur les bougies 22:00-23:00 et 23:00-24:00. Un signal sur l'une
  de ces deux bougies donne une durée nulle (écarté et compté par le moteur) ;
- ST11 : plus haut des 365 bougies journalières précédentes ; ST12 : plus haut de toutes les bougies journalières
  précédentes de l'historique de la paire, à partir de la 366e (au moins 365 bougies avant) ;
- ST13 : niveaux majeurs (`INDICATEURS.md` § 10.3, `patterns.levels.round_levels`) de la clôture précédente : signal
  quand la clôture dépasse strictement le niveau majeur immédiatement au-dessus (ou égal à) la clôture précédente ;
- ST14 : plus haut du jour UTC précédent complet ; ST15 : plus haut de la semaine précédente (lundi 00:00 UTC),
  complète (42 bougies 4 h, donc ses 168 heures) ; 1re clôture de la semaine au-dessus ; sortie à la clôture de la
  dernière bougie 4 h de la semaine (dimanche 24:00 : condition vraie sur les bougies de 16:00 et de 20:00).
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ....patterns import primitives
from .. import indicators as ind
from .._jit import njit
from ..data import DAY_H, Bougies, agreger
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({
    "dow", "fibonacci", "pivots", "plus_haut_annuel", "plus_haut_historique", "niveau_rond", "plus_haut_veille",
    "plus_haut_semaine"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

SEMAINE_H = 7 * DAY_H
DECALAGE_LUNDI_H = 3 * DAY_H          # 1970-01-01 est un jeudi : heures depuis le lundi = (h + 72) mod 168
M_PAR_UNITE = {1: primitives.ZIGZAG_M["1h"], 4: primitives.ZIGZAG_M["4h"], DAY_H: primitives.ZIGZAG_M["1d"]}


# --- ATR de Wilder et ZigZag (INDICATEURS.md § 1), copies compilées des primitives gelées ------------------------

@njit
def _wilder_suite(tr: np.ndarray, out: np.ndarray, period: int) -> None:
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period


def atr_wilder(high, low, close, period: int = primitives.ATR_PERIOD) -> np.ndarray:
    """`primitives.atr` à l'identique (même amorce `tr[:period].mean()`, même récurrence), boucle compilée."""
    tr = primitives.true_range(high, low, close)
    out = np.full(len(tr), np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    _wilder_suite(tr, out, int(period))
    return out


@njit
def _zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, m: float):
    n = len(high)
    idx = np.empty(n, dtype=np.int64)
    kind = np.empty(n, dtype=np.int8)
    price = np.empty(n)
    known = np.empty(n, dtype=np.int64)
    k = 0
    start = -1
    for i in range(n):
        if np.isfinite(atr[i]):
            start = i
            break
    if start < 0:
        return idx[:0], kind[:0], price[:0], known[:0]
    hi = start
    lo = start
    direction = 0
    for i in range(start + 1, n):
        if direction >= 0 and high[i] > high[hi]:
            hi = i
        if direction <= 0 and low[i] < low[lo]:
            lo = i
        if direction == 0:
            if np.isfinite(atr[lo]) and high[i] >= low[lo] + m * atr[lo] and lo < i:
                idx[k], kind[k], price[k], known[k] = lo, -1, low[lo], i
                k += 1
                direction = 1
                hi = i
            elif np.isfinite(atr[hi]) and low[i] <= high[hi] - m * atr[hi] and hi < i:
                idx[k], kind[k], price[k], known[k] = hi, 1, high[hi], i
                k += 1
                direction = -1
                lo = i
            continue
        if direction == 1 and low[i] <= high[hi] - m * atr[hi] and hi < i:
            idx[k], kind[k], price[k], known[k] = hi, 1, high[hi], i
            k += 1
            direction = -1
            lo = i
        elif direction == -1 and high[i] >= low[lo] + m * atr[lo] and lo < i:
            idx[k], kind[k], price[k], known[k] = lo, -1, low[lo], i
            k += 1
            direction = 1
            hi = i
    return idx[:k], kind[:k], price[:k], known[:k]


class Pivots(NamedTuple):
    """Pivots ZigZag en tableaux : bougie de l'extrême, sens (1 haut, −1 bas), prix, bougie de confirmation."""
    indice: np.ndarray
    sens: np.ndarray
    prix: np.ndarray
    connu: np.ndarray


def zigzag_rapide(high, low, atr, m: float) -> Pivots:
    """`primitives.zigzag` à l'identique, compilé."""
    f = np.asarray
    return Pivots(*_zigzag(f(high, dtype=np.float64), f(low, dtype=np.float64), f(atr, dtype=np.float64), float(m)))


def pivots_zigzag(frame: Bougies, m: float, *, extreme: bool = False) -> Pivots:
    """Pivots ZigZag de `frame`, datés à leur CONFIRMATION. `extreme=True` : MUTATION des contrôles (pivot daté à son
    extrême), jamais utilisée par une stratégie."""
    piv = zigzag_rapide(frame.haut, frame.bas, atr_wilder(frame.haut, frame.bas, frame.cloture), m)
    return piv._replace(connu=piv.indice.copy()) if extreme else piv


def m_zigzag(ctx: Contexte) -> float:
    """`m` du ZigZag de l'unité du test (INDICATEURS.md § 1)."""
    return M_PAR_UNITE[ctx.unite]


# --- ST01 Dow ---------------------------------------------------------------------------------------------------

@njit
def _dow(n: int, sens: np.ndarray, prix: np.ndarray, connu: np.ndarray):
    state = np.zeros(n, dtype=np.bool_)
    out = np.zeros(n, dtype=np.bool_)
    j = 0
    h_last = np.nan
    h_prev = np.nan
    l_last = np.nan
    l_prev = np.nan
    for t in range(n):
        while j < len(connu) and connu[j] <= t:
            if sens[j] == 1:
                h_prev = h_last
                h_last = prix[j]
            else:
                if prix[j] < l_last:
                    out[t] = True
                l_prev = l_last
                l_last = prix[j]
            j += 1
        state[t] = h_last > h_prev and l_last > l_prev
    return state, out


def _dow_impl(frame: Bougies, ctx: Contexte, *, extreme: bool = False) -> Signaux:
    p = ctx.params
    m = {4: p["m_haut"], DAY_H: p["m_bas"]}[ctx.unite]
    piv = pivots_zigzag(frame, m, extreme=extreme)
    state, out = _dow(len(frame), piv.sens, piv.prix, piv.connu)
    return Signaux(state, out)


def dow(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST01 (état) : plus hauts et plus bas ZigZag confirmés ascendants ; sortie à la confirmation d'un pivot bas plus
    bas que le précédent."""
    return _dow_impl(frame, ctx)


# --- ST03-ST05 Fibonacci ----------------------------------------------------------------------------------------

@njit
def _fibonacci(low: np.ndarray, close: np.ndarray, sens: np.ndarray, prix: np.ndarray, connu: np.ndarray, r: float):
    n = len(close)
    entry = np.zeros(n, dtype=np.bool_)
    out = np.zeros(n, dtype=np.bool_)
    j = 0
    active = False
    touched = False
    level = np.nan
    top = np.nan
    bottom = np.nan
    exit_top = np.nan
    exit_bottom = np.nan
    for t in range(n):
        while j < len(connu) and connu[j] <= t:
            if sens[j] == 1 and j >= 1:
                top = prix[j]
                bottom = prix[j - 1]
                level = top - r * (top - bottom)
                active = True
                touched = False
            else:
                active = False
            j += 1
        if active:
            if low[t] <= level:
                touched = True
            if touched and close[t] > level:
                entry[t] = True
                active = False
                exit_top = top
                exit_bottom = bottom
        out[t] = close[t] >= exit_top or close[t] < exit_bottom
    return entry, out


def _fibonacci_impl(frame: Bougies, ctx: Contexte, *, extreme: bool = False) -> Signaux:
    piv = pivots_zigzag(frame, m_zigzag(ctx), extreme=extreme)
    entry, out = _fibonacci(np.asarray(frame.bas, dtype=np.float64), np.asarray(frame.cloture, dtype=np.float64),
                            piv.sens, piv.prix, piv.connu, float(ctx.params["niveau"]))
    return Signaux(entry, out)


def fibonacci(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST03-ST05 : sur la dernière jambe haussière ZigZag confirmée [B ; H], 1re clôture > H − r (H − B) après un
    contact du niveau, avant tout nouveau pivot ; sortie clôture ≥ H ou < B."""
    return _fibonacci_impl(frame, ctx)


# --- Outils journaliers et hebdomadaires -------------------------------------------------------------------------

@njit
def _premiere_par_groupe(cond: np.ndarray, groupe: np.ndarray) -> np.ndarray:
    out = np.zeros(len(cond), dtype=np.bool_)
    current = np.int64(-(1 << 62))
    seen = False
    for t in range(len(cond)):
        if groupe[t] != current:
            current = groupe[t]
            seen = False
        if cond[t] and not seen:
            out[t] = True
            seen = True
    return out


def premiere_du_groupe(cond: np.ndarray, groupe: np.ndarray) -> np.ndarray:
    """Vrai à la première bougie de chaque groupe (jour, semaine) où `cond` est vraie."""
    return _premiere_par_groupe(np.asarray(cond, dtype=np.bool_), np.asarray(groupe, dtype=np.int64))


def fin_de_journee(frame: Bougies) -> np.ndarray:
    """Bougies 1 h dont la sortie à la clôture suivante tombe au plus tard à 24:00 UTC (22:00-23:00, 23:00-24:00)."""
    position = frame.ouverture_h % DAY_H
    return position >= DAY_H - 2 * frame.unite


def semaine(frame: Bougies) -> np.ndarray:
    """Numéro de la semaine (lundi 00:00 UTC) de chaque bougie."""
    return (frame.ouverture_h + DECALAGE_LUNDI_H) // SEMAINE_H


def fin_de_semaine(frame: Bougies) -> np.ndarray:
    """Bougies dont la sortie à la clôture suivante tombe au plus tard le dimanche à 24:00 UTC."""
    position = (frame.ouverture_h + DECALAGE_LUNDI_H) % SEMAINE_H
    return position >= SEMAINE_H - 2 * frame.unite


def veille(frame: Bougies, ctx: Contexte) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(plus haut, plus bas, clôture) du jour UTC précédent COMPLET de chaque bougie (NaN sinon)."""
    daily = ctx.quotidien if ctx.quotidien is not None else agreger(frame, DAY_H)
    days = frame.ouverture_h // DAY_H
    daily_days = daily.ouverture_h // DAY_H
    idx = np.searchsorted(daily_days, days - 1, side="left")
    ok = idx < len(daily_days)
    ok[ok] = daily_days[idx[ok]] == days[ok] - 1
    out = []
    for values in (daily.haut, daily.bas, daily.cloture):
        col = np.full(len(frame), np.nan)
        col[ok] = values[idx[ok]]
        out.append(col)
    return out[0], out[1], out[2]


def niveaux_pivots(kind: str, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict[str, np.ndarray]:
    """Points pivots journaliers (INDICATEURS.md § 10.4 ; Camarilla de N. Scott ; Woodie)."""
    span = high - low
    if kind == "classique":
        p = (high + low + close) / 3
        return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + span}
    if kind == "camarilla":
        return {"L3": close - 1.1 * span / 4, "H3": close + 1.1 * span / 4, "H4": close + 1.1 * span / 2}
    if kind == "woodie":
        p = (high + low + 2 * close) / 4
        return {"P": p, "S1": 2 * p - high}
    raise ValueError(f"pivots inconnus : {kind}")


#: (type, niveau) → niveau de sortie « clôture ≥ … » (None : fin de journée seulement), § 12 de ZOO.md
SORTIES_PIVOTS = {("classique", "S1"): "P", ("classique", "R1"): "R2", ("camarilla", "L3"): "H3",
                  ("camarilla", "H4"): None, ("woodie", "S1"): "P"}


def pivots(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST06-ST10 (1 h) : rebond (plus bas ≤ niveau, clôture > niveau) ou cassure (clôture > niveau) sur les pivots
    du jour, première fois du jour ; sortie au niveau du § 12, sinon fin de journée UTC."""
    p = ctx.params
    high, low, close = veille(frame, ctx)
    levels = niveaux_pivots(p["type"], high, low, close)
    level = levels[p["niveau"]]
    if p["mode"] == "rebond":
        cond = au_plus(frame.bas, level) & ind.superieur(frame.cloture, level)
    elif p["mode"] == "cassure":
        cond = ind.superieur(frame.cloture, level)
    else:
        raise ValueError(f"mode inconnu : {p['mode']}")
    entry = premiere_du_groupe(cond, frame.ouverture_h // DAY_H)
    out = fin_de_journee(frame)
    target = SORTIES_PIVOTS[(p["type"], p["niveau"])]
    if target is not None:
        out = out | au_plus(levels[target], frame.cloture)
    return Signaux(entry, out)


def au_plus(a, b) -> np.ndarray:
    """a ≤ b, faux si l'un est indéfini."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a <= b)


# --- ST11-ST15 ----------------------------------------------------------------------------------------------------

def plus_haut_annuel(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST11 (1 j) : clôture > plus haut des 365 bougies journalières précédentes ; tenue de 20 jours."""
    high = ind.max_glissant(frame.haut, ctx.params["n"], inclure=False)
    return Signaux(ind.superieur(frame.cloture, high), None)


def plus_haut_historique(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST12 (1 j) : clôture > plus haut de toutes les bougies journalières précédentes, au moins 365 avant ; tenue de
    20 jours."""
    previous = ind.decale(np.maximum.accumulate(np.asarray(frame.haut, dtype=np.float64)), 1)
    previous[:ctx.params["minimum"]] = np.nan
    return Signaux(ind.superieur(frame.cloture, previous), None)


def niveau_majeur_au_dessus(prix) -> np.ndarray:
    """Niveau rond majeur immédiatement au-dessus de chaque prix (un niveau égal au prix compte comme au-dessus),
    vectorisation de `patterns.levels.round_levels(...)["major_above"]` ; NaN si le prix n'est pas > 0."""
    x = np.asarray(prix, dtype=np.float64)
    out = np.full(len(x), np.nan)
    ok = x > 0
    if not ok.any():
        return out
    v = x[ok]
    step = 10.0 ** np.floor(np.log10(v))
    below = np.floor(v / step) * step
    same = np.isclose(below, v, rtol=1e-12, atol=0.0)
    out[ok] = np.round(np.where(same, v, below + step), 12)
    return out


def niveau_rond(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST13 : la clôture croise au-dessus du niveau rond majeur de la clôture précédente ; tenue de 10 bougies."""
    level = niveau_majeur_au_dessus(ind.decale(frame.cloture, 1))
    return Signaux(ind.superieur(frame.cloture, level), None)


def plus_haut_veille(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST14 (1 h) : 1re clôture du jour > plus haut de la veille (jour complet) ; sortie en fin de journée UTC."""
    high, _, _ = veille(frame, ctx)
    entry = premiere_du_groupe(ind.superieur(frame.cloture, high), frame.ouverture_h // DAY_H)
    return Signaux(entry, fin_de_journee(frame))


def plus_haut_semaine_precedente(frame: Bougies) -> np.ndarray:
    """Plus haut de la semaine précédente (lundi 00:00 UTC) quand elle est complète (toutes ses bougies), sinon NaN."""
    week = semaine(frame)
    out = np.full(len(frame), np.nan)
    if len(frame) == 0:
        return out
    weeks, starts, counts = np.unique(week, return_index=True, return_counts=True)
    highs = np.maximum.reduceat(np.asarray(frame.haut, dtype=np.float64), starts)
    complete = counts == SEMAINE_H // frame.unite
    idx = np.searchsorted(weeks, week - 1, side="left")
    ok = idx < len(weeks)
    ok[ok] = (weeks[idx[ok]] == week[ok] - 1) & complete[idx[ok]]
    out[ok] = highs[idx[ok]]
    return out


def plus_haut_semaine(frame: Bougies, ctx: Contexte) -> Signaux:
    """ST15 (4 h) : 1re clôture de la semaine > plus haut de la semaine précédente ; sortie à la fin de la semaine."""
    level = plus_haut_semaine_precedente(frame)
    entry = premiere_du_groupe(ind.superieur(frame.cloture, level), semaine(frame))
    return Signaux(entry, fin_de_semaine(frame))
