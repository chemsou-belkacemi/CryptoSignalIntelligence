"""Météo du marché (docs/METEO_MARCHE.md) : composantes, feu, statistique bloc par bloc, placebos, intervalle,
garde-fous, causalité et mutations. Données SYNTHÉTIQUES uniquement, aucun réseau."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import meteo as mt
from crypto_signal_intelligence.research import meteo_controls as mc
from crypto_signal_intelligence.research import meteo_study as ms

UTC = "UTC"


def _hours(start: str, periods: int, close: np.ndarray | None = None, *, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range(start, periods=periods, freq="h", tz=UTC).as_unit("ns")
    if close is None:
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, periods)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open_time": times, "open": open_, "high": np.maximum(open_, close) * 1.002,
                         "low": np.minimum(open_, close) * 0.998, "close": close, "quote_volume": 1e7,
                         "available_at": times + pd.Timedelta(hours=1, seconds=2)})


# --- Blocs, rotations, statistique -----------------------------------------------------------------------------------

def test_blocks_of_main_and_variant_periods():
    main = ms.blocks_of(len(ms.MAIN.days))
    assert len(ms.MAIN.days) == 1967 and len(main) == 22 and main[-1] == (21 * 91, 56)
    variant = ms.blocks_of(len(ms.VARIANT.days))
    assert len(ms.VARIANT.days) == 2667 and len(variant) == 30 and variant[-1] == (29 * 91, 28)
    assert ms.blocks_of(91 + 21) == [(0, 112)]                    # dernier bloc de 3 semaines fusionné
    assert ms.MAIN.start.dayofweek == 0 and ms.VARIANT.start.dayofweek == 0 and ms.MAIN.end.dayofweek == 6


def test_rotations_are_multiples_of_seven_and_counted():
    assert [len(ms.shifts(n)) - 1 for n in (91, 56, 28)] == [12, 7, 3]
    assert all(u % 7 == 0 for u in ms.shifts(91))
    assert mc.rotation_violations(1967, mutation=None, seed=1) == 0
    assert mc.rotation_violations(1967, mutation="rotation_non_multiple_7", seed=1) > 0


def test_block_contrast_by_hand():
    red = np.zeros(14, bool)
    red[[0, 8]] = True
    y = np.arange(14, dtype=float)
    c, n_r, n_nr = ms.block_matrix(red, np.zeros(14, bool), y)
    assert c[0, 0] == pytest.approx((y.sum() - 8) / 12 - 4)          # non rouges − rouges
    assert (n_r[0] == 2).all() and (n_nr[0] == 12).all()
    rotated = np.roll(red, 7)                                         # couleur du jour t − 7
    assert c[0, 1] == pytest.approx(y[~rotated].mean() - y[rotated].mean())


def test_weekday_only_light_gives_exactly_zero_excess():
    """Jour de semaine seul (lundi et mardi rouges) : les rotations par semaines entières gardent les jours rouges ;
    l'excès contre P_T est exactement nul, quels que soient les résultats."""
    days = ms.MAIN.days
    rng = np.random.default_rng(3)
    y = rng.normal(0, 1, len(days)) + np.where(days.dayofweek == 0, 0.5, 0.0)
    color = np.where(days.dayofweek.isin([0, 1]), mt.ROUGE, mt.VERT)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, ms.blocks_of(len(days)))
    observed, placebo, excess = ms.excess_of(prep)
    assert abs(excess) < 1e-12 and observed < -0.2              # les lundis rouges sont « meilleurs »


def test_block_useless_when_every_rotation_sends_a_red_day_onto_a_hole():
    """B1a : un bloc dont chaque rotation non nulle envoie son seul jour rouge sur un trou n'est utile nulle part."""
    n = 28
    color = np.full(2 * n, mt.VERT)
    color[0] = mt.ROUGE
    color[[7, 14, 21]] = mt.SANS_FEU
    color[n + 3] = mt.ROUGE
    color[n + 12] = mt.ROUGE
    y = np.random.default_rng(0).normal(0, 1, 2 * n)
    blocks = [(0, n), (n, n)]
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, blocks)
    assert prep.useful.tolist() == [False, True] and prep.weights[0] == 0.0
    alone = ms.prepare(color[n:] == mt.ROUGE, ms.holes_of(color[n:], y[n:]), y[n:], [(0, n)])
    assert ms.excess_of(prep) == pytest.approx(ms.excess_of(alone))


def test_day_without_result_is_a_hole():
    """B1d : un jour sans y_d devient un trou, comme un jour SANS_FEU."""
    color = np.array([mt.ROUGE, mt.VERT] * 14)
    y = np.ones(28)
    y[0] = np.nan
    hole = ms.holes_of(color, y)
    assert hole[0] and hole.sum() == 1
    _, n_r, n_nr = ms.block_matrix(color == mt.ROUGE, hole, y)
    assert n_r[0, 0] == 13 and n_nr[0, 0] == 14


def test_replica_without_red_day_drops_block_and_renormalises(monkeypatch):
    """B1b : une réplique qui fait perdre ses jours rouges à un bloc le retire de cette réplique ; Δ̂ est renormalisé
    sur les blocs encore définis, sans changer leurs poids."""
    n = 28
    rng = np.random.default_rng(1)
    color = np.full(2 * n, mt.VERT)
    color[[0, 1]] = mt.ROUGE                                          # bloc A : rouges en première semaine seulement
    color[n + np.array([2, 9, 17, 25])] = mt.ROUGE                    # bloc B
    y = rng.normal(0, 1, 2 * n)
    red, hole = color == mt.ROUGE, ms.holes_of(color, y)
    prep = ms.prepare(red, hole, y, [(0, n), (n, n)])
    assert prep.useful.all()
    no_red = np.array([2, 3, 4, 5, 6] + list(range(7, 28)) + [2, 3])  # aucun des jours 0 et 1
    calls = []

    def positions(length, _rng, samples):                             # bloc A : sans rouge ; bloc B : identique
        calls.append(length)
        return np.tile(no_red if len(calls) == 1 else np.arange(length), (samples, 1))

    monkeypatch.setattr(ms, "replica_positions", positions)
    boot = ms.bootstrap(red, hole, y, prep, np.random.default_rng(0), samples=5)
    assert boot.dropped.tolist() == [5, 0] and boot.undefined == 0
    only_b = ms.prepare(red[n:], hole[n:], y[n:], [(0, n)])
    assert boot.excess == pytest.approx(np.full(5, ms.excess_of(only_b)[2]))


def test_placebo_mean_is_exact_enumeration_and_p_values_bounds():
    days = ms.MAIN.days
    rng = np.random.default_rng(5)
    color = np.where(rng.random(len(days)) < 0.2, mt.ROUGE, mt.VERT)
    y = rng.normal(0, 1, len(days))
    a = ms.analyse(color, y, ms.MAIN, samples=2000)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, ms.blocks_of(len(days)))
    manual = sum(w * c[1:].mean() for c, w, u in zip(prep.contrasts, prep.weights, prep.useful, strict=True) if u)
    assert a.placebo_mean == pytest.approx(manual / prep.weights.sum())
    assert a.delta_exc == pytest.approx(a.delta_hat - a.placebo_mean)
    assert 1 / 2001 <= a.p_high <= 1 and 1 / 2001 <= a.p_low <= 1
    assert a.ci[0] <= a.ci[1]


def test_injected_effect_raises_excess_by_about_delta_min():
    days = ms.MAIN.days
    rng = np.random.default_rng(6)
    color = np.where(rng.random(len(days)) < 0.25, mt.ROUGE, mt.VERT)
    y = rng.normal(0, 0.7, len(days))
    base = ms.analyse(color, y, ms.MAIN, samples=500, with_guards=False)
    hit = ms.analyse(color, y - ms.DELTA_MIN * (color == mt.ROUGE), ms.MAIN, samples=500, with_guards=False)
    assert hit.delta_exc - base.delta_exc == pytest.approx(ms.DELTA_MIN, abs=0.03)


def test_g2_turns_episode_into_holes_and_can_empty_a_block():
    """B1c : retirer le meilleur épisode rouge fait de ses jours des trous ; un bloc dont c'était le seul épisode
    n'est plus utile, et Δ_exc est recalculé sans lui."""
    days = mt.day_index("2022-01-03", "2022-01-03")
    period = ms.Period("TEST", days[0], days[0] + pd.Timedelta(days=55), "F6", (2022,))
    n = 56
    color = np.full(n, mt.VERT)
    color[[3, 4]] = mt.ROUGE                                          # bloc unique : un seul épisode
    color[[30, 31, 40]] = mt.ROUGE
    y = np.zeros(n)
    y[[3, 4]] = -5.0
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, [(0, 28), (28, 28)])
    g, _ = ms.guards(color, y, period, prep, excess=ms.excess_of(prep)[2])
    assert g["g2_episode"] == (3, 4)
    hole = ms.holes_of(color, y)
    hole[3:5] = True
    after = ms.prepare(color == mt.ROUGE, hole, y, [(0, 28), (28, 28)])
    assert after.useful.tolist() == [False, True]
    assert g["g2_excess"] == pytest.approx(ms.excess_of(after)[2])


def test_decide_outcomes():
    def fake(p_high, p_low, ci, g1=True, g2=True):
        a = ms.Analysis(0, 0, 0, 1, 0, p_high, p_low, (0, 0), ci, 0.9, 6, 60, 8, [], 0)
        a.guards = {"G1": g1, "G2": g2, "G3": True, "G4": True}
        return a
    assert ms.decide(fake(0.01, 0.99, (0.2, 0.4))).verdict == ms.PERSISTANCE
    assert ms.decide(fake(0.01, 0.99, (0.2, 0.4), g2=False)).verdict == ms.PERSISTANCE_FRAGILE
    assert ms.decide(fake(0.99, 0.01, (-0.4, -0.2))).verdict == ms.INVERSE
    assert ms.decide(fake(0.5, 0.5, (-0.1, 0.1))).verdict == ms.EQUIVALENT_NUL
    assert ms.decide(fake(0.5, 0.5, (-0.1, 0.1)), equivalence_judgeable=False).verdict == ms.NON_CONCLUANT
    assert ms.decide(fake(0.5, 0.5, (-0.1, 0.2))).verdict == ms.NON_CONCLUANT
    assert ms.decide(fake(0.01, 0.99, (0.2, 0.4), g1=False)).verdict == ms.INSUFFISANT
    both = ms.decide(fake(0.01, 0.99, (0.05, 0.12)))
    assert both.verdict == ms.PERSISTANCE and both.equivalence and "inférieur" in both.note
    assert ms.global_verdict(ms.INSTRUMENT_TROP_FAIBLE, None).startswith("Rien")
    assert "ne pas" not in ms.global_verdict(ms.NON_CONCLUANT, None, equivalence_judgeable=False)
    assert "inutile" in ms.global_verdict(ms.NON_CONCLUANT, None, equivalence_judgeable=False)


# --- Composantes, à la main -------------------------------------------------------------------------------------------

def test_ema_seeded_by_simple_mean():
    values = np.arange(1.0, 61.0)
    ema = mt.ema_seeded(values, 50)
    assert np.isnan(ema[48]) and ema[49] == pytest.approx(25.5)
    assert ema[50] == pytest.approx(2 / 51 * 51 + 49 / 51 * 25.5)


def test_strict_rank_excludes_today_and_counts_strictly_below():
    values = np.concatenate([np.arange(300.0), [150.0]])
    p, n = mt.strict_rank(values, window=365, minimum=300)
    assert n[-1] == 300 and p[-1] == pytest.approx(150 / 300)
    assert np.isnan(p[-2])                                          # 299 valeurs seulement


def test_btc_structure_uses_previous_complete_day_only():
    h1 = _hours("2024-01-01", 24 * 80)
    days = mt.day_index("2024-03-10", "2024-03-15")
    full = mt.btc_structure(mt.daily_from_h1(h1), days)
    daily = mt.daily_from_h1(h1)
    closes = daily.loc[daily["complete"], "close"]
    ema = mt.ema_seeded(closes.to_numpy())
    d = days[2]
    expected = float(closes.loc[d - pd.Timedelta(days=1)] <= ema[closes.index.get_loc(d - pd.Timedelta(days=1))])
    assert full.loc[d, "BTC_STRUCTURE"] == expected
    gap = h1[h1["open_time"] != d - pd.Timedelta(hours=5)]             # journée d − 1 incomplète : absente
    assert np.isnan(mt.btc_structure(mt.daily_from_h1(gap), days).loc[d, "BTC_STRUCTURE"])


def test_fear_funding_and_losses_by_hand():
    days = mt.day_index("2024-01-10", "2024-01-11")
    fng = pd.Series([24.0, 30.0, 10.0], index=mt.day_index("2024-01-09", "2024-01-11"))
    out = mt.fear(fng, days)
    assert out["PEUR_EXTREME"].tolist() == [1.0, 0.0]                 # valeur horodatée d − 1
    times = pd.date_range("2024-01-03 08:00", "2024-01-11 00:00", freq="8h", tz=UTC)
    funding = pd.DataFrame({"time": times, "rate": 0.0006})
    hot = mt.funding_hot(funding, mt.day_index("2024-01-11", "2024-01-11"))
    assert hot["funding_n"].iloc[0] == 21 and hot["FINANCEMENT_CHAUD"].iloc[0] == 1.0
    few = mt.funding_hot(funding.iloc[::2], mt.day_index("2024-01-11", "2024-01-11"))
    assert np.isnan(few["FINANCEMENT_CHAUD"].iloc[0])                 # moins de 18 règlements : absente
    s1_days = mt.day_index("2023-01-01", "2024-02-01")
    s1 = pd.DataFrame({"day": np.repeat(s1_days, 20), "r_gross": np.tile(np.linspace(-1, 1, 20), len(s1_days))})
    s1.loc[s1["day"] >= pd.Timestamp("2024-01-24", tz=UTC), "r_gross"] = -2.0
    s1.loc[s1["day"] >= pd.Timestamp("2024-01-28", tz=UTC), "r_gross"] = 100.0     # d − 3 à d − 1 : jamais lus
    loss = mt.recent_losses(s1, mt.day_index("2024-01-31", "2024-01-31"))
    assert loss["m"].iloc[0] == pytest.approx(4 * 20 * -2.0 / 140)
    assert loss["PERTES_RECENTES"].iloc[0] == 1.0


def test_light_rules_and_episodes_with_holes():
    states = pd.DataFrame([[1, 1, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0], [1, np.nan, np.nan, 0, 0, 0],
                           [1, 0, 0, 0, 0, np.nan]], columns=list(mt.COMPONENTS), dtype=float)
    assert mt.light(states).tolist() == [mt.ROUGE, mt.ORANGE, mt.SANS_FEU, mt.VERT]
    r4 = pd.DataFrame([[1, 1, 0, np.nan], [1, 0, 0, 0], [0, 0, 0, 0]], columns=list(mt.COMPONENTS_R4), dtype=float)
    assert mt.light(r4, rule="R4").tolist() == [mt.ROUGE, mt.ORANGE, mt.VERT]
    color = np.array([0, 2, -1, 2, 1, -1, -1, 0, 2, -1])
    assert mt.episodes(color) == [(1, 3), (8, 9)]                      # un épisode de SANS_FEU seuls n'existe pas


def test_largeur_share_and_minimum():
    days_all = mt.day_index("2024-01-01", "2024-03-31")
    rows = []
    for k in range(12):
        trend = np.linspace(100, 150 if k < 3 else 60, len(days_all))
        rows += [{"day": d, "symbol": f"S{k}", "close": c, "quote_volume": 1e6} for d, c in zip(days_all, trend, strict=True)]
    pit = pd.DataFrame(rows)
    members = pd.DataFrame([{"month": m, "symbol": f"S{k}"} for m in pd.date_range("2024-01-01", "2024-03-01", freq="MS",
                                                                                    tz=UTC) for k in range(12)])
    out = mt.largeur(pit, members, mt.day_index("2024-03-15", "2024-03-15"))
    assert out["largeur_eligible"].iloc[0] == 12 and out["largeur_share"].iloc[0] == pytest.approx(3 / 12)
    assert out["LARGEUR"].iloc[0] == 1.0
    few = mt.largeur(pit, members[members["symbol"].isin(["S0", "S1", "S2", "S3", "S4", "S5"])],
                     mt.day_index("2024-03-15", "2024-03-15"))
    assert np.isnan(few["LARGEUR"].iloc[0])
    assert mt.largeur(pit, members[members["symbol"].isin(["S0", "S1", "S2", "S3", "S4", "S5"])],
                      mt.day_index("2024-03-15", "2024-03-15"), rule="R4")["LARGEUR"].iloc[0] == 0.0


# --- Panier S2N et sortie ---------------------------------------------------------------------------------------------

def test_basket_exit_rules_including_delisting():
    """Vente à l'ouverture de d + 1 01:00 ; si elle manque, à la prochaine ouverture ; sans bougie après, à la
    dernière clôture (COTATION_ARRETEE, gardée) ; jamais retirée du panier pour une donnée future."""
    h1 = _hours("2024-01-01", 24 * 6)
    days = pd.DatetimeIndex([pd.Timestamp(d, tz=UTC) for d in ("2024-01-02", "2024-01-03", "2024-01-05")])
    h1 = h1[(h1["open_time"] != pd.Timestamp("2024-01-04 01:00", tz=UTC))
            & (h1["open_time"] <= pd.Timestamp("2024-01-05 20:00", tz=UTC))]
    out = mt.basket_returns(h1, days)
    by_time = h1.set_index("open_time")
    assert out["exit_kind"].tolist() == ["OUVERTURE_D1", "OUVERTURE_SUIVANTE", mt.COTATION_ARRETEE]
    assert out.loc[days[1], "exit"] == by_time.loc[pd.Timestamp("2024-01-04 02:00", tz=UTC), "open"]
    assert out.loc[days[2], "exit"] == h1["close"].iloc[-1]
    assert out.loc[days[0], "exit"] == by_time.loc[pd.Timestamp("2024-01-03 01:00", tz=UTC), "open"]
    assert out["r"].notna().all()
    leaky = mt.basket_returns(h1, days, mutation="panier_sans_ouverture_future")
    assert leaky["r"].isna().sum() == 2                               # mutation : paires retirées (doit être détectée)


def test_s2n_eligibility_and_normalisation():
    days = mt.day_index("2024-06-01", "2024-06-03")
    returns = {s: pd.DataFrame({"r": [0.02, -0.01, np.nan]}, index=days) for s in ("A", "B")}
    sigma = {"A": pd.Series([0.04, 0.04, 0.04], index=days), "B": pd.Series([0.02, np.nan, 0.02], index=days)}
    members = pd.DataFrame([{"month": pd.Timestamp("2024-05-01", tz=UTC), "symbol": s} for s in ("A", "B")]
                           + [{"month": pd.Timestamp("2024-06-01", tz=UTC), "symbol": "A"}])
    firsts = {"A": pd.Timestamp("2023-01-01", tz=UTC), "B": pd.Timestamp("2024-04-01", tz=UTC)}
    out = mt.s2n(returns, sigma, members, firsts, days)
    assert out.loc[days[0], "y"] == pytest.approx(0.5)                  # B a moins de 90 jours
    assert out.loc[days[1], "n"] == 1 and np.isnan(out.loc[days[2], "y"])


# --- Prévision H24 recalculée : bougies futures supprimées ou falsifiées -------------------------------------------------

def test_future_candles_do_not_change_h24_forecast():
    """Supprimer ou falsifier les bougies postérieures à T_d ne change ni la présence ni la valeur de la prévision
    ni de son rang ; la mutation « filtre sur la cible » change la présence (détectée)."""
    from crypto_signal_intelligence.research import volatility_hourly as vh
    btc = _hours("2021-01-01", 24 * 520, seed=1)
    eth = _hours("2021-01-01", 24 * 520, seed=2)
    training = pd.concat([vh.hourly_frame(btc, btc).assign(symbol="BTCUSDT"),
                          vh.hourly_frame(eth, btc).assign(symbol="ETHUSDT")], ignore_index=True)
    forecaster = mt.RealForecaster(training, seed=0)
    d = pd.Timestamp("2022-05-30", tz=UTC)
    days = pd.DatetimeIndex([d])
    full = mt.vol_high(mt.real_btc_forecasts(forecaster, vh.hourly_frame(btc, btc), days), days)
    cut = btc[btc["available_at"] <= d + mt.DECISION]
    got = mt.vol_high(mt.real_btc_forecasts(forecaster, vh.hourly_frame(cut, cut), days), days)
    assert np.isfinite(full.loc[d, "btc_forecast"]) and np.isfinite(full.loc[d, "vol_rank"])
    assert got.loc[d, "btc_forecast"] == pytest.approx(full.loc[d, "btc_forecast"], rel=1e-12)
    assert got.loc[d, "vol_rank"] == pytest.approx(full.loc[d, "vol_rank"])
    leaky = mt.vol_high(mt.real_btc_forecasts(forecaster, vh.hourly_frame(cut, cut), days,
                                              mutation="vol_cible_filtree"), days)
    assert np.isnan(leaky.loc[d, "btc_forecast"])


# --- Causalité et mutations du § 7.1 sur un petit marché synthétique ------------------------------------------------------

SHORT = ms.Period("COURT", pd.Timestamp("2020-02-03", tz=UTC), pd.Timestamp("2020-02-03", tz=UTC) + pd.Timedelta(days=181),
                  "F6", (2020,))


@pytest.fixture(scope="module")
def world():
    return mc.make_world(mc.Spec("N2", "principale", 7), period=SHORT)


def test_light_is_causal_on_truncated_and_falsified_data(world):
    picks = pd.DatetimeIndex([SHORT.start + pd.Timedelta(days=40), SHORT.start + pd.Timedelta(days=150)])
    assert mc.light_differences(world, picks, mutation=None, seed=0) == []
    assert mc.basket_differences(world, picks[:1], mutation=None, seed=0) == []
    assert mc.membership_differences(world, picks, mutation=None, seed=0) == []
    assert mc.funding_differences(world, mutation=None, count=3, seed=0) == []


@pytest.mark.parametrize("mutation", ["ema_open_time", "largeur_jour_d", "fng_jour_d", "pertes_d3_d1"])
def test_light_mutations_are_detected(world, mutation):
    picks = pd.DatetimeIndex([SHORT.start + pd.Timedelta(days=40), SHORT.start + pd.Timedelta(days=150)])
    assert mc.light_differences(world, picks, mutation=mutation, seed=0)


def test_other_mutations_are_detected(world):
    picks = pd.DatetimeIndex([SHORT.start + pd.Timedelta(days=40)])
    assert mc.basket_differences(world, picks, mutation="panier_sans_ouverture_future", seed=0)
    assert mc.membership_differences(world, picks, mutation="membres_mois_courant", seed=0)
    assert mc.funding_differences(world, mutation="financement_sans_latence", count=3, seed=0)
    assert mc.rank_reference(world, SHORT.days, mutation=None) == 0
    assert mc.rank_reference(world, SHORT.days, mutation="rang_inclut_jour") > 0


def test_vol_rank_base_comes_from_the_same_instance():
    """Base et valeur de la même instance : la mutation « base prise d'une autre instance » change le rang."""
    days = mt.day_index("2023-01-01", "2023-12-31")
    calendar = mt.day_index("2022-01-01", "2023-12-31")
    rng = np.random.default_rng(2)
    first, second = pd.Timestamp("2023-01-01", tz=UTC), pd.Timestamp("2023-07-01", tz=UTC)
    base = pd.Series(rng.lognormal(0, 0.3, len(calendar)), index=calendar)
    by_instance = {first: base, second: base * 1.5}                    # même modèle, échelle différente
    instance_of = mt.instance_of_days(days, [first, second])
    vol = mt.VolForecasts(by_instance, instance_of)
    good = mt.vol_high(vol, days)
    d = pd.Timestamp("2023-09-01", tz=UTC)
    window = by_instance[second][(calendar >= d - pd.Timedelta(days=365)) & (calendar < d)]
    assert good.loc[d, "vol_rank"] == pytest.approx(float((window < by_instance[second][d]).mean()))
    leaky = mt.vol_high(vol, days, mutation="base_autre_instance")
    assert (leaky["vol_rank"] - good["vol_rank"]).abs().max() > 0.05
    assert (mt.vol_high(vol, days, mutation="rang_inclut_jour")["vol_rank"] != good["vol_rank"]).any()
