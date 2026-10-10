"""Données du zoo (docs/ZOO.md § 2) : agrégation datée par la dernière bougie 1 h, blocs complets, coupure dure au
2025-06-30 (un futur falsifié n'est jamais lu), jointure externe sur `available_at`, univers du mois de la décision."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.zoo import data as Z

from .zoo_outils import heure, paire, univers_mois


def test_agregation_4h_et_1j_datee_par_la_derniere_bougie_1h():
    closes = np.arange(1, 49, dtype=float)
    p = paire(closes, start="2020-03-02", mecha=0.0)
    h4 = p.unite(4)
    assert len(h4) == 12
    assert h4.ouverture_h[0] == heure("2020-03-02") and h4.cloture_h[0] == heure("2020-03-02 04:00")
    assert h4.disponible[0] == p.h1.disponible[3]                 # available_at de la 4e bougie 1 h
    assert h4.cloture[0] == 4 and h4.ouverture[0] == 1 and h4.haut[0] == 4 and h4.bas[0] == 1
    assert h4.volume[0] == pytest.approx(4 * p.h1.volume[0])
    day = p.unite(24)
    assert len(day) == 2 and day.disponible[0] == p.h1.disponible[23] and day.cloture[1] == 48


def test_bloc_incomplet_absent():
    p = paire(np.arange(1, 49, dtype=float), start="2020-03-02", manquantes=[5])
    assert len(p.unite(4)) == 11
    assert len(p.unite(24)) == 1 and p.unite(24).ouverture_h[0] == heure("2020-03-03")
    q = paire(np.arange(1, 7, dtype=float), start="2020-03-02 02:00")       # commence au milieu d'un bloc
    assert len(q.unite(4)) == 1 and q.unite(4).ouverture_h[0] == heure("2020-03-02 04:00")


def test_coupure_dure_refusee_au_dela():
    with pytest.raises(Z.LectureRefusee):
        Z.borne_lecture(pd.Timestamp("2025-07-01 00:00:01", tz="UTC"))
    assert Z.borne_lecture(pd.Timestamp("2025-07-01", tz="UTC")) == Z.HARD_END
    assert pd.Timestamp("2025-07-01", tz="UTC") == Z.HARD_END
    assert Z.VALIDATION.fin_exclusive == Z.HARD_END
    assert Z.DECOUVERTE.fin_exclusive == pd.Timestamp("2023-01-01", tz="UTC")


def _canonical(start: str, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    open_time = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"open_time": open_time, "open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "quote_volume": 1e6, "taker_buy_quote_volume": 5e5, "number_of_trades": 10.0,
                         "available_at": open_time + pd.Timedelta(hours=1, seconds=2)})


def test_charger_paire_ne_lit_jamais_apres_la_borne(monkeypatch, settings):
    from crypto_signal_intelligence.research import long_history
    frame = _canonical("2025-06-25", 24 * 12, 1)
    falsified = frame.copy()
    after = frame["open_time"] >= pd.Timestamp("2025-07-01", tz="UTC")
    falsified.loc[after, ["open", "high", "low", "close"]] *= 1000
    monkeypatch.setattr(long_history, "load_long", lambda s, sym: frame)
    a = Z.charger_paire(settings, "AAAUSDT", Z.HARD_END)
    monkeypatch.setattr(long_history, "load_long", lambda s, sym: falsified)
    b = Z.charger_paire(settings, "AAAUSDT", Z.HARD_END)
    assert a.h1.cloture_h[-1] == heure("2025-07-01")
    np.testing.assert_array_equal(a.h1.cloture, b.h1.cloture)
    d = Z.charger_paire(settings, "AAAUSDT", Z.DECOUVERTE.fin_exclusive)
    assert d is None
    with pytest.raises(Z.LectureRefusee):
        Z.charger_paire(settings, "AAAUSDT", pd.Timestamp("2025-07-02", tz="UTC"))


def test_serie_externe_coupee_et_jointe_sur_available_at():
    days = pd.date_range("2025-06-25", "2025-07-03", freq="D", tz="UTC")
    frame = pd.DataFrame({"date": days, "value": np.arange(len(days), dtype=float),
                          "available_at": days + pd.Timedelta(hours=22)})
    serie = Z.serie_depuis(frame, "x", Z.HARD_END)
    assert pd.Timestamp(int(serie.disponible.max()), tz="UTC") < Z.HARD_END
    assert len(serie) == 6                                   # 25 au 30 juin (le 30 : publié à 22:00, avant la borne)
    decisions = np.array([pd.Timestamp("2025-06-27 22:00", tz="UTC").value,
                          pd.Timestamp("2025-06-27 22:00:01", tz="UTC").value])
    assert Z.joindre(decisions, serie).tolist() == [1.0, 2.0]    # strictement antérieur


def test_week_end_reprend_la_valeur_du_vendredi():
    friday = pd.Timestamp("2024-03-08", tz="UTC")
    frame = pd.DataFrame({"date": [friday, friday + pd.Timedelta(days=3)], "value": [1.0, 2.0],
                          "available_at": [friday + pd.Timedelta(hours=22), friday + pd.Timedelta(days=3, hours=22)]})
    serie = Z.serie_depuis(frame, "x", Z.HARD_END)
    weekend = [pd.Timestamp(t, tz="UTC").value for t in ("2024-03-09 12:00", "2024-03-10 23:00", "2024-03-11 23:00")]
    assert Z.joindre(np.array(weekend), serie).tolist() == [1.0, 1.0, 2.0]


def test_univers_du_mois_de_la_decision_et_unites_de_temps():
    u = univers_mois({"2019-01": ["A"], "2019-02": ["B"]})
    instants = np.array([pd.Timestamp("2019-01-31 23:00:02", tz="UTC").value,
                         pd.Timestamp("2019-02-01 00:00:02", tz="UTC").value])
    assert u.membre("A", instants).tolist() == [True, False]
    assert u.membre("B", instants).tolist() == [False, True]
    micro = pd.Series(pd.to_datetime(["2019-02-01"], utc=True)).dt.as_unit("us")
    assert Z.en_ns(micro)[0] == pd.Timestamp("2019-02-01", tz="UTC").value
    built = Z.Univers.depuis(pd.DataFrame({"month": micro, "symbol": ["C"]}))
    assert built.membre("C", instants[1:]).tolist() == [True]


def test_etapes_et_jours():
    assert Z.DECOUVERTE.jours()[0] == pd.Timestamp("2019-01-01").value // 86_400_000_000_000
    assert len(Z.DECOUVERTE.jours()) == 1461 and len(Z.VALIDATION.jours()) == 912
    assert Z.annee_du_jour(np.array([Z.VALIDATION.fin_jour])).tolist() == [2025]
