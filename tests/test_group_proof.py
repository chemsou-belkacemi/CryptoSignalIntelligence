"""Avis lié au groupe (docs/EXTERNAL_SIGNALS.md) : preuve en direct et preuve sur historique, chacune avec ses
critères ; ce qu'elle débloque (vetos de géométrie) et ce qu'elle ne débloque jamais (refus, signal périmé).
Valeurs SYNTHÉTIQUES."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import explain
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.external import audit as au
from crypto_signal_intelligence.external import record as rec
from crypto_signal_intelligence.external.evaluate import evaluate
from crypto_signal_intelligence.external.parser import group_of
from crypto_signal_intelligence.external.registry import ExternalSignalRegistry
from crypto_signal_intelligence.features import indicators as ind

from .conftest import canonical

T0 = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()


def live_signals(registry, source: str, count: int, *, r: float, base: float, days: int, start=T0) -> None:
    for k in range(count):
        sid = registry.record(received_at=start + timedelta(days=k % days, minutes=k), source=source,
                              content_hash=f"{source}{k}", template="structured", symbol="ETHUSDT", entry=100.0,
                              stop=98.0, tp1=104.0, targets=[104.0], decision_time=None, close=100.0,
                              verdict="INDETERMINE", p_tp1=0.4, base_expectancy_r=base, evaluation={}, raw_text="x",
                              resolvable=True)
        value = r if k % 4 else r - 0.4                     # un peu de dispersion
        registry.mark(sid, "TP1_FIRST" if value > 0 else "SL_FIRST", value, start, start + timedelta(days=k % days))


# --- Preuve en direct ---------------------------------------------------------------------------------------------

def test_live_proof_needs_thirty_signals_fifteen_days_and_both_intervals_above_zero(settings):
    registry = ExternalSignalRegistry(settings.external_db)
    live_signals(registry, "Prouvé", 30, r=0.6, base=-0.1, days=15)
    live_signals(registry, "Court", 29, r=0.6, base=-0.1, days=15)
    live_signals(registry, "Serré", 30, r=0.6, base=-0.1, days=14)
    live_signals(registry, "Marché", 30, r=0.6, base=0.55, days=15)       # gagne, mais pas mieux que le hasard
    live_signals(registry, "Perdant", 30, r=-0.2, base=-0.6, days=15)     # mieux que le hasard, mais perd
    records = {r.source: r for r in rec.source_records(registry, samples=600)}
    assert records["Prouvé"].proven and "prouvé en direct" in records["Prouvé"].proof
    assert (records["Prouvé"].resolved, records["Prouvé"].days) == (30, 15) and records["Prouvé"].r_ci95[0] > 0
    assert not records["Court"].proven and "29/30" in records["Court"].proof
    assert not records["Serré"].proven and "14/15" in records["Serré"].proof
    assert not records["Marché"].proven and records["Marché"].r_ci95[0] > 0 and records["Marché"].edge_ci95[0] <= 0
    assert not records["Perdant"].proven and records["Perdant"].edge_ci95[0] > 0 and "non prouvé" in records["Perdant"].proof
    assert rec.source_record(registry, "Prouvé", seed=1).proven and rec.source_record(registry, "Absent") is None


# --- Preuve sur historique ------------------------------------------------------------------------------------------

def entry(resolved: int = 60, days: int = 25, ci=(0.05, 0.4), missing=0.02) -> dict:
    return {"messages_supprimes_part": missing,
            "conventions": {au.LADDER: {"resolus": resolved, "jours": days, "ic95": ci, "r_moyen": 0.2}}}


def test_history_proof_needs_every_criterion():
    assert au.history_proof(entry())["proven"]
    assert au.history_proof(entry(missing=None))["proven"]               # reçus en direct : rien de supprimé
    failing = {"assez_de_signaux": entry(resolved=49), "assez_de_jours": entry(days=19),
               "gain_moyen_positif_ic95": entry(ci=(0.0, 0.4)), "peu_de_messages_supprimes": entry(missing=0.11)}
    for check, case in failing.items():
        proof = au.history_proof(case)
        assert not proof["proven"] and [k for k, ok in proof["checks"].items() if not ok] == [check], check
    assert not au.history_proof(entry(ci=None))["proven"]
    assert au.history_proof(entry())["convention"] == au.LADDER == au.PROOF_CONVENTION


def test_deleted_messages_are_counted_from_telegram_numbering():
    def export(ids):
        return {"name": "G", "messages": [{"id": i, "type": "message", "date_unixtime": str(1772445600 + i),
                                           "text": f"message {i}"} for i in ids]}

    assert au.read_telegram_export(export(range(1, 101)))[0].missing_share == 0.0
    holes = [i for i in range(1, 101) if i % 5]                          # un message sur cinq supprimé
    assert au.read_telegram_export(export(holes))[0].missing_share == pytest.approx(19 / 99, abs=1e-4)
    summary = au.summarize([au.AuditRow(received_at=T0.isoformat(), group="G", status="ILLISIBLE", missing_share=0.2),
                            au.AuditRow(received_at=T0.isoformat(), group="G", status="ILLISIBLE", missing_share=0.05)],
                           samples=100, seed=1)
    assert summary["G"]["messages_supprimes_part"] == 0.2 and not summary["G"]["preuve"]["proven"]


def test_history_proof_is_saved_per_group_and_expires(settings):
    proven = {"messages": 60, "statuts": {}, "messages_supprimes_part": 0.0, "conventions": {}, "preuve": au.history_proof(entry())}
    weak = proven | {"preuve": au.history_proof(entry(resolved=10))}
    report = au.AuditReport(generated_at=pd.Timestamp(T0).isoformat(), weights="early", rows=[],
                            summary={"Bon": proven, "Faible": weak, au.ALL: proven}, notes=[])
    au.save_history(settings, report)
    assert au.latest_history(settings, "Bon", now=T0 + timedelta(days=29))["proven"]
    expired = au.latest_history(settings, "Bon", now=T0 + timedelta(days=31))
    assert not expired["proven"] and "à refaire" in expired["text"]
    assert not au.latest_history(settings, "Faible", now=T0)["proven"]
    assert au.latest_history(settings, au.ALL, now=T0) is None and au.latest_history(settings, "Inconnu", now=T0) is None
    newer = au.AuditReport(generated_at=pd.Timestamp(T0 + timedelta(days=2)).isoformat(), weights="early", rows=[],
                           summary={"Bon": weak}, notes=[])
    au.save_history(settings, newer)
    assert not au.latest_history(settings, "Bon", now=T0 + timedelta(days=3))["proven"]   # la plus récente compte


# --- Effet sur l'avis ------------------------------------------------------------------------------------------------

@pytest.fixture
def market(settings):
    store = CandleStore(settings.data_dir)
    setup = canonical(4000, symbol="ETHUSDT", seed=5)
    store.save(setup, "ETHUSDT", "15m")
    store.save(canonical(1000, "1h", symbol="ETHUSDT", seed=6), "ETHUSDT", "1h")
    store.save(canonical(1000, "1h", symbol="BTCUSDT", seed=7), "BTCUSDT", "1h")
    last = setup.iloc[-1]
    close = float(last["close"])
    atr = float(ind.atr(setup["high"], setup["low"], setup["close"]).iloc[-1])
    return close, atr, last["available_at"].to_pydatetime() + timedelta(minutes=1)


def signal(entry: float, stop: float, tp1: float, header: str = "LEGEND TRADING INDICATOR") -> str:
    return (f"👑 {header} 👑\nPAIR: ETH/USDT\nENTRY 1: {entry:.2f}\nT1: {tp1:.2f}\nT2: {tp1 * 1.01:.2f}\n"
            f"SL: {stop:.2f}\nPLATFORM: Binance")


def prove(settings, source: str, now, proven: bool = True) -> None:
    preuve = au.history_proof(entry() if proven else entry(resolved=5))
    au.save_history(settings, au.AuditReport(pd.Timestamp(now).isoformat(), "early", [],
                                             {source: {"preuve": preuve}}, []))


def test_a_proven_group_lifts_geometry_vetoes_but_never_refusals(settings, market):
    close, atr, now = market
    wide = signal(close * 1.001, close * 1.001 - 9 * atr, close * 1.001 + 0.5 * atr)       # stop large, TP1 proche
    blind = evaluate(settings, wide, source="LEGEND TRADING INDICATOR", now=now, record=False)
    assert blind.verdict == "DEFAVORABLE" and {c.label for c in blind.failed} == {"distance du stop", "RR TP1 net de coûts"}
    prove(settings, "LEGEND TRADING INDICATOR", now)
    lifted = evaluate(settings, wide, source="LEGEND TRADING INDICATOR", now=now, record=False)
    assert lifted.verdict == "FAVORABLE" and lifted.verdict_basis == "groupe"
    assert lifted.source_proof["basis"] == "historique" and lifted.source_proof["proven"]
    assert "historique" in explain(lifted.to_dict()) and "pas une garantie" in explain(lifted.to_dict())
    late = evaluate(settings, signal(close * 1.05, close * 0.95, close * 1.10), source="LEGEND TRADING INDICATOR",
                    now=now, record=False)
    assert late.verdict == "DEFAVORABLE" and late.failed[0].label == "entrée par rapport au dernier prix"
    dead = evaluate(settings, signal(close * 1.001, close * 1.0005, close * 1.01), source="LEGEND TRADING INDICATOR",
                    now=now, record=False)
    assert dead.verdict == "REFUSE"
    stale = evaluate(settings, wide, source="LEGEND TRADING INDICATOR", now=now + timedelta(hours=3), record=False)
    assert stale.verdict == "REFUSE"
    other = evaluate(settings, wide, source="Autre groupe", now=now, record=False)
    assert other.verdict == "DEFAVORABLE" and other.verdict_basis == ""                   # la preuve est par groupe
    prove(settings, "LEGEND TRADING INDICATOR", now + timedelta(minutes=1), proven=False)
    assert evaluate(settings, wide, source="LEGEND TRADING INDICATOR", now=now + timedelta(minutes=2),
                    record=False).verdict == "DEFAVORABLE"


def test_a_live_proof_also_counts_and_generic_names_take_the_header(settings, market):
    close, atr, now = market
    registry = ExternalSignalRegistry(settings.external_db)
    live_signals(registry, "LEGEND TRADING INDICATOR", 30, r=0.6, base=-0.1, days=15, start=now - timedelta(days=40))
    wide = signal(close * 1.001, close * 1.001 - 9 * atr, close * 1.001 + 0.5 * atr)
    named = evaluate(settings, wide, source="telegram 8793686453", now=now, record=False)
    assert named.source == "LEGEND TRADING INDICATOR" == group_of(wide)
    assert named.verdict == "FAVORABLE" and named.source_proof["basis"] == "direct"
    plain = evaluate(settings, wide.split("\n", 1)[1], source="telegram 8793686453", now=now, record=False)
    assert plain.source == "telegram 8793686453" and plain.verdict == "DEFAVORABLE"       # aucun en-tête : nom gardé
    assert evaluate(settings, wide, source="Mon groupe", now=now, record=False).source == "Mon groupe"
    live_signals(registry, "Débutant", 10, r=0.6, base=-0.1, days=10, start=now - timedelta(days=40))
    young = evaluate(settings, wide, source="Débutant", now=now, record=False)
    assert young.verdict == "DEFAVORABLE" and not young.source_proof["proven"] and "10/30" in young.source_proof["proof"]


def test_the_summary_says_how_often_tp1_must_be_hit(settings, market):
    close, atr, now = market
    entry_price = close * 1.001
    ev = evaluate(settings, signal(entry_price, entry_price - 2 * atr, entry_price + 1.2 * atr), source="G", now=now,
                  record=False)
    ratio = ev.geometry["rr_net_tp1_central"]
    text = explain(ev.to_dict())
    assert ratio > 0 and f"plus de {1 / (1 + ratio) * 100:.0f} %" in text and "Gagner souvent ne suffit pas" in text
    assert "l'historique comparable en donne" in text


# --- Route de l'API et page ----------------------------------------------------------------------------------------

def test_history_route_measures_the_export_saves_proofs_and_guards_its_input(settings):
    from http import HTTPStatus

    from crypto_signal_intelligence.api.server import ApiError, CsiApi

    now = pd.Timestamp("2026-06-01", tz="UTC").to_pydatetime()
    api = CsiApi(settings, now=lambda: now)
    start = pd.Timestamp("2026-03-02", tz="UTC")
    candles = pd.DataFrame([(start + k * pd.Timedelta(minutes=15), 101.0, 101.0, 101.0, 101.0) for k in range(200)],
                           columns=["open_time", "open", "high", "low", "close"])
    api.audit_bars = lambda symbol, first, last: candles
    message = "👑 LEGEND TRADING INDICATOR 👑\n#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95 (4h)"
    export = {"name": "Canal", "messages": [
        {"id": 1, "type": "message", "date_unixtime": str(int((start + pd.Timedelta(hours=10)).timestamp())), "text": message},
        {"id": 3, "type": "service"}]}
    result = api.dispatch("POST", "/sources/history", {}, {"export": export, "weights": "equal"})
    group = result["summary"]["LEGEND TRADING INDICATOR"]
    assert group["statuts"] == {"OK": 1} and group["messages_supprimes_part"] == round(1 / 3, 4)
    assert not group["preuve"]["proven"] and result["rows"][0]["symbol"] == "ABCUSDT" and result["messages"] == 1
    assert (settings.reports_dir / result["report"] / "audit.json").exists()
    saved = api.dispatch("GET", "/sources/history", {}, None)["groups"]
    assert [g["source"] for g in saved] == ["LEGEND TRADING INDICATOR"] and saved[0]["proven"] is False
    for bad in ({"export": export, "weights": "late"}, {"export": "x"}, {"export": {"name": "Vide", "messages": []}}):
        with pytest.raises(ApiError) as error:
            api.dispatch("POST", "/sources/history", {}, bad)
        assert error.value.status == HTTPStatus.BAD_REQUEST
    api._history_lock.acquire()
    try:
        with pytest.raises(ApiError) as busy:
            api.dispatch("POST", "/sources/history", {}, {"export": export})
        assert busy.value.status == HTTPStatus.CONFLICT
    finally:
        api._history_lock.release()


def test_only_the_history_route_accepts_a_large_body(settings):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from crypto_signal_intelligence.api.server import CsiApi, make_handler

    handler = make_handler(CsiApi(settings), token=None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(path: str, body: bytes) -> int:
        request = urllib.request.Request(base + path, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    big = json.dumps({"export": {"name": "G", "messages": [{"id": i, "type": "service", "text": "x" * 200}
                                                           for i in range(2000)]}}).encode()
    try:
        assert len(big) > 300_000
        assert post("/sources/history", big) == 400                       # lu entièrement : aucun message texte
        assert post("/evaluate", big) == 413
        assert post("/sources/history", b"x" * (8 * 1024 * 1024 + 1)) == 413
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_page_offers_the_history_import():
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "src" / "crypto_signal_intelligence" / "api" / "static"
    page, script = (static / "index.html").read_text(encoding="utf-8"), (static / "app.js").read_text(encoding="utf-8")
    assert 'id="history-file"' in page and 'id="history-run"' in page and "Exporter l'historique" in page
    assert '"/sources/history"' in script and "slimExport" in script and "date_unixtime" in script
