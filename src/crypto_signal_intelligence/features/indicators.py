"""Implémentation UNIQUE des indicateurs, conventions documentées.

- EMA : récursive, alpha = 2/(n+1), amorcée sur la première valeur, valeurs
  masquées tant que n observations ne sont pas disponibles (min_periods=n).
- RSI et ATR : lissage de Wilder (alpha = 1/n), comme la définition d'origine.
  Ce ne sont PAS des moyennes simples ; ne pas mélanger avec d'autres librairies.
- Bollinger : centre = moyenne simple des n clôtures, écart-type de POPULATION
  (ddof=0, définition de Bollinger) ; bandes = centre ± k × écart-type.
- Les valeurs récursives dépendent de la profondeur d'historique : l'écart
  devient négligeable après quelques multiples de n (voir tests de convergence).
Toutes les fonctions sont causales : la valeur en t n'utilise que les données <= t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder(-delta.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous = close.shift(1)
    ranges = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def prior_max(series: pd.Series, n: int) -> pd.Series:
    """Maximum des n valeurs PRÉCÉDENTES (la valeur courante est exclue)."""
    return series.shift(1).rolling(n, min_periods=n).max()


def prior_min(series: pd.Series, n: int) -> pd.Series:
    return series.shift(1).rolling(n, min_periods=n).min()


def prior_mean(series: pd.Series, n: int) -> pd.Series:
    """Moyenne des n valeurs précédentes : référence de volume excluant la bougie courante."""
    return series.shift(1).rolling(n, min_periods=n).mean()


def recent_min(series: pd.Series, n: int) -> pd.Series:
    """Minimum des n dernières valeurs, bougie courante INCLUSE (elle est clôturée à la décision)."""
    return series.rolling(n, min_periods=n).min()


def bollinger(close: pd.Series, n: int = 20) -> tuple[pd.Series, pd.Series]:
    """(centre, écart-type) ; les bandes se déduisent avec le multiplicateur k de la stratégie."""
    window = close.rolling(n, min_periods=n)
    return window.mean(), window.std(ddof=0)
