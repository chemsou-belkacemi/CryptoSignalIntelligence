"""Marché synthétique du contrôle sous H0 (docs/ZOO.md § 8.1) : troncature (prix > 0, moyenne nulle), profil de
moyenne 1, dérive H0-b exacte, dates de cotation, retraits décidés sur le passé, reproductibilité, cotations EV02."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research.zoo import synth as S


def test_troncature_garde_le_prix_positif_meme_sous_volatilite_extreme():
    n = 1_000
    rng = np.random.default_rng(1)
    u = S._student(rng, n) * 3
    e = S._student(rng, n) * 3
    ones = np.ones(n)
    o, h, lo, c, z, scale = S._trajectoire(u, e, 0.7, 0.6, ones, ones, np.zeros(n), 0, rng.standard_normal(n),
                                           rng.standard_normal(n), 1.0, S.ALPHA, S.GAMMA, S.BETA)
    assert (c > 0).all() and (lo > 0).all() and (h >= np.maximum(o, c)).all()
    r = c / o - 1
    assert np.abs(r).max() <= 0.9 + 1e-12 and (1 + r >= 0.1 - 1e-12).all()     # chaque pas garde au moins 10 %
    assert (np.abs(z) <= np.minimum(20, 0.9 / scale) + 1e-12).all()


def test_choc_tronque_de_moyenne_nulle():
    n = 400_000
    rng = np.random.default_rng(2)
    u, e = S._student(rng, n), S._student(rng, n)
    ones = np.ones(n)
    *_, z, _ = S._trajectoire(u, e, 0.7, 0.012, ones, ones, np.zeros(n), 0, np.zeros(n), np.zeros(n), 1.0,
                              S.ALPHA, S.GAMMA, S.BETA)
    assert abs(z.mean()) < 4 * z.std() / np.sqrt(n)
    assert abs(np.var(S._student(rng, n)) - 1) < 0.1


def test_profil_horaire_de_moyenne_un():
    week = np.arange(168) + S.DEBUT_H - (S.DEBUT_H % 168)
    assert abs(S.profil_horaire(week).mean() - 1) < 1e-12
    hours = np.arange(S.DEBUT_H, S.DEBUT_H + 24 * 7 * 10)
    s = S.profil_horaire(hours)
    weekday = ((hours // 24) + 3) % 7
    assert s[(hours % 24 == 14) & (weekday < 5)].mean() > s[weekday >= 5].mean()


def test_paires_reproductibles_et_derive_h0b_exacte():
    commun = S.Commun.tirer(3)
    a = S.generer_paire(commun, "H0a", 0)
    a2 = S.generer_paire(commun, "H0a", 0)
    b = S.generer_paire(commun, "H0b", 0)
    np.testing.assert_array_equal(a.h1.cloture, a2.h1.cloture)
    ra = a.h1.cloture / a.h1.ouverture - 1
    rb = b.h1.cloture / b.h1.ouverture - 1
    np.testing.assert_allclose(rb - ra, 0.0010 / 24, atol=1e-12)
    assert a.h1.cloture_h[-1] == S.FIN_H and a.h1.ouverture_h[0] == S.DEBUT_H
    assert (np.diff(a.h1.disponible) > 0).all()


def test_dates_de_cotation_et_parametres():
    early = [S.params_paire(0, j).debut for j in range(2, 12)]
    later = [S.params_paire(0, j).debut for j in range(12, 100)]
    limit = S.heure(pd.Timestamp("2018-01-01", tz="UTC")) - S.DEBUT_H
    assert max(early) < limit and min(later) >= limit
    assert max(later) < S.heure(pd.Timestamp("2025-01-01", tz="UTC")) - S.DEBUT_H
    assert S.params_paire(0, 0).lam == 1.0 and S.params_paire(0, 1).lam == 0.85
    lams = [S.params_paire(0, j).lam for j in range(2, 100)]
    sigmas = [S.params_paire(0, j).sigma for j in range(2, 100)]
    assert min(lams) >= 0.55 and max(lams) <= 0.85 and min(sigmas) >= 0.008 and max(sigmas) <= 0.015


def test_retrait_decide_sur_le_passe_seul():
    n = S.FIN_H - S.DEBUT_H
    close = np.full(n, 100.0)
    crash_start = S.heure(pd.Timestamp("2019-01-01", tz="UTC")) - S.DEBUT_H
    close[crash_start:] = 10.0                          # −90 % : le 1er du mois suivant 90 jours plus tard au plus

    class Always:
        def random(self, size):
            return np.zeros(size)

    end = S._retrait(close, 0, Always(), True)
    assert end < n
    when = pd.Timestamp((end - 1 + S.DEBUT_H) * S.HOUR_NS, tz="UTC")
    # décidé au 1er février (clôtures connues à cette date : −90 % sur 90 jours), retrait dans le mois
    assert pd.Timestamp("2019-02-01", tz="UTC") <= when < pd.Timestamp("2019-03-01", tz="UTC")
    assert S._retrait(close, 0, Always(), False) == n          # BTC et ETH jamais retirés
    # même chute APRÈS la date de décision : aucune influence sur une décision antérieure
    late = np.full(n, 100.0)
    late[crash_start + 24 * 400:] = 10.0
    end_late = S._retrait(late, 0, Always(), True)
    assert end_late > crash_start + 24 * 400


def test_univers_synthetique_par_pit_universe():
    commun = S.Commun.tirer(4)
    daily = {}
    for j in range(5):
        pair = S.generer_paire(commun, "H0a", j)
        daily[pair.symbole] = pair.unite(24)
    univers = S.univers_synthetique(daily)
    first = min(univers.par_mois)
    assert univers.membres_du_mois(first) <= set(daily)
    month = pd.Timestamp("2018-01-01", tz="UTC")
    assert first == int(np.datetime64(month.tz_localize(None), "M").astype(np.int64))


def test_cotations_ev02_aux_dates_reelles_et_annonces():
    commun = S.Commun.tirer(5)
    cotations = {"XXXUSDT": pd.Timestamp("2020-05-03", tz="UTC"), "YYYUSDT": pd.Timestamp("2022-01-10", tz="UTC")}
    daily = S.generer_ev02(commun, "H0a", cotations)
    assert daily["XXXUSDT"].ouverture_h[0] == S.heure(pd.Timestamp("2020-05-03", tz="UTC"))
    assert (daily["YYYUSDT"].cloture > 0).all()
    ann = pd.DataFrame({"category": ["COTATION_SPOT"], "tickers": ["XXX"],
                        "event_time": [pd.Timestamp("2020-05-01 10:00", tz="UTC")]})
    out = S.annonces_ev02(ann, cotations).set_index("symbol")
    assert out.loc["XXXUSDT", "reelle"] and out.loc["XXXUSDT", "event_time"] == pd.Timestamp("2020-05-01 10:00", tz="UTC")
    assert not out.loc["YYYUSDT", "reelle"]
    assert out.loc["YYYUSDT", "event_time"] == pd.Timestamp("2022-01-09", tz="UTC")


def test_regimes_h0c_communs_et_persistants():
    commun = S.Commun.tirer(6)
    switches = np.flatnonzero(np.diff(commun.regime))
    assert 5 <= len(switches) <= 40                    # ≈ 8 ans / 180 jours ≈ 16 bascules
    drift_btc, scale = S.derive_et_echelle(commun, "H0c", "BTCUSDT")
    assert set(np.round(drift_btc * 24, 6)) <= {0.0015, -0.0015} and set(scale) <= {0.8, 1.25}
