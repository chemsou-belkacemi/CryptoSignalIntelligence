"""Régression logistique de référence (L2), en numpy, déterministe (point 17, lot 5 : « baseline logistique »).

Ajustement par Newton-Raphson (IRLS) sur variables standardisées avec les moyennes et écarts-types du
SEUL jeu d'entraînement ; l'ordonnée à l'origine n'est pas pénalisée. Aucune dépendance ajoutée : un
modèle simple, lisible et testable vaut mieux qu'une boîte noire tant qu'aucun apport n'est démontré.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LogisticModel:
    features: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    l2: float
    iterations: int

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        z = ((np.asarray(X, dtype=float) - self.mean) / self.scale) @ self.coef + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))

    def to_dict(self) -> dict:
        return {"features": list(self.features), "intercept": round(self.intercept, 6), "l2": self.l2,
                "iterations": self.iterations,
                "standardized_coefficients": {f: round(float(c), 6) for f, c in zip(self.features, self.coef,
                                                                                     strict=True)}}


def fit_logistic(X: np.ndarray, y: np.ndarray, features: tuple[str, ...] | list[str], *, l2: float = 1.0,
                 max_iter: int = 100, tol: float = 1e-8) -> LogisticModel:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim != 2 or len(X) != len(y) or len(X) == 0:
        raise ValueError("X (n × k) et y (n) requis, n > 0")
    if not np.isfinite(X).all() or not set(np.unique(y)) <= {0.0, 1.0}:
        raise ValueError("variables finies et étiquettes 0/1 requises")
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)          # variable constante : laissée telle quelle (coef ≈ 0)
    Z = np.column_stack([np.ones(len(X)), (X - mean) / scale])
    w = np.zeros(Z.shape[1])
    penalty = np.full(Z.shape[1], float(l2))
    penalty[0] = 0.0
    iterations = 0
    while iterations < max_iter:
        iterations += 1
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -35, 35)))
        gradient = Z.T @ (p - y) + penalty * w
        hessian = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(penalty) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.max(np.abs(step)) < tol:
            break
    return LogisticModel(tuple(features), mean, scale, w[1:], float(w[0]), float(l2), iterations)


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Aire sous la courbe ROC par les rangs (ex æquo moyennés) ; None si une seule classe."""
    y = np.asarray(y, dtype=float)
    positives, negatives = int(y.sum()), int(len(y) - y.sum())
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sorted_p = np.asarray(p)[order]
    i = 0
    while i < len(sorted_p):                               # rangs moyens des ex æquo
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))
