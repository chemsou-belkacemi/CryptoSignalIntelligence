"""Test en direct F5_MODELE_A (forward/f5.py, forward/light.py) : feu tricolore à la main, portefeuilles simulés
(bande, rouge, orange, actif rouge, statique), décision de A sur un panneau synthétique, conformité, seuil.
SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f5, light, registry
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.research import factors as fa

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT"), {}, "a" * 64, "b" * 64)


# --- Feu tricolore ----------------------------------------------------------------------------------------

def test_light_rules_by_hand():
    quiet = dict(vol_rank=0.5, btc_funding_8h=0.0001, peg_deviation_pct={"USDT": 0.01, "USDC": -0.02}, delist_titles=[], maintenance_titles=[])
    assert light.decide(date(2026, 10, 7), **quiet)["color"] == light.GREEN                 # mercredi ordinaire
    assert light.decide(date(2026, 10, 10), **quiet)["color"] == light.ORANGE                # samedi
    assert light.decide(date(2026, 10, 30), **quiet)["orange_reasons"] == ["dernier vendredi du mois"]
    assert light.decide(date(2026, 10, 14), **quiet)["red_reasons"] == ["macro : CPI"]      # CPI : rouge
    assert light.decide(date(2026, 10, 28), **quiet)["color"] == light.RED                  # Fed
    assert light.decide(date(2026, 10, 7), **(quiet | {"vol_rank": 0.95}))["color"] == light.RED
    assert light.decide(date(2026, 10, 7), **(quiet | {"vol_rank": 0.89}))["color"] == light.GREEN
    assert light.decide(date(2026, 10, 7), **(quiet | {"btc_funding_8h": 0.0006}))["color"] == light.RED
    assert light.decide(date(2026, 10, 7), **(quiet | {"peg_deviation_pct": {"USDT": -0.6, "USDC": 0.0}}))["color"] == light.RED
    assert light.decide(date(2026, 10, 7), **(quiet | {"vol_rank": None, "btc_funding_8h": None}))["color"] == light.GREEN
    maintenance = light.decide(date(2026, 10, 7), **(quiet | {"maintenance_titles": ["Binance Will Perform Scheduled System Maintenance - 2026-10-08"]}))
    assert maintenance["color"] == light.ORANGE and "maintenance" in maintenance["orange_reasons"][0]
    delist = light.decide(date(2026, 10, 7), **(quiet | {"delist_titles": [
        "Binance Will Delist ABC, DEF on 2026-10-10", "Binance Futures Will Delist XYZ Perpetual Contracts",
        "Notice of Removal of Spot Trading Pairs - 2026-10-02"]}))
    assert delist["red_assets"] == ["ABC", "DEF"] and delist["color"] == light.GREEN          # rouge pour l'actif seulement


def test_volatility_rank_and_calendar_helpers():
    history = [float(v) for v in range(1, 201)]
    assert light.volatility_rank(history, 200.0) == 1.0 and light.volatility_rank(history, 100.0) == 0.5
    assert light.volatility_rank(history[:50], 10.0) is None and light.volatility_rank(history, None) is None
    assert light.is_last_friday(date(2026, 10, 30)) and not light.is_last_friday(date(2026, 10, 23))
    assert light.macro_events("2026-12-09") == ["FED"] and light.macro_events("2026-12-08") == []
    assert len(light.MACRO_CALENDAR) == 11


# --- Portefeuilles ----------------------------------------------------------------------------------------

def test_rebalance_band_costs_and_no_leverage():
    ledger = f5.new_ledger()
    prices = {"BTCUSDT": 100.0, "ETHUSDT": 10.0}
    out = f5.rebalance(ledger, {"BTCUSDT": 0.2, "ETHUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    cost = 0.00075 + 0.0002
    assert out["trades"] == 2 and out["holdings"]["BTCUSDT"] == pytest.approx(0.2) and out["fees"] == pytest.approx(0.4 * cost)
    assert out["cash"] == pytest.approx(1 - 0.4 - 0.4 * cost) and out["value"] == pytest.approx(1 - 0.4 * cost)
    # Dans la bande : aucun échange ; hors bande : échange ; sortie : toujours.
    same = f5.rebalance(out, {"BTCUSDT": 0.23, "ETHUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    assert same["trades"] == 2
    moved = f5.rebalance(out, {"BTCUSDT": 0.3, "ETHUSDT": 0.0}, scenario=CENTRAL, prices=prices)
    assert moved["trades"] == 4 and "ETHUSDT" not in moved["holdings"] and moved["holdings"]["BTCUSDT"] == pytest.approx(0.3 * out["value"])
    # Jamais plus que le cash disponible (pas de levier).
    full = f5.rebalance(f5.new_ledger(), {"BTCUSDT": 0.9, "ETHUSDT": 0.9}, scenario=CENTRAL, prices=prices)
    assert full["cash"] >= -1e-9 and sum(full["holdings"].values()) <= 1.0
    # Revalorisation : ratio de prix ; actif sans prix : valeur conservée.
    up = f5.revalue(out, {"BTCUSDT": 110.0}, prices)
    assert up["holdings"]["BTCUSDT"] == pytest.approx(0.22) and up["holdings"]["ETHUSDT"] == pytest.approx(0.2)


def test_light_effects_on_the_portfolio():
    prices = {"BTCUSDT": 100.0, "ETHUSDT": 10.0}
    base = f5.rebalance(f5.new_ledger(), {"BTCUSDT": 0.2, "ETHUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    red = {"color": light.RED, "red_assets": []}
    first = f5.apply_light(base, red, {"BTCUSDT": 0.2, "ETHUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    assert first["red_streak"] == 1 and first["holdings"]["BTCUSDT"] == pytest.approx(0.1, abs=1e-6)
    second = f5.apply_light(first, red, None, scenario=CENTRAL, prices=prices)
    assert second["red_streak"] == 2 and second["holdings"]["BTCUSDT"] == pytest.approx(first["holdings"]["BTCUSDT"])  # pas de 2e coupe
    orange = f5.apply_light(second, {"color": light.ORANGE, "red_assets": []}, {"BTCUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    assert orange["trades"] == second["trades"] and orange["red_streak"] == 0                   # rien n'est échangé
    back = f5.apply_light(orange, {"color": light.GREEN, "red_assets": []}, {"BTCUSDT": 0.2, "ETHUSDT": 0.2}, scenario=CENTRAL, prices=prices)
    assert back["holdings"]["BTCUSDT"] == pytest.approx(0.2 * back["value"], rel=1e-3)
    delist = f5.apply_light(back, {"color": light.GREEN, "red_assets": ["ETH"]}, None, scenario=CENTRAL, prices=prices)
    assert "ETHUSDT" not in delist["holdings"] and "BTCUSDT" in delist["holdings"]


# --- Décision de A ------------------------------------------------------------------------------------------

def hourly(symbol: str, days: int, drift: float, start="2025-01-01") -> pd.DataFrame:
    index = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    price = 100 * (1 + drift) ** (pd.Series(range(len(index))) / 24)
    return pd.DataFrame({"open_time": index, "open": price.to_numpy(), "high": price.to_numpy() * 1.001,
                         "low": price.to_numpy() * 0.999, "close": price.to_numpy(), "quote_volume": 5e6 / 24,
                         "available_at": index + pd.Timedelta(hours=1, seconds=2)})


def test_decide_follows_lot_8_rules(monkeypatch):
    monkeypatch.setattr(fa, "MIN_MEDIAN_VOLUME", 0.0)
    frames = {"BTCUSDT": hourly("BTCUSDT", 300, 0.002), "ETHUSDT": hourly("ETHUSDT", 300, 0.002),
              "SOLUSDT": hourly("SOLUSDT", 300, -0.002), "XRPUSDT": hourly("XRPUSDT", 300, 0.002),
              "ADAUSDT": hourly("ADAUSDT", 300, 0.002), "DOTUSDT": hourly("DOTUSDT", 300, 0.002)}
    frames["XRPUSDT"]["quote_volume"] *= 3
    monday = pd.Timestamp("2025-10-06", tz="UTC")
    sigma = {"BTCUSDT": 0.4, "ETHUSDT": 1.0, "SOLUSDT": 0.5, "XRPUSDT": 0.5, "ADAUSDT": 0.5, "DOTUSDT": float("nan")}
    out = f5.decide({s: f[f["open_time"] + pd.Timedelta(hours=1) <= monday] for s, f in frames.items()}, sigma, monday)
    assert out["basket"][:2] == ["BTCUSDT", "ETHUSDT"] and "DOTUSDT" not in out["basket"] and "XRPUSDT" in out["basket"]
    assert out["votes"]["SOLUSDT"] == 0 and out["votes"]["BTCUSDT"] == 3
    assert out["weights"]["BTCUSDT"] == pytest.approx(0.2) and out["weights"]["ETHUSDT"] == pytest.approx(0.1)
    assert "SOLUSDT" not in out["weights"]
    assert f5.sigma_from_forecast({"available": True, "horizons": {"7": {"move_pct": 10.0}}}) == pytest.approx(0.1 * (365 / 7) ** 0.5)
    assert f5.sigma_from_forecast(None) != f5.sigma_from_forecast(None)                          # NaN


def test_conformity_recomputes_each_decision(settings):
    start = registry.start(settings, f5.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f5.TEST_ID)
    good = {"week": "2026-10-05", "basket": ["BTCUSDT", "ETHUSDT"], "votes": {"BTCUSDT": 3, "ETHUSDT": 1},
            "sigma": {"BTCUSDT": 0.4, "ETHUSDT": 1.0}, "weights": {"BTCUSDT": 0.2}}
    journal.append(f5.DECISION, good, now=datetime(2026, 10, 5, 10, 15, tzinfo=UTC))
    assert f5.conformity(journal) == {"decisions": 1, "mismatches": [], "status": f5.CONFORM}
    journal.append(f5.DECISION, good | {"week": "2026-10-12", "weights": {"BTCUSDT": 0.15}}, now=datetime(2026, 10, 12, 10, 15, tzinfo=UTC))
    assert f5.conformity(journal)["mismatches"] == ["2026-10-12"]
    out = f5.stats(journal, start, now=datetime(2026, 10, 13, tzinfo=UTC))
    assert out["decisions"] == 2 and out["verdict"] == f5.RUNNING and out["conformity"]["status"] == f5.NOT_CONFORM
    ended = f5.stats(journal, start, now=datetime(2027, 1, 1, tzinfo=UTC))
    assert ended["verdict"] == "SUIVI_TERMINE_NON_CONFORME" and f5.finalize(journal, start, now=datetime(2027, 1, 1, tzinfo=UTC)) == registry.VERDICT


def test_valuation_series_measures_and_announcement_parsing():
    values = pd.Series([1.0, 1.1, 1.0, 1.2], index=["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"])
    out = f5.describe(values)
    assert out["return"] == pytest.approx(0.2) and out["max_drawdown"] == pytest.approx(-0.1 / 1.1, rel=1e-6) and out["days"] == 4
    assert f5.describe(values.iloc[:1]) == {"days": 1, "return": None}
    assert set(f5.TEST.params["light"]["calendar"][0]) == {"2026-10-02", "NFP"} and f5.STATIC_EXPOSURE == 0.3225


def test_f5_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f5.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("0,3225", "10 % les plus hauts", "0,05 % par 8 h", "0,5 %", "11 dates", "réduction de moitié",
                  "84 jours", "SUIVI_TERMINE_CONFORME", "LONG-20261002T155625Z-967d85"):
        assert value in text, value
    assert f5.TEST.params["static_exposure"] == 0.3225 and f5.RED_CUT == 0.5 and light.HISTORY_DAYS == 365
    assert ADVERSE in f5.SCENARIOS and CENTRAL in f5.SCENARIOS
