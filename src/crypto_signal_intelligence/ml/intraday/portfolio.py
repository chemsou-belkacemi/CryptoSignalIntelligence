"""Couche stratégie et portefeuille (docs/ML_INTRADAY.md §5-6), indépendante du modèle.

Entrées : des CANDIDATS (paire, heure de décision, nombre de bougies jusqu'à la sortie prévue,
rendement net des coûts, score de priorité = espérance nette). Les limites d'exposition viennent du
registre CENTRAL `risk.exposure` (communes à toutes les stratégies, positions déjà ouvertes comprises).
Sortie à l'heure prévue par la règle de sortie de la cible (horizon fixe ou triple barrière).

Limite déclarée : le capital est suivi en RÉALISÉ (positions valorisées à leur sortie, au plus 4 h
plus tard) ; la perte maximale journalière ignore les variations intra-position.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...risk.exposure import ExposureBook, Position, RiskLimits

STRATEGY = "ML_INTRADAY"
STEP_NS = 15 * 60 * 10**9
TRADE_COLUMNS = ["symbol", "entry_time", "exit_time", "notional", "net", "pnl", "candidate"]


def run_book(candidates: pd.DataFrame, limits: RiskLimits, *, strategy: str = STRATEGY,
             existing: tuple[Position, ...] = ()) -> tuple[pd.DataFrame, np.ndarray]:
    """Trades exécutés et statut de CHAQUE candidat (ENTER ou raison du refus par les limites).

    Colonnes attendues : symbol, decision_time, bars (bougies jusqu'à la sortie), net, score.
    Priorité : ordre chronologique, puis score décroissant entre candidats simultanés.
    """
    status = np.full(len(candidates), "", dtype=object)
    if candidates.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS), status
    decision_ns = pd.to_datetime(candidates["decision_time"], utc=True).to_numpy("datetime64[ns]").astype(np.int64)
    order = np.lexsort((-candidates["score"].to_numpy(float), decision_ns))
    symbols = candidates["symbol"].to_numpy(object)
    bars = candidates["bars"].to_numpy(np.int64)
    nets = candidates["net"].to_numpy(float)
    book = ExposureBook(limits, positions=list(existing))
    for i in order:
        moment = int(decision_ns[i])
        _, reason = book.try_open(str(symbols[i]), strategy, moment, moment + int(bars[i]) * STEP_NS,
                                  float(nets[i]), int(i))
        status[i] = reason or "ENTER"
    book.finish()
    mine = [p for p in book.closed if p.strategy == strategy and p.tag >= 0]
    if not mine:
        return pd.DataFrame(columns=TRADE_COLUMNS), status
    trades = pd.DataFrame({
        "symbol": [p.symbol for p in mine],
        "entry_time": pd.to_datetime([p.entry_ns for p in mine], utc=True),
        "exit_time": pd.to_datetime([p.exit_ns for p in mine], utc=True),
        "notional": [p.notional for p in mine], "net": [p.net for p in mine],
        "candidate": [p.tag for p in mine]})
    trades["pnl"] = trades["notional"] * trades["net"]
    return trades.sort_values("entry_time").reset_index(drop=True)[TRADE_COLUMNS], status


def simulate(candidates: pd.DataFrame, limits: RiskLimits, **kwargs) -> pd.DataFrame:
    return run_book(candidates, limits, **kwargs)[0]


def daily_returns(trades: pd.DataFrame, start, end) -> pd.Series:
    """Rendements journaliers du capital réalisé (jours sans sortie : 0)."""
    days = pd.date_range(pd.Timestamp(start).floor("D"), pd.Timestamp(end).floor("D"), freq="D")
    if trades.empty:
        return pd.Series(0.0, index=days)
    pnl = trades.groupby(pd.to_datetime(trades["exit_time"], utc=True).dt.floor("D"))["pnl"].sum()
    pnl = pnl.reindex(days, fill_value=0.0)
    equity = 1.0 + pnl.cumsum()
    previous = equity.shift(1).fillna(1.0)
    return pnl / previous


def sharpe(returns: np.ndarray) -> float | None:
    returns = np.asarray(returns, dtype=float)
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    return float(returns.mean() / std * np.sqrt(365)) if std > 0 else None


def series_metrics(returns: pd.Series) -> dict:
    """Rendement, CAGR, perte maximale et Sharpe annualisé (365 jours) d'une série journalière."""
    if returns.empty:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": None}
    curve = (1 + returns).cumprod()
    years = max(len(returns) / 365, 1e-9)
    value = sharpe(returns.to_numpy())
    return {"total_return": round(float(curve.iloc[-1] - 1), 5),
            "cagr": round(float(curve.iloc[-1] ** (1 / years) - 1), 5),
            "max_drawdown": round(float((curve / curve.cummax() - 1).min()), 5),
            "sharpe": round(value, 4) if value is not None else None}


def block_bootstrap(returns: np.ndarray, *, block_days: int, samples: int, seed: int) -> dict:
    """IC95 du Sharpe et du rendement total par bootstrap à BLOCS CIRCULAIRES de jours consécutifs
    (préserve l'autocorrélation et les grappes de volatilité à l'échelle du bloc)."""
    returns = np.asarray(returns, dtype=float)
    n = len(returns)
    if n < 2 * block_days:
        return {"sharpe_ci95": None, "total_return_ci95": None, "blocks": n // max(block_days, 1)}
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block_days))
    starts = rng.integers(0, n, size=(samples, blocks))
    index = (starts[:, :, None] + np.arange(block_days)[None, None, :]).reshape(samples, -1)[:, :n] % n
    draws = returns[index]
    std = draws.std(axis=1, ddof=1)
    sharpes = np.where(std > 0, draws.mean(axis=1) / np.where(std > 0, std, 1) * np.sqrt(365), 0.0)
    totals = np.prod(1 + draws, axis=1) - 1
    return {"sharpe_ci95": [round(float(v), 4) for v in np.percentile(sharpes, [2.5, 97.5])],
            "total_return_ci95": [round(float(v), 5) for v in np.percentile(totals, [2.5, 97.5])],
            "blocks": blocks}


def trade_mean_ci(net: np.ndarray, times, *, block_days: int, samples: int, seed: int,
                  min_blocks: int = 10) -> list[float] | None:
    """IC95 du gain moyen par trade : tirage avec remise de blocs de `block_days` jours AYANT des trades,
    consécutifs (les jours sans trade ne comptent pas dans la longueur d'un bloc)."""
    net = np.asarray(net, dtype=float)
    if len(net) == 0:
        return None
    days = pd.to_datetime(pd.Series(times), utc=True).dt.floor("D")
    frame = pd.DataFrame({"day": days.to_numpy(), "v": net}).groupby("day")["v"].agg(["sum", "count"])
    blocks = int(np.ceil(len(frame) / block_days))
    if blocks < min_blocks:
        return None
    pad = blocks * block_days - len(frame)
    sums = np.concatenate([frame["sum"].to_numpy(), np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    counts = np.concatenate([frame["count"].to_numpy(), np.zeros(pad)]).reshape(blocks, block_days).sum(axis=1)
    picks = np.random.default_rng(seed).integers(0, blocks, size=(samples, blocks))
    draws = sums[picks].sum(axis=1) / np.maximum(counts[picks].sum(axis=1), 1)
    return [round(float(v), 6) for v in np.percentile(draws, [2.5, 97.5])]


def exceptional_dependence(trades: pd.DataFrame) -> dict:
    """Dépendance aux trades exceptionnels : résultats sans le 1 % des meilleurs, sans les 10 meilleurs."""
    if trades.empty:
        return {"avg_net_without_top1pct": None, "pnl_without_top1pct": None, "avg_net_without_top10": None,
                "pnl_without_top10": None, "top10_share_of_gross_gain": None}
    ranked = trades.sort_values("pnl", ascending=False)
    top1 = max(1, int(np.ceil(0.01 * len(ranked))))
    gross_gain = float(ranked.loc[ranked["pnl"] > 0, "pnl"].sum())
    rest1, rest10 = ranked.iloc[top1:], ranked.iloc[10:]
    return {
        "avg_net_without_top1pct": round(float(rest1["net"].mean()), 6) if len(rest1) else None,
        "pnl_without_top1pct": round(float(rest1["pnl"].sum()), 6),
        "avg_net_without_top10": round(float(rest10["net"].mean()), 6) if len(rest10) else None,
        "pnl_without_top10": round(float(rest10["pnl"].sum()), 6),
        "top10_share_of_gross_gain": (round(float(ranked["pnl"].head(10).clip(lower=0).sum() / gross_gain), 4)
                                      if gross_gain > 0 else None),
    }


def breakdown(trades: pd.DataFrame, extra_keys: tuple[str, ...] = ()) -> dict:
    """Résultats par année, trimestre, paire et par colonnes de contexte de marché à la décision
    présentes dans `trades` (`extra_keys`, par exemple tendance 1 h, volatilité, BTC sur 24 h)."""
    if trades.empty:
        return {}
    frame = trades.copy()
    entry = pd.to_datetime(frame["entry_time"], utc=True).dt.tz_localize(None)
    frame["annee"], frame["trimestre"] = entry.dt.year.astype(str), entry.dt.to_period("Q").astype(str)

    def group(key: str) -> dict:
        g = frame.groupby(key, observed=True)
        table = pd.DataFrame({"trades": g.size(), "avg_net": g["net"].mean(), "pnl": g["pnl"].sum(),
                              "win_rate": g["net"].apply(lambda s: float((s > 0).mean()))})
        return {str(k): {"trades": int(r.trades), "avg_net": round(float(r.avg_net), 6),
                         "pnl": round(float(r.pnl), 6), "win_rate": round(float(r.win_rate), 4)}
                for k, r in table.iterrows()}

    return {key: group(key) for key in ("annee", "trimestre", "symbol", *extra_keys)}


def buy_and_hold(daily_closes: pd.DataFrame, start, end) -> dict:
    """Références : BTC acheté et gardé, univers à parts égales acheté et gardé (sans rééquilibrage)."""
    window = daily_closes[(daily_closes.index >= pd.Timestamp(start).floor("D"))
                          & (daily_closes.index <= pd.Timestamp(end))]
    out: dict = {"cash": {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": None}}
    if "BTCUSDT" in window and window["BTCUSDT"].notna().sum() > 1:
        out["btc_buy_and_hold"] = series_metrics(window["BTCUSDT"].dropna().pct_change().dropna())
    if len(window) > 1:
        normalized = window / window.bfill().iloc[0]
        equal = normalized.mean(axis=1).dropna()
        if len(equal) > 1:
            out["equal_weight_buy_and_hold"] = series_metrics(equal.pct_change().dropna())
    return out


def random_entries(pool: pd.DataFrame, count: int, limits: RiskLimits, start, end, *, draws: int, seed: int) -> dict:
    """Distribution du Sharpe d'entrées tirées au hasard (même nombre de candidats, mêmes limites)."""
    if pool.empty or count == 0:
        return {"draws": 0, "sharpe_p50": None, "sharpe_p95": None}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        pick = pool.iloc[rng.choice(len(pool), size=min(count, len(pool)), replace=False)]
        trades = simulate(pick.assign(score=rng.random(len(pick))), limits)
        value = sharpe(daily_returns(trades, start, end).to_numpy())
        values.append(value if value is not None else 0.0)
    return {"draws": draws, "sharpe_p50": round(float(np.percentile(values, 50)), 4),
            "sharpe_p95": round(float(np.percentile(values, 95)), 4)}
