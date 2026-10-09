"""Évaluation de l'assistant de marché à une clôture 4 h UTC (docs/ASSISTANT.md) : lectures seules, aucun ordre.

Entrées (toutes causales : bougies clôturées à la clôture évaluée `at`, connues à `now`) :
- bougies 1 h du magasin de F15 (`forward_figures/data`, tenu à jour par F15, lu sans écriture) ;
- feu de protection du marché (`risk/market_light.current`) ; BTC contre son EMA50 journalière ;
- prévision H24 de F12 via `risk/advice.advice` (sinon volatilité réalisée 7 jours, source marquée) ;
- calendrier macro (`forward/light.macro_events`), news de risque (`news/risk.risk_items`) ;
- carnet `/api/v3/depth` (1 000 niveaux), seulement pour les candidats qui ont passé tout le reste ;
- discipline : appels actifs, repos de 48 h, 3 appels par jour UTC (lus dans le journal de F18).
Sortie : l'EVALUATION (régimes, candidats, refus par raison) et les APPELS complets avec leurs placebos.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pandas as pd

from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..data.store import CandleStore
from ..forward import liquidity_log
from ..forward.journal import utc_iso
from . import rules as R

log = logging.getLogger("csi.assistant")

MARKET = "BTCUSDT"
F15_ID = "F15_FIGURES"
DEPTH_PATH, DEPTH_LIMIT = "/api/v3/depth", 1000
SIZE_NORMAL, SIZE_REDUCED = "normale", "réduite"
NOTE = "Shadow : aucun ordre. Test en direct F18, aucun gain démontré."


# --- Sources (lecture seule) --------------------------------------------------------------------------------------

def store_for(settings: Settings) -> CandleStore:
    """Magasin de bougies de F15 (`forward_figures/data`), lu sans écriture."""
    from ..forward.f15 import figure_store
    return figure_store(settings)


def frozen_universe(settings: Settings) -> dict | None:
    """Liste halal figée au DEMARRAGE de F15 : {symbols, sha256, source} ; None si F15 n'a pas démarré sur cette racine."""
    from ..forward.registry import START, journal_for
    start = journal_for(settings, F15_ID).first(START)
    if start is None:
        return None
    halal = start["data"]["halal"]
    return {"symbols": list(halal["symbols"]), "sha256": halal.get("sha256"), "source": F15_ID}


def universe(settings: Settings) -> list[str]:
    """Paires de la liste halal figée au démarrage de F15 ; sans journal F15 (vérification à la main hors
    conteneur), la liste halal admise du jour ; sinon aucune."""
    from ..forward.halal import HalalNotValidated, admitted
    frozen = frozen_universe(settings)
    if frozen is not None:
        return frozen["symbols"]
    try:
        return list(admitted(settings).symbols)
    except HalalNotValidated:
        return []


def load_h1(store: CandleStore, symbol: str, *, at: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h clôturées au plus tard à `at` ET connues à `now` (`available_at`), sur HISTORY_DAYS."""
    frame = store.load_since(symbol, "1h", at - pd.Timedelta(days=R.HISTORY_DAYS))
    if frame.empty:
        return frame
    frame = frame.assign(open_time=pd.to_datetime(frame["open_time"], utc=True))
    if "available_at" in frame.columns:
        frame = frame[pd.to_datetime(frame["available_at"], utc=True) <= now]
    frame = frame[frame["open_time"] + R.HOUR <= at]
    return frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def light_at(settings: Settings, at: pd.Timestamp) -> dict:
    """Feu de protection du marché aux données connues à `at` (INCONNU si quelque chose manque)."""
    from ..risk import market_light
    try:
        out = market_light.current(settings, now=at)
        return {"color": out["color"], "explanation": out["explanation"]}
    except Exception as exc:  # noqa: BLE001 - un feu illisible vaut INCONNU (règle BTC seule, marquée)
        log.warning("feu de protection illisible : %s", exc)
        return {"color": market_light.UNKNOWN, "explanation": f"feu illisible : {type(exc).__name__}"}


def forecast_at(settings: Settings, at: pd.Timestamp) -> dict:
    """Prévision H24 de F12 inscrite au plus tard à `at` (lecture seule de son journal), au format de `risk/advice`."""
    from ..forward.journal import Journal
    from ..risk.advice import advice, journal_path
    last = None
    for entry in Journal(journal_path(settings)).entries({"PREVISION"}):
        if pd.Timestamp(entry["at"]) <= at:
            last = entry["data"]
    return advice(last, now=at)


def news_at(settings: Settings, at: pd.Timestamp) -> list[dict] | None:
    """News de risque des 24 h précédant `at` ; None si la base est illisible (refus NEWS_INJOIGNABLES)."""
    from ..news.risk import risk_items
    try:
        return risk_items(settings, since=(at - R.NEWS_WINDOW).to_pydatetime())
    except Exception as exc:  # noqa: BLE001 - base illisible : prudence, aucun appel
        log.warning("news de risque illisibles : %s", exc)
        return None


def fetch_book(client: PublicHttpClient, symbol: str) -> dict | None:
    try:
        return client.get_json(DEPTH_PATH, {"symbol": symbol, "limit": DEPTH_LIMIT})
    except (HttpError, ValueError, PermissionError) as exc:
        log.warning("carnet %s injoignable : %s", symbol, exc)
        return None


def tick_for(settings: Settings, symbol: str) -> Decimal | None:
    from ..external.universe import tick_size_for
    try:
        return tick_size_for(settings, symbol)
    except ValueError:
        return None


# --- Lecture d'une paire ---------------------------------------------------------------------------------------------

def btc_state(h1: pd.DataFrame, at: pd.Timestamp) -> dict:
    """BTC : clôture journalière (dernière journée complète) contre EMA50 et EMA20 journalières."""
    daily = R.closed(R.aggregate(h1, "1d"), "1d", at) if not h1.empty else h1
    if daily.empty or daily["open_time"].iloc[-1] != at.floor("D") - R.DAY:
        return {"known": False, "reason": "journée complète de la veille absente"}
    read = R.daily_regime(daily)
    if "close" not in read:
        return {"known": False, "reason": read["reason"]}
    return {"known": True, "close": read["close"], "ema20": read["ema20"], "ema50": read["ema50"],
            "above_ema50": read["close"] > read["ema50"], "above_ema20": read["close"] > read["ema20"],
            "day": f"{read['day']:%Y-%m-%d}"}


def read_pair(h1: pd.DataFrame, at: pd.Timestamp) -> dict:
    """Régime, couloir et configuration candidate d'une paire à la clôture `at` (règles pures)."""
    if h1.empty:
        return {"regime": R.UNREADABLE, "reason": "aucune bougie"}
    daily = R.closed(R.aggregate(h1, "1d"), "1d", at)
    if daily.empty or daily["open_time"].iloc[-1] != at.floor("D") - R.DAY:
        return {"regime": R.UNREADABLE, "reason": "journée complète de la veille absente"}
    h4 = R.closed(R.aggregate(h1, "4h"), "4h", at)
    if h4.empty or h4["open_time"].iloc[-1] != at - R.H4:
        return {"regime": R.UNREADABLE, "reason": "bougie 4 h qui vient de clôturer absente ou incomplète"}
    regime = R.daily_regime(daily)
    out: dict = {"regime": regime["regime"], "reason": regime["reason"], "daily": regime}
    if regime["regime"] == R.DOWN:
        return out
    if "atr_d" not in regime:
        return out
    levels = R.levels_4h(h4)
    out["levels"] = {"atr4": levels["atr4"], "supports": levels["supports"][:3], "resistances": levels["resistances"][:3]}
    if regime["regime"] == R.UP:
        setup = R.pullback_setup(h4, regime, levels)
        resistance = levels["resistances"][0]["price"] if levels["resistances"] else None
    else:
        corridor = R.corridor(levels, regime["atr_d"])
        if corridor is None:
            out["reason"] = "ni hausse ni baisse, pas de couloir clair sur 45 jours"
            return out
        out["regime"], out["reason"] = R.RANGE, (f"couloir {corridor['support']['price']:.8g} – "
                                                   f"{corridor['resistance']['price']:.8g} ({corridor['height_atr_d']:.1f} ATR journaliers)")
        out["corridor"] = corridor
        setup = R.rejection_setup(h4, corridor, levels)
        resistance = corridor["resistance"]["price"]
    out["setup"] = setup
    out["resistance"] = resistance
    out["setup_name"] = R.PULLBACK if regime["regime"] == R.UP else R.REJECTION
    return out


def call_id(symbol: str, at: pd.Timestamp) -> str:
    return hashlib.sha256(f"{R.TEST_ID}:{symbol}:{utc_iso(at)}".encode()).hexdigest()[:16]


# --- Évaluation complète --------------------------------------------------------------------------------------------

def evaluate(*, at: pd.Timestamp, now: pd.Timestamp, frames: dict[str, pd.DataFrame], light: dict, forecast: dict,
             news: list[dict] | None, macro_events, book: Callable[[str], dict | None], discipline: dict,
             tick: Callable[[str], Decimal | None] | None = None, btc_frame: pd.DataFrame | None = None) -> dict:
    """Évaluation à la clôture `at` : marché entier (feu, BTC), régime et configuration de chaque paire, filtres, score,
    discipline, puis les appels (3 par jour UTC au plus, les mieux classés d'abord).

    `frames` : bougies 1 h causales par paire ; `discipline` : {"active": {paire: id}, "rest_until": {paire: horodatage},
    "calls_today": n} ; `book(symbol)` rend le carnet brut ou None (injoignable) ; `tick(symbol)` le pas de cotation."""
    from ..forward.light import macro_events as default_macro
    from ..risk.market_light import ORANGE, RED, UNKNOWN
    at, now = pd.Timestamp(at), pd.Timestamp(now)
    events = macro_events or default_macro
    delay_min = round((now - at) / pd.Timedelta(minutes=1), 2)
    late = now - at > R.MAX_DELAY
    btc = btc_state(btc_frame if btc_frame is not None else frames.get(MARKET, pd.DataFrame()), at)
    out: dict = {"at": utc_iso(at), "evaluated_at": utc_iso(now), "light": light, "btc": btc,
                 "regimes": dict.fromkeys(R.REGIMES, 0), "pairs": {}, "candidates": 0, "refusals": [],
                 "refusals_by_reason": {}, "calls": [], "note": NOTE, "decided_at": utc_iso(now), "delay_min": delay_min,
                 "late": late}
    size = SIZE_REDUCED if light["color"] == ORANGE else SIZE_NORMAL
    silence = None
    if light["color"] == RED:
        silence = "feu ROUGE : silence total"
    elif not btc["known"]:
        silence = f"BTC inconnu ({btc['reason']}) : silence (prudence)"
    elif not btc["above_ema50"]:
        silence = "BTC clôture ≤ EMA50 journalière : silence total"
    if light["color"] == UNKNOWN and silence is None:
        out["light_note"] = "feu INCONNU : règle BTC seule"
    out["size"] = size
    out["silence"] = silence
    macro = R.macro_blocked(at, events)
    day_calls = int(discipline.get("calls_today", 0))
    candidates = []
    for symbol, h1 in frames.items():
        if not h1.empty:                                        # causalité : rien de clôturé après `at`, quoi qu'on reçoive
            h1 = h1[pd.to_datetime(h1["open_time"], utc=True) + R.HOUR <= at]
        read = read_pair(h1, at)
        out["regimes"][read["regime"]] += 1
        summary = {"regime": read["regime"], "reason": read["reason"]}
        out["pairs"][symbol] = summary
        if silence is not None or read.get("setup") is None:
            continue
        setup = read["setup"]
        if not setup["ok"]:
            summary["setup"] = {"name": read["setup_name"], "reason": setup["reason"]}
            continue
        out["candidates"] += 1
        refusal = {"symbol": symbol, "regime": read["regime"], "setup": setup["setup"]}
        levels = R.targets(setup["entry"], setup["stop"], read["resistance"], tick(symbol) if tick else None)
        if not levels["ok"]:
            _refuse(out, refusal, levels["reason"], levels["detail"])
            continue
        if macro:
            _refuse(out, refusal, R.MACRO, "événement macro : " + ", ".join(macro))
            continue
        if news is None:
            _refuse(out, refusal, R.NEWS_UNREACHABLE, "base de news illisible")
            continue
        hits = R.news_hits(symbol, news, at)
        if hits:
            _refuse(out, refusal, R.NEWS, "news de risque : " + " | ".join(hits))
            continue
        move, source = _move_24h(symbol, h1, forecast)
        vol = R.stop_versus_volatility(levels["entry"], levels["stop"], move)
        if not vol["ok"]:
            _refuse(out, refusal, vol["reason"], f"{vol['detail']} (source : {source})")
            continue
        net = R.tp2_net_r(levels["entry"], levels["stop"], levels["tp2"], symbol)
        if net < R.MIN_TP2_R_NET:
            _refuse(out, refusal, R.GAIN_RISK, f"TP2 net {net:.2f} R (< {R.MIN_TP2_R_NET} R ; TP2 brut à {levels['r_tp2']:.2f} R)")
            continue
        if late:                                            # carnet trop postérieur à la clôture : aucun appel
            _refuse(out, refusal, R.LATE, f"évaluation {delay_min:.0f} min après la clôture (> 30) : aucun appel")
            continue
        if symbol in discipline.get("active", {}):
            _refuse(out, refusal, R.ACTIVE, f"appel {discipline['active'][symbol]} encore en cours")
            continue
        rest = discipline.get("rest_until", {}).get(symbol)
        if rest is not None and pd.Timestamp(rest) > at:
            _refuse(out, refusal, R.REST, f"repos de 48 h jusqu'au {utc_iso(rest)}")
            continue
        candidates.append({"symbol": symbol, "read": read, "setup": setup, "levels": levels, "vol": vol,
                           "move": move, "move_source": source, "net": net, "refusal": refusal})
    if silence is None and candidates and day_calls >= R.MAX_CALLS_PER_DAY:
        for cand in candidates:
            _refuse(out, cand["refusal"], R.QUOTA, f"{day_calls} appels déjà faits ce jour UTC")
        candidates = []
    scored = []
    for cand in candidates:                                     # carnet : seulement pour les survivants (rare)
        check = R.book_check(book(cand["symbol"]), liquidity_log.book_metrics)
        if not check["ok"]:
            _refuse(out, cand["refusal"], check["reason"], check["detail"])
            continue
        cand["book"] = check
        cand["score"] = R.score(touches=int(cand["setup"].get("touches", 0)), imbalance=check["imbalance"],
                                volume_multiple=float(cand["setup"].get("volume_multiple", float("nan"))),
                                stop_ratio=cand["vol"]["ratio"], btc_above_ema20=bool(btc.get("above_ema20")))
        scored.append(cand)
    scored.sort(key=lambda c: (-c["score"]["total"], c["symbol"]))
    for rank, cand in enumerate(scored):
        if day_calls + rank >= R.MAX_CALLS_PER_DAY:
            _refuse(out, cand["refusal"], R.QUOTA, f"3 appels par jour UTC : classé {rank + 1}e, score {cand['score']['total']}")
            continue
        out["calls"].append(_decision(cand, at=at, now=now, size=size, light=light, btc=btc))
    return out


def _refuse(out: dict, refusal: dict, reason: str, detail: str) -> None:
    out["refusals"].append(refusal | {"reason": reason, "detail": detail})
    out["refusals_by_reason"][reason] = out["refusals_by_reason"].get(reason, 0) + 1
    out["pairs"][refusal["symbol"]]["refusal"] = reason


def _move_24h(symbol: str, h1: pd.DataFrame, forecast: dict) -> tuple[float | None, str]:
    pair = ((forecast or {}).get("pairs") or {}).get(symbol)
    if pair and pair.get("move_24h_pct"):
        return float(pair["move_24h_pct"]), "prévision H24 de F12"
    return R.realized_move_24h_pct(h1), "volatilité réalisée 7 jours"


def _decision(cand: dict, *, at: pd.Timestamp, now: pd.Timestamp, size: str, light: dict, btc: dict) -> dict:
    symbol, read, setup, lv = cand["symbol"], cand["read"], cand["setup"], cand["levels"]
    ident = call_id(symbol, at)
    regime = read["regime"]
    if setup["setup"] == R.PULLBACK:
        zone = setup["zone"]
        lines = [f"Hausse journalière ({read['reason']}). Repli jusqu'à {setup['pullback_low']:.8g} dans la zone de valeur "
                 f"[{zone[0]:.8g} ; {zone[1]:.8g}] (EMA20 journalière, support 4 h, VAL 30 jours), puis clôture 4 h au-dessus "
                 f"avec un volume de {setup['volume_multiple']:.1f} × la moyenne."]
    else:
        lines = [f"Range journalier : {read['reason']}. Support touché par une mèche à {setup['wick_low']:.8g} "
                 f"({setup['support_touches']} touches sur 45 jours), clôture 4 h au-dessus avec un volume de "
                 f"{setup['volume_multiple']:.1f} × la moyenne."]
    lines.append(f"Stop à {cand['vol']['stop_pct']:.2f} % de l'entrée, soit {cand['vol']['ratio']:.1f} × le mouvement attendu "
                 f"sur 24 h ({cand['move']:.2f} %, {cand['move_source']}) ; TP2 = {lv['tp2_source']} à {lv['r_tp2']:.2f} R ; "
                 f"TP2 net {cand['net']:.2f} R. Carnet : écart {cand['book']['spread_pct']:.3f} %, glissement de 500 USDT "
                 f"{cand['book']['slippage_pct']:.3f} %, déséquilibre à ±1 % {cand['book']['imbalance']:+.2f}.")
    lines.append(f"Feu {light['color']}" + (", taille réduite" if size == SIZE_REDUCED else "")
                 + f" ; BTC {'au-dessus' if btc.get('above_ema20') else 'sous'} son EMA20 journalière.")
    return {"call_id": ident, "symbol": symbol, "at": utc_iso(at), "decided_at": utc_iso(now), "book_read_at": utc_iso(now),
            "delay_min": round((now - at) / pd.Timedelta(minutes=1), 2), "regime": regime,
            "setup": setup["setup"], "entry": lv["entry"], "stop": lv["stop"], "hard_stop": lv["hard_stop"],
            "tp1": lv["tp1"], "tp2": lv["tp2"], "risk": lv["risk"], "r_tp2": round(lv["r_tp2"], 4),
            "tp2_net_r": round(cand["net"], 4), "size": size, "light": light["color"], "score": cand["score"]["total"],
            "score_parts": cand["score"]["parts"], "explanation": lines,
            "volatility": {"move_24h_pct": cand["move"], "source": cand["move_source"], "stop_pct": round(cand["vol"]["stop_pct"], 4),
                           "ratio": round(cand["vol"]["ratio"], 4)},
            "book": {k: cand["book"][k] for k in ("spread_pct", "slippage_pct", "imbalance", "bid_usdt", "ask_usdt")},
            "placebo_offsets_h": R.placebo_offsets(ident), "horizon_end": utc_iso(at + R.MAX_HOLD),
            "resolution_end": utc_iso(at + R.PLACEBO_MAX_H * R.HOUR + R.MAX_HOLD), "note": NOTE}


# --- Glue : lectures réelles ---------------------------------------------------------------------------------------------

def inputs_for(settings: Settings, *, at: pd.Timestamp, now: pd.Timestamp, store: CandleStore | None = None,
               symbols: list[str] | None = None) -> dict:
    """Toutes les lectures (bougies, feu, prévision, news) pour une évaluation à `at`, connues à `now`."""
    store = store or store_for(settings)
    symbols = universe(settings) if symbols is None else symbols
    frames = {s: load_h1(store, s, at=at, now=now) for s in symbols}
    btc_frame = frames.get(MARKET)
    if btc_frame is None or btc_frame.empty:
        btc_frame = load_h1(CandleStore(settings.data_dir), MARKET, at=at, now=now)
    return {"frames": frames, "btc_frame": btc_frame, "light": light_at(settings, at), "forecast": forecast_at(settings, at),
            "news": news_at(settings, at), "macro_events": None}


def book_client(settings: Settings) -> PublicHttpClient:
    return PublicHttpClient.depth(settings.data.rest_base_url, retries=2)


def run(settings: Settings, *, at: pd.Timestamp, now: datetime, discipline: dict, store: CandleStore | None = None,
        symbols: list[str] | None = None, client: PublicHttpClient | None = None, book=None) -> dict:
    """Une évaluation complète à la clôture `at` (lectures réelles, carnet public pour les seuls survivants)."""
    moment = pd.Timestamp(now)
    data = inputs_for(settings, at=at, now=moment, store=store, symbols=symbols)
    if book is None:
        depth = client or book_client(settings)

        def book(symbol: str) -> dict | None:
            return fetch_book(depth, symbol)
    return evaluate(at=at, now=moment, discipline=discipline, book=book, tick=lambda s: tick_for(settings, s), **data)


def closing_time(now: datetime) -> pd.Timestamp:
    """Dernière clôture 4 h UTC (00/04/08/12/16/20) au plus tard à `now`."""
    return pd.Timestamp(now).floor("4h")


def available(store: CandleStore, symbol: str, *, at: pd.Timestamp, now: pd.Timestamp) -> bool:
    """La bougie 1 h [at − 1 h ; at) est en magasin et connue à `now` (latence comprise dans `available_at`)."""
    frame = store.load_since(symbol, "1h", at - R.HOUR)
    if frame.empty:
        return False
    rows = frame[pd.to_datetime(frame["open_time"], utc=True) == at - R.HOUR]
    if rows.empty:
        return False
    if "available_at" in rows.columns:
        return bool((pd.to_datetime(rows["available_at"], utc=True) <= now).any())
    return True
