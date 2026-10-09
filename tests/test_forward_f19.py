"""Test en direct F19_PRICE_ACTION (forward/f19.py, price_action/evaluate.py, state.py, outbox.py) : démarrage sur une
racine temporaire, évaluation d'une clôture 4 h une seule fois, retard, quota de 5 appels par jour, discipline lue
dans le journal, résolution avec placebos, trou, verdict par configuration, boîte Telegram fusionnée avec celle de
l'assistant, route `/price-action`, carte, rapport, et aucune écriture dans `signals/` ni `state/assistant*`.
SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi, make_handler
from crypto_signal_intelligence.assistant import outbox as assistant_outbox
from crypto_signal_intelligence.forward import f19, registry
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.forward.tests import BY_ID, TESTS
from crypto_signal_intelligence.price_action import detect as D
from crypto_signal_intelligence.price_action import evaluate as ev
from crypto_signal_intelligence.price_action import manage as M
from crypto_signal_intelligence.price_action import outbox, state
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .conftest import PROJECT
from .test_price_action import T0, base_retest_bars, hours_from_4h

HALAL = HalalList(("BTCUSDT", "RETUSDT"), {}, "a" * 64, "b" * 64)
T = T0 + 136 * pd.Timedelta(hours=4)                 # clôture de la bougie d'entrée de la base (2026-01-23 16:00)
START_AT = T - pd.Timedelta(days=1)
NOW = T + pd.Timedelta(minutes=5)


def rising(n: int = 120) -> list[tuple]:
    """Après l'entrée (107,2) : monte de 0,15 par bougie 4 h (TP1 puis objectif à 112 touchés)."""
    return [(107.2 + 0.15 * k, 107.2 + 0.15 * (k + 1) + 0.05, 107.2 + 0.15 * k - 0.05, 107.2 + 0.15 * (k + 1), 1.0) for k in range(n)]


def frames(extra: list[tuple] | None = None) -> dict[str, pd.DataFrame]:
    ret = hours_from_4h(base_retest_bars(extra=extra))
    btc = hours_from_4h([(100.0, 100.5, 99.5, 100.0, 1.0)] * (len(ret) // 4))
    return {"RETUSDT": ret, "BTCUSDT": btc}


def write_store(settings, data: dict[str, pd.DataFrame]):
    store = ev.store_for(settings)
    for symbol, frame in data.items():
        path = store.path(symbol, "1h")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    return store


@pytest.fixture
def no_tick(monkeypatch):
    monkeypatch.setattr(ev, "tick_for", lambda settings, symbol: None)


def started(settings, monkeypatch, now=START_AT):
    monkeypatch.setattr(registry, "current_commit", lambda: "abc123def456")
    return registry.start(settings, f19.TEST, now=now, halal=HALAL)


# --- Démarrage, inscription, gel -------------------------------------------------------------------------------------------------

def test_forward_start_f19_on_a_temporary_root(settings, monkeypatch):
    with pytest.raises(registry.DirtyCode):
        monkeypatch.setattr(registry, "current_commit", lambda: "abc123+DIRTY")
        registry.start(settings, f19.TEST, now=START_AT, halal=HALAL)
    data = started(settings, monkeypatch)
    assert data["test_id"] == "F19_PRICE_ACTION" and set(data["fingerprints"]) == {"doc", "params", "code"}
    assert pd.Timestamp(data["final_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=84)
    assert pd.Timestamp(data["interim_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=42)
    experiments = ExperimentRegistry(settings.experiments_db)
    assert experiments.program_trials("FORWARD") == 1 and experiments.program_trials("DEVELOPMENT") == 0
    with pytest.raises(registry.AlreadyStarted):
        registry.start(settings, f19.TEST, now=START_AT, halal=HALAL)
    assert registry.check_frozen(settings, f19.TEST, now=NOW) is None


def test_f19_is_registered_after_f18_and_freezes_the_price_action_modules():
    ids = [t.test_id for t, _ in TESTS]
    assert ids.index("F19_PRICE_ACTION") == ids.index("F18_ASSISTANT") + 1 and BY_ID["F19_PRICE_ACTION"][1] is f19
    names = {getattr(o, "__name__", "") for o in registry.frozen_objects(f19.TEST)}
    for name in ("crypto_signal_intelligence.forward.f19", "crypto_signal_intelligence.price_action",
                 "crypto_signal_intelligence.price_action.detect", "crypto_signal_intelligence.price_action.manage",
                 "crypto_signal_intelligence.price_action.evaluate", "crypto_signal_intelligence.price_action.state",
                 "crypto_signal_intelligence.price_action.outbox", "crypto_signal_intelligence.forward.costs",
                 "day_block_ci95", "day_block_ci", "verdict", "figure_store", "ema", "atr", "round_tick", "CandleStore"):
        assert name in names, name
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    text = registry.section(doc, f19.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("Placebos", "5 appels par jour", "1,5 R", "84 jours", "sha256", "INSUFFISANT", "aucun gain",
                  "pa:", "price_action_outbox"):
        assert value in text, value
    assert "F19_PRICE_ACTION" not in registry.section(doc, "Cadre")
    assert f19.TEST.params["detect"]["CONFIGS"] == D.CONFIGS and f19.TEST.params["max_calls_per_day"] == 5


# --- Évaluation en service ---------------------------------------------------------------------------------------------------------

def test_record_decisions_evaluates_each_close_once_and_journals_the_call(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    early = f19.record_decisions(settings, journal, start, now=T + pd.Timedelta(seconds=1), store=store)
    assert early["evaluations"] == 0 and "waiting" in early and journal.first(f19.EVALUATION) is None
    out = f19.record_decisions(settings, journal, start, now=NOW, store=store)
    assert out == {"evaluations": 1, "calls": 1, "repaired": 0}
    evaluation = journal.first(f19.EVALUATION)["data"]
    assert evaluation["at"] == T.isoformat() and evaluation["late"] is False and evaluation["delay_min"] == 5
    assert evaluation["candidates"][D.BASE_RETEST] == 1 and evaluation["universe"]["source"] == "F19_PRICE_ACTION"
    call = journal.first(f19.CALL)["data"]
    assert call["config"] == D.BASE_RETEST and call["symbol"] == "RETUSDT" and call["unit"] == "4h"
    assert call["entry"] == pytest.approx(107.2) and call["placebo_offsets_h"] == M.placebo_offsets(call["call_id"], "4h")
    assert call["call_id"] == M.signal_id(D.BASE_RETEST, "RETUSDT", T)
    assert call["tp1"] == pytest.approx(call["entry"] + call["risk"]) and call["hard_stop"] == pytest.approx(call["entry"] - 1.5 * call["risk"])
    assert f19.record_decisions(settings, journal, start, now=NOW + timedelta(hours=1), store=store) == {"evaluations": 0, "calls": 0, "repaired": 0}
    waiting = outbox.pending(settings, now=NOW)
    assert [m["id"] for m in waiting] == [f"pa:APPEL:{call['call_id']}"]
    text = waiting[0]["text"]
    assert text.splitlines()[0].startswith("Price action CSI — BASE_RETEST") and "RETUSDT" in text and "4 h" in text
    assert "Stop de clôture" in text and "stop de secours" in text and "TP1" in text and "objectif" in text and "R = " in text
    assert text.endswith("Shadow : aucun ordre. Test en direct F19, aucun gain démontré.")
    current = state.read(settings)
    assert current["available"] and current["calls_made"] == 1 and current["active_calls"][0]["call_id"] == call["call_id"]
    assert current["places_orders"] is False and len(current["resume"].splitlines()) == 4
    # Rien dans signals/ ni dans les fichiers de l'assistant.
    assert not settings.signals_db.exists() and not (settings.root / "signals").exists()
    assert list((settings.root / "state").glob("assistant*")) == []


def test_late_evaluation_is_recorded_without_any_call(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    out = f19.record_decisions(settings, journal, start, now=T + timedelta(minutes=45), store=store)
    assert out == {"evaluations": 1, "calls": 0, "repaired": 0}
    evaluation = journal.first(f19.EVALUATION)["data"]
    assert evaluation["late"] is True and evaluation["refusals_by_reason"] == {ev.LATE: 1} and journal.first(f19.CALL) is None
    assert outbox.pending(settings, now=T + timedelta(minutes=45)) == []


def fake_candidate(config: str, symbol: str, at: pd.Timestamp) -> dict:
    return {"config": config, "symbol": symbol, "at": at, "at_ns": int(D.to_ns([at])[0]), "unit": D.UNIT[config],
            "entry": 10.0, "stop": 9.0, "objective": 12.0, "risk": 1.0,
            "detail": {"squeeze_bars": 7, "bollinger_high": 9.9, "volume_multiple": 2.0}}


def test_quota_of_five_calls_per_utc_day_and_discipline(monkeypatch):
    at = pd.Timestamp("2026-10-10 08:00", tz="UTC")
    cands = [fake_candidate(D.SQUEEZE, f"P{k}USDT", at) for k in range(7)]
    monkeypatch.setattr(ev, "detect_at", lambda frames, at, btc_frame=None, blocked=None: {"candidates": cands, "refusals": [], "events": 0})
    out = ev.evaluate(at=at, now=at + timedelta(minutes=3), frames={}, discipline={"calls_today": 0})
    order = [c["symbol"] for c in sorted(cands, key=ev.priority)]                 # même instant : ordre des identifiants
    assert [c["symbol"] for c in out["calls"]] == order[:5] and out["refusals_by_reason"] == {ev.QUOTA: 2}
    assert ev.priority(fake_candidate(D.SQUEEZE, "ZUSDT", at + timedelta(hours=4))) < ev.priority(cands[0])   # plus récent d'abord
    blocked = {"active": {f"{order[0]}:SQUEEZE": "x"}, "rest_until": {f"{order[1]}:SQUEEZE": (at + timedelta(hours=1)).isoformat(),
                                                                       f"{order[2]}:SQUEEZE": (at - timedelta(hours=1)).isoformat()}}
    out = ev.evaluate(at=at, now=at + timedelta(minutes=3), frames={}, discipline={"calls_today": 4} | blocked)
    assert [c["symbol"] for c in out["calls"]] == [order[2]]
    assert out["refusals_by_reason"] == {ev.ACTIVE: 1, ev.REST: 1, ev.QUOTA: 4}


def test_discipline_is_read_from_the_journal_per_pair_and_configuration(settings, monkeypatch):
    started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, {})
    base = {"entry": 100.0, "stop": 96.0, "tp1": 104.0, "hard_stop": 94.0, "objective": 108.0, "risk": 4.0, "unit": "4h"}
    journal.append(f19.CALL, base | {"call_id": "a", "symbol": "AUSDT", "config": D.SQUEEZE, "at": T.isoformat()}, now=NOW)
    journal.append(f19.CALL, base | {"call_id": "b", "symbol": "AUSDT", "config": D.INSIDE_DAY, "at": T.isoformat(), "unit": "1d"}, now=NOW)
    old = (T - timedelta(days=5)).isoformat()
    journal.append(f19.CALL, base | {"call_id": "c", "symbol": "BUSDT", "config": D.SQUEEZE, "at": old}, now=NOW)
    journal.append(f19.RESOLUTION, {"call_id": "c", "status": "RESOLU", "results": {"central": {"exit_at": (T - timedelta(days=1)).isoformat()}}}, now=NOW)
    d = f19.discipline_at(journal, store, at=T + timedelta(hours=4), now=NOW + timedelta(hours=4))
    assert d["active"] == {"AUSDT:SQUEEZE": "a", "AUSDT:INSIDE_DAY": "b"} and d["calls_today"] == 2
    assert d["rest_until"] == {"BUSDT:SQUEEZE": (T + timedelta(days=1)).isoformat()}


# --- Résolution, mesures, verdict -----------------------------------------------------------------------------------------------

def test_resolve_call_and_placebos_then_verdict_per_configuration(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    f19.record_decisions(settings, journal, start, now=NOW, store=store)
    call = journal.first(f19.CALL)["data"]
    store = write_store(settings, frames(extra=rising()))
    end = pd.Timestamp(call["resolution_end"])
    assert end == T + pd.Timedelta(hours=84) + pd.Timedelta(days=10)
    assert f19.resolve(settings, journal, now=NOW + timedelta(days=3), store=store) == {}            # placebos futurs en cours
    assert f19.resolve(settings, journal, now=end + timedelta(hours=1), store=store) == {f19.RESOLVED: 1}
    res = journal.first(f19.RESOLUTION)["data"]
    central = res["results"]["central"]
    assert central["outcome"] == M.TARGET and central["r"] > 0 and len(central["placebos"]) == 20
    assert central["excess"] == pytest.approx(central["r"] - central["placebo_mean"], abs=1e-6)
    assert res["results"]["defavorable"]["r"] < central["r"]
    assert outbox.pending(settings, now=end + timedelta(hours=1))[-1]["id"] == f"pa:RESOLUTION:{call['call_id']}"
    running = f19.stats(journal, start, now=end + timedelta(hours=1))
    assert running["configs"][D.BASE_RETEST]["resolved"] == 1 and running["verdict"][D.BASE_RETEST] == "EN_COURS"
    final = pd.Timestamp(start["final_at"]) + timedelta(days=1)
    verdicts = f19.stats(journal, start, now=final)["verdict"]
    assert verdicts == dict.fromkeys(D.CONFIGS, "INSUFFISANT")
    assert f19.finalize(journal, start, now=final) == "VERDICT" and f19.finalize(journal, start, now=final) == "CLOTURE"
    assert registry.status(settings, f19.TEST, now=final)["state"] == registry.CLOSED and journal.verify()["ok"]


def test_a_gap_is_declared_two_days_after_the_window(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    f19.record_decisions(settings, journal, start, now=NOW, store=store)
    call = journal.first(f19.CALL)["data"]
    end = pd.Timestamp(call["resolution_end"])
    assert f19.resolve(settings, journal, now=end + timedelta(days=1), store=store) == {}
    assert f19.resolve(settings, journal, now=end + f19.GAP_AFTER, store=store) == {f19.GAP: 1}
    assert journal.first(f19.RESOLUTION)["data"]["results"] is None


def test_daily_report_renders_f19(settings, monkeypatch):
    from crypto_signal_intelligence.forward import report
    started(settings, monkeypatch)
    text = report.markdown(report.build(settings, now=NOW))
    assert "## F19_PRICE_ACTION" in text and "| BASE_RETEST |" in text and "aucun gain démontré" in text


# --- Boîte Telegram fusionnée, route, carte ----------------------------------------------------------------------------------------

def test_outbox_is_merged_chronologically_and_marked_in_the_right_box(settings, monkeypatch):
    now = pd.Timestamp("2026-10-10 08:30", tz="UTC")
    api = CsiApi(settings, now=lambda: now.to_pydatetime())
    assistant_outbox.queue(settings, message_id="APPEL:a1", text="assistant 1", now=now - timedelta(minutes=20))
    outbox.queue(settings, message_id="APPEL:p1", text="price action 1", now=now - timedelta(minutes=10))
    assistant_outbox.queue(settings, message_id="APPEL:a2", text="assistant 2", now=now - timedelta(minutes=5))
    messages = api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]
    assert [m["id"] for m in messages] == ["APPEL:a1", "pa:APPEL:p1", "APPEL:a2"]
    for k in range(25):
        outbox.queue(settings, message_id=f"APPEL:x{k:02d}", text="x", now=now - timedelta(minutes=4) + timedelta(seconds=k))
    assert len(api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]) == 20
    monkeypatch.setenv("CSI_API_TOKEN", "jeton-de-test")
    assert api.dispatch("POST", "/assistant/sent", {}, {"ids": ["APPEL:a1", "pa:APPEL:p1", "pa:inconnu"]}) == {"marked": 2}
    left = [m["id"] for m in api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]]
    assert "APPEL:a1" not in left and "pa:APPEL:p1" not in left and "APPEL:a2" in left
    assert assistant_outbox.counts(settings)["ENVOYE"] == 1 and outbox.counts(settings)["ENVOYE"] == 1


def test_price_action_route_and_token(settings, monkeypatch):
    now = pd.Timestamp("2026-10-10 08:30", tz="UTC")
    api = CsiApi(settings, now=lambda: now.to_pydatetime())
    empty = api.dispatch("GET", "/price-action", {}, None)
    assert empty["available"] is False and empty["places_orders"] is False
    state.write(settings, state.build({"at": T.isoformat(), "candidates": {D.SQUEEZE: 1}}, active_calls=[], last_refusals=[], now=now))
    assert api.dispatch("GET", "/price-action", {}, None)["available"] is True
    with pytest.raises(ApiError):
        api.dispatch("POST", "/price-action", {}, {})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, token="jeton-de-test"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(base + "/price-action", timeout=10)
        assert refused.value.code == 401
        request = urllib.request.Request(base + "/price-action", headers={"Authorization": "Bearer jeton-de-test"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert json.loads(response.read())["available"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_card_sits_under_the_assistant_in_the_market_tab():
    from crypto_signal_intelligence.api.server import STATIC_DIR
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    market = page[page.index('id="tab-market"'):]
    assert market.index('id="assistant-result"') < market.index('id="price-action-result"') < market.index('id="meteo-result"')
    assert 'api("/price-action")' in script and "loadPriceAction();" in script and "Price action (shadow, test F19)" in script
    assert "aucun gain démontré" in script and "aucun ordre" in script
