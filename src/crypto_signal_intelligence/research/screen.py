"""Criblage d'hypothèses (familles D à I du cahier des charges) AVANT toute stratégie complète.

Question posée à chaque condition d'entrée : après l'événement, le rendement futur BRUT dépasse-t-il
(1) la dérive propre de la paire sur la même durée et (2) le seuil des coûts aller-retour ? Si une
idée n'a pas d'avantage brut ici, une stratégie complète (stops, cibles, walk-forward) n'en créera pas.

Règles (toutes causales) :
- événement évalué à la clôture de la bougie t ; entrée à l'OUVERTURE de t+1 ; sortie à la clôture
  de t+h (15m comme 1h). Aucun stop, aucune cible : c'est un criblage, pas une simulation de trading ;
- excès = rendement − moyenne inconditionnelle de la même paire au même horizon (dérive retirée) ;
- incertitude : moyennes quotidiennes (UTC) des excès, bootstrap par blocs de jours consécutifs
  (événements d'un même jour et de paires corrélées non traités comme indépendants) ;
- période DEVELOPMENT uniquement : le test final réservé n'est jamais lu, BTC de contexte compris ;
- les classements G et I ne portent que sur l'univers demandé : BTC n'y entre que s'il en fait partie ;
- le nombre d'essais (conditions × horizons) est enregistré : plus on crible, plus un résultat
  isolé risque d'être un hasard.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import load_candles
from .experiments import ExperimentRegistry, dependency_versions, git_state, new_run_id
from .protocol import clip_to_development, development_end

HORIZONS_HOURS = (1, 4, 24)
CONDITIONS = {
    "D_BREAKOUT_RETEST": "cassure du plus haut 20 bougies, retest du niveau (≤ +0,1 %) dans les 8 bougies, "
                         "clôture de reprise au-dessus du niveau et haussière",
    "E_FAILED_BREAKDOWN_RECLAIM": "support = plus bas des bougies t−43..t−4 ; clôture précédente sous le support, "
                                  "clôture courante au-dessus",
    "F_SQUEEZE_BREAKOUT": "largeur de Bollinger(20) de la bougie précédente dans le quintile bas des 500 "
                          "précédentes, puis clôture au-dessus du plus haut 20 bougies",
    "H_DAILY_VWAP_RECLAIM": "VWAP ancré à 00:00 UTC ; trois clôtures précédentes sous le VWAP, clôture courante "
                            "au-dessus",
    "G_RELATIVE_STRENGTH_TOP3": "toutes les 4 h, les 3 paires au meilleur rendement 7 j ajusté de la volatilité",
    "I_RESIDUAL_MOMENTUM_TOP3": "toutes les 4 h, les 3 paires au meilleur rendement 7 j résiduel (bêta 30 j "
                                "contre BTC retiré), BTC exclu",
}


@dataclass
class ScreenRow:
    condition: str
    horizon_h: int
    events: int
    pairs: int
    mean_return_pct: float | None
    mean_excess_pct: float | None
    ci95_excess_pct: tuple[float, float] | None
    pairs_positive_share: float | None
    years_positive_share: float | None
    beats_costs: bool


@dataclass
class ScreenResult:
    run_id: str
    period_end: str
    cost_hurdle_pct: float
    n_trials: int
    program_trials: int = 0
    rows: list[ScreenRow] = field(default_factory=list)


# --- conditions sur une paire (bougies 15m) ----------------------------------------------------
def _breakout_retest(df: pd.DataFrame) -> np.ndarray:
    """Cassure (clôture > plus haut 20 bougies précédentes), puis retest du niveau (plus bas ≤ niveau + 0,1 %)
    dans les 8 bougies, puis première clôture haussière au-dessus du niveau : un signal par cassure."""
    o, lo, c = (df[k].to_numpy(float) for k in ("open", "low", "close"))
    level = df["high"].rolling(20).max().shift(1).to_numpy()
    events = np.zeros(len(df), dtype=bool)
    active_level, age, retested = np.nan, 0, False
    for t in range(len(df)):
        if not np.isnan(active_level):
            age += 1
            if age > 8 or c[t] < active_level * 0.998:          # retest absent ou niveau invalidé
                active_level = np.nan
            else:
                if retested and c[t] > active_level and c[t] > o[t]:
                    events[t] = True
                    active_level = np.nan                        # un seul signal par cassure
                    continue
                retested = retested or lo[t] <= active_level * 1.001
        if np.isnan(active_level) and not np.isnan(level[t]) and c[t] > level[t]:
            active_level, age, retested = level[t], 0, False
    return events


def _failed_breakdown(df: pd.DataFrame) -> np.ndarray:
    support = df["low"].rolling(40).min().shift(4)
    close = df["close"]
    return ((close.shift(1) < support) & (close > support)).to_numpy()


def _squeeze_breakout(df: pd.DataFrame) -> np.ndarray:
    mid = df["close"].rolling(20).mean()
    width = 4 * df["close"].rolling(20).std(ddof=0) / mid
    low_quantile = width.rolling(500).quantile(0.2).shift(1)
    breakout = df["close"] > df["high"].rolling(20).max().shift(1)
    return ((width.shift(1) <= low_quantile) & breakout).to_numpy()


def _vwap_reclaim(df: pd.DataFrame) -> np.ndarray:
    day = df["open_time"].dt.floor("D")
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = (typical * df["base_volume"]).groupby(day).cumsum()
    vol = df["base_volume"].groupby(day).cumsum()
    vwap = pv / vol.replace(0, np.nan)
    below = df["close"] < vwap
    prior_below = below.shift(1) & below.shift(2) & below.shift(3)
    return (prior_below.fillna(False).astype(bool) & (df["close"] > vwap)).to_numpy()


SERIES_CONDITIONS: dict[str, Callable[[pd.DataFrame], np.ndarray]] = {
    "D_BREAKOUT_RETEST": _breakout_retest,
    "E_FAILED_BREAKDOWN_RECLAIM": _failed_breakdown,
    "F_SQUEEZE_BREAKOUT": _squeeze_breakout,
    "H_DAILY_VWAP_RECLAIM": _vwap_reclaim,
}


def forward_returns(df: pd.DataFrame, bars: int) -> np.ndarray:
    """Entrée à l'ouverture de t+1, sortie à la clôture de t+bars ; NaN au-delà des données."""
    entry = df["open"].shift(-1)
    exit_ = df["close"].shift(-bars)
    return (exit_ / entry - 1).to_numpy()


def _collect(events: np.ndarray, fwd: np.ndarray, times: pd.Series, symbol: str) -> pd.DataFrame:
    valid = events & ~np.isnan(fwd)
    drift = np.nanmean(fwd)
    return pd.DataFrame({"time": times.to_numpy()[valid], "symbol": symbol, "ret": fwd[valid],
                         "excess": fwd[valid] - drift})


# --- conditions transversales (bougies 1h, toutes les 4 h) --------------------------------------
def _cross_sectional(closes: pd.DataFrame, kind: str, btc_close: pd.Series) -> pd.DataFrame:
    """Booléens (horodatage × paire de `closes`) : paires retenues parmi les 3 meilleures du score.
    `btc_close` sert de facteur à I ; il n'est candidat que s'il figure dans `closes` (et jamais pour I)."""
    rets = np.log(closes).diff()
    ret_7d = np.log(closes / closes.shift(168))
    if kind == "G":
        score = ret_7d / (rets.rolling(168).std() * np.sqrt(168))
    else:
        btc_close = btc_close.reindex(closes.index)
        btc = np.log(btc_close).diff()
        btc_7d = np.log(btc_close / btc_close.shift(168))
        beta = rets.rolling(720).cov(btc).div(btc.rolling(720).var(), axis=0)
        score = ret_7d - beta.mul(btc_7d, axis=0)
        score = score.drop(columns=["BTCUSDT"], errors="ignore")
    ranks = score.rank(axis=1, ascending=False)
    chosen = (ranks <= 3) & score.notna()
    on_grid = chosen.index.hour % 4 == 0
    chosen.loc[~on_grid] = False
    return chosen


def _day_block_ci(frame: pd.DataFrame, block_days: int, samples: int, seed: int) -> tuple[float, float] | None:
    """IC95 de la MÊME moyenne que celle affichée (pondérée par événement), en tirant des blocs de jours
    consécutifs : les événements d'un même jour, et de jours voisins, restent ensemble."""
    grouped = frame.groupby(frame["time"].dt.floor("D"))["excess"]
    sums, counts = grouped.sum().to_numpy(), grouped.count().to_numpy()
    blocks = len(sums) // block_days
    if blocks < 10:
        return None
    block_sums = sums[: blocks * block_days].reshape(blocks, block_days).sum(axis=1)
    block_counts = counts[: blocks * block_days].reshape(blocks, block_days).sum(axis=1)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, blocks, size=(samples, blocks))
    draws = block_sums[picks].sum(axis=1) / block_counts[picks].sum(axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return round(float(low) * 100, 4), round(float(high) * 100, 4)


def _row(condition: str, horizon: int, frame: pd.DataFrame, hurdle_pct: float, settings: Settings) -> ScreenRow:
    if frame.empty:
        return ScreenRow(condition, horizon, 0, 0, None, None, None, None, None, False)
    by_pair = frame.groupby("symbol")["excess"].mean()
    by_year = frame.groupby(frame["time"].dt.year)["excess"].mean()
    mean_ret = float(frame["ret"].mean()) * 100
    ci = _day_block_ci(frame, 10, settings.protocol.bootstrap_samples, settings.protocol.seed)
    return ScreenRow(condition, horizon, len(frame), int(by_pair.size), round(mean_ret, 4),
                     round(float(frame["excess"].mean()) * 100, 4), ci, round(float((by_pair > 0).mean()), 3),
                     round(float((by_year > 0).mean()), 3),
                     beats_costs=bool(mean_ret > hurdle_pct and ci is not None and ci[0] > 0))


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None) -> ScreenResult:
    symbols = symbols or list(settings.data.symbols)
    end = pd.Timestamp(development_end(settings))
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    result = ScreenResult(new_run_id("SCREEN"), end.isoformat(), round(hurdle_pct, 4),
                          n_trials=len(CONDITIONS) * len(HORIZONS_HOURS))
    collected: dict[tuple[str, int], list[pd.DataFrame]] = {(c, h): [] for c in CONDITIONS for h in HORIZONS_HOURS}
    closes_1h: dict[str, pd.Series] = {}
    opens_1h: dict[str, pd.Series] = {}
    for symbol in symbols:
        if progress:
            progress(symbol)
        df = load_candles(settings, symbol, "15m")
        df = df[df["open_time"] <= end].reset_index(drop=True)
        fwd = {h: forward_returns(df, h * 4) for h in HORIZONS_HOURS}
        for name, condition in SERIES_CONDITIONS.items():
            events = condition(df)
            for h in HORIZONS_HOURS:
                collected[(name, h)].append(_collect(events, fwd[h], df["open_time"], symbol))
        hourly = clip_to_development(load_candles(settings, symbol, "1h"), settings).set_index("open_time")
        closes_1h[symbol], opens_1h[symbol] = hourly["close"], hourly["open"]
        del df
    closes = pd.DataFrame(closes_1h).sort_index()
    opens = pd.DataFrame(opens_1h).sort_index()
    btc_close = closes["BTCUSDT"] if "BTCUSDT" in closes else clip_to_development(
        load_candles(settings, "BTCUSDT", "1h"), settings).set_index("open_time")["close"]
    for kind, name in (("G", "G_RELATIVE_STRENGTH_TOP3"), ("I", "I_RESIDUAL_MOMENTUM_TOP3")):
        chosen = _cross_sectional(closes, kind, btc_close)
        for h in HORIZONS_HOURS:
            fwd = closes.shift(-h) / opens.shift(-1) - 1      # entrée à l'ouverture t+1, sortie clôture t+h
            drift = fwd.mean()
            for symbol in chosen.columns:
                mask = chosen[symbol].to_numpy() & fwd[symbol].notna().to_numpy()
                values = fwd[symbol].to_numpy()[mask]
                collected[(name, h)].append(pd.DataFrame({"time": chosen.index[mask], "symbol": symbol,
                                                          "ret": values, "excess": values - drift[symbol]}))
    for (name, h), parts in collected.items():
        frame = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(
            not p.empty for p in parts) else pd.DataFrame(columns=["time", "symbol", "ret", "excess"])
        if not frame.empty:
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
        result.rows.append(_row(name, h, frame, hurdle_pct, settings))
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + result.n_trials
    _record(settings, result, now=now, symbols=symbols)
    return result


def _record(settings: Settings, result: ScreenResult, *, now: datetime, symbols: list[str]) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"conditions": CONDITIONS}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind="SCREEN",
        hypothesis="criblage brut des familles D à I : avantage après dérive et au-delà des coûts ?",
        strategy="SCREEN_D_TO_I", strategy_version=1, variant="conditions figées (voir CONDITIONS)",
        params={"horizons_h": list(HORIZONS_HOURS), "conditions": CONDITIONS}, period_label="DEVELOPMENT",
        period_start=settings.data.history_start.isoformat(), period_end=result.period_end, universe=symbols,
        data_hashes={}, git_commit=git_state(settings.root), dependencies=dependency_versions(),
        seed=settings.protocol.seed, cost_scenario="central (seuil aller-retour)",
        simulation_rules={"entry": "ouverture t+1", "exit": "clôture t+h", "stops": "aucun"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials,
                 "cost_hurdle_pct": result.cost_hurdle_pct,
                 "rows": [asdict(r) for r in result.rows]}, status="COMPLETED", report_dir=str(report_dir))
