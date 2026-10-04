"""Supports et résistances mécaniques contre niveaux placebo (docs/NIVEAUX.md, déclaré le 2026-10-04 avant code et
exécution ; méthode d'Osler, 2000) : rejet au contact d'une résistance, cassure au-dessus, issues à barrières
symétriques de 1 ATR sur 24 bougies, 1 h et 4 h, 40 paires de recherche, DEVELOPMENT seulement. 4 comparaisons."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from ..patterns.primitives import ZIGZAG_M, atr, zigzag
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .universe import RESEARCH_UNIVERSE

KIND = "LEVEL_REACTION"
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
TIMEFRAMES = {"1h": 1, "4h": 4}
WINDOW = 300
MERGE_ATR = 0.5
NEAR_ATR = 0.1
BARRIER_ATR = 1.0
HORIZON = 24
PLACEBO_SHIFT = (1.0, 3.0)
DECISIVE = ("rejet_resistance", "cassure_resistance")
DESCRIPTIVE = ("rebond_support", "cassure_support")
N_TRIALS = len(DECISIVE) * len(TIMEFRAMES)
LEVEL = 1 - 0.05 / N_TRIALS
BLOCK_DAYS, SAMPLES, SEED = 7, 10_000, 20261004
UP, DOWN, NULL = 1, -1, 0
EFFECT, INVERSE, NOTHING = "EFFET", "EFFET_INVERSE", "RIEN"


@dataclass(frozen=True)
class Level:
    key: tuple                  # identité : indices des pivots (réel) ou (indices, signe) (placebo)
    price: float
    placebo: bool
    start: int                  # première bougie où il est actif
    end: int                    # première bougie où il ne l'est plus
    side: int = 0               # placebo : +1 au-dessus du niveau réel, −1 en dessous ; réel : 0


def aggregate(h1: pd.DataFrame, hours: int) -> pd.DataFrame:
    frame = h1[["open_time", "open", "high", "low", "close"]].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.sort_values("open_time").reset_index(drop=True)
    if hours == 1:
        return frame
    step = pd.Timedelta(hours=hours)
    frame["bucket"] = frame["open_time"].dt.floor(step)
    grouped = frame.groupby("bucket")
    out = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                      count=("open", "size")).reset_index()
    out = out[out["count"] == hours].rename(columns={"bucket": "open_time"})
    return out[["open_time", "open", "high", "low", "close"]].reset_index(drop=True)


def clusters(points: list[tuple[float, int]], tolerance: float) -> list[tuple[tuple[int, ...], float]]:
    """(identité = indices triés des pivots, prix moyen) des groupes de pivots proches de moins de `tolerance`."""
    out: list[list] = []
    for price, index in sorted(points):
        if out and price - out[-1][2] <= tolerance:
            out[-1][0].append(index)
            out[-1][1].append(price)
            out[-1][2] = price
        else:
            out.append([[index], [price], price])
    return [(tuple(sorted(idx)), float(np.mean(prices))) for idx, prices, _ in out]


def _shifts(symbol: str, timeframe: str, key: tuple) -> tuple[float, float]:
    digest = hashlib.sha256(f"{symbol}:{timeframe}:{key}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
    return tuple(rng.uniform(*PLACEBO_SHIFT, size=2))           # type: ignore[return-value]


def levels_over_time(high, low, atr_values, m: float, *, symbol: str, timeframe: str) -> list[Level]:
    """Niveaux réels et placebo, chacun avec sa durée de vie ; à la bougie t, seuls les pivots connus à la clôture de
    t − 1 (known_at ≤ t − 1) et d'indice ≥ t − 300 comptent."""
    high, low, atr_values = (np.asarray(v, dtype=float) for v in (high, low, atr_values))
    n = len(high)
    pivots = sorted(zigzag(high, low, atr_values, m), key=lambda p: p.known_at)
    known: list = []
    pointer = 0
    active: dict[tuple, dict] = {}
    out: list[Level] = []
    previous_signature = None
    for t in range(1, n):
        while pointer < len(pivots) and pivots[pointer].known_at <= t - 1:
            known.append(pivots[pointer])
            pointer += 1
        window = [p for p in known if p.index >= t - WINDOW]
        signature = tuple(p.index for p in window)
        if signature == previous_signature:
            continue
        previous_signature = signature
        a = atr_values[t - 1]
        if not np.isfinite(a) or a <= 0:
            current: dict[tuple, dict] = {}
        else:
            current = {}
            real = clusters([(p.price, p.index) for p in window], MERGE_ATR * a)
            real_prices = [price for _, price in real]
            for key, price in real:
                if key in active:                       # niveau qui continue : ses placebos aussi (ou aucun)
                    current[key] = active[key]
                    for sign in (1, -1):
                        if (key, sign) in active:
                            current[(key, sign)] = active[(key, sign)]
                    continue
                # Niveau qui naît : ses placebos naissent avec lui ou jamais (écartés une fois pour toutes).
                current[key] = {"price": price, "placebo": False, "start": t, "side": 0}
                for sign, shift in zip((1, -1), _shifts(symbol, timeframe, key), strict=True):
                    pprice = price + sign * shift * a
                    if pprice > 0 and all(abs(pprice - r) >= MERGE_ATR * a for r in real_prices):
                        current[(key, sign)] = {"price": pprice, "placebo": True, "start": t, "side": sign}
        for key, info in active.items():
            if key not in current:
                out.append(Level(key, info["price"], info["placebo"], info["start"], t, info["side"]))
        active = current
    for key, info in active.items():
        out.append(Level(key, info["price"], info["placebo"], info["start"], n, info["side"]))
    return out


def outcome(high, low, close, atr_values, t: int, horizon: int = HORIZON) -> int:
    """« haut d'abord » (+1), « bas d'abord » (−1) ou nulle (0 : les deux dans la même bougie, ou aucune)."""
    up, down = close[t] + BARRIER_ATR * atr_values[t], close[t] - BARRIER_ATR * atr_values[t]
    for k in range(t + 1, min(len(close), t + 1 + horizon)):
        hit_up, hit_down = high[k] >= up, low[k] <= down
        if hit_up and hit_down:
            return NULL
        if hit_up:
            return UP
        if hit_down:
            return DOWN
    return NULL


def events(level: Level, high, low, close, atr_values) -> list[tuple[str, int]]:
    """Premier événement de chaque type sur la durée de vie du niveau : (type, bougie)."""
    t0, t1 = max(level.start, 1), level.end
    if t1 <= t0:
        return []
    idx = np.arange(t0, t1)
    lv, a = level.price, atr_values[idx]
    prev, h, lo, c = close[idx - 1], high[idx], low[idx], close[idx]
    below, above = prev < lv, prev > lv
    found = []
    for name, cond in (
            ("rejet_resistance", below & (h >= lv - NEAR_ATR * a) & (c <= lv)),
            ("cassure_resistance", below & (c > lv + NEAR_ATR * a)),
            ("rebond_support", above & (lo <= lv + NEAR_ATR * a) & (c >= lv)),
            ("cassure_support", above & (c < lv - NEAR_ATR * a))):
        hit = np.flatnonzero(cond & np.isfinite(a))
        if len(hit):
            found.append((name, int(idx[hit[0]])))
    return found


SUCCESS = {"rejet_resistance": DOWN, "cassure_resistance": UP, "rebond_support": UP, "cassure_support": DOWN}


EVENT_COLUMNS = ["symbol", "timeframe", "event", "placebo", "side", "time", "outcome", "success", "age", "atr_pct", "in_range"]


def pair_events(bars: pd.DataFrame, timeframe: str, symbol: str, *, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Événements d'une paire, avec les variables des contrôles descriptifs : âge du niveau au contact (bougies),
    ATR / clôture au contact, niveau dans la plage [plus bas ; plus haut] des 300 bougies précédentes ou hors plage."""
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(h, lo, c)
    rows = []
    times = bars["open_time"]
    for level in levels_over_time(h, lo, a, ZIGZAG_M[timeframe], symbol=symbol, timeframe=timeframe):
        for name, t in events(level, h, lo, c, a):
            when = times.iloc[t]
            if not start <= when <= end:
                continue
            result = outcome(h, lo, c, a, t)
            first = max(0, t - WINDOW)
            in_range = bool(lo[first:t].min() <= level.price <= h[first:t].max()) if t > first else False
            rows.append({"symbol": symbol, "timeframe": timeframe, "event": name, "placebo": bool(level.placebo),
                         "side": int(level.side), "time": when, "outcome": int(result),
                         "success": None if result == NULL else bool(result == SUCCESS[name]),
                         "age": int(t - level.start), "atr_pct": float(a[t] / c[t]), "in_range": in_range})
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    return frame.astype({"placebo": bool, "side": int, "outcome": int, "age": int, "atr_pct": float, "in_range": bool})


def compare(frame: pd.DataFrame, *, level: float = LEVEL, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """Écart de taux de réussite réel − placebo et IC par blocs de 7 jours (issues nulles exclues, comptées)."""
    frame = frame.astype({"placebo": bool})
    used = frame[frame["success"].notna()].copy()
    out = {"real_events": int((~frame["placebo"]).sum()), "placebo_events": int(frame["placebo"].sum()),
           "null_real": int(((~frame["placebo"]) & frame["success"].isna()).sum()),
           "null_placebo": int((frame["placebo"] & frame["success"].isna()).sum())}
    if used.empty or used["placebo"].all() or (~used["placebo"]).all():
        return out | {"diff": None, "ci": None}
    used["success"] = used["success"].astype(float)
    used["block"] = (pd.to_datetime(used["time"], utc=True) - FIRST_DAY).dt.days // BLOCK_DAYS
    table = used.groupby(["block", "placebo"])["success"].agg(["sum", "count"]).unstack("placebo", fill_value=0)
    s_real, n_real = table[("sum", False)].to_numpy(), table[("count", False)].to_numpy()
    s_pl, n_pl = table[("sum", True)].to_numpy(), table[("count", True)].to_numpy()
    real_rate, pl_rate = s_real.sum() / n_real.sum(), s_pl.sum() / n_pl.sum()
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(table), size=(samples, len(table)))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = s_real[pick].sum(1) / n_real[pick].sum(1) - s_pl[pick].sum(1) / n_pl[pick].sum(1)
    diffs = diffs[np.isfinite(diffs)]
    alpha = 1 - level
    ci = [float(np.quantile(diffs, alpha / 2)), float(np.quantile(diffs, 1 - alpha / 2))] if len(table) >= 20 else None
    years = used.assign(year=pd.to_datetime(used["time"], utc=True).dt.year).groupby(["year", "placebo"])["success"].mean().unstack()
    pairs = used.groupby(["symbol", "placebo"])["success"].mean().unstack().dropna()
    return out | {"real_rate": round(float(real_rate), 4), "placebo_rate": round(float(pl_rate), 4),
                  "diff": round(float(real_rate - pl_rate), 4), "ci": [round(x, 4) for x in ci] if ci else None,
                  "blocks": int(len(table)),
                  "years_positive": f"{int((years[False] > years[True]).sum())}/{int(years.notna().all(axis=1).sum())}",
                  "pairs_positive_share": round(float((pairs[False] > pairs[True]).mean()), 3)}


def _rate(part: pd.DataFrame) -> float | None:
    used = part["success"].dropna()
    return round(float(used.astype(float).mean()), 4) if len(used) else None


def controls(frame: pd.DataFrame) -> dict:
    """Contrôles DESCRIPTIFS d'équité réel / placebo (relecture du 2026-10-04) : aucun n'entre dans le verdict."""
    frame = frame.astype({"placebo": bool})
    real, placebo = frame[~frame["placebo"]], frame[frame["placebo"]]
    out: dict = {"rate_placebo_above": _rate(placebo[placebo["side"] > 0]),
                 "rate_placebo_below": _rate(placebo[placebo["side"] < 0])}
    for flag, name in ((True, "in_range"), (False, "out_of_range")):
        out[f"rate_real_{name}"] = _rate(real[real["in_range"] == flag])
        out[f"rate_placebo_{name}"] = _rate(placebo[placebo["in_range"] == flag])
    for column in ("age", "atr_pct"):
        out[f"median_{column}_real"] = float(real[column].median()) if len(real) else None
        out[f"median_{column}_placebo"] = float(placebo[column].median()) if len(placebo) else None
    used = frame[frame["success"].notna()].copy()
    if len(used) and used["placebo"].any() and (~used["placebo"]).any():
        used["success"] = used["success"].astype(float)
        used["cell"] = (pd.qcut(used["age"].rank(method="first"), 10, labels=False).astype(str) + ":"
                        + pd.qcut(used["atr_pct"].rank(method="first"), 10, labels=False).astype(str))
        cells = used.groupby(["cell", "placebo"])["success"].agg(["mean", "count"]).unstack("placebo")
        cells = cells.dropna()
        weights = cells[("count", False)] / cells[("count", False)].sum()
        out["diff_reweighted_age_atr"] = round(float((weights * (cells[("mean", False)] - cells[("mean", True)])).sum()), 4)
        dedup = used.drop_duplicates(["symbol", "time", "placebo"])
        out["diff_dedup_bar"] = round(float(dedup[~dedup["placebo"]]["success"].mean() - dedup[dedup["placebo"]]["success"].mean()), 4)
    nulls_fail = frame.assign(s=frame["success"].map(lambda x: 0.0 if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)))
    out["diff_nulls_as_failures"] = round(float(nulls_fail[~nulls_fail["placebo"]]["s"].mean()
                                                - nulls_fail[nulls_fail["placebo"]]["s"].mean()), 4) if len(real) and len(placebo) else None
    return out


def verdict(row: dict) -> str:
    ci = row.get("ci")
    if not ci:
        return NOTHING
    return EFFECT if ci[0] > 0 else INVERSE if ci[1] < 0 else NOTHING


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        symbols: list[str] | None = None) -> dict:
    from .long_history import load_long
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    from .derivatives_screen import fingerprint
    frames = []
    missing = []
    hashes: dict[str, str] = {}
    for symbol in symbols or list(RESEARCH_UNIVERSE):
        try:
            h1 = load_long(settings, symbol)
        except MissingData:
            missing.append(symbol)
            continue
        h1 = h1[pd.to_datetime(h1["open_time"], utc=True) <= end]
        hashes[symbol] = fingerprint(h1[["open_time", "open", "high", "low", "close"]].reset_index(drop=True))
        for timeframe, hours in TIMEFRAMES.items():
            say(f"{symbol} {timeframe}")
            frames.append(pair_events(aggregate(h1, hours), timeframe, symbol, start=FIRST_DAY, end=end))
    events_all = pd.concat(frames, ignore_index=True)
    rows: dict = {}
    for timeframe in TIMEFRAMES:
        for name in (*DECISIVE, *DESCRIPTIVE):
            part = events_all[(events_all["timeframe"] == timeframe) & (events_all["event"] == name)]
            result = compare(part) | {"controles": controls(part)}
            if name in DECISIVE:
                result["verdict"] = verdict(result)
            rows[f"{timeframe}/{name}"] = result
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("LEVELS")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6),
               "rows": rows, "missing": missing, "doc": "docs/NIVEAUX.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="les supports et résistances mécaniques changent-ils la réaction du prix (rejet, cassure) par "
                               "rapport à des niveaux placebo ?",
                    strategy="LEVEL_REACTION", strategy_version=1, variant="définitions figées (docs/NIVEAUX.md)",
                    params={"window": WINDOW, "merge_atr": MERGE_ATR, "near_atr": NEAR_ATR, "barrier_atr": BARRIER_ATR,
                            "horizon": HORIZON, "placebo_shift": PLACEBO_SHIFT, "zigzag_m": ZIGZAG_M,
                            "block_days": BLOCK_DAYS, "samples": SAMPLES, "seed": SEED},
                    period_label="DEVELOPMENT", period_start=str(FIRST_DAY.date()), period_end=end.isoformat(),
                    universe=sorted(set(events_all["symbol"])), data_hashes=hashes, git_commit=state,
                    dependencies=dependency_versions(), seed=SEED, cost_scenario="aucun (probabilités)",
                    simulation_rules={"outcome": "barrières ±1 ATR, 24 bougies, nulle exclue"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "rows": rows}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    events_all.to_parquet(report_dir / "events.parquet", index=False)
    return payload
