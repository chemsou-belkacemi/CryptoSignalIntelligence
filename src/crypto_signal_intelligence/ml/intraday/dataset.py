"""Variables causales (15 min + contexte 1 h, 4 h et BTC) et cibles nettes de coûts, par paire.

Toute variable d'une ligne est connue à la clôture de sa bougie 15 min (instant de décision) :
fenêtres glissantes passées, jointures vers le passé sur `available_at`, contexte trop ancien
rendu inconnu. Les cibles (rendements futurs) ne servent qu'à l'entraînement et à l'évaluation ;
elles reproduisent EXACTEMENT la règle de sortie de la stratégie qui les apprend (§2 du protocole) :
- horizon fixe (fh) : achat à l'ouverture de t+1+d, vente à la clôture de t+H+d ;
- triple barrière (tb) : même entrée, objectif et stop à ±K·σ_H de l'entrée (σ_H = volatilité
  réalisée 96 bougies × √H, connue à la décision), sinon clôture de t+H+d ; stop et objectif dans
  la même bougie → stop ; ouverture au-delà du stop → sortie à l'ouverture ; au-delà de l'objectif
  → sortie à l'objectif.
d = bougies de retard à l'entrée (scénarios défavorables). Coûts de marché (glissement + demi-spread)
et frais des deux côtés. Une cible dont la fenêtre t..t+H+d n'est pas contiguë est invalide (NaN).
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ...config import CostScenario, RegimeSection
from ...data.quality import with_gap_column
from ...domain.market import MAX_CONTEXT_AGE
from ...features import indicators as ind
from ...features.builder import context_features
from ...features.higher_tf import resample_complete

HORIZONS = (2, 4, 8, 16)          # bougies 15 min : 30 min, 1 h, 2 h, 4 h
TARGETS = ("fh", "tb")            # horizon fixe, triple barrière
BARRIER_K = 1.5
STEP = pd.Timedelta(minutes=15)
MAX_4H_AGE = pd.Timedelta(hours=8)  # une bougie 4 h a au plus 4 h de retard normal, plus une de marge
GAP_BLOCK_BARS = 96                  # un trou récent rend les fenêtres d'indicateurs douteuses

FAMILIES: dict[str, tuple[str, ...]] = {
    "prix": ("r_1", "r_2", "r_4", "r_8", "r_16", "r_32", "r_96", "rv_16", "rv_96", "atr_pct", "rsi",
             "dist_ema20", "dist_ema50", "slope_ema20", "bb_z", "range_pos_32"),
    "volume": ("vol_z_96", "vol_ratio_20"),
    "transactions": ("taker_ratio", "taker_ratio_4", "trades_ratio_96"),
    "contexte": ("h1_ret_24h", "h1_ema50_slope", "h1_atr_pct", "h1_dist_ema50", "h1_trend_bull", "h1_trend_bear",
                 "h1_vol_high", "h1_vol_low", "h4_ret_6", "h4_ret_42", "h4_ema50_slope", "h4_atr_pct",
                 "h4_dist_ema50"),
    "marche": ("btc_ret_24h", "btc_atr_pct", "btc_ema50_slope"),
    "calendrier": ("hour_sin", "hour_cos", "dow_sin", "dow_cos"),
}
FEATURES: tuple[str, ...] = tuple(name for family in FAMILIES.values() for name in family)


def feature_set(families: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for family in families for name in FAMILIES[family])


def _asof(left: pd.DataFrame, right: pd.DataFrame, prefix: str, max_age: pd.Timedelta) -> pd.DataFrame:
    """Jointure vers le passé sur `available_at` ; contexte plus ancien que `max_age` → valeurs absentes."""
    right = right.rename(columns={c: f"{prefix}{c}" for c in right.columns if c != "available_at"})
    seen = f"{prefix}seen_at"
    right = right.assign(**{seen: right["available_at"]}).sort_values("available_at")
    joined = pd.merge_asof(left.sort_values("available_at"), right, on="available_at", direction="backward",
                           allow_exact_matches=True)
    stale = joined[seen].isna() | (joined["available_at"] - joined[seen] > max_age)
    value_columns = [c for c in right.columns if c not in ("available_at", seen)]
    joined.loc[stale.to_numpy(), value_columns] = np.nan
    return joined.drop(columns=[seen]).sort_values("open_time").reset_index(drop=True)


def _higher_tf_features(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    ema50 = ind.ema(close, 50)
    return pd.DataFrame({
        "available_at": bars["available_at"],
        "ret_6": close / close.shift(6) - 1, "ret_42": close / close.shift(42) - 1,
        "ema50_slope": ema50 / ema50.shift(3) - 1,
        "atr_pct": ind.atr(bars["high"], bars["low"], close, 14) / close,
        "dist_ema50": close / ema50 - 1,
    })


def realized_vol(close: pd.Series, window: int = 96) -> pd.Series:
    return np.log(close).diff().rolling(window, min_periods=window).std()


def targets(setup: pd.DataFrame, costs: CostScenario, horizon: int, kind: str, *, delay: int = 0,
            k: float = BARRIER_K) -> tuple[np.ndarray, np.ndarray]:
    """(rendement net, nombre de bougies entre la décision et la sortie) par ligne de `setup` trié.

    La sortie a lieu à la CLÔTURE de la bougie t+offset : heure de sortie = décision + offset × 15 min.
    """
    if kind not in TARGETS:
        raise ValueError(f"cible inconnue : {kind}")
    df = setup.sort_values("open_time").reset_index(drop=True)
    o, h, low, c = (df[col].to_numpy(float) for col in ("open", "high", "low", "close"))
    n, last = len(df), delay + horizon
    times = df["open_time"]
    contiguous = ((times.shift(-last) - times) == last * STEP).to_numpy()

    def ahead(values: np.ndarray, offset: int) -> np.ndarray:
        out = np.full(n, np.nan)
        if offset < n:
            out[:n - offset] = values[offset:]
        return out

    entry = ahead(o, 1 + delay)
    exit_price = np.full(n, np.nan)
    offset = np.full(n, -1, dtype=np.int16)
    if kind == "fh":
        exit_price, offset[:] = ahead(c, last), last
    else:
        sigma = realized_vol(df["close"]).to_numpy() * np.sqrt(horizon)
        up, down = entry * (1 + k * sigma), entry * (1 - k * sigma)
        active = np.isfinite(sigma) & np.isfinite(entry)
        for j in range(1, horizon + 1):
            bar = delay + j
            oj, hj, lj, cj = ahead(o, bar), ahead(h, bar), ahead(low, bar), ahead(c, bar)
            rules = [] if j == 1 else [(oj <= down, oj), (oj >= up, up)]   # ouverture au-delà d'une barrière
            rules += [(lj <= down, down), (hj >= up, up)]                     # stop d'abord (défavorable)
            if j == horizon:
                rules.append((np.ones(n, dtype=bool), cj))
            for hit, price in rules:
                now = active & hit
                exit_price[now], offset[now] = price[now], bar
                active &= ~now
    market = (costs.slippage_bps + costs.half_spread_bps) / 1e4
    fee = costs.fee_bps / 1e4
    net = exit_price * (1 - market) * (1 - fee) / (entry * (1 + market) * (1 + fee)) - 1
    valid = contiguous & np.isfinite(net)
    return np.where(valid, net, np.nan), np.where(valid, offset, -1).astype(np.int16)


def pair_dataset(setup: pd.DataFrame, context_1h: pd.DataFrame, btc_1h: pd.DataFrame, *, symbol: str,
                 regimes: RegimeSection, costs: CostScenario, horizons: tuple[int, ...] = HORIZONS,
                 kinds: tuple[str, ...] = TARGETS,
                 resampler: Callable[[pd.DataFrame, int], pd.DataFrame] = resample_complete) -> pd.DataFrame:
    """Une ligne par bougie 15 min clôturée : variables à l'instant de décision et cibles par horizon.

    `resampler` n'existe que pour le test de mutation de l'audit des fuites.
    """
    df = with_gap_column(setup.sort_values("open_time").reset_index(drop=True), "15m")
    h, low, c = df["high"], df["low"], df["close"]
    volume = df["base_volume"]
    log_close = np.log(c)
    r1 = log_close.diff()
    ema20, ema50 = ind.ema(c, 20), ind.ema(c, 50)
    mid, sigma = ind.bollinger(c, 20)
    low_32, high_32 = low.rolling(32, min_periods=32).min(), h.rolling(32, min_periods=32).max()
    log_volume = np.log1p(volume)
    taker = df["taker_buy_base_volume"] / volume.where(volume > 0)
    prior_volume, prior_trades = ind.prior_mean(volume, 20), ind.prior_mean(df["number_of_trades"], 96)
    decision = df["open_time"] + STEP
    hours = decision.dt.hour + decision.dt.minute / 60
    dow = decision.dt.dayofweek
    out = pd.DataFrame({
        "open_time": df["open_time"], "decision_time": decision, "available_at": df["available_at"],
        **{f"r_{k}": log_close - log_close.shift(k) for k in (1, 2, 4, 8, 16, 32, 96)},
        "rv_16": r1.rolling(16, min_periods=16).std(), "rv_96": r1.rolling(96, min_periods=96).std(),
        "atr_pct": ind.atr(h, low, c, 14) / c, "rsi": ind.rsi(c, 14) / 100,
        "dist_ema20": c / ema20 - 1, "dist_ema50": c / ema50 - 1, "slope_ema20": ema20 / ema20.shift(4) - 1,
        "bb_z": (c - mid) / sigma.where(sigma > 1e-12),
        "range_pos_32": (c - low_32) / (high_32 - low_32).where(high_32 > low_32),
        "vol_z_96": (log_volume - log_volume.shift(1).rolling(96, min_periods=96).mean())
                    / log_volume.shift(1).rolling(96, min_periods=96).std(),
        "vol_ratio_20": volume / prior_volume.where(prior_volume > 0),
        "taker_ratio": taker, "taker_ratio_4": taker.rolling(4, min_periods=4).mean(),
        "trades_ratio_96": df["number_of_trades"] / prior_trades.where(prior_trades > 0),
        "hour_sin": np.sin(2 * np.pi * hours / 24), "hour_cos": np.cos(2 * np.pi * hours / 24),
        "dow_sin": np.sin(2 * np.pi * dow / 7), "dow_cos": np.cos(2 * np.pi * dow / 7),
        "gap_recent": df["gap_before"].rolling(GAP_BLOCK_BARS, min_periods=1).max().fillna(0) > 0,
    })

    ctx = context_features(context_1h, regimes)
    h1 = pd.DataFrame({"available_at": ctx["available_at"], "ret_24h": ctx["ret_24h"],
                       "ema50_slope": ctx["ema50_slope"], "atr_pct": ctx["atr_pct"],
                       "dist_ema50": ctx["close"] / ctx["ema50"] - 1,
                       "trend_bull": (ctx["trend"] == "BULL").astype(float),
                       "trend_bear": (ctx["trend"] == "BEAR").astype(float),
                       "vol_high": (ctx["volatility"] == "HIGH").astype(float),
                       "vol_low": (ctx["volatility"] == "LOW").astype(float)})
    out = _asof(out, h1, "h1_", MAX_CONTEXT_AGE)
    out = _asof(out, _higher_tf_features(resampler(context_1h, 4)), "h4_", MAX_4H_AGE)
    btc = context_features(btc_1h, regimes)
    out = _asof(out, pd.DataFrame({"available_at": btc["available_at"], "ret_24h": btc["ret_24h"],
                                   "atr_pct": btc["atr_pct"], "ema50_slope": btc["ema50_slope"]}),
                "btc_", MAX_CONTEXT_AGE)

    result = out[["open_time", "decision_time", "gap_recent"]].copy()
    result.insert(0, "symbol", symbol)
    result[list(FEATURES)] = out[list(FEATURES)].astype("float32")
    for kind in kinds:
        for horizon in horizons:
            net, offset = targets(df, costs, horizon, kind)
            result[f"net_{kind}_{horizon}"] = net.astype("float32")
            result[f"bars_{kind}_{horizon}"] = offset
    return result


def training_rows(data: pd.DataFrame, kind: str, horizon: int) -> pd.Series:
    """Masque des lignes d'entraînement pour (cible, H) : cible valide, pas de trou récent, une ligne
    toutes les H bougies par paire (cibles disjointes dans une même paire)."""
    position = (data["open_time"] - pd.Timestamp("2000-01-01", tz="UTC")) // STEP
    return data[f"net_{kind}_{horizon}"].notna() & ~data["gap_recent"] & ((position % horizon) == 0)


def causality_violations(setup: pd.DataFrame, context_1h: pd.DataFrame, btc_1h: pd.DataFrame, *, symbol: str,
                         regimes: RegimeSection, costs: CostScenario, decisions: list[pd.Timestamp], seed: int = 0,
                         resampler: Callable[[pd.DataFrame, int], pd.DataFrame] = resample_complete) -> list[dict]:
    """Audit des fuites : pour chaque instant (heure d'ouverture d'une bougie 15 min), les variables
    recalculées (1) avec seulement les données disponibles à l'instant de décision et (2) avec un futur
    falsifié doivent être identiques au calcul complet. Renvoie les écarts (liste vide : causal)."""
    def build(s: pd.DataFrame, c1: pd.DataFrame, b1: pd.DataFrame) -> pd.DataFrame:
        frame = pair_dataset(s, c1, b1, symbol=symbol, regimes=regimes, costs=costs, horizons=(2,), kinds=("fh",),
                             resampler=resampler)
        return frame.set_index("open_time")[list(FEATURES)]

    full = build(setup, context_1h, btc_1h)
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for moment in decisions:
        row = setup.loc[setup["open_time"] == moment]
        if row.empty:
            continue
        known_at = row["available_at"].iloc[0]

        def cut(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            return frame[frame["available_at"] <= at]

        def falsify(frame: pd.DataFrame, at=known_at) -> pd.DataFrame:
            frame = frame.copy()
            future = frame["available_at"] > at
            factor = rng.uniform(0.5, 1.5, int(future.sum()))
            for column in ("open", "high", "low", "close"):
                frame.loc[future, column] = frame.loc[future, column] * factor
            for column in ("base_volume", "taker_buy_base_volume", "number_of_trades"):
                if column in frame:
                    frame[column] = frame[column].astype(float)
                    frame.loc[future, column] = frame.loc[future, column] * rng.uniform(0.1, 3, int(future.sum()))
            return frame

        expected = full.loc[moment]
        for label, frames in (("tronqué", (cut(setup), cut(context_1h), cut(btc_1h))),
                              ("futur falsifié", (falsify(setup), falsify(context_1h), falsify(btc_1h)))):
            got = build(*frames).loc[moment]
            differs = ~(np.isclose(got.to_numpy(float), expected.to_numpy(float), rtol=1e-5, atol=1e-7,
                                   equal_nan=True))
            if differs.any():
                problems.append({"symbol": symbol, "open_time": str(moment), "check": label,
                                 "features": [f for f, d in zip(FEATURES, differs, strict=True) if d]})
    return problems
