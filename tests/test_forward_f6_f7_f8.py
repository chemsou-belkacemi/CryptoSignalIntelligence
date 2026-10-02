"""Tests en direct F6_CAPITULATION, F7_LISTINGS et F8_NEWS (forward/f6.py, f7.py, f8.py) : règles à la main, sources
simulées, bout en bout court, pré-inscriptions. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f5, f6, f7, f8, registry
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.forward.sources import PublicSources

from .conftest import PROJECT

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT", "PODUSDT"), {}, "a" * 64, "b" * 64)
DAY = pd.Timestamp("2026-10-10", tz="UTC")


# --- F6 : capitulation ----------------------------------------------------------------------------------------

def record(funding_rates, oi_now, oi_old):
    ms = DAY.timestamp() * 1000
    funding = [[ms - (k + 1) * 8 * 3600 * 1000, str(r), "1"] for k, r in enumerate(funding_rates)]
    oi = [[ms - 80 * 3600 * 1000, str(oi_old), "0"], [ms - 72 * 3600 * 1000, str(oi_old), "0"],
          [ms - 3600 * 1000, str(oi_now), "0"], [ms, str(oi_now), "0"]]
    return {"funding": funding, "open_interest": oi}


def closes(last, past):
    return pd.Series({DAY - pd.Timedelta(days=1): last, DAY - pd.Timedelta(days=4): past})


def test_capitulation_needs_all_three_conditions():
    hit = f6.indicators(record([-0.0001, -0.0002, -0.0001], 85.0, 100.0), closes(90.0, 100.0), DAY)
    assert hit["triggered"] and hit["funding_24h"] == pytest.approx(-0.00015, abs=1e-6)      # 2 règlements dans (D−24 h, D]
    assert hit["oi_change_3d"] == pytest.approx(-0.15) and hit["price_change_3d"] == pytest.approx(-0.10)
    assert not f6.indicators(record([0.0003, -0.0002, -0.0001], 85.0, 100.0), closes(90.0, 100.0), DAY)["triggered"]  # financement ≥ 0
    assert not f6.indicators(record([-0.0001] * 3, 86.0, 100.0), closes(90.0, 100.0), DAY)["triggered"]           # OI −14 %
    assert not f6.indicators(record([-0.0001] * 3, 85.0, 100.0), closes(91.0, 100.0), DAY)["triggered"]           # prix −9 %
    missing = f6.indicators({"funding": [], "open_interest": []}, closes(90.0, 100.0), DAY)
    assert missing["funding_24h"] is None and missing["oi_change_3d"] is None and not missing["triggered"]
    days = f6.placebo_days("x")
    assert len(set(days)) == 20 and min(days) >= 1 and max(days) <= 90 and days == f6.placebo_days("x")


def test_f6_poll_records_checks_events_and_cooldown(settings, monkeypatch):
    start = registry.start(settings, f6.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f6.TEST_ID)
    records = {"BTCUSDT": record([-0.0001] * 3, 85.0, 100.0), "ETHUSDT": record([0.0001] * 3, 85.0, 100.0)}
    monkeypatch.setattr(f6, "derivatives_records", lambda settings_, day: records)
    monkeypatch.setattr(f6, "daily_closes", lambda settings_, symbol, until: closes(90.0, 100.0))
    now = datetime(2026, 10, 10, 0, 20, tzinfo=UTC)
    assert f6.poll(settings, journal, start, now=now) == {"checks": 2, "events": 1, "decisions": 1}
    assert f6.poll(settings, journal, start, now=now) == {"checks": 0, "events": 0, "decisions": 0}
    event = next(journal.entries({f6.EVENT}))["data"]
    assert event["symbol"] == "BTCUSDT" and event["status"] == f6.DECIDED and len(event["placebo_entries"]) == 20
    later = datetime(2026, 10, 13, 0, 20, tzinfo=UTC)                                     # 3 jours après : délai
    monkeypatch.setattr(f6, "indicators", lambda record_, closes_, day: {"funding_24h": -1e-4, "funding_rates": 3, "oi_change_3d": -0.2,
                                                                          "price_change_3d": -0.2, "triggered": True})
    assert f6.poll(settings, journal, start, now=later) == {"checks": 2, "events": 2, "decisions": 1}   # ETH joué, BTC en délai
    out = f6.stats(journal, start, now=later)
    assert out["by_status"] == {f6.DECIDED: 2, f6.COOLING: 1} and out["pending"] == 2 and set(out["verdicts"].values()) == {f6.RUNNING}
    assert journal.verify()["ok"]


# --- F7 : listings --------------------------------------------------------------------------------------------

def notices():
    return [{"id": 6635, "title": "돌핀(POD) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", "listed_at": "2026-10-10T13:37:21+09:00"},
            {"id": 6636, "title": "샌드박스(SAND) 거래 유의 종목 지정 해제 안내", "listed_at": "2026-10-10T16:00:00+09:00"},
            {"id": 6637, "title": "알파, 베타(ALPHA, BETA) 신규 거래지원 안내", "listed_at": "2026-10-10T14:00:00+09:00"}]


def test_upbit_and_coinbase_parsing():
    found = f7.upbit_listings(notices())
    assert [(f["id"], f["assets"]) for f in found] == [("upbit:6635", ["POD"]), ("upbit:6637", ["ALPHA", "BETA"])]
    assert found[0]["announced_at"] == "2026-10-10T04:37:21+00:00"
    products = [{"base_currency": "btc", "status": "online"}, {"base_currency": "NEW", "status": "online"}, {"base_currency": "OLD", "status": "delisted"}]
    assert f7.coinbase_bases(products) == {"BTC", "NEW"}


def fake_exchanges(*, notices_now, products_now):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api-manager.upbit.com":
            return httpx.Response(200, json={"success": True, "data": {"notices": notices_now()}})
        assert request.url.host == "api.exchange.coinbase.com"
        return httpx.Response(200, json=products_now())
    return PublicSources(transport=httpx.MockTransport(handler))


def test_f7_poll_detects_listings_once_and_counts_unplayable(settings):
    start = registry.start(settings, f7.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f7.TEST_ID)
    products = [{"base_currency": "BTC", "status": "online"}]
    client = fake_exchanges(notices_now=notices, products_now=lambda: products)
    now = datetime(2026, 10, 10, 5, 0, tzinfo=UTC)
    assert f7.poll(settings, journal, start, now=now, client=client) == {"listings": 2, "decisions": 1}
    listings = [e["data"] for e in journal.entries({f7.LISTING})]
    assert listings[0]["status"] == f7.DECIDED and listings[0]["symbols"] == ["PODUSDT"] and listings[0]["latency_min"] == pytest.approx(22.65)
    assert listings[1]["status"] == f7.COUNTED and listings[1]["assets"] == ["ALPHA", "BETA"]
    assert f7.poll(settings, journal, start, now=now, client=client) == {"listings": 0, "decisions": 0}
    products.append({"base_currency": "SOL", "status": "online"})                              # nouveau produit Coinbase
    assert f7.poll(settings, journal, start, now=datetime(2026, 10, 11, 5, tzinfo=UTC), client=client) == {"listings": 1, "decisions": 1}
    coinbase = [e["data"] for e in journal.entries({f7.LISTING})][-1]
    assert coinbase["source"] == "coinbase" and coinbase["symbols"] == ["SOLUSDT"] and coinbase["latency_min"] is None
    out = f7.stats(journal, start, now=now)
    assert out["decisions"] == 2 and out["counted"] == 1 and out["by_source"] == {"upbit": 2, "coinbase": 1} and out["pending"] == 2


class FakeRest:
    def get_json(self, path, params):
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min")
        price = 100.0 if params["symbol"] == "BTCUSDT" else 10.0 * (1 + 0.001 * (when - DAY).total_seconds() / 3600)
        return [[int(when.timestamp() * 1000), str(price), str(price), str(price), str(price)]]


def test_f7_resolution_measures_absolute_and_relative_returns(settings):
    start = registry.start(settings, f7.TEST, now=datetime(2026, 10, 5, 9, tzinfo=UTC), allow_dirty=True, halal=HALAL)
    journal = registry.journal_for(settings, f7.TEST_ID)
    client = fake_exchanges(notices_now=notices, products_now=lambda: [])
    f7.poll(settings, journal, start, now=datetime(2026, 10, 10, 5, tzinfo=UTC), client=client)
    assert f7.resolve(settings, journal, now=datetime(2026, 10, 12, tzinfo=UTC), rest=FakeRest()) == {}
    assert f7.resolve(settings, journal, now=datetime(2026, 10, 17, 5, 5, tzinfo=UTC), rest=FakeRest()) == {"RESOLU": 1}
    result = next(journal.entries({f7.RESOLUTION}))["data"]
    central = result["results"][CENTRAL]["7j"]
    assert central["r"] > 0 and central["btc_r"] < 0 and central["relative_r"] == pytest.approx(central["r"] - central["btc_r"])
    out = f7.stats(journal, start, now=datetime(2026, 10, 17, 6, tzinfo=UTC))
    assert out["scenarios"][CENTRAL]["24h"]["n"] == 1 and out["pending"] == 0 and journal.verify()["ok"]


# --- F8 : news --------------------------------------------------------------------------------------------------

def test_news_classification_rules():
    universe = ["BTC", "ETH", "SOL", "LINK"]
    assert f8.classify("Solana (SOL) wallet drained in $5M exploit", universe) == {"status": f8.APPLIED, "asset": "SOL", "term": "drained", "theft": True}
    assert f8.classify("SEC lawsuit targets Chainlink over token sales", universe)["status"] == f8.AMBIGUOUS       # actif après le terme
    assert f8.classify("Chainlink faces SEC lawsuit over token sales", universe)["asset"] == "LINK"
    protected = f8.classify("Bitcoin worth $100M stolen from exchange", universe)
    assert protected["status"] == f8.AMBIGUOUS and "BTC ou ETH" in protected["reason"]
    assert f8.classify("Bitcoin exchange files for bankruptcy", universe)["asset"] == "BTC"                     # terme hors vol : appliqué
    assert f8.classify("Solana and Chainlink hacked", universe)["reason"] == "plusieurs actifs cités"
    assert f8.classify("Market rallies as prices climb", universe) == {"status": None}
    assert f8.classify("Faillite de la plateforme Solana Labs annoncée", universe)["status"] == f8.AMBIGUOUS       # actif après le terme
    assert f8.classify("Solana : piratage de 5 M$", universe)["asset"] == "SOL"
    masked = f8.masked_until([{"status": f8.APPLIED, "asset": "SOL", "day": "2026-10-10"}], pd.Timestamp("2026-10-16", tz="UTC"))
    assert masked == {"SOL": "2026-10-17"} and f8.masked_until([{"status": f8.APPLIED, "asset": "SOL", "day": "2026-10-10"}], pd.Timestamp("2026-10-17", tz="UTC")) == {}


class FakeNews:
    def __init__(self, items):
        self.items = items

    def recent(self, since, asset=None, limit=200):
        return [i for i in self.items if pd.Timestamp(i["first_seen_at"]) >= pd.Timestamp(since)]


def test_f8_replays_f5_with_news_cuts(settings):
    now0 = datetime(2026, 10, 5, 9, tzinfo=UTC)
    f5_start = registry.start(settings, f5.TEST, now=now0, allow_dirty=True, halal=HALAL)
    start = registry.start(settings, f8.TEST, now=now0, allow_dirty=True, halal=HALAL)
    f5_journal, journal = registry.journal_for(settings, f5.TEST_ID), registry.journal_for(settings, f8.TEST_ID)
    prices = {"BTCUSDT": 100.0, "ETHUSDT": 10.0, "SOLUSDT": 1.0}
    f5_journal.append(f5.DECISION, {"week": "2026-10-12", "basket": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "votes": {}, "sigma": {},
                                    "weights": {"BTCUSDT": 0.2, "SOLUSDT": 0.2}}, now=datetime(2026, 10, 12, 0, 20, tzinfo=UTC))
    ledgers = {s: {v: f5.new_ledger() for v in f5.VARIANTS} for s in f5.SCENARIOS}
    f5_journal.append(f5.VALUATION, {"day": "2026-10-12", "prices": prices, "week_decision": "2026-10-12", "ledgers": ledgers,
                                     "values": {s: {v: 1.0 for v in f5.VARIANTS} for s in f5.SCENARIOS}}, now=datetime(2026, 10, 12, 1, 15, tzinfo=UTC))
    news = FakeNews([{"item_id": "n1", "title": "Solana (SOL) bridge exploited for $10M", "first_seen_at": "2026-10-12T00:30:00+00:00", "source_id": "x"},
                     {"item_id": "n2", "title": "Bitcoin stolen in exchange hack", "first_seen_at": "2026-10-12T00:40:00+00:00", "source_id": "x"}])
    out = f8.poll(settings, journal, start, now=datetime(2026, 10, 12, 1, 30, tzinfo=UTC), store=news, f5_journal=f5_journal)
    assert out["valued"] == 1 and out["applied"] == 1 and out["ambiguous"] == 1
    valuation = next(journal.entries({f8.VALUATION}))["data"]
    assert valuation["masked"] == {"SOL": "2026-10-19"}
    a, a_news = valuation["ledgers"][CENTRAL]["A"], valuation["ledgers"][CENTRAL]["A_NEWS"]
    assert "SOLUSDT" in a["holdings"] and "SOLUSDT" not in a_news["holdings"] and "BTCUSDT" in a_news["holdings"]
    assert f8.poll(settings, journal, start, now=datetime(2026, 10, 12, 2, tzinfo=UTC), store=news, f5_journal=f5_journal)["valued"] == 0
    stats = f8.stats(journal, start, now=datetime(2026, 10, 12, 2, tzinfo=UTC))
    assert stats["applied"] == 1 and stats["by_asset"] == {"SOL": 1} and stats["masked_days"] == 1 and stats["verdict"] == f8.RUNNING
    assert ADVERSE in stats["scenarios"] and f5_start["run_id"] != start["run_id"]


@pytest.mark.parametrize(("module", "values"), [
    (f6, ("0,2 événement", "−15 %", "−10 %", "7 jours", "20 achats", "10 événements", "graine 20261006")),
    (f7, ("신규 거래지원", "api.exchange.coinbase.com", "24 h", "graine 20261007", "relativement à BTC")),
    (f8, ("**jamais**", "BTC ni à ETH", "7 jours", "AMBIGU", "SUIVI_TERMINE", "A_NEWS")),
])
def test_preregistrations_are_complete(module, values):
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), module.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in values:
        assert value in text, value
    assert json.dumps(module.TEST.params, default=str)
