"""Gestion « stop suiveur » du propriétaire (docs/EXTERNAL_SIGNALS.md, « Gestion avec stop suiveur »).

Règle déclarée par le propriétaire le 2026-10-02 :
- les ventes se font à chaque objectif, sur les `tp_count` premiers objectifs du signal, parts « early »
  (n, n−1, … 1 : 33 / 27 / 20 / 13 / 7 % pour cinq objectifs), comme le réglage de BinanceSpotManager ;
- TP1 touché → le stop monte à l'ENTRÉE 1 du signal (son prix écrit, pas le prix obtenu) ; TP2 → il y reste ; TP3 → TP1 ; TP4 → TP2 ; TPk → TP(k−2) ;
- le stop remonté ne s'applique qu'à partir de la bougie SUIVANTE (vente confirmée d'abord), sauf si le plus
  bas de la bougie du TP passe déjà sous le nouveau stop : on suppose alors le pire (stop touché après le TP).

Conventions de remplissage identiques à la résolution TP1 (`registry.limit_fill`) : achat limite à l'entrée 1,
ouverture sous la limite → rempli à l'ouverture, sinon il faut que le prix TRAVERSE la limite ; dans la bougie de
remplissage « au contact », aucun objectif (le plus haut a pu précéder l'entrée). Objectif vendu au marché au
contact (prix ≥ objectif), stop vendu au marché ; ouverture sous le stop → vendu à l'ouverture ; objectif et stop
dans la même bougie → stop d'abord (pessimiste). Sans objectif ni stop après `horizon` bougies : le reste est
vendu à la clôture. R = gain net (frais, glissement) rapporté au risque prévu (entrée − stop du signal).

UN seul moteur vectorisé sert au signal réel (`replay_trailing`) et aux ordres aveugles du taux de base
(`blind_trailing`) : les deux appliquent exactement les mêmes règles.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import CostScenario

TP_COUNT = 5
TRAIL_LAG = 2                    # TPk touché → stop à TP(k−2) ; TP1 et TP2 → entrée
STOP, ALL_TARGETS, TIMEOUT = "SL", "TOUS_TP", "TEMPS"


def early_weights(count: int) -> np.ndarray:
    total = count * (count + 1) / 2
    return np.array([(count - k) / total for k in range(count)])


def trail_stop(entry: np.ndarray, targets: np.ndarray, hits: np.ndarray, initial: np.ndarray) -> np.ndarray:
    """Stop après `hits` objectifs touchés : stop initial (0), entrée (1 ou 2), TP(hits−2) ensuite."""
    out = initial.copy()
    out = np.where(hits >= 1, np.maximum(out, entry), out)
    lagged = hits - TRAIL_LAG
    idx = np.clip(lagged - 1, 0, targets.shape[1] - 1)
    level = targets[np.arange(len(hits)), idx]
    return np.where(lagged >= 1, np.maximum(out, level), out)


def simulate(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, *, starts: np.ndarray,
             limit: np.ndarray, stop: np.ndarray, targets: np.ndarray, entry_window: int, horizon: int,
             costs: CostScenario) -> dict[str, np.ndarray]:
    """Ordres placés à la bougie `starts[i]` (première bougie après la décision). `targets` : matrice (N, K) des
    objectifs utilisés, croissants. Renvoie, par ordre : rempli, terminé, R net, objectifs touchés, issue."""
    n_bars, count = len(opens), len(starts)
    fee = costs.fee_bps / 1e4
    market = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    weights = early_weights(targets.shape[1]) if targets.shape[1] else np.zeros(0)
    filled = np.zeros(count, dtype=bool)
    fill_bar = np.full(count, -1, dtype=np.int64)
    price = np.full(count, np.nan)
    touched = np.zeros(count, dtype=bool)
    pending = np.arange(count)
    for k in range(entry_window):
        if not len(pending):
            break
        j = starts[pending] + k
        inside = j < n_bars
        pending, j = pending[inside], j[inside]
        at_open = opens[j] <= limit[pending]
        on_touch = ~at_open & (lows[j] < limit[pending])
        now = at_open | on_touch
        hit = pending[now]
        fill_bar[hit] = j[now]
        price[hit] = np.where(at_open[now], np.minimum(opens[j[now]] * (1 + market), limit[hit]), limit[hit])
        touched[hit] = on_touch[now]
        filled[hit] = True
        pending = pending[~now]
    window_done = starts + entry_window <= n_bars
    remaining = np.where(filled, 1.0, 0.0)
    proceeds = np.zeros(count)                       # somme des parts vendues × prix de vente net de frais
    hits = np.zeros(count, dtype=np.int64)
    current = stop.copy()
    done = ~filled
    outcome = np.full(count, "", dtype=object)
    # Ouverture de la bougie de remplissage déjà sous le stop (rempli à l'ouverture) : vendu aussitôt.
    gap = filled & ~touched & (opens[np.where(filled, fill_bar, 0)] <= stop)
    proceeds[gap] = opens[fill_bar[gap]] * (1 - market) * (1 - fee)
    remaining[gap], done[gap], outcome[gap] = 0.0, True, STOP
    active = np.flatnonzero(~done)
    order = np.arange(targets.shape[1])[None, :]
    for step in range(horizon):
        if not len(active):
            break
        j = fill_bar[active] + step
        inside = j < n_bars
        active, j = active[inside], j[inside]
        if not len(active):
            break
        first = step == 0
        o, h, low, c = opens[j], highs[j], lows[j], closes[j]
        s, tgt, before = current[active], targets[active], hits[active]
        open_of = order >= before[:, None]                         # objectifs encore ouverts
        exit_price = np.full(len(active), np.nan)
        # 1. Ouverture sous le stop (bougies suivantes) : tout le reste vendu à l'ouverture.
        gap_down = np.zeros(len(active), dtype=bool) if first else (o <= s)
        exit_price[gap_down] = o[gap_down] * (1 - market)
        # 2. Objectifs déjà dépassés à l'ouverture (bougies suivantes) : vendus avant tout stop de la bougie.
        at_open = np.zeros_like(open_of) if first else (open_of & (tgt <= o[:, None]) & ~gap_down[:, None])
        # 3. Dans la bougie : stop d'abord s'il est touché (pessimiste) ; sinon objectifs atteints par le plus haut
        #    (aucun dans une bougie remplie « au contact » : le plus haut a pu précéder l'entrée).
        sl_hit = ~gap_down & (low <= s)
        blocked = (first & touched[active])[:, None]
        in_bar = open_of & ~at_open & (tgt <= h[:, None]) & ~sl_hit[:, None] & ~gap_down[:, None] & ~blocked
        take = at_open | in_bar
        new_hits = before + take.sum(axis=1)
        proceeds[active] += (take * weights[None, :] * tgt * (1 - market) * (1 - fee)).sum(axis=1)
        remaining[active] -= (take * weights[None, :]).sum(axis=1)
        all_sold = new_hits >= tgt.shape[1]
        stopped = sl_hit & ~all_sold
        exit_price[stopped] = s[stopped] * (1 - market)
        # 4. Stop remonté (dès la bougie suivante) ; si le plus bas de cette bougie le traverse déjà : pire cas.
        raised = trail_stop(limit[active], tgt, new_hits, s)     # « l'entrée 1 » du signal
        late = ~gap_down & ~stopped & ~all_sold & in_bar.any(axis=1) & (low <= raised) & (raised > s)
        exit_price[late] = raised[late] * (1 - market)
        closing = (gap_down | stopped | late) & ~all_sold
        idx = active[closing]
        proceeds[idx] += remaining[idx] * exit_price[closing] * (1 - fee)
        remaining[idx] = 0.0
        outcome[idx] = [f"TP{k}_PUIS_SL" if k else STOP for k in new_hits[closing]]
        finished = active[all_sold]
        remaining[finished] = 0.0
        outcome[finished] = ALL_TARGETS
        hits[active], current[active] = new_hits, raised
        ended = closing | all_sold
        if step == horizon - 1:
            last = active[~ended]
            proceeds[last] += remaining[last] * c[~ended] * (1 - market) * (1 - fee)
            remaining[last] = 0.0
            outcome[last] = [f"TP{k}_PUIS_TEMPS" if k else TIMEOUT for k in hits[last]]
            ended = np.ones(len(active), dtype=bool)
        done[active[ended]] = True
        active = active[~ended]
    complete = filled & done
    r = np.full(count, np.nan)
    risk = limit - stop
    r[complete] = (proceeds[complete] - price[complete] * (1 + fee)) / risk[complete]
    return {"filled": filled, "complete": complete, "r": r, "hits": hits, "outcome": outcome,
            "unfilled": ~filled & window_done, "fill_bar": fill_bar}


def used_targets(targets: list[float], tp_count: int = TP_COUNT) -> list[float]:
    return list(targets[:max(1, tp_count)])


def replay_trailing(bars: pd.DataFrame, *, entry: float, stop: float, targets: list[float], entry_window: int,
                    max_hold: int, costs: CostScenario, tp_count: int = TP_COUNT) -> tuple[str, float | None]:
    """Un signal réel, sur les bougies clôturées qui suivent sa réception : (issue, R net)."""
    if bars.empty:
        return "PENDING", None
    o, h, low, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tgt = np.array([used_targets(targets, tp_count)], dtype=float)
    out = simulate(o, h, low, c, starts=np.array([0]), limit=np.array([float(entry)]), stop=np.array([float(stop)]),
                   targets=tgt, entry_window=entry_window, horizon=max_hold, costs=costs)
    if out["complete"][0]:
        return str(out["outcome"][0]), round(float(out["r"][0]), 4)
    if out["unfilled"][0]:
        return "UNFILLED", None
    return "PENDING", None


def blind_trailing(frame: pd.DataFrame, *, entry_offset: float, stop_atr: float, target_rs: list[float],
                   entry_window: int, horizon: int, costs: CostScenario,
                   mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Ordres aveugles de même géométrie à chaque bougie de `frame` (comme le taux de base TP1) : limite à
    close × (1 + écart), stop à `stop_atr` ATR sous la limite, objectifs à `target_rs` R au-dessus. Renvoie les R
    des ordres remplis et terminés, et leur instant de décision."""
    opens, highs, lows, closes = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = frame["atr14"].to_numpy(float)
    eligible = np.isfinite(atr) & (atr > 0) & np.isfinite(closes)
    if len(frame):
        eligible[-1] = False
    if mask is not None:
        eligible &= np.asarray(mask, dtype=bool)
    idx = np.flatnonzero(eligible)
    limit = closes[idx] * (1 + entry_offset)
    stop = limit - stop_atr * atr[idx]
    keep = stop > 0
    idx, limit, stop = idx[keep], limit[keep], stop[keep]
    targets = limit[:, None] + np.asarray(target_rs, dtype=float)[None, :] * (limit - stop)[:, None]
    out = simulate(opens, highs, lows, closes, starts=idx + 1, limit=limit, stop=stop, targets=targets,
                   entry_window=entry_window, horizon=horizon, costs=costs)
    done = out["complete"]
    return out["r"][done], frame["decision_time"].to_numpy()[idx][done]


def trailing_rate(frame: pd.DataFrame, *, entry_offset: float, stop_atr: float, target_rs: list[float], trend: str,
                  volatility: str, entry_window: int, horizon: int, costs: CostScenario, min_samples: int, seed: int,
                  bootstrap_samples: int, bar_minutes: int = 15, history_end: pd.Timestamp | None = None) -> dict:
    """Taux de base de la gestion « stop suiveur » : mêmes ordres aveugles que le taux de base TP1 (même régime
    quand l'échantillon suffit, sinon tous régimes), fenêtre complète avant `history_end` (fin de DEVELOPMENT).
    Espérance en R par ordre rempli, part des trades terminés en gain, IC95 par blocs de jours calendaires."""
    import math

    from ..backtest.metrics import day_block_ci95
    block_days = max(1, math.ceil(horizon * bar_minutes / 1440))
    historical = None
    if history_end is not None:
        span = pd.Timedelta(minutes=bar_minutes) * (entry_window + horizon)
        historical = (frame["decision_time"] + span <= pd.Timestamp(history_end)).to_numpy()

    def run(mask):
        if historical is not None:
            mask = historical if mask is None else (mask & historical)
        r, times = blind_trailing(frame, entry_offset=entry_offset, stop_atr=stop_atr, target_rs=target_rs,
                                  entry_window=entry_window, horizon=horizon, costs=costs, mask=mask)
        ci, blocks = day_block_ci95(r, times, block_days=block_days, samples=bootstrap_samples, seed=seed)
        return r, ci, blocks

    conditioned = trend != "UNKNOWN" and volatility != "UNKNOWN"
    if conditioned:
        mask = (frame["ctx_trend"].to_numpy() == trend) & (frame["ctx_volatility"].to_numpy() == volatility)
        r, ci, blocks = run(mask)
        if len(r) < min_samples or ci is None:
            conditioned = False
    if not conditioned:
        r, ci, blocks = run(None)
    return {"samples": int(len(r)), "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
            "expectancy_r_ci95": ci, "positive_share": round(float((r > 0).mean()), 4) if len(r) else None,
            "regime": f"{trend}/{volatility}" if conditioned else "tous régimes", "regime_conditioned": conditioned,
            "horizon_bars": horizon, "block_days": block_days, "blocks": blocks, "tp_count": len(target_rs),
            "history_end": None if history_end is None else f"{pd.Timestamp(history_end):%Y-%m-%d}"}
