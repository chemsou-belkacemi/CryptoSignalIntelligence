"""Tests en direct F20 à F24 : chacune des cinq configurations « price action » mesurée SEULE, avec son propre quota
(docs/FORWARD_TESTS.md, sections F20_BASE_RETEST … F24_SORTIE_BASE_LONGUE ; docs/PRICE_ACTION.md). Module commun ;
`forward/f20.py` … `f24.py` ne font que le nommer.

Mêmes règles exactement que F19_PRICE_ACTION (même détecteur `price_action/detect`, même enchaînement que
`price_action/evaluate.evaluate`, même gestion `price_action/manage`, mêmes frais `forward/costs`, mêmes placebos en
descriptif, même verdict au R net seul `forward/f19.verdict`, même retard maximal de 30 min sur l'horloge réelle, même
univers figé de F15, même magasin `forward_figures/data` en lecture seule, mêmes clôtures 4 h et journalières). SEULES
différences : la configuration est unique, le quota de 5 appels par jour UTC est PROPRE au test, et les placebos ont
leur graine `sha256("<TEST_ID>:" + call_id)`.

Une seule détection par clôture et par passage pour les cinq tests : la lecture du magasin et le détecteur partagé
(`scan_pair`, `force_events`, `force_pair`) sont faits une fois, paire par paire (mémoire d'une paire à la fois), et
gardés en mémoire du processus (clé : heure de clôture + empreinte des paires + magasins) jusqu'à la fin du passage
(`clear_cache`, appelé par `forward/runner.run_tests`). Ensuite chaque test applique SA discipline (pour
`FORCE_RELATIVE`, les paires bloquées sont écartées avant le choix des 3, comme dans `evaluate.detect_at`) et SON quota.

Telegram : boîte `state/price_action_single_outbox.json` (identifiants `ps:`), même format et même expiration que celle
de F19 ; un appel identique à un appel de F19 (même identifiant : même paire, même configuration, même clôture) n'y est
pas déposé (`aussi_dans_F19: true` au journal), ni sa résolution. État court par test dans
`state/price_action_single.json` (carte « Price action », `GET /price-action`). Rien n'est écrit dans
`SignalRegistry`, `signals/` ni `state/assistant*` ; aucun ordre, aucun gain démontré.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.store import CandleStore
from ..price_action import detect as D
from ..price_action import evaluate as ev
from ..price_action import manage as M
from ..price_action import outbox as pa_outbox
from . import f19
from .costs import ADVERSE, CENTRAL, SCENARIOS
from .journal import Journal, utc_iso
from .registry import ForwardTest

CONFIG_OF = {"F20_BASE_RETEST": D.BASE_RETEST, "F21_SQUEEZE": D.SQUEEZE, "F22_FORCE_RELATIVE": D.FORCE_RELATIVE,
             "F23_INSIDE_DAY": D.INSIDE_DAY, "F24_SORTIE_BASE_LONGUE": D.LONG_BASE}
TEST_IDS = tuple(CONFIG_OF)
SHORT = {test_id: test_id.split("_", 1)[0] for test_id in TEST_IDS}
F19_ID = f19.TEST_ID
EVALUATION, CALL, RESOLUTION = f19.EVALUATION, f19.CALL, f19.RESOLUTION
RESOLVED, GAP = f19.RESOLVED, f19.GAP
GAP_AFTER = f19.GAP_AFTER
MAX_CALLS_PER_DAY = 5                     # quota PROPRE à chaque test (sa seule configuration)
PREFIX = "ps:"
ALSO_IN_F19 = "aussi_dans_F19"
NOTE = ("Tests séparés F20 à F24 : chaque configuration « price action » mesurée seule, avec son propre quota de 5 appels "
        "par jour ; shadow, aucun ordre, aucun gain démontré.")


def note(test_id: str) -> str:
    return f"Shadow : aucun ordre. Test en direct {SHORT[test_id]} (séparé, quota propre), aucun gain démontré."


# --- Cache de la détection partagée (un passage) ---------------------------------------------------------------------------

_CACHE: dict[tuple, dict] = {}


def clear_cache() -> None:
    """Libère la détection gardée en mémoire (fin du passage horaire)."""
    _CACHE.clear()


def pairs_fingerprint(symbols: list[str]) -> str:
    return hashlib.sha256(",".join(symbols).encode("utf-8")).hexdigest()


def scan_at(settings: Settings | None, *, at: pd.Timestamp, now: pd.Timestamp, store: CandleStore, symbols: list[str]) -> dict:
    """Détection partagée à la clôture `at`, toutes configurations, AVANT toute discipline et tout quota : candidats et
    refus de géométrie des configurations par paire, événements BTC stabilisés à `at` et lecture `force_pair` de chaque
    paire pour chacun. Lectures identiques à `evaluate.inputs_for` (magasin de F15 ; BTCUSDT de ce magasin, sinon de
    celui de la surveillance), mais une paire à la fois : ses `Bars` sont libérées avant la suivante."""
    at, now = pd.Timestamp(at), pd.Timestamp(now)
    at_ns = int(D.to_ns([at])[0])
    names = sorted(dict.fromkeys(symbols))
    store_btc = ev.load_bars(store, D.FR_MARKET, at=at, now=now) if D.FR_MARKET in names else None
    market_raw = store_btc
    if market_raw is None and settings is not None:
        market_raw = ev.load_bars(CandleStore(settings.data_dir), D.FR_MARKET, at=at, now=now)
    market = ev._causal_bars(market_raw, at, at_ns) if market_raw is not None else None
    events = [e for e in D.force_events(market) if e["stabilized"] == at_ns] if market is not None else []
    candidates: list[dict] = []
    refusals: list[dict] = []
    readings: list[dict[str, dict | None]] = [{} for _ in events]
    with_close = 0
    for symbol in names:
        raw = store_btc if symbol == D.FR_MARKET else ev.load_bars(store, symbol, at=at, now=now)
        with_close += int(ev.has_close(raw, at))
        bars = ev._causal_bars(raw, at, at_ns)
        del raw
        if bars is None:
            continue
        found, refused = D.scan_pair(bars, symbol)
        candidates += [c for c in found if c["at_ns"] == at_ns]
        refusals += [r for r in refused if r["at_ns"] == at_ns]
        if symbol != D.FR_MARKET:
            for k, event in enumerate(events):
                readings[k][symbol] = D.force_pair(bars, event)
        del bars
    return {"at": at, "at_ns": at_ns, "pairs": len(names), "pairs_with_close": with_close, "events": events,
            "pair_candidates": candidates, "pair_refusals": refusals, "readings": readings, "read_at": utc_iso(now)}


def _store_key(store: CandleStore, settings: Settings | None) -> tuple[str, str]:
    return str(getattr(store, "data_dir", "")), str(getattr(settings, "data_dir", "")) if settings is not None else ""


def shared_scan(settings: Settings | None, *, at: pd.Timestamp, now: pd.Timestamp, store: CandleStore,
                symbols: list[str]) -> dict:
    """`scan_at` une seule fois par clôture et par passage pour les cinq tests (clé : clôture + empreinte des paires +
    magasins). Une seule entrée gardée : la précédente est libérée. La lecture est faite à l'heure du PREMIER test qui
    la demande ; les suivants évaluent plus tard (horloge réelle), donc tout ce qui était connu l'est encore pour eux :
    la lecture reste causale."""
    key = (utc_iso(pd.Timestamp(at)), pairs_fingerprint(list(symbols)), *_store_key(store, settings))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    _CACHE.clear()
    scan = scan_at(settings, at=at, now=now, store=store, symbols=list(symbols))
    _CACHE[key] = scan
    return scan


def candidates_for(scan: dict, blocked: Callable[[str, str], str | None] | None = None) -> dict:
    """Candidats et refus d'instant `at`, toutes configurations, EXACTEMENT comme `evaluate.detect_at` avec la même
    fonction `blocked` (discipline d'un test) : pour FORCE_RELATIVE, une paire bloquée est écartée avant le choix des 3."""
    at, at_ns = scan["at"], scan["at_ns"]
    candidates = list(scan["pair_candidates"])
    refusals = list(scan["pair_refusals"])
    for event, all_readings in zip(scan["events"], scan["readings"], strict=True):
        readings = {}
        for symbol, reading in all_readings.items():
            reason = blocked(symbol, D.FORCE_RELATIVE) if (blocked and reading and reading["held"]) else None
            if reason:
                refusals.append({"config": D.FORCE_RELATIVE, "symbol": symbol, "at": at, "at_ns": at_ns, "reason": reason,
                                 "detail": "discipline (avant le choix des 3 paires)", "discipline_checked": True})
                continue
            readings[symbol] = reading
        candidates += D.force_select(event, readings)
    candidates.sort(key=ev.priority)
    return {"candidates": candidates, "refusals": refusals, "events": len(scan["events"])}


# --- Évaluation d'UNE configuration --------------------------------------------------------------------------------------------

def placebo_offsets(test_id: str, call_id: str, unit: str) -> list[int]:
    """20 décalages en heures, tirage de `manage.placebo_offsets` à l'identique, graine sha256("<TEST_ID>:" + call_id)."""
    return offsets_from_seed(f"{test_id}:{call_id}", unit)


def offsets_from_seed(text: str, unit: str) -> list[int]:
    rng = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:16], 16))
    if unit == "4h":
        low, high, scale = *M.PLACEBO_HOURS, 1
    else:
        low, high, scale = *M.PLACEBO_DAYS, 24
    candidates = [*range(-high, -low + 1), *range(low, high + 1)]
    return sorted(scale * x for x in rng.sample(candidates, M.PLACEBOS))


def evaluate_single(test_id: str, scan: dict, *, now, discipline: dict, tick: Callable[[str], Decimal | None] | None = None,
                    also_in_f19: set[str] | frozenset[str] = frozenset()) -> dict:
    """`evaluate.evaluate` restreinte à la configuration du test : même discipline (une position par paire, 48 h de
    repos), même retard (au-delà de 30 min : aucun appel), même arrondi, même ordre (plus récents d'abord, puis
    l'identifiant sha256) ; quota de 5 appels par jour UTC PROPRE au test (`discipline["calls_today"]` ne compte que ses
    appels)."""
    config = CONFIG_OF[test_id]
    at, moment = pd.Timestamp(scan["at"]), pd.Timestamp(now)
    delay_min = round((moment - at) / pd.Timedelta(minutes=1), 2)
    late = moment - at > ev.MAX_DELAY

    def blocked(symbol: str, cfg: str) -> str | None:
        k = ev.key(symbol, cfg)
        if k in discipline.get("active", {}):
            return ev.ACTIVE
        rest = discipline.get("rest_until", {}).get(k)
        if rest is not None and pd.Timestamp(rest) > at:
            return ev.REST
        return None

    found = candidates_for(scan, blocked)
    out: dict = {"at": utc_iso(at), "evaluated_at": utc_iso(moment), "decided_at": utc_iso(moment), "delay_min": delay_min,
                 "late": bool(late), "config": config, "pairs": scan["pairs"], "pairs_with_close": scan["pairs_with_close"],
                 "events": found["events"], "candidates": {config: 0}, "refusals": [], "refusals_by_reason": {}, "calls": [],
                 "read_at": scan["read_at"], "note": note(test_id)}
    for r in found["refusals"]:
        if r["config"] == config:
            ev._refuse(out, r, r["reason"], r["detail"])
    day_calls = int(discipline.get("calls_today", 0))
    kept = []
    for cand in sorted(found["candidates"], key=ev.priority):
        if cand["config"] != config:
            continue
        out["candidates"][config] += 1
        reason = None if cand["config"] == D.FORCE_RELATIVE else blocked(cand["symbol"], cand["config"])
        if late:
            ev._refuse(out, cand, ev.LATE, f"évaluation {delay_min:.0f} min après la clôture (> 30) : aucun appel")
            continue
        if reason == ev.ACTIVE:
            ev._refuse(out, cand, ev.ACTIVE, f"appel {discipline['active'][ev.key(cand['symbol'], cand['config'])]} encore en cours")
            continue
        if reason == ev.REST:
            ev._refuse(out, cand, ev.REST, f"repos de 48 h jusqu'au {discipline['rest_until'][ev.key(cand['symbol'], cand['config'])]}")
            continue
        lv = ev._levels(cand, tick(cand["symbol"]) if tick else None)
        if lv is None:
            ev._refuse(out, cand, D.BAD_GEOMETRY, "stop collé au prix après arrondi")
            continue
        kept.append((cand, lv))
    for rank, (cand, lv) in enumerate(kept):
        if day_calls + rank >= MAX_CALLS_PER_DAY:
            ev._refuse(out, cand, ev.QUOTA, f"5 appels par jour UTC pour {config} (quota propre à {SHORT[test_id]}) : "
                                            f"{day_calls} déjà faits, classé {rank + 1}e")
            continue
        out["calls"].append(decision(test_id, cand, lv, at=at, now=moment, also_in_f19=also_in_f19))
    return out


def decision(test_id: str, cand: dict, lv: dict, *, at: pd.Timestamp, now: pd.Timestamp,
             also_in_f19: set[str] | frozenset[str] = frozenset()) -> dict:
    """Décision de F19 (`evaluate.decision`, même identifiant d'appel), avec les placebos et la mention du test."""
    base = ev.decision(cand, lv, at=at, now=now)
    return base | {"test_id": test_id, "placebo_offsets_h": placebo_offsets(test_id, base["call_id"], base["unit"]),
                   "placebo_seed": f"sha256({test_id}:call_id)", ALSO_IN_F19: base["call_id"] in also_in_f19,
                   "note": note(test_id)}


# --- Boîte Telegram séparée (même format, même expiration que celle de F19) --------------------------------------------------

def outbox_path(settings: Settings) -> Path:
    return settings.root / "state" / "price_action_single_outbox.json"


def queue(settings: Settings, *, message_id: str, text: str, now: datetime) -> bool:
    """Ajoute un message EN_ATTENTE (une fois par identifiant, préfixé `ps:`) ; False s'il existait déjà."""
    ident = message_id if message_id.startswith(PREFIX) else PREFIX + message_id
    path = outbox_path(settings)
    moment = pd.Timestamp(now)
    with pa_outbox._locked(path):
        messages = pa_outbox._read(path)
        if any(m["id"] == ident for m in messages):
            return False
        messages.append({"id": ident, "created_at": utc_iso(moment), "text": text, "status": pa_outbox.PENDING})
        pa_outbox._expire(messages, moment)
        pa_outbox.write_atomic(path, {"messages": messages[-pa_outbox.KEEP_MESSAGES:]})
    return True


def pending(settings: Settings, *, now: datetime, limit: int = pa_outbox.MAX_PENDING_RETURNED) -> list[dict]:
    """Messages EN_ATTENTE, du plus ancien au plus récent, 20 au plus ; ceux de plus de 6 h passent EXPIRE avant."""
    path = outbox_path(settings)
    moment = pd.Timestamp(now)
    with pa_outbox._locked(path):
        messages = pa_outbox._read(path)
        if pa_outbox._expire(messages, moment):
            pa_outbox.write_atomic(path, {"messages": messages[-pa_outbox.KEEP_MESSAGES:]})
    waiting = sorted((m for m in messages if m.get("status") == pa_outbox.PENDING), key=lambda m: m["created_at"])
    return [{"id": m["id"], "created_at": m["created_at"], "text": m["text"]} for m in waiting[:limit]]


def mark_sent(settings: Settings, ids: list[str]) -> int:
    """Passe ENVOYE les messages listés (préfixés `ps:`) ; rend le nombre marqués."""
    path = outbox_path(settings)
    wanted = {str(i) for i in ids if str(i).startswith(PREFIX)}
    if not wanted:
        return 0
    marked = 0
    with pa_outbox._locked(path):
        messages = pa_outbox._read(path)
        for m in messages:
            if m["id"] in wanted and m.get("status") != pa_outbox.SENT:
                m["status"], marked = pa_outbox.SENT, marked + 1
        if marked:
            pa_outbox.write_atomic(path, {"messages": messages})
    return marked


def counts(settings: Settings) -> dict[str, int]:
    out = {pa_outbox.PENDING: 0, pa_outbox.SENT: 0, pa_outbox.EXPIRED: 0}
    for m in pa_outbox._read(outbox_path(settings)):
        out[m.get("status", pa_outbox.PENDING)] = out.get(m.get("status", pa_outbox.PENDING), 0) + 1
    return out


def _retitle(text: str, test_id: str) -> str:
    """Texte de F19 avec l'en-tête « Price action CSI (F2x, test séparé) » et la mention du test séparé."""
    lines = text.split("\n")
    lines[0] = lines[0].replace("Price action CSI", f"Price action CSI ({SHORT[test_id]}, test séparé)", 1)
    if lines[-1] == pa_outbox.FOOTER:
        lines[-1] = note(test_id)
    return "\n".join(lines)


def call_message(test_id: str, d: dict) -> str:
    return _retitle(pa_outbox.call_message(d), test_id)


def resolution_message(test_id: str, res: dict, d: dict) -> str:
    return _retitle(pa_outbox.resolution_message(res, d), test_id)


def _message_id(test_id: str, kind: str, call_id: str) -> str:
    return f"{PREFIX}{SHORT[test_id]}:{kind}:{call_id}"


# --- Décisions -----------------------------------------------------------------------------------------------------------------

def real_clock() -> datetime:
    return datetime.now(UTC)


def f19_call_ids(settings: Settings) -> set[str]:
    """Identifiants des appels déjà inscrits par F19 (lecture seule de son journal)."""
    from .registry import journal_for
    return set(f19.calls(journal_for(settings, F19_ID)))


def record_decisions(test_id: str, settings: Settings, journal: Journal, start: dict, *, now: datetime,
                     store: CandleStore | None = None, clock: Callable[[], datetime] | None = None) -> dict:
    """Comme `f19.record_decisions`, pour la seule configuration du test et avec son quota propre : la dernière clôture
    4 h UTC passée depuis le démarrage est évaluée une seule fois, dès que la bougie 1 h de clôture de BTCUSDT est en
    magasin ; retard mesuré sur l'HORLOGE RÉELLE au moment où CE test évalue (`clock`, injectable)."""
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    store = store or ev.store_for(settings)
    at = ev.closing_time(moment)
    counts_ = {"evaluations": 0, "calls": 0, "repaired": repair_calls(test_id, settings, journal, now=moment)}
    if at <= started or moment >= final:
        return counts_
    if any(e["data"]["at"] == utc_iso(at) for e in journal.entries({EVALUATION})):
        return counts_
    universe = ev.frozen_universe(settings) or {"symbols": list(start["halal"]["symbols"]), "sha256": start["halal"].get("sha256"),
                                                "source": test_id}
    symbols = universe["symbols"]
    probe = D.FR_MARKET if D.FR_MARKET in symbols else (symbols[0] if symbols else D.FR_MARKET)
    if not ev.available(store, probe, at=at, now=moment):
        return counts_ | {"waiting": f"bougie 1 h de {probe} clôturée à {utc_iso(at)} pas encore en magasin"}
    discipline = f19.discipline_at(journal, store, at=at, now=moment)
    decided = pd.Timestamp((clock or real_clock)())
    scan = shared_scan(settings, at=at, now=decided, store=store, symbols=symbols)
    result = evaluate_single(test_id, scan, now=decided, discipline=discipline, tick=lambda s: ev.tick_for(settings, s),
                             also_in_f19=f19_call_ids(settings))
    result["pass_started_at"] = utc_iso(moment)
    record = result | {"call_ids": [c["call_id"] for c in result["calls"]], "discipline": discipline,
                       "universe": {"source": universe["source"], "pairs": len(symbols), "sha256": universe["sha256"]}}
    journal.append(EVALUATION, record, now=decided)
    counts_["evaluations"] = 1
    for d in result["calls"]:
        journal.append(CALL, d, now=decided)
        if not d[ALSO_IN_F19]:
            queue(settings, message_id=_message_id(test_id, "APPEL", d["call_id"]), text=call_message(test_id, d), now=decided)
        counts_["calls"] += 1
    write_state(test_id, settings, journal, store, now=decided)
    return counts_


def repair_calls(test_id: str, settings: Settings, journal: Journal, *, now: pd.Timestamp) -> int:
    """Arrêt brutal entre EVALUATION et APPEL : l'appel listé est ré-émis depuis l'EVALUATION (marqué `repaired`)."""
    known = set(f19.calls(journal))
    repaired = 0
    for entry in list(journal.entries({EVALUATION})):
        for d in entry["data"].get("calls") or []:
            if d["call_id"] in known:
                continue
            journal.append(CALL, d | {"repaired": True, "repaired_at": utc_iso(now)}, now=now)
            if not d.get(ALSO_IN_F19):
                queue(settings, message_id=_message_id(test_id, "APPEL", d["call_id"]), text=call_message(test_id, d), now=now)
            known.add(d["call_id"])
            repaired += 1
    return repaired


# --- Résolution ----------------------------------------------------------------------------------------------------------------

def measure(test_id: str, bars, *, ident: str, at, entry: float, stop: float, objective: float, symbol: str, scenario: str,
            unit: str, complete: bool, tp1: float | None = None, hard_stop: float | None = None) -> dict | None:
    """`manage.measure` à l'identique, avec les placebos du test (graine sha256("<TEST_ID>:" + call_id))."""
    return measure_with(bars, offsets=placebo_offsets(test_id, ident, unit), at=at, entry=entry, stop=stop,
                        objective=objective, symbol=symbol, scenario=scenario, unit=unit, complete=complete, tp1=tp1,
                        hard_stop=hard_stop)


def measure_with(bars, *, offsets: list[int], at, entry: float, stop: float, objective: float, symbol: str, scenario: str,
                 unit: str, complete: bool, tp1: float | None = None, hard_stop: float | None = None) -> dict | None:
    """Corps de `manage.measure` (signal et placebos, moyennes avant / arrière, excès) pour des décalages donnés."""
    hours = M.Hourly.of(bars)
    trade = M.simulate(hours, entry_at=at, entry=entry, stop=stop, objective=objective, symbol=symbol, scenario=scenario,
                       unit=unit, complete=complete, tp1=tp1, hard_stop=hard_stop)
    if trade["status"] == M.RUNNING:
        return None
    values: list[float | None] = []
    waiting = False
    for offset in offsets:
        p = M.placebo(hours, at=at, offset_h=offset, entry=entry, stop=stop, objective=objective, symbol=symbol,
                      scenario=scenario, unit=unit, complete=complete, tp1=tp1, hard_stop=hard_stop)
        waiting = waiting or p["status"] == M.RUNNING
        values.append(p["r"] if p["status"] == M.RESOLVED else None)
    usable = [v for v in values if v is not None]
    back = [v for v, o in zip(values, offsets, strict=True) if v is not None and o < 0]
    ahead = [v for v, o in zip(values, offsets, strict=True) if v is not None and o > 0]

    def mean(xs: list[float]) -> float | None:
        return round(float(np.mean(xs)), 6) if xs else None
    m, mb, mf = mean(usable), mean(back), mean(ahead)
    return {"outcome": trade["outcome"], "hits": trade["hits"], "r": trade["r"], "exit_at": trade["exit_at"],
            "exit_ns": trade["exit_ns"], "placebos": values, "placebos_resolved": len(usable), "placebo_mean": m,
            "excess": round(trade["r"] - m, 6) if m is not None else None, "placebo_mean_back": mb,
            "placebo_mean_forward": mf, "excess_back": round(trade["r"] - mb, 6) if mb is not None else None,
            "excess_forward": round(trade["r"] - mf, 6) if mf is not None else None, "pending": waiting}


def resolve_one(test_id: str, call: dict, bars: pd.DataFrame, *, late: bool) -> dict | None:
    """Comme `f19.resolve_one`, avec les placebos du test."""
    hours = M.Hourly.of(bars) if not bars.empty else None
    results: dict = {}
    waiting = False
    for scenario in SCENARIOS:
        out = None if hours is None else measure(
            test_id, hours, ident=call["call_id"], at=pd.Timestamp(call["at"]), entry=call["entry"], stop=call["stop"],
            objective=call["objective"], symbol=call["symbol"], scenario=scenario, unit=call["unit"], complete=False,
            tp1=call["tp1"], hard_stop=call["hard_stop"])
        if out is None:
            waiting = True
            results[scenario] = None
            continue
        waiting = waiting or out["pending"]
        results[scenario] = f19._jsonable(out)
    if waiting and not late:
        return None
    base = {"call_id": call["call_id"], "config": call["config"], "symbol": call["symbol"], "at": call["at"], "unit": call["unit"],
            ALSO_IN_F19: bool(call.get(ALSO_IN_F19))}
    if results[CENTRAL] is None or results[ADVERSE] is None:
        return base | {"status": GAP, "reason": "bougies 1 h manquantes : appel non résolu", "results": None}
    return base | {"status": RESOLVED, "reason": None, "results": results, "late": late}


def resolve(test_id: str, settings: Settings, journal: Journal, *, now: datetime, store: CandleStore | None = None) -> dict:
    """Comme `f19.resolve` : chaque appel (et ses placebos) une fois les bougies disponibles ; trou constaté 2 jours
    après la fin de la fenêtre. Un message par RESOLUTION, sauf pour un appel aussi fait par F19 (déjà annoncé)."""
    moment = pd.Timestamp(now)
    store = store or ev.store_for(settings)
    done = f19.resolutions(journal)
    out_counts: dict[str, int] = {}
    for ident, call in f19.calls(journal).items():
        if ident in done:
            continue
        at = pd.Timestamp(call["at"])
        if moment < at + pd.Timedelta(hours=4):
            continue
        end = pd.Timestamp(call["resolution_end"])
        reach = pd.Timedelta(M.placebo_reach_ns(call["unit"]), unit="ns")
        bars = f19._bars(store, call["symbol"], since=at - reach - pd.Timedelta(hours=1),
                         until=min(moment, end + pd.Timedelta(hours=1)), now=moment)
        out = resolve_one(test_id, call, bars, late=moment >= end + GAP_AFTER)
        if out is None:
            continue
        journal.append(RESOLUTION, out, now=moment)
        if not call.get(ALSO_IN_F19):
            queue(settings, message_id=_message_id(test_id, "RESOLUTION", ident), text=resolution_message(test_id, out, call),
                  now=moment)
        out_counts[out["status"]] = out_counts.get(out["status"], 0) + 1
    if out_counts:
        write_state(test_id, settings, journal, store, now=moment)
    return out_counts


# --- Mesures et verdict ------------------------------------------------------------------------------------------------------

def stats(test_id: str, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Mesures de la configuration du test : celles de `f19.stats` pour une configuration (R net, intervalle
    1 − 0,05/5 et IC95, placebos descriptifs), verdict `f19.verdict` (correctif de 5 tests gardé : la famille reste
    celle des cinq configurations)."""
    config = CONFIG_OF[test_id]
    evaluations = [e["data"] for e in journal.entries({EVALUATION})]
    decided = f19.calls(journal)
    done = f19.resolutions(journal)
    resolved = [r for r in done.values() if r["status"] == RESOLVED and r.get("results")]
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) and all(k in done for k in decided)
    refusals: dict[str, int] = {}
    candidates = 0
    for e in evaluations:
        for reason, n in (e.get("refusals_by_reason") or {}).items():
            refusals[reason] = refusals.get(reason, 0) + n
        candidates += int((e.get("candidates") or {}).get(config, 0))
    span = max(1.0, (pd.Timestamp(now) - pd.Timestamp(start["started_at"])) / pd.Timedelta(days=1))
    scenarios = {s: f19._measure(resolved, s) for s in SCENARIOS}
    also = sum(1 for c in decided.values() if c.get(ALSO_IN_F19))
    return {"test_id": test_id, "config": config, "evaluations": len(evaluations),
            "late": sum(1 for e in evaluations if e.get("late")), "candidates": candidates, "calls": len(decided),
            "also_in_f19": also, "own_calls": len(decided) - also, "resolved": len(resolved),
            "gaps": sum(1 for r in done.values() if r["status"] == GAP), "pending": sum(1 for k in decided if k not in done),
            "calls_per_week": round(len(decided) / span * 7, 2), "refusals_by_reason": refusals, "scenarios": scenarios,
            "price_action_single": True, "ended": bool(ended), "verdict": f19.verdict(scenarios, ended=ended)}


def finalize(test_id: str, journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(test_id, journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "config": result["config"], "scenarios": result["scenarios"],
                                 "calls": result["calls"], "also_in_f19": result["also_in_f19"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


# --- État court (carte « Price action », GET /price-action) ------------------------------------------------------------------

def state_path(settings: Settings) -> Path:
    return settings.root / "state" / "price_action_single.json"


def summary(test_id: str, journal: Journal, store: CandleStore, *, now: pd.Timestamp) -> dict:
    """Ligne d'un test séparé : dernière évaluation, appels actifs, résolus, trous, verdict inscrit ou `EN_COURS`."""
    from .registry import VERDICT
    last = None
    for entry in journal.entries({EVALUATION}):
        last = entry["data"]
    decided = f19.calls(journal)
    done = f19.resolutions(journal)
    active = f19.active_calls(journal, store, now=now)
    recorded = journal.first(VERDICT)
    return {"test_id": test_id, "config": CONFIG_OF[test_id], "last_at": (last or {}).get("at"),
            "last_late": (last or {}).get("late"), "last_candidates": ((last or {}).get("candidates") or {}).get(CONFIG_OF[test_id], 0),
            "last_calls": len((last or {}).get("calls") or []), "calls": len(decided),
            "also_in_f19": sum(1 for c in decided.values() if c.get(ALSO_IN_F19)),
            "active_calls": [{k: c.get(k) for k in ("call_id", "symbol", "at", "entry", "stop", "objective", "status", "latent_r")}
                             for c in active],
            "resolved": sum(1 for r in done.values() if r["status"] == RESOLVED),
            "gaps": sum(1 for r in done.values() if r["status"] == GAP),
            "verdict": recorded["data"]["verdict"] if recorded is not None else f19.RUNNING,
            "written_at": utc_iso(now)}


def write_state(test_id: str, settings: Settings, journal: Journal, store: CandleStore, *, now: pd.Timestamp) -> dict:
    """Réécrit la ligne du test dans `state/price_action_single.json` (verrou de fichier, écriture atomique)."""
    row = summary(test_id, journal, store, now=pd.Timestamp(now))
    path = state_path(settings)
    with pa_outbox._locked(path):
        data = read_state(settings)
        known = data.get("tests")
        tests: dict = dict(known) if isinstance(known, dict) else {}
        tests[test_id] = row
        pa_outbox.write_atomic(path, {"tests": tests, "places_orders": False, "note": NOTE})
    return row


def read_state(settings: Settings) -> dict:
    path = state_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}



def make_test(test_id: str) -> ForwardTest:
    config = CONFIG_OF[test_id]
    short = SHORT[test_id]
    return ForwardTest(
        test_id=test_id,
        title=f"Price action, {config} seule ({ev.TITLES[config]}), quota propre, contre placebos",
        hypothesis=(f"Un appel {config} (docs/PRICE_ACTION.md), géré comme dans F19 (stop de clôture de son unité, stop de "
                    "secours à −1,5 R, moitié à +1 R puis l'autre à l'objectif, 10 ou 30 jours au plus), rapporte en moyenne "
                    "un R net > 0 après frais ; mêmes règles que F19_PRICE_ACTION, mais cette configuration seule avec son "
                    "propre quota de 5 appels par jour UTC (20 placebos de même géométrie en descriptif). Attendu : "
                    "NON_DEMONTRE ou INSUFFISANT."),
        params={"config": config, "rules": "celles de F19_PRICE_ACTION (docs/FORWARD_TESTS.md), cette configuration seule",
                "evaluation": "chaque clôture 4 h UTC (la clôture journalière est celle de 00:00)",
                "history_days": ev.HISTORY_DAYS, "max_calls_per_day": MAX_CALLS_PER_DAY, "quota": "propre au test",
                "max_delay_minutes": 30, "detect": D.params(),
                "manage": {"hard_stop_factor": M.HARD_STOP_FACTOR, "tp1_share": M.TP1_SHARE, "max_hold_days": M.MAX_HOLD_DAYS,
                           "rest_hours": M.REST_HOURS, "placebos": M.PLACEBOS, "placebo_hours": list(M.PLACEBO_HOURS),
                           "placebo_days": list(M.PLACEBO_DAYS), "seed_prefix": test_id},
                "min_resolved": f19.MIN_RESOLVED, "min_days_resolved": f19.MIN_DAYS, "alpha": f19.ALPHA, "tests": f19.TESTS,
                "decision_level": f19.LEVEL, "decision": "R net seul (docs/PRICE_ACTION.md § 11.5) ; placebos descriptifs",
                "samples": f19.SAMPLES, "seed": f19.SEED, "block_days": f19.BLOCK_DAYS, "gap_after_days": GAP_AFTER.days,
                "exits": "taker partout ; bougie 1 h touchant objectif et stop de secours : stop",
                "universe": "liste halal figée de F15, magasin forward_figures/data en lecture seule",
                "telegram": f"boîte state/price_action_single_outbox.json (ps:{short}:…), sans les appels aussi faits par F19"},
        rule_objects=(), config_keys=("data.assumed_availability_latency_seconds",),
        frozen_modules=(f"crypto_signal_intelligence.forward.{short.lower()}", "crypto_signal_intelligence.forward.pa_single",
                        "crypto_signal_intelligence.forward.f19", "crypto_signal_intelligence.price_action",
                        "crypto_signal_intelligence.price_action.detect", "crypto_signal_intelligence.price_action.manage",
                        "crypto_signal_intelligence.price_action.evaluate", "crypto_signal_intelligence.price_action.state",
                        "crypto_signal_intelligence.price_action.outbox", "crypto_signal_intelligence.forward.costs",
                        "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
        frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci95"),
                          ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                          ("crypto_signal_intelligence.forward.f15", "figure_store"),
                          ("crypto_signal_intelligence.patterns.indicators", "ema"),
                          ("crypto_signal_intelligence.patterns.primitives", "atr"),
                          ("crypto_signal_intelligence.patterns.primitives", "true_range"),
                          ("crypto_signal_intelligence.technical.analysis", "round_tick"),
                          ("crypto_signal_intelligence.data.store", "CandleStore")),
    )
