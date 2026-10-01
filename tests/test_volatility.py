"""Prévision de volatilité (docs/VOLATILITY.md, protocole v1) : cibles et variables calculées à la main, causalité
et mutation, modèles, purge, entraînement glissant, pertes, règle, refus des données incomplètes, coupure à la
fin de DEVELOPMENT, bout en bout. Données SYNTHÉTIQUES : elles testent le code, jamais une prévision de marché."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.research import volatility as vol
from crypto_signal_intelligence.research.experiments import ExperimentRegistry
from crypto_signal_intelligence.research.intervals import calendar_mean_ci
from crypto_signal_intelligence.research.long_history import long_settings
from crypto_signal_intelligence.research.universe import RESEARCH_UNIVERSE

from .conftest import canonical

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DOC = Path(__file__).resolve().parents[1] / "docs" / "VOLATILITY.md"
PAIRS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT")
NONE = vol.NO_IMPROVEMENT
FAST_TREES = {"LGBM_ROUNDS": 20, "LGBM_PARAMS": vol.LGBM_PARAMS | {"min_data_in_leaf": 20}}


def day(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def hourly(symbol: str, *, days: int, seed: int, start: str = "2024-01-01") -> pd.DataFrame:
    return canonical(24 * days, "1h", symbol=symbol, start=start, seed=seed)


def candles(returns, *, start: str = "2024-01-01") -> pd.DataFrame:
    """Bougies 1 h dont les rendements log horaires sont `returns` (une bougie de base les précède)."""
    frame = canonical(len(returns) + 1, "1h", start=start)
    return frame.assign(close=100 * np.exp(np.r_[0.0, np.cumsum(returns)]))


def alternating(magnitudes) -> np.ndarray:
    """Rendements de l'ampleur donnée, de signe alterné (le prix ne dérive pas)."""
    magnitudes = np.asarray(magnitudes, dtype=float)
    return magnitudes * np.where(np.arange(len(magnitudes)) % 2, 1.0, -1.0)


def table_of(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Lignes journalières de plusieurs paires, comme dans l'entraînement glissant (BTC sert de marché)."""
    return pd.concat([vol.daily_frame(frame, series["BTCUSDT"]).assign(symbol=symbol)
                      for symbol, frame in series.items()], ignore_index=True)


def fast_trees(monkeypatch) -> None:
    """Moins d'arbres pour la vitesse des tests ; les réglages réels sont vérifiés à part."""
    for name, value in FAST_TREES.items():
        monkeypatch.setattr(vol, name, value)


# --- Protocole déclaré -----------------------------------------------------------------------------------------

def test_declared_constants_match_the_document():
    assert vol.HORIZONS == (1, 3, 7) and vol.PROTOCOL_VERSION == 1
    assert vol.MODELS == ("M0_RECENT_7D", "M1_EWMA", "M2_HAR_PAIR", "M3_HAR_POOLED", "M4_HAR_POOLED_BTC",
                          "M5_LGBM_POOLED")
    assert vol.N_TRIALS == 15 and pytest.approx(1 - 0.05 / 15) == vol.LEVEL
    assert vol.FIRST_FORECAST == "2019-01-01" and vol.MIN_HISTORY_DAYS == 400
    assert (vol.DAY_HOURS, vol.WEEK_HOURS, vol.MONTH_HOURS, vol.EWMA_LAMBDA) == (24, 168, 720, 0.94)
    assert vol.LGBM_ROUNDS == 300 and vol.LGBM_PARAMS == {"objective": "regression", "num_leaves": 15,
                                                          "learning_rate": 0.05, "min_data_in_leaf": 200}
    assert (vol.MIN_TRAIN_ROWS, vol.MIN_BLOCKS, vol.MIN_YEARS_BETTER, vol.MIN_PAIRS_SHARE) == (100, 20, 6, 0.70)
    assert vol.YEARS == (2019, 2020, 2021, 2022, 2023, 2024, 2025)
    assert [vol.block_days_for(h) for h in vol.HORIZONS] == [10, 10, 14]
    assert vol.AUDIT_PAIRS == ("BTCUSDT", "ETHUSDT", "SOLUSDT") and vol.AUDIT_ORIGINS == 3
    assert pd.Timedelta(days=2) == vol.END_TOLERANCE and len(RESEARCH_UNIVERSE) == 40
    assert vol.FEATURES == ("log_var_d", "log_var_w", "log_var_m", "btc_log_var_d", "btc_log_var_w",
                            "btc_log_var_m", "dow")
    raw = DOC.read_text(encoding="utf-8")
    versions = [int(v) for v in re.findall(r"^- 2026-\d\d-\d\d, v(\d+)", raw, flags=re.MULTILINE)]
    assert versions and max(versions) == vol.PROTOCOL_VERSION
    doc = " ".join(raw.split())
    for text in ("déclaré le 2026-10-01 **avant toute exécution**", "λ = 0,94", "300 arbres", "num_leaves 15",
                 "min_child_samples 200", "400 jours d'historique", "2019-01-01", "15 comparaisons déclarées",
                 "99,67 %", "au moins 6 années civiles sur 7", "au moins 70 % des paires",
                 "100 lignes d'entraînement", "origine + H jours ≤ date de réajustement", "moyenne de exp(résidu)",
                 "max(10, 2·H) jours", "PREVISION_UTILE", "AUCUNE_AMELIORATION", *vol.MODELS, *vol.FEATURES):
        assert text in doc, text


# --- Cible et variables, calculées à la main -------------------------------------------------------------------

def test_target_is_the_realized_variance_of_the_hours_that_follow_the_origin():
    # Deux jours à 1 % par heure (47 rendements après la bougie de base), puis trois jours à 2 %.
    frame = vol.daily_frame(candles(alternating(np.r_[np.full(47, 0.01), np.full(72, 0.02)]))).set_index("origin")
    assert list(frame.index) == list(pd.date_range("2024-01-02", "2024-01-06", tz="UTC"))
    second, third, fourth = (frame.loc[day(text)] for text in ("2024-01-02", "2024-01-03", "2024-01-04"))
    # Origine du 3 janvier à 00:00 : les 24 heures passées sont à 1 %, les 72 suivantes à 2 %.
    assert third["decision_available_at"] == pd.Timestamp("2024-01-03 00:00:02", tz="UTC")
    assert third["var_d"] == pytest.approx(0.01 ** 2) and third["log_var_d"] == pytest.approx(np.log(1e-4))
    assert third["rv2_1"] == pytest.approx(24 * 0.02 ** 2)
    assert np.sqrt(third["rv2_1"]) == pytest.approx(0.02 * np.sqrt(24))                 # RV_1 ≈ 9,8 %
    assert third["rv2_3"] == pytest.approx(72 * 0.02 ** 2) and np.isnan(third["rv2_7"])
    # Origine du 2 janvier : la cible à 3 jours mêle un jour à 1 % et deux jours à 2 %.
    assert second["rv2_1"] == pytest.approx(24 * 1e-4)
    assert second["rv2_3"] == pytest.approx(24 * 1e-4 + 48 * 4e-4)
    # 23 rendements sur 24 (la bougie de base n'en a pas) : au moins 95 % de la fenêtre, moyenne des heures connues.
    assert second["var_d"] == pytest.approx(0.01 ** 2)
    # La cible ne déborde jamais des données : 3 jours après le 4 janvier, il n'y en a plus.
    assert fourth["rv2_1"] == pytest.approx(24 * 4e-4) and np.isnan(fourth["rv2_3"])
    assert frame[["rv2_1", "rv2_3", "rv2_7"]].iloc[-1].isna().all()
    assert third["dow"] == 2.0 and third["history_days"] == 2.0     # un mercredi, 2 jours après la 1re bougie


def test_variables_and_the_two_simple_forecasts_match_a_hand_computation():
    # Rendements des bougies 1 à 575 : 3 % ; 576 à 719 (6 jours) : 2 % ; 720 à 743 (1 jour) : 1 % ; puis 5 %.
    magnitudes = np.r_[np.full(575, 0.03), np.full(144, 0.02), np.full(24, 0.01), np.full(24, 0.05)]
    frame = vol.daily_frame(candles(alternating(magnitudes))).set_index("origin")
    row = frame.loc[day("2024-02-01")]                               # clôture de la bougie 743
    week = (144 * 0.02 ** 2 + 24 * 0.01 ** 2) / 168
    month = (552 * 0.03 ** 2 + 144 * 0.02 ** 2 + 24 * 0.01 ** 2) / 720
    assert row["var_d"] == pytest.approx(0.01 ** 2) and row["var_w"] == pytest.approx(week)
    assert row["var_m"] == pytest.approx(month)
    assert [row[name] for name in vol.OWN] == pytest.approx([np.log(1e-4), np.log(week), np.log(month)])
    assert row["rv2_1"] == pytest.approx(24 * 0.05 ** 2)             # le jour SUIVANT, absent des variables
    # La veille : 719 rendements sur 720, au-dessus des 95 % exigés (684) : moyenne des heures connues.
    assert frame.loc[day("2024-01-31"), "var_m"] == pytest.approx((575 * 0.03 ** 2 + 144 * 0.02 ** 2) / 719)
    assert np.isnan(frame.loc[day("2024-01-30"), "var_m"])           # moins de 720 heures depuis la 1re bougie
    assert frame["log_var_m"].notna().sum() == 3                     # 31 janvier, 1er et 2 février
    # M0 : variance horaire moyenne des 168 dernières heures × 24·H.
    last = frame.loc[[day("2024-02-01")]]
    assert vol.recent_forecast(last, 1) == pytest.approx([week * 24])
    assert vol.recent_forecast(last, 7) == pytest.approx([week * 168])
    # M1 : variance journalière 0,0216 pendant 23 jours, puis 0,0096 six jours, puis 0,0024, lissée à 0,94.
    state = 0.0096 + (0.0216 - 0.0096) * 0.94 ** 6
    state = 0.94 * state + 0.06 * 0.0024
    assert frame.loc[day("2024-01-02"), "ewma_var"] == pytest.approx(0.0216)   # 23 heures sur 24 : premier état
    assert frame.loc[day("2024-01-25"), "ewma_var"] == pytest.approx(0.0216)
    assert row["ewma_var"] == pytest.approx(state)
    assert vol.ewma_forecast(last, 3) == pytest.approx([3 * state])
    assert vol.ewma(np.array([np.nan, 1.0, np.nan, 2.0])).tolist() == pytest.approx(
        [np.nan, 1.0, 1.0, 0.94 * 1.0 + 0.06 * 2.0], nan_ok=True)   # un jour manquant laisse l'état inchangé


def test_a_window_that_is_not_contiguous_has_no_target_and_no_variable():
    data = candles(alternating(np.full(80 * 24, 0.01)))
    gap = data.drop(index=40 * 24 + 12)                              # la bougie du 2024-02-10 à 12:00 manque
    frame = vol.daily_frame(gap).set_index("origin")
    assert len(frame) == 80                                          # les origines existent toujours
    # Cibles : toute fenêtre qui contient l'heure manquante (ou la suivante, sans rendement) est vide.
    assert np.isnan(frame.loc[day("2024-02-10"), "rv2_1"])
    assert frame.loc[day("2024-02-09"), "rv2_1"] == pytest.approx(24e-4)
    assert frame.loc[day("2024-02-11"), "rv2_1"] == pytest.approx(24e-4)
    missing_week = frame.index[frame["rv2_7"].isna() & (frame.index <= day("2024-03-01"))]
    assert list(missing_week) == list(pd.date_range("2024-02-04", "2024-02-10", tz="UTC"))
    # Variables (v1 complétée : au moins 95 % des heures de la fenêtre) : l'heure manquante et la suivante (sans
    # rendement) retirent 2 heures. Le jour qui les contient (22/24) n'a pas de variable ; la semaine (166/168) et
    # le mois (718/720) restent connus : une maintenance d'une heure ne retire plus un mois d'origines.
    assert np.isnan(frame.loc[day("2024-02-11"), "log_var_d"]) and np.isfinite(frame.loc[day("2024-02-12"), "log_var_d"])
    assert frame.loc[day("2024-02-11"), "var_w"] == pytest.approx(1e-4) and np.isfinite(frame.loc[day("2024-02-17"), "log_var_w"])
    known_month = frame.index[frame["log_var_m"].notna()]
    assert list(known_month) == list(pd.date_range("2024-01-31", "2024-03-21", tz="UTC"))
    both = vol.daily_frame(gap, gap)                                 # le même trou dans BTC : mêmes jours perdus
    assert list(both.loc[vol.complete_rows(both), "origin"]) == [d for d in known_month if d != day("2024-02-11")]
    # Une interruption de deux jours retire, elle, toutes les fenêtres « jour » et « semaine » qui la contiennent.
    outage = data[~data["open_time"].between(pd.Timestamp("2024-02-10", tz="UTC"), pd.Timestamp("2024-02-11 23:00", tz="UTC"))]
    weekly = vol.daily_frame(outage).set_index("origin")["log_var_w"]
    assert weekly.loc[day("2024-02-12"):day("2024-02-17")].isna().all() and np.isfinite(weekly.loc[day("2024-02-19")])
    # Un jour sans bougie de 23:00 n'a pas d'origine.
    no_close = vol.daily_frame(data.drop(index=47))
    assert day("2024-01-03") not in set(no_close["origin"]) and len(no_close) == 79


def test_a_zero_variance_is_neither_a_variable_nor_a_usable_target():
    magnitudes = np.full(40 * 24, 0.01)
    magnitudes[35 * 24 - 1:36 * 24 - 1] = 0.0                        # prix immobile toute la journée du 5 février
    series = candles(alternating(magnitudes))
    frame = vol.daily_frame(series, series).assign(symbol="ETHUSDT").set_index("origin", drop=False)
    assert frame.loc[day("2024-02-05"), "rv2_1"] == 0.0              # cible nulle : ni log ni QLIKE fini
    assert frame.loc[day("2024-02-06"), "var_d"] == 0.0 and np.isnan(frame.loc[day("2024-02-06"), "log_var_d"])
    complete = vol.complete_rows(frame)
    assert complete.loc[day("2024-02-05")] and not complete.loc[day("2024-02-06")]
    train = vol.training_rows(frame, day("2024-02-09"), 1)
    assert day("2024-02-04") in train.index and day("2024-02-07") in train.index
    assert day("2024-02-05") not in train.index and day("2024-02-06") not in train.index


def test_btc_variables_are_those_of_the_same_day_and_only_once_available():
    pair, btc = hourly("ETHUSDT", days=45, seed=1), hourly("BTCUSDT", days=45, seed=2)
    frame, market = vol.daily_frame(pair, btc), vol.daily_frame(btc)
    assert frame["origin"].equals(market["origin"])
    np.testing.assert_array_equal(frame[list(vol.MARKET_FEATURES)].to_numpy(), market[list(vol.OWN)].to_numpy())
    assert np.isfinite(frame[list(vol.FEATURES)].to_numpy()).all(axis=1).sum() == 16     # origines 30 à 45 (719/720 h)
    own = vol.daily_frame(btc, btc)                                   # BTC : ses propres variables
    np.testing.assert_array_equal(own[list(vol.MARKET_FEATURES)].to_numpy(), own[list(vol.OWN)].to_numpy())
    # Une bougie BTC connue 2 h plus tard n'est pas disponible à la décision : variable manquante, et jamais
    # remplacée par celle de la veille.
    late = btc.assign(available_at=btc["available_at"] + pd.Timedelta(hours=2))
    assert vol.daily_frame(pair, late)[list(vol.MARKET_FEATURES)].isna().all().all()
    assert vol.daily_frame(pair)[list(vol.MARKET_FEATURES)].isna().all().all()
    assert not vol.complete_rows(vol.daily_frame(pair)).any()         # sans BTC, aucune ligne complète


# --- Causalité -------------------------------------------------------------------------------------------------

def test_variables_are_causal_and_a_one_hour_look_ahead_is_detected():
    pair, btc = hourly("ETHUSDT", days=60, seed=3), hourly("BTCUSDT", days=60, seed=4)
    origins = [vol.daily_frame(pair, btc)["origin"].iloc[i] for i in (33, 44, 57)]
    assert vol.causality_violations(pair, btc, origins=origins, seed=5) == []
    assert vol.causality_violations(btc, btc, origins=origins, seed=5) == []
    # MUTATION : la variable « jour » lit la première heure après l'origine. Les deux contrôles la voient, à
    # chaque origine, et n'accusent que ce qui en dépend (la variable et le lissage de M1).
    found = vol.causality_violations(pair, btc, origins=origins, seed=5, leaky=True)
    assert [(v["origin"], v["check"]) for v in found] == [(str(o), c) for o in origins
                                                           for c in ("tronqué", "futur falsifié")]
    assert all(v["features"] == ["log_var_d", "ewma_var"] for v in found)
    absent = vol.causality_violations(pair, btc, origins=[day("2030-01-01")])
    assert absent == [{"origin": str(day("2030-01-01")), "check": "calcul complet", "features": ["(origine absente)"]}]


# --- Pertes ----------------------------------------------------------------------------------------------------

def test_qlike_is_zero_for_an_exact_forecast_and_grows_with_the_error():
    assert vol.qlike(0.0123, 0.0123) == 0 and vol.log_error(0.0123, 0.0123) == 0
    factors = np.array([1.1, 1.5, 2.0, 4.0, 10.0])
    over, under = vol.qlike(1.0, factors), vol.qlike(1.0, 1 / factors)
    assert (over > 0).all() and (np.diff(over) > 0).all() and (np.diff(under) > 0).all()
    assert (under > over).all()                                       # sous-estimer coûte plus cher
    assert vol.qlike(2.0, 1.0) == pytest.approx(2 - np.log(2) - 1)   # RV²/F = 2
    assert vol.qlike(3e-4, 6e-4) == pytest.approx(0.5 - np.log(0.5) - 1)
    assert vol.log_error(4.0, 1.0) == pytest.approx(np.log(2) ** 2)  # RV = 2 contre 1 prévu
    assert vol.log_error(1.0, 4.0) == pytest.approx(np.log(2) ** 2)  # symétrique en log
    assert not np.isfinite(vol.qlike(0.0, 1.0)) and not np.isfinite(vol.qlike(1.0, 0.0))


# --- Modèles ---------------------------------------------------------------------------------------------------

def test_har_recovers_known_coefficients_and_returns_to_the_variance_scale():
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (800, 3))
    truth = np.array([-9.0, 0.4, 0.35, 0.2])                          # constante, jour, semaine, mois
    exact = vol.fit_har(X, truth[0] + X @ truth[1:])
    assert exact.coef == pytest.approx(truth, abs=1e-9) and exact.smearing == pytest.approx(1.0)
    assert exact.predict(X[:5]) == pytest.approx(np.exp(truth[0] + X[:5] @ truth[1:]))
    y = truth[0] + X @ truth[1:] + rng.normal(0, 0.5, 800)
    model = vol.fit_har(X, y)
    assert model.coef == pytest.approx(truth, abs=0.08) and model.rows == 800
    # Retour à la variance : exp(log prévu) vise la médiane ; le facteur ramène la moyenne de RV²/F à 1.
    naive = np.exp(np.column_stack([np.ones(800), X]) @ model.coef)
    assert np.mean(np.exp(y) / naive) == pytest.approx(model.smearing) and model.smearing > 1.1
    assert model.smearing == pytest.approx(np.exp(0.5 ** 2 / 2), abs=0.03)       # ≈ 1,13 pour un bruit gaussien
    assert np.mean(np.exp(y) / model.predict(X)) == pytest.approx(1.0)
    assert np.mean(vol.qlike(np.exp(y), model.predict(X))) < np.mean(vol.qlike(np.exp(y), naive))
    assert vol.fit_har(X[:99], y[:99]) is None and vol.fit_har(X[:100], y[:100]) is not None


def test_lightgbm_uses_the_declared_settings_and_is_reproducible():
    rng = np.random.default_rng(1)
    X = rng.normal(0, 1, (3000, 7))
    y = -9.0 + np.where(X[:, 0] > 0, 1.0, -1.0) * X[:, 1] + rng.normal(0, 0.3, 3000)     # non linéaire
    model, again = vol.fit_lgbm(X, y, seed=7), vol.fit_lgbm(X, y, seed=7)
    assert model.booster.num_trees() == 300 and model.rows == 3000
    assert {k: model.booster.params[k] for k in vol.LGBM_PARAMS} == vol.LGBM_PARAMS
    assert model.booster.params["seed"] == 7 and model.booster.params["deterministic"]
    np.testing.assert_array_equal(model.predict(X), again.predict(X))
    assert np.mean(np.exp(y) / model.predict(X)) == pytest.approx(1.0)           # même retour à la variance
    assert np.corrcoef(np.log(model.predict(X)), y)[0, 1] > 0.9                  # il apprend l'interaction
    assert vol.fit_lgbm(X[:99], y[:99], seed=7) is None


# --- Purge et entraînement glissant ----------------------------------------------------------------------------

def test_training_rows_are_purged_at_the_refit_date(monkeypatch):
    fast_trees(monkeypatch)
    refit = day("2024-08-01")
    series = {symbol: hourly(symbol, days=260, seed=index) for index, symbol in enumerate(PAIRS[:3])}
    data = table_of(series)
    for horizon in vol.HORIZONS:
        ends = vol.training_rows(data, refit, horizon)["origin"] + pd.Timedelta(days=horizon)
        assert ends.max() == refit                                    # la dernière cible connue est gardée
    # Futur falsifié à partir de la date de réajustement : aucun modèle ajusté à cette date ne bouge.
    rng = np.random.default_rng(1)
    falsified = {}
    for symbol, frame in series.items():
        later = (frame["open_time"] >= refit).to_numpy()
        falsified[symbol] = frame.assign(close=np.where(later, frame["close"] * rng.uniform(0.5, 1.5, len(frame)),
                                                        frame["close"]))
    changed = table_of(falsified)
    sample = data.loc[vol.complete_rows(data), list(vol.FEATURES)].to_numpy(float)[:60]
    for horizon in vol.HORIZONS:
        # Le test a des dents : les cibles qui dépassent la date de réajustement ont bien changé.
        crossing = (data["origin"] > refit - pd.Timedelta(days=horizon)) & (data["origin"] <= refit)
        assert crossing.sum() == 3 * horizon
        assert not np.isclose(data.loc[crossing, f"rv2_{horizon}"], changed.loc[crossing, f"rv2_{horizon}"]).any()
        before, after = (vol.fit_at(table, refit, horizon, seed=11) for table in (data, changed))
        assert before.rows == after.rows > 300 and set(before.by_pair) == set(series)
        for one, other in ((before.pooled, after.pooled), (before.pooled_btc, after.pooled_btc),
                           *((before.by_pair[s], after.by_pair[s]) for s in series)):
            np.testing.assert_array_equal(one.coef, other.coef)
            assert one.smearing == other.smearing and one.rows == other.rows
        np.testing.assert_array_equal(before.trees.predict(sample), after.trees.predict(sample))


def test_walk_forward_follows_the_declared_calendar(settings, monkeypatch):
    """Constantes RÉELLES (400 jours, 2019-01-01, 100 lignes) ; seuls les arbres sont réduits."""
    monkeypatch.setattr(vol, "LGBM_ROUNDS", 20)
    settings.protocol.development_end = datetime(2019, 6, 30, 23, 59, 59, tzinfo=UTC)
    series = {"BTCUSDT": hourly("BTCUSDT", days=640, seed=1, start="2017-10-01"),
              "ETHUSDT": hourly("ETHUSDT", days=640, seed=2, start="2017-10-01"),
              "XRPUSDT": hourly("XRPUSDT", days=489, seed=3, start="2018-03-01")}      # jusqu'au 2019-07-03
    frames = {symbol: vol.daily_frame(frame, series["BTCUSDT"]) for symbol, frame in series.items()}
    forecasts = vol.walk_forward(frames, settings)
    assert list(forecasts.columns) == list(vol.FORECAST_COLUMNS)
    assert set(forecasts["model"]) == set(vol.MODELS) and set(forecasts["horizon"]) == set(vol.HORIZONS)
    assert (forecasts.groupby(["symbol", "origin", "horizon"])["model"].nunique() == 6).all()
    assert np.isfinite(forecasts[["forecast", "realized"]].to_numpy()).all() and (forecasts["forecast"] > 0).all()
    first = forecasts.groupby("symbol")["origin"].min()
    assert first["BTCUSDT"] == first["ETHUSDT"] == day("2019-01-01")              # première prévision déclarée
    assert first["XRPUSDT"] == day("2018-03-01") + pd.Timedelta(days=400) == day("2019-04-05")
    # Dernière origine : la cible se termine avec la dernière bougie de DEVELOPMENT, alors que les bougies
    # fournies vont jusqu'au 3 juillet.
    last = forecasts.groupby("horizon")["origin"].max()
    assert {h: last[h] for h in vol.HORIZONS} == {1: day("2019-06-30"), 3: day("2019-06-28"), 7: day("2019-06-24")}
    # Le modèle ajusté le 1er mars prévoit tout le mois de mars ; il n'est pas réajusté chaque jour.
    data = pd.concat([frame.assign(symbol=symbol) for symbol, frame in frames.items()], ignore_index=True)
    march = forecasts[(forecasts["horizon"] == 3) & (forecasts["symbol"] == "ETHUSDT")
                      & (forecasts["origin"] >= day("2019-03-01")) & (forecasts["origin"] < day("2019-04-01"))]
    rows = frames["ETHUSDT"].set_index("origin").loc[march.loc[march["model"] == "M3_HAR_POOLED", "origin"]]
    assert len(rows) == 31

    def of(model: str) -> np.ndarray:
        return march.loc[march["model"] == model, "forecast"].to_numpy(float)

    seed = settings.protocol.seed
    fitted, later = (vol.fit_at(data, day(text), 3, seed=seed) for text in ("2019-03-01", "2019-04-01"))
    own, with_btc = rows[list(vol.OWN)].to_numpy(float), rows[[*vol.OWN, *vol.MARKET_FEATURES]].to_numpy(float)
    np.testing.assert_allclose(of("M3_HAR_POOLED"), fitted.pooled.predict(own), rtol=1e-12)
    np.testing.assert_allclose(of("M4_HAR_POOLED_BTC"), fitted.pooled_btc.predict(with_btc), rtol=1e-12)
    np.testing.assert_allclose(of("M2_HAR_PAIR"), fitted.by_pair["ETHUSDT"].predict(own), rtol=1e-12)
    np.testing.assert_allclose(of("M5_LGBM_POOLED"), fitted.trees.predict(rows[list(vol.FEATURES)].to_numpy(float)),
                               rtol=1e-12)
    assert not np.allclose(of("M3_HAR_POOLED"), later.pooled.predict(own), rtol=1e-6)
    # Variables déclarées : 3 pour les HAR par paire et commun, 6 avec BTC, 7 pour LightGBM ; mêmes lignes communes.
    assert [len(m.coef) for m in (fitted.by_pair["ETHUSDT"], fitted.pooled, fitted.pooled_btc)] == [4, 4, 7]
    assert fitted.trees.booster.num_feature() == 7
    assert fitted.by_pair["ETHUSDT"].rows < fitted.pooled.rows == fitted.pooled_btc.rows == fitted.trees.rows == fitted.rows
    np.testing.assert_allclose(of("M0_RECENT_7D"), rows["var_w"].to_numpy(float) * 72, rtol=1e-12)
    np.testing.assert_allclose(of("M1_EWMA"), rows["ewma_var"].to_numpy(float) * 3, rtol=1e-12)
    np.testing.assert_array_equal(march.loc[march["model"] == "M0_RECENT_7D", "realized"].to_numpy(float),
                                  rows["rv2_3"].to_numpy(float))
    # L'entraînement du 1er janvier 2019 contient les lignes des 400 premiers jours des paires.
    january = vol.training_rows(data, day("2019-01-01"), 1)
    assert set(january["symbol"]) == set(series) and (january["history_days"] < 400).any()
    assert january["origin"].min() == day("2017-10-31")               # 720 heures (719 rendements), pas 400 jours


def test_an_origin_is_scored_only_when_all_six_models_forecast_it(settings, monkeypatch):
    fast_trees(monkeypatch)
    monkeypatch.setattr(vol, "MIN_HISTORY_DAYS", 40)
    monkeypatch.setattr(vol, "FIRST_FORECAST", "2024-08-10")
    settings.protocol.development_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    series = {"BTCUSDT": hourly("BTCUSDT", days=400, seed=1), "ADAUSDT": hourly("ADAUSDT", days=248, seed=2,
                                                                                start="2024-06-01")}
    frames = {symbol: vol.daily_frame(frame, series["BTCUSDT"]) for symbol, frame in series.items()}
    forecasts = vol.walk_forward(frames, settings)
    first = forecasts.groupby(["symbol", "horizon"])["origin"].min()
    # Aucune prévision avant la date déclarée, même en cours de mois ; le modèle est alors celui du 1er du mois.
    assert all(first[("BTCUSDT", h)] == day("2024-08-10") for h in vol.HORIZONS)
    # ADA a ses variables dès le 1er juillet (720 heures de cotation) et 40 jours d'historique le 11 juillet, mais son HAR par paire n'a
    # 100 lignes d'entraînement qu'au réajustement de novembre : avant, AUCUN modèle n'est évalué sur ADA.
    ada = frames["ADAUSDT"]
    assert ada.loc[vol.complete_rows(ada), "origin"].min() == day("2024-07-01")
    assert all(first[("ADAUSDT", h)] == day("2024-11-01") for h in vol.HORIZONS)
    data = pd.concat([frame.assign(symbol=symbol) for symbol, frame in frames.items()], ignore_index=True)
    assert "ADAUSDT" not in vol.fit_at(data, day("2024-10-01"), 1, seed=1).by_pair
    assert vol.fit_at(data, day("2024-11-01"), 1, seed=1).by_pair["ADAUSDT"].rows == 123     # ses lignes : 1er juillet → 31 octobre
    tenth = forecasts[(forecasts["origin"] == day("2024-08-10")) & (forecasts["horizon"] == 1)
                      & (forecasts["model"] == "M3_HAR_POOLED")]
    august = vol.fit_at(data, day("2024-08-01"), 1, seed=1)
    known = frames["BTCUSDT"].set_index("origin").loc[[day("2024-08-10")], list(vol.OWN)].to_numpy(float)
    assert list(tenth["symbol"]) == ["BTCUSDT"] and tenth["forecast"].to_numpy() == pytest.approx(
        august.pooled.predict(known), rel=1e-12)


# --- Règle « PREVISION_UTILE » (cas construits) ----------------------------------------------------------------

DAYS = pd.date_range("2019-01-01", "2025-06-30", freq="D", tz="UTC")
YEAR = DAYS.year.to_numpy()


def crafted(diff, *, pairs: int = 10, log_diff: float = -0.01) -> pd.DataFrame:
    """Pertes construites du 2019-01-01 au 2025-06-30 : `diff[jour, paire]` = QLIKE du modèle − QLIKE de M0."""
    diff = np.broadcast_to(np.asarray(diff, dtype=float), (len(DAYS), pairs))
    return pd.DataFrame({"symbol": np.tile([f"P{i}" for i in range(pairs)], len(DAYS)), "origin": DAYS.repeat(pairs),
                         "qlike_base": 0.5, "qlike": 0.5 + diff.ravel(), "log_error_base": 0.2,
                         "log_error": 0.2 + log_diff})


def criteria(**failed: bool) -> dict[str, bool]:
    return {"ci_upper_below_zero": True, "years": True, "pairs": True, "secondary_loss": True} | failed


def test_each_criterion_alone_rejects_a_model():
    rng = np.random.default_rng(0)
    good = vol._row("M3_HAR_POOLED", 1, crafted(-0.02 + rng.normal(0, 0.05, (len(DAYS), 10))))
    assert good.useful and good.criteria == criteria() and good.ci_qlike_diff[1] < 0
    assert (good.forecasts, good.days, good.pairs) == (len(DAYS) * 10, len(DAYS), 10)
    assert good.years_better == 7 and good.pairs_better_share == 1.0 and good.log_error_diff == pytest.approx(-0.01)
    assert list(good.qlike_diff_by_year) == [str(y) for y in vol.YEARS]

    # 1. IC : mieux en moyenne, chaque année et pour chaque paire, mais trop irrégulier pour l'affirmer
    #    (trois oscillations par année, de somme nulle sur l'année).
    sizes = pd.Series(YEAR).value_counts().sort_index()
    position = np.concatenate([np.arange(n) / n for n in sizes])
    swinging = vol._row("M3_HAR_POOLED", 1, crafted((-0.01 + np.sin(2 * np.pi * 3 * position))[:, None]))
    assert swinging.criteria == criteria(ci_upper_below_zero=False) and not swinging.useful
    assert swinging.qlike_diff == pytest.approx(-0.01) and swinging.ci_qlike_diff[0] < 0 < swinging.ci_qlike_diff[1]

    # 2. Années : IC entièrement sous 0, mais M0 fait mieux deux années sur sept.
    two_years = vol._row("M3_HAR_POOLED", 1, crafted(np.where(YEAR <= 2020, 0.001, -0.05)[:, None]))
    assert two_years.criteria == criteria(years=False) and two_years.years_better == 5 and not two_years.useful
    one_year = vol._row("M3_HAR_POOLED", 1, crafted(np.where(YEAR == 2019, 0.001, -0.05)[:, None]))
    assert one_year.useful and one_year.years_better == 6            # 6 années sur 7 suffisent

    # 3. Paires : mieux pour 6 paires sur 10 seulement.
    six = vol._row("M3_HAR_POOLED", 1, crafted(np.where(np.arange(10) < 6, -0.05, 0.001)[None, :]))
    assert six.criteria == criteria(pairs=False) and six.pairs_better_share == 0.6 and not six.useful
    seven = vol._row("M3_HAR_POOLED", 1, crafted(np.where(np.arange(10) < 7, -0.05, 0.001)[None, :]))
    assert seven.useful and seven.pairs_better_share == 0.7          # 70 % suffisent

    # 4. Perte secondaire : meilleur QLIKE, mais erreur de log RV plus forte.
    secondary = vol._row("M3_HAR_POOLED", 1, crafted(-0.02, log_diff=0.01))
    assert secondary.criteria == criteria(secondary_loss=False) and not secondary.useful

    # Moins de 20 blocs : pas d'IC, donc pas de modèle utile.
    table = crafted(-0.02)
    short = vol._row("M3_HAR_POOLED", 1, table[table["origin"] < day("2019-07-01")])
    assert short.ci_qlike_diff is None and not short.criteria["ci_upper_below_zero"] and not short.useful
    empty = vol._row("M3_HAR_POOLED", 1, table.iloc[:0])
    assert empty.forecasts == 0 and not empty.useful and not any(empty.criteria.values())


def test_interval_uses_one_value_per_day_calendar_blocks_and_the_bonferroni_level():
    rng = np.random.default_rng(1)
    table = crafted(-0.02 + rng.normal(0, 0.05, (len(DAYS), 10)))
    daily = (table["qlike"] - table["qlike_base"]).groupby(table["origin"]).mean()
    rows = {}
    for horizon, block in ((1, 10), (3, 10), (7, 14)):
        rows[horizon] = vol._row("M1_EWMA", horizon, table)
        assert rows[horizon].ci_qlike_diff == calendar_mean_ci(daily.to_numpy(), daily.index, block_days=block,
                                                               min_blocks=20, level=1 - 0.05 / 15)
    loose = calendar_mean_ci(daily.to_numpy(), daily.index, block_days=10, min_blocks=20, level=0.95)
    assert rows[1].ci_qlike_diff[0] < loose[0] < loose[1] < rows[1].ci_qlike_diff[1]    # plus large qu'à 95 %
    # Un jour = une valeur : en 2019, deux paires seulement, où M0 fait mieux ; ensuite dix paires.
    early = table[(table["origin"] >= day("2020-01-01")) | table["symbol"].isin(["P0", "P1"])].copy()
    early["qlike"] = np.where(early["origin"] < day("2020-01-01"), 0.53, 0.48)
    row = vol._row("M1_EWMA", 1, early)
    by_day = (365 * 0.03 + (len(DAYS) - 365) * -0.02) / len(DAYS)
    by_forecast = (730 * 0.03 + (len(early) - 730) * -0.02) / len(early)
    assert row.qlike_diff == pytest.approx(by_day, abs=1e-6) and abs(by_day - by_forecast) > 0.005
    assert row.qlike_diff_by_year["2019"] == pytest.approx(0.03) and row.years_better == 6


def forecast_table(values: dict[str, float], *, horizon: int = 1, pairs=("A", "B", "C")) -> pd.DataFrame:
    """Prévisions constantes par modèle, variance réalisée 1, pour chaque jour de 2019-01-01 à 2025-06-30."""
    rows = [(symbol, origin, horizon, model, forecast, 1.0)
            for origin in DAYS for symbol in pairs for model, forecast in values.items()]
    return pd.DataFrame(rows, columns=list(vol.FORECAST_COLUMNS))


def test_evaluation_compares_each_model_to_the_reference_and_keeps_the_best_useful_one():
    table = forecast_table({"M0_RECENT_7D": 2.0, "M1_EWMA": 1.5, "M2_HAR_PAIR": 4.0, "M3_HAR_POOLED": 1.0,
                            "M4_HAR_POOLED_BTC": 1.25, "M5_LGBM_POOLED": 2.0})
    rows = vol.evaluate(table)
    assert [(r.model, r.horizon_days) for r in rows] == [(m, h) for h in vol.HORIZONS for m in vol.MODELS[1:]]
    one = {r.model: r for r in rows if r.horizon_days == 1}
    reference = 0.5 + np.log(2.0) - 1                                 # M0 prévoit le double de la variance
    assert all(r.qlike_baseline == pytest.approx(reference, abs=1e-6) for r in one.values())
    assert one["M3_HAR_POOLED"].qlike == 0 and one["M3_HAR_POOLED"].qlike_diff == pytest.approx(-reference, abs=1e-6)
    assert one["M1_EWMA"].qlike == pytest.approx(1 / 1.5 + np.log(1.5) - 1, abs=1e-6)
    assert one["M1_EWMA"].log_error_diff == pytest.approx((np.log(1.5) ** 2 - np.log(2) ** 2) / 4, abs=1e-6)
    assert [m for m, r in one.items() if r.useful] == ["M1_EWMA", "M3_HAR_POOLED", "M4_HAR_POOLED_BTC"]
    assert one["M2_HAR_PAIR"].qlike_diff > 0 and not any(one["M2_HAR_PAIR"].criteria.values())
    assert one["M5_LGBM_POOLED"].qlike_diff == 0 and not one["M5_LGBM_POOLED"].useful     # égal n'est pas mieux
    assert all(r.forecasts == 0 and not r.useful for r in rows if r.horizon_days != 1)
    assert all(r.forecasts == len(DAYS) * 3 and r.pairs == 3 for r in one.values())
    selected, verdict = vol.decide(rows)
    assert selected == {1: "M3_HAR_POOLED", 3: NONE, 7: NONE} and verdict == "PREVISION_UTILE"
    without = [r for r in rows if r.model in ("M2_HAR_PAIR", "M5_LGBM_POOLED")]
    assert vol.decide(without) == ({1: NONE, 3: NONE, 7: NONE}, "AUCUNE_AMELIORATION")
    # Un modèle utile à un seul horizon suffit au verdict global ; chaque horizon garde son meilleur.
    week = vol.evaluate(forecast_table({"M0_RECENT_7D": 2.0, "M1_EWMA": 1.5, "M4_HAR_POOLED_BTC": 1.9}, horizon=7))
    assert vol.decide(week) == ({1: NONE, 3: NONE, 7: "M1_EWMA"}, "PREVISION_UTILE")


# --- Protocole de bout en bout (petit univers synthétique) -----------------------------------------------------

@pytest.fixture
def stored(settings, monkeypatch):
    store = CandleStore(long_settings(settings).data_dir)
    for index, symbol in enumerate(PAIRS):                            # 2023-10-10 → 2025-02-10
        store.save(hourly(symbol, days=490, seed=index, start="2023-10-10"), symbol, "1h")
    settings.protocol.development_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC)
    # Seuils ramenés à la période synthétique (en réel : 400 jours, 2019-01-01, années 2019 à 2025, 300 arbres).
    fast_trees(monkeypatch)
    monkeypatch.setattr(vol, "MIN_HISTORY_DAYS", 60)
    monkeypatch.setattr(vol, "FIRST_FORECAST", "2024-03-01")
    monkeypatch.setattr(vol, "YEARS", (2024, 2025))
    monkeypatch.setattr(vol, "MIN_YEARS_BETTER", 2)
    monkeypatch.setattr(vol, "code_state", lambda: "0123abcd")
    return settings


def test_protocol_audits_then_records_the_fifteen_declared_comparisons(stored):
    result = vol.run(stored, now=NOW, symbols=list(PAIRS))
    audit = result.leak_audit
    assert audit["passed"] and audit["checked_pairs"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"] and not audit["violations"]
    assert audit["mutation_detected"] == {"BTCUSDT": True, "ETHUSDT": True, "SOLUSDT": True}
    assert all(len(origins) == 3 for origins in audit["origins"].values())
    assert result.n_trials == 15 == len(result.rows) and result.level == pytest.approx(1 - 0.05 / 15, abs=1e-6)
    assert [(r.model, r.horizon_days) for r in result.rows] == [(m, h) for h in vol.HORIZONS for m in vol.MODELS[1:]]
    # Origines du 2024-03-01 à la fin de DEVELOPMENT moins H jours, quatre paires par jour.
    assert {r.horizon_days: r.days for r in result.rows} == {1: 337, 3: 335, 7: 331}
    assert all(r.pairs == 4 and r.forecasts == 4 * r.days and r.ci_qlike_diff is not None for r in result.rows)
    useful = {h: [r for r in result.rows if r.horizon_days == h and r.useful] for h in vol.HORIZONS}
    assert result.selected == {h: min(rows, key=lambda r: r.qlike).model if rows else NONE
                               for h, rows in useful.items()}
    assert result.verdict == ("PREVISION_UTILE" if any(useful.values()) else "AUCUNE_AMELIORATION")
    assert set(result.data_hashes) == set(PAIRS) and len(set(result.data_hashes.values())) == 4
    assert result.coverage["pairs"]["ADAUSDT"] == {"daily_rows": 480, "complete_rows": 451, "evaluated": {
        "1": 337, "3": 335, "7": 331}, "first_evaluated": str(day("2024-03-01"))}
    assert result.coverage["series"]["ETHUSDT"]["missing_hours"] == 0

    report = stored.reports_dir / result.run_id
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    assert summary["protocol_version"] == 1 and summary["verdict"] == result.verdict and len(summary["rows"]) == 15
    assert summary["doc"] == "docs/VOLATILITY.md" and set(summary["models"]) == set(vol.MODELS)
    run = ExperimentRegistry(stored.experiments_db).get(result.run_id)
    assert run["kind"] == "VOLATILITY" and run["strategy"] == "VOLATILITY_FORECAST"
    assert run["period_label"] == "DEVELOPMENT" and run["period_end"].startswith("2025-01-31")
    assert run["period_start"].startswith("2023-10-10") and run["universe"] == list(PAIRS)
    assert run["metrics"]["n_trials"] == 15 == run["metrics"]["program_trials"] == result.program_trials
    assert run["metrics"]["verdict"] == result.verdict and len(run["metrics"]["rows"]) == 15
    assert run["metrics"]["selected"] == {str(h): model for h, model in result.selected.items()}
    assert run["data_hashes"] == result.data_hashes and run["seed"] == stored.protocol.seed
    assert run["dependencies"]["lightgbm"] != "absent" and run["status"] == "COMPLETED"
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 15

    # Vérification indépendante sur les prévisions enregistrées : une ligne recalculée sans le code de mesure.
    saved = pd.read_parquet(report / "forecasts.parquet")
    assert list(saved.columns) == list(vol.FORECAST_COLUMNS) and len(saved) == 6 * 4 * (337 + 335 + 331)
    part = saved[saved["horizon"] == 3].set_index(["origin", "symbol", "model"])
    realized = part["realized"].xs("M0_RECENT_7D", level="model")
    assert (part.index.get_level_values("origin") + pd.Timedelta(days=3) - pd.Timedelta(hours=1)
            <= pd.Timestamp(stored.protocol.development_end)).all()

    def loss(model: str) -> pd.Series:
        ratio = realized / part["forecast"].xs(model, level="model")
        return ratio - np.log(ratio) - 1

    daily = (loss("M2_HAR_PAIR") - loss("M0_RECENT_7D")).groupby(level="origin").mean()
    row = next(r for r in result.rows if (r.model, r.horizon_days) == ("M2_HAR_PAIR", 3))
    assert row.qlike_diff == pytest.approx(daily.mean(), abs=1e-6)
    assert row.qlike == pytest.approx(loss("M2_HAR_PAIR").groupby(level="origin").mean().mean(), abs=1e-6)
    assert row.ci_qlike_diff == calendar_mean_ci(daily.to_numpy(), daily.index, block_days=10, min_blocks=20,
                                                 level=vol.LEVEL)


def test_nothing_after_the_end_of_development_is_read(stored):
    first = vol.run(stored, now=NOW, symbols=list(PAIRS))
    end = pd.Timestamp(stored.protocol.development_end)
    store, rng = CandleStore(long_settings(stored).data_dir), np.random.default_rng(0)
    for symbol in PAIRS:                                              # futur falsifié dans le magasin long
        frame = store.load(symbol, "1h")
        later = (frame["open_time"] > end).to_numpy()
        assert later.sum() == 10 * 24
        for column in ("open", "high", "low", "close"):
            frame.loc[later, column] = frame.loc[later, column] * rng.uniform(0.5, 1.5, int(later.sum()))
        store.save(frame, symbol, "1h")
    second = vol.run(stored, now=NOW, symbols=list(PAIRS))
    assert [asdict(r) for r in second.rows] == [asdict(r) for r in first.rows]
    assert second.data_hashes == first.data_hashes                    # ce qui est lu n'a pas changé
    assert second.leak_audit == first.leak_audit and second.coverage == first.coverage
    assert (second.verdict, second.selected) == (first.verdict, first.selected)
    pd.testing.assert_frame_equal(*(pd.read_parquet(stored.reports_dir / r.run_id / "forecasts.parquet")
                                    for r in (first, second)))
    assert second.program_trials == 30                                # chaque exécution compte ses 15 comparaisons
    series = vol.load_series(stored, "ETHUSDT", end + pd.Timedelta(days=30))     # même en demandant plus tard
    assert series["open_time"].max() == day("2025-01-31 23:00") and list(series.columns) == list(vol.READ_COLUMNS)


def test_incomplete_data_is_refused_before_any_trial(stored):
    store = CandleStore(long_settings(stored).data_dir)
    end = pd.Timestamp(stored.protocol.development_end)
    with pytest.raises(vol.IncompleteData, match="XRPUSDT : absent du magasin long"):
        vol.run(stored, now=NOW)                                      # par défaut : les 40 paires de l'univers figé
    eth = store.load("ETHUSDT", "1h")
    store.save(eth[eth["open_time"] <= day("2025-01-29 23:00")], "ETHUSDT", "1h")       # 2 jours et 1 h avant la fin
    with pytest.raises(vol.IncompleteData, match="ETHUSDT : s'arrête avant la fin de DEVELOPMENT"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    store.save(eth[eth["open_time"] <= day("2025-01-30 00:00")], "ETHUSDT", "1h")       # dans la tolérance
    assert set(vol.check_complete(stored, list(PAIRS), end)) == set(PAIRS)
    store.path("SOLUSDT", "1h").unlink()
    with pytest.raises(vol.IncompleteData, match="SOLUSDT : absent du magasin long"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    store.path("BTCUSDT", "1h").unlink()                              # BTC est exigé même hors de la liste
    with pytest.raises(vol.IncompleteData, match="BTCUSDT : absent du magasin long"):
        vol.run(stored, now=NOW, symbols=["ETHUSDT", "ADAUSDT"])
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0 and not stored.reports_dir.exists()


def test_the_leak_audit_needs_its_three_pairs(stored):
    CandleStore(long_settings(stored).data_dir).path("SOLUSDT", "1h").unlink()
    with pytest.raises(vol.LeakAuditFailed, match=r"'checked_pairs': \['BTCUSDT', 'ETHUSDT'\]"):
        vol.run(stored, now=NOW, symbols=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0 and not stored.reports_dir.exists()


def test_nothing_is_produced_when_a_leak_exists_or_would_go_unnoticed(stored, monkeypatch):
    real = vol.daily_frame
    # « Mutation » inerte : l'audit ne prouverait plus qu'il sait voir une fuite.
    monkeypatch.setattr(vol, "daily_frame", lambda h1, btc_h1=None, *, leaky=False: real(h1, btc_h1))
    with pytest.raises(vol.LeakAuditFailed, match="mutation_detected"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    # Fuite réelle : les variables lisent l'heure qui suit l'origine.
    monkeypatch.setattr(vol, "daily_frame", lambda h1, btc_h1=None, *, leaky=False: real(h1, btc_h1, leaky=True))
    with pytest.raises(vol.LeakAuditFailed, match="log_var_d"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 0 and not stored.reports_dir.exists()


def test_btc_is_joined_backward_only():
    """BTC disponible 30 min APRÈS la décision (dans la tolérance d'une heure) : pas encore connu, variable
    manquante. Une jointure « la plus proche » ou « vers l'avant » la prendrait."""
    pair, btc = hourly("ETHUSDT", days=45, seed=1), hourly("BTCUSDT", days=45, seed=2)
    soon = btc.assign(available_at=btc["available_at"] + pd.Timedelta(minutes=30))
    assert vol.daily_frame(pair, soon)[list(vol.MARKET_FEATURES)].isna().all().all()
    earlier = btc.assign(available_at=btc["available_at"] - pd.Timedelta(minutes=30))
    assert vol.daily_frame(pair, earlier)[list(vol.MARKET_FEATURES)].notna().any().all()


def test_uncommitted_code_and_too_little_data_are_refused_before_any_record(stored, monkeypatch):
    monkeypatch.setattr(vol, "code_state", lambda: "0123abcd+DIRTY")
    with pytest.raises(vol.DirtyCode, match="DIRTY"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    monkeypatch.setattr(vol, "code_state", lambda: "0123abcd")
    monkeypatch.setattr(vol, "MIN_BLOCKS", 40)                       # 337 jours en blocs de 10 : 34 blocs < 40
    with pytest.raises(vol.IncompleteData, match="trop peu de jours"):
        vol.run(stored, now=NOW, symbols=list(PAIRS))
    assert not stored.experiments_db.exists() or ExperimentRegistry(stored.experiments_db).program_trials() == 0
    assert not list(stored.reports_dir.glob("VOL-*"))


def test_the_command_runs_the_protocol(stored, monkeypatch):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    monkeypatch.setattr(cli, "_heavy_job", lambda settings: None)
    monkeypatch.setattr(vol, "RESEARCH_UNIVERSE", PAIRS)
    monkeypatch.setenv("CSI_PROTOCOL__DEVELOPMENT_END", "2025-01-31T23:59:59Z")
    done = CliRunner().invoke(cli.app, ["volatility"])
    assert done.exit_code == 0, done.output
    output = " ".join(done.output.split())                          # rich coupe les lignes à 80 colonnes
    assert "Verdict :" in output and "ne dit rien de la direction" in output
    assert ExperimentRegistry(stored.experiments_db).program_trials() == 15
