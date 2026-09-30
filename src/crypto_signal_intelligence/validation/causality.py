"""Test central de causalité sur données réelles.

Pour plusieurs bougies de décision t : calcule features et décisions (a) avec les
seules bougies DISPONIBLES à l'instant de décision de t (available_at, pour chaque
unité de temps : une bougie 1h en formation est donc exclue), (b) avec tout
l'historique, (c) avec un futur falsifié (prix des bougies non encore disponibles
multipliés, volumes et nombre de trades permutés). Les features et décisions à t et
avant doivent être identiques dans les trois cas. Une jointure faite sur open_time au
lieu d'available_at est détectée (tests/test_features.py). Ce test réduit le risque
de fuite ; il ne prouve pas à lui seul son absence.
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
FALSIFIED_COUNTS = ("base_volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume",
                    "number_of_trades")


@dataclass
class CausalityCheck:
    cut: str
    rows_compared: int
    feature_mismatches: list[str]
    decision_mismatches: int

    @property
    def ok(self) -> bool:
        return not self.feature_mismatches and self.decision_mismatches == 0


def _falsify_future(frame: pd.DataFrame, decided_at: pd.Timestamp, rng: np.random.Generator) -> pd.DataFrame:
    frame = frame.copy()
    future = frame["available_at"] > decided_at
    factor = rng.uniform(0.5, 1.5, int(future.sum()))
    for column in ("open", "high", "low", "close"):
        frame.loc[future, column] = frame.loc[future, column].to_numpy() * factor
    for column in FALSIFIED_COUNTS:
        if column in frame:
            frame.loc[future, column] = rng.permutation(frame.loc[future, column].to_numpy())
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
    setup = inputs["setup"]
    for cut in cuts:
        # Instant de décision de la bougie t : sa propre disponibilité (clôture + latence supposée).
        decided_at = setup.loc[setup["open_time"] <= cut, "available_at"].max()
        truncated = build({k: v[v["available_at"] <= decided_at] for k, v in inputs.items()})
        falsified = build({k: _falsify_future(v, decided_at, rng) for k, v in inputs.items()})
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
