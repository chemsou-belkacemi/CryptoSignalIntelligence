"""Admission des paires selon l'avis de screening halal (règle du propriétaire, 2026-10-01). Aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi
from crypto_signal_intelligence.external import admission as adm
from crypto_signal_intelligence.external.universe import REQUESTED, UserUniverse

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
LISTED = {"POLUSDT", "RENDERUSDT", "GRTUSDT", "DOGEUSDT", "LTCUSDT", "BNBUSDT", "UNIUSDT"}


def listing(settings, symbol):
    """Binance simulé : quelques paires négociables seulement."""
    return Decimal("0.0001") if symbol in LISTED else None


def test_status_follows_the_declared_rule():
    assert adm.classify({"HS": "halal", "SB": "halal"}) == adm.FAVORABLE
    assert adm.classify({"HS": "halal", "SB": "halal", "IFG": "ambigu"}) == adm.FAVORABLE      # SOL
    assert adm.classify({"HS": "halal", "SB": "halal", "IFG": "haram"}) == adm.DEFAVORABLE
    assert adm.classify({"HS": "halal", "SB": "douteux", "IFG": "halal"}) == adm.DOUTEUX
    assert adm.classify({"HS": "halal"}) == adm.INEXPLOITABLE                                 # une seule source
    assert adm.classify({"resume": "favorable", "note": "x"}) == adm.FAVORABLE
    assert adm.classify({"note": "rien"}) == adm.INEXPLOITABLE


def test_the_recorded_screening_matches_the_universe_document(settings):
    screenings, checked_on = adm.load_screening(settings)
    assert checked_on == "2026-09-30"
    assert all(screenings[adm.base_of(s)].status == adm.FAVORABLE for s in settings.data.symbols)   # 16 paires
    assert {b for b, s in screenings.items() if s.status == adm.DEFAVORABLE} == {"UNI", "AAVE", "MKR", "ENA"}
    assert screenings["BNB"].status == adm.DOUTEUX and screenings["LTC"].status == adm.INEXPLOITABLE
    assert screenings["POL"].status == screenings["GRT"].status == adm.FAVORABLE
    assert adm.screening_for(settings, "PEPEUSDT").status == adm.DOUTEUX
    assert adm.screening_for(settings, "WIFUSDT").status == adm.INEXPLOITABLE        # absente du screening


def test_admit_all_adds_favorables_refuses_haram_and_leaves_the_rest_to_the_owner(settings):
    results = {r["symbol"]: r for r in adm.admit_all(settings, now=NOW, lookup=listing)}
    assert "BTCUSDT" not in results                                                   # déjà dans la configuration
    assert results["POLUSDT"]["decision"] == adm.AJOUTEE and results["POLUSDT"]["decided_by"] == adm.RULE
    assert results["XTZUSDT"]["decision"] == adm.INDISPONIBLE                         # favorable, non négociable
    assert results["UNIUSDT"]["decision"] == adm.REFUSEE
    assert results["DOGEUSDT"]["decision"] == results["LTCUSDT"]["decision"] == adm.A_DECIDER
    universe = UserUniverse(settings.external_db)
    assert universe.get("POLUSDT")["status"] == REQUESTED and universe.get("DOGEUSDT") is None
    assert universe.get("UNIUSDT") is None


def test_owner_decisions_prevail_and_are_never_overwritten_by_the_rule(settings):
    adm.admit_all(settings, now=NOW, lookup=listing)
    added = adm.decide(settings, "DOGEUSDT", add=True, now=NOW, lookup=listing)
    refused = adm.decide(settings, "LTCUSDT", add=False, now=NOW, lookup=listing)
    assert added["decision"] == adm.AJOUTEE and added["decided_by"] == adm.OWNER
    assert refused["decision"] == adm.REFUSEE and refused["decided_by"] == adm.OWNER
    assert UserUniverse(settings.external_db).get("DOGEUSDT")["status"] == REQUESTED
    again = {r["symbol"]: r for r in adm.admit_all(settings, now=NOW, lookup=listing)}
    assert again["DOGEUSDT"]["decided_by"] == again["LTCUSDT"]["decided_by"] == adm.OWNER
    assert adm.AdmissionLog(settings.external_db).pending() == [
        p for p in adm.AdmissionLog(settings.external_db).pending() if p["symbol"] not in ("DOGEUSDT", "LTCUSDT")]


def test_api_lists_runs_and_records_decisions(settings):
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = listing
    run = api.dispatch("POST", "/admissions/run", {}, {})
    assert run["counts"]["AJOUTEE"] >= 3 and run["counts"]["REFUSEE"] == 4
    listing_view = api.dispatch("GET", "/admissions", {}, None)
    pending = {p["symbol"] for p in listing_view["pending"]}
    assert {"DOGEUSDT", "BNBUSDT", "LTCUSDT"} <= pending and "ce projet ne certifie rien" in listing_view["rule"]
    decided = api.dispatch("POST", "/admissions/decide", {}, {"symbol": "bnbusdt", "decision": "refuse"})
    assert decided["decision"] == adm.REFUSEE and decided["decided_by"] == adm.OWNER
    assert "BNBUSDT" not in {p["symbol"] for p in api.dispatch("GET", "/admissions", {}, None)["pending"]}
    for bad in ({"symbol": "BNB", "decision": "add"}, {"symbol": "BNBUSDT", "decision": "maybe"}, {}):
        with pytest.raises(ApiError):
            api.dispatch("POST", "/admissions/decide", {}, bad)
