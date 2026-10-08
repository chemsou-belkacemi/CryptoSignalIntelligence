"""Feu de protection du marché (« météo du marché », docs/METEO_PROTECTION.md) : VERT, ORANGE, ROUGE ou INCONNU.

C'est un OUTIL DE GESTION DU RISQUE, comme la perte maximale du jour : il dit « prudence » quand le marché est
agité ou baisse partout. Ce n'est PAS une stratégie et AUCUN GAIN n'est démontré. L'étude séparée
`docs/METEO_MARCHE.md` (branche `recherche/meteo`) mesurera plus tard ce qu'il vaut ; rien ici n'en dépend.

Règle DÉCLARÉE, sans aucun réglage optimisé (seuils repris de F5 et de METEO_MARCHE.md, fixés a priori) :
- Volatilité prévue : rang de la prévision à 24 h de BTC (H1_HAR_PROFILE, seule prévision de volatilité confirmée
  hors échantillon ; calculée chaque jour à 00:00 UTC par le test en direct F12, lu ici sans écriture) parmi ses
  valeurs des 365 jours précédents (part des valeurs STRICTEMENT inférieures, au moins 300 valeurs).
- Structure BTC : clôture journalière de BTCUSDT ≤ son EMA50 journalière.
- Largeur : part des paires de la configuration dont la clôture journalière est > leur EMA50 journalière.
- ROUGE si rang ≥ 90 %, ou si BTC est sous son EMA50 ET la largeur < 1/3 ;
  sinon ORANGE si rang ≥ 75 %, ou BTC sous son EMA50, ou largeur < 1/2 ; sinon VERT ;
  INCONNU si une composante manque (la liste dit laquelle).

Causalité : seules les bougies dont `available_at` ≤ maintenant entrent ; la clôture journalière est celle de la
bougie 1 h de 23:00 d'une journée UTC complète (24 bougies) ; le rang compare la prévision du jour aux seules
prévisions des jours d'avant. Les entrées de F12 sont lues si leur horodatage est ≤ maintenant.

Historique du rang : les jours où F12 tournait viennent de son journal ; les jours d'avant sont recalculés une fois
par la surveillance (`ensure_vol_history`) avec les MÊMES fonctions gelées que F12 (`volatility_hourly.hourly_frame`,
`fit_at`, `quarter_forecasts`, réajustement trimestriel sur le seul passé, mêmes paires), comme F5 l'a fait pour sa
prévision à 7 jours. F12 n'est pas modifié.

Journal quotidien en ajout seul : `state/market_light.jsonl` (un feu par jour UTC), pour comparer plus tard.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from ..data.store import CandleStore

MARKET = "BTCUSDT"
SOURCE_TEST = "F12_VOL_FORWARD"
MODEL = "H1_HAR_PROFILE"
HORIZON = "24h"
GREEN, ORANGE, RED, UNKNOWN = "VERT", "ORANGE", "ROUGE", "INCONNU"
COLORS = (GREEN, ORANGE, RED, UNKNOWN)
# --- Seuils déclarés (aucun n'est optimisé) ---------------------------------------------------------------------
RANK_RED = 0.90
RANK_ORANGE = 0.75
BREADTH_RED = 1 / 3
BREADTH_ORANGE = 1 / 2
RANK_DAYS = 365                     # fenêtre du rang : les 365 jours qui précèdent l'origine du jour
RANK_MIN_VALUES = 300               # sous ce nombre de valeurs dans la fenêtre, le rang est inconnu
EMA_DAYS = 50
EMA_ALPHA = 2 / (EMA_DAYS + 1)      # départ : moyenne simple des 50 premières clôtures
CLOSES_WINDOW = pd.Timedelta(days=500)   # bougies lues : l'EMA50 a oublié son départ bien avant (≈ 1e-8)
BREADTH_MIN_PAIRS = 10              # sous ce nombre de paires éligibles, la largeur est inconnue
MAX_FORECAST_AGE = pd.Timedelta(hours=36)   # même limite que risk/advice.py
MAX_CLOSE_AGE = pd.Timedelta(days=2)        # dernière journée complète au plus tard avant-hier
RECORD_AFTER = pd.Timedelta(hours=6)        # journal : feu inscrit dès la prévision du jour, au plus tard à 06:00
HISTORY_RETRY = pd.Timedelta(hours=20)      # historique du rang : au plus un recalcul par jour s'il reste incomplet
NOTE = ("Outil de prudence, aucun gain démontré ; étude en cours (docs/METEO_MARCHE.md). Ce feu est un garde-fou de "
        "gestion du risque, comme la perte maximale du jour : ce n'est pas une stratégie, il ne prévoit pas le sens "
        "du marché. CSI ne passe aucun ordre.")
RULE = ("ROUGE si le rang de la volatilité prévue de BTC à 24 h est ≥ 90 % de ses 365 jours précédents, ou si BTC "
        "clôture sous son EMA50 journalière ET que moins d'1/3 des paires sont au-dessus de la leur ; sinon ORANGE si "
        "le rang est ≥ 75 %, ou si BTC est sous son EMA50, ou si moins de la moitié des paires sont au-dessus de leur "
        "EMA50 ; sinon VERT. INCONNU si une donnée manque. Seuils déclarés, jamais optimisés.")
COMPONENT_NAMES = {"volatility": "volatilité prévue de BTC (rang sur 365 jours)",
                   "btc_structure": "structure BTC (clôture journalière contre EMA50)",
                   "breadth": "largeur (paires au-dessus de leur EMA50)"}


# --- Règle pure ---------------------------------------------------------------------------------------------------

def decide(vol_rank: float | None, btc_below_ema: bool | None, breadth: float | None) -> dict:
    """Couleur du feu et raisons, à partir des trois composantes (None = absente → INCONNU)."""
    missing = [name for name, value in (("volatility", vol_rank), ("btc_structure", btc_below_ema),
                                        ("breadth", breadth)) if value is None]
    if missing:
        return {"color": UNKNOWN, "reasons": [], "missing": missing}
    assert vol_rank is not None and btc_below_ema is not None and breadth is not None
    red, orange = [], []
    if vol_rank >= RANK_RED:
        red.append(f"volatilité prévue de BTC très haute (rang {vol_rank:.0%} ≥ 90 %)")
    if btc_below_ema and breadth < BREADTH_RED:
        red.append(f"BTC sous son EMA50 et seulement {breadth:.0%} des paires au-dessus de la leur (< 1/3)")
    if red:
        return {"color": RED, "reasons": red, "missing": []}
    if vol_rank >= RANK_ORANGE:
        orange.append(f"volatilité prévue de BTC haute (rang {vol_rank:.0%} ≥ 75 %)")
    if btc_below_ema:
        orange.append("BTC clôture sous son EMA50 journalière")
    if breadth < BREADTH_ORANGE:
        orange.append(f"seulement {breadth:.0%} des paires au-dessus de leur EMA50 (< 1/2)")
    return {"color": ORANGE if orange else GREEN, "reasons": orange, "missing": []}


def explanation(decision: dict, missing_detail: dict[str, str] | None = None) -> str:
    """Une phrase en français, pour la carte, l'API et BinanceSpotManager."""
    color = decision["color"]
    if color == UNKNOWN:
        detail = missing_detail or {}
        parts = [f"{COMPONENT_NAMES[m]} : {detail.get(m, 'absente')}" for m in decision["missing"]]
        return "Feu INCONNU : donnée manquante — " + " ; ".join(parts) + "."
    if color == GREEN:
        return ("Feu VERT : volatilité prévue de BTC sous son 75e rang, BTC au-dessus de son EMA50 et au moins la moitié "
                "des paires au-dessus de la leur.")
    return f"Feu {color} : " + " ; ".join(decision["reasons"]) + "."


def volatility_rank(history: list[float], today: float | None) -> float | None:
    """Part des valeurs de l'historique STRICTEMENT inférieures à celle du jour ; None sous RANK_MIN_VALUES."""
    values = np.array([v for v in history if v is not None and np.isfinite(v)], float)
    if today is None or not np.isfinite(today) or len(values) < RANK_MIN_VALUES:
        return None
    return float((values < today).mean())


def ema(closes: np.ndarray) -> float | None:
    """EMA des clôtures (α = 2/51), départ par la moyenne simple des 50 premières ; None sous 50 clôtures."""
    if len(closes) < EMA_DAYS:
        return None
    value = float(np.mean(closes[:EMA_DAYS]))
    for close in closes[EMA_DAYS:]:
        value = EMA_ALPHA * float(close) + (1 - EMA_ALPHA) * value
    return value


def daily_closes(h1: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Clôtures journalières des journées UTC COMPLÈTES (24 bougies 1 h connues à `now`) : index = jour, colonnes
    `close` (bougie de 23:00) et `known_at` (son `available_at`)."""
    empty = pd.DataFrame(columns=["close", "known_at"])
    if h1.empty:
        return empty
    frame = h1.loc[pd.to_datetime(h1["available_at"], utc=True) <= pd.Timestamp(now), ["open_time", "close", "available_at"]]
    if frame.empty:
        return empty
    frame = frame.assign(open_time=pd.to_datetime(frame["open_time"], utc=True),
                         available_at=pd.to_datetime(frame["available_at"], utc=True))
    frame = frame.drop_duplicates("open_time").sort_values("open_time")
    frame = frame[np.isfinite(frame["close"].to_numpy(float))]
    days = frame.assign(day=frame["open_time"].dt.floor("D")).groupby("day").agg(
        n=("close", "size"), close=("close", "last"), last_open=("open_time", "last"), known_at=("available_at", "last"))
    complete = days[(days["n"] == 24) & (days["last_open"].dt.hour == 23)]
    return complete[["close", "known_at"]]


def ema_position(closes: pd.DataFrame) -> dict | None:
    """Dernière clôture journalière complète, son EMA50 et la journée ; None sans 50 clôtures."""
    if len(closes) < EMA_DAYS:
        return None
    value = ema(closes["close"].to_numpy(float))
    if value is None:
        return None
    return {"day": closes.index[-1], "close": float(closes["close"].iloc[-1]), "ema50": value,
            "known_at": closes["known_at"].iloc[-1]}


# --- Lecture des données de la surveillance --------------------------------------------------------------------

def _candles(settings: Settings, symbol: str, now: datetime) -> pd.DataFrame:
    return CandleStore(settings.data_dir).load_since(symbol, "1h", pd.Timestamp(now) - CLOSES_WINDOW)


def structure_and_breadth(settings: Settings, *, now: datetime,
                          loader=None) -> tuple[dict | None, str | None, dict | None, str | None]:
    """(structure BTC, raison si absente, largeur, raison si absente). `loader(symbol)` remplace le magasin (tests)."""
    load = loader or (lambda symbol: _candles(settings, symbol, now))
    moment = pd.Timestamp(now)
    btc = ema_position(daily_closes(load(MARKET), now))
    if btc is None:
        return None, "moins de 50 journées complètes de BTCUSDT en 1 h", None, "structure BTC absente (journée de référence)"
    if btc["day"] < moment.floor("D") - MAX_CLOSE_AGE:
        reason = f"dernière journée complète de BTCUSDT le {btc['day']:%Y-%m-%d} : bougies périmées"
        return None, reason, None, reason
    structure = {"close": round(btc["close"], 8), "ema50": round(btc["ema50"], 8), "below": btc["close"] <= btc["ema50"],
                 "day": f"{btc['day']:%Y-%m-%d}", "known_at": btc["known_at"].isoformat()}
    pairs: dict[str, bool] = {}
    known = btc["known_at"]
    for symbol in dict.fromkeys(settings.data.symbols):
        position = btc if symbol == MARKET else ema_position(daily_closes(load(symbol), now))
        if position is None or position["day"] != btc["day"]:
            continue
        pairs[symbol] = position["close"] > position["ema50"]
        known = max(known, position["known_at"])
    if len(pairs) < BREADTH_MIN_PAIRS:
        return structure, None, None, (f"{len(pairs)} paire(s) éligible(s) au {btc['day']:%Y-%m-%d} "
                                       f"(au moins {BREADTH_MIN_PAIRS})")
    above = sum(pairs.values())
    breadth = {"share": round(above / len(pairs), 4), "above": above, "eligible": len(pairs),
               "configured": len(dict.fromkeys(settings.data.symbols)), "day": structure["day"], "known_at": known.isoformat(),
               "pairs_above": sorted(s for s, up in pairs.items() if up)}
    return structure, None, breadth, None


def f12_journal_path(settings: Settings) -> Path:
    return settings.root / "forward" / f"{SOURCE_TEST}.jsonl"


def history_path(settings: Settings) -> Path:
    return settings.root / "state" / "market_light_vol_history.json"


def journal_path(settings: Settings) -> Path:
    return settings.root / "state" / "market_light.jsonl"


def _f12_entries(settings: Settings, now: datetime) -> list[dict]:
    """Entrées PREVISION de F12 inscrites au plus tard à `now` (lecture seule du journal)."""
    from ..forward.journal import Journal
    moment = pd.Timestamp(now)
    return [e for e in Journal(f12_journal_path(settings)).entries({"PREVISION"}) if pd.Timestamp(e["at"]) <= moment]


def _btc_value(entry_data: dict) -> float | None:
    value = ((((entry_data.get("pairs") or {}).get(MARKET) or {}).get(HORIZON)) or {}).get(MODEL)
    return float(value) if isinstance(value, int | float) and math.isfinite(value) and value > 0 else None


def vol_series(settings: Settings, now: datetime) -> tuple[dict[str, float], dict | None]:
    """(valeurs par jour d'origine « AAAA-MM-JJ », dernière prévision de F12 {origin, value, known_at}). Les valeurs
    de F12 l'emportent sur l'historique recalculé pour un même jour."""
    values: dict[str, float] = {}
    path = history_path(settings)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            values.update({k: float(v) for k, v in (stored.get("values") or {}).items() if v is not None})
        except (OSError, ValueError, TypeError):
            values = {}
    latest = None
    for entry in _f12_entries(settings, now):
        data = entry["data"]
        value = _btc_value(data)
        origin = pd.Timestamp(data["origin"])
        if origin > pd.Timestamp(now):
            continue
        if value is not None:
            values[f"{origin:%Y-%m-%d}"] = value
        latest = {"origin": origin, "value": value, "known_at": pd.Timestamp(entry["at"])}
    return values, latest


def volatility_component(settings: Settings, *, now: datetime) -> tuple[dict | None, str | None]:
    values, latest = vol_series(settings, now)
    if latest is None:
        return None, "aucune prévision à 24 h de F12 pour l'instant"
    origin = latest["origin"]
    if pd.Timestamp(now) - origin > MAX_FORECAST_AGE:
        return None, f"prévision de F12 du {origin:%Y-%m-%d %H:%M} UTC périmée"
    if latest["value"] is None:
        return None, f"prévision de BTCUSDT absente du journal de F12 ({origin:%Y-%m-%d})"
    first = origin - pd.Timedelta(days=RANK_DAYS)
    history = [v for k, v in values.items() if first <= pd.Timestamp(k, tz="UTC") < origin.floor("D")]
    rank = volatility_rank(history, latest["value"])
    if rank is None:
        return None, (f"{len(history)} prévision(s) dans les 365 jours précédents (au moins {RANK_MIN_VALUES}) : "
                      "historique du rang pas encore calculé par la surveillance")
    return {"rank": round(rank, 4), "_rank": rank, "move_24h_pct": round(math.sqrt(latest["value"]) * 100, 2),
            "history_values": len(history), "origin": origin.isoformat(), "known_at": latest["known_at"].isoformat(),
            "model": MODEL, "source": SOURCE_TEST}, None


def current(settings: Settings, *, now: datetime, loader=None) -> dict:
    """Feu du moment, avec chaque composante (valeur, heure à laquelle elle est connue), la phrase d'explication et
    l'heure de calcul. `loader(symbol)` remplace le magasin de bougies (tests)."""
    vol, vol_reason = volatility_component(settings, now=now)
    structure, structure_reason, breadth, breadth_reason = structure_and_breadth(settings, now=now, loader=loader)
    # Décision sur les valeurs EXACTES (les arrondis ne servent qu'à l'affichage : 4/12 doit rester égal à 1/3).
    decision = decide(None if vol is None else vol["_rank"], None if structure is None else structure["below"],
                      None if breadth is None else breadth["above"] / breadth["eligible"])
    if vol is not None:
        vol = {k: v for k, v in vol.items() if k != "_rank"}
    detail = {"volatility": vol_reason or "", "btc_structure": structure_reason or "", "breadth": breadth_reason or ""}
    return {"color": decision["color"], "explanation": explanation(decision, detail), "reasons": decision["reasons"],
            "missing": [{"component": m, "label": COMPONENT_NAMES[m], "reason": detail[m]} for m in decision["missing"]],
            "components": {"volatility": vol, "btc_structure": structure, "breadth": breadth},
            "thresholds": {"rank_red": RANK_RED, "rank_orange": RANK_ORANGE, "breadth_red": round(BREADTH_RED, 4),
                           "breadth_orange": BREADTH_ORANGE, "rank_days": RANK_DAYS, "ema_days": EMA_DAYS},
            "computed_at": pd.Timestamp(now).isoformat(), "rule": RULE, "note": NOTE, "places_orders": False}


# --- Journal quotidien (ajout seul) -----------------------------------------------------------------------------

def record_day(settings: Settings, *, now: datetime) -> dict | None:
    """Inscrit le feu du jour UTC une seule fois : dès que la prévision du jour de F12 est là, au plus tard après
    06:00 UTC (même INCONNU). None si rien à inscrire."""
    moment = pd.Timestamp(now)
    day = f"{moment:%Y-%m-%d}"
    path = journal_path(settings)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                if line.strip() and json.loads(line).get("day") == day:
                    return None
            except ValueError:
                continue
    out = current(settings, now=now)
    vol = out["components"]["volatility"]
    fresh = vol is not None and pd.Timestamp(vol["origin"]).floor("D") == moment.floor("D")
    if not fresh and moment < moment.floor("D") + RECORD_AFTER:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"day": day, "recorded_at": moment.isoformat(), **out}, ensure_ascii=False,
                                default=str) + "\n")
    return {"day": day, "color": out["color"]}


# --- Historique du rang, recalculé une fois (fonctions gelées de F12, lues sans modification) --------------------

def _f12_symbols(settings: Settings) -> list[str]:
    """Paires du groupe d'ajustement de F12 : configuration ∩ liste halal figée à son démarrage (sinon configuration)."""
    from ..forward.journal import Journal
    start = Journal(f12_journal_path(settings)).first("DEMARRAGE")
    allowed = set(((start or {}).get("data") or {}).get("halal", {}).get("symbols") or [])
    symbols = list(dict.fromkeys(settings.data.symbols))
    return [s for s in symbols if s in allowed] if allowed else symbols


def compute_vol_history(settings: Settings, *, now: datetime, days: int = RANK_DAYS) -> dict[str, float]:
    """Prévisions H1_HAR_PROFILE à 24 h de BTC aux origines 00:00 des `days` jours avant aujourd'hui, réajustées le 1er
    de chaque trimestre sur leur seul passé purgé : mêmes fonctions gelées, mêmes paires et même graine que F12."""
    from ..forward import f12
    from ..research import volatility_hourly as vh
    limit = pd.Timestamp(now)
    market = f12.series_until(settings, MARKET, limit)
    if market.empty:
        return {}
    frames = []
    for symbol in dict.fromkeys([MARKET, *_f12_symbols(settings)]):
        own = market if symbol == MARKET else f12.series_until(settings, symbol, limit)
        if not own.empty:
            frames.append(vh.hourly_frame(own, market).assign(symbol=symbol))
    data = pd.concat(frames, ignore_index=True)
    last = limit.floor("D")
    first = last - pd.Timedelta(days=days)
    origins = data["origin"]
    rows = data[(data["symbol"] == MARKET) & (origins >= first) & (origins < last) & (origins.dt.hour == 0)
                & (origins.dt.minute == 0)]
    rows = rows[vh.complete_rows(rows) & (rows["history_days"] >= vh.MIN_HISTORY_DAYS)]
    out: dict[str, float] = {}
    quarters = rows["origin"].dt.tz_convert(None).dt.to_period("Q").dt.start_time.dt.tz_localize("UTC")
    for refit in sorted(set(quarters)):
        block = rows[quarters == refit]
        models = vh.fit_at(data, refit, 24, seed=settings.protocol.seed)
        table = vh.quarter_forecasts(models, block, 24)
        for origin, value in zip(table["origin"], table[MODEL], strict=True):
            if np.isfinite(value) and value > 0:
                out[f"{pd.Timestamp(origin):%Y-%m-%d}"] = float(value)
    return out


def _recent(elapsed: pd.Timedelta) -> bool:
    """Vrai si le dernier calcul est assez récent pour ne pas recommencer (horloge qui recule : on recalcule)."""
    return pd.Timedelta(0) <= elapsed < HISTORY_RETRY


def ensure_vol_history(settings: Settings, *, now: datetime) -> dict | None:
    """Recalcule l'historique du rang quand la fenêtre du jour a moins de RANK_MIN_VALUES valeurs (premier passage,
    ou trou), au plus une fois toutes les HISTORY_RETRY. Écriture atomique ; None si rien à faire. Travail lourd
    (≈ 20 s, ≈ 1 Go sur 16 paires) : appelé par la surveillance seulement, jamais par l'API."""
    values, latest = vol_series(settings, now)
    origin = (latest or {}).get("origin") or pd.Timestamp(now).floor("D")
    first = origin - pd.Timedelta(days=RANK_DAYS)
    present = sum(1 for k in values if first <= pd.Timestamp(k, tz="UTC") < origin.floor("D"))
    if present >= RANK_MIN_VALUES:
        return None
    path = history_path(settings)
    if path.exists():
        try:
            computed_at = pd.Timestamp(json.loads(path.read_text(encoding="utf-8"))["computed_at"])
            if _recent(pd.Timestamp(now) - computed_at):
                return None
        except (OSError, ValueError, KeyError, TypeError):
            pass
    from ..forward.registry import code_fingerprint
    from ..research import volatility_hourly as vh
    computed = compute_vol_history(settings, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"computed_at": pd.Timestamp(now).isoformat(), "model": MODEL, "horizon": HORIZON, "symbol": MARKET,
               "pool": [MARKET, *[s for s in _f12_symbols(settings) if s != MARKET]], "seed": settings.protocol.seed,
               "code": code_fingerprint((vh.hourly_frame, vh.complete_rows, vh.fit_at, vh.quarter_forecasts)),
               "values": dict(sorted(computed.items()))}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return {"values": len(computed), "before": present}
