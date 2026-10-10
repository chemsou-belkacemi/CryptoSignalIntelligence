"""Tests en direct séparés F20 à F24 (forward/pa_single.py, f20.py … f24.py) : une configuration « price action »
chacun, mêmes règles que F19, quota PROPRE de 5 appels par jour. Démarrage sur une racine temporaire, mêmes candidats
que F19 avant quota, quota indépendant des autres configurations, doublons avec F19 (pas de message,
`aussi_dans_F19`), placebos reproductibles à graine propre, retard, verdict, causalité par le magasin, détection
partagée une seule fois, rafraîchissement du magasin, fusion des trois boîtes Telegram et relais, route, carte, rapport,
et aucune écriture dans `signals/` ni `state/assistant*`. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.assistant import outbox as assistant_outbox
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward import f19, f20, f21, f22, f23, f24, pa_single, registry, runner
from crypto_signal_intelligence.forward.tests import BY_ID, TESTS
from crypto_signal_intelligence.price_action import detect as D
from crypto_signal_intelligence.price_action import evaluate as ev
from crypto_signal_intelligence.price_action import manage as M
from crypto_signal_intelligence.price_action import outbox
from crypto_signal_intelligence.research import price_action_h0 as H
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .conftest import PROJECT
from .test_forward_f19 import HALAL, NOW, START_AT, T, at_clock, frames, rising, write_store

MODULES = {"F20_BASE_RETEST": f20, "F21_SQUEEZE": f21, "F22_FORCE_RELATIVE": f22, "F23_INSIDE_DAY": f23,
           "F24_SORTIE_BASE_LONGUE": f24}
HOUR = pd.Timedelta(hours=1)


@pytest.fixture(autouse=True)
def fresh_cache():
    pa_single.clear_cache()
    yield
    pa_single.clear_cache()


@pytest.fixture
def no_tick(monkeypatch):
    monkeypatch.setattr(ev, "tick_for", lambda settings, symbol: None)


def start(settings, monkeypatch, test, now=START_AT):
    monkeypatch.setattr(registry, "current_commit", lambda: "abc123def456")
    return registry.start(settings, test, now=now, halal=HALAL)


# --- Démarrage, inscription, gel -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("test_id", sorted(MODULES))
def test_forward_start_each_separate_test_on_a_temporary_root(settings, monkeypatch, test_id):
    module = MODULES[test_id]
    with pytest.raises(registry.DirtyCode):
        monkeypatch.setattr(registry, "current_commit", lambda: "abc123+DIRTY")
        registry.start(settings, module.TEST, now=START_AT, halal=HALAL)
    data = start(settings, monkeypatch, module.TEST)
    assert data["test_id"] == test_id and set(data["fingerprints"]) == {"doc", "params", "code"}
    assert pd.Timestamp(data["final_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=84)
    assert pd.Timestamp(data["interim_at"]) - pd.Timestamp(data["started_at"]) == pd.Timedelta(days=42)
    assert ExperimentRegistry(settings.experiments_db).program_trials("FORWARD") == 1
    with pytest.raises(registry.AlreadyStarted):
        registry.start(settings, module.TEST, now=START_AT, halal=HALAL)
    assert registry.check_frozen(settings, module.TEST, now=NOW) is None
    assert module.TEST.params["config"] == pa_single.CONFIG_OF[test_id] and module.TEST.params["max_calls_per_day"] == 5
    assert module.TEST.params["manage"]["seed_prefix"] == test_id and module.TEST.params["tests"] == 5


def test_separate_tests_come_after_f19_and_freeze_their_rules():
    ids = [t.test_id for t, _ in TESTS]
    assert ids[ids.index("F19_PRICE_ACTION") + 1:] == list(MODULES)
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    assert doc.index("## F19_PRICE_ACTION") < doc.index("## F20_BASE_RETEST") < doc.index("## F24_SORTIE_BASE_LONGUE") \
        < doc.index("## LECTURE_TP_MAHWASHI")
    for test_id, module in MODULES.items():
        assert BY_ID[test_id][1] is module and test_id == module.TEST_ID
        names = {getattr(o, "__name__", "") for o in registry.frozen_objects(module.TEST)}
        for name in (f"crypto_signal_intelligence.forward.{test_id[:3].lower()}", "crypto_signal_intelligence.forward.pa_single",
                     "crypto_signal_intelligence.forward.f19", "crypto_signal_intelligence.price_action",
                     "crypto_signal_intelligence.price_action.detect", "crypto_signal_intelligence.price_action.manage",
                     "crypto_signal_intelligence.price_action.evaluate", "crypto_signal_intelligence.price_action.state",
                     "crypto_signal_intelligence.price_action.outbox", "crypto_signal_intelligence.forward.costs",
                     "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal",
                     "day_block_ci95", "day_block_ci", "figure_store", "ema", "atr", "true_range", "round_tick", "CandleStore"):
            assert name in names, (test_id, name)
        assert module.TEST.config_keys == ("data.assumed_availability_latency_seconds",)
        text = registry.section(doc, test_id)
        assert text is not None and registry.missing_fields(text) == []
        for value in ("F19_PRICE_ACTION", "PRICE_ACTION.md", "5 appels par jour", "quota propre", f'sha256("{test_id}:"',
                      "23 tests en direct", "aussi_dans_F19", "TROU", "Chevauchement avec F19", "Puissance attendue",
                      "price_action_single_outbox", "ps:", "R net seul", "1 − 0,05/5", "50 jours distincts", "aucun gain"):
            assert value in text, (test_id, value)
    assert "F20_BASE_RETEST" not in registry.section(doc, "Cadre")


# --- Mêmes candidats que F19 avant quota --------------------------------------------------------------------------------------

def h0_store(root, pairs: int = 10, days: int = 420) -> tuple[CandleStore, list[str]]:
    store = CandleStore(root)
    symbols = [H.symbol_of(H.PAIRS)] + [H.symbol_of(i) for i in range(pairs)]
    for index in [H.PAIRS, *range(pairs)]:
        frame = H.market(index, end=H.START + pd.Timedelta(days=days))
        frame["available_at"] = frame["open_time"] + HOUR + pd.Timedelta(seconds=2)
        path = store.path(H.symbol_of(index), "1h")
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    return store, symbols


def closes_to_check(store: CandleStore, symbols: list[str], per_config: int = 2, events: int = 5) -> list[pd.Timestamp]:
    """Clôtures où quelque chose se passe : les premiers instants de candidats de chaque configuration par paire et les
    stabilisations des chutes de BTC (FORCE_RELATIVE, discipline appliquée avant le choix des 3), après 60 jours."""
    first = int(D.to_ns([H.START + pd.Timedelta(days=60)])[0])
    by_config: dict[str, set[int]] = {}
    stabilized: set[int] = set()
    for symbol in symbols:
        bars = D.Bars(pd.read_parquet(store.path(symbol, "1h")))
        for c in D.scan_pair(bars, symbol)[0]:
            if c["at_ns"] >= first:
                by_config.setdefault(c["config"], set()).add(c["at_ns"])
        if symbol == D.FR_MARKET:
            stabilized = {e["stabilized"] for e in D.force_events(bars) if e["stabilized"] >= first}
    times = set(sorted(stabilized)[:events])
    for values in by_config.values():
        times |= set(sorted(values)[:per_config])
    return [D.stamp(t) for t in sorted(times)]


def keyed(out: dict) -> tuple:
    return ([(c["config"], c["symbol"], c["at_ns"], c["entry"], c["stop"], c["objective"], json.dumps(c["detail"], default=str,
                                                                                                     sort_keys=True))
             for c in out["candidates"]],
            [(r["config"], r["symbol"], r["reason"], r["detail"]) for r in out["refusals"]], out["events"])


def test_same_candidates_as_f19_before_quota(settings, tmp_path):
    store, symbols = h0_store(tmp_path / "h0")
    closes = closes_to_check(store, symbols)
    blocked_pairs = set(symbols[1::2])

    def blocked(symbol, config):                                       # discipline : une paire sur deux bloquée
        return ev.ACTIVE if symbol in blocked_pairs else None

    configs, with_force, force_refused = set(), 0, 0
    for at in closes:
        now = at + pd.Timedelta(minutes=3)
        loaded = ev.inputs_for(settings, at=at, now=now, store=store, symbols=symbols)
        scan = pa_single.scan_at(settings, at=at, now=now, store=store, symbols=symbols)
        assert scan["pairs_with_close"] == loaded["pairs_with_close"] and scan["pairs"] == len(loaded["frames"])
        for rule in (None, blocked):
            reference = ev.detect_at(loaded["frames"], at, btc_frame=loaded["btc_frame"], blocked=rule)
            mine = pa_single.candidates_for(scan, rule)
            assert keyed(mine) == keyed(reference), at
            configs |= {c["config"] for c in mine["candidates"]}
            with_force += int(mine["events"] > 0)
            force_refused += sum(1 for r in mine["refusals"] if r.get("discipline_checked"))
        # Même compte par configuration que F19 (evaluate) avant quota, à discipline égale.
        discipline = {"calls_today": 0, "active": {}}
        f19_out = ev.evaluate(at=at, now=now, discipline=discipline, **loaded)
        for test_id, config in pa_single.CONFIG_OF.items():
            single = pa_single.evaluate_single(test_id, scan, now=now, discipline=discipline)
            assert single["candidates"] == {config: f19_out["candidates"][config]}
            assert [r for r in single["refusals"] if r["reason"] != ev.QUOTA] == \
                [r for r in f19_out["refusals"] if r["config"] == config and r["reason"] != ev.QUOTA]
    assert configs == set(D.CONFIGS) and with_force > 0 and force_refused > 0   # cinq configurations, chutes de BTC, discipline


# --- Quota propre ---------------------------------------------------------------------------------------------------------------

def fake(config: str, symbol: str, at: pd.Timestamp) -> dict:
    detail = {D.INSIDE_DAY: {"inside_day": "2026-10-09T00:00:00+00:00", "mother_low": 9.0, "mother_high": 10.0},
              D.SQUEEZE: {"squeeze_bars": 7, "volume_multiple": 2.0}}[config]
    return {"config": config, "symbol": symbol, "at": at, "at_ns": int(D.to_ns([at])[0]), "unit": D.UNIT[config],
            "entry": 10.0, "stop": 9.0, "objective": 12.0, "detail": detail}


def fake_scan(at: pd.Timestamp, cands: list[dict]) -> dict:
    return {"at": at, "at_ns": int(D.to_ns([at])[0]), "pairs": 20, "pairs_with_close": 20, "events": [],
            "pair_candidates": cands, "pair_refusals": [], "readings": [], "read_at": at.isoformat()}


def test_own_quota_does_not_depend_on_other_configurations(monkeypatch):
    at = pd.Timestamp("2026-10-10 08:00", tz="UTC")
    inside = [fake(D.INSIDE_DAY, f"I{k}USDT", at) for k in range(7)]
    squeeze = [fake(D.SQUEEZE, f"S{k}USDT", at) for k in range(4)]
    scan = fake_scan(at, inside + squeeze)
    now = at + timedelta(minutes=3)
    f23_out = pa_single.evaluate_single("F23_INSIDE_DAY", scan, now=now, discipline={"calls_today": 0})
    order = [c["symbol"] for c in sorted(inside, key=ev.priority)]
    assert [c["symbol"] for c in f23_out["calls"]] == order[:5] and f23_out["refusals_by_reason"] == {ev.QUOTA: 2}
    assert all(c["config"] == D.INSIDE_DAY and c["test_id"] == "F23_INSIDE_DAY" for c in f23_out["calls"])
    # Les 4 SQUEEZE ne prennent rien au quota de F23, et F21 a les siens.
    assert len(pa_single.evaluate_single("F21_SQUEEZE", scan, now=now, discipline={"calls_today": 0})["calls"]) == 4
    assert pa_single.evaluate_single("F20_BASE_RETEST", scan, now=now, discipline={"calls_today": 0})["calls"] == []
    # F19 n'en garde que 5 toutes configurations confondues.
    monkeypatch.setattr(ev, "detect_at", lambda frames, at, btc_frame=None, blocked=None:
                        {"candidates": sorted(inside + squeeze, key=ev.priority), "refusals": [], "events": 0})
    f19_out = ev.evaluate(at=at, now=now, frames={}, discipline={"calls_today": 0})
    assert len(f19_out["calls"]) == 5 and f19_out["refusals_by_reason"] == {ev.QUOTA: 6}
    f19_inside = [c for c in f19_out["calls"] if c["config"] == D.INSIDE_DAY]
    assert len(f19_inside) < 5 and {c["call_id"] for c in f19_inside} <= {c["call_id"] for c in f23_out["calls"]}
    # Le quota de F23 ne compte que ses propres appels du jour ; la discipline est la sienne.
    later = pa_single.evaluate_single("F23_INSIDE_DAY", scan, now=now, discipline={"calls_today": 3})
    assert len(later["calls"]) == 2 and later["refusals_by_reason"] == {ev.QUOTA: 5}
    blocked = pa_single.evaluate_single("F23_INSIDE_DAY", scan, now=now,
                                        discipline={"calls_today": 0, "active": {f"{order[0]}:{D.INSIDE_DAY}": "x"},
                                                    "rest_until": {f"{order[1]}:{D.INSIDE_DAY}": (at + HOUR).isoformat()}})
    assert [c["symbol"] for c in blocked["calls"]] == order[2:7]
    assert blocked["refusals_by_reason"] == {ev.ACTIVE: 1, ev.REST: 1}


@pytest.mark.parametrize("test_id", sorted(MODULES))
def test_each_configuration_keeps_five_calls_whatever_the_others(test_id):
    """Pour chaque configuration : 7 candidats d'elle et 7 de chacune des autres le même jour → 5 appels pour son test."""
    at = pd.Timestamp("2026-10-10 00:00", tz="UTC")
    cands = []
    for config in D.CONFIGS:
        for k in range(7):
            c = fake(D.SQUEEZE, f"{config[:2]}{k}USDT", at)
            cands.append(c | {"config": config, "unit": D.UNIT[config], "detail": {**c["detail"], **fake(D.INSIDE_DAY, "X", at)["detail"],
                                                                                   "base_bars": 10, "base_low": 9.0, "base_high": 10.0,
                                                                                   "breakout_at": at.isoformat(), "retest_at": at.isoformat(),
                                                                                   "btc_drop_24h": -0.06, "pre_low": 9.0, "rank": 1,
                                                                                   "held_pairs": 3, "pair_drop": -0.01, "base_days": 30}})
    out = pa_single.evaluate_single(test_id, fake_scan(at, cands), now=at + timedelta(minutes=2), discipline={"calls_today": 0})
    config = pa_single.CONFIG_OF[test_id]
    assert len(out["calls"]) == 5 and {c["config"] for c in out["calls"]} == {config}
    assert out["candidates"] == {config: 7} and out["refusals_by_reason"] == {ev.QUOTA: 2}


# --- Décisions en service, doublons avec F19, Telegram -------------------------------------------------------------------------

def test_a_call_also_made_by_f19_is_not_sent_again_but_journaled(settings, monkeypatch, no_tick):
    f19_start = start(settings, monkeypatch, f19.TEST)
    mine = start(settings, monkeypatch, f20.TEST)
    store = write_store(settings, frames())
    f19_journal = registry.journal_for(settings, f19.TEST_ID)
    journal = registry.journal_for(settings, f20.TEST_ID)
    assert f19.record_decisions(settings, f19_journal, f19_start, now=NOW, store=store, clock=at_clock(NOW))["calls"] == 1
    out = f20.record_decisions(settings, journal, mine, now=NOW, store=store, clock=at_clock(NOW))
    assert out == {"evaluations": 1, "calls": 1, "repaired": 0}
    call = journal.first(pa_single.CALL)["data"]
    assert call["call_id"] == f19_journal.first(f19.CALL)["data"]["call_id"] and call["aussi_dans_F19"] is True
    assert pa_single.pending(settings, now=NOW) == []                             # rien dans la boîte séparée
    assert [m["id"] for m in outbox.pending(settings, now=NOW)] == [f"pa:APPEL:{call['call_id']}"]   # F19 seul l'annonce
    # Les autres tests séparés évaluent la même clôture sans appel (pas de candidat de leur configuration).
    for module in (f21, f22, f23, f24):
        s = start(settings, monkeypatch, module.TEST)
        assert module.record_decisions(settings, registry.journal_for(settings, module.TEST_ID), s, now=NOW, store=store,
                                       clock=at_clock(NOW))["calls"] == 0
    # Résolution : pas de message non plus.
    store = write_store(settings, frames(extra=rising()))
    end = pd.Timestamp(call["resolution_end"]) + timedelta(hours=1)
    assert f20.resolve(settings, journal, now=end, store=store) == {pa_single.RESOLVED: 1}
    assert journal.first(pa_single.RESOLUTION)["data"]["aussi_dans_F19"] is True and pa_single.pending(settings, now=end) == []
    assert f20.stats(journal, mine, now=end)["also_in_f19"] == 1
    assert not settings.signals_db.exists() and not (settings.root / "signals").exists()
    assert list((settings.root / "state").glob("assistant*")) == []


def test_own_call_is_sent_with_its_header_and_resolved_with_its_placebos(settings, monkeypatch, no_tick):
    mine = start(settings, monkeypatch, f20.TEST)                    # F19 n'a rien fait ici
    journal = registry.journal_for(settings, f20.TEST_ID)
    store = write_store(settings, frames())
    early = f20.record_decisions(settings, journal, mine, now=T + timedelta(seconds=1), store=store,
                                 clock=at_clock(T + timedelta(seconds=1)))
    assert early["evaluations"] == 0 and "waiting" in early
    assert f20.record_decisions(settings, journal, mine, now=NOW, store=store, clock=at_clock(NOW))["calls"] == 1
    evaluation = journal.first(pa_single.EVALUATION)["data"]
    assert evaluation["config"] == D.BASE_RETEST and evaluation["candidates"] == {D.BASE_RETEST: 1}
    assert evaluation["delay_min"] == 5 and evaluation["late"] is False and evaluation["pairs_with_close"] == 2
    call = journal.first(pa_single.CALL)["data"]
    assert call["call_id"] == M.signal_id(D.BASE_RETEST, "RETUSDT", T) and call["aussi_dans_F19"] is False
    own = pa_single.placebo_offsets("F20_BASE_RETEST", call["call_id"], "4h")
    assert call["placebo_offsets_h"] == own and own != M.placebo_offsets(call["call_id"], "4h")
    assert own == pa_single.placebo_offsets("F20_BASE_RETEST", call["call_id"], "4h")       # reproductible
    assert own != pa_single.placebo_offsets("F23_INSIDE_DAY", call["call_id"], "4h")        # graine propre au test
    waiting = pa_single.pending(settings, now=NOW)
    assert [m["id"] for m in waiting] == [f"ps:F20:APPEL:{call['call_id']}"]
    text = waiting[0]["text"]
    assert text.splitlines()[0].startswith("Price action CSI (F20, test séparé) — BASE_RETEST") and "RETUSDT" in text
    assert text.endswith("Shadow : aucun ordre. Test en direct F20 (séparé, quota propre), aucun gain démontré.")
    assert outbox.pending(settings, now=NOW) == []                    # la boîte de F19 n'est pas touchée
    # Une clôture n'est évaluée qu'une fois.
    assert f20.record_decisions(settings, journal, mine, now=NOW + timedelta(hours=1), store=store,
                                clock=at_clock(NOW + timedelta(hours=1)))["evaluations"] == 0
    store = write_store(settings, frames(extra=rising()))
    end = pd.Timestamp(call["resolution_end"])
    assert f20.resolve(settings, journal, now=NOW + timedelta(days=3), store=store) == {}
    assert f20.resolve(settings, journal, now=end + timedelta(hours=1), store=store) == {pa_single.RESOLVED: 1}
    central = journal.first(pa_single.RESOLUTION)["data"]["results"]["central"]
    expected = pa_single.measure_with(M.Hourly.of(f19._bars(store, "RETUSDT", since=T - pd.Timedelta(hours=85),
                                                            until=end + timedelta(hours=1), now=end + timedelta(hours=1))),
                                      offsets=own, at=T, entry=call["entry"], stop=call["stop"], objective=call["objective"],
                                      symbol="RETUSDT", scenario="central", unit="4h", complete=False, tp1=call["tp1"],
                                      hard_stop=call["hard_stop"])
    assert central["placebos"] == expected["placebos"] and central["outcome"] == M.TARGET and len(central["placebos"]) == 20
    assert pa_single.pending(settings, now=end + timedelta(hours=1))[-1]["id"] == f"ps:F20:RESOLUTION:{call['call_id']}"
    line = pa_single.read_state(settings)["tests"]["F20_BASE_RETEST"]
    assert line["resolved"] == 1 and line["verdict"] == "EN_COURS" and line["config"] == D.BASE_RETEST
    final = pd.Timestamp(mine["final_at"]) + timedelta(days=1)
    assert f20.stats(journal, mine, now=final)["verdict"] == "INSUFFISANT"
    assert f20.finalize(journal, mine, now=final) == "VERDICT" and f20.finalize(journal, mine, now=final) == "CLOTURE"
    assert registry.status(settings, f20.TEST, now=final)["state"] == registry.CLOSED and journal.verify()["ok"]
    assert list((settings.root / "state").glob("assistant*")) == [] and not (settings.root / "signals").exists()


def test_placebo_measure_is_manage_measure_with_another_seed():
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.01, 24 * 60))
    times = pd.date_range("2026-01-01", periods=len(closes), freq="h", tz="UTC")
    frame = pd.DataFrame({"open_time": times, "open": np.r_[100, closes[:-1]], "high": closes * 1.004, "low": closes * 0.996,
                          "close": closes, "quote_volume": 1.0})
    at = times[24 * 20] + HOUR
    entry = float(frame["close"].iloc[24 * 20])
    for unit in ("4h", "1d"):
        ident = M.signal_id(D.SQUEEZE, "XUSDT", at)
        assert pa_single.offsets_from_seed(f"PRICE_ACTION:{ident}", unit) == M.placebo_offsets(ident, unit)
        for scenario in ("central", "defavorable"):
            kwargs = dict(at=at, entry=entry, stop=entry * 0.97, objective=entry * 1.06, symbol="XUSDT", scenario=scenario,
                          unit=unit, complete=False)
            assert pa_single.measure_with(frame, offsets=M.placebo_offsets(ident, unit), **kwargs) == M.measure(frame, ident=ident, **kwargs)


def test_late_evaluation_on_the_real_clock_makes_no_call(settings, monkeypatch, no_tick):
    mine = start(settings, monkeypatch, f20.TEST)
    journal = registry.journal_for(settings, f20.TEST_ID)
    store = write_store(settings, frames())
    out = f20.record_decisions(settings, journal, mine, now=NOW, store=store, clock=at_clock(T + timedelta(minutes=45)))
    evaluation = journal.first(pa_single.EVALUATION)["data"]
    assert out["calls"] == 0 and evaluation["late"] is True and evaluation["delay_min"] == 45
    assert evaluation["pass_started_at"] == NOW.isoformat() and evaluation["refusals_by_reason"] == {ev.LATE: 1}
    assert pa_single.pending(settings, now=NOW) == [] and pa_single.real_clock().tzinfo is not None


def test_repair_reissues_a_call_listed_in_the_evaluation(settings, monkeypatch, no_tick):
    mine = start(settings, monkeypatch, f20.TEST)
    journal = registry.journal_for(settings, f20.TEST_ID)
    store = write_store(settings, frames())
    f20.record_decisions(settings, journal, mine, now=NOW, store=store, clock=at_clock(NOW))
    evaluation = journal.first(pa_single.EVALUATION)["data"]
    lost = registry.journal_for(settings, "F20_COPIE")
    lost.append(pa_single.EVALUATION, evaluation, now=NOW)            # arrêt brutal : EVALUATION sans APPEL
    assert pa_single.repair_calls("F20_BASE_RETEST", settings, lost, now=NOW + HOUR) == 1
    assert lost.first(pa_single.CALL)["data"]["repaired"] is True


# --- Verdict --------------------------------------------------------------------------------------------------------------------

def fill(journal, start_at: pd.Timestamp, rs: list[float], config: str = D.SQUEEZE) -> None:
    for k, r in enumerate(rs):
        at = start_at + pd.Timedelta(days=k)
        ident = f"c{k}"
        journal.append(pa_single.CALL, {"call_id": ident, "config": config, "symbol": f"P{k % 7}USDT", "at": at.isoformat(),
                                         "unit": "4h", "aussi_dans_F19": k % 3 == 0}, now=at)
        result = {"r": r, "excess": r / 2, "excess_back": None, "excess_forward": r / 3, "hits": int(r > 0),
                  "outcome": M.TARGET if r > 0 else M.STOP_HARD}
        journal.append(pa_single.RESOLUTION, {"call_id": ident, "config": config, "symbol": f"P{k % 7}USDT", "at": at.isoformat(),
                                               "status": "RESOLU", "results": {"central": result,
                                                                                "defavorable": result | {"r": r - 0.05}}}, now=at)


@pytest.mark.parametrize(("values", "expected"), [((0.8, 0.2), "SUPERIEUR_A_ZERO"), ((-0.8, 0.2), "INFERIEUR_A_ZERO"),
                                                  ((0.02, 1.0), "NON_DEMONTRE")])
def test_verdict_on_net_r_only_with_the_five_test_correction(settings, monkeypatch, values, expected):
    mine = start(settings, monkeypatch, f21.TEST)
    journal = registry.journal_for(settings, f21.TEST_ID)
    rng = np.random.default_rng(5)
    mean, spread = values
    fill(journal, pd.Timestamp(mine["started_at"]), list(mean + spread * rng.standard_normal(70)))
    final = pd.Timestamp(mine["final_at"]) + timedelta(days=1)
    result = f21.stats(journal, mine, now=final)
    assert result["resolved"] == 70 and result["scenarios"]["central"]["days"] == 70 and result["verdict"] == expected
    assert result["also_in_f19"] == 24 and result["own_calls"] == 46
    assert f21.stats(journal, mine, now=final - timedelta(days=2))["verdict"] == "EN_COURS"
    assert pa_single.f19.LEVEL == 1 - 0.05 / 5


def test_fewer_than_fifty_distinct_days_is_insufficient(settings, monkeypatch):
    mine = start(settings, monkeypatch, f21.TEST)
    journal = registry.journal_for(settings, f21.TEST_ID)
    fill(journal, pd.Timestamp(mine["started_at"]), [0.9] * 49)       # 49 jours distincts : 7 blocs de 7 jours
    final = pd.Timestamp(mine["final_at"]) + timedelta(days=1)
    assert f21.stats(journal, mine, now=final)["verdict"] == "INSUFFISANT"


# --- Causalité par le magasin, détection partagée ------------------------------------------------------------------------------

def test_falsified_future_in_the_store_changes_nothing(settings, tmp_path):
    def evaluate(data, root):
        store = CandleStore(root)
        for symbol, frame in data.items():
            path = store.path(symbol, "1h")
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
        scan = pa_single.shared_scan(settings, at=T, now=NOW, store=store, symbols=list(data))
        out = {}
        for test_id in pa_single.TEST_IDS:
            got = pa_single.evaluate_single(test_id, scan, now=NOW, discipline={"calls_today": 0})
            out[test_id] = ([(c["symbol"], c["entry"], c["stop"], c["objective"], c["tp1"], c["hard_stop"]) for c in got["calls"]],
                            got["candidates"], got["refusals"])
        return out
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
    assert evaluate(lied, tmp_path / "faux") == reference and len(reference["F20_BASE_RETEST"][0]) == 1


def test_detection_is_shared_once_per_close_and_pass(settings, monkeypatch, no_tick):
    store = write_store(settings, frames())
    seen = []
    original = pa_single.scan_at

    def counted(*args, **kwargs):
        seen.append(kwargs["at"])
        return original(*args, **kwargs)
    monkeypatch.setattr(pa_single, "scan_at", counted)
    for module in MODULES.values():
        s = start(settings, monkeypatch, module.TEST)
        module.record_decisions(settings, registry.journal_for(settings, module.TEST_ID), s, now=NOW, store=store, clock=at_clock(NOW))
    assert len(seen) == 1 and len(pa_single._CACHE) == 1
    runner.run_tests(settings, now=NOW + timedelta(minutes=1))       # fin du passage : la détection est libérée
    assert pa_single._CACHE == {}


# --- Magasin de F15 ------------------------------------------------------------------------------------------------------------

def test_store_refresh_continues_while_a_separate_test_runs(settings, monkeypatch, no_tick):
    from crypto_signal_intelligence.forward import f15
    f15_start = T - pd.Timedelta(days=100)                              # F15 terminé 16 jours avant T
    registry.start(settings, f15.TEST, now=f15_start, allow_dirty=True, halal=HALAL)
    full = frames(extra=rising())
    calls = []

    def fake_download(settings_, symbol, timeframe, *, now, rest_client, rest_only):
        assert rest_only and timeframe == "1h" and settings_.root == settings.root / "forward_figures"
        calls.append(symbol)
        frame = full[symbol]
        write_store(settings, {symbol: frame[frame["available_at"] <= pd.Timestamp(now)]})
    monkeypatch.setattr("crypto_signal_intelligence.data.pipeline.download", fake_download)
    assert runner.refresh_figure_store(settings, now=NOW, rest=object()) is None          # aucun lecteur démarré
    mine = start(settings, monkeypatch, f22.TEST)
    assert runner.refresh_figure_store(settings, now=NOW, rest=object()) == {"pairs": 2, "errors": 0,
                                                                                "readers": ["F22_FORCE_RELATIVE"]}
    assert set(calls) == {"BTCUSDT", "RETUSDT"}
    # Après la fin du recueil de F22, tant qu'il résout encore (TERMINE), le magasin est encore tenu à jour.
    after = pd.Timestamp(mine["final_at"]) + timedelta(days=1)
    assert registry.status(settings, f22.TEST, now=after)["state"] == registry.ENDED
    assert runner.refresh_figure_store(settings, now=after, rest=object())["readers"] == ["F22_FORCE_RELATIVE"]
    # F19 garde son comportement : il est listé avant les tests séparés.
    start(settings, monkeypatch, f19.TEST)
    assert runner.refresh_figure_store(settings, now=NOW, rest=object())["readers"] == ["F19_PRICE_ACTION", "F22_FORCE_RELATIVE"]
    assert runner.refresh_figure_store(settings, now=f15_start + timedelta(days=1), rest=object()) is None   # F15 en cours


# --- Boîtes Telegram fusionnées, relais, route, carte, rapport ---------------------------------------------------------------

def test_three_boxes_are_merged_and_marked_in_the_right_box(settings, monkeypatch):
    now = pd.Timestamp("2026-10-10 08:30", tz="UTC")
    api = CsiApi(settings, now=lambda: now.to_pydatetime())
    assistant_outbox.queue(settings, message_id="APPEL:a1", text="assistant 1", now=now - timedelta(minutes=30))
    outbox.queue(settings, message_id="APPEL:p1", text="price action 1", now=now - timedelta(minutes=20))
    pa_single.queue(settings, message_id="F21:APPEL:s1", text="séparé 1", now=now - timedelta(minutes=10))
    assert pa_single.queue(settings, message_id="F21:APPEL:s1", text="séparé 1", now=now) is False   # une fois par identifiant
    assistant_outbox.queue(settings, message_id="APPEL:a2", text="assistant 2", now=now - timedelta(minutes=5))
    messages = api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]
    assert [m["id"] for m in messages] == ["APPEL:a1", "pa:APPEL:p1", "ps:F21:APPEL:s1", "APPEL:a2"]
    for k in range(25):
        pa_single.queue(settings, message_id=f"F23:APPEL:x{k:02d}", text="x", now=now - timedelta(minutes=4) + timedelta(seconds=k))
    assert len(api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]) == 20
    monkeypatch.setenv("CSI_API_TOKEN", "jeton-de-test")
    marked = api.dispatch("POST", "/assistant/sent", {}, {"ids": ["APPEL:a1", "pa:APPEL:p1", "ps:F21:APPEL:s1", "ps:inconnu"]})
    assert marked == {"marked": 3}
    left = [m["id"] for m in api.dispatch("GET", "/assistant/outbox", {}, None)["messages"]]
    assert not {"APPEL:a1", "pa:APPEL:p1", "ps:F21:APPEL:s1"} & set(left) and "APPEL:a2" in left
    assert assistant_outbox.counts(settings)["ENVOYE"] == 1 and outbox.counts(settings)["ENVOYE"] == 1
    assert pa_single.counts(settings)["ENVOYE"] == 1 and pa_single.counts(settings)["EN_ATTENTE"] == 25
    # Expiration au bout de 6 h, comme les autres boîtes.
    assert pa_single.pending(settings, now=now + timedelta(hours=7)) == [] and pa_single.counts(settings)["EXPIRE"] == 25


def test_relay_sends_and_marks_messages_of_the_three_boxes(settings, tmp_path, monkeypatch):
    from crypto_signal_intelligence.relay import telegram as tg

    from .test_telegram_relay_envoi import OWNER, config
    now = pd.Timestamp("2026-10-10 08:30", tz="UTC")
    monkeypatch.setenv("CSI_API_TOKEN", "api")
    api = CsiApi(settings, now=lambda: now.to_pydatetime())
    assistant_outbox.queue(settings, message_id="APPEL:a1", text="assistant", now=now - timedelta(minutes=3))
    outbox.queue(settings, message_id="APPEL:p1", text="F19", now=now - timedelta(minutes=2))
    pa_single.queue(settings, message_id="F24:APPEL:s1", text="F24 séparé", now=now - timedelta(minutes=1))
    sent = []

    def http(method, url, body, headers, timeout):
        if url.endswith("/getUpdates"):
            return {"ok": True, "result": []}
        if url.endswith("/sendMessage"):
            sent.append(body["text"])
            return {"ok": True, "result": {"message_id": len(sent)}}
        assert headers["Authorization"] == "Bearer api"
        path = url.split("8503", 1)[1]
        return api.dispatch(method, path, {}, body)
    relay = tg.Relay(config(tmp_path, owner_chat_id=str(OWNER)), http=http)
    out = relay.cycle()
    assert out["assistant_sent"] == 3 and out["assistant_failed"] == 0 and sent == ["assistant", "F19", "F24 séparé"]
    assert api.dispatch("GET", "/assistant/outbox", {}, None)["messages"] == []
    assert pa_single.counts(settings)["ENVOYE"] == 1 and relay.cycle()["assistant_sent"] == 0


def test_price_action_route_lists_the_five_separate_tests(settings, monkeypatch, no_tick):
    api = CsiApi(settings, now=lambda: NOW.to_pydatetime())
    rows = api.dispatch("GET", "/price-action", {}, None)["separate_tests"]
    assert [r["test_id"] for r in rows] == list(MODULES) and {r["state"] for r in rows} == {registry.NOT_STARTED}
    mine = start(settings, monkeypatch, f20.TEST)
    store = write_store(settings, frames())
    f20.record_decisions(settings, registry.journal_for(settings, f20.TEST_ID), mine, now=NOW, store=store, clock=at_clock(NOW))
    out = api.dispatch("GET", "/price-action", {}, None)
    row = out["separate_tests"][0]
    assert row["state"] == registry.RUNNING and row["active"] == 1 and row["resolved"] == 0 and row["verdict"] == "EN_COURS"
    assert row["config"] == D.BASE_RETEST and row["short"] == "F20" and out["available"] is False   # F19 n'a rien écrit


def test_card_shows_one_line_per_separate_test():
    from crypto_signal_intelligence.api.server import STATIC_DIR
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "Tests séparés (F20 à F24, quota propre)" in script and script.count("...priceActionSingle(d)") == 2
    assert "d.separate_tests" in script and "Verdict courant" in script and "aucun gain démontré" in script


def test_daily_report_has_one_section_for_the_five(settings, monkeypatch):
    from crypto_signal_intelligence.forward import report
    start(settings, monkeypatch, f23.TEST)
    text = report.markdown(report.build(settings, now=NOW))
    assert text.count("## F20 à F24 : tests séparés") == 1 and "## F20_BASE_RETEST" not in text
    assert "| F23_INSIDE_DAY | INSIDE_DAY | EN_COURS |" in text and "| F20_BASE_RETEST | — | NON_DEMARRE |" in text
    assert "## F19_PRICE_ACTION" in text and "aucun gain" in text
