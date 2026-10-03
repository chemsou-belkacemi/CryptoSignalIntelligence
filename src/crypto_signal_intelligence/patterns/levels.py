"""Niveaux de la période précédente, sessions et chiffres ronds (docs/INDICATEURS.md § 10.1 à 10.3, écrits avant le
code). Bibliothèque seulement : aucun test en cours ne les utilise."""
from __future__ import annotations

import math

import pandas as pd

PERIODS = {"D": "jour", "W": "semaine", "M": "mois"}
PREFIX = {"D": "PD", "W": "PW", "M": "PM"}
SESSIONS = {"ASIE": (0, 8), "LONDRES": (7, 16), "NEW_YORK": (13, 22)}     # heures UTC fixes, début inclus, fin exclue


def _hours(h1: pd.DataFrame) -> pd.DataFrame:
    frame = h1[["open_time", "open", "high", "low", "close"]].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    return frame.sort_values("open_time").reset_index(drop=True)


def _period_start(times: pd.Series, period: str) -> pd.Series:
    naive = times.dt.tz_convert(None)
    if period == "D":
        start = naive.dt.floor("D")
    elif period == "W":
        start = naive.dt.floor("D") - pd.to_timedelta(naive.dt.dayofweek, unit="D")
    else:
        start = naive.dt.to_period("M").dt.start_time
    return start.dt.tz_localize("UTC")


def _period_hours(start: pd.Timestamp, period: str) -> int:
    if period == "D":
        return 24
    if period == "W":
        return 7 * 24
    return int(start.days_in_month) * 24


def period_table(h1: pd.DataFrame, period: str) -> pd.DataFrame:
    """Une ligne par période COMPLÈTE : début, fin, ouverture, plus haut, plus bas, clôture."""
    frame = _hours(h1)
    frame["start"] = _period_start(frame["open_time"], period)
    grouped = frame.groupby("start")
    table = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                        hours=("open", "size")).reset_index()
    table["expected"] = [_period_hours(s, period) for s in table["start"]]
    table = table[table["hours"] == table["expected"]]
    ends = {"D": pd.Timedelta(days=1), "W": pd.Timedelta(days=7)}
    table["end"] = (table["start"] + ends[period] if period in ends
                    else table["start"].apply(lambda s: s + pd.offsets.MonthBegin(1)))
    return table[["start", "end", "open", "high", "low", "close"]].reset_index(drop=True)


def previous_levels(h1: pd.DataFrame, at: pd.Timestamp) -> dict[str, float | None]:
    """Niveaux de la dernière période terminée avant `at` (`at` ≥ fin de la période), pour le jour, la semaine et le
    mois ; None si cette période est incomplète."""
    at = pd.Timestamp(at)
    out: dict[str, float | None] = {}
    for period, prefix in PREFIX.items():
        frame = _hours(h1[pd.to_datetime(h1["open_time"], utc=True) < at])
        if frame.empty:
            out |= {f"{prefix}{x}": None for x in "OHLC"}
            continue
        current = _period_start(pd.Series([at]), period).iloc[0]
        previous = _period_start(pd.Series([current - pd.Timedelta(hours=1)]), period).iloc[0]
        table = period_table(frame, period)
        row = table[(table["start"] == previous) & (table["end"] <= at)]
        values = row.iloc[0] if len(row) else None
        for letter, column in zip("OHLC", ("open", "high", "low", "close"), strict=True):
            out[f"{prefix}{letter}"] = float(values[column]) if values is not None else None
    return out


def sessions(h1: pd.DataFrame) -> pd.DataFrame:
    """Plus haut et plus bas de chaque session complète de chaque jour ; `known_at` = fin de la session."""
    frame = _hours(h1)
    frame["day"] = frame["open_time"].dt.floor("D")
    frame["hour"] = frame["open_time"].dt.hour
    rows = []
    for name, (begin, end) in SESSIONS.items():
        part = frame[(frame["hour"] >= begin) & (frame["hour"] < end)]
        for day, group in part.groupby("day"):
            if len(group) != end - begin:
                continue
            rows.append({"day": day, "session": name, "high": float(group["high"].max()), "low": float(group["low"].min()),
                         "known_at": day + pd.Timedelta(hours=end)})
    return pd.DataFrame(rows, columns=["day", "session", "high", "low", "known_at"])


def round_levels(price: float) -> dict[str, float]:
    """Niveaux ronds autour de `price` : majeurs (multiples de 10^k) et mineurs (multiples de 5 × 10^(k−1)), k =
    ⌊log10 price⌋ ; un niveau égal au prix compte comme « au-dessus »."""
    if not price > 0:
        raise ValueError("prix strictement positif attendu")
    k = math.floor(math.log10(price))
    out = {}
    for name, step in (("major", 10.0 ** k), ("minor", 5 * 10.0 ** (k - 1))):
        below = math.floor(price / step) * step
        if math.isclose(below, price, rel_tol=1e-12):
            above, below = price, below - step
        else:
            above = below + step
        out[f"{name}_below"], out[f"{name}_above"] = round(below, 12), round(above, 12)
    return out
