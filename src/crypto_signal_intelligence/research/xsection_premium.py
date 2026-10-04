"""Primes coréenne et Coinbase des altcoins en coupe hebdomadaire (docs/XSECTION_PRIMES.md, déclaré le 2026-10-04 avant
code et exécution) : chaque lundi, paires classées par leur prime (niveau sur 7 jours, ou saut par rapport aux 28 jours
précédents) ; écart de rendement de la semaine suivante entre le tiers haut et le tiers bas. 4 comparaisons,
DEVELOPMENT seulement ; magasin de contexte (lignes HISTORIQUE)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from ..context.store import HISTORY, path_for
from ..context.views import wide
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .intervals import calendar_mean_ci
from .protocol import development_end
from .xsection import holding_returns

KIND = "XSECTION_PREMIUM"
VARIABLES = ("KR_LEVEL", "KR_JUMP", "CB_LEVEL", "CB_JUMP")
N_TRIALS = len(VARIABLES)
LEVEL = 1 - 0.05 / N_TRIALS
EXCLUDED = ("BTC", "ETH", "PAXG")
SHORT, SHORT_MIN = 7, 5
LONG, LONG_MIN = 28, 20
MIN_PAIRS, MIN_PER_TIER = 10, 3
#: Prime hors de [−50 % ; +100 %] : erreur de données (deux jetons sous le même symbole, changement d'unité ; STRAX
#: à −90 % sur 73 % des jours), ignorée. Règle fixée le 2026-10-04 sur les seules primes, avant tout rendement.
PREMIUM_BOUNDS = (-0.5, 1.0)
BLOCK_DAYS, MIN_BLOCKS = 56, 10
DAY = pd.Timedelta(days=1)
UP, DOWN, NOTHING = "HAUT_MIEUX", "HAUT_MOINS", "RIEN"
SERIES = ("binance_daily", "upbit", "coinbase", "ecb")


def _days(frame: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    return frame[~frame.index.duplicated(keep="last")].reindex(index)


def daily_premiums(raw: dict[str, pd.DataFrame], end: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """(primes journalières {« KR », « CB »} : jour × paire, clôtures Binance : jour × paire), données ≤ `end`."""
    def cut(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame.index <= end]

    binance = cut(raw["binance_daily:close"])
    binance = binance[[c for c in binance.columns if c.endswith("USDT")]].rename(columns=lambda c: c[:-4])
    binance = binance[[c for c in binance.columns if c not in EXCLUDED]]
    index = pd.date_range(binance.index.min(), end.floor("D"), freq="D", tz="UTC")
    binance = _days(binance, index)
    rates = cut(raw["ecb:per_eur"])
    krw_per_usd = (rates["KRW"] / rates["USD"]).reindex(index.union(rates.index)).ffill().reindex(index)
    upbit = _days(cut(raw["upbit:close_krw"]), index)
    coinbase = _days(cut(raw["coinbase:close_usd"]), index)
    kr_cols = [c for c in upbit.columns if c in binance.columns]
    cb_cols = [c for c in coinbase.columns if c in binance.columns]
    korea = upbit[kr_cols].div(krw_per_usd, axis=0) / binance[kr_cols] - 1
    usa = coinbase[cb_cols] / binance[cb_cols] - 1
    low, high = PREMIUM_BOUNDS
    clean = {name: frame.where((frame > low) & (frame < high)) for name, frame in (("KR", korea), ("CB", usa))}
    return clean, binance


def weekly_variables(premiums: dict[str, pd.DataFrame], mondays: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Variables au lundi T (journées jusqu'au dimanche T − 1) : niveau sur 7 jours ; saut = niveau − moyenne des
    28 jours T − 35 à T − 8."""
    out = {}
    for prefix, prem in premiums.items():
        short = prem.rolling(SHORT, min_periods=SHORT_MIN).mean()
        long = prem.rolling(LONG, min_periods=LONG_MIN).mean()
        level = short.reindex(mondays - DAY)
        level.index = mondays
        before = long.reindex(mondays - 8 * DAY)
        before.index = mondays
        out[f"{prefix}_LEVEL"] = level
        out[f"{prefix}_JUMP"] = level - before
    return out


def weekly_spreads(variable: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    """Par semaine : nombre de paires, écart tiers haut − tiers bas, tiers haut − moyenne, tiers bas − moyenne,
    corrélation de rang (paires avec variable et rendement ; au moins 10 paires et 3 par tiers)."""
    rows = []
    for monday in variable.index:
        if monday not in returns.index:
            continue
        x, r = variable.loc[monday], returns.loc[monday]
        ok = x.notna() & r.notna() & np.isfinite(x) & np.isfinite(r)
        n = int(ok.sum())
        if n < MIN_PAIRS:
            continue
        order = x[ok].sort_values(kind="mergesort").index           # égalités : ordre stable des colonnes
        k = n // 3
        if k < MIN_PER_TIER:
            continue
        bottom, top = r[order[:k]], r[order[-k:]]
        mean = r[ok].mean()
        rows.append({"monday": monday, "pairs": n, "spread": float(top.mean() - bottom.mean()),
                     "top_minus_all": float(top.mean() - mean), "bottom_minus_all": float(bottom.mean() - mean),
                     "rank_corr": float(x[ok].rank().corr(r[ok].rank()))})
    return pd.DataFrame(rows, columns=["monday", "pairs", "spread", "top_minus_all", "bottom_minus_all", "rank_corr"])


def summarize(weeks: pd.DataFrame, *, level: float = LEVEL) -> dict:
    if weeks.empty:
        return {"weeks": 0, "mean_spread_pct": None, "ci_pct": None}
    times = pd.to_datetime(weeks["monday"], utc=True)
    ci = calendar_mean_ci(weeks["spread"].to_numpy(float), times, block_days=BLOCK_DAYS, min_blocks=MIN_BLOCKS, level=level)
    by_year = weeks.assign(year=times.dt.year).groupby("year")["spread"].mean()
    middle = times.min() + (times.max() - times.min()) / 2
    return {"weeks": int(len(weeks)), "pairs_median": float(weeks["pairs"].median()),
            "first": str(times.min().date()), "last": str(times.max().date()),
            "mean_spread_pct": round(float(weeks["spread"].mean()) * 100, 4),
            "ci_pct": [round(c * 100, 4) for c in ci] if ci else None,
            "top_minus_all_pct": round(float(weeks["top_minus_all"].mean()) * 100, 4),
            "bottom_minus_all_pct": round(float(weeks["bottom_minus_all"].mean()) * 100, 4),
            "rank_corr_mean": round(float(weeks["rank_corr"].mean()), 4),
            "spread_by_year_pct": {str(y): round(float(v) * 100, 3) for y, v in by_year.items()},
            "spread_halves_pct": [round(float(weeks.loc[(times <= middle).to_numpy(), "spread"].mean()) * 100, 3),
                                  round(float(weeks.loc[(times > middle).to_numpy(), "spread"].mean()) * 100, 3)]}


def verdict(row: dict) -> str:
    ci = row.get("ci_pct")
    if not ci:
        return NOTHING
    return UP if ci[0] > 0 else DOWN if ci[1] < 0 else NOTHING


def load_raw(settings: Settings) -> dict[str, pd.DataFrame]:
    needed = {"binance_daily": "close", "upbit": "close_krw", "coinbase": "close_usd", "ecb": "per_eur"}
    raw = {}
    for series, field in needed.items():
        if not path_for(settings, series).exists():
            raise FileNotFoundError(f"série de contexte absente : {series} (lancer `csi context-backfill`)")
        raw[f"{series}:{field}"] = wide(settings, series, field, kind=HISTORY)
    return raw


def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        raw: dict[str, pd.DataFrame] | None = None) -> dict:
    from .derivatives_screen import fingerprint
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    raw = raw if raw is not None else load_raw(settings)
    hashes = {name: fingerprint(frame[frame.index <= end].reset_index()) for name, frame in raw.items()}
    files = {name: hashlib.sha256(path_for(settings, name).read_bytes()).hexdigest()
             for name in SERIES if path_for(settings, name).exists()}
    say("primes")
    premiums, closes = daily_premiums(raw, end)
    first_monday = closes.index.min() + ((7 - closes.index.min().dayofweek) % 7) * DAY
    mondays = pd.date_range(first_monday, end.floor("D"), freq="7D", tz="UTC")
    returns = holding_returns(closes, mondays)
    returns = returns[returns.index + 7 * DAY <= end.floor("D")]          # sortie (lundi suivant) dans DEVELOPMENT
    variables = weekly_variables(premiums, mondays)
    rows: dict = {}
    for name in VARIABLES:
        say(name)
        weeks = weekly_spreads(variables[name], returns)
        row = summarize(weeks)
        row["verdict"] = verdict(row)
        rows[name] = row
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("XPREM")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6), "rows": rows,
               "data_hashes": hashes, "store_files_sha256": files, "doc": "docs/XSECTION_PRIMES.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="la prime coréenne ou Coinbase d'un altcoin (niveau, saut) annonce-t-elle sa semaine suivante "
                               "par rapport aux autres ?",
                    strategy="XSECTION_PREMIUM", strategy_version=1, variant="variables figées (docs/XSECTION_PRIMES.md)",
                    params={"variables": VARIABLES, "short": [SHORT, SHORT_MIN], "long": [LONG, LONG_MIN],
                            "min_pairs": MIN_PAIRS, "min_per_tier": MIN_PER_TIER, "block_days": BLOCK_DAYS,
                            "min_blocks": MIN_BLOCKS, "level": LEVEL},
                    period_label="DEVELOPMENT", period_start=min((r.get("first") or "9999") for r in rows.values()),
                    period_end=end.isoformat(), universe=sorted(set(premiums["KR"].columns) | set(premiums["CB"].columns)),
                    data_hashes=hashes, git_commit=state, dependencies=dependency_versions(), seed=0,
                    cost_scenario="aucun (écart de rendement)",
                    simulation_rules={"entry": "clôture du lundi (décision sur le dimanche)", "exit": "clôture du lundi suivant"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "rows": rows}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return payload
