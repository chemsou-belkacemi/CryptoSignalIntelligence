"""Modèles comparés (docs/ML_INTRADAY.md §3, docs/ML_SWING.md §3) : logistique de référence, LightGBM,
XGBoost, CatBoost (swing).

Interface commune : `fit(spec, X, y, seed)` → objet avec `predict_proba(X)` (probabilité brute de la
classe 1). Grilles fixées dans SPECS, sans arrêt précoce (qui consommerait des données de validation).
Les arbres gèrent les valeurs absentes ; la logistique les remplace par la médiane de l'entraînement.
Les probabilités brutes sont ensuite ÉTALONNÉES (Platt) sur un bloc postérieur à l'ajustement, et
l'espérance nette d'un setup s'en déduit (`expected_net`).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import numpy as np

from ..logistic import LogisticModel, auc, fit_logistic

THREADS = max(1, min(4, (os.cpu_count() or 2) - 2))


@dataclass(frozen=True)
class ModelSpec:
    family: str                 # logistic | lightgbm | xgboost
    name: str
    params: dict = field(default_factory=dict)

    def fingerprint(self, features: tuple[str, ...]) -> str:
        payload = json.dumps({"family": self.family, "params": self.params, "features": list(features)},
                             sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("logistic", "logistic_l2", {"l2": 1.0}),
    *(ModelSpec("lightgbm", f"lgbm_leaves{leaves}_n{n}",
                {"num_leaves": leaves, "n_estimators": n, "learning_rate": 0.05, "min_child_samples": 200,
                 "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8})
      for leaves in (15, 63) for n in (200, 500)),
    *(ModelSpec("xgboost", f"xgb_depth{depth}_n{n}",
                {"max_depth": depth, "n_estimators": n, "learning_rate": 0.05, "min_child_weight": 50,
                 "subsample": 0.8, "colsample_bytree": 0.8})
      for depth in (3, 6) for n in (200, 500)),
)
SPECS_BY_NAME = {spec.name: spec for spec in SPECS}


class _Logistic:
    def __init__(self, model: LogisticModel, medians: np.ndarray):
        self.model, self.medians = model, medians

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.where(np.isnan(X), self.medians, X)
        return self.model.predict_proba(X)


class _Tree:
    """Modèle d'arbres (interfaces natives : pas de dépendance à scikit-learn)."""

    def __init__(self, family: str, booster):
        self.family, self.booster = family, booster

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        if self.family == "xgboost":
            import xgboost
            return np.asarray(self.booster.predict(xgboost.DMatrix(X, missing=np.nan)), dtype=float)
        if self.family == "catboost":
            return np.asarray(self.booster.predict_proba(X)[:, 1], dtype=float)
        return np.asarray(self.booster.predict(X), dtype=float)

    def save(self, path: str) -> None:
        self.booster.save_model(path)


def fit(spec: ModelSpec, X: np.ndarray, y: np.ndarray, *, seed: int):
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.int32)
    if len(np.unique(y)) < 2:
        raise ValueError("une seule classe dans l'entraînement")
    if spec.family == "catboost":
        return _fit_catboost(spec, X, y, seed=seed)
    params = dict(spec.params)
    if spec.family == "logistic":
        medians = np.nanmedian(X, axis=0) if len(X) else np.zeros(X.shape[1])
        medians = np.where(np.isnan(medians), 0.0, medians)
        filled = np.where(np.isnan(X), medians, X).astype(float)
        names = tuple(f"x{i}" for i in range(X.shape[1]))
        return _Logistic(fit_logistic(filled, y, names, l2=params.get("l2", 1.0)), medians)
    rounds = int(params.pop("n_estimators"))
    if spec.family == "lightgbm":
        import lightgbm
        native = {"objective": "binary", "num_leaves": params["num_leaves"], "learning_rate": params["learning_rate"],
                  "min_data_in_leaf": params["min_child_samples"], "bagging_fraction": params["subsample"],
                  "bagging_freq": params["subsample_freq"], "feature_fraction": params["colsample_bytree"],
                  "seed": seed, "num_threads": THREADS, "verbose": -1, "deterministic": True, "force_col_wise": True}
        return _Tree("lightgbm", lightgbm.train(native, lightgbm.Dataset(X, label=y), num_boost_round=rounds))
    if spec.family == "xgboost":
        import xgboost
        native = {"objective": "binary:logistic", "max_depth": params["max_depth"], "eta": params["learning_rate"],
                  "min_child_weight": params["min_child_weight"], "subsample": params["subsample"],
                  "colsample_bytree": params["colsample_bytree"], "tree_method": "hist", "nthread": THREADS,
                  "seed": seed, "eval_metric": "logloss"}
        return _Tree("xgboost", xgboost.train(native, xgboost.DMatrix(X, label=y, missing=np.nan),
                                              num_boost_round=rounds))
    raise ValueError(f"famille inconnue : {spec.family}")


def _fit_catboost(spec: ModelSpec, X: np.ndarray, y: np.ndarray, *, seed: int):
    import catboost
    params = spec.params
    model = catboost.CatBoostClassifier(depth=params["depth"], iterations=params["iterations"],
                                        learning_rate=params["learning_rate"], l2_leaf_reg=params["l2_leaf_reg"],
                                        random_seed=seed, thread_count=THREADS, verbose=False,
                                        allow_writing_files=False)
    model.fit(X, y)
    return _Tree("catboost", model)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@dataclass(frozen=True)
class Platt:
    """Étalonnage de Platt : p = sigmoïde(a · logit(brute) + b), ajusté sur un bloc d'étalonnage."""
    model: LogisticModel

    def __call__(self, raw: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(_logit(raw)[:, None])


def fit_platt(raw: np.ndarray, y: np.ndarray) -> Platt:
    return Platt(fit_logistic(_logit(raw)[:, None], np.asarray(y, dtype=float), ("logit",), l2=1.0))


@dataclass(frozen=True)
class Payoff:
    """Gain moyen net des gagnants et perte moyenne nette des perdants (période d'ajustement)."""
    mean_win: float
    mean_loss: float

    @classmethod
    def from_returns(cls, net: np.ndarray) -> Payoff:
        net = np.asarray(net, dtype=float)
        wins, losses = net[net > 0], net[net <= 0]
        return cls(float(wins.mean()) if len(wins) else 0.0, float(losses.mean()) if len(losses) else 0.0)

    def expected(self, p: np.ndarray) -> np.ndarray:
        """Espérance nette : p × gain moyen + (1 − p) × perte moyenne (ampleurs supposées indépendantes de p)."""
        p = np.asarray(p, dtype=float)
        return p * self.mean_win + (1 - p) * self.mean_loss


def calibration_report(p: np.ndarray, y: np.ndarray, base_rate: float, bins: int = 10) -> dict:
    """Qualité des probabilités ÉTALONNÉES sur des données non vues : Brier contre le taux de base de
    l'entraînement, erreur d'étalonnage (moyenne pondérée des écarts par déciles de p), AUC, fiabilité."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if len(p) == 0:
        return {"n": 0}
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    groups = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, max(len(edges) - 2, 0))
    table, ece = [], 0.0
    for g in np.unique(groups):
        mask = groups == g
        mean_p, freq = float(p[mask].mean()), float(y[mask].mean())
        ece += mask.mean() * abs(mean_p - freq)
        table.append({"p_mean": round(mean_p, 4), "observed": round(freq, 4), "n": int(mask.sum())})
    brier = float(np.mean((p - y) ** 2))
    brier_base = float(np.mean((base_rate - y) ** 2))
    area = auc(y, p)
    return {"n": int(len(p)), "brier": round(brier, 6), "brier_base_rate": round(brier_base, 6),
            "brier_skill": round(1 - brier / brier_base, 5) if brier_base > 0 else None,
            "ece": round(float(ece), 5), "auc": round(area, 4) if area is not None else None,
            "observed_rate": round(float(y.mean()), 4), "reliability": table}
