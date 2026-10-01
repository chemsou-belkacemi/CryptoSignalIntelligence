"""Moteur commun des programmes ML de CSI (intraday, swing, expériences avancées).

Un `Program` décrit un protocole déclaré avant exécution (docs/ML_INTRADAY.md, docs/ML_SWING.md) : série
de base, horizons, cibles, familles de variables, modèles, marges, validations, règle d'admission,
construction des données, audit des fuites. Le moteur fait le reste, identiquement pour tous :
1. audit des fuites sur données réelles — échec → arrêt ;
2. validations purgées (ajustement, étalonnage de Platt, validation), espérance nette et abstention ;
3. portefeuille simulé avec les limites CENTRALISÉES (`risk/exposure.py`) ;
4. règle d'admission : stabilité par validation, puis (règle stricte) contrôles sur les trades du système
   (IC du gain moyen, coûts défavorables, dépendance aux meilleurs trades, concentration) ;
5. variantes une à la fois (familles, méta-filtre, abstention de même sévérité) ;
6. analyse, journal de chaque décision, modèles archivés, registre (échecs compris) ;
7. période finale : une seule consultation, verrouillée (empreintes du code et de la configuration,
   vérification sur bougies plus fines).
Aucun ordre, aucun signal publié.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import CostScenario, Settings
from ..research.experiments import ExperimentRegistry, code_state, new_run_id
from ..research.intervals import calendar_mean_ci
from ..research.protocol import FinalTestLocked, Period
from ..research.protocol import period as resolve_period
from ..risk.exposure import RiskLimits
from .intraday.models import ModelSpec, Payoff, calibration_report, fit, fit_platt
from .intraday.portfolio import (
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
from .logistic import fit_logistic

NO_EDGE = "AUCUN_AVANTAGE_DEMONTRE"
ADMISSIBLE = "SYSTEME_ADMISSIBLE"
ONE_DAY = pd.Timedelta(days=1)
META_MIN_SIGNALS = 300
META_MIN_MINORITY_PER_FEATURE = 10
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS: dict[str, Program] = {}


# --- Description d'un programme ---------------------------------------------------------------------

@dataclass(frozen=True)
class SelectionRule:
    """Règle d'admission déclarée. Non stricte (intraday v5) : stabilité seulement. Stricte (swing v6) :
    stabilité avec un minimum de trades ET de jours d'entrée distincts par validation, puis contrôles sur
    les trades du système (IC du gain moyen et de son excès sur la moyenne de toutes les décisions au même
    instant, coûts défavorables, sans le 1 % des meilleurs trades, concentration)."""
    stability_share: float = 0.70
    min_trades: int = 200
    min_trades_per_fold: int = 0
    min_entry_periods_per_fold: int = 0       # paris indépendants : périodes d'entrée distinctes (`entry_period`)
    min_evaluated_folds: int = 3
    strict: bool = False
    max_group_share: float = 0.6
    ci_method: str = "bootstrap"              # bootstrap (intraday v5) | student_calendar (swing v2) : `mean_ci`
    min_ci_blocks: int = 10
    excess_check: bool = False                # gain en excès de la moyenne de toutes les décisions au même instant

    def required_positive(self, evaluated: int) -> int:
        return math.ceil(self.stability_share * evaluated - 1e-9)

    def kept(self, wins: int, evaluated: int) -> bool:
        return evaluated >= self.min_evaluated_folds and wins >= self.required_positive(evaluated)

    def counts(self, sharpe, trades, entry_periods) -> bool:
        """Une validation compte comme « positive » : Sharpe > 0, assez de trades et assez de périodes d'entrée
        distinctes ; un nombre de périodes inconnu ne compte pas dès qu'un minimum est exigé."""
        enough_periods = self.min_entry_periods_per_fold == 0 or (
            entry_periods is not None and entry_periods >= self.min_entry_periods_per_fold)
        return (sharpe or 0.0) > 0 and (trades or 0) >= self.min_trades_per_fold and enough_periods


def variant_kept(wins: int, evaluated: int, admissible: bool, rule: SelectionRule) -> bool:
    """Une variante n'est conservée que si elle bat la référence assez souvent ET (règle stricte) si elle est
    elle-même admissible."""
    return rule.kept(wins, evaluated) and (admissible or not rule.strict)


def meta_filter_kept(wins_vs_reference: int, evaluated_vs_reference: int, admissible: bool,
                     wins_vs_matched: int, evaluated_vs_matched: int, rule: SelectionRule) -> bool:
    """Le méta-filtre est conservé s'il est conservable comme variante (`variant_kept`) ET s'il bat aussi
    l'abstention de même sévérité assez souvent (sinon il ne fait que trier par espérance)."""
    return (variant_kept(wins_vs_reference, evaluated_vs_reference, admissible, rule)
            and rule.kept(wins_vs_matched, evaluated_vs_matched))


@dataclass(frozen=True)
class Program:
    name: str                                   # nom lisible (rapports)
    strategy_id: str                            # ML_INTRADAY, ML_SWING…
    kind_select: str
    kind_final: str
    run_prefix: str
    protocol_version: int
    doc: str
    hypothesis: str
    step: pd.Timedelta                          # bougie de base (cibles, purge, sorties)
    decision_every: int                         # une décision toutes les N bougies de base
    horizons: tuple[int, ...]                   # en bougies de base
    targets: tuple[str, ...]
    families: dict[str, tuple[str, ...]]
    specs: tuple[ModelSpec, ...]
    margins: tuple[float, ...]
    family_variants: dict[str, tuple[str, ...]]
    meta_features: tuple[str, ...]
    context_required: tuple[str, ...]           # contexte inconnu → aucune décision
    train_months: int | None                    # None : entraînement ancré au début de l'historique
    calib_months: int
    valid_months: int
    first_valid_months: int                     # première validation = début de l'historique + N mois
    min_calib_rows: int
    selection: SelectionRule
    prepare: Callable[..., Prepared]            # (settings, *, end, progress) → Prepared
    scenario_targets: Callable[..., tuple[np.ndarray, np.ndarray]]   # (prep, kind, horizon, costs, delay)
    leak_audit: Callable[..., dict]             # (settings, prep, *, seed) → dict
    training_stride: Callable[[int], int]       # horizon → pas entre lignes d'entraînement (en décisions)
    labels: Callable[[pd.DataFrame], pd.DataFrame]   # contexte de marché des candidats (analyse)
    reserves: Callable[[int], list[str]]
    finer_check: Callable[[Path], tuple[bool, str]]
    finer_label: str
    decision_modules: tuple[str, ...]
    extra_params: dict = field(default_factory=dict)
    random_draws: int = 200
    # None (défaut) : début de l'historique des réglages (`data.history_start`). Un programme qui lit un autre
    # magasin (historique long) déclare ici le début de SON historique : ancrage, plis, `period_start` du registre.
    history_start: date | None = None
    # None (défaut) : première validation = début de l'historique + `first_valid_months`. Sinon cette date,
    # pour un historique qui ne commence pas un premier du mois.
    first_valid_start: date | None = None

    @property
    def all_families(self) -> tuple[str, ...]:
        return tuple(self.families)

    @property
    def features(self) -> tuple[str, ...]:
        return tuple(name for family in self.families.values() for name in family)

    @property
    def feature_index(self) -> dict[str, int]:
        return {name: i for i, name in enumerate(self.features)}

    @property
    def specs_by_name(self) -> dict[str, ModelSpec]:
        return {spec.name: spec for spec in self.specs}

    @property
    def declared_trials(self) -> int:
        return len(self.targets) * len(self.horizons) * len(self.specs) * len(self.margins) + len(
            self.family_variants) + 2

    def feature_set(self, families: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(name for family in families for name in self.families[family])

    def start(self, settings: Settings) -> pd.Timestamp:
        """Début de l'historique : celui que le programme déclare, sinon celui des réglages."""
        return pd.Timestamp((self.history_start or settings.data.history_start).isoformat(), tz="UTC")

    def folds(self, settings: Settings, first_valid, end) -> list[Fold]:
        return make_folds(first_valid, end, train_months=self.train_months, calib_months=self.calib_months,
                          valid_months=self.valid_months, history_start=self.start(settings))

    def first_valid(self, settings: Settings) -> pd.Timestamp:
        if self.first_valid_start is not None:
            return pd.Timestamp(self.first_valid_start.isoformat(), tz="UTC")
        return self.start(settings) + pd.DateOffset(months=self.first_valid_months)


def register(program: Program) -> Program:
    PROGRAMS[program.strategy_id] = program
    return program


# --- Systèmes et validations -------------------------------------------------------------------------

@dataclass(frozen=True)
class System:
    """Un système = cible + horizon + modèle + marge d'abstention + variables + filtre éventuel."""
    kind: str
    horizon: int
    model: str
    margin: float
    families: tuple[str, ...] = ()              # () : toutes les familles du programme
    filter: str = "none"                        # none | meta | matched (abstention de même sévérité)
    program: str = "ML_INTRADAY"

    @property
    def spec(self) -> Program:
        return PROGRAMS[self.program]

    @property
    def resolved_families(self) -> tuple[str, ...]:
        return tuple(self.families) or self.spec.all_families

    @property
    def variant(self) -> str:
        families = self.resolved_families
        names = [n for n, f in self.spec.family_variants.items() if f == families]
        label = names[0] if names else ("tout" if families == self.spec.all_families else "+".join(families))
        return label if self.filter == "none" else f"{label}|{self.filter}"

    @property
    def key(self) -> str:
        return f"{self.kind}_H{self.horizon}_{self.model}_m{round(self.margin * 1e4)}bp_{self.variant}"

    @property
    def features(self) -> tuple[str, ...]:
        return self.spec.feature_set(self.resolved_families)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "horizon": self.horizon, "model": self.model, "margin": self.margin,
                "families": list(self.resolved_families), "filter": self.filter, "key": self.key,
                "program": self.program}

    @classmethod
    def from_dict(cls, data: dict) -> System:
        return cls(data["kind"], int(data["horizon"]), data["model"], float(data["margin"]),
                   tuple(data["families"]), data.get("filter", "none"), data.get("program", "ML_INTRADAY"))


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


def make_folds(first_valid, end, *, train_months: int | None, calib_months: int, valid_months: int,
               history_start=None) -> list[Fold]:
    """Validations de `valid_months` mois à partir de `first_valid` ; entraînement de `train_months` mois
    avant chacune (None : ancré à `history_start`), dont les `calib_months` derniers pour l'étalonnage."""
    folds: list[Fold] = []
    start, last = pd.Timestamp(first_valid), pd.Timestamp(end)
    while start < last:
        following = start + pd.DateOffset(months=valid_months)
        train_start = (pd.Timestamp(history_start) if train_months is None
                       else start - pd.DateOffset(months=train_months))
        folds.append(Fold(len(folds), train_start, start - pd.DateOffset(months=calib_months), start,
                          min(following - pd.Timedelta(seconds=1), last)))
        start = following
    return folds


# --- Versions reproductibles ---------------------------------------------------------------------------

def source_fingerprint(program: Program, package_root: Path | None = None) -> str:
    """Empreinte du code qui décide (fins de ligne normalisées)."""
    root = package_root or PACKAGE_ROOT
    digest = hashlib.sha256()
    for relative in program.decision_modules:
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def config_fingerprint(settings: Settings) -> str:
    """Empreinte des réglages qui décident : coûts, limites, régimes, univers, protocole."""
    payload = {"costs": {k: v.model_dump() for k, v in settings.costs.items()}, "risk": settings.risk.model_dump(),
               "regimes": settings.regimes.model_dump(), "symbols": list(settings.data.symbols),
               "history_start": str(settings.data.history_start), "seed": settings.protocol.seed,
               "bootstrap_block_days": settings.protocol.bootstrap_block_days,
               "bootstrap_samples": settings.protocol.bootstrap_samples,
               "max_group_pnl_share": settings.admission.max_group_pnl_share}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def require_clean_code(settings: Settings, *, allow_dirty: bool) -> str:
    """Versions reproductibles : code commité (et dépôt git présent) avant tout chargement de données."""
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise RuntimeError(f"code non commité ou sans dépôt git ({state}) : exécution refusée (versions "
                           "reproductibles ; --allow-dirty pour un essai local, enregistré comme tel)")
    return state


# --- Données préparées ---------------------------------------------------------------------------------

@dataclass
class Prepared:
    meta: pd.DataFrame                 # symbol, open_time, decision_time, gap_recent, cibles (une ligne par décision)
    X: np.ndarray                      # variables (float32), colonnes dans l'ordre de program.features
    inputs: dict[str, dict[str, pd.DataFrame]]
    common: dict
    daily_closes: pd.DataFrame
    costs: dict[str, CostScenario]
    end: pd.Timestamp
    symbols: list[str]                 # ordre des paires dans `meta` (alignement des cibles recalculées)
    program: Program
    _columns: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    _series: dict[str, pd.Series] = field(default_factory=dict, repr=False)

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
            index = self.program.feature_index
            columns = [index[name] for name in self.program.context_required]
            self._columns["_context"] = np.isfinite(self.X[:, columns]).all(axis=1)
        return self._columns["_context"]


# --- Ajustement, étalonnage, prédiction -----------------------------------------------------------------

@dataclass
class Split:
    fit: np.ndarray
    calib: np.ndarray
    valid: np.ndarray


def training_mask(prep: Prepared, kind: str, horizon: int) -> pd.Series:
    """Lignes d'entraînement : cible valide, pas de trou récent, une ligne toutes les `training_stride(H)`
    décisions par paire (repère absolu : mêmes lignes quel que soit le découpage)."""
    program = prep.program
    meta = prep.meta
    position = (meta["open_time"] - pd.Timestamp("2000-01-01", tz="UTC")) // (program.step * program.decision_every)
    stride = max(1, program.training_stride(horizon))
    return meta[f"net_{kind}_{horizon}"].notna() & ~meta["gap_recent"] & ((position % stride) == 0)


def split_rows(prep: Prepared, fold: Fold, kind: str, horizon: int) -> Split:
    """Purge : ajustement et étalonnage ne contiennent que des lignes dont la barrière verticale (t+H)
    précède le bloc suivant ; la validation, que des décisions dont t+H reste dans la validation et dont
    le contexte est connu (sinon aucune décision, comme en service)."""
    meta = prep.meta
    decision = meta["decision_time"]
    barrier = decision + horizon * prep.program.step
    usable = meta[f"net_{kind}_{horizon}"].notna() & ~meta["gap_recent"]
    train = training_mask(prep, kind, horizon)
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
    program = prep.program
    index = program.feature_index
    columns = [index[f] for f in system.features]
    net = prep.column(f"net_{system.kind}_{system.horizon}").astype(float)
    if len(rows.calib) < program.min_calib_rows:
        raise ValueError(f"étalonnage trop court ({len(rows.calib)} lignes)")
    y_fit, y_calib = net[rows.fit] > 0, net[rows.calib] > 0
    if y_calib.all() or not y_calib.any():
        raise ValueError("une seule classe dans l'étalonnage")
    spec = program.specs_by_name[system.model]
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
    index = prep.program.feature_index
    for name in prep.program.meta_features:
        if name in index:
            frame[name] = prep.X[rows, index[name]]
    return frame


def step_ns(program: Program) -> int:
    return int(program.step.total_seconds() * 10**9)


def entry_period(program: Program, horizon: int) -> pd.Timedelta:
    """Durée d'une période d'entrée : max(1 jour, H). Plusieurs entrées dans la même période (consécutives
    depuis le début de la validation) ne font qu'un pari face au marché."""
    return max(ONE_DAY, horizon * program.step)


def fold_metrics(trades: pd.DataFrame, fold: Fold, *, valid_rows: int, submitted: int,
                 period: pd.Timedelta = ONE_DAY) -> dict:
    stats = series_metrics(daily_returns(trades, fold.valid_start, fold.valid_end))
    entry = pd.to_datetime(trades["entry_time"], utc=True)
    entry_periods = int(((entry - fold.valid_start) // period).nunique()) if len(trades) else 0
    return {"fold": fold.index, "sharpe": stats["sharpe"], "total_return": stats["total_return"],
            "max_drawdown": stats["max_drawdown"], "trades": int(len(trades)), "entry_periods": entry_periods,
            "avg_net": round(float(trades["net"].mean()), 6) if len(trades) else None,
            "submitted": int(submitted), "valid_rows": int(valid_rows),
            "abstention": round(1 - submitted / valid_rows, 4) if valid_rows else None}


# --- Grille principale ----------------------------------------------------------------------------------

def run_grid(prep: Prepared, folds: list[Fold], limits: RiskLimits, *, seed: int, progress: Callable[[str], None],
             on_rows: Callable[[list[dict]], None] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Les systèmes de base sur chaque validation : (résultats par pli, qualité des probabilités)."""
    program = prep.program
    results, quality = [], []
    for fold in folds:
        for kind in program.targets:
            for horizon in program.horizons:
                rows = split_rows(prep, fold, kind, horizon)
                batch: list[dict] = []
                for spec in program.specs:
                    progress(f"pli {fold.index + 1}/{len(folds)} {kind} H{horizon} {spec.name}")
                    base = System(kind, horizon, spec.name, program.margins[0], program=program.strategy_id)
                    try:
                        prediction = predict_fold(prep, rows, base, seed=seed)
                    except Exception as exc:  # noqa: BLE001 - un essai en échec est enregistré, pas masqué
                        for margin in program.margins:
                            batch.append(replace(base, margin=margin).to_dict() | {
                                "fold": fold.index, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    quality.append({"kind": kind, "horizon": horizon, "model": spec.name, "fold": fold.index,
                                    "n_fit": prediction.n_fit, "n_calib": prediction.n_calib,
                                    "base_rate": round(prediction.base_rate, 4),
                                    **{k: v for k, v in prediction.quality.items() if k != "reliability"}})
                    for margin in program.margins:
                        system = replace(base, margin=margin)
                        candidates = signals(prep, system, prediction)
                        trades, _ = run_book(candidates, limits, step_ns=step_ns(program),
                                             strategy=program.strategy_id)
                        batch.append(system.to_dict() | {"status": "OK", "error": ""} | fold_metrics(
                            trades, fold, valid_rows=len(prediction.rows), submitted=len(candidates),
                            period=entry_period(program, horizon)))
                results += batch
                if on_rows:
                    on_rows(batch)
    return pd.DataFrame(results), pd.DataFrame(quality)


def summarize(results: pd.DataFrame, rule: SelectionRule, evaluated_folds: dict[str, int] | None = None) -> pd.DataFrame:
    """Une ligne par système : validations positives, trades, Sharpe médian (pli sans trade = 0), stabilité.

    Une validation n'est « positive » qu'avec un Sharpe > 0, au moins `rule.min_trades_per_fold` trades et
    `rule.min_entry_periods_per_fold` périodes d'entrée distinctes (`entry_period`).
    `admissible` = stabilité ; la règle stricte complète cette colonne dans `select` (contrôles sur trades)."""
    if results.empty:
        return pd.DataFrame()
    rows = []
    n_folds = int(results["fold"].nunique())
    for key, group in results.groupby("key", sort=False):
        ok = group[group["status"] == "OK"]
        evaluated = (evaluated_folds or {}).get(str(key), n_folds)
        sharpes = ok["sharpe"].astype(float).fillna(0.0).tolist() + [0.0] * max(evaluated - len(ok), 0)
        periods = ok["entry_periods"] if "entry_periods" in ok else pd.Series(np.nan, index=ok.index)
        counted = [rule.counts(sh, tr, None if pd.isna(ep) else ep) for sh, tr, ep in
                   zip(ok["sharpe"].astype(float).fillna(0.0), ok["trades"].astype(float), periods, strict=True)]
        positive = int(sum(counted))
        trades = int(ok["trades"].sum())
        first = group.iloc[0]
        stable = bool(evaluated >= rule.min_evaluated_folds and positive >= rule.required_positive(evaluated)
                      and trades >= rule.min_trades)
        rows.append({"key": key, "kind": first["kind"], "horizon": int(first["horizon"]), "model": first["model"],
                     "margin": float(first["margin"]), "families": first["families"], "filter": first["filter"],
                     "program": first.get("program", "ML_INTRADAY"),
                     "evaluated_folds": evaluated, "positive_folds": positive, "trades": trades,
                     "median_sharpe": round(float(np.median(sharpes)), 4) if sharpes else 0.0,
                     "sharpe_by_fold": [None if pd.isna(v) else round(float(v), 3) for v in ok["sharpe"]],
                     "failed_folds": int((group["status"] != "OK").sum()),
                     "stable": stable, "admissible": stable})
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


# --- Système complet sur des validations -----------------------------------------------------------------

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


def _meta_mask(history: pd.DataFrame, current: pd.DataFrame, meta_features: tuple[str, ...]) -> np.ndarray | None:
    """Méta-filtre : logistique L2 sur les signaux HORS ENTRAÎNEMENT des validations précédentes.
    None si l'historique est insuffisant (pli non évaluable)."""
    minority_needed = META_MIN_MINORITY_PER_FEATURE * len(meta_features)
    if len(history) < META_MIN_SIGNALS or current.empty:
        return None
    y = (history["net"].to_numpy(float) > 0).astype(float)
    if min(y.sum(), len(y) - y.sum()) < minority_needed:
        return None
    X = history[list(meta_features)].to_numpy(float)
    medians = np.nanmedian(X, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    model = fit_logistic(np.where(np.isnan(X), medians, X), y, meta_features, l2=1.0)
    Z = current[list(meta_features)].to_numpy(float)
    return model.predict_proba(np.where(np.isnan(Z), medians, Z)) > float(y.mean())


def run_system(prep: Prepared, system: System, folds: list[Fold], limits: RiskLimits, *, seed: int,
               progress: Callable[[str], None] = lambda _t: None, cache: dict | None = None) -> list[FoldRun]:
    """Le système sur chaque validation. `cache` : prédictions déjà calculées pour le même modèle, la même
    cible et les mêmes variables (marges et filtres n'y changent rien ; résultats identiques, déterministes)."""
    program = prep.program
    runs: list[FoldRun] = []
    history: list[pd.DataFrame] = []
    for fold in folds:
        progress(f"{system.key} : pli {fold.index + 1}/{len(folds)}")
        state = "OK"
        key = (fold.index, fold.valid_start, system.kind, system.horizon, system.model, system.resolved_families)
        try:
            if cache is not None and key in cache:
                prediction = cache[key]
            else:
                prediction = predict_fold(prep, split_rows(prep, fold, system.kind, system.horizon), system, seed=seed)
                if cache is not None:
                    cache[key] = prediction
        except Exception as exc:  # noqa: BLE001 - pli en échec : enregistré, compté comme sans gain
            prediction, state = _failed_prediction(exc), "FAILED"
        candidates = signals(prep, system, prediction)
        submitted = np.ones(len(candidates), dtype=bool)
        if system.filter != "none" and state == "OK":
            past = pd.concat(history, ignore_index=True) if history else candidates.iloc[0:0]
            mask = _meta_mask(past, candidates, tuple(f for f in program.meta_features if f in candidates))
            if mask is None:
                state = "NOT_EVALUABLE"
            elif system.filter == "meta":
                submitted = mask
            else:                                        # même nombre de signaux, les plus forts en espérance
                order = np.argsort(-candidates["score"].to_numpy(), kind="stable")
                submitted = np.zeros(len(candidates), dtype=bool)
                submitted[order[:int(mask.sum())]] = True
        history.append(candidates)
        trades, status = run_book(candidates[submitted].reset_index(drop=True), limits, step_ns=step_ns(program),
                                  strategy=program.strategy_id)
        full_status = np.full(len(candidates), "FILTERED", dtype=object)
        full_status[submitted] = status
        if not trades.empty:                             # rang dans les candidats complets
            trades["candidate"] = np.flatnonzero(submitted)[trades["candidate"].to_numpy(int)]
        runs.append(FoldRun(fold, prediction, candidates, submitted, trades, full_status,
                            fold_metrics(trades, fold, valid_rows=len(prediction.rows),
                                         submitted=int(submitted.sum()),
                                         period=entry_period(program, system.horizon)), state))
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


# --- Analyse d'un système -----------------------------------------------------------------------------------

def all_trades(runs: list[FoldRun], labels: Callable[[pd.DataFrame], pd.DataFrame] | None = None) -> pd.DataFrame:
    frames = []
    for run in runs:
        if run.trades.empty:
            continue
        trades = run.trades.reset_index(drop=True)
        if labels is not None:
            context = labels(run.candidates).iloc[trades["candidate"].to_numpy(int)].reset_index(drop=True)
            trades = pd.concat([trades, context], axis=1)
        frames.append(trades.assign(fold=run.fold.index))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["symbol", "entry_time", "exit_time", "notional", "net", "pnl", "candidate", "fold"])


def mean_ci(values, times, *, block_days: int, samples: int, seed: int, method: str = "bootstrap",
            min_blocks: int = 10) -> list[float] | None:
    """IC95 d'une moyenne par trade selon la méthode déclarée par la règle d'admission : `bootstrap`
    (intraday v5 : blocs de jours AYANT des trades, `trade_mean_ci`) ou `student_calendar` (swing v2 :
    `calendar_mean_ci`)."""
    if method == "student_calendar":
        return calendar_mean_ci(values, times, block_days=block_days, min_blocks=min_blocks)
    if method != "bootstrap":
        raise ValueError(f"méthode d'IC inconnue : {method}")
    return trade_mean_ci(np.asarray(values, dtype=float), times, block_days=block_days, samples=samples, seed=seed,
                         min_blocks=min_blocks)


def block_days_for(program: Program, horizon: int, settings: Settings) -> int:
    """Blocs du bootstrap : au moins le réglage du protocole, et au moins deux fois l'horizon."""
    days = math.ceil(horizon * program.step / pd.Timedelta(days=1))
    return max(settings.protocol.bootstrap_block_days, 2 * days)


def replay(prep: Prepared, system: System, runs: list[FoldRun], limits: RiskLimits, scenario: str,
           delay: int | None = None) -> dict:
    """Mêmes décisions, rendements recalculés sous un scénario de coûts et de retard d'entrée."""
    program = prep.program
    costs = prep.costs[scenario]
    delay = costs.extra_entry_delay_bars if delay is None else delay
    net, bars = program.scenario_targets(prep, system.kind, system.horizon, costs, delay)
    folds, frames = [], []
    for run in runs:
        rows = run.candidates["row"].to_numpy(int)[run.submitted]
        candidates = run.candidates[run.submitted].assign(net=net[rows], bars=bars[rows])
        exit_time = (pd.to_datetime(candidates["decision_time"], utc=True)
                     + candidates["bars"].clip(lower=0) * program.step)
        inside = exit_time <= run.fold.valid_end                     # même purge qu'en central
        candidates = candidates[np.isfinite(candidates["net"]) & (candidates["bars"] >= 0)
                                & inside.to_numpy()].reset_index(drop=True)
        trades, _ = run_book(candidates, limits, step_ns=step_ns(program), strategy=program.strategy_id)
        folds.append(fold_metrics(trades, run.fold, valid_rows=len(run.prediction.rows), submitted=len(candidates),
                                  period=entry_period(program, system.horizon)))
        frames.append(trades)
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame(columns=["net", "pnl"])
    return {"scenario": scenario, "entry_delay_bars": delay,
            "total_return_by_fold": [f["total_return"] for f in folds],
            "sharpe_by_fold": [f["sharpe"] for f in folds],
            "avg_net_per_trade": round(float(trades["net"].mean()), 6) if len(trades) else None,
            "trades": int(len(trades)), "positive_folds": sum((f["sharpe"] or 0) > 0 for f in folds)}


def aggregate(runs: list[FoldRun], settings: Settings, block_days: int | None = None, *,
              ci_method: str = "bootstrap", min_ci_blocks: int = 10) -> dict:
    """Plis enchaînés : rendements journaliers de chaque pli (capital du pli) mis bout à bout — équivalent
    à un compte unique, la taille des positions étant proportionnelle au capital — puis mesures du
    portefeuille, IC par blocs circulaires et mesures par trade."""
    protocol = settings.protocol
    days = block_days or protocol.bootstrap_block_days
    trades = all_trades(runs)
    returns = (pd.concat([daily_returns(run.trades, run.fold.valid_start, run.fold.valid_end) for run in runs])
               if runs else pd.Series(dtype=float))
    out = series_metrics(returns) | {"trades": int(len(trades)), "days": int(len(returns))}
    out |= block_bootstrap(returns.to_numpy(), block_days=days, samples=protocol.bootstrap_samples, seed=protocol.seed)
    if trades.empty:
        return out | {"turnover_per_year": 0.0, "avg_net_per_trade": None, "avg_net_ci95": None, "win_rate": None,
                      "pnl_share_max_symbol": None, "pnl_share_max_quarter": None, "pnl_share_max_fold": None}
    entry = pd.to_datetime(trades["entry_time"], utc=True)
    gains = float(trades["pnl"].sum())
    quarters = entry.dt.tz_localize(None).dt.to_period("Q").astype(str)
    return out | {
        "turnover_per_year": round(float(2 * trades["notional"].sum() / max(len(returns) / 365, 1e-9)), 2),
        "avg_net_per_trade": round(float(trades["net"].mean()), 6),
        "avg_net_ci95": mean_ci(trades["net"].to_numpy(float), entry, block_days=days,
                                samples=protocol.bootstrap_samples, seed=protocol.seed, method=ci_method,
                                min_blocks=min_ci_blocks),
        "win_rate": round(float((trades["net"] > 0).mean()), 4),
        "pnl_share_max_symbol": round(float(trades.groupby("symbol")["pnl"].sum().max() / gains), 4) if gains > 0 else None,
        "pnl_share_max_quarter": round(float(trades.groupby(quarters)["pnl"].sum().max() / gains), 4) if gains > 0 else None,
        "pnl_share_max_fold": round(float(trades.groupby("fold")["pnl"].sum().max() / gains), 4) if gains > 0 else None,
    }


def same_time_mean_net(prep: Prepared, kind: str, horizon: int) -> pd.Series:
    """Moyenne du rendement net de TOUTES les décisions valides (contexte connu, sans trou) à chaque instant de
    décision, toutes paires confondues : ce que le marché aurait donné sans aucune sélection."""
    key = f"same_time_{kind}_{horizon}"
    if key not in prep._series:
        net = prep.column(f"net_{kind}_{horizon}").astype(float)
        valid = np.isfinite(net) & ~prep.column("gap_recent").astype(bool) & prep.context_known()
        times = pd.DatetimeIndex(prep.column("decision_time")[valid]).as_unit("ns")
        prep._series[key] = pd.Series(net[valid], index=times).groupby(level=0).mean()
    return prep._series[key]


def excess_over_market(trades: pd.DataFrame, reference: pd.Series) -> tuple[np.ndarray, pd.Series]:
    """(gain net de chaque trade moins la moyenne de toutes les décisions valides au même instant, instants
    d'entrée). `reference` : indexée par instant de décision (UTC sans fuseau). Trades sans référence exclus."""
    entry = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_convert(None).reset_index(drop=True)
    baseline = reference.reindex(pd.DatetimeIndex(entry).as_unit("ns")).to_numpy(float)
    excess = trades["net"].to_numpy(float) - baseline
    known = np.isfinite(excess)
    return excess[known], entry[known].reset_index(drop=True)


def judge_strict(metrics: dict, rule: SelectionRule) -> dict:
    """Jugement de la règle stricte à partir des mesures (fonction pure, testée)."""
    ci, excess_ci = metrics.get("avg_net_ci95"), metrics.get("excess_ci95")
    symbol_share, fold_share = metrics.get("pnl_share_max_symbol"), metrics.get("pnl_share_max_fold")
    checks = {
        "ic_gain_moyen_positif": bool(ci is not None and ci[0] > 0),
        "positif_couts_defavorables": bool((metrics.get("adverse_avg_net") or -1) > 0),
        "positif_sans_meilleurs_trades": bool((metrics.get("avg_net_without_top1pct") or -1) > 0),
        "concentration_limitee": bool(symbol_share is not None and fold_share is not None
                                      and symbol_share <= rule.max_group_share and fold_share <= rule.max_group_share),
    }
    if rule.excess_check:
        checks["ic_exces_sur_le_marche_positif"] = bool(excess_ci is not None and excess_ci[0] > 0)
    return {"passed": all(checks.values()), "checks": checks}


def strict_checks(prep: Prepared, system: System, runs: list[FoldRun], limits: RiskLimits, *,
                  settings: Settings, rule: SelectionRule) -> dict:
    """Règle stricte (v6) sur les validations : IC du gain moyen > 0 ; (si déclaré) IC de son excès sur la
    moyenne de toutes les décisions valides au même instant > 0 (sépare la sélection de la dérive commune du
    marché) ; positif en coûts défavorables et sans le 1 % des meilleurs trades ; aucune paire ni validation
    au-delà de `max_group_share` du gain."""
    protocol = settings.protocol
    days = block_days_for(prep.program, system.horizon, settings)
    totals = aggregate(runs, settings, days, ci_method=rule.ci_method, min_ci_blocks=rule.min_ci_blocks)
    adverse = replay(prep, system, runs, limits, "adverse") if "adverse" in prep.costs else {}
    trades = all_trades(runs)
    exceptional = exceptional_dependence(trades)
    metrics = {"avg_net_ci95": totals.get("avg_net_ci95"), "adverse_avg_net": adverse.get("avg_net_per_trade"),
               "avg_net_without_top1pct": exceptional.get("avg_net_without_top1pct"),
               "pnl_share_max_symbol": totals.get("pnl_share_max_symbol"),
               "pnl_share_max_fold": totals.get("pnl_share_max_fold"), "excess_ci95": None, "avg_excess": None}
    if rule.excess_check and len(trades):
        excess, entry = excess_over_market(trades, same_time_mean_net(prep, system.kind, system.horizon))
        metrics["avg_excess"] = round(float(excess.mean()), 6) if len(excess) else None
        metrics["excess_ci95"] = mean_ci(excess, entry, block_days=days, samples=protocol.bootstrap_samples,
                                         seed=protocol.seed, method=rule.ci_method, min_blocks=rule.min_ci_blocks)
    return judge_strict(metrics, rule) | metrics


def runs_stable(runs: list[FoldRun], rule: SelectionRule) -> bool:
    """Stabilité d'un système à partir de ses plis (mêmes règles que `summarize`)."""
    evaluated = len(runs)
    positive = sum(rule.counts(r.metrics["sharpe"], r.metrics["trades"], r.metrics.get("entry_periods")) for r in runs)
    trades = sum(r.metrics["trades"] for r in runs)
    return evaluated >= rule.min_evaluated_folds and positive >= rule.required_positive(evaluated) and \
        trades >= rule.min_trades


def analyse(prep: Prepared, system: System, runs: list[FoldRun], limits: RiskLimits, *, settings: Settings,
            progress: Callable[[str], None]) -> dict:
    program = prep.program
    protocol = settings.protocol
    trades = all_trades(runs, program.labels)
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
                                             run.fold.valid_end, draws=program.random_draws,
                                             seed=protocol.seed + run.fold.index, step_ns=step_ns(program),
                                             strategy=program.strategy_id),
            "calibration": run.prediction.quality, "state": run.state})
    progress("analyse : robustesse (coûts, retard)")
    robustness = [replay(prep, system, runs, limits, "central", delay=1)]
    robustness += [replay(prep, system, runs, limits, name) for name in ("adverse", "stress") if name in prep.costs]
    label_columns = [c for c in trades.columns if c not in
                     ("symbol", "entry_time", "exit_time", "notional", "net", "pnl", "candidate", "fold")]
    return {
        "system": system.to_dict(),
        "by_fold": by_fold,
        "aggregate": aggregate(runs, settings, block_days_for(program, system.horizon, settings),
                               ci_method=program.selection.ci_method,
                               min_ci_blocks=program.selection.min_ci_blocks),
        "exceptional_trades": exceptional_dependence(trades),
        "breakdown": breakdown(trades, tuple(label_columns)) if len(trades) else {},
        "robustness": robustness,
    }


def journal(prep: Prepared, system: System, runs: list[FoldRun], model_hashes: dict[int, str]) -> pd.DataFrame:
    """Journal de CHAQUE décision de validation, abstentions comprises : données, modèle, prédiction,
    coûts, décision et résultat observé (le rendement qu'aurait eu l'entrée, même refusée)."""
    costs = prep.costs["central"]
    round_trip_bps = 2 * (costs.fee_bps + costs.slippage_bps + costs.half_spread_bps)
    data_hash = {s: "/".join(f"{k}:{v}" for k, v in sorted(h.items())) for s, h in prep.common["data_hashes"].items()}
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
            "data_hash": [data_hash.get(s, "") for s in symbols], "model_spec": run.prediction.fingerprint,
            "model_sha256": model_hashes.get(run.fold.index, ""),
            "system": system.key, "p": run.prediction.p.astype("float32"),
            "expected_net": run.prediction.expected.astype("float32"), "margin": system.margin,
            "decision": decision, "cost_round_trip_bps": round_trip_bps,
            "observed_net": np.asarray(prep.column(f"net_{system.kind}_{system.horizon}"))[rows],
            "exit_bars": np.asarray(prep.column(f"bars_{system.kind}_{system.horizon}"))[rows],
            "fold_state": run.state}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


MODEL_EXTENSIONS = {"xgboost": "json", "lightgbm": "txt", "catboost": "cbm"}


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
            path = directory / f"{stem}.{model.family}.{MODEL_EXTENSIONS.get(model.family, 'bin')}"
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


# --- Sélection (DEVELOPMENT) -----------------------------------------------------------------------------

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


def development_period(program: Program, settings: Settings, *, now: datetime) -> Period:
    """DEVELOPMENT vu par le programme : même fin (bornée par le protocole), début de SON historique."""
    period = resolve_period(settings, "development", now=now)
    return replace(period, start=program.start(settings).to_pydatetime())


def _system_from_row(row, program: Program) -> System:
    return System(row["kind"], int(row["horizon"]), row["model"], float(row["margin"]), tuple(row["families"]),
                  row["filter"], program.strategy_id)


def select(program: Program, settings: Settings, *, now: datetime, allow_dirty: bool = False,
           progress: Callable[[str], None] | None = None) -> SelectResult:
    say = progress or (lambda _text: None)
    rule = program.selection
    period = development_period(program, settings, now=now)
    run_id = new_run_id(program.run_prefix)
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
        prep = program.prepare(settings, end=period.end, progress=say)
        state["common"] = prep.common
        say("audit des fuites")
        audit = program.leak_audit(settings, prep, seed=seed)
        (report_dir / "leak_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str),
                                                    encoding="utf-8")
        if not audit["passed"]:
            raise RuntimeError(f"audit des fuites en échec : {len(audit['violations'])} écart(s), mutation détectée : "
                               f"{audit['mutation_detected']}")
        folds = program.folds(settings, program.first_valid(settings), period.end)
        grid_path = report_dir / "grid.csv"
        seen: set[str] = set()

        def checkpoint(batch: list[dict]) -> None:
            pd.DataFrame(batch).to_csv(grid_path, mode="a", header=not grid_path.exists(), index=False)
            seen.update(row["key"] for row in batch)
            state["evaluated"] = len(seen)

        results, quality = run_grid(prep, folds, limits, seed=seed, progress=say, on_rows=checkpoint)
        state["evaluated"] = int(results["key"].nunique()) if not results.empty else 0
        quality.to_csv(report_dir / "calibration.csv", index=False)
        summary = summarize(results, rule)
        strict_details: dict[str, dict] = {}
        cached_runs: dict[str, list[FoldRun]] = {}
        predictions: dict = {}
        if rule.strict:
            # Deuxième temps de la règle v6 : contrôles sur les trades des systèmes stables (aucun nouvel essai).
            for _, row in summary[summary["stable"]].iterrows():
                system = _system_from_row(row, program)
                say(f"règle stricte : {system.key}")
                runs = run_system(prep, system, folds, limits, seed=seed, cache=predictions)
                cached_runs[system.key] = runs
                strict_details[system.key] = strict_checks(prep, system, runs, limits, settings=settings, rule=rule)
            summary["admissible"] = [bool(strict_details.get(k, {}).get("passed", False)) for k in summary["key"]]
            summary = summary.sort_values(["admissible", "median_sharpe"], ascending=[False, False]).reset_index(drop=True)
        admissible = summary[summary["admissible"]]
        ref_row = (admissible if not admissible.empty else summary).iloc[0]
        reference = _system_from_row(ref_row, program)
        reference_status = "ADMISSIBLE" if not admissible.empty else "DIAGNOSTIC"

        def evaluate_variant(system: System, runs: list[FoldRun]) -> tuple[bool, dict | None]:
            if not rule.strict:
                return runs_stable(runs, rule), None
            details = strict_checks(prep, system, runs, limits, settings=settings, rule=rule)
            return runs_stable(runs, rule) and details["passed"], details

        say("variantes : familles de variables")
        ref_runs = cached_runs.get(reference.key) or run_system(prep, reference, folds, limits, seed=seed, progress=say,
                                                                cache=predictions)
        variant_rows, decisions = [], []
        family_systems: dict[str, list[FoldRun]] = {}
        for name, families in program.family_variants.items():
            system = replace(reference, families=families)
            runs = run_system(prep, system, folds, limits, seed=seed, progress=say, cache=predictions)
            family_systems[system.key] = runs
            variant_rows += runs_to_rows(system, runs)
            wins, evaluated = beats(runs, ref_runs)
            admissible_variant, details = evaluate_variant(system, runs)
            keep = variant_kept(wins, evaluated, admissible_variant, rule)
            decisions.append({"variant": name, "key": system.key, "wins_vs_reference": wins, "evaluated": evaluated,
                              "admissible": admissible_variant, "kept": keep, **({"strict": details} if details else {})})
        state["evaluated"] += len(program.family_variants)
        kept_families = [d for d in decisions if d["kept"]]
        base_system, base_runs = reference, ref_runs
        if kept_families:
            table = summarize(pd.DataFrame([r for r in variant_rows if r["key"] in {d["key"] for d in kept_families}]),
                              rule)
            best = table.iloc[0]
            base_system = replace(reference, families=tuple(best["families"]))
            base_runs = family_systems[base_system.key]

        say("variantes : méta-filtre et abstention de même sévérité")
        meta_system, matched_system = replace(base_system, filter="meta"), replace(base_system, filter="matched")
        meta_runs = run_system(prep, meta_system, folds, limits, seed=seed, progress=say, cache=predictions)
        matched_runs = run_system(prep, matched_system, folds, limits, seed=seed, progress=say, cache=predictions)
        state["evaluated"] += 2
        variant_rows += runs_to_rows(meta_system, meta_runs) + runs_to_rows(matched_system, matched_runs)
        wins_ref, evaluated_ref = beats(meta_runs, base_runs)
        wins_matched, evaluated_matched = beats(meta_runs, matched_runs)
        meta_admissible, meta_details = evaluate_variant(meta_system, meta_runs)
        meta_kept = meta_filter_kept(wins_ref, evaluated_ref, meta_admissible, wins_matched, evaluated_matched, rule)
        decisions.append({"variant": "méta-filtre", "key": meta_system.key, "wins_vs_reference": wins_ref,
                          "wins_vs_matched_abstention": wins_matched, "evaluated": evaluated_ref,
                          "evaluated_vs_matched_abstention": evaluated_matched,
                          "admissible": meta_admissible, "kept": meta_kept,
                          **({"strict": meta_details} if meta_details else {})})

        variants = pd.DataFrame(variant_rows)
        variants.to_csv(report_dir / "variants.csv", index=False)
        # Un pli NOT_EVALUABLE du méta-filtre trade tous les candidats (comme la base) : il compte dans la
        # stabilité et le Sharpe médian ; il n'est exclu que de la comparaison méta-filtre / référence.
        variant_summary = summarize(variants.assign(status=variants["status"].replace({"NOT_EVALUABLE": "OK"})), rule)
        eligible_keys = {d["key"] for d in decisions if d["kept"]}
        eligible = pd.concat([admissible, variant_summary[variant_summary["key"].isin(eligible_keys)
                                                          & variant_summary["stable"]]], ignore_index=True)
        final_system: System | None = None
        if not eligible.empty:
            best = eligible.sort_values("median_sharpe", ascending=False).iloc[0]
            final_system = _system_from_row(best, program)
        conclusion = ADMISSIBLE if final_system else NO_EDGE

        analysed = final_system or reference
        runs_by_key = {**cached_runs, reference.key: ref_runs, meta_system.key: meta_runs,
                       matched_system.key: matched_runs, **family_systems}
        analysed_runs = runs_by_key.get(analysed.key) or run_system(prep, analysed, folds, limits, seed=seed,
                                                                    progress=say, cache=predictions)
        analysis = analyse(prep, analysed, analysed_runs, limits, settings=settings, progress=say)
        say("journal des décisions et modèles")
        model_hashes, fold_hashes = save_models(analysed_runs, report_dir / "models")
        journal(prep, analysed, analysed_runs, fold_hashes).to_parquet(report_dir / "decisions.parquet",
                                                                       compression="zstd", index=False)

        n_trials = state["evaluated"]
        program_trials = program_before + n_trials
        summary.to_csv(report_dir / "summary_systems.csv", index=False)
        payload = {
            "run_id": run_id, "program": program.strategy_id, "protocol_version": program.protocol_version,
            "conclusion": conclusion, "final_system": final_system.to_dict() if final_system else None,
            "reference": reference.to_dict() | {"status": reference_status},
            "analysed_system_status": "RETENU" if final_system else "DIAGNOSTIC (non admissible)",
            "folds": [f.to_dict() for f in folds], "limits": limits.__dict__, "selection_rule": rule.__dict__,
            "n_trials": n_trials, "declared_trials": program.declared_trials, "program_trials": program_trials,
            "stable_count": int(summary["stable"].sum()), "admissible_count": int(len(admissible)),
            "strict_checks": strict_details, "top_systems": _records(summary.head(15)),
            "target_comparison": compare_targets(summary, results),
            "variants": decisions, "variant_systems": _records(variant_summary),
            "analysis": analysis, "leak_audit": {k: v for k, v in audit.items() if k != "violations"},
            "models": model_hashes, "finer_check": {"done": False, "required_before_final": True,
                                                    "label": program.finer_label},
            "grid_edges": _grid_edges(program, analysed), "source_fingerprint": source_fingerprint(program),
            "config_fingerprint": config_fingerprint(settings), "reserves": program.reserves(program_before),
        }
        _write(program, report_dir, payload)
        experiments.record(
            run_id=run_id, created_at=now.isoformat(), kind=program.kind_select, hypothesis=program.hypothesis,
            strategy=program.strategy_id, strategy_version=program.protocol_version,
            variant=f"protocole v{program.protocol_version}",
            params={"targets": list(program.targets), "horizons": list(program.horizons),
                    "models": [s.name for s in program.specs], "margins": list(program.margins),
                    "family_variants": program.family_variants, "meta_features": list(program.meta_features),
                    "selection_rule": rule.__dict__, **program.extra_params},
            period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
            cost_scenario="central (robustesse : central + 1 bougie, adverse, stress)",
            simulation_rules={"limits": limits.__dict__, "folds": _folds_text(program),
                              "engine": "ouverture suivante, sortie selon la cible, ordre intra-bougie défavorable"},
            metrics={"conclusion": conclusion, "verdict": conclusion, "n_trials": n_trials,
                     "program_trials": program_trials, "final_system": payload["final_system"],
                     "reference": payload["reference"], "admissible_count": payload["admissible_count"],
                     "source_fingerprint": payload["source_fingerprint"],
                     "config_fingerprint": payload["config_fingerprint"]},
            status="COMPLETED", report_dir=str(report_dir), **prep.common)
        return SelectResult(run_id, conclusion, payload["final_system"], payload["reference"], summary, audit,
                            report_dir, payload)
    except Exception as exc:
        _record_failure(program, experiments, run_id, now, period, state, report_dir, exc)
        raise


def _folds_text(program: Program) -> str:
    train = "ancré" if program.train_months is None else f"{program.train_months} mois"
    return (f"entraînement {train} (dont {program.calib_months} mois d'étalonnage) / validations de "
            f"{program.valid_months} mois")


def _record_failure(program: Program, experiments: ExperimentRegistry, run_id: str, now: datetime, period,
                    state: dict, report_dir: Path, exc: Exception) -> None:
    """Un échec est enregistré avec ses paramètres et les essais déjà évalués (ils ont été vus)."""
    common = state.get("common") or {"git_commit": "inconnu", "dependencies": {}, "seed": 0, "universe": [],
                                     "data_hashes": {}}
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind=program.kind_select, hypothesis=program.name,
        strategy=program.strategy_id, strategy_version=program.protocol_version,
        variant=f"protocole v{program.protocol_version}", params={}, period_label=period.label,
        period_start=period.start.isoformat(), period_end=period.end.isoformat(), cost_scenario="central",
        simulation_rules={},
        metrics={"verdict": "FAILED", "error": f"{type(exc).__name__}: {exc}", "n_trials": int(state.get("evaluated", 0))},
        status="FAILED", report_dir=str(report_dir), **common)


def _grid_edges(program: Program, system: System) -> list[str]:
    """Un système retenu au bord de la grille déclarée invite à la prudence (optimum peut-être hors grille)."""
    edges = []
    if len(program.horizons) > 1 and system.horizon in (min(program.horizons), max(program.horizons)):
        edges.append(f"horizon H={system.horizon} au bord de {list(program.horizons)}")
    if len(program.margins) > 1 and system.margin in (min(program.margins), max(program.margins)):
        edges.append(f"marge {system.margin:.2%} au bord de {[f'{m:.2%}' for m in program.margins]}")
    return edges


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", force_ascii=False)) if not frame.empty else []


def _fmt(value, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return f"{value:.2%}" if pct else f"{value}"


def _write(program: Program, report_dir: Path, payload: dict) -> None:
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    analysis = payload["analysis"]
    system = analysis["system"]
    rule = program.selection
    lines = [f"# {program.name} — {payload['run_id']} (protocole v{payload['protocol_version']})", "",
             f"**Conclusion : {payload['conclusion']}**", ""]
    if payload["conclusion"] == NO_EDGE:
        lines += ["Aucun système testé ne satisfait la règle d'admission déclarée : **aucune stratégie testée ne "
                  "démontre d'avantage exploitable**. La période finale n'est pas consultée et reste vierge.", ""]
    else:
        lines += [f"Système retenu : `{payload['final_system']['key']}`. Prochaine étape obligatoire : "
                  f"vérification en bougies {program.finer_label}, puis une seule consultation de la période finale "
                  "(décision du propriétaire).", ""]
    lines += [f"Essais de cette exécution : {payload['n_trials']} (déclarés : {payload['declared_trials']}) ; "
              f"programme sur DEVELOPMENT : {payload['program_trials']}.", "",
              f"Règle d'admission : Sharpe > 0 dans au moins {rule.stability_share:.0%} des validations"
              + (" (pour compter, une validation a au moins " + " et ".join(
                  ([f"{rule.min_trades_per_fold} trades"] if rule.min_trades_per_fold else [])
                  + ([f"{rule.min_entry_periods_per_fold} périodes d'entrée distinctes de max(1 jour, H)"]
                     if rule.min_entry_periods_per_fold else [])) + ")"
                 if rule.min_trades_per_fold or rule.min_entry_periods_per_fold else "")
              + f", au moins {rule.min_trades} trades au total"
              + (" ; puis IC du gain moyen > 0"
                 + (", IC de son excès sur la moyenne de toutes les décisions au même instant > 0" if rule.excess_check else "")
                 + ", positif en coûts défavorables et sans le 1 % des meilleurs "
                 f"trades, aucune paire ni validation > {rule.max_group_share:.0%} du gain" if rule.strict else "")
              + (f" (IC : Student sur sommes par blocs de jours calendaires, au moins {rule.min_ci_blocks} blocs "
                 "avec trades)" if rule.ci_method == "student_calendar" else "")
              + f". Systèmes stables : {payload['stable_count']} ; admissibles : {payload['admissible_count']}.", "",
              f"Audit des fuites : {'réussi' if payload['leak_audit']['passed'] else 'ÉCHEC'} (mutation détectée : "
              f"{payload['leak_audit']['mutation_detected']}).", "",
              "## Meilleurs systèmes (validation)", "",
              "| Système | Validations > 0 | Trades | Sharpe médian | Stable | Admissible |", "|---|---|---|---|---|---|"]
    lines += [f"| `{r['key']}` | {r['positive_folds']}/{r['evaluated_folds']} | {r['trades']} | {r['median_sharpe']} | "
              f"{'oui' if r['stable'] else 'non'} | {'oui' if r['admissible'] else 'non'} |"
              for r in payload["top_systems"][:10]]
    if payload.get("strict_checks"):
        lines += ["", "## Contrôles stricts des systèmes stables", "",
                  "| Système | IC gain moyen > 0 | IC excès sur le marché > 0 | Coûts défavorables > 0 | "
                  "Sans meilleurs trades > 0 | Concentration | Admis |", "|---|---|---|---|---|---|---|"]
        for key, d in payload["strict_checks"].items():
            c = d["checks"]
            lines.append(f"| `{key}` | {_fmt(d['avg_net_ci95'])} | {_fmt(d.get('excess_ci95'))} | {_fmt(d['adverse_avg_net'])} | "
                         f"{_fmt(d['avg_net_without_top1pct'])} | paire {_fmt(d['pnl_share_max_symbol'])}, validation "
                         f"{_fmt(d['pnl_share_max_fold'])} | {'oui' if d['passed'] else 'non'} "
                         f"({', '.join(k for k, v in c.items() if not v) or 'tout passe'}) |")
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
              f"- Gain moyen par trade {_fmt(totals.get('avg_net_per_trade'))} (IC95, méthode {rule.ci_method} : "
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
              f"« p » est la probabilité ÉTALONNÉE (Platt, {program.calib_months} mois postérieurs à l'ajustement) que "
              "le rendement net de l'entrée selon la règle de sortie de la cible soit positif ; sa fiabilité est "
              "mesurée en validation (calibration.csv : Brier, erreur d'étalonnage, AUC). Journal de chaque décision : "
              "decisions.parquet."]
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- Estimation unique (FINAL_TEST) -----------------------------------------------------------------------

def final_criteria(metrics: dict, random: dict, benchmarks: dict, robustness: list[dict], exceptional: dict,
                   max_group_share: float = 0.6) -> tuple[str, list[dict]]:
    """Critères déclarés avant la consultation (protocole intraday §7, repris par le swing §5)."""
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


def final(program: Program, settings: Settings, *, now: datetime, allow_final_test: bool,
          selection_run: str | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Une seule consultation de FINAL_TEST avec le système figé par la sélection."""
    say = progress or (lambda _text: None)
    period = resolve_period(settings, "final-test", now=now, allow_final_test=allow_final_test)
    experiments = ExperimentRegistry(settings.experiments_db)
    if experiments.final_test_consultations_total():
        raise FinalTestLocked("la période finale a déjà été consultée par le programme de recherche (compteur "
                              "global, docs/PROTOCOL.md) : elle n'est plus un test vierge")
    run = experiments.get(selection_run) if selection_run else next(
        (experiments.get(r["run_id"]) for r in experiments.recent(500)
         if r["kind"] == program.kind_select and r["status"] == "COMPLETED"), None)
    if not run or run["kind"] != program.kind_select or run["status"] != "COMPLETED":
        raise FinalTestLocked(f"aucune sélection {program.name} terminée : la période finale reste vierge")
    if run["metrics"].get("conclusion") != ADMISSIBLE:
        raise FinalTestLocked("la sélection n'a retenu aucun système (aucun avantage démontré) : la période finale "
                              "n'est pas consultée")
    passed, detail = program.finer_check(Path(run["report_dir"]))
    if not passed:
        raise FinalTestLocked(f"vérification en bougies {program.finer_label} : {detail} (obligatoire avant la "
                              "période finale)")
    if run["metrics"].get("source_fingerprint") != source_fingerprint(program) or \
            run["metrics"].get("config_fingerprint") != config_fingerprint(settings):
        raise FinalTestLocked("code ou configuration qui décident différents de ceux de la sélection : relancer "
                              "la sélection (ses essais seront comptés) avant la période finale")
    require_clean_code(settings, allow_dirty=False)
    system = System.from_dict(run["metrics"]["final_system"] | {"program": program.strategy_id})
    limits = RiskLimits.from_settings(settings.risk)
    run_id = new_run_id(program.run_prefix + "F")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    consultations = experiments.consult_final_test(run_id, program.strategy_id)
    prep = program.prepare(settings, end=now, progress=say)
    final_folds = program.folds(settings, period.start, period.end)
    folds = (program.folds(settings, program.first_valid(settings), pd.Timestamp(period.start) - pd.Timedelta(seconds=1))
             if system.filter != "none" else [])
    offset = len(folds)
    folds += [replace(f, index=f.index + offset) for f in final_folds]
    runs = [r for r in run_system(prep, system, folds, limits, seed=settings.protocol.seed, progress=say)
            if r.fold.index >= offset]
    trades = all_trades(runs, program.labels)
    metrics = aggregate(runs, settings, block_days_for(program, system.horizon, settings),
                        ci_method=program.selection.ci_method,
                        min_ci_blocks=program.selection.min_ci_blocks)
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
                            draws=program.random_draws, seed=settings.protocol.seed, step_ns=step_ns(program),
                            strategy=program.strategy_id)
    benchmarks = buy_and_hold(prep.daily_closes, period.start, period.end)
    robustness = [replay(prep, system, runs, limits, "central", delay=1),
                  replay(prep, system, runs, limits, "adverse"), replay(prep, system, runs, limits, "stress")]
    exceptional = exceptional_dependence(trades)
    verdict, criteria = final_criteria(metrics, random, benchmarks, robustness, exceptional,
                                       settings.admission.max_group_pnl_share)
    labels = [c for c in trades.columns if c not in ("symbol", "entry_time", "exit_time", "notional", "net", "pnl",
                                                     "candidate", "fold")]
    payload = {"run_id": run_id, "selection_run": run["run_id"], "system": system.to_dict(), "verdict": verdict,
               "criteria": criteria, "metrics": metrics, "random_entries": random, "benchmarks": benchmarks,
               "robustness": robustness, "exceptional_trades": exceptional,
               "breakdown": breakdown(trades, tuple(labels)) if len(trades) else {},
               "final_test_consultations": consultations,
               "reserves": program.reserves(experiments.program_trials("DEVELOPMENT"))}
    payload["models"], fold_hashes = save_models(runs, report_dir / "models")
    journal(prep, system, runs, fold_hashes).to_parquet(report_dir / "decisions.parquet", compression="zstd",
                                                        index=False)
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind=program.kind_final, hypothesis="estimation unique du système retenu",
        strategy=program.strategy_id, strategy_version=program.protocol_version, variant=system.key,
        params=system.to_dict(), period_label=period.label, period_start=period.start.isoformat(),
        period_end=period.end.isoformat(), cost_scenario="central (+ robustesse)",
        simulation_rules={"limits": limits.__dict__},
        metrics={"verdict": verdict, "n_trials": 1, "criteria": criteria, "final_test_consultations": consultations},
        status="COMPLETED", report_dir=str(report_dir), **prep.common)
    return payload
