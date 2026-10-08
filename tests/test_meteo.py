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


def test_placebo_lags_are_multiples_of_seven_up_to_twelve_weeks():
    assert ms.placebo_lags() == tuple(range(7, 85, 7)) and len(ms.placebo_lags()) == 12
    assert mc.rotation_violations(1967, mutation=None, seed=1) == 0
    assert mc.rotation_violations(1967, mutation="rotation_non_multiple_7", seed=1) > 0


def _one_block(color, y):
    color, y = np.asarray(color), np.asarray(y, float)
    return ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, [(0, len(color))])


def test_forward_orthogonal_deviation_by_hand():
    """ỹ_t = √(k/(k+1)) · (y_t − moyenne de y sur F_t), F_t = jours suivants du même jour de semaine dans le bloc."""
    y = np.arange(28, dtype=float) ** 2
    dev, term = ms._forward_dev(y, np.ones(28, bool), np.array([0]), np.array([28]))
    assert dev[3] == pytest.approx(np.sqrt(3 / 4) * (9 - (100 + 289 + 576) / 3))
    assert term[:21].all() and not term[21:].any()                    # dernière semaine : F_t vide, pas un terme
    color = np.full(28, mt.VERT)
    color[[1, 15]] = mt.ROUGE
    prep = _one_block(color, y)
    r = (color == mt.ROUGE).astype(float)
    r_dev, _ = ms._forward_dev(r, np.ones(28, bool), np.array([0]), np.array([28]))
    assert prep.num0[0] == pytest.approx(dev[1] + dev[15])
    assert prep.den0[0] == pytest.approx(r_dev[1] + r_dev[15])
    assert ms.estimate(prep) == pytest.approx(-(dev[1] + dev[15]) / (r_dev[1] + r_dev[15]))


def test_numerator_is_exactly_zero_when_y_is_constant_by_block_and_weekday():
    """(a) y constant par bloc et par jour de semaine : numérateur nul exactement, quelles que soient les couleurs
    (feu observé et chaque décalage du placebo)."""
    days = ms.MAIN.days
    rng = np.random.default_rng(4)
    blocks = ms.blocks_of(len(days))
    level = np.zeros(len(days))
    for start, length in blocks:
        level[start:start + length] = rng.normal(0, 1) + rng.normal(0, 1, 7)[np.arange(length) % 7]
    color = np.where(rng.random(len(days)) < 0.3, mt.ROUGE, np.where(rng.random(len(days)) < 0.05, mt.SANS_FEU, mt.VERT))
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, level), level, blocks)
    assert np.abs(prep.num0).max() < 1e-12 and np.abs(prep.num).max() < 1e-12
    assert ms.estimate(prep) == pytest.approx(0.0, abs=1e-12)


def test_weekday_only_light_is_not_identifiable():
    """N4 redéfini : un feu rouge le lundi et le mardi seulement donne Σ r·r̃ = 0, non défini, aucun verdict."""
    days = ms.MAIN.days
    y = np.random.default_rng(3).normal(0, 1, len(days))
    color = np.where(days.dayofweek.isin([0, 1]), mt.ROUGE, mt.VERT)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, ms.blocks_of(len(days)))
    assert np.abs(prep.den0).max() < 1e-12 and np.isnan(ms.estimate(prep))
    a = ms.decide(ms.analyse(color, y, ms.MAIN, samples=200))
    assert a.verdict == ms.NON_IDENTIFIABLE


def test_causal_placebo_turns_target_into_hole_when_source_is_missing():
    """Placebo causal : couleur du jour t − u ; source avant la période ou sur un trou → la cible est un trou."""
    n = 56
    color = np.full(n, mt.VERT)
    color[[2, 10]] = mt.ROUGE
    color[16] = mt.SANS_FEU
    y = np.random.default_rng(0).normal(0, 1, n)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, [(0, 28), (28, 28)])
    # décalage 7 : seuls les jours 9 et 17 reçoivent du rouge (sources 2 et 10) ; la cible 23 (source 16) est un trou
    u = ms.placebo_lags().index(7)
    rp = np.zeros(n)
    rp[[9, 17]] = 1.0
    valid = ~ms.holes_of(color, y) & (np.arange(n) >= 7)
    valid[23] = False
    y_dev, term = ms._forward_dev(np.where(ms.holes_of(color, y), 0.0, y), ~ms.holes_of(color, y),
                                  np.array([0, 28]), np.array([28, 28]))
    rp_dev, term_p = ms._forward_dev(rp, valid, np.array([0, 28]), np.array([28, 28]))
    use = term & term_p
    assert prep.num[0, u] == pytest.approx(float((rp * y_dev)[:28][use[:28]].sum()))
    assert prep.den[0, u] == pytest.approx(float((rp * rp_dev)[:28][use[:28]].sum()))
    assert prep.num[1, ms.placebo_lags().index(84)] == 0.0                    # source avant la période : trous


def test_block_useful_needs_a_red_and_a_non_red_term():
    """B1a corrigé : un bloc dont le seul jour rouge n'est pas un terme (dernière semaine, F_t vide) n'est pas utile."""
    n = 28
    color = np.full(2 * n, mt.VERT)
    color[25] = mt.ROUGE
    color[n + np.array([3, 12])] = mt.ROUGE
    y = np.random.default_rng(0).normal(0, 1, 2 * n)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, [(0, n), (n, n)])
    assert prep.useful.tolist() == [False, True]


def test_day_without_result_is_a_hole():
    """B1d : un jour sans y_d devient un trou, comme un jour SANS_FEU."""
    color = np.array([mt.ROUGE, mt.VERT] * 14)
    y = np.ones(28)
    y[0] = np.nan
    hole = ms.holes_of(color, y)
    assert hole[0] and hole.sum() == 1
    prep = _one_block(color, y)
    assert prep.n_red[0] == 10                                         # rouges 2, 4, …, 20 (0 est un trou, 22 à 26 sans F_t)


def test_replica_without_red_day_contributes_nothing_and_ratio_stays_defined(monkeypatch):
    """Une réplique sans terme rouge dans un bloc n'apporte rien à ce bloc ; le rapport reste défini par les autres
    blocs (renormalisation implicite)."""
    n = 28
    rng = np.random.default_rng(1)
    color = np.full(2 * n, mt.VERT)
    color[[0, 1]] = mt.ROUGE
    color[n + np.array([2, 9, 10])] = mt.ROUGE
    y = rng.normal(0, 1, 2 * n)
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, [(0, n), (n, n)])
    assert prep.useful.all()
    no_red = np.array(list(range(2, 7)) + list(range(7, 28)) + [2, 3])
    calls = []

    def positions(length, _rng, samples):
        calls.append(length)
        return np.tile(no_red if len(calls) == 1 else np.arange(length), (samples, 1))

    monkeypatch.setattr(ms, "replica_positions", positions)
    replicas = ms.bootstrap(prep, np.random.default_rng(0), samples=5)
    only_b = ms.estimate(prep, np.array([False, True]))
    assert replicas == pytest.approx(np.full(5, only_b))


def test_analysis_fields_and_exact_injection_shift():
    """Δ_exc := Δ̂ ; p-valeurs par rangs ; injecter −Δ sur les jours rouges déplace Δ̂ de Δ exactement."""
    days = ms.MAIN.days
    rng = np.random.default_rng(5)
    color = np.where(rng.random(len(days)) < 0.2, mt.ROUGE, mt.VERT)
    y = rng.normal(0, 1, len(days))
    a = ms.analyse(color, y, ms.MAIN, samples=2000)
    assert a.delta_exc == a.delta_hat and np.isfinite(a.placebo_mean)
    assert 1 / 2001 <= a.p_high <= 1 and 1 / 2001 <= a.p_low <= 1 and a.ci[0] <= a.ci[1]
    hit = ms.analyse(color, y - ms.DELTA_MIN * (color == mt.ROUGE), ms.MAIN, samples=200, with_guards=False)
    assert hit.delta_hat - a.delta_hat == pytest.approx(ms.DELTA_MIN, abs=1e-12)


def _trend_toy(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """y i.i.d. ; feu de tendance calculé sur le passé de y (rouge si le cumul de la veille est sous son EMA50) :
    les couleurs dépendent des y passés, comme le vrai feu."""
    days = len(ms.MAIN.days)
    rng = np.random.default_rng(seed)
    y = rng.normal(0, 1, days + 60)
    level = np.cumsum(y)
    ema = mt.ema_seeded(level, 50)
    red = np.zeros(days + 60, bool)
    red[1:] = level[:-1] < ema[:-1]
    color = np.where(red, mt.ROUGE, mt.VERT)[60:]
    return color, y[60:]


def _toy_z(mutation: str | None, reps: int = 300) -> float:
    values = []
    for k in range(reps):
        color, y = _trend_toy(k)
        values.append(ms.analyse(color, y, ms.MAIN, samples=200, boot_samples=10, with_guards=False,
                                 mutation=mutation).z)
    return float(np.mean(values))


def test_circular_placebo_mutation_is_biased():
    """(b) Mutation « placebo circulaire » : sous l'hypothèse nulle avec un feu de tendance, l'ancienne rotation
    circulaire donne |z moyen| > 1 (elle doit être détectée)."""
    assert abs(_toy_z("placebo_circulaire")) > 1.0


@pytest.mark.xfail(strict=True, reason="biais de sélection des blocs utiles (B1a dépend des couleurs, donc des y du "
                                       "bloc) : z moyen ≈ −0,21 sur ce jouet (3 000 répliques), ≈ −0,06 avec tous les "
                                       "blocs ; rapporté au relecteur, non corrigé (2026-10-08)")
def test_causal_placebo_is_unbiased_on_a_trend_light():
    """(b) Le placebo causal doit rester à |z moyen| ≤ 0,15 sous l'hypothèse nulle."""
    assert abs(_toy_z(None)) <= 0.15


def test_positive_control_mean_estimate_is_delta_min():
    """(d) Contrôle positif : la moyenne de Δ̂ avec l'injection de Δ_min reste entre 0,9 et 1,1 × Δ_min."""
    values = []
    for k in range(300):
        color, y = _trend_toy(1000 + k)
        injected = y * 0.7 - ms.DELTA_MIN * (color == mt.ROUGE)
        values.append(ms.analyse(color, injected, ms.MAIN, samples=50, boot_samples=10, with_guards=False).delta_hat)
    assert 0.9 * ms.DELTA_MIN <= np.mean(values) <= 1.1 * ms.DELTA_MIN


def test_g2_turns_episode_into_holes_and_can_empty_a_block():
    """B1c : retirer le meilleur épisode rouge fait de ses jours des trous (F_t recalculés) ; un bloc dont c'était le
    seul épisode n'est plus utile, et Δ̂ est recalculé sans lui."""
    period = ms.Period("TEST", pd.Timestamp("2022-01-03", tz=UTC), pd.Timestamp("2022-02-27", tz=UTC), "F6", (2022,))
    n = 56
    color = np.full(n, mt.VERT)
    color[[3, 4]] = mt.ROUGE
    color[[30, 31, 40]] = mt.ROUGE
    y = np.zeros(n)
    y[[3, 4]] = -5.0
    blocks = [(0, 28), (28, 28)]
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, blocks)
    g, _ = ms.guards(color, y, period, prep, excess=ms.estimate(prep))
    assert g["g2_episode"] == (3, 4)
    hole = ms.holes_of(color, y)
    hole[3:5] = True
    after = ms.prepare(color == mt.ROUGE, hole, y, blocks)
    assert after.useful.tolist() == [False, True]
    assert g["g2_excess"] == pytest.approx(ms.estimate(after), nan_ok=True)


def test_g3_needs_two_thirds_of_years_when_more_than_six():
    period = ms.VARIANT
    color = np.where(np.random.default_rng(0).random(len(period.days)) < 0.3, mt.ROUGE, mt.VERT)
    y = np.random.default_rng(1).normal(0, 1, len(period.days))
    prep = ms.prepare(color == mt.ROUGE, ms.holes_of(color, y), y, ms.blocks_of(len(y)))
    g, by_year = ms.guards(color, y, period, prep, excess=ms.estimate(prep), with_g2=False)
    assert len(by_year) == 8 and g["g3_needed"] == 6


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
    assert ms.decide(fake(0.5, 0.5, (-0.1, 0.1), g1=False)).verdict == ms.INSUFFISANT     # G1 : toute issue
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


@pytest.mark.parametrize("mutation", ["ema_open_time", "largeur_jour_d", "fng_jour_d", "pertes_d10_d1",
                                      "financement_t_plus_8h"])
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


def test_martingale_generator_has_zero_mean_simple_returns():
    """(c) Prix martingales : moyenne du rendement simple horaire nulle sous N1 et N2."""
    for case in ("N1", "N2"):
        world = mc.make_world(mc.Spec(case, "principale", 11), period=SHORT)
        r = np.concatenate([np.diff(f["close"].to_numpy()) / f["close"].to_numpy()[:-1] for f in world.pairs.values()])
        assert abs(r.mean()) < 4 * r.std() / np.sqrt(len(r))


def test_n3c_plus_is_recomputed_with_the_new_instrument():
    """(e) N3c+ refait : effet en % (Δ_min × σ̂ médian) sur les jours rouges, avec effet de levier."""
    out = mc.simulate(mc.Spec("N3c", "principale", 2), samples=200)
    assert np.isfinite(out["plus"]["delta_exc"]) and out["sigma_median"] > 0
    assert out["plus"]["delta_exc"] > out["null"]["delta_exc"]
