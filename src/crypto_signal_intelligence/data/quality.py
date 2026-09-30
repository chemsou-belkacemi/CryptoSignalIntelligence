"""Contrôles de qualité et quarantaine. Aucune imputation, aucun forward-fill.

Une ligne invalide est retirée et conservée en quarantaine avec son motif.
Les trous restent des trous : `gap_before` indique le nombre de bougies
manquantes avant chaque ligne ; les décisions qui en dépendent sont bloquées.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from .schema import PRICE_COLUMNS, VOLUME_COLUMNS, interval

TAKER_TOLERANCE = 1e-9
EPOCH = pd.Timestamp(0, tz="UTC")


@dataclass
class QualityReport:
    symbol: str
    timeframe: str
    rows: int = 0
    first_open_time: str | None = None
    last_open_time: str | None = None
    duplicates: int = 0
    non_monotonic: int = 0
    misaligned: int = 0
    invalid_ohlc: int = 0
    negative_volume: int = 0
    taker_inconsistent: int = 0
    non_finite: int = 0
    open_candles: int = 0
    gaps: list[dict] = field(default_factory=list)
    missing_bars: int = 0
    staleness_bars: float | None = None
    quarantined: int = 0

    @property
    def ok(self) -> bool:
        return self.quarantined == 0 and not self.gaps and self.non_monotonic == 0

    def to_dict(self) -> dict:
        return asdict(self) | {"ok": self.ok}


def row_problems(frame: pd.DataFrame, timeframe: str) -> pd.Series:
    """Motif de rejet par ligne ('' si valide)."""
    step = interval(timeframe)
    reasons = pd.Series("", index=frame.index, dtype="object")

    def flag(mask, reason):
        nonlocal reasons
        reasons = reasons.where(~(mask & (reasons == "")), reason)

    prices = frame[PRICE_COLUMNS]
    volumes = frame[VOLUME_COLUMNS]
    flag(~np.isfinite(prices).all(axis=1) | ~np.isfinite(volumes).all(axis=1), "NON_FINITE")
    flag((prices <= 0).any(axis=1), "NON_POSITIVE_PRICE")
    flag((frame["low"] > frame[["open", "close"]].min(axis=1)) |
         (frame["high"] < frame[["open", "close"]].max(axis=1)) | (frame["low"] > frame["high"]), "INVALID_OHLC")
    flag((volumes < 0).any(axis=1), "NEGATIVE_VOLUME")
    flag((frame["taker_buy_base_volume"] > frame["base_volume"] * (1 + TAKER_TOLERANCE) + TAKER_TOLERANCE) |
         (frame["taker_buy_quote_volume"] > frame["quote_volume"] * (1 + TAKER_TOLERANCE) + TAKER_TOLERANCE),
         "TAKER_INCONSISTENT")
    # Indépendant de la résolution interne (ms/us/ns) choisie par pandas.
    flag((frame["open_time"] - EPOCH) % step != pd.Timedelta(0), "MISALIGNED")
    flag(~frame["is_closed"].astype(bool), "OPEN_CANDLE")
    return reasons


def clean(frame: pd.DataFrame, timeframe: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(lignes valides triées et uniques, quarantaine avec colonne `reason`)."""
    if frame.empty:
        return frame, frame.assign(reason=pd.Series(dtype="object"))
    reasons = row_problems(frame, timeframe)
    bad = frame[reasons != ""].assign(reason=reasons[reasons != ""])
    good = frame[reasons == ""]
    duplicated = good.duplicated("open_time", keep="last")
    if duplicated.any():
        bad = pd.concat([bad, good[duplicated].assign(reason="DUPLICATE")])
        good = good[~duplicated]
    return good.sort_values("open_time").reset_index(drop=True), bad.reset_index(drop=True)


def with_gap_column(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Ajoute `gap_before` : bougies manquantes entre la ligne précédente et celle-ci."""
    step = interval(timeframe)
    diffs = frame["open_time"].diff()
    gap = ((diffs / step).fillna(1) - 1).round().astype(int).clip(lower=0)
    return frame.assign(gap_before=gap.to_numpy())


def assess(frame: pd.DataFrame, symbol: str, timeframe: str, now: datetime | None = None) -> QualityReport:
    """Rapport sur un jeu (brut ou stocké) sans le modifier."""
    report = QualityReport(symbol=symbol, timeframe=timeframe, rows=int(len(frame)))
    if frame.empty:
        return report
    report.first_open_time = frame["open_time"].min().isoformat()
    report.last_open_time = frame["open_time"].max().isoformat()
    report.duplicates = int(frame.duplicated("open_time").sum())
    report.non_monotonic = int((frame["open_time"].diff().dt.total_seconds() <= 0).sum())
    reasons = row_problems(frame, timeframe)
    counts = reasons[reasons != ""].value_counts()
    report.invalid_ohlc = int(counts.get("INVALID_OHLC", 0) + counts.get("NON_POSITIVE_PRICE", 0))
    report.negative_volume = int(counts.get("NEGATIVE_VOLUME", 0))
    report.taker_inconsistent = int(counts.get("TAKER_INCONSISTENT", 0))
    report.misaligned = int(counts.get("MISALIGNED", 0))
    report.non_finite = int(counts.get("NON_FINITE", 0))
    report.open_candles = int(counts.get("OPEN_CANDLE", 0))
    report.quarantined = int(len(counts) and counts.sum()) + report.duplicates
    ordered = frame.drop_duplicates("open_time").sort_values("open_time")
    gaps = with_gap_column(ordered, timeframe)
    for _, row in gaps[gaps["gap_before"] > 0].iterrows():
        report.gaps.append({"before_open_time": row["open_time"].isoformat(), "missing_bars": int(row["gap_before"])})
    report.missing_bars = int(gaps["gap_before"].sum())
    if now is not None:
        last_end = frame["open_time"].max() + interval(timeframe)
        report.staleness_bars = round((pd.Timestamp(now) - last_end) / interval(timeframe), 2)
    return report
