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


def at_clock(moment):
    """Horloge injectée : l'heure réelle d'évaluation vaut `moment` (les tests rejouent des dates passées)."""
    return lambda: moment


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
                 "day_block_ci95", "day_block_ci", "figure_store", "ema", "atr", "round_tick", "CandleStore"):
        assert name in names, name
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    text = registry.section(doc, f19.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("Placebos", "5 appels par jour", "1,5 R", "84 jours", "sha256", "INSUFFISANT", "aucun gain",
                  "pa:", "price_action_outbox", "R net seul", "SUPERIEUR_A_ZERO", "descriptif"):
        assert value in text, value
    assert "F19_PRICE_ACTION" not in registry.section(doc, "Cadre")
    assert f19.TEST.params["detect"]["CONFIGS"] == D.CONFIGS and f19.TEST.params["max_calls_per_day"] == 5


# --- Évaluation en service ---------------------------------------------------------------------------------------------------------

def test_record_decisions_evaluates_each_close_once_and_journals_the_call(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    early = f19.record_decisions(settings, journal, start, now=T + pd.Timedelta(seconds=1), store=store, clock=at_clock(T + pd.Timedelta(seconds=1)))
    assert early["evaluations"] == 0 and "waiting" in early and journal.first(f19.EVALUATION) is None
    out = f19.record_decisions(settings, journal, start, now=NOW, store=store, clock=at_clock(NOW))
    assert out == {"evaluations": 1, "calls": 1, "repaired": 0}
    evaluation = journal.first(f19.EVALUATION)["data"]
    assert evaluation["at"] == T.isoformat() and evaluation["late"] is False and evaluation["delay_min"] == 5
    assert evaluation["candidates"][D.BASE_RETEST] == 1 and evaluation["universe"]["source"] == "F19_PRICE_ACTION"
    assert evaluation["pairs_with_close"] == 2 and evaluation["pass_started_at"] == NOW.isoformat()
    call = journal.first(f19.CALL)["data"]
    assert call["config"] == D.BASE_RETEST and call["symbol"] == "RETUSDT" and call["unit"] == "4h"
    assert call["entry"] == pytest.approx(107.2) and call["placebo_offsets_h"] == M.placebo_offsets(call["call_id"], "4h")
    assert call["call_id"] == M.signal_id(D.BASE_RETEST, "RETUSDT", T)
    assert call["tp1"] == pytest.approx(call["entry"] + call["risk"]) and call["hard_stop"] == pytest.approx(call["entry"] - 1.5 * call["risk"])
    assert f19.record_decisions(settings, journal, start, now=NOW + timedelta(hours=1), store=store, clock=at_clock(NOW + timedelta(hours=1))) == {"evaluations": 0, "calls": 0, "repaired": 0}
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
    out = f19.record_decisions(settings, journal, start, now=T + timedelta(minutes=45), store=store, clock=at_clock(T + timedelta(minutes=45)))
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
    f19.record_decisions(settings, journal, start, now=NOW, store=store, clock=at_clock(NOW))
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
    f19.record_decisions(settings, journal, start, now=NOW, store=store, clock=at_clock(NOW))
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


def test_f19_verdict_uses_net_r_only():
    def block(n=40, days=20, low=0.1, high=0.5, low95=0.05, high95=0.6, excess_ci=(-1.0, -0.5)):
        return {"n": n, "days": days, "r_ci_decision": (low, high), "r_ci95": (low95, high95), "placebo_excess_ci": excess_ci}
    assert f19.verdict({"central": block(), "defavorable": block()}, ended=False) == "EN_COURS"
    assert f19.verdict({"central": block(), "defavorable": block()}, ended=True) == "SUPERIEUR_A_ZERO"   # excès ignoré
    assert f19.verdict({"central": block(), "defavorable": block(low=-0.01)}, ended=True) == "NON_DEMONTRE"
    assert f19.verdict({"central": block(low=-0.5, high=-0.1, low95=-0.4, high95=-0.05),
                        "defavorable": block(low=-0.6, high=-0.2, low95=-0.5, high95=-0.1)}, ended=True) == "INFERIEUR_A_ZERO"
    assert f19.verdict({"central": block(n=29), "defavorable": block()}, ended=True) == "INSUFFISANT"
    assert f19.verdict({"central": block(days=9), "defavorable": block()}, ended=True) == "INSUFFISANT"


def test_delay_is_measured_on_the_real_clock_not_the_pass_start(settings, monkeypatch, no_tick):
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    store = write_store(settings, frames())
    # Le passage a commencé 5 min après la clôture, mais F19 n'évalue que 45 min après (tests précédents lents).
    out = f19.record_decisions(settings, journal, start, now=NOW, store=store, clock=at_clock(T + timedelta(minutes=45)))
    evaluation = journal.first(f19.EVALUATION)["data"]
    assert out["calls"] == 0 and evaluation["late"] is True and evaluation["delay_min"] == 45
    assert evaluation["pass_started_at"] == NOW.isoformat() and evaluation["refusals_by_reason"] == {ev.LATE: 1}
    assert f19.real_clock().tzinfo is not None


def test_per_pair_reading_gives_the_same_candidates_and_reads_only_useful_columns(settings):
    data = frames()
    store = write_store(settings, data)
    with_frames = ev.detect_at(data, T)
    loaded = ev.inputs_for(settings, at=T, now=NOW, store=store, symbols=list(data))
    assert all(isinstance(b, D.Bars) for b in loaded["frames"].values()) and loaded["pairs_with_close"] == 2
    with_bars = ev.detect_at(loaded["frames"], T, btc_frame=loaded["btc_frame"])
    key = lambda out: [(c["config"], c["symbol"], c["at_ns"], c["entry"], c["stop"], c["objective"]) for c in out["candidates"]]  # noqa: E731
    assert key(with_frames) == key(with_bars) and len(key(with_bars)) == 1
    columns = set(ev.load_h1(store, "RETUSDT", at=T, now=NOW).columns)
    assert columns == set(ev.READ_COLUMNS)
    with pytest.raises(ValueError):                                     # des Bars qui voient l'avenir sont refusées
        ev.detect_at({"RETUSDT": D.Bars(data["RETUSDT"])}, T)


def test_falsified_future_in_the_store_changes_nothing(settings, tmp_path):
    """Mutation du futur PAR LE MAGASIN : bougies falsifiées après T, dont la bougie en formation déjà « publiée »."""
    import numpy as np

    from crypto_signal_intelligence.data.store import CandleStore

    def evaluate(data, root):
        store = CandleStore(root)
        for symbol, frame in data.items():
            path = store.path(symbol, "1h")
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
        out = ev.run(settings, at=T, now=NOW, discipline={"calls_today": 0}, store=store, symbols=list(data))
        return ([(c["symbol"], c["config"], c["entry"], c["stop"], c["objective"], c["tp1"], c["hard_stop"]) for c in out["calls"]],
                out["candidates"], out["refusals"])
    reference = evaluate(frames(), tmp_path / "vrai")
    rng = np.random.default_rng(1)
    lied = {}
    for symbol, frame in frames(extra=rising()).items():
        frame = frame.copy()
        after = frame["open_time"] >= T
        n = int(after.sum())
        noise = 100 * rng.lognormal(0, 1, n)
        frame.loc[after, ["open", "close"]] = np.c_[noise, noise[::-1]]
        frame.loc[after, "high"] = np.maximum(noise, noise[::-1]) * 2
        frame.loc[after, "low"] = np.minimum(noise, noise[::-1]) * 0.3
        frame.loc[after, "quote_volume"] = 1e9
        frame.loc[frame["open_time"] == T, "available_at"] = T + pd.Timedelta(seconds=1)
        lied[symbol] = frame
    assert evaluate(lied, tmp_path / "faux") == reference and len(reference[0]) == 1


def test_after_f15_ends_the_store_is_refreshed_and_f19_still_evaluates_and_resolves(settings, monkeypatch, no_tick):
    from crypto_signal_intelligence.forward import f15, runner
    f15_start = T - pd.Timedelta(days=100)                                 # F15 terminé 16 jours avant T
    registry.start(settings, f15.TEST, now=f15_start, allow_dirty=True, halal=HALAL)
    start = started(settings, monkeypatch)
    journal = registry.journal_for(settings, f19.TEST_ID)
    full = frames(extra=rising())
    write_store(settings, {s: f[f["open_time"] + pd.Timedelta(hours=1) <= T - pd.Timedelta(days=2)] for s, f in full.items()})
    calls = []

    def fake_download(settings_, symbol, timeframe, *, now, rest_client, rest_only):
        """Télécharge (ici : recopie) les bougies 1 h connues à `now` dans le magasin de F15."""
        assert rest_only and timeframe == "1h" and settings_.root == settings.root / "forward_figures"
        calls.append((symbol, pd.Timestamp(now)))
        frame = full[symbol]
        known = frame[frame["available_at"] <= pd.Timestamp(now)]
        write_store(settings, {symbol: known})
    monkeypatch.setattr("crypto_signal_intelligence.data.pipeline.download", fake_download)
    assert registry.status(settings, f15.TEST, now=NOW)["state"] == registry.ENDED
    assert f15.record_decisions(settings, registry.journal_for(settings, f15.TEST_ID), registry.journal_for(settings, f15.TEST_ID).first(registry.START)["data"], now=NOW)["figures"] == 0
    refreshed = runner.refresh_figure_store(settings, now=NOW, rest=object())
    assert refreshed == {"pairs": 2, "errors": 0, "readers": ["F19_PRICE_ACTION"]} and {c[0] for c in calls} == {"BTCUSDT", "RETUSDT"}
    store = ev.store_for(settings)
    out = f19.record_decisions(settings, journal, start, now=NOW, store=store, clock=at_clock(NOW))
    assert out["calls"] == 1
    call = journal.first(f19.CALL)["data"]
    end = pd.Timestamp(call["resolution_end"]) + timedelta(hours=1)
    runner.refresh_figure_store(settings, now=end, rest=object())
    assert f19.resolve(settings, journal, now=end, store=store) == {f19.RESOLVED: 1}
    stats = f19.stats(journal, start, now=end)
    assert stats["configs"][D.BASE_RETEST]["gaps"] == 0
    assert stats["configs"][D.BASE_RETEST]["scenarios"]["central"]["r_by_pair"]["top"][0]["symbol"] == "RETUSDT"
    # Pas de mise à jour tant que F15 tourne, ni quand plus aucun lecteur n'en dépend.
    assert runner.refresh_figure_store(settings, now=f15_start + timedelta(days=1), rest=object()) is None
    monkeypatch.setattr(runner, "STORE_READERS", ())
    assert runner.refresh_figure_store(settings, now=end, rest=object()) is None
