"""Admission des paires selon l'avis de screening halal (règle du propriétaire, 2026-10-01). Aucun réseau."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi
from crypto_signal_intelligence.external import admission as adm
from crypto_signal_intelligence.external.universe import REQUESTED, UserUniverse

from .conftest import PROJECT

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


def test_pairs_added_before_the_rule_are_reclassified_retroactively(settings):
    """Ancien mode test : DOGE (douteux) et RARE (hors screening) sont READY ; QTUM soumis à la main."""
    from decimal import Decimal

    from crypto_signal_intelligence.external.evaluate import evaluate
    universe = UserUniverse(settings.external_db)
    for symbol, reason in (("DOGEUSDT", "mode test auto_add_pairs, sans validation manuelle (source « g »)"),
                           ("RAREUSDT", "mode test auto_add_pairs, sans validation manuelle (source « g »)"),
                           ("QTUMUSDT", "signal soumis à la main (source « g »)"),
                           ("UNIUSDT", "mode test auto_add_pairs, sans validation manuelle (source « g »)")):
        universe.request(symbol, Decimal("0.001"), reason=reason, now=NOW)
        universe.mark_ready(symbol, now=NOW)
    results = {r["symbol"]: r for r in adm.admit_all(settings, now=NOW, lookup=listing)}
    assert results["DOGEUSDT"]["decision"] == results["RAREUSDT"]["decision"] == adm.A_DECIDER
    assert "ajoutée avant la règle" in results["DOGEUSDT"]["reason"]
    assert results["QTUMUSDT"]["decision"] == adm.AJOUTEE and results["QTUMUSDT"]["decided_by"] == adm.OWNER
    assert results["UNIUSDT"]["decision"] == adm.REFUSEE
    # Leurs signaux : retenus (EN_ATTENTE) pour les cryptos à décider, refusés pour UNI, évalués pour QTUM.
    signal = "PAIR: {}/USDT\nENTRY 1: 2.000\nT1: 2.100\nT2: 2.200\nSL: 1.900\nPLATFORM: Binance"
    doge = evaluate(settings, signal.format("DOGE"), source="g", now=NOW, user_validated=False)
    assert doge.verdict == "EN_ATTENTE" and "ajoutée avant la règle" in doge.failed[0].detail
    uni = evaluate(settings, signal.format("UNI"), source="g", now=NOW, user_validated=False)
    assert uni.verdict == "REFUSE" and "défavorable" in uni.failed[0].detail
    qtum = evaluate(settings, signal.format("QTUM"), source="g", now=NOW, user_validated=False)
    assert qtum.failed[0].label != "paire dans l'univers"                   # QTUM reste évaluée (données absentes ici)
    # Décision du propriétaire sur DOGE : ses signaux passent ensuite, sans nouvel ajout (déjà READY).
    adm.decide(settings, "DOGEUSDT", add=True, now=NOW, lookup=listing)
    assert universe.get("DOGEUSDT")["status"] == "READY"
    doge = evaluate(settings, signal.format("DOGE"), source="g", now=NOW, user_validated=False)
    assert doge.failed[0].label != "paire dans l'univers"
    # Soumission à la main d'une crypto à décider déjà READY : décision du propriétaire, tracée.
    rare = evaluate(settings, signal.format("RARE"), source="g", now=NOW, user_validated=True)
    assert rare.failed[0].label != "paire dans l'univers"
    assert adm.AdmissionLog(settings.external_db).get("RAREUSDT")["decided_by"] == adm.OWNER


def test_doubtful_cryptos_not_listed_on_binance_are_unavailable_not_pending(settings):
    results = {r["symbol"]: r for r in adm.admit_all(settings, now=NOW, lookup=listing)}
    assert results["XMRUSDT"]["decision"] == results["QNTUSDT"]["decision"] == adm.INDISPONIBLE
    assert results["DOGEUSDT"]["decision"] == adm.A_DECIDER                   # douteux ET négociable
    pending = {p["symbol"] for p in adm.AdmissionLog(settings.external_db).pending()}
    assert "XMRUSDT" not in pending and "DOGEUSDT" in pending


def test_a_defavorable_crypto_cannot_be_added_by_button_and_explain_has_its_accent(settings):
    assert adm.screening_for(settings, "UNIUSDT").explain().startswith("défavorable")
    with pytest.raises(adm.DefavorableRefused):
        adm.decide(settings, "UNIUSDT", add=True, now=NOW, lookup=listing)
    assert UserUniverse(settings.external_db).get("UNIUSDT") is None
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = listing
    with pytest.raises(ApiError) as exc:
        api.dispatch("POST", "/admissions/decide", {}, {"symbol": "UNIUSDT", "decision": "add"})
    assert exc.value.status == 409


def test_a_binance_outage_during_admission_records_nothing_and_is_reported(settings):
    from http import HTTPStatus

    from crypto_signal_intelligence.data.http import HttpError

    def down(settings, symbol):
        raise HttpError("panne", 503)

    results = {r["symbol"]: r for r in adm.admit_all(settings, now=NOW, lookup=down)}
    assert results["POLUSDT"]["decision"] == adm.INJOIGNABLE and results["UNIUSDT"]["decision"] == adm.REFUSEE
    assert adm.AdmissionLog(settings.external_db).get("POLUSDT") is None       # rien n'est enregistré
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = down
    with pytest.raises(ApiError) as exc:
        api.dispatch("POST", "/admissions/decide", {}, {"symbol": "POLUSDT", "decision": "add"})
    assert exc.value.status == HTTPStatus.BAD_GATEWAY


def test_usdc_pairs_can_be_decided_and_decide_all_adds_every_pending_crypto(settings):
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = lambda settings, symbol: Decimal("0.0001")
    adm.AdmissionLog(settings.external_db).record("DOGEUSDC", adm.screening_for(settings, "DOGEUSDC"), adm.A_DECIDER,
                                                   by=adm.RULE, reason="douteux", now=NOW)
    decided = api.dispatch("POST", "/admissions/decide", {}, {"symbol": "DOGEUSDC", "decision": "refuse"})
    assert decided["decision"] == adm.REFUSEE and decided["decided_by"] == adm.OWNER
    api.dispatch("POST", "/admissions/run", {}, {})
    pending_before = {p["symbol"] for p in api.dispatch("GET", "/admissions", {}, None)["pending"]}
    assert pending_before
    out = api.dispatch("POST", "/admissions/decide-all", {}, {})
    assert out["counts"]["AJOUTEE"] == len(pending_before)
    assert api.dispatch("GET", "/admissions", {}, None)["pending"] == []
    assert all(r["decided_by"] == adm.OWNER for r in out["results"])
    assert UserUniverse(settings.external_db).get("BNBUSDT")["status"] == REQUESTED


def test_run_refuses_a_concurrent_application(settings):
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = listing
    assert api._admissions_lock.acquire(blocking=False)
    try:
        with pytest.raises(ApiError) as exc:
            api.dispatch("POST", "/admissions/run", {}, {})
        assert exc.value.status == 409
    finally:
        api._admissions_lock.release()


def test_the_current_screening_file_matches_its_source_snapshot(settings):
    """Le relevé courant (config/halal_screening.toml) reprend exactement l'instantané des sources, et rien d'autre."""
    import json
    screenings, checked_on = adm.load_screening_file(PROJECT / "config" / "halal_screening.toml")
    snapshot = json.loads((PROJECT / "docs" / "universe_sources" / f"{checked_on}.json").read_text(encoding="utf-8"))
    src = snapshot["sources"]
    assert len(screenings) > 100
    for base, screening in screenings.items():
        assert screening.sources.get("HS") == ("halal" if base in src["HS"]["pass"] else None), base
        assert screening.sources.get("SB") == ("halal" if base in src["SB"]["halal"] else
                                               "douteux" if base in src["SB"]["grey_area"] else None), base
        assert screening.sources.get("IFG") == ("halal" if base in src["IFG"]["halal_yes"] else
                                                "haram" if base in src["IFG"]["halal_no"] else None), base
    assert all(screenings[adm.base_of(p)].status == adm.FAVORABLE for p in settings.data.symbols)
    assert not set(screenings) & set(snapshot["binance"]["excluded_stable_or_fiat"])      # ni stablecoin ni fiat
    assert {b for b, s in screenings.items() if "haram" in s.sources.values()} == {
        b for b, s in screenings.items() if s.status == adm.DEFAVORABLE}


def test_pending_cryptos_are_grouped_by_evidence_and_can_be_added_by_group(settings):
    api = CsiApi(settings, now=lambda: NOW)
    api.listing = lambda settings, symbol: Decimal("0.0001")
    log = adm.AdmissionLog(settings.external_db)
    one = adm.Screening("ABC", adm.INEXPLOITABLE, {"SB": "halal"})
    grey = adm.Screening("DEF", adm.DOUTEUX, {"SB": "douteux", "IFG": "halal"})
    none = adm.Screening("GHI", adm.INEXPLOITABLE, {})
    assert [adm.pending_group(s) for s in (one, grey, none)] == ["une_source", "douteux", "aucune_source"]
    api.dispatch("POST", "/admissions/run", {}, {})
    pending = api.dispatch("GET", "/admissions", {}, None)["pending"]
    groups = {p["symbol"]: p["group"] for p in pending}
    assert groups["LTCUSDT"] == "aucune_source" and groups["BNBUSDT"] == "douteux"      # fichier de test figé
    chosen = [p["symbol"] for p in pending if p["group"] == "douteux"]
    out = api.dispatch("POST", "/admissions/decide-all", {}, {"symbols": chosen + ["POLUSDT", "INCONNUEUSDT"]})
    assert sorted(r["symbol"] for r in out["results"]) == sorted(chosen)                # rien d'autre n'est touché
    left = {p["symbol"] for p in api.dispatch("GET", "/admissions", {}, None)["pending"]}
    assert not left & set(chosen) and "LTCUSDT" in left and log.get("POLUSDT")["decided_by"] == adm.RULE
    with pytest.raises(ApiError):
        api.dispatch("POST", "/admissions/decide-all", {}, {"symbols": "DOGEUSDT"})
