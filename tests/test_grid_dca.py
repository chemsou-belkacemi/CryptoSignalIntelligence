"""Grille et DCA (research/grid_dca.py) : remplissage seulement si le prix traverse, ventes un pas plus haut, DCA par
paliers, prise de gain, liquidation, comparaison à « garder ». SYNTHÉTIQUE."""
from __future__ import annotations

import pandas as pd
import pytest

from crypto_signal_intelligence.research import grid_dca as gd


def bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_grid_buys_on_crossing_sells_one_step_up_and_ignores_a_touch():
    # Niveaux à 98 et 96 (±4 %, pas 2 %) ; 98 touché seulement (low = 98) : aucun achat.
    touched = bars([(100, 100.5, 98.0, 99), (99, 99.5, 98.5, 99)])
    ret, trades, exposure = gd.simulate_grid(touched, 0.04, 0.02, fee=0.0, exit_cost=0.0)
    assert trades == 0 and ret == pytest.approx(0.0) and exposure == 0.0
    # 98 traversé (achat), puis 99,96 traversé (vente 98 × 1,02) : un aller-retour gagnant de 2 % sur la moitié du capital.
    cycle = bars([(100, 100.5, 97.9, 98.5), (98.5, 100.0, 98.2, 99.5)])
    ret, trades, _ = gd.simulate_grid(cycle, 0.04, 0.02, fee=0.0, exit_cost=0.0)
    assert trades == 2 and ret == pytest.approx(0.5 * 0.02)


def test_dca_buys_on_each_further_drop_and_takes_profit():
    falling = bars([(100, 101, 99, 100), (100, 100, 94.9, 95), (95, 95, 90.3, 90.5), (90.5, 91, 90.3, 90)])
    ret, trades, exposure = gd.simulate_dca(falling, 4, 0.05, None, fee=0.0, exit_cost=0.0)
    assert trades == 2 and exposure == pytest.approx(0.5)                    # 100, puis 95 ; 90,25 jamais traversé
    expected = 0.25 * (90 / 100 - 1) + 0.25 * (90 / 95 - 1)
    assert ret == pytest.approx(expected)
    bounce = bars([(100, 101, 99, 100), (100, 100, 94.9, 95), (95, 101, 95, 100)])
    ret, trades, _ = gd.simulate_dca(bounce, 4, 0.05, 0.03, fee=0.0, exit_cost=0.0)
    average = 0.5 / (0.25 / 100 + 0.25 / 95)                                 # coût moyen des deux tranches
    assert trades == 3 and ret == pytest.approx(0.5 * (average * 1.03 / average - 1), rel=1e-6)


def test_hold_return_and_costs():
    month = bars([(100, 110, 95, 105)])
    assert gd.hold_return(month, 0.0, 0.0) == pytest.approx(0.05)
    assert gd.hold_return(month, 0.001, 0.0003) < 0.05
