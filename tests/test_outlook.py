"""Tableau de bord interactif : perspective d'une paire, simulation des stratégies sans publication,
pages servies par l'API et garde-fous HTTP. Données SYNTHÉTIQUES : on teste le code, pas un marché."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CSP, CsiApi, make_handler
from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.outlook.pair import (
    HORIZONS,
    SL,
    TIMEOUT,
    TP,
    OutlookError,
    block_days_for,
    forward_returns,
    pair_outlook,
    plan_outcomes,
)
from crypto_signal_intelligence.signals.analyze import analyze
from crypto_signal_intelligence.strategies.registry import STRATEGIES

from .conftest import canonical

NO_COSTS = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)
STEP = pd.Timedelta(minutes=15)


def bars(rows):
    """Bougies 15 min (open, high, low, close) et volatilité réalisée constante de 1 %."""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["open_time"] = pd.date_range("2024-01-01", periods=len(frame), freq="15min", tz="UTC")
    frame["realized_vol_96"] = 0.01
    return frame


FLAT = (100.0, 100.0, 100.0, 100.0)


@pytest.mark.parametrize("case", ["objectif", "meme_bougie", "ouverture_sous_stop", "temps"])
def test_plan_outcomes_follow_the_conservative_exit_rules(case):
    # H = 4 bougies : σ_H = 1 % × 2 = 2 % → stop 98, objectif 103 pour une entrée à 100.
    paths = {
        "objectif": [FLAT, (100, 100.5, 99.5, 100), (100, 103.5, 99.9, 101)],
        "meme_bougie": [FLAT, (100, 104, 97, 100)],
        "ouverture_sous_stop": [FLAT, FLAT, (97, 97.5, 96.5, 97)],
        "temps": [FLAT, (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 101)],
    }
    rows = paths[case] + [FLAT] * (8 - len(paths[case]))
    out = plan_outcomes(bars(rows), np.array([0]), 4, NO_COSTS, STEP)
    expected = {"objectif": (TP, 103.0), "meme_bougie": (SL, 98.0), "ouverture_sous_stop": (SL, 97.0),
                "temps": (TIMEOUT, 101.0)}[case]
    assert out.outcome[0] == expected[0]
    assert out.net[0] == pytest.approx(expected[1] / 100 - 1)
    assert out.r[0] == pytest.approx((expected[1] / 100 - 1) / 0.02)


def test_plan_outcomes_skip_windows_with_missing_data_or_unknown_volatility():
    frame = bars([FLAT] * 12)
    frame.loc[3, "realized_vol_96"] = np.nan
    gap = frame.drop(index=[9]).reset_index(drop=True)
    out = plan_outcomes(gap, np.arange(len(gap)), 4, NO_COSTS, STEP)
    assert 3 not in set(out.rows)                                   # volatilité inconnue
    assert not set(out.rows) & {5, 6, 7, 8}                         # fenêtre qui traverse le trou


def test_forward_returns_buy_next_open_and_net_of_costs():
    rows = [(100, 101, 99, 100), (101, 102, 100, 101), (102, 103, 101, 102), (103, 104, 102, 104), FLAT, FLAT]
    costs = CostScenario(fee_bps=10, slippage_bps=2, half_spread_bps=1)
    gross, net = forward_returns(bars(rows), 3, costs, STEP)
    assert gross[0] == pytest.approx(104 / 100 - 1)
    assert net[0] == pytest.approx(104 * (1 - 3e-4) * (1 - 1e-3) / (101 * (1 + 3e-4) * (1 + 1e-3)) - 1)
    assert np.isnan(gross[-3:]).all()
    assert block_days_for(4) == 10 and block_days_for(672) == 14


@pytest.fixture
def market(settings, monkeypatch):
    """Deux paires synthétiques de 60 jours ; « maintenant » = juste après la dernière bougie."""
    store = CandleStore(settings.data_dir)
    for i, symbol in enumerate(("ETHUSDT", "BTCUSDT")):
        store.save(canonical(96 * 60, "15m", symbol=symbol, start="2024-01-01", seed=i), symbol, "15m")
        store.save(canonical(24 * 60, "1h", symbol=symbol, start="2024-01-01", seed=i + 5), symbol, "1h")
    settings.data.symbols = ["ETHUSDT", "BTCUSDT"]
    last = pd.Timestamp("2024-01-01", tz="UTC") + (96 * 60 - 1) * STEP
    return settings, (last + STEP + timedelta(minutes=1)).to_pydatetime()


def test_pair_outlook_defines_its_numbers_and_rounds_levels_to_the_tick(market):
    settings, now = market
    result = pair_outlook(settings, "ethusdt", "24h", now=now)
    assert result["symbol"] == "ETHUSDT" and result["context"]["fresh"]
    assert [o["horizon"] for o in result["overview"]] == list(HORIZONS)
    for row in result["overview"]:
        for key in ("p_up", "p_net_positive"):
            value = row[key]["value"]
            assert value is None or 0 <= value <= 1
    plan = result["plan"]
    tick = settings.data.tick_size["ETHUSDT"]
    entry, stop, target = (Decimal(plan[k]) for k in ("entry_reference", "stop", "target"))
    assert stop < entry < target and all(v % tick == 0 for v in (entry, stop, target))
    assert plan["verdict"] in {"FAVORABLE", "DEFAVORABLE", "INDETERMINE", "INSUFFISANT"}
    assert 0 < plan["breakeven_win_rate"] < 1 and plan["rr_gross"] == 1.5
    assert abs(plan["tp_first"] + plan["sl_first"] + plan["timeout"] - 1) < 1e-3
    assert {"comparable", "p_up", "net", "plan", "ci95", "verdict"} <= set(result["definitions"])


def test_pair_outlook_flags_stale_data_and_refuses_bad_requests(market):
    settings, now = market
    stale = pair_outlook(settings, "ETHUSDT", "1h", now=now + timedelta(days=2))
    assert not stale["context"]["fresh"] and stale["plan"]["verdict"] == "DONNEES_ANCIENNES"
    with pytest.raises(OutlookError, match="horizon"):
        pair_outlook(settings, "ETHUSDT", "2h", now=now)
    with pytest.raises(OutlookError, match="hors univers"):
        pair_outlook(settings, "DOGEUSDT", "24h", now=now)


def test_strategy_simulation_never_publishes(market):
    settings, now = market
    for strategy_id in STRATEGIES:
        out = analyze(settings, "ETHUSDT", strategy_id, now=now, publish=False)
        assert out.signal_id is None and out.signal_path is None
        assert out.action == "NO_TRADE" or (out.publication_status == "SIMULATION" and out.levels)
    assert not settings.signals_db.exists() and not list(settings.root.glob("signals/**/*.txt"))


def test_analyze_pair_route_validates_simulates_and_caches(market):
    settings, now = market
    api = CsiApi(settings, now=lambda: now)
    for bad in ({}, {"symbol": "ETH/USDT"}, {"symbol": "ETHUSDT", "horizon": "2h"}, {"symbol": "x" * 30}):
        with pytest.raises(Exception) as error:
            api.dispatch("POST", "/analyze-pair", {}, bad)
        assert getattr(error.value, "status", None) == 400
    first = api.dispatch("POST", "/analyze-pair", {}, {"symbol": "ETHUSDT", "horizon": "4h"})
    assert first["cached"] is False and [s["strategy"] for s in first["strategies"]] == list(STRATEGIES)
    assert api.dispatch("POST", "/analyze-pair", {}, {"symbol": "ETHUSDT", "horizon": "4h"})["cached"] is True
    unknown = pytest.raises(Exception)
    with unknown as error:
        api.dispatch("POST", "/analyze-pair", {}, {"symbol": "DOGEUSDT", "horizon": "4h"})
    assert getattr(error.value, "status", None) == 422
    pairs = api.dispatch("GET", "/pairs", {}, None)
    assert [p["symbol"] for p in pairs["pairs"]] == ["ETHUSDT", "BTCUSDT"] and len(pairs["horizons"]) == 6
    assert api.dispatch("GET", "/models", {}, None)["models"] == []
    assert not settings.signals_db.exists()


@pytest.fixture
def page_server(settings):
    handler = make_handler(CsiApi(settings), token="jeton", hosts={"127.0.0.1", "localhost"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def fetch(url, *, host=None, token=None):
    request = urllib.request.Request(url)
    if host:
        request.add_header("Host", host)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_dashboard_pages_are_served_with_a_strict_content_policy(page_server):
    status, headers, body = fetch(f"{page_server}/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert headers["Content-Security-Policy"] == CSP and "'unsafe-inline'" not in CSP
    assert b'src="/app.js"' in body and b"<script>" not in body           # aucun script en ligne
    status, headers, script = fetch(f"{page_server}/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    assert b"innerHTML" not in script and b"eval(" not in script
    assert fetch(f"{page_server}/app.css")[0] == 200
    assert fetch(f"{page_server}/health")[0] == 401                         # les données exigent le jeton
    assert fetch(f"{page_server}/health", token="jeton")[0] == 200


def test_requests_for_a_foreign_host_name_are_refused(page_server):
    port = page_server.rsplit(":", 1)[1]
    assert fetch(f"{page_server}/", host=f"attaquant.example:{port}")[0] == 421
    assert fetch(f"{page_server}/health", host=f"attaquant.example:{port}", token="jeton")[0] == 421
    assert fetch(f"{page_server}/health", host=f"localhost:{port}", token="jeton")[0] == 200


def test_static_files_are_shipped_with_the_package():
    from pathlib import Path
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"crypto_signal_intelligence.api" = ["static/*"]' in pyproject


def test_schema_migration_tolerates_a_concurrent_connection(tmp_path):
    """Bug trouvé par le test navigateur : l'onglet Suivi lance 6 requêtes à la fois sur une base ancienne ;
    deux connexions voyaient la colonne absente et la seconde échouait (« duplicate column name »)."""
    import sqlite3

    from crypto_signal_intelligence.data.sqlite_schema import add_column
    from crypto_signal_intelligence.external.registry import ExternalSignalRegistry

    db = sqlite3.connect(tmp_path / "t.sqlite3")
    db.execute("CREATE TABLE t (a TEXT)")
    add_column(db, "t", "b", "TEXT")
    add_column(db, "t", "b", "TEXT")                       # seconde connexion en retard : sans erreur
    with pytest.raises(sqlite3.OperationalError):
        add_column(db, "absente", "c", "TEXT")             # toute autre erreur remonte
    db.close()
    old = sqlite3.connect(tmp_path / "ext.sqlite3")         # base créée avant la migration du 2026-09-30
    old.execute("""CREATE TABLE external_signals (id TEXT PRIMARY KEY, received_at TEXT NOT NULL, source TEXT NOT NULL,
        content_hash TEXT NOT NULL, template TEXT NOT NULL, symbol TEXT NOT NULL, entry REAL, stop REAL, tp1 REAL,
        targets TEXT, decision_time TEXT, close REAL, verdict TEXT NOT NULL, p_tp1 REAL, base_expectancy_r REAL,
        evaluation TEXT NOT NULL, outcome TEXT NOT NULL, outcome_r REAL, filled_at TEXT, resolved_at TEXT,
        raw_text TEXT NOT NULL)""")
    old.commit()
    old.close()
    errors = []

    def read():
        try:
            ExternalSignalRegistry(tmp_path / "ext.sqlite3").recent(5)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_refresh_pair_downloads_only_public_missing_candles_one_at_a_time(market):
    settings, now = market
    api = CsiApi(settings, now=lambda: now)
    calls = []
    api.downloader = lambda settings, symbol, timeframe, **kwargs: calls.append((symbol, timeframe, kwargs["rest_only"]))
    result = api.dispatch("POST", "/refresh-pair", {}, {"symbol": "ethusdt"})
    assert result["symbol"] == "ETHUSDT" and result["last_candle"]
    assert calls == [("ETHUSDT", "15m", True), ("ETHUSDT", "1h", True), ("BTCUSDT", "1h", True)]
    with pytest.raises(Exception) as error:
        api.dispatch("POST", "/refresh-pair", {}, {"symbol": "DOGEUSDT"})
    assert getattr(error.value, "status", None) == 422
    api._refresh_lock.acquire()
    with pytest.raises(Exception) as busy:
        api.dispatch("POST", "/refresh-pair", {}, {"symbol": "ETHUSDT"})
    assert getattr(busy.value, "status", None) == 409
    api._refresh_lock.release()

    def offline(*_args, **_kwargs):
        raise OSError("réseau coupé")

    api.downloader = offline
    with pytest.raises(Exception) as down:
        api.dispatch("POST", "/refresh-pair", {}, {"symbol": "ETHUSDT"})
    assert getattr(down.value, "status", None) == 502


def test_models_route_lists_verdicts_but_not_descriptive_backtests(settings):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    registry = ExperimentRegistry(settings.experiments_db)
    common = {"hypothesis": "h", "strategy_version": 1, "variant": "v", "params": {}, "period_label": "DEVELOPMENT",
              "period_start": "a", "period_end": "b", "universe": [], "data_hashes": {}, "git_commit": "x",
              "dependencies": {}, "seed": 1, "cost_scenario": "central", "simulation_rules": {}, "report_dir": "r"}
    registry.record(run_id="B1", created_at="2026-09-30T00:00:00", kind="BACKTEST_REFERENCE", strategy="A",
                    metrics={}, status="COMPLETED", **common)
    registry.record(run_id="W1", created_at="2026-09-30T00:00:00", kind="WALK_FORWARD", strategy="A",
                    metrics={"verdict": "REJECTED", "n_trials": 12}, status="COMPLETED", **common)
    registry.record(run_id="S1", created_at="2026-09-30T00:00:00", kind="SCREEN", strategy="SCREEN_D_TO_I",
                    metrics={"rows": [{"beats_costs": False}], "n_trials": 6}, status="COMPLETED", **common)
    out = CsiApi(settings).dispatch("GET", "/models", {}, None)
    assert [(m["kind"], m["verdict"]) for m in out["models"]] == [
        ("SCREEN", "AUCUNE_CONDITION_AU_DELA_DES_COUTS"), ("WALK_FORWARD", "REJECTED")]
    assert out["program_trials"] == 1 + 12 + 6
