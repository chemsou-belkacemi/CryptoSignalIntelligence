"""Marché à terme (docs/DERIVATIVES.md) : positionnement du moment, données publiques SIMULÉES (aucun réseau)."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import numpy as np
import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi
from crypto_signal_intelligence.data.http import FUTURES_REST_ALLOWED_PATHS, PublicHttpClient
from crypto_signal_intelligence.derivatives.live import HOUR_MS, rank, snapshot

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
HOUR_START = NOW_MS - NOW_MS % HOUR_MS                       # début de l'heure en cours (période non terminée)


def market(failing: frozenset[str] = frozenset()):
    """Réponses publiques simulées, et la liste des chemins demandés."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path in failing:
            return httpx.Response(500, text="panne simulée")
        if path == "/fapi/v1/fundingRate":
            last = NOW_MS - 90 * 60_000                       # dernier règlement 1 h 30 avant maintenant
            times = [last - 8 * HOUR_MS * i for i in range(269, -1, -1)]
            rates = ["0.00010000"] * 261 + ["0.00030000"] * 9  # 3 derniers jours : 0,03 % par règlement
            return httpx.Response(200, json=[{"symbol": "ETHUSDT", "fundingTime": t, "fundingRate": r,
                                              "markPrice": "100"} for t, r in zip(times, rates, strict=True)])
        if path == "/fapi/v1/premiumIndex":
            return httpx.Response(200, json={"symbol": "ETHUSDT", "markPrice": "101", "indexPrice": "100",
                                             "lastFundingRate": "0.00020000", "nextFundingTime": NOW_MS + HOUR_MS})
        if path == "/fapi/v1/premiumIndexKlines":
            opens = [HOUR_START - HOUR_MS * i for i in range(719, -1, -1)]   # la dernière est en cours
            closes = [0.001] * 695 + [0.002] * 24 + [0.9]                  # 0,9 : période non terminée
            return httpx.Response(200, json=[[o, "0", "0", "0", str(c), "0", o + HOUR_MS - 1, "0", 720, "0", "0", "0"]
                                             for o, c in zip(opens, closes, strict=True)])
        if path == "/futures/data/openInterestHist":
            stamps = [HOUR_START - HOUR_MS * i for i in range(499, -1, -1)]
            return httpx.Response(200, json=[{"symbol": "ETHUSDT", "sumOpenInterest": str(1e6 * 1.001 ** i),
                                              "sumOpenInterestValue": str(1e9 * 1.002 ** i), "timestamp": s}
                                             for i, s in enumerate(stamps)])
        if path in ("/futures/data/globalLongShortAccountRatio", "/futures/data/topLongShortPositionRatio"):
            stamps = [HOUR_START - HOUR_MS * i for i in range(499, -1, -1)]
            ratios = list(np.linspace(1.0, 2.0, 499)) + [1.5]
            if path.endswith("topLongShortPositionRatio"):                      # gros comptes : série distincte
                ratios = list(np.linspace(2.0, 3.9, 499)) + [4.0]           # dernière valeur : la plus haute
            return httpx.Response(200, json=[{"symbol": "ETHUSDT", "longShortRatio": str(r),
                                              "longAccount": str(r / (1 + r)), "shortAccount": str(1 / (1 + r)),
                                              "timestamp": s} for s, r in zip(stamps, ratios, strict=True)])
        if path == "/futures/data/takerlongshortRatio":
            stamps = [HOUR_START - HOUR_MS * i for i in range(499, -1, -1)]   # la dernière est en cours
            rows = [{"buySellRatio": "1", "buyVol": "100", "sellVol": "100", "timestamp": s} for s in stamps]
            rows[-1]["buyVol"] = "1000000"                                    # ne doit pas compter
            return httpx.Response(200, json=rows)
        return httpx.Response(404)

    client = PublicHttpClient.futures_rest("https://fapi.invalid", transport=httpx.MockTransport(handler),
                                           retries=1, sleep=lambda _s: None)
    return client, calls


def test_rank_is_the_share_of_strictly_lower_values():
    assert rank(range(1, 11), 5.5) == 0.5 and rank(range(1, 11), 1) == 0.0 and rank(range(1, 11), 11) == 1.0
    assert rank(range(1, 10), 5) is None                          # moins de 10 valeurs : pas de rang
    assert rank([np.nan] * 5 + list(range(10)), 4.5) == 0.5      # valeurs manquantes ignorées
    assert rank(range(20), np.nan) is None


def test_snapshot_describes_each_section_from_public_market_data_only():
    client, calls = market()
    out = snapshot(client, "ETHUSDT", now=NOW)
    assert out["available"] and set(calls) <= FUTURES_REST_ALLOWED_PATHS
    funding = out["funding"]
    assert funding["interval_hours"] == 8 and funding["last_rate"] == pytest.approx(0.0003)
    assert funding["mean_3d"] == pytest.approx(0.0003) and funding["annualized_3d"] == pytest.approx(0.0003 * 3 * 365)
    assert funding["rank_mean_3d"] > 0.95 and funding["estimated_next_rate"] == pytest.approx(0.0002)
    premium = out["premium"]
    assert premium["now"] == pytest.approx(0.01) and premium["mean_24h"] == pytest.approx(0.002)
    oi = out["open_interest"]
    # variations en NOMBRE de contrats (sans l'effet du prix) ; valeur affichée en dollars
    assert oi["change_24h"] == pytest.approx(1.001 ** 24 - 1) and oi["change_7d"] == pytest.approx(1.001 ** 168 - 1)
    assert oi["value_usd"] == pytest.approx(1e9 * 1.002 ** 499, rel=1e-6)
    assert out["accounts"]["ratio"] == pytest.approx(1.5) and out["accounts"]["rank"] == pytest.approx(0.5, abs=0.01)
    assert out["accounts"]["long_share"] == pytest.approx(0.6)
    top = out["top_positions"]
    assert top["ratio"] == pytest.approx(4.0) and top["long_share"] == pytest.approx(0.8) and top["rank"] == 1.0
    assert set(out["definitions"]) == {"financement", "prime", "interet_ouvert", "comptes", "gros_comptes",
                                       "agressifs", "rang"}
    assert out["taker"]["ratio_24h"] == pytest.approx(1.0)       # l'heure en cours (achats énormes) est exclue
    assert "pas un signal" in out["note"] and set(out["definitions"]) >= {"financement", "prime", "rang"}


def test_a_failing_endpoint_only_blanks_its_own_section():
    client, _ = market(failing=frozenset({"/futures/data/openInterestHist"}))
    out = snapshot(client, "ETHUSDT", now=NOW)
    assert "unavailable" in out["open_interest"] and out["available"]
    assert "unavailable" not in out["funding"] and "unavailable" not in out["taker"]
    client, _ = market(failing=FUTURES_REST_ALLOWED_PATHS)
    assert not snapshot(client, "ETHUSDT", now=NOW)["available"]


def test_api_route_checks_the_pair_and_rereads_at_most_every_few_minutes(settings):
    clock = {"now": NOW}
    api = CsiApi(settings, now=lambda: clock["now"])
    api.futures_client, calls = market()
    first = api.dispatch("GET", "/derivatives", {"symbol": ["ethusdt"]}, None)
    assert first["symbol"] == "ETHUSDT" and not first["cached"] and first["available"]
    count = len(calls)
    again = api.dispatch("GET", "/derivatives", {"symbol": ["ETHUSDT"]}, None)
    assert again["cached"] and len(calls) == count                # aucune nouvelle requête
    clock["now"] = NOW.replace(minute=40)
    assert not api.dispatch("GET", "/derivatives", {"symbol": ["ETHUSDT"]}, None)["cached"]
    for bad in ("DOGEUSDT", "ETH/USDT", ""):
        with pytest.raises(ApiError):
            api.dispatch("GET", "/derivatives", {"symbol": [bad]}, None)
