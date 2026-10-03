"""Conseil de risque à 24 h en shadow (risk/advice.py) : lecture seule du journal de F12, ampleur = √variance prévue,
taille relative à risque égal bornée, prévision périmée refusée, journal du conseil sans doublon, route de l'API.
Données SYNTHÉTIQUES."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_intelligence.api.server import CsiApi
from crypto_signal_intelligence.forward.journal import Journal
from crypto_signal_intelligence.risk import advice as ra

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
ORIGIN = datetime(2026, 10, 3, tzinfo=UTC)


def forecast(variances: dict[str, float], origin: datetime = ORIGIN) -> dict:
    return {"day": origin.date().isoformat(), "origin": origin.isoformat(),
            "pairs": {s: {"24h": {"H1_HAR_PROFILE": v, "R0_RECENT_24H": 2 * v}, "1d": {"M5_LGBM_POOLED": v}} for s, v in variances.items()}}


def test_move_size_and_bounds():
    out = ra.advice(forecast({"A": 0.0004, "B": 0.0016, "C": 0.0001, "D": 0.04}), now=NOW)
    assert out["available"] and out["model"] == "H1_HAR_PROFILE"
    moves = {s: math.sqrt(v) for s, v in {"A": 0.0004, "B": 0.0016, "C": 0.0001, "D": 0.04}.items()}
    median = sorted(moves.values())[1] / 2 + sorted(moves.values())[2] / 2
    assert out["pairs"]["B"]["move_24h_pct"] == pytest.approx(4.0)
    assert out["pairs"]["A"]["relative_size"] == pytest.approx(round(median / moves["A"], 2))
    assert out["pairs"]["C"]["relative_size"] == 2.0                    # bornée en haut
    assert out["pairs"]["D"]["relative_size"] == 0.25                   # bornée en bas
    assert list(out["pairs"]) == ["D", "B", "A", "C"]                   # du plus agité au plus calme


def test_missing_stale_or_empty_forecasts_give_no_advice():
    assert ra.advice(None, now=NOW)["available"] is False
    stale = ra.advice(forecast({"A": 0.0004}, origin=ORIGIN - timedelta(days=2)), now=NOW)
    assert stale["available"] is False and "périmée" in stale["reason"]
    assert ra.advice(forecast({"A": -1.0, "B": float("nan")}), now=NOW)["available"] is False


def write_f12(settings, data: dict) -> None:
    Journal(ra.journal_path(settings)).append("PREVISION", data, now=NOW)


def test_reads_f12_journal_and_records_once(settings):
    assert ra.current(settings, now=NOW)["available"] is False
    write_f12(settings, forecast({"ETHUSDT": 0.0009, "BTCUSDT": 0.0004}))
    out = ra.current(settings, now=NOW)
    assert out["available"] and set(out["pairs"]) == {"ETHUSDT", "BTCUSDT"}
    assert ra.record_day(settings, now=NOW) == {"origin": ORIGIN.isoformat(), "pairs": 2}
    assert ra.record_day(settings, now=NOW + timedelta(hours=1)) is None          # même prévision : pas de doublon
    lines = (settings.root / "state" / "risk_shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["pairs"]["ETHUSDT"]["move_24h_pct"] == 3.0


def test_api_route(settings):
    write_f12(settings, forecast({"ETHUSDT": 0.0009}))
    out = CsiApi(settings, now=lambda: NOW).dispatch("GET", "/risk", {}, None)
    assert out["available"] and out["pairs"]["ETHUSDT"]["move_24h_pct"] == 3.0 and "shadow" in out["note"]
