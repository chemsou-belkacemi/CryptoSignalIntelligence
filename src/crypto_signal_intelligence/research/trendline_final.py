"""Cassures de ligne de tendance en 1 h : confirmation sur la période réservée (docs/LIGNES_DE_TENDANCE.md, section
« Confirmation sur la période réservée », déclaré le 2026-10-05 avant code, téléchargement et lecture ; décision du
propriétaire). Même déclencheur, mêmes transactions, mêmes placebos et même décision que sur DEVELOPMENT ; 263 paires
du top 40 à date connues au début de la période ; contrôle de complétude, puis consultation enregistrée AVANT la
lecture ; répétition sur la fin de DEVELOPMENT sans rien enregistrer. 2 essais sur la période finale."""
from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from ..forward import f15
from ..forward.costs import ADVERSE, CENTRAL, SCENARIOS
from . import figures_history as fh
from . import trendline_confirmation as tc
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import FROZEN_FINAL_TEST_START, FinalTestLocked, development_end
from .universe import RESEARCH_UNIVERSE

KIND = "TRENDLINE_FINAL"
STRATEGY = "TRENDLINE_1H"
N_TRIALS = 2
START = pd.Timestamp(FROZEN_FINAL_TEST_START)
CUTOFF = pd.Timestamp("2026-09-30 23:59:59", tz="UTC")
REHEARSAL_START = pd.Timestamp("2024-04-01", tz="UTC")
COVERAGE_LEAD = pd.Timedelta(days=30)                  # placebos de F15 : 30 jours avant l'exécution
HORIZON = (f15.ORDER_BARS + f15.HOLD_BARS) * f15.TIMEFRAMES[tc.TIMEFRAME]
HOLD = f15.HOLD_BARS * f15.TIMEFRAMES[tc.TIMEFRAME]


class AlreadyConsulted(PermissionError):
    pass


def universe(settings: Settings) -> list[str]:
    """Toutes les paires passées par le top 40 à date avant le début de la période (recensement de UNIVERSE_PIT.md) :
    les mois à partir du début de la période sont ignorés (une appartenance recalculée plus tard ne sélectionne pas
    sur l'avenir)."""
    from .pit_universe import load_membership
    members = load_membership(settings)
    members = members[pd.to_datetime(members["month"], utc=True) < START]
    return sorted(set(members["symbol"]))


def window_setups(h1: pd.DataFrame, symbol: str, *, start: pd.Timestamp, cutoff: pd.Timestamp) -> list[fh.Setup]:
    """Déclencheurs `TRENDLINE` 1 h dont la détection tombe dans [start ; …] après 90 jours d'historique, horizon
    entier avant la coupure ; la détection lit l'historique antérieur (pivots), jamais après la coupure."""
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= cutoff].reset_index(drop=True)
    if h1.empty:
        return []
    first = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).min())
    frame = f15.aggregate(h1, tc.TIMEFRAME)
    return [s for s in fh.figure_setups(frame, tc.TIMEFRAME, symbol) if s.method == tc.METHOD
            and s.at >= max(start, first + fh.WARMUP) and s.at + HORIZON <= cutoff]


def presence(hours: pd.Series, minutes: pd.Series, *, start: pd.Timestamp, cutoff: pd.Timestamp) -> dict | None:
    """Complétude à partir des SEULES dates d'ouverture (aucun prix) : part des heures 1 h de [start − 30 j ; coupure]
    qui ont au moins une minute, fin des minutes contre fin des heures. None : pas d'heure dans la fenêtre."""
    hours = pd.to_datetime(hours, utc=True)
    hours = hours[(hours >= start - COVERAGE_LEAD) & (hours <= cutoff)]
    if hours.empty:
        return None
    minutes = pd.to_datetime(minutes, utc=True)
    minutes = minutes[(minutes >= start - COVERAGE_LEAD) & (minutes <= cutoff)]
    have = set(minutes.dt.floor("h").dt.as_unit("ns").astype("int64").tolist())
    covered = float(np.mean([h in have for h in hours.dt.as_unit("ns").astype("int64")]))
    last_hour = pd.Timestamp(hours.max())
    last_minute = pd.Timestamp(minutes.max()) if len(minutes) else None
    late_end = last_minute is None or last_minute < last_hour + pd.Timedelta(minutes=59) - tc.END_TOLERANCE
    return {"hours": int(len(hours)), "covered": round(covered, 5), "last_hour": str(last_hour),
            "last_minute": str(last_minute), "ok": bool(covered >= tc.MIN_COVERAGE and not late_end)}


def check_complete(settings: Settings, symbols: list[str], *, start: pd.Timestamp, cutoff: pd.Timestamp) -> dict:
    """Contrôle avant la consultation : ne lit que la colonne des dates d'ouverture des magasins 1 h et 1 minute."""
    import pyarrow.parquet as pq

    from .long_history import long_settings
    from .minute_history import minute_settings

    def times(path) -> pd.Series:
        return pq.read_table(path, columns=["open_time"]).column(0).to_pandas() if path.exists() else pd.Series([], dtype="datetime64[ns, UTC]")

    out = {}
    for symbol in symbols:
        h = long_settings(settings).data_dir / "candles" / "binance" / "spot" / symbol / "1h.parquet"
        m = minute_settings(settings).data_dir / "candles" / "binance" / "spot" / symbol / "1m.parquet"
        if not h.exists():
            continue
        check = presence(times(h), times(m), start=start, cutoff=cutoff)
        if check is not None:
            out[symbol] = check
    return out


def pair_rows(h1: pd.DataFrame, m: fh.Minutes, symbol: str, *, start: pd.Timestamp, cutoff: pd.Timestamp,
              latency: pd.Timedelta) -> list[dict]:
    setups = window_setups(h1, symbol, start=start, cutoff=cutoff)
    if not setups or len(m.ns) == 0:
        return []
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= cutoff]
    first = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).min())
    lo_ns = max(start, first + fh.WARMUP).as_unit("ns").value
    hi_ns = min(cutoff.as_unit("ns").value, int(m.ns[-1])) - HOLD.value
    last_hour = pd.Timestamp(pd.to_datetime(h1["open_time"], utc=True).max())
    rows = []
    for setup in setups:
        row = tc.with_uniform_placebos(fh.play(setup, m, symbol, latency), m, lo_ns=lo_ns, hi_ns=hi_ns)
        row["research40"] = symbol in RESEARCH_UNIVERSE
        row["delisted_pair"] = bool(last_hour < cutoff - pd.Timedelta(days=2))
        row["quarter"] = f"{setup.at.year}T{(setup.at.month - 1) // 3 + 1}"
        rows.append(row)
    return rows


def _one(args: tuple) -> tuple[str, list[dict], dict]:
    from .derivatives_screen import fingerprint
    from .long_history import load_long
    from .minute_history import load_minutes
    settings, symbol, start, cutoff = args
    try:
        h1 = load_long(settings, symbol)
    except MissingData:
        return symbol, [], {}
    h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= cutoff].reset_index(drop=True)
    if not window_setups(h1, symbol, start=start, cutoff=cutoff):
        return symbol, [], {}
    bars = load_minutes(settings, symbol)
    bars = bars.loc[pd.to_datetime(bars["open_time"], utc=True) <= cutoff, ["open_time", "open", "high", "low", "close"]]
    m = fh.Minutes.from_frame(bars.reset_index(drop=True))
    del bars
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    rows = pair_rows(h1, m, symbol, start=start, cutoff=cutoff, latency=latency)
    return symbol, rows, {f"1h/{symbol}": fingerprint(h1[["open_time", "open", "high", "low", "close"]]),
                          f"1m/{symbol}": fh.minutes_hash(m)}


def evaluate(trades: pd.DataFrame, *, samples: int = fh.SAMPLES) -> dict:
    scenarios = {s: tc.measure(trades, s, samples=samples) for s in SCENARIOS}
    descr: dict = {"triggers": int(len(trades)), "status": trades["status"].value_counts().to_dict(),
                   "research40": tc.measure(trades[trades["research40"]], CENTRAL, level=0.95, samples=2000),
                   "others": tc.measure(trades[~trades["research40"]], CENTRAL, level=0.95, samples=2000),
                   "delisted_pairs": tc.measure(trades[trades["delisted_pair"]], CENTRAL, level=0.95, samples=2000),
                   "top40_months": "non calculable : l'appartenance au top 40 s'arrête au 2025-06-01"}
    done = trades[trades["status"] == fh.EXECUTED]
    if not done.empty:
        descr["quarters"] = {q: {"n": int(len(g)), "r_mean": round(float(g[f"r_{CENTRAL}"].mean()), 4),
                                 "uexcess_adj_mean": round(float(g[f"uexcess_adj_{CENTRAL}"].astype(float).mean()), 4)}
                             for q, g in done.groupby("quarter")}
        for column in (f"r_{CENTRAL}", f"uexcess_adj_{CENTRAL}"):
            values = done[column].astype(float)
            for name, groups in (("pair", done["symbol"]), ("quarter", done["quarter"])):
                sums = values.groupby(groups).sum()
                total = float(sums.sum())
                top = str(sums.idxmax())
                descr[f"top_{name}_{column}"] = {name: top, "share": round(float(sums.max()) / total, 4) if total > 0 else None,
                                                 "mean_without": round(float(values[groups != top].mean()), 4)}
    return {"scenarios": scenarios, "decision": tc.decide(scenarios[CENTRAL], scenarios[ADVERSE]), "descriptif": descr}


def run(settings: Settings, *, now: datetime, allow_final_test: bool = False, rehearsal: bool = False,
        progress: Callable[[str], None] | None = None, allow_dirty: bool = False, workers: int = 4,
        symbols: list[str] | None = None) -> dict:
    """`rehearsal` : même code sur REHEARSAL_START → fin de DEVELOPMENT, rien d'enregistré ni consulté. Sinon : la lecture
    UNIQUE de la période réservée, consultation enregistrée après le contrôle de complétude et AVANT tout calcul."""
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not (allow_dirty and rehearsal):
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    registry = ExperimentRegistry(settings.experiments_db)
    if rehearsal:
        start, cutoff = REHEARSAL_START, pd.Timestamp(development_end(settings)).tz_convert("UTC")
    else:
        if not allow_final_test:
            raise FinalTestLocked("période réservée : ajouter --i-understand-final-test (une seule consultation, enregistrée)")
        if registry.final_test_consulted(STRATEGY):
            raise AlreadyConsulted(f"{STRATEGY} a déjà consulté la période réservée : aucune seconde lecture")
        start, cutoff = START, CUTOFF
    pairs = symbols or universe(settings)
    say("complétude")
    checks = check_complete(settings, pairs, start=start, cutoff=cutoff)
    bad = sorted(k for k, v in checks.items() if not v["ok"])
    if bad:                                                 # avant la consultation : un magasin incomplet ne la consomme pas
        raise tc.IncompleteMinutes(f"bougies 1 minute incomplètes pour {len(bad)} paires : {bad[:10]} ; rien n'est lu")
    run_id = new_run_id("TRNR" if rehearsal else "TRNF")
    consultations = None if rehearsal else registry.consult_final_test(run_id, STRATEGY)
    base = {"run_id": run_id, "created_at": now.isoformat(), "kind": KIND,
            "hypothesis": "les cassures de ligne de tendance en 1 h confirmées sur DEVELOPMENT battent-elles le hasard et "
                          "gagnent-elles, frais compris, sur la période réservée ?",
            "strategy": STRATEGY, "strategy_version": 1, "variant": "définitions figées (docs/LIGNES_DE_TENDANCE.md)",
            "params": {"start": str(start), "cutoff": str(cutoff), "placebos": tc.PLACEBOS, "block_days": fh.BLOCK_DAYS,
                       "samples": fh.SAMPLES, "seed": fh.SEED},
            "period_label": "FINAL_TEST", "period_start": str(start), "period_end": str(cutoff), "git_commit": state,
            "dependencies": dependency_versions(), "seed": fh.SEED,
            "cost_scenario": "central et défavorable (forward/costs.py)",
            "simulation_rules": {"execution": "figures_history.play",
                                 "placebos": "20 minutes uniformes sur la période utilisable de la paire"}}
    try:
        return _compute(settings, registry, base, pairs=pairs, start=start, cutoff=cutoff, rehearsal=rehearsal,
                        consultations=consultations, checks=checks, workers=workers, say=say)
    except BaseException as exc:                            # consultation consommée sans résultat : trace au registre
        if not rehearsal:
            registry.record(**base, universe=[], data_hashes={}, status="FAILED",
                            metrics={"n_trials": N_TRIALS, "consultations_total": consultations,
                                     "error": f"{type(exc).__name__}: {exc}"})
        raise


def _compute(settings: Settings, registry: ExperimentRegistry, base: dict, *, pairs: list[str], start: pd.Timestamp,
             cutoff: pd.Timestamp, rehearsal: bool, consultations: int | None, checks: dict, workers: int,
             say: Callable[[str], None]) -> dict:
    run_id = base["run_id"]
    rows: list[dict] = []
    hashes: dict[str, str] = {}
    jobs = [(settings, symbol, start, cutoff) for symbol in pairs]

    def collect(results) -> None:
        for symbol, found, h in results:
            say(symbol)
            rows.extend(found)
            hashes.update(h)

    if workers <= 1:
        collect(map(_one, jobs))
    else:
        with ProcessPoolExecutor(workers) as pool:
            collect(pool.map(_one, jobs))
    if not rows:
        raise tc.IncompleteMinutes("aucune transaction")
    trades = pd.DataFrame(rows).sort_values(["at", "key"]).reset_index(drop=True)
    result = evaluate(trades)
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "rehearsal": rehearsal, "window": [str(start), str(cutoff)], "n_trials": 0 if rehearsal else N_TRIALS,
               "final_test_trials": None if rehearsal else registry.program_trials("FINAL_TEST") + N_TRIALS,
               "consultations_total": consultations, "level": round(tc.LEVEL, 6), "result": result, "pairs": len(pairs),
               "coverage": checks, "doc": "docs/LIGNES_DE_TENDANCE.md"}
    # Rapport écrit AVANT l'inscription au registre : une erreur du registre ne perd pas un résultat déjà calculé.
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    trades.to_parquet(report_dir / "trades.parquet", index=False)
    if not rehearsal:
        registry.record(**base, universe=sorted(set(trades["symbol"])), data_hashes=hashes, status="COMPLETED",
                        metrics={"n_trials": N_TRIALS, "consultations_total": consultations, "decision": result["decision"]},
                        report_dir=str(report_dir))
    return payload
