"""Évaluation « price action » à une clôture 4 h UTC pour le test en direct F19_PRICE_ACTION (docs/PRICE_ACTION.md § 7,
docs/FORWARD_TESTS.md section F19_PRICE_ACTION) : lectures seules, aucun ordre.

Entrées causales : bougies 1 h clôturées au plus tard à la clôture évaluée `at` et connues à `now` (`available_at`),
lues dans le magasin de F15 (`forward_figures/data`, lecture seule) sur 300 jours ; BTCUSDT du même magasin (sinon du
magasin de la surveillance) pour FORCE_RELATIVE ; discipline lue dans le journal de F19. Le détecteur est celui de
l'étude historique (`price_action/detect.py`), appelé sur ces 300 jours ; seuls les candidats d'instant `at` comptent.
Sortie : l'EVALUATION (candidats et refus par configuration) et les APPELS (5 par jour UTC au plus, toutes
configurations confondues) avec leurs placebos tirés d'avance.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pandas as pd

from ..config import Settings
from ..data.store import CandleStore
from ..forward.journal import utc_iso
from ..technical.analysis import round_tick
from . import detect as D
from . import manage as M

log = logging.getLogger("csi.price_action")

TEST_ID = "F19_PRICE_ACTION"
F15_ID = "F15_FIGURES"
HISTORY_DAYS = 300
MAX_DELAY = pd.Timedelta(minutes=30)
MAX_CALLS_PER_DAY = 5
H4 = pd.Timedelta(hours=4)
HOUR = pd.Timedelta(hours=1)
NOTE = "Shadow : aucun ordre. Test en direct F19, aucun gain démontré."
ACTIVE, REST, QUOTA, LATE = "APPEL_ACTIF", "REPOS_48H", "QUOTA_JOUR", "EVALUATION_TARDIVE"
UNIT_LABEL = {"4h": "4 h", "1d": "1 jour"}
TITLES = {D.BASE_RETEST: "base, cassure puis retest", D.SQUEEZE: "sortie de compression",
          D.FORCE_RELATIVE: "force relative après une chute de BTC", D.INSIDE_DAY: "cassure d'une journée intérieure",
          D.LONG_BASE: "sortie d'une base longue"}


# --- Sources (lecture seule) ----------------------------------------------------------------------------------------------

def store_for(settings: Settings) -> CandleStore:
    """Magasin de bougies de F15 (`forward_figures/data`), lu sans écriture."""
    from ..forward.f15 import figure_store
    return figure_store(settings)


def frozen_universe(settings: Settings) -> dict | None:
    """Liste halal figée au DEMARRAGE de F15 : {symbols, sha256, source} ; None si F15 n'a pas démarré ici."""
    from ..forward.registry import START, journal_for
    start = journal_for(settings, F15_ID).first(START)
    if start is None:
        return None
    halal = start["data"]["halal"]
    return {"symbols": list(halal["symbols"]), "sha256": halal.get("sha256"), "source": F15_ID}


def universe(settings: Settings) -> list[str]:
    """Paires figées de F15 ; sans journal F15 (vérification à la main), la liste halal admise du jour."""
    from ..forward.halal import HalalNotValidated, admitted
    frozen = frozen_universe(settings)
    if frozen is not None:
        return frozen["symbols"]
    try:
        return list(admitted(settings).symbols)
    except HalalNotValidated:
        return []


READ_COLUMNS = ("open_time", "open", "high", "low", "close", "quote_volume", "available_at")


def load_h1(store: CandleStore, symbol: str, *, at: pd.Timestamp, now: pd.Timestamp, days: int = HISTORY_DAYS) -> pd.DataFrame:
    """Bougies 1 h clôturées au plus tard à `at` ET connues à `now`, sur `days` jours. Seules les colonnes utiles sont
    lues (date d'ouverture, prix, volume quote, `available_at`) : 166 paires × 300 jours tiennent en mémoire."""
    import pyarrow.parquet as pq
    path = store.path(symbol, "1h")
    if not path.exists():
        return pd.DataFrame(columns=list(READ_COLUMNS))
    names = set(pq.ParquetFile(path).schema_arrow.names)
    table = pq.read_table(path, columns=[c for c in READ_COLUMNS if c in names],
                          filters=[("open_time", ">=", (at - pd.Timedelta(days=days)).to_pydatetime())])
    frame = table.to_pandas()
    if frame.empty:
        return frame
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    if "available_at" in frame.columns:
        frame = frame[pd.to_datetime(frame["available_at"], utc=True) <= now]
    frame = frame[frame["open_time"] + HOUR <= at]
    return frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def load_bars(store: CandleStore, symbol: str, *, at: pd.Timestamp, now: pd.Timestamp) -> D.Bars | None:
    """`Bars` d'une paire (causales à `at`, connues à `now`), construits tout de suite : le DataFrame lu est libéré
    avant la paire suivante (pic de mémoire d'une paire à la fois)."""
    frame = load_h1(store, symbol, at=at, now=now)
    if frame.empty:
        return None
    bars = D.Bars(frame)
    del frame
    return bars


def has_close(bars: D.Bars | None, at: pd.Timestamp) -> bool:
    """La bougie 1 h qui clôture à `at` est dans ces bougies."""
    return bool(bars is not None and len(bars.h1) and int(bars.h1.t[-1]) + D.HOUR_NS == int(D.to_ns([at])[0]))


def available(store: CandleStore, symbol: str, *, at: pd.Timestamp, now: pd.Timestamp) -> bool:
    """La bougie 1 h [at − 1 h ; at) est en magasin et connue à `now`."""
    frame = store.load_since(symbol, "1h", at - HOUR)
    if frame.empty:
        return False
    rows = frame[pd.to_datetime(frame["open_time"], utc=True) == at - HOUR]
    if rows.empty:
        return False
    if "available_at" in rows.columns:
        return bool((pd.to_datetime(rows["available_at"], utc=True) <= now).any())
    return True


def tick_for(settings: Settings, symbol: str) -> Decimal | None:
    from ..external.universe import tick_size_for
    try:
        return tick_size_for(settings, symbol)
    except ValueError:
        return None


def closing_time(now) -> pd.Timestamp:
    """Dernière clôture 4 h UTC au plus tard à `now`."""
    return pd.Timestamp(now).floor("4h")


# --- Détection à une clôture ------------------------------------------------------------------------------------------------

def key(symbol: str, config: str) -> str:
    return f"{symbol}:{config}"


def _causal_bars(item, at: pd.Timestamp, at_ns: int) -> D.Bars | None:
    """`Bars` causales à `at` : un DataFrame est coupé (bougies clôturées au plus tard à `at`, quoi qu'on reçoive) ;
    des `Bars` déjà construites (lecture `load_bars`) sont refusées si elles contiennent une bougie postérieure."""
    if item is None:
        return None
    if isinstance(item, D.Bars):
        if len(item.h1) and int(item.h1.t[-1]) + D.HOUR_NS > at_ns:
            raise ValueError("bougies postérieures à la clôture évaluée : lecture non causale")
        return item if len(item.h1) else None
    if item.empty:
        return None
    return D.Bars(item[pd.to_datetime(item["open_time"], utc=True) + HOUR <= at])


def detect_at(frames: dict, at: pd.Timestamp, *, btc_frame=None,
              blocked: Callable[[str, str], str | None] | None = None) -> dict:
    """Candidats et refus de géométrie d'instant `at` (détecteur partagé), toutes configurations. `frames` : par paire,
    un DataFrame de bougies 1 h ou des `Bars` déjà lues causalement. `blocked(paire, config)` rend une raison de
    discipline ou None : pour FORCE_RELATIVE, une paire bloquée est écartée AVANT le choix des 3 (comme dans l'étude
    historique)."""
    at = pd.Timestamp(at)
    at_ns = int(D.to_ns([at])[0])
    candidates, refusals, bars_of = [], [], {}
    for symbol in sorted(frames):
        bars = _causal_bars(frames[symbol], at, at_ns)
        if bars is None:
            continue
        bars_of[symbol] = bars
        found, refused = D.scan_pair(bars, symbol)
        candidates += [c for c in found if c["at_ns"] == at_ns]
        refusals += [r for r in refused if r["at_ns"] == at_ns]
    market = _causal_bars(btc_frame, at, at_ns) if btc_frame is not None else None
    market = market if market is not None else bars_of.get(D.FR_MARKET)
    events = [e for e in D.force_events(market) if e["stabilized"] == at_ns] if market is not None else []
    for event in events:
        readings = {}
        for symbol, bars in bars_of.items():
            if symbol == D.FR_MARKET:
                continue
            reading = D.force_pair(bars, event)
            reason = blocked(symbol, D.FORCE_RELATIVE) if (blocked and reading and reading["held"]) else None
            if reason:
                refusals.append({"config": D.FORCE_RELATIVE, "symbol": symbol, "at": at, "at_ns": at_ns, "reason": reason,
                                 "detail": "discipline (avant le choix des 3 paires)", "discipline_checked": True})
                continue
            readings[symbol] = reading
        candidates += D.force_select(event, readings)
    candidates.sort(key=priority)
    return {"candidates": candidates, "refusals": refusals, "events": len(events)}


def priority(candidate: dict) -> tuple[int, str]:
    """Ordre des candidats pour le quota : les plus récents d'abord ; à instant égal (cas courant : tous à la même
    clôture), l'ordre de leur identifiant sha256, neutre entre configurations et entre paires."""
    return -candidate["at_ns"], M.signal_id(candidate["config"], candidate["symbol"], candidate["at"])


# --- Évaluation complète --------------------------------------------------------------------------------------------------

def _levels(candidate: dict, tick: Decimal | None) -> dict | None:
    """Niveaux arrondis au pas de cotation : entrée (achat), TP1 et objectif vers le haut ; stop et stop de secours
    vers le bas ; sans pas connu, 8 chiffres significatifs."""
    entry = round_tick(float(f"{candidate['entry']:.12g}"), tick, ROUND_CEILING)
    stop = round_tick(candidate["stop"], tick, ROUND_FLOOR)
    if not 0 < stop < entry:
        return None
    raw = M.levels(entry, stop, candidate["objective"])
    objective = round_tick(float(f"{candidate['objective']:.12g}"), tick, ROUND_CEILING)
    return {"entry": entry, "stop": stop, "objective": objective, "risk": entry - stop,
            "tp1": round_tick(float(f"{raw['tp1']:.12g}"), tick, ROUND_CEILING),
            "hard_stop": round_tick(float(f"{raw['hard_stop']:.12g}"), tick, ROUND_FLOOR)}


def explanation(c: dict) -> list[str]:
    """Deux ou trois lignes en français : ce que le détecteur a vu, sans promesse."""
    d = c["detail"]
    if c["config"] == D.BASE_RETEST:
        first = (f"Base de {d['base_bars']} bougies 4 h entre {d['base_low']:.8g} et {d['base_high']:.8g} (≤ 1,5 ATR "
                 f"journalier), cassée le {d['breakout_at'][:16].replace('T', ' ')} UTC avec {d['volume_multiple']:.1f} × le "
                 f"volume moyen, retestée le {d['retest_at'][:16].replace('T', ' ')} UTC sans clôture sous le haut.")
        second = "Entrée sur la bougie 4 h qui repart ; stop sous le haut de base (0,25 ATR 4 h) ; objectif = hauteur de base reportée."
    elif c["config"] == D.SQUEEZE:
        first = (f"Tendance journalière haussière ; Bollinger dans Keltner pendant {d['squeeze_bars']} bougies 4 h, puis "
                 f"clôture au-dessus de la Bollinger haute avec {d['volume_multiple']:.1f} × le volume moyen.")
        second = "Stop sous le plus bas des 6 bougies de compression ; objectif +2 R."
    elif c["config"] == D.FORCE_RELATIVE:
        first = (f"BTC a perdu {abs(d['btc_drop_24h']) * 100:.1f} % en 24 h ; la paire a tenu au-dessus de son plus bas des 10 "
                 f"jours ({d['pre_low']:.8g}) et a le moins baissé (rang {d['rank']} sur {d['held_pairs']} qui ont tenu, "
                 f"{d['pair_drop'] * 100:+.1f} %).")
        second = "Entrée à la stabilisation de BTC ; stop sous le plus bas de la chute (0,25 ATR 4 h) ; objectif +2 R."
    elif c["config"] == D.INSIDE_DAY:
        first = (f"Tendance journalière haussière ; journée intérieure le {d['inside_day'][:10]} dans la mère "
                 f"[{d['mother_low']:.8g} ; {d['mother_high']:.8g}], cassée par une clôture 4 h.")
        second = "Stop de clôture journalière au bas de la mère ; objectif +2 R."
    else:
        first = (f"Base de {d['base_days']} jours entre {d['base_low']:.8g} et {d['base_high']:.8g} (≤ 25 %), sortie par une "
                 f"clôture journalière à un plus haut de 90 jours avec {d['volume_multiple']:.1f} × le volume moyen.")
        second = "Stop de clôture journalière sous le milieu de la base ; objectif = hauteur reportée, au moins +2 R."
    return [first, second]


def evaluate(*, at: pd.Timestamp, now: pd.Timestamp, frames: dict, discipline: dict, btc_frame=None,
             tick: Callable[[str], Decimal | None] | None = None, pairs_with_close: int | None = None) -> dict:
    """Évaluation à la clôture `at` : détection, discipline (une position par paire et par configuration, 48 h de repos),
    retard (au-delà de 30 min : aucun appel), quota (5 appels par jour UTC, les plus récents d'abord, puis l'ordre de
    l'identifiant sha256, neutre entre configurations). `discipline` : {"active": {"PAIRE:CONFIG": id}, "rest_until": {"PAIRE:CONFIG": iso},
    "calls_today": n}."""
    at, now = pd.Timestamp(at), pd.Timestamp(now)
    delay_min = round((now - at) / pd.Timedelta(minutes=1), 2)
    late = now - at > MAX_DELAY

    def blocked(symbol: str, config: str) -> str | None:
        k = key(symbol, config)
        if k in discipline.get("active", {}):
            return ACTIVE
        rest = discipline.get("rest_until", {}).get(k)
        if rest is not None and pd.Timestamp(rest) > at:
            return REST
        return None

    found = detect_at(frames, at, btc_frame=btc_frame, blocked=blocked)
    out: dict = {"at": utc_iso(at), "evaluated_at": utc_iso(now), "decided_at": utc_iso(now), "delay_min": delay_min,
                 "late": bool(late), "pairs": len(frames), "pairs_with_close": pairs_with_close, "events": found["events"],
                 "candidates": {c: 0 for c in D.CONFIGS}, "refusals": [], "refusals_by_reason": {}, "calls": [], "note": NOTE}
    for r in found["refusals"]:
        _refuse(out, r, r["reason"], r["detail"])
    day_calls = int(discipline.get("calls_today", 0))
    kept = []
    for cand in sorted(found["candidates"], key=priority):
        out["candidates"][cand["config"]] += 1
        reason = None if cand["config"] == D.FORCE_RELATIVE else blocked(cand["symbol"], cand["config"])
        if late:
            _refuse(out, cand, LATE, f"évaluation {delay_min:.0f} min après la clôture (> 30) : aucun appel")
            continue
        if reason == ACTIVE:
            _refuse(out, cand, ACTIVE, f"appel {discipline['active'][key(cand['symbol'], cand['config'])]} encore en cours")
            continue
        if reason == REST:
            _refuse(out, cand, REST, f"repos de 48 h jusqu'au {discipline['rest_until'][key(cand['symbol'], cand['config'])]}")
            continue
        lv = _levels(cand, tick(cand["symbol"]) if tick else None)
        if lv is None:
            _refuse(out, cand, D.BAD_GEOMETRY, "stop collé au prix après arrondi")
            continue
        kept.append((cand, lv))
    for rank, (cand, lv) in enumerate(kept):
        if day_calls + rank >= MAX_CALLS_PER_DAY:
            _refuse(out, cand, QUOTA, f"5 appels par jour UTC : {day_calls} déjà faits, classé {rank + 1}e")
            continue
        out["calls"].append(decision(cand, lv, at=at, now=now))
    return out


def _refuse(out: dict, item: dict, reason: str, detail: str) -> None:
    out["refusals"].append({"config": item["config"], "symbol": item["symbol"], "reason": reason, "detail": detail})
    out["refusals_by_reason"][reason] = out["refusals_by_reason"].get(reason, 0) + 1


def decision(cand: dict, lv: dict, *, at: pd.Timestamp, now: pd.Timestamp) -> dict:
    ident = M.signal_id(cand["config"], cand["symbol"], at)
    unit = cand["unit"]
    reach = pd.Timedelta(M.placebo_reach_ns(unit), unit="ns")
    hold = pd.Timedelta(M.max_hold_ns(unit), unit="ns")
    detail = {k: v for k, v in cand["detail"].items()}
    return {"call_id": ident, "config": cand["config"], "symbol": cand["symbol"], "unit": unit, "at": utc_iso(at),
            "decided_at": utc_iso(now), "delay_min": round((now - at) / pd.Timedelta(minutes=1), 2),
            "entry": lv["entry"], "stop": lv["stop"], "hard_stop": lv["hard_stop"], "tp1": lv["tp1"],
            "objective": lv["objective"], "risk": lv["risk"], "r_objective": round((lv["objective"] - lv["entry"]) / lv["risk"], 4),
            "stop_pct": round(lv["risk"] / lv["entry"] * 100, 4), "detail": detail, "explanation": explanation(cand),
            "placebo_offsets_h": M.placebo_offsets(ident, unit), "horizon_end": utc_iso(at + hold),
            "resolution_end": utc_iso(at + reach + hold), "note": NOTE}


# --- Lectures réelles -------------------------------------------------------------------------------------------------------

def inputs_for(settings: Settings, *, at: pd.Timestamp, now: pd.Timestamp, store: CandleStore | None = None,
               symbols: list[str] | None = None) -> dict:
    """`Bars` causales de chaque paire, lues une à une (colonnes utiles seulement, DataFrame libéré aussitôt), BTCUSDT
    pour FORCE_RELATIVE (magasin de F15, sinon celui de la surveillance), et le nombre de paires dont la bougie de
    clôture `at` est en magasin."""
    store = store or store_for(settings)
    symbols = universe(settings) if symbols is None else symbols
    frames: dict[str, D.Bars | None] = {}
    with_close = 0
    for symbol in symbols:
        frames[symbol] = load_bars(store, symbol, at=at, now=now)
        with_close += int(has_close(frames[symbol], at))
    btc = frames.get(D.FR_MARKET)
    if btc is None and settings is not None:
        btc = load_bars(CandleStore(settings.data_dir), D.FR_MARKET, at=at, now=now)
    return {"frames": frames, "btc_frame": btc, "pairs_with_close": with_close}


def run(settings: Settings, *, at: pd.Timestamp, now, discipline: dict, store: CandleStore | None = None,
        symbols: list[str] | None = None) -> dict:
    """Une évaluation complète à la clôture `at` (lectures réelles, aucun réseau)."""
    moment = pd.Timestamp(now)
    data = inputs_for(settings, at=at, now=moment, store=store, symbols=symbols)
    return evaluate(at=at, now=moment, discipline=discipline, tick=lambda s: tick_for(settings, s), **data)
