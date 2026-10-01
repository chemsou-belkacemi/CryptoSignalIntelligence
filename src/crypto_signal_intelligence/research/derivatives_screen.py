"""Criblage du positionnement sur le marché à terme (docs/DERIVATIVES.md §Criblage), déclaré avant exécution.

Question posée à chaque condition : après l'événement, le rendement Spot futur BRUT dépasse-t-il (1) la dérive
de la paire sur sa période évaluable et (2) le seuil des coûts aller-retour ? Quatre conditions fixées à
l'avance × trois horizons (1, 3, 7 jours) = 12 essais, comptés dans le programme. DEVELOPMENT seulement.

Règles :
- décision toutes les 4 h à la clôture d'une bougie 1 h ; entrée à l'ouverture suivante ; sortie à la clôture
  de t+H ; aucun stop, aucune cible : un criblage, pas une stratégie ;
- excès = rendement − moyenne des rendements de la même paire aux décisions où la condition est ÉVALUABLE
  (même période de données) ; excès transversal (diagnostic) = rendement − moyenne de toutes les paires
  évaluables au même instant ;
- IC : Student sur sommes par blocs de jours calendaires (au moins 2 × H et 10 jours), au moins 20 blocs avec
  événements, niveau corrigé de Bonferroni pour les 12 essais ;
- « passe » = rendement brut moyen > seuil de coûts ET borne basse de l'IC de l'excès > 0 ;
- audit des fuites sur les données réelles AVANT tout résultat (données tronquées, futur falsifié, une
  mutation par famille qui doit être détectée) : en échec, aucun résultat n'est produit.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..derivatives import features as fx
from ..derivatives.history import DerivativesStore
from ..features.loader import load_candles
from .experiments import ExperimentRegistry, dependency_versions, git_state, new_run_id
from .intervals import calendar_mean_ci
from .protocol import clip_to_development, development_end

KIND, STRATEGY = "SCREEN", "SCREEN_DERIVATIVES"
PROTOCOL_VERSION = 1
HORIZONS = fx.HORIZONS
N_TRIALS = len(fx.CONDITIONS) * len(HORIZONS)
LEVEL = 1 - 0.05 / N_TRIALS
MIN_BLOCKS = 20
AUDIT_PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
AUDIT_DECISIONS = 3


class LeakAuditFailed(RuntimeError):
    pass


@dataclass
class Row:
    condition: str
    horizon_h: int
    events: int
    pairs: int
    event_days: int
    mean_return_pct: float | None
    mean_excess_pct: float | None
    ci_excess_pct: list[float] | None
    mean_cross_excess_pct: float | None
    ci_cross_excess_pct: list[float] | None
    pairs_positive_share: float | None
    years_positive_share: float | None
    beats_costs: bool


@dataclass
class Result:
    run_id: str
    period_end: str
    cost_hurdle_pct: float
    level: float
    n_trials: int = N_TRIALS
    program_trials: int = 0
    leak_audit: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)


def block_days_for(horizon_h: int) -> int:
    return max(10, 2 * math.ceil(horizon_h / 24))


def _pct(value: float | None) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value) * 100, 4)


def _pct_ci(ci: list[float] | None) -> list[float] | None:
    return None if ci is None else [round(v * 100, 4) for v in ci]


def load_sources(settings: Settings, symbol: str, end: pd.Timestamp) -> tuple[pd.DataFrame, ...]:
    """Spot 1 h et séries du marché à terme d'une paire, coupés à la fin de DEVELOPMENT."""
    store = DerivativesStore(settings.data_dir)
    spot = clip_to_development(load_candles(settings, symbol, "1h"), settings)
    series = []
    for dataset in ("funding", "premium", "metrics"):
        frame = store.load(dataset, symbol)
        series.append(frame[frame["time"] <= end].reset_index(drop=True) if not frame.empty else frame)
    return (spot, *series)


def leak_audit(settings: Settings, end: pd.Timestamp, *, seed: int) -> dict:
    """Causalité sur données réelles (quelques décisions de 3 paires, dont des heures de règlement) et chaque
    mutation détectée par sa famille."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(fx.MUTATIONS, False)
    checked = []
    for symbol in AUDIT_PAIRS:
        if symbol not in settings.data.symbols:
            continue
        sources = load_sources(settings, symbol, end)
        if any(frame.empty for frame in sources):
            continue
        frame = fx.decision_frame(*sources, horizons=())
        ready = frame.dropna(subset=list(fx.FEATURES))
        ready = ready[ready["decision_time"].dt.hour.isin((0, 8, 16))]     # heures de règlement du financement
        if ready.empty:
            continue
        picks = sorted(rng.choice(ready["decision_time"].to_numpy(), size=min(AUDIT_DECISIONS, len(ready)),
                                  replace=False))
        moments = [pd.Timestamp(t).tz_convert("UTC") if pd.Timestamp(t).tzinfo else pd.Timestamp(t, tz="UTC")
                   for t in picks]
        lo, hi = moments[0] - pd.Timedelta(days=100), moments[-1] + pd.Timedelta(days=8)

        def window(source: pd.DataFrame, key: str, lo=lo, hi=hi) -> pd.DataFrame:
            return source[(source[key] >= lo) & (source[key] <= hi)].reset_index(drop=True)

        windowed = (window(sources[0], "open_time"), *(window(s, "time") for s in sources[1:]))
        violations += [v | {"symbol": symbol} for v in fx.causality_violations(*windowed, decisions=moments,
                                                                               seed=seed)]
        for name, (builder, family) in fx.MUTATIONS.items():
            found = fx.causality_violations(*windowed, decisions=moments, seed=seed, builders={name: builder})
            detected[name] |= any(f in family for v in found for f in v["features"])
        checked.append(symbol)
    mutation_detected = bool(checked) and all(detected.values())
    return {"violations": violations, "mutation_by_family": detected, "checked_pairs": checked,
            "decisions_per_pair": AUDIT_DECISIONS, "passed": bool(checked) and not violations and mutation_detected}


def _row(name: str, horizon: int, frame: pd.DataFrame, evaluable: np.ndarray, events: np.ndarray,
         hurdle_pct: float) -> Row:
    ret_column = f"ret_{horizon}"
    valid = evaluable & frame[ret_column].notna().to_numpy()
    base = frame[valid]
    hits = frame[valid & events]
    if hits.empty:
        return Row(name, horizon, 0, 0, 0, None, None, None, None, None, None, None, False)
    drift = base.groupby("symbol")[ret_column].mean()
    cross = base.groupby("decision_time")[ret_column].mean()
    ret = hits[ret_column].to_numpy(float)
    excess = ret - hits["symbol"].map(drift).to_numpy(float)
    cross_excess = ret - hits["decision_time"].map(cross).to_numpy(float)
    times = hits["decision_time"]
    days = block_days_for(horizon)
    ci = calendar_mean_ci(excess, times, block_days=days, min_blocks=MIN_BLOCKS, level=LEVEL)
    ci_cross = calendar_mean_ci(cross_excess, times, block_days=days, min_blocks=MIN_BLOCKS, level=LEVEL)
    by_pair = pd.Series(excess, index=hits["symbol"].to_numpy()).groupby(level=0).mean()
    by_year = pd.Series(excess, index=times.dt.year.to_numpy()).groupby(level=0).mean()
    mean_ret = float(np.mean(ret)) * 100
    return Row(name, horizon, int(len(hits)), int(by_pair.size), int(times.dt.floor("D").nunique()),
               round(mean_ret, 4), _pct(float(np.mean(excess))), _pct_ci(ci), _pct(float(np.mean(cross_excess))),
               _pct_ci(ci_cross), round(float((by_pair > 0).mean()), 3), round(float((by_year > 0).mean()), 3),
               beats_costs=bool(mean_ret > hurdle_pct and ci is not None and ci[0] > 0))


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None) -> Result:
    say = progress or (lambda _text: None)
    symbols = symbols or list(settings.data.symbols)
    end = pd.Timestamp(development_end(settings))
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    result = Result(new_run_id("SCREEN"), end.isoformat(), round(hurdle_pct, 4), round(LEVEL, 6))
    say("audit des fuites")
    result.leak_audit = leak_audit(settings, end, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    frames = []
    for symbol in symbols:
        say(f"variables {symbol}")
        sources = load_sources(settings, symbol, end)
        frame = fx.decision_frame(*sources).assign(symbol=symbol)
        frames.append(frame)
        result.coverage[symbol] = {
            name: {"first": (str(frame.loc[fx.condition_masks(frame, name)[0], "decision_time"].min())[:10]
                             if fx.condition_masks(frame, name)[0].any() else None),
                   "evaluable": int(fx.condition_masks(frame, name)[0].sum())}
            for name in fx.CONDITIONS}
    data = pd.concat(frames, ignore_index=True)
    for name in fx.CONDITIONS:
        evaluable, events = fx.condition_masks(data, name)
        for horizon in HORIZONS:
            result.rows.append(_row(name, horizon, data, evaluable, events, hurdle_pct))
    registry = ExperimentRegistry(settings.experiments_db)
    result.program_trials = registry.program_trials() + result.n_trials
    _record(settings, result, now=now, symbols=symbols)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str]) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    conditions = {name: text for name, (text, _, _) in fx.CONDITIONS.items()}
    payload = asdict(result) | {"conditions": conditions, "protocol_version": PROTOCOL_VERSION,
                                "doc": "docs/DERIVATIVES.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    passing = [r for r in result.rows if r.beats_costs]
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="criblage du positionnement sur le marché à terme : avantage après dérive et au-delà des coûts ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="conditions figées (docs/DERIVATIVES.md)",
        params={"horizons_h": list(HORIZONS), "conditions": conditions, "level": result.level,
                "min_blocks": MIN_BLOCKS}, period_label="DEVELOPMENT",
        period_start=settings.data.history_start.isoformat(), period_end=result.period_end, universe=symbols,
        data_hashes={}, git_commit=git_state(settings.root), dependencies=dependency_versions(),
        seed=settings.protocol.seed, cost_scenario="central (seuil aller-retour)",
        simulation_rules={"entry": "ouverture t+1", "exit": "clôture t+H", "stops": "aucun",
                          "decisions": "toutes les 4 h"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials,
                 "cost_hurdle_pct": result.cost_hurdle_pct, "passing": len(passing),
                 "verdict": (f"{len(passing)} CONDITION(S) AU-DELÀ DES COÛTS" if passing
                             else "AUCUNE_CONDITION_AU_DELA_DES_COUTS"),
                 "rows": [asdict(r) for r in result.rows]}, status="COMPLETED", report_dir=str(report_dir))
