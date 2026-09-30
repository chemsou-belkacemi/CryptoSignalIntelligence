"""Walk-forward purgé sur la période DEVELOPMENT (docs/PROTOCOL.md).

Fenêtres ancrées : l'entraînement va du début de la période au début de la
fenêtre de test ; les tests successifs couvrent la suite sans chevauchement.
Seuls les résultats des fenêtres de test sont agrégés.

Recalibrage : grille grossière déclarée par la stratégie (budget d'essais = taille
de la grille). Chaque combinaison est simulée UNE fois en continu sur la période,
en coûts centraux ; les trades d'entraînement d'une fenêtre sont ceux dont le
setup ET la sortie précèdent le début du test (purge : aucun résultat
d'entraînement ne chevauche le test). La continuité de la simulation peut
décaler un setup en bordure de fenêtre (règle « un setup actif par paire »),
effet limité à l'entraînement.
Sélection : E[R] nette moyenne de la combinaison et de ses voisines de grille
(zone stable plutôt que pic isolé), parmi celles qui ont assez de trades. Sans
abstention : la meilleure combinaison est jouée même si son score est négatif.

Test : simulation exacte de chaque fenêtre avec les paramètres retenus. Les
décisions sont limitées à la fenêtre ; un trade ouvert peut se terminer après,
jamais au-delà de la période (le test final reste réservé).
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..backtest.metrics import buy_and_hold
from ..config import Settings
from ..data.quality import assess
from ..domain.enums import ValidationVerdict
from ..strategies import registry
from ..strategies.base import Strategy
from ..validation.causality import check as causality_check
from . import admission
from .backtest_run import (
    CONSUMER_PROFILE,
    FrameCache,
    UniverseRun,
    concat_trades,
    run_context,
    simulate_universe,
)
from .experiments import ExperimentRegistry, new_run_id
from .protocol import Period
from .protocol import period as resolve_period

BASE = "base"
FIXED_V1 = "v1_fixe"


@dataclass(frozen=True)
class Window:
    index: int
    train_start: datetime
    test_start: datetime
    test_end: datetime


def make_windows(start: datetime, end: datetime, *, train_months: int, test_months: int) -> list[Window]:
    windows: list[Window] = []
    test_start = pd.Timestamp(start) + pd.DateOffset(months=train_months)
    last = pd.Timestamp(end)
    while test_start < last:
        next_start = test_start + pd.DateOffset(months=test_months)
        test_end = min(next_start - pd.Timedelta(seconds=1), last)
        windows.append(Window(len(windows), start, test_start.to_pydatetime(), test_end.to_pydatetime()))
        test_start = next_start
    return windows


def train_score(trades: pd.DataFrame, window: Window) -> tuple[int, float | None]:
    """(trades clos, E[R]) des trades dont le setup ET la sortie précèdent le test."""
    closed = admission.closed_trades(trades)
    if closed.empty:
        return 0, None
    setup = pd.to_datetime(closed["setup_time"], utc=True)
    exit_time = pd.to_datetime(closed["exit_time"], utc=True)
    selected = closed.loc[(setup >= pd.Timestamp(window.train_start)) & (exit_time < pd.Timestamp(window.test_start)),
                          "r_multiple"]
    return int(len(selected)), (float(selected.mean()) if len(selected) else None)


def select_combo(scores: dict[tuple, tuple[int, float | None]], grid: dict[str, tuple], *, min_trades: int,
                 default: tuple) -> tuple[tuple, str, float | None]:
    """Combinaison retenue, mode de sélection, score de plateau."""
    eligible = {combo: e for combo, (n, e) in scores.items() if n >= min_trades and e is not None}
    if not eligible:
        return default, "DEFAUT_AUCUNE_COMBINAISON_ELIGIBLE", None
    axes = list(grid.values())

    def plateau(combo: tuple) -> float:
        values = [eligible[combo]]
        for i, axis in enumerate(axes):
            position = axis.index(combo[i])
            for step in (-1, 1):
                if 0 <= position + step < len(axis):
                    neighbour = combo[:i] + (axis[position + step],) + combo[i + 1:]
                    if neighbour in eligible:
                        values.append(eligible[neighbour])
        return sum(values) / len(values)

    # Tri stable : à égalité, l'ordre de la grille départage (déterministe).
    best = sorted(eligible, key=lambda combo: (plateau(combo), eligible[combo]), reverse=True)[0]
    return best, "PLATEAU", round(plateau(best), 4)


def check_integrity(settings: Settings, strategy: Strategy, inputs: dict[str, dict[str, pd.DataFrame]],
                    period: Period, windows: list[Window]) -> tuple[bool, str]:
    """Critère 1 : anomalies structurelles des données et test de causalité sur données réelles."""
    problems, gaps = [], 0
    timeframes = {"setup": settings.data.setup_timeframe, "context": settings.data.context_timeframe}
    cuts = [pd.Timestamp(w.test_start) for w in windows][:: max(1, len(windows) // 3)][:3]
    for symbol, data in inputs.items():
        for name, timeframe in timeframes.items():
            frame = data[name]
            report = assess(frame[(frame["open_time"] >= pd.Timestamp(period.start))
                                  & (frame["open_time"] <= pd.Timestamp(period.end))], symbol, timeframe)
            structural = (report.duplicates + report.non_monotonic + report.invalid_ohlc + report.non_finite
                          + report.misaligned + report.open_candles)
            if structural:
                problems.append(f"{symbol} {timeframe} : {structural} anomalie(s)")
            gaps += len(report.gaps)
        leaks = [r.cut[:10] for r in causality_check(settings, strategy, data, symbol, cuts) if not r.ok]
        if leaks:
            problems.append(f"{symbol} : causalité en échec aux coupures {', '.join(leaks)}")
    detail = "; ".join(problems) if problems else f"aucune anomalie structurelle, causalité OK ({len(cuts)} coupures)"
    return not problems, f"{detail} ; {gaps} trou(s) d'historique, décisions bloquées ensuite (DATA_GAP)"


@dataclass
class WalkForwardResult:
    run_id: str
    strategy: str
    strategy_version: int
    period: Period
    windows: list[dict]
    grid: dict[str, tuple]
    verdict: ValidationVerdict
    criteria: list[admission.Criterion]
    summaries: dict[str, dict]
    baselines: dict
    report_dir: Path
    oos: dict[str, UniverseRun] = field(repr=False, default_factory=dict)
    calibration: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def _variants(strategy_cls: type[Strategy], scenarios: list[str]) -> list[tuple[str, str, dict, str | None]]:
    """(variante, scénario, surcharges, politique de sortie imposée) joués sur chaque fenêtre de test."""
    variants: list[tuple[str, str, dict, str | None]] = [(BASE, scenario, {}, None) for scenario in scenarios]
    variants += [(name, "central", overrides, None)
                 for name, overrides in (strategy_cls.ablations | strategy_cls.extensions).items()]
    return [*variants, (CONSUMER_PROFILE[0], "central", {}, CONSUMER_PROFILE[1]), (FIXED_V1, "central", {}, None)]


def run(settings: Settings, strategy_id: str, *, now: datetime, progress=None) -> WalkForwardResult:
    say = progress or (lambda _text: None)
    period = resolve_period(settings, "development", now=now)
    config = settings.walk_forward
    windows = make_windows(period.start, period.end, train_months=config.train_min_months,
                           test_months=config.test_months)
    if not windows:
        raise ValueError("Période DEVELOPMENT trop courte pour une fenêtre d'entraînement et de test.")
    base = registry.build(strategy_id, settings.strategies)
    strategy_cls = type(base)
    grid = strategy_cls.calibration_grid
    keys = list(grid)
    combos = list(itertools.product(*grid.values()))
    default = tuple(getattr(base.params, key) for key in keys)
    run_id = new_run_id("WF")
    inputs, common = run_context(settings)
    frames = FrameCache(settings, inputs)

    # 1. Calibrage : une simulation continue par combinaison, scores par fenêtre d'entraînement.
    scores: dict[int, dict[tuple, tuple[int, float | None]]] = {w.index: {} for w in windows}
    calibration_rows = []
    for number, combo in enumerate(combos, 1):
        say(f"calibrage {number}/{len(combos)}")
        strategy = registry.build(strategy_id, settings.strategies, **dict(zip(keys, combo, strict=True)))
        continuous = simulate_universe(settings, strategy, frames, "central", start=period.start, end=period.end)
        for w in windows:
            scores[w.index][combo] = train_score(continuous.trades, w)
            calibration_rows.append({"window": w.index, "test_start": w.test_start.isoformat(),
                                     **dict(zip(keys, combo, strict=True)), "train_trades": scores[w.index][combo][0],
                                     "train_expectancy_r": scores[w.index][combo][1]})

    # 2. Test : chaque fenêtre avec les paramètres retenus sur son seul passé.
    variants = _variants(strategy_cls, list(settings.costs))
    oos = {f"{name}/{scenario}": UniverseRun(trades=concat_trades([])) for name, scenario, _, _ in variants}
    window_rows: list[dict[str, Any]] = []
    for w in windows:
        combo, how, plateau = select_combo(scores[w.index], grid, min_trades=config.min_train_trades, default=default)
        chosen = dict(zip(keys, combo, strict=True))
        train_trades, train_expectancy = scores[w.index].get(combo, (0, None))
        row = {"index": w.index, "test_start": w.test_start.isoformat(), "test_end": w.test_end.isoformat(),
               "chosen": chosen, "selection": how, "plateau_score": plateau, "train_trades": train_trades,
               "train_expectancy_r": train_expectancy}
        for name, scenario, overrides, policy in variants:
            say(f"test {w.test_start:%Y-%m} {name}/{scenario}")
            params = overrides if name == FIXED_V1 else chosen | overrides
            strategy = registry.build(strategy_id, settings.strategies, **params)
            run = simulate_universe(settings, strategy, frames, scenario, start=w.test_start, end=period.end,
                                    decisions_end=w.test_end, exit_policy_id=policy)
            if not run.trades.empty:
                run.trades["window"] = w.index
            oos[f"{name}/{scenario}"].extend(run)
            if (name, scenario) == (BASE, "central"):
                window_summary = run.summary(settings)
                row |= {"oos_trades": window_summary["trades_closed"],
                        "oos_expectancy_r": window_summary.get("expectancy_r")}
        window_rows.append(row)

    # 3. Agrégat hors échantillon, intégrité, verdict.
    say("agrégat et critères d'admission")
    summaries = {label: universe.summary(settings) for label, universe in oos.items()}
    central_trades = oos[f"{BASE}/central"].trades
    closed = admission.closed_trades(central_trades)
    windows_with_trades = int(closed["window"].nunique()) if not closed.empty else 0
    say("contrôle d'intégrité et de causalité")
    integrity = check_integrity(settings, base, inputs, period, windows)
    verdict, criteria = admission.evaluate(
        central=summaries[f"{BASE}/central"], adverse=summaries.get(f"{BASE}/adverse", {}),
        central_trades=central_trades, windows_with_trades=windows_with_trades,
        ablations={name: summaries[f"{name}/central"] for name in strategy_cls.ablations},
        integrity=integrity, rules=settings.admission)

    oos_start = windows[0].test_start
    baselines = {"cash": {"return_pct": 0.0, "exposure_pct": 0.0}}
    for symbol in settings.data.symbols:
        baselines[f"buy_and_hold_{symbol}"] = buy_and_hold(inputs[symbol]["setup"], oos_start, period.end,
                                                           settings.costs["central"].fee_bps)

    result = WalkForwardResult(
        run_id=run_id, strategy=strategy_id, strategy_version=base.version, period=period, windows=window_rows,
        grid=grid, verdict=verdict, criteria=criteria, summaries=summaries, baselines=baselines,
        report_dir=settings.reports_dir / run_id, oos=oos, calibration=pd.DataFrame(calibration_rows))
    experiments = ExperimentRegistry(settings.experiments_db)
    program_trials = experiments.program_trials(period.label) + len(combos)
    payload = _payload(settings, result, base, integrity, len(combos)) | {
        "program_trials": program_trials,
        "program_trials_note": "essais cumulés du programme sur DEVELOPMENT, celui-ci compris ; à titre indicatif, "
                               f"un seuil de Bonferroni serait 0,05 / {program_trials}"}
    experiments.record(
        run_id=run_id, created_at=now.isoformat(), kind="WALK_FORWARD", hypothesis=base.hypothesis,
        strategy=strategy_id, strategy_version=base.version, variant="recalibré (grille)",
        params={"grid": grid, "defaults": base.params.model_dump(mode="json"),
                "selections": [{"test_start": r["test_start"], **r["chosen"]} for r in window_rows]},
        period_label=period.label, period_start=period.start.isoformat(), period_end=period.end.isoformat(),
        cost_scenario="sélection : central ; test : " + "/".join(settings.costs),
        simulation_rules={"max_hold_bars": settings.simulation.max_hold_bars,
                          "walk_forward": config.model_dump(), "admission": settings.admission.model_dump(),
                          "fill_rules": "voir backtest/simulator.py (docstring)"},
        metrics={"verdict": verdict.value, "n_trials": len(combos), "program_trials": program_trials,
                 "criteria": payload["criteria"],
                 "oos": summaries}, status="COMPLETED", report_dir=str(result.report_dir), **common)
    _write_artifacts(result, payload)
    return result


def _payload(settings: Settings, result: WalkForwardResult, base: Strategy, integrity: tuple[bool, str],
             n_trials: int) -> dict:
    return {
        "run_id": result.run_id, "strategy": result.strategy, "strategy_version": result.strategy_version,
        "hypothesis": base.hypothesis, "symbols": settings.data.symbols,
        "period": {"label": result.period.label, "start": result.period.start.isoformat(),
                   "end": result.period.end.isoformat()},
        "config": settings.walk_forward.model_dump(), "grid": {k: list(v) for k, v in result.grid.items()},
        "defaults": {k: getattr(base.params, k) for k in result.grid}, "n_trials": n_trials,
        "windows": result.windows, "verdict": result.verdict.value,
        "criteria": [c.to_dict() for c in result.criteria], "integrity": {"ok": integrity[0], "detail": integrity[1]},
        "oos": result.summaries, "baselines": result.baselines,
    }


def _write_artifacts(result: WalkForwardResult, payload: dict) -> None:
    result.report_dir.mkdir(parents=True, exist_ok=True)
    result.calibration.to_csv(result.report_dir / "calibration.csv", index=False)
    for label, universe in result.oos.items():
        universe.trades.to_csv(result.report_dir / f"trades_oos_{label.replace('/', '_')}.csv", index=False)
    (result.report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                                    encoding="utf-8")
    from ..reporting.walk_forward_report import render_markdown
    (result.report_dir / "report.md").write_text(render_markdown(payload), encoding="utf-8")
