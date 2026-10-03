"""Coûts d'exécution sur les transactions (research/ticks.py) : écart, achat au marché après délai, remplissage
d'un ordre limite (non touché, touché seulement, traversé). SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from crypto_signal_intelligence.research import ticks

DAY = date(2025, 3, 15)
T0 = pd.Timestamp(DAY, tz="UTC")


def trades(rows: list[tuple[float, float, bool]]) -> pd.DataFrame:
    """(secondes depuis 00:00, prix, acheteur maker = vente au marché)."""
    when = pd.to_datetime([T0 + pd.Timedelta(seconds=s) for s, _, _ in rows], utc=True)
    return pd.DataFrame({"time": when, "ns": when.as_unit("ns").asi8, "price": [p for _, p, _ in rows],
                         "qty": 1.0, "buyer_is_maker": [m for _, _, m in rows]})


def test_spread_and_market_buy_after_delay():
    mark = T0 + pd.Timedelta(minutes=15)
    data = trades([(890, 100.0, True), (895, 100.2, False), (899, 100.1, True),   # avant la clôture : bid 100,1 ask 100,2
                   (905, 100.3, False), (950, 100.5, False)])
    close = ticks.closes(data, DAY).iloc[0]
    assert close["close"] == 100.1 and close["mark"] == mark
    assert ticks.spread_at(data, mark, int(close["last_index"])) == pytest.approx(0.1 / 100.15 * 1e4)
    assert ticks.market_buy_after(data, mark, 100.1, 1) == pytest.approx((100.3 / 100.1 - 1) * 1e4)
    assert ticks.market_buy_after(data, mark, 100.1, 40) == pytest.approx((100.5 / 100.1 - 1) * 1e4)
    assert ticks.market_buy_after(data, mark, 100.1, 200) is None                    # rien dans les 2 min


def test_limit_fill_distinguishes_touched_and_traded_through():
    mark = T0 + pd.Timedelta(minutes=15)
    touched = trades([(899, 100.0, True), (960, 99.9, True), (1000, 100.0, False)])
    assert ticks.limit_fill(touched, mark, 100.0, 0.001, 15) == "touche_seulement"   # 99,9 atteint, jamais dessous
    through = trades([(899, 100.0, True), (960, 99.8, True)])
    assert ticks.limit_fill(through, mark, 100.0, 0.001, 15) == "traverse"
    untouched = trades([(899, 100.0, True), (960, 99.95, True)])
    assert ticks.limit_fill(untouched, mark, 100.0, 0.001, 15) == "non_touche"
    late = trades([(899, 100.0, True), (2000, 99.0, True)])
    assert ticks.limit_fill(late, mark, 100.0, 0.001, 15) == "non_touche"            # hors de la fenêtre de 15 min
    assert ticks.limit_fill(late, mark, 100.0, 0.001, 60) == "traverse"
