"""Criblage S (research/seasonality_screen.py) : drapeaux du calendrier, meilleure heure apprise sur le passé seul,
rendement de l'ouverture à la clôture. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import seasonality_screen as ss


def test_calendar_flags_by_hand():
    index = pd.date_range("2024-03-28", "2024-04-02 23:00", freq="h", tz="UTC")
    flagged = lambda name: list(index[ss.calendar_flags(index, name)].strftime("%Y-%m-%d %H"))  # noqa: E731
    assert flagged("S1_WEEKEND") == ["2024-03-30 00"] and flagged("S2_LUNDI") == ["2024-04-01 00"]
    assert flagged("S3_TOURNANT_DU_MOIS") == ["2024-03-30 00"]                  # deux jours avant le 31 mars
    assert flagged("S5_EXPIRATION_OPTIONS") == ["2024-03-29 08"]                # dernier vendredi de mars 2024
    assert flagged("S6_OUVERTURE_USA") == ["2024-03-28 14", "2024-03-29 14", "2024-04-01 14", "2024-04-02 14"]
    assert len(flagged("S4_APRES_FINANCEMENT")) == 6 * 3


def test_forward_return_from_open_to_close():
    index = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
    opens = pd.DataFrame({"A": [100.0, 101, 102, 103, 104, 105]}, index=index)
    closes = pd.DataFrame({"A": [101.0, 102, 103, 104, 105, 106]}, index=index)
    assert ss.forward(opens, closes, 1)["A"].iloc[0] == pytest.approx(0.01)
    assert ss.forward(opens, closes, 4)["A"].iloc[0] == pytest.approx(104 / 100 - 1)


def test_best_hour_is_learned_on_previous_days_only(monkeypatch):
    monkeypatch.setattr(ss, "LEARN_DAYS", 30)
    monkeypatch.setattr(ss, "LEARN_MIN_DAYS", 20)
    index = pd.date_range("2024-01-01", periods=24 * 260, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    values = rng.normal(0, 0.001, len(index))
    values[index.hour == 7] += 0.01                                             # l'heure 7 est la meilleure
    one_hour = pd.DataFrame({"A": values}, index=index)
    flags = ss.best_hour_flags(one_hour)["A"]
    assert set(index[flags.to_numpy()].hour[-30:]) == {7}
    assert not flags.iloc[: 24 * 20].any()                                      # moins de 20 journées passées
    changed = one_hour.copy()
    changed.iloc[-24:, 0] = 0.5                                                 # le dernier jour change
    assert ss.best_hour_flags(changed)["A"].iloc[: -24].equals(flags.iloc[: -24])
