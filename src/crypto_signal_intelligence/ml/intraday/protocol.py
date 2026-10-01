"""Protocole ML intraday v5 (docs/ML_INTRADAY.md), déclaré et commité avant toute exécution.

Sélection (`select`, DEVELOPMENT seulement) :
1. audit des fuites sur données réelles (tronqué, futur falsifié, mutation 4 h) — échec → arrêt ;
2. 7 validations glissantes (12 mois : 10 d'ajustement + 2 d'étalonnage purgés ; 6 mois de validation) ;
3. grille déclarée : 2 cibles × 4 horizons × 9 modèles × 3 marges = 216 systèmes ; portefeuille simulé
   avec les limites CENTRALISÉES ; règle de stabilité (Sharpe > 0 dans ≥ 70 % des validations,
   ≥ 200 trades) ;
4. variantes sur le système de référence, une à la fois : familles de variables (6), puis méta-filtre
   et abstention de même sévérité (2) ;
5. système retenu (le meilleur Sharpe médian parmi les admissibles) ou conclusion « aucun avantage
   démontré » ; analyse du système retenu (ou de la référence, en diagnostic) : robustesse, références,
   incertitude par blocs, trades exceptionnels, périodes et contextes, journal de chaque décision.
Estimation (`final`, FINAL_TEST) : une seule consultation, enregistrée, seulement si un système est
admissible, que la vérification en bougies 1 min a été faite et que le code et la configuration qui
décident sont identiques à ceux de la sélection.
Tout est enregistré dans le registre des expériences, échecs compris. Aucun ordre, aucun signal publié.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ...config import CostScenario, Settings
from ...features.higher_tf import resample_complete
from ...features.loader import data_hashes
from ...research.backtest_run import run_context
from ...research.experiments import ExperimentRegistry, git_state, new_run_id
from ...research.protocol import FinalTestLocked
from ...research.protocol import period as resolve_period
from ...risk.exposure import RiskLimits
from ..logistic import fit_logistic
from . import dataset as ds
from . import finer
from .models import SPECS, SPECS_BY_NAME, Payoff, calibration_report, fit, fit_platt
from .portfolio import (
    block_bootstrap,
    breakdown,
    buy_and_hold,
    daily_returns,
    exceptional_dependence,
    random_entries,
    run_book,
    series_metrics,
    trade_mean_ci,
)

PROTOCOL_VERSION = 5
KIND_SELECT, KIND_FINAL = "ML_INTRADAY_SELECT", "ML_INTRADAY_FINAL"
STRATEGY_ID = "ML_INTRADAY"
MARGINS = (0.0, 0.0005, 0.0010)
TRAIN_MONTHS, CALIB_MONTHS, VALID_MONTHS = 12, 2, 6
STABILITY_SHARE = 0.70
MIN_TRADES = 200
MIN_EVALUATED_FOLDS = 3
MIN_CALIB_ROWS = 200
RANDOM_DRAWS = 200
ALL_FAMILIES = tuple(ds.FAMILIES)
# Le calendrier (heure, jour) est présent dans chaque variante : chaque comparaison isole UNE famille ;
# « +contexte » = tout sauf le marché (BTC), « sans calendrier » mesure l'apport du calendrier.
FAMILY_VARIANTS: dict[str, tuple[str, ...]] = {
    "prix": ("prix", "calendrier"),
    "prix+volume": ("prix", "volume", "calendrier"),
    "prix+transactions": ("prix", "transactions", "calendrier"),
    "prix+volume+transactions": ("prix", "volume", "transactions", "calendrier"),
    "+contexte": ("prix", "volume", "transactions", "contexte", "calendrier"),
    "sans calendrier": ("prix", "volume", "transactions", "contexte", "marche"),
}
META_FEATURES = ("p", "expected", "rv_96", "atr_pct", "h1_trend_bull", "h1_trend_bear", "h1_vol_high", "h1_vol_low",
                 "btc_ret_24h", "r_96", "vol_z_96", "taker_ratio_4")
META_MIN_SIGNALS = 300
META_MIN_MINORITY_PER_FEATURE = 10
DECLARED_TRIALS = 2 * len(ds.HORIZONS) * len(SPECS) * len(MARGINS) + len(FAMILY_VARIANTS) + 2
AUDIT_PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
AUDIT_TIMES = 4
FEATURE_INDEX = {name: i for i, name in enumerate(ds.FEATURES)}
# Contexte inconnu (jointure périmée ou absente) → aucune décision, comme en service (§8).
CONTEXT_REQUIRED = ("h1_ret_24h", "h1_atr_pct", "h4_ret_6", "h4_atr_pct")
# Modules et réglages qui DÉCIDENT : la période finale exige qu'ils soient identiques à ceux de la sélection.
DECISION_MODULES = ("ml/intraday/dataset.py", "ml/intraday/models.py", "ml/intraday/portfolio.py",
                    "ml/intraday/protocol.py", "ml/logistic.py", "risk/exposure.py", "features/higher_tf.py",
                    "features/indicators.py", "features/builder.py", "regimes/classifier.py", "data/quality.py",
                    "domain/market.py")



def reserves(program_trials_before: int, declared: int = DECLARED_TRIALS) -> list[str]:
    return [
        "Univers : les 16 paires actuelles, choisies aujourd'hui (filtre halal) et toutes cotées sur toute la "
        "période : biais du survivant possible ; l'univers reproductible dans le passé (cotations, retraits) "
        "est la première expérience avancée (§11).",
        f"Sélection sur {declared} essais : le meilleur résultat de validation est optimiste par construction ; "
        "seule la période finale, consultée une fois, juge l'approche.",
        f"Le programme de recherche comptait déjà {program_trials_before} essais sur DEVELOPMENT avant cette "
        "exécution (stratégies A-C, criblage D-I, méta-labeling) : un résultat isolé favorable doit être lu avec "
        "ce nombre en tête.",
        "Si le signe du Sharpe de chaque validation était un pile ou face, un système serait « admissible » "
        "(au moins 5 sur 7) environ 23 % du temps, et au moins une des 6 familles serait « conservée » par "
        "hasard environ 79 % du temps : « admissible » ou « conservée » ne démontre rien seul.",
        "Abstention de même sévérité : le nombre de signaux gardés vient du méta-filtre du même pli (sans ses "
        "issues) ; c'est une référence de comparaison, jamais un système retenu.",
        "Moteur : remplissage complet, ordre intra-bougie défavorable, capital réalisé, pas d'impact de "
        "marché ; cibles traversant un trou de données exclues ; vérification en bougies 1 min obligatoire "
        "avant la période finale.",
        "Aucun ordre, aucun signal publié : un système retenu n'ouvrirait qu'une phase shadow prospective.",
    ]


def source_fingerprint(package_root: Path | None = None) -> str:
    """Empreinte du code qui décide (fins de ligne normalisées)."""
    root = package_root or Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative in DECISION_MODULES:
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def config_fingerprint(settings: Settings) -> str:
    """Empreinte des réglages qui décident : coûts, limites, régimes, univers, protocole."""
    payload = {"costs": {k: v.model_dump() for k, v in settings.costs.items()}, "risk": settings.risk.model_dump(),
               "regimes": settings.regimes.model_dump(), "symbols": list(settings.data.symbols),
               "history_start": str(settings.data.history_start), "seed": settings.protocol.seed}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def require_clean_code(settings: Settings, *, allow_dirty: bool) -> str:
    """Versions reproductibles : code commité (et dépôt git présent) avant tout chargement de données."""
    state = git_state(settings.root)
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise RuntimeError(f"code non commité ou sans dépôt git ({state}) : exécution refusée (versions "
                           "reproductibles ; --allow-dirty pour un essai local, enregistré comme tel)")
    return state


# --- Systèmes et validations -------------------------------------------------------------------

@dataclass(frozen=True)
class System:
    """Un système = cible + horizon + modèle + marge d'abstention + variables + filtre éventuel."""
    kind: str
    horizon: int
    model: str
    margin: float
    families: tuple[str, ...] = ALL_FAMILIES
    filter: str = "none"        # none | meta | matched (abstention de même sévérité que le méta-filtre)

    @property
    def variant(self) -> str:
        names = [n for n, f in FAMILY_VARIANTS.items() if f == self.families]
        label = names[0] if names else ("tout" if self.families == ALL_FAMILIES else "+".join(self.families))
        return label if self.filter == "none" else f"{label}|{self.filter}"

    @property
    def key(self) -> str:
        return f"{self.kind}_H{self.horizon}_{self.model}_m{round(self.margin * 1e4)}bp_{self.variant}"

    @property
    def features(self) -> tuple[str, ...]:
        return ds.feature_set(self.families)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "horizon": self.horizon, "model": self.model, "margin": self.margin,
                "families": list(self.families), "filter": self.filter, "key": self.key}

    @classmethod
    def from_dict(cls, data: dict) -> System:
        return cls(data["kind"], int(data["horizon"]), data["model"], float(data["margin"]),
                   tuple(data["families"]), data.get("filter", "none"))


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: pd.Timestamp
    calib_start: pd.Timestamp
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp

    def to_dict(self) -> dict:
        return {"index": self.index, "train_start": str(self.train_start), "calib_start": str(self.calib_start),
                "valid_start": str(self.valid_start), "valid_end": str(self.valid_end)}


def make_folds(first_valid, end) -> list[Fold]:
    """Validations de VALID_MONTHS mois à partir de `first_valid`, chacune précédée de TRAIN_MONTHS mois
    d'entraînement (les CALIB_MONTHS derniers servent à l'étalonnage)."""
    folds: list[Fold] = []
    start, last = pd.Timestamp(first_valid), pd.Timestamp(end)
    while start < last:
        following = start + pd.DateOffset(months=VALID_MONTHS)
        folds.append(Fold(len(folds), start - pd.DateOffset(months=TRAIN_MONTHS),
                          start - pd.DateOffset(months=CALIB_MONTHS), start,
                          min(following - pd.Timedelta(seconds=1), last)))
        start = following
    return folds


def required_positive(evaluated: int) -> int:
    return math.ceil(STABILITY_SHARE * evaluated - 1e-9)


# --- Données -------------------------------------------------------------------------------------

@dataclass
class Prepared:
    meta: pd.DataFrame                 # symbol, open_time, decision_time, gap_recent, cibles
    X: np.ndarray                      # variables (float32), colonnes dans l'ordre de ds.FEATURES
    inputs: dict[str, dict[str, pd.DataFrame]]
    common: dict
    daily_closes: pd.DataFrame
    costs: dict[str, CostScenario]
    end: pd.Timestamp
    symbols: list[str]                 # ordre des paires dans `meta` (alignement des cibles recalculées)
    _columns: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def column(self, name: str) -> np.ndarray:
        """Colonne en tableau numpy (horodatages : datetime64 UTC sans fuseau, jamais des objets Python)."""
        if name not in self._columns:
            series = self.meta[name]
            if isinstance(series.dtype, pd.DatetimeTZDtype):
                series = series.dt.tz_convert(None)
            self._columns[name] = series.to_numpy()
        return self._columns[name]

    def context_known(self) -> np.ndarray:
        if "_context" not in self._columns:
            columns = [FEATURE_INDEX[name] for name in CONTEXT_REQUIRED]
            self._columns["_context"] = np.isfinite(self.X[:, columns]).all(axis=1)
        return self._columns["_context"]


def prepare(settings: Settings, *, end, progress: Callable[[str], None]) -> Prepared:
    """Données de toutes les paires jusqu'à `end` (inclus), variables et cibles en coûts centraux."""
    inputs, common = run_context(settings)
    limit = pd.Timestamp(end)
    inputs = {symbol: {name: frame[frame["open_time"] <= limit].reset_index(drop=True)
                       for name, frame in data.items()} for symbol, data in inputs.items()}
    common["data_hashes"] = {symbol: data_hashes(data) for symbol, data in inputs.items()}
    common["dependencies"] = dict(common["dependencies"])
    for package in ("lightgbm", "xgboost"):
        try:
            common["dependencies"][package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            common["dependencies"][package] = "absent"
    lock = settings.root / "pylock.toml"
    common["dependencies"]["pylock_sha256"] = (hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
                                               if lock.exists() else "absent")
    metas, matrices = [], []
    for symbol in settings.data.symbols:
        progress(f"variables {symbol}")
        data = inputs[symbol]
        frame = ds.pair_dataset(data["setup"], data["context"], data["btc"], symbol=symbol,
                                regimes=settings.regimes, costs=settings.costs["central"])
        matrices.append(frame[list(ds.FEATURES)].to_numpy(np.float32))
        metas.append(frame.drop(columns=list(ds.FEATURES)))
    meta = pd.concat(metas, ignore_index=True)
    meta["symbol"] = meta["symbol"].astype("category")
    closes = {symbol: data["context"].set_index("open_time")["close"].resample("D").last()
              for symbol, data in inputs.items() if not data["context"].empty}
    return Prepared(meta, np.vstack(matrices), inputs, common, pd.DataFrame(closes), dict(settings.costs), limit,
                    list(settings.data.symbols))


def scenario_targets(prep: Prepared, symbols: list[str], kind: str, horizon: int, costs: CostScenario,
                     delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Cibles recalculées sous un autre scénario de coûts / retard, alignées sur `prep.meta`."""
    nets, bars = [], []
    for symbol in symbols:
        net, offset = ds.targets(prep.inputs[symbol]["setup"], costs, horizon, kind, delay=delay)
        nets.append(net)
        bars.append(offset)
    return np.concatenate(nets), np.concatenate(bars)


# --- Audit des fuites ----------------------------------------------------------------------------

def _leaky_resampler(candles: pd.DataFrame, hours: int) -> pd.DataFrame:
    """MUTATION volontaire : bougie 4 h visible dès la fin de sa première heure (fuite)."""
    bars = resample_complete(candles, hours)
    return bars.assign(available_at=bars["open_time"] + pd.Timedelta(hours=1, seconds=3))


def leak_audit(settings: Settings, prep: Prepared, *, seed: int) -> dict:
    """Variables recalculées avec seulement le passé, puis avec un futur falsifié : identiques ; la
    mutation (4 h visible trop tôt) doit être détectée. Instants tirés au hasard, à 1 h 30 dans un bloc
    4 h (là où une bougie 4 h en formation fuirait)."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    mutation: list[dict] = []
    for symbol in [s for s in AUDIT_PAIRS if s in prep.inputs]:
        data = prep.inputs[symbol]
        setup = data["setup"]
        if len(setup) < 5000:
            continue
        days = setup["open_time"].dt.floor("D").unique()
        picks = sorted(rng.choice(days[60:-30], size=AUDIT_TIMES, replace=False))
        moments = [pd.Timestamp(day) + pd.Timedelta(hours=int(rng.integers(0, 6)) * 4 + 1, minutes=30)
                   for day in picks]
        lo, hi = moments[0] - pd.Timedelta(days=45), moments[-1] + pd.Timedelta(days=3)

        def window(frame: pd.DataFrame, lo=lo, hi=hi) -> pd.DataFrame:
            return frame[(frame["open_time"] >= lo) & (frame["open_time"] <= hi)].reset_index(drop=True)

        for resampler, found in ((resample_complete, violations), (_leaky_resampler, mutation)):
            found += ds.causality_violations(window(setup), window(data["context"]), window(data["btc"]),
                                             symbol=symbol, regimes=settings.regimes,
                                             costs=settings.costs["central"], decisions=moments, seed=seed,
                                             resampler=resampler)
    return {"violations": violations, "mutation_detected": bool(mutation), "checked_pairs": list(AUDIT_PAIRS),
            "times_per_pair": AUDIT_TIMES, "passed": not violations and bool(mutation)}


# --- Ajustement, étalonnage, prédiction ---------------------------------------------------------

@dataclass
class Split:
    fit: np.ndarray
    calib: np.ndarray
    valid: np.ndarray


def split_rows(prep: Prepared, fold: Fold, kind: str, horizon: int) -> Split:
    """Purge : ajustement et étalonnage ne contiennent que des lignes dont la barrière verticale (t+H)
    précède le bloc suivant ; la validation, que des décisions dont t+H reste dans la validation et dont
    le contexte 1 h / 4 h est connu (sinon aucune décision, comme en service)."""
    meta = prep.meta
    decision = meta["decision_time"]
    barrier = decision + horizon * ds.STEP
    usable = meta[f"net_{kind}_{horizon}"].notna() & ~meta["gap_recent"]
    train = ds.training_rows(meta, kind, horizon)
    fit_mask = train & (decision >= fold.train_start) & (barrier <= fold.calib_start)
    calib_mask = train & (decision >= fold.calib_start) & (barrier <= fold.valid_start)
    valid_mask = usable & (decision >= fold.valid_start) & (barrier <= fold.valid_end) & prep.context_known()
    return Split(np.flatnonzero(fit_mask.to_numpy()), np.flatnonzero(calib_mask.to_numpy()),
                 np.flatnonzero(valid_mask.to_numpy()))


@dataclass
class Prediction:
    rows: np.ndarray            # lignes de validation (positions dans prep.meta)
    p: np.ndarray               # probabilité étalonnée de « rendement net > 0 »
    expected: np.ndarray        # espérance nette
    payoff: Payoff
    base_rate: float
    quality: dict
    model: Any                  # (modèle, étalonnage de Platt)
    fingerprint: str
    n_fit: int
    n_calib: int


def predict_fold(prep: Prepared, rows: Split, system: System, *, seed: int) -> Prediction:
    columns = [FEATURE_INDEX[f] for f in system.features]
    net = prep.column(f"net_{system.kind}_{system.horizon}").astype(float)
    if len(rows.calib) < MIN_CALIB_ROWS:
        raise ValueError(f"étalonnage trop court ({len(rows.calib)} lignes)")
    y_fit, y_calib = net[rows.fit] > 0, net[rows.calib] > 0
    if y_calib.all() or not y_calib.any():
        raise ValueError("une seule classe dans l'étalonnage")
    spec = SPECS_BY_NAME[system.model]
    model = fit(spec, prep.X[np.ix_(rows.fit, columns)], y_fit, seed=seed)
    platt = fit_platt(model.predict_proba(prep.X[np.ix_(rows.calib, columns)]), y_calib)
    p = platt(model.predict_proba(prep.X[np.ix_(rows.valid, columns)])) if len(rows.valid) else np.array([])
    payoff = Payoff.from_returns(net[rows.fit])
    base_rate = float(y_fit.mean())
    return Prediction(rows.valid, p, payoff.expected(p), payoff, base_rate,
                      calibration_report(p, net[rows.valid] > 0, base_rate), (model, platt),
                      spec.fingerprint(system.features), int(len(rows.fit)), int(len(rows.calib)))


def signals(prep: Prepared, system: System, prediction: Prediction, *, net: np.ndarray | None = None,
            bars: np.ndarray | None = None) -> pd.DataFrame:
    """Candidats du système : décisions de validation dont l'espérance nette dépasse la marge."""
    keep = prediction.expected > system.margin
    rows = prediction.rows[keep]
    net = prep.column(f"net_{system.kind}_{system.horizon}") if net is None else net
    bars = prep.column(f"bars_{system.kind}_{system.horizon}") if bars is None else bars
    frame = pd.DataFrame({"row": rows, "symbol": np.asarray(prep.column("symbol"))[rows].astype(str),
                          "decision_time": prep.column("decision_time")[rows], "bars": bars[rows],
                          "net": np.asarray(net, dtype=float)[rows], "score": prediction.expected[keep],
                          "p": prediction.p[keep], "expected": prediction.expected[keep]})
    for name in META_FEATURES:
        if name in FEATURE_INDEX:
            frame[name] = prep.X[rows, FEATURE_INDEX[name]]
    return frame


def fold_metrics(trades: pd.DataFrame, fold: Fold, *, valid_rows: int, submitted: int) -> dict:
    stats = series_metrics(daily_returns(trades, fold.valid_start, fold.valid_end))
    return {"fold": fold.index, "sharpe": stats["sharpe"], "total_return": stats["total_return"],
            "max_drawdown": stats["max_drawdown"], "trades": int(len(trades)),
            "avg_net": round(float(trades["net"].mean()), 6) if len(trades) else None,
            "submitted": int(submitted), "valid_rows": int(valid_rows),
            "abstention": round(1 - submitted / valid_rows, 4) if valid_rows else None}


# --- Grille principale ---------------------------------------------------------------------------

def run_grid(prep: Prepared, folds: list[Fold], limits: RiskLimits, *, seed: int, progress: Callable[[str], None],
             on_rows: Callable[[list[dict]], None] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Les 216 systèmes de base sur chaque validation : (résultats par pli, qualité des probabilités)."""
    results, quality = [], []
    for fold in folds:
        for kind in ds.TARGETS:
            for horizon in ds.HORIZONS:
                rows = split_rows(prep, fold, kind, horizon)
                batch: list[dict] = []
                for spec in SPECS:
                    progress(f"pli {fold.index + 1}/{len(folds)} {kind} H{horizon} {spec.name}")
                    base = System(kind, horizon, spec.name, MARGINS[0])
                    try:
                        prediction = predict_fold(prep, rows, base, seed=seed)
                    except Exception as exc:  # noqa: BLE001 - un essai en échec est enregistré, pas masqué
                        for margin in MARGINS:
                            batch.append(replace(base, margin=margin).to_dict() | {
                                "fold": fold.index, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    quality.append({"kind": kind, "horizon": horizon, "model": spec.name, "fold": fold.index,
                                    "n_fit": prediction.n_fit, "n_calib": prediction.n_calib,
                                    "base_rate": round(prediction.base_rate, 4),
                                    **{k: v for k, v in prediction.quality.items() if k != "reliability"}})
                    for margin in MARGINS:
                        system = replace(base, margin=margin)
                        candidates = signals(prep, system, prediction)
                        trades, _ = run_book(candidates, limits)
                        batch.append(system.to_dict() | {"status": "OK", "error": ""} | fold_metrics(
                            trades, fold, valid_rows=len(prediction.rows), submitted=len(candidates)))
                results += batch
                if on_rows:
                    on_rows(batch)
    return pd.DataFrame(results), pd.DataFrame(quality)


def summarize(results: pd.DataFrame, evaluated_folds: dict[str, int] | None = None) -> pd.DataFrame:
    """Une ligne par système : validations positives, trades, Sharpe médian (pli sans trade = 0), admissible.

    `evaluated_folds` : nombre de validations évaluables par système (défaut : toutes celles présentes)."""
    if results.empty:
        return pd.DataFrame()
    rows = []
    n_folds = int(results["fold"].nunique())
    for key, group in results.groupby("key", sort=False):
        ok = group[group["status"] == "OK"]
        evaluated = (evaluated_folds or {}).get(str(key), n_folds)
        sharpes = ok["sharpe"].astype(float).fillna(0.0).tolist() + [0.0] * max(evaluated - len(ok), 0)
        positive = int((ok["sharpe"].astype(float) > 0).sum())
        trades = int(ok["trades"].sum())
        first = group.iloc[0]
        rows.append({"key": key, "kind": first["kind"], "horizon": int(first["horizon"]), "model": first["model"],
                     "margin": float(first["margin"]), "families": first["families"], "filter": first["filter"],
                     "evaluated_folds": evaluated, "positive_folds": positive, "trades": trades,
                     "median_sharpe": round(float(np.median(sharpes)), 4) if sharpes else 0.0,
                     "sharpe_by_fold": [None if pd.isna(v) else round(float(v), 3) for v in ok["sharpe"]],
                     "failed_folds": int((group["status"] != "OK").sum()),
                     "admissible": bool(evaluated >= MIN_EVALUATED_FOLDS and positive >= required_positive(evaluated)
                                        and trades >= MIN_TRADES)})
    table = pd.DataFrame(rows)
    return table.sort_values(["admissible", "median_sharpe"], ascending=[False, False]).reset_index(drop=True)


def compare_targets(summary: pd.DataFrame, results: pd.DataFrame) -> dict:
    """Horizon fixe contre triple barrière, à horizon, modèle et marge égaux : Sharpe par validation."""
    base = results[(results["filter"] == "none") & (results["status"] == "OK")]
    pivot = base.pivot_table(index=["horizon", "model", "margin", "fold"], columns="kind", values="sharpe",
                             aggfunc="first").fillna(0.0)
    if not {"fh", "tb"} <= set(pivot.columns):
        return {}
    wins = (pivot["tb"] > pivot["fh"]).groupby(level=[0, 1, 2]).sum()
    folds = pivot.groupby(level=[0, 1, 2]).size()
    medians = summary[summary["filter"] == "none"].groupby("kind")["median_sharpe"].median()
    return {"pairs": int(len(wins)), "tb_beats_fh_in_majority": int((wins > folds / 2).sum()),
            "median_of_median_sharpe": {k: round(float(v), 4) for k, v in medians.items()},
            "admissible": {k: int(v) for k, v in summary[summary["filter"] == "none"].groupby("kind")["admissible"]
                           .sum().items()}}


# --- Système complet sur des validations (référence, variantes, analyse, période finale) ---------

@dataclass
class FoldRun:
    fold: Fold
    prediction: Prediction
    candidates: pd.DataFrame        # E > marge (avant filtre)
    submitted: np.ndarray           # masque des candidats soumis au registre de risque
    trades: pd.DataFrame
    status: np.ndarray              # par candidat : ENTER, raison du refus, ou FILTERED
    metrics: dict
    # OK | NOT_EVALUABLE (méta-filtre sans historique : tous les candidats sont soumis, comme la base)
    # | FAILED (ajustement impossible : aucun trade, rendements nuls). Tous les plis comptent dans la sélection
    # et l'analyse ; seuls les plis NOT_EVALUABLE sont exclus de la comparaison méta-filtre / référence.
    state: str = "OK"


def _failed_prediction(error: Exception) -> Prediction:
    empty = np.array([], dtype=float)
    return Prediction(np.array([], dtype=np.int64), empty, empty, Payoff(0.0, 0.0), float("nan"),
                      {"n": 0, "error": f"{type(error).__name__}: {error}"}, None, "", 0, 0)


def _meta_mask(history: pd.DataFrame, current: pd.DataFrame) -> np.ndarray | None:
    """Méta-filtre : logistique L2 sur les signaux HORS ENTRAÎNEMENT des validations précédentes.
    None si l'historique est insuffisant (pli non évaluable)."""
    minority_needed = META_MIN_MINORITY_PER_FEATURE * len(META_FEATURES)
    if len(history) < META_MIN_SIGNALS or current.empty:
        return None
    y = (history["net"].to_numpy(float) > 0).astype(float)
    if min(y.sum(), len(y) - y.sum()) < minority_needed:
        return None
    X = history[list(META_FEATURES)].to_numpy(float)
    medians = np.nanmedian(X, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    model = fit_logistic(np.where(np.isnan(X), medians, X), y, META_FEATURES, l2=1.0)
    Z = current[list(META_FEATURES)].to_numpy(float)
    return model.predict_proba(np.where(np.isnan(Z), medians, Z)) > float(y.mean())


def run_system(prep: Prepared, system: System, folds: list[Fold], limits: RiskLimits, *, seed: int,
               progress: Callable[[str], None] = lambda _t: None) -> list[FoldRun]:
    runs: list[FoldRun] = []
    history: list[pd.DataFrame] = []
    for fold in folds:
        progress(f"{system.key} : pli {fold.index + 1}/{len(folds)}")
        state = "OK"
        try:
            prediction = predict_fold(prep, split_rows(prep, fold, system.kind, system.horizon), system, seed=seed)
        except Exception as exc:  # noqa: BLE001 - pli en échec : enregistré, compté comme sans gain
            prediction, state = _failed_prediction(exc), "FAILED"
        candidates = signals(prep, system, prediction)
        submitted = np.ones(len(candidates), dtype=bool)
        if system.filter != "none" and state == "OK":
            past = pd.concat(history, ignore_index=True) if history else candidates.iloc[0:0]
            mask = _meta_mask(past, candidates)
            if mask is None:
                state = "NOT_EVALUABLE"
            elif system.filter == "meta":
                submitted = mask
            else:                                        # même nombre de signaux, les plus forts en espérance
                order = np.argsort(-candidates["score"].to_numpy(), kind="stable")
                submitted = np.zeros(len(candidates), dtype=bool)
                submitted[order[:int(mask.sum())]] = True
        history.append(candidates)
        trades, status = run_book(candidates[submitted].reset_index(drop=True), limits)
        full_status = np.full(len(candidates), "FILTERED", dtype=object)
        full_status[submitted] = status
        if not trades.empty:                             # rang dans les candidats complets
            trades["candidate"] = np.flatnonzero(submitted)[trades["candidate"].to_numpy(int)]
        runs.append(FoldRun(fold, prediction, candidates, submitted, trades, full_status,
                            fold_metrics(trades, fold, valid_rows=len(prediction.rows),
                                         submitted=int(submitted.sum())), state))
    return runs


def runs_to_rows(system: System, runs: list[FoldRun]) -> list[dict]:
    return [system.to_dict() | {"status": run.state, "error": run.prediction.quality.get("error", "")} | run.metrics
            | {"auc": run.prediction.quality.get("auc"), "brier_skill": run.prediction.quality.get("brier_skill")}
            for run in runs]


def beats(variant: list[FoldRun], reference: list[FoldRun]) -> tuple[int, int]:
    """(validations où la variante a un meilleur Sharpe, validations évaluables) ; sans trade ou pli en
    échec = 0 ; seuls les plis où le méta-filtre n'a pas encore d'historique sont exclus."""
    pairs = [(v.metrics["sharpe"] or 0.0, r.metrics["sharpe"] or 0.0)
             for v, r in zip(variant, reference, strict=True) if v.state != "NOT_EVALUABLE"]
    return sum(a > b for a, b in pairs), len(pairs)


def kept(wins: int, evaluated: int) -> bool:
    return evaluated >= MIN_EVALUATED_FOLDS and wins >= required_positive(evaluated)


# --- Analyse d'un système ------------------------------------------------------------------------

def _labels(candidates: pd.DataFrame) -> pd.DataFrame:
    bull = candidates.get("h1_trend_bull", pd.Series(np.nan, index=candidates.index))
    bear = candidates.get("h1_trend_bear", pd.Series(np.nan, index=candidates.index))
    high = candidates.get("h1_vol_high", pd.Series(np.nan, index=candidates.index))
    low = candidates.get("h1_vol_low", pd.Series(np.nan, index=candidates.index))
    btc = candidates.get("btc_ret_24h", pd.Series(np.nan, index=candidates.index))
    return pd.DataFrame({
        "tendance_1h": np.select([bull == 1, bear == 1, bull.isna()], ["hausse", "baisse", "inconnue"], "neutre"),
        "volatilite_1h": np.select([high == 1, low == 1, high.isna()], ["haute", "basse", "inconnue"], "normale"),
        "btc_24h": np.select([btc > 0, btc <= 0], ["hausse", "baisse"], "inconnu"),
    }, index=candidates.index)


def all_trades(runs: list[FoldRun]) -> pd.DataFrame:
    frames = []
    for run in runs:
        if run.trades.empty:
            continue
        labels = _labels(run.candidates).iloc[run.trades["candidate"].to_numpy(int)].reset_index(drop=True)
        frames.append(pd.concat([run.trades.reset_index(drop=True), labels], axis=1).assign(fold=run.fold.index))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["symbol", "entry_time", "exit_time", "notional", "net", "pnl", "candidate", "fold"])


def replay(prep: Prepared, system: System, runs: list[FoldRun], limits: RiskLimits, scenario: str,
           delay: int | None = None) -> dict:
    """Mêmes décisions, rendements recalculés sous un scénario de coûts et de retard d'entrée."""
    costs = prep.costs[scenario]
    delay = costs.extra_entry_delay_bars if delay is None else delay
    net, bars = scenario_targets(prep, prep.symbols, system.kind, system.horizon, costs, delay)
    folds, frames = [], []
    for run in runs:
        rows = run.candidates["row"].to_numpy(int)[run.submitted]
        candidates = run.candidates[run.submitted].assign(net=net[rows], bars=bars[rows])
        exit_time = pd.to_datetime(candidates["decision_time"], utc=True) + candidates["bars"].clip(lower=0) * ds.STEP
        inside = exit_time <= run.fold.valid_end                     # même purge qu'en central
        candidates = candidates[np.isfinite(candidates["net"]) & (candidates["bars"] >= 0)
                                & inside.to_numpy()].reset_index(drop=True)
        trades, _ = run_book(candidates, limits)
        folds.append(fold_metrics(trades, run.fold, valid_rows=len(run.prediction.rows), submitted=len(candidates)))
        frames.append(trades)
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame(columns=["net", "pnl"])
    return {"scenario": scenario, "entry_delay_bars": delay,
            "total_return_by_fold": [f["total_return"] for f in folds],
            "sharpe_by_fold": [f["sharpe"] for f in folds],
            "avg_net_per_trade": round(float(trades["net"].mean()), 6) if len(trades) else None,
            "trades": int(len(trades)), "positive_folds": sum((f["sharpe"] or 0) > 0 for f in folds)}


def aggregate(runs: list[FoldRun], settings: Settings) -> dict:
    """Plis enchaînés : rendements journaliers de chaque pli (capital du pli) mis bout à bout — équivalent
    à un compte unique, la taille des positions étant proportionnelle au capital — puis mesures du
    portefeuille, IC par blocs circulaires et mesures par trade."""
    protocol = settings.protocol
    trades = all_trades(runs)
    returns = (pd.concat([daily_returns(run.trades, run.fold.valid_start, run.fold.valid_end) for run in runs])
               if runs else pd.Series(dtype=float))
    out = series_metrics(returns) | {"trades": int(len(trades)), "days": int(len(returns))}
    out |= block_bootstrap(returns.to_numpy(), block_days=protocol.bootstrap_block_days,
                           samples=protocol.bootstrap_samples, seed=protocol.seed)
    if trades.empty:
        return out | {"turnover_per_year": 0.0, "avg_net_per_trade": None, "avg_net_ci95": None, "win_rate": None,
                      "pnl_share_max_symbol": None, "pnl_share_max_quarter": None}
    entry = pd.to_datetime(trades["entry_time"], utc=True)
    gains = float(trades["pnl"].sum())
    quarters = entry.dt.tz_localize(None).dt.to_period("Q").astype(str)
    return out | {
        "turnover_per_year": round(float(2 * trades["notional"].sum() / max(len(returns) / 365, 1e-9)), 2),
        "avg_net_per_trade": round(float(trades["net"].mean()), 6),
        "avg_net_ci95": trade_mean_ci(trades["net"].to_numpy(float), entry, block_days=protocol.bootstrap_block_days,
                                      samples=protocol.bootstrap_samples, seed=protocol.seed),
        "win_rate": round(float((trades["net"] > 0).mean()), 4),
        "pnl_share_max_symbol": round(float(trades.groupby("symbol")["pnl"].sum().max() / gains), 4) if gains > 0 else None,
        "pnl_share_max_quarter": round(float(trades.groupby(quarters)["pnl"].sum().max() / gains), 4) if gains > 0 else None,
    }


def analyse(prep: Prepared, system: System, runs: list[FoldRun], limits: RiskLimits, *, settings: Settings,
            progress: Callable[[str], None]) -> dict:
    protocol = settings.protocol
    trades = all_trades(runs)
    progress("analyse : références et entrées au hasard")
    by_fold = []
    for run in runs:
        net = prep.column(f"net_{system.kind}_{system.horizon}")
        bars = prep.column(f"bars_{system.kind}_{system.horizon}")
        rows = run.prediction.rows
        pool = pd.DataFrame({"symbol": np.asarray(prep.column("symbol"))[rows].astype(str),
                             "decision_time": prep.column("decision_time")[rows], "bars": bars[rows],
                             "net": np.asarray(net, dtype=float)[rows]})
        by_fold.append(run.metrics | {
            "benchmarks": buy_and_hold(prep.daily_closes, run.fold.valid_start, run.fold.valid_end),
            "random_entries": random_entries(pool, int(run.submitted.sum()), limits, run.fold.valid_start,
                                             run.fold.valid_end, draws=RANDOM_DRAWS, seed=protocol.seed + run.fold.index),
            "calibration": run.prediction.quality, "state": run.state})
    progress("analyse : robustesse (coûts, retard)")
    robustness = [replay(prep, system, runs, limits, "central", delay=1)]
    robustness += [replay(prep, system, runs, limits, name) for name in ("adverse", "stress") if name in prep.costs]
    return {
        "system": system.to_dict(),
        "by_fold": by_fold,
        "aggregate": aggregate(runs, settings),
        "exceptional_trades": exceptional_dependence(trades),
        "breakdown": breakdown(trades, ("tendance_1h", "volatilite_1h", "btc_24h")) if len(trades) else {},
        "robustness": robustness,
    }


def journal(prep: Prepared, system: System, runs: list[FoldRun], model_hashes: dict[int, str]) -> pd.DataFrame:
    """Journal de CHAQUE décision de validation, abstentions comprises : données, modèle, prédiction,
    coûts, décision et résultat observé (le rendement qu'aurait eu l'entrée, même refusée)."""
    costs = prep.costs["central"]
    round_trip_bps = 2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps)
    setup_hash = {s: h.get("setup", "") for s, h in prep.common["data_hashes"].items()}
    frames = []
    for run in runs:
        rows = run.prediction.rows
        decision = np.full(len(rows), "ABSTAIN", dtype=object)
        position = {int(r): i for i, r in enumerate(rows)}
        for i, row in enumerate(run.candidates["row"].to_numpy(int)):
            decision[position[row]] = run.status[i]
        symbols = np.asarray(prep.column("symbol"))[rows].astype(str)
        frames.append(pd.DataFrame({
            "fold": run.fold.index, "symbol": symbols,
            "decision_time": pd.to_datetime(prep.column("decision_time")[rows], utc=True),
            "data_hash": [setup_hash.get(s, "") for s in symbols], "model_spec": run.prediction.fingerprint,
            "model_sha256": model_hashes.get(run.fold.index, ""),
            "system": system.key, "p": run.prediction.p.astype("float32"),
            "expected_net": run.prediction.expected.astype("float32"), "margin": system.margin,
            "decision": decision, "cost_round_trip_bps": round_trip_bps,
            "observed_net": np.asarray(prep.column(f"net_{system.kind}_{system.horizon}"))[rows],
            "exit_bars": np.asarray(prep.column(f"bars_{system.kind}_{system.horizon}"))[rows],
            "fold_state": run.state}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def save_models(runs: list[FoldRun], directory: Path) -> tuple[dict[str, str], dict[int, str]]:
    """Modèles et étalonnages de chaque validation (versions reproductibles) et leurs SHA-256 :
    (empreinte par fichier, empreinte du modèle par pli)."""
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    by_fold: dict[int, str] = {}
    for run in runs:
        if run.prediction.model is None:
            continue
        model, platt = run.prediction.model
        stem = f"pli{run.fold.index}_{run.prediction.fingerprint}"
        if hasattr(model, "save"):
            path = directory / f"{stem}.{model.family}.{'json' if model.family == 'xgboost' else 'txt'}"
            model.save(str(path))
        else:
            path = directory / f"{stem}.logistic.json"
            path.write_text(json.dumps(model.model.to_dict() | {"medians": model.medians.tolist()}), encoding="utf-8")
        calibration = directory / f"{stem}.platt.json"
        calibration.write_text(json.dumps(platt.model.to_dict()), encoding="utf-8")
        for item in (path, calibration):
            files[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
        by_fold[run.fold.index] = files[path.name]
    return files, by_fold


# --- Sélection (DEVELOPMENT) ---------------------------------------------------------------------

@dataclass
class SelectResult:
    run_id: str
    conclusion: str
    final_system: dict | None
    reference: dict
    summary: pd.DataFrame
    leak_audit: dict
    report_dir: Path
    payload: dict = field(default_factory=dict, repr=False)


NO_EDGE = "AUCUN_AVANTAGE_DEMONTRE"
ADMISSIBLE = "SYSTEME_ADMISSIBLE"


def select(settings: Settings, *, now: datetime, allow_dirty: bool = False,
           progress: Callable[[str], None] | None = None) -> SelectResult:
    say = progress or (lambda _text: None)
    period = resolve_period(settings, "development", now=now)
    run_id = new_run_id("MLI")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    experiments = ExperimentRegistry(settings.experiments_db)
    limits = RiskLimits.from_settings(settings.risk)
    seed = settings.protocol.seed
    state: dict = {"evaluated": 0, "common": {}}
    program_before = experiments.program_trials(period.label)
    try:
        require_clean_code(settings, allow_dirty=allow_dirty)
        say("données")
        prep = prepare(settings, end=period.end, progress=say)
        state["common"] = prep.common
        say("audit des fuites")
        audit = leak_audit(settings, prep, seed=seed)
        (report_dir / "leak_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        if not audit["passed"]:
            raise RuntimeError(f"audit des fuites en échec : {len(audit['violations'])} écart(s), mutation détectée : "
                               f"{audit['mutation_detected']}")
        folds = make_folds(pd.Timestamp(period.start) + pd.DateOffset(months=TRAIN_MONTHS), period.end)
        grid_path = report_dir / "grid.csv"
        seen: set[str] = set()

        def checkpoint(batch: list[dict]) -> None:
            pd.DataFrame(batch).to_csv(grid_path, mode="a", header=not grid_path.exists(), index=False)
            seen.update(row["key"] for row in batch)
            state["evaluated"] = len(seen)

        results, quality = run_grid(prep, folds, limits, seed=seed, progress=say, on_rows=checkpoint)
        state["evaluated"] = int(results["key"].nunique()) if not results.empty else 0
        quality.to_csv(report_dir / "calibration.csv", index=False)
        summary = summarize(results)
        admissible = summary[summary["admissible"]]
        ref_row = (admissible if not admissible.empty else summary).iloc[0]
        reference = System(ref_row["kind"], int(ref_row["horizon"]), ref_row["model"], float(ref_row["margin"]))
        reference_status = "ADMISSIBLE" if not admissible.empty else "DIAGNOSTIC"

        say("variantes : familles de variables")
        ref_runs = run_system(prep, reference, folds, limits, seed=seed, progress=say)
        variant_rows, decisions = [], []
        family_systems: dict[str, list[FoldRun]] = {}
        for name, families in FAMILY_VARIANTS.items():
            system = replace(reference, families=families)
            runs = run_system(prep, system, folds, limits, seed=seed, progress=say)
            family_systems[system.key] = runs
            variant_rows += runs_to_rows(system, runs)
            wins, evaluated = beats(runs, ref_runs)
            decisions.append({"variant": name, "key": system.key, "wins_vs_reference": wins, "evaluated": evaluated,
                              "kept": kept(wins, evaluated)})
        state["evaluated"] += len(FAMILY_VARIANTS)
        kept_families = [d for d in decisions if d["kept"]]
        base_system, base_runs = reference, ref_runs
        if kept_families:
            table = summarize(pd.DataFrame([r for r in variant_rows if r["key"] in {d["key"] for d in kept_families}]))
            best = table.iloc[0]
            base_system = replace(reference, families=tuple(best["families"]))
            base_runs = family_systems[base_system.key]

        say("variantes : méta-filtre et abstention de même sévérité")
        meta_system, matched_system = replace(base_system, filter="meta"), replace(base_system, filter="matched")
        meta_runs = run_system(prep, meta_system, folds, limits, seed=seed, progress=say)
        matched_runs = run_system(prep, matched_system, folds, limits, seed=seed, progress=say)
        state["evaluated"] += 2
        variant_rows += runs_to_rows(meta_system, meta_runs) + runs_to_rows(matched_system, matched_runs)
        wins_ref, evaluated_ref = beats(meta_runs, base_runs)
        wins_matched, evaluated_matched = beats(meta_runs, matched_runs)
        meta_kept = kept(wins_ref, evaluated_ref) and kept(wins_matched, evaluated_matched)
        decisions.append({"variant": "méta-filtre", "key": meta_system.key, "wins_vs_reference": wins_ref,
                          "wins_vs_matched_abstention": wins_matched, "evaluated": evaluated_ref, "kept": meta_kept})

        variants = pd.DataFrame(variant_rows)
        variants.to_csv(report_dir / "variants.csv", index=False)
        # Un pli NOT_EVALUABLE du méta-filtre trade tous les candidats (comme la base) : il compte dans la
        # stabilité et le Sharpe médian ; il n'est exclu que de la comparaison méta-filtre / référence.
        variant_summary = summarize(variants.assign(status=variants["status"].replace({"NOT_EVALUABLE": "OK"})))
        eligible_keys = {d["key"] for d in decisions if d["kept"]}
        eligible = pd.concat([admissible, variant_summary[variant_summary["key"].isin(eligible_keys)
                                                          & variant_summary["admissible"]]], ignore_index=True)
        final_system: System | None = None
        if not eligible.empty:
            best = eligible.sort_values("median_sharpe", ascending=False).iloc[0]
            final_system = System(best["kind"], int(best["horizon"]), best["model"], float(best["margin"]),
                                  tuple(best["families"]), best["filter"])
        conclusion = ADMISSIBLE if final_system else NO_EDGE

        analysed = final_system or reference
        runs_by_key = {reference.key: ref_runs, meta_system.key: meta_runs, matched_system.key: matched_runs,
                       **family_systems}
        analysed_runs = runs_by_key.get(analysed.key) or run_system(prep, analysed, folds, limits, seed=seed,
                                                                    progress=say)
        analysis = analyse(prep, analysed, analysed_runs, limits, settings=settings, progress=say)
        say("journal des décisions et modèles")
        model_hashes, fold_hashes = save_models(analysed_runs, report_dir / "models")
        journal(prep, analysed, analysed_runs, fold_hashes).to_parquet(report_dir / "decisions.parquet",
                                                                       compression="zstd", index=False)

        n_trials = state["evaluated"]
        program_trials = program_before + n_trials
        edges = _grid_edges(analysed)
        summary.to_csv(report_dir / "summary_systems.csv", index=False)
        payload = {
            "run_id": run_id, "protocol_version": PROTOCOL_VERSION, "conclusion": conclusion,
            "final_system": final_system.to_dict() if final_system else None,
            "reference": reference.to_dict() | {"status": reference_status},
            "analysed_system_status": "RETENU" if final_system else "DIAGNOSTIC (non admissible)",
            "folds": [f.to_dict() for f in folds], "limits": limits.__dict__,
            "n_trials": n_trials, "declared_trials": DECLARED_TRIALS, "program_trials": program_trials,
            "admissible_count": int(len(admissible)), "top_systems": _records(summary.head(15)),
            "target_comparison": compare_targets(summary, results),
            "variants": decisions, "variant_systems": _records(variant_summary),
            "analysis": analysis, "leak_audit": {k: v for k, v in audit.items() if k != "violations"},
            "models": model_hashes, "minute_check": {"done": False, "required_before_final": True},
            "grid_edges": edges, "source_fingerprint": source_fingerprint(),
            "config_fingerprint": config_fingerprint(settings), "reserves": reserves(program_before),
        }
        _write(report_dir, payload)
        experiments.record(
            run_id=run_id, created_at=now.isoformat(), kind=KIND_SELECT,
            hypothesis="un modèle (logistique, LightGBM, XGBoost) sur 15 min + contexte 1 h/4 h sélectionne des "
                       "entrées intraday d'espérance nette positive, stable d'une validation à l'autre",
            strategy=STRATEGY_ID, strategy_version=PROTOCOL_VERSION, variant=f"protocole v{PROTOCOL_VERSION}",
            params={"targets": list(ds.TARGETS), "horizons": list(ds.HORIZONS), "models": [s.name for s in SPECS],
                    "margins": list(MARGINS), "family_variants": FAMILY_VARIANTS, "meta_features": list(META_FEATURES),
                    "barrier_k": ds.BARRIER_K},
            period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
            cost_scenario="central (robustesse : central + 1 bougie, adverse, stress)",
            simulation_rules={"limits": limits.__dict__, "folds": "12 mois (10 + 2 étalonnage) / 6 mois",
                              "engine": "ouverture t+1, sortie selon la cible, ordre intra-bougie défavorable"},
            metrics={"conclusion": conclusion, "verdict": conclusion, "n_trials": n_trials,
                     "program_trials": program_trials, "final_system": payload["final_system"],
                     "reference": payload["reference"], "admissible_count": payload["admissible_count"],
                     "source_fingerprint": payload["source_fingerprint"],
                     "config_fingerprint": payload["config_fingerprint"]},
            status="COMPLETED", report_dir=str(report_dir), **prep.common)
        return SelectResult(run_id, conclusion, payload["final_system"], payload["reference"], summary, audit,
                            report_dir, payload)
    except Exception as exc:
        _record_failure(experiments, run_id, now, period, state, report_dir, exc)
        raise


def _record_failure(experiments: ExperimentRegistry, run_id: str, now: datetime, period, state: dict,
                    report_dir: Path, exc: Exception) -> None:
    """Un échec est enregistré avec ses paramètres et les essais déjà évalués (ils ont été vus)."""
    common = state.get("common") or {"git_commit": "inconnu", "dependencies": {}, "seed": 0, "universe": [],
                                     "data_hashes": {}}
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind=KIND_SELECT, hypothesis="protocole ML intraday",
        strategy=STRATEGY_ID, strategy_version=PROTOCOL_VERSION, variant=f"protocole v{PROTOCOL_VERSION}",
        params={}, period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
        cost_scenario="central", simulation_rules={},
        metrics={"verdict": "FAILED", "error": f"{type(exc).__name__}: {exc}", "n_trials": int(state.get("evaluated", 0))},
        status="FAILED", report_dir=str(report_dir), **common)


def _grid_edges(system: System) -> list[str]:
    """Un système retenu au bord de la grille déclarée invite à la prudence (optimum peut-être hors grille)."""
    edges = []
    if system.horizon in (min(ds.HORIZONS), max(ds.HORIZONS)):
        edges.append(f"horizon H={system.horizon} au bord de {list(ds.HORIZONS)}")
    if system.margin in (min(MARGINS), max(MARGINS)):
        edges.append(f"marge {system.margin:.2%} au bord de {[f'{m:.2%}' for m in MARGINS]}")
    return edges


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", force_ascii=False)) if not frame.empty else []


def _fmt(value, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return f"{value:.2%}" if pct else f"{value}"


def _write(report_dir: Path, payload: dict) -> None:
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    analysis = payload["analysis"]
    system = analysis["system"]
    lines = [f"# ML intraday — {payload['run_id']} (protocole v{payload['protocol_version']})", "",
             f"**Conclusion : {payload['conclusion']}**", ""]
    if payload["conclusion"] == NO_EDGE:
        lines += ["Aucun système testé ne satisfait la règle de stabilité (Sharpe > 0 dans au moins 70 % des "
                  "validations, au moins 200 trades) : **aucune stratégie testée ne démontre d'avantage "
                  "exploitable**. La période finale n'est pas consultée et reste vierge.", ""]
    else:
        lines += [f"Système retenu : `{payload['final_system']['key']}`. Prochaine étape obligatoire : "
                  "vérification en bougies 1 min, puis une seule consultation de la période finale.", ""]
    lines += [f"Essais de cette exécution : {payload['n_trials']} (déclarés : {payload['declared_trials']}) ; "
              f"programme sur DEVELOPMENT : {payload['program_trials']}.", "",
              f"Audit des fuites : {'réussi' if payload['leak_audit']['passed'] else 'ÉCHEC'} (mutation détectée : "
              f"{payload['leak_audit']['mutation_detected']}).", "",
              "## Meilleurs systèmes (validation)", "",
              "| Système | Validations > 0 | Trades | Sharpe médian | Admissible |", "|---|---|---|---|---|"]
    lines += [f"| `{r['key']}` | {r['positive_folds']}/{r['evaluated_folds']} | {r['trades']} | {r['median_sharpe']} | "
              f"{'oui' if r['admissible'] else 'non'} |" for r in payload["top_systems"][:10]]
    comparison = payload["target_comparison"]
    if comparison:
        lines += ["", "## Cibles : horizon fixe contre triple barrière", "",
                  f"Triple barrière meilleure dans la majorité des validations pour {comparison['tb_beats_fh_in_majority']}"
                  f" des {comparison['pairs']} couples (horizon, modèle, marge) ; systèmes admissibles : "
                  f"{comparison['admissible']} ; médiane des Sharpe médians : {comparison['median_of_median_sharpe']}."]
    lines += ["", f"## Variantes sur la référence `{payload['reference']['key']}` ({payload['reference']['status']})", "",
              "| Variante | Meilleure que la référence | Évaluables | Conservée |", "|---|---|---|---|"]
    for d in payload["variants"]:
        extra = f" (contre l'abstention de même sévérité : {d['wins_vs_matched_abstention']})" \
            if "wins_vs_matched_abstention" in d else ""
        lines.append(f"| {d['variant']} | {d['wins_vs_reference']}{extra} | {d['evaluated']} | "
                     f"{'oui' if d['kept'] else 'non'} |")
    totals = analysis["aggregate"]
    lines += ["", f"## Analyse du système `{system['key']}` — {payload['analysed_system_status']}", "",
              f"- Validations enchaînées ({totals['days']} jours) : rendement {_fmt(totals['total_return'], True)}, "
              f"perte maximale {_fmt(totals['max_drawdown'], True)}, Sharpe {_fmt(totals['sharpe'])} "
              f"(IC95 par blocs circulaires : {_fmt(totals.get('sharpe_ci95'))}), {totals['trades']} trades, "
              f"rotation {_fmt(totals.get('turnover_per_year'))} par an, taux de gain {_fmt(totals.get('win_rate'))}.",
              f"- Gain moyen par trade {_fmt(totals.get('avg_net_per_trade'))} (IC95 par blocs de jours : "
              f"{_fmt(totals.get('avg_net_ci95'))}) ; sans le 1 % des meilleurs trades : "
              f"{_fmt(analysis['exceptional_trades'].get('avg_net_without_top1pct'))}.",
              *([f"- Au bord de la grille : {'; '.join(payload['grid_edges'])}."] if payload["grid_edges"] else []),
              "", "| Validation | Sharpe | Trades | Abstention | Hasard p95 | BTC acheté-gardé | AUC | Brier skill |",
              "|---|---|---|---|---|---|---|---|"]
    for f in analysis["by_fold"]:
        btc = f["benchmarks"].get("btc_buy_and_hold", {}).get("sharpe")
        lines.append(f"| {f['fold']} | {_fmt(f['sharpe'])} | {f['trades']} | {_fmt(f['abstention'], True)} | "
                     f"{_fmt(f['random_entries']['sharpe_p95'])} | {_fmt(btc)} | {_fmt(f['calibration'].get('auc'))} | "
                     f"{_fmt(f['calibration'].get('brier_skill'))} |")
    lines += ["", "| Robustesse | Retard (bougies) | Validations > 0 | Gain moyen par trade |", "|---|---|---|---|"]
    lines += [f"| {r['scenario']} | {r['entry_delay_bars']} | {r['positive_folds']} | {_fmt(r['avg_net_per_trade'])} |"
              for r in analysis["robustness"]]
    lines += ["", "## Réserves", "", *(f"- {r}" for r in payload["reserves"]), "",
              "« p » est la probabilité ÉTALONNÉE (Platt, 2 mois postérieurs à l'ajustement) que le rendement net "
              "de l'entrée selon la règle de sortie de la cible soit positif ; sa fiabilité est mesurée en "
              "validation (calibration.csv : Brier, erreur d'étalonnage, AUC). Journal de chaque décision : "
              "decisions.parquet."]
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- Estimation unique (FINAL_TEST) ---------------------------------------------------------------

def final_criteria(metrics: dict, random: dict, benchmarks: dict, robustness: list[dict], exceptional: dict,
                   max_group_share: float = 0.6) -> tuple[str, list[dict]]:
    """Critères §7, déclarés avant la consultation."""
    sharpe = metrics.get("sharpe")
    ci = metrics.get("avg_net_ci95")
    btc = benchmarks.get("btc_buy_and_hold", {})
    adverse = [r for r in robustness if r["scenario"] in ("adverse", "central")]
    checks = [
        ("rendement > 0 et IC95 du gain moyen > 0", metrics["total_return"] > 0 and ci is not None and ci[0] > 0),
        ("Sharpe > 95e centile du hasard", sharpe is not None and random.get("sharpe_p95") is not None
         and sharpe > random["sharpe_p95"]),
        ("meilleur que BTC acheté-gardé (Sharpe, ou perte max deux fois plus faible avec gain)",
         sharpe is not None and (sharpe > (btc.get("sharpe") or 0.0) or (
             metrics["total_return"] > 0 and metrics["max_drawdown"] > btc.get("max_drawdown", 0.0) / 2))),
        ("au moins 100 trades, aucune paire ni trimestre > 60 % du gain",
         metrics["trades"] >= 100 and (metrics.get("pnl_share_max_symbol") or 1) <= max_group_share
         and (metrics.get("pnl_share_max_quarter") or 1) <= max_group_share),
        ("positif en coûts défavorables et avec une bougie de retard",
         bool(adverse) and all((r["avg_net_per_trade"] or -1) > 0 for r in adverse)),
        ("gain moyen positif sans le 1 % des meilleurs trades", (exceptional.get("avg_net_without_top1pct") or -1) > 0),
    ]
    criteria = [{"number": i + 1, "label": label, "passed": bool(ok)} for i, (label, ok) in enumerate(checks)]
    if metrics["trades"] < 100:
        return "INCONCLUSIVE", criteria
    return ("VALIDATED" if all(c["passed"] for c in criteria) else "REJECTED"), criteria


def final(settings: Settings, *, now: datetime, allow_final_test: bool, selection_run: str | None = None,
          progress: Callable[[str], None] | None = None) -> dict:
    """Une seule consultation de FINAL_TEST avec le système figé par la sélection."""
    say = progress or (lambda _text: None)
    period = resolve_period(settings, "final-test", now=now, allow_final_test=allow_final_test)
    experiments = ExperimentRegistry(settings.experiments_db)
    if experiments.final_test_consulted(STRATEGY_ID):
        raise FinalTestLocked("la période finale a déjà été consultée pour le ML intraday : une seule consultation")
    run = experiments.get(selection_run) if selection_run else next(
        (experiments.get(r["run_id"]) for r in experiments.recent(500)
         if r["kind"] == KIND_SELECT and r["status"] == "COMPLETED"), None)
    if not run or run["kind"] != KIND_SELECT or run["status"] != "COMPLETED":
        raise FinalTestLocked("aucune sélection ML intraday terminée : la période finale reste vierge")
    if run["metrics"].get("conclusion") != ADMISSIBLE:
        raise FinalTestLocked("la sélection n'a retenu aucun système (aucun avantage démontré) : la période finale "
                              "n'est pas consultée")
    passed, detail = finer.minute_check_status(Path(run["report_dir"]))
    if not passed:
        raise FinalTestLocked(f"vérification en bougies 1 min : {detail} (obligatoire avant la période finale, §5)")
    if run["metrics"].get("source_fingerprint") != source_fingerprint() or \
            run["metrics"].get("config_fingerprint") != config_fingerprint(settings):
        raise FinalTestLocked("code ou configuration qui décident différents de ceux de la sélection : relancer "
                              "la sélection (ses essais seront comptés) avant la période finale")
    require_clean_code(settings, allow_dirty=False)
    system = System.from_dict(run["metrics"]["final_system"])
    limits = RiskLimits.from_settings(settings.risk)
    run_id = new_run_id("MLIF")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    consultations = experiments.consult_final_test(run_id, STRATEGY_ID)
    prep = prepare(settings, end=now, progress=say)
    final_folds = make_folds(period.start, period.end)
    folds = (make_folds(pd.Timestamp(settings.data.history_start.isoformat(), tz="UTC")
                        + pd.DateOffset(months=TRAIN_MONTHS), pd.Timestamp(period.start) - pd.Timedelta(seconds=1))
             if system.filter != "none" else [])
    offset = len(folds)
    folds += [replace(f, index=f.index + offset) for f in final_folds]
    runs = [r for r in run_system(prep, system, folds, limits, seed=settings.protocol.seed, progress=say)
            if r.fold.index >= offset]
    trades = all_trades(runs)
    metrics = aggregate(runs, settings)
    pool_parts = []
    for r in runs:
        rows = r.prediction.rows
        pool_parts.append(pd.DataFrame({
            "symbol": np.asarray(prep.column("symbol"))[rows].astype(str),
            "decision_time": prep.column("decision_time")[rows],
            "bars": prep.column(f"bars_{system.kind}_{system.horizon}")[rows],
            "net": np.asarray(prep.column(f"net_{system.kind}_{system.horizon}"), dtype=float)[rows]}))
    pool = pd.concat(pool_parts, ignore_index=True)
    random = random_entries(pool, int(sum(r.submitted.sum() for r in runs)), limits, period.start, period.end,
                            draws=RANDOM_DRAWS, seed=settings.protocol.seed)
    benchmarks = buy_and_hold(prep.daily_closes, period.start, period.end)
    robustness = [replay(prep, system, runs, limits, "central", delay=1),
                  replay(prep, system, runs, limits, "adverse"), replay(prep, system, runs, limits, "stress")]
    exceptional = exceptional_dependence(trades)
    verdict, criteria = final_criteria(metrics, random, benchmarks, robustness, exceptional,
                                       settings.admission.max_group_pnl_share)
    payload = {"run_id": run_id, "selection_run": run["run_id"], "system": system.to_dict(), "verdict": verdict,
               "criteria": criteria, "metrics": metrics, "random_entries": random, "benchmarks": benchmarks,
               "robustness": robustness, "exceptional_trades": exceptional,
               "breakdown": breakdown(trades, ("tendance_1h", "volatilite_1h", "btc_24h")) if len(trades) else {},
               "final_test_consultations": consultations,
               "reserves": reserves(experiments.program_trials("DEVELOPMENT"))}
    payload["models"], fold_hashes = save_models(runs, report_dir / "models")
    journal(prep, system, runs, fold_hashes).to_parquet(report_dir / "decisions.parquet", compression="zstd",
                                                        index=False)
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind=KIND_FINAL, hypothesis="estimation unique du système retenu",
        strategy=STRATEGY_ID, strategy_version=PROTOCOL_VERSION, variant=system.key, params=system.to_dict(),
        period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
        cost_scenario="central (+ robustesse)", simulation_rules={"limits": limits.__dict__},
        metrics={"verdict": verdict, "n_trials": 1, "criteria": criteria, "final_test_consultations": consultations},
        status="COMPLETED", report_dir=str(report_dir), **prep.common)
    return payload
