"""Feu tricolore quotidien (phase 8 de la mission du 2026-10-02) : filtre de risque du modèle A, calculé chaque jour
à 00:00 UTC à partir d'informations connues à ce moment-là. Règles pures ici ; les lectures (annonces Binance,
prévision de volatilité) sont faites par le test F5 et journalisées avec le feu.

ROUGE global si au moins une condition :
- décision de la Fed, CPI américain ou emploi américain (NFP) le jour UTC même (calendrier figé ci-dessous) ;
- volatilité prévue à 7 jours de BTC (VOLATILITY_FORECAST) dans les 10 % les plus élevés de ses 365 derniers jours ;
- financement moyen 7 jours du perpétuel BTC au-dessus de 0,05 % par 8 h ;
- USDT ou USDC à plus de 0,5 % de sa parité avec le dollar.
ROUGE propre à un actif : annonce officielle Binance « Will Delist » citant l'actif (hors contrats à terme et hors
simple retrait de paires).
ORANGE global (si pas rouge) : samedi ou dimanche UTC ; dernier vendredi du mois ; maintenance Binance annoncée
pour le jour même ou le lendemain. VERT sinon.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

import numpy as np
import pandas as pd

RED, ORANGE, GREEN = "ROUGE", "ORANGE", "VERT"
VOL_TOP_SHARE = 0.10
FUNDING_LIMIT_8H = 0.0005
PEG_LIMIT_PCT = 0.5
HISTORY_DAYS = 365

#: Calendrier macro FIGÉ pour la période du test (jours UTC) ; sources officielles relevées le 2026-10-02 :
#: Fed = calendrier FOMC (federalreserve.gov, décision le 2e jour), CPI et NFP = calendriers de publication du BLS.
MACRO_CALENDAR: tuple[tuple[str, str], ...] = (
    ("2026-10-02", "NFP"), ("2026-10-14", "CPI"), ("2026-10-28", "FED"),
    ("2026-11-06", "NFP"), ("2026-11-10", "CPI"),
    ("2026-12-04", "NFP"), ("2026-12-09", "FED"), ("2026-12-10", "CPI"),
    ("2027-01-08", "NFP"), ("2027-01-13", "CPI"), ("2027-01-27", "FED"),
)
MACRO_SOURCE = "FOMC calendar (federalreserve.gov) ; BLS release schedules (bls.gov), relevés le 2026-10-02"

DELIST_TITLE = re.compile(r"^Binance Will Delist\b(?P<rest>.*)$", re.IGNORECASE)
MAINTENANCE_TITLE = re.compile(r"\b(maintenance|system upgrade|scheduled upgrade)\b", re.IGNORECASE)
TITLE_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
TOKEN = re.compile(r"\b([A-Z0-9]{2,10})\b")
NOT_ASSETS = frozenset({"ON", "AND", "THE", "OF", "USDT", "USDC", "BUSD", "FDUSD", "SPOT", "MARGIN", "BINANCE"})


def macro_events(day: date | str) -> list[str]:
    key = str(day)[:10]
    return [name for d, name in MACRO_CALENDAR if d == key]


def is_last_friday(day: date) -> bool:
    last_day = calendar.monthrange(day.year, day.month)[1]
    last = date(day.year, day.month, last_day)
    while last.weekday() != 4:
        last = last.replace(day=last.day - 1)
    return day == last


def volatility_rank(history: list[float], today: float | None) -> float | None:
    """Rang de la prévision du jour parmi les valeurs des 365 derniers jours (1,0 = la plus haute) ; None sans
    au moins 100 valeurs."""
    values = np.array([v for v in history if v is not None and np.isfinite(v)], float)
    if today is None or not np.isfinite(today) or len(values) < 100:
        return None
    return round(float((values <= today).mean()), 4)


def delisted_assets(titles: list[str]) -> set[str]:
    """Actifs cités dans les titres « Binance Will Delist … » (contrats à terme et retraits de paires exclus)."""
    out: set[str] = set()
    for title in titles:
        match = DELIST_TITLE.match(title.strip())
        if not match:
            continue
        for token in TOKEN.findall(match.group("rest")):
            if token not in NOT_ASSETS and not token.isdigit():
                out.add(token)
    return out


def maintenance_days(titles: list[str]) -> set[str]:
    """Jours (UTC) annoncés dans les titres de maintenance ou de mise à niveau de Binance."""
    out: set[str] = set()
    for title in titles:
        if MAINTENANCE_TITLE.search(title):
            out.update(TITLE_DATE.findall(title))
    return out


def decide(day: date, *, vol_rank: float | None, btc_funding_8h: float | None, peg_deviation_pct: dict[str, float | None],
           delist_titles: list[str], maintenance_titles: list[str]) -> dict:
    """Couleur du jour et ses raisons (toutes les entrées sont rapportées, décidantes ou non)."""
    reasons: list[str] = []
    events = macro_events(day)
    if events:
        reasons.append("macro : " + ", ".join(events))
    if vol_rank is not None and vol_rank >= 1 - VOL_TOP_SHARE:
        reasons.append(f"volatilité prévue BTC dans les 10 % les plus hauts (rang {vol_rank:.2f})")
    if btc_funding_8h is not None and btc_funding_8h > FUNDING_LIMIT_8H:
        reasons.append(f"financement BTC 7 jours {btc_funding_8h * 100:.4f} % > 0,05 % par 8 h")
    for coin, deviation in sorted(peg_deviation_pct.items()):
        if deviation is not None and abs(deviation) > PEG_LIMIT_PCT:
            reasons.append(f"{coin} à {deviation:+.2f} % de sa parité")
    assets = sorted(delisted_assets(delist_titles))
    maintenance = maintenance_days(maintenance_titles)
    orange: list[str] = []
    if day.weekday() >= 5:
        orange.append("week-end")
    if is_last_friday(day):
        orange.append("dernier vendredi du mois")
    tomorrow = str(day + pd.Timedelta(days=1))[:10]
    if str(day) in maintenance or tomorrow in maintenance:
        orange.append("maintenance Binance annoncée")
    color = RED if reasons else (ORANGE if orange else GREEN)
    return {"day": str(day), "color": color, "red_reasons": reasons, "orange_reasons": orange,
            "red_assets": assets, "inputs": {"vol_rank": vol_rank, "btc_funding_8h": btc_funding_8h,
                                             "peg_deviation_pct": peg_deviation_pct, "macro": events,
                                             "maintenance_days": sorted(maintenance)}}
