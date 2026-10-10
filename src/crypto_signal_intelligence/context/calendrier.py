"""Calendrier des « données du zoo » (docs/CONTEXTE.md) : règles PURES, sans réseau.

- FOMC : lecture des pages publiques de la Fed (réunions, communiqués `monetaryAAAAMMJJa.htm`) ;
- CPI et emploi américain (NFP) : millésimes ALFRED des séries CPIAUCNS (CPI non corrigé, jamais révisé par les
  coefficients saisonniers) et PAYEMS (emplois non agricoles) ; un millésime suivi d'un autre à moins de 7 jours
  est une correction, pas une publication (écarté et compté) ;
- halvings de Bitcoin : horodatages des blocs 210 000, 420 000, 630 000 et 840 000 (vérifiés le 2026-10-10 sur
  l'explorateur public mempool.space) ;
- phases lunaires (nouvelle et pleine lune) : calcul astronomique local (Meeus, « Astronomical Algorithms »,
  chapitre 49), précision de l'ordre de la minute ;
- annonces de cotation Binance : classement des titres.

`forward/light.py` (calendrier macro figé du test F5) est seulement LU pour recouper les dates, jamais modifié.
"""
from __future__ import annotations

import math
import re
from datetime import date

import pandas as pd

NEW_YORK = "America/New_York"
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}
CORRECTION_WINDOW_DAYS = 7

# --- FOMC -------------------------------------------------------------------------------------------------------

_STATEMENT = re.compile(r"monetary(\d{8})a\.htm")
_YEAR_BLOCK = re.compile(r"(\d{4}) FOMC Meetings")
_ROW = re.compile(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong></div>\s*'
                  r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>')
_HEADING = re.compile(r"<h5[^>]*>\s*([^<]+?)\s*</h5>")
_HEADING_TEXT = re.compile(r"^(?P<month>[A-Za-z/]+)\s+(?P<days>\d+(?:-\d+)?)\*?\s*(?:\((?P<note>[^)]*)\))?\s*"
                           r"(?:Meeting)?\s*-\s*(?P<year>\d{4})$")
_DATE_TEXT = re.compile(r"^(?P<days>\d+(?:-\d+)?)\*?\s*(?:\((?P<note>[^)]*)\))?\s*$")


def _month(name: str) -> int:
    key = name.strip().lower()[:3]
    if key not in MONTHS:
        raise ValueError(f"mois illisible : {name!r}")
    return MONTHS[key]


def meeting_last_day(year: int, month_label: str, days: str) -> date:
    """Dernier jour d'une réunion : « Jan/Feb » + « 31-1 » → 1er février ; « March » + « 16-17 » → 17 mars."""
    months = [_month(m) for m in month_label.split("/") if m.strip()]
    numbers = [int(x) for x in days.split("-")]
    month = months[-1] if len(months) > 1 and len(numbers) > 1 and numbers[-1] < numbers[0] else months[0]
    if len(months) > 1 and len(numbers) == 1:
        month = months[-1]
    return date(year, month, numbers[-1])


def _note(text: str | None) -> str:
    return (text or "").strip().lower()


def parse_fomc_calendar(html: str) -> tuple[list[dict], set[date]]:
    """Page courante des calendriers (5 à 7 années) : réunions par année et dates des communiqués."""
    meetings: list[dict] = []
    marks = list(_YEAR_BLOCK.finditer(html))
    for i, mark in enumerate(marks):
        year = int(mark.group(1))
        block = html[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(html)]
        for month_label, date_text in _ROW.findall(block):
            parsed = _DATE_TEXT.match(date_text.strip())
            if not parsed:
                raise ValueError(f"date de réunion illisible : {date_text!r} ({year})")
            meetings.append({"day": meeting_last_day(year, month_label, parsed.group("days")),
                             "note": _note(parsed.group("note"))})
    return meetings, _statements(html)


def parse_fomc_historical(html: str, year: int) -> tuple[list[dict], set[date]]:
    """Page historique d'une année (titres « January 29-30 Meeting - 2019 », « October 4 (unscheduled) - 2019 »)."""
    meetings = []
    for heading in _HEADING.findall(html):
        parsed = _HEADING_TEXT.match(" ".join(heading.split()))
        if not parsed or int(parsed.group("year")) != year:
            continue
        meetings.append({"day": meeting_last_day(year, parsed.group("month"), parsed.group("days")),
                         "note": _note(parsed.group("note"))})
    return meetings, {d for d in _statements(html) if d.year == year}


def _statements(html: str) -> set[date]:
    return {date(int(s[:4]), int(s[4:6]), int(s[6:])) for s in _STATEMENT.findall(html)}


def fomc_events(meetings: list[dict], statements: set[date]) -> list[dict]:
    """Événements FOMC : réunions (annulées écartées) et communiqués hors réunion.

    Heure : communiqué des réunions régulières à 14:00 (New York) ; réunion non programmée, vote par notation ou
    communiqué isolé : heure inconnue, 23:59 (New York) retenu. `available_at` = heure + 1 h (la date
    d'une réunion régulière est publiée l'année précédente, mais la source ne le prouve pas : un test qui veut
    s'en servir à l'avance doit le déclarer)."""
    out: dict[date, dict] = {}
    for meeting in meetings:
        note = meeting["note"]
        if "cancel" in note:
            continue
        day = meeting["day"]
        scheduled = note == ""
        detail = "réunion programmée" if scheduled else f"réunion ({note})"
        previous = out.get(day)
        if previous is None or (scheduled and not previous["scheduled"]):
            out[day] = {"day": day, "scheduled": scheduled, "detail": detail}
    for day in statements:
        out.setdefault(day, {"day": day, "scheduled": False, "detail": "communiqué hors réunion"})
    events = []
    for day, item in sorted(out.items()):
        statement = day in statements
        local = pd.Timestamp(day).tz_localize(NEW_YORK)
        when = local + (pd.Timedelta(hours=14) if item["scheduled"] else pd.Timedelta(hours=23, minutes=59))
        detail = item["detail"] + ("" if statement else " ; aucun communiqué daté de ce jour")
        events.append({"event": "FOMC", "key": day.isoformat(), "event_time": when.tz_convert("UTC"),
                       "available_at": (when + pd.Timedelta(hours=1)).tz_convert("UTC"),
                       "scheduled": item["scheduled"], "detail": detail})
    return events


# --- CPI et NFP (millésimes ALFRED) -----------------------------------------------------------------------------

_VINTAGE = re.compile(r'<option value="(\d{4}-\d{2}-\d{2})">')


def parse_vintages(html: str) -> list[date]:
    days = sorted({date.fromisoformat(v) for v in _VINTAGE.findall(html)})
    if not days:
        raise ValueError("ALFRED : aucun millésime dans la page")
    return days


def release_days(vintages: list[date], *, start: date) -> tuple[list[date], list[date]]:
    """(publications, corrections écartées) : un millésime à moins de 7 jours du précédent est une correction."""
    kept: list[date] = []
    dropped: list[date] = []
    for day in vintages:
        if kept and (day - kept[-1]).days < CORRECTION_WINDOW_DAYS:
            dropped.append(day)
            continue
        kept.append(day)
    return [d for d in kept if d >= start], [d for d in dropped if d >= start]


def release_events(event: str, days: list[date], *, series: str) -> list[dict]:
    """Publications du BLS à 08:30 (New York) ; `available_at` = 09:30 New York."""
    out = []
    for day in days:
        when = pd.Timestamp(day).tz_localize(NEW_YORK) + pd.Timedelta(hours=8, minutes=30)
        out.append({"event": event, "key": day.isoformat(), "event_time": when.tz_convert("UTC"),
                    "available_at": (when + pd.Timedelta(hours=1)).tz_convert("UTC"), "scheduled": True,
                    "detail": f"millésime ALFRED {series}"})
    return out


# --- Halvings ---------------------------------------------------------------------------------------------------

#: Hauteur du bloc → horodatage du bloc (UTC), relevés le 2026-10-10 sur mempool.space (API publique).
HALVINGS = {210_000: "2012-11-28T15:24:38Z", 420_000: "2016-07-09T16:46:13Z", 630_000: "2020-05-11T19:23:43Z",
            840_000: "2024-04-20T00:09:27Z"}


def halving_events() -> list[dict]:
    out = []
    for height, stamp in HALVINGS.items():
        when = pd.Timestamp(stamp)
        out.append({"event": "HALVING", "key": str(height), "event_time": when,
                    "available_at": when + pd.Timedelta(hours=1), "scheduled": True,
                    "detail": f"bloc {height} (récompense divisée par deux)"})
    return out


# --- Phases lunaires (Meeus, chapitre 49) ------------------------------------------------------------------------

SYNODIC_DAYS = 29.530588861
DELTA_T_SECONDS = 69.0          # TT − UT, ~69 s de 2017 à 2026 (erreur < 2 s sur la période)
_NEW = (-0.40720, 0.17241, 0.01608, 0.01039, 0.00739, -0.00514, 0.00208)
_FULL = (-0.40614, 0.17302, 0.01614, 0.01043, 0.00734, -0.00515, 0.00209)
_PLANETARY = ((299.77, 0.107408, 0.000325), (251.88, 0.016321, 0.000165), (251.83, 26.651886, 0.000164),
              (349.42, 36.412478, 0.000126), (84.66, 18.206239, 0.000110), (141.74, 53.303771, 0.000062),
              (207.14, 2.453732, 0.000060), (154.84, 7.306860, 0.000056), (34.52, 27.261239, 0.000047),
              (207.19, 0.121824, 0.000042), (291.34, 1.844379, 0.000040), (161.72, 24.198154, 0.000037),
              (239.56, 25.513099, 0.000035), (331.55, 3.592518, 0.000023))


def _phase_jde(k: float) -> float:
    """Jour julien des éphémérides (TT) de la phase k (entier : nouvelle lune ; + 0,5 : pleine lune)."""
    t = k / 1236.85
    jde = (2451550.09766 + SYNODIC_DAYS * k + 0.00015437 * t ** 2 - 0.000000150 * t ** 3
           + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    rad = math.radians
    m = rad(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = rad(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4)
    f = rad(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4)
    om = rad(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    c = _NEW if abs(k - round(k)) < 1e-9 else _FULL
    sin = math.sin
    jde += (c[0] * sin(mp) + c[1] * e * sin(m) + c[2] * sin(2 * mp) + c[3] * sin(2 * f)
            + c[4] * e * sin(mp - m) + c[5] * e * sin(mp + m) + c[6] * e * e * sin(2 * m)
            - 0.00111 * sin(mp - 2 * f) - 0.00057 * sin(mp + 2 * f) + 0.00056 * e * sin(2 * mp + m)
            - 0.00042 * sin(3 * mp) + 0.00042 * e * sin(m + 2 * f) + 0.00038 * e * sin(m - 2 * f)
            - 0.00024 * e * sin(2 * mp - m) - 0.00017 * sin(om) - 0.00007 * sin(mp + 2 * m)
            + 0.00004 * sin(2 * mp - 2 * f) + 0.00004 * sin(3 * m) + 0.00003 * sin(mp + m - 2 * f)
            + 0.00003 * sin(2 * mp + 2 * f) - 0.00003 * sin(mp + m + 2 * f) + 0.00003 * sin(mp - m + 2 * f)
            - 0.00002 * sin(mp - m - 2 * f) - 0.00002 * sin(3 * mp + m) + 0.00002 * sin(4 * mp))
    for base, rate, amplitude in _PLANETARY:
        angle = base + rate * k - (0.009173 * t ** 2 if base == 299.77 else 0.0)
        jde += amplitude * sin(rad(angle))
    return jde


def _from_jde(jde: float) -> pd.Timestamp:
    unix = (jde - 2440587.5) * 86400.0 - DELTA_T_SECONDS
    return pd.Timestamp(round(unix), unit="s", tz="UTC")


def moon_phases(start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    """Nouvelles et pleines lunes entre `start` et `end` (UTC). Calculables d'avance : `available_at` est posé à
    l'instant de la phase (prudent) ; un test qui veut le calendrier lunaire à l'avance peut le recalculer ici."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    k = math.floor((start.year + (start.dayofyear - 1) / 365.25 - 2000) * 12.3685) - 1
    out = []
    while True:
        for phase, offset in (("NOUVELLE_LUNE", 0.0), ("PLEINE_LUNE", 0.5)):
            when = _from_jde(_phase_jde(k + offset))
            if start <= when <= end:
                out.append({"event": phase, "key": when.strftime("%Y-%m-%dT%H:%MZ"), "event_time": when,
                            "available_at": when, "scheduled": True, "detail": "calcul astronomique (Meeus 49)"})
            if when > end:
                return out
        k += 1


# --- Annonces de cotation Binance ---------------------------------------------------------------------------------

_TICKERS = re.compile(r"\(([A-Z0-9]{2,12})\)")
CATEGORIES = (
    ("FUTURES", re.compile(r"\bFutures\b|Perpetual|Delivery Contract", re.IGNORECASE)),
    ("PROGRAMME", re.compile(r"Launchpool|Launchpad|HODLer Airdrop|Megadrop|Alpha\b|Pre-Market|Simple Earn",
                             re.IGNORECASE)),
    ("COTATION_SPOT", re.compile(r"^Binance (Will List|Lists)\b|Trading on Binance\b|^Binance Will Open Trading",
                                 re.IGNORECASE)),
    ("NOUVELLES_PAIRES", re.compile(r"Trading Pairs?\b|Will Add .* Pairs", re.IGNORECASE)),
    ("MARGE_COLLATERAL", re.compile(r"\bMargin\b|Collateral|Cross Margin|Isolated", re.IGNORECASE)),
)


def listing_category(title: str) -> str:
    """Catégorie d'une annonce (règle fixe et testée ; « AUTRE » si aucune ne s'applique)."""
    for name, pattern in CATEGORIES:
        if pattern.search(title):
            return name
    return "AUTRE"


def tickers(title: str) -> list[str]:
    return sorted(set(_TICKERS.findall(title)))
