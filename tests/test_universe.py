"""Univers du propriétaire : un signal soumis à la main ajoute sa paire ; jamais un signal automatique."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.external.evaluate import evaluate
from crypto_signal_intelligence.external.registry import ExternalSignalRegistry
from crypto_signal_intelligence.external.universe import (
    FAILED,
    READY,
    REQUESTED,
    UserUniverse,
    download_pending,
    tick_size_for,
    universe_symbols,
)

from .conftest import canonical

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
SIGNAL = "PAIR: QTUM/USDT\nENTRY 1: 2.000\nT1: 2.100\nT2: 2.200\nSL: 1.900\nPLATFORM: Binance"


def fake_tick(settings, symbol):
    return Decimal("0.001")


def store_pair(settings, symbol="QTUMUSDT"):
    """Bougies synthétiques de la paire (15m et 1h) et de BTC 1h, comme après un téléchargement."""
    store = CandleStore(settings.data_dir)
    store.save(canonical(4000, symbol=symbol, seed=21), symbol, "15m")
    store.save(canonical(1000, "1h", symbol=symbol, seed=22), symbol, "1h")
    if store.last_open_time("BTCUSDT", "1h") is None:
        store.save(canonical(1000, "1h", symbol="BTCUSDT", seed=23), "BTCUSDT", "1h")


def test_manual_signal_adds_the_pair_then_becomes_evaluable_once_data_is_ready(settings):
    registry = ExternalSignalRegistry(settings.external_db)
    universe = UserUniverse(settings.external_db)
    assert "QTUMUSDT" not in universe_symbols(settings)

    first = evaluate(settings, SIGNAL, source="Suhaib", now=NOW, user_validated=True, tick_size_lookup=fake_tick)
    assert first.verdict == "EN_ATTENTE" and first.record_id is None and registry.recent() == []
    assert "ajoutée à l'univers sur ta validation" in first.failed[0].detail
    entry = universe.get("QTUMUSDT")
    assert entry["status"] == REQUESTED and entry["tick_size"] == "0.001" and "Suhaib" in entry["reason"]
    assert tick_size_for(settings, "QTUMUSDT") == Decimal("0.001")
    assert tick_size_for(settings, "ETHUSDT") == settings.data.tick_size["ETHUSDT"]

    # Reçu automatiquement pendant le téléchargement : en attente aussi, toujours rien d'enregistré.
    automatic = evaluate(settings, SIGNAL, source="Suhaib", now=NOW, user_validated=False)
    assert automatic.verdict == "EN_ATTENTE" and registry.recent() == []

    rechecks = []

    def downloader(settings_, symbol, timeframe, *, now, recheck_archives=False, **_):
        rechecks.append((timeframe, recheck_archives))
        store_pair(settings_, symbol)

    outcome = download_pending(settings, now=NOW + timedelta(minutes=1), downloader=downloader)
    # Première fois : aucune bougie stockée, archives revérifiées (reprise sûre après un arrêt) ;
    # le 1h est déjà là quand on y arrive (store_pair écrit les deux) : pas de revérification inutile.
    assert rechecks == [("15m", True), ("1h", False)]
    assert outcome["ready"] == ["QTUMUSDT"] and outcome["failed"] == []
    assert universe.get("QTUMUSDT")["status"] == READY and "QTUMUSDT" in universe_symbols(settings)

    last = CandleStore(settings.data_dir).load("QTUMUSDT", "15m").iloc[-1]
    later = last["available_at"].to_pydatetime() + timedelta(minutes=1)
    close = float(last["close"])
    text = f"PAIR: QTUM/USDT\nENTRY 1: {close * 1.001:.3f}\nT1: {close * 1.03:.3f}\nSL: {close * 0.98:.3f}\nPLATFORM: Binance"
    ready = evaluate(settings, text, source="Suhaib", now=later, user_validated=False)
    assert ready.verdict in {"DEFAVORABLE", "INDETERMINE", "FAVORABLE"} and ready.record_id
    assert next(c for c in ready.checks if c.label == "paire dans l'univers").ok
    assert registry.recent()[0]["symbol"] == "QTUMUSDT"


def test_automatic_signal_never_adds_a_pair_and_unknown_pair_is_refused(settings):
    calls = []

    def lookup(settings_, symbol):
        calls.append(symbol)
        from crypto_signal_intelligence.data.http import HttpError
        raise HttpError("HTTP 400 : Invalid symbol", 400)

    automatic = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=False, tick_size_lookup=lookup)
    assert automatic.verdict == "REFUSE" and "vaut validation" in automatic.failed[0].detail
    assert calls == [] and UserUniverse(settings.external_db).all() == []

    unknown = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=True, tick_size_lookup=lookup)
    assert unknown.verdict == "REFUSE" and "introuvable sur Binance Spot" in unknown.failed[0].detail
    assert calls == ["QTUMUSDT"] and UserUniverse(settings.external_db).all() == []


def test_binance_outage_is_neither_a_refusal_nor_an_addition(settings):
    """Panne réseau ou 5xx/429 : avis en attente, rien d'enregistré ; seul un 400 de Binance vaut « introuvable »."""
    from crypto_signal_intelligence.data.http import HttpError

    def outage(settings_, symbol):
        raise HttpError("Erreur réseau (ConnectTimeout) sur /api/v3/exchangeInfo")

    def overloaded(settings_, symbol):
        raise HttpError("HTTP 503", 503)

    def invalid(settings_, symbol):
        raise HttpError("HTTP 400 : Invalid symbol", 400)

    for lookup in (outage, overloaded):
        result = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=True, tick_size_lookup=lookup)
        assert result.verdict == "EN_ATTENTE" and "impossible pour l'instant" in result.failed[0].detail
        assert result.record_id is None and UserUniverse(settings.external_db).all() == []
    refused = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=True, tick_size_lookup=invalid)
    assert refused.verdict == "REFUSE" and "introuvable sur Binance Spot (HTTP 400)" in refused.failed[0].detail


def test_download_failures_are_counted_then_the_pair_is_retried_on_manual_submission(settings):
    universe = UserUniverse(settings.external_db)
    universe.request("QTUMUSDT", Decimal("0.001"), reason="test", now=NOW)

    def broken(settings_, symbol, timeframe, *, now, **_):
        raise ConnectionError("archives injoignables")

    for attempt in range(1, 4):
        outcome = download_pending(settings, now=NOW, downloader=broken)
        assert outcome["failed"][0]["attempts"] == attempt
    entry = universe.get("QTUMUSDT")
    assert entry["status"] == FAILED and "archives injoignables" in entry["last_error"]
    assert download_pending(settings, now=NOW, downloader=broken)["processed"] == []   # plus de tentative seule

    automatic = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=False)
    assert automatic.verdict == "REFUSE" and "téléchargement en échec" in automatic.failed[0].detail
    manual = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=True, tick_size_lookup=fake_tick)
    assert manual.verdict == "EN_ATTENTE"
    entry = universe.get("QTUMUSDT")
    assert entry["status"] == REQUESTED and entry["attempts"] == 0 and entry["last_error"] is None

    # Un téléchargement qui ne stocke rien n'est jamais un succès.
    silent = download_pending(settings, now=NOW, downloader=lambda *a, **k: None)
    assert silent["failed"][0]["attempts"] == 1 and "aucune bougie" in silent["failed"][0]["error"]


def test_auto_add_mode_follows_the_halal_screening_of_automatic_signals(settings):
    """Mode d'ajout automatique : une crypto favorable au screening est ajoutée ; une crypto douteuse reste en
    attente de la décision du propriétaire (signal non transmis) ; une crypto défavorable est refusée."""
    test_mode = settings.model_copy(update={"external": settings.external.model_copy(update={"auto_add_pairs": True})})
    automatic = evaluate(test_mode, SIGNAL, source="g", now=NOW, user_validated=False, tick_size_lookup=fake_tick)
    assert automatic.verdict == "EN_ATTENTE"
    entry = UserUniverse(settings.external_db).get("QTUMUSDT")                 # QTUM : favorable au screening
    assert entry["status"] == REQUESTED and "favorable au screening halal" in entry["reason"]
    assert "automatiquement" in automatic.failed[0].detail and "ta validation" not in automatic.failed[0].detail
    doubtful = evaluate(test_mode, SIGNAL.replace("QTUM", "DOGE"), source="g", now=NOW, user_validated=False,
                        tick_size_lookup=fake_tick)
    assert doubtful.verdict == "EN_ATTENTE" and "en attente de ta décision" in doubtful.failed[0].detail
    assert UserUniverse(settings.external_db).get("DOGEUSDT") is None              # rien n'est ajouté
    haram = evaluate(test_mode, SIGNAL.replace("QTUM", "UNI"), source="g", now=NOW, user_validated=False,
                     tick_size_lookup=fake_tick)
    assert haram.verdict == "REFUSE" and "défavorable au screening halal" in haram.failed[0].detail


def test_owner_decisions_rule_signal_evaluation(settings):
    """Refus par bouton → signal automatique refusé ; soumission manuelle = nouvelle décision du propriétaire ;
    DOGE reçu automatiquement → à décider ; DOGE soumis à la main → décision du propriétaire."""
    from crypto_signal_intelligence.external import admission as adm
    test_mode = settings.model_copy(update={"external": settings.external.model_copy(update={"auto_add_pairs": True})})
    adm.decide(settings, "POLUSDT", add=False, now=NOW, lookup=fake_tick)
    refused = evaluate(test_mode, SIGNAL.replace("QTUM", "POL"), source="g", now=NOW, user_validated=False,
                       tick_size_lookup=fake_tick)
    assert refused.verdict == "REFUSE" and "refusée par toi" in refused.failed[0].detail
    manual = evaluate(test_mode, SIGNAL.replace("QTUM", "POL"), source="g", now=NOW, user_validated=True,
                      tick_size_lookup=fake_tick)
    assert manual.verdict == "EN_ATTENTE" and "sur ta validation" in manual.failed[0].detail
    log = adm.AdmissionLog(settings.external_db)
    assert log.get("POLUSDT")["decision"] == adm.AJOUTEE and log.get("POLUSDT")["decided_by"] == adm.OWNER
    doge = evaluate(test_mode, SIGNAL.replace("QTUM", "DOGE"), source="g", now=NOW, user_validated=False,
                    tick_size_lookup=fake_tick)
    assert doge.verdict == "EN_ATTENTE" and "DOGEUSDT" in {p["symbol"] for p in log.pending()}
    manual_doge = evaluate(test_mode, SIGNAL.replace("QTUM", "DOGE"), source="g", now=NOW, user_validated=True,
                           tick_size_lookup=fake_tick)
    assert manual_doge.verdict == "EN_ATTENTE" and log.get("DOGEUSDT")["decided_by"] == adm.OWNER
    assert "DOGEUSDT" not in {p["symbol"] for p in log.pending()}


def test_a_defavorable_crypto_is_refused_even_when_submitted_by_hand(settings):
    from crypto_signal_intelligence.external import admission as adm
    uni = evaluate(settings, SIGNAL.replace("QTUM", "UNI"), source="g", now=NOW, user_validated=True,
                   tick_size_lookup=fake_tick)
    assert uni.verdict == "REFUSE" and "défavorable" in uni.failed[0].detail
    assert UserUniverse(settings.external_db).get("UNIUSDT") is None
    decided = adm.AdmissionLog(settings.external_db).get("UNIUSDT")
    assert decided["decision"] == adm.REFUSEE and decided["decided_by"] == adm.RULE


def test_without_auto_add_a_doubtful_automatic_signal_is_still_flagged_to_decide(settings):
    from crypto_signal_intelligence.external import admission as adm
    assert not settings.external.auto_add_pairs
    doge = evaluate(settings, SIGNAL.replace("QTUM", "DOGE"), source="g", now=NOW, user_validated=False,
                    tick_size_lookup=fake_tick)
    assert doge.verdict == "EN_ATTENTE"
    assert "DOGEUSDT" in {p["symbol"] for p in adm.AdmissionLog(settings.external_db).pending()}
    automatic_favourable = evaluate(settings, SIGNAL, source="g", now=NOW, user_validated=False,
                                    tick_size_lookup=fake_tick)                # QTUM : favorable, sans ajout auto
    assert automatic_favourable.verdict == "REFUSE" and "La soumettre à la main" in automatic_favourable.failed[0].detail


def test_forget_and_tick_size_errors(settings):
    universe = UserUniverse(settings.external_db)
    assert universe.forget("QTUMUSDT") is False
    universe.request("QTUMUSDT", Decimal("0.001"), reason="test", now=NOW)
    universe.mark_ready("QTUMUSDT", now=NOW)
    assert universe.ready_symbols() == ["QTUMUSDT"] and universe.forget("QTUMUSDT") is True
    assert universe.all() == []
    with pytest.raises(ValueError):
        tick_size_for(settings, "QTUMUSDT")
