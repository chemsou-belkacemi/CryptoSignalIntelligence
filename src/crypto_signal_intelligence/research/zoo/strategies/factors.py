"""Facteurs en coupe (XS), forme (b) : 13 tests (docs/ZOO.md § 4.2 et § 12.2 ; docs/ZOO_CATALOGUE.md § 7).

Portefeuille acheteur seul du **quintile** du côté publié (⌊n / 5⌋ paires, au moins 3), à poids égaux, parmi les
membres du top 40 dont la variable est calculable ; référence : ces mêmes membres éligibles à poids égaux (`univers`).
Hebdomadaire (décision lundi 00:00 sur les clôtures jusqu'au dimanche, échange à la clôture du lundi) ; XS24
quotidien, exécuté à la clôture de la bougie 1 h 01:00-02:00 qui suit la décision, vendu 24 h plus tard.

Lectures déclarées (aucun réglage, aucun regard sur un résultat ; à relire) :
1. **Ligne de décision** : la journée de la décision (dimanche pour l'hebdomadaire) ; si AUCUNE paire n'a de bougie
   journalière complète ce jour-là (maintenance de Binance : ≈ 6 jours par an, communs à toutes les paires), la
   dernière journée cotée antérieure (`ligne_cotee`). L'appartenance reste celle du mois de la décision.
2. **Calculable** : membre du top 40 du mois de la décision, clôture connue sur la ligne de décision, cotée depuis le
   début de la fenêtre (première clôture du panneau au plus tard le premier jour lu), et assez d'observations valides
   dans la fenêtre (2 pour une moyenne, un maximum ou un ratio ; 3 pour un écart-type, une asymétrie, une régression).
   Les journées manquantes de la fenêtre sont ignorées (statistiques « nan », comme `XSECTION.md`).
3. **Rendements** : quotidiens de clôture à clôture (NaN si l'une manque) ; rendement sur `n` jours = clôture de la
   ligne / dernière clôture connue au plus tard `n` jours avant − 1.
4. **Égalités** : classement par valeur, puis par ordre alphabétique des paires (déterministe).
5. XS12, XS14 : BTC est le facteur de la régression, il n'est pas candidat (résidu nul, bêta 1 par construction) ; sans
   BTC calculable sur la fenêtre, aucune paire n'est éligible.
6. XS17 : plus haut de toute l'histoire du panneau = maximum des plus hauts journaliers depuis la première bougie de la
   paire, journée de décision comprise ; cotée depuis au moins 365 jours (première clôture ≤ ligne − 365).
7. XS18 : plus haut des 365 journées finissant à la ligne de décision (plus hauts journaliers, convention 6 du README).
8. XS19 : volume moyen des 7 dernières journées / volume moyen des 70 journées précédentes (10 semaines).
9. XS20 : Σ achats au marché / Σ volume sur les 7 dernières journées (USDT).
10. XS23 : SMA 21 / SMA 200 des clôtures journalières (Avramov, Kaplanski et Subrahmanyam, 2021), quintile haut.
11. XS24 : rendement de la journée (clôture de la veille à clôture du jour), quintile bas ; décision à 00:00 UTC sur
    cette clôture, échange à la clôture de la 1re bougie 1 h qui s'ouvre après l'`available_at` (01:00-02:00) ;
    décision suivante 24 h plus tard au même instant (la vente de la veille et l'achat du jour se compensent quand une
    paire reste dans le quintile : seule la variation de poids paie des frais, comme pour toute forme (b)). Journée
    sans aucune bougie : aucun signal (portefeuille et référence en USDT jusqu'à la décision suivante) ; le lendemain,
    rendement depuis la dernière clôture connue (lecture 3).
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..data import DAY_H
from ..spec import Contexte, Panneau, Poids
from .commun import decisions_hebdomadaires, decisions_quotidiennes

IMPLEMENTEES: frozenset[str] = frozenset({"quintile", "retournement_jour"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

BTC, ETH = "BTCUSDT", "ETHUSDT"
#: XS24 : clôture de la bougie 1 h 01:00-02:00 UTC (1re bougie qui s'ouvre après l'available_at d'une clôture journalière)
HEURE_EXECUTION_JOUR = 2
MIN_MOYENNE, MIN_DISPERSION = 2, 3
QUINTILE_MIN = 3


# --- Outils de coupe (partagés par RO et PF) ------------------------------------------------------------------------

def rendements(cloture: np.ndarray) -> np.ndarray:
    """Rendements quotidiens de clôture à clôture (journées × paires), NaN si l'une des deux clôtures manque."""
    out = np.full(cloture.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[1:] = cloture[1:] / cloture[:-1] - 1.0
    return out


def premiere_cotation(cloture: np.ndarray) -> np.ndarray:
    """Indice de la première clôture connue de chaque paire (len(cloture) si aucune)."""
    finite = np.isfinite(cloture)
    return np.where(finite.any(axis=0), finite.argmax(axis=0), len(cloture))


def propager(x: np.ndarray) -> np.ndarray:
    """Dernière valeur connue à chaque journée (NaN avant la première)."""
    rows = np.where(np.isfinite(x), np.arange(len(x))[:, None], 0)
    np.maximum.accumulate(rows, axis=0, out=rows)
    return x[rows, np.arange(x.shape[1])[None, :]]


def ligne_cotee(cloture: np.ndarray, j: int, recul: int = 7) -> int:
    """Ligne de décision : la dernière des journées `j`, `j − 1`, …, `j − recul + 1` où au moins une paire a une
    bougie journalière complète ; −1 si aucune."""
    for k in range(j, max(j - recul, -1), -1):
        if np.isfinite(cloture[k]).any():
            return k
    return -1


def fenetre(x: np.ndarray, j: int, n: int) -> np.ndarray:
    """Les `n` lignes finissant à `j` (incluse) ; NaN avant le début du panneau."""
    start = j - n + 1
    if start >= 0:
        return x[start:j + 1]
    return np.vstack([np.full((-start, x.shape[1]), np.nan), x[:j + 1]])


def cotee_depuis(premiere: np.ndarray, j: int, jours: int) -> np.ndarray:
    """Paire cotée au plus tard `jours` journées avant la ligne `j`."""
    return premiere <= j - jours


def rendement_n(cloture: np.ndarray, propage: np.ndarray, premiere: np.ndarray, j: int, n: int) -> np.ndarray:
    """Rendement sur `n` jours à la ligne `j` : clôture de `j` / dernière clôture connue au plus tard en `j − n` − 1
    (NaN sans clôture en `j` ou si la paire n'est pas cotée depuis `n` jours)."""
    if j - n < 0:
        return np.full(cloture.shape[1], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = cloture[j] / propage[j - n] - 1.0
    return np.where(cotee_depuis(premiere, j, n), out, np.nan)


def _compte(x: np.ndarray) -> np.ndarray:
    return np.isfinite(x).sum(axis=0)


def moyenne_valide(x: np.ndarray, minimum: int = MIN_MOYENNE) -> np.ndarray:
    n = _compte(x)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(np.isfinite(x), x, 0.0).sum(axis=0) / n
    return np.where(n >= minimum, out, np.nan)


def maximum_valide(x: np.ndarray, minimum: int = MIN_MOYENNE) -> np.ndarray:
    n = _compte(x)
    out = np.where(np.isfinite(x), x, -np.inf).max(axis=0, initial=-np.inf)
    return np.where(n >= minimum, out, np.nan)


def ecart_type(x: np.ndarray, minimum: int = MIN_DISPERSION) -> np.ndarray:
    """Écart-type (ddof = 1) des valeurs valides de chaque colonne."""
    n = _compte(x)
    mean = moyenne_valide(x, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = np.where(np.isfinite(x), (x - mean) ** 2, 0.0).sum(axis=0) / (n - 1)
    return np.where(n >= minimum, np.sqrt(var), np.nan)


def asymetrie(x: np.ndarray, minimum: int = MIN_DISPERSION) -> np.ndarray:
    """Coefficient d'asymétrie de Fisher-Pearson g1 = m3 / m2^1,5 (moments centrés, divisés par n)."""
    n = _compte(x)
    mean = moyenne_valide(x, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        dev = np.where(np.isfinite(x), x - mean, 0.0)
        m2 = (dev ** 2).sum(axis=0) / n
        m3 = (dev ** 3).sum(axis=0) / n
        out = m3 / m2 ** 1.5
    return np.where((n >= minimum) & (m2 > 0), out, np.nan)


def regression_btc(x: np.ndarray, marche: np.ndarray, minimum: int = MIN_DISPERSION) -> tuple[np.ndarray, np.ndarray]:
    """(bêta, écart-type des résidus) de la régression MCO avec constante de chaque colonne de `x` sur `marche`, sur
    les journées où les deux sont connus ; écart-type des résidus = √(SCR / (n − 2))."""
    ok = np.isfinite(x) & np.isfinite(marche)[:, None]
    n = ok.sum(axis=0)
    xm = np.where(ok, x, 0.0)
    mm = np.where(ok, marche[:, None], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, mb = xm.sum(axis=0) / n, mm.sum(axis=0) / n
        dx, db = np.where(ok, x - mx, 0.0), np.where(ok, marche[:, None] - mb, 0.0)
        sbb = (db ** 2).sum(axis=0)
        beta = (dx * db).sum(axis=0) / sbb
        resid = np.where(ok, dx - beta * db, 0.0)
        sigma = np.sqrt((resid ** 2).sum(axis=0) / (n - 2))
    good = (n >= minimum) & (sbb > 0)
    return np.where(good, beta, np.nan), np.where(good, sigma, np.nan)


def quintile_choisi(valeurs: np.ndarray, eligibles: np.ndarray, cote: str) -> np.ndarray:
    """Masque du quintile du côté `cote` (« haut » ou « bas ») : ⌊n / 5⌋ paires, au moins 3 (toutes si n ≤ 3) ;
    égalités départagées par l'ordre des colonnes (alphabétique dans le panneau)."""
    idx = np.flatnonzero(eligibles)
    chosen = np.zeros(len(valeurs), dtype=bool)
    if len(idx) == 0:
        return chosen
    k = min(len(idx), max(QUINTILE_MIN, len(idx) // 5))
    keys = valeurs[idx] if cote == "bas" else -valeurs[idx]
    order = np.lexsort((idx, keys))
    chosen[idx[order[:k]]] = True
    return chosen


def poids_egaux(masque: np.ndarray) -> np.ndarray:
    """Poids égaux sur les lignes d'un masque (décisions × paires), 0 sur une ligne vide."""
    counts = masque.sum(axis=1, keepdims=True)
    return np.where(masque, 1.0 / np.maximum(counts, 1), 0.0)


def colonne(frame: Panneau, symbole: str) -> int | None:
    return frame.symboles.index(symbole) if symbole in frame.symboles else None


# --- Variables des facteurs ---------------------------------------------------------------------------------------

class Base:
    """Tableaux du panneau calculés une fois : rendements, clôtures propagées, première cotation, colonne de BTC."""

    def __init__(self, frame: Panneau):
        self.frame = frame
        self.r = rendements(frame.cloture)
        self.propage = propager(frame.cloture)
        self.premiere = premiere_cotation(frame.cloture)
        self.btc = colonne(frame, BTC)
        self._ath: np.ndarray | None = None

    @property
    def ath(self) -> np.ndarray:
        """Plus haut journalier de toute l'histoire de chaque paire, journée comprise (NaN avant sa cotation)."""
        if self._ath is None:
            self._ath = np.fmax.accumulate(self.frame.haut, axis=0)
        return self._ath


Variable = Callable[[Base, int], np.ndarray]


def _rendement_7j(b: Base, j: int) -> np.ndarray:
    return rendement_n(b.frame.cloture, b.propage, b.premiere, j, 7)


def _volume_28j(b: Base, j: int) -> np.ndarray:
    return np.where(cotee_depuis(b.premiere, j, 27), moyenne_valide(fenetre(b.frame.volume, j, 28)), np.nan)


def _amihud_28j(b: Base, j: int) -> np.ndarray:
    vol = fenetre(b.frame.volume, j, 28)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(vol > 0, np.abs(fenetre(b.r, j, 28)) / vol, np.nan)
    return np.where(cotee_depuis(b.premiere, j, 28), moyenne_valide(ratio), np.nan)


def _max_28j(b: Base, j: int) -> np.ndarray:
    return np.where(cotee_depuis(b.premiere, j, 28), maximum_valide(fenetre(b.r, j, 28)), np.nan)


def _sans_btc(b: Base, values: np.ndarray) -> np.ndarray:
    values = values.copy()
    if b.btc is None:
        return np.full(len(values), np.nan)
    values[b.btc] = np.nan
    return values


def _vol_idio_28j(b: Base, j: int) -> np.ndarray:
    if b.btc is None or not cotee_depuis(b.premiere, j, 28)[b.btc]:
        return np.full(b.r.shape[1], np.nan)
    window = fenetre(b.r, j, 28)
    _, sigma = regression_btc(window, window[:, b.btc])
    return _sans_btc(b, np.where(cotee_depuis(b.premiere, j, 28), sigma, np.nan))


def _beta_90j(b: Base, j: int) -> np.ndarray:
    if b.btc is None or not cotee_depuis(b.premiere, j, 90)[b.btc]:
        return np.full(b.r.shape[1], np.nan)
    window = fenetre(b.r, j, 90)
    beta, _ = regression_btc(window, window[:, b.btc])
    return _sans_btc(b, np.where(cotee_depuis(b.premiere, j, 90), beta, np.nan))


def _asymetrie_28j(b: Base, j: int) -> np.ndarray:
    return np.where(cotee_depuis(b.premiere, j, 28), asymetrie(fenetre(b.r, j, 28)), np.nan)


def _proximite_ath(b: Base, j: int) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return b.frame.cloture[j] / b.ath[j]


def _proximite_52s(b: Base, j: int) -> np.ndarray:
    high = maximum_valide(fenetre(b.frame.haut, j, 365), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = b.frame.cloture[j] / high
    return np.where(cotee_depuis(b.premiere, j, 364), out, np.nan)


def _choc_volume_hebdo(b: Base, j: int) -> np.ndarray:
    week = moyenne_valide(fenetre(b.frame.volume, j, 7))
    before = moyenne_valide(fenetre(b.frame.volume, j - 7, 70)) if j >= 7 else np.full(len(week), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(before > 0, week / before, np.nan)
    return np.where(cotee_depuis(b.premiere, j, 76), out, np.nan)


def _flux_acheteur_7j(b: Base, j: int) -> np.ndarray:
    vol, buy = fenetre(b.frame.volume, j, 7), fenetre(b.frame.achat, j, 7)
    ok = np.isfinite(vol) & np.isfinite(buy)
    total = np.where(ok, vol, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(ok, buy, 0.0).sum(axis=0) / total
    return np.where(cotee_depuis(b.premiere, j, 6) & (ok.sum(axis=0) >= MIN_MOYENNE) & (total > 0), out, np.nan)


def _mad_21_200(b: Base, j: int) -> np.ndarray:
    fast = moyenne_valide(fenetre(b.frame.cloture, j, 21))
    slow = moyenne_valide(fenetre(b.frame.cloture, j, 200))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = fast / slow
    return np.where(cotee_depuis(b.premiere, j, 199), out, np.nan)


VARIABLES: dict[str, Variable] = {
    "rendement_7j": _rendement_7j, "volume_28j": _volume_28j, "amihud_28j": _amihud_28j, "max_28j": _max_28j,
    "vol_idio_28j": _vol_idio_28j, "beta_90j": _beta_90j, "asymetrie_28j": _asymetrie_28j,
    "proximite_ath": _proximite_ath, "proximite_52s": _proximite_52s, "choc_volume_hebdo": _choc_volume_hebdo,
    "flux_acheteur_7j": _flux_acheteur_7j, "mad_21_200": _mad_21_200,
}


def variable(frame: Panneau, nom: str, j: int, base: Base | None = None) -> np.ndarray:
    """Valeur de la variable `nom` de chaque paire à la ligne `j` (NaN si non calculable), sans condition
    d'appartenance (outil de test et de contrôle)."""
    return VARIABLES[nom](base or Base(frame), j)


# --- Stratégies -------------------------------------------------------------------------------------------------

def _coupe(frame: Panneau, idx: np.ndarray, valeur: Variable, cote: str, *, cotation_min: int = 0,
           recul: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """(poids, éligibles) : quintile du côté `cote` parmi les membres éligibles, à chaque décision `idx` (ligne de
    décision reculée d'au plus `recul` − 1 journées sur une journée sans aucune bougie)."""
    base = Base(frame)
    weights = np.zeros((len(idx), len(frame.symboles)))
    eligible = np.zeros((len(idx), len(frame.symboles)), dtype=bool)
    for k, j in enumerate(idx):
        row = ligne_cotee(frame.cloture, int(j), recul)
        if row < 0:
            continue
        values = valeur(base, row)
        ok = frame.membre[j] & np.isfinite(frame.cloture[row]) & np.isfinite(values)
        if cotation_min:
            ok &= cotee_depuis(base.premiere, row, cotation_min)
        eligible[k] = ok
        chosen = quintile_choisi(values, ok, cote)
        if chosen.any():
            weights[k, chosen] = 1.0 / chosen.sum()
    return weights, eligible


def quintile(frame: Panneau, ctx: Contexte) -> Poids:
    """XS01, XS09-XS12, XS14, XS15, XS17-XS20, XS23 (hebdomadaire) : quintile du côté publié de la variable, à poids
    égaux ; référence : les membres éligibles à poids égaux."""
    p = ctx.params
    idx, decisions, execution = decisions_hebdomadaires(frame)
    weights, eligible = _coupe(frame, idx, VARIABLES[p["variable"]], p["cote"],
                               cotation_min=int(p.get("cotation_min_jours", 0)))
    return Poids(decisions, execution, frame.symboles, weights, univers=eligible)


def _rendement_jour(b: Base, j: int) -> np.ndarray:
    return rendement_n(b.frame.cloture, b.propage, b.premiere, j, 1)


def retournement_jour(frame: Panneau, ctx: Contexte) -> Poids:
    """XS24 (quotidien, exécution 1 h) : quintile des plus fortes baisses de la journée, acheté à la clôture de la
    bougie 1 h 01:00-02:00 qui suit la décision, revendu 24 h plus tard (décision suivante)."""
    idx, decisions, _ = decisions_quotidiennes(frame)
    execution = (frame.jours[idx] + 1) * DAY_H + HEURE_EXECUTION_JOUR
    weights, eligible = _coupe(frame, idx, _rendement_jour, "bas", recul=1)   # journée manquante : aucun signal
    return Poids(decisions, execution, frame.symboles, weights, univers=eligible)
