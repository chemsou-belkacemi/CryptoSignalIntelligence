"""Confirmation des prévisions de volatilité sur la PÉRIODE FINALE réservée (docs/VOLATILITY.md § 18, déclaré le
2026-10-03 avant toute lecture, avec l'accord explicite du propriétaire : une seule lecture, pour la volatilité
seulement).

Cinq comparaisons, toutes déjà sélectionnées sur DEVELOPMENT, rejouées sans aucun réglage nouveau :
- `D1`, `D3`, `D7` : les modèles en service (LightGBM groupé à 1 et 3 jours, HAR + BTC à 7 jours) contre la
  référence M0 (variance des 7 derniers jours), protocole v1 (§ 12-13) ;
- `V3` : la moyenne des deux modèles (M4, M5) contre le modèle en service à 3 jours, protocole v2 (§ 15) ;
- `H24` : HAR + profil heure × jour contre « les 24 dernières heures » à 24 h, protocole v3 (§ 16).

Mêmes fonctions que les protocoles d'origine (`volatility.daily_frame`, `fit_at`, `month_forecasts`,
`volatility_hourly.hourly_frame`, `fit_at`, `quarter_forecasts`, lues sans modification : F12 et F14 les gèlent),
mêmes 40 paires, même rythme de réajustement (mensuel, trimestriel), mêmes purges. Origines du 2025-07-01 au
2026-09-30, cibles entièrement connues au plus tard à la bougie de 2026-09-30 23:00 (aucune donnée au-delà n'est
lue). Les modèles s'entraînent sur tout le passé purgé connu au réajustement, période finale antérieure comprise :
c'est un walk-forward, jamais une prévision vue avant d'être faite.

La consultation est enregistrée (`final_test_consultations`) et ne peut avoir lieu qu'une fois.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import volatility as v1
from . import volatility_hourly as vh
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .long_history import load_long
from .protocol import FROZEN_DEVELOPMENT_END, FROZEN_FINAL_TEST_START, FinalTestLocked, development_end
from .universe import MARKET, RESEARCH_UNIVERSE

STRATEGY = "VOLATILITY_CONFIRMATION"
DOC = "docs/VOLATILITY.md § 18"
START = pd.Timestamp(FROZEN_FINAL_TEST_START)
CUTOFF = pd.Timestamp("2026-09-30 23:00", tz="UTC")       # dernière bougie 1 h lue (heure d'ouverture)
#: Répétition : le même code sur les 15 derniers mois de DEVELOPMENT (aucune donnée finale lue, rien n'est compté),
#: pour qu'un plantage ou une durée imprévue ne gâche jamais la lecture unique.
REHEARSAL_START = pd.Timestamp("2024-04-01", tz="UTC")
REHEARSAL_CUTOFF = pd.Timestamp("2025-06-30 23:00", tz="UTC")
MEAN = "MEAN_M4_M5"


@dataclass(frozen=True)
class Comparison:
    key: str
    family: str           # "daily" | "hourly"
    horizon: int          # jours (daily) ou heures (hourly)
    model: str
    reference: str
    source: str


COMPARISONS = (
    Comparison("D1", "daily", 1, "M5_LGBM_POOLED", v1.BASELINE, "v1 § 12-13 (en service)"),
    Comparison("D3", "daily", 3, "M5_LGBM_POOLED", v1.BASELINE, "v1 § 12-13 (en service)"),
    Comparison("D7", "daily", 7, "M4_HAR_POOLED_BTC", v1.BASELINE, "v1 § 12-13 (en service)"),
    Comparison("V3", "daily", 3, MEAN, "M5_LGBM_POOLED", "v2 § 15 (candidat au branchement)"),
    Comparison("H24", "hourly", 24, "H1_HAR_PROFILE", vh.BASELINE, "v3 § 16 (candidat au branchement)"),
)
N_TRIALS = len(COMPARISONS)
#: Séquence fixe déclarée AVANT la lecture (procédure hiérarchique : chaque test à 95 %, arrêt au premier test non
#: confirmé ; risque global de fausse confirmation ≤ 5 %). Ordre : puissance estimée sur DEVELOPMENT (part de
#: fenêtres de 15 mois où l'IC à 95 % est sous 0 : H24 22/22, D7 19/22, D3 16/22, D1 15/22, V3 8/22).
SEQUENCE = ("H24", "D7", "D3", "D1", "V3")
LEVEL = 0.95
MIN_BLOCKS = v1.MIN_BLOCKS
MIN_PAIRS_SHARE = v1.MIN_PAIRS_SHARE
YEARS = (2025, 2026)
REHEARSAL_YEARS = (2024, 2025)
CONFIRMED, INCONCLUSIVE, CONTRADICTED = "CONFIRME", "NON_CONCLUANT", "CONTREDIT"
NO_DATA, NOT_TESTED = "DONNEES_INSUFFISANTES", "NON_TESTE"
MIN_COVERAGE = 0.99                                         # heures présentes dans la fenêtre, par paire


@dataclass
class Verdict:
    key: str
    model: str
    reference: str
    horizon: str
    evaluated: int
    days: int
    pairs: int
    qlike: float | None
    qlike_reference: float | None
    qlike_diff: float | None
    ci: list[float] | None
    by_year: dict[str, float]
    pairs_better_share: float | None
    log_diff: float | None
    criteria: dict[str, bool]
    verdict: str
    outcome: str = ""            # issue propre, avant la séquence (descriptif pour un test non atteint)


@dataclass
class Result:
    run_id: str
    level: float
    verdicts: list[Verdict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    consultations_total: int = 0
    rehearsal: bool = False


# --- Données -----------------------------------------------------------------------------------------------------

def load_until(settings: Settings, symbol: str, cutoff: pd.Timestamp = CUTOFF) -> pd.DataFrame:
    """Bougies 1 h du magasin long jusqu'à `cutoff` inclus ; rien au-delà n'est lu."""
    frame = load_long(settings, symbol)
    return frame.loc[frame["open_time"] <= cutoff, list(v1.READ_COLUMNS)].reset_index(drop=True)


# --- Prévisions (walk-forward sur la période finale) ---------------------------------------------------------------

def daily_forecasts(frames: dict[str, pd.DataFrame], *, seed: int, start: pd.Timestamp = START,
                    cutoff: pd.Timestamp = CUTOFF, progress: Callable[[str], None] | None = None) -> dict[int, pd.DataFrame]:
    """Par horizon (1, 3, 7 j) : table large (paire, origine, réalisé, M0, M4, M5, moyenne M4-M5) des origines
    ≥ START dont la cible finit au plus tard à CUTOFF ; réajustement le 1er de chaque mois (v1.fit_at, purge)."""
    say = progress or (lambda _text: None)
    data = pd.concat([f.assign(symbol=s) for s, f in frames.items() if len(f)], ignore_index=True)
    origins = data["origin"]
    eligible = v1.complete_rows(data) & (data["history_days"] >= v1.MIN_HISTORY_DAYS) & (origins >= start)
    keep = ["symbol", "origin", "realized", v1.BASELINE, "M4_HAR_POOLED_BTC", "M5_LGBM_POOLED"]
    out: dict[int, pd.DataFrame] = {}
    for horizon in (1, 3, 7):
        last_bar = origins + pd.Timedelta(days=horizon) - v1.STEP
        scored = (eligible & (data[f"rv2_{horizon}"] > 0) & (last_bar <= cutoff)).to_numpy()
        parts = []
        if scored.any():
            for refit in pd.date_range(start, origins[scored].max(), freq="MS"):
                month = scored & ((origins >= refit) & (origins < refit + pd.offsets.MonthBegin(1))).to_numpy()
                if not month.any():
                    continue
                say(f"{horizon} j — {refit:%Y-%m}")
                wide = v1.month_forecasts(v1.fit_at(data, refit, horizon, seed=seed), data[month], horizon)[keep]
                values = wide[keep[3:]].to_numpy(float)
                parts.append(wide[(np.isfinite(values) & (values > 0)).all(axis=1)])
        table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=keep)
        out[horizon] = table.assign(**{MEAN: (table["M4_HAR_POOLED_BTC"] + table["M5_LGBM_POOLED"]) / 2})
    return out


def hourly_forecasts(frames: dict[str, pd.DataFrame], *, seed: int, horizon: int = 24, start: pd.Timestamp = START,
                     cutoff: pd.Timestamp = CUTOFF, progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    """Table large (paire, origine, réalisé, R0, H1, H2) des origines horaires ≥ START dont la cible finit au plus
    tard à CUTOFF ; réajustement trimestriel (vh.fit_at : fenêtre glissante de 3 ans, purge)."""
    say = progress or (lambda _text: None)
    data = pd.concat([f.assign(symbol=s) for s, f in frames.items() if len(f)], ignore_index=True)
    origins = data["origin"]
    eligible = vh.complete_rows(data) & (data["history_days"] >= vh.MIN_HISTORY_DAYS) & (origins >= start)
    last_bar = origins + pd.Timedelta(hours=horizon) - v1.STEP
    scored = (eligible & (data[f"rv2_{horizon}"] > 0) & (last_bar <= cutoff)).to_numpy()
    parts = []
    if scored.any():
        for refit in pd.date_range(start, origins[scored].max(), freq=vh.REFIT_FREQ):
            quarter = scored & ((origins >= refit) & (origins < refit + pd.offsets.QuarterBegin(1, startingMonth=1))).to_numpy()
            if not quarter.any():
                continue
            say(f"{horizon} h — {refit:%Y-%m}")
            wide = vh.quarter_forecasts(vh.fit_at(data, refit, horizon, seed=seed), data[quarter], horizon)
            values = wide[list(vh.MODELS)].to_numpy(float)
            parts.append(wide[(np.isfinite(values) & (values > 0)).all(axis=1)])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["symbol", "origin", "realized", *vh.MODELS])


# --- Règle ---------------------------------------------------------------------------------------------------------

def judge(comparison: Comparison, wide: pd.DataFrame, *, years: tuple[int, ...] = YEARS) -> Verdict:
    """Issue propre d'une comparaison. CONFIRME si TOUT est vrai : borne haute de l'IC à 95 % (blocs calendaires du
    protocole d'origine, au moins 20 blocs) de la différence de QLIKE sous 0 ; différence moyenne négative en 2025 (juillet-décembre) ET en 2026 ; au moins
    70 % des paires mieux prévues ; perte secondaire (erreur de log RV) plus faible. CONTREDIT : borne basse > 0
    (pire que la référence). DONNEES_INSUFFISANTES : pas d'IC. Sinon NON_CONCLUANT."""
    criteria = dict.fromkeys(("ci_upper_below_zero", "both_years", "pairs", "secondary_loss"), False)
    unit = "j" if comparison.family == "daily" else "h"
    label = f"{comparison.horizon} {unit}"
    if wide.empty:
        return Verdict(comparison.key, comparison.model, comparison.reference, label, 0, 0, 0, None, None, None, None,
                       {}, None, None, criteria, NO_DATA, NO_DATA)
    realized = wide["realized"].to_numpy(float)
    losses = pd.DataFrame({"symbol": wide["symbol"].to_numpy(), "day": pd.DatetimeIndex(wide["origin"]).floor("D"),
                           "qlike": v1.qlike(realized, wide[comparison.model]),
                           "qlike_base": v1.qlike(realized, wide[comparison.reference]),
                           "log_diff": v1.log_error(realized, wide[comparison.model]) - v1.log_error(realized, wide[comparison.reference])})
    losses["diff"] = losses["qlike"] - losses["qlike_base"]
    daily = losses.groupby("day")[["qlike", "qlike_base", "diff", "log_diff"]].mean().sort_index()
    block = v1.block_days_for(comparison.horizon) if comparison.family == "daily" else vh.block_days_for(comparison.horizon)
    ci = calendar_mean_ci(daily["diff"].to_numpy(float), daily.index, block_days=block, min_blocks=MIN_BLOCKS, level=LEVEL)
    by_year = daily["diff"].groupby(daily.index.year).mean()
    by_pair = losses.groupby("symbol")["diff"].mean()
    mean = daily.mean()
    criteria = {"ci_upper_below_zero": ci is not None and ci[1] < 0,
                "both_years": all(year in by_year.index and by_year[year] < 0 for year in years),
                "pairs": float((by_pair < 0).mean()) >= MIN_PAIRS_SHARE,
                "secondary_loss": bool(mean["log_diff"] < 0)}
    if ci is None:
        outcome = NO_DATA
    elif all(criteria.values()):
        outcome = CONFIRMED
    elif ci[0] > 0:
        outcome = CONTRADICTED            # démontrablement PIRE que la référence
    else:
        outcome = INCONCLUSIVE
    return Verdict(comparison.key, comparison.model, comparison.reference, label, int(len(losses)), int(len(daily)),
                   int(len(by_pair)), round(float(mean["qlike"]), 6), round(float(mean["qlike_base"]), 6),
                   round(float(mean["diff"]), 6), [round(float(c), 6) for c in ci] if ci else None,
                   {str(y): round(float(v), 6) for y, v in by_year.items()}, round(float((by_pair < 0).mean()), 4),
                   round(float(mean["log_diff"]), 6), criteria, outcome, outcome)


def apply_sequence(verdicts: list[Verdict]) -> list[Verdict]:
    """Procédure hiérarchique : dans l'ordre SEQUENCE, chaque comparaison n'est testée que si toutes les précédentes
    sont confirmées ; après le premier échec, les suivantes sont NON_TESTE (leurs chiffres restent descriptifs)."""
    by_key = {v.key: v for v in verdicts}
    open_ = True
    for key in SEQUENCE:
        verdict = by_key[key]
        verdict.verdict = verdict.outcome if open_ else NOT_TESTED
        open_ = open_ and verdict.outcome == CONFIRMED
    return [by_key[key] for key in SEQUENCE]


def open_times(settings: Settings, symbol: str) -> pd.Series:
    """Heures d'ouverture des bougies 1 h du magasin long, en ne lisant QUE cette colonne (aucun prix chargé)."""
    import pyarrow.parquet as pq

    from ..data.store import CandleStore
    from .long_history import TIMEFRAME, long_settings
    path = CandleStore(long_settings(settings).data_dir).path(symbol, TIMEFRAME)
    if not path.exists():
        raise v1.MissingData(f"{symbol} absente du magasin long")
    return pd.to_datetime(pd.Series(pq.read_table(path, columns=["open_time"]).column("open_time").to_pandas()), utc=True)


def coverage_problems(settings: Settings, symbols: list[str], start: pd.Timestamp, cutoff: pd.Timestamp) -> list[str]:
    """Contrôle AVANT la consultation, sur les seules heures d'ouverture des bougies : au moins 99 % des heures de la
    fenêtre présentes et une bougie à `cutoff`, pour chaque paire. Aucun prix n'est lu."""
    expected = int((cutoff - start) / v1.STEP) + 1
    problems = []
    for symbol in dict.fromkeys([*symbols, MARKET]):
        try:
            times = open_times(settings, symbol)
        except v1.MissingData:
            problems.append(f"{symbol} : absente du magasin long")
            continue
        inside = times[(times >= start) & (times <= cutoff)]
        share = inside.nunique() / expected
        if share < MIN_COVERAGE or cutoff not in set(inside):
            problems.append(f"{symbol} : {share:.1%} des heures, dernière {inside.max() if len(inside) else 'aucune'}")
    return problems


def consult_once(settings: Settings, run_id: str) -> int:
    """Vérifie et enregistre la consultation dans UNE transaction (BEGIN IMMEDIATE) : deux lancements simultanés ne
    peuvent pas lire tous les deux. Renvoie le total des consultations de tout le programme."""
    registry = ExperimentRegistry(settings.experiments_db)
    with registry.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM final_test_consultations WHERE strategy=?", (STRATEGY,)).fetchone()[0]:
            db.execute("ROLLBACK")
            raise FinalTestLocked("la confirmation de la volatilité a déjà lu la période finale : une seule lecture est permise")
        db.execute("INSERT INTO final_test_consultations VALUES (?, ?, ?)", (datetime.now(UTC).isoformat(), run_id, STRATEGY))
        return int(db.execute("SELECT COUNT(*) FROM final_test_consultations").fetchone()[0])


# --- Exécution --------------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, allow_final_test: bool = False, rehearsal: bool = False,
        progress: Callable[[str], None] | None = None, symbols: list[str] | None = None, allow_dirty: bool = False) -> Result:
    """`rehearsal` : même code sur REHEARSAL_START → REHEARSAL_CUTOFF (DEVELOPMENT), rien d'enregistré au registre.
    Sinon : la lecture UNIQUE de la période finale, consultation enregistrée AVANT la lecture des données."""
    say = progress or (lambda _text: None)
    registry = ExperimentRegistry(settings.experiments_db)
    if rehearsal:
        start, cutoff, years = REHEARSAL_START, REHEARSAL_CUTOFF, REHEARSAL_YEARS
        if cutoff > pd.Timestamp(min(development_end(settings), FROZEN_DEVELOPMENT_END)):
            raise FinalTestLocked("la répétition doit rester dans DEVELOPMENT")
    else:
        start, cutoff, years = START, CUTOFF, YEARS
        if allow_dirty:
            raise v1.DirtyCode("la lecture unique exige un code commité (--allow-dirty : répétition seulement)")
        if not allow_final_test:
            raise FinalTestLocked("période finale réservée : ajouter --i-understand-final-test (une seule consultation, "
                                  "enregistrée ; docs/VOLATILITY.md § 18)")
        if registry.final_test_consulted(STRATEGY):
            raise FinalTestLocked("la confirmation de la volatilité a déjà lu la période finale : une seule lecture est permise")
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise v1.DirtyCode(f"code non commité ({state}) : exécution refusée (une seule lecture, elle doit être reproductible)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    say("audits de causalité (protocoles v1 et v3, sur DEVELOPMENT)")
    dev_end = pd.Timestamp(development_end(settings))
    for name, audit in (("v1", v1.leak_audit), ("v3", vh.leak_audit)):
        report = audit(settings, dev_end, settings.protocol.seed)
        if not report["passed"]:
            raise v1.LeakAuditFailed(f"audit des fuites {name} en échec : {report}")
    problems = coverage_problems(settings, symbols, start, cutoff)
    if problems:                          # avant la consultation : un magasin incomplet ne consomme pas la lecture
        raise v1.IncompleteData("données incomplètes, rien n'est lu : " + " ; ".join(problems))
    result = Result(new_run_id("VOLR" if rehearsal else "VOLC"), round(LEVEL, 6), rehearsal=rehearsal)
    if rehearsal:
        return _compute(settings, result, symbols=symbols, start=start, cutoff=cutoff, years=years, say=say)
    from ..live.lock import InstanceLock
    with InstanceLock(settings.root / "state" / "volatility_confirm.lock"):
        # Enregistrée AVANT toute lecture : un plantage ne donne pas droit à un second regard.
        result.consultations_total = consult_once(settings, result.run_id)
        _compute(settings, result, symbols=symbols, start=start, cutoff=cutoff, years=years, say=say)
    registry.record(run_id=result.run_id, created_at=now.isoformat(), kind=v1.KIND,
                    hypothesis="les prévisions de volatilité retenues sur DEVELOPMENT restent meilleures sur la période finale",
                    strategy=STRATEGY, strategy_version=1, variant=f"5 comparaisons en séquence figée ({DOC})",
                    params={"comparisons": [asdict(c) for c in COMPARISONS], "sequence": list(SEQUENCE), "level": result.level,
                            "start": str(start), "cutoff": str(cutoff), "min_pairs_share": MIN_PAIRS_SHARE, "years": list(years)},
                    period_label="FINAL_TEST", period_start=str(start), period_end=str(cutoff), universe=symbols,
                    data_hashes=result.data_hashes, git_commit=state, dependencies=dependency_versions(),
                    seed=settings.protocol.seed, cost_scenario="aucun (erreur de prévision)",
                    simulation_rules={"refit": "mensuel (journalier), trimestriel (horaire), passé purgé"},
                    metrics={"n_trials": N_TRIALS, "verdicts": {v.key: v.verdict for v in result.verdicts},
                             "rows": [asdict(v) for v in result.verdicts]},
                    status="COMPLETED", report_dir=str(settings.reports_dir / result.run_id))
    return result


def _compute(settings: Settings, result: Result, *, symbols: list[str], start: pd.Timestamp, cutoff: pd.Timestamp,
             years: tuple[int, ...], say: Callable[[str], None]) -> Result:
    market = load_until(settings, MARKET, cutoff)
    daily_frames, hourly_frames = {}, {}
    for symbol in symbols:
        say(f"variables {symbol}")
        series = market if symbol == MARKET else load_until(settings, symbol, cutoff)
        result.data_hashes[symbol] = fingerprint(series)
        daily_frames[symbol] = v1.daily_frame(series, market)
        hourly_frames[symbol] = vh.hourly_frame(series, market)
    daily = daily_forecasts(daily_frames, seed=settings.protocol.seed, start=start, cutoff=cutoff, progress=say)
    hourly = hourly_forecasts(hourly_frames, seed=settings.protocol.seed, start=start, cutoff=cutoff, progress=say)
    result.verdicts = apply_sequence([judge(c, daily[c.horizon] if c.family == "daily" else hourly, years=years)
                                      for c in COMPARISONS])
    result.coverage = {"origins": {f"{h} j": int(len(t)) for h, t in daily.items()} | {"24 h": int(len(hourly))},
                       "pairs": len(symbols), "start": str(start), "cutoff": str(cutoff)}
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    pd.concat([t.assign(horizon=f"{h} j") for h, t in daily.items()] + [hourly.assign(horizon="24 h")],
              ignore_index=True).to_parquet(report_dir / "forecasts.parquet", index=False)
    payload = asdict(result) | {"doc": DOC, "comparisons": [asdict(c) for c in COMPARISONS], "sequence": list(SEQUENCE),
                                "n_trials": N_TRIALS}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return result
