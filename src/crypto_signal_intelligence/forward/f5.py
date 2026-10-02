"""Test en direct F5_MODELE_A : suivi simulé du modèle A (lot 8 v2) avec et sans feu tricolore, contre l'allocation
statique (docs/FORWARD_TESTS.md, section F5_MODELE_A ; règles figées au démarrage). Phases 2 et 8 de la mission
du 2026-10-02.

Chaque lundi après 00:10 UTC : décision de A (vote de 3 horizons, poids 1/5 × min(1, 0,50/σ̂) avec la volatilité
prévue à 7 jours du jour), exécutée à l'ouverture de la bougie 1 h de 01:00. Chaque jour après 01:10 UTC : feu du
jour (calculé sur les informations connues à 00:00), puis valorisation des trois portefeuilles simulés (A, A + feu,
STATIQUE) au prix d'ouverture de 01:00, frais du modèle commun (central et défavorable). Sur 3 mois, peu de
transactions : ce suivi vérifie le COMPORTEMENT réel du modèle, il ne le valide pas.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.store import CandleStore
from ..outlook import volatility as live_vol
from ..research import factors as fa
from ..research import long_horizon as lh
from ..research import volatility as vol
from ..research.universe import RESEARCH_UNIVERSE
from . import light
from .costs import CENTRAL, SCENARIOS, costs_for
from .journal import Journal, utc_iso
from .registry import ForwardTest, code_fingerprint
from .sources import PublicSources, SourceError

TEST_ID = "F5_MODELE_A"
MARKET = "BTCUSDT"
VARIANTS = ("A", "A_FEU", "STATIQUE")
DECISION_AFTER = pd.Timedelta(minutes=10)         # lundi 00:10 UTC : bougie 1 h de 23:00 stockée
VALUATION_AFTER = pd.Timedelta(hours=1, minutes=10)   # 01:10 UTC : ouverture de 01:00 connue
EXEC_HOUR = 1
BAND = lh.BAND
RED_CUT = 0.5
HALF_LIFE_RED = 1                                 # réduction de moitié le premier jour rouge d'une série
STATIC_EXPOSURE = 0.3225                          # exposition moyenne de A, essai LONG-20261002T155625Z-967d85 (central)
STATIC_RUN = "LONG-20261002T155625Z-967d85"
ANNOUNCEMENT_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
CATALOG_DELISTING, CATALOG_MAINTENANCE = 161, 157
DECISION, LIGHT, VALUATION, SOURCE_ERROR, HISTORY = "DECISION", "FEU", "VALORISATION", "ERREUR_SOURCE", "HISTORIQUE_VOL"
CONFORM, NOT_CONFORM, RUNNING = "CONFORME", "NON_CONFORME", "EN_COURS"


# --- Entrées du modèle ------------------------------------------------------------------------------------

def universe_for(start: dict) -> list[str]:
    frozen = set(start["halal"]["symbols"])
    return [s for s in RESEARCH_UNIVERSE if s in frozen]


def frames_for(settings: Settings, symbols: list[str], *, until: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Bougies 1 h stockées, closes avant `until` (exclu) : rien de postérieur à la décision n'est lu."""
    store = CandleStore(settings.data_dir)
    out = {}
    for symbol in symbols:
        frame = store.load(symbol, "1h")
        if frame.empty:
            continue
        frame = frame[frame["open_time"] + pd.Timedelta(hours=1) <= until]
        if not frame.empty:
            out[symbol] = frame.reset_index(drop=True)
    return out


def sigma_from_forecast(forecast: dict | None) -> float:
    """σ̂ annualisée à partir du mouvement typique à 7 jours (un écart-type, en %) : move/100 × √(365/7)."""
    if not forecast or not forecast.get("available"):
        return math.nan
    move = (forecast.get("horizons") or {}).get("7", {}).get("move_pct")
    return move / 100 * math.sqrt(365 / 7) if move else math.nan


def decide(frames: dict[str, pd.DataFrame], sigma: dict[str, float], monday: pd.Timestamp) -> dict:
    """Décision de A pour `monday` (00:00 UTC) avec les règles du lot 8 v2 (`research/long_horizon`)."""
    panel = fa.build_panel(frames)
    decisions = pd.DatetimeIndex([monday])
    table = pd.DataFrame({s: [sigma.get(s, math.nan)] for s in panel.symbols}, index=decisions)
    inputs = lh.Inputs(panel, decisions, table, pd.DataFrame(index=decisions))
    book = fa.Book(panel, decisions)
    if monday not in panel.close.index:
        raise ValueError(f"aucune journée de bougies pour {monday:%Y-%m-%d}")
    basket = lh.baskets(inputs, book)[monday]
    votes = lh.trend_votes(book).loc[monday]
    weights = lh.weights_a(inputs, book, with_funding=False).loc[monday]
    eligible = book.eligible.loc[monday]
    return {"basket": basket, "votes": {s: int(votes[s]) for s in basket},
            "sigma": {s: (round(float(table.loc[monday, s]), 4) if np.isfinite(table.loc[monday, s]) else None) for s in basket},
            "weights": {s: round(float(weights[s]), 6) for s in panel.symbols if weights[s] > 0},
            "eligible": int(eligible.sum()), "symbols_with_data": len(panel.symbols)}


# --- Portefeuilles simulés -----------------------------------------------------------------------------

def new_ledger() -> dict:
    return {"holdings": {}, "cash": 1.0, "entry": {}, "trades": 0, "fees": 0.0, "value": 1.0, "red_streak": 0}


def _cost(symbol: str, scenario: str) -> float:
    costs = costs_for(symbol, scenario)
    return costs.fee + costs.market


def revalue(ledger: dict, prices: dict[str, float], previous: dict[str, float]) -> dict:
    """Valeur des positions au nouveau prix (ratio au prix précédent ; actif sans prix : valeur conservée)."""
    holdings = dict(ledger["holdings"])
    for symbol, value in holdings.items():
        before, now = previous.get(symbol), prices.get(symbol)
        if before and now and before > 0:
            holdings[symbol] = value * now / before
    ledger = ledger | {"holdings": holdings}
    ledger["value"] = ledger["cash"] + sum(holdings.values())
    return ledger


def rebalance(ledger: dict, targets: dict[str, float], *, scenario: str, band: float | None = BAND,
              allow_buys: bool = True, allow_sells: bool = True, prices: dict[str, float] | None = None) -> dict:
    """Ramène les positions aux cibles (fractions du portefeuille) selon la bande de tolérance du lot 8 : un actif
    est échangé si son poids s'écarte de la cible de plus de `band` ou s'il entre ou sort ; frais par côté."""
    total = ledger["value"]
    if total <= 0:
        return ledger
    holdings = dict(ledger["holdings"])
    cash, trades, fees = ledger["cash"], 0, 0.0
    symbols = set(holdings) | set(targets)
    for symbol in sorted(symbols):
        if prices is not None and symbol not in prices:
            continue                                        # pas de prix du jour : rien n'est échangé
        current = holdings.get(symbol, 0.0)
        wanted = max(0.0, float(targets.get(symbol, 0.0))) * total
        entering, leaving = current <= 1e-12 < wanted, wanted <= 1e-12 < current
        if band is not None and not entering and not leaving and abs(wanted - current) / total <= band:
            continue
        if wanted > current and not allow_buys:
            continue
        if wanted < current and not allow_sells:
            continue
        delta = wanted - current
        if abs(delta) <= 1e-12:
            continue
        unit_cost = _cost(symbol, scenario)
        if delta > 0:
            delta = min(delta, max(0.0, cash) / (1 + unit_cost))   # achat et ses frais couverts par le cash : jamais de levier
            if delta <= 1e-12:
                continue
        fee = abs(delta) * unit_cost
        holdings[symbol] = current + delta
        cash -= delta + fee
        if holdings[symbol] <= 1e-12:
            holdings.pop(symbol, None)
        trades += 1
        fees += fee
    out = ledger | {"holdings": holdings, "cash": cash, "trades": ledger["trades"] + trades, "fees": ledger["fees"] + fees}
    out["value"] = out["cash"] + sum(holdings.values())
    return out


def apply_light(ledger: dict, light_day: dict, weekly_targets: dict[str, float] | None, *, scenario: str,
                prices: dict[str, float]) -> dict:
    """A + feu : rouge = ventes de moitié le premier jour rouge d'une série, aucune entrée ; orange = rien ;
    vert = cibles de la semaine (le lundi) ; actif rouge (delisting) = vendu entièrement."""
    color = light_day["color"]
    red_assets = {a for a in light_day.get("red_assets", [])}
    out = dict(ledger)
    if color == light.RED:
        out["red_streak"] = ledger.get("red_streak", 0) + 1
        if out["red_streak"] == HALF_LIFE_RED and ledger["holdings"]:
            halves = {s: v * RED_CUT / ledger["value"] for s, v in ledger["holdings"].items()}
            out = rebalance(out, halves, scenario=scenario, band=None, allow_buys=False, prices=prices)
    else:
        out["red_streak"] = 0
        if color == light.GREEN and weekly_targets is not None:
            out = rebalance(out, weekly_targets, scenario=scenario, prices=prices)
    if red_assets:
        kept = {s: v / out["value"] for s, v in out["holdings"].items() if s.replace("USDT", "") not in red_assets}
        if len(kept) != len(out["holdings"]):
            out = rebalance(out, kept, scenario=scenario, band=None, allow_buys=False, prices=prices)
    return out


# --- Lectures externes ------------------------------------------------------------------------------------

def announcement_titles(client: PublicSources, catalog: int, *, page_size: int = 20) -> list[str]:
    reply = client.get_json(ANNOUNCEMENT_URL, {"type": 1, "pageNo": 1, "pageSize": page_size, "catalogId": catalog})
    catalogs = (((reply or {}).get("data") or {}).get("catalogs")) if isinstance(reply, dict) else None
    if not catalogs:
        raise SourceError("annonces Binance : réponse sans catalogue")
    return [str(a.get("title", "")) for a in catalogs[0].get("articles", [])]


def btc_funding_8h(settings: Settings, *, day: pd.Timestamp) -> float | None:
    """Financement moyen par 8 h des 7 derniers jours du perpétuel BTC, lu dans le relevé F0_DERIVES."""
    from .derivlog import PAIR, journal
    rates = []
    low = (day - pd.Timedelta(days=7)).timestamp() * 1000
    for entry in journal(settings).entries({PAIR}):
        data = entry["data"]
        if data.get("spot") != MARKET:
            continue
        for stamp, rate, _ in data.get("funding", []):
            if stamp and low < float(stamp) <= day.timestamp() * 1000:
                rates.append(float(rate))
    return round(float(np.mean(rates)), 8) if rates else None


def peg_deviation(settings: Settings) -> dict[str, float | None]:
    from .datalog import SOURCE, journal
    latest: dict[str, float | None] = {"USDT": None, "USDC": None}
    for entry in journal(settings).entries({SOURCE}):
        if entry["data"].get("name") == "stablecoins":
            for coin in latest:
                value = (entry["data"]["data"].get(coin) or {}).get("deviation_pct")
                latest[coin] = float(value) if value is not None else latest[coin]
    return latest


def btc_forecast_history(settings: Settings, *, now: datetime, days: int = light.HISTORY_DAYS) -> list[list]:
    """Prévisions hors échantillon à 7 jours de BTC (σ en % sur 7 jours) aux 365 origines précédentes, chaque mois
    ajusté sur son seul passé (mêmes modèles et mêmes données que la prévision du jour)."""
    from ..external.universe import universe_symbols
    store = CandleStore(settings.data_dir)
    limit = pd.Timestamp(now)
    market = store.load(MARKET, "1h")
    market = market.loc[market["available_at"] <= limit, list(vol.READ_COLUMNS)].reset_index(drop=True)
    frames = {}
    for symbol in dict.fromkeys([MARKET, *universe_symbols(settings)]):
        own = market if symbol == MARKET else store.load(symbol, "1h")
        if own.empty:
            continue
        own = own.loc[own["available_at"] <= limit, list(vol.READ_COLUMNS)].reset_index(drop=True)
        frames[symbol] = vol.daily_frame(own, market).assign(symbol=symbol)
    data = pd.concat(frames.values(), ignore_index=True)
    last = live_vol.expected_origin(now)
    first = last - pd.Timedelta(days=days)
    rows = data[(data["symbol"] == MARKET) & (data["origin"] > first) & (data["origin"] <= last)]
    rows = rows[vol.complete_rows(rows) & (rows["history_days"] >= vol.MIN_HISTORY_DAYS)]
    out: list[list] = []
    for refit in sorted({pd.Timestamp(o).replace(day=1) for o in rows["origin"]}):
        month = rows[(rows["origin"] >= refit) & (rows["origin"] < refit + pd.offsets.MonthBegin(1))]
        models = vol.fit_at(data, refit, 7, seed=settings.protocol.seed)
        table = vol.month_forecasts(models, month, 7)
        for origin, variance in zip(table["origin"], table[live_vol.SELECTED[7]], strict=True):
            out.append([utc_iso(origin), round(math.sqrt(variance) * 100, 4) if np.isfinite(variance) and variance > 0 else None])
    return out


# --- Journal ---------------------------------------------------------------------------------------------

def _entries_by(journal: Journal, kind: str, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in journal.entries({kind}):
        out.setdefault(entry["data"][field], entry["data"])
    return out


def _monday_of(now: pd.Timestamp) -> pd.Timestamp | None:
    day = now.floor("D")
    return day if day.dayofweek == 0 and now >= day + DECISION_AFTER else None


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Le lundi après 00:10 UTC : une décision de A, inscrite une fois, avec ses entrées (votes, σ̂, panier)."""
    moment = pd.Timestamp(now)
    monday = _monday_of(moment)
    if monday is None or monday < pd.Timestamp(start["started_at"]).floor("D") or monday >= pd.Timestamp(start["final_at"]):
        return {"decisions": 0}
    if str(monday)[:10] in _entries_by(journal, DECISION, "week"):
        return {"decisions": 0}
    symbols = universe_for(start)
    frames = frames_for(settings, symbols, until=monday)
    forecast = live_vol.ensure(settings, now=now) or {}
    pairs = forecast.get("pairs", {})
    sigma = {s: sigma_from_forecast(pairs.get(s)) for s in symbols}
    decision = decide(frames, sigma, monday)
    journal.append(DECISION, {"week": str(monday)[:10], "decided_at": utc_iso(moment), "forecast_origin": forecast.get("origin"),
                              "forecast_source_run": forecast.get("source_run"), **decision,
                              "rules_code": code_fingerprint((fa.build_panel, fa.Book, lh.trend_votes, lh.baskets, lh.weights_a)),
                              "vol_code": code_fingerprint((vol.fit_at, vol.month_forecasts, vol.daily_frame))}, now=now)
    return {"decisions": 1, "basket": decision["basket"]}


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime, client: PublicSources | None = None) -> dict:
    """Chaque jour après 01:10 UTC : feu du jour puis valorisation (une fois par jour)."""
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    if moment < day + VALUATION_AFTER or day < pd.Timestamp(start["started_at"]).floor("D"):
        return {"valued": 0}
    if day > pd.Timestamp(start["final_at"]).floor("D"):
        return {"valued": 0}
    key = str(day)[:10]
    valuations = _entries_by(journal, VALUATION, "day")
    if key in valuations:
        return {"valued": 0}
    counts: dict = {"valued": 0}
    if journal.first(HISTORY) is None:
        try:
            journal.append(HISTORY, {"values": btc_forecast_history(settings, now=now), "days": light.HISTORY_DAYS}, now=now)
        except Exception as exc:  # noqa: BLE001 - sans historique, le rang de volatilité reste inconnu (journalisé)
            journal.append(SOURCE_ERROR, {"source": "historique_vol", "error": f"{type(exc).__name__}: {exc}"[:300]}, now=now)
    # --- feu du jour (entrées connues à 00:00) ---
    lights = _entries_by(journal, LIGHT, "day")
    if key not in lights:
        source = client or PublicSources()
        titles: dict[str, list[str]] = {}
        errors = {}
        try:
            for name, catalog in (("delisting", CATALOG_DELISTING), ("maintenance", CATALOG_MAINTENANCE)):
                try:
                    titles[name] = announcement_titles(source, catalog)
                except (SourceError, PermissionError) as exc:
                    titles[name] = []
                    errors[name] = str(exc)[:200]
        finally:
            if client is None:
                source.close()
        history_entry = journal.first(HISTORY)
        history = [v for _, v in (history_entry["data"]["values"] if history_entry else [])]
        for past in lights.values():
            if past.get("btc_forecast_pct") is not None:
                history.append(past["btc_forecast_pct"])
        forecast = live_vol.ensure(settings, now=now) or {}
        btc = (forecast.get("pairs") or {}).get(MARKET) or {}
        today = ((btc.get("horizons") or {}).get("7") or {}).get("move_pct") if btc.get("available") else None
        rank = light.volatility_rank(history[-light.HISTORY_DAYS:], today)
        decided = light.decide(day.date(), vol_rank=rank, btc_funding_8h=btc_funding_8h(settings, day=day),
                               peg_deviation_pct=peg_deviation(settings), delist_titles=titles.get("delisting", []),
                               maintenance_titles=titles.get("maintenance", []))
        journal.append(LIGHT, decided | {"btc_forecast_pct": today, "forecast_origin": forecast.get("origin"),
                                         "announcements": {k: v[:20] for k, v in titles.items()}, "source_errors": errors,
                                         "calendar_source": light.MACRO_SOURCE}, now=now)
        lights[key] = decided
        counts["light"] = decided["color"]
    # --- valorisation à l'ouverture de 01:00 ---
    symbols = universe_for(start)
    store = CandleStore(settings.data_dir)
    prices: dict[str, float] = {}
    for symbol in symbols:
        frame = store.load_since(symbol, "1h", day)
        row = frame[frame["open_time"] == day + pd.Timedelta(hours=EXEC_HOUR)]
        if not row.empty:
            prices[symbol] = float(row["open"].iloc[0])
    if MARKET not in prices:
        return counts | {"waiting": "ouverture de 01:00 absente"}
    previous_day = max(valuations) if valuations else None
    previous_prices = valuations[previous_day]["prices"] if previous_day else prices
    ledgers = valuations[previous_day]["ledgers"] if previous_day else {
        s: {v: new_ledger() for v in VARIANTS} for s in SCENARIOS}
    week = str(day)[:10] if day.dayofweek == 0 else None
    decision: dict | None = _entries_by(journal, DECISION, "week").get(week) if week else None
    weekly_targets = decision["weights"] if decision else None
    static_targets = ({s: STATIC_EXPOSURE / len(decision["basket"]) for s in decision["basket"]}
                      if decision and decision["basket"] else None)
    today_light: dict = lights.get(key) or {"color": light.GREEN, "red_assets": []}
    out_ledgers: dict = {}
    for scenario in SCENARIOS:
        out_ledgers[scenario] = {}
        for variant in VARIANTS:
            ledger = revalue(ledgers[scenario][variant], prices, previous_prices)
            if variant == "A" and weekly_targets is not None:
                ledger = rebalance(ledger, weekly_targets, scenario=scenario, prices=prices)
            elif variant == "A_FEU":
                ledger = apply_light(ledger, today_light, weekly_targets, scenario=scenario, prices=prices)
            elif variant == "STATIQUE" and static_targets is not None:
                members = frozenset(static_targets)
                if frozenset(ledger["holdings"]) != members:
                    ledger = rebalance(ledger, static_targets, scenario=scenario, band=None, prices=prices)
            out_ledgers[scenario][variant] = ledger
    journal.append(VALUATION, {"day": key, "prices": prices, "light": today_light["color"], "week_decision": week if decision else None,
                               "ledgers": out_ledgers,
                               "values": {s: {v: round(led["value"], 6) for v, led in per.items()} for s, per in out_ledgers.items()}},
                   now=now)
    counts["valued"] = 1
    return counts


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    return {}


# --- Mesures ---------------------------------------------------------------------------------------------

def _series(valuations: list[dict], scenario: str, variant: str) -> pd.Series:
    return pd.Series({v["day"]: v["values"][scenario][variant] for v in valuations}, dtype=float).sort_index()


def describe(values: pd.Series) -> dict:
    if len(values) < 2:
        return {"days": int(len(values)), "return": None}
    total = float(values.iloc[-1] / values.iloc[0] - 1)
    daily = values.pct_change().dropna()
    peak = values.cummax()
    return {"days": int(len(values)), "return": round(total, 6),
            "volatility_annual": round(float(daily.std(ddof=1) * math.sqrt(365)), 4) if len(daily) > 1 else None,
            "max_drawdown": round(float((values / peak - 1).min()), 6)}


def conformity(journal: Journal) -> dict:
    """Chaque décision est recalculée à partir de ses entrées journalisées (votes, σ̂, panier) : les poids doivent
    être identiques (règle 1/5 × min(1, 0,50/σ̂) pour un actif à ≥ 2 votes)."""
    checked, mismatches = 0, []
    for decision in _entries_by(journal, DECISION, "week").values():
        checked += 1
        expected = {}
        for symbol in decision["basket"]:
            sigma = decision["sigma"].get(symbol)
            if decision["votes"].get(symbol, 0) >= lh.VOTE_MIN and sigma:
                expected[symbol] = round(min(1.0, lh.SIGMA_TARGET / sigma) / lh.BASKET, 6)
        if {k: round(v, 5) for k, v in expected.items()} != {k: round(v, 5) for k, v in decision["weights"].items()}:
            mismatches.append(decision["week"])
    return {"decisions": checked, "mismatches": mismatches, "status": CONFORM if not mismatches else NOT_CONFORM}


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    valuations = sorted(_entries_by(journal, VALUATION, "day").values(), key=lambda v: v["day"])
    lights = _entries_by(journal, LIGHT, "day")
    colors = [v["color"] for v in lights.values()]
    out: dict = {"decisions": len(_entries_by(journal, DECISION, "week")), "days": len(valuations),
                 "light_days": {c: colors.count(c) for c in (light.RED, light.ORANGE, light.GREEN)},
                 "light_share": {c: round(colors.count(c) / len(colors), 4) for c in (light.RED, light.ORANGE, light.GREEN)} if colors else {},
                 "red_reasons": sorted({r for v in lights.values() for r in v.get("red_reasons", [])})[:20],
                 "red_assets": sorted({a for v in lights.values() for a in v.get("red_assets", [])}),
                 "source_errors": sum(1 for _ in journal.entries({SOURCE_ERROR})),
                 "static_exposure": STATIC_EXPOSURE, "scenarios": {}}
    for scenario in SCENARIOS:
        per: dict = {}
        for variant in VARIANTS:
            series = _series(valuations, scenario, variant)
            last = valuations[-1]["ledgers"][scenario][variant] if valuations else new_ledger()
            per[variant] = describe(series) | {"trades": last["trades"], "fees_pct": round(last["fees"] * 100, 4),
                                               "exposure": round(1 - last["cash"] / last["value"], 4) if last["value"] > 0 else None}
        a, feu = per["A"].get("return"), per["A_FEU"].get("return")
        per["A_FEU_minus_A"] = round(feu - a, 6) if a is not None and feu is not None else None
        out["scenarios"][scenario] = per
    out["conformity"] = conformity(journal)
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) + pd.Timedelta(days=1)
    out["ended"] = bool(ended)
    out["verdict"] = (f"SUIVI_TERMINE_{out['conformity']['status']}" if ended else RUNNING)
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "conformity": result["conformity"],
                                 "central": result["scenarios"][CENTRAL], "light_share": result["light_share"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Modèle A en direct (lot 8 v2), avec et sans feu tricolore, contre l'allocation statique",
    hypothesis=("Suivi du comportement réel du modèle A (vote de 3 horizons, ciblage de volatilité prévue à 50 %) et de "
                "l'effet du feu tricolore quotidien, en portefeuilles simulés valorisés chaque jour. Aucune validation "
                "attendue sur 3 mois : conformité des décisions et description des écarts."),
    params={"decision": "lundi 00:00 UTC (passage après 00:10), exécution à l'ouverture de 01:00",
            "variants": list(VARIANTS), "band": BAND, "red_cut": RED_CUT, "static_exposure": STATIC_EXPOSURE, "static_run": STATIC_RUN,
            "light": {"vol_top_share": light.VOL_TOP_SHARE, "funding_limit_8h": light.FUNDING_LIMIT_8H, "peg_limit_pct": light.PEG_LIMIT_PCT,
                      "history_days": light.HISTORY_DAYS, "calendar": list(light.MACRO_CALENDAR), "calendar_source": light.MACRO_SOURCE,
                      "announcements": {"delisting": CATALOG_DELISTING, "maintenance": CATALOG_MAINTENANCE}},
            "model": {"horizons_days": list(lh.HORIZONS_DAYS), "vote_min": lh.VOTE_MIN, "sigma_target": lh.SIGMA_TARGET, "basket": lh.BASKET},
            "rules_code": "non gelé (recherche) : empreinte inscrite dans chaque décision"},
    rule_objects=(), config_keys=("data.symbols",),
    frozen_modules=("crypto_signal_intelligence.forward.f5", "crypto_signal_intelligence.forward.light",
                    "crypto_signal_intelligence.forward.costs", "crypto_signal_intelligence.forward.registry",
                    "crypto_signal_intelligence.forward.journal", "crypto_signal_intelligence.outlook.volatility"),
    frozen_functions=(("crypto_signal_intelligence.data.store", "CandleStore"),),
)
