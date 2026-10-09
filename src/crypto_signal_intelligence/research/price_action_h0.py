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

Critères, par configuration (une configuration qui échoue est retirée de l'étude, 0 essai) :
- au moins 100 signaux synthétiques (sinon non jugeable : échec) ;
- |excès moyen| ≤ 0,05 R ;
- couverture de l'IC de l'excès (niveau de l'étude, 1 − 0,05/5, blocs de 7 jours) ≥ 0,90 : les paires sont réparties
  en G groupes (paire i → groupe i mod G), G = min(20, max(3, n // 100)) pour qu'un groupe ait environ 100 signaux ;
  couverture = part des G groupes dont l'intervalle contient 0 (un groupe sans intervalle calculable compte comme
  non couvert).
Ce contrôle MESURE le biais de la méthode de mesure ; il ne le corrige pas.
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
from ..forward.costs import CENTRAL
from ..price_action import detect as D
from . import price_action_study as S
from .experiments import code_state

PAIRS, GROUPS = 100, 20
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
MAX_ABS_EXCESS = 0.05
MIN_COVERAGE = 0.90
MIN_GROUPS = 3                                                # groupes de couverture : au moins 3 …
SIGNALS_PER_GROUP = 100                                       # … et environ 100 signaux par groupe, 20 au plus
MIN_SIGNALS = 100
SCENARIOS = (CENTRAL,)


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
    """Le pipeline exact de l'étude sur le marché synthétique ; lignes par configuration (avec le groupe de la paire)."""
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


def groups_for(n: int) -> int:
    """Nombre de groupes du critère de couverture : min(20, max(3, n // 100))."""
    return min(GROUPS, max(MIN_GROUPS, n // SIGNALS_PER_GROUP))


def group_of(symbol: str, groups: int = GROUPS) -> int:
    return int(symbol[1:3]) % groups


def criteria(rows: list[dict], *, samples: int = S.SAMPLES) -> dict:
    """Critères d'une configuration (module) et descriptifs."""
    n = len(rows)
    out: dict = {"n": n}
    if n:
        res = [x["results"][CENTRAL] for x in rows]
        excess = np.array([np.nan if x["excess"] is None else x["excess"] for x in res], float)
        ok = np.isfinite(excess)
        times = pd.to_datetime([x["at_ns"] for x in rows], utc=True).to_numpy()
        r = np.array([x["r"] for x in res], float)
        out |= {"r_mean": round(float(r.mean()), 4), "r_sd": round(float(r.std(ddof=1)), 4) if n > 1 else None,
                "excess": round(float(excess[ok].mean()), 4) if ok.any() else None,
                "excess_se": round(float(excess[ok].std(ddof=1) / np.sqrt(ok.sum())), 4) if ok.sum() > 1 else None,
                "excess_back": _mean([x["excess_back"] for x in res]), "excess_forward": _mean([x["excess_forward"] for x in res])}
        whole, _ = day_block_ci(excess[ok], times[ok], block_days=S.BLOCK_DAYS, samples=samples, seed=S.SEED,
                                level=S.EXCESS_LEVEL, min_blocks=S.MIN_BLOCKS) if ok.any() else (None, 0)
        out["excess_ci_all"] = whole
        groups = []
        count = groups_for(n)
        for g in range(count):
            mask = np.array([group_of(x["symbol"], count) == g for x in rows]) & ok
            ci, _ = (day_block_ci(excess[mask], times[mask], block_days=S.BLOCK_DAYS, samples=samples, seed=S.SEED + g,
                                  level=S.EXCESS_LEVEL, min_blocks=S.MIN_BLOCKS) if mask.any() else (None, 0))
            groups.append({"group": g, "n": int(mask.sum()), "ci": ci,
                           "covers_zero": None if ci is None else bool(ci[0] <= 0 <= ci[1])})
        defined = [x for x in groups if x["ci"] is not None]
        out["groups"] = groups
        out["groups_count"] = count
        out["groups_defined"] = len(defined)
        out["coverage"] = round(sum(1 for x in groups if x["covers_zero"]) / count, 4)
    reasons = []
    if n < MIN_SIGNALS:
        reasons.append(f"{n} signaux synthétiques (< {MIN_SIGNALS}) : non jugeable")
    if out.get("excess") is None or abs(out["excess"]) > MAX_ABS_EXCESS:
        reasons.append(f"|excès moyen| {out.get('excess')} > {MAX_ABS_EXCESS} R")
    if out.get("coverage") is None or out["coverage"] < MIN_COVERAGE:
        reasons.append(f"couverture {out.get('coverage')} < {MIN_COVERAGE} ({out.get('groups_defined', 0)} groupes "
                       f"calculables sur {out.get('groups_count', 0)})")
    return out | {"passes": not reasons, "reasons": reasons}


def _mean(values) -> float | None:
    xs = [v for v in values if v is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def run(*, now: datetime, out_dir: Path, workers: int = 2, pairs: int = PAIRS,
        progress: Callable[[str], None] | None = None) -> dict:
    """Le contrôle complet ; écrit `criteres.json` (à inscrire dans `price_action_review.CONTROLE_H0` avec son
    empreinte) et le rend."""
    collected = collect(pairs=pairs, workers=workers, progress=progress)
    configs = {c: criteria(collected["rows"][c]) for c in D.CONFIGS}
    report = {"study": S.STUDY, "control": "H0_MARCHES_ALEATOIRES", "created_at": pd.Timestamp(now).isoformat(),
              "commit": code_state(), "pairs": pairs, "seed": SEED, "start": str(START), "first_signal": str(FIRST_SIGNAL),
              "end": str(END), "sigma_range": SIGMA_RANGE, "btc_sigma": BTC_SIGMA, "garch": [GARCH_ALPHA, GARCH_BETA],
              "student_df": STUDENT_DF, "events": collected["events"], "counts": collected["counts"],
              "criteria": {"max_abs_excess": MAX_ABS_EXCESS, "min_coverage": MIN_COVERAGE, "min_groups": MIN_GROUPS,
                           "max_groups": GROUPS, "signals_per_group": SIGNALS_PER_GROUP, "min_signals": MIN_SIGNALS,
                           "level": S.EXCESS_LEVEL},
              "day_volume_sigma": DAY_VOLUME_SIGMA,
              "configs": configs, "all_passed": all(c["passes"] for c in configs.values()),
              "kept": [c for c in D.CONFIGS if configs[c]["passes"]]}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "criteres.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    report["path"] = str(path)
    report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report
