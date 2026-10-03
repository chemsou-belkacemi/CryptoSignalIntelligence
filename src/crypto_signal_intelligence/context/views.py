"""Séries dérivées des données de contexte (docs/CONTEXTE.md) : primes Coinbase et coréenne par paire.

Journées UTC alignées : bougie journalière Binance (clôture à 00:00 UTC suivant), Coinbase et Upbit (journées UTC).
Le taux BCE d'un jour ouvré sert jusqu'au suivant (report vers l'avant seulement)."""
from __future__ import annotations

import pandas as pd

from ..config import Settings
from .store import HISTORY, OBSERVED, load


def wide(settings: Settings, series: str, field: str, *, kind: str | None = None) -> pd.DataFrame:
    """Tableau jour × clé d'un champ ; `kind` = HISTORIQUE ou RELEVE (None : le relevé du jour prime, sinon
    l'historique)."""
    frame = load(settings, series)
    frame = frame[frame["field"] == field]
    if kind is not None:
        frame = frame[frame["kind"] == kind]
    else:
        frame = frame.assign(rank=(frame["kind"] == OBSERVED).astype(int)).sort_values("rank")
        frame = frame.drop_duplicates(["key", "date"], keep="last")
    if frame.empty:
        return pd.DataFrame()
    frame = frame.assign(day=pd.to_datetime(frame["date"], utc=True).dt.floor("D"))
    return frame.pivot_table(index="day", columns="key", values="value", aggfunc="last").sort_index()


def _binance_by_base(settings: Settings, kind: str | None) -> pd.DataFrame:
    closes = wide(settings, "binance_daily", "close", kind=kind)
    closes = closes[[c for c in closes.columns if c.endswith("USDT")]]
    return closes.rename(columns=lambda c: c[:-4])


def coinbase_premium(settings: Settings, *, kind: str | None = None) -> pd.DataFrame:
    """Prime Coinbase par actif : clôture BASE-USD sur Coinbase / clôture BASEUSDT sur Binance − 1 (1 USDT compté
    pour 1 USD : écart réel de l'ordre de 0,1 %, déclaré)."""
    coinbase = wide(settings, "coinbase", "close_usd", kind=kind)
    binance = _binance_by_base(settings, kind)
    common = coinbase.columns.intersection(binance.columns)
    return (coinbase[common] / binance[common].reindex(coinbase.index) - 1).dropna(how="all")


def korea_premium(settings: Settings, *, kind: str | None = None) -> pd.DataFrame:
    """Prime coréenne par actif : (clôture KRW-BASE sur Upbit / wons par dollar) / clôture BASEUSDT sur Binance − 1 ;
    wons par dollar = (wons par euro) / (dollars par euro), taux BCE reporté jusqu'au jour ouvré suivant."""
    upbit = wide(settings, "upbit", "close_krw", kind=kind)
    rates = wide(settings, "ecb", "per_eur", kind=kind if kind != OBSERVED else None)
    if upbit.empty or rates.empty or not {"KRW", "USD"} <= set(rates.columns):
        return pd.DataFrame()
    krw_per_usd = (rates["KRW"] / rates["USD"]).reindex(upbit.index.union(rates.index)).ffill().reindex(upbit.index)
    binance = _binance_by_base(settings, kind)
    common = upbit.columns.intersection(binance.columns)
    in_usd = upbit[common].div(krw_per_usd, axis=0)
    return (in_usd / binance[common].reindex(upbit.index) - 1).dropna(how="all")


__all__ = ["HISTORY", "OBSERVED", "coinbase_premium", "korea_premium", "wide"]
