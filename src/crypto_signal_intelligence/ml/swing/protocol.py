"""Protocole ML swing v3 (docs/ML_SWING.md), déclaré et commité avant toute exécution.

Programme du moteur commun (`ml/engine.py`) : bougies 1 h, une décision toutes les 4 h, horizons 1, 3 et
7 jours, cibles horizon fixe et triple barrière, logistique / LightGBM / XGBoost / CatBoost, entraînement
ancré, 6 validations, règle d'admission STRICTE v6 (leçons de la sélection intraday) complétée avant toute
exécution (périodes d'entrée distinctes, IC de Student sur blocs calendaires, excès sur le marché). Aucun ordre.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ...config import CostScenario, Settings
from ...features.higher_tf import resample_complete
from .. import engine
from ..engine import Prepared, SelectionRule, SelectResult
from ..intraday.models import ModelSpec
from ..intraday.protocol import common_context, daily_closes
from . import dataset as ds

PROTOCOL_VERSION = 3
KIND_SELECT, KIND_FINAL = "ML_SWING_SELECT", "ML_SWING_FINAL"
STRATEGY_ID = "ML_SWING"
MARGINS = (0.0, 0.0025, 0.0050)
CALIB_MONTHS, VALID_MONTHS, FIRST_VALID_MONTHS = 3, 6, 18
MIN_CALIB_ROWS = 200
SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("logistic", "logistic_l2", {"l2": 1.0}),
    *(ModelSpec("lightgbm", f"lgbm_leaves{leaves}_n300",
                {"num_leaves": leaves, "n_estimators": 300, "learning_rate": 0.05, "min_child_samples": 100,
                 "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8}) for leaves in (15, 63)),
    *(ModelSpec("xgboost", f"xgb_depth{depth}_n300",
                {"max_depth": depth, "n_estimators": 300, "learning_rate": 0.05, "min_child_weight": 20,
                 "subsample": 0.8, "colsample_bytree": 0.8}) for depth in (3, 6)),
    *(ModelSpec("catboost", f"catboost_depth{depth}_n400",
                {"depth": depth, "iterations": 400, "learning_rate": 0.05, "l2_leaf_reg": 3}) for depth in (4, 6)),
)
ALL_FAMILIES = tuple(ds.FAMILIES)
FAMILY_VARIANTS: dict[str, tuple[str, ...]] = {
    "prix": ("prix", "calendrier"),
    "prix+volume": ("prix", "volume", "calendrier"),
    "prix+transactions": ("prix", "transactions", "calendrier"),
    "prix+volume+transactions": ("prix", "volume", "transactions", "calendrier"),
    "sans contexte": tuple(f for f in ALL_FAMILIES if f != "contexte"),
    "sans marché": tuple(f for f in ALL_FAMILIES if f != "marche"),
    "sans coupe transversale": tuple(f for f in ALL_FAMILIES if f != "coupe"),
    "sans calendrier": tuple(f for f in ALL_FAMILIES if f != "calendrier"),
}
META_FEATURES = ("p", "expected", "rv_168", "atr_pct", "d1_ret_30", "btc_ret_168", "xs_rank_168", "r_168",
                 "vol_ratio_24", "taker_24", "h4_ret_42", "dd_720")
CONTEXT_REQUIRED = ("h4_ret_6", "d1_ret_7", "btc_ret_24")
RULE = SelectionRule(stability_share=0.70, min_trades=150, min_trades_per_fold=20, min_entry_periods_per_fold=20,
                     strict=True, max_group_share=0.6, ci_method="student_calendar", min_ci_blocks=20,
                     excess_check=True)
AUDIT_PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
AUDIT_TIMES = 4
DECISION_MODULES = ("ml/engine.py", "research/intervals.py", "ml/swing/dataset.py", "ml/swing/protocol.py", "ml/intraday/dataset.py",
                    "ml/intraday/models.py", "ml/intraday/portfolio.py", "ml/intraday/protocol.py", "ml/logistic.py",
                    "risk/exposure.py", "features/higher_tf.py", "features/indicators.py", "data/quality.py",
                    "domain/market.py")


def reserves(program_trials_before: int) -> list[str]:
    return [
        "Univers : les 16 paires actuelles, choisies aujourd'hui (filtre halal) et toutes cotées sur toute la "
        "période : biais du survivant possible (expérience avancée 1 du protocole intraday).",
        f"Sélection sur {SWING.declared_trials} essais ; le programme comptait déjà {program_trials_before} essais "
        "sur DEVELOPMENT avant cette exécution : un résultat isolé favorable doit être lu avec ce nombre en tête.",
        "Règle d'admission v6 (stricte, complétée en v2) : elle réduit les faux positifs de la règle intraday, "
        "sans les supprimer ; « admissible » reste une sélection en échantillon, que seule la période finale juge.",
        "Hasard : à pile ou face, un système obtient au moins 5 validations positives sur 6 avec une probabilité "
        "d'environ 10,9 % (davantage pour un portefeuille long seul en marché haussier) ; d'où les critères 3 à 7.",
        "Limite de perte journalière presque inerte : capital réalisé et positions jusqu'à 7 jours ; la perte "
        "latente n'est pas suivie par la simulation.",
        "Étalonnage de Platt sur 3 mois : pour H = 7 jours, une douzaine de semaines indépendantes seulement.",
        "Gain et perte moyens de l'espérance nette estimés sur l'entraînement ancré, qui inclut 2021 (marché très "
        "haussier).",
        "Cibles qui traversent un trou de données exclues : léger biais de sélection possible autour des incidents.",
        "Regards non comptés : le tableau de bord (analyse d'une paire de 1 h à 7 jours) montre des statistiques "
        "sur DEVELOPMENT ; ces regards ne sont pas des essais enregistrés.",
        "Critère 7 (excès sur le marché) : il ne mesure que la sélection ENTRE paires ; un avantage de pur timing, "
        "le même pour toutes les paires, est rejeté par construction (choix déclaré avant exécution).",
        "IC des critères 3 et 7 : environ 2 % de faux positifs simulés seulement si la dépendance ne dépasse pas "
        "deux blocs voisins ; avec une dérive commune persistante, le critère 3 en accepte environ 9 à 10 %, et un "
        "penchant persistant vers les mêmes paires (survivantes) passe le critère 7 dans 5 à 11 % des tirages "
        "(17 % avec une demi-vie de 140 jours), simulation de relecture : limite d'un échantillon de 3 ans.",
        "Cibles qui se chevauchent dans l'entraînement (déclaré) ; purge par la barrière verticale entre blocs.",
        "Moteur : remplissage complet à l'ouverture de la bougie 1 h suivante, ordre intra-bougie défavorable, "
        "capital réalisé, pas d'impact de marché ; vérification en bougies 15 min obligatoire avant la période "
        "finale.",
        "Aucun ordre, aucun signal publié : un système retenu n'ouvrirait qu'une phase shadow prospective.",
    ]


# --- Données -------------------------------------------------------------------------------------------

def prepare(settings: Settings, *, end, progress: Callable[[str], None]) -> Prepared:
    """Décisions de toutes les paires jusqu'à `end` (inclus), variables (coupe transversale comprise) et
    cibles en coûts centraux."""
    inputs, common = common_context(settings, end)
    frames = []
    for symbol in settings.data.symbols:
        progress(f"variables {symbol}")
        data = inputs[symbol]
        frames.append(ds.pair_decisions(data["context"], data["btc"], symbol=symbol, costs=settings.costs["central"]))
    frame = ds.add_cross_section(pd.concat(frames, ignore_index=True))
    meta = frame.drop(columns=[*ds.FEATURES, *(c for c in frame.columns if c in ("available_at", "r_720"))])
    meta["symbol"] = meta["symbol"].astype("category")
    return Prepared(meta, frame[list(ds.FEATURES)].to_numpy(np.float32), inputs, common, daily_closes(inputs),
                    dict(settings.costs), pd.Timestamp(end), list(settings.data.symbols),
                    engine.PROGRAMS[STRATEGY_ID])


def scenario_targets(prep: Prepared, kind: str, horizon: int, costs: CostScenario,
                     delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Cibles recalculées sous un autre scénario de coûts / retard, alignées sur `prep.meta`."""
    nets, bars = [], []
    for symbol in prep.symbols:
        h1 = prep.inputs[symbol]["context"].sort_values("open_time").reset_index(drop=True)
        net, offset = ds.targets(h1, costs, horizon, kind, delay=delay)
        mask = ds.decision_mask(h1)
        nets.append(net[mask])
        bars.append(offset[mask])
    return np.concatenate(nets), np.concatenate(bars)


# --- Audit des fuites ------------------------------------------------------------------------------------

def _leaky_4h(candles: pd.DataFrame, hours: int) -> pd.DataFrame:
    """MUTATION volontaire : bougie 4 h jointe sur son ouverture (visible dès qu'elle commence : fuite)."""
    bars = resample_complete(candles, hours)
    return bars.assign(available_at=bars["open_time"]) if hours == 4 else bars


def _leaky_1d(candles: pd.DataFrame, hours: int) -> pd.DataFrame:
    """MUTATION volontaire : bougie 1 jour visible dès la fin de sa première heure (fuite)."""
    bars = resample_complete(candles, hours)
    return bars.assign(available_at=bars["open_time"] + pd.Timedelta(hours=1, seconds=3)) if hours == 24 else bars


# Chaque mutation doit être détectée par les variables de SA famille (une seule ne suffit pas).
MUTATIONS: dict[str, tuple[Callable[[pd.DataFrame, int], pd.DataFrame], str]] = {
    "4h": (_leaky_4h, "h4_"), "1d": (_leaky_1d, "d1_")}


def leak_audit(settings: Settings, prep: Prepared, *, seed: int) -> dict:
    """Variables de la paire recalculées avec seulement le passé, puis avec un futur falsifié : identiques ;
    chaque mutation (bougie 4 h jointe sur son ouverture, bougie 1 jour visible après sa première heure) doit
    être détectée par les variables de sa famille ; la coupe transversale à une heure de décision ne change pas
    quand on coupe toutes les paires à cette heure."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(MUTATIONS, False)
    present = [s for s in AUDIT_PAIRS if s in prep.inputs]
    for symbol in present:
        h1 = prep.inputs[symbol]["context"].sort_values("open_time").reset_index(drop=True)
        btc = prep.inputs[symbol]["btc"]
        if len(h1) < 2000:
            continue
        days = h1["open_time"].dt.floor("D").unique()
        picks = sorted(rng.choice(days[60:-10], size=AUDIT_TIMES, replace=False))
        moments = [pd.Timestamp(day) + pd.Timedelta(hours=11) for day in picks]       # décision de 12:00
        lo, hi = moments[0] - pd.Timedelta(days=120), moments[-1] + pd.Timedelta(days=3)

        def window(frame: pd.DataFrame, lo=lo, hi=hi) -> pd.DataFrame:
            return frame[(frame["open_time"] >= lo) & (frame["open_time"] <= hi)].reset_index(drop=True)

        violations += [v | {"symbol": symbol} for v in ds.causality_violations(
            window(h1), window(btc), decisions=moments, seed=seed)]
        for name, (resampler, prefix) in MUTATIONS.items():
            found = ds.causality_violations(window(h1), window(btc), decisions=moments, seed=seed, resampler=resampler)
            detected[name] |= any(f.startswith(prefix) for v in found for f in v["features"])
    # Coupe transversale : calcul complet contre calcul où TOUTES les paires sont coupées à la décision.
    if len(present) >= 2:
        h1s = {s: prep.inputs[s]["context"].sort_values("open_time").reset_index(drop=True) for s in present}
        days = h1s[present[0]]["open_time"].dt.floor("D").unique()
        moment = pd.Timestamp(days[len(days) // 2]) + pd.Timedelta(hours=11)
        decision = moment + ds.STEP
        lo = moment - pd.Timedelta(days=60)

        def section(cut: bool) -> pd.DataFrame:
            frames = []
            for s in present:
                h1 = h1s[s][h1s[s]["open_time"] >= lo]
                btc = prep.inputs[s]["btc"][prep.inputs[s]["btc"]["open_time"] >= lo]
                if cut:
                    known = h1.loc[h1["open_time"] == moment, "available_at"]
                    if known.empty:
                        continue
                    h1, btc = h1[h1["available_at"] <= known.iloc[0]], btc[btc["available_at"] <= known.iloc[0]]
                else:
                    h1 = h1[h1["open_time"] <= moment + pd.Timedelta(days=3)]
                frames.append(ds.pair_frame(h1, btc).assign(symbol=s))
            out = ds.add_cross_section(pd.concat(frames, ignore_index=True))
            return out[out["decision_time"] == decision].set_index("symbol")[list(ds.FAMILIES["coupe"])].sort_index()

        full, truncated = section(False), section(True)
        if not np.allclose(full.to_numpy(float), truncated.to_numpy(float), equal_nan=True):
            violations.append({"check": "coupe transversale", "decision_time": str(decision)})
    mutation_detected = all(detected.values())
    return {"violations": violations, "mutation_detected": mutation_detected, "mutation_by_timeframe": detected,
            "checked_pairs": present, "times_per_pair": AUDIT_TIMES, "passed": not violations and mutation_detected}


def labels(candidates: pd.DataFrame) -> pd.DataFrame:
    """Contexte de marché des candidats : tendance 30 jours de la paire, BTC sur 7 jours, momentum relatif."""
    trend = candidates.get("d1_ret_30", pd.Series(np.nan, index=candidates.index))
    btc = candidates.get("btc_ret_168", pd.Series(np.nan, index=candidates.index))
    rank = candidates.get("xs_rank_168", pd.Series(np.nan, index=candidates.index))
    return pd.DataFrame({
        "tendance_30j": np.select([trend > 0, trend <= 0], ["hausse", "baisse"], "inconnue"),
        "btc_7j": np.select([btc > 0, btc <= 0], ["hausse", "baisse"], "inconnu"),
        "momentum_relatif": np.select([rank >= 0.5, rank < 0.5], ["fort", "faible"], "inconnu"),
    }, index=candidates.index)


def finer_check(report_dir: Path) -> tuple[bool, str]:
    """Vérification en bougies 15 min (docs/ML_SWING.md §5) : construite seulement si un système est admissible."""
    return False, "pas encore implémentée (construite seulement si une sélection retient un système)"


SWING = engine.register(engine.Program(
    name="ML swing", strategy_id=STRATEGY_ID, kind_select=KIND_SELECT, kind_final=KIND_FINAL, run_prefix="MLS",
    protocol_version=PROTOCOL_VERSION, doc="docs/ML_SWING.md",
    hypothesis="un modèle (logistique, LightGBM, XGBoost, CatBoost) sur bougies 1 h + contexte 4 h / 1 jour, BTC et "
               "coupe transversale sélectionne des entrées de 1 à 7 jours d'espérance nette positive, stable et "
               "robuste (règle v6)",
    step=ds.STEP, decision_every=ds.DECISION_EVERY, horizons=ds.HORIZONS, targets=ds.TARGETS,
    families=ds.FAMILIES, specs=SPECS, margins=MARGINS, family_variants=FAMILY_VARIANTS, meta_features=META_FEATURES,
    context_required=CONTEXT_REQUIRED, train_months=None, calib_months=CALIB_MONTHS, valid_months=VALID_MONTHS,
    first_valid_months=FIRST_VALID_MONTHS, min_calib_rows=MIN_CALIB_ROWS, selection=RULE,
    prepare=lambda settings, *, end, progress: prepare(settings, end=end, progress=progress),
    scenario_targets=scenario_targets,
    leak_audit=lambda settings, prep, *, seed: leak_audit(settings, prep, seed=seed),
    training_stride=lambda horizon: max(1, horizon // 16), labels=labels,
    reserves=lambda before: reserves(before), finer_check=lambda report_dir: finer_check(report_dir),
    finer_label="15 min", decision_modules=DECISION_MODULES,
    extra_params={"barrier_k": ds.BARRIER_K, "vol_window_bars": ds.VOL_WINDOW, "decision_every_bars": ds.DECISION_EVERY}))
DECLARED_TRIALS = SWING.declared_trials


def select(settings: Settings, *, now: datetime, allow_dirty: bool = False,
           progress: Callable[[str], None] | None = None, program: engine.Program = SWING) -> SelectResult:
    return engine.select(program, settings, now=now, allow_dirty=allow_dirty, progress=progress)


def final(settings: Settings, *, now: datetime, allow_final_test: bool, selection_run: str | None = None,
          progress: Callable[[str], None] | None = None, program: engine.Program = SWING) -> dict:
    return engine.final(program, settings, now=now, allow_final_test=allow_final_test, selection_run=selection_run,
                        progress=progress)
