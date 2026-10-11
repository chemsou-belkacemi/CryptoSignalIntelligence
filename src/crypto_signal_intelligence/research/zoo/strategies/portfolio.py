"""Portefeuille (PF), forme (b) : PF05, PF06, PF07, PF09, mensuels (premier lundi du mois : décision sur la clôture du
dimanche, échange à la clôture du lundi) ; docs/ZOO.md § 4.2 et § 12.2, docs/ZOO_CATALOGUE.md § 17.

Toujours investis. `univers` = les membres du top 40 du mois de la décision cotés à la ligne de décision (lecture 1
de `factors.py`) : référence « top 40 à poids égaux » (PF06, PF07, PF09), ou panier laissé flotter renouvelé chaque
trimestre (PF05 : le moteur ne rééquilibre la référence qu'au premier échange de chaque trimestre).

Lectures déclarées (à relire) :
1. PF05 : fréquence MENSUELLE, celle du registre (`spec.py`, champ `frequence`), de `ZOO.md` § 4.2 (« mensuelle pour
   PF05 à PF09 ») et du recensement (« poids égaux rééquilibrés chaque mois ») ; le libellé « rééquilibré chaque
   semaine » de `ZOO.md` § 12.2 contredit les trois autres et n'est pas suivi (signalé).
2. PF06 : volatilité = écart-type (ddof = 1) des rendements quotidiens des 30 dernières journées (au moins 3 valides),
   paire cotée depuis 30 jours ; poids ∝ 1 / volatilité, somme 1 ; une paire sans volatilité calculable reste hors du
   portefeuille (mais dans la référence).
3. PF07 : rendements quotidiens des 90 dernières journées des membres cotés depuis 90 jours ; journées où l'un d'eux
   manque retirées (maintenance de Binance, commune à toutes les paires) ; covariance rétrécie de Ledoit et Wolf (2004,
   « A well-conditioned estimator for large-dimensional covariance matrices » : cible μ × I, intensité estimée, la
   version de `sklearn.covariance.LedoitWolf`) ; minimum de variance sous Σ w = 1, w ≥ 0, résolu exactement par
   ensembles actifs.
4. PF09 : 60 % BTC, 40 % ETH à chaque décision, quels que soient les membres (une paire sans prix à l'échange reste
   en USDT, règle du moteur).
"""
from __future__ import annotations

import numpy as np

from ..spec import Contexte, Panneau, Poids
from .commun import decisions_mensuelles
from .factors import (
    BTC,
    ETH,
    colonne,
    cotee_depuis,
    ecart_type,
    fenetre,
    ligne_cotee,
    premiere_cotation,
    rendements,
)

IMPLEMENTEES: frozenset[str] = frozenset({"btc_eth", "inverse_volatilite", "poids_egaux", "variance_minimale"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()
TOLERANCE = 1e-12


def _univers(frame: Panneau, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lignes de décision, membres cotés) à chaque décision."""
    rows = np.array([ligne_cotee(frame.cloture, int(j)) for j in idx], dtype=np.int64)
    universe = np.zeros((len(idx), len(frame.symboles)), dtype=bool)
    for k, (j, row) in enumerate(zip(idx, rows, strict=True)):
        if row >= 0:
            universe[k] = frame.membre[j] & np.isfinite(frame.cloture[row])
    return rows, universe


def poids_egaux(frame: Panneau, ctx: Contexte) -> Poids:
    """PF05 : top 40 à poids égaux, rééquilibré à chaque décision mensuelle."""
    idx, decisions, execution = decisions_mensuelles(frame)
    _, universe = _univers(frame, idx)
    counts = universe.sum(axis=1, keepdims=True)
    weights = np.where(universe, 1.0 / np.maximum(counts, 1), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)


def inverse_volatilite(frame: Panneau, ctx: Contexte) -> Poids:
    """PF06 : poids ∝ 1 / volatilité journalière sur 30 j, parmi les membres cotés."""
    days = int(ctx.params["jours"])
    idx, decisions, execution = decisions_mensuelles(frame)
    rows, universe = _univers(frame, idx)
    r, first = rendements(frame.cloture), premiere_cotation(frame.cloture)
    weights = np.zeros(universe.shape)
    for k, row in enumerate(rows):
        if row < 0:
            continue
        vol = ecart_type(fenetre(r, int(row), days))
        ok = universe[k] & cotee_depuis(first, int(row), days) & np.isfinite(vol) & (vol > 0)
        if ok.any():
            inverse = np.where(ok, 1.0 / np.where(ok, vol, 1.0), 0.0)
            weights[k] = inverse / inverse.sum()
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)


def ledoit_wolf(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Covariance rétrécie de Ledoit et Wolf (2004) vers μ × I (lignes = observations) : (matrice, intensité). Même
    calcul que `sklearn.covariance.ledoit_wolf` (moyenne retirée, covariance divisée par n)."""
    n, p = x.shape
    x = x - x.mean(axis=0)
    sample = x.T @ x / n
    x2 = x ** 2
    trace = x2.sum(axis=0) / n
    mu = float(trace.sum() / p)
    beta_ = float((x2.T @ x2).sum())
    delta_ = float(((x.T @ x) ** 2).sum() / n ** 2)
    beta = (beta_ / n - delta_) / (p * n)
    delta = (delta_ - 2.0 * mu * trace.sum() + p * mu ** 2) / p
    beta = min(beta, delta)
    shrinkage = 0.0 if beta == 0 else beta / delta
    return (1.0 - shrinkage) * sample + shrinkage * mu * np.eye(p), shrinkage


def variance_minimale_longue(cov: np.ndarray, *, iterations: int = 1000) -> np.ndarray:
    """min w'Σw sous Σ w = 1, w ≥ 0 (Σ définie positive), par ensembles actifs primal : départ sur la paire de plus
    faible variance ; à chaque face, optimum avec contrainte d'égalité seule ; pas jusqu'à la première contrainte
    rencontrée ; ajout de la paire qui viole le plus les conditions de Karush-Kuhn-Tucker (Σw)_i ≥ w'Σw."""
    p = len(cov)
    w = np.zeros(p)
    w[int(np.argmin(np.diag(cov)))] = 1.0
    free = w > 0
    scale = float(np.abs(cov).max()) or 1.0
    for _ in range(iterations):
        f = np.flatnonzero(free)
        solved = np.linalg.solve(cov[np.ix_(f, f)], np.ones(len(f)))
        target = solved / solved.sum()
        step = target - w[f]
        blocking = step < -TOLERANCE
        if blocking.any():
            ratios = w[f][blocking] / -step[blocking]
            t = min(1.0, float(ratios.min()))
        else:
            t = 1.0
        w[f] = w[f] + t * step
        if t < 1.0:
            hit = f[blocking][int(np.argmin(ratios))]
            w[hit] = 0.0
            w[w < TOLERANCE] = 0.0
            free = w > 0
            continue
        gradient = cov @ w
        level = float(w @ gradient)
        gap = np.where(free, np.inf, gradient - level)
        worst = int(np.argmin(gap))
        if gap[worst] >= -TOLERANCE * scale:
            return w / w.sum()
        free[worst] = True
    raise RuntimeError("variance minimale : ensembles actifs sans convergence")


def variance_minimale(frame: Panneau, ctx: Contexte) -> Poids:
    """PF07 : minimum de variance long seul, covariance 90 j rétrécie de Ledoit-Wolf, parmi les membres cotés."""
    days = int(ctx.params["jours"])
    idx, decisions, execution = decisions_mensuelles(frame)
    rows, universe = _univers(frame, idx)
    r, first = rendements(frame.cloture), premiere_cotation(frame.cloture)
    weights = np.zeros(universe.shape)
    for k, row in enumerate(rows):
        if row < 0:
            continue
        cols = np.flatnonzero(universe[k] & cotee_depuis(first, int(row), days))
        if len(cols) == 0:
            continue
        window = fenetre(r, int(row), days)[:, cols]
        window = window[np.isfinite(window).all(axis=1)]
        if len(window) < 2:
            continue
        cov, _ = ledoit_wolf(window)
        weights[k, cols] = variance_minimale_longue(cov)
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)


def btc_eth(frame: Panneau, ctx: Contexte) -> Poids:
    """PF09 : 60 % BTC, 40 % ETH, rééquilibré chaque mois ; référence : top 40 à poids égaux."""
    p = ctx.params
    idx, decisions, execution = decisions_mensuelles(frame)
    _, universe = _univers(frame, idx)
    weights = np.zeros(universe.shape)
    for symbol, share in ((BTC, float(p["btc"])), (ETH, float(p["eth"]))):
        col = colonne(frame, symbol)
        if col is not None:
            weights[:, col] = share
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)
