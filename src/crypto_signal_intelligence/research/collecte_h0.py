"""Contrôle sous l'hypothèse nulle des tests en direct F25 à F30 (docs/FORWARD_TESTS.md, sections F25_LIQ_CASCADE …
F30_TRENDING), lancé UNE fois avant leur démarrage (`tests/test_collecte_h0.py`, marqué `slow`).

Marché synthétique, 200 répliques INDÉPENDANTES par test (graine 20261012, `SeedSequence`, une sous-graine par test et
par réplique) :
- **prix martingales** sur des clôtures 15 min : r_t = σ_t (0,6 m_t + 0,8 e_t), facteur de marché m commun, e propre à
  la paire, gaussiens (moitié des paires) ou Student à 4 degrés de variance 1 (l'autre moitié) ; σ_t GARCH(1,1)
  (α = 0,05, β = 0,94), σ moyen par quart d'heure tiré log-uniforme entre 0,25 % et 0,6 % ; prix = 100 × Π(1 + r) :
  E[prix futur | passé] = prix présent, l'espérance du rendement brut à 24 h est nulle ;
- **grandeurs du collecteur liées au rendement CONTEMPORAIN, jamais au futur** :
  F25 liquidations longues par quart d'heure L_p · exp(0,8 z) · exp(−1,5 r/σ) (plus fortes quand le prix baisse), somme
  sur 1 h ; F26 déséquilibre horaire tanh(0,5 r_h/σ_h + u_h), u AR(1) ; F27 profondeur acheteuse horaire
  B_p · exp(v_h − 0,4 |r_h|/σ_h), v AR(1) ; F28 solde des gros ordres par quart d'heure W_p · (0,8 r/σ + Student 3),
  somme sur 1 h ; F29 asymétrie AR(1) (0,995) poussée par −r/σ, DVOL AR(1) poussé par |r|/σ ; F30 présence dans les
  « trending » en chaîne de Markov (reste avec p = 0,85, entre avec p = logistique(−4,5 + 0,8 R24/σ24) : plus souvent
  après une hausse sur 24 h) ;
- **trous du collecteur** : 6 pannes par réplique (début uniforme, durée uniforme de 15 min à 8 h) : fenêtres touchées
  sans valeur ;
- **chaîne exacte du direct** à partir des séries : seuils, déclenchements et rodage de `forward/collecte_events`
  (`triggers`, `select_events`, `entry_time`), mesure `returns_from`, `summarize` et `verdict` (10 000 tirages) ; le
  test démarre 1 jour après le début des données, 84 jours de recueil.

Critère, par test : faux `SUPERIEUR_A_ZERO` (hypothèse positive) ou faux `INFERIEUR_A_ZERO` (hypothèse négative)
≤ 0,05/6 × 2 sur les 200 répliques. Contrôle positif (aucune exigence, ces tests sont en direct) : +0,5 % ajouté au
rendement brut à 24 h de chaque événement (−0,5 % pour les hypothèses négatives), prix de sortie multiplié d'autant ;
puissance = part des répliques qui concluent. Placebos non simulés (descriptifs, ils ne décident de rien).
Ce qui n'est PAS contrôlé ici : la lecture des journaux (testée à part sur journaux fictifs, `tests/test_forward_f25_f30.py`).
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from ..forward import collecte_events as C
from ..forward.costs import SCENARIOS

SEED = 20261012
REPLICATES = 200
START = pd.Timestamp("2027-01-04", tz="UTC")          # début des données synthétiques (date fictive)
LEAD_DAYS, TEST_DAYS, TAIL_DAYS = 1, 84, 11
STEPS_PER_DAY = 96
PAIRS = {C.LIQ: 30, C.IMB: 16, C.BID_DROP: 16, C.WHALE: 16, C.FEAR: 1, C.TREND: 40}
OUTAGES, OUTAGE_MIN, OUTAGE_MAX = 6, pd.Timedelta(minutes=15), pd.Timedelta(hours=8)
INJECT = 0.005
MAX_FALSE = 0.05 / 6 * 2
ALPHA_G, BETA_G = 0.05, 0.94
Q = C.STEP


def symbol_of(i: int) -> str:
    return C.BTC if i < 0 else f"S{i:02d}USDT"


def market(rng: np.random.Generator, pairs: int, days: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    """Clôtures 15 min (index = instant de clôture), rendements simples r et σ_t, forme (temps, paires)."""
    n = days * STEPS_PER_DAY
    times = pd.date_range(START + Q, periods=n, freq=Q)
    mean_sigma = np.exp(rng.uniform(np.log(0.0025), np.log(0.006), pairs))
    omega = mean_sigma ** 2 * (1 - ALPHA_G - BETA_G)
    m = rng.standard_normal(n)
    e = rng.standard_normal((n, pairs))
    heavy = np.arange(pairs) % 2 == 1
    e[:, heavy] = rng.standard_t(4, (n, heavy.sum())) / math.sqrt(2.0)
    var = mean_sigma ** 2
    r = np.empty((n, pairs))
    sigma = np.empty((n, pairs))
    for t in range(n):
        s = np.sqrt(var)
        sigma[t] = s
        r[t] = s * (0.6 * m[t] + 0.8 * e[t])
        r[t] = np.maximum(r[t], -0.5)
        var = omega + ALPHA_G * r[t] ** 2 + BETA_G * var
    return times, r, sigma


def _outage_mask(rng: np.random.Generator, ends: pd.DatetimeIndex, window: pd.Timedelta) -> np.ndarray:
    """Vrai pour une fin E dont la fenêtre [E − fenêtre, E) touche une panne du collecteur."""
    bad = np.zeros(len(ends), bool)
    span = (ends[-1] - ends[0]).total_seconds()
    for _ in range(OUTAGES):
        begin = ends[0] + pd.Timedelta(seconds=float(rng.uniform(0, span)))
        length = OUTAGE_MIN + (OUTAGE_MAX - OUTAGE_MIN) * float(rng.uniform())
        bad |= (ends - window < begin + length) & (ends > begin)
    return bad


def _hourly(times: pd.DatetimeIndex, r: np.ndarray) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Rendements (log) sommés par heure, rangés à la fin de l'heure."""
    hours = times[3::4]
    return hours, np.log1p(r).reshape(-1, 4, r.shape[1]).sum(axis=1)


def series(kind: str, rng: np.random.Generator, times: pd.DatetimeIndex, r: np.ndarray,
           sigma: np.ndarray) -> pd.DataFrame:
    """Grandeur synthétique du collecteur sur la grille du test (NaN = fenêtre touchée par une panne)."""
    pairs = r.shape[1]
    cols = [symbol_of(i) for i in range(pairs)] if kind != C.FEAR else ["skew", "dvol"]
    z = r / sigma
    if kind in (C.LIQ, C.WHALE):
        if kind == C.LIQ:
            level = np.exp(rng.uniform(np.log(2_000), np.log(40_000), pairs))
            bucket = level * np.exp(0.8 * rng.standard_normal(r.shape)) * np.exp(-1.5 * z)
        else:
            level = np.exp(rng.uniform(np.log(20_000), np.log(200_000), pairs))
            bucket = level * (0.8 * z + rng.standard_t(3, r.shape))
        values = pd.DataFrame(bucket, index=times).rolling(4, min_periods=4).sum()
        values.columns = cols
        values.loc[_outage_mask(rng, times, C.HOUR)] = np.nan
        return values
    if kind == C.FEAR:
        n = len(times)
        skew, dvol = np.empty(n), np.empty(n)
        s, d = 5.0, 45.0
        noise = rng.standard_normal((n, 2))
        for t in range(n):
            s = 0.995 * s + 0.005 * 5.0 + 0.3 * (-z[t, 0]) + 0.1 * noise[t, 0]
            d = 0.998 * d + 0.002 * 45.0 + 0.2 * (abs(z[t, 0]) - 0.8) + 0.1 * noise[t, 1]
            skew[t], dvol[t] = s, d
        values = pd.DataFrame({"skew": skew, "dvol": dvol}, index=times)
        values.loc[_outage_mask(rng, times, Q)] = np.nan
        missing_skew = rng.uniform(size=n) < 0.02                         # asymétrie absente 2 % du temps : repli DVOL
        values.loc[missing_skew, "skew"] = np.nan
        return values
    hours, rh = _hourly(times, r)
    sh = sigma.reshape(-1, 4, pairs).mean(axis=1) * 2.0                 # σ horaire ≈ 2 σ du quart d'heure
    zh = rh / sh
    n = len(hours)
    if kind == C.IMB:
        u = np.zeros((n, pairs))
        noise = rng.standard_normal((n, pairs)) * 0.3
        for t in range(1, n):
            u[t] = 0.7 * u[t - 1] + noise[t]
        out = np.tanh(0.5 * zh + u)
    elif kind == C.BID_DROP:
        v = np.zeros((n, pairs))
        noise = rng.standard_normal((n, pairs)) * 0.2
        for t in range(1, n):
            v[t] = 0.9 * v[t - 1] + noise[t]
        level = np.exp(rng.uniform(np.log(1e5), np.log(5e6), pairs))
        out = level * np.exp(v - 0.4 * np.abs(zh))
    else:                                                                # C.TREND
        out = np.zeros((n, pairs))
        past = pd.DataFrame(rh).rolling(24, min_periods=24).sum().fillna(0.0).to_numpy()
        scale = sh * math.sqrt(24)
        present = np.zeros(pairs, bool)
        for t in range(n):
            enter = rng.uniform(size=pairs) < 1 / (1 + np.exp(-(-4.5 + 0.8 * past[t] / scale[t])))
            stay = rng.uniform(size=pairs) < 0.85
            present = np.where(present, stay, enter)
            out[t] = present
    values = pd.DataFrame(out, index=hours, columns=cols, dtype=float)
    values.loc[_outage_mask(rng, hours, C.HOUR)] = np.nan
    return values


def _row(closes: pd.Series, symbol: str, entry: pd.Timestamp, shift: float) -> dict | None:
    """Rendements de l'événement par `returns_from` ; contrôle positif : prix de sortie à 24 h × (1 + shift)."""
    event = C.returns_from(closes, entry, symbol)
    if event[C.PRIMARY] is None:
        return None
    if shift:
        start, end = closes.get(entry), closes.get(entry + C.HORIZONS[C.PRIMARY]) * (1 + shift)
        event[C.PRIMARY] = {C.GROSS: end / start - 1, **{s: C.net_return(start, end, symbol, s) for s in SCENARIOS}}
    empty = {h: {"n": 0, **{m: None for m in C.MEASURES}} for h in C.HORIZONS}
    return {"entry_at": C.utc_iso(entry), "results": {"event": event, "placebo_mean": empty,
                                                     "excess": {h: {m: None for m in C.MEASURES} for h in C.HORIZONS}}}


def replicate(test_id: str, seed: np.random.SeedSequence, *, samples: int = C.SAMPLES) -> dict:
    """Une réplique : marché, grandeur, événements par la chaîne du direct, verdict sans et avec effet injecté."""
    spec = C.SPECS[test_id]
    rng = np.random.default_rng(seed)
    days = LEAD_DAYS + TEST_DAYS + TAIL_DAYS
    times, r, sigma = market(rng, PAIRS[spec.kind], days)
    values = series(spec.kind, rng, times, r, sigma)
    started = START + pd.Timedelta(days=LEAD_DAYS)
    final = started + pd.Timedelta(days=TEST_DAYS)
    rows = C.triggers(spec, values)
    chosen = C.select_events(rows, start_at=started, final_at=final, last_by_symbol={})
    names = [symbol_of(i) for i in range(r.shape[1])] if spec.kind != C.FEAR else [C.BTC]
    prices = pd.DataFrame(100.0 * np.cumprod(1 + r, axis=0), index=times, columns=names)
    shift = INJECT * spec.sign
    null, positive = [], []
    for item in chosen:
        entry = C.entry_time(item["end"] + spec.lag)
        closes = prices[item["symbol"]]
        a, b = _row(closes, item["symbol"], entry, 0.0), _row(closes, item["symbol"], entry, shift)
        if a is not None and b is not None:
            null.append(a)
            positive.append(b)
    out: dict = {"events": len(chosen), "measured": len(null),
           "days": int(pd.DatetimeIndex([x["entry_at"] for x in null]).floor("D").nunique()) if null else 0,
           "trou": int((~rows["evaluable"]).sum()), "windows": int(len(rows))}
    for name, rows_ in (("null", null), ("positive", positive)):
        measures = C.summarize(rows_, samples=samples)
        out[name] = {"verdict": C.verdict(measures, sign=spec.sign, ended=True),
                     "gross_mean": measures[C.GROSS][C.PRIMARY].get("mean"),
                     "gross_ci": measures[C.GROSS][C.PRIMARY].get("ci_decision"),
                     "central_mean": measures["central"][C.PRIMARY].get("mean")}
    return out


def run_test(test_id: str, *, replicates: int = REPLICATES, samples: int = C.SAMPLES,
             progress: Callable[[str], None] | None = None) -> dict:
    spec = C.SPECS[test_id]
    seeds = np.random.SeedSequence([SEED, C.TEST_IDS.index(test_id)]).spawn(replicates)
    reps = []
    for k, seed in enumerate(seeds):
        reps.append(replicate(test_id, seed, samples=samples))
        if progress and (k + 1) % 20 == 0:
            progress(f"{test_id} : {k + 1}/{replicates}")
    target = C.ABOVE if spec.sign > 0 else C.BELOW
    false = sum(r["null"]["verdict"] == target for r in reps) / replicates
    power = sum(r["positive"]["verdict"] == target for r in reps) / replicates
    edge = [r["null"]["gross_ci"] for r in reps if r["null"]["gross_ci"] is not None]
    verdicts: dict[str, int] = {}
    for r in reps:
        verdicts[r["null"]["verdict"]] = verdicts.get(r["null"]["verdict"], 0) + 1
    gross = [r["null"]["gross_mean"] for r in reps if r["null"]["gross_mean"] is not None]
    return {"test_id": test_id, "kind": spec.kind, "sign": spec.sign, "replicates": replicates, "pairs": PAIRS[spec.kind],
            "target": target, "false_rate": round(false, 4), "max_false": round(MAX_FALSE, 4), "passes": false <= MAX_FALSE,
            "power": round(power, 4), "null_verdicts": verdicts,
            "gross_ci_excludes_zero_rate": round(sum((c[0] > 0) if spec.sign > 0 else (c[1] < 0) for c in edge) / replicates, 4),
            "gross_mean_avg": round(float(np.mean(gross)), 6) if gross else None,
            "events_mean": round(float(np.mean([r["events"] for r in reps])), 1),
            "events_min": int(min(r["events"] for r in reps)), "events_max": int(max(r["events"] for r in reps)),
            "days_mean": round(float(np.mean([r["days"] for r in reps])), 1),
            "insufficient_rate": round(verdicts.get(C.INSUFFICIENT, 0) / replicates, 4),
            "trou_share": round(float(np.mean([r["trou"] / max(1, r["windows"]) for r in reps])), 4)}


def run(*, out_path: Path | None = None, replicates: int = REPLICATES, samples: int = C.SAMPLES,
        tests: tuple[str, ...] = C.TEST_IDS, progress: Callable[[str], None] | None = None) -> dict:
    report = {"seed": SEED, "replicates": replicates, "samples": samples, "inject": INJECT, "max_false": MAX_FALSE,
              "lead_days": LEAD_DAYS, "test_days": TEST_DAYS, "tests": {t: run_test(t, replicates=replicates, samples=samples,
                                                                                    progress=progress) for t in tests}}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
