"""Variables causales du swing (bougies 1 h, une décision toutes les 4 h) et cibles nettes de coûts.

Toute variable d'une ligne de décision est connue à la clôture de sa bougie 1 h : fenêtres glissantes
passées, contexte 4 h et 1 jour reconstruit à partir de bougies 1 h COMPLÈTES et joint vers le passé sur
`available_at` (contexte trop ancien rendu inconnu), BTC joint de même, coupe transversale calculée
entre paires à la MÊME heure de décision. Cibles (docs/ML_SWING.md §2) : achat à l'ouverture de t+1+d,
vente à la clôture de t+H+d (horizon fixe) ou à la première barrière à ±K·σ_H (triple barrière : stop au
contact, objectif seulement s'il est dépassé, stop et objectif dans la même bougie → stop) ; coûts des
deux côtés ; une cible dont la fenêtre n'est pas contiguë est invalide.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ...config import CostScenario
from ...data.quality import with_gap_column
from ...domain.market import MAX_CONTEXT_AGE
from ...features import indicators as ind
from ...features.higher_tf import resample_complete
from ..intraday.dataset import _asof

STEP = pd.Timedelta(hours=1)
DECISION_EVERY = 4                       # décisions aux clôtures 04:00, 08:00… UTC (fin des blocs de 4 h)
HORIZONS = (24, 72, 168)                 # 1, 3 et 7 jours
TARGETS = ("fh", "tb")
BARRIER_K = 1.5
VOL_WINDOW = 168                         # σ : volatilité réalisée 1 h sur 7 jours
GAP_BLOCK_BARS = 168
MAX_4H_AGE = pd.Timedelta(hours=8)
MAX_1D_AGE = pd.Timedelta(hours=26)      # la bougie du jour précédent : au plus 24 h + marge

FAMILIES: dict[str, tuple[str, ...]] = {
    "prix": ("r_1", "r_4", "r_24", "r_72", "r_168", "rv_24", "rv_168", "atr_pct", "rsi", "dist_ema20", "dist_ema50",
             "dist_ema200", "slope_ema50", "bb_z", "range_pos_168", "dd_720"),
    "volume": ("vol_z_168", "vol_ratio_24"),
    "transactions": ("taker_24", "trades_ratio_24"),
    "contexte": ("h4_ret_6", "h4_ret_42", "h4_ema50_slope", "h4_dist_ema50", "d1_ret_7", "d1_ret_30", "d1_ret_90",
                 "d1_dist_ema50", "d1_atr_pct"),
    "marche": ("btc_ret_24", "btc_ret_168", "btc_ret_720", "btc_rv_168"),
    "coupe": ("xs_rank_168", "xs_rank_720", "xs_excess_168"),
    "calendrier": ("dow_sin", "dow_cos", "slot_sin", "slot_cos"),
}
FEATURES: tuple[str, ...] = tuple(name for family in FAMILIES.values() for name in family)
PAIR_FEATURES: tuple[str, ...] = tuple(f for f in FEATURES if f not in FAMILIES["coupe"])


def realized_vol(close: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    return np.log(close).diff().rolling(window, min_periods=window).std()


def decision_mask(h1: pd.DataFrame) -> np.ndarray:
    """Lignes de décision d'un tableau 1 h TRIÉ : bougies dont la clôture tombe sur une fin de bloc de 4 h."""
    return ((h1["open_time"] + STEP).dt.hour % DECISION_EVERY == 0).to_numpy()


def targets(h1: pd.DataFrame, costs: CostScenario, horizon: int, kind: str, *, delay: int = 0, k: float = BARRIER_K,
            vol_window: int = VOL_WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """(rendement net, bougies entre la décision et la sortie) pour chaque ligne du tableau 1 h trié."""
    if kind not in TARGETS:
        raise ValueError(f"cible inconnue : {kind}")
    df = h1.sort_values("open_time").reset_index(drop=True)
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
        sigma = realized_vol(df["close"], vol_window).to_numpy() * np.sqrt(horizon)
        up, down = entry * (1 + k * sigma), entry * (1 - k * sigma)
        active = np.isfinite(sigma) & np.isfinite(entry)
        for j in range(1, horizon + 1):
            bar = delay + j
            oj, hj, lj, cj = ahead(o, bar), ahead(h, bar), ahead(low, bar), ahead(c, bar)
            rules = [] if j == 1 else [(oj <= down, oj), (oj >= up, up)]
            rules += [(lj <= down, down), (hj > up, up)]          # objectif : seulement s'il est dépassé
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


def _context_4h(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    ema50 = ind.ema(close, 50)
    return pd.DataFrame({"available_at": bars["available_at"], "ret_6": close / close.shift(6) - 1,
                         "ret_42": close / close.shift(42) - 1, "ema50_slope": ema50 / ema50.shift(3) - 1,
                         "dist_ema50": close / ema50 - 1})


def _context_1d(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    ema50 = ind.ema(close, 50)
    return pd.DataFrame({"available_at": bars["available_at"], "ret_7": close / close.shift(7) - 1,
                         "ret_30": close / close.shift(30) - 1, "ret_90": close / close.shift(90) - 1,
                         "dist_ema50": close / ema50 - 1,
                         "atr_pct": ind.atr(bars["high"], bars["low"], close, 14) / close})


def _btc(btc: pd.DataFrame) -> pd.DataFrame:
    df = btc.sort_values("open_time").reset_index(drop=True)
    close = df["close"]
    return pd.DataFrame({"available_at": df["available_at"], "ret_24": close / close.shift(24) - 1,
                         "ret_168": close / close.shift(168) - 1, "ret_720": close / close.shift(720) - 1,
                         "rv_168": realized_vol(close, 168)})


def pair_frame(h1: pd.DataFrame, btc_h1: pd.DataFrame, *,
               resampler: Callable[[pd.DataFrame, int], pd.DataFrame] = resample_complete) -> pd.DataFrame:
    """Une ligne par bougie 1 h clôturée : variables de la paire (hors coupe transversale) à sa clôture.

    `resampler` n'existe que pour le test de mutation de l'audit des fuites."""
    df = with_gap_column(h1.sort_values("open_time").reset_index(drop=True), "1h")
    h, low, c = df["high"], df["low"], df["close"]
    volume, trades = df["base_volume"], df["number_of_trades"]
    log_close = np.log(c)
    r1 = log_close.diff()
    ema20, ema50, ema200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    mid, sigma = ind.bollinger(c, 20)
    low_168, high_168 = low.rolling(168, min_periods=168).min(), h.rolling(168, min_periods=168).max()
    high_720 = h.rolling(720, min_periods=720).max()
    log_volume = np.log1p(volume)
    prior = log_volume.shift(1).rolling(168, min_periods=168)
    prior_mean, prior_std = prior.mean(), prior.std()
    volume_24 = volume.rolling(24, min_periods=24).sum()
    volume_daily = volume.shift(24).rolling(720, min_periods=720).mean() * 24
    trades_24 = trades.rolling(24, min_periods=24).sum()
    trades_daily = trades.shift(24).rolling(720, min_periods=720).mean() * 24
    decision = df["open_time"] + STEP
    hours = decision.dt.hour + decision.dt.minute / 60
    dow = decision.dt.dayofweek
    out = pd.DataFrame({
        "open_time": df["open_time"], "decision_time": decision, "available_at": df["available_at"],
        "r_1": r1, **{f"r_{k}": log_close - log_close.shift(k) for k in (4, 24, 72, 168, 720)},
        "rv_24": r1.rolling(24, min_periods=24).std(), "rv_168": r1.rolling(168, min_periods=168).std(),
        "atr_pct": ind.atr(h, low, c, 14) / c, "rsi": ind.rsi(c, 14) / 100,
        "dist_ema20": c / ema20 - 1, "dist_ema50": c / ema50 - 1, "dist_ema200": c / ema200 - 1,
        "slope_ema50": ema50 / ema50.shift(24) - 1, "bb_z": (c - mid) / sigma.where(sigma > 1e-12),
        "range_pos_168": (c - low_168) / (high_168 - low_168).where(high_168 > low_168),
        "dd_720": c / high_720 - 1,
        "vol_z_168": (log_volume - prior_mean) / prior_std.where(prior_std > 0),
        "vol_ratio_24": volume_24 / volume_daily.where(volume_daily > 0),
        "taker_24": df["taker_buy_base_volume"].rolling(24, min_periods=24).sum() / volume_24.where(volume_24 > 0),
        "trades_ratio_24": trades_24 / trades_daily.where(trades_daily > 0),
        "dow_sin": np.sin(2 * np.pi * dow / 7), "dow_cos": np.cos(2 * np.pi * dow / 7),
        "slot_sin": np.sin(2 * np.pi * hours / 24), "slot_cos": np.cos(2 * np.pi * hours / 24),
        "gap_recent": df["gap_before"].rolling(GAP_BLOCK_BARS, min_periods=1).max().fillna(0) > 0,
    })
    out = _asof(out, _context_4h(resampler(h1, 4)), "h4_", MAX_4H_AGE)
    out = _asof(out, _context_1d(resampler(h1, 24)), "d1_", MAX_1D_AGE)
    return _asof(out, _btc(btc_h1), "btc_", MAX_CONTEXT_AGE)


def add_cross_section(frame: pd.DataFrame) -> pd.DataFrame:
    """Coupe transversale à chaque heure de décision, parmi les paires présentes à cette heure :
    rangs (percentiles) des rendements 7 j et 30 j, écart du rendement 7 j à la médiane."""
    grouped = frame.groupby("decision_time")
    return frame.assign(xs_rank_168=grouped["r_168"].rank(pct=True), xs_rank_720=grouped["r_720"].rank(pct=True),
                        xs_excess_168=frame["r_168"] - grouped["r_168"].transform("median"))


def pair_decisions(h1: pd.DataFrame, btc_h1: pd.DataFrame, *, symbol: str, costs: CostScenario,
                   horizons: tuple[int, ...] = HORIZONS, kinds: tuple[str, ...] = TARGETS,
                   resampler: Callable[[pd.DataFrame, int], pd.DataFrame] = resample_complete) -> pd.DataFrame:
    """Lignes de DÉCISION d'une paire (une toutes les 4 h) : variables de la paire et cibles par horizon."""
    sorted_h1 = h1.sort_values("open_time").reset_index(drop=True)
    frame = pair_frame(sorted_h1, btc_h1, resampler=resampler)
    for kind in kinds:
        for horizon in horizons:
            net, offset = targets(sorted_h1, costs, horizon, kind)
            frame[f"net_{kind}_{horizon}"] = net.astype("float32")
            frame[f"bars_{kind}_{horizon}"] = offset
    frame.insert(0, "symbol", symbol)
    return frame[decision_mask(sorted_h1)].reset_index(drop=True)


def causality_violations(h1: pd.DataFrame, btc_h1: pd.DataFrame, *, decisions: list[pd.Timestamp], seed: int = 0,
                         resampler: Callable[[pd.DataFrame, int], pd.DataFrame] = resample_complete) -> list[dict]:
    """Audit des fuites : pour chaque décision (heure d'ouverture de sa bougie 1 h), les variables de la paire
    recalculées avec seulement les données disponibles à la décision, puis avec un futur falsifié, doivent
    être identiques au calcul complet. Renvoie les écarts (liste vide : causal)."""
    columns = [*PAIR_FEATURES, "r_720"]

    def build(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        return pair_frame(a, b, resampler=resampler).set_index("open_time")[columns]

    full = build(h1, btc_h1)
    rng = np.random.default_rng(seed)
    problems: list[dict] = []
    for moment in decisions:
        row = h1.loc[h1["open_time"] == moment]
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
        for label, frames in (("tronqué", (cut(h1), cut(btc_h1))), ("futur falsifié", (falsify(h1), falsify(btc_h1)))):
            got = build(*frames).loc[moment]
            differs = ~np.isclose(got.to_numpy(float), expected.to_numpy(float), rtol=1e-5, atol=1e-7, equal_nan=True)
            if differs.any():
                problems.append({"open_time": str(moment), "check": label,
                                 "features": [f for f, d in zip(columns, differs, strict=True) if d]})
    return problems
