"""Volatilité prévue EN SERVICE : une prévision par jour et par paire, à 1, 3 et 7 jours (docs/VOLATILITY.md §13).

Les modèles sont ceux que le protocole a retenus sur DEVELOPMENT (`VOL-20261001T194742Z-926ff2`) : LightGBM
commun à 1 et 3 jours, HAR commun + BTC à 7 jours. Même code que la recherche (`research/volatility.py` :
lignes journalières, variables, purge, réajustement le 1er du mois) ; seule la source change : les bougies 1 h du
magasin de la surveillance, jusqu'à maintenant.

Ce que le chiffre veut dire : « mouvement typique » = écart-type prévu du rendement sur H jours (racine de la
variance réalisée prévue), en %. Il dit l'AMPLEUR attendue, jamais le sens. Résultat utile sur DEVELOPMENT,
pas encore confirmé sur la période finale : information, aucune décision n'en dépend.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..data.store import CandleStore
from ..research import volatility as vol

log = logging.getLogger("csi.volatility")
SOURCE_RUN = "VOL-20261001T194742Z-926ff2"
SELECTED = {1: "M5_LGBM_POOLED", 3: "M5_LGBM_POOLED", 7: "M4_HAR_POOLED_BTC"}
BASELINE = vol.BASELINE
FILE_NAME = "volatility.json"
MARKET = "BTCUSDT"
READY_DELAY = pd.Timedelta(minutes=10)      # après 00:00 UTC : le temps que la bougie de 23:00 soit stockée
NOTE = ("Mouvement typique attendu (un écart-type du rendement sur la période), prévu par les modèles retenus par le "
        "protocole de volatilité sur DEVELOPMENT ; non confirmé sur la période finale. Il dit l'ampleur, jamais le sens.")
RETRY_AFTER = pd.Timedelta(minutes=15)
_LOCK = threading.Lock()
_ATTEMPT: dict[str, pd.Timestamp] = {}


def state_path(settings: Settings) -> Path:
    return settings.root / "state" / FILE_NAME


def expected_origin(now: datetime) -> pd.Timestamp:
    """Dernière origine journalière (00:00 UTC) dont la prévision peut exister à `now`."""
    moment = pd.Timestamp(now)
    moment = moment.tz_localize("UTC") if moment.tzinfo is None else moment.tz_convert("UTC")
    return (moment - READY_DELAY).floor("D")


def _pct(variance: float) -> float | None:
    return round(math.sqrt(variance) * 100, 2) if math.isfinite(variance) and variance > 0 else None


def compute(settings: Settings, *, now: datetime, symbols: list[str] | None = None) -> dict:
    """Prévisions à la dernière origine connue. Une paire sans 400 jours d'historique, ou dont les variables
    manquent ce jour-là (trou de données), n'a pas de prévision : c'est écrit, jamais remplacé par une valeur."""
    from ..external.universe import universe_symbols
    symbols = list(symbols or universe_symbols(settings))
    store = CandleStore(settings.data_dir)
    limit = pd.Timestamp(now)

    def series(symbol: str) -> pd.DataFrame:
        frame = store.load(symbol, "1h")
        if frame.empty:
            return frame
        return frame.loc[frame["available_at"] <= limit, list(vol.READ_COLUMNS)].reset_index(drop=True)

    market = series(MARKET)
    if market.empty:
        raise RuntimeError("bougies 1 h de BTCUSDT absentes : aucune prévision possible")
    frames, skipped = {}, {}
    for symbol in dict.fromkeys([MARKET, *symbols]):
        own = market if symbol == MARKET else series(symbol)
        if own.empty:
            skipped[symbol] = "aucune bougie 1 h"
            continue
        frames[symbol] = vol.daily_frame(own, market).assign(symbol=symbol)
    data = pd.concat(frames.values(), ignore_index=True)
    origin = min(expected_origin(now), data["origin"].max())
    refit = origin.replace(day=1)
    today = data[(data["origin"] == origin)]
    usable = today[vol.complete_rows(today) & (today["history_days"] >= vol.MIN_HISTORY_DAYS)]
    pairs: dict[str, dict] = {}
    for symbol in symbols:
        row = today[today["symbol"] == symbol]
        if symbol in skipped:
            pairs[symbol] = {"available": False, "reason": skipped[symbol]}
        elif row.empty:
            pairs[symbol] = {"available": False, "reason": "pas de bougie de 23:00 pour ce jour"}
        elif float(row["history_days"].iloc[0]) < vol.MIN_HISTORY_DAYS:
            pairs[symbol] = {"available": False, "reason": f"moins de {vol.MIN_HISTORY_DAYS} jours d'historique"}
        elif symbol not in set(usable["symbol"]):
            pairs[symbol] = {"available": False, "reason": "variables incomplètes (trou de données récent)"}
        else:
            pairs[symbol] = {"available": True, "horizons": {}}
    models_used = {}
    for horizon, name in SELECTED.items():
        if usable.empty:
            break
        fitted = vol.fit_at(data, refit, horizon, seed=settings.protocol.seed)
        table = vol.month_forecasts(fitted, usable, horizon)
        models_used[str(horizon)] = {"model": name, "training_rows": fitted.rows, "refit": str(refit)}
        for symbol, forecast, recent in zip(table["symbol"], table[name], table[BASELINE], strict=True):
            entry = pairs.get(symbol)
            if entry is None or not entry.get("available"):
                continue
            move, usual = _pct(float(forecast)), _pct(float(recent))
            entry["horizons"][str(horizon)] = {
                "move_pct": move, "recent_move_pct": usual,
                "ratio": round(move / usual, 2) if move and usual else None}
    for entry in pairs.values():
        if entry.get("available") and not any(h.get("move_pct") for h in entry["horizons"].values()):
            entry.update(available=False, reason="modèle non ajusté (trop peu de lignes d'entraînement)")
    return {"origin": origin.isoformat(), "generated_at": pd.Timestamp(now).isoformat(), "source_run": SOURCE_RUN,
            "models": models_used, "model_names": {str(h): vol.MODEL_TEXT[m] for h, m in SELECTED.items()},
            "note": NOTE, "pairs": pairs}


def read(settings: Settings) -> dict | None:
    path = state_path(settings)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure(settings: Settings, *, now: datetime, force: bool = False) -> dict | None:
    """Prévisions du jour : relues si elles sont à jour (bonne origine, toutes les paires de l'univers), sinon
    recalculées et écrites de façon atomique. Un seul calcul à la fois, et au plus une tentative par quart
    d'heure : quand les bougies du jour ne sont pas encore là, on garde l'existant sans recalculer en boucle."""
    from ..external.universe import universe_symbols
    wanted = expected_origin(now).isoformat()

    def fresh_enough(current: dict | None) -> bool:
        return (current is not None and current.get("origin") == wanted
                and set(universe_symbols(settings)) <= set(current.get("pairs", {})))

    current = read(settings)
    if fresh_enough(current) and not force:
        return current
    with _LOCK:
        current = read(settings)
        if fresh_enough(current) and not force:
            return current
        moment = pd.Timestamp(now)
        last = _ATTEMPT.get(str(settings.root))
        if not force and last is not None and moment - last < RETRY_AFTER:
            return current
        _ATTEMPT[str(settings.root)] = moment
        fresh = compute(settings, now=now)
        path = state_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        log.info("volatilité prévue : %s paires à l'origine %s", sum(p.get("available", False)
                                                                    for p in fresh["pairs"].values()), fresh["origin"])
        return fresh


def for_symbol(settings: Settings, symbol: str) -> dict | None:
    """Prévision déjà calculée d'une paire (jamais de calcul ici : lecture du fichier du jour)."""
    current = read(settings)
    if current is None:
        return None
    entry = current.get("pairs", {}).get(symbol)
    return None if entry is None else entry | {"origin": current["origin"]}


def distances(forecast: dict | None, *, tp1_pct: float, stop_pct: float) -> dict | None:
    """Distances d'un signal rapportées au mouvement typique prévu : TP1 et stop « en nombre de mouvements » à 1, 3
    et 7 jours. Descriptif : un TP1 à 0,5 mouvement est proche, un stop à 3 mouvements est très large."""
    if not forecast or not forecast.get("available"):
        return None
    out = {}
    for horizon, values in forecast["horizons"].items():
        move = values.get("move_pct")
        if move:
            out[horizon] = {"move_pct": move, "tp1_moves": round(tp1_pct / move, 2), "stop_moves": round(stop_pct / move, 2)}
    return out or None
