"""Protocole ML intraday v5 (docs/ML_INTRADAY.md), déclaré et commité avant toute exécution.

Programme du moteur commun (`ml/engine.py`) : bougies 15 min, contexte 1 h / 4 h et BTC, horizons de
30 min à 4 h, cibles horizon fixe et triple barrière, logistique / LightGBM / XGBoost, 7 validations
glissantes de 12 mois, règle de stabilité v5. Ce module fournit les données, l'audit des fuites et les
textes propres à l'intraday ; le moteur fait le reste. Aucun ordre, aucun signal publié.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd

from ...config import CostScenario, Settings
from ...features.higher_tf import resample_complete
from ...features.loader import data_hashes
from ...research.backtest_run import run_context
from .. import engine
from ..engine import (  # noqa: F401 - réexportés pour la ligne de commande et les tests
    ADMISSIBLE,
    NO_EDGE,
    Fold,
    FoldRun,
    Prediction,
    Prepared,
    SelectionRule,
    SelectResult,
    Split,
    System,
    aggregate,
    all_trades,
    analyse,
    beats,
    compare_targets,
    config_fingerprint,
    final_criteria,
    fold_metrics,
    journal,
    predict_fold,
    replay,
    require_clean_code,
    run_grid,
    run_system,
    runs_to_rows,
    save_models,
    signals,
    split_rows,
)
from . import dataset as ds
from . import finer
from .models import SPECS

PROTOCOL_VERSION = 5
KIND_SELECT, KIND_FINAL = "ML_INTRADAY_SELECT", "ML_INTRADAY_FINAL"
STRATEGY_ID = "ML_INTRADAY"
MARGINS = (0.0, 0.0005, 0.0010)
TRAIN_MONTHS, CALIB_MONTHS, VALID_MONTHS = 12, 2, 6
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
AUDIT_PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
AUDIT_TIMES = 4
FEATURE_INDEX = {name: i for i, name in enumerate(ds.FEATURES)}
# Contexte inconnu (jointure périmée ou absente) → aucune décision, comme en service (§8).
CONTEXT_REQUIRED = ("h1_ret_24h", "h1_atr_pct", "h4_ret_6", "h4_atr_pct")
# Modules et réglages qui DÉCIDENT : la période finale exige qu'ils soient identiques à ceux de la sélection.
DECISION_MODULES = ("ml/engine.py", "research/intervals.py", "ml/intraday/dataset.py", "ml/intraday/models.py", "ml/intraday/portfolio.py",
                    "ml/intraday/protocol.py", "ml/logistic.py", "risk/exposure.py", "features/higher_tf.py",
                    "features/indicators.py", "features/builder.py", "regimes/classifier.py", "data/quality.py",
                    "domain/market.py")
RULE = SelectionRule(stability_share=0.70, min_trades=200)


def reserves(program_trials_before: int, declared: int | None = None) -> list[str]:
    declared = declared if declared is not None else INTRADAY.declared_trials
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


# --- Données -------------------------------------------------------------------------------------

def common_context(settings: Settings, end) -> tuple[dict, dict]:
    """Données de toutes les paires jusqu'à `end` (inclus) et traçabilité (empreintes APRÈS coupure)."""
    inputs, common = run_context(settings)
    limit = pd.Timestamp(end)
    inputs = {symbol: {name: frame[frame["open_time"] <= limit].reset_index(drop=True)
                       for name, frame in data.items()} for symbol, data in inputs.items()}
    common["data_hashes"] = {symbol: data_hashes(data) for symbol, data in inputs.items()}
    common["dependencies"] = ml_dependencies(settings, common["dependencies"])
    return inputs, common


def ml_dependencies(settings: Settings, base: dict) -> dict:
    """Versions à tracer pour un programme ML : celles de `base`, les bibliothèques de modèles et l'empreinte
    du verrou des dépendances."""
    dependencies = dict(base)
    for package in ("lightgbm", "xgboost", "catboost"):
        try:
            dependencies[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            dependencies[package] = "absent"
    lock = settings.root / "pylock.toml"
    dependencies["pylock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.exists() else "absent"
    return dependencies


def daily_closes(inputs: dict) -> pd.DataFrame:
    return pd.DataFrame({symbol: data["context"].set_index("open_time")["close"].resample("D").last()
                         for symbol, data in inputs.items() if not data["context"].empty})


def prepare(settings: Settings, *, end, progress: Callable[[str], None], program=None) -> Prepared:
    """Données de toutes les paires jusqu'à `end` (inclus), variables et cibles en coûts centraux."""
    inputs, common = common_context(settings, end)
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
    return Prepared(meta, np.vstack(matrices), inputs, common, daily_closes(inputs), dict(settings.costs),
                    pd.Timestamp(end), list(settings.data.symbols), program or engine.PROGRAMS[STRATEGY_ID])


def _scenario_targets(prep: Prepared, kind: str, horizon: int, costs: CostScenario,
                      delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Cibles recalculées sous un autre scénario de coûts / retard, alignées sur `prep.meta`."""
    nets, bars = [], []
    for symbol in prep.symbols:
        net, offset = ds.targets(prep.inputs[symbol]["setup"], costs, horizon, kind, delay=delay)
        nets.append(net)
        bars.append(offset)
    return np.concatenate(nets), np.concatenate(bars)


def scenario_targets(prep: Prepared, symbols: list[str], kind: str, horizon: int, costs: CostScenario,
                     delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Compatibilité : `symbols` doit être l'ordre de `prep.meta` (`prep.symbols`)."""
    assert list(symbols) == list(prep.symbols)
    return _scenario_targets(prep, kind, horizon, costs, delay)


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


def labels(candidates: pd.DataFrame) -> pd.DataFrame:
    """Contexte de marché des candidats pour l'analyse par contexte."""
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


INTRADAY = engine.register(engine.Program(
    name="ML intraday", strategy_id=STRATEGY_ID, kind_select=KIND_SELECT, kind_final=KIND_FINAL, run_prefix="MLI",
    protocol_version=PROTOCOL_VERSION, doc="docs/ML_INTRADAY.md",
    hypothesis="un modèle (logistique, LightGBM, XGBoost) sur 15 min + contexte 1 h/4 h sélectionne des entrées "
               "intraday d'espérance nette positive, stable d'une validation à l'autre",
    step=ds.STEP, decision_every=1, horizons=ds.HORIZONS, targets=ds.TARGETS, families=ds.FAMILIES, specs=SPECS,
    margins=MARGINS, family_variants=FAMILY_VARIANTS, meta_features=META_FEATURES,
    context_required=CONTEXT_REQUIRED, train_months=TRAIN_MONTHS, calib_months=CALIB_MONTHS,
    valid_months=VALID_MONTHS, first_valid_months=TRAIN_MONTHS, min_calib_rows=MIN_CALIB_ROWS, selection=RULE,
    prepare=lambda settings, *, end, progress: prepare(settings, end=end, progress=progress),
    scenario_targets=_scenario_targets,
    leak_audit=lambda settings, prep, *, seed: leak_audit(settings, prep, seed=seed),
    training_stride=lambda horizon: horizon, labels=labels,
    reserves=lambda before: reserves(before),
    finer_check=lambda report_dir: finer.minute_check_status(report_dir), finer_label="1 min",
    decision_modules=DECISION_MODULES, extra_params={"barrier_k": ds.BARRIER_K}, random_draws=RANDOM_DRAWS))
DECLARED_TRIALS = INTRADAY.declared_trials


# --- Compatibilité (ligne de commande, tests) -----------------------------------------------------

def make_folds(first_valid, end) -> list[Fold]:
    return engine.make_folds(first_valid, end, train_months=TRAIN_MONTHS, calib_months=CALIB_MONTHS,
                             valid_months=VALID_MONTHS)


def required_positive(evaluated: int) -> int:
    return RULE.required_positive(evaluated)


def kept(wins: int, evaluated: int) -> bool:
    return RULE.kept(wins, evaluated)


def summarize(results: pd.DataFrame, evaluated_folds: dict[str, int] | None = None) -> pd.DataFrame:
    return engine.summarize(results, RULE, evaluated_folds)


def _meta_mask(history: pd.DataFrame, current: pd.DataFrame) -> np.ndarray | None:
    return engine._meta_mask(history, current, META_FEATURES)


def source_fingerprint(package_root: Path | None = None) -> str:
    return engine.source_fingerprint(INTRADAY, package_root)


def select(settings: Settings, *, now: datetime, allow_dirty: bool = False,
           progress: Callable[[str], None] | None = None, program: engine.Program = INTRADAY) -> SelectResult:
    return engine.select(program, settings, now=now, allow_dirty=allow_dirty, progress=progress)


def final(settings: Settings, *, now: datetime, allow_final_test: bool, selection_run: str | None = None,
          progress: Callable[[str], None] | None = None, program: engine.Program = INTRADAY) -> dict:
    return engine.final(program, settings, now=now, allow_final_test=allow_final_test, selection_run=selection_run,
                        progress=progress)
