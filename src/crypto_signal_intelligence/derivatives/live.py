"""Positionnement du moment sur le marché à terme USDⓈ-M, pour le tableau de bord (données publiques).

Chaque valeur est donnée avec son rang dans son propre historique récent (part des valeurs de la fenêtre
strictement inférieures). C'est une DESCRIPTION : rien ici ne décide un signal, et le pouvoir prédictif de
ces données est jugé à part, par un criblage déclaré avant exécution (docs/DERIVATIVES.md).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd

from ..data.http import HttpError, PublicHttpClient

HOUR_MS = 3_600_000
MIN_RANK_VALUES = 10
NOTE = ("Positionnement du moment sur le marché à terme (données publiques de Binance) : information, pas un "
        "signal. CSI ne négocie aucun contrat à terme ; le pouvoir prédictif de ces chiffres est évalué à part "
        "(docs/DERIVATIVES.md).")
DEFINITIONS = {
    "financement": "Taux payé à chaque règlement (toutes les 8 h en général) par les positions acheteuses aux "
                   "vendeuses s'il est positif, l'inverse s'il est négatif. Positif : marché à terme penché à "
                   "l'achat avec levier.",
    "prime": "Écart du prix du contrat perpétuel (prix de marque) sur l'indice Spot. Négatif : le perpétuel "
             "s'échange sous le Spot, signe de pression vendeuse sur le marché à terme.",
    "interet_ouvert": "Valeur en dollars de tous les contrats ouverts. Hausse : nouvelles positions à levier ; "
                      "forte baisse : positions fermées ou liquidées.",
    "comptes": "Comptes acheteurs / comptes vendeurs, sur tous les comptes du marché à terme de cette paire.",
    "gros_comptes": "Positions acheteuses / vendeuses des plus gros comptes (en taille de position).",
    "agressifs": "Volume des achats agressifs / volume des ventes agressives (ordres au marché) sur 24 h.",
    "rang": "Part des valeurs de la fenêtre indiquée strictement inférieures à la valeur actuelle : 0 % = plus "
            "basse de la fenêtre, 100 % = plus haute. Une description du passé récent, pas une probabilité.",
}


def rank(values, current) -> float | None:
    """Part des valeurs (finies) strictement inférieures à `current` ; None si moins de 10 valeurs."""
    window = np.asarray(values, dtype=float)
    window = window[np.isfinite(window)]
    if len(window) < MIN_RANK_VALUES or current is None or not np.isfinite(current):
        return None
    return round(float((window < current).mean()), 3)


def _number(value) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value), 8)


def _closed(frame: pd.DataFrame, now_ms: int, time_column: str, period_ms: int) -> pd.DataFrame:
    """Lignes dont la période est terminée à `now` (une période commence à son horodatage)."""
    return frame[frame[time_column] + period_ms <= now_ms].sort_values(time_column).reset_index(drop=True)


def funding_section(client: PublicHttpClient, symbol: str, now_ms: int) -> dict:
    rows = client.get_json("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": now_ms - 90 * 24 * HOUR_MS,
                                                    "limit": 1000})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"unavailable": "aucun règlement de financement publié sur 90 jours"}
    frame["time"] = frame["fundingTime"].astype("int64")
    frame["rate"] = pd.to_numeric(frame["fundingRate"], errors="coerce")
    frame = frame[frame["time"] <= now_ms].sort_values("time").reset_index(drop=True)
    gaps = np.diff(frame["time"].to_numpy()[-10:]) / HOUR_MS
    interval_h = int(round(float(np.median(gaps)))) if len(gaps) else 8
    series = pd.Series(frame["rate"].to_numpy(), index=pd.to_datetime(frame["time"], unit="ms", utc=True))
    mean_3d = series.rolling("72h").mean()
    complete = mean_3d[mean_3d.index >= series.index[0] + pd.Timedelta(hours=72)]   # fenêtres de 3 jours pleines
    estimate = client.get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    per_year = 24 / max(interval_h, 1) * 365
    return {"last_rate": _number(series.iloc[-1]), "last_at": series.index[-1].isoformat(), "interval_hours": interval_h,
            "estimated_next_rate": _number(pd.to_numeric(estimate.get("lastFundingRate"), errors="coerce")),
            "next_at": pd.Timestamp(int(estimate["nextFundingTime"]), unit="ms", tz="UTC").isoformat()
            if estimate.get("nextFundingTime") else None,
            "mean_3d": _number(mean_3d.iloc[-1]), "annualized_3d": _number(mean_3d.iloc[-1] * per_year),
            "rank_mean_3d": rank(complete.iloc[:-1].to_numpy(), mean_3d.iloc[-1]), "window_days": 90,
            "settlements": int(len(series))}


def premium_section(client: PublicHttpClient, symbol: str, now_ms: int) -> dict:
    index = client.get_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    mark, spot = (pd.to_numeric(index.get(k), errors="coerce") for k in ("markPrice", "indexPrice"))
    rows = client.get_json("/fapi/v1/premiumIndexKlines", {"symbol": symbol, "interval": "1h", "limit": 720})
    frame = pd.DataFrame([r[:7] for r in rows], columns=["open_time", "open", "high", "low", "close", "volume",
                                                          "close_time"])
    frame["open_time"] = frame["open_time"].astype("int64")
    frame = _closed(frame, now_ms, "open_time", HOUR_MS)
    closes = pd.to_numeric(frame["close"], errors="coerce")
    mean_24h = closes.rolling(24, min_periods=24).mean()
    now_premium = float(mark / spot - 1) if spot and np.isfinite(spot) and spot > 0 else float("nan")
    return {"now": _number(now_premium), "mean_24h": _number(mean_24h.iloc[-1]) if len(mean_24h) else None,
            "rank_mean_24h": rank(mean_24h.iloc[:-1].to_numpy(), mean_24h.iloc[-1]) if len(mean_24h) else None,
            "window_days": round(len(frame) / 24, 1)}


def open_interest_section(client: PublicHttpClient, symbol: str, now_ms: int) -> dict:
    rows = client.get_json("/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 500})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"unavailable": "aucune donnée d'intérêt ouvert"}
    frame["timestamp"] = frame["timestamp"].astype("int64")
    frame = frame[frame["timestamp"] <= now_ms].sort_values("timestamp").reset_index(drop=True)
    value = pd.to_numeric(frame["sumOpenInterestValue"], errors="coerce")
    change_24h = value / value.shift(24) - 1
    return {"value_usd": _number(value.iloc[-1]), "at": pd.Timestamp(int(frame["timestamp"].iloc[-1]), unit="ms",
                                                                      tz="UTC").isoformat(),
            "change_24h": _number(change_24h.iloc[-1]),
            "change_7d": _number(value.iloc[-1] / value.iloc[-169] - 1) if len(value) > 168 else None,
            "rank_change_24h": rank(change_24h.iloc[:-1].to_numpy(), change_24h.iloc[-1]),
            "window_days": round(len(frame) / 24, 1)}


def ratio_section(client: PublicHttpClient, path: str, symbol: str, now_ms: int) -> dict:
    rows = client.get_json(path, {"symbol": symbol, "period": "1h", "limit": 500})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"unavailable": "aucune donnée"}
    frame["timestamp"] = frame["timestamp"].astype("int64")
    frame = frame[frame["timestamp"] <= now_ms].sort_values("timestamp").reset_index(drop=True)
    ratio = pd.to_numeric(frame["longShortRatio"], errors="coerce")
    long_share = pd.to_numeric(frame["longAccount"], errors="coerce")
    return {"ratio": _number(ratio.iloc[-1]), "long_share": _number(long_share.iloc[-1]),
            "rank": rank(ratio.iloc[:-1].to_numpy(), ratio.iloc[-1]), "window_days": round(len(frame) / 24, 1)}


def taker_section(client: PublicHttpClient, symbol: str, now_ms: int) -> dict:
    rows = client.get_json("/futures/data/takerlongshortRatio", {"symbol": symbol, "period": "1h", "limit": 500})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"unavailable": "aucune donnée"}
    frame["timestamp"] = frame["timestamp"].astype("int64")
    frame = _closed(frame, now_ms, "timestamp", HOUR_MS)
    buys = pd.to_numeric(frame["buyVol"], errors="coerce").rolling(24, min_periods=24).sum()
    sells = pd.to_numeric(frame["sellVol"], errors="coerce").rolling(24, min_periods=24).sum()
    ratio_24h = buys / sells.where(sells > 0)
    return {"ratio_24h": _number(ratio_24h.iloc[-1]) if len(ratio_24h) else None,
            "rank": rank(ratio_24h.iloc[:-1].to_numpy(), ratio_24h.iloc[-1]) if len(ratio_24h) else None,
            "window_days": round(len(frame) / 24, 1)}


def snapshot(client: PublicHttpClient, symbol: str, *, now: datetime) -> dict:
    """Toutes les sections ; une section en échec est marquée indisponible sans bloquer les autres."""
    now_ms = int(pd.Timestamp(now).timestamp() * 1000)
    sections: dict[str, Callable[[], dict]] = {
        "funding": lambda: funding_section(client, symbol, now_ms),
        "premium": lambda: premium_section(client, symbol, now_ms),
        "open_interest": lambda: open_interest_section(client, symbol, now_ms),
        "accounts": lambda: ratio_section(client, "/futures/data/globalLongShortAccountRatio", symbol, now_ms),
        "top_positions": lambda: ratio_section(client, "/futures/data/topLongShortPositionRatio", symbol, now_ms),
        "taker": lambda: taker_section(client, symbol, now_ms),
    }
    out: dict = {"symbol": symbol, "as_of": pd.Timestamp(now).isoformat(),
                 "source": "Binance USDⓈ-M, données publiques (fapi.binance.com)", "note": NOTE,
                 "definitions": DEFINITIONS}
    for name, fetch in sections.items():
        try:
            out[name] = fetch()
        except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
            out[name] = {"unavailable": f"indisponible ({type(exc).__name__})"}
    out["available"] = any("unavailable" not in out[name] for name in sections)
    return out
