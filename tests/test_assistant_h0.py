"""Contrôle de l'assistant SOUS L'HYPOTHÈSE NULLE (docs/ASSISTANT.md § limites) : marches aléatoires 1 h (martingale en
rendements simples, aucune information), détecteur et gestion exacts de l'assistant, placebos exacts. Sous H0,
l'espérance du R net est celle des frais, et l'excès sur les placebos doit rester proche de 0 : ce test MESURE le biais
de la méthode (placebos arrière qui partagent le chemin de la configuration, placebos avant), il ne le corrige pas.

Test LENT (≈ 30 min) : lancé seulement avec `--lents`. Les chiffres obtenus le 2026-10-09 sont inscrits dans
ASSISTANT.md. Hors H0 (ne dépendent pas du chemin de prix) : feu, BTC, carnet, news, macro, discipline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.assistant import evaluate as ev
from crypto_signal_intelligence.assistant import rules as R

pytestmark = pytest.mark.slow

PAIRS, DAYS, SIGMA, SEED = 40, 400, 0.004, 20261009
START = pd.Timestamp("2025-01-01", tz="UTC")


def random_walk(rng: np.random.Generator, symbol: str) -> pd.DataFrame:
    n = DAYS * 24
    times = pd.date_range(START, periods=n, freq="h")
    close = 100.0 * np.cumprod(1 + rng.normal(0.0, SIGMA, n))         # martingale : E[r] = 0 en rendements simples
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, SIGMA / 2, n)) * close
    frame = pd.DataFrame({"open_time": times, "open": open_, "high": np.maximum(open_, close) + wick,
                          "low": np.minimum(open_, close) - wick, "close": close,
                          "quote_volume": rng.lognormal(10, 0.4, n)})
    frame["available_at"] = frame["open_time"] + R.HOUR + pd.Timedelta(seconds=2)
    return frame


def detect(h1: pd.DataFrame, bars: pd.DataFrame, symbol: str, at: pd.Timestamp) -> dict | None:
    """Même chemin que `evaluate` pour ce qui dépend des prix : lecture, niveaux, stop/volatilité réalisée, TP2 net."""
    sub = h1[(h1["open_time"] >= at - pd.Timedelta(days=R.HISTORY_DAYS)) & (h1["open_time"] + R.HOUR <= at)]
    read = ev.read_pair(sub, at)
    setup = read.get("setup")
    if setup is None or not setup["ok"]:
        return None
    levels = R.targets(setup["entry"], setup["stop"], read["resistance"], None)
    if not levels["ok"] or not R.stop_versus_volatility(levels["entry"], levels["stop"], R.realized_move_24h_pct(sub))["ok"]:
        return None
    if R.tp2_net_r(levels["entry"], levels["stop"], levels["tp2"], symbol) < R.MIN_TP2_R_NET:
        return None
    ident = ev.call_id(symbol, at)
    geometry = {"entry": levels["entry"], "stop": levels["stop"], "tp1": levels["tp1"], "tp2": levels["tp2"], "symbol": symbol}
    trade = R.simulate(bars, entry_at=at, scenario="central", **geometry)
    if trade["status"] != "RESOLU":
        return None
    offsets = R.placebo_offsets(ident)
    placebos = [R.placebo(bars, at=at, offset_h=o, scenario="central", **geometry) for o in offsets]
    values = [p.get("r") if p["status"] == "RESOLU" else None for p in placebos]
    back = [v for v, o in zip(values, offsets, strict=True) if v is not None and o < 0]
    forward = [v for v, o in zip(values, offsets, strict=True) if v is not None and o > 0]
    usable = back + forward
    if not usable:
        return None
    return {"regime": read["regime"], "r": trade["r"], "excess": trade["r"] - float(np.mean(usable)),
            "excess_back": trade["r"] - float(np.mean(back)) if back else np.nan,
            "excess_forward": trade["r"] - float(np.mean(forward)) if forward else np.nan}


def test_h0_random_walk_control():
    rng = np.random.default_rng(SEED)
    rows = []
    for i in range(PAIRS):
        symbol = f"H{i:02d}USDT"
        h1 = random_walk(rng, symbol)
        bars = h1[["open_time", "open", "high", "low", "close"]]
        first = START + pd.Timedelta(days=R.MIN_DAYS + 5)
        last = START + pd.Timedelta(days=DAYS) - R.PLACEBO_MAX_H * R.HOUR - R.MAX_HOLD - R.DAY
        for at in pd.date_range(first.ceil("4h"), last, freq="4h"):
            out = detect(h1, bars, symbol, at)
            if out is not None:
                rows.append(out)
    table = pd.DataFrame(rows)
    n = len(table)
    summary = {"appels": n, "r_moyen": round(float(table["r"].mean()), 4), "exces_global": round(float(table["excess"].mean()), 4),
               "exces_arriere": round(float(table["excess_back"].mean()), 4), "exces_avant": round(float(table["excess_forward"].mean()), 4),
               "ecart_type_r": round(float(table["r"].std(ddof=1)), 4), "par_regime": table["regime"].value_counts().to_dict(),
               "ic95_exces_global": [round(float(table["excess"].mean() - 1.96 * table["excess"].std(ddof=1) / np.sqrt(n)), 4),
                                     round(float(table["excess"].mean() + 1.96 * table["excess"].std(ddof=1) / np.sqrt(n)), 4)]}
    print("\nCONTROLE H0 :", summary)
    assert n >= 200, f"{n} appels synthétiques seulement (au moins 200 attendus)"
    assert np.isfinite(summary["exces_global"])
