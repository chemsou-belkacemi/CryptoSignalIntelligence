"""Étude historique pré-inscrite « peur sur les options » (research/options_peur.py, docs/OPTIONS_PEUR.md) : règle à la
main, téléchargement borné au 2025-06-29 (client factice), mesure, décision et garde-fou, gardes de l'exécution unique,
marché synthétique du contrôle sous H0. Aucun réseau, aucune donnée réelle. Le contrôle complet est marqué `slow`."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.forward.costs import costs_for
from crypto_signal_intelligence.research import options_peur as op

D, H = pd.Timedelta(days=1), pd.Timedelta(hours=1)
T0 = pd.Timestamp("2022-01-01", tz="UTC")


def test_events_by_hand_strictly_previous_reference_and_five_day_gap():
    values = [50.0 + (k % 3) for k in range(30)] + [60.0, 70.0, 52.0, 52.0, 52.0, 80.0, 90.0]
    dvol = pd.Series(values, index=pd.date_range(T0, periods=len(values), freq="D"))
    found = op.events(dvol)
    days = [e["day"] for e in found]
    assert days == [T0 + 30 * D, T0 + 35 * D]                 # 70 (31e jour) à moins de 5 jours du 1er : écarté
    first = found[0]
    ref = np.quantile(values[:30], 0.95)
    assert first["threshold"] == pytest.approx(ref) and first["known_at"] == T0 + 31 * D
    assert first["entry_at"] == T0 + 31 * D + H
    short = pd.Series([50.0] * 20 + [99.0], index=pd.date_range(T0, periods=21, freq="D"))
    assert op.events(short) == []                              # référence de moins de 24 jours : rien


def test_download_is_capped_before_the_reserved_period_and_follows_pages():
    class Http:
        def __init__(self):
            self.calls = []

        def get_json(self, url, params):
            self.calls.append(params)
            assert url == op.URL and params["resolution"] == "1D" and params["currency"] == "BTC"
            end = pd.Timestamp(params["end_timestamp"], unit="ms", tz="UTC")
            if len(self.calls) == 1:
                days = pd.date_range(end.floor("D") - 2 * D, end.floor("D"), freq="D")
                return {"result": {"data": [[int(d.timestamp() * 1000), 1, 1, 1, 40.0 + i] for i, d in enumerate(days)],
                                   "continuation": int((end.floor("D") - 2 * D).timestamp() * 1000)}}
            days = pd.date_range(pd.Timestamp(params["start_timestamp"], unit="ms", tz="UTC"), end - D, freq="D")
            return {"result": {"data": [[int(d.timestamp() * 1000), 1, 1, 1, 30.0] for d in days], "continuation": None}}

    with pytest.raises(ValueError):
        op.download_dvol(Http(), last=pd.Timestamp("2025-06-30", tz="UTC"))
    http = Http()
    out = op.download_dvol(http, first=pd.Timestamp("2025-06-20", tz="UTC"))
    assert out.index.max() == op.LAST_DAY and out.index.min() == pd.Timestamp("2025-06-20", tz="UTC") and len(http.calls) == 2
    assert pd.Timestamp(http.calls[0]["end_timestamp"], unit="ms", tz="UTC") < pd.Timestamp("2025-06-30", tz="UTC")


def test_measure_by_hand_and_never_past_the_development_end():
    entry = T0 + 31 * D + H
    closes = pd.Series({entry: 100.0, entry + op.HORIZONS["24h"]: 103.0, entry + op.HORIZONS["72h"]: 99.0,
                        entry + op.HORIZONS["7j"]: 110.0})
    out = op.measure(closes, {"entry_at": entry})
    c = costs_for("BTCUSDT", "central")
    assert out["24h"]["brut"] == pytest.approx(0.03)
    assert out["24h"]["central"] == pytest.approx(103 * (1 - c.market) * (1 - c.fee) / (100 * (1 + c.market) * (1 + c.fee)) - 1)
    assert op.measure(closes, {"entry_at": entry}, shift=0.005)["24h"]["brut"] == pytest.approx(103 * 1.005 / 100 - 1)
    late = pd.Timestamp("2025-06-24 01:00", tz="UTC")              # sortie à 7 jours le 2025-07-01 : refusé
    assert op.measure(pd.Series({late + k * H: 100.0 for k in range(200)}), {"entry_at": late}) is None
    del closes[entry + op.HORIZONS["72h"]]
    assert op.measure(closes, {"entry_at": entry}) is None


def rows(values: list[float], years: list[int]) -> list[dict]:
    out = []
    for i, (v, y) in enumerate(zip(values, years, strict=True)):
        at = pd.Timestamp(f"{y}-01-01", tz="UTC") + (i * 6) * D
        out.append({"entry_at": at.isoformat(), "returns": {"24h": {"brut": v, "central": v - 0.0019, "defavorable": v - 0.0024}}})
    return out


def test_decision_piste_guard_and_insufficient():
    rng = np.random.default_rng(3)
    years = [2021 + (i % 4) for i in range(60)]
    good = op.decide(rows(list(0.03 + rng.normal(0, 0.005, 60)), years), samples=2000)
    assert good["decision"] == op.PISTE and good["guard"]
    lucky = [0.08 if y == 2022 else rng.normal(0, 0.01) for y in years]
    assert op.decide(rows(lucky, years), samples=2000)["decision"] == op.RIEN       # tient seulement par la meilleure année
    assert op.decide(rows([0.03] * 29, years[:29]), samples=2000)["decision"] == op.INSUFFISANT


def test_execution_refuses_without_inscribed_control_and_review(settings, monkeypatch):
    class Never:
        def get_json(self, *a, **k):
            raise AssertionError("aucun appel réseau avant les gardes")

    with pytest.raises(op.NotReady):
        op.run(settings, now=datetime(2026, 10, 10, tzinfo=UTC), http=Never())
    path = settings.reports_dir / "h0.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"study": "OPTIONS_PEUR", "executable": true, "status": "PASSE"}', encoding="utf-8")
    import hashlib
    monkeypatch.setattr(op, "CONTROLE_H0", f"{path}#{hashlib.sha256(path.read_bytes()).hexdigest()}")
    with pytest.raises(op.NotReady, match="relecture"):
        op.run(settings, now=datetime(2026, 10, 10, tzinfo=UTC), http=Never())
    path.write_text('{"study": "OPTIONS_PEUR", "executable": false, "status": "INSTRUMENT_TROP_FAIBLE"}', encoding="utf-8")
    with pytest.raises(op.NotReady, match="modifié"):
        op.run(settings, now=datetime(2026, 10, 10, tzinfo=UTC), http=Never())
    monkeypatch.setattr(op, "CONTROLE_H0", f"{path}#{hashlib.sha256(path.read_bytes()).hexdigest()}")
    with pytest.raises(op.NotReady, match="INSTRUMENT_TROP_FAIBLE"):
        op.run(settings, now=datetime(2026, 10, 10, tzinfo=UTC), http=Never())


def test_synthetic_market_is_deterministic_and_martingale_shaped():
    a = op.h0_market(np.random.default_rng(7))
    b = op.h0_market(np.random.default_rng(7))
    pd.testing.assert_series_equal(a[0], b[0])
    pd.testing.assert_series_equal(a[1], b[1])
    dvol, closes = a
    assert dvol.index[0] == op.DVOL_START and dvol.index[-1] == op.LAST_DAY
    r = closes.pct_change().dropna()
    assert abs(r.mean()) < 4 * r.std() / np.sqrt(len(r))
    assert len(op.events(dvol)) > 30                                     # assez d'événements pour juger le contrôle


@pytest.mark.slow
def test_controle_h0_options_peur_once():
    """Passage UNIQUE du contrôle sous H0 (200 marchés synthétiques). Fichier écrit dans `CSI_OPTIONS_PEUR_H0_OUT`
    (sinon un dossier temporaire) ; chiffres inscrits dans docs/OPTIONS_PEUR.md § 6."""
    target = os.environ.get("CSI_OPTIONS_PEUR_H0_OUT")
    out = Path(target) if target else None
    report = op.controle_h0(out_path=out)
    print(report)
    assert report["replicates"] == 200 and report["status"] in ("PASSE", "ECHEC", "INSTRUMENT_TROP_FAIBLE")
    assert report["false_piste"] <= op.H0_MAX_FALSE
