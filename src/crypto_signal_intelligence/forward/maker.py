"""Entrée par ordre limite (maker) contre entrée au marché (taker), sur une même décision (test F1_MAKER_TAKER,
docs/FORWARD_TESTS.md). Fonctions pures, sans entrée-sortie.

`bars[0]` est la première bougie où l'ordre peut être passé (ouverture ≥ heure où le plan existe).

Chaque entrée est d'abord rejouée SANS AUCUN COÛT : on garde l'issue et le rapport brut prix de sortie / prix
d'entrée. Les coûts sont appliqués ensuite par `net_return`, ce qui permet de mesurer l'écart observé (sans écart
supposé à l'entrée) et l'écart d'équilibre, sans rejouer les bougies.

- Taker : exactement la règle du plan (`tracking.replay_plan`, rejouée sans coût) : entrée à l'ouverture de
  `bars[0]` ; stop au contact (ou à l'ouverture sous le stop) ; objectif seulement s'il est dépassé ; stop d'abord.
- Maker : ordre limite au prix `limit`, valable `valid_bars` bougies. Rempli seulement si une bougie passe
  STRICTEMENT sous `limit × (1 − through)`. Dans la bougie du remplissage, le pire : stop touché si le plus bas
  l'atteint, objectif jamais compté. Ensuite, mêmes règles que le plan, jusqu'à la MÊME fin d'horizon que le taker.
  Non rempli : aucune position.
"""
from __future__ import annotations

import pandas as pd

from ..outlook.tracking import GAP, SL, TIMEOUT, TP, replay_plan

NOT_FILLED, PENDING = "NON_REMPLI", "PENDING"


def net_return(gross: float, *, fee: float, entry_cost: float, exit_cost: float) -> float:
    """Rendement net d'un aller-retour : frais à chaque ordre, écart et glissement à l'entrée et à la sortie.
    Avec entry_cost = exit_cost = market, c'est exactement la formule de `tracking.replay_plan`."""
    return gross * (1 - exit_cost) * (1 - fee) / ((1 + entry_cost) * (1 + fee)) - 1


def taker_path(bars: pd.DataFrame, *, stop_pct: float, target_pct: float, horizon_bars: int,
               step: pd.Timedelta) -> dict:
    """{"outcome", "gross"} de la règle du plan, sans coût (gross = sortie / entrée)."""
    outcome, r0 = replay_plan(bars, stop_pct=stop_pct, target_pct=target_pct, horizon_bars=horizon_bars, step=step,
                              fee=0.0, market=0.0)
    return {"outcome": outcome, "gross": None if r0 is None else 1 + r0 * (-stop_pct / 100)}


def fill_index(low, *, limit: float, valid_bars: int, through: float) -> int | None:
    """Première bougie (parmi les `valid_bars` premières) dont le plus bas passe strictement sous le seuil."""
    threshold = limit * (1 - through)
    for j in range(min(valid_bars, len(low))):
        if low[j] < threshold:
            return j
    return None


def maker_path(bars: pd.DataFrame, *, limit: float, stop_pct: float, target_pct: float, horizon_bars: int,
               valid_bars: int, step: pd.Timedelta, through: float = 0.0) -> dict:
    """{"outcome", "gross", "fill_bar"} sans coût ; outcome NON_REMPLI, TP, SL, TEMPS, TROU ou PENDING."""
    if bars.empty:
        return {"outcome": PENDING, "gross": None, "fill_bar": None}
    window = bars.iloc[:horizon_bars]
    if not (window["open_time"].diff().iloc[1:] == step).all():
        return {"outcome": GAP, "gross": None, "fill_bar": None}
    if len(window) < horizon_bars:
        return {"outcome": PENDING, "gross": None, "fill_bar": None}
    o, h, low, c = (window[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    j = fill_index(low, limit=limit, valid_bars=valid_bars, through=through)
    if j is None:
        return {"outcome": NOT_FILLED, "gross": None, "fill_bar": None}
    entry = limit
    stop, target = entry * (1 + stop_pct / 100), entry * (1 + target_pct / 100)
    outcome, exit_price = None, None
    if low[j] <= stop:                                   # pire cas dans la bougie du remplissage
        outcome, exit_price = SL, stop
    for k in range(j + 1, len(window)):
        if outcome is not None:
            break
        if o[k] <= stop:
            outcome, exit_price = SL, o[k]
        elif o[k] >= target:
            outcome, exit_price = TP, target
        elif low[k] <= stop:
            outcome, exit_price = SL, stop
        elif h[k] > target:
            outcome, exit_price = TP, target
    if outcome is None:
        outcome, exit_price = TIMEOUT, c[-1]
    assert exit_price is not None
    return {"outcome": outcome, "gross": float(exit_price / entry), "fill_bar": int(j)}
