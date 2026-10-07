"""Contrôles d'avant exécution du programme « combinaisons » (docs/COMBINAISONS.md, § 1.8), sur données SYNTHÉTIQUES
seulement : marches aléatoires sans mémoire (5 cas), test « facteur commun » avec ses deux témoins, contrôles positifs.

Même code de bout en bout que l'étude (`combinations.brick_table`, `combinations_study.play_rows`, `evaluate`).
Aucune donnée réelle n'est lue ; rien n'est inscrit au registre (rien n'est un essai).
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..forward.costs import SCENARIOS
from . import combinations as cb
from . import combinations_study as cs
from . import figures_history as fh

CASES = ("constante", "regimes_vol", "tendances", "hausse_chute", "facteur_commun")
DRIFT_CASES = frozenset({"tendances", "hausse_chute", "facteur_commun"})
WALKS = 120
YEARS = 3
START = pd.Timestamp("2019-01-01", tz="UTC")
SIGMA = 0.0012                     # écart-type d'un rendement log d'une minute (≈ 0,9 % par heure)
DRIFT = 0.004                      # ± 0,4 % par jour
VOL_BLOCK_DAYS, TREND_BLOCK_DAYS = 20, 30
LATENCY = pd.Timedelta(seconds=2)
MAX_Z, MAX_BIAS = 3.0, 0.02
WITNESS_STRIDE = 4                 # témoins : une clôture sur 4 heures (non comptés ; l'espérance ne change pas)
POSITIVE_EFFECT = 0.15
NULL_RULES = (*cb.NEW_BRICKS, *cs.STEP2_RULES)


@dataclass
class Walk:
    symbol: str
    h1: pd.DataFrame
    btc: pd.DataFrame
    minutes: fh.Minutes


def _blocks(rng: np.random.Generator, n_minutes: int, days: int, choices: tuple[float, ...]) -> np.ndarray:
    size = days * 1440
    count = math.ceil(n_minutes / size)
    return np.repeat(rng.choice(choices, size=count), size)[:n_minutes]


def _ohlc(returns: np.ndarray, sigma: np.ndarray, rng: np.random.Generator, first: float = 100.0):
    c = first * np.exp(np.cumsum(returns))
    o = np.r_[first, c[:-1]]
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 1, len(c))) * sigma / 2)
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 1, len(c))) * sigma / 2)
    return o, h, lo, c


def _hourly(o, h, lo, c, rng: np.random.Generator, with_flow: bool = True) -> pd.DataFrame:
    hours = len(c) // 60
    shape = (hours, 60)
    times = START + pd.to_timedelta(np.arange(hours), unit="h")
    frame = pd.DataFrame({"open_time": times, "open": o[:hours * 60].reshape(shape)[:, 0],
                          "high": h[:hours * 60].reshape(shape).max(axis=1), "low": lo[:hours * 60].reshape(shape).min(axis=1),
                          "close": c[:hours * 60].reshape(shape)[:, -1]})
    frame["available_at"] = frame["open_time"] + pd.Timedelta(hours=1) + LATENCY
    if with_flow:                   # flux tirés indépendamment des rendements
        volume = np.exp(rng.normal(math.log(1000.0), 0.5, hours))
        share = np.clip(rng.normal(0.48, 0.06, hours), 0.01, 0.99)
        lam = np.exp(rng.normal(math.log(400.0), 0.7, hours))
        frame["base_volume"] = volume
        frame["taker_buy_base_volume"] = volume * share
        frame["number_of_trades"] = rng.poisson(lam).astype(float)
    return frame


def synthetic_walk(case: str, seed: int, *, years: int = YEARS, drift_after: list[tuple[int, int, float]] | None = None) -> Walk:
    """Une paire synthétique de `years` années en minutes, et son BTC. `drift_after` : (minute de début, durée en
    minutes, dérive log totale) ajoutées aux rendements (contrôles positifs)."""
    if case not in CASES:
        raise ValueError(f"cas inconnu : {case}")
    rng = np.random.default_rng(seed)
    n = years * 365 * 1440
    mult = np.ones(n)
    drift = np.zeros(n)
    if case in ("regimes_vol", "hausse_chute"):
        mult = _blocks(rng, n, VOL_BLOCK_DAYS, (0.5, 1.0, 2.0))
    if case == "tendances":
        drift = _blocks(rng, n, TREND_BLOCK_DAYS, (-DRIFT, DRIFT)) / 1440
    if case == "hausse_chute":
        drift = np.where(np.arange(n) < n // 2, DRIFT, -DRIFT) / 1440
    sigma = SIGMA * mult
    if case == "facteur_commun":
        btc_sigma = np.full(n, 0.0008)
        btc_ret = rng.normal(0, 1, n) * btc_sigma + _blocks(rng, n, TREND_BLOCK_DAYS, (-DRIFT, DRIFT)) / 1440
        beta = rng.uniform(0.5, 1.5)
        own = rng.normal(0, 1, n) * 0.0009
        returns = beta * btc_ret + own
        sigma = np.sqrt((beta * 0.0008) ** 2 + 0.0009 ** 2) * np.ones(n)
    else:
        returns = rng.normal(0, 1, n) * sigma + drift
        btc_ret = rng.normal(0, 1, n) * 0.0008
    for begin, length, total in drift_after or []:
        end = min(n, begin + length)
        if end > begin:
            returns[begin:end] += total / length
    o, h, lo, c = _ohlc(returns, sigma, rng)
    h1 = _hourly(o, h, lo, c, rng)
    bo, bh, blo, bc = _ohlc(btc_ret, np.full(n, 0.0008), rng, 10_000.0)
    btc = _hourly(bo, bh, blo, bc, rng, with_flow=False)
    ns = (START + pd.to_timedelta(np.arange(n), unit="min")).as_unit("ns").asi8
    arrays = [np.ascontiguousarray(a) for a in (o, h, lo, c, ns)]
    return Walk(f"S{seed}USDT", h1, btc[["open_time", "close", "available_at"]],
                fh.Minutes(o=arrays[0], h=arrays[1], lo=arrays[2], c=arrays[3], ns=arrays[4]))


def walk_trades(walk: Walk, *, witnesses: bool = False) -> pd.DataFrame:
    """Toutes les transactions d'une marche (déclencheurs, et témoins du test « facteur commun » si demandé)."""
    end = pd.Timestamp(int(walk.minutes.ns[-1]), tz="UTC")
    daily = cb.btc_daily(walk.btc)
    data = cs.pair_data(walk.symbol, walk.h1, daily, end=end)
    rows = set(cs.trigger_rows(data).tolist())
    w1 = w2 = np.zeros(len(data.table), dtype=bool)
    if witnesses:
        stride = (pd.DatetimeIndex(data.table["open_time"]).hour % WITNESS_STRIDE == 0)
        w1 = (data.table["BTC_HAUSSIER"].to_numpy(float) == 1) & data.in_period & stride
        w2 = (cb.btc_week_up(data.table["available_at"], walk.btc) == 1) & data.in_period & stride
        rows |= set(np.flatnonzero(w1 | w2).tolist())
    ordered = np.asarray(sorted(rows), np.int64)
    out = pd.DataFrame(cs.play_rows(data, ordered, walk.minutes, LATENCY))
    if len(out):
        out["witness_1"] = w1[ordered]
        out["witness_2"] = w2[ordered]
        out["trigger"] = data.votes["trigger"].to_numpy(bool)[ordered] & data.in_period[ordered]
    return out


def _null_job(args: tuple) -> pd.DataFrame:
    case, seed = args
    walk = synthetic_walk(case, seed)
    out = walk_trades(walk, witnesses=case == "facteur_commun")
    return out.assign(walk=seed, case=case)


def cluster_z(values: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float]:
    """(moyenne, erreur type par grappes, z) ; grappes = marche × mois (comme le contrôle des lignes de tendance)."""
    ok = np.isfinite(values)
    values, clusters = values[ok], clusters[ok]
    if len(values) < 2:
        return math.nan, math.nan, math.nan
    mean = float(values.mean())
    frame = pd.DataFrame({"x": values, "g": clusters}).groupby("g")["x"].agg(["sum", "count"])
    se = float(np.sqrt(((frame["sum"] - mean * frame["count"]) ** 2).sum()) / frame["count"].sum())
    return mean, se, (mean / se if se > 0 else math.nan)


def null_statistics(trades: pd.DataFrame) -> dict:
    """Excès moyens, erreurs types et z de chaque règle (et des témoins), par type d'excès et scénario."""
    trades = trades.reset_index(drop=True)
    trig = trades[trades["trigger"]].reset_index(drop=True)
    models, infos = cs.logit_models(trig)
    buy, _ = cs.logit_select(trig, models)
    masks = cs.rule_masks(trig, logit_buy=buy)
    parts = {name: trig[masks[name]] for name in NULL_RULES}
    if "witness_1" in trades:
        parts["TEMOIN_1"] = trades[trades["witness_1"]]
        parts["TEMOIN_2"] = trades[trades["witness_2"]]
    out: dict = {"logit_folds": infos}
    for name, part in parts.items():
        done = part[part["status"] == fh.EXECUTED]
        clusters = (done["walk"].astype(str) + pd.to_datetime(done["fill_at"], utc=True).dt.strftime("%Y-%m")).to_numpy()
        row: dict = {"n": int(len(done))}
        for kind, column in (("uniforme", "uexcess_adj"), ("timing", "texcess")):
            for s in SCENARIOS:
                mean, se, z = cluster_z(done[f"{column}_{s}"].astype(float).to_numpy(), clusters)
                row[f"{kind}_{s}"] = {"mean": round(mean, 4), "se": round(se, 4), "z": round(z, 2)}
        row["r_central"] = round(float(done["r_central"].astype(float).mean()), 4) if len(done) else None
        out[name] = row
    return out


def judge(case: str, stats: dict) -> dict:
    """Critère du § 1.8 par règle et type d'excès : |z| < 3 et |biais| ≤ 0,02 R (les deux scénarios). L'excès uniforme
    des règles à états n'est pas jugé dans les cas à dérive persistante."""
    out = {}
    for name in NULL_RULES:
        row = stats[name]
        verdict = {}
        for kind in ("uniforme", "timing"):
            if kind == "uniforme" and name in cs.STATE_RULES and case in DRIFT_CASES:
                verdict[kind] = "NON_JUGE"
                continue
            ok = all(abs(row[f"{kind}_{s}"]["z"]) < MAX_Z and abs(row[f"{kind}_{s}"]["mean"]) <= MAX_BIAS
                     for s in SCENARIOS if math.isfinite(row[f"{kind}_{s}"]["z"]))
            verdict[kind] = "PASSE" if ok else "ECHEC"
        out[name] = verdict
    return out


def fallback_bias(results: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Règle de repli (§ 1.8) : pour une règle en échec sur un type d'excès, biais maximal = max(0, plus grand biais
    mesuré sur ses cas valides) ; le témoin 2 compte parmi les biais de timing des règles à états."""
    out: dict[str, dict[str, float]] = {}
    witness = max((results["facteur_commun"]["stats"]["TEMOIN_2"][f"timing_{s}"]["mean"] for s in SCENARIOS),
                  default=0.0) if "facteur_commun" in results else 0.0
    for name in NULL_RULES:
        entry: dict[str, float] = {}
        for kind in ("uniforme", "timing"):
            valid = [c for c in results if results[c]["verdict"][name][kind] != "NON_JUGE"]
            biases = [results[c]["stats"][name][f"{kind}_{s}"]["mean"] for c in valid for s in SCENARIOS]
            if kind == "timing" and name in cs.STATE_RULES:
                biases.append(witness)
            failed = any(results[c]["verdict"][name][kind] == "ECHEC" for c in valid)
            state_timing = kind == "timing" and name in cs.STATE_RULES and witness > 0
            if failed or state_timing:
                entry[kind] = round(max([0.0, *[b for b in biases if math.isfinite(b)]]), 4)
        if entry:
            out[name] = entry
    return out


def run_null(*, walks: int = WALKS, cases: tuple[str, ...] = CASES, workers: int = 4, seed0: int = 70_000,
             progress: Callable[[str], None] | None = None) -> dict:
    say = progress or (lambda _t: None)
    results: dict = {}
    for k, case in enumerate(cases):
        jobs = [(case, seed0 + 1000 * k + w) for w in range(walks)]
        with ProcessPoolExecutor(workers) as pool:
            frames = []
            for i, frame in enumerate(pool.map(_null_job, jobs)):
                frames.append(frame)
                say(f"{case} : marche {i + 1}/{walks}")
        trades = pd.concat(frames, ignore_index=True)
        stats = null_statistics(trades)
        results[case] = {"walks": walks, "trades": int((trades["status"] == fh.EXECUTED).sum()), "stats": stats,
                         "verdict": judge(case, stats)}
        if case == "facteur_commun":
            w1 = stats["TEMOIN_1"]["uniforme_central"]
            results[case]["temoin_1_borne_basse"] = round(w1["mean"] - 1.96 * w1["se"], 4)
            results[case]["temoin_1_ok"] = bool(w1["mean"] - 1.96 * w1["se"] > 0)
    return {"results": results, "fallback_bias": fallback_bias(results)}


# --- Contrôles positifs (informatifs) ---------------------------------------------------------------------------------

def _positive_job(args: tuple) -> pd.DataFrame:
    kind, seed = args
    walk = synthetic_walk("constante", seed)
    end = pd.Timestamp(int(walk.minutes.ns[-1]), tz="UTC")
    data = cs.pair_data(walk.symbol, walk.h1, cb.btc_daily(walk.btc), end=end)
    if kind == "CVD_DIV":
        marked = data.table["CVD_DIV"].to_numpy(bool) & data.in_period
    else:
        marked = data.votes["trigger"].to_numpy(bool) & (data.votes["n_votes"].to_numpy(float) >= 3) & data.in_period
    drifts = []
    for i in np.flatnonzero(marked):
        row = data.table.iloc[i]
        start = int((pd.Timestamp(row["at"]) + LATENCY).ceil("min").value // 60_000_000_000
                    - START.value // 60_000_000_000)
        risk = cb.STOP_ATR * float(row["atr"]) / float(row["close"])
        if math.isfinite(risk):
            drifts.append((start, 60 * 60, POSITIVE_EFFECT * risk))
    injected = synthetic_walk("constante", seed, drift_after=drifts)
    return walk_trades(injected).assign(walk=seed)


def run_positive(*, simulations: int = 10, pairs: int = 40, workers: int = 4, seed0: int = 90_000,
                 progress: Callable[[str], None] | None = None) -> dict:
    """Part des simulations (univers de `pairs` marches de 3 ans) où la règle sort `PISTE` avec un effet injecté de
    +0,15 R : (1) après une divergence CVD, (2) après un vote à 3 au moins. Puissance informative, non bloquante."""
    say = progress or (lambda _t: None)
    out: dict = {}
    for kind, rule in (("CVD_DIV", "CVD_DIV"), ("VOTE_3", "VOTE_3")):
        decisions = []
        for sim in range(simulations):
            jobs = [(kind, seed0 + 10_000 * (kind == "VOTE_3") + 100 * sim + p) for p in range(pairs)]
            with ProcessPoolExecutor(workers) as pool:
                trades = pd.concat(list(pool.map(_positive_job, jobs)), ignore_index=True)
            trig = trades[trades["trigger"]].reset_index(drop=True)
            rows = cs.evaluate(trig, (rule,), level=cs.LEVEL_R, samples=cs.SAMPLES_R)
            decisions.append({"decision": rows[rule]["excess"]["decision"],
                              "timing_mean": rows[rule]["measures"]["central"]["timing"]["mean"],
                              "timing_ci": rows[rule]["measures"]["central"]["timing"]["ci"],
                              "n": rows[rule]["measures"]["central"]["timing"]["n"]})
            say(f"contrôle positif {kind} : simulation {sim + 1}/{simulations}")
        out[kind] = {"simulations": simulations, "pairs": pairs, "years": YEARS,
                     "piste_share": round(sum(d["decision"] == cs.PISTE for d in decisions) / simulations, 3),
                     "runs": decisions}
    return out


def write(directory, payload: dict, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
