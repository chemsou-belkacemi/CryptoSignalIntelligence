"""Test en direct F3_STABLECOINS (forward/f3.py) : règles d'événement à la main, placebos, rendements, lecture des
chaînes sur des réponses simulées, bout en bout avec des bougies simulées, seuil de décision. SYNTHÉTIQUE : rien ici
ne dit ce que donnera le marché."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.forward import f3, registry, runner
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL
from crypto_signal_intelligence.forward.halal import HalalList
from crypto_signal_intelligence.forward.sources import PublicSources

from .conftest import PROJECT

BTC_ONLY = HalalList(("BTCUSDT",), {}, "a" * 64, "b" * 64)
T0 = pd.Timestamp("2026-10-03 10:00", tz="UTC")
CIRCLE = "0x5b6122c109b78c6755486966148c1d70a50a47d7"
CCTP = "0xc4922d64a24675e16e1586e3e3aa56c06fabe907"


def mint(key, coin, amount, at, counted=True):
    return {"key": key, "stablecoin": coin, "amount_usd": amount, "emitted_at": at.isoformat(), "counted": counted}


# --- Règles pures ------------------------------------------------------------------------------------------

def test_qualifying_moments_cumulate_one_hour_per_stablecoin():
    mints = [mint("a", "USDC", 60e6, T0), mint("b", "USDC", 50e6, T0 + pd.Timedelta(minutes=30)),
             mint("c", "USDT", 90e6, T0 + pd.Timedelta(minutes=40)), mint("d", "USDC", 30e6, T0 + pd.Timedelta(hours=2))]
    moments = f3.qualifying_moments(mints)
    assert [(m["stablecoin"], m["amount_usd"]) for m in moments] == [("USDC", 110e6)]
    assert moments[0]["mints"] == ["a", "b"] and moments[0]["at"] == (T0 + pd.Timedelta(minutes=30)).isoformat()
    assert f3.qualifying_moments([mint("x", "USDT", 1e9, T0)])[0]["amount_usd"] == 1e9
    assert f3.qualifying_moments([mint("x", "USDC", 1e9, T0, counted=False)]) == []
    exact = [mint("a", "USDC", 60e6, T0), mint("b", "USDC", 40e6, T0 + pd.Timedelta(hours=1))]   # fenêtre ouverte à gauche
    assert f3.qualifying_moments(exact) == []


def test_events_are_separated_by_24h_across_stablecoins():
    mints = [mint("a", "USDT", 1e9, T0), mint("b", "USDT", 1e9, T0 + pd.Timedelta(minutes=50)),
             mint("c", "USDC", 150e6, T0 + pd.Timedelta(hours=20)), mint("d", "USDC", 150e6, T0 + pd.Timedelta(hours=45))]
    events = f3.events_from(mints)
    assert [(e["stablecoin"], e["moments"]) for e in events] == [("USDT", 3), ("USDC", 1)]
    assert events[0]["last_at"] == (T0 + pd.Timedelta(hours=20)).isoformat()
    assert events[0]["event_id"] == f3.events_from(mints[:1])[0]["event_id"]      # identité stable quand des créations s'ajoutent
    assert f3.events_from([]) == []


def test_placebo_days_are_reproducible_distinct_and_within_30_days():
    days = f3.placebo_days("abc")
    assert days == f3.placebo_days("abc") and len(set(days)) == f3.PLACEBOS and min(days) >= 1 and max(days) <= 30
    assert days != f3.placebo_days("abd") and days == sorted(days)


def test_net_return_by_hand():
    expected = 101 * (1 - 0.0002) * (1 - 0.00075) / (100 * (1 + 0.0002) * (1 + 0.00075)) - 1
    assert f3.net_return(100.0, 101.0, "BTCUSDT", CENTRAL) == pytest.approx(expected)
    flat_central, flat_adverse = f3.net_return(100.0, 100.0, "BTCUSDT", CENTRAL), f3.net_return(100.0, 100.0, "BTCUSDT", ADVERSE)
    assert flat_adverse < flat_central < 0


@pytest.mark.parametrize(("central", "adverse", "expected"), [
    ({"n": 12, "excess_ci": (0.001, 0.02)}, {"excess_ci": (0.0005, 0.01)}, f3.POSITIVE),
    ({"n": 12, "excess_ci": (-0.02, -0.001)}, {"excess_ci": (-0.03, -0.002)}, f3.NEGATIVE),
    ({"n": 12, "excess_ci": (0.001, 0.02)}, {"excess_ci": (-0.001, 0.01)}, f3.NO_DIFFERENCE),
    ({"n": 9, "excess_ci": (0.001, 0.02)}, {"excess_ci": (0.001, 0.01)}, f3.INSUFFICIENT),
    ({"n": 12, "excess_ci": None}, {"excess_ci": (0.001, 0.01)}, f3.INSUFFICIENT),
])
def test_f3_decision_threshold(central, adverse, expected):
    rows = {CENTRAL: {"24h": central}, ADVERSE: {"24h": adverse}}
    assert f3.verdict(rows, ended=True)["24h"] == expected
    assert f3.verdict(rows, ended=False)["24h"] == f3.RUNNING


# --- Lecture des chaînes --------------------------------------------------------------------------------

def tron_event(amount_usdt, at, tx="8330cbe9"):
    return {"block_number": 85550940, "block_timestamp": int(at.timestamp() * 1000), "event_index": 1,
            "event_name": "Issue", "result": {"amount": str(int(amount_usdt * 1_000_000))}, "transaction_id": tx}


def eth_log(amount, block, minter=None, tx="0xaa", index=3):
    topics = [f3.TOPIC_MINT, "0x" + "0" * 24 + minter[2:], "0x" + "0" * 64] if minter else [f3.TOPIC_ISSUE]
    return {"blockNumber": hex(block), "data": hex(int(amount * 1_000_000)), "transactionHash": tx,
            "logIndex": hex(index), "topics": topics}


def test_parse_tron_and_ethereum_payloads():
    now = T0 + pd.Timedelta(minutes=12)
    tron = f3.parse_tron([tron_event(1_000_000_000, T0)], detected_at=now)
    assert tron[0]["amount_usd"] == 1e9 and tron[0]["key"] == "tron:8330cbe9:1" and tron[0]["emitted_at"] == T0.isoformat()
    assert tron[0]["counted"] is True and tron[0]["stablecoin"] == "USDT"
    stamps = {700: T0}
    usdc = f3.parse_eth([eth_log(120e6, 700, CIRCLE), eth_log(200e6, 700, CCTP, tx="0xbb")], stablecoin="USDC",
                        timestamps=stamps, detected_at=now)
    assert [m["counted"] for m in usdc] == [True, False] and usdc[1]["reason"].startswith("pont CCTP")
    assert usdc[0]["minter"] == CIRCLE and usdc[0]["key"] == "eth:0xaa:3"
    usdt = f3.parse_eth([eth_log(50e6, 700)], stablecoin="USDT", timestamps=stamps, detected_at=now)
    assert usdt[0]["minter"] is None and usdt[0]["amount_usd"] == 50e6
    assert f3.parse_tron([{"result": {}}], detected_at=now) == []              # ligne illisible : ignorée


def fake_chains(*, now, tron=(), usdc=(), usdt=(), tron_status=200, block_time=None):
    """Transport HTTP simulé : TronGrid et nœud Ethereum, hors réseau."""
    latest = 0x100000
    stamp = block_time or (now - pd.Timedelta(minutes=10))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.trongrid.io":
            assert request.url.path.endswith("/events") and "min_block_timestamp" in str(request.url)
            return httpx.Response(tron_status, json={"data": list(tron)})
        assert request.url.host == "ethereum-rpc.publicnode.com"
        body = json.loads(request.content)
        if body["method"] == "eth_blockNumber":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(latest)})
        if body["method"] == "eth_getLogs":
            spec = body["params"][0]
            assert int(spec["toBlock"], 16) - int(spec["fromBlock"], 16) == f3.ETH_BLOCKS
            logs = list(usdc) if spec["address"] == f3.ETH_USDC else list(usdt)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": logs})
        if body["method"] == "eth_getBlockByNumber":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"timestamp": hex(int(stamp.timestamp()))}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "méthode inconnue"}})

    return PublicSources(transport=httpx.MockTransport(handler))


class FakeRest:
    """Bougies 1 min simulées : prix déterministe en fonction de l'heure ; `delay` décale la première bougie trouvée."""

    def __init__(self, delay_minutes=0, fail=False):
        self.delay, self.fail, self.calls = delay_minutes, fail, 0

    @staticmethod
    def price(when: pd.Timestamp) -> float:
        return 100.0 + (when - T0).total_seconds() / 3600 * 0.1          # +0,1 par heure

    def get_json(self, path, params):
        from crypto_signal_intelligence.data.http import HttpError
        self.calls += 1
        if self.fail:
            raise HttpError("panne simulée")
        assert path == "/api/v3/klines" and params["symbol"] == "BTCUSDT" and params["interval"] == "1m"
        when = pd.Timestamp(params["startTime"], unit="ms", tz="UTC").ceil("min") + pd.Timedelta(minutes=self.delay)
        price = self.price(when)
        return [[int(when.timestamp() * 1000), str(price), str(price + 0.5), str(price - 0.5), str(price + 0.1)]]


def started(settings, when):
    return registry.start(settings, f3.TEST, now=when, allow_dirty=True, halal=BTC_ONLY)


def test_f3_end_to_end(settings):
    start_at = datetime(2026, 10, 3, 9, tzinfo=UTC)
    start = started(settings, start_at)
    journal = registry.journal_for(settings, f3.TEST_ID)
    now = T0 + pd.Timedelta(minutes=20)
    chains = fake_chains(now=now, tron=[tron_event(1_000_000_000, T0)],
                         usdc=[eth_log(120e6, 0xFFFFF, CIRCLE), eth_log(200e6, 0xFFFFF, CCTP, tx="0xbb")])
    counts = f3.poll(settings, journal, start, now=now, client=chains)
    assert counts == {"mints": 3, "events": 1, "decisions": 1, "errors": 0}
    assert f3.poll(settings, journal, start, now=now + pd.Timedelta(minutes=15), client=chains) == {"mints": 0, "events": 0, "decisions": 0, "errors": 0}
    event = next(journal.entries({f3.EVENT}))["data"]
    assert event["status"] == f3.DECIDED and event["stablecoin"] == "USDT" and event["amount_usd"] == 1e9
    assert event["entry_at"] == (now + f3.LATENCY).isoformat() and event["latency_min"] == 20.0
    assert len(event["placebo_entries"]) == 20 and event["placebo_days"] == f3.placebo_days(event["event_id"])
    mints = [e["data"] for e in journal.entries({f3.MINT})]
    assert [m["counted"] for m in mints] == [True, True, False]
    # L'USDC (120 M$ à 10 min d'intervalle) prolonge l'événement de l'USDT : pas de second événement.
    assert sum(1 for _ in journal.entries({f3.EVENT})) == 1

    rest = FakeRest()
    assert f3.resolve(settings, journal, now=now + pd.Timedelta(hours=10), rest=rest) == {} and rest.calls == 0
    later = pd.Timestamp(event["entry_at"]) + pd.Timedelta(hours=72, minutes=3)
    assert f3.resolve(settings, journal, now=later, rest=rest) == {"RESOLU": 1}
    assert f3.resolve(settings, journal, now=later, rest=rest) == {}
    result = next(journal.entries({f3.RESOLUTION}))["data"]
    entry = pd.Timestamp(event["entry_at"]).ceil("min")
    by_hand = f3.net_return(FakeRest.price(entry), FakeRest.price(entry + pd.Timedelta(hours=24)), "BTCUSDT", CENTRAL)
    assert result["results"][CENTRAL]["24h"]["event_r"] == pytest.approx(by_hand, abs=1e-6)
    placebo = [f3.net_return(FakeRest.price(pd.Timestamp(t).ceil("min")),
                             FakeRest.price(pd.Timestamp(t).ceil("min") + pd.Timedelta(hours=72)), "BTCUSDT", ADVERSE)
               for t in event["placebo_entries"]]
    assert result["results"][ADVERSE]["72h"]["placebo_mean"] == pytest.approx(sum(placebo) / 20, abs=1e-6)
    assert result["results"][CENTRAL]["24h"]["excess"] == pytest.approx(
        result["results"][CENTRAL]["24h"]["event_r"] - result["results"][CENTRAL]["24h"]["placebo_mean"], abs=1e-6)
    out = f3.stats(journal, start, now=later)
    assert out["decisions"] == 1 and out["pending"] == 0 and out["gaps"] == 0 and out["mints_ignored"] == 1
    assert out["scenarios"][CENTRAL]["24h"]["n"] == 1 and out["by_stablecoin"] == {"USDT": 1, "USDC": 0}
    assert set(out["verdicts"].values()) == {f3.RUNNING} and f3.finalize(journal, start, now=later) is None
    assert journal.verify()["ok"]


def test_late_detection_and_mints_before_start_are_never_played(settings):
    start_at = datetime(2026, 10, 3, 9, 30, tzinfo=UTC)
    start = started(settings, start_at)
    journal = registry.journal_for(settings, f3.TEST_ID)
    before = T0 - pd.Timedelta(hours=1)                        # avant le démarrage : jamais inscrite
    late = T0 + pd.Timedelta(hours=3)
    chains = fake_chains(now=late, tron=[tron_event(1e9, before, tx="old"), tron_event(1e9, T0, tx="new")])
    assert f3.poll(settings, journal, start, now=late, client=chains) == {"mints": 1, "events": 1, "decisions": 0, "errors": 0}
    event = next(journal.entries({f3.EVENT}))["data"]
    assert event["status"] == f3.LATE and "entry_at" not in event and event["latency_min"] == 180.0
    out = f3.stats(journal, start, now=late)
    assert out["by_status"][f3.LATE] == 1 and out["decisions"] == 0


def test_source_errors_are_journaled_once_per_six_hours(settings):
    start = started(settings, datetime(2026, 10, 3, 9, tzinfo=UTC))
    journal = registry.journal_for(settings, f3.TEST_ID)
    now = T0 + pd.Timedelta(minutes=5)
    chains = fake_chains(now=now, tron_status=500)
    assert f3.poll(settings, journal, start, now=now, client=chains)["errors"] == 1
    assert f3.poll(settings, journal, start, now=now + pd.Timedelta(hours=1), client=chains)["errors"] == 0
    assert f3.poll(settings, journal, start, now=now + pd.Timedelta(hours=7), client=chains)["errors"] == 1
    errors = [e["data"] for e in journal.entries({f3.SOURCE_ERROR})]
    assert len(errors) == 2 and errors[0]["source"] == "tron" and "HTTP 500" in errors[0]["error"]


def test_resolution_waits_for_the_source_then_marks_a_gap(settings):
    start = started(settings, datetime(2026, 10, 3, 9, tzinfo=UTC))
    journal = registry.journal_for(settings, f3.TEST_ID)
    now = T0 + pd.Timedelta(minutes=10)
    f3.poll(settings, journal, start, now=now, client=fake_chains(now=now, tron=[tron_event(1e9, T0)]))
    entry = now + f3.LATENCY
    due = entry + pd.Timedelta(hours=72, minutes=5)
    assert f3.resolve(settings, journal, now=due, rest=FakeRest(fail=True)) == {}        # panne : on réessaie
    assert f3.resolve(settings, journal, now=due, rest=FakeRest(delay_minutes=20)) == {"TROU": 1}   # bougie trop tardive
    result = next(journal.entries({f3.RESOLUTION}))["data"]
    assert result["gap"] is True and result["results"] is None
    assert f3.stats(journal, start, now=due)["gaps"] == 1


def test_runner_polls_running_tests_every_cycle(settings, monkeypatch):
    start = started(settings, datetime(2026, 10, 3, 9, tzinfo=UTC))
    calls = []

    class Fake:
        TEST = f3.TEST

        @staticmethod
        def poll(settings_, journal, start_, *, now):
            calls.append(now)
            return {"ok": True}

        @staticmethod
        def record_decisions(settings_, journal, start_, *, now):
            return {}

        @staticmethod
        def resolve(settings_, journal, *, now):
            return {}

        @staticmethod
        def finalize(journal, start_, *, now):
            return None

    monkeypatch.setattr(runner, "TESTS", ((f3.TEST, Fake),))
    monkeypatch.setattr(runner, "_LAST", {})
    monkeypatch.setattr(runner, "check_frozen", lambda *a, **k: None)
    first = datetime(2026, 10, 3, 9, 15, tzinfo=UTC)
    assert runner.poll_tests(settings, now=first) == {f3.TEST_ID: {"ok": True}}
    assert runner.daily(settings, now=first, force=True) is not None          # passage complet (force) : pas de poll seul
    assert runner.daily(settings, now=first + timedelta(minutes=5)) is None  # trop tôt pour tout
    out = runner.daily(settings, now=first + timedelta(minutes=15))
    assert out == {"poll": {f3.TEST_ID: {"ok": True}}} and len(calls) == 2
    assert start["started_at"]


def test_f3_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f3.TEST_ID)
    assert text is not None and registry.missing_fields(text) == []
    for value in ("100 M$", "24 h", "5 minutes", "120 minutes", "20 achats", "10 événements", "1 − 0,05/2",
                  "graine 20261004", "blocs de 3 jours", "84 jours", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"):
        assert value in text, value
    assert f3.TEST.params["seed"] == 20261004 and f3.TEST.params["placebos"] == 20 and f3.MIN_EVENTS == 10
