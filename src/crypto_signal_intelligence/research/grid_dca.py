"""Stratégies sans prédiction : grille et DCA (point 8 du plan de travail ; docs/GRID_DCA.md, déclaré le 2026-10-03 avant
exécution). La catégorie des bots du marché (Alpha Prime, OctoBot…) : elles ne prédisent pas le sens, elles exploitent
les allers-retours du prix (grille) ou étalent l'entrée (DCA). Question : sur un mois, font-elles mieux que de garder
la crypto, ou que de rester en liquidités, une fois les frais payés, et dans quel régime ?

Simulation sur bougies 1 h, par (paire, mois civil), capital 1 : ordres limites remplis quand le prix les TRAVERSE
(plus bas < prix d'achat, plus haut > prix de vente : jamais au simple contact, conformément à la mesure sur les
transactions), frais maker du scénario à chaque ordre, liquidation au marché à la clôture du dernier jour du mois.
Quatre variantes figées = 4 essais.
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from ..config import Settings
from . import factors as fa
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .intervals import calendar_mean_ci
from .protocol import development_end
from .universe import RESEARCH_UNIVERSE

FIRST_MONTH = pd.Timestamp("2019-01-01", tz="UTC")
VARIANTS: dict[str, dict[str, Any]] = {
    "G1_GRILLE_10": {"kind": "grid", "half_range": 0.10, "step": 0.02},
    "G2_GRILLE_20": {"kind": "grid", "half_range": 0.20, "step": 0.04},
    "D1_DCA_PALIERS": {"kind": "dca", "tranches": 4, "drop": 0.05, "take_profit": None},
    "D2_DCA_PRISE_DE_GAIN": {"kind": "dca", "tranches": 4, "drop": 0.05, "take_profit": 0.03},
}
N_TRIALS = len(VARIANTS)
FEE = {"central": 0.00075, "defavorable": 0.0010}
MARKET_EXIT = {"central": 0.0003, "defavorable": 0.0008}     # glissement + demi-écart à la liquidation au marché
BTC_REGIME = 0.05


@dataclass
class Month:
    symbol: str
    month: str
    variant: str
    scenario: str
    ret: float
    hold: float
    trades: int
    max_exposure: float


def simulate_grid(bars: pd.DataFrame, half_range: float, step: float, fee: float, exit_cost: float) -> tuple[float, int, float]:
    """Grille d'achats sous le prix d'ouverture du mois (capital réparti également entre les niveaux), chaque achat
    rempli place une vente un pas plus haut ; inventaire liquidé à la clôture. (rendement, ordres, exposition max)."""
    start = float(bars["open"].iloc[0])
    levels = [start * (1 - step * k) for k in range(1, int(round(half_range / step)) + 1)]
    budget = 1.0 / len(levels)
    cash, units, trades, max_exposure = 1.0, 0.0, 0, 0.0
    open_buys = {i: lvl for i, lvl in enumerate(levels)}
    open_sells: dict[int, tuple[float, float]] = {}
    for h, lo in zip(bars["high"].to_numpy(float), bars["low"].to_numpy(float), strict=True):
        for i, (price, qty) in list(open_sells.items()):
            if h > price:
                cash += qty * price * (1 - fee)
                units -= qty
                trades += 1
                del open_sells[i]
                open_buys[i] = levels[i]
        for i, price in list(open_buys.items()):
            if lo < price and cash >= budget * 0.999:
                qty = budget / (price * (1 + fee))
                cash -= budget
                units += qty
                trades += 1
                del open_buys[i]
                open_sells[i] = (price * (1 + step), qty)
        max_exposure = max(max_exposure, 1 - cash)
    final = cash + units * float(bars["close"].iloc[-1]) * (1 - exit_cost) * (1 - fee)
    return final - 1, trades, max_exposure


def simulate_dca(bars: pd.DataFrame, tranches: int, drop: float, take_profit: float | None, fee: float,
                 exit_cost: float) -> tuple[float, int, float]:
    """DCA : une tranche à l'ouverture du mois, puis une tranche à chaque baisse de `drop` sous le dernier achat (ordre
    limite) ; avec `take_profit`, tout est vendu dès que le prix dépasse le coût moyen × (1 + take_profit), et le cycle
    recommence au prix suivant. Liquidation à la clôture du mois."""
    budget = 1.0 / tranches
    cash, units, cost, trades, max_exposure = 1.0, 0.0, 0.0, 0, 0.0
    o, h, lo = (bars[c].to_numpy(float) for c in ("open", "high", "low"))
    next_buy, bought = None, 0
    for t in range(len(bars)):
        if bought == 0 and cash >= budget * 0.999 and next_buy is None:
            price = o[t]
            cash -= budget
            units += budget / (price * (1 + fee))
            cost += budget
            trades, bought, next_buy = trades + 1, 1, price * (1 - drop)
        if take_profit is not None and units > 0:
            target = cost / units * (1 + take_profit)
            if h[t] > target:
                cash += units * target * (1 - fee)
                units, cost, trades, bought, next_buy = 0.0, 0.0, trades + 1, 0, None
                max_exposure = max(max_exposure, 1 - cash)
                continue
        if next_buy is not None and bought < tranches and lo[t] < next_buy and cash >= budget * 0.999:
            price = next_buy
            cash -= budget
            units += budget / (price * (1 + fee))
            cost += budget
            trades, bought, next_buy = trades + 1, bought + 1, price * (1 - drop)
        max_exposure = max(max_exposure, 1 - cash)
    final = cash + units * float(bars["close"].iloc[-1]) * (1 - exit_cost) * (1 - fee)
    return final - 1, trades, max_exposure


def hold_return(bars: pd.DataFrame, fee: float, exit_cost: float) -> float:
    """Garder : achat à l'ouverture du mois (limite, frais maker), vente à la clôture (marché)."""
    return float(bars["close"].iloc[-1]) * (1 - exit_cost) * (1 - fee) / (float(bars["open"].iloc[0]) * (1 + fee)) - 1


def months_of(h1: pd.DataFrame, end: pd.Timestamp) -> dict[pd.Timestamp, pd.DataFrame]:
    frame = h1[(h1["open_time"] >= FIRST_MONTH) & (h1["open_time"] <= end)].sort_values("open_time")
    out = {}
    for month, bars in frame.groupby(frame["open_time"].dt.tz_convert(None).dt.to_period("M")):
        expected = month.days_in_month * 24
        if len(bars) >= 0.95 * expected:
            out[pd.Timestamp(month.start_time, tz="UTC")] = bars.reset_index(drop=True)
    return out


def simulate_pair(h1: pd.DataFrame, symbol: str, end: pd.Timestamp, members: set | None = None) -> list[Month]:
    out = []
    for month, bars in months_of(h1, end).items():
        if members is not None and (month, symbol) not in members:
            continue
        for scenario in FEE:
            fee, exit_cost = FEE[scenario], MARKET_EXIT[scenario]
            hold = hold_return(bars, fee, exit_cost)
            for name, spec in VARIANTS.items():
                if spec["kind"] == "grid":
                    ret, trades, exposure = simulate_grid(bars, spec["half_range"], spec["step"], fee, exit_cost)
                else:
                    ret, trades, exposure = simulate_dca(bars, spec["tranches"], spec["drop"], spec["take_profit"], fee, exit_cost)
                out.append(Month(symbol, str(month)[:10], name, scenario, ret, hold, trades, exposure))
    return out


def btc_regimes(frames: dict[str, pd.DataFrame], end: pd.Timestamp) -> dict[str, str]:
    """Régime DESCRIPTIF de chaque mois (lu après coup, pour la ventilation seulement) : rendement de BTC sur le mois."""
    btc = frames.get("BTCUSDT")
    if btc is None:
        return {}
    out = {}
    for month, bars in months_of(btc, end).items():
        change = float(bars["close"].iloc[-1]) / float(bars["open"].iloc[0]) - 1
        out[str(month)[:10]] = "hausse" if change > BTC_REGIME else ("baisse" if change < -BTC_REGIME else "plat")
    return out


def summarize(rows: list[Month], regimes: dict[str, str]) -> dict:
    frame = pd.DataFrame([asdict(r) for r in rows])
    frame["excess_vs_hold"] = frame["ret"] - frame["hold"]
    frame["regime"] = frame["month"].map(regimes).fillna("inconnu")
    out: dict = {}
    for (variant, scenario), part in frame.groupby(["variant", "scenario"]):
        times = pd.to_datetime(part["month"], utc=True)
        ci_cash = calendar_mean_ci(part["ret"].to_numpy(float), times, block_days=90, min_blocks=10)
        ci_hold = calendar_mean_ci(part["excess_vs_hold"].to_numpy(float), times, block_days=90, min_blocks=10)
        by_regime = {k: {"pair_months": int(len(g)), "ret_pct": round(float(g["ret"].mean()) * 100, 3),
                         "hold_pct": round(float(g["hold"].mean()) * 100, 3), "excess_pct": round(float(g["excess_vs_hold"].mean()) * 100, 3)}
                     for k, g in part.groupby("regime")}
        out.setdefault(variant, {})[scenario] = {
            "pair_months": int(len(part)), "ret_pct": round(float(part["ret"].mean()) * 100, 3),
            "ret_ci_pct": [round(c * 100, 3) for c in ci_cash] if ci_cash else None,
            "hold_pct": round(float(part["hold"].mean()) * 100, 3),
            "excess_vs_hold_pct": round(float(part["excess_vs_hold"].mean()) * 100, 3),
            "excess_ci_pct": [round(c * 100, 3) for c in ci_hold] if ci_hold else None,
            "worst_month_pct": round(float(part["ret"].min()) * 100, 2), "hold_worst_month_pct": round(float(part["hold"].min()) * 100, 2),
            "ret_std_pct": round(float(part["ret"].std()) * 100, 3), "hold_std_pct": round(float(part["hold"].std()) * 100, 3),
            "trades_per_month": round(float(part["trades"].mean()), 1), "mean_max_exposure": round(float(part["max_exposure"].mean()), 3),
            "by_regime": by_regime}
    return out


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, point_in_time: bool = False) -> dict:
    """`point_in_time` : univers à date (top 40 du mois, paires retirées comprises, docs/UNIVERSE_PIT.md) au lieu des
    40 paires de recherche (survivantes)."""
    from ..features.loader import MissingData
    from .long_history import load_long
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    members: set | None = None
    if point_in_time:
        from .pit_universe import load_membership
        table = load_membership(settings)
        members = {(pd.Timestamp(m), s) for m, s in zip(table["month"], table["symbol"], strict=True)}
        symbols = sorted({s for _, s in members})
    symbols = list(symbols or RESEARCH_UNIVERSE)
    frames, hashes, missing = {}, {}, []
    for symbol in symbols:
        try:
            h1 = load_long(settings, symbol)
        except MissingData:
            missing.append(symbol)
            continue
        frames[symbol] = h1[h1["open_time"] <= end].reset_index(drop=True)
        hashes[symbol] = fingerprint(frames[symbol])
    rows: list[Month] = []
    for symbol, h1 in frames.items():
        say(symbol)
        rows += simulate_pair(h1, symbol, end, members)
    if "BTCUSDT" not in frames:
        with contextlib.suppress(MissingData):
            frames["BTCUSDT"] = load_long(settings, "BTCUSDT")
    summary = summarize(rows, btc_regimes(frames, end))
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("GRID")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "point_in_time": point_in_time,
               "symbols": len(frames), "missing": missing, "variants": VARIANTS, "summary": summary, "doc": "docs/GRID_DCA.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    pd.DataFrame([asdict(r) for r in rows]).to_parquet(report_dir / "months.parquet", index=False)
    registry.record(run_id=run_id, created_at=now.isoformat(), kind="GRID_DCA",
                    hypothesis="grille et DCA (sans prédiction) : font-elles mieux que garder la crypto, ou que les liquidités, sur un mois, "
                               "après frais, et dans quel régime ?",
                    strategy="GRID_DCA", strategy_version=1, variant="quatre variantes figées (docs/GRID_DCA.md)",
                    params={"variants": VARIANTS, "fee": FEE, "market_exit": MARKET_EXIT, "point_in_time": point_in_time},
                    period_label="DEVELOPMENT", period_start=str(FIRST_MONTH)[:10], period_end=end.isoformat(), universe=list(frames),
                    data_hashes=hashes, git_commit=state, dependencies=dependency_versions(), seed=settings.protocol.seed,
                    cost_scenario="central et défavorable (frais maker, liquidation au marché)",
                    simulation_rules={"fill": "prix traversé, jamais au simple contact", "horizon": "mois civil, liquidation à la clôture"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "summary": summary}, status="COMPLETED", report_dir=str(report_dir))
    return payload
