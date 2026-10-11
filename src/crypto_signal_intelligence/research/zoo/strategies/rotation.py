"""Rotation et marché (RO), forme (b) : RO02 à RO05, hebdomadaires (docs/ZOO.md § 4.2 et § 12.2 ;
docs/ZOO_CATALOGUE.md § 8).

- RO02, RO03 : rotations entre deux actifs ; `composante_a`, `composante_b` et `dans_a` sont rendus à chaque décision
  pour la référence de même composition moyenne `f̄ × A + (1 − f̄) × B` (README du zoo) ;
- RO04, RO05 : top 40 à poids égaux ou USDT ; `univers` = les membres cotés à la décision (référence d'exposition
  égale `f̄ × U`).

Lectures déclarées (en plus de celles de `factors.py` : ligne de décision, rendements, fenêtres ; à relire) :
1. RO02 : ETHBTC = clôture journalière d'ETHUSDT / clôture de BTCUSDT (le magasin n'a que des paires USDT ; les deux
   clôtures sont celles du même instant) ; SMA 50 journalière du ratio, journée de décision comprise ; règle fausse ou
   non calculable : BTC.
2. RO03 : altcoins = membres du top 40 autres que BTC dont le rendement sur 90 j est calculable ; « battent BTC » =
   rendement sur 90 j strictement supérieur à celui de BTC ; A = ces altcoins à poids égaux ; part ≥ 0,75 → A, sinon
   (ou non calculable) BTC.
3. RO04 : membres dont la SMA 50 est calculable ; « plus de la moitié » = part strictement > 0,5 ; investi : tous les
   membres cotés à poids égaux.
4. RO05 : corrélation moyenne = moyenne des corrélations de Pearson par paires (observations communes, au moins 3)
   des rendements quotidiens des 30 dernières journées, entre les membres cotés depuis 30 jours ; série calculée
   chaque jour avec les membres du jour ; médiane des 365 journées précédentes (valeurs connues), exigeant 365 jours
   d'historique de la série ; strictement sous la médiane → investi ; non calculable → USDT.
"""
from __future__ import annotations

import numpy as np

from ..spec import Contexte, Panneau, Poids
from .commun import decisions_hebdomadaires
from .factors import (
    BTC,
    ETH,
    MIN_DISPERSION,
    colonne,
    cotee_depuis,
    fenetre,
    ligne_cotee,
    moyenne_valide,
    poids_egaux,
    premiere_cotation,
    propager,
    rendement_n,
    rendements,
)

IMPLEMENTEES: frozenset[str] = frozenset({"altseason", "correlation", "eth_btc", "largeur"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def _une(n_decisions: int, n_symboles: int, col: int | None) -> np.ndarray:
    out = np.zeros((n_decisions, n_symboles))
    if col is not None:
        out[:, col] = 1.0
    return out


def _rotation(frame: Panneau, decisions: np.ndarray, execution: np.ndarray, a: np.ndarray, b: np.ndarray,
              inside: np.ndarray) -> Poids:
    weights = np.where(inside[:, None], a, b)
    return Poids(decisions, execution, frame.symboles, weights, univers=(a > 0) | (b > 0), composante_a=a,
                 composante_b=b, dans_a=inside)


def ratio_eth_btc(frame: Panneau) -> np.ndarray:
    """Clôture ETHUSDT / clôture BTCUSDT de chaque journée (NaN si l'une manque ou si une paire est absente)."""
    eth, btc = colonne(frame, ETH), colonne(frame, BTC)
    if eth is None or btc is None:
        return np.full(len(frame.jours), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return frame.cloture[:, eth] / frame.cloture[:, btc]


def eth_btc(frame: Panneau, ctx: Contexte) -> Poids:
    """RO02 : 100 % ETH si ETHBTC > SMA 50 journalière, sinon 100 % BTC."""
    n = int(ctx.params["sma"])
    idx, decisions, execution = decisions_hebdomadaires(frame)
    ratio = ratio_eth_btc(frame)[:, None]
    first = premiere_cotation(ratio)
    inside = np.zeros(len(idx), dtype=bool)
    for k, j in enumerate(idx):
        row = ligne_cotee(frame.cloture, int(j))
        if row < 0 or not cotee_depuis(first, row, n - 1)[0]:
            continue
        average = moyenne_valide(fenetre(ratio, row, n))[0]
        inside[k] = bool(np.isfinite(ratio[row, 0]) and np.isfinite(average) and ratio[row, 0] > average)
    shape = (len(idx), len(frame.symboles))
    return _rotation(frame, decisions, execution, _une(*shape, colonne(frame, ETH)), _une(*shape, colonne(frame, BTC)),
                     inside)


def altseason(frame: Panneau, ctx: Contexte) -> Poids:
    """RO03 : altcoins membres à poids égaux si au moins 75 % d'entre eux battent BTC sur 90 j, sinon 100 % BTC."""
    p = ctx.params
    days, share = int(p["jours"]), float(p["part"])
    idx, decisions, execution = decisions_hebdomadaires(frame)
    btc = colonne(frame, BTC)
    first, filled = premiere_cotation(frame.cloture), propager(frame.cloture)
    a = np.zeros((len(idx), len(frame.symboles)))
    inside = np.zeros(len(idx), dtype=bool)
    for k, j in enumerate(idx):
        row = ligne_cotee(frame.cloture, int(j))
        if row < 0:
            continue
        returns = rendement_n(frame.cloture, filled, first, row, days)
        alts = frame.membre[j] & np.isfinite(returns)
        if btc is not None:
            alts[btc] = False
        if not alts.any():
            continue
        a[k, alts] = 1.0 / alts.sum()
        if btc is not None and np.isfinite(returns[btc]):
            inside[k] = bool((returns[alts] > returns[btc]).mean() >= share)
    return _rotation(frame, decisions, execution, a, _une(len(idx), len(frame.symboles), btc), inside)


def _membres_cotes(frame: Panneau, j: int, row: int) -> np.ndarray:
    return frame.membre[j] & np.isfinite(frame.cloture[row]) if row >= 0 else np.zeros(len(frame.symboles), bool)


def largeur(frame: Panneau, ctx: Contexte) -> Poids:
    """RO04 : top 40 à poids égaux si plus de la moitié des membres clôture au-dessus de sa SMA 50, sinon USDT."""
    p = ctx.params
    n, share = int(p["sma"]), float(p["part"])
    idx, decisions, execution = decisions_hebdomadaires(frame)
    first = premiere_cotation(frame.cloture)
    universe = np.zeros((len(idx), len(frame.symboles)), dtype=bool)
    invested = np.zeros(len(idx), dtype=bool)
    for k, j in enumerate(idx):
        row = ligne_cotee(frame.cloture, int(j))
        universe[k] = _membres_cotes(frame, int(j), row)
        if not universe[k].any():
            continue
        average = moyenne_valide(fenetre(frame.cloture, row, n))
        ok = universe[k] & cotee_depuis(first, row, n - 1) & np.isfinite(average)
        if ok.any():
            invested[k] = bool((frame.cloture[row, ok] > average[ok]).mean() > share)
    weights = np.where(invested[:, None], poids_egaux(universe), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)


def correlations_moyennes(frame: Panneau, fenetre_jours: int) -> np.ndarray:
    """Série quotidienne de la corrélation moyenne par paires des rendements des `fenetre_jours` dernières journées,
    entre les membres du jour cotés depuis `fenetre_jours` jours (corrélation de Pearson sur les observations
    communes, au moins 3 ; NaN sans au moins une paire)."""
    r = rendements(frame.cloture)
    first = premiere_cotation(frame.cloture)
    out = np.full(len(frame.jours), np.nan)
    for d in range(len(frame.jours)):
        cols = np.flatnonzero(frame.membre[d] & np.isfinite(frame.cloture[d]) & cotee_depuis(first, d, fenetre_jours))
        if len(cols) < 2:
            continue
        out[d] = correlation_moyenne(fenetre(r, d, fenetre_jours)[:, cols])
    return out


def correlation_moyenne(x: np.ndarray) -> float:
    """Moyenne des corrélations de Pearson par paires de colonnes, chacune sur les lignes où les deux sont connues
    (au moins 3, variances non nulles) ; NaN sans aucune paire calculable."""
    ok = np.isfinite(x)
    m = ok.astype(float)
    z = np.where(ok, x, 0.0)
    n = m.T @ m
    sa = z.T @ m                      # Σ x_a sur les lignes communes à (a, b)
    sb = sa.T
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = z.T @ z - sa * sb / n
        var_a = (z ** 2).T @ m - sa ** 2 / n
        var_b = var_a.T
        corr = cov / np.sqrt(var_a * var_b)
    upper = np.triu(np.ones(n.shape, dtype=bool), 1) & (n >= MIN_DISPERSION) & (var_a > 0) & (var_b > 0)
    values = corr[upper & np.isfinite(corr)]
    return float(values.mean()) if len(values) else float("nan")


def correlation(frame: Panneau, ctx: Contexte) -> Poids:
    """RO05 : top 40 à poids égaux si la corrélation moyenne sur 30 j est sous sa médiane des 365 j précédents,
    sinon USDT."""
    p = ctx.params
    window, history = int(p["fenetre"]), int(p["historique"])
    idx, decisions, execution = decisions_hebdomadaires(frame)
    series = correlations_moyennes(frame, window)
    first = premiere_cotation(series[:, None])[0]
    universe = np.zeros((len(idx), len(frame.symboles)), dtype=bool)
    invested = np.zeros(len(idx), dtype=bool)
    for k, j in enumerate(idx):
        row = ligne_cotee(frame.cloture, int(j))
        universe[k] = _membres_cotes(frame, int(j), row)
        if row < 0 or first > row - history or not np.isfinite(series[row]):
            continue
        past = series[row - history:row]
        past = past[np.isfinite(past)]
        invested[k] = bool(len(past) and series[row] < np.median(past))
    weights = np.where(invested[:, None], poids_egaux(universe), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)
