"""Historique long (research/long_history.py) : magasin séparé, début à la cotation. Aucun réseau."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from crypto_signal_intelligence.data.http import PublicHttpClient
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.features.loader import MissingData
from crypto_signal_intelligence.research import long_history as lh

from .conftest import canonical

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def rest_listing(first_ms: dict[str, int]) -> PublicHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbol"]
        if symbol not in first_ms:
            return httpx.Response(400, json={"msg": "Invalid symbol."})
        return httpx.Response(200, json=[[first_ms[symbol], "1", "1", "1", "1", "0", 0, "0", 0, "0", "0", "0"]])
    return PublicHttpClient.rest("https://r.invalid", transport=httpx.MockTransport(handler), retries=1,
                                 sleep=lambda _s: None)


def test_long_store_is_separate_and_starts_at_the_listing_month(settings):
    calls = []

    def fake_download(target, symbol, timeframe, **kwargs):
        calls.append((symbol, timeframe, target.data.history_start, target.data_dir))
        CandleStore(target.data_dir).save(canonical(48, "1h", symbol=symbol, start="2017-08-17", seed=1), symbol, "1h")
        report = SimpleNamespace(rows=48, first_open_time="2017-08-17T00:00:00+00:00",
                                 last_open_time="2017-08-18T23:00:00+00:00", gaps=[], missing_bars=0)
        return SimpleNamespace(report=report, archives_ingested=1, archives_missing=[], quarantined=0)

    first = {"BTCUSDT": int(datetime(2017, 8, 17, tzinfo=UTC).timestamp() * 1000)}
    rows = lh.download_long(settings, ["BTCUSDT", "ABSENTEUSDT"], now=NOW, workers=2, rest_client=rest_listing(first),
                            downloader=fake_download)
    ok, failed = rows
    assert ok["listed"] == "2017-08-17" and ok["rows"] == 48 and "error" in failed       # un échec n'arrête rien
    assert calls == [("BTCUSDT", "1h", date(2017, 8, 1), settings.root / "long_history" / "data")]
    assert settings.data.history_start == date(2021, 1, 1)                               # réglages d'origine intacts
    assert len(lh.load_long(settings, "BTCUSDT")) == 48
    assert CandleStore(settings.data_dir).load("BTCUSDT", "1h").empty                    # magasin courant non touché
    with pytest.raises(MissingData):
        lh.load_long(settings, "ETHUSDT")
