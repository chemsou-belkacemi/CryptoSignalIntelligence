"""Données du zoo (docs/CONTEXTE.md, « Données du zoo (2026-10) ») : liste fermée (refus AVANT tout appel réseau),
lecteurs sur réponses FICTIVES (aucun réseau), `available_at` (clôture de New York, week-end, publications
hebdomadaires), calendriers (FOMC, millésimes ALFRED, halvings, lunes), magasin et contrôles de qualité."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.context import calendrier, zoo
from crypto_signal_intelligence.context.store import load
from crypto_signal_intelligence.context.zoo_net import RefusedUrl, ZooHttp, ZooNetError, check_url
from crypto_signal_intelligence.forward.sources import PublicSources

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


# --- Liste fermée --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
    "https://alfred.stlouisfed.org/series/downloaddata?seid=PAYEMS",
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "https://www.federalreserve.gov/monetarypolicy/fomchistorical2019.htm",
    "https://api.alternative.me/fng/?limit=0",
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?next_page_token=abc",
    "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?catalogId=48",
])
def test_closed_list_accepts_the_declared_prefixes(url):
    assert check_url(url) == url


@pytest.mark.parametrize("url", [
    "http://fred.stlouisfed.org/graph/fredgraph.csv",                   # pas en HTTPS
    "https://fred.stlouisfed.org/graph/fredgraph.png",                  # autre chemin
    "https://api.stlouisfed.org/fred/series/observations",              # API à clé
    "https://www.binance.com/bapi/accounts/v1/private/user",            # privé
    "https://api.binance.com/api/v3/order",                             # ordre
    "https://fapi.binance.com/fapi/v1/order",                           # ordre à terme
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query/../../private",
    "https://user:pass@api.alternative.me/fng/",
    "https://api.alternative.me:8443/fng/",
    "https://evil.example/fng/",
    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",       # historique payant : non utilisé
])
def test_closed_list_refuses_before_any_network_call(url):
    calls = []
    client = ZooHttp(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200)),
                     sleep=lambda _s: None)
    with pytest.raises(RefusedUrl):
        client.get(url)
    assert calls == []


def test_forbidden_words_in_parameters_are_refused():
    with pytest.raises(RefusedUrl):
        check_url("https://api.alternative.me/fng/", {"apiKey": "x"})
    with pytest.raises(RefusedUrl):
        check_url("https://fred.stlouisfed.org/graph/fredgraph.csv", {"signature": "x"})


def test_crawl_delay_is_respected_for_fred():
    waits = []
    client = ZooHttp(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x")),
                     sleep=waits.append)
    client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "A"})
    client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "B"})
    client.get("https://api.alternative.me/fng/")
    assert len(waits) == 1 and 0 < waits[0] <= 2.0


def test_http_errors_and_oversized_answers_raise():
    client = ZooHttp(transport=httpx.MockTransport(lambda r: httpx.Response(503)), sleep=lambda _s: None)
    with pytest.raises(ZooNetError):
        client.get("https://api.alternative.me/fng/")


# --- available_at ----------------------------------------------------------------------------------------------------

def test_us_close_is_known_only_after_new_york_close_with_daylight_saving():
    # Été (EDT, UTC-4) : 16:00 New York = 20:00 UTC, + 2 h.
    assert zoo.available_at_fred("SP500", ts("2026-10-09")) == ts("2026-10-09 22:00")
    # Hiver (EST, UTC-5) : 16:00 New York = 21:00 UTC, + 2 h.
    assert zoo.available_at_fred("NASDAQ100", ts("2026-01-09")) == ts("2026-01-09 23:00")
    assert zoo.available_at_fred("VIXCLS", ts("2026-01-09")) == ts("2026-01-09 23:15")


def test_rates_and_weekly_dollar_index_have_longer_delays():
    # Taux à 10 ans (H.15) d'un vendredi : samedi 16:15 New York + 2 h.
    assert zoo.available_at_fred("DGS10", ts("2026-10-09")) == ts("2026-10-10 22:15")
    # Indice dollar large de la Fed (H.10 hebdomadaire) : mardi de la semaine suivante.
    for day in ("2026-09-28", "2026-09-30", "2026-10-02"):
        assert zoo.available_at_fred("DTWEXBGS", ts(day)) == ts("2026-10-06 22:15")


def test_friday_value_reaches_the_weekend_only_after_new_york_close():
    frame = pd.DataFrame({"date": [ts("2026-10-08"), ts("2026-10-09")], "value": [1.0, 2.0]})
    frame["available_at"] = [zoo.available_at_fred("SP500", d) for d in frame["date"]]
    probes = pd.DataFrame({"t": [ts("2026-10-09 15:00"), ts("2026-10-09 21:59"), ts("2026-10-09 22:00"),
                                 ts("2026-10-11 12:00")]})
    joined = pd.merge_asof(probes, frame.sort_values("available_at"), left_on="t", right_on="available_at")
    assert joined["value"].tolist() == [1.0, 1.0, 2.0, 2.0]


def test_ecb_fng_and_coinmetrics_delays():
    assert zoo.available_at_ecb(ts("2026-01-15")) == ts("2026-01-15 16:00")      # 17:00 CET
    assert zoo.available_at_ecb(ts("2026-07-15")) == ts("2026-07-15 15:00")      # 17:00 CEST
    assert zoo.available_at_fng(ts("2026-10-10")) == ts("2026-10-10 02:00")
    assert zoo.available_at_coinmetrics(ts("2026-10-07")) == ts("2026-10-09")


# --- Lecteurs sur réponses fictives --------------------------------------------------------------------------------

def _fomc_calendar_html() -> str:
    def row(month, days, statement=None):
        link = f'<a href="/newsevents/pressreleases/monetary{statement}a.htm">HTML</a>' if statement else ""
        return (f'<div class="fomc-meeting__month col-xs-5"><strong>{month}</strong></div>\n'
                f'<div class="fomc-meeting__date col-xs-4">{days}</div>{link}')
    return ('<h4><a id="1">2026 FOMC Meetings</a></h4>' + row("January", "27-28", "20260128")
            + row("Jan/Feb", "31-1*") + '<h4><a id="2">2025 FOMC Meetings</a></h4>'
            + row("August", "22 (notation vote)", "20250822") + row("December", "9-10*", "20251210"))


def _fomc_history_html(year: int) -> str:
    return (f'<h5 class="panel-heading">January 29-30 Meeting - {year}</h5>'
            f'<a href="/newsevents/pressreleases/monetary{year}0130a.htm">x</a>'
            f'<h5 class="panel-heading">March 17-18 (cancelled) Meeting - {year}</h5>'
            f'<h5 class="panel-heading">October 4 (unscheduled) - {year}</h5>'
            f'<a href="/newsevents/pressreleases/monetary{year}1011a.htm">x</a>')


def _alfred_html(days: list[str]) -> str:
    return "<select>" + "".join(f'<option value="{d}">{d}</option>' for d in days) + "</select>"


class FakeServer:
    """Routeur de réponses fictives par hôte et chemin ; `fail` : hôtes en panne (HTTP 503)."""

    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, host, path = request.url, request.url.host, request.url.path
        self.paths.append(f"{host}{path}")
        if host in self.fail:
            return httpx.Response(503)
        params = dict(url.params)
        if host == "fred.stlouisfed.org":
            series = params["id"]
            return httpx.Response(200, text=f"observation_date,{series}\n2026-10-08,100.5\n2026-10-09,.\n"
                                            f"2026-10-12,101.0\n")
        if host == "alfred.stlouisfed.org":
            days = (["2025-12-10", "2026-01-13", "2026-02-11"] if params["seid"] == "CPIAUCNS"
                    else ["2020-05-08", "2020-05-11", "2020-06-05"])
            return httpx.Response(200, text=_alfred_html(days))
        if path.endswith("fomccalendars.htm"):
            return httpx.Response(200, text=_fomc_calendar_html())
        if "fomchistorical" in path:
            return httpx.Response(200, text=_fomc_history_html(int(path[-8:-4])))
        if host == "api.alternative.me":
            return httpx.Response(200, json={"data": [{"value": "64", "timestamp": "1791590400"},
                                                      {"value": "30", "timestamp": "1517443200"}]})
        if host == "community-api.coinmetrics.io":
            if "next_page_token" in params:
                return httpx.Response(200, json={"data": [{"asset": "eth", "time": "2026-10-08T00:00:00.000000000Z",
                                                           "CapMrktCurUSD": "3.0e11"}]})
            return httpx.Response(200, json={
                "data": [{"asset": "btc", "time": "2026-10-08T00:00:00.000000000Z", "CapMrktCurUSD": "2.2e12"}],
                "next_page_url": "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?next_page_token=p2"})
        if host == "data-api.ecb.europa.eu":
            currency = path.split("/")[-1].split(".")[1]
            rate = {"USD": 1.1, "JPY": 160.0, "GBP": 0.85, "CAD": 1.5, "SEK": 11.0, "CHF": 0.95}[currency]
            text = "KEY,TIME_PERIOD,OBS_VALUE\n" + f"x,2026-10-08,{rate}\n"
            if currency == "USD":
                text += "x,2026-10-09,1.2\n"                         # un seul taux ce jour-là : pas d'indice
            return httpx.Response(200, text=text)
        if host == "www.binance.com":
            page = int(params["pageNo"])
            articles = [] if page > 1 else [
                {"code": "a1", "title": "Binance Will List Example (EXM)", "releaseDate": 1791590400000},
                {"code": "a2", "title": "Binance Futures Will Launch USDⓈ-M EXM Perpetual Contract",
                 "releaseDate": 1791504000000}]
            return httpx.Response(200, json={"data": {"catalogs": [{"articles": articles}]}})
        if host == "api.nasdaq.com":
            return httpx.Response(200, json={"data": {"tradesTable": {"rows": [
                {"date": "10/09/2026", "close": "$250.10"}, {"date": "10/08/2026", "close": "$249.00"}]}}})
        return httpx.Response(404)


def _clients(server: FakeServer) -> zoo.Clients:
    return zoo.Clients(zoo=ZooHttp(transport=httpx.MockTransport(server), sleep=lambda _s: None),
                       web=PublicSources(transport=httpx.MockTransport(server)))


def test_fred_reader_skips_missing_values_and_stamps_availability():
    client = ZooHttp(transport=httpx.MockTransport(FakeServer()), sleep=lambda _s: None)
    rows = zoo.fred_series(client, "SP500")
    assert [str(r["date"].date()) for r in rows] == ["2026-10-08", "2026-10-12"]
    assert rows[0]["available_at"] == ts("2026-10-08 22:00")


def test_fred_reader_rejects_an_unexpected_header():
    client = ZooHttp(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
                     sleep=lambda _s: None)
    with pytest.raises(ZooNetError):
        zoo.fred_series(client, "SP500")


def test_ecb_dollar_index_needs_the_six_rates():
    client = ZooHttp(transport=httpx.MockTransport(FakeServer()), sleep=lambda _s: None)
    rows = zoo.ecb_dollar(client)
    index = [r for r in rows if r["key"] == "DXY_BCE"]
    assert [str(r["date"].date()) for r in index] == ["2026-10-08"]
    assert 50 < index[0]["value"] < 200
    assert index[0]["available_at"] == ts("2026-10-08 15:00")


def test_coinmetrics_follows_pages_through_the_closed_list():
    client = ZooHttp(transport=httpx.MockTransport(FakeServer()), sleep=lambda _s: None)
    rows = zoo.coinmetrics_caps(client)
    assert {r["key"] for r in rows} == {"btc", "eth"}
    assert all(r["available_at"] == ts("2026-10-10") for r in rows)


def test_binance_listings_are_classified_and_delayed():
    client = ZooHttp(transport=httpx.MockTransport(FakeServer()), sleep=lambda _s: None)
    events = zoo.binance_listings(client)
    by_key = {e["key"]: e for e in events}
    assert by_key["a1"]["category"] == "COTATION_SPOT" and by_key["a1"]["tickers"] == "EXM"
    assert by_key["a2"]["category"] == "FUTURES"
    assert by_key["a1"]["available_at"] - by_key["a1"]["event_time"] == pd.Timedelta(minutes=10)


# --- Calendriers -------------------------------------------------------------------------------------------------

def test_fomc_meetings_spanning_two_months_end_on_the_second():
    assert calendrier.meeting_last_day(2017, "Jan/Feb", "31-1") == date(2017, 2, 1)
    assert calendrier.meeting_last_day(2019, "April/May", "30-1") == date(2019, 5, 1)
    assert calendrier.meeting_last_day(2026, "March", "17-18") == date(2026, 3, 18)
    assert calendrier.meeting_last_day(2019, "October", "4") == date(2019, 10, 4)


def test_fomc_events_drop_cancelled_and_flag_unscheduled():
    meetings, statements = calendrier.parse_fomc_historical(_fomc_history_html(2020), 2020)
    events = {e["key"]: e for e in calendrier.fomc_events(meetings, statements)}
    assert "2020-03-18" not in events                                  # annulée
    assert events["2020-01-30"]["scheduled"] is True
    assert events["2020-01-30"]["event_time"] == ts("2020-01-30 19:00")  # 14:00 New York (hiver)
    assert events["2020-10-04"]["scheduled"] is False
    assert events["2020-10-11"]["detail"] == "communiqué hors réunion"
    for e in events.values():
        assert e["available_at"] == e["event_time"] + pd.Timedelta(hours=1)


def test_fomc_calendar_page_reads_years_and_notation_votes():
    meetings, statements = calendrier.parse_fomc_calendar(_fomc_calendar_html())
    days = {m["day"]: m["note"] for m in meetings}
    assert days[date(2026, 2, 1)] == "" and days[date(2025, 8, 22)] == "notation vote"
    assert date(2025, 12, 10) in statements


def test_alfred_corrections_within_seven_days_are_dropped():
    vintages = calendrier.parse_vintages(_alfred_html(["2016-12-02", "2020-05-08", "2020-05-11", "2020-06-05"]))
    kept, dropped = calendrier.release_days(vintages, start=date(2017, 1, 1))
    assert kept == [date(2020, 5, 8), date(2020, 6, 5)] and dropped == [date(2020, 5, 11)]
    event = calendrier.release_events("NFP", [date(2020, 6, 5)], series="PAYEMS")[0]
    assert event["event_time"] == ts("2020-06-05 12:30")              # 08:30 New York (été)
    assert event["available_at"] == ts("2020-06-05 13:30")


def test_moon_phases_match_known_events_within_three_minutes():
    known = {("NOUVELLE_LUNE", "2017-08-21 18:30"), ("NOUVELLE_LUNE", "2024-04-08 18:21"),
             ("PLEINE_LUNE", "2025-03-14 06:55")}
    phases = calendrier.moon_phases(ts("2017-01-01"), ts("2025-12-31"))
    for event, when in known:
        nearest = min(abs(p["event_time"] - ts(when)) for p in phases if p["event"] == event)
        assert nearest <= pd.Timedelta(minutes=3), (event, when)
    gaps = pd.Series(sorted(p["event_time"] for p in phases if p["event"] == "PLEINE_LUNE")).diff().dropna()
    assert gaps.min() > pd.Timedelta(days=29) and gaps.max() < pd.Timedelta(days=30)


def test_halvings_are_the_four_blocks():
    events = calendrier.halving_events()
    assert [e["key"] for e in events] == ["210000", "420000", "630000", "840000"]
    assert events[-1]["event_time"] == ts("2024-04-20 00:09:27")


@pytest.mark.parametrize(("title", "category"), [
    ("Binance Will List Example (EXM)", "COTATION_SPOT"),
    ("Binance Lists ENG", "COTATION_SPOT"),
    ("Qtum Trading on Binance", "COTATION_SPOT"),
    ("Binance Adds IOTX/USDT and RLC/USDT Trading Pairs", "NOUVELLES_PAIRES"),
    ("Binance Futures Will Launch NEO/USDT Perpetual Contract", "FUTURES"),
    ("Introducing Example (EXM) on Binance HODLer Airdrops!", "PROGRAMME"),
    ("Binance Will Add 4 bStocks Tokenized Securities as Collateral Asset", "MARGE_COLLATERAL"),
    ("Notice on something else", "AUTRE"),
])
def test_listing_categories(title, category):
    assert calendrier.listing_category(title) == category


# --- Téléchargement complet, magasin, qualité ------------------------------------------------------------------------

def test_download_writes_every_series_with_available_at(settings):
    server = FakeServer()
    out = zoo.download(settings, now=NOW, clients=_clients(server))
    assert all("error" not in r for r in out.values()), out
    assert out["zoo_marches"]["errors"] == {}
    marches = load(settings, "zoo_marches")
    assert {"SP500", "VIXCLS", "DGS10", "DTWEXBGS", "GLD"} <= set(marches["key"])
    assert marches["available_at"].notna().all()
    assert (pd.to_datetime(marches["available_at"], utc=True) > pd.to_datetime(marches["date"], utc=True)).all()
    macro = zoo.load_events(settings, "calendrier_macro")
    assert {"FOMC", "CPI", "NFP"} <= set(macro["event"])
    assert "2020-05-11" not in set(macro.loc[macro["event"] == "NFP", "key"])
    # Aucun appel hors des hôtes déclarés.
    assert {p.split("/")[0] for p in server.paths} <= {
        "fred.stlouisfed.org", "alfred.stlouisfed.org", "www.federalreserve.gov", "api.alternative.me",
        "community-api.coinmetrics.io", "data-api.ecb.europa.eu", "www.binance.com", "api.nasdaq.com"}


def test_download_is_idempotent_and_a_failing_source_does_not_stop_the_others(settings):
    zoo.download(settings, now=NOW, clients=_clients(FakeServer()))
    before = len(load(settings, "zoo_marches"))
    out = zoo.download(settings, now=NOW, clients=_clients(FakeServer(fail={"api.alternative.me"})))
    assert "error" in out["zoo_fear_greed"]
    assert "error" not in out["zoo_capitalisations"]
    assert len(load(settings, "zoo_marches")) == before                 # pas de doublon au second passage
    assert len(load(settings, "zoo_fear_greed")) == 2                   # l'ancien téléchargement reste


def test_unknown_series_is_refused(settings):
    with pytest.raises(ValueError):
        zoo.download(settings, now=NOW, only=["inconnue"], clients=_clients(FakeServer()))


def test_write_series_refuses_an_availability_before_the_value(settings):
    record = {"key": "SP500", "date": ts("2026-10-09"), "field": "value", "value": 1.0,
              "available_at": ts("2026-10-08")}
    with pytest.raises(ValueError):
        zoo.write_series(settings, "zoo_marches", [record], source="test", now=NOW)


def test_quality_counts_gaps_duplicates_and_units():
    frame = pd.DataFrame({
        "key": ["VIXCLS"] * 4, "field": ["value"] * 4, "kind": ["HISTORIQUE"] * 4,
        "date": [ts("2026-10-05"), ts("2026-10-06"), ts("2026-10-06"), ts("2026-10-12")],
        "value": [15.0, 16.0, 16.0, 0.16],                              # 0,16 : fraction au lieu de points
        "available_at": [ts("2026-10-05 22:15"), ts("2026-10-06 22:15"), ts("2026-10-06 22:15"), ts("2026-10-11")]})
    q = zoo.quality_series(frame, business_days=True)["VIXCLS:value"]
    assert q["duplicates"] == 1
    assert q["missing_days"] == 3                                       # 7, 8 et 9 octobre (jours ouvrés)
    assert q["largest_gap_days"] == 6
    assert q["out_of_unit_range"] == 1
    assert q["bad_available_at"] == 1


def test_status_reports_and_crosschecks_the_frozen_light_calendar(settings):
    zoo.download(settings, now=NOW, clients=_clients(FakeServer()))
    report = zoo.status(settings)
    assert set(report) == set(zoo.SERIES)
    json.dumps(report, default=str)
    assert "recoupement_light" in report["calendrier_macro"]
