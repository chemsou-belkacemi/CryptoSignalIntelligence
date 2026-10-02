"""Étape 4 du plan de travail (2026-10-02) — suivi de tendance JOURNALIER, long seul : docs/TREND_DAILY.md (déclaré
avant exécution, 2 essais).

Ensemble de canaux de Donchian sur clôtures journalières (20, 50 et 100 jours : entrée sur un plus haut de L jours,
sortie sous un plus bas de L/2 jours), position moyenne des trois (0, 1/3, 2/3, 1), panier des 10 paires les plus
liquides éligibles (BTC et ETH d'abord), parts 1/10, avec ou sans ciblage de volatilité réalisée à 50 % par an ;
décision chaque jour à 00:00 UTC sur les clôtures de la veille, exécution à l'ouverture de 01:00, bande de 5 points.
Références : allocation STATIQUE à l'exposition moyenne, buy-and-hold du panier, BTC. DEVELOPMENT seulement ;
aucun ordre ; audit des fuites d'abord. Réplication des ensembles de tendance journaliers de la littérature
(Zarattini 2025), sur notre univers halal et avec notre modèle de frais.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import factors as fa
from . import long_horizon as lh
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION = "TREND_DAILY", "DONCHIAN_ENSEMBLE", 1
DOC = "docs/TREND_DAILY.md"
N_TRIALS = 2
LEVEL = 1 - 0.025 / N_TRIALS
DAY = pd.Timedelta(days=1)
FIRST_DECISION = pd.Timestamp("2019-04-01", tz="UTC")
LOOKBACKS = (20, 50, 100)
EXIT_DIVISOR = 2
CORE = ("BTCUSDT", "ETHUSDT")
BASKET = 10
BAND = 0.05
SIGMA_TARGET, VOL_DAYS = 0.50, 20
BLOCK_DAYS, SAMPLES = 56, 20_000
MIN_RATIO_RETURN, MAX_RATIO_DRAWDOWN, MIN_FOLDS_DRAWDOWN = lh.MIN_RATIO_RETURN, lh.MAX_RATIO_DRAWDOWN, lh.MIN_FOLDS_DRAWDOWN
VARIANTS = ("ENSEMBLE", "ENSEMBLE_VOL")


class LeakAuditFailed(RuntimeError):
    pass


# --- Signaux ---------------------------------------------------------------------------------------------------

def channel_positions(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Position 0/1 par actif : entrée quand la clôture dépasse le plus haut des `lookback` clôtures PRÉCÉDENTES,
    sortie quand elle passe sous le plus bas des `lookback // EXIT_DIVISOR` clôtures précédentes."""
    upper = close.shift(1).rolling(lookback, min_periods=lookback).max()
    lower = close.shift(1).rolling(max(1, lookback // EXIT_DIVISOR), min_periods=max(1, lookback // EXIT_DIVISOR)).min()
    enter = (close > upper).to_numpy()
    leave = (close < lower).to_numpy()
    values = close.to_numpy(float)
    out = np.zeros(close.shape, dtype=float)
    state = np.zeros(close.shape[1], dtype=bool)
    for i in range(close.shape[0]):
        known = np.isfinite(values[i])
        state = np.where(leave[i] & known, False, state)
        state = np.where(enter[i] & known, True, state)
        state = np.where(known, state, False)                     # journée sans clôture : position fermée
        out[i] = state
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def ensemble(close: pd.DataFrame) -> pd.DataFrame:
    """Moyenne des positions des trois canaux (0, 1/3, 2/3, 1)."""
    total = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for lookback in LOOKBACKS:
        total = total + channel_positions(close, lookback)
    return total / len(LOOKBACKS)


def realized_sigma(close: pd.DataFrame) -> pd.DataFrame:
    """Volatilité réalisée annualisée des rendements journaliers des `VOL_DAYS` jours précédents."""
    returns = np.log(close).diff()
    return returns.rolling(VOL_DAYS, min_periods=VOL_DAYS).std() * math.sqrt(365)


def baskets(panel: fa.Panel, book: fa.Book, decisions: pd.DatetimeIndex) -> dict[pd.Timestamp, list[str]]:
    """Panier du jour : BTC et ETH s'ils sont éligibles, puis les plus liquides éligibles (médiane 30 jours du volume)."""
    volume = panel.volume.rolling(fa.LIQUIDITY_DAYS, min_periods=fa.MIN_LIQUIDITY_DAYS).median()
    out = {}
    for moment in decisions:
        ok = book.eligible.loc[moment]
        members = [s for s in CORE if ok.get(s, False)]
        others = volume.loc[moment][ok & ~ok.index.isin(CORE)].sort_values(ascending=False)
        members += list(others.index[:BASKET - len(members)])
        out[moment] = members[:BASKET]
    return out


def weights(panel: fa.Panel, book: fa.Book, decisions: pd.DatetimeIndex, *, vol_target: bool,
            close: pd.DataFrame | None = None) -> pd.DataFrame:
    close = book.close if close is None else close
    signal = ensemble(close)
    sigma = realized_sigma(close) if vol_target else None
    out = pd.DataFrame(0.0, index=decisions, columns=panel.symbols)
    for moment, members in baskets(panel, book, decisions).items():
        for symbol in members:
            weight = float(signal.loc[moment, symbol]) / BASKET
            if vol_target and weight > 0:
                s = float(sigma.loc[moment, symbol]) if sigma is not None else math.nan
                weight *= min(1.0, SIGMA_TARGET / s) if np.isfinite(s) and s > 0 else 0.0
            out.loc[moment, symbol] = weight
    return out


def basket_table(panel: fa.Panel, book: fa.Book, decisions: pd.DatetimeIndex) -> pd.DataFrame:
    out = pd.DataFrame(0.0, index=decisions, columns=panel.symbols)
    for moment, members in baskets(panel, book, decisions).items():
        for symbol in members:
            out.loc[moment, symbol] = 1 / len(members)
    return out


# --- Simulation journalière ----------------------------------------------------------------------------------

@dataclass
class Run:
    daily: pd.Series
    values: pd.Series
    trades: int
    fees: float
    exposure: pd.Series


def simulate(panel: fa.Panel, targets: pd.DataFrame, *, costs: pd.Series, delay_days: int = 0,
             band: float | None = BAND) -> Run:
    """Portefeuille valorisé chaque jour au prix de 01:00 ; cibles du jour exécutées `delay_days` jours plus tard ;
    un actif n'est échangé que si son poids s'écarte de la cible de plus de `band` ou s'il entre ou sort ; frais par
    côté ; jamais de levier. Journée sans prix pour un actif : sa position est conservée telle quelle."""
    symbols = panel.symbols
    plan_days = targets.index + delay_days * DAY
    days = panel.price.index[(panel.price.index >= plan_days[0]) & (panel.price.index <= plan_days[-1] + DAY)]
    filled = panel.price.ffill().reindex(days).to_numpy(float)
    tradable = panel.price.reindex(days).notna().to_numpy()
    cost = costs.reindex(symbols).fillna(lh.SPREAD_OTHER + lh.FEE).to_numpy(float)
    plan = {d: targets.loc[m].reindex(symbols).fillna(0.0).to_numpy(float) for m, d in zip(targets.index, plan_days, strict=True)}
    holdings, cash = np.zeros(len(symbols)), 1.0
    values, exposure, trades, fees = [], {}, 0, 0.0
    for i, day in enumerate(days):
        if i:
            ratio = np.where(np.isfinite(filled[i]) & np.isfinite(filled[i - 1]) & (filled[i - 1] > 0), filled[i] / filled[i - 1], 1.0)
            holdings = holdings * ratio
        total = cash + holdings.sum()
        values.append(total)
        target = plan.get(day)
        if target is not None and total > 0:
            current = holdings / total
            change = (np.abs(target - current) > band) | ((target > 0) != (holdings > 1e-12)) if band is not None else np.ones(len(symbols), bool)
            desired = np.where(change & tradable[i], target * total, holdings)
            moved = np.abs(desired - holdings) > 1e-12
            if moved.any():
                buys = np.clip(desired - holdings, 0, None).sum()
                room = cash - (np.abs(desired - holdings) * cost).sum()
                if buys > room > 0:
                    desired = np.where(desired > holdings, holdings + (desired - holdings) * room / buys, desired)
                elif buys > 0 and room <= 0:
                    desired = np.where(desired > holdings, holdings, desired)
                fee_now = float((np.abs(desired - holdings) * cost).sum())
                moved = np.abs(desired - holdings) > 1e-12
                trades += int(moved.sum())
                fees += fee_now
                cash = total - fee_now - desired.sum()
                holdings = desired
            exposure[day] = float(holdings.sum() / (cash + holdings.sum())) if cash + holdings.sum() > 0 else 0.0
    series = pd.Series(values, index=days)
    return Run(series.pct_change().dropna(), series, trades, round(fees, 6), pd.Series(exposure))


def static_targets(table: pd.DataFrame, exposure: float) -> pd.DataFrame:
    """Référence statique : `exposure` répartie à parts égales sur les membres du panier de chaque jour."""
    members = (table > 0)
    counts = members.sum(axis=1).replace(0, np.nan)
    return members.div(counts, axis=0).fillna(0.0) * exposure


# --- Mesures -----------------------------------------------------------------------------------------------------

def sharpe(daily: np.ndarray) -> float:
    daily = np.asarray(daily, dtype=float)
    if len(daily) < 2:
        return 0.0
    std = float(np.std(daily, ddof=1))
    return float(np.mean(daily) / std * math.sqrt(365)) if std > fa.MIN_STD else 0.0


def sharpe_diff_ci(strategy: np.ndarray, benchmark: np.ndarray, *, level: float, seed: int,
                   block: int = BLOCK_DAYS, samples: int = SAMPLES) -> list[float] | None:
    """IC de l'écart de Sharpe (annualisé sur 365 jours) par blocs circulaires de `block` jours, tirés ensemble."""
    a, b = np.asarray(strategy, dtype=float), np.asarray(benchmark, dtype=float)
    n = len(a)
    if n < 4 * block:
        return None
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(samples, blocks))
    index = ((starts[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(samples, -1)[:, :n]

    def sharpes(x: np.ndarray) -> np.ndarray:
        drawn = x[index]
        std = drawn.std(axis=1, ddof=1)
        return np.where(std > fa.MIN_STD, drawn.mean(axis=1) / np.where(std > fa.MIN_STD, std, 1.0), 0.0) * math.sqrt(365)

    diff = sharpes(a) - sharpes(b)
    low, high = np.percentile(diff, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return [round(float(low), 4), round(float(high), 4)]


def describe(run: Run, *, trials: int) -> dict:
    daily = run.daily.to_numpy(float)
    total = float(np.prod(1 + daily) - 1)
    years = len(daily) / 365
    folds = lh.fold_of(pd.DatetimeIndex(run.daily.index))
    by_fold: list[dict | None] = []
    for k in range(len(lh.FOLD_STARTS)):
        moments = run.daily.index[folds == k]
        if len(moments) < 10:
            by_fold.append(None)
            continue
        part = run.values[(run.values.index >= moments[0] - DAY) & (run.values.index <= moments[-1])]
        by_fold.append({"sharpe": round(sharpe(daily[folds == k]), 4), "max_drawdown": round(fa.max_drawdown(part.to_numpy()), 4)})
    return {"days": len(daily), "annual_return": round((1 + total) ** (1 / years) - 1, 4) if years > 0 and total > -1 else None,
            "volatility": round(float(np.std(daily, ddof=1) * math.sqrt(365)), 4) if len(daily) > 1 else None,
            "sharpe": round(sharpe(daily), 4), "deflated_sharpe": lh.deflated_sharpe(daily, trials),
            "max_drawdown": round(fa.max_drawdown(run.values.to_numpy()), 4), "drawdown_days": lh.drawdown_days(run.values),
            "trades": run.trades, "fees_pct": round(run.fees * 100, 3),
            "average_exposure": round(float(run.exposure.mean()), 4) if len(run.exposure) else 0.0, "folds": by_fold}


# --- Audit des fuites ------------------------------------------------------------------------------------------

def all_weights(panel: fa.Panel, decisions: pd.DatetimeIndex, *, close: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    book = fa.Book(panel, decisions)
    return {"ENSEMBLE": weights(panel, book, decisions, vol_target=False, close=close),
            "ENSEMBLE_VOL": weights(panel, book, decisions, vol_target=True, close=close)}


def leak_audit(frames: dict[str, pd.DataFrame], panel: fa.Panel, decisions: pd.DatetimeIndex, *, seed: int, picks: int = 4) -> dict:
    """Poids recalculés avec les seules bougies antérieures à une décision, puis avec un futur falsifié : identiques.
    Mutation : un signal lu sur la clôture du LENDEMAIN doit être détecté."""
    rng = np.random.default_rng(seed)
    full = all_weights(panel, decisions)
    chosen = sorted(rng.choice(np.arange(len(decisions) // 3, len(decisions)), size=picks, replace=False))
    violations: list[dict] = []
    detected = False
    for pick in chosen:
        moment = decisions[pick]
        for label, cut in (("tronqué", {s: f[f["open_time"] < moment] for s, f in frames.items()}),
                           ("futur falsifié", {s: lh._falsify(f, moment, rng) for s, f in frames.items()})):
            kept = {s: f for s, f in cut.items() if not f.empty}
            past = decisions[decisions <= moment]
            redo = all_weights(fa.build_panel(kept), past)
            for key, table in full.items():
                row = redo[key].loc[moment].reindex(table.columns).fillna(0.0).to_numpy()
                if not np.allclose(row, table.loc[moment].to_numpy(), atol=1e-12):
                    violations.append({"decision": str(moment), "check": label, "weights": key})
        book = fa.Book(panel, decisions)
        mutated = all_weights(panel, decisions, close=book.close.shift(-1))["ENSEMBLE"]
        detected |= not np.allclose(mutated.loc[moment].to_numpy(), full["ENSEMBLE"].loc[moment].to_numpy(), atol=1e-12)
    return {"violations": violations, "mutation_detected": bool(detected), "decisions": [str(decisions[p]) for p in chosen],
            "passed": not violations and bool(detected)}


# --- Exécution ---------------------------------------------------------------------------------------------------

@dataclass
class Result:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    program_trials: int = 0
    level: float = LEVEL
    leak_audit: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)


def decisions_of(panel: fa.Panel, *, first: pd.Timestamp = FIRST_DECISION) -> pd.DatetimeIndex:
    """Chaque jour à 00:00 UTC de `first` au dernier jour qui a un prix d'exécution le lendemain."""
    last_price_day = panel.price.dropna(how="all").index.max()
    index = panel.close.index
    return index[(index >= first) & (index + DAY <= last_price_day)]


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None, allow_dirty: bool = False) -> Result:
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("TREND"), end.isoformat())
    say("données")
    frames = fa.load_frames(settings, symbols, end)
    result.data_hashes = {s: fingerprint(f) for s, f in frames.items()}
    panel = fa.build_panel(frames)
    decisions = decisions_of(panel)
    say("audit des fuites")
    result.leak_audit = leak_audit(frames, panel, decisions, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    before = ExperimentRegistry(settings.experiments_db).program_trials()
    trials = before + N_TRIALS
    book = fa.Book(panel, decisions)
    tables = all_weights(panel, decisions)
    basket = basket_table(panel, book, decisions)
    result.coverage = {"decisions": len(decisions), "first": str(decisions[0]), "last": str(decisions[-1]),
                       "baskets": {str(m): b for m, b in list(baskets(panel, book, decisions).items())[::90]},
                       "mean_position": {k: round(float((t > 0).mean(axis=1).mean()), 4) for k, t in tables.items()}}
    runs: dict[str, dict[str, Run]] = {"central": {}, "adverse": {}}
    for scenario, adverse in (("central", False), ("adverse", True)):
        costs = lh.cost_per_side(panel.symbols, adverse=adverse)
        delay = 1 if adverse else 0
        for key, table in tables.items():
            say(f"{key} ({scenario})")
            runs[scenario][key] = simulate(panel, table, costs=costs, delay_days=delay)
        say(f"références ({scenario})")
        runs[scenario]["REF_BH"] = simulate(panel, basket, costs=costs, delay_days=delay)
        runs[scenario]["BTC"] = simulate(panel, pd.DataFrame({MARKET: 1.0}, index=decisions[:1]).reindex(columns=panel.symbols).fillna(0.0),
                                         costs=costs, delay_days=delay, band=None)
        for key in VARIANTS:
            exposure = float(runs[scenario][key].exposure.mean()) if len(runs[scenario][key].exposure) else 0.0
            runs[scenario][f"STATIC_{key}"] = simulate(panel, static_targets(basket, exposure), costs=costs, delay_days=delay)
    for scenario in runs:
        result.models[scenario] = {k: describe(r, trials=trials) for k, r in runs[scenario].items()}
    for key in VARIANTS:
        reference = f"STATIC_{key}"
        judged: dict = {}
        for scenario in ("central", "adverse"):
            a, b = runs[scenario][key].daily, runs[scenario][reference].daily
            common = a.index.intersection(b.index)
            ci = sharpe_diff_ci(a.loc[common].to_numpy(), b.loc[common].to_numpy(), level=LEVEL, seed=settings.protocol.seed)
            judged[scenario] = lh.judge(result.models[scenario][key], result.models[scenario][reference], ci) | {
                "sharpe_diff": round(sharpe(a.loc[common].to_numpy()) - sharpe(b.loc[common].to_numpy()), 4), "sharpe_diff_ci": ci,
                "vs_buy_and_hold": lh.judge(result.models[scenario][key], result.models[scenario]["REF_BH"], None)}
        result.verdicts[key] = {"reference": reference, "buy_and_hold": "REF_BH",
                                "verdict": lh.verdict(judged["central"], judged["adverse"])} | judged
    result.program_trials = before + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="suivi de tendance journalier : un ensemble de canaux de Donchian (20, 50, 100 jours), long seul, avec ou sans "
                   "ciblage de volatilité réalisée à 50 %, réduit-il la perte à rendement comparable, face à une allocation "
                   "statique au même panier, frais inclus ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant=f"{N_TRIALS} essais figés (docs/TREND_DAILY.md)",
        params={"lookbacks": list(LOOKBACKS), "exit_divisor": EXIT_DIVISOR, "basket": BASKET, "band": BAND,
                "sigma_target": SIGMA_TARGET, "vol_days": VOL_DAYS, "level": LEVEL, "block_days": BLOCK_DAYS,
                "samples": SAMPLES, "fee": lh.FEE, "spread": lh.SPREAD, "spread_other": lh.SPREAD_OTHER},
        period_label="DEVELOPMENT", period_start=str(FIRST_DECISION), period_end=result.period_end, universe=symbols,
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="frais 7,5 pb + écart 2 à 5 pb ; défavorable 10 pb, écart doublé, un jour de retard",
        simulation_rules={"decision": "chaque jour 00:00 UTC sur les clôtures de la veille", "execution": "ouverture de 01:00", "leverage": "aucun"},
        metrics={"n_trials": N_TRIALS, "program_trials": result.program_trials, "coverage": result.coverage,
                 "verdict": " ; ".join(f"{k} {v['verdict']}" for k, v in result.verdicts.items()),
                 "verdicts": result.verdicts, "models": result.models}, status="COMPLETED", report_dir=str(report_dir))
