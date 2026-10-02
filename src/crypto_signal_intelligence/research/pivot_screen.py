"""Étape 8 du plan de travail (2026-10-02) — criblage K : pivots confirmés (docs/SCREENING.md, section « Criblage K »,
déclaré avant exécution ; 2 conditions × 2 cadres = 4 essais).

Bougies 4 h et 1 jour reconstruites des bougies 1 h du magasin long (blocs complets alignés UTC), DEVELOPMENT seul.
Un pivot n'existe qu'à la clôture de la bougie i + k (k = 3 bougies de confirmation) et n'est utilisable qu'à partir
de la bougie suivante ; un niveau est le dernier pivot confirmé, il expire après 100 bougies. Mêmes conventions que
les criblages D à I : entrée à l'ouverture de t + 1, sortie à la clôture de t + h, dérive de la paire retirée, IC95
par blocs de jours, « passe » = rendement brut moyen > seuil de coûts ET borne basse de l'IC95 > 0.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import factors as fa
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .protocol import development_end
from .screen import ScreenRow, _row, forward_returns
from .universe import RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION = "SCREEN", "SCREEN_PIVOT_K", 1
DOC = "docs/SCREENING.md"
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
FRAMES = {"4h": {"bars": 4, "horizon_bars": 6, "horizon_h": 24}, "1d": {"bars": 24, "horizon_bars": 7, "horizon_h": 168}}
K_BARS = 3                      # bougies de confirmation à droite (et de référence à gauche)
MAX_AGE_BARS = 100              # un pivot plus vieux n'est plus un niveau
TOUCH = 0.005                   # rebond : plus bas ≤ support × (1 + 0,5 %)
CONDITIONS = {
    "K1_SUPPORT_BOUNCE": "plus bas de t ≤ dernier pivot bas confirmé × 1,005, clôture au-dessus du support et haussière, "
                         "clôture de t−1 au-dessus du support ; un événement par niveau",
    "K2_RESISTANCE_BREAK": "première clôture au-dessus du dernier pivot haut confirmé (clôture de t−1 ≤ résistance) ; "
                           "un événement par niveau",
}
N_TRIALS = len(CONDITIONS) * len(FRAMES)
AUDIT_PICKS = 4


class LeakAuditFailed(RuntimeError):
    pass


@dataclass
class PivotResult:
    run_id: str
    period_end: str
    cost_hurdle_pct: float
    n_trials: int = N_TRIALS
    program_trials: int = 0
    leak_audit: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    rows: list[ScreenRow] = field(default_factory=list)


# --- Bougies agrégées et pivots ----------------------------------------------------------------------------------

def aggregate(h1: pd.DataFrame, bars: int) -> pd.DataFrame:
    """Blocs de `bars` bougies 1 h alignés UTC (4 h : 00, 04, … ; 1 jour : 00:00), complets seulement."""
    ordered = h1.sort_values("open_time").reset_index(drop=True)
    times = pd.to_datetime(ordered["open_time"], utc=True)
    block = times.dt.floor(f"{bars}h")
    grouped = ordered.assign(_block=block).groupby("_block")
    out = grouped.agg(hours=("close", "size"), open=("open", "first"), high=("high", "max"), low=("low", "min"),
                      close=("close", "last"), available_at=("available_at", "last"))
    out = out[out["hours"] == bars].drop(columns="hours").reset_index().rename(columns={"_block": "open_time"})
    return out


def pivot_levels(high: np.ndarray, low: np.ndarray, *, k: int = K_BARS, max_age: int = MAX_AGE_BARS,
                 leaky: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Niveaux utilisables à chaque bougie t : dernier pivot haut (résistance) et dernier pivot bas (support)
    confirmés AVANT t, avec l'indice de la bougie pivot (−1 sans niveau). Un pivot i est confirmé à la clôture de
    i + k, utilisable dès i + k + 1 ; `leaky` (mutation de l'audit) l'utilise dès i + 1, avant ses bougies de
    confirmation : il lit alors le futur."""
    n = len(high)
    res_level, sup_level = np.full(n, np.nan), np.full(n, np.nan)
    res_index, sup_index = np.full(n, -1), np.full(n, -1)
    pivots_high: list[int] = []
    pivots_low: list[int] = []
    for i in range(k, n - k):
        before, after = slice(i - k, i), slice(i + 1, i + k + 1)
        if high[i] > high[before].max() and high[i] >= high[after].max():
            pivots_high.append(i)
        if low[i] < low[before].min() and low[i] <= low[after].min():
            pivots_low.append(i)
    usable = 1 if leaky else k + 1
    for pivots, levels, indices, source in ((pivots_high, res_level, res_index, high), (pivots_low, sup_level, sup_index, low)):
        for i in pivots:
            start, stop = i + usable, min(n, i + usable + max_age)
            if start < n:
                levels[start:stop] = source[i]
                indices[start:stop] = i
    return res_level, res_index, sup_level, sup_index


def events_of(bars: pd.DataFrame, *, leaky: bool = False) -> dict[str, np.ndarray]:
    """Événements K1 et K2 à la clôture de chaque bougie, un seul par niveau."""
    o, h, lo, c = (bars[col].to_numpy(float) for col in ("open", "high", "low", "close"))
    res, res_i, sup, sup_i = pivot_levels(h, lo, leaky=leaky)
    n = len(c)
    bounce, breakout = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    used_sup, used_res = -1, -1
    for t in range(1, n):
        if sup_i[t] >= 0 and sup_i[t] != used_sup and lo[t] <= sup[t] * (1 + TOUCH) and c[t] > sup[t] and c[t] > o[t] and c[t - 1] > sup[t]:
            bounce[t], used_sup = True, sup_i[t]
        if res_i[t] >= 0 and res_i[t] != used_res and c[t] > res[t] and c[t - 1] <= res[t]:
            breakout[t], used_res = True, res_i[t]
    return {"K1_SUPPORT_BOUNCE": bounce, "K2_RESISTANCE_BREAK": breakout}


def collect(bars: pd.DataFrame, events: np.ndarray, horizon_bars: int, symbol: str) -> pd.DataFrame:
    fwd = forward_returns(bars, horizon_bars)
    times = pd.to_datetime(bars["open_time"], utc=True)
    valid = events & ~np.isnan(fwd) & (times >= FIRST_DAY).to_numpy()
    drift = float(np.nanmean(fwd[(times >= FIRST_DAY).to_numpy()])) if valid.any() else np.nan
    return pd.DataFrame({"time": times.to_numpy()[valid], "symbol": symbol, "ret": fwd[valid], "excess": fwd[valid] - drift})


# --- Audit des fuites -----------------------------------------------------------------------------------------------

def leak_audit(frames: dict[str, pd.DataFrame], *, seed: int, picks: int = AUDIT_PICKS) -> dict:
    """Pour chaque cadre, `picks` pivots confirmés tirés au hasard ; coupe des bougies 1 h juste après la bougie
    i + 1 (le pivot i n'a pas encore ses bougies de confirmation) : niveaux et événements honnêtes identiques jusqu'à
    la coupe ; mutation (pivot utilisé dès i + 1) : les niveaux tronqués doivent différer des niveaux complets."""
    rng = np.random.default_rng(seed)
    violations: list[dict] = []
    detected = dict.fromkeys(FRAMES, False)
    moments: list[str] = []
    symbols = sorted(frames)
    for name, spec in FRAMES.items():
        for _ in range(picks):
            symbol = symbols[int(rng.integers(len(symbols)))]
            h1 = frames[symbol]
            full = aggregate(h1, spec["bars"])
            if len(full) < 300:
                continue
            high, low = full["high"].to_numpy(float), full["low"].to_numpy(float)
            res, res_i, sup, sup_i = pivot_levels(high, low)
            pivots = sorted(set(res_i[res_i >= 0].tolist()) | set(sup_i[sup_i >= 0].tolist()))
            candidates = [i for i in pivots if len(full) // 3 <= i < len(full) - 2]
            if not candidates:
                continue
            pivot = int(rng.choice(candidates))
            moment = full["open_time"].iloc[pivot + 2]
            moments.append(f"{name} {symbol} pivot {pivot} coupe {moment}")
            part = aggregate(h1[pd.to_datetime(h1["open_time"], utc=True) < moment], spec["bars"])
            cut_res, _, cut_sup, _ = pivot_levels(part["high"].to_numpy(float), part["low"].to_numpy(float))
            for label, whole, cut in (("resistance", res, cut_res), ("support", sup, cut_sup)):
                if not np.array_equal(whole[: len(part)], cut, equal_nan=True):
                    violations.append({"frame": name, "symbol": symbol, "moment": str(moment), "level": label})
            honest_full, honest_cut = events_of(full), events_of(part)
            for condition in CONDITIONS:
                if not np.array_equal(honest_full[condition][: len(part)], honest_cut[condition]):
                    violations.append({"frame": name, "symbol": symbol, "moment": str(moment), "condition": condition})
            leaky = pivot_levels(high, low, leaky=True)
            leaky_cut = pivot_levels(part["high"].to_numpy(float), part["low"].to_numpy(float), leaky=True)
            detected[name] |= any(not np.array_equal(whole[: len(part)], cut, equal_nan=True)
                                  for whole, cut in ((leaky[0], leaky_cut[0]), (leaky[2], leaky_cut[2])))
    return {"violations": violations, "mutation_detected": detected, "moments": moments,
            "passed": not violations and all(detected.values())}


# --- Exécution ---------------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False) -> PivotResult:
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    result = PivotResult(new_run_id("SCREEN"), end.isoformat(), round(hurdle_pct, 4))
    say("données")
    frames = fa.load_frames(settings, symbols, end)
    result.data_hashes = {s: fingerprint(f) for s, f in frames.items()}
    say("audit des fuites")
    result.leak_audit = leak_audit(frames, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    collected: dict[tuple[str, str], list[pd.DataFrame]] = {(c, f): [] for c in CONDITIONS for f in FRAMES}
    bars_count: dict[str, dict[str, int]] = {name: {} for name in FRAMES}
    for symbol, h1 in frames.items():
        say(symbol)
        for name, spec in FRAMES.items():
            bars = aggregate(h1, spec["bars"])
            bars_count[name][symbol] = int(len(bars))
            events = events_of(bars)
            for condition, flags in events.items():
                collected[(condition, name)].append(collect(bars, flags, spec["horizon_bars"], symbol))
    result.coverage = {"bars": bars_count, "first": str(FIRST_DAY)[:10]}
    for (condition, name), parts in collected.items():
        frame = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) \
            else pd.DataFrame(columns=["time", "symbol", "ret", "excess"])
        if not frame.empty:
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
        row = _row(f"{condition}_{name.upper()}", FRAMES[name]["horizon_h"], frame, hurdle_pct, settings)
        result.rows.append(row)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, code=state)
    return result


def _record(settings: Settings, result: PivotResult, *, now: datetime, symbols: list[str], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"conditions": CONDITIONS, "frames": FRAMES, "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="criblage K : un rebond sur un pivot bas confirmé ou une cassure d'un pivot haut confirmé, en 4 h et en 1 jour, "
                   "donne-t-il un avantage après dérive et coûts ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="conditions figées (docs/SCREENING.md, criblage K)",
        params={"frames": FRAMES, "conditions": CONDITIONS, "k_bars": K_BARS, "max_age_bars": MAX_AGE_BARS, "touch": TOUCH},
        period_label="DEVELOPMENT", period_start=str(FIRST_DAY)[:10], period_end=result.period_end, universe=symbols,
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="central (seuil aller-retour)",
        simulation_rules={"entry": "ouverture t+1", "exit": "clôture t+h (6 bougies 4 h, 7 bougies 1 j)", "stops": "aucun"},
        metrics={"n_trials": N_TRIALS, "program_trials": result.program_trials, "cost_hurdle_pct": result.cost_hurdle_pct,
                 "coverage": result.coverage, "rows": [asdict(r) for r in result.rows]}, status="COMPLETED", report_dir=str(report_dir))
