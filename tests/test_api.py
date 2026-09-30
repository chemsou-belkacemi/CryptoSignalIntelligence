"""API locale : routes en lecture et évaluation, garde-fous HTTP (jeton, taille, type, méthodes)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import ThreadingHTTPServer

import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi, explain, make_handler

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
FOREIGN = "PAIR: PEPE/USDT\nENTRY 1: 0.0000100\nT1: 0.0000110\nT2: 0.0000120\nSL: 0.0000090\nPLATFORM: Binance"


@pytest.fixture
def api(settings):
    return CsiApi(settings, now=lambda: NOW)


def test_read_routes_answer_on_an_empty_state(api):
    health = api.dispatch("GET", "/health", {}, None)
    assert health["ready"] is False and health["places_orders"] is False
    assert api.dispatch("GET", "/strategies", {}, None)["strategies"] == []
    assert api.dispatch("GET", "/sources", {}, None)["sources"] == []
    assert api.dispatch("GET", "/signals/recent", {"limit": ["5"]}, None)["signals"] == []
    assert api.dispatch("GET", "/execution-report", {}, None)["rows"] == []
    with pytest.raises(ApiError) as unknown:
        api.dispatch("GET", "/orders", {}, None)
    assert unknown.value.status == HTTPStatus.NOT_FOUND
    with pytest.raises(ApiError):
        api.dispatch("GET", "/signals/recent", {"limit": ["beaucoup"]}, None)


def test_evaluate_validates_input_and_refuses_pairs_outside_the_universe(api):
    for bad in ({}, {"text": "  ", "source": "g"}, {"text": FOREIGN}, {"text": FOREIGN, "source": "x" * 81},
                {"text": FOREIGN, "source": "g", "record": "oui"}):
        with pytest.raises(ApiError) as error:
            api.dispatch("POST", "/evaluate", {}, bad)
        assert error.value.status == HTTPStatus.BAD_REQUEST
    result = api.dispatch("POST", "/evaluate", {}, {"text": FOREIGN, "source": "Groupe A", "record": False})
    assert result["verdict"] == "REFUSE" and result["record_id"] is None
    assert "Refusé" in result["summary_fr"] and "hors univers" in result["summary_fr"]
    recorded = api.dispatch("POST", "/evaluate", {}, {"text": FOREIGN, "source": "Groupe A"})
    assert recorded["record_id"]
    assert api.dispatch("GET", "/signals/recent", {}, None)["signals"][0]["source"] == "Groupe A"


def test_owner_submitted_signal_adds_its_pair_and_universe_lists_it(api, monkeypatch):
    from decimal import Decimal

    from crypto_signal_intelligence.external import evaluate as evaluate_module
    monkeypatch.setattr(evaluate_module, "_binance_tick_size", lambda settings, symbol: Decimal("0.0000001"))
    with pytest.raises(ApiError):
        api.dispatch("POST", "/evaluate", {}, {"text": FOREIGN, "source": "g", "user_validated": "oui"})
    pending = api.dispatch("POST", "/evaluate", {}, {"text": FOREIGN, "source": "Groupe A", "user_validated": True})
    assert pending["verdict"] == "EN_ATTENTE" and pending["record_id"] is None
    assert "En attente" in pending["summary_fr"] and "redemander" in pending["summary_fr"]
    listing = api.dispatch("GET", "/universe", {}, None)
    assert listing["configured"] == list(api.settings.data.symbols)
    assert [(p["symbol"], p["status"]) for p in listing["user_pairs"]] == [("PEPEUSDT", "REQUESTED")]
    again = api.dispatch("POST", "/evaluate", {}, {"text": FOREIGN, "source": "Groupe A"})
    assert again["verdict"] == "EN_ATTENTE"                    # automatique : en attente aussi, jamais ajouté deux fois
    assert api.dispatch("GET", "/signals/recent", {}, None)["signals"] == []


def test_generated_signals_are_listed_with_their_strategy_status_and_a_bsm_text(api, settings):
    from crypto_signal_intelligence.signals.outbox import SignalRegistry
    from tests.test_signals import one_tp_signal
    assert api.dispatch("GET", "/signals/generated", {}, None)["signals"] == []
    signal = one_tp_signal()
    SignalRegistry(settings.signals_db, settings.publication_dir(), root=settings.root).publish(signal, signal.created_at)
    listed = api.dispatch("GET", "/signals/generated", {"limit": ["5"]}, None)
    row = listed["signals"][0]
    assert row["signal_id"] == signal.signal_id and row["symbol"] == signal.symbol and row["expired"] is True
    assert row["validation_status"] == str(getattr(signal.validation_status, "value", signal.validation_status))
    assert "promesse" in listed["note"]
    base = signal.symbol[:-4]
    assert row["bsm_text"] == (f"PAIR: {base}/USDT\nENTRY 1: {signal.entry_1}\nT1: {signal.tp_1}\n"
                               f"SL: {signal.stop_loss}\nPLATFORM: Binance")


def test_explanation_defines_every_number():
    text = explain({"verdict": "INDETERMINE", "checks": [], "source": "g",
                    "base_rate": {"samples": 412, "tp_first": 0.37, "expectancy_r": -0.08,
                                  "expectancy_r_ci95": [-0.2, 0.05], "regime_conditioned": True},
                    "source_stats": {"evaluated": 3, "resolved": 1}})
    assert "412 ordres de même géométrie" in text and "37 %" in text and "IC95" in text
    assert "pas la probabilité que CE signal réussisse" in text and "ce n'est pas du 50/50" in text


@pytest.fixture
def server(settings):
    """Vrai serveur HTTP sur un port libre, avec jeton."""
    handler = make_handler(CsiApi(settings, now=lambda: NOW), token="jeton-de-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(url, *, method="GET", body: bytes | None = None, token="jeton-de-test", content_type="application/json"):
    request = urllib.request.Request(url, data=body, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_http_guards(server):
    assert call(f"{server}/health")[0] == 200
    assert call(f"{server}/health", token=None)[0] == 401
    assert call(f"{server}/health", token="mauvais")[0] == 401
    status, payload = call(f"{server}/evaluate", method="POST",
                           body=json.dumps({"text": FOREIGN, "source": "g", "record": False}).encode())
    assert status == 200 and payload["verdict"] == "REFUSE"
    assert call(f"{server}/evaluate", method="POST", body=b"x" * 20_000)[0] == 413
    assert call(f"{server}/evaluate", method="POST", body=b"{}", content_type="text/plain")[0] == 415
    assert call(f"{server}/evaluate", method="POST", body=b"[1, 2]")[0] == 400
    assert call(f"{server}/evaluate", method="POST", body=b"pas du json")[0] == 400
    assert call(f"{server}/health", method="DELETE")[0] == 405
    assert call(f"{server}/orders")[0] == 404


def test_unfavourable_explanation_says_whether_a_veto_fired():
    losing = {"verdict": "DEFAVORABLE", "checks": [{"label": "x", "ok": True, "detail": ""}]}
    vetoed = {"verdict": "DEFAVORABLE", "checks": [{"label": "distance du stop", "ok": False, "detail": "0,2 ATR"}]}
    assert "aucun veto" in explain(losing)
    assert "un veto est déclenché" in explain(vetoed) and "distance du stop (0,2 ATR)" in explain(vetoed)
