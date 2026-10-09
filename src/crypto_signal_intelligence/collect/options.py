"""Source OPTIONS : API publique de Deribit (REST, sans clé), toutes les 15 min, pour BTC et ETH.

Trois lectures par devise (`https://www.deribit.com/api/v2/public/…`, liste fermée de `collect/net.py`) :
- `get_index_price?index_name=btc_usd` → prix de l'indice ;
- `get_volatility_index_data` (résolution 60 s = bougies horaires de DVOL, 3 dernières heures) → dernière clôture ;
- `get_book_summary_by_currency?currency=BTC&kind=option` → un résumé par option : `instrument_name`
  (`BTC-27DEC24-100000-C`), `mark_iv` (volatilité implicite du prix de marque, en %), `underlying_price`,
  `open_interest` (en monnaie de base pour les options), `bid_price`, `ask_price`.

Calculs (fonctions pures, testées sur un résumé fictif) :
- volatilité implicite ATM à ~30 jours : échéance la plus proche de 30 jours, strike le plus proche du sous-jacent,
  moyenne des `mark_iv` du call et du put ;
- asymétrie 25-delta (put − call, en points de volatilité) sur la même échéance : le résumé ne donne pas les grecs,
  le delta est donc APPROCHÉ par Black-Scholes avec `mark_iv`, taux 0 ; dit dans l'entrée (`delta_method`) ;
- put/call en intérêt ouvert (toutes échéances) et intérêt ouvert total ;
- max pain de la première échéance à plus de 24 h (même formule que `context/fetch.py`).

Entrée `OPTIONS_15M` (journal `C_OPTIONS-AAAA-MM.jsonl`), une par devise ; une lecture en panne laisse `None` et
une raison dans `errors`, les autres champs restent. À VÉRIFIER AU DÉPLOIEMENT : noms des champs de Deribit lus ici
d'après sa documentation publique (2026-10), aucune réponse réelle n'a été vue par les tests.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime

import pandas as pd

from ..context.fetch import max_pain
from ..forward.journal import utc_iso
from .base import OPTIONS, Context
from .net import CollectHttp

log = logging.getLogger("csi.collect.options")

BASE = "https://www.deribit.com/api/v2/public/"
KIND = "OPTIONS_15M"
CURRENCIES = ("BTC", "ETH")
EVERY = pd.Timedelta(minutes=15)
OFFSET = pd.Timedelta(seconds=20)
TARGET_DAYS = 30.0
DELTA_TARGET = 0.25
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def parse_instrument(name: str) -> tuple[pd.Timestamp, float, str] | None:
    """`BTC-27DEC24-100000-C` → (échéance 08:00 UTC, strike, 'C' | 'P') ; None si le nom n'a pas cette forme."""
    parts = str(name).split("-")
    if len(parts) != 4 or parts[3] not in ("C", "P"):
        return None
    code = parts[1]
    try:
        day, month, year = int(code[:-5]), MONTHS[code[-5:-2]], 2000 + int(code[-2:])
        expiry = pd.Timestamp(year=year, month=month, day=day, hour=8, tz="UTC")
        strike = float(parts[2].replace("d", "."))
    except (KeyError, ValueError):
        return None
    return expiry, strike, parts[3]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_delta(spot: float, strike: float, iv_pct: float, years: float, *, call: bool) -> float | None:
    """Delta de Black-Scholes (taux 0) à partir de la volatilité implicite en % ; None si les entrées sont inutilisables."""
    if spot <= 0 or strike <= 0 or iv_pct <= 0 or years <= 0:
        return None
    sigma = iv_pct / 100
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / (sigma * math.sqrt(years))
    return _norm_cdf(d1) if call else _norm_cdf(d1) - 1


def summarize(rows: list[dict], *, now: datetime, index_price: float | None = None) -> dict:
    """Mesures d'un résumé d'options Deribit (liste de lignes `get_book_summary_by_currency`)."""
    moment = pd.Timestamp(now)
    by_expiry: dict[pd.Timestamp, dict[float, dict[str, dict]]] = {}
    oi = {"C": 0.0, "P": 0.0}
    underlying: list[float] = []
    n = 0
    for row in rows:
        parsed = parse_instrument(row.get("instrument_name", ""))
        if parsed is None:
            continue
        expiry, strike, side = parsed
        n += 1
        interest = float(row.get("open_interest") or 0.0)
        oi[side] += interest
        by_expiry.setdefault(expiry, {}).setdefault(strike, {})[side] = row
        if row.get("underlying_price"):
            underlying.append(float(row["underlying_price"]))
    out: dict = {"instruments": n, "put_call_oi": round(oi["P"] / oi["C"], 4) if oi["C"] else None,
                 "oi_calls": round(oi["C"], 2), "oi_puts": round(oi["P"], 2), "oi_unit": "monnaie de base",
                 "atm_iv_30d": None, "skew_25d": None, "max_pain": None,
                 "delta_method": "Black-Scholes approché (mark_iv, taux 0), pas les grecs de Deribit"}
    if not by_expiry:
        return out
    spot = index_price or (sorted(underlying)[len(underlying) // 2] if underlying else None)
    future = {e: s for e, s in by_expiry.items() if e > moment}
    if not future or not spot:
        return out
    target = min(future, key=lambda e: abs((e - moment) / pd.Timedelta(days=1) - TARGET_DAYS))
    days = (target - moment) / pd.Timedelta(days=1)
    strikes = future[target]
    atm_strike = min(strikes, key=lambda k: abs(k - spot))
    ivs = [float(strikes[atm_strike][s]["mark_iv"]) for s in ("C", "P")
           if s in strikes[atm_strike] and strikes[atm_strike][s].get("mark_iv")]
    if ivs:
        out["atm_iv_30d"] = {"expiry": utc_iso(target), "days": round(days, 2), "strike": atm_strike,
                             "iv_pct": round(sum(ivs) / len(ivs), 2), "legs": len(ivs)}
    best: dict[str, tuple[float, float, float]] = {}         # côté → (écart au delta cible, strike, iv)
    for strike, sides in strikes.items():
        for side, row in sides.items():
            iv = float(row.get("mark_iv") or 0.0)
            delta = bs_delta(spot, strike, iv, days / 365, call=side == "C")
            if delta is None:
                continue
            gap = abs(abs(delta) - DELTA_TARGET)
            if side not in best or gap < best[side][0]:
                best[side] = (gap, strike, iv)
    if "C" in best and "P" in best and best["C"][0] < 0.1 and best["P"][0] < 0.1:
        call, put = best["C"], best["P"]
        out["skew_25d"] = {"expiry": utc_iso(target), "put_iv_pct": round(put[2], 2), "call_iv_pct": round(call[2], 2),
                           "put_strike": put[1], "call_strike": call[1], "skew_pts": round(put[2] - call[2], 2)}
    nearest = sorted(e for e in future if e > moment + pd.Timedelta(hours=24))
    if nearest:
        expiry = nearest[0]
        calls = {k: float(s["C"].get("open_interest") or 0.0) for k, s in by_expiry[expiry].items() if "C" in s}
        puts = {k: float(s["P"].get("open_interest") or 0.0) for k, s in by_expiry[expiry].items() if "P" in s}
        all_strikes = sorted(set(calls) | set(puts))
        if all_strikes:
            out["max_pain"] = {"expiry": utc_iso(expiry), "days": round((expiry - moment) / pd.Timedelta(days=1), 2),
                               "strike": max_pain(all_strikes, calls, puts),
                               "oi": round(sum(calls.values()) + sum(puts.values()), 2)}
    return out


def _result(payload) -> dict | list:
    if not isinstance(payload, dict) or "result" not in payload:
        raise ValueError("réponse Deribit sans « result »")
    return payload["result"]


def snapshot(http: CollectHttp, currency: str, *, now: datetime) -> dict:
    """Une lecture complète pour une devise ; chaque panne est notée dans `errors`, le reste est gardé."""
    moment = pd.Timestamp(now)
    out: dict = {"currency": currency, "time": utc_iso(moment), "index_price": None, "dvol": None, "errors": {}}
    try:
        index = _result(http.get_json(f"{BASE}get_index_price", {"index_name": f"{currency.lower()}_usd"}))
        if not isinstance(index, dict):
            raise ValueError("indice : objet attendu")
        out["index_price"] = float(index["index_price"])
    except Exception as exc:  # noqa: BLE001 - une lecture en panne n'empêche pas les autres
        out["errors"]["index_price"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        end = int(moment.timestamp() * 1000)
        data = _result(http.get_json(f"{BASE}get_volatility_index_data",
                                     {"currency": currency, "start_timestamp": end - 3 * 3_600_000,
                                      "end_timestamp": end, "resolution": "60"}))
        candles = data.get("data") or [] if isinstance(data, dict) else []
        if not candles:
            raise ValueError("DVOL vide")
        last = candles[-1]
        out["dvol"] = {"value": float(last[4]), "at": utc_iso(pd.Timestamp(int(last[0]), unit="ms", tz="UTC"))}
    except Exception as exc:  # noqa: BLE001
        out["errors"]["dvol"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        rows = _result(http.get_json(f"{BASE}get_book_summary_by_currency", {"currency": currency, "kind": "option"}))
        if not isinstance(rows, list):
            raise ValueError("résumé des options : liste attendue")
        out.update(summarize(rows, now=moment, index_price=out["index_price"]))
    except Exception as exc:  # noqa: BLE001
        out["errors"]["book_summary"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def next_slot(now) -> pd.Timestamp:
    """Prochain quart d'heure + 20 s (après la clôture, hors de la rafale du scanner)."""
    moment = pd.Timestamp(now)
    slot = moment.floor(EVERY) + OFFSET
    return slot if slot > moment else slot + EVERY


async def run(ctx: Context) -> None:
    http = ctx.http
    if http is None:
        raise RuntimeError("client HTTP absent")
    ctx.state.touch(OPTIONS, detail=f"Deribit public, {', '.join(CURRENCIES)}, toutes les 15 min")
    while not ctx.stop.is_set():
        wait = (next_slot(ctx.clock()) - ctx.now()).total_seconds()
        await ctx.sleep(max(0.0, wait))
        if ctx.stop.is_set():
            break
        for currency in CURRENCIES:
            now = ctx.clock()
            data = await asyncio.to_thread(snapshot, http, currency, now=now)
            ctx.state.message(OPTIONS, now=now)
            if data["errors"]:
                ctx.state.error(OPTIONS, "; ".join(f"{k}: {v}" for k, v in data["errors"].items()), status="EN_SERVICE")
            ctx.recorder.append(OPTIONS, KIND, data, now=ctx.clock())
        ctx.state.write()
