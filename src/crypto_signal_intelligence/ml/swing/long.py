"""Protocole ML swing long v1 (docs/ML_SWING_LONG.md), déclaré avant toute exécution.

Le protocole ML swing (`ml/swing/protocol.py` : mêmes variables, mêmes cibles, même règle d'admission v6, même
moteur `ml/engine.py`) rejoué sur tout l'historique long : bougies 1 h depuis la cotation de chaque paire
(magasin séparé `long_history/`, `research/long_history.py`), univers de recherche de 40 paires
(`research/universe.py`), entraînement ancré au 2017-08-17, 12 validations de 6 mois (2019-07 → 2025-06),
grille réduite à 42 essais. Ce module n'ajoute que ce qui lui est propre : le chargement du magasin long coupé
à la fin de DEVELOPMENT, l'appartenance à la date par la liquidité, l'alignement des cibles recalculées, un
contrôle de causalité du filtre de liquidité et les réserves déclarées. Le registre d'expériences, les
rapports, les coûts, les limites de risque et la graine restent ceux des réglages normaux. Aucun ordre, aucun
signal publié.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ...config import CostScenario, Settings
from ...data.store import content_hash
from ...research.experiments import code_state, dependency_versions
from ...research.long_history import load_long
from ...research.protocol import development_end
from ...research.universe import MARKET, RESEARCH_UNIVERSE
from .. import engine
from ..engine import Prepared, SelectResult
from ..intraday.models import ModelSpec
from ..intraday.protocol import daily_closes, ml_dependencies
from . import dataset as ds
from . import protocol as swing

PROTOCOL_VERSION = 1
KIND_SELECT, KIND_FINAL = "ML_SWING_LONG_SELECT", "ML_SWING_LONG_FINAL"
STRATEGY_ID = "ML_SWING_LONG"
UNIVERSE: tuple[str, ...] = RESEARCH_UNIVERSE
HISTORY_START = date(2017, 8, 17)            # première bougie de BTCUSDT sur Binance Spot : ancrage
FIRST_VALID_START = date(2019, 7, 1)         # 12 validations de 6 mois jusqu'au 2025-06-30
VALIDATIONS = 12
HORIZONS = (72, 168)                         # 3 et 7 jours
MARGINS = (0.0, 0.0025)
# La configuration la plus simple de chaque famille du swing, hyperparamètres inchangés ; fixée sans regarder
# les résultats par configuration de la sélection swing.
MODEL_NAMES = ("logistic_l2", "lgbm_leaves15_n300", "xgb_depth3_n300", "catboost_depth4_n400")
SPECS: tuple[ModelSpec, ...] = tuple(spec for spec in swing.SPECS if spec.name in MODEL_NAMES)
RULE = swing.RULE                            # règle stricte v6, inchangée
LIQUIDITY_DAYS = 30
LIQUIDITY_BARS = 24 * LIQUIDITY_DAYS         # 720 bougies 1 h clôturées, connues à la décision
MIN_DAILY_QUOTE_VOLUME = 1_000_000.0         # USDT par jour, en moyenne sur les 30 jours précédents
MAX_STALE = pd.Timedelta(days=2)             # chaque paire doit aller jusqu'à 2 jours de la coupure
LEAK_LEAD_BARS = 24                          # mutation de l'audit : fenêtre de volume décalée d'un jour
DECISION_MODULES = (*swing.DECISION_MODULES, "ml/swing/long.py", "research/long_history.py",
                    "research/universe.py")


def chance_of_stability(validations: int = VALIDATIONS, rule: engine.SelectionRule = RULE) -> float:
    """Probabilité qu'un système sans avantage obtienne le minimum de validations positives exigé si le signe
    de chaque validation était un pile ou face indépendant (critère 1 seul)."""
    needed = rule.required_positive(validations)
    return sum(math.comb(validations, k) for k in range(needed, validations + 1)) / 2 ** validations


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def reserves(program_trials_before: int) -> list[str]:
    needed = RULE.required_positive(VALIDATIONS)
    chance = chance_of_stability()
    base_systems = len(ds.TARGETS) * len(HORIZONS) * len(SPECS) * len(MARGINS)
    return [
        f"Univers : les {len(UNIVERSE)} paires de l'univers de recherche, choisies le 2026-10-01 parmi les cryptos "
        "encore cotées : une crypto retirée de la cote ou écartée depuis n'y figure pas (biais des survivantes). "
        "Ce biais favorise un portefeuille long seul ET la sélection entre paires (critère 7) : les paires "
        "effondrées puis retirées manquent, et les survivantes de 2019-2020 sont connues pour leur parcours "
        "ultérieur. Au swing, un penchant persistant vers les mêmes paires passait le critère 7 dans 5 à 11 % "
        "des tirages simulés (17 % avec une demi-vie de 140 jours) ; sur 6 ans, ce taux n'est pas simulé.",
        "Le filtre de liquidité retire peu de décisions (3,7 % au total ; 17 % en 2019, 6 % en 2020, presque "
        "rien ensuite) : l'appartenance à la date revient presque à la date de cotation.",
        "Appartenance à la date limitée à la disponibilité (cotation, contexte connu) et à la liquidité (volume "
        "moyen d'au moins 1 M$ par jour sur 30 jours) : elle ne recrée pas l'univers qu'on aurait choisi à l'époque.",
        "Peu de paires au début : 4 cotées avant 2018, 11 avant 2019, 18 avant la première validation "
        "(2019-07-01), 29 avant 2021. Les premières validations reposent sur peu de paires et peu de lignes "
        "d'ajustement ; la coupe transversale y compare peu de paires.",
        "Coûts supposés constants sur toute la période (scénario central), alors que les écarts de prix étaient "
        "plus larges en 2017-2019 : les premières validations sont probablement trop favorables.",
        "Coupe transversale calculée sur toutes les paires cotées à l'instant de la décision, liquides ou non.",
        "Liquidité : une heure sans bougie compte pour un volume nul ; le seuil de 1 M$ est fixe en dollars sur "
        "toute la période (aucune correction pour la taille du marché de l'époque).",
        f"Sélection sur {LONG.declared_trials} essais ; le programme comptait déjà {program_trials_before} essais "
        "sur DEVELOPMENT avant cette exécution. La période 2021-2025 a déjà été parcourue par ces essais, dont le "
        "swing sur 16 paires avec les mêmes variables et les mêmes cibles : ses validations ne sont pas "
        "indépendantes de ce qui a déjà été vu.",
        f"Hasard : à pile ou face, un système obtient au moins {needed} validations positives sur {VALIDATIONS} "
        f"avec une probabilité de {_decimal(100 * chance)} % ; sur {base_systems} systèmes de base, cela ferait "
        f"environ {_decimal(base_systems * chance)} systèmes « stables » par hasard s'ils étaient "
        "indépendants (ils ne le sont pas, et un portefeuille long seul en marché haussier en obtient davantage) ; "
        "d'où les critères 3 à 7.",
        "Règle d'admission v6 inchangée (docs/ML_SWING.md §4) : elle réduit les faux positifs sans les supprimer ; "
        "« admissible » reste une sélection en échantillon, que seule la période finale juge. Les taux de faux "
        "positifs des IC (critères 3 et 7) y ont été simulés pour 3 ans de validations, pas pour 6.",
        "Critère 7 (excès sur le marché) : la référence est la moyenne des décisions valides et LIQUIDES au même "
        "instant ; il ne mesure que la sélection entre paires, un avantage de pur timing est rejeté par "
        "construction.",
        "Vérification en bougies 15 min : le magasin 15 min commence le 2021-01-01, elle est impossible sur les "
        "validations 2019-2020 ; elle n'est pas encore construite et reste obligatoire avant la période finale.",
        "Gain et perte moyens de l'espérance nette estimés sur l'entraînement ancré, qui inclut 2017 et 2021 "
        "(marchés très haussiers) ; étalonnage de Platt sur 3 mois : pour H = 7 jours, une douzaine de semaines "
        "indépendantes seulement.",
        "Limite de perte journalière presque inerte : capital réalisé et positions jusqu'à 7 jours ; la perte "
        "latente n'est pas suivie par la simulation.",
        "Cibles qui traversent un trou de données exclues (les interruptions de Binance sont plus fréquentes "
        "avant 2021) ; cibles qui se chevauchent dans l'entraînement (déclaré), purge par la barrière verticale.",
        "Moteur : remplissage complet à l'ouverture de la bougie 1 h suivante, ordre intra-bougie défavorable, "
        "capital réalisé, pas d'impact de marché.",
        "Aucun ordre, aucun signal publié : un système retenu n'ouvrirait qu'une phase shadow prospective.",
    ]


# --- Appartenance à la date : liquidité ----------------------------------------------------------------

def daily_quote_volume(h1: pd.DataFrame, *, lead: int = 0) -> np.ndarray:
    """Volume moyen en USDT par jour sur les 30 jours qui précèdent la clôture de chaque bougie d'un tableau
    1 h TRIÉ : somme des `quote_volume` des 720 heures qui se terminent à cette clôture (la bougie elle-même
    comprise : elle est clôturée à la décision), divisée par 30.

    Une heure sans bougie compte pour un volume nul ; moins de 30 jours depuis la première bougie → inconnu
    (NaN). Chaque valeur ne dépend que de ses 720 heures : couper ou falsifier le futur n'y change rien.
    `lead` n'existe que pour le test de mutation de l'audit (fenêtre décalée de `lead` heures vers le futur)."""
    out = np.full(len(h1), np.nan)
    if h1.empty:
        return out
    times = h1["open_time"]
    slots = ((times - times.iloc[0]) // ds.STEP).to_numpy(np.int64)       # rang de l'heure depuis la cotation
    grid = np.zeros(int(slots[-1]) + 1)
    grid[slots] = h1["quote_volume"].to_numpy(float)
    if len(grid) < LIQUIDITY_BARS:
        return out
    sums = np.full(len(grid), np.nan)
    sums[LIQUIDITY_BARS - 1:] = sliding_window_view(grid, LIQUIDITY_BARS).sum(axis=1)
    position = slots + lead
    known = position < len(grid)
    out[known] = sums[position[known]] / LIQUIDITY_DAYS
    return out


def decision_rows(h1: pd.DataFrame) -> np.ndarray:
    """Lignes de décision du programme dans un tableau 1 h TRIÉ : fin d'un bloc de 4 h (comme le swing) ET
    paire liquide à cet instant. Seule définition de la population : `prepare` et `scenario_targets` s'en
    servent tous deux, les cibles recalculées restent alignées sur `prep.meta`."""
    return ds.decision_mask(h1) & (daily_quote_volume(h1) >= MIN_DAILY_QUOTE_VOLUME)


# --- Données -------------------------------------------------------------------------------------------

def load_inputs(settings: Settings, end) -> dict[str, dict[str, pd.DataFrame]]:
    """Bougies 1 h du magasin long de chaque paire de l'univers, coupées à `end` (bougies ouvertes au plus tard
    à `end`). Refus si une paire manque ou s'arrête plus de 2 jours avant la coupure (magasin incomplet)."""
    limit = pd.Timestamp(end)
    frames: dict[str, pd.DataFrame] = {}
    for symbol in dict.fromkeys((MARKET, *UNIVERSE)):
        frame = load_long(settings, symbol)
        frames[symbol] = frame[frame["open_time"] <= limit].reset_index(drop=True)
    stale = [s for s, f in frames.items() if f.empty or f["open_time"].iloc[-1] < limit - MAX_STALE]
    if stale:
        raise RuntimeError(f"magasin long incomplet avant la coupure du {limit.isoformat()} : {', '.join(stale)} "
                           "(lancer le téléchargement de l'historique long d'abord)")
    return {symbol: {"context": frames[symbol], "btc": frames[MARKET]} for symbol in UNIVERSE}


def _prepare(settings: Settings, *, end, progress: Callable[[str], None]) -> Prepared:
    """Décisions de toutes les paires jusqu'à `end` : variables du swing (coupe transversale sur toutes les
    paires cotées), cibles en coûts centraux, puis seules les lignes LIQUIDES sont gardées."""
    program = engine.PROGRAMS[STRATEGY_ID]
    limit = pd.Timestamp(end)
    inputs = load_inputs(settings, limit)
    market_hash = content_hash(next(iter(inputs.values()))["btc"]) if inputs else "EMPTY"
    common = {"git_commit": code_state(),
              "dependencies": ml_dependencies(settings, dependency_versions()),
              "seed": settings.protocol.seed, "universe": list(UNIVERSE),
              "data_hashes": {symbol: {"context": content_hash(data["context"]), "btc": market_hash}
                              for symbol, data in inputs.items()}}
    frames = []
    for symbol in UNIVERSE:
        progress(f"variables {symbol}")
        h1, btc = inputs[symbol]["context"], inputs[symbol]["btc"]
        frame = ds.pair_decisions(h1, btc, symbol=symbol, costs=settings.costs["central"],
                                  horizons=program.horizons, kinds=program.targets)
        at_decisions = ds.decision_mask(h1)
        if not np.array_equal(frame["open_time"].to_numpy(), h1.loc[at_decisions, "open_time"].to_numpy()):
            raise RuntimeError(f"{symbol} : lignes de décision désalignées sur les bougies 1 h")
        frame["quote_volume_30d"] = daily_quote_volume(h1)[at_decisions]
        frames.append(frame)
    frame = ds.add_cross_section(pd.concat(frames, ignore_index=True))       # avant le filtre : toutes les paires
    frame = frame[frame["quote_volume_30d"].to_numpy() >= MIN_DAILY_QUOTE_VOLUME].reset_index(drop=True)
    meta = frame.drop(columns=[*ds.FEATURES, *(c for c in frame.columns if c in ("available_at", "r_720"))])
    meta["symbol"] = meta["symbol"].astype("category")
    return Prepared(meta, frame[list(ds.FEATURES)].to_numpy(np.float32), inputs, common, daily_closes(inputs),
                    dict(settings.costs), limit, list(UNIVERSE), program)


def prepare(settings: Settings, *, end, progress: Callable[[str], None]) -> Prepared:
    """Données de la SÉLECTION : jamais au-delà de la fin de DEVELOPMENT, quelle que soit la date demandée."""
    limit = min(pd.Timestamp(end), pd.Timestamp(development_end(settings)))
    return _prepare(settings, end=limit, progress=progress)


def prepare_final(settings: Settings, *, end, progress: Callable[[str], None]) -> Prepared:
    """Données de l'estimation unique : seule `final` s'en sert, après les verrous du moteur (période finale
    explicitement demandée, consultation enregistrée)."""
    return _prepare(settings, end=end, progress=progress)


def scenario_targets(prep: Prepared, kind: str, horizon: int, costs: CostScenario,
                     delay: int) -> tuple[np.ndarray, np.ndarray]:
    """Cibles recalculées sous un autre scénario de coûts / retard, alignées sur `prep.meta` (mêmes lignes :
    décisions liquides de chaque paire, dans l'ordre des paires)."""
    nets, bars = [], []
    for symbol in prep.symbols:
        h1 = prep.inputs[symbol]["context"].sort_values("open_time").reset_index(drop=True)
        net, offset = ds.targets(h1, costs, horizon, kind, delay=delay)
        rows = decision_rows(h1)
        nets.append(net[rows])
        bars.append(offset[rows])
    return np.concatenate(nets), np.concatenate(bars)


# --- Audit des fuites ------------------------------------------------------------------------------------

def liquidity_violations(h1: pd.DataFrame, *, decisions: list[pd.Timestamp], seed: int = 0,
                         lead: int = 0) -> list[dict]:
    """Pour chaque décision (heure d'ouverture de sa bougie 1 h), le volume du filtre de liquidité recalculé
    avec les seules bougies disponibles à la décision, puis avec un futur falsifié, doit être identique au
    calcul complet. Renvoie les écarts (liste vide : causal)."""
    full = daily_quote_volume(h1, lead=lead)
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for moment in decisions:
        at = np.flatnonzero((h1["open_time"] == moment).to_numpy())
        if not len(at):
            continue
        known = (h1["available_at"] <= h1["available_at"].iloc[at[0]]).to_numpy()
        falsified = h1.assign(quote_volume=h1["quote_volume"].astype(float))
        falsified.loc[~known, "quote_volume"] *= rng.uniform(0.1, 3, int((~known).sum()))
        for label, frame in (("tronqué", h1[known]), ("futur falsifié", falsified)):
            got = daily_quote_volume(frame, lead=lead)[np.flatnonzero((frame["open_time"] == moment).to_numpy())[0]]
            if not (got == full[at[0]] or (np.isnan(got) and np.isnan(full[at[0]]))):
                problems.append({"open_time": str(moment), "check": f"liquidité : {label}",
                                 "features": ["quote_volume_30d"]})
    return problems


def population_violations(prep: Prepared, symbol: str) -> list[dict]:
    """La population PRÉPARÉE d'une paire est exactement celle de `decision_rows` (fonction de liquidité
    contrôlée par `liquidity_violations`) : mêmes heures de décision, même volume. Une fenêtre de liquidité
    différente, ou un filtre lisant le futur, dans `_prepare` est ainsi pris en défaut par l'audit lui-même."""
    h1 = prep.inputs[symbol]["context"].sort_values("open_time").reset_index(drop=True)
    rows = decision_rows(h1)
    expected = pd.Series(daily_quote_volume(h1)[rows], index=pd.DatetimeIndex(h1.loc[rows, "open_time"]))
    mine = prep.meta[(prep.meta["symbol"] == symbol).to_numpy()]
    got = pd.Series(mine["quote_volume_30d"].to_numpy(float), index=pd.DatetimeIndex(mine["open_time"]))
    if not got.index.equals(expected.index):
        return [{"symbol": symbol, "check": "population préparée ≠ décisions liquides",
                 "missing": int(len(expected.index.difference(got.index))),
                 "extra": int(len(got.index.difference(expected.index)))}]
    if not np.allclose(got.to_numpy(), expected.to_numpy(), rtol=0, atol=1e-6):
        return [{"symbol": symbol, "check": "volume de liquidité préparé ≠ volume recalculé"}]
    return []


def leak_audit(settings: Settings, prep: Prepared, *, seed: int) -> dict:
    """L'audit du swing (variables recalculées avec le seul passé, futur falsifié, mutations 4 h et 1 jour
    détectées chacune par sa famille, coupe transversale), plus deux contrôles propres à ce programme :
    - le volume du filtre de liquidité à une décision ne change pas quand on coupe ou falsifie les bougies
      après cette décision, et une fenêtre décalée d'un jour vers le futur (mutation) est détectée ;
    - aucune bougie chargée n'est postérieure à la coupure."""
    audit = swing.leak_audit(settings, prep, seed=seed)
    rng = np.random.default_rng(seed)
    violations: list[dict] = list(audit["violations"])
    liquidity_found: list[dict] = []
    liquidity_detected = False
    checked = 0
    for symbol in audit["checked_pairs"]:
        h1 = prep.inputs[symbol]["context"].sort_values("open_time").reset_index(drop=True)
        if len(h1) < 2000:
            continue
        days = h1["open_time"].dt.floor("D").unique()
        picks = sorted(rng.choice(days[60:-10], size=swing.AUDIT_TIMES, replace=False))
        moments = [pd.Timestamp(day) + pd.Timedelta(hours=11) for day in picks]       # décision de 12:00
        checked += 1
        liquidity_found += [v | {"symbol": symbol} for v in liquidity_violations(h1, decisions=moments, seed=seed)]
        liquidity_detected |= bool(liquidity_violations(h1, decisions=moments, seed=seed, lead=LEAK_LEAD_BARS))
    population = [v for symbol in prep.symbols for v in population_violations(prep, symbol)]
    # Coupure : la fin de DEVELOPMENT des réglages, pas `prep.end` (qui vient de la préparation elle-même).
    cutoff = pd.Timestamp(development_end(settings))
    late = sorted({symbol for symbol, data in prep.inputs.items() for frame in data.values()
                   if len(frame) and frame["open_time"].max() > cutoff})
    if prep.end > cutoff:
        late = sorted({*late, "(préparation au-delà de DEVELOPMENT)"})
    violations += liquidity_found + population + [{"check": "bougie après la coupure", "symbol": s} for s in late]
    mutation_detected = bool(audit["mutation_detected"]) and liquidity_detected
    return audit | {
        "violations": violations, "mutation_detected": mutation_detected,
        "liquidity": {"violations": len(liquidity_found), "mutation_detected": liquidity_detected,
                      "checked_pairs": checked, "times_per_pair": swing.AUDIT_TIMES,
                      "min_daily_quote_volume": MIN_DAILY_QUOTE_VOLUME},
        "cutoff": {"end": str(prep.end), "development_end": str(cutoff), "pairs_beyond": late},
        "population": {"violations": len(population), "checked_pairs": len(prep.symbols)},
        "passed": not violations and mutation_detected}


def finer_check(report_dir: Path) -> tuple[bool, str]:
    """Vérification en bougies 15 min (docs/ML_SWING_LONG.md §5) : construite seulement si un système est
    admissible ; les bougies 15 min n'existent qu'à partir de 2021."""
    return False, ("pas encore implémentée (construite seulement si une sélection retient un système ; les "
                   "bougies 15 min ne couvrent que les validations 2021-2025)")


LONG = engine.register(engine.Program(
    name="ML swing long", strategy_id=STRATEGY_ID, kind_select=KIND_SELECT, kind_final=KIND_FINAL, run_prefix="MLL",
    protocol_version=PROTOCOL_VERSION, doc="docs/ML_SWING_LONG.md",
    hypothesis="le protocole ML swing (bougies 1 h + contexte 4 h / 1 jour, BTC et coupe transversale, règle v6), "
               "rejoué sur l'historique long (2017-2025) et 40 paires avec appartenance à la date, sélectionne "
               "des entrées de 3 à 7 jours d'espérance nette positive, stable sur 12 validations et robuste",
    step=ds.STEP, decision_every=ds.DECISION_EVERY, horizons=HORIZONS, targets=ds.TARGETS,
    families=ds.FAMILIES, specs=SPECS, margins=MARGINS, family_variants=swing.FAMILY_VARIANTS,
    meta_features=swing.META_FEATURES, context_required=swing.CONTEXT_REQUIRED, train_months=None,
    calib_months=swing.CALIB_MONTHS, valid_months=swing.VALID_MONTHS,
    first_valid_months=0,                    # sans effet : `first_valid_start` fixe la première validation
    min_calib_rows=swing.MIN_CALIB_ROWS, selection=RULE,
    prepare=lambda settings, *, end, progress: prepare(settings, end=end, progress=progress),
    scenario_targets=scenario_targets,
    leak_audit=lambda settings, prep, *, seed: leak_audit(settings, prep, seed=seed),
    training_stride=swing.SWING.training_stride, labels=swing.labels,
    reserves=lambda before: reserves(before), finer_check=lambda report_dir: finer_check(report_dir),
    finer_label="15 min", decision_modules=DECISION_MODULES,
    extra_params={"barrier_k": ds.BARRIER_K, "vol_window_bars": ds.VOL_WINDOW,
                  "decision_every_bars": ds.DECISION_EVERY, "universe": list(UNIVERSE),
                  "history_start": HISTORY_START.isoformat(), "first_valid_start": FIRST_VALID_START.isoformat(),
                  "liquidity_bars": LIQUIDITY_BARS, "min_daily_quote_volume": MIN_DAILY_QUOTE_VOLUME,
                  "data_store": "long_history (bougies 1 h depuis la cotation)"},
    history_start=HISTORY_START, first_valid_start=FIRST_VALID_START))
DECLARED_TRIALS = LONG.declared_trials


def select(settings: Settings, *, now: datetime, allow_dirty: bool = False,
           progress: Callable[[str], None] | None = None, program: engine.Program = LONG) -> SelectResult:
    """Sélection ; refusée si la fin de DEVELOPMENT des réglages ne donne pas exactement les 12 validations
    déclarées (les réserves et le hasard calculé valent pour 12)."""
    folds = program.folds(settings, program.first_valid(settings), pd.Timestamp(development_end(settings)))
    if program is LONG and len(folds) != VALIDATIONS:
        raise RuntimeError(f"{len(folds)} validations au lieu des {VALIDATIONS} déclarées (fin de DEVELOPMENT "
                           f"{development_end(settings).isoformat()}) : exécution refusée")
    return engine.select(program, settings, now=now, allow_dirty=allow_dirty, progress=progress)


def final(settings: Settings, *, now: datetime, allow_final_test: bool, selection_run: str | None = None,
          progress: Callable[[str], None] | None = None, program: engine.Program = LONG) -> dict:
    """Estimation unique : la seule étape qui lit des bougies postérieures à DEVELOPMENT, après les verrous du
    moteur (système admissible, vérification fine, code et réglages identiques, consultation enregistrée)."""
    unclipped = replace(program, prepare=lambda settings, *, end, progress: prepare_final(
        settings, end=end, progress=progress))
    return engine.final(unclipped, settings, now=now, allow_final_test=allow_final_test,
                        selection_run=selection_run, progress=progress)
