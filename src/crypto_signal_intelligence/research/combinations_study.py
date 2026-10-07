"""Transactions, placebos, modèle logistique, mesures et décisions du programme « combinaisons »
(docs/COMBINAISONS.md, § 1.4 à 1.7, 3, 4 et 6).

Une heure de déclenchement d'une paire = UNE transaction commune (achat au marché, stop à 2 ATR, tiers à 1, 2 et 3 R,
60 heures), la même pour toutes les règles qui la gardent : les ensembles des règles sont emboîtés. Chaque transaction
exécutée reçoit 20 placebos uniformes (identifiant `COMBINAISONS`) et jusqu'à 20 placebos appariés (même paire, même
année, mêmes états). Simulation : `figures_history.play` et sa copie compilée, importées sans changement.

Les exécutions réelles (`run_bricks`, `run_votes`, `run_confirmation`) sont uniques, exigent un code commité et
inscrivent leurs essais au registre ; `run_counts` ne simule rien (aucun R) et n'inscrit rien.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..features.loader import MissingData
from ..forward import f15
from ..forward.costs import CENTRAL, SCENARIOS, costs_for
from . import combinations as cb
from . import figures_history as fh
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

PLACEBOS = 20
MATCHED_MIN = 5
SEED = 20261007
SAMPLES_R, SAMPLES_C = 10_000, 100_000
MIN_BLOCKS = 10
BLOCKS_EVENT, BLOCKS_STATE = 28, 91
MIN_TRADES, MIN_GAIN_TRADES = 300, 30
TOP_SHARE = 0.01
MAX_PAIR_SHARE, MAX_YEAR_SHARE = 0.25, 0.50
YEARS = tuple(range(2019, 2026))
MIN_POSITIVE_YEARS = 4
LOGIT_FOLDS = (2021, 2022, 2023, 2024, 2025)
LOGIT_MIN_TRAIN = 2000
LOGIT_MIN_FOLD_TRADES = 100
LOGIT_MIN_FOLDS = 4
LOGIT_MIN_POSITIVE_FOLDS = 3
LOGIT_ABSENT_SHARE = 0.01
LOGIT_C = 1.0
LOGIT_MAX_ITER = 1000
VOTE_RULES = {"VOTE_2": 2, "VOTE_3": 3, "VOTE_4": 4}
STEP2_RULES = ("REF_TOUS", *VOTE_RULES, "LOGIT")
STATE_RULES = frozenset({*VOTE_RULES, "LOGIT"})
N_TRIALS_STEP = 10
LEVEL_R = 1 - 0.05 / N_TRIALS_STEP
KINDS = {"bricks": "COMBO_BRIQUES", "votes": "COMBO_VOTES", "confirmation": "COMBO_CONFIRMATION"}
PISTE, PISTE_FRAGILE, INVERSE, INSUFFICIENT, NOTHING = "PISTE", "PISTE_FRAGILE", "INVERSE", "INSUFFISANT", "RIEN"
CONFIRMED, NOT_CONFIRMED = "CONFIRMEE", "NON_CONFIRMEE"
GAIN, LOSS, NO_GAIN = "GAIN_DEMONTRE", "PERTE_DEMONTREE", "GAIN_NON_DEMONTRE"
FRAGILE_PAIRS = "fragile au tirage par paires"
# Règle de repli du § 1.8 : biais maximaux mesurés sous l'hypothèse nulle (données synthétiques), inscrits dans le
# protocole avant toute exécution. Une règle absente de ce tableau a passé le contrôle : seuil 0.
NULL_BIAS: dict[str, dict[str, float]] = {}
CONTROLS_DATE: str | None = None           # date d'inscription des contrôles du § 1.8 (exécution refusée avant)


class AlreadyRun(RuntimeError):
    """Exécution unique déjà faite (ou étape précédente absente) : rien n'est compté."""


class NotReady(RuntimeError):
    """Contrôles du § 1.8 non inscrits : exécution refusée."""


# --- Placebos uniformes (réimplémentation de `trendline_confirmation`, identifiant propre) ---------------------------

def placebo_minutes(key: str, lo_ns: int, hi_ns: int, count: int = PLACEBOS, *, test_id: str = cb.TEST_ID) -> np.ndarray:
    """Minutes tirées uniformément sans remise dans [lo, hi] ; graine déduite de `test_id:clé` (identique à
    `trendline_confirmation.placebo_minutes` avec `test_id="TRENDLINE_CONFIRMATION"`, test d'identité)."""
    span = int((hi_ns - lo_ns) // fh.MINUTE_NS)
    if span < count:
        return np.empty(0, np.int64)
    rng = random.Random(int(hashlib.sha256(f"{test_id}:{key}".encode()).hexdigest()[:16], 16))
    return np.asarray(sorted(lo_ns + k * fh.MINUTE_NS for k in rng.sample(range(span + 1), count)), np.int64)


def _placebo_columns(row: dict, m: fh.Minutes, whens: np.ndarray, prefix: str) -> None:
    """R des placebos (achats au marché aux minutes `whens`, même géométrie en %, même sortie, mêmes frais)."""
    fill_ns = pd.Timestamp(row["fill_at"]).as_unit("ns").value
    offsets = ((fill_ns - whens) // fh.MINUTE_NS).astype(np.int64)
    entry, stop = float(row["entry"]), float(row["stop"])
    third = float(row["tp1"]) - entry
    targets = np.array([entry + k * third for k in (1, 2, 3)])
    hold_ns = f15.HOLD_BARS * f15.TIMEFRAMES["1h"].value
    for s in SCENARIOS:
        costs = costs_for(row["symbol"], s)
        if len(offsets):
            raw, hits = fh._placebos(m.o, m.h, m.lo, m.c, m.ns, fill_ns, offsets, entry, stop, targets, hold_ns,
                                     costs.market, costs.fee, fh.WEIGHTS, True)
        else:
            raw, hits = np.empty(0), np.empty(0, np.int64)
        r = np.array([round(float(x), 6) if np.isfinite(x) else np.nan for x in raw])
        usable = np.isfinite(r)
        handicap = costs.market * (1 + costs.fee) / row["risk_pct"] if row.get("maker") else 0.0
        row[f"{prefix}placebo_n_{s}"] = int(usable.sum())
        row[f"{prefix}placebo_mean_{s}"] = round(float(r[usable].mean()), 6) if usable.any() else None
        for k in (1, 2, 3):
            row[f"{prefix}placebo_tp{k}_{s}"] = float((hits[usable] >= k).mean()) if usable.any() else None
        name = "uexcess_adj" if prefix == "u" else f"{prefix}excess"
        row[f"{name}_{s}"] = (round(row[f"r_{s}"] - float(r[usable].mean()) - handicap, 6) if usable.any() else None)


def with_uniform_placebos(row: dict, m: fh.Minutes, *, lo_ns: int, hi_ns: int, test_id: str = cb.TEST_ID) -> dict:
    """Comme `trendline_confirmation.with_uniform_placebos`, avec l'identifiant de l'étude (§ 1.5 a)."""
    if row["status"] != fh.EXECUTED:
        return row
    _placebo_columns(row, m, placebo_minutes(row["key"], lo_ns, hi_ns, test_id=test_id), "u")
    return row


def matched_seed(key: str) -> int:
    return int(hashlib.sha256(f"{cb.TEST_ID}:APPARIE:{key}".encode()).hexdigest()[:16], 16)


def matched_draw(key: str, candidates: np.ndarray) -> np.ndarray:
    """20 candidats tirés sans remise (tous s'il y en a de 5 à 19, aucun sous 5)."""
    if len(candidates) < MATCHED_MIN:
        return np.empty(0, np.int64)
    if len(candidates) <= PLACEBOS:
        return np.asarray(candidates, np.int64)
    rng = random.Random(matched_seed(key))
    return np.asarray(sorted(candidates[k] for k in rng.sample(range(len(candidates)), PLACEBOS)), np.int64)


def order_start_ns(at: pd.Series | pd.DatetimeIndex, latency: pd.Timedelta) -> np.ndarray:
    """Première minute après la clôture plus la latence (`figures_history.order_window`), en ns."""
    return (pd.DatetimeIndex(at) + latency).ceil("min").as_unit("ns").asi8


# --- Transactions d'une paire -----------------------------------------------------------------------------------------

@dataclass
class PairData:
    symbol: str
    table: pd.DataFrame
    votes: pd.DataFrame
    in_period: np.ndarray
    first_bar: pd.Timestamp
    end: pd.Timestamp
    hashes: dict = field(default_factory=dict)


def pair_data(symbol: str, h1: pd.DataFrame, daily: pd.DataFrame | None, *, end: pd.Timestamp) -> PairData:
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end].reset_index(drop=True)
    table = cb.brick_table(h1, None, daily=daily)
    first = pd.Timestamp(table["open_time"].iloc[0])
    return PairData(symbol, table, cb.votes(table), cb.period_mask(table, first_bar=first, end=end), first, end)


def trigger_rows(data: PairData, bricks: tuple[str, ...] = cb.EVENT_BRICKS) -> np.ndarray:
    """Lignes de déclenchement dans la période : au moins un événement des briques données à cette heure."""
    fired = data.table[list(bricks)].to_numpy(bool).any(axis=1)
    return np.flatnonzero(fired & data.in_period)


def play_rows(data: PairData, rows: np.ndarray, m: fh.Minutes, latency: pd.Timedelta) -> list[dict]:
    """Transaction commune de chaque ligne, avec placebos uniformes et appariés, votes et états de l'heure."""
    table, v = data.table, data.votes
    lo_ns = max(fh.FIRST_DAY, data.first_bar + fh.WARMUP).as_unit("ns").value
    hold_ns = f15.HOLD_BARS * f15.TIMEFRAMES["1h"].value
    hi_ns = min(data.end.as_unit("ns").value, int(m.ns[-1])) - hold_ns
    years = pd.DatetimeIndex(table["at"]).year.to_numpy()
    states = cb.state_key(table)
    starts = order_start_ns(table["at"], latency)
    groups: dict[tuple, np.ndarray] = {}
    eligible = np.flatnonzero(data.in_period)
    for key in set(zip(years[eligible], states[eligible], strict=True)):
        groups[key] = eligible[(years[eligible] == key[0]) & (states[eligible] == key[1])]
    out = []
    for i in rows:
        row_t = table.iloc[i]
        setup = cb.setup_for(data.symbol, row_t)
        row = fh.play(setup, m, data.symbol, latency)
        row["open_time"] = row_t["open_time"]
        row["year"] = int(years[i])
        row["state"] = str(states[i])
        for brick in cb.EVENT_BRICKS:
            row[f"event_{brick}"] = bool(table[brick].iloc[i])
        for brick in cb.VOTERS:
            row[f"vote_{brick}"] = float(v[brick].iloc[i])
        row["n_votes"] = float(v["n_votes"].iloc[i])
        candidates = groups.get((years[i], states[i]), np.empty(0, np.int64))
        candidates = candidates[candidates != i]
        row["matched_candidates"] = int(len(candidates))
        if row["status"] == fh.EXECUTED:
            start, until, hold = fh.order_window(setup, latency)
            exit_info = fh.simulate_fast(m, entry=row["entry"], stop=row["stop"],
                                         targets=[row["entry"] + k * (row["entry"] - row["stop"]) for k in fh.R_MULTIPLES],
                                         order_from=start, order_until=until, hold_minutes=hold, symbol=data.symbol,
                                         scenario=CENTRAL, market_entry=True)
            row["exit_at"] = pd.Timestamp(exit_info["exit_at"])
            with_uniform_placebos(row, m, lo_ns=lo_ns, hi_ns=hi_ns)
            drawn = matched_draw(setup.key, candidates)
            row["matched_rows"] = [int(x) for x in drawn]
            _placebo_columns(row, m, starts[drawn], "t")
        out.append(row)
    return out


# --- Une position par paire (§ 1.4) ----------------------------------------------------------------------------------

def one_position(at: np.ndarray, exit_known: np.ndarray, *, mutation: str | None = None) -> np.ndarray:
    """Masque des transactions jouées « une position par paire » (une paire, une règle, ordre du temps). `at` :
    décision ; `exit_known` : instant où la sortie est connue (fin de la minute de sortie ; +inf si inconnue ou non
    exécutée : NaT pour un déclencheur non exécuté, qui n'ouvre rien). À `t`, on ne sait que si la transaction gardée
    précédente est sortie (sortie connue ≤ t). Mutation `exit_at` (fuite) : on laisse passer le déclencheur si la
    transaction ouverte sortira dans les 12 heures (date de sortie future lue)."""
    keep = np.zeros(len(at), dtype=bool)
    open_until: float = -np.inf
    for k in range(len(at)):
        t = float(at[k])
        blocked = open_until > t
        if mutation == "exit_at" and blocked and open_until - t <= 12 * 3600e9 and np.isfinite(open_until):
            blocked = False
        if blocked:
            continue
        keep[k] = True
        if np.isfinite(exit_known[k]):
            open_until = float(exit_known[k])
        else:
            open_until = -np.inf if np.isnan(exit_known[k]) else np.inf
    return keep


def exit_known_ns(trades: pd.DataFrame) -> np.ndarray:
    """Fin de la minute de sortie (ns) ; NaN pour un déclencheur non exécuté (aucune position ouverte)."""
    out = np.full(len(trades), np.nan)
    done = (trades["status"] == fh.EXECUTED).to_numpy()
    if done.any():
        exits = pd.to_datetime(trades.loc[done, "exit_at"], utc=True) + pd.Timedelta(minutes=1)
        out[done] = exits.dt.as_unit("ns").astype("int64").to_numpy().astype(float)
    return out


def one_position_mask(trades: pd.DataFrame, selected: np.ndarray) -> np.ndarray:
    """« Une position par paire » des déclencheurs `selected` d'une règle, paire par paire."""
    out = np.zeros(len(trades), dtype=bool)
    at = pd.to_datetime(trades["at"], utc=True).dt.as_unit("ns").astype("int64").to_numpy().astype(float)
    known = exit_known_ns(trades)
    for _, idx in trades[selected].groupby("symbol").groups.items():
        rows = np.asarray(sorted(trades.index.get_indexer(idx), key=lambda r: at[r]))
        out[rows[one_position(at[rows], known[rows])]] = True
    return out


# --- Modèle logistique (§ 4.3) -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Logit:
    """Régression logistique L2 (C = 1, ordonnée non pénalisée, variables brutes) : la fonction objectif de
    `sklearn.linear_model.LogisticRegression()` par défaut, dont le minimum est unique (scikit-learn n'est pas
    installé : résolue par Newton, précision d'implémentation inscrite au protocole)."""
    features: tuple[str, ...]
    coef: np.ndarray
    intercept: float
    base_rate: float
    rows: int
    iterations: int

    def predict(self, X: np.ndarray) -> np.ndarray:
        z = np.asarray(X, float) @ self.coef + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


def fit_logit(X: np.ndarray, y: np.ndarray, features: tuple[str, ...], *, c: float = LOGIT_C,
              max_iter: int = LOGIT_MAX_ITER, tol: float = 1e-10) -> Logit:
    X, y = np.asarray(X, float), np.asarray(y, float)
    Z = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(Z.shape[1])
    penalty = np.full(Z.shape[1], 1.0 / c)
    penalty[0] = 0.0
    iterations = 0
    for iterations in range(1, max_iter + 1):           # noqa: B007 - nombre d'itérations gardé
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -500, 500)))
        gradient = Z.T @ (p - y) + penalty * w
        hessian = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.max(np.abs(step)) < tol:
            break
    return Logit(features, w[1:], float(w[0]), float(y.mean()), len(y), iterations)


def logit_objective(w: np.ndarray, X: np.ndarray, y: np.ndarray, c: float = LOGIT_C) -> float:
    """Objectif minimisé (pour le test d'équivalence) : Σ log-pertes + ‖coef‖² / (2C)."""
    z = X @ w[1:] + w[0]
    return float(np.sum(np.logaddexp(0, z) - y * z) + 0.5 / c * np.sum(w[1:] ** 2))


def logit_design(frame: pd.DataFrame, absent: tuple[str, ...]) -> np.ndarray:
    votes = frame[[f"vote_{b}" for b in cb.VOTERS]].to_numpy(float)
    cols = [np.nan_to_num(votes, nan=0.0)]
    for brick in absent:
        cols.append(frame[f"vote_{brick}"].isna().to_numpy(float)[:, None])
    return np.column_stack(cols) if cols else np.zeros((len(frame), 0))


def train_rows(trades: pd.DataFrame, year: int) -> np.ndarray:
    """Lignes d'entraînement du pli `year` : déclencheurs exécutés dont la sortie est terminée avant le 1er janvier
    (purge : aucune issue d'entraînement ne chevauche le test)."""
    done = (trades["status"] == fh.EXECUTED).to_numpy()
    cutoff = pd.Timestamp(f"{year}-01-01", tz="UTC")
    exits = pd.to_datetime(trades["exit_at"], utc=True) + pd.Timedelta(minutes=1)
    return done & (exits <= cutoff).fillna(False).to_numpy()


def fit_fold(trades: pd.DataFrame, year: int) -> tuple[Logit | None, tuple[str, ...], dict]:
    mask = train_rows(trades, year)
    train = trades[mask]
    y = (train[f"r_{CENTRAL}"].astype(float) > 0).to_numpy(float)
    info: dict = {"year": year, "train_rows": int(len(train))}
    if len(train) < LOGIT_MIN_TRAIN or len(set(y.tolist())) < 2:
        info["model"] = None
        return None, (), info
    absent = tuple(b for b in cb.VOTERS if float(train[f"vote_{b}"].isna().mean()) > LOGIT_ABSENT_SHARE)
    names = (*cb.VOTERS, *(f"absente_{b}" for b in absent))
    model = fit_logit(logit_design(train, absent), y, names)
    info |= {"model": {"coef": dict(zip(names, np.round(model.coef, 6).tolist(), strict=True)),
                       "intercept": round(model.intercept, 6), "base_rate": round(model.base_rate, 6),
                       "iterations": model.iterations}, "absent": list(absent)}
    return model, absent, info


def logit_select(trades: pd.DataFrame, models: dict[int, tuple[Logit | None, tuple[str, ...]]]) -> tuple[np.ndarray, np.ndarray]:
    """(achat, probabilité) de chaque déclencheur des plis : p̂ > taux de base de l'entraînement du pli."""
    years = pd.to_datetime(trades["at"], utc=True).dt.year.to_numpy()
    buy = np.zeros(len(trades), dtype=bool)
    prob = np.full(len(trades), np.nan)
    for year, (model, absent) in models.items():
        idx = np.flatnonzero(years == year)
        if model is None or not len(idx):
            continue
        p = model.predict(logit_design(trades.iloc[idx], absent))
        prob[idx] = p
        buy[idx] = p > model.base_rate
    return buy, prob


# --- Intervalles -------------------------------------------------------------------------------------------------------

def _day_blocks(times: np.ndarray, block_days: int) -> tuple[np.ndarray, int]:
    """Indice de bloc de chaque valeur : jours présents triés, regroupés par `block_days` consécutifs (comme
    `metrics.day_block_ci`)."""
    days = pd.to_datetime(times, utc=True).floor("D")
    uniq = np.unique(days.as_unit("ns").asi8)
    rank = np.searchsorted(uniq, days.as_unit("ns").asi8)
    return rank // block_days, math.ceil(len(uniq) / block_days)


def block_ci(values: np.ndarray, times: np.ndarray, *, block_days: int, samples: int, level: float):
    if len(values) == 0:
        return None, 0
    return day_block_ci(np.asarray(values, float), times, block_days=block_days, samples=samples, seed=SEED,
                        level=level, min_blocks=MIN_BLOCKS)


def nested_diff_ci(values: np.ndarray, kept: np.ndarray, times: np.ndarray, *, block_days: int, samples: int,
                   level: float) -> tuple[tuple[float, float] | None, int, float | None]:
    """Différence « moyenne des gardés − moyenne de tous » (ensembles emboîtés), même tirage de blocs de jours
    présents (ceux de l'ensemble complet) appliqué aux deux ensembles. (IC, blocs, différence observée)."""
    values, kept = np.asarray(values, float), np.asarray(kept, bool)
    if not kept.any():
        return None, 0, None
    block, blocks = _day_blocks(times, block_days)
    observed = float(values[kept].mean() - values.mean())
    if blocks < MIN_BLOCKS:
        return None, blocks, observed
    s_all = np.bincount(block, weights=values, minlength=blocks)
    n_all = np.bincount(block, minlength=blocks).astype(float)
    s_k = np.bincount(block, weights=np.where(kept, values, 0.0), minlength=blocks)
    n_k = np.bincount(block, weights=kept.astype(float), minlength=blocks)
    rng = np.random.default_rng(SEED)
    picks = rng.integers(0, blocks, size=(samples, blocks))
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = s_k[picks].sum(axis=1) / n_k[picks].sum(axis=1) - s_all[picks].sum(axis=1) / n_all[picks].sum(axis=1)
    tail = (1 - level) / 2 * 100
    low, high = np.nanpercentile(draws, [tail, 100 - tail])
    return (round(float(low), 4), round(float(high), 4)), blocks, round(observed, 4)


def cross_ci(values: np.ndarray, times: np.ndarray, pairs: np.ndarray, *, block_days: int, samples: int,
             level: float, chunk: int = 2000) -> tuple[float, float] | None:
    """Contrôle de sensibilité (§ 1.6) : blocs de jours et paires tirés indépendamment ; chaque transaction pesée par
    le produit des multiplicités de son bloc et de sa paire."""
    values = np.asarray(values, float)
    if not len(values):
        return None
    block, blocks = _day_blocks(times, block_days)
    if blocks < MIN_BLOCKS:
        return None
    codes, pair = np.unique(np.asarray(pairs), return_inverse=True)
    S = np.zeros((blocks, len(codes)))
    C = np.zeros((blocks, len(codes)))
    np.add.at(S, (block, pair), values)
    np.add.at(C, (block, pair), 1.0)
    rng = np.random.default_rng(SEED)
    draws = np.empty(samples)
    for begin in range(0, samples, chunk):
        size = min(chunk, samples - begin)
        mb = np.stack([np.bincount(r, minlength=blocks) for r in rng.integers(0, blocks, size=(size, blocks))])
        mp = np.stack([np.bincount(r, minlength=len(codes)) for r in rng.integers(0, len(codes), size=(size, len(codes)))])
        with np.errstate(divide="ignore", invalid="ignore"):
            draws[begin:begin + size] = ((mb @ S) * mp).sum(axis=1) / ((mb @ C) * mp).sum(axis=1)
    tail = (1 - level) / 2 * 100
    low, high = np.nanpercentile(draws, [tail, 100 - tail])
    return round(float(low), 4), round(float(high), 4)


# --- Mesure et décisions (§ 1.6, 1.7, 4.4 à 4.6) ----------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    level: float
    samples: int
    block_days: int


def _executed(part: pd.DataFrame) -> pd.DataFrame:
    return part[part["status"] == fh.EXECUTED].sort_values(["fill_at", "key"])


def _times(part: pd.DataFrame) -> np.ndarray:
    return pd.to_datetime(part["fill_at"], utc=True).to_numpy()


def _stat(part: pd.DataFrame, column: str, plan: Plan, *, cross: bool = True) -> dict:
    values = part[column].astype(float).to_numpy()
    ok = np.isfinite(values)
    if not ok.any():
        return {"n": 0, "mean": None, "ci": None, "blocks": 0, "cross_ci": None}
    times = _times(part)[ok]
    ci, blocks = block_ci(values[ok], times, block_days=plan.block_days, samples=plan.samples, level=plan.level)
    out = {"n": int(ok.sum()), "mean": round(float(values[ok].mean()), 4), "ci": ci, "blocks": int(blocks)}
    out["cross_ci"] = (cross_ci(values[ok], times, part["symbol"].to_numpy()[ok], block_days=plan.block_days,
                                samples=plan.samples, level=plan.level) if cross else None)
    return out


def guards(part: pd.DataFrame, scenario: str, *, logit: bool) -> dict:
    """Garde-fous 2 à 4 et 6 du § 1.7, sur l'excès de timing de l'ensemble « tous les déclencheurs »."""
    column = f"texcess_{scenario}"
    done = part[np.isfinite(part[column].astype(float).to_numpy())]
    values = done[column].astype(float).to_numpy()
    out: dict = {"n": int(len(values))}
    if not len(values):
        return out | {"top": False, "pair": False, "year": False, "regular": False, "folds_ok": False}
    drop = math.ceil(TOP_SHARE * len(values))
    rest = np.sort(values)[:len(values) - drop]
    out["mean_without_top"] = round(float(rest.mean()), 4) if len(rest) else None
    out["top"] = bool(len(rest) and rest.mean() > 0)
    total = float(values.sum())
    years = done["year"].to_numpy()
    by_pair = pd.Series(values).groupby(done["symbol"].to_numpy()).sum()
    by_year = pd.Series(values).groupby(years).sum()
    out["pair_share"] = round(float(by_pair.max()) / total, 4) if total > 0 else None
    out["year_share"] = round(float(by_year.max()) / total, 4) if total > 0 else None
    out["pair"] = bool(total > 0 and by_pair.max() / total <= MAX_PAIR_SHARE)
    out["year"] = bool(total > 0 and by_year.max() / total <= MAX_YEAR_SHARE)
    means = pd.Series(values).groupby(years).mean()
    counts = pd.Series(values).groupby(years).size()
    if logit:
        counted = [y for y in LOGIT_FOLDS if counts.get(y, 0) >= LOGIT_MIN_FOLD_TRADES]
        out["folds_counted"] = counted
        out["folds_ok"] = len(counted) >= LOGIT_MIN_FOLDS
        out["positive_folds"] = sum(1 for y in counted if means.get(y, 0.0) > 0)
        out["regular"] = out["positive_folds"] >= LOGIT_MIN_POSITIVE_FOLDS
    else:
        out["positive_years"] = sum(1 for y in YEARS if means.get(y, -1.0) > 0)
        out["regular"] = out["positive_years"] >= MIN_POSITIVE_YEARS
        out["folds_ok"] = True
    out["by_year"] = {int(y): {"n": int(counts[y]), "mean": round(float(means[y]), 4)} for y in means.index}
    return out


def measure_rule(all_part: pd.DataFrame, one_part: pd.DataFrame, plan: Plan, *, cross: bool = True) -> dict:
    """Mesures d'une règle par scénario : excès uniforme et de timing (tous les déclencheurs), R moyen (une position
    par paire), part « régime » (descriptive)."""
    done, one = _executed(all_part), _executed(one_part)
    out = {}
    for s in SCENARIOS:
        u = _stat(done, f"uexcess_adj_{s}", plan, cross=cross)
        t = _stat(done, f"texcess_{s}", plan, cross=cross)
        r = _stat(one, f"r_{s}", plan, cross=cross)
        both = done[[f"uexcess_adj_{s}", f"texcess_{s}"]].astype(float).dropna()
        out[s] = {"triggers": int(len(all_part)), "executed": int(len(done)), "uniform": u, "timing": t, "gain": r,
                  "without_matched": int(done[f"texcess_{s}"].isna().sum()),
                  "regime_share": (round(float((both.iloc[:, 0] - both.iloc[:, 1]).mean()), 4) if len(both) else None),
                  "outcomes": done[f"outcome_{s}"].value_counts().to_dict() if len(done) else {}}
    return out


def decide_gain(m: dict) -> str:
    rows = [m[s]["gain"] for s in SCENARIOS]
    if any(r["n"] < MIN_GAIN_TRADES or r["ci"] is None for r in rows):
        return INSUFFICIENT
    if all(r["ci"][0] > 0 for r in rows):
        return GAIN
    if all(r["ci"][1] < 0 for r in rows):
        return LOSS
    return NO_GAIN


def _insufficient(m: dict) -> bool:
    return any(m[s][k]["n"] < MIN_TRADES or m[s][k]["ci"] is None for s in SCENARIOS for k in ("uniform", "timing"))


def decide_excess(name: str, m: dict, g: dict[str, dict], *, extra: dict[str, bool] | None = None,
                  confirmation: bool = False) -> dict:
    """Décision « excès ». `g` : garde-fous par scénario ; `extra` : conditions supplémentaires de la même décision
    (différence avec `REF_TOUS`, sous-ensemble sans `TRENDLINE`) ; `confirmation` : statuts du § 4.5."""
    bias = NULL_BIAS.get(name, {})
    thr_u, thr_t = max(0.0, bias.get("uniforme", 0.0)), max(0.0, bias.get("timing", 0.0))
    logit = name == "LOGIT"
    reasons: list[str] = []
    if _insufficient(m) or (logit and not all(g[s]["folds_ok"] for s in SCENARIOS)):
        return {"decision": INSUFFICIENT, "reasons": ["moins de 300 transactions, de 10 blocs ou de 4 plis comptés"]}
    above = all(m[s]["uniform"]["ci"][0] > thr_u and m[s]["timing"]["ci"][0] > thr_t for s in SCENARIOS)
    inverse = all(m[s]["timing"]["ci"][1] < 0 for s in SCENARIOS)
    if not above:
        return {"decision": INVERSE if inverse else (NOT_CONFIRMED if confirmation else NOTHING), "reasons": reasons}
    names = ("top", "regular") if confirmation else ("top", "pair", "year", "regular")
    failed = sorted({k for s in SCENARIOS for k in names if not g[s].get(k)})
    for key, ok in (extra or {}).items():
        if ok is None:
            return {"decision": INSUFFICIENT, "reasons": [f"{key} non calculable"]}
        if not ok:
            reasons.append(key)
    if reasons:
        return {"decision": NOT_CONFIRMED if confirmation else NOTHING, "reasons": reasons + failed}
    if failed:
        return {"decision": NOT_CONFIRMED if confirmation else PISTE_FRAGILE, "reasons": failed}
    out = {"decision": CONFIRMED if confirmation else PISTE, "reasons": []}
    fragile = any(m[s][k]["cross_ci"] is not None and m[s][k]["cross_ci"][0] <= 0
                  for s in SCENARIOS for k in ("uniform", "timing"))
    if fragile:
        out["mention"] = FRAGILE_PAIRS
    return out


def diff_condition(ref: pd.DataFrame, kept: np.ndarray, plan: Plan) -> tuple[bool | None, dict]:
    """Différence d'excès de timing « règle − REF_TOUS » (§ 4.4), borne basse > 0 en central et en défavorable."""
    detail: dict = {}
    oks = []
    done_mask = (ref["status"] == fh.EXECUTED).to_numpy()
    for s in SCENARIOS:
        values = ref[f"texcess_{s}"].astype(float).to_numpy()
        ok = done_mask & np.isfinite(values)
        if int((ok & kept).sum()) < MIN_TRADES:
            return None, {"reason": "moins de 300 transactions de la règle"}
        ci, blocks, diff = nested_diff_ci(values[ok], kept[ok], _times(ref)[ok], block_days=plan.block_days,
                                          samples=plan.samples, level=plan.level)
        detail[s] = {"ci": ci, "blocks": blocks, "diff": diff}
        if ci is None:
            return None, detail
        oks.append(ci[0] > 0)
    return all(oks), detail


def subset_condition(part: pd.DataFrame, plan: Plan) -> tuple[bool | None, dict]:
    """§ 4.6 : excès de timing du sous-ensemble emboîté où `TRENDLINE` ne vote pas, borne basse > 0 (niveau de C)."""
    sub = _executed(part[part["vote_TRENDLINE"].astype(float) != 1.0])
    detail = {s: _stat(sub, f"texcess_{s}", plan, cross=False) for s in SCENARIOS}
    if any(detail[s]["ci"] is None for s in SCENARIOS):
        return None, detail
    return all(detail[s]["ci"][0] > 0 for s in SCENARIOS), detail


# --- Règles -------------------------------------------------------------------------------------------------------------

def rule_masks(trades: pd.DataFrame, *, logit_buy: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Déclencheurs gardés par chaque règle (ensemble « tous les déclencheurs »)."""
    out = {b: trades[f"event_{b}"].to_numpy(bool) for b in cb.NEW_BRICKS}
    out["REF_TOUS"] = np.ones(len(trades), dtype=bool)
    for name, k in VOTE_RULES.items():
        out[name] = trades["n_votes"].to_numpy(float) >= k
    if logit_buy is not None:
        out["LOGIT"] = logit_buy
    return out


def evaluate(trades: pd.DataFrame, names: tuple[str, ...], *, level: float, samples: int,
             logit_buy: np.ndarray | None = None, confirmation: bool = False, step2: frozenset[str] = frozenset()) -> dict:
    trades = trades.reset_index(drop=True)
    masks = rule_masks(trades, logit_buy=logit_buy)
    years = trades["year"].to_numpy()
    rows: dict = {}
    for name in names:
        mask = masks[name]
        block = BLOCKS_STATE if name in STATE_RULES else BLOCKS_EVENT
        plan = Plan(level, samples, block)
        all_part = trades[mask]
        one_part = trades[one_position_mask(trades, mask)]
        m = measure_rule(all_part, one_part, plan)
        g = {s: guards(_executed(all_part), s, logit=name == "LOGIT") for s in SCENARIOS}
        extra: dict = {}
        detail: dict = {}
        if name in STATE_RULES:
            ref_mask = np.ones(len(trades), dtype=bool) if name != "LOGIT" else np.isin(years, LOGIT_FOLDS)
            ok, detail["vs_ref"] = diff_condition(trades[ref_mask], mask[ref_mask], Plan(level, samples, BLOCKS_STATE))
            extra["difference_avec_REF_TOUS"] = ok
        if confirmation and name in step2:
            ok, detail["sans_trendline"] = subset_condition(all_part, plan)
            extra["sans_TRENDLINE"] = ok
        if name in VOTE_RULES and not confirmation:          # descriptif : même période que LOGIT
            late = np.isin(years, LOGIT_FOLDS)
            detail["descriptif_2021_2025"] = measure_rule(trades[mask & late], trades[one_position_mask(trades, mask & late)],
                                                          plan, cross=False)
        rows[name] = {"measures": m, "guards": g, "conditions": detail,
                      "excess": decide_excess(name, m, g, extra=extra, confirmation=confirmation),
                      "gain": decide_gain(m), "block_days": block, "one_position_ignored": int(mask.sum() - len(one_part))}
    return rows


# --- Lecture des données réelles ------------------------------------------------------------------------------------

def _load_pair(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.DataFrame | None:
    from .long_history import load_long
    try:
        h1 = load_long(settings, symbol)
    except MissingData:
        return None
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end].reset_index(drop=True)
    return h1 if len(h1) else None


def btc_reference(settings: Settings, end: pd.Timestamp) -> pd.DataFrame:
    btc = _load_pair(settings, MARKET, end)
    if btc is None:
        raise MissingData("BTCUSDT absente du magasin long : état BTC_HAUSSIER impossible")
    return cb.btc_daily(btc)


def _pair_job(args: tuple) -> tuple[str, list[dict] | None, dict]:
    """Une paire : briques, déclencheurs, transactions (contrôle des minutes d'abord)."""
    from .derivatives_screen import fingerprint
    from .minute_history import load_minutes
    from .trendline_confirmation import coverage
    settings, symbol, end, daily, bricks = args
    h1 = _load_pair(settings, symbol, end)
    if h1 is None:
        return symbol, None, {}
    data = pair_data(symbol, h1, daily, end=end)
    hashes: dict = {f"1h/{symbol}": fingerprint(h1[list(cb.READ_COLUMNS[:-1])])}
    rows = trigger_rows(data, bricks)
    if not len(rows):
        return symbol, [], hashes
    try:
        bars = load_minutes(settings, symbol)
    except MissingData:
        return symbol, [], hashes | {"coverage": {"ok": False, "reason": "aucune bougie 1 minute"}}
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    check = coverage(h1, m, first=data.first_bar)
    hashes |= {f"1m/{symbol}": fh.minutes_hash(m), "coverage": check}
    if not check["ok"]:
        return symbol, [], hashes
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    return symbol, play_rows(data, rows, m, latency), hashes


def collect_trades(settings: Settings, symbols: list[str], *, end: pd.Timestamp, bricks: tuple[str, ...], workers: int,
                   say: Callable[[str], None]) -> tuple[pd.DataFrame, dict, list[str]]:
    from .trendline_confirmation import IncompleteMinutes
    daily = btc_reference(settings, end)
    jobs = [(settings, s, end, daily, bricks) for s in symbols]
    rows: list[dict] = []
    hashes: dict = {}
    coverages: dict = {}
    missing: list[str] = []

    def collect(results) -> None:
        for symbol, found, h in results:
            say(symbol)
            if found is None:
                missing.append(symbol)
                continue
            if "coverage" in h:
                coverages[symbol] = h.pop("coverage")
            rows.extend(found)
            hashes.update(h)

    if workers <= 1:
        collect(map(_pair_job, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:
            collect(pool.map(_pair_job, jobs))
    bad = sorted(k for k, v in coverages.items() if not v.get("ok"))
    if bad:
        raise IncompleteMinutes(f"bougies 1 minute incomplètes pour {len(bad)} paires : {bad[:10]} ; rien n'est compté")
    if not rows:
        raise IncompleteMinutes("aucune transaction : rien n'est compté")
    trades = pd.DataFrame(rows).sort_values(["at", "key"]).reset_index(drop=True)
    return trades, hashes | {"coverage": coverages}, missing


# --- Comptages avant l'étude (§ 1.8) : aucune simulation, aucun R --------------------------------------------------------

def flux_yes(table: pd.DataFrame) -> np.ndarray:
    """Brique FLUX de l'étape 3 (§ 5.3) à chaque heure : somme des deltaC des 24 dernières bougies > 0 (NaN si l'une
    manque) ; sert au comptage par année (§ 1.8, descriptif)."""
    delta = pd.Series(table["delta_c"].to_numpy(float))
    sums = delta.rolling(24, min_periods=24).sum().to_numpy()
    return np.where(np.isfinite(sums), (sums > 0).astype(float), np.nan)


def pair_counts(data: PairData) -> dict:
    t, v, period = data.table, data.votes, data.in_period
    years = pd.DatetimeIndex(t["at"]).year.to_numpy()
    states = cb.state_key(t)
    out: dict = {"hours": int(period.sum())}
    out["events"] = {b: {int(y): int(((t[b].to_numpy(bool)) & period & (years == y)).sum()) for y in YEARS}
                     for b in cb.EVENT_BRICKS}
    out["absent"] = {b: int((~t[f"calc_{b}"].to_numpy(bool) & period).sum()) for b in cb.EVENT_BRICKS}
    out["absent"] |= {b: int((np.isnan(t[b].to_numpy(float)) & period).sum()) for b in cb.STATE_BRICKS}
    trig = v["trigger"].to_numpy(bool) & period
    n_votes = v["n_votes"].to_numpy(float)
    rules = {"REF_TOUS": trig, **{k: trig & (n_votes >= q) for k, q in VOTE_RULES.items()},
             **{b: t[b].to_numpy(bool) & period for b in cb.NEW_BRICKS}}
    out["rules_by_year"] = {r: {int(y): int((mask & (years == y)).sum()) for y in YEARS} for r, mask in rules.items()}
    out["rules_by_state"] = {r: pd.Series(states[mask]).value_counts().to_dict() for r, mask in rules.items()}
    cells = pd.DataFrame({"year": years[period], "state": states[period]}).value_counts()
    out["candidates"] = {f"{y}|{s}": int(n) for (y, s), n in cells.items()}
    sizes = {(int(y), str(s)): int(n) for (y, s), n in cells.items()}
    cand = np.array([sizes.get((int(y), str(s)), 0) - 1 for y, s in zip(years, states, strict=True)])
    out["matched"] = {r: {"triggers": int(mask.sum()), "under_5": int((mask & (cand < MATCHED_MIN)).sum()),
                          "under_20": int((mask & (cand < PLACEBOS)).sum())} for r, mask in rules.items()}
    flux = flux_yes(t)
    out["flux"] = {int(y): {"hours": int(np.isfinite(flux[period & (years == y)]).sum()),
                            "yes": int(np.nansum(flux[period & (years == y)]))} for y in YEARS}
    out["votes_distribution"] = pd.Series(n_votes[trig]).value_counts().sort_index().to_dict()
    out["vote_yes"] = {b: int((v[b].to_numpy(float)[trig] == 1).sum()) for b in cb.VOTERS}
    out["vote_absent"] = {b: int(np.isnan(v[b].to_numpy(float)[trig]).sum()) for b in cb.VOTERS}
    return out


def _count_job(args: tuple) -> tuple[str, dict | None]:
    settings, symbol, end, daily, check_minutes = args
    h1 = _load_pair(settings, symbol, end)
    if h1 is None:
        return symbol, None
    data = pair_data(symbol, h1, daily, end=end)
    out = pair_counts(data)
    if check_minutes:
        from .minute_history import load_minutes
        from .trendline_confirmation import coverage
        try:
            bars = load_minutes(settings, symbol)
            bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= end, ["open_time", "open", "high", "low", "close"]]
            out["coverage"] = coverage(h1, fh.Minutes.from_frame(bars.reset_index(drop=True)), first=data.first_bar)
        except MissingData:
            out["coverage"] = {"ok": False, "reason": "aucune bougie 1 minute"}
    return symbol, out


def _merge(total: dict, part: dict) -> None:
    for key, value in part.items():
        if isinstance(value, dict):
            _merge(total.setdefault(key, {}), value)
        elif isinstance(value, int | float) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def run_counts(settings: Settings, *, symbols: list[str] | None = None, workers: int = 4, check_minutes: bool = True,
               progress: Callable[[str], None] | None = None) -> dict:
    """Comptages du § 1.8 sur les paires de recherche, DEVELOPMENT seulement : aucune transaction simulée, aucun R."""
    say = progress or (lambda _text: None)
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    daily = btc_reference(settings, end)
    pairs = symbols or list(RESEARCH_UNIVERSE)
    jobs = [(settings, s, end, daily, check_minutes) for s in pairs]
    per_pair: dict = {}
    if workers <= 1:
        results = list(map(_count_job, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(_count_job, jobs))
    total: dict = {}
    missing = []
    for symbol, counts in results:
        say(symbol)
        if counts is None:
            missing.append(symbol)
            continue
        per_pair[symbol] = counts
        _merge(total, {k: v for k, v in counts.items() if k != "coverage"})
    total["fold_rows_by_year"] = total.get("rules_by_year", {}).get("REF_TOUS", {})
    payload = {"generated_for": cb.DOC, "period_end": str(end), "pairs": pairs, "missing": missing, "total": total,
               "per_pair": per_pair, "note": "comptages sans aucune transaction simulée ni R (§ 1.8)"}
    directory = settings.reports_dir / f"COMBO-COMPTAGES-{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%SZ}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "comptages.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["directory"] = str(directory)
    return payload


# --- Exécutions uniques (étapes 1, 2 et confirmation) -------------------------------------------------------------------

def _check_ready(settings: Settings, kind: str, allow_dirty: bool) -> tuple[str, ExperimentRegistry]:
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    if CONTROLS_DATE is None:
        raise NotReady("contrôles du § 1.8 non inscrits (CONTROLS_DATE) : exécution refusée")
    registry = ExperimentRegistry(settings.experiments_db)
    with registry.connect() as db:
        done = db.execute("SELECT COUNT(*) FROM runs WHERE kind=?", (kind,)).fetchone()[0]
    if done:
        raise AlreadyRun(f"{kind} a déjà été exécuté : exécution unique (§ 9)")
    return state, registry


def _latest(registry: ExperimentRegistry, kind: str) -> dict:
    with registry.connect() as db:
        row = db.execute("SELECT run_id FROM runs WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone()
    if row is None:
        raise AlreadyRun(f"étape précédente absente ({kind}) : ordre du § 9")
    got = registry.get(row[0])
    assert got is not None
    return got


def _record(settings: Settings, registry: ExperimentRegistry, *, kind: str, prefix: str, now: datetime, state: str,
            n_trials: int, level: float, samples: int, rows: dict, trades: pd.DataFrame, hashes: dict,
            missing: list[str], hypothesis: str, extra: dict | None = None) -> dict:
    program = registry.program_trials() + n_trials
    run_id = new_run_id(prefix)
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    decisions = {k: {"excess": v["excess"]["decision"], "gain": v["gain"]} for k, v in rows.items()}
    payload = {"run_id": run_id, "kind": kind, "n_trials": n_trials, "program_trials": program, "level": round(level, 6),
               "samples": samples, "rows": rows, "missing": missing, "doc": cb.DOC} | (extra or {})
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=kind, hypothesis=hypothesis, strategy=kind,
                    strategy_version=1, variant="définitions figées (docs/COMBINAISONS.md)",
                    params={"placebos": PLACEBOS, "matched_min": MATCHED_MIN, "stop_atr": cb.STOP_ATR,
                            "blocks": {"evenement": BLOCKS_EVENT, "etats": BLOCKS_STATE}, "samples": samples,
                            "seed": SEED, "null_bias": NULL_BIAS, "controls": CONTROLS_DATE},
                    period_label="DEVELOPMENT", period_start=str(fh.FIRST_DAY.date()),
                    period_end=str(trades["at"].max()) if len(trades) else "", universe=sorted(set(trades["symbol"])),
                    data_hashes=hashes, git_commit=state, dependencies=dependency_versions(), seed=SEED,
                    cost_scenario="central et défavorable (forward/costs.py)",
                    simulation_rules={"execution": "figures_history.play (achat au marché, stop 2 ATR, tiers 1/2/3 R, 60 h)",
                                      "placebos": "20 uniformes (COMBINAISONS) + 20 appariés (paire, année, états)"},
                    metrics={"n_trials": n_trials, "program_trials": program, "decisions": decisions},
                    status="COMPLETED", report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    return payload


def run_bricks(settings: Settings, *, now: datetime, allow_dirty: bool = False, workers: int = 4,
               progress: Callable[[str], None] | None = None) -> dict:
    """Étape 1 (§ 3) : les 5 briques nouvelles seules sur les 40 paires, 10 essais."""
    kind = KINDS["bricks"]
    state, registry = _check_ready(settings, kind, allow_dirty)
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    trades, hashes, missing = collect_trades(settings, list(RESEARCH_UNIVERSE), end=end, bricks=cb.NEW_BRICKS,
                                             workers=workers, say=progress or (lambda _t: None))
    rows = evaluate(trades, cb.NEW_BRICKS, level=LEVEL_R, samples=SAMPLES_R)
    return _record(settings, registry, kind=kind, prefix="CMB1", now=now, state=state, n_trials=N_TRIALS_STEP,
                   level=LEVEL_R, samples=SAMPLES_R, rows=rows, trades=trades, hashes=hashes, missing=missing,
                   hypothesis="chacune des 5 briques nouvelles (profil de volume, VWAP ancré, CVD, absorption), jouée avec "
                              "la transaction commune, fait-elle mieux que des achats au hasard, uniformes et appariés ?")


def logit_models(trades: pd.DataFrame) -> tuple[dict, list[dict]]:
    models, infos = {}, []
    for year in LOGIT_FOLDS:
        model, absent, info = fit_fold(trades, year)
        models[year] = (model, absent)
        infos.append(info)
    return models, infos


def describe_folds(trades: pd.DataFrame, infos: list[dict]) -> None:
    """Descriptif des plis (§ 4.3) : AUC et score de Brier contre le taux de base, part des déclencheurs retenus."""
    from ..ml.logistic import auc
    years = pd.to_datetime(trades["at"], utc=True).dt.year.to_numpy()
    done = (trades["status"] == fh.EXECUTED).to_numpy()
    for info in infos:
        test = done & (years == info["year"]) & np.isfinite(trades["logit_p"].to_numpy(float))
        if not test.any() or info.get("model") is None:
            continue
        y = (trades.loc[test, f"r_{CENTRAL}"].astype(float) > 0).to_numpy(float)
        p = trades.loc[test, "logit_p"].to_numpy(float)
        base = info["model"]["base_rate"]
        info["test"] = {"rows": int(test.sum()), "auc": auc(y, p), "brier": round(float(np.mean((p - y) ** 2)), 6),
                        "brier_base": round(float(np.mean((base - y) ** 2)), 6),
                        "kept_share": round(float(trades.loc[test, "logit_buy"].mean()), 4)}


def run_votes(settings: Settings, *, now: datetime, allow_dirty: bool = False, workers: int = 4,
              progress: Callable[[str], None] | None = None) -> dict:
    """Étape 2 (§ 4) : REF_TOUS, VOTE_2/3/4 et LOGIT sur les 40 paires, 10 essais."""
    kind = KINDS["votes"]
    state, registry = _check_ready(settings, kind, allow_dirty)
    _latest(registry, KINDS["bricks"])
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    trades, hashes, missing = collect_trades(settings, list(RESEARCH_UNIVERSE), end=end, bricks=cb.EVENT_BRICKS,
                                             workers=workers, say=progress or (lambda _t: None))
    models, infos = logit_models(trades)
    buy, prob = logit_select(trades, models)
    trades["logit_buy"], trades["logit_p"] = buy, prob
    describe_folds(trades, infos)
    rows = evaluate(trades, STEP2_RULES, level=LEVEL_R, samples=SAMPLES_R, logit_buy=buy)
    return _record(settings, registry, kind=kind, prefix="CMB2", now=now, state=state, n_trials=N_TRIALS_STEP,
                   level=LEVEL_R, samples=SAMPLES_R, rows=rows, trades=trades, hashes=hashes, missing=missing,
                   hypothesis="un vote de 2, 3 ou 4 briques sur 9, ou un modèle logistique annuel, choisit-il mieux le "
                              "moment d'achat que tous les déclencheurs et que le hasard apparié ?",
                   extra={"logit_folds": infos})


def confirmation_plan(pistes1: list[str], pistes2: list[str]) -> tuple[int, float]:
    """Un seul Bonferroni (§ 4.5) : 2 · (m₁ + m₂) essais, niveau 1 − 0,05 / (2 · (m₁ + m₂)) ; aucune piste : rien."""
    count = len(pistes1) + len(pistes2)
    if not count:
        raise AlreadyRun("aucune règle PISTE sur R : pas d'exécution sur C, rien compté (§ 4.5)")
    return 2 * count, 1 - 0.05 / (2 * count)


def run_confirmation(settings: Settings, *, now: datetime, allow_dirty: bool = False, workers: int = 4,
                     progress: Callable[[str], None] | None = None, symbols: list[str] | None = None) -> dict:
    """Confirmation sur les paires C (§ 4.5) des règles `PISTE` des étapes 1 et 2, en une seule exécution."""
    from .trendline_confirmation import universe
    kind = KINDS["confirmation"]
    state, registry = _check_ready(settings, kind, allow_dirty)
    step1, step2 = _latest(registry, KINDS["bricks"]), _latest(registry, KINDS["votes"])
    pistes1 = [k for k, v in step1["metrics"]["decisions"].items() if v["excess"] == PISTE]
    pistes2 = [k for k, v in step2["metrics"]["decisions"].items() if v["excess"] == PISTE]
    n_trials, level = confirmation_plan(pistes1, pistes2)
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    bricks = cb.EVENT_BRICKS if pistes2 else cb.NEW_BRICKS
    trades, hashes, missing = collect_trades(settings, symbols or universe(settings), end=end, bricks=bricks,
                                             workers=workers, say=progress or (lambda _t: None))
    buy = None
    infos: list[dict] = []
    if "LOGIT" in pistes2:
        r_trades = pd.read_parquet(f"{step2['report_dir']}/trades.parquet")
        models, infos = logit_models(r_trades)               # modèles ajustés sur les paires R, appliqués tels quels
        buy, prob = logit_select(trades, models)
        trades["logit_buy"], trades["logit_p"] = buy, prob
    rows = evaluate(trades, (*pistes1, *pistes2), level=level, samples=SAMPLES_C, logit_buy=buy, confirmation=True,
                    step2=frozenset(pistes2))
    return _record(settings, registry, kind=kind, prefix="CMBC", now=now, state=state, n_trials=n_trials, level=level,
                   samples=SAMPLES_C, rows=rows, trades=trades, hashes=hashes, missing=missing,
                   hypothesis="les règles PISTE des étapes 1 et 2 se confirment-elles, sans aucun changement, sur les "
                              "paires du top 40 à date jamais utilisées pour ces briques ?",
                   extra={"pistes": {"etape1": pistes1, "etape2": pistes2}, "logit_folds": infos,
                          "runs": {"etape1": step1["run_id"], "etape2": step2["run_id"]}})

