"""Exécution d'un backtest de référence : scénarios de coûts, ablations, références.

Pour une stratégie et une période : la version de base sous chaque scénario de
coûts, puis chaque ablation (filtre retiré) et extension (filtre ajouté) sous le
scénario central, et la référence buy-and-hold. Chaque exécution est enregistrée
dans le registre. Les aides `FrameCache` et `simulate_universe` servent aussi au
walk-forward.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..backtest.metrics import buy_and_hold, calendar_months, summarize
from ..backtest.simulator import SimulationRules, simulate, trades_frame
from ..config import Settings
from ..data.schema import interval
from ..domain.enums import ValidationVerdict
from ..features.loader import data_hashes, decision_frame, load_inputs
from ..strategies import registry
from ..strategies.base import Strategy
from .experiments import ExperimentRegistry, dependency_versions, git_state, new_run_id
from .protocol import period as resolve_period


class FrameCache:
    """Tableaux de décisions par paire, partagés entre variantes aux mêmes paramètres de features."""

    def __init__(self, settings: Settings, inputs: dict[str, dict[str, pd.DataFrame]]):
        self.settings, self.inputs = settings, inputs
        self._frames: dict[tuple, pd.DataFrame] = {}

    def get(self, symbol: str, strategy: Strategy) -> pd.DataFrame:
        key = (symbol, strategy.feature_params(self.settings.data.gap_block_bars))
        if key not in self._frames:
            self._frames[key] = decision_frame(self.settings, strategy, self.inputs[symbol])
        return self._frames[key]


@dataclass
class UniverseRun:
    """Résultat cumulé d'une variante sur toutes les paires (signaux indépendants)."""
    trades: pd.DataFrame
    no_trade: dict[str, int] = field(default_factory=dict)
    evaluated_bars: int = 0
    bars_in_position: int = 0
    candidates: int = 0
    # Durée calendaire des décisions (comptée une fois pour toutes les paires ; cumulée entre fenêtres).
    months: float = 0.0

    def summary(self, settings: Settings) -> dict:
        protocol = settings.protocol
        return summarize(self.trades, evaluated_bars=self.evaluated_bars, bars_in_position=self.bars_in_position,
                         candidates=self.candidates, no_trade=self.no_trade,
                         bootstrap_block=protocol.bootstrap_block_size, bootstrap_samples=protocol.bootstrap_samples,
                         seed=protocol.seed, months=self.months)

    def extend(self, other: UniverseRun) -> None:
        self.trades = concat_trades([self.trades, other.trades])
        for reason, count in other.no_trade.items():
            self.no_trade[reason] = self.no_trade.get(reason, 0) + count
        self.evaluated_bars += other.evaluated_bars
        self.bars_in_position += other.bars_in_position
        self.candidates += other.candidates
        self.months += other.months


def concat_trades(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["entry_status"])


def simulation_rules(settings: Settings, strategy: Strategy, symbol: str, scenario: str,
                     exit_policy_id: str | None = None) -> SimulationRules:
    return SimulationRules(
        costs=settings.costs[scenario], max_hold_bars=settings.simulation.max_hold_bars,
        min_net_rr=strategy.params.min_net_rr, tick_size=settings.tick_size(symbol),
        setup_interval=interval(settings.data.setup_timeframe), max_staleness_bars=settings.data.max_staleness_bars,
        requires_context=strategy.requires_context, exit_policy_id=exit_policy_id)


def simulate_universe(settings: Settings, strategy: Strategy, frames: FrameCache, scenario: str, *,
                      start: datetime, end: datetime, decisions_end: datetime | None = None,
                      exit_policy_id: str | None = None, progress=None) -> UniverseRun:
    out = UniverseRun(trades=concat_trades([]))
    for symbol in settings.data.symbols:
        if progress:
            progress(symbol)
        sim = simulate(frames.get(symbol, strategy), symbol, strategy,
                       simulation_rules(settings, strategy, symbol, scenario, exit_policy_id),
                       start=start, end=end, decisions_end=decisions_end)
        out.extend(UniverseRun(trades_frame(sim), dict(sim.no_trade), sim.evaluated_bars, sim.bars_in_position,
                               sim.candidates))
    out.months = calendar_months(start, decisions_end or end)
    return out


def variant_overrides(strategy_cls: type[Strategy]) -> dict[str, dict]:
    """Variantes comparées à la base : ablations (filtre retiré) puis extensions (filtre ajouté)."""
    return strategy_cls.ablations | strategy_cls.extensions


# Mêmes signaux, sorties selon le profil observé du consommateur (docs/BSM_PROFILE.md).
CONSUMER_PROFILE = ("profil_BSM", "BSM_MARKET_TP_FIXED_SL_V2")


def run_context(settings: Settings) -> tuple[dict, dict[str, Any]]:
    """Données de toutes les paires + éléments de traçabilité communs aux exécutions."""
    inputs = {symbol: load_inputs(settings, symbol) for symbol in settings.data.symbols}
    common = {"git_commit": git_state(settings.root), "dependencies": dependency_versions(),
              "seed": settings.protocol.seed, "universe": settings.data.symbols,
              "data_hashes": {symbol: data_hashes(data) for symbol, data in inputs.items()}}
    return inputs, common


@dataclass
class VariantResult:
    run_id: str
    variant: str
    scenario: str
    summary: dict
    trades: pd.DataFrame = field(repr=False)


@dataclass
class BacktestBatch:
    batch_id: str
    strategy: str
    period_label: str
    period_start: datetime
    period_end: datetime
    symbols: list[str]
    results: list[VariantResult]
    baselines: dict
    final_test_consultations: int
    report_dir: Path


def run(settings: Settings, strategy_id: str, *, period_label: str, now: datetime, allow_final_test: bool = False,
        scenarios: list[str] | None = None, ablations: bool = True, progress=None) -> BacktestBatch:
    period = resolve_period(settings, period_label, now=now, allow_final_test=allow_final_test)
    experiments = ExperimentRegistry(settings.experiments_db)
    batch_id = new_run_id("BT")
    consultations = experiments.consult_final_test(batch_id, strategy_id) if period.label == "FINAL_TEST" else 0
    scenarios = scenarios or list(settings.costs)
    base = registry.build(strategy_id, settings.strategies)
    variants: list[tuple[str, dict, str, str | None]] = [("base", {}, s, None) for s in scenarios]
    if ablations:
        variants += [(name, overrides, "central", None) for name, overrides in variant_overrides(type(base)).items()]
        variants.append((CONSUMER_PROFILE[0], {}, "central", CONSUMER_PROFILE[1]))

    inputs, common = run_context(settings)
    frames = FrameCache(settings, inputs)
    report_dir = settings.reports_dir / batch_id
    results = []
    for variant, overrides, scenario, policy in variants:
        strategy = registry.build(strategy_id, settings.strategies, **overrides)
        universe = simulate_universe(settings, strategy, frames, scenario, start=period.start, end=period.end,
                                     exit_policy_id=policy,
                                     progress=progress and (lambda s, v=variant, c=scenario: progress(f"{v}/{c}/{s}")))
        summary = universe.summary(settings)
        run_id = f"{batch_id}-{variant}-{scenario}"
        experiments.record(
            run_id=run_id, created_at=now.isoformat(), kind="BACKTEST_REFERENCE",
            hypothesis=strategy.hypothesis, strategy=strategy_id, strategy_version=strategy.version,
            variant=variant, params=strategy.params.model_dump(mode="json"), period_label=period.label,
            period_start=period.start.isoformat(), period_end=period.end.isoformat(), cost_scenario=scenario,
            simulation_rules={"max_hold_bars": settings.simulation.max_hold_bars,
                              "costs": settings.costs[scenario].model_dump(),
                              "exit_policy": policy or "déclarée par le signal",
                              "latency_seconds": settings.data.assumed_availability_latency_seconds,
                              "fill_rules": "voir backtest/simulator.py et backtest/exits.py (docstrings)"},
            metrics=summary, status="COMPLETED", report_dir=str(report_dir), **common)
        results.append(VariantResult(run_id, variant, scenario, summary, universe.trades))

    baselines = {"cash": {"return_pct": 0.0, "exposure_pct": 0.0}}
    for symbol in settings.data.symbols:
        baselines[f"buy_and_hold_{symbol}"] = buy_and_hold(inputs[symbol]["setup"], period.start, period.end,
                                                           settings.costs["central"].fee_bps)
    batch = BacktestBatch(batch_id, strategy_id, period.label, period.start, period.end, settings.data.symbols,
                          results, baselines, consultations, report_dir)
    _write_artifacts(batch, base.version)
    return batch


def _write_artifacts(batch: BacktestBatch, version: int) -> None:
    batch.report_dir.mkdir(parents=True, exist_ok=True)
    for result in batch.results:
        result.trades.to_csv(batch.report_dir / f"trades_{result.variant}_{result.scenario}.csv", index=False)
    payload = {
        "batch_id": batch.batch_id, "strategy": batch.strategy, "strategy_version": version,
        "period": {"label": batch.period_label, "start": batch.period_start.isoformat(),
                   "end": batch.period_end.isoformat()},
        "symbols": batch.symbols, "final_test_consultations": batch.final_test_consultations,
        "verdict": ValidationVerdict.NOT_EVALUATED.value,
        "results": [{"run_id": r.run_id, "variant": r.variant, "scenario": r.scenario, "summary": r.summary}
                    for r in batch.results],
        "baselines": batch.baselines,
    }
    (batch.report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                                   encoding="utf-8")
    from ..reporting.backtest_report import render_markdown
    (batch.report_dir / "report.md").write_text(render_markdown(payload), encoding="utf-8")
