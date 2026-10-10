"""Contrôle de l'étude « price action » SOUS L'HYPOTHÈSE NULLE (docs/PRICE_ACTION.md § 5), avant toute donnée réelle.

Marché synthétique sans information : 100 paires × 6 ans de signaux (bougies 1 h du 2018-07-01 au 2024-12-31, les six
premiers mois servent à amorcer les indicateurs) et un BTC synthétique. Rendements SIMPLES martingales :
r_t = σ_t z_t, E[r_t | passé] = 0, avec une volatilité qui varie (GARCH(1,1), α = 0,05, β = 0,94) pour que les
compressions et les cassures existent comme sur un vrai marché ; z gaussien pour les paires 0 à 49, Student à 4
degrés (variance 1) pour les paires 50 à 99 et gaussien pour BTC ; volume quote lognormal multiplié par (0,5 + |z|)
(lié à l'ampleur du mouvement, jamais à son sens) et par un facteur journalier lognormal (σ = 0,5), sans quoi un
volume journalier ne dépasserait jamais 1,5 × sa moyenne (ajout décidé sur les seuls COMPTAGES de candidats
synthétiques, aucun R ni excès regardé : docs/PRICE_ACTION.md § 5.4). Rien n'y est prévisible : l'espérance du R net est celle des frais
et l'excès sur les placebos doit rester proche de 0. Le contrôle passe le détecteur, la discipline, la gestion et les
placebos EXACTS de l'étude (`price_action_study.pair_rows`, `force_items`, `force_rows`), frais du scénario central.

Contrôle n° 1 (commit fa7b872) : critère sur l'excès, ÉCHEC des cinq configurations (biais des placebos arrière,
docs/PRICE_ACTION.md § 5.5). Contrôle n° 2 (§ 11.4, décision au R NET seul), ce module :
- 200 sous-échantillons de 40 paires (graine 20261011) ; dans chacun, la décision de l'étude (§ 11.1, garde-fous
  compris) ; faux PISTE ≤ 0,02, sinon ÉCHEC (0 essai) ; en descriptif, intervalle 99 % du R BRUT > 0 ;
- contrôle positif : dérive en rendement simple ajoutée après chaque signal, calibrée pour +0,15 R net central en
  moyenne ; puissance (part des mêmes sous-échantillons décidés PISTE) ≥ 0,50, sinon INSTRUMENT_TROP_FAIBLE (0 essai
  historique, F19 la mesure quand même).
Ce contrôle MESURE la méthode de décision ; il ne la corrige pas.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..forward.costs import ADVERSE, CENTRAL, costs_for
from ..price_action import detect as D
from ..price_action import manage as M
from . import price_action_study as S
from .experiments import code_state

PAIRS = 100
START = pd.Timestamp("2018-07-01", tz="UTC")
FIRST_SIGNAL = pd.Timestamp("2019-01-01", tz="UTC")
END = pd.Timestamp("2025-01-01", tz="UTC")                   # borne exclusive : 6 ans de signaux (2019-2024)
SEED = 20261010
SIGMA_RANGE = (0.004, 0.012)                                  # volatilité horaire moyenne des paires (log-uniforme)
BTC_SIGMA = 0.006
GARCH_ALPHA, GARCH_BETA = 0.05, 0.94
STUDENT_DF = 4
VOLUME_MU, VOLUME_SIGMA = 10.0, 0.4
DAY_VOLUME_SIGMA = 0.5                                        # facteur de volume par jour (lognormal), sans lien au sens
CONTROL = S.CONTROL_KIND
REPLICATES, SUBSET, SUBSET_SEED = 200, 40, 20261011          # sous-échantillons de 40 paires parmi 100
MAX_FALSE_PISTE = 0.05 / 5 * 2                                # faux PISTE ≤ 0,02
MIN_POWER = 0.50                                              # puissance du contrôle positif ≥ 0,50
TARGET_NET_R = 0.15                                           # contrôle positif : +0,15 R net central en moyenne
VALID, FAILED, WEAK = "VALIDE", "ECHEC_FAUX_PISTE", "INSTRUMENT_TROP_FAIBLE"
SCENARIOS = (CENTRAL, ADVERSE)


def _seeds() -> list[np.random.SeedSequence]:
    return np.random.SeedSequence(SEED).spawn(PAIRS + 1)


def market(index: int, *, sigma: float | None = None, student: bool | None = None, start: pd.Timestamp = START,
           end: pd.Timestamp = END) -> pd.DataFrame:
    """Bougies 1 h de la paire synthétique `index` (0…99) ; `index = PAIRS` : le BTC synthétique. Déterministe."""
    rng = np.random.default_rng(_seeds()[index])
    if sigma is None:
        sigma = BTC_SIGMA if index == PAIRS else float(np.exp(rng.uniform(*np.log(SIGMA_RANGE))))
    if student is None:
        student = PAIRS // 2 <= index < PAIRS
    n = int((end - start) / pd.Timedelta(hours=1))
    if student:
        z = rng.standard_t(STUDENT_DF, n) / np.sqrt(STUDENT_DF / (STUDENT_DF - 2))
    else:
        z = rng.standard_normal(n)
    omega = sigma ** 2 * (1 - GARCH_ALPHA - GARCH_BETA)
    variances, returns = [], []
    h = sigma ** 2
    for shock in z.tolist():
        variances.append(h)
        step = math.sqrt(h) * shock
        returns.append(step)
        h = omega + GARCH_ALPHA * step * step + GARCH_BETA * h
    var = np.array(variances)
    r = np.maximum(np.array(returns), -0.5)                      # garde (jamais atteinte en pratique)
    close = 100.0 * np.cumprod(1 + r)
    open_ = np.r_[100.0, close[:-1]]
    vol = np.sqrt(var)
    up = np.abs(rng.normal(0.0, 0.5, n)) * vol
    down = np.abs(rng.normal(0.0, 0.5, n)) * vol
    days = np.arange(n) // 24
    day_factor = np.exp(rng.normal(0.0, DAY_VOLUME_SIGMA, int(days[-1]) + 1))[days]
    times = pd.date_range(start, periods=n, freq="h", tz="UTC").as_unit("ns")
    return pd.DataFrame({"open_time": times, "open": open_, "high": np.maximum(open_, close) * (1 + up),
                         "low": np.minimum(open_, close) * (1 - np.minimum(down, 0.9)), "close": close,
                         "quote_volume": rng.lognormal(VOLUME_MU, VOLUME_SIGMA, n) * (0.5 + np.abs(z)) * day_factor})


def symbol_of(index: int) -> str:
    return D.FR_MARKET if index == PAIRS else f"H{index:02d}USDT"


def always(symbol: str, at_ns: int) -> bool:
    return True


def _worker(args: tuple) -> dict:
    index, events, configs, first_ns, end_ns, pairs_override = args
    frame = market(index) if pairs_override is None else pairs_override
    bars = D.Bars(frame)
    symbol = symbol_of(index)
    pair_configs = tuple(c for c in configs if c != D.FORCE_RELATIVE)
    rows, counts = S.pair_rows(bars, symbol, eligible=always, first_ns=first_ns, end_ns=end_ns, scenarios=SCENARIOS,
                               configs=pair_configs)
    items = (S.force_items(bars, symbol, events, eligible=always, first_ns=first_ns, end_ns=end_ns, scenarios=SCENARIOS)
             if D.FORCE_RELATIVE in configs else [])
    return {"index": index, "rows": rows, "counts": counts, "items": items}


def collect(*, pairs: int = PAIRS, configs: tuple[str, ...] = D.CONFIGS, workers: int = 2,
            progress: Callable[[str], None] | None = None) -> dict:
    """Le pipeline exact de l'étude sur le marché synthétique ; lignes par configuration (deux scénarios de coûts)."""
    say = progress or (lambda _text: None)
    first_ns, end_ns = int(D.to_ns([FIRST_SIGNAL])[0]), int(D.to_ns([END])[0])
    events = D.force_events(D.Bars(market(PAIRS))) if D.FORCE_RELATIVE in configs else []
    jobs = [(i, events, configs, first_ns, end_ns, None) for i in range(pairs)]
    results = []
    workers = max(1, min(int(workers), S.MAX_WORKERS))
    if workers == 1:
        for job in jobs:
            results.append(_worker(job))
            say(f"paire synthétique {job[0] + 1}/{pairs}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(_worker, jobs):
                results.append(result)
                say(f"paire synthétique {result['index'] + 1}/{pairs}")
    rows: dict[str, list[dict]] = {c: [] for c in configs}
    counts: dict[str, dict] = {c: {} for c in configs}
    items = []
    for result in results:
        for row in result["rows"]:
            rows[row["config"]].append(row)
        for config, bucket in result["counts"].items():
            for key, value in bucket.items():
                counts[config][key] = counts[config].get(key, 0) + value
        items += result["items"]
    if D.FORCE_RELATIVE in configs:
        rows[D.FORCE_RELATIVE], counts[D.FORCE_RELATIVE] = S.force_rows(events, items)
    return {"rows": rows, "counts": counts, "events": len(events)}


def subsets(pairs: int = PAIRS) -> list[set[str]]:
    """200 sous-échantillons de 40 paires tirées sans remise (graine 20261011)."""
    rng = np.random.default_rng(SUBSET_SEED)
    size = min(SUBSET, pairs)
    return [{symbol_of(int(i)) for i in rng.choice(pairs, size, replace=False)} for _ in range(REPLICATES)]


def gross_r(row: dict, scenario: str) -> float:
    """R BRUT (frais nuls) d'un signal, retrouvé exactement depuis son R net : toutes les ventes portent le même
    facteur de frais, l'achat aussi."""
    c = costs_for(row["symbol"], scenario)
    sell, buy = (1 - c.market) * (1 - c.fee), (1 + c.market) * (1 + c.fee)
    risk = row["entry"] - row["stop"]
    proceeds = (row["results"][scenario]["r"] * risk + row["entry"] * buy) / sell
    return (proceeds - row["entry"]) / risk


def drifted(hours: M.Hourly, row: dict, m: float, scenario: str) -> float:
    """R net du signal quand une dérive en rendement simple est ajoutée après la décision : la k-ième bougie 1 h qui
    suit est multipliée par (1 + δ)^k, δ = m × (entrée − stop) / entrée, sur toute la durée de détention."""
    start = row["at_ns"]
    lo, hi = np.searchsorted(hours.t, [start, start + M.max_hold_ns(row["unit"])])
    delta = m * (row["entry"] - row["stop"]) / row["entry"]
    factor = (1 + delta) ** np.arange(1, hi - lo + 1)
    window = M.Hourly(hours.t[lo:hi], hours.o[lo:hi] * factor, hours.h[lo:hi] * factor, hours.l[lo:hi] * factor,
                      hours.c[lo:hi] * factor)
    out = M.simulate(window, entry_at=start, entry=row["entry"], stop=row["stop"], objective=row["objective"],
                     symbol=row["symbol"], scenario=scenario, unit=row["unit"], complete=True)
    return float(out["r"])


def calibrate(rows: list[dict], hours_of: dict[str, M.Hourly], *, target: float = TARGET_NET_R) -> float:
    """Plus petit m (R par heure, à 1e-7 près, par dichotomie) tel que le R net moyen CENTRAL de tous les signaux avec
    dérive atteigne `target` (la moyenne avance par sauts : des sorties changent ; elle est donc ≥ `target`, au plus
    près)."""
    def mean(m: float) -> float:
        return float(np.mean([drifted(hours_of[r["symbol"]], r, m, CENTRAL) for r in rows]))
    low, high = 0.0, 0.002
    while mean(high) < target:
        low, high = high, high * 2
        if high > 1.0:
            raise RuntimeError("dérive introuvable pour le contrôle positif")
    for _ in range(40):
        middle = (low + high) / 2
        if mean(middle) < target:
            low = middle
        else:
            high = middle
        if high - low < 1e-7:
            break
    return high


def with_r(rows: list[dict], values: dict[str, list[float]]) -> list[dict]:
    """Copies des lignes dont le R net de chaque scénario est remplacé (placebos inchangés, descriptifs)."""
    out = []
    for k, row in enumerate(rows):
        results = {s: row["results"][s] | {"r": values[s][k]} for s in SCENARIOS}
        out.append(row | {"results": results})
    return out


def replicate_rates(rows: list[dict], samples: list[set[str]], *, decision_samples: int = S.SAMPLES) -> dict:
    """Décision du § 11.1 dans chaque sous-échantillon : part des PISTE, des PERTE, des INSUFFISANT ; et part des
    sous-échantillons dont l'intervalle 99 % du R brut central est > 0 (descriptif)."""
    counts: dict[str, int] = {}
    gross_positive = 0
    for k, sample in enumerate(samples):
        mine = [r for r in rows if r["symbol"] in sample]
        decision = S.decide(mine, samples=decision_samples, seed=S.SEED + k)["decision"]
        counts[decision] = counts.get(decision, 0) + 1
        if mine:
            mine = sorted(mine, key=lambda r: r["at_ns"])
            ci, _ = day_block_ci(np.array([gross_r(r, CENTRAL) for r in mine]),
                                 pd.to_datetime([r["at_ns"] for r in mine], utc=True).to_numpy(),
                                 block_days=S.BLOCK_DAYS, samples=decision_samples, seed=S.SEED + k,
                                 level=S.DECISION_LEVEL, min_blocks=S.MIN_BLOCKS)
            gross_positive += int(ci is not None and ci[0] > 0)
    total = len(samples)
    return {"replicates": total, "decisions": counts, "piste_rate": round(counts.get(S.PISTE, 0) / total, 4),
            "gross_ci_positive_rate": round(gross_positive / total, 4)}


def _mean(values) -> float | None:
    xs = [v for v in values if v is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def describe(rows: list[dict]) -> dict:
    """Descriptifs sous H0 : R net moyen et écart-type (deux scénarios), R brut moyen, excès global, arrière, avant."""
    if not rows:
        return {"n": 0}
    central = [r["results"][CENTRAL] for r in rows]
    r = np.array([x["r"] for x in central], float)
    return {"n": len(rows), "r_mean": round(float(r.mean()), 4), "r_sd": round(float(r.std(ddof=1)), 4) if len(r) > 1 else None,
            "r_mean_adverse": round(float(np.mean([x["results"][ADVERSE]["r"] for x in rows])), 4),
            "gross_r_mean": round(float(np.mean([gross_r(x, CENTRAL) for x in rows])), 4),
            "excess": _mean(x["excess"] for x in central), "excess_back": _mean(x["excess_back"] for x in central),
            "excess_forward": _mean(x["excess_forward"] for x in central)}


def criteria(rows: list[dict], hours_of: dict[str, M.Hourly], samples: list[set[str]], *,
             decision_samples: int = S.SAMPLES) -> dict:
    """Contrôle n° 2 d'une configuration : faux PISTE sous H0, puis contrôle positif (+0,15 R net central)."""
    out: dict = describe(rows)
    null = replicate_rates(rows, samples, decision_samples=decision_samples)
    out["null"] = null
    out["false_piste_rate"] = null["piste_rate"]
    reasons = []
    if null["piste_rate"] > MAX_FALSE_PISTE:
        reasons.append(f"faux PISTE {null['piste_rate']} > {MAX_FALSE_PISTE:.2f}")
    if rows:
        m = calibrate(rows, hours_of)
        values = {s: [drifted(hours_of[r["symbol"]], r, m, s) for r in rows] for s in SCENARIOS}
        positive = replicate_rates(with_r(rows, values), samples, decision_samples=decision_samples)
        out["positive"] = positive | {"drift_r_per_hour": m, "r_mean": round(float(np.mean(values[CENTRAL])), 4),
                                      "r_mean_adverse": round(float(np.mean(values[ADVERSE])), 4)}
        out["power"] = positive["piste_rate"]
    else:
        out["power"] = 0.0
    weak = out["power"] < MIN_POWER
    if weak:
        reasons.append(f"puissance {out['power']} < {MIN_POWER}")
    status = FAILED if null["piste_rate"] > MAX_FALSE_PISTE else (WEAK if weak else VALID)
    return out | {"status": status, "passes": status == VALID, "reasons": reasons}


def hours_for(pairs: int) -> dict[str, M.Hourly]:
    """Bougies 1 h de chaque paire synthétique (régénérées : déterministes)."""
    return {symbol_of(i): M.Hourly.of(D.Bars(market(i))) for i in range(pairs)}


def run(*, now: datetime, out_dir: Path, workers: int = 2, pairs: int = PAIRS,
        progress: Callable[[str], None] | None = None, decision_samples: int = S.SAMPLES) -> dict:
    """Le contrôle n° 2 complet ; écrit `criteres.json` (à inscrire dans `price_action_review.CONTROLE_H0` avec son
    empreinte) et le rend."""
    say = progress or (lambda _text: None)
    collected = collect(pairs=pairs, workers=workers, progress=progress)
    say("bougies des paires synthétiques")
    hours_of = hours_for(pairs)
    samples = subsets(pairs)
    configs = {}
    for config in D.CONFIGS:
        say(f"répliques et contrôle positif : {config}")
        configs[config] = criteria(collected["rows"][config], hours_of, samples, decision_samples=decision_samples)
    report = {"study": S.STUDY, "control": CONTROL, "created_at": pd.Timestamp(now).isoformat(), "commit": code_state(),
              "pairs": pairs, "seed": SEED, "start": str(START), "first_signal": str(FIRST_SIGNAL), "end": str(END),
              "sigma_range": SIGMA_RANGE, "btc_sigma": BTC_SIGMA, "garch": [GARCH_ALPHA, GARCH_BETA],
              "student_df": STUDENT_DF, "day_volume_sigma": DAY_VOLUME_SIGMA, "events": collected["events"],
              "counts": collected["counts"],
              "criteria": {"replicates": REPLICATES, "subset_pairs": SUBSET, "subset_seed": SUBSET_SEED,
                           "max_false_piste": MAX_FALSE_PISTE, "min_power": MIN_POWER, "target_net_r": TARGET_NET_R,
                           "decision_level": S.DECISION_LEVEL, "decision_samples": decision_samples,
                           "min_signals": S.MIN_SIGNALS},
              "configs": configs, "kept": [c for c in D.CONFIGS if configs[c]["passes"]],
              "weak": [c for c in D.CONFIGS if configs[c]["status"] == WEAK],
              "failed": [c for c in D.CONFIGS if configs[c]["status"] == FAILED]}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "criteres.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    report["path"] = str(path)
    report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report
