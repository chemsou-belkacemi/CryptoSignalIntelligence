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

from crypto_signal_intelligence.api.server import CSP, ApiError, CsiApi, make_handler
from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.outlook.pair import (
    HORIZONS,
    MIN_BLOCKS,
    POSITIVE,
    SL,
    TIMEOUT,
    TP,
    OutlookError,
    block_days_for,
    block_interval,
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


@pytest.mark.parametrize("case", ["objectif", "objectif_au_contact", "ouverture_au_dessus", "meme_bougie",
                                  "ouverture_sous_stop", "temps"])
def test_plan_outcomes_follow_the_conservative_exit_rules(case):
    # H = 4 bougies : σ_H = 1 % × 2 = 2 % → stop 98, objectif 103 pour une entrée à 100.
    paths = {
        "objectif": [FLAT, (100, 100.5, 99.5, 100), (100, 103.5, 99.9, 101)],
        "objectif_au_contact": [FLAT, (100, 103, 99.9, 100), (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 100),
                                (100, 100.2, 99.8, 100.5)],                    # touché, pas dépassé : pas de gain
        "ouverture_au_dessus": [FLAT, FLAT, (104, 105, 103.5, 104)],            # sortie à l'objectif, pas mieux
        "meme_bougie": [FLAT, (100, 104, 97, 100)],
        "ouverture_sous_stop": [FLAT, FLAT, (97, 97.5, 96.5, 97)],
        "temps": [FLAT, (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 100), (100, 100.2, 99.8, 101)],
    }
    rows = paths[case] + [FLAT] * (8 - len(paths[case]))
    out = plan_outcomes(bars(rows), np.array([0]), 4, NO_COSTS, STEP)
    expected = {"objectif": (TP, 103.0), "objectif_au_contact": (TIMEOUT, 100.5), "ouverture_au_dessus": (TP, 103.0),
                "meme_bougie": (SL, 98.0), "ouverture_sous_stop": (SL, 97.0), "temps": (TIMEOUT, 101.0)}[case]
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
    assert plan["rr_gross"] == pytest.approx(float((target - entry) / (entry - stop)), abs=1e-3)
    # 60 jours de données : moins de 30 blocs indépendants → aucun intervalle, aucun état chiffré.
    assert plan["state"] == "INSUFFISANT" and plan["expectancy_r_ci"] is None and plan["blocks"] < MIN_BLOCKS
    assert abs(plan["tp_first"] + plan["sl_first"] + plan["timeout"] - 1) < 1e-3
    assert {"historique", "comparable", "p_up", "p_net_positive", "mean_net", "fourchette", "plan", "issues",
            "esperance", "ecart", "intervalle", "exemples", "couts", "etat"} <= set(result["definitions"])
    assert result["history_end"] == "2025-06-30" and "PAS une proposition" in result["definitions"]["etat"]


def test_pair_outlook_flags_stale_data_and_refuses_bad_requests(market):
    settings, now = market
    stale = pair_outlook(settings, "ETHUSDT", "1h", now=now + timedelta(days=2))
    assert not stale["context"]["fresh"] and stale["plan"]["state"] == "DONNEES_ANCIENNES"
    earlier = pair_outlook(settings, "ETHUSDT", "1h", now=now - timedelta(days=10))   # point dans le temps
    assert earlier["context"]["fresh"] and pd.Timestamp(earlier["context"]["decision_time"]) <= now - timedelta(days=10)
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


def test_copied_plan_text_is_prose_with_no_signal_label(market):
    settings, now = market
    text = pair_outlook(settings, "ETHUSDT", "24h", now=now)["plan"]["copy_text"]
    assert text.startswith("CSI — plan indicatif, PAS un signal") and "\n" not in text
    import re
    assert not re.search(r"\b(PAIR|PAIRE|ENTRY|ENTRÉE|ENTREE|TP\d?|SL|STOP|OBJECTIF|ACHAT|BUY)\s*:", text.upper())


def test_cached_analysis_never_presents_aged_data_as_fresh(market):
    settings, now = market
    clock = {"now": now}
    api = CsiApi(settings, now=lambda: clock["now"])
    first = api.dispatch("POST", "/analyze-pair", {}, {"symbol": "ETHUSDT", "horizon": "1h"})
    assert first["context"]["fresh"] and first["plan"]["state"] != "DONNEES_ANCIENNES"
    clock["now"] = now + timedelta(minutes=5)
    again = api.dispatch("POST", "/analyze-pair", {}, {"symbol": "ETHUSDT", "horizon": "1h"})
    assert again["cached"] and again["context"]["data_age_minutes"] == first["context"]["data_age_minutes"] + 5
    clock["now"] = now + timedelta(days=2)
    later = api.dispatch("POST", "/analyze-pair", {}, {"symbol": "ETHUSDT", "horizon": "1h"})
    assert later["cached"] is False and not later["context"]["fresh"]
    assert later["plan"]["state"] == "DONNEES_ANCIENNES"


def test_refresh_is_refused_while_the_monitor_holds_its_lock(market):
    from crypto_signal_intelligence.live.lock import InstanceLock
    settings, now = market
    api = CsiApi(settings, now=lambda: now)
    api.downloader = lambda *args, **kwargs: None
    with InstanceLock(settings.root / settings.live.lock_file), pytest.raises(Exception) as error:
        api.dispatch("POST", "/refresh-pair", {}, {"symbol": "ETHUSDT"})
    assert getattr(error.value, "status", None) == 409 and "surveillance" in str(error.value)
    assert api.dispatch("POST", "/refresh-pair", {}, {"symbol": "ETHUSDT"})["symbol"] == "ETHUSDT"


def test_a_simulated_buy_writes_nothing_even_in_outbox_mode_and_matches_the_published_levels(settings, monkeypatch):
    from crypto_signal_intelligence.domain.enums import Action, EntryMode
    from crypto_signal_intelligence.domain.market import EntryIntent, StrategyResult
    from crypto_signal_intelligence.signals import analyze as analyze_module
    from crypto_signal_intelligence.signals.outbox import SignalRegistry
    from crypto_signal_intelligence.strategies.donchian import DonchianVolumeBreakout

    from .test_live import DECISION, store_candles

    def always_buy(self, context):
        close = float(context.setup["close"])
        return StrategyResult(strategy_id=self.strategy_id, strategy_version=1, action=Action.BUY,
                              setup_time=context.decision_time, regime=context.regime,
                              entry_intent=EntryIntent(EntryMode.LIMIT, close, 10, 2),
                              invalidation_reference=close * 0.98, exit_policy_id="FIXED_SL_ONE_TP_V1", target_r=2.0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("la simulation a touché la publication")

    monkeypatch.setattr(DonchianVolumeBreakout, "evaluate", always_buy)
    store_candles(settings)
    now = DECISION + timedelta(seconds=20)
    with monkeypatch.context() as patch:
        patch.setattr(settings.publication, "mode", "outbox")
        patch.setattr(SignalRegistry, "publish", forbidden)
        patch.setattr(analyze_module, "build_signal", forbidden)
        patch.setattr(analyze_module, "publication_suspended", forbidden)
        patch.setattr(type(settings), "publication_dir", forbidden)
        simulated = analyze(settings, "ETHUSDT", "DONCHIAN_VOLUME_BREAKOUT", now=now, publish=False)
    assert simulated.action == "BUY" and simulated.publication_status == "SIMULATION" and simulated.levels
    assert not settings.signals_db.exists() and not list(settings.root.rglob("*.txt"))
    published = analyze(settings, "ETHUSDT", "DONCHIAN_VOLUME_BREAKOUT", now=now)
    assert published.publication_status == "PUBLISHED" and published.levels == simulated.levels
    signal = SignalRegistry(settings.signals_db, settings.publication_dir()).load(published.signal_id)
    assert (str(signal.entry_1), str(signal.stop_loss), str(signal.tp_1)) == (
        simulated.levels["entry"], simulated.levels["stop"], simulated.levels["targets"][0])



def test_block_interval_needs_enough_independent_blocks_and_widens_with_the_correction():
    rng = np.random.default_rng(4)
    times = pd.date_range("2022-01-01", periods=600, freq="D", tz="UTC").to_numpy()
    values = rng.normal(0.01, 0.05, 600)
    narrow, blocks = block_interval(values, times, block_days=10, samples=800, seed=1, level=0.95)
    wide, _ = block_interval(values, times, block_days=10, samples=800, seed=1)
    assert blocks == 60 and wide[0] < narrow[0] < narrow[1] < wide[1]
    assert block_interval(values[:200], times[:200], block_days=10, samples=100, seed=1) == (None, 20)


@pytest.fixture
def long_market(settings):
    """800 jours, marche aléatoire à rendements indépendants AVEC dérive (aucune prévisibilité)."""
    def build(seed: int, drift: float = 0.00005):
        store = CandleStore(settings.data_dir)
        days = 800
        store.save(canonical(96 * days, "15m", symbol="ETHUSDT", start="2024-01-01", seed=seed, drift=drift),
                   "ETHUSDT", "15m")
        for symbol, offset in (("ETHUSDT", 1), ("BTCUSDT", 2)):
            store.save(canonical(24 * days, "1h", symbol=symbol, start="2024-01-01", seed=seed + offset, drift=drift),
                       symbol, "1h")
        settings.data.symbols = ["ETHUSDT", "BTCUSDT"]
        last = pd.Timestamp("2024-01-01", tz="UTC") + (96 * days - 1) * STEP
        return (last + STEP + timedelta(minutes=1)).to_pydatetime()
    return settings, build


@pytest.mark.parametrize("seed", [21, 3])
def test_past_drift_alone_never_reads_as_a_positive_history(long_market, seed):
    """Relecture leak-auditor : sous une dérive constante sans prévisibilité, l'ancien avis « FAVORABLE »
    sortait à 7 jours ; l'écart à « tous moments » et l'intervalle corrigé l'empêchent."""
    settings, build = long_market
    now = build(seed)
    for horizon in ("3j", "7j"):
        plan = pair_outlook(settings, "ETHUSDT", horizon, now=now)["plan"]
        assert plan["state"] != POSITIVE, (seed, horizon, plan)


def test_statistics_never_use_data_after_the_development_end(long_market):
    """Le test final reste réservé : falsifier tout ce qui suit le 2025-06-30 ne change aucune statistique
    « tous moments » (les statistiques du régime courant dépendent, elles, de la situation actuelle)."""
    settings, build = long_market
    now = build(5)
    first = pair_outlook(settings, "ETHUSDT", "24h", now=now)
    store = CandleStore(settings.data_dir)
    end = pd.Timestamp("2025-06-30 23:59:59", tz="UTC")
    for symbol, timeframe in (("ETHUSDT", "15m"), ("ETHUSDT", "1h"), ("BTCUSDT", "1h")):
        frame = store.load(symbol, timeframe)
        later = frame["open_time"] > end                                  # seulement ce qui suit la fin
        for column in ("open", "high", "low", "close"):
            frame.loc[later, column] = frame.loc[later, column] * 1.7
        store.save(frame, symbol, timeframe)
    second = pair_outlook(settings, "ETHUSDT", "24h", now=now)
    assert first["history_end"] == "2025-06-30"
    for a, b in zip(first["overview"], second["overview"], strict=True):
        assert a["p_up_all_moments"] == b["p_up_all_moments"], a["horizon"]
    assert first["plan"]["baseline_expectancy_r"] == second["plan"]["baseline_expectancy_r"]


def test_stale_btc_context_and_a_recent_data_gap_are_reported(market):
    settings, now = market
    store = CandleStore(settings.data_dir)
    btc = store.load("BTCUSDT", "1h")
    store.save(btc[btc["open_time"] < btc["open_time"].max() - pd.Timedelta(days=4)], "BTCUSDT", "1h")
    result = pair_outlook(settings, "ETHUSDT", "4h", now=now)
    assert result["context"]["btc_ret_24h_pct"] is None
    setup = store.load("ETHUSDT", "15m")
    cut = setup["open_time"].max() - pd.Timedelta(hours=3)
    store.save(setup[(setup["open_time"] < cut - pd.Timedelta(hours=5)) | (setup["open_time"] >= cut)], "ETHUSDT", "15m")
    gap = pair_outlook(settings, "ETHUSDT", "4h", now=now)
    assert gap["context"]["data_gap_recent"] and "trou" in gap["plan"]["unavailable"]
    assert gap["plan"]["state"] == "INSUFFISANT" and "copy_text" not in gap["plan"]



def test_models_route_also_reads_the_research_registry_read_only(settings, tmp_path, monkeypatch):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    research = ExperimentRegistry(tmp_path / "recherche" / "experiments.sqlite3")
    common = {"hypothesis": "h", "strategy_version": 1, "variant": "v", "params": {}, "period_label": "DEVELOPMENT",
              "period_start": "a", "period_end": "b", "universe": [], "data_hashes": {}, "git_commit": "x",
              "dependencies": {}, "seed": 1, "cost_scenario": "central", "simulation_rules": {}, "report_dir": "r"}
    research.record(run_id="MLS-1", created_at="2026-10-01T06:00:00", kind="ML_SWING_SELECT", strategy="ML_SWING",
                    metrics={"conclusion": "AUCUN_AVANTAGE_DEMONTRE", "n_trials": 136}, status="COMPLETED", **common)
    monkeypatch.setenv("CSI_RESEARCH_REGISTRY", str(research.db_path))
    out = CsiApi(settings).dispatch("GET", "/models", {}, None)
    assert [(m["kind"], m["verdict"], m["source"]) for m in out["models"]] == [
        ("ML_SWING_SELECT", "AUCUN_AVANTAGE_DEMONTRE", "recherche")]
    assert out["research_program_trials"] == 136 and out["program_trials"] == 0


def test_opportunities_route_reads_every_horizon_of_a_pair_once(market, monkeypatch):
    settings, now = market
    from crypto_signal_intelligence.features import loader
    from crypto_signal_intelligence.outlook import pair as outlook_pair
    from crypto_signal_intelligence.signals import analyze as signals_analyze
    calls = []
    real = loader.load_inputs

    def counted(*a, **k):
        calls.append(a[1])
        return real(*a, **k)

    for module in (loader, outlook_pair, signals_analyze):          # chaque module garde sa propre référence
        monkeypatch.setattr(module, "load_inputs", counted)
    api = CsiApi(settings, now=lambda: now)
    out = api.dispatch("POST", "/opportunities/pair", {}, {"symbol": "ethusdt"})
    assert out["symbol"] == "ETHUSDT" and calls == ["ETHUSDT"]                 # historique relu une seule fois
    assert [h["horizon"] for h in out["horizons"]] == ["1h", "4h", "12h", "24h", "3j", "7j"]
    assert all(h.get("state") or h.get("error") for h in out["horizons"])
    states = {h.get("state") for h in out["horizons"]}
    assert states <= {"HISTORIQUE_POSITIF_NON_VALIDE", "AUCUN_AVANTAGE_HISTORIQUE", "HISTORIQUE_DEFAVORABLE",
                      "INSUFFISANT", "DONNEES_ANCIENNES"}                       # états descriptifs seulement
    assert {s["strategy"] for s in out["strategies"]} and all("walk_forward_verdict" in s for s in out["strategies"])
    with pytest.raises(ApiError):
        api.dispatch("POST", "/opportunities/pair", {}, {"symbol": "DOGEUSDT"})  # hors univers
