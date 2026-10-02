"""Relevé QUOTIDIEN du financement et de l'intérêt ouvert (journal F0_DERIVES, docs/FORWARD_TESTS.md).

Ce n'est pas un test : c'est une collecte de données publiques en lecture seule, pour les paires de la liste
halal admise qui ont un contrat perpétuel USDⓈ-M. Le cadre permet de LIRE ces données comme information ;
CSI ne négocie aucun contrat. Binance ne garde que 30 jours d'historique horaire de l'intérêt ouvert : ce relevé
est la seule façon d'en constituer un historique. Un jour manqué (machine éteinte) reste un trou : il n'est jamais
comblé après coup avec des valeurs reconstruites.

Par paire et par jour : la ligne brute de /fapi/v1/premiumIndex (prix de marque, indice, dernier financement,
prochain règlement), les règlements de financement des dernières 26 heures et les 30 dernières valeurs horaires
de l'intérêt ouvert (environ 4 jours : un jour manqué est rattrapé par de VRAIES valeurs de Binance, rien n'est
reconstruit). Le jour n'est clos (entrée JOUR) que si l'appel commun a réussi : sinon, nouvel essai au passage
suivant, sans réinscrire les paires déjà relevées ce jour-là.
"""
from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

from ..config import Settings
from ..data.http import HttpError, PublicHttpClient
from ..external.admission import base_of
from .halal import HalalList, admitted
from .journal import Journal

JOURNAL_ID = "F0_DERIVES"
PAIR, DAY, ERROR = "PAIRE", "JOUR", "ERREUR"
RECORD_AFTER = pd.Timedelta(minutes=10)
FUNDING_WINDOW = pd.Timedelta(hours=26)
OI_ROWS = 100                    # ~4 jours de valeurs horaires : un jour manqué se rattrape avec des valeurs de Binance
PAUSE_SECONDS = 0.1


def journal(settings: Settings) -> Journal:
    return Journal(settings.root / "forward" / f"{JOURNAL_ID}.jsonl")


def perpetual_for(symbol: str, available: set[str]) -> str | None:
    """Perpétuel de la paire Spot : même nom, ou préfixé « 1000 » (petits prix unitaires)."""
    base = base_of(symbol)
    for candidate in (f"{base}USDT", f"1000{base}USDT", f"1000000{base}USDT"):
        if candidate in available:
            return candidate
    return None


def day_of(now: datetime) -> str:
    return (pd.Timestamp(now) - RECORD_AFTER).strftime("%Y-%m-%d")


def recorded_days(log: Journal) -> set[str]:
    return {e["data"]["day"] for e in log.entries({DAY})}


def record_day(settings: Settings, *, now: datetime, client: PublicHttpClient | None = None,
               halal: HalalList | None = None, sleep=time.sleep) -> dict:
    """Relevé du jour UTC (une seule fois par jour, après 00:10 UTC)."""
    log = journal(settings)
    day = day_of(now)
    if day in recorded_days(log):
        return {"day": day, "already": True}
    halal = halal or admitted(settings)
    own = client is None
    client = client or PublicHttpClient.futures_rest(settings.derivatives.rest_base_url)
    now_ms = int(pd.Timestamp(now).timestamp() * 1000)
    counts: dict = {"day": day, "pairs": 0, "no_perpetual": 0, "errors": 0}
    try:
        try:
            premium = {row["symbol"]: row for row in client.get_json("/fapi/v1/premiumIndex")}
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            log.append(ERROR, {"day": day, "what": "premiumIndex", "error": f"{type(exc).__name__}: {exc}"[:300]},
                       now=now)
            return counts | {"errors": 1}
        done = {e["data"]["spot"] for e in log.entries({PAIR}) if e["data"]["day"] == day}
        missing = []
        for symbol in halal.symbols:
            if symbol in done:
                counts["pairs"] += 1
                continue
            perp = perpetual_for(symbol, set(premium))
            if perp is None:
                missing.append(symbol)
                continue
            try:
                funding = client.get_json("/fapi/v1/fundingRate", {
                    "symbol": perp, "startTime": now_ms - int(FUNDING_WINDOW.total_seconds() * 1000), "limit": 100})
                sleep(PAUSE_SECONDS)
                interest = client.get_json("/futures/data/openInterestHist",
                                           {"symbol": perp, "period": "1h", "limit": OI_ROWS})
                sleep(PAUSE_SECONDS)
            except (HttpError, ValueError, KeyError, TypeError) as exc:
                log.append(ERROR, {"day": day, "spot": symbol, "perp": perp,
                                   "error": f"{type(exc).__name__}: {exc}"[:300]}, now=now)
                counts["errors"] += 1
                continue
            log.append(PAIR, {
                "day": day, "spot": symbol, "perp": perp, "premium": premium[perp],
                "funding": [[r.get("fundingTime"), r.get("fundingRate"), r.get("markPrice")] for r in funding],
                "open_interest": [[r.get("timestamp"), r.get("sumOpenInterest"), r.get("sumOpenInterestValue")]
                                  for r in interest]}, now=now)
            counts["pairs"] += 1
        counts["no_perpetual"] = len(missing)
        log.append(DAY, counts | {"complete": True, "without_perpetual": missing, "halal_sha256": halal.sha256},
                   now=now)
        return counts
    finally:
        if own:
            client.close()


def summary(settings: Settings) -> dict:
    log = journal(settings)
    days = [e["data"] for e in log.entries({DAY})]
    return {"days": len(days), "first_day": days[0]["day"] if days else None,
            "last": days[-1] if days else None, "verified": log.verify()}
