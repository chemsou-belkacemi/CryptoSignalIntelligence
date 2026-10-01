"""Comparer les gestions sur les signaux RÉELS d'un groupe (docs/EXTERNAL_SIGNALS.md, « Comparer les gestions »).

Une gestion ne crée pas d'avantage à partir d'entrées prises au hasard ; elle peut en revanche tirer bien plus (ou
bien moins) d'un groupe qui choisit bien ses entrées. On rejoue donc chaque signal du groupe avec chaque gestion de
`management_grid()` (1 à 7 objectifs, part vendue à TP1, règle du stop), puis :
- CHOIX sur les deux premiers tiers des signaux (dans l'ordre chronologique) : la gestion de meilleur R moyen ;
- CONFIRMATION sur le dernier tiers, jamais vu pendant le choix : son R moyen et l'écart, signal par signal, avec
  la gestion actuelle du propriétaire, chacun avec un IC95 par jours de publication.
Essayer ~100 gestions et garder la meilleure sur les mêmes signaux trouverait toujours un « gagnant » : seule la
confirmation compte. Aucune conclusion sous MIN_CONFIRM signaux sur MIN_CONFIRM_DAYS jours dans le dernier tiers.
Mesure d'une source externe, aucun ordre.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci95
from ..config import CostScenario
from .trailing import OWNER, Management, management_grid, simulate

STEP = pd.Timedelta(minutes=15)
CHOICE_SHARE = 2 / 3
MIN_CHOICE, MIN_CONFIRM, MIN_CONFIRM_DAYS = 20, 20, 10
TOP = 5


def replay_all(signals: Sequence[dict], candles: dict[str, pd.DataFrame], managements: Sequence[Management], *,
               entry_window: int, horizon: int, costs: CostScenario) -> pd.DataFrame:
    """R net de chaque signal (lignes) pour chaque gestion (colonnes, clé de la gestion) ; NaN si le signal n'est
    pas rempli ou pas terminé. `signals` : dictionnaires avec symbol, received_at, entry, stop, targets."""
    table = pd.DataFrame(np.nan, index=range(len(signals)), columns=[m.key for m in managements])
    by_symbol: dict[str, list[int]] = {}
    for i, s in enumerate(signals):
        by_symbol.setdefault(s["symbol"], []).append(i)
    for symbol, rows in by_symbol.items():
        bars = candles.get(symbol)
        if bars is None or bars.empty:
            continue
        times = bars["open_time"]
        o, h, low, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        starts = np.array([int(times.searchsorted(pd.Timestamp(signals[i]["received_at"]).ceil("15min")))
                           for i in rows])
        limit = np.array([float(signals[i]["entry"]) for i in rows])
        stop = np.array([float(signals[i]["stop"]) for i in rows])
        for m in managements:
            used = [list(signals[i]["targets"])[:max(1, m.tp_count)] for i in rows]
            width = max(len(u) for u in used)
            targets = np.full((len(rows), width), np.inf)
            weights = np.zeros((len(rows), width))
            for k, u in enumerate(used):
                targets[k, :len(u)] = u
                weights[k, :len(u)] = m.weights(len(u))
            out = simulate(o, h, low, c, starts=starts, limit=limit, stop=stop, targets=targets,
                           entry_window=entry_window, horizon=horizon, costs=costs, weights=weights, management=m)
            table.loc[rows, m.key] = np.where(out["complete"], out["r"], np.nan)
    return table


def _summary(values: np.ndarray, times: np.ndarray, *, samples: int, seed: int) -> dict:
    values = np.asarray(values, dtype=float)
    ci, days = (day_block_ci95(values, times, block_days=1, samples=samples, seed=seed, min_blocks=MIN_CONFIRM_DAYS)
                if len(values) >= MIN_CONFIRM else (None, 0))
    return {"signals": int(len(values)), "r_mean": round(float(values.mean()), 4) if len(values) else None,
            "win_share": round(float((values > 0).mean()), 4) if len(values) else None, "ic95": ci}


def compare(signals: Sequence[dict], candles: dict[str, pd.DataFrame], *, entry_window: int, horizon: int,
            costs: CostScenario, samples: int, seed: int, managements: Sequence[Management] | None = None,
            current: Management = OWNER) -> dict:
    """Choix sur les deux premiers tiers, confirmation sur le dernier (voir la docstring du module)."""
    grid = list(managements or management_grid())
    if current.key not in {m.key for m in grid}:
        grid.append(current)
    labels = {m.key: m.label for m in grid}
    table = replay_all(signals, candles, grid, entry_window=entry_window, horizon=horizon, costs=costs)
    common = table.dropna()                                        # même échantillon pour toutes les gestions
    out: dict = {"variants": len(grid), "signals": int(len(common)), "current": current.key,
                 "current_label": current.label}
    if len(common) < MIN_CHOICE + MIN_CONFIRM:
        out["conclusion"] = (f"trop peu de signaux terminés ({len(common)} < {MIN_CHOICE + MIN_CONFIRM}) pour "
                             "choisir puis confirmer une gestion")
        return out
    times = pd.to_datetime(pd.Series([signals[i]["received_at"] for i in common.index], index=common.index), utc=True)
    order = times.sort_values().index
    cut = int(len(order) * CHOICE_SHARE)
    choice, confirm = order[:cut], order[cut:]
    means = common.loc[choice].mean().sort_values(ascending=False)
    best = means.index[0]
    confirm_times = times.loc[confirm].to_numpy()
    out["choice_period"] = [str(times.loc[choice].min())[:10], str(times.loc[choice].max())[:10]]
    out["confirm_period"] = [str(times.loc[confirm].min())[:10], str(times.loc[confirm].max())[:10]]
    out["top_on_choice"] = [{"key": k, "label": labels[k], "r_mean_choice": round(float(v), 4),
                             "r_mean_confirm": round(float(common.loc[confirm, k].mean()), 4)}
                            for k, v in means.head(TOP).items()]
    out["best"] = {"key": best, "label": labels[best],
                   "choice": _summary(common.loc[choice, best].to_numpy(), times.loc[choice].to_numpy(),
                                      samples=samples, seed=seed),
                   "confirm": _summary(common.loc[confirm, best].to_numpy(), confirm_times, samples=samples, seed=seed)}
    out["current_confirm"] = _summary(common.loc[confirm, current.key].to_numpy(), confirm_times, samples=samples,
                                      seed=seed)
    diff = (common.loc[confirm, best] - common.loc[confirm, current.key]).to_numpy()
    out["difference_confirm"] = _summary(diff, confirm_times, samples=samples, seed=seed)
    best_ci, diff_ci = out["best"]["confirm"]["ic95"], out["difference_confirm"]["ic95"]
    days = len({str(t)[:10] for t in confirm_times})
    if len(confirm) < MIN_CONFIRM or days < MIN_CONFIRM_DAYS or best_ci is None:
        out["conclusion"] = (f"confirmation impossible : {len(confirm)} signaux sur {days} jours dans le dernier tiers "
                             f"(il en faut {MIN_CONFIRM} sur {MIN_CONFIRM_DAYS} jours)")
    elif best == current.key:
        out["conclusion"] = "ta gestion actuelle est déjà la meilleure sur les deux premiers tiers"
    elif diff_ci is not None and diff_ci[0] > 0:
        out["conclusion"] = (f"« {labels[best]} » fait mieux que ta gestion sur le dernier tiers, jamais vu pendant le "
                             "choix (IC95 de l'écart entièrement > 0) : à essayer en Demo")
    elif diff_ci is not None and diff_ci[1] < 0:
        out["conclusion"] = "la meilleure gestion des deux premiers tiers fait MOINS bien sur le dernier : garde la tienne"
    else:
        out["conclusion"] = ("aucune gestion ne fait mieux que la tienne de façon démontrée sur le dernier tiers "
                             "(IC95 de l'écart contenant 0)")
    return out
