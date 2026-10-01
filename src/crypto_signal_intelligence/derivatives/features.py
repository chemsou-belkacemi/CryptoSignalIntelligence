"""Variables CAUSALES de positionnement aux heures de décision (docs/DERIVATIVES.md, criblage).

Décisions toutes les 4 h, à la clôture des bougies Spot 1 h de 03:00, 07:00… (comme le swing). Chaque
variable n'utilise que des lignes dont `available_at` précède celui de la décision, avec un âge maximal :
- financement : taux ramené à son équivalent sur 8 h (taux × 8 / intervalle en heures), moyenne des
  règlements des 72 dernières heures (heures de règlement arrondies à la minute la plus proche : leur gigue
  de quelques millisecondes, dans un sens ou dans l'autre, ne fait jamais entrer un 10e règlement), et son
  10e centile sur les 90 jours PRÉCÉDENTS (la valeur courante exclue) ;
- prime : moyenne des 24 dernières bougies 1 h closes, et son 10e centile sur les 90 jours précédents ;
- intérêt ouvert et ratio de comptes : valeurs « metrics » ramenées sur une grille horaire (la dernière ligne
  disponible à chaque heure), variation sur 24 h du NOMBRE DE CONTRATS ouverts (pas de leur valeur en dollars,
  qui contient la variation du prix), 10e centiles sur les 90 jours précédents ; une valeur nulle ou négative
  (présente dans les archives officielles) est manquante.
Cibles : achat à l'ouverture de la bougie 1 h suivante, vente à la clôture de t+H (fenêtre contiguë, sinon
pas de cible). Le contrôle de causalité (données tronquées, futur falsifié) et ses mutations sont dans
`causality_violations`.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

STEP = pd.Timedelta(hours=1)
DECISION_EVERY = 4
HORIZONS = (24, 72, 168)
WINDOW = "90D"
QUANTILE = 0.10
MAX_FUNDING_AGE = pd.Timedelta(hours=9)       # règlement toutes les 8 h (4 h pour certains contrats) + marge
MAX_PREMIUM_AGE = pd.Timedelta(hours=2)
MAX_METRICS_AGE = pd.Timedelta(minutes=30)    # sur la grille horaire : dernière ligne 5 min de moins de 30 min
MIN_FUNDING_HISTORY = 180                     # règlements (60 jours à 8 h) avant tout centile
MIN_HOURLY_HISTORY = 1440                     # heures (60 jours) avant tout centile
FEATURES = ("funding_3d", "funding_3d_q10", "premium_24h", "premium_24h_q10", "oi_change_24h",
            "oi_change_24h_q10", "accounts_ratio", "accounts_q10", "spot_ret_24h")


def _sorted(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    return frame.sort_values(key).reset_index(drop=True)


def funding_table(funding: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par règlement : moyenne 72 h et 10e centile des 90 jours précédents (valeur courante exclue)."""
    if funding.empty:
        return pd.DataFrame(columns=["available_at", "funding_3d", "funding_3d_q10"])
    f = _sorted(funding, "time")
    f = f.set_index(f["time"].dt.round("min"))
    per_8h = f["rate"] * 8 / f["interval_hours"].where(f["interval_hours"] > 0).fillna(8)
    mean_3d = per_8h.rolling("72h").mean()
    q10 = mean_3d.rolling(WINDOW, closed="left", min_periods=MIN_FUNDING_HISTORY).quantile(QUANTILE)
    return pd.DataFrame({"available_at": f["available_at"].to_numpy(), "funding_3d": mean_3d.to_numpy(),
                         "funding_3d_q10": q10.to_numpy()})


def premium_table(premium: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par bougie 1 h de prime : moyenne des 24 dernières heures et son 10e centile précédent."""
    if premium.empty:
        return pd.DataFrame(columns=["available_at", "premium_24h", "premium_24h_q10"])
    p = _sorted(premium, "time").set_index("time")
    mean_24h = p["close"].rolling("24h", min_periods=20).mean()
    q10 = mean_24h.rolling(WINDOW, closed="left", min_periods=MIN_HOURLY_HISTORY).quantile(QUANTILE)
    return pd.DataFrame({"available_at": p["available_at"].to_numpy(), "premium_24h": mean_24h.to_numpy(),
                         "premium_24h_q10": q10.to_numpy()})


def metrics_grid(metrics: pd.DataFrame) -> pd.DataFrame:
    """Grille horaire : à chaque heure h, la dernière ligne « metrics » DISPONIBLE à h (âge ≤ 30 min), puis
    variation 24 h de l'intérêt ouvert et 10e centiles des 90 jours précédents. `available_at` = h."""
    columns = ["available_at", "oi_change_24h", "oi_change_24h_q10", "accounts_ratio", "accounts_q10"]
    if metrics.empty:
        return pd.DataFrame(columns=columns)
    m = _sorted(metrics, "available_at")
    m = m.assign(**{column: m[column].where(m[column] > 0) for column in ("oi", "accounts_ratio")})
    hours = pd.date_range(m["available_at"].iloc[0].ceil("h"), m["available_at"].iloc[-1].floor("h"), freq="h")
    grid = pd.merge_asof(pd.DataFrame({"available_at": hours.as_unit("ns")}),
                         m[["available_at", "oi", "accounts_ratio"]].assign(
                             available_at=m["available_at"].dt.as_unit("ns")),
                         on="available_at", direction="backward", tolerance=MAX_METRICS_AGE)
    g = grid.set_index("available_at")
    change = g["oi"] / g["oi"].shift(24) - 1
    return pd.DataFrame({
        "available_at": g.index, "oi_change_24h": change.to_numpy(),
        "oi_change_24h_q10": change.rolling(WINDOW, closed="left", min_periods=MIN_HOURLY_HISTORY).quantile(
            QUANTILE).to_numpy(),
        "accounts_ratio": g["accounts_ratio"].to_numpy(),
        "accounts_q10": g["accounts_ratio"].rolling(WINDOW, closed="left", min_periods=MIN_HOURLY_HISTORY).quantile(
            QUANTILE).to_numpy()})


def _asof(left: pd.DataFrame, right: pd.DataFrame, max_age: pd.Timedelta) -> pd.DataFrame:
    """Jointure vers le passé sur `available_at` (côté gauche : celui de la décision)."""
    right = right.dropna(subset=["available_at"]).assign(
        available_at=lambda d: pd.to_datetime(d["available_at"], utc=True).dt.as_unit("ns"))
    joined = pd.merge_asof(left.sort_values("decision_available_at"), _sorted(right, "available_at").rename(
        columns={"available_at": "_source_at"}), left_on="decision_available_at", right_on="_source_at",
        direction="backward", tolerance=max_age)
    return joined.drop(columns=["_source_at"])


Builder = Callable[[pd.DataFrame], pd.DataFrame]
BUILDERS: dict[str, Builder] = {"funding": funding_table, "premium": premium_table, "metrics": metrics_grid}


def decision_frame(spot_1h: pd.DataFrame, funding: pd.DataFrame, premium: pd.DataFrame, metrics: pd.DataFrame,
                   *, horizons: tuple[int, ...] = HORIZONS, builders: dict[str, Builder] | None = None) -> pd.DataFrame:
    """Une ligne par décision (toutes les 4 h) : variables de positionnement et rendements futurs bruts.
    `builders` n'existe que pour les mutations de l'audit des fuites."""
    build = BUILDERS | (builders or {})
    spot = _sorted(spot_1h, "open_time")
    o, c = spot["open"].to_numpy(float), spot["close"].to_numpy(float)
    times = spot["open_time"]
    out = pd.DataFrame({"open_time": times, "decision_time": times + STEP,
                        "decision_available_at": pd.to_datetime(spot["available_at"], utc=True).dt.as_unit("ns"),
                        "spot_ret_24h": c / np.r_[np.full(24, np.nan), c[:-24]] - 1})
    contiguous_24 = (times - times.shift(24)) == 24 * STEP
    out.loc[~contiguous_24.to_numpy(), "spot_ret_24h"] = np.nan
    for horizon in horizons:
        entry = np.r_[o[1:], np.nan]
        exit_ = np.r_[c[horizon:], np.full(horizon, np.nan)]
        contiguous = ((times.shift(-horizon) - times) == horizon * STEP).to_numpy()
        out[f"ret_{horizon}"] = np.where(contiguous, exit_ / entry - 1, np.nan)
    out = out[(out["decision_time"].dt.hour % DECISION_EVERY == 0).to_numpy()].reset_index(drop=True)
    out = _asof(out, build["funding"](funding), MAX_FUNDING_AGE)
    out = _asof(out, build["premium"](premium), MAX_PREMIUM_AGE)
    out = _asof(out, build["metrics"](metrics), STEP)
    return out.sort_values("decision_time").reset_index(drop=True)


# --- Conditions déclarées (docs/DERIVATIVES.md §Criblage) --------------------------------------------------

def _evaluable(frame: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
    return np.isfinite(frame[list(columns)].to_numpy(float)).all(axis=1)


CONDITIONS: dict[str, tuple[str, tuple[str, ...], Callable[[pd.DataFrame], np.ndarray]]] = {
    "FUNDING_LOW": ("financement moyen des 72 dernières heures sous son 10e centile des 90 jours précédents "
                    "(acheteurs à levier inhabituellement peu nombreux ou vendeurs payés)",
                    ("funding_3d", "funding_3d_q10"),
                    lambda f: (f["funding_3d"] < f["funding_3d_q10"]).to_numpy()),
    "PREMIUM_DISCOUNT": ("prime moyenne des 24 dernières heures sous son 10e centile des 90 jours précédents "
                         "(perpétuel décoté : pression vendeuse sur le marché à terme)",
                         ("premium_24h", "premium_24h_q10"),
                         lambda f: (f["premium_24h"] < f["premium_24h_q10"]).to_numpy()),
    "OI_FLUSH": ("variation 24 h du nombre de contrats ouverts sous son 10e centile des 90 jours précédents ET prix "
                 "Spot en baisse sur 24 h (positions fermées ou liquidées)",
                 ("oi_change_24h", "oi_change_24h_q10", "spot_ret_24h"),
                 lambda f: ((f["oi_change_24h"] < f["oi_change_24h_q10"]) & (f["spot_ret_24h"] < 0)).to_numpy()),
    "ACCOUNTS_SHORT": ("ratio comptes acheteurs / vendeurs sous son 10e centile des 90 jours précédents (comptes "
                       "inhabituellement vendeurs)",
                       ("accounts_ratio", "accounts_q10"),
                       lambda f: (f["accounts_ratio"] < f["accounts_q10"]).to_numpy()),
}


def condition_masks(frame: pd.DataFrame, name: str) -> tuple[np.ndarray, np.ndarray]:
    """(évaluable, événement) pour une condition déclarée."""
    _, columns, rule = CONDITIONS[name]
    evaluable = _evaluable(frame, columns)
    return evaluable, evaluable & np.asarray(rule(frame), dtype=bool)


# --- Contrôle de causalité -----------------------------------------------------------------------------------

def _leaky(builder: Builder) -> Builder:
    """MUTATION volontaire : la table dérivée se croit disponible à l'horodatage BRUT de la donnée (règlement,
    ouverture de bougie, heure d'archive), c'est-à-dire trop tôt."""
    return lambda frame: builder(frame.assign(available_at=frame["time"]))


# Chaque mutation doit être détectée par les variables de SA famille.
MUTATIONS: dict[str, tuple[Builder, tuple[str, ...]]] = {
    "funding": (_leaky(funding_table), ("funding_3d", "funding_3d_q10")),
    "premium": (_leaky(premium_table), ("premium_24h", "premium_24h_q10")),
    "metrics": (_leaky(metrics_grid), ("oi_change_24h", "oi_change_24h_q10", "accounts_ratio", "accounts_q10")),
}


def causality_violations(spot_1h: pd.DataFrame, funding: pd.DataFrame, premium: pd.DataFrame, metrics: pd.DataFrame,
                         *, decisions: list[pd.Timestamp], seed: int = 0,
                         builders: dict[str, Builder] | None = None) -> list[dict]:
    """Pour chaque décision (heure de décision), les variables recalculées avec seulement les données disponibles
    à la décision (selon leur `available_at` brut), puis avec un futur falsifié, doivent égaler le calcul
    complet. Liste vide : causal. `builders` : mutations de l'audit."""
    full = decision_frame(spot_1h, funding, premium, metrics, horizons=(), builders=builders).set_index(
        "decision_time")
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for moment in decisions:
        if moment not in full.index:
            continue
        known_at = full.loc[moment, "decision_available_at"]

        def cut(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            return frame[pd.to_datetime(frame["available_at"], utc=True) <= at]

        def falsify(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            frame = frame.copy()
            future = (pd.to_datetime(frame["available_at"], utc=True) > at).to_numpy()
            for column in frame.columns:
                if column not in ("time", "open_time", "close_time", "available_at") and \
                        pd.api.types.is_numeric_dtype(frame[column]):
                    frame[column] = frame[column].astype(float)
                    frame.loc[future, column] = frame.loc[future, column] * rng.uniform(0.5, 1.5, int(future.sum()))
            return frame

        expected = full.loc[moment, list(FEATURES)].to_numpy(float)
        sources = (spot_1h, funding, premium, metrics)
        for label, frames in (("tronqué", [cut(f) for f in sources]), ("futur falsifié", [falsify(f) for f in sources])):
            got = decision_frame(*frames, horizons=(), builders=builders).set_index("decision_time")
            if moment not in got.index:
                problems.append({"decision_time": str(moment), "check": label, "features": ["(décision absente)"]})
                continue
            values = got.loc[moment, list(FEATURES)].to_numpy(float)
            differs = ~np.isclose(values, expected, rtol=1e-6, atol=1e-12, equal_nan=True)
            if differs.any():
                problems.append({"decision_time": str(moment), "check": label,
                                 "features": [f for f, d in zip(FEATURES, differs, strict=True) if d]})
    return problems
