"""Bougies de 1 seconde pour l'exécution simulée (data/seconds.py) : conversion, pagination, cache des fenêtres
écoulées seulement, et règles prudentes (traverser la limite, trou d'ouverture). Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

import pandas as pd

from crypto_signal_intelligence.data import seconds as sec

T0 = pd.Timestamp("2026-09-01 12:00:00", tz="UTC")


def bars(rows: list[tuple[float, float, float, float]], start: pd.Timestamp = T0) -> pd.DataFrame:
    raw = [[int((start + pd.Timedelta(seconds=i)).timestamp() * 1000), str(o), str(h), str(lo), str(c), "1"]
           for i, (o, h, lo, c) in enumerate(rows)]
    return sec.to_frame(raw)


def test_conversion_and_order():
    frame = sec.to_frame([[int(T0.timestamp() * 1000) + 1000, "2", "3", "1", "2", "5"],
                          [int(T0.timestamp() * 1000), "1", "1", "1", "1", "0"]])
    assert list(frame["open_time"]) == [T0, T0 + pd.Timedelta(seconds=1)] and frame["high"].iloc[1] == 3.0


class Pager:
    def __init__(self, total: int):
        self.total, self.calls = total, []

    def get_json(self, path, params):
        assert path == "/api/v3/klines" and params["interval"] == "1s"
        self.calls.append(params["startTime"])
        base = int(T0.timestamp() * 1000)
        start = (params["startTime"] - base) // 1000
        stop = min(self.total, start + params["limit"], (params["endTime"] - base) // 1000 + 1)
        return [[int(T0.timestamp() * 1000) + i * 1000, "1", "1", "1", "1", "1"] for i in range(start, stop)]


def test_fetch_paginates_until_the_window_ends():
    pager = Pager(2500)
    frame = sec.fetch(pager, "ETHUSDT", T0, T0 + pd.Timedelta(seconds=2500))
    assert len(frame) == 2500 and len(pager.calls) == 3


def test_only_finished_windows_are_cached(settings):
    pager = Pager(10**6)
    past = sec.window(settings, "ETHUSDT", T0, before=pd.Timedelta(seconds=10), after=pd.Timedelta(seconds=20), client=pager)
    assert len(past) == 30 and len(list((sec.seconds_dir(settings) / "ETHUSDT").glob("*.parquet"))) == 1
    again = sec.window(settings, "ETHUSDT", T0, before=pd.Timedelta(seconds=10), after=pd.Timedelta(seconds=20), client=None)
    assert len(again) == 30                                                     # relu du cache, sans réseau
    future = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=1)
    sec.window(settings, "BTCUSDT", future, client=Pager(0))
    assert not (sec.seconds_dir(settings) / "BTCUSDT").exists()                 # fenêtre pas écoulée : pas gardée


def test_market_fill_is_the_next_open():
    frame = bars([(100, 101, 99, 100), (102, 103, 101, 102)])
    assert sec.market_fill(frame, T0 + pd.Timedelta(milliseconds=500)) == sec.Fill(T0 + pd.Timedelta(seconds=1), 102.0)
    assert sec.market_fill(frame, T0 + pd.Timedelta(seconds=5)) is None


def test_limit_buy_needs_to_trade_through():
    touch = bars([(101, 101, 100, 100.5), (100.5, 101, 100, 100.2)])            # touche 100 sans passer dessous
    assert sec.limit_buy_fill(touch, 100.0, T0, T0 + pd.Timedelta(seconds=2)) is None
    through = bars([(101, 101, 100, 100.5), (100.5, 100.6, 99.9, 100)])
    assert sec.limit_buy_fill(through, 100.0, T0, T0 + pd.Timedelta(seconds=2)) == sec.Fill(T0 + pd.Timedelta(seconds=1), 100.0)
    gap = bars([(101, 101, 100.5, 101), (98, 99, 97, 98)])                       # ouvre sous la limite : meilleur prix
    assert sec.limit_buy_fill(gap, 100.0, T0, T0 + pd.Timedelta(seconds=2)).price == 98.0
    assert sec.limit_buy_fill(through, 100.0, T0, T0 + pd.Timedelta(seconds=1)) is None   # hors de la validité


def test_target_and_stop_with_gaps():
    frame = bars([(100, 104, 99, 103), (103, 106, 102, 105), (90, 91, 89, 90)])
    assert sec.target_fill(frame, 104.0, T0) == sec.Fill(T0 + pd.Timedelta(seconds=1), 104.0)   # 104 touché, pas dépassé
    assert sec.stop_fill(frame, 95.0, T0) == sec.Fill(T0 + pd.Timedelta(seconds=2), 90.0)       # trou : vente à l'ouverture
    assert sec.stop_fill(frame, 99.0, T0) == sec.Fill(T0, 99.0)
