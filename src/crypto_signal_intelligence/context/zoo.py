"""« Données du zoo » (programme « zoo des stratégies », 2026-10) : données gratuites et publiques qui manquaient,
téléchargées dans le magasin de contexte (docs/CONTEXTE.md, section « Données du zoo (2026-10) »).

Séries numériques (magasin `data/context/<série>.parquet`, format long, lignes HISTORIQUE, plus une colonne
`available_at`) :
- `zoo_marches` : S&P 500, Nasdaq 100, Nasdaq composite, VIX, taux à 10 ans, indices dollar de la Fed (FRED), or
  (ETF GLD, Nasdaq) ;
- `zoo_dollar_bce` : indice dollar recalculé (formule ICE) aux taux de référence de la BCE, et ces taux ;
- `zoo_fear_greed` : indice Fear & Greed (alternative.me) ;
- `zoo_capitalisations` : capitalisation de BTC et ETH (CoinMetrics community).
Événements (`data/context/evenements/<nom>.parquet`, une ligne par événement) :
- `calendrier_macro` : FOMC (Fed), CPI et NFP (millésimes ALFRED) ;
- `calendrier_crypto` : halvings, nouvelles et pleines lunes (calcul local) ;
- `annonces_binance` : annonces de la catégorie « New Cryptocurrency Listing » (titres, heure de publication).

CAUSALITÉ : chaque ligne porte `available_at` = heure de publication + latence prudente (règles `available_at_*`
ci-dessous, déclarées dans docs/CONTEXTE.md). Une valeur de vendredi d'un marché fermé le week-end n'est connue
qu'après la clôture de New York ; une jointure « au plus tard » sur `available_at` reprend ensuite la dernière valeur
connue pendant le week-end. Valeurs HISTORIQUE : telles que publiées aujourd'hui (révisions comprises).

Information seulement : aucune de ces données n'entre dans un test en cours, une décision ou un signal. Aucun
rendement n'est calculé ici : téléchargement et contrôle de qualité (trous, doublons, unités) seulement.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..forward.sources import PublicSources, SourceError, dxy_from_ecb
from . import calendrier
from .fetch import nasdaq_gld
from .store import HISTORY, context_dir, load, locked, rows, upsert
from .zoo_net import ZooHttp, ZooNetError

START = date(2017, 1, 1)
NEW_YORK = "America/New_York"
FRANKFURT = "Europe/Berlin"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFRED = "https://alfred.stlouisfed.org/series/downloaddata"
FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORY = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
FNG = "https://api.alternative.me/fng/"
COINMETRICS = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A"
BINANCE_CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
LISTING_CATALOG = 48
ECB_CURRENCIES = ("USD", "JPY", "GBP", "CAD", "SEK", "CHF")

#: Série FRED → (unité, borne basse, borne haute, règle de disponibilité). Bornes : contrôle d'UNITÉ seulement
#: (pourcentage contre fraction, points contre milliers), pas un filtre de valeurs.
FRED_SERIES = {
    "SP500": ("points d'indice", 500.0, 50_000.0, "close"),
    "NASDAQ100": ("points d'indice", 1_000.0, 200_000.0, "close"),
    "NASDAQCOM": ("points d'indice", 1_000.0, 200_000.0, "close"),
    "VIXCLS": ("points de volatilité", 5.0, 150.0, "vix"),
    "DGS10": ("pour cent par an", -2.0, 25.0, "h15"),
    "DTWEXBGS": ("indice, janvier 2006 = 100", 50.0, 250.0, "h10"),
    "DTWEXAFEGS": ("indice, janvier 2006 = 100", 50.0, 250.0, "h10"),
}
UNITS = {**{k: v[:3] for k, v in FRED_SERIES.items()},
         "GLD": ("dollars par part", 50.0, 2_000.0),
         "DXY_BCE": ("points d'indice (formule ICE)", 50.0, 200.0),
         "USD": ("unités par euro", 0.5, 2.5), "JPY": ("unités par euro", 50.0, 400.0),
         "GBP": ("unités par euro", 0.5, 1.5), "CAD": ("unités par euro", 0.8, 2.5),
         "SEK": ("unités par euro", 5.0, 20.0), "CHF": ("unités par euro", 0.5, 2.0),
         "fear_greed": ("indice 0-100", 0.0, 100.0),
         "btc": ("dollars", 1e5, 1e14), "eth": ("dollars", 1e7, 1e14)}
SOURCES = {"zoo_marches": "FRED (St. Louis Fed ; SP500 et Nasdaq sous licence, 10 ans au plus pour SP500) ; Nasdaq (GLD)",
           "zoo_dollar_bce": "BCE (taux de référence) ; formule ICE de l'indice dollar recalculée",
           "zoo_fear_greed": "alternative.me (attribution demandée)",
           "zoo_capitalisations": "CoinMetrics community (CC BY-NC, attribution)"}
EVENTS = ("calendrier_macro", "calendrier_crypto", "annonces_binance")
EVENT_COLUMNS = ["event", "key", "event_time", "available_at", "scheduled", "detail", "category", "tickers", "source",
                 "retrieved_at"]
#: Marchés américains : séries au rythme des jours ouvrés ; crypto : tous les jours.
BUSINESS_DAYS = {"zoo_marches", "zoo_dollar_bce"}


# --- Règles de disponibilité (available_at) -----------------------------------------------------------------------

def _at(day: pd.Timestamp, zone: str, hours: float) -> pd.Timestamp:
    local = pd.Timestamp(day.date()).tz_localize(zone) + pd.Timedelta(hours=hours)
    return local.tz_convert("UTC")


def available_at_fred(series: str, day: pd.Timestamp) -> pd.Timestamp:
    """Clôture des indices : 16:00 New York + 2 h ; VIX : 16:15 + 2 h ; taux à 10 ans (H.15) : lendemain 16:15 +
    2 h ; indices dollar de la Fed (H.10, hebdomadaire, publié le lundi suivant) : mardi suivant 16:15 + 2 h."""
    rule = FRED_SERIES[series][3]
    if rule == "close":
        return _at(day, NEW_YORK, 18.0)
    if rule == "vix":
        return _at(day, NEW_YORK, 18.25)
    if rule == "h15":
        return _at(day + pd.Timedelta(days=1), NEW_YORK, 18.25)
    if rule == "h10":
        tuesday = day + pd.Timedelta(days=8 - day.weekday())   # mardi de la semaine suivante
        return _at(tuesday, NEW_YORK, 18.25)
    raise ValueError(rule)


def available_at_close_new_york(day: pd.Timestamp) -> pd.Timestamp:
    return _at(day, NEW_YORK, 18.0)


def available_at_ecb(day: pd.Timestamp) -> pd.Timestamp:
    """Taux de référence publiés vers 16:00, heure de Francfort : + 1 h."""
    return _at(day, FRANKFURT, 17.0)


def available_at_fng(day: pd.Timestamp) -> pd.Timestamp:
    """Valeur horodatée J 00:00 UTC, publiée vers J 00:00 UTC : + 2 h."""
    return day.tz_convert("UTC").floor("D") + pd.Timedelta(hours=2)


def available_at_coinmetrics(day: pd.Timestamp) -> pd.Timestamp:
    """Journée J (00:00 → 24:00 UTC), publiée vers J+1 02-03 h UTC : J+2 00:00 UTC retenu (déjà déclaré)."""
    return day.tz_convert("UTC").floor("D") + pd.Timedelta(days=2)


# --- Lecteurs ---------------------------------------------------------------------------------------------------

def fred_series(client: ZooHttp, series: str, *, start: date = START) -> list[dict]:
    text = client.get(FRED, {"id": series, "cosd": start.isoformat()}).decode()
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2 or header[1] != series:
        raise ZooNetError(f"FRED {series} : en-tête inattendu {header}")
    out = []
    for row in reader:
        if len(row) >= 2 and row[1] not in ("", "."):
            day = pd.Timestamp(row[0], tz="UTC")
            out.append({"key": series, "date": day, "field": "value", "value": float(row[1]),
                        "available_at": available_at_fred(series, day)})
    return out


def gld(client: PublicSources, *, today: date) -> list[dict]:
    """ETF GLD (approximation de l'or), API publique de Nasdaq (10 ans au plus ; fin = veille)."""
    start = max(START, today - timedelta(days=3650))
    out = []
    for r in nasdaq_gld(client, start=start.isoformat(), end=(today - timedelta(days=1)).isoformat()):
        day = pd.Timestamp(pd.Timestamp(r["date"]).date(), tz="UTC")
        out.append({**r, "date": day, "available_at": available_at_close_new_york(day)})
    return out


def ecb_dollar(client: ZooHttp, *, start: date = START) -> list[dict]:
    """Taux de référence BCE (1 EUR = x) des six devises de l'indice dollar, et l'indice recalculé (formule ICE)
    les jours où les six taux existent."""
    by_day: dict[str, dict[str, float]] = {}
    for currency in ECB_CURRENCIES:
        text = client.get(ECB.format(currency=currency),
                          {"startPeriod": start.isoformat(), "format": "csvdata", "detail": "dataonly"}).decode()
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("OBS_VALUE") not in (None, "", "NaN"):
                by_day.setdefault(row["TIME_PERIOD"], {})[currency] = float(row["OBS_VALUE"])
    out = []
    for stamp, rates in sorted(by_day.items()):
        day = pd.Timestamp(stamp, tz="UTC")
        when = available_at_ecb(day)
        for currency, value in rates.items():
            out.append({"key": currency, "date": day, "field": "per_eur", "value": value, "available_at": when})
        if len(rates) == len(ECB_CURRENCIES):
            out.append({"key": "DXY_BCE", "date": day, "field": "value", "value": dxy_from_ecb(rates),
                        "available_at": when})
    if not out:
        raise ZooNetError("BCE : aucun taux")
    return out


def fear_greed(client: ZooHttp) -> list[dict]:
    payload = client.get_json(FNG, {"limit": "0", "format": "json"})
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise ZooNetError("alternative.me : réponse sans « data »")
    out = []
    for row in data:
        day = pd.Timestamp(int(row["timestamp"]), unit="s", tz="UTC")
        out.append({"key": "fear_greed", "date": day, "field": "value", "value": float(row["value"]),
                    "available_at": available_at_fng(day)})
    return out


def coinmetrics_caps(client: ZooHttp, *, start: str = "2010-01-01", pages: int = 20) -> list[dict]:
    params: dict | None = {"assets": "btc,eth", "metrics": "CapMrktCurUSD", "frequency": "1d", "page_size": 5000,
                           "start_time": start, "paging_from": "start"}
    url, out = COINMETRICS, []
    for _ in range(pages):
        payload = client.get_json(url, params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ZooNetError("CoinMetrics : réponse sans « data »")
        for row in data:
            if row.get("CapMrktCurUSD") is not None:
                day = pd.Timestamp(row["time"]).tz_convert("UTC")
                out.append({"key": row["asset"], "date": day, "field": "market_cap_usd",
                            "value": float(row["CapMrktCurUSD"]), "available_at": available_at_coinmetrics(day)})
        url, params = payload.get("next_page_url"), None
        if not url:
            break
    return out


def fomc(client: ZooHttp, *, start: date = START) -> list[dict]:
    meetings, statements = calendrier.parse_fomc_calendar(client.get(FOMC_CALENDAR).decode())
    if not meetings:
        raise ZooNetError("Fed : aucune réunion lue dans le calendrier")
    first = min(m["day"].year for m in meetings)
    for year in range(start.year, first):
        more, said = calendrier.parse_fomc_historical(client.get(FOMC_HISTORY.format(year=year)).decode(), year)
        if not more:
            raise ZooNetError(f"Fed : aucune réunion lue pour {year}")
        meetings += more
        statements |= said
    return [e for e in calendrier.fomc_events(meetings, statements) if e["key"] >= start.isoformat()]


def bls_releases(client: ZooHttp, *, start: date = START) -> tuple[list[dict], dict]:
    """CPI (CPIAUCNS) et NFP (PAYEMS) : dates de publication tirées des millésimes ALFRED."""
    out, dropped = [], {}
    for event, series in (("CPI", "CPIAUCNS"), ("NFP", "PAYEMS")):
        vintages = calendrier.parse_vintages(client.get(ALFRED, {"seid": series}).decode())
        kept, corrections = calendrier.release_days(vintages, start=start)
        out += calendrier.release_events(event, kept, series=series)
        dropped[event] = [d.isoformat() for d in corrections]
    return out, dropped


def binance_listings(client: ZooHttp, *, page_size: int = 50, max_pages: int = 100) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        payload = client.get_json(BINANCE_CMS, {"type": 1, "catalogId": LISTING_CATALOG, "pageNo": page,
                                                "pageSize": page_size})
        catalogs = ((payload or {}).get("data") or {}).get("catalogs") if isinstance(payload, dict) else None
        if not isinstance(catalogs, list):
            raise ZooNetError("Binance : réponse sans « catalogs »")
        articles = [a for c in catalogs for a in (c.get("articles") or [])]
        for article in articles:
            when = pd.Timestamp(int(article["releaseDate"]), unit="ms", tz="UTC")
            title = str(article["title"])
            out.append({"event": "ANNONCE_BINANCE", "key": str(article["code"]), "event_time": when,
                        "available_at": when + pd.Timedelta(minutes=10), "scheduled": False, "detail": title,
                        "category": calendrier.listing_category(title), "tickers": ",".join(calendrier.tickers(title))})
        if len(articles) < page_size:
            break
    if not out:
        raise ZooNetError("Binance : aucune annonce")
    return out


# --- Magasin ----------------------------------------------------------------------------------------------------

def events_path(settings: Settings, name: str) -> Path:
    if name not in EVENTS:
        raise ValueError(f"table d'événements inconnue : {name}")
    return context_dir(settings) / "evenements" / f"{name}.parquet"


def write_series(settings: Settings, series: str, records: list[dict], *, source: str, now: datetime) -> int:
    """Lignes HISTORIQUE avec `available_at` (remplacent le téléchargement précédent, comme le reste du magasin)."""
    if not records:
        return 0
    frame = rows([{k: r[k] for k in ("key", "date", "field", "value")} for r in records], kind=HISTORY, source=source,
                 now=now)
    stamps = pd.DataFrame(records)[["key", "date", "field", "available_at"]]
    stamps["date"] = pd.to_datetime(stamps["date"], utc=True)
    stamps["available_at"] = pd.to_datetime(stamps["available_at"], utc=True)
    frame = frame.merge(stamps.drop_duplicates(["key", "date", "field"], keep="last"), on=["key", "date", "field"],
                        how="left")
    if frame["available_at"].isna().any() or (frame["available_at"] < frame["date"]).any():
        raise ValueError(f"{series} : available_at manquant ou antérieur à la date de la valeur")
    return upsert(settings, series, frame)


def write_events(settings: Settings, name: str, events: list[dict], *, source: str, now: datetime) -> int:
    """Table d'événements réécrite en entier (dernier téléchargement), doublons (event, key) écartés."""
    frame = pd.DataFrame(events)
    for column in EVENT_COLUMNS:
        if column not in frame:
            frame[column] = "" if column in ("detail", "category", "tickers") else None
    frame = frame.assign(source=source, retrieved_at=pd.Timestamp(now))[EVENT_COLUMNS]
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    frame["scheduled"] = frame["scheduled"].astype(bool)
    if (frame["available_at"] < frame["event_time"]).any():
        raise ValueError(f"{name} : available_at antérieur à l'événement")
    frame = frame.drop_duplicates(["event", "key"], keep="last").sort_values(["event_time", "event", "key"])
    path = events_path(settings, name)
    with locked(settings):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        frame.reset_index(drop=True).to_parquet(tmp, index=False)
        tmp.replace(path)
    return len(frame)


def load_events(settings: Settings, name: str) -> pd.DataFrame:
    path = events_path(settings, name)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=EVENT_COLUMNS)


# --- Contrôles de qualité ------------------------------------------------------------------------------------------

def quality_series(frame: pd.DataFrame, *, business_days: bool) -> dict[str, dict]:
    """Par clé et champ : lignes, première et dernière date, doublons, jours manquants (jours ouvrés pour les marchés,
    tous les jours sinon ; jours fériés comptés comme manquants), plus long trou, valeurs hors bornes d'unité,
    `available_at` absent ou antérieur à la date."""
    out: dict[str, dict] = {}
    frame = frame[frame["kind"] == HISTORY] if "kind" in frame else frame
    for (key, field), part in frame.groupby(["key", "field"]):
        days = pd.to_datetime(part["date"], utc=True).dt.floor("D")
        unique = pd.DatetimeIndex(days.drop_duplicates().sort_values())
        expected = (pd.bdate_range(unique[0], unique[-1], tz="UTC") if business_days
                    else pd.date_range(unique[0], unique[-1], freq="D", tz="UTC"))
        gaps = unique.to_series().diff().dt.days.dropna()
        low, high = UNITS.get(str(key), ("?", float("-inf"), float("inf")))[1:]
        values = part["value"].astype(float)
        stamps = pd.to_datetime(part["available_at"], utc=True) if "available_at" in part else None
        bad_stamp = (int(stamps.isna().sum() + (stamps < pd.to_datetime(part["date"], utc=True)).sum())
                     if stamps is not None else len(part))
        out[f"{key}:{field}"] = {
            "rows": int(len(part)), "first": str(unique[0].date()), "last": str(unique[-1].date()),
            "duplicates": int(len(days) - len(unique)), "missing_days": int(len(expected.difference(unique))),
            "largest_gap_days": int(gaps.max()) if len(gaps) else 0,
            "out_of_unit_range": int(((values < low) | (values > high)).sum()), "bad_available_at": bad_stamp,
            "unit": UNITS.get(str(key), ("?",))[0]}
    return out


def quality_events(frame: pd.DataFrame) -> dict[str, dict]:
    """Par type d'événement : nombre, par année, doublons, jours de week-end (heure de New York), available_at."""
    out: dict[str, dict] = {}
    for event, part in frame.groupby("event"):
        times = pd.to_datetime(part["event_time"], utc=True)
        local = times.dt.tz_convert(NEW_YORK)
        out[str(event)] = {
            "rows": int(len(part)), "first": str(times.min())[:16], "last": str(times.max())[:16],
            "per_year": dict(sorted(Counter(int(y) for y in times.dt.year).items())),
            "duplicates": int(part.duplicated(["event", "key"]).sum()),
            "weekend_new_york": int((local.dt.weekday >= 5).sum()),
            "bad_available_at": int((pd.to_datetime(part["available_at"], utc=True) < times).sum())}
    return out


def crosscheck_light(frame: pd.DataFrame) -> dict:
    """Recoupe les dates CPI / NFP / FOMC avec le calendrier FIGÉ de `forward/light.py` (lu, jamais modifié) sur
    leur période commune passée : dates présentes des deux côtés, absentes d'un côté."""
    from ..forward.light import MACRO_CALENDAR
    names = {"FED": "FOMC", "CPI": "CPI", "NFP": "NFP"}
    frozen = {(names[n], d) for d, n in MACRO_CALENDAR}
    ours = {(str(e), str(t)[:10]) for e, t in zip(frame["event"], pd.to_datetime(frame["event_time"], utc=True)
                                                  .dt.tz_convert(NEW_YORK).dt.date.astype(str), strict=True)}
    last_seen = {e: max((d for ev, d in ours if ev == e), default="") for e in names.values()}
    window = {(e, d) for e, d in frozen if d <= last_seen.get(e, "")}
    return {"common": sorted(window & ours), "missing_here": sorted(window - ours)}


# --- Téléchargements ----------------------------------------------------------------------------------------------

class Clients:
    def __init__(self, *, zoo: ZooHttp | None = None, web: PublicSources | None = None):
        self.zoo = zoo or ZooHttp()
        self.web = web or PublicSources()

    def close(self) -> None:
        self.zoo.close()
        self.web.close()


def _jobs(settings: Settings, clients: Clients, *, now: datetime) -> dict[str, Callable[[], dict]]:
    today = pd.Timestamp(now).date()

    def marches() -> dict:
        written, errors = 0, {}
        for series in FRED_SERIES:
            try:
                written += write_series(settings, "zoo_marches", fred_series(clients.zoo, series),
                                        source=f"FRED {series}", now=now)
            except (ZooNetError, ValueError) as exc:
                errors[series] = str(exc)[:160]
        try:
            written += write_series(settings, "zoo_marches", gld(clients.web, today=today),
                                    source="Nasdaq (ETF GLD)", now=now)
        except (SourceError, ValueError) as exc:
            errors["GLD"] = str(exc)[:160]
        return {"rows": written, "errors": errors}

    def macro_calendar() -> dict:
        events = fomc(clients.zoo)
        releases, corrections = bls_releases(clients.zoo)
        written = write_events(settings, "calendrier_macro", events + releases,
                               source="Fed (calendrier FOMC) ; ALFRED (millésimes CPIAUCNS, PAYEMS)", now=now)
        return {"rows": written, "corrections_ecartees": corrections}

    def crypto_calendar() -> dict:
        end = pd.Timestamp(date(today.year + 1, 12, 31), tz="UTC")
        events = calendrier.halving_events() + calendrier.moon_phases(pd.Timestamp(START, tz="UTC"), end)
        return {"rows": write_events(settings, "calendrier_crypto", events,
                                     source="blocs Bitcoin (mempool.space, vérifiés) ; calcul lunaire local", now=now)}

    return {
        "zoo_marches": marches,
        "zoo_dollar_bce": lambda: {"rows": write_series(settings, "zoo_dollar_bce", ecb_dollar(clients.zoo),
                                                        source=SOURCES["zoo_dollar_bce"], now=now)},
        "zoo_fear_greed": lambda: {"rows": write_series(settings, "zoo_fear_greed", fear_greed(clients.zoo),
                                                        source=SOURCES["zoo_fear_greed"], now=now)},
        "zoo_capitalisations": lambda: {"rows": write_series(settings, "zoo_capitalisations",
                                                             coinmetrics_caps(clients.zoo),
                                                             source=SOURCES["zoo_capitalisations"], now=now)},
        "calendrier_macro": macro_calendar,
        "calendrier_crypto": crypto_calendar,
        "annonces_binance": lambda: {"rows": write_events(settings, "annonces_binance", binance_listings(clients.zoo),
                                                          source="Binance (annonces publiques)", now=now)},
    }


SERIES = ("zoo_marches", "zoo_dollar_bce", "zoo_fear_greed", "zoo_capitalisations", *EVENTS)


def download(settings: Settings, *, now: datetime, only: list[str] | None = None, clients: Clients | None = None,
             progress: Callable[[str], None] | None = None) -> dict:
    """Télécharge les séries demandées (toutes par défaut) ; une source en panne n'arrête pas les autres."""
    unknown = sorted(set(only or []) - set(SERIES))
    if unknown:
        raise ValueError(f"séries inconnues : {', '.join(unknown)} (connues : {', '.join(SERIES)})")
    say = progress or (lambda _text: None)
    own = clients is None
    clients = clients or Clients()
    out: dict = {}
    try:
        jobs = _jobs(settings, clients, now=now)
        for name in only or list(SERIES):
            say(name)
            try:
                out[name] = jobs[name]()
            except Exception as exc:  # noqa: BLE001 - une source en panne n'arrête pas les autres
                out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        if own:
            clients.close()
    return out


def status(settings: Settings) -> dict:
    """Contrôle de qualité de tout ce qui est en magasin (aucun réseau, aucun rendement)."""
    out: dict = {}
    for series in SERIES:
        if series in EVENTS:
            frame = load_events(settings, series)
            if frame.empty:
                continue
            out[series] = quality_events(frame)
            if series == "calendrier_macro":
                out[series]["recoupement_light"] = crosscheck_light(frame)
        else:
            frame = load(settings, series)
            if frame.empty:
                continue
            out[series] = quality_series(frame, business_days=series in BUSINESS_DAYS)
    return out
