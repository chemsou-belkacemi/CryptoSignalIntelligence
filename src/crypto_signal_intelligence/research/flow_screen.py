"""Étape 6 du plan de travail (2026-10-02) — criblage J : flux d'ordres, offre nouvelle, valeur on-chain
(docs/SCREENING.md, section « Criblage J », déclaré avant exécution ; 5 conditions × 3 horizons = 15 essais).

Journées UTC du magasin long (bougies 1 h agrégées), DEVELOPMENT seul. Une condition vraie le jour d (connue à
d+1 00:00) achète à l'ouverture de 01:00 de d+1 et vend à la clôture de 23:00 h−1 jours plus tard ; l'excès est
le rendement moins la dérive de la paire sur le même horizon (toutes les journées). Même lecture que les criblages
D à I : « passe » = rendement brut moyen au-dessus du seuil de coûts ET borne basse de l'IC95 de l'excès > 0 ;
pour une condition de VETO (offre nouvelle, MVRV élevé), « veto justifié » = borne haute de l'IC95 < 0.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from . import factors as fa
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .protocol import development_end
from .screen import ScreenRow, _day_block_ci
from .universe import RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION = "SCREEN", "SCREEN_FLOW_J", 1
DOC = "docs/SCREENING.md"
HORIZONS_DAYS = (1, 7, 30)
FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
FLOW_WINDOW, FLOW_MIN_DAYS = 365, 200
FLOW_TOP, FLOW_BOTTOM = 0.90, 0.10
NEW_SUPPLY_MIN_DAYS, NEW_SUPPLY_MAX_DAYS = 30, 180
MVRV_ASSETS = {"BTCUSDT": "btc", "ETHUSDT": "eth"}
MVRV_WINDOW, MVRV_MIN_DAYS, MVRV_LOW, MVRV_HIGH = 730, 365, 0.20, 0.80
MVRV_LATENCY_DAYS = 2                          # valeur du jour d connue à d + 2 (hypothèse prudente, déclarée)
MVRV_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CONDITIONS = {
    "J1_FLOW_BUY_TOP": "part des achats au marché (taker) dans le volume du jour ≥ 90e centile des 365 jours précédents de la paire",
    "J2_FLOW_SELL_BOTTOM": "part des achats au marché ≤ 10e centile des 365 jours précédents (pression vendeuse, achat à contre-courant)",
    "J3_NEW_SUPPLY_VETO": "paire cotée sur Binance depuis 30 à 180 jours (offre nouvelle) — condition de veto",
    "J4_MVRV_LOW": "MVRV (CoinMetrics, communauté) de BTC ou d'ETH ≤ 20e centile des 730 jours précédents, connu 2 jours plus tard",
    "J5_MVRV_HIGH_VETO": "MVRV ≥ 80e centile des 730 jours précédents — condition de veto",
}
VETO_CONDITIONS = frozenset({"J3_NEW_SUPPLY_VETO", "J5_MVRV_HIGH_VETO"})
N_TRIALS = len(CONDITIONS) * len(HORIZONS_DAYS)


class LeakAuditFailed(RuntimeError):
    pass


@dataclass
class FlowRow(ScreenRow):
    veto_justified: bool = False


@dataclass
class FlowResult:
    run_id: str
    period_end: str
    cost_hurdle_pct: float
    n_trials: int = N_TRIALS
    program_trials: int = 0
    horizons_days: list[int] = field(default_factory=lambda: list(HORIZONS_DAYS))
    leak_audit: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    rows: list[FlowRow] = field(default_factory=list)


# --- Données journalières -------------------------------------------------------------------------------------

def daily_flow(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Part des achats au marché dans le volume en USDT de chaque journée valide (≥ 20 bougies), indexée au
    lendemain 00:00 UTC (moment où la journée est connue), comme les clôtures du panneau."""
    out = {}
    for symbol, h1 in frames.items():
        if h1.empty or "taker_buy_quote_volume" not in h1.columns:
            continue
        ordered = h1.sort_values("open_time")
        grouped = ordered.groupby(ordered["open_time"].dt.floor("D"))
        daily = grouped.agg(hours=("close", "size"), taker=("taker_buy_quote_volume", "sum"), total=("quote_volume", "sum"))
        daily = daily[(daily["hours"] >= fa.MIN_HOURS_PER_DAY) & (daily["total"] > 0)]
        out[symbol] = pd.Series((daily["taker"] / daily["total"]).to_numpy(float), index=daily.index + pd.Timedelta(days=1))
    return pd.DataFrame(out).sort_index()


def flow_flags(share: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drapeaux haut et bas : part du jour contre les centiles des 365 journées PRÉCÉDENTES (au moins 200)."""
    previous = share.shift(1)
    high = previous.rolling(FLOW_WINDOW, min_periods=FLOW_MIN_DAYS).quantile(FLOW_TOP)
    low = previous.rolling(FLOW_WINDOW, min_periods=FLOW_MIN_DAYS).quantile(FLOW_BOTTOM)
    valid = share.notna() & high.notna() & low.notna() & (high > low)      # fenêtre dégénérée (constante) : aucun drapeau
    return (share >= high) & valid, (share <= low) & valid


def new_supply_flags(close: pd.DataFrame) -> pd.DataFrame:
    """Vrai entre 30 et 180 jours après la première clôture connue de la paire."""
    first = close.notna().idxmax()
    age = pd.DataFrame({s: (close.index - first[s]).days for s in close.columns}, index=close.index)
    return (age >= NEW_SUPPLY_MIN_DAYS) & (age <= NEW_SUPPLY_MAX_DAYS) & close.notna()


def mvrv_flags(mvrv: pd.Series, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """MVRV du jour d connu à d + 2 : drapeaux bas et haut contre les centiles des 730 valeurs précédentes."""
    known = mvrv.copy()
    known.index = pd.DatetimeIndex(known.index) + pd.Timedelta(days=MVRV_LATENCY_DAYS)
    known = known[~known.index.duplicated()].sort_index().reindex(index, method="ffill")
    previous = known.shift(1)
    low = previous.rolling(MVRV_WINDOW, min_periods=MVRV_MIN_DAYS).quantile(MVRV_LOW)
    high = previous.rolling(MVRV_WINDOW, min_periods=MVRV_MIN_DAYS).quantile(MVRV_HIGH)
    valid = known.notna() & low.notna() & high.notna() & (high > low)
    return (known <= low) & valid, (known >= high) & valid


def forward_returns(panel: fa.Panel, horizon_days: int) -> pd.DataFrame:
    """Achat à l'ouverture de 01:00 du jour D (journée d connue), vente à la clôture de 23:00 de D + h − 1
    (= close.loc[D + h] dans le panneau) : rendement par journée et par paire."""
    exit_close = panel.close.shift(-horizon_days)
    return exit_close / panel.price - 1


def aged_drift(close: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """Dérive de référence du veto J3 : rendement moyen, au même horizon, de toutes les (paire, journée) où la paire a
    plus de NEW_SUPPLY_MAX_DAYS jours de cotation. La dérive propre d'une paire jeune serait faite de sa fenêtre de
    veto et tirerait mécaniquement l'excès vers zéro."""
    first = close.notna().idxmax()
    age = pd.DataFrame({s: (fwd.index - first[s]).days for s in fwd.columns}, index=fwd.index)
    aged = fwd.where(age > NEW_SUPPLY_MAX_DAYS).to_numpy(float)
    value = float(np.nanmean(aged)) if np.isfinite(aged).any() else np.nan
    return pd.Series(value, index=fwd.columns)


def events_frame(flags: pd.DataFrame, fwd: pd.DataFrame, drift: pd.Series | None = None) -> pd.DataFrame:
    """Événements (journée, paire) avec rendement et excès ; `drift` : dérive retirée par paire, par défaut la
    moyenne de `fwd` de la paire sur toute la période (convention des criblages D à I)."""
    drift = fwd.mean() if drift is None else drift
    parts = []
    for symbol in flags.columns:
        if symbol not in fwd.columns or symbol not in drift.index or not np.isfinite(drift[symbol]):
            continue
        mask = flags[symbol].to_numpy(bool) & fwd[symbol].notna().to_numpy()
        values = fwd[symbol].to_numpy(float)[mask]
        parts.append(pd.DataFrame({"time": flags.index[mask], "symbol": symbol, "ret": values, "excess": values - float(drift[symbol])}))
    frame = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) \
        else pd.DataFrame(columns=["time", "symbol", "ret", "excess"])
    if not frame.empty:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def all_flags(panel: fa.Panel, share: pd.DataFrame, mvrv: dict[str, pd.Series]) -> dict[str, pd.DataFrame]:
    high, low = flow_flags(share.reindex(panel.close.index))
    out = {"J1_FLOW_BUY_TOP": high.reindex(columns=panel.symbols).fillna(False),
           "J2_FLOW_SELL_BOTTOM": low.reindex(columns=panel.symbols).fillna(False),
           "J3_NEW_SUPPLY_VETO": new_supply_flags(panel.close)}
    low_v = pd.DataFrame(False, index=panel.close.index, columns=panel.symbols)
    high_v = low_v.copy()
    for symbol, series in mvrv.items():
        if symbol in low_v.columns and not series.empty:
            lo, hi = mvrv_flags(series, panel.close.index)
            low_v[symbol], high_v[symbol] = lo.to_numpy(bool), hi.to_numpy(bool)
    out["J4_MVRV_LOW"], out["J5_MVRV_HIGH_VETO"] = low_v, high_v
    return out


# --- MVRV (CoinMetrics, API communautaire, sans clé) ------------------------------------------------------------

def mvrv_path(settings: Settings, asset: str) -> Path:
    return settings.data_dir / "onchain" / f"{asset}_mvrv.parquet"


def fetch_mvrv(asset: str, *, start: str = "2017-01-01", end: str | None = None, client=None) -> pd.Series:
    """Série journalière CapMVRVCur (pages de 10 000 lignes) ; `client` : PublicSources (liste blanche)."""
    from ..forward.sources import PublicSources
    source = client or PublicSources()
    rows: list[tuple[str, float]] = []
    params: dict = {"assets": asset, "metrics": "CapMVRVCur", "frequency": "1d", "start_time": start, "page_size": 10000}
    if end:
        params["end_time"] = end
    try:
        reply = source.get_json(MVRV_URL, params)
        for _ in range(20):
            for item in reply.get("data", []):
                value = item.get("CapMVRVCur")
                if value is not None:
                    rows.append((item["time"], float(value)))
            token = reply.get("next_page_token")
            if not token:
                break
            reply = source.get_json(MVRV_URL, params | {"next_page_token": token})
    finally:
        if client is None:
            source.close()
    series = pd.Series({pd.Timestamp(t).tz_convert("UTC").floor("D"): v for t, v in rows}, dtype=float).sort_index()
    return series[~series.index.duplicated()]


def load_mvrv(settings: Settings, asset: str, *, end: pd.Timestamp, fetcher: Callable[..., pd.Series] | None = None) -> pd.Series:
    """Série en cache (data/onchain), téléchargée une fois si absente ; coupée à `end`."""
    path = mvrv_path(settings, asset)
    if path.exists():
        frame = pd.read_parquet(path)
        series = pd.Series(frame["value"].to_numpy(float), index=pd.DatetimeIndex(frame["time"]).tz_convert("UTC"))
    else:
        series = (fetcher or fetch_mvrv)(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"time": series.index, "value": series.to_numpy(float)}).to_parquet(path, index=False)
    return series[series.index <= end]


# --- Audit des fuites et exécution -------------------------------------------------------------------------------

def leak_audit(frames: dict[str, pd.DataFrame], panel: fa.Panel, mvrv: dict[str, pd.Series], *, seed: int, picks: int = 4) -> dict:
    """Drapeaux recalculés avec les seules bougies antérieures à un jour tiré : identiques ; mutation : une part
    d'achats lue sur le lendemain doit changer des drapeaux."""
    rng = np.random.default_rng(seed)
    share = daily_flow(frames)
    full = all_flags(panel, share, mvrv)
    mutated_full = all_flags(panel, share.shift(-1), mvrv)
    days = panel.close.index[panel.close.index >= FIRST_DAY]
    chosen = sorted(rng.choice(np.arange(len(days) // 3, len(days)), size=picks, replace=False))
    violations: list[dict] = []
    detected = False
    for pick in chosen:
        moment = days[pick]
        cut = {s: f[f["open_time"] < moment] for s, f in frames.items()}
        kept = {s: f for s, f in cut.items() if not f.empty}
        cut_panel, cut_share = fa.build_panel(kept), daily_flow(kept)
        cut_mvrv = {s: v[v.index + pd.Timedelta(days=MVRV_LATENCY_DAYS) <= moment] for s, v in mvrv.items()}
        redo = all_flags(cut_panel, cut_share, cut_mvrv)
        for name, table in full.items():
            again = redo[name].reindex(columns=table.columns).fillna(False)
            if moment not in again.index or not (again.loc[moment].to_numpy() == table.loc[moment].to_numpy()).all():
                violations.append({"day": str(moment), "condition": name})
        # Mutation passée par la MÊME coupe : les drapeaux d'une part lue sur le lendemain doivent différer entre le
        # calcul complet et le calcul tronqué (le lendemain n'y existe pas). Sinon l'audit ne verrait pas une fuite.
        again_mutated = all_flags(cut_panel, cut_share.shift(-1), cut_mvrv)
        for name in ("J1_FLOW_BUY_TOP", "J2_FLOW_SELL_BOTTOM"):
            table = mutated_full[name]
            redo_mutated = again_mutated[name].reindex(columns=table.columns).fillna(False)
            detected |= moment not in redo_mutated.index or not (redo_mutated.loc[moment].to_numpy() == table.loc[moment].to_numpy()).all()
    return {"violations": violations, "mutation_detected": bool(detected), "days": [str(days[p]) for p in chosen],
            "passed": not violations and bool(detected)}


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, mvrv_fetcher: Callable[..., pd.Series] | None = None) -> FlowResult:
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    result = FlowResult(new_run_id("SCREEN"), end.isoformat(), round(hurdle_pct, 4))
    say("données")
    frames = fa.load_frames(settings, symbols, end)
    result.data_hashes = {s: fingerprint(f) for s, f in frames.items()}
    panel = fa.build_panel(frames)
    say("MVRV")
    mvrv = {s: load_mvrv(settings, a, end=end, fetcher=mvrv_fetcher) for s, a in MVRV_ASSETS.items() if s in frames}
    for symbol, series in mvrv.items():
        result.data_hashes[f"mvrv_{MVRV_ASSETS[symbol]}"] = fingerprint(pd.DataFrame({"time": series.index, "value": series.to_numpy(float)}))
    say("audit des fuites")
    result.leak_audit = leak_audit(frames, panel, mvrv, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    share = daily_flow(frames)
    flags = all_flags(panel, share, mvrv)
    result.coverage = {"days": int((panel.close.index >= FIRST_DAY).sum()), "first": str(FIRST_DAY)[:10], "last": str(panel.close.index.max())[:10],
                       "mvrv_days": {s: int(len(v)) for s, v in mvrv.items()}, "flow_pairs": int(share.shape[1]),
                       "flag_days": {name: int(table.loc[table.index >= FIRST_DAY].to_numpy().sum()) for name, table in flags.items()}}
    for horizon in HORIZONS_DAYS:
        say(f"horizon {horizon} j")
        fwd = forward_returns(panel, horizon)
        fwd = fwd[fwd.index >= FIRST_DAY]
        for name, table in flags.items():
            drift = aged_drift(panel.close, fwd) if name == "J3_NEW_SUPPLY_VETO" else None
            frame = events_frame(table[table.index >= FIRST_DAY], fwd, drift)
            row = _row(name, horizon, frame, hurdle_pct, settings)
            result.rows.append(row)
    result.program_trials = ExperimentRegistry(settings.experiments_db).program_trials() + N_TRIALS
    _record(settings, result, now=now, symbols=symbols, code=state)
    return result


def _row(name: str, horizon_days: int, frame: pd.DataFrame, hurdle_pct: float, settings: Settings) -> FlowRow:
    if frame.empty:
        return FlowRow(name, horizon_days * 24, 0, 0, None, None, None, None, None, False, False)
    by_pair = frame.groupby("symbol")["excess"].mean()
    by_year = frame.groupby(frame["time"].dt.year)["excess"].mean()
    mean_ret = float(frame["ret"].mean()) * 100
    block_days = max(10, 2 * horizon_days)
    ci = _day_block_ci(frame, block_days, settings.protocol.bootstrap_samples, settings.protocol.seed)
    beats = bool(mean_ret > hurdle_pct and ci is not None and ci[0] > 0) if name not in VETO_CONDITIONS else False
    veto = bool(ci is not None and ci[1] < 0) if name in VETO_CONDITIONS else False
    return FlowRow(name, horizon_days * 24, len(frame), int(by_pair.size), round(mean_ret, 4),
                   round(float(frame["excess"].mean()) * 100, 4), ci, round(float((by_pair > 0).mean()), 3),
                   round(float((by_year > 0).mean()), 3), beats, veto)


def _record(settings: Settings, result: FlowResult, *, now: datetime, symbols: list[str], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"conditions": CONDITIONS, "protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="criblage J : flux d'ordres (part des achats au marché), offre nouvelle (paire récente) et valeur on-chain "
                   "(MVRV) donnent-ils un avantage, ou justifient-ils un veto, à 1, 7 et 30 jours, après dérive et coûts ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="conditions figées (docs/SCREENING.md, criblage J)",
        params={"horizons_days": result.horizons_days, "conditions": CONDITIONS, "flow": [FLOW_WINDOW, FLOW_MIN_DAYS, FLOW_TOP, FLOW_BOTTOM],
                "new_supply_days": [NEW_SUPPLY_MIN_DAYS, NEW_SUPPLY_MAX_DAYS], "mvrv": [MVRV_WINDOW, MVRV_MIN_DAYS, MVRV_LOW, MVRV_HIGH, MVRV_LATENCY_DAYS]},
        period_label="DEVELOPMENT", period_start=str(FIRST_DAY)[:10], period_end=result.period_end, universe=symbols,
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="central (seuil aller-retour)", simulation_rules={"entry": "ouverture 01:00 du lendemain", "exit": "clôture 23:00 à h jours", "stops": "aucun"},
        metrics={"n_trials": N_TRIALS, "program_trials": result.program_trials, "cost_hurdle_pct": result.cost_hurdle_pct,
                 "coverage": result.coverage, "rows": [asdict(r) for r in result.rows]}, status="COMPLETED", report_dir=str(report_dir))
