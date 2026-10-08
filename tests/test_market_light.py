"""Feu de protection du marché (risk/market_light.py, docs/METEO_PROTECTION.md) : règle (tous les cas, frontières),
EMA50 et clôtures journalières à la main, rang strict, causalité (bougies et prévisions futures sans effet), données
manquantes → INCONNU, journal quotidien, historique du rang identique à F12, route de l'API.
Données SYNTHÉTIQUES : rien ici ne dit ce que vaut le feu sur le marché (étude séparée, docs/METEO_MARCHE.md)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.api.server import CsiApi, make_handler
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.forward.journal import Journal
from crypto_signal_intelligence.risk import market_light as ml

from .conftest import canonical

NOW = datetime(2026, 10, 8, 3, tzinfo=UTC)
ORIGIN = datetime(2026, 10, 8, tzinfo=UTC)
SYMBOLS = [f"P{i:02d}USDT" for i in range(11)]          # 11 paires + BTC : au-dessus du minimum de 10


# --- Règle pure ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("rank", "below", "breadth", "color"), [
    (0.90, False, 0.9, ml.RED),             # rang ≥ 90 % : rouge, même avec un marché sain
    (0.8999, False, 0.9, ml.ORANGE),        # juste sous 90 % : orange (≥ 75 %)
    (0.75, False, 0.9, ml.ORANGE),          # frontière orange
    (0.7499, False, 0.9, ml.GREEN),
    (0.10, True, 0.33, ml.RED),             # BTC sous EMA50 ET largeur < 1/3
    (0.10, True, 1 / 3, ml.ORANGE),         # largeur exactement 1/3 : pas rouge (strictement inférieure), BTC sous → orange
    (0.10, True, 0.9, ml.ORANGE),           # BTC sous son EMA50 seul : orange
    (0.10, False, 0.2, ml.ORANGE),          # largeur faible sans BTC sous : orange, jamais rouge
    (0.10, False, 0.4999, ml.ORANGE),
    (0.10, False, 0.5, ml.GREEN),           # largeur exactement 1/2 : vert
    (0.0, False, 1.0, ml.GREEN),
    (0.95, True, 0.1, ml.RED),              # les deux raisons du rouge
])
def test_rule_all_cases_and_boundaries(rank, below, breadth, color):
    out = ml.decide(rank, below, breadth)
    assert out["color"] == color and out["missing"] == []
    assert (out["reasons"] == []) == (color == ml.GREEN)
    assert ml.explanation(out).startswith(f"Feu {color}")


@pytest.mark.parametrize("missing", ["volatility", "btc_structure", "breadth"])
def test_missing_component_gives_unknown_unless_red_is_already_certain(missing):
    # Valeurs qui ne donneraient pas ROUGE : toute donnée manquante → INCONNU, en disant laquelle.
    values = {"volatility": 0.5, "btc_structure": True, "breadth": 0.4}
    values[missing] = None
    out = ml.decide(values["volatility"], values["btc_structure"], values["breadth"])
    assert out["color"] == ml.UNKNOWN and out["missing"] == [missing]
    text = ml.explanation(out, {missing: "raison précise"})
    assert ml.COMPONENT_NAMES[missing] in text and "raison précise" in text
    # Valeurs ROUGE par deux voies (rang ≥ 90 %, BTC sous EMA50 et largeur < 1/3) : une seule donnée manquante laisse
    # toujours une voie entière, le feu reste ROUGE avec la mention de la donnée manquante.
    red = {"volatility": 0.95, "btc_structure": True, "breadth": 0.1}
    red[missing] = None
    out = ml.decide(red["volatility"], red["btc_structure"], red["breadth"])
    assert out["color"] == ml.RED and out["missing"] == [missing] and out["reasons"]
    text = ml.explanation(out, {missing: "raison précise"})
    assert text.startswith("Feu ROUGE") and "Données manquantes" in text and "raison précise" in text


@pytest.mark.parametrize(("rank", "below", "breadth", "color"), [
    (0.95, None, None, ml.RED),             # rang seul suffit
    (None, True, 0.2, ml.RED),              # BTC sous EMA50 et largeur < 1/3 suffisent
    (0.89, None, 0.9, ml.UNKNOWN),          # sous 90 % : la donnée manquante pourrait tout changer
    (None, True, None, ml.UNKNOWN),         # BTC sous EMA50 sans la largeur : pas de rouge certain
    (None, False, 0.1, ml.UNKNOWN),         # largeur faible sans BTC sous : jamais rouge par cette voie
    (None, None, None, ml.UNKNOWN),
])
def test_partial_red_only_when_present_data_already_give_red(rank, below, breadth, color):
    assert ml.decide(rank, below, breadth)["color"] == color


def test_rank_is_the_share_strictly_below_and_needs_300_values():
    history = list(range(300))
    assert ml.volatility_rank(history, 150) == pytest.approx(150 / 300)       # 150 valeurs < 150 (égalité exclue)
    assert ml.volatility_rank(history, 1000) == 1.0 and ml.volatility_rank(history, -1) == 0.0
    assert ml.volatility_rank(history[:299], 150) is None
    assert ml.volatility_rank([*history, float("nan")], None) is None


def test_ema_by_hand():
    closes = np.arange(1.0, 61.0)                    # 60 clôtures
    value = float(np.mean(closes[:50]))              # 25,5
    for close in closes[50:]:
        value = 2 / 51 * close + 49 / 51 * value
    assert ml.ema(closes) == pytest.approx(value)
    assert ml.ema(closes[:49]) is None and ml.ema(closes[:50]) == pytest.approx(25.5)


def hourly(days: int, *, end: datetime, slope: float = 0.0, symbol: str = "BTCUSDT", start_price: float = 100.0) -> pd.DataFrame:
    """Bougies 1 h synthétiques (prix en pente constante par heure) finissant à la bougie de 23:00 de la veille de
    `end` ; `available_at` = clôture + 2 s."""
    n = days * 24
    opens = pd.date_range(end=pd.Timestamp(end) - pd.Timedelta(hours=1), periods=n, freq="h", tz=None)
    opens = opens.tz_localize("UTC") if opens.tz is None else opens
    close = start_price * np.exp(slope * np.arange(n))
    return pd.DataFrame({"open_time": opens, "close": close,
                         "available_at": opens + pd.Timedelta(hours=1) + pd.Timedelta(seconds=2), "symbol": symbol})


def test_daily_closes_keep_complete_days_known_at_now():
    frame = hourly(3, end=ORIGIN)                       # 5, 6 et 7 octobre complets
    frame = frame.drop(index=30)                        # une heure du 6 manque : journée incomplète
    closes = ml.daily_closes(frame, NOW)
    assert [f"{d:%Y-%m-%d}" for d in closes.index] == ["2026-10-05", "2026-10-07"]
    assert closes["close"].iloc[-1] == frame["close"].iloc[-1]
    assert closes["known_at"].iloc[-1] == pd.Timestamp("2026-10-08 00:00:02", tz="UTC")
    # Une seconde avant la disponibilité de la bougie de 23:00, le 7 n'est pas complet.
    early = ml.daily_closes(frame, datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC))
    assert [f"{d:%Y-%m-%d}" for d in early.index] == ["2026-10-05"]


# --- Lecture complète avec un magasin et un journal F12 synthétiques ------------------------------------------------

def market(btc_slope: float, up: int, *, days: int = 80, end: datetime = ORIGIN) -> dict[str, pd.DataFrame]:
    """BTC en pente `btc_slope` ; `up` paires en hausse, les autres en baisse (au-dessus / sous leur EMA50)."""
    frames = {"BTCUSDT": hourly(days, end=end, slope=btc_slope)}
    for i, symbol in enumerate(SYMBOLS):
        frames[symbol] = hourly(days, end=end, slope=0.001 if i < up else -0.001, symbol=symbol)
    return frames


@pytest.fixture
def light_settings(settings):
    settings.data.symbols = ["BTCUSDT", *SYMBOLS]
    return settings


def write_vol(settings, *, today: float, history: list[float], origin: datetime = ORIGIN, at: datetime | None = None) -> None:
    """Historique recalculé (jours d'avant) + prévision du jour dans le journal de F12."""
    days = {f"{(pd.Timestamp(origin) - pd.Timedelta(days=len(history) - i)):%Y-%m-%d}": v for i, v in enumerate(history)}
    path = ml.history_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"computed_at": "2026-10-01T00:00:00+00:00", "values": days}), encoding="utf-8")
    Journal(ml.f12_journal_path(settings)).append(
        "PREVISION", {"day": f"{origin:%Y-%m-%d}", "origin": pd.Timestamp(origin).isoformat(),
                      "pairs": {"BTCUSDT": {"24h": {"H1_HAR_PROFILE": today, "R0_RECENT_24H": today}}}},
        now=at or pd.Timestamp(origin) + pd.Timedelta(minutes=10))


def test_green_orange_red_from_the_store(light_settings):
    calm = [0.0004 + 0.000001 * i for i in range(365)]             # prévision du jour au milieu : rang ≈ 50 %
    write_vol(light_settings, today=0.000582, history=calm)
    frames = market(0.001, up=11)
    out = ml.current(light_settings, now=NOW, loader=frames.__getitem__)
    assert out["color"] == ml.GREEN and out["places_orders"] is False
    vol, btc, breadth = (out["components"][k] for k in ("volatility", "btc_structure", "breadth"))
    assert vol["rank"] == pytest.approx(182 / 365, abs=1e-3) and vol["history_values"] == 365
    assert vol["known_at"] == "2026-10-08T00:10:00+00:00" and vol["move_24h_pct"] == pytest.approx(2.41, abs=0.01)
    assert btc["below"] is False and btc["day"] == "2026-10-07" and btc["known_at"] == "2026-10-08T00:00:02+00:00"
    assert breadth["above"] == 12 and breadth["eligible"] == 12 and breadth["share"] == 1.0
    assert "aucun gain démontré" in out["note"] and out["computed_at"] == pd.Timestamp(NOW).isoformat()
    # BTC en baisse, 3 paires sur 12 au-dessus (< 1/3) : ROUGE.
    red = ml.current(light_settings, now=NOW, loader=market(-0.001, up=3).__getitem__)
    assert red["color"] == ml.RED and red["components"]["breadth"]["share"] == 0.25
    # BTC en baisse, 4 paires sur 12 (= 1/3) : ORANGE.
    assert ml.current(light_settings, now=NOW, loader=market(-0.001, up=4).__getitem__)["color"] == ml.ORANGE


def test_causality_future_candles_and_forecasts_change_nothing(light_settings):
    write_vol(light_settings, today=0.0005, history=[0.0004 + 0.000001 * i for i in range(365)])
    frames = market(0.001, up=7)
    base = ml.current(light_settings, now=NOW, loader=frames.__getitem__)
    # Mutation : bougies FUTURES (available_at > NOW) ajoutées et falsifiées (krach), plus une prévision future de F12.
    mutated = {}
    for symbol, frame in frames.items():
        future = hourly(3, end=ORIGIN + timedelta(days=3), slope=-0.05, symbol=symbol, start_price=1.0)
        future = future[future["available_at"] > pd.Timestamp(NOW)]
        mutated[symbol] = pd.concat([frame, future], ignore_index=True)
    Journal(ml.f12_journal_path(light_settings)).append(
        "PREVISION", {"day": "2026-10-09", "origin": "2026-10-09T00:00:00+00:00",
                      "pairs": {"BTCUSDT": {"24h": {"H1_HAR_PROFILE": 1.0}}}}, now=NOW + timedelta(days=1))
    again = ml.current(light_settings, now=NOW, loader=mutated.__getitem__)
    assert again == base
    # Contrôle de la mutation : vue au moment où ces données existent, la même falsification change bien le feu.
    later = ml.current(light_settings, now=NOW + timedelta(days=1), loader=mutated.__getitem__)
    assert later["components"] != base["components"]


def test_missing_data_gives_unknown_with_the_reason(light_settings):
    frames = market(0.001, up=11)
    out = ml.current(light_settings, now=NOW, loader=frames.__getitem__)       # aucune prévision de F12
    assert out["color"] == ml.UNKNOWN and [m["component"] for m in out["missing"]] == ["volatility"]
    assert "F12" in out["missing"][0]["reason"] and "INCONNU" in out["explanation"]
    write_vol(light_settings, today=0.0005, history=[0.0004] * 299)           # 299 valeurs : rang inconnu
    out = ml.current(light_settings, now=NOW, loader=frames.__getitem__)
    assert out["color"] == ml.UNKNOWN and "299" in out["missing"][0]["reason"]
    # Prévision de plus de 36 h : périmée.
    stale = ml.current(light_settings, now=NOW + timedelta(days=2), loader=frames.__getitem__)
    assert stale["color"] == ml.UNKNOWN and any("périmée" in m["reason"] for m in stale["missing"])
    # Moins de 10 paires éligibles (les autres ont une journée de retard) : largeur inconnue.
    lagging = {s: (f if s == "BTCUSDT" or s in SYMBOLS[:5] else f[f["open_time"] < pd.Timestamp("2026-10-07", tz="UTC")])
               for s, f in frames.items()}
    write_vol(light_settings, today=0.0005, history=[0.0006] * 365)           # rang 0 : pas de rouge sans la largeur
    out = ml.current(light_settings, now=NOW, loader=lagging.__getitem__)
    assert out["color"] == ml.UNKNOWN and [m["component"] for m in out["missing"]] == ["breadth"]
    # Même largeur absente, mais rang ≥ 90 % : ROUGE quand même, avec la mention de la largeur manquante.
    write_vol(light_settings, today=0.0005, history=[0.0004] * 365)
    out = ml.current(light_settings, now=NOW, loader=lagging.__getitem__)
    assert out["color"] == ml.RED and [m["component"] for m in out["missing"]] == ["breadth"]
    assert "Données manquantes" in out["explanation"] and out["components"]["breadth"] is None
    # BTC sans bougies : structure et largeur inconnues.
    empty = dict(frames, BTCUSDT=frames["BTCUSDT"].iloc[0:0])
    out = ml.current(light_settings, now=NOW, loader=empty.__getitem__)
    assert {m["component"] for m in out["missing"]} == {"btc_structure", "breadth"}


def test_daily_journal_once_a_day_after_the_forecast(light_settings, monkeypatch):
    frames = market(0.001, up=11)
    monkeypatch.setattr(ml, "_candles", lambda settings, symbol, now: frames[symbol])
    write_vol(light_settings, today=0.0005, history=[0.0004 + 0.000001 * i for i in range(365)],
              origin=ORIGIN - timedelta(days=1))                       # prévision d'hier seulement
    assert ml.record_day(light_settings, now=datetime(2026, 10, 8, 0, 20, tzinfo=UTC)) is None     # on attend F12
    Journal(ml.f12_journal_path(light_settings)).append(
        "PREVISION", {"day": "2026-10-08", "origin": "2026-10-08T00:00:00+00:00",
                      "pairs": {"BTCUSDT": {"24h": {"H1_HAR_PROFILE": 0.0005}}}}, now=datetime(2026, 10, 8, 0, 25, tzinfo=UTC))
    assert ml.record_day(light_settings, now=datetime(2026, 10, 8, 0, 30, tzinfo=UTC)) == {"day": "2026-10-08", "color": ml.GREEN}
    assert ml.record_day(light_settings, now=datetime(2026, 10, 8, 5, tzinfo=UTC)) is None          # une fois par jour
    # Le lendemain sans prévision : inscrit quand même après 06:00 (INCONNU ou couleur, jamais rien).
    assert ml.record_day(light_settings, now=datetime(2026, 10, 9, 5, tzinfo=UTC)) is None
    assert ml.record_day(light_settings, now=datetime(2026, 10, 9, 6, 5, tzinfo=UTC))["day"] == "2026-10-09"
    lines = [json.loads(line) for line in ml.journal_path(light_settings).read_text(encoding="utf-8").splitlines()]
    assert [line["day"] for line in lines] == ["2026-10-08", "2026-10-09"] and lines[0]["components"]["volatility"]


def test_history_is_computed_once_and_only_when_needed(light_settings, monkeypatch):
    calls = []

    def fake(settings, *, now, days=365):
        calls.append(now)
        return {f"{(pd.Timestamp(ORIGIN) - pd.Timedelta(days=d)):%Y-%m-%d}": 0.0004 for d in range(1, 366)}

    monkeypatch.setattr(ml, "compute_vol_history", fake)
    out = ml.ensure_vol_history(light_settings, now=NOW)
    assert out == {"values": 365, "before": 0} and len(calls) == 1
    stored = json.loads(ml.history_path(light_settings).read_text(encoding="utf-8"))
    assert stored["model"] == "H1_HAR_PROFILE" and stored["pool"][0] == "BTCUSDT" and len(stored["code"]) == 64
    assert ml.ensure_vol_history(light_settings, now=NOW + timedelta(hours=1)) is None and len(calls) == 1   # complet
    assert ml.ensure_vol_history(light_settings, now=NOW + timedelta(hours=1), force=True) is not None       # --force
    assert len(calls) == 2


def test_a_crashed_history_attempt_is_not_retried_for_20_hours(light_settings, monkeypatch):
    """Un calcul qui lève (ou est tué faute de mémoire) laisse son marqueur de tentative écrit AVANT : pas de nouvel
    essai 1 h plus tard, un nouvel essai 21 h plus tard."""
    calls = []

    def crash(settings, *, now, days=365):
        calls.append(now)
        raise MemoryError("tué")

    monkeypatch.setattr(ml, "compute_vol_history", crash)
    with pytest.raises(MemoryError):
        ml.ensure_vol_history(light_settings, now=NOW)
    marker = json.loads(ml.attempt_path(light_settings).read_text(encoding="utf-8"))
    assert marker["attempted_at"] == pd.Timestamp(NOW).isoformat() and not ml.history_path(light_settings).exists()
    assert ml.ensure_vol_history(light_settings, now=NOW + timedelta(hours=1)) is None and len(calls) == 1
    with pytest.raises(MemoryError):
        ml.ensure_vol_history(light_settings, now=NOW + timedelta(hours=21))
    assert len(calls) == 2


def test_the_monitor_never_runs_the_heavy_history(settings, monkeypatch):
    from crypto_signal_intelligence.forward import runner

    def forbidden(*args, **kwargs):
        raise AssertionError("calcul lourd lancé par la surveillance")

    monkeypatch.setattr(ml, "ensure_vol_history", forbidden)
    monkeypatch.setattr(ml, "compute_vol_history", forbidden)
    for thread in threading.enumerate():                             # passage en fond lancé par un autre test
        if thread.name == "csi-forward":
            thread.join(timeout=120)
    out = runner.daily(settings, now=datetime(2026, 10, 8, 7, tzinfo=UTC), force=True)
    assert out is not None and "market_light" in out and "error" not in (out["market_light"] or {})


def test_the_cli_runs_the_history_once(light_settings, monkeypatch):
    from typer.testing import CliRunner

    from crypto_signal_intelligence.cli import app
    monkeypatch.setattr(ml, "compute_vol_history", lambda settings, *, now, days=365: {"2026-10-01": 0.0004})
    monkeypatch.setattr("crypto_signal_intelligence.cli._heavy_job", lambda settings: None)
    result = CliRunner().invoke(app, ["meteo-historique"])
    assert result.exit_code == 0 and "1 jours calculés" in result.output
    again = CliRunner().invoke(app, ["meteo-historique"])
    assert again.exit_code == 0 and "Rien à faire" in again.output


def test_f12_forecast_is_not_seen_before_it_is_written(light_settings):
    """Maintenant = 00:05 : la prévision d'origine 00:00, inscrite par F12 à 00:10, n'existe pas encore."""
    history = [0.0004 + 0.000001 * i for i in range(365)]
    write_vol(light_settings, today=0.0005, history=history, origin=ORIGIN - timedelta(days=1))   # hier, à 00:10
    Journal(ml.f12_journal_path(light_settings)).append(
        "PREVISION", {"day": "2026-10-08", "origin": ORIGIN.isoformat(),
                      "pairs": {"BTCUSDT": {"24h": {"H1_HAR_PROFILE": 0.01}}}},
        now=ORIGIN + timedelta(minutes=10))
    early, _ = ml.volatility_component(light_settings, now=ORIGIN + timedelta(minutes=5))
    assert early is not None and early["origin"] == (ORIGIN - timedelta(days=1)).isoformat()      # celle d'hier
    later, _ = ml.volatility_component(light_settings, now=ORIGIN + timedelta(minutes=15))
    assert later is not None and later["origin"] == ORIGIN.isoformat() and later["rank"] == 1.0


def test_history_matches_what_f12_journals(settings, monkeypatch):
    """L'historique du rang est la prévision que F12 aurait inscrite ce jour-là (mêmes fonctions gelées, mêmes paires,
    même graine, réajustement trimestriel sur le seul passé)."""
    from crypto_signal_intelligence.forward import f12
    from crypto_signal_intelligence.research import volatility as v1
    pairs = ("BTCUSDT", "ETHUSDT")
    store = CandleStore(settings.data_dir)
    for index, symbol in enumerate(pairs):
        store.save(canonical(24 * 1400, "1h", symbol=symbol, start="2022-12-14", seed=index), symbol, "1h")
    settings.data.symbols = list(pairs)
    monkeypatch.setattr(v1, "LGBM_ROUNDS", 10)
    monkeypatch.setattr(v1, "LGBM_PARAMS", v1.LGBM_PARAMS | {"min_data_in_leaf": 20})
    f12._HOURLY_CACHE.clear()
    origin = pd.Timestamp("2026-10-02", tz="UTC")
    live = f12.hourly_forecasts(settings, list(pairs), origin, seed=settings.protocol.seed)["BTCUSDT"]["24h"]["H1_HAR_PROFILE"]
    history = ml.compute_vol_history(settings, now=datetime(2026, 10, 3, 0, 20, tzinfo=UTC), days=2)
    assert set(history) == {"2026-10-01", "2026-10-02"}
    assert history["2026-10-02"] == pytest.approx(live, rel=1e-9)


# --- Route de l'API -------------------------------------------------------------------------------------------

def test_api_route_with_token(light_settings, monkeypatch):
    frames = market(0.001, up=11)
    monkeypatch.setattr(ml, "_candles", lambda settings, symbol, now: frames[symbol])
    write_vol(light_settings, today=0.0009, history=[0.0004 + 0.000001 * i for i in range(365)])   # rang 100 % : ROUGE
    api = CsiApi(light_settings, now=lambda: NOW)
    out = api.dispatch("GET", "/meteo", {}, None)
    assert out["color"] == ml.RED and out["components"]["volatility"]["rank"] == 1.0 and "Feu ROUGE" in out["explanation"]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, token="jeton-de-test"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/meteo"
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(url, timeout=10)
        assert refused.value.code == 401
        request = urllib.request.Request(url, headers={"Authorization": "Bearer jeton-de-test"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200 and payload["color"] == ml.RED and payload["places_orders"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_card_at_the_top_of_the_market_tab_says_no_gain_is_demonstrated():
    from crypto_signal_intelligence.api.server import STATIC_DIR
    script, page = (STATIC_DIR / "app.js").read_text(encoding="utf-8"), (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    market_tab = page[page.index('id="tab-market"'):]
    assert market_tab.index('id="meteo-result"') < market_tab.index('<section class="card">')    # en tête de l'onglet
    assert 'api("/meteo")' in script and "Météo du marché (protection)" in script
    assert "outil de prudence, aucun gain démontré ; étude en cours" in script.lower()
    assert "loadMeteo();" in script
