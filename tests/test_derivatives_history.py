"""Historique du marché à terme (docs/DERIVATIVES.md) : archives SIMULÉES, aucun réseau."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime

import httpx
import pandas as pd
import pytest

from crypto_signal_intelligence.data.http import PublicHttpClient
from crypto_signal_intelligence.derivatives import history as h

MS = 1_000


def zipped(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name.replace(".zip", ".csv"), text)
    return buffer.getvalue()


def funding_csv(month: date, header: bool = True) -> str:
    start = int(pd.Timestamp(month, tz="UTC").timestamp() * 1000)
    rows = [f"{start + 8 * 3_600_000 * i + 2},8,0.0001" for i in range(3 * 28)]
    return ("calc_time,funding_interval_hours,last_funding_rate\n" if header else "") + "\n".join(rows)


def premium_csv(day0: pd.Timestamp, hours: int, unit_us: bool = False) -> str:
    factor = 1000 if unit_us else 1
    rows = []
    for i in range(hours):
        t = int((day0 + pd.Timedelta(hours=i)).timestamp() * 1000) * factor
        rows.append(f"{t},0.0001,0.0002,-0.0001,0.00005,0,{t + 3_599_999 * factor},0,720,0,0,0")
    return "\n".join(rows)


def metrics_csv(day: date) -> str:
    head = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
            "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n")
    stamps = pd.date_range(pd.Timestamp(day), periods=288, freq="5min")
    return head + "\n".join(f"{t:%Y-%m-%d %H:%M:%S},BTCUSDT,100,1000000,1.2,1.5,1.1,0.9" for t in stamps)


def test_plan_follows_the_granularity_of_each_dataset():
    today = date(2024, 3, 10)
    funding = h.plan("funding", "BTCUSDT", date(2024, 1, 1), today)
    assert [r.label for r in funding] == ["2024-01", "2024-02"]           # mensuel seulement
    premium = h.plan("premium", "BTCUSDT", date(2024, 1, 1), today)
    assert [r.label for r in premium][:2] == ["2024-01", "2024-02"] and premium[-1].label == "2024-03-09"
    metrics = h.plan("metrics", "BTCUSDT", date(2024, 3, 1), today)
    assert len(metrics) == 9 and metrics[0].path == (
        "/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2024-03-01.zip")
    assert funding[0].path == "/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip"
    assert premium[0].path == "/data/futures/um/monthly/premiumIndexKlines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"


def test_parse_dates_each_row_with_its_declared_availability(settings):
    funding = h.parse("funding", zipped("f.zip", funding_csv(date(2024, 1, 1))), settings)
    assert funding["available_at"].iloc[0] - funding["time"].iloc[0] == pd.Timedelta(seconds=60)
    assert (h.parse("funding", zipped("f.zip", funding_csv(date(2024, 1, 1), header=False)), settings)["rate"]
            == 0.0001).all()                                                 # archive sans en-tête
    day0 = pd.Timestamp("2025-02-01", tz="UTC")
    in_ms = h.parse("premium", zipped("p.zip", premium_csv(day0, 3)), settings)
    in_us = h.parse("premium", zipped("p.zip", premium_csv(day0, 3, unit_us=True)), settings)
    assert in_ms["time"].iloc[0] == day0 and in_us["time"].equals(in_ms["time"])   # ms et µs reconnus
    assert in_ms["available_at"].iloc[0] == day0 + pd.Timedelta(hours=1, seconds=2)  # fin de l'heure + 2 s
    metrics = h.parse("metrics", zipped("m.zip", metrics_csv(date(2024, 1, 1))), settings)
    assert set(h.METRICS_COLUMNS.values()) <= set(metrics.columns) and len(metrics) == 288
    assert metrics["available_at"].iloc[0] - metrics["time"].iloc[0] == pd.Timedelta(seconds=602)
    with pytest.raises(ValueError, match="échelle"):
        h.epoch_to_utc(["1700000000"])                                       # secondes : refusées


class FakeArchives:
    """Serveur d'archives simulé : fichiers et sommes de contrôle, archives volontairement absentes ou fausses."""

    def __init__(self, files: dict[str, bytes], corrupt: frozenset[str] = frozenset()):
        self.files, self.corrupt, self.requests = files, corrupt, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        name = path.removesuffix(".CHECKSUM")
        if name not in self.files:
            return httpx.Response(404)
        content = self.files[name]
        if path.endswith(".CHECKSUM"):
            digest = hashlib.sha256(b"autre" if name in self.corrupt else content).hexdigest()
            return httpx.Response(200, text=f"{digest}  {name.rsplit('/', 1)[1]}")
        return httpx.Response(200, content=content)


def test_download_verifies_stores_and_resumes_without_refetching(settings):
    settings.data.history_start = date(2024, 1, 1)
    funding = h.DATASETS["funding"]
    premium = h.DATASETS["premium"]
    files = {funding.path("BTCUSDT", "monthly", label): zipped(f"{label}.zip", funding_csv(date(2024, m, 1)))
             for m, label in ((1, "2024-01"), (2, "2024-02"))}
    files[premium.path("BTCUSDT", "monthly", "2024-01")] = zipped("p.zip", premium_csv(
        pd.Timestamp("2024-01-01", tz="UTC"), 31 * 24))
    for day in (1, 2):                    # février sans archive mensuelle : repli sur les archives journalières
        label = f"2024-02-0{day}"
        files[premium.path("BTCUSDT", "daily", label)] = zipped("p.zip", premium_csv(
            pd.Timestamp(label, tz="UTC"), 24))
    server = FakeArchives(files, corrupt=frozenset({premium.path("BTCUSDT", "daily", "2024-02-02")}))
    client = PublicHttpClient.futures_archives("https://a.invalid", transport=httpx.MockTransport(server),
                                               retries=1, sleep=lambda _s: None)
    now = datetime(2024, 3, 2, 12, tzinfo=UTC)
    out = h.download(settings, datasets=["funding", "premium"], symbols=["BTCUSDT"], now=now, client=client)
    by = {s.dataset: s for s in out}
    assert by["funding"].ingested == 2 and by["funding"].quality["rows"] == 168
    assert by["premium"].ingested == 2 and len(by["premium"].rejected) == 1      # 2 février : SHA-256 faux, refusé
    assert len(by["premium"].missing) == 27 + 1                                  # 3-29 février et 1er mars absents
    stored = h.DerivativesStore(settings.data_dir).load("premium", "BTCUSDT")
    assert len(stored) == 31 * 24 + 24 and stored["time"].is_monotonic_increasing
    assert stored["time"].max() < pd.Timestamp("2024-02-02", tz="UTC")           # rien de l'archive refusée
    assert by["premium"].quality["gaps"] == 0
    before = len(server.requests)
    again = {s.dataset: s for s in h.download(settings, datasets=["funding", "premium"], symbols=["BTCUSDT"], now=now,
                                              client=client)}
    assert again["funding"].ingested == 0 and again["funding"].skipped == 2
    archives_fetched = [p for p in server.requests[before:] if p.endswith(".zip")]
    assert archives_fetched == [premium.path("BTCUSDT", "daily", "2024-02-02")]  # seule l'archive refusée est relue
    with pytest.raises(ValueError, match="inconnu"):
        h.download(settings, datasets=["options"], symbols=["BTCUSDT"], now=now, client=client)


def test_quality_reports_gaps_without_filling_them():
    times = pd.Series(pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")).drop(index=[4, 5])
    frame = pd.DataFrame({"time": times.to_numpy(), "available_at": times.to_numpy(), "close": 1.0})
    report = h.quality("premium", frame)
    assert report["rows"] == 8 and report["gaps"] == 1 and report["largest_gap_hours"] == 3.0
