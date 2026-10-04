"""Données de contexte contre direction de BTC et ETH (docs/CONTEXTE_PREDICTION.md, déclaré le 2026-10-04 avant code
et exécution) : 7 variables (flux vers les plateformes, primes Coinbase et coréenne, stablecoins, top traders,
Wikipédia), rang sur les 365 jours précédents, écart de rendement tiers haut − tiers bas à 1, 3 et 7 jours.
21 comparaisons, DEVELOPMENT seulement ; données du magasin de contexte (lignes HISTORIQUE)."""
from __future__ import annotations

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
from .protocol import development_end

KIND = "CONTEXT_SCREEN"
HORIZONS = (1, 3, 7)
FEATURES = ("BTC_NETFLOW", "ETH_NETFLOW", "CB_PREMIUM", "KR_PREMIUM", "STABLE_GROWTH", "TOP_TRADERS", "WIKI_ATTENTION")
TARGET = {"BTC_NETFLOW": "BTCUSDT", "ETH_NETFLOW": "ETHUSDT", "CB_PREMIUM": "BTCUSDT", "KR_PREMIUM": "BTCUSDT",
          "STABLE_GROWTH": "BTCUSDT", "TOP_TRADERS": "BTCUSDT", "WIKI_ATTENTION": "BTCUSDT"}
N_TRIALS = len(FEATURES) * len(HORIZONS)
LEVEL = 1 - 0.05 / N_TRIALS
WINDOW, MIN_PRESENT = 7, 5
RANK_DAYS, RANK_MIN = 365, 180
TOP, BOTTOM = 2 / 3, 1 / 3
BLOCK_DAYS, MIN_BLOCKS, MIN_DAYS = 56, 20, 100
SAMPLES, SEED = 10_000, 20261004
DAY = pd.Timedelta(days=1)
UP, DOWN, NOTHING = "HAUSSE_SI_HAUT", "BAISSE_SI_HAUT", "RIEN"
SERIES = ("flows", "coinbase", "upbit", "ecb", "binance_daily", "stablecoins", "ratios_archive", "wikipedia")


def _daily(series: pd.Series) -> pd.Series:
    """Série journalière continue (jours manquants : NaN), index = journée UTC."""
    series = series.dropna().sort_index()
    if series.empty:
        return series
    full = pd.date_range(series.index.min(), series.index.max(), freq="D", tz="UTC")
    return series[~series.index.duplicated(keep="last")].reindex(full)


def at_decision(daily_values: pd.Series, lag: int) -> pd.Series:
    """Valeur du jour d utilisée à la décision T = d + lag (jamais plus tôt)."""
    out = daily_values.copy()
    out.index = out.index + lag * DAY
    return out


def rolling_mean(daily_values: pd.Series) -> pd.Series:
    return _daily(daily_values).rolling(WINDOW, min_periods=MIN_PRESENT).mean()


def rank_vs_past(values: pd.Series, *, days: int = RANK_DAYS, minimum: int = RANK_MIN) -> pd.Series:
    """Rang p ∈ [0, 1] de la valeur du jour T parmi les valeurs des `days` jours de décision précédents (au moins
    `minimum` présentes) : (nombre plus petites + ½ égales) / nombre ; NaN sinon. Le jour T n'entre pas dans sa
    propre référence."""
    values = _daily(values)
    x = values.to_numpy(float)
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        if not np.isfinite(x[i]):
            continue
        past = x[max(0, i - days):i]
        past = past[np.isfinite(past)]
        if len(past) < minimum:
            continue
        out[i] = ((past < x[i]).sum() + 0.5 * (past == x[i]).sum()) / len(past)
    return pd.Series(out, index=values.index)


def build_features(raw: dict[str, pd.DataFrame], end: pd.Timestamp) -> dict[str, pd.Series]:
    """Les 7 variables, indexées par jour de décision T (déjà retardées)."""
    def cut(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame.index <= end] if not frame.empty else frame

    flows_in, flows_out, supply = (cut(raw[f"flows:{f}"]) for f in ("FlowInExNtv", "FlowOutExNtv", "SplyExNtv"))
    out: dict[str, pd.Series] = {}
    for code, asset in (("BTC_NETFLOW", "btc"), ("ETH_NETFLOW", "eth")):
        net = _daily(flows_in[asset] - flows_out[asset]).rolling(WINDOW, min_periods=MIN_PRESENT).sum()
        out[code] = at_decision(net / _daily(supply[asset]).reindex(net.index), 2)
    binance = cut(raw["binance_daily:close"])
    btc = _daily(binance["BTCUSDT"])
    coinbase = _daily(cut(raw["coinbase:close_usd"])["BTC"])
    out["CB_PREMIUM"] = at_decision(rolling_mean(coinbase / btc.reindex(coinbase.index) - 1), 1)
    rates = cut(raw["ecb:per_eur"])
    krw_per_usd = _daily(rates["KRW"] / rates["USD"]).ffill()
    upbit = _daily(cut(raw["upbit:close_krw"])["BTC"])
    korea = upbit / krw_per_usd.reindex(upbit.index) / btc.reindex(upbit.index) - 1
    out["KR_PREMIUM"] = at_decision(rolling_mean(korea), 1)
    stable = _daily(cut(raw["stablecoins:circulating_usd"])["all"])
    out["STABLE_GROWTH"] = at_decision(stable / stable.shift(7) - 1, 2)
    top = cut(raw["ratios_archive:sum_toptrader_long_short_ratio"])["BTCUSDT"]
    out["TOP_TRADERS"] = at_decision(rolling_mean(top), 2)
    views = cut(raw["wikipedia:views"])["Bitcoin"]
    out["WIKI_ATTENTION"] = at_decision(rolling_mean(views), 2)
    return {k: v.dropna() for k, v in out.items()}


def forward_returns(close: pd.Series, horizon: int, *, delay: int = 0) -> pd.Series:
    """Rendement log de la décision T : de la clôture de T − 1 + delay à celle de T + H − 1 + delay ; index = T."""
    close = _daily(close)
    start = np.log(close)
    end = start.shift(-horizon)
    out = (end - start)
    out.index = out.index + DAY                      # la clôture de T − 1 est l'entrée de la décision T
    return out.shift(-delay)


def compare(rank: pd.Series, returns: pd.Series, *, level: float = LEVEL, samples: int = SAMPLES, seed: int = SEED) -> dict:
    frame = pd.DataFrame({"p": rank, "r": returns}).dropna()
    top, bottom = frame[frame["p"] >= TOP], frame[frame["p"] <= BOTTOM]
    out: dict = {"days": int(len(frame)), "days_top": int(len(top)), "days_bottom": int(len(bottom)),
                 "mean_all_pct": round(float(frame["r"].mean()) * 100, 4) if len(frame) else None,
                 "mean_top_pct": round(float(top["r"].mean()) * 100, 4) if len(top) else None,
                 "mean_bottom_pct": round(float(bottom["r"].mean()) * 100, 4) if len(bottom) else None}
    if len(top) < MIN_DAYS or len(bottom) < MIN_DAYS:
        return out | {"diff_pct": None, "ci_pct": None}
    first = frame.index.min()
    block = ((frame.index - first).days // BLOCK_DAYS).to_numpy()
    blocks = np.unique(block)
    is_top, is_bottom = (frame["p"] >= TOP).to_numpy(), (frame["p"] <= BOTTOM).to_numpy()
    r = frame["r"].to_numpy(float)
    st = np.array([r[(block == b) & is_top].sum() for b in blocks])
    nt = np.array([((block == b) & is_top).sum() for b in blocks])
    sb = np.array([r[(block == b) & is_bottom].sum() for b in blocks])
    nb = np.array([((block == b) & is_bottom).sum() for b in blocks])
    diff = st.sum() / nt.sum() - sb.sum() / nb.sum()
    ci = None
    if len(blocks) >= MIN_BLOCKS:
        rng = np.random.default_rng(seed)
        pick = rng.integers(0, len(blocks), size=(samples, len(blocks)))
        with np.errstate(invalid="ignore", divide="ignore"):
            draws = st[pick].sum(1) / nt[pick].sum(1) - sb[pick].sum(1) / nb[pick].sum(1)
        draws = draws[np.isfinite(draws)]
        alpha = 1 - level
        ci = [round(float(np.quantile(draws, alpha / 2)) * 100, 4), round(float(np.quantile(draws, 1 - alpha / 2)) * 100, 4)]
    years = {}
    for year, part in frame.groupby(frame.index.year):
        t, b = part[part["p"] >= TOP]["r"], part[part["p"] <= BOTTOM]["r"]
        if len(t) >= 20 and len(b) >= 20:
            years[str(year)] = round(float(t.mean() - b.mean()) * 100, 3)
    middle = frame.index.min() + (frame.index.max() - frame.index.min()) / 2
    halves = []
    for part in (frame[frame.index <= middle], frame[frame.index > middle]):
        t, b = part[part["p"] >= TOP]["r"], part[part["p"] <= BOTTOM]["r"]
        halves.append(round(float(t.mean() - b.mean()) * 100, 3) if len(t) and len(b) else None)
    return out | {"diff_pct": round(float(diff) * 100, 4), "ci_pct": ci, "blocks": int(len(blocks)),
                  "rank_corr": round(float(frame["p"].corr(frame["r"], method="spearman")), 4),
                  "diff_by_year_pct": years, "diff_halves_pct": halves,
                  "first": str(frame.index.min().date()), "last": str(frame.index.max().date())}


def verdict(row: dict) -> str:
    ci = row.get("ci_pct")
    if not ci:
        return NOTHING
    return UP if ci[0] > 0 else DOWN if ci[1] < 0 else NOTHING


def load_raw(settings: Settings) -> dict[str, pd.DataFrame]:
    needed = {"flows": ("FlowInExNtv", "FlowOutExNtv", "SplyExNtv"), "binance_daily": ("close",), "coinbase": ("close_usd",),
              "upbit": ("close_krw",), "ecb": ("per_eur",), "stablecoins": ("circulating_usd",),
              "ratios_archive": ("sum_toptrader_long_short_ratio",), "wikipedia": ("views",)}
    raw = {}
    for series, fields in needed.items():
        if not path_for(settings, series).exists():
            raise FileNotFoundError(f"série de contexte absente : {series} (lancer `csi context-backfill`)")
        for field in fields:
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
    say("variables")
    features = build_features(raw, end)
    closes = raw["binance_daily:close"]
    closes = closes[closes.index <= end]
    rows: dict = {}
    for code in FEATURES:
        rank = rank_vs_past(features[code])
        for horizon in HORIZONS:
            say(f"{code} {horizon} j")
            returns = forward_returns(closes[TARGET[code]], horizon)
            delayed = forward_returns(closes[TARGET[code]], horizon, delay=1)
            last_decision = end.floor("D") - (horizon - 1) * DAY        # sortie (clôture de T + H − 1) dans DEVELOPMENT
            returns, delayed = returns[returns.index <= last_decision], delayed[delayed.index <= last_decision - DAY]
            row = compare(rank, returns)
            row["verdict"] = verdict(row)
            adverse = compare(rank, delayed, samples=2000)
            row["diff_delayed_entry_pct"] = adverse.get("diff_pct")
            rows[f"{code}/{horizon}j"] = row
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("CTXP")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "level": round(LEVEL, 6), "rows": rows,
               "data_hashes": hashes, "doc": "docs/CONTEXTE_PREDICTION.md"}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="les données de contexte (flux, primes, stablecoins, top traders, attention) annoncent-elles la "
                               "direction de BTC et ETH à 1, 3 et 7 jours ?",
                    strategy="CONTEXT_SCREEN", strategy_version=1, variant="variables figées (docs/CONTEXTE_PREDICTION.md)",
                    params={"features": FEATURES, "horizons": HORIZONS, "window": WINDOW, "min_present": MIN_PRESENT,
                            "rank_days": RANK_DAYS, "rank_min": RANK_MIN, "terciles": [BOTTOM, TOP], "block_days": BLOCK_DAYS,
                            "samples": SAMPLES, "seed": SEED, "level": LEVEL},
                    period_label="DEVELOPMENT", period_start=min((r.get("first") or "9999") for r in rows.values()),
                    period_end=end.isoformat(), universe=sorted(set(TARGET.values())), data_hashes=hashes, git_commit=state,
                    dependencies=dependency_versions(), seed=SEED, cost_scenario="aucun (écart de rendement)",
                    simulation_rules={"entry": "clôture de T − 1 (ouverture de T)", "exit": "clôture de T + H − 1"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "rows": rows}, status="COMPLETED",
                    report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return payload
