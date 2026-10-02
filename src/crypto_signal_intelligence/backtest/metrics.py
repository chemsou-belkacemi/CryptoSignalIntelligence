"""Métriques d'un backtest de SIGNAUX INDÉPENDANTS (pas un portefeuille).

- L'espérance est donnée en R (multiple du risque initial) et en % net.
- Borne pessimiste (convention retenue) ET borne optimiste pour les cas ambigus.
- Intervalle de confiance par bootstrap PAR BLOCS DE JOURS consécutifs (date d'entrée) :
  les trades d'un même jour, sur des paires corrélées, et des jours voisins ne sont pas
  indépendants ; des blocs de N trades les traiteraient comme tels et resserreraient l'IC.
  Même méthode que le criblage et le taux de base des signaux externes.
- Drawdown : sur la courbe cumulée des trades CLOS en R (pas mark-to-market,
  limite documentée). Pas de Sharpe annualisé sur une liste de trades irréguliers.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CLOSED = {"TP", "SL", "SL_GAP", "TIMEOUT"}


def day_block_ci95(values: np.ndarray, times: np.ndarray, *, block_days: int, samples: int,
                   seed: int, min_blocks: int = 10) -> tuple[tuple[float, float] | None, int]:
    """IC95 de la moyenne (pondérée par entrée, donc la MÊME que celle affichée) : on tire, avec remise,
    des blocs de `block_days` jours calendaires consécutifs (jours présents dans l'échantillon)."""
    if len(values) == 0:
        return None, 0
    days = pd.to_datetime(times, utc=True).floor("D")
    frame = pd.DataFrame({"day": days, "v": values}).groupby("day")["v"].agg(["sum", "count"])
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    blocks = math.ceil(len(sums) / block_days)
    if blocks < min_blocks:
        return None, blocks
    pad = blocks * block_days - len(sums)
    sums = np.concatenate([sums, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    counts = np.concatenate([counts, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, blocks, size=(samples, blocks))
    draws = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return (round(float(low), 4), round(float(high), 4)), blocks


def day_block_ci(values: np.ndarray, times: np.ndarray, *, block_days: int, samples: int, seed: int,
                 level: float, min_blocks: int = 10) -> tuple[tuple[float, float] | None, int]:
    """Comme `day_block_ci95`, à un niveau quelconque (intervalle bilatéral de niveau `level`)."""
    if len(values) == 0:
        return None, 0
    days = pd.to_datetime(times, utc=True).floor("D")
    frame = pd.DataFrame({"day": days, "v": values}).groupby("day")["v"].agg(["sum", "count"])
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    blocks = math.ceil(len(sums) / block_days)
    if blocks < min_blocks:
        return None, blocks
    pad = blocks * block_days - len(sums)
    sums = np.concatenate([sums, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    counts = np.concatenate([counts, np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, blocks, size=(samples, blocks))
    draws = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(draws, [tail, 100 - tail])
    return (round(float(low), 4), round(float(high), 4)), blocks


def max_drawdown(cumulative: np.ndarray) -> float:
    if not len(cumulative):
        return 0.0
    curve = np.concatenate([[0.0], cumulative])
    return float((curve - np.maximum.accumulate(curve)).min())


def calendar_months(start, end) -> float:
    return max((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds(), 0.0) / (30.44 * 86_400)


def summarize(trades: pd.DataFrame, *, evaluated_bars: int, bars_in_position: int, candidates: int,
              no_trade: dict, bootstrap_block_days: int, bootstrap_samples: int, seed: int,
              months: float | None = None) -> dict:
    """`months` : durée calendaire des décisions évaluées (fréquence des trades par mois)."""
    total = len(trades)
    filled = trades[trades["entry_status"].isin(["FILLED_OPEN", "FILLED_TOUCH"])] if total else trades
    closed = filled[filled["exit_reason"].isin(CLOSED)].sort_values("exit_time") if len(filled) else filled
    censored = int((filled["exit_reason"] == "CENSORED").sum()) if len(filled) else 0
    summary = {
        "label": "INDEPENDENT_SIGNALS",
        "evaluated_bars": int(evaluated_bars),
        "candidates": int(candidates),
        "no_trade_reasons": dict(sorted(no_trade.items(), key=lambda item: -item[1])),
        "orders_simulated": int(total),
        "entries_filled": int(len(filled)),
        "entries_filled_at_open": int((filled["entry_status"] == "FILLED_OPEN").sum()) if len(filled) else 0,
        "entries_expired": int((trades["entry_status"] == "EXPIRED").sum()) if total else 0,
        "trades_closed": int(len(closed)),
        "trades_censored": censored,
        "exposure_pct": round(100 * bars_in_position / evaluated_bars, 2) if evaluated_bars else 0.0,
    }
    if closed.empty:
        return summary | {"verdict_hint": "AUCUN_TRADE_CLOS"}
    r = closed["r_multiple"].to_numpy(float)
    r_opt = closed["r_multiple_optimistic"].to_numpy(float)
    net = closed["net_return"].to_numpy(float)
    gross = closed["gross_return"].to_numpy(float)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    summary |= {
        "win_rate": round(float((r > 0).mean()), 4),
        "expectancy_r": round(float(r.mean()), 4),
        "expectancy_r_optimistic_bound": round(float(r_opt.mean()), 4),
        "expectancy_r_ci95_block_bootstrap": day_block_ci95(
            r, closed["entry_time"].to_numpy(), block_days=bootstrap_block_days, samples=bootstrap_samples,
            seed=seed)[0],
        "ci95_method": f"bootstrap par blocs de {bootstrap_block_days} jours consécutifs (date d'entrée)",
        "median_r": round(float(np.median(r)), 4),
        "avg_net_return_pct": round(float(net.mean() * 100), 4),
        "avg_gross_return_pct": round(float(gross.mean() * 100), 4),
        "sum_net_return_pct_independent": round(float(net.sum() * 100), 2),
        "profit_factor_r": round(float(wins / losses), 3) if losses > 0 else None,
        "max_drawdown_r_closed_trades": round(max_drawdown(np.cumsum(r)), 2),
        "avg_bars_held": round(float(closed["bars_held"].mean()), 1),
        "mae_r_avg": round(float(closed["mae_r"].mean()), 3),
        "mfe_r_avg": round(float(closed["mfe_r"].mean()), 3),
        "ambiguous_trades": int(closed["ambiguous"].sum()),
        "exit_reasons": {k: int(v) for k, v in closed["exit_reason"].value_counts().items()},
        "trades_per_month": round(len(closed) / months, 2) if months else None,
        "by_symbol": _group(closed, "symbol"),
        "by_trend_regime": _group(closed, "trend_regime"),
        "by_volatility_regime": _group(closed, "volatility_regime"),
        "by_year": _group(closed.assign(year=pd.to_datetime(closed["entry_time"], utc=True).dt.year), "year"),
    }
    return summary


def _group(frame: pd.DataFrame, column: str) -> dict:
    out = {}
    for key, group in frame.groupby(column):
        out[str(key)] = {"trades": int(len(group)), "expectancy_r": round(float(group["r_multiple"].mean()), 3),
                         "win_rate": round(float((group["r_multiple"] > 0).mean()), 3)}
    return out


def buy_and_hold(candles: pd.DataFrame, start, end, fee_bps: float) -> dict:
    """Référence : achat au premier close de la période, valorisation au dernier."""
    window = candles[(candles["open_time"] >= pd.Timestamp(start)) & (candles["open_time"] <= pd.Timestamp(end))]
    if len(window) < 2:
        return {}
    close = window["close"].to_numpy(float)
    fee = fee_bps / 1e4
    total = close[-1] * (1 - fee) / (close[0] * (1 + fee)) - 1
    curve = close / close[0]
    drawdown = float((curve / np.maximum.accumulate(curve) - 1).min())
    return {"return_pct": round(total * 100, 2), "max_drawdown_pct_mark_to_market": round(drawdown * 100, 2),
            "exposure_pct": 100.0}
