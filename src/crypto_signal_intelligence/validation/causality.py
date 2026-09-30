"""Test central de causalité sur données réelles.

Pour plusieurs instants t : calcule features et décisions (a) avec les données
jusqu'à t seulement, (b) avec tout l'historique, (c) avec un futur falsifié
(prix après t multipliés, volumes permutés). Les features et décisions à t et
avant doivent être identiques dans les trois cas. Ce test réduit le risque de
fuite ; il ne prouve pas à lui seul son absence.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.builder import build_decision_frame
from ..features.context import iter_contexts
from ..strategies.base import Strategy

COMPARED_ROWS = 400


@dataclass
class CausalityCheck:
    cut: str
    rows_compared: int
    feature_mismatches: list[str]
    decision_mismatches: int

    @property
    def ok(self) -> bool:
        return not self.feature_mismatches and self.decision_mismatches == 0


def _falsify_future(frame: pd.DataFrame, cut: pd.Timestamp, rng: np.random.Generator) -> pd.DataFrame:
    frame = frame.copy()
    future = frame["open_time"] > cut
    factor = rng.uniform(0.5, 1.5, int(future.sum()))
    for column in ("open", "high", "low", "close"):
        frame.loc[future, column] = frame.loc[future, column].to_numpy() * factor
    frame.loc[future, "base_volume"] = rng.permutation(frame.loc[future, "base_volume"].to_numpy())
    return frame


def _decisions(frame: pd.DataFrame, symbol: str, timeframe: str, strategy: Strategy) -> list[str]:
    out = []
    for _, context in iter_contexts(frame, symbol, timeframe, strategy.setup_keys):
        result = strategy.evaluate(context)
        out.append(f"{result.action.value}:{result.no_trade_reason}")
    return out


def check(settings: Settings, strategy: Strategy, inputs: dict[str, pd.DataFrame], symbol: str,
          cuts: list[pd.Timestamp], seed: int = 7) -> list[CausalityCheck]:
    rng = np.random.default_rng(seed)
    tf = settings.data.setup_timeframe
    params = strategy.feature_params(settings.data.gap_block_bars)

    def build(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        return build_decision_frame(data["setup"], data["context"], data["btc"], setup_timeframe=tf,
                                    params=params, regimes=settings.regimes)

    full = build(inputs)
    checks = []
    for cut in cuts:
        truncated = build({k: v[v["open_time"] <= cut] for k, v in inputs.items()})
        falsified = build({k: _falsify_future(v, cut, rng) for k, v in inputs.items()})
        rows = full[full["open_time"] <= cut].tail(COMPARED_ROWS)
        window = rows["open_time"]
        views = [frame[frame["open_time"].isin(window)].reset_index(drop=True) for frame in (full, truncated, falsified)]
        mismatches = []
        numeric = [c for c in views[0].columns if pd.api.types.is_numeric_dtype(views[0][c])]
        for column in views[0].columns:
            reference = views[0][column]
            for other in views[1:]:
                if column in numeric:
                    same = np.allclose(reference.to_numpy(float), other[column].to_numpy(float),
                                       rtol=1e-12, atol=0, equal_nan=True)
                else:
                    same = reference.astype(str).equals(other[column].astype(str))
                if not same and column not in mismatches:
                    mismatches.append(column)
        decisions = [_decisions(v, symbol, tf, strategy) for v in views]
        decision_mismatches = sum(a != b or a != c for a, b, c in zip(*decisions, strict=True))
        checks.append(CausalityCheck(cut.isoformat(), len(window), mismatches, decision_mismatches))
    return checks
