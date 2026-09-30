import hashlib
import io
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.data import archives, rest
from crypto_signal_intelligence.data.http import PublicHttpClient
from crypto_signal_intelligence.data.pipeline import download
from crypto_signal_intelligence.data.quality import assess, clean
from crypto_signal_intelligence.data.schema import normalize
from crypto_signal_intelligence.data.store import CandleStore, merge
from crypto_signal_intelligence.data.timeunits import TimestampUnitError, archive_unit, to_utc

from .conftest import canonical, raw_klines

NOW = datetime(2030, 1, 1, tzinfo=UTC)


# --- unités d'horodatage -------------------------------------------------------

def test_units_are_declared_per_source_and_checked():
    assert archive_unit(date(2024, 12, 31)) == "ms" and archive_unit(date(2025, 1, 1)) == "us"
    ms = 1735689600000  # 2025-01-01T00:00:00Z
    assert to_utc([ms], "ms")[0] == to_utc([ms * 1000], "us")[0] == pd.Timestamp("2025-01-01", tz="UTC")
    with pytest.raises(TimestampUnitError):
        to_utc([ms * 1000], "ms")   # des microsecondes lues comme millisecondes
    with pytest.raises(TimestampUnitError):
        to_utc([ms], "us")


def test_microsecond_archive_normalizes_to_same_candles():
    a = normalize(raw_klines(10, unit="ms"), unit="ms", symbol="X", timeframe="15m", source="s", source_version="v",
                  now=NOW, latency_seconds=2)
    b = normalize(raw_klines(10, unit="us"), unit="us", symbol="X", timeframe="15m", source="s", source_version="v",
                  now=NOW, latency_seconds=2)
    pd.testing.assert_series_equal(a["open_time"], b["open_time"])
    assert (a["available_at"] - a["open_time"] == pd.Timedelta(minutes=15, seconds=2)).all()


# --- archives ------------------------------------------------------------------

def _zip(frame: pd.DataFrame, name: str, header: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, frame.to_csv(index=False, header=header))
    return buffer.getvalue()


def test_zip_parsing_with_and_without_header():
    frame = raw_klines(5)
    for header in (False, True):
        parsed = archives.read_zip_csv(_zip(frame, "x.csv", header))
        assert len(parsed) == 5 and parsed["open_time"].iloc[0] == frame["open_time"].iloc[0]


def test_checksum_is_enforced():
    content = b"abc"
    good = hashlib.sha256(content).hexdigest()
    assert archives.parse_checksum(f"{good}  F.zip\n", "F.zip") == good
    with pytest.raises(archives.ChecksumError):
        archives.verify(b"tampered", good, "F.zip")
    with pytest.raises(archives.ChecksumError):
        archives.parse_checksum(f"{good}  OTHER.zip", "F.zip")


def test_archive_plan_monthly_then_daily():
    refs = archives.plan("BTCUSDT", "1h", date(2025, 11, 15), date(2026, 1, 3))
    assert [r.label for r in refs] == ["2025-11", "2025-12", "2026-01-01", "2026-01-02"]
    assert refs[0].path == "/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2025-11.zip"
    assert refs[-1].unit == "us"


# --- HTTP : aucune route d'ordre -----------------------------------------------

def test_http_whitelist_refuses_private_endpoints():
    client = PublicHttpClient.rest("https://example.invalid",
                                   transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    for path in ("/api/v3/order", "/api/v3/order/test", "/api/v3/account", "/sapi/v1/asset", "/api/v3/openOrders"):
        with pytest.raises(PermissionError):
            client.get(path)
    archive = PublicHttpClient.archives("https://example.invalid",
                                        transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(PermissionError):
        archive.get("/data/spot/../../api/v3/order")


def test_http_retries_with_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3"}) if len(calls) == 1 else httpx.Response(200, json=[])

    client = PublicHttpClient.rest("https://example.invalid", transport=httpx.MockTransport(handler),
                                   sleep=sleeps.append)
    assert client.get_json("/api/v3/klines") == [] and sleeps == [3.0]


def test_source_code_contains_no_order_or_signing_route():
    source = Path(__file__).resolve().parents[1] / "src"
    forbidden = re.compile(r"/api/v3/(order|account|myTrades|openOrders)|X-MBX-APIKEY|hmac|signature=", re.I)
    offenders = [str(p) for p in source.rglob("*.py") if forbidden.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# --- qualité, quarantaine, fusion ------------------------------------------------

def test_quality_quarantines_invalid_rows_and_reports_gaps_without_filling():
    frame = canonical(50)
    frame.loc[5, "high"] = frame.loc[5, "low"] - 1                       # OHLC incohérent
    frame.loc[6, "taker_buy_base_volume"] = frame.loc[6, "base_volume"] * 2
    frame.loc[7, "open_time"] += pd.Timedelta(minutes=1)                 # non aligné
    frame.loc[8, "is_closed"] = False                                    # bougie ouverte
    frame = pd.concat([frame, frame.iloc[[10]]])                         # doublon
    frame = frame.drop(index=[20, 21, 22])                               # trou de 3 bougies
    good, bad = clean(frame, "15m")
    assert set(bad["reason"]) == {"INVALID_OHLC", "TAKER_INCONSISTENT", "MISALIGNED", "OPEN_CANDLE", "DUPLICATE"}
    report = assess(good, "ETHUSDT", "15m")
    assert report.missing_bars >= 3 and len(good) == 50 - 3 - 4  # aucun comblement
    assert good["open_time"].is_monotonic_increasing


def test_merge_prefers_verified_archive_and_counts_differences():
    archive = canonical(10).assign(source="BINANCE_PUBLIC_DATA")
    rest_rows = canonical(10).assign(source="BINANCE_REST_KLINES")
    rest_rows.loc[3, "close"] *= 1.01
    merged, changed = merge(rest_rows, archive)
    assert changed == 1 and (merged["source"] == "BINANCE_PUBLIC_DATA").all()
    again, changed_again = merge(merged, archive)
    pd.testing.assert_frame_equal(again, merged)
    assert changed_again == 0


def test_download_pipeline_offline(settings, monkeypatch):
    """Archive mensuelle vérifiée + complément REST (dont une bougie ouverte ignorée)."""
    monkeypatch.setattr(archives, "plan", lambda s, tf, start, today: [archives.ArchiveRef(s, tf, "monthly",
                                                                                           date(2024, 1, 1))])
    month = raw_klines(24 * 31, "1h", start="2024-01-01")
    content = _zip(month, "BTCUSDT-1h-2024-01.csv")
    digest = hashlib.sha256(content).hexdigest()

    def archive_handler(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{digest}  BTCUSDT-1h-2024-01.zip")
        return httpx.Response(200, content=content)

    recent = raw_klines(24 * 31 + 5, "1h", start="2024-01-01").iloc[-8:]
    now = datetime(2024, 2, 1, 4, 30, tzinfo=UTC)  # la dernière bougie (04:00) est ouverte

    def rest_handler(request):
        return httpx.Response(200, json=recent.astype(object).values.tolist())

    summary = download(settings, "BTCUSDT", "1h", now=now,
                       archive_client=PublicHttpClient.archives("https://a.invalid",
                                                                transport=httpx.MockTransport(archive_handler)),
                       rest_client=PublicHttpClient.rest("https://r.invalid",
                                                         transport=httpx.MockTransport(rest_handler)))
    stored = CandleStore(settings.data_dir).load("BTCUSDT", "1h")
    assert summary.archives_ingested == 1 and summary.quarantined == 0
    assert len(stored) == 24 * 31 + 4 and stored["is_closed"].all()
    assert summary.report.ok
    again = download(settings, "BTCUSDT", "1h", now=now,
                     archive_client=PublicHttpClient.archives("https://a.invalid",
                                                              transport=httpx.MockTransport(archive_handler)),
                     rest_client=PublicHttpClient.rest("https://r.invalid",
                                                       transport=httpx.MockTransport(rest_handler)))
    assert again.archives_skipped == 1 and len(CandleStore(settings.data_dir).load("BTCUSDT", "1h")) == len(stored)
    assert rest.KLINES_PAGE_LIMIT == 1000 and np.isfinite(stored["close"]).all()


def test_last_open_time_reads_only_the_time_column(settings):
    store = CandleStore(settings.data_dir)
    assert store.last_open_time("ETHUSDT", "15m") is None
    frame = canonical(50, "15m", symbol="ETHUSDT")
    store.save(frame, "ETHUSDT", "15m")
    assert store.last_open_time("ETHUSDT", "15m") == frame["open_time"].max()


def test_parallel_rest_refreshes_merge_one_series_at_a_time(settings, monkeypatch):
    """Surveillance : réseau en parallèle, mais fusion + écriture une série à la fois (pic mémoire borné)."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    store = CandleStore(settings.data_dir)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    for symbol in symbols:
        store.save(canonical(48, "1h", symbol=symbol, start="2024-01-01"), symbol, "1h")
    recent = raw_klines(52, "1h", start="2024-01-01").iloc[-6:]
    state = {"active": 0, "max": 0, "network": 0, "network_max": 0}
    lock = threading.Lock()
    real_upsert = CandleStore.upsert_tail

    def slow_upsert(self, incoming, symbol, timeframe):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        try:
            return real_upsert(self, incoming, symbol, timeframe)
        finally:
            with lock:
                state["active"] -= 1

    def rest_handler(request):
        with lock:
            state["network"] += 1
            state["network_max"] = max(state["network_max"], state["network"])
        time.sleep(0.05)
        with lock:
            state["network"] -= 1
        return httpx.Response(200, json=recent.astype(object).values.tolist())

    monkeypatch.setattr(CandleStore, "upsert_tail", slow_upsert)
    client = PublicHttpClient.rest("https://r.invalid", transport=httpx.MockTransport(rest_handler))
    now = datetime(2024, 1, 3, 5, 30, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda s: download(settings, s, "1h", now=now, rest_client=client, rest_only=True), symbols))
    assert state["max"] == 1                      # jamais deux fusions en même temps
    assert state["network_max"] > 1               # le réseau, lui, reste parallèle
    assert all(store.last_open_time(s, "1h") > pd.Timestamp("2024-01-02 23:00", tz="UTC") for s in symbols)


def test_tail_merge_is_identical_to_a_full_merge():
    """La fusion limitée au chevauchement donne EXACTEMENT le résultat d'une fusion complète."""
    from crypto_signal_intelligence.data.store import _merge_full
    rng = np.random.default_rng(3)
    existing = canonical(500, "15m").assign(source="BINANCE_REST_KLINES")
    for overlap in (0, 1, 7, 40):
        incoming = canonical(520, "15m").iloc[500 - overlap:].assign(source="BINANCE_REST_KLINES")
        incoming = incoming.copy()
        incoming.loc[incoming.index[: max(overlap // 2, 0)], "close"] *= 1 + rng.normal(0, 0.01)
        fast, changed_fast = merge(existing, incoming)
        full, changed_full = _merge_full(existing, incoming)
        pd.testing.assert_frame_equal(fast, full)
        assert changed_fast == changed_full
    archive = canonical(30, "15m").iloc[10:20].assign(source="BINANCE_PUBLIC_DATA")   # révision au milieu
    fast, _ = merge(existing, archive)
    full, _ = _merge_full(existing, archive)
    pd.testing.assert_frame_equal(fast, full)


def test_upsert_tail_equals_a_full_merge_and_save(tmp_path):
    """Chemin rapide de la surveillance : fichier relu identique à load → merge → save."""
    fast, slow = CandleStore(tmp_path / "fast"), CandleStore(tmp_path / "slow")
    history = canonical(400, "15m", symbol="ETHUSDT").assign(source="BINANCE_PUBLIC_DATA")
    fast.save(history, "ETHUSDT", "15m")
    slow.save(history, "ETHUSDT", "15m")
    batches = [canonical(410, "15m", symbol="ETHUSDT").iloc[395:].assign(source="BINANCE_REST_KLINES"),   # chevauche
               canonical(430, "15m", symbol="ETHUSDT").iloc[410:].assign(source="BINANCE_REST_KLINES")]   # nouvelles
    revised = canonical(430, "15m", symbol="ETHUSDT").iloc[405:408].assign(source="BINANCE_REST_KLINES")
    revised.loc[revised.index[1], "close"] *= 1.002
    batches.append(revised)
    for batch in batches:
        last, changed = fast.upsert_tail(batch, "ETHUSDT", "15m")
        merged, changed_slow = merge(slow.load("ETHUSDT", "15m"), batch)
        slow.save(merged, "ETHUSDT", "15m")
        assert changed == changed_slow and last == merged["open_time"].max()
        pd.testing.assert_frame_equal(fast.load("ETHUSDT", "15m"), slow.load("ETHUSDT", "15m"))
    fresh = CandleStore(tmp_path / "new")                                   # série encore inexistante
    last, _ = fresh.upsert_tail(batches[1], "SOLUSDT", "15m")
    assert last == batches[1]["open_time"].max() and len(fresh.load("SOLUSDT", "15m")) == len(batches[1])


def test_tail_load_keeps_the_latest_bars_only(settings):
    from crypto_signal_intelligence.features.loader import load_candles
    store = CandleStore(settings.data_dir)
    frame = canonical(5000, "15m", symbol="ETHUSDT")
    store.save(frame, "ETHUSDT", "15m")
    tail = load_candles(settings, "ETHUSDT", "15m", tail=300)
    assert len(tail) == 300 and tail["open_time"].iat[-1] == frame["open_time"].iat[-1]
    pd.testing.assert_frame_equal(tail, load_candles(settings, "ETHUSDT", "15m").tail(300).reset_index(drop=True))
