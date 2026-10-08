"""Contrôles synthétiques de la météo du marché (docs/METEO_MARCHE.md, § 7.1 et § 7.2). Aucune donnée réelle.

Chaque simulation fabrique un marché de 20 paires (rendement simple de la paire = β × celui de BTC + bruit propre, β dans
[0,5 ; 1,5] ; prix martingales P_t = P_{t−1} · (1 + σ_h · ε_t), correction du 2026-10-08), en bougies 1 h,
avec 2 ans de rodage non mesurés puis la période de la question (principale : 281 semaines ; variante : 381 semaines),
un Fear & Greed et un financement synthétiques (formules du § 7.2). Le feu, `S2N` et les décisions sont calculés par le
même code que sur le réel (`research/meteo.py`, `research/meteo_study.py`), avec les approximations déclarées :
- `VOL_HAUTE` et `σ̂` : HAR journalier simple (veille, 5 jours, 22 jours, sans profil), réajusté chaque bloc de
  13 semaines sur le passé (moindres carrés, facteur de Duan) ; base et valeur de la même instance ;
- `PERTES_RECENTES` : achats de la transaction de référence sur les bougies 1 h (stop d'abord), 20 par jour, R brut ;
- `LARGEUR` et panier : les 20 paires sont toutes membres du top 40 ;
- hauts et bas d'une heure : maximum et minimum d'un pont brownien de même variance (loi exacte en gaussien).
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import meteo as mt
from . import meteo_study as ms
from . import volatility as v1

N_PAIRS = 20
SIMS = 500
BURN_DAYS = 728                                   # 104 semaines (8 blocs de 13 semaines)
TAIL_DAYS = 4                                     # sorties du dernier jour mesuré et horizon de 60 h
BETA = (0.5, 1.5)
HAR = (0.35, 0.35, 0.25)
ETA_SD = 0.3
VOL_BTC, VOL_OWN = 0.03, 0.04
DRIFT = 0.003                                     # ± 0,3 % par jour (N3, N3b)
REGIME_MEAN_DAYS = 60
LEVERAGE = 0.5                                    # N3c
MONDAY = 0.2                                      # N4 : lundi + 0,2 σ
FG = {"center": 50.0, "scale": 40.0, "phi": 0.8, "sd": 8.0}
FUND = {"base": 0.0001, "slope": 0.0004, "sd": 0.0001}
CASES = ("N1", "N2", "N2t", "N3", "N3b", "N3c", "N4")
JUDGED = ("N1", "N2", "N2t", "N3", "N3c", "N4")
CRITERIA = {"z_mean": 0.15, "false_positive": 0.07, "ks_p": 0.01, "coverage_side": 0.075,
            "power": 0.50, "false_equivalence": 0.07}
LATENCY = mt.LATENCY
CALIBRATION_DAYS = 200_000
MIN_SIMPLE = -0.99                                # plancher d'un rendement horaire simple (jamais atteint en pratique)


@dataclass(frozen=True)
class Spec:
    case: str
    question: str                                 # "principale" ou "variante"
    sim: int

    @property
    def period(self) -> ms.Period:
        return ms.MAIN if self.question == "principale" else ms.VARIANT

    @property
    def seed(self) -> list[int]:
        return [mt.SEED, CASES.index(self.case), 0 if self.question == "principale" else 1, self.sim]


# --- Volatilité à mémoire longue -------------------------------------------------------------------------------------

def har_path(days: int, series: int, rng: np.random.Generator, *, z_daily: np.ndarray | None = None,
             leverage: float = 0.0) -> np.ndarray:
    """x_d = 0,35 · x_{d−1} + 0,35 · moyenne(5 j) + 0,25 · moyenne(22 j) + N(0 ; 0,3²) [+ levier · max(0, −z_{d−1})],
    centré (la constante c est réglée à part, `level`)."""
    x = np.zeros((days, series))
    eta = rng.normal(0.0, ETA_SD, (days, series))
    for d in range(22, days):
        x[d] = (HAR[0] * x[d - 1] + HAR[1] * x[d - 5:d].mean(axis=0) + HAR[2] * x[d - 22:d].mean(axis=0) + eta[d])
        if leverage and z_daily is not None:
            x[d] += leverage * np.maximum(0.0, -z_daily[d - 1])
    return x


@cache
def level_shift(leverage: float) -> float:
    """2 · log E[exp(x / 2)] du processus stationnaire (simulation longue, graine fixe) : la constante c est réglée
    pour que la volatilité journalière MOYENNE soit celle demandée (3 % pour BTC, 4 % pour le bruit propre)."""
    rng = np.random.default_rng([mt.SEED, 99, int(leverage * 100)])
    z = rng.standard_normal((CALIBRATION_DAYS, 1))
    x = har_path(CALIBRATION_DAYS, 1, rng, z_daily=z, leverage=leverage)[1000:, 0]
    return float(2 * np.log(np.mean(np.exp(x / 2))))


# --- Un marché synthétique -------------------------------------------------------------------------------------------

@dataclass
class World:
    period: ms.Period
    start: pd.Timestamp
    n_days: int
    btc: pd.DataFrame
    pairs: dict[str, pd.DataFrame]
    fng: pd.Series
    funding: pd.DataFrame
    log_returns: np.ndarray                       # (séries, heures) : BTC puis les 20 paires


def bridge_extremes(x: np.ndarray, s: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Maximum et minimum (en log) d'un pont brownien de 0 à x, d'écart-type s sur l'heure."""
    u1, u2 = rng.random(x.shape), rng.random(x.shape)
    hi = (x + np.sqrt(x ** 2 - 2 * s ** 2 * np.log(u1))) / 2
    lo = (x - np.sqrt(x ** 2 - 2 * s ** 2 * np.log(u2))) / 2
    return hi, lo


def bars(lr: np.ndarray, s: np.ndarray, start: pd.Timestamp, rng: np.random.Generator, base: float) -> pd.DataFrame:
    lp = math.log(base) + np.cumsum(lr)
    close = np.exp(lp)
    open_ = np.exp(np.concatenate([[math.log(base)], lp[:-1]]))
    hi, lo = bridge_extremes(lr, s, rng)
    times = pd.date_range(start, periods=len(lr), freq="h").as_unit("ns")
    return pd.DataFrame({"open_time": times, "open": open_, "high": open_ * np.exp(hi), "low": open_ * np.exp(lo),
                         "close": close, "available_at": times + mt.HOUR + LATENCY})


def regimes(n_days: int, rng: np.random.Generator, *, block: bool) -> np.ndarray:
    """Dérive journalière de BTC : constante par bloc de 13 semaines (N3) ou par régime de durée exponentielle (N3b)."""
    drift = np.zeros(n_days)
    if block:
        for s in range(0, n_days, ms.BLOCK_DAYS):
            drift[s:s + ms.BLOCK_DAYS] = DRIFT * rng.choice((-1.0, 1.0))
        return drift
    d = 0
    while d < n_days:
        length = max(1, int(round(rng.exponential(REGIME_MEAN_DAYS))))
        drift[d:d + length] = DRIFT * rng.choice((-1.0, 1.0))
        d += length
    return drift


def make_world(spec: Spec, period: ms.Period | None = None) -> World:
    period = period or spec.period
    rng = np.random.default_rng(spec.seed)
    n_days = BURN_DAYS + len(period.days) + TAIL_DAYS
    start = period.start - BURN_DAYS * mt.DAY
    series = N_PAIRS + 1
    if spec.case == "N2t":
        eps = rng.standard_t(4, (n_days, 24, series)) / math.sqrt(2.0)
    else:
        eps = rng.standard_normal((n_days, 24, series))
    z_daily = eps.sum(axis=1) / math.sqrt(24.0)
    if spec.case == "N1":
        log_var = np.zeros((n_days, series))
        shift = 0.0
    else:
        leverage = LEVERAGE if spec.case == "N3c" else 0.0
        log_var = har_path(n_days, series, rng, z_daily=z_daily, leverage=leverage)
        shift = level_shift(leverage)
    vols = np.array([VOL_BTC] + [VOL_OWN] * N_PAIRS)
    var = np.exp(log_var - shift) * vols[None, :] ** 2                          # variance journalière
    sd_h = np.sqrt(var / 24.0)                                                  # (jours, séries)
    shocks = eps * sd_h[:, None, :]
    drift = np.zeros(n_days)
    if spec.case == "N3":
        drift = regimes(n_days, rng, block=True)
    elif spec.case == "N3b":
        drift = regimes(n_days, rng, block=False)
    days = pd.date_range(start, periods=n_days, freq="D")
    if spec.case == "N4":
        drift = drift + np.where(days.dayofweek == 0, MONDAY * np.sqrt(var[:, 0]), 0.0)
    # Prix martingales : P_t = P_{t−1} · (1 + σ_h · ε_t) ; dérives de N3, N3b et N4 ajoutées en rendement SIMPLE.
    btc_r = (shocks[:, :, 0] + drift[:, None] / 24.0).reshape(-1)
    btc_lr = np.log1p(np.maximum(btc_r, MIN_SIMPLE))
    betas = rng.uniform(*BETA, N_PAIRS)
    out_lr = [btc_lr]
    btc = bars(btc_lr, np.repeat(sd_h[:, 0], 24), start, rng, 10_000.0)
    pairs = {}
    for i in range(N_PAIRS):
        lr = np.log1p(np.maximum(betas[i] * btc_r + shocks[:, :, i + 1].reshape(-1), MIN_SIMPLE))
        s = np.sqrt(betas[i] ** 2 * np.repeat(sd_h[:, 0], 24) ** 2 + np.repeat(sd_h[:, i + 1], 24) ** 2)
        pairs[f"P{i:02d}USDT"] = bars(lr, s, start, rng, 100.0)
        out_lr.append(lr)
    log_returns = np.vstack(out_lr)
    return World(period, start, n_days, btc, pairs, fear_greed(btc_lr, start, n_days, rng),
                 funding(btc_lr, start, rng), log_returns)


def fear_greed(btc_lr: np.ndarray, start: pd.Timestamp, n_days: int, rng: np.random.Generator) -> pd.Series:
    """FG_d = min(100, max(0, 50 + 40 · tanh(r30_d / s30_d) + e_d)) ; r30, s30 sur les 30 jours finissant à d − 1 ;
    e_d = 0,8 · e_{d−1} + N(0 ; 8²). Rangé à la date d − 1 (valeur « horodatée d − 1 » lue le jour d)."""
    daily = btc_lr.reshape(n_days, 24).sum(axis=1)
    e = np.zeros(n_days)
    noise = rng.normal(0.0, FG["sd"], n_days)
    for d in range(1, n_days):
        e[d] = FG["phi"] * e[d - 1] + noise[d]
    values = np.full(n_days, np.nan)
    for d in range(31, n_days):
        window = daily[d - 30:d]
        s30 = window.std(ddof=1) * math.sqrt(30)
        values[d] = min(100.0, max(0.0, FG["center"] + FG["scale"] * math.tanh(window.sum() / s30) + e[d]))
    index = pd.date_range(start - mt.DAY, periods=n_days, freq="D")
    return pd.Series(values, index=index).dropna()


def funding(btc_lr: np.ndarray, start: pd.Timestamp, rng: np.random.Generator) -> pd.DataFrame:
    """Un règlement toutes les 8 h : f = 0,0001 + 0,0004 · max(0, r7 / s7) + N(0 ; 0,0001²) ; r7 = rendement log des
    168 heures closes au règlement, s7 = écart-type horaire de ces heures × √168 (connus au règlement)."""
    hours = np.arange(168, len(btc_lr), 8)
    cs = np.concatenate([[0.0], np.cumsum(btc_lr)])
    r7 = cs[hours] - cs[hours - 168]
    s7 = np.array([btc_lr[h - 168:h].std(ddof=1) for h in hours]) * math.sqrt(168)
    rate = FUND["base"] + FUND["slope"] * np.maximum(0.0, r7 / s7) + rng.normal(0.0, FUND["sd"], len(hours))
    return pd.DataFrame({"time": start + pd.to_timedelta(hours, unit="h"), "rate": rate})


# --- Prévisions synthétiques : HAR journalier simple -----------------------------------------------------------------

def daily_rv(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> np.ndarray:
    """RV_d = somme des carrés des rendements log horaires du jour d (NaN si une heure manque)."""
    t = mt.utc(frame["open_time"])
    grid = pd.date_range(calendar[0] - mt.HOUR, calendar[-1] + 23 * mt.HOUR, freq="h").as_unit("ns")
    close = pd.Series(frame["close"].to_numpy(float), index=t).reindex(grid).to_numpy(float)
    r2 = np.diff(np.log(close)) ** 2                                       # rendement de chaque heure du calendrier
    return r2.reshape(len(calendar), 24).sum(axis=1)


def har_features(rv: np.ndarray) -> np.ndarray:
    """Variables à l'origine d 00:00 : log RV_{d−1}, log moyenne RV_{d−5..d−1}, log moyenne RV_{d−22..d−1} (NaN si
    une journée de la fenêtre manque)."""
    out = np.full((len(rv), 3), np.nan)
    known = np.isfinite(rv)
    cs = np.concatenate([[0.0], np.cumsum(np.where(known, rv, 0.0))])
    cm = np.concatenate([[0], np.cumsum(~known)])
    d = np.arange(22, len(rv))
    out[d, 0] = np.log(rv[d - 1])
    for col, k in ((1, 5), (2, 22)):
        full = cm[d] - cm[d - k] == 0
        out[d, col] = np.where(full, np.log(np.maximum((cs[d] - cs[d - k]) / k, 1e-300)), np.nan)
    return out


@dataclass
class SyntheticForecasts:
    vol: mt.VolForecasts
    sigma: dict[str, pd.Series]


def synthetic_forecasts(btc: pd.DataFrame, pairs: dict[str, pd.DataFrame], start: pd.Timestamp, n_days: int,
                        days: pd.DatetimeIndex, *, mutation: str | None = None) -> SyntheticForecasts:
    calendar = mt.day_index(start, start + (n_days - 1) * mt.DAY)
    names = ["BTC", *pairs]
    rv = np.vstack([daily_rv(btc, calendar)] + [daily_rv(f, calendar) for f in pairs.values()])
    with np.errstate(divide="ignore", invalid="ignore"):
        feats = np.stack([har_features(r) for r in rv])                    # (séries, jours, 3)
        target = np.log(rv)
    refits = [calendar[0] + k * ms.BLOCK_DAYS * mt.DAY for k in range(n_days // ms.BLOCK_DAYS + 1)]
    instance_of = mt.instance_of_days(days, refits)
    by_instance: dict[pd.Timestamp, pd.Series] = {}
    sigma = {name: pd.Series(np.nan, index=days) for name in pairs}
    complete = np.isfinite(feats).all(axis=2)
    if mutation == "vol_cible_filtree":
        complete = complete & np.isfinite(target)
    rows_day = np.arange(n_days)
    for refit in refits:
        chosen = days[(instance_of == refit).to_numpy()]
        if not len(chosen):
            continue
        k = calendar.get_loc(refit)
        train = np.isfinite(feats).all(axis=2) & np.isfinite(target) & (rows_day[None, :] + 1 <= k)
        model = v1.fit_har(feats[train], target[train]) if train.sum() else None
        if model is None:
            continue
        lo = max(0, calendar.get_loc(chosen.min()) - mt.RANK_DAYS)
        hi = calendar.get_loc(chosen.max()) + 1
        span = slice(lo, hi)
        ok = complete[0, span]
        values = np.full(hi - lo, np.nan)
        values[ok] = model.predict(feats[0, span][ok])
        by_instance[refit] = pd.Series(values, index=calendar[span])
        rows = calendar.get_indexer(chosen)
        for j, name in enumerate(names[1:], start=1):
            ok_p = complete[j, rows]
            var = np.full(len(rows), np.nan)
            var[ok_p] = model.predict(feats[j, rows][ok_p])
            sigma[name].loc[chosen] = np.sqrt(var)
    return SyntheticForecasts(mt.VolForecasts(by_instance, instance_of), sigma)


# --- Feu, S2N et analyses d'une simulation ---------------------------------------------------------------------------

def pit_daily_of(pairs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for name, frame in pairs.items():
        daily = mt.daily_from_h1(frame)
        daily = daily[daily["complete"]]
        parts.append(pd.DataFrame({"day": daily.index, "symbol": name, "close": daily["close"].to_numpy(float),
                                   "quote_volume": 1e8}))
    return pd.concat(parts, ignore_index=True)


def all_members(names: list[str], start: pd.Timestamp, n_days: int) -> pd.DataFrame:
    months = pd.date_range(start.tz_convert(None).to_period("M").start_time, start.tz_convert(None)
                           + pd.Timedelta(days=n_days + 31), freq="MS", tz="UTC")
    return pd.DataFrame([{"month": m, "symbol": s} for m in months for s in names])


def s1_days(world: World, days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    first = days.min() - (mt.RANK_DAYS + mt.LOSS_FROM + 2) * mt.DAY
    return mt.day_index(max(first, world.start + 30 * mt.DAY), days.max())


@cache
def _draws(first: pd.Timestamp, last: pd.Timestamp, names: tuple[str, ...]) -> pd.DataFrame:
    """Tirages `S1` (mêmes graines que sur le réel) : identiques d'une simulation à l'autre, calculés une fois."""
    eligible = pd.DataFrame(True, index=mt.day_index(first, last), columns=list(names))
    return mt.s1_draws(eligible, minutes=24, step=mt.HOUR)


def synthetic_s1(world: World, s1_calendar: pd.DatetimeIndex, *, pairs: dict[str, pd.DataFrame] | None = None
                 ) -> pd.DataFrame:
    """Achats `S1` sur les bougies 1 h (approximation déclarée) : mêmes tirages et même transaction que sur le réel."""
    pairs = pairs if pairs is not None else world.pairs
    draws = _draws(s1_calendar[0], s1_calendar[-1], tuple(world.pairs)).copy()
    draws["r_gross"] = np.nan
    for name, idx in draws.groupby("symbol").groups.items():
        frame = pairs[name]
        if frame.empty:
            continue
        at = pd.DatetimeIndex(draws.loc[idx, "at"])
        r, _ = mt.play_s1(frame, frame, at)
        draws.loc[idx, "r_gross"] = r
    return draws[["day", "symbol", "at", "r_gross"]]


def inputs_of(world: World, days: pd.DatetimeIndex, *, btc: pd.DataFrame | None = None,
              pairs: dict[str, pd.DataFrame] | None = None, fng: pd.Series | None = None,
              funding_: pd.DataFrame | None = None, forecasts: bool = True,
              mutation: str | None = None) -> tuple[mt.LightInputs, SyntheticForecasts | None]:
    btc = world.btc if btc is None else btc
    pairs = world.pairs if pairs is None else pairs
    fc = synthetic_forecasts(btc, pairs, world.start, world.n_days, days, mutation=mutation) if forecasts else None
    inputs = mt.LightInputs(btc_h1=btc, pit_daily=pit_daily_of(pairs), members=all_members(list(world.pairs),
                            world.start, world.n_days), fng=world.fng if fng is None else fng,
                            funding=world.funding if funding_ is None else funding_,
                            s1=synthetic_s1(world, s1_days(world, days), pairs=pairs),
                            forecasts=fc.vol if fc is not None else None, latency=LATENCY)
    return inputs, fc


def basket(world: World, days: pd.DatetimeIndex, sigma: dict[str, pd.Series], *,
           pairs: dict[str, pd.DataFrame] | None = None, mutation: str | None = None) -> pd.DataFrame:
    pairs = world.pairs if pairs is None else pairs
    returns = {s: mt.basket_returns(f, days, mutation=mutation) for s, f in pairs.items()}
    firsts = {s: world.start for s in pairs}
    return mt.s2n(returns, sigma, all_members(list(pairs), world.start, world.n_days), firsts, days)


def sigma_168_all(world: World, days: pd.DatetimeIndex) -> dict[str, pd.Series]:
    return {s: mt.sigma_168(f, days) for s, f in world.pairs.items()}


def summary(a: ms.Analysis) -> dict:
    return {"delta_exc": a.delta_exc, "z": a.z, "p_high": a.p_high, "p_low": a.p_low, "ci": list(a.ci),
            "verdict": a.verdict, "equivalence": a.equivalence, "useful_blocks": a.useful_blocks,
            "red_days": a.red_days, "episodes": a.episodes, "dropped": int(sum(a.dropped)),
            "undefined": a.undefined_replicas, "guards": {k: a.guards.get(k) for k in ("G1", "G2", "G3", "G4")}}


def simulate(spec: Spec, *, samples: int = ms.SAMPLES) -> dict:
    """Une simulation : feu synthétique, `S2N`, analyses (nulle ; injection de Δ_min ; N3c+ ; lundi-mardi)."""
    world = make_world(spec)
    period = spec.period
    days = period.days
    variant = spec.question == "variante"
    inputs, fc = inputs_of(world, days, forecasts=not variant)
    states = mt.components(inputs, days, rule=period.rule)
    color = states["color"].to_numpy()
    sigma = sigma_168_all(world, days) if variant else fc.sigma            # type: ignore[union-attr]
    y_table = basket(world, days, sigma)
    y = y_table["y"].to_numpy(float)
    out = {"case": spec.case, "question": spec.question, "sim": spec.sim,
           "colors": {mt.COLOR_NAMES[c]: int((color == c).sum()) for c in mt.COLOR_NAMES},
           "components_danger": {c: float(np.nanmean(states[c].to_numpy(float))) for c in
                                 (mt.COMPONENTS_R4 if variant else mt.COMPONENTS)}}
    out["null"] = summary(ms.decide(ms.analyse(color, y, period, seed=mt.SEED + spec.sim, samples=samples)))
    red = color == mt.ROUGE
    if spec.case in ("N3", "N2"):
        injected = y - ms.DELTA_MIN * red                                   # −Δ_min × σ̂_{i,d} sur chaque paire (m1)
        out["injected"] = summary(ms.decide(ms.analyse(color, injected, period, seed=mt.SEED + spec.sim,
                                                       samples=samples)))
    if spec.case == "N3c":
        values = np.concatenate([sigma[s].reindex(days).to_numpy(float) for s in sigma])
        med = float(np.nanmedian(values))
        plus = y - ms.DELTA_MIN * med * y_table["inv_sigma"].to_numpy(float) * red     # effet en % (N3c+)
        out["plus"] = summary(ms.decide(ms.analyse(color, plus, period, seed=mt.SEED + spec.sim, samples=samples)))
        out["sigma_median"] = med
    if spec.case == "N4":                                                   # feu lundi-mardi seul : Σ r·r̃ = 0
        weekday = np.where(days.dayofweek.isin([0, 1]), mt.ROUGE, mt.VERT)
        prep = ms.prepare(weekday == mt.ROUGE, ms.holes_of(weekday, y), y, ms.blocks_of(len(days)))
        out["weekday_only_denominator"] = float(prep.den0.sum())
        out["weekday_only_defined"] = bool(np.isfinite(ms.estimate(prep)))
    return out


# --- Critères du § 7.2 ----------------------------------------------------------------------------------------------

def criteria(results: list[dict], key: str = "null") -> dict:
    rows = [r[key] for r in results if key in r]
    z = np.array([r["z"] for r in rows], float)
    hi = np.array([r["p_high"] for r in rows], float)
    lo = np.array([r["p_low"] for r in rows], float)
    ci = np.array([r["ci"] for r in rows], float)
    fp = float(np.mean((hi <= ms.ALPHA_SIDE) | (lo <= ms.ALPHA_SIDE)))
    ks_hi = float(stats.kstest(hi[np.isfinite(hi)], "uniform").pvalue)
    ks_lo = float(stats.kstest(lo[np.isfinite(lo)], "uniform").pvalue)
    above = float(np.mean(ci[:, 1] < 0))                 # vraie valeur (0) au-dessus de la borne haute
    below = float(np.mean(ci[:, 0] > 0))                 # vraie valeur sous la borne basse
    out: dict = {"simulations": len(rows), "z_mean": float(np.nanmean(z)), "false_positive": fp, "ks_p_high": ks_hi,
           "ks_p_low": ks_lo, "coverage_above": above, "coverage_below": below,
           "dropped_replicas": int(sum(r["dropped"] for r in rows)),
           "undefined_replicas": int(sum(r["undefined"] for r in rows)),
           "insufficient": float(np.mean([r["verdict"] == ms.INSUFFISANT for r in rows])),
           "useful_blocks_mean": float(np.mean([r["useful_blocks"] for r in rows])),
           "red_days_mean": float(np.mean([r["red_days"] for r in rows])),
           "episodes_mean": float(np.mean([r["episodes"] for r in rows]))}
    out["passed"] = {"z": abs(out["z_mean"]) <= CRITERIA["z_mean"], "false_positive": fp <= CRITERIA["false_positive"],
                     "ks": min(ks_hi, ks_lo) >= CRITERIA["ks_p"],
                     "coverage": max(above, below) <= CRITERIA["coverage_side"]}
    return out


def powers(by_case: dict[str, list[dict]]) -> dict:
    out: dict = {}
    if "N3" in by_case:
        rows = [r["injected"] for r in by_case["N3"]]
        out["superiority"] = float(np.mean([r["verdict"] == ms.PERSISTANCE for r in rows]))
        out["false_equivalence_N3"] = float(np.mean([_equivalent(r) for r in rows]))
    if "N2" in by_case:
        out["equivalence"] = float(np.mean([_equivalent(r["null"]) for r in by_case["N2"]]))
        out["superiority_N2_injected"] = float(np.mean([r["injected"]["verdict"] == ms.PERSISTANCE
                                                        for r in by_case["N2"] if "injected" in r]))
    if "N3c" in by_case:
        out["false_equivalence_N3c_plus"] = float(np.mean([_equivalent(r["plus"]) for r in by_case["N3c"]]))
    return out


def _equivalent(row: dict) -> bool:
    """Conclusion d'équivalence : intervalle dans [−Δ_min ; +Δ_min] et minimums G1 atteints (sinon INSUFFISANT). Une
    équivalence qui tombe avec une persistance (« effet inférieur à Δ_min ») compte aussi."""
    return bool(row["equivalence"] and row["verdict"] != ms.INSUFFISANT)


def rule(p: dict) -> dict:
    """Règle d'inutilité en deux parties (§ 7.2)."""
    sup = p.get("superiority", np.nan)
    eq = p.get("equivalence", np.nan)
    false_eq = max(p.get("false_equivalence_N3", np.nan), p.get("false_equivalence_N3c_plus", np.nan))
    return {"instrument_trop_faible": not (sup >= CRITERIA["power"]),
            "equivalence_non_jugeable": not (eq >= CRITERIA["power"]) or not (false_eq <= CRITERIA["false_equivalence"]),
            "superiority": sup, "equivalence": eq, "false_equivalence_max": false_eq}


def _job(args: tuple) -> dict:
    case, question, sim, samples = args
    return simulate(Spec(case, question, sim), samples=samples)


def run_case(case: str, question: str, *, sims: int = SIMS, workers: int = 4, samples: int = ms.SAMPLES,
             progress: Callable[[str], None] | None = None) -> list[dict]:
    say = progress or (lambda _t: None)
    jobs = [(case, question, k, samples) for k in range(sims)]
    out = []
    with ProcessPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        for k, result in enumerate(pool.map(_job, jobs, chunksize=2), start=1):
            out.append(result)
            if k % 20 == 0:
                say(f"{question} {case} : {k}/{sims}")
    return out


def run_controls(directory: Path, *, question: str, cases: tuple[str, ...] = CASES, sims: int = SIMS,
                 workers: int = 4, samples: int = ms.SAMPLES, progress: Callable[[str], None] | None = None) -> dict:
    """Cas synthétiques d'une question, l'un après l'autre ; résultats bruts et critères écrits dans `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    by_case: dict[str, list[dict]] = {}
    report: dict = {"question": question, "sims": sims, "samples": samples, "criteria": CRITERIA, "cases": {}}
    for case in cases:
        results = run_case(case, question, sims=sims, workers=workers, samples=samples, progress=progress)
        by_case[case] = results
        (directory / f"{question}_{case}.json").write_text(json.dumps(results, default=float), encoding="utf-8")
        entry = criteria(results)
        entry["judged"] = case in JUDGED
        if case == "N4":
            entry["weekday_only_defined_share"] = float(np.mean([r["weekday_only_defined"] for r in results]))
            entry["passed"]["weekday_only_undefined"] = entry["weekday_only_defined_share"] == 0.0
        report["cases"][case] = entry
        (directory / f"{question}_criteres.json").write_text(json.dumps(report, indent=2, default=float),
                                                             encoding="utf-8")
    report["powers"] = powers(by_case)
    report["rule"] = rule(report["powers"]) if {"N2", "N3", "N3c"} <= set(by_case) else None
    report["all_null_criteria_passed"] = all(all(v["passed"].values()) for c, v in report["cases"].items()
                                             if v["judged"])
    (directory / f"{question}_criteres.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    return report


def real_color_power(color: np.ndarray, period: ms.Period, *, sims: int = SIMS, samples: int = ms.SAMPLES,
                     workers: int = 4) -> dict:
    """Puissances sur la VRAIE suite des couleurs avec des rendements synthétiques `N2` (m5, passage unique) :
    équivalence sans injection, supériorité avec l'injection de Δ_min sur les jours rouges réels."""
    jobs = [(np.asarray(color), period, k, samples) for k in range(sims)]
    with ProcessPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        rows = list(pool.map(_real_color_job, jobs, chunksize=2))
    return {"simulations": sims, "superiority": float(np.mean([r["sup"] for r in rows])),
            "equivalence": float(np.mean([r["eq"] for r in rows]))}


def _real_color_job(args: tuple) -> dict:
    color, period, sim, samples = args
    spec = Spec("N2", "principale" if period.rule == "F6" else "variante", 10_000 + sim)
    world = make_world(spec)
    days = period.days
    sigma = (sigma_168_all(world, days) if period.rule == "R4"
             else synthetic_forecasts(world.btc, world.pairs, world.start, world.n_days, days).sigma)
    y = basket(world, days, sigma)["y"].to_numpy(float)
    red = color == mt.ROUGE
    null = ms.decide(ms.analyse(color, y, period, seed=mt.SEED + sim, samples=samples))
    inj = ms.decide(ms.analyse(color, y - ms.DELTA_MIN * red, period, seed=mt.SEED + sim, samples=samples))
    return {"sup": inj.verdict == ms.PERSISTANCE, "eq": null.equivalence}


def describe(report: dict) -> list[str]:
    lines = []
    for case, entry in report["cases"].items():
        p = entry["passed"]
        lines.append(f"{case} : |z moyen| {abs(entry['z_mean']):.3f} ({'OK' if p['z'] else 'ÉCHEC'}), faux positifs "
                     f"{entry['false_positive']:.3f} ({'OK' if p['false_positive'] else 'ÉCHEC'}), KS "
                     f"{min(entry['ks_p_high'], entry['ks_p_low']):.3f} ({'OK' if p['ks'] else 'ÉCHEC'}), couverture "
                     f"{entry['coverage_above']:.3f} / {entry['coverage_below']:.3f} "
                     f"({'OK' if p['coverage'] else 'ÉCHEC'})" + ("" if entry["judged"] else " [descriptif]"))
    if report.get("powers"):
        lines.append(f"puissances : {json.dumps(report['powers'], default=float)}")
    return lines


_ = asdict


# --- Causalité et mutations (§ 7.1), sur un marché synthétique ---------------------------------------------------------

CAUSAL_COLUMNS = (*mt.COMPONENTS, "btc_close", "btc_ema50", "largeur_share", "largeur_eligible", "fng", "funding_mean",
                  "funding_n", "m", "m_rank", "btc_forecast", "vol_rank", "color")
BY_TRUNCATION = ("ema_open_time", "largeur_jour_d", "fng_jour_d", "vol_cible_filtree", "pertes_d10_d1",
                 "financement_t_plus_8h")


def _changed(world: World, at: pd.Timestamp, how: str, rng: np.random.Generator, *, buy: bool = False
             ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.Series, pd.DataFrame]:
    """Données tronquées à `at` (ou futur falsifié) : bougies (`available_at`, ou ouverture pour l'achat), Fear &
    Greed (connu un jour après sa date), financement (connu 1 min après le règlement)."""
    column = "open_time" if buy else "available_at"
    if how == "tronqué":
        def change(frame: pd.DataFrame, col: str, delay: pd.Timedelta = mt.ZERO) -> pd.DataFrame:
            return mt.truncate(frame, col, at, delay=delay)
    else:
        def change(frame: pd.DataFrame, col: str, delay: pd.Timedelta = mt.ZERO) -> pd.DataFrame:
            return mt.falsify(frame, col, at, rng, delay=delay)
    fng = world.fng.rename("value").rename_axis("day").reset_index()
    fng = change(fng, "day", mt.FNG_KNOWN).set_index("day")["value"]
    return (change(world.btc, column), {s: change(f, column) for s, f in world.pairs.items()}, fng,
            change(world.funding, "time", mt.FUNDING_LATENCY))


def light_differences(world: World, picks: pd.DatetimeIndex, *, mutation: str | None, seed: int) -> list[dict]:
    """Feu et composantes recalculés au jour `d` sur des données tronquées à `T_d` et avec le futur falsifié."""
    rng = np.random.default_rng(seed)
    days = world.period.days
    full_inputs, _ = inputs_of(world, days, mutation=mutation)
    full = mt.components(full_inputs, days, rule=world.period.rule, mutation=mutation)
    out = []
    for d in picks:
        at = d + mt.DECISION
        for how in ("tronqué", "futur falsifié"):
            btc, pairs, fng, fund = _changed(world, at, how, rng)
            inputs, _ = inputs_of(world, pd.DatetimeIndex([d]), btc=btc, pairs=pairs, fng=fng, funding_=fund,
                                  mutation=mutation)
            got = mt.components(inputs, pd.DatetimeIndex([d]), rule=world.period.rule, mutation=mutation)
            diff = mt.compare_at(full.loc[d, list(CAUSAL_COLUMNS)], got.loc[d, list(CAUSAL_COLUMNS)])
            if diff:
                out.append({"day": str(d.date()), "check": how, "values": diff})
    return out


def basket_differences(world: World, picks: pd.DatetimeIndex, *, mutation: str | None, seed: int) -> list[dict]:
    """Panier du jour `d` : présence de chaque paire (éligibilité) et σ̂ recalculés sur des données tronquées à
    l'heure de l'achat (d 01:00, son ouverture comprise) ou avec le futur falsifié."""
    rng = np.random.default_rng(seed)
    days = world.period.days
    fc = synthetic_forecasts(world.btc, world.pairs, world.start, world.n_days, days)
    out = []
    for d in picks:
        buy = d + mt.EXEC_HOUR * mt.HOUR
        ref = {s: mt.basket_returns(f, pd.DatetimeIndex([d]), mutation=mutation)["r"].notna().iloc[0]
               for s, f in world.pairs.items()}
        sigma_ref = {s: fc.sigma[s].get(d) for s in world.pairs}
        for how in ("tronqué", "futur falsifié"):
            btc_t, pairs_t, _, _ = _changed(world, d + mt.DECISION, how, rng)
            fc_t = synthetic_forecasts(btc_t, pairs_t, world.start, world.n_days, pd.DatetimeIndex([d]))
            _, pairs_b, _, _ = _changed(world, buy, how, rng, buy=True)
            for s in world.pairs:
                present = mt.basket_returns(pairs_b[s], pd.DatetimeIndex([d]), mutation=mutation)["r"].notna().iloc[0]
                same_sigma = np.isclose(fc_t.sigma[s].get(d), sigma_ref[s], rtol=1e-9, equal_nan=True)
                if present != ref[s] or not same_sigma:
                    out.append({"day": str(d.date()), "check": how, "pair": s, "presence": bool(present != ref[s]),
                                "sigma": not bool(same_sigma)})
    return out


def membership_differences(world: World, picks: pd.DatetimeIndex, *, mutation: str | None, seed: int) -> list[dict]:
    """`LARGEUR` avec un top 40 à date recalculé à partir de volumes synthétiques variables (mutation « membres du
    mois en cours »)."""
    rng = np.random.default_rng(seed)
    pit = pit_daily_of(world.pairs)
    pit["quote_volume"] = rng.lognormal(18.0, 1.0, len(pit))
    pit["day"] = mt.utc(pit["day"])
    out = []
    for d in picks:
        at = d + mt.DECISION
        full = mt.largeur(pit, mt.membership_at(pit, mutation=mutation), pd.DatetimeIndex([d]))
        for how in ("tronqué", "futur falsifié"):
            if how == "tronqué":
                part = mt.truncate(pit, "day", at, delay=mt.DAY + LATENCY)
            else:
                part = mt.falsify(pit, "day", at, rng, delay=mt.DAY + LATENCY)
            got = mt.largeur(part, mt.membership_at(part, mutation=mutation), pd.DatetimeIndex([d]))
            diff = mt.compare_at(full.loc[d], got.loc[d])
            if diff:
                out.append({"day": str(d.date()), "check": how, "values": diff})
    return out


def funding_differences(world: World, *, mutation: str | None, count: int, seed: int) -> list[dict]:
    """Moyenne du financement à 30 s après des règlements tirés au hasard, données tronquées à cet instant."""
    rng = np.random.default_rng(seed)
    times = mt.utc(world.funding["time"])
    chosen = times[rng.choice(np.arange(200, len(times)), count, replace=False)] + pd.Timedelta(seconds=30)
    out = []
    for at in chosen:
        full, n_full = mt.funding_mean_at(world.funding, [at], mutation=mutation)
        part = mt.truncate(world.funding, "time", at, delay=mt.FUNDING_LATENCY)
        got, n_got = mt.funding_mean_at(part, [at], mutation=mutation)
        if n_full[0] != n_got[0] or not np.isclose(full[0], got[0], rtol=1e-12, equal_nan=True):
            out.append({"at": str(at), "n": [int(n_full[0]), int(n_got[0])]})
    return out


def rank_reference(world: World, days: pd.DatetimeIndex, *, mutation: str | None) -> int:
    """Rang de `VOL_HAUTE` recalculé à la main (base d − 365 à d − 1, instance du jour) : jours différents."""
    fc = synthetic_forecasts(world.btc, world.pairs, world.start, world.n_days, days)
    got = mt.vol_high(fc.vol, days, mutation=mutation)
    wrong = 0
    for d in days[::7]:
        inst = fc.vol.instance_of.get(d)
        if pd.isna(inst):
            continue
        series = fc.vol.by_instance[pd.Timestamp(inst)]
        value = series.get(d, np.nan)
        base = series[(series.index >= d - mt.RANK_DAYS * mt.DAY) & (series.index < d)].dropna().to_numpy()
        ref = float((base < value).mean()) if len(base) >= mt.RANK_MIN and np.isfinite(value) else np.nan
        if not np.isclose(ref, got.loc[d, "vol_rank"], equal_nan=True):
            wrong += 1
    return wrong


def rotation_violations(n_days: int, *, mutation: str | None, seed: int) -> int:
    """Un décalage permis du placebo garde, sur les cibles dont la source existe, la composition des jours rouges par
    jour de semaine (décalage multiple de 7) ; nombre de décalages qui la changent."""
    rng = np.random.default_rng(seed)
    red = rng.random(n_days) < 0.25
    weekday = np.arange(n_days) % ms.WEEK
    bad = 0
    for u in ms.placebo_lags(mutation=mutation):
        target = np.arange(u, n_days)
        source = target - u
        if (np.bincount(weekday[target][red[source]], minlength=7).tolist()
                != np.bincount(weekday[source][red[source]], minlength=7).tolist()):
            bad += 1
    return bad


def mutation_checks(*, sims: int = 1, picks: int = 4, progress: Callable[[str], None] | None = None) -> dict:
    """Contrôle de causalité (aucune différence tolérée sans mutation) et détection de chaque mutation du § 7.1."""
    say = progress or (lambda _t: None)
    report: dict = {"picks_per_sim": picks, "sims": sims, "baseline": {}, "mutations": {}}
    for k in range(sims):
        world = make_world(Spec("N2", "principale", 50_000 + k))
        rng = np.random.default_rng([mt.SEED, 7, k])
        days = world.period.days
        chosen = pd.DatetimeIndex(sorted(rng.choice(days, picks, replace=False)))
        say(f"causalité, simulation {k + 1}")
        base = {"feu": light_differences(world, chosen, mutation=None, seed=k),
                "panier": basket_differences(world, chosen, mutation=None, seed=k),
                "membres": membership_differences(world, chosen, mutation=None, seed=k),
                "financement": funding_differences(world, mutation=None, count=picks, seed=k),
                "rang": rank_reference(world, days, mutation=None),
                "rotations": rotation_violations(len(days), mutation=None, seed=k)}
        report["baseline"][str(k)] = base
        for mutation in mt.MUTATIONS:
            say(f"mutation {mutation}")
            found: list | int
            if mutation in BY_TRUNCATION:
                found = light_differences(world, chosen, mutation=mutation, seed=k)
                method = "feu recalculé sur données tronquées à T_d et futur falsifié"
            elif mutation == "panier_sans_ouverture_future":
                found = basket_differences(world, chosen, mutation=mutation, seed=k)
                method = "panier recalculé sur données tronquées à l'heure de l'achat"
            elif mutation == "membres_mois_courant":
                found = membership_differences(world, chosen, mutation=mutation, seed=k)
                method = "LARGEUR avec top 40 recalculé, données tronquées"
            elif mutation == "financement_sans_latence":       # règle de latence testée à part (règlement + 30 s)
                found = funding_differences(world, mutation=mutation, count=picks, seed=k)
                method = "moyenne du financement 30 s après un règlement, données tronquées"
            elif mutation in ("rang_inclut_jour", "base_autre_instance"):
                found = rank_reference(world, days, mutation=mutation)
                method = "rang recalculé à la main (base d − 365 à d − 1, même instance)"
            elif mutation == "rotation_non_multiple_7":
                found = rotation_violations(len(days), mutation=mutation, seed=k)
                method = "nombre de jours rouges et composition par jour de semaine de chaque rotation"
            else:
                report["mutations"][mutation] = {"detected": None, "method": "test pytest (coupure des lecteurs réels)"}
                continue
            entry = report["mutations"].setdefault(mutation, {"detected": True, "method": method, "found": []})
            entry["found"].append(found if isinstance(found, int) else len(found))
            entry["detected"] = entry["detected"] and bool(found)
    report["baseline_clean"] = all(not v["feu"] and not v["panier"] and not v["membres"] and not v["financement"]
                                   and v["rang"] == 0 and v["rotations"] == 0 for v in report["baseline"].values())
    report["all_detected"] = all(v["detected"] for v in report["mutations"].values() if v["detected"] is not None)
    return report
