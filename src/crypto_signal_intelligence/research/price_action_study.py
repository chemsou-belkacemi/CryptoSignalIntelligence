"""Étude historique « price action » (docs/PRICE_ACTION.md § 4) : les cinq configurations du détecteur partagé
(`price_action/detect.py`) sur DEVELOPMENT (2019-01 → 2025-06-30), univers principal = top 40 À DATE, contre 20 placebos
de même géométrie, même gestion (`price_action/manage.py`), mêmes frais.

Exécution UNIQUE (`csi price-action executer --executer`) : code commité et identique, sur les chemins relus, au
commit inscrit par la relecture `leak-auditor` (`price_action_review.CODE_REVIEW`), configuration effective identique
(`CONFIG_FINGERPRINT`), contrôle sous H0 inscrit (`CONTROLE_H0`) ; une configuration qui échoue au contrôle H0 est
retirée (0 essai). Rien n'est lu après le 2025-06-30 : les bougies sont coupées à la fin de DEVELOPMENT et un signal
n'entre que si toute sa fenêtre (placebos et échéance) y tient.

Les fonctions par paire (`pair_rows`, `force_items`, `force_rows`) servent aussi au contrôle sous H0
(`research/price_action_h0.py`) : même code de bout en bout.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci, day_block_ci95
from ..config import Settings
from ..forward.costs import ADVERSE, CENTRAL
from ..price_action import detect as D
from ..price_action import manage as M
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode

STUDY = "PRICE_ACTION"
KIND = "PRICE_ACTION_HISTORIQUE"
FIRST_SIGNAL = pd.Timestamp("2019-01-01", tz="UTC")
SCENARIOS = (CENTRAL, ADVERSE)
ALPHA, TESTS = 0.05, 5
EXCESS_LEVEL = 1 - ALPHA / TESTS                 # IC de l'excès à 1 − 0,05/5 (5 configurations)
BLOCK_DAYS, SAMPLES, SEED, MIN_BLOCKS = 7, 10_000, 20261010, 8
MIN_SIGNALS = 100                                # INSUFFISANT sous 100 signaux
MIN_POSITIVE_YEARS = 4                           # garde-fou : au moins 4 années positives
REST_NS = M.REST_HOURS * D.HOUR_NS
MAX_WORKERS = 4
CONFIG_SECTIONS = ("data", "protocol")
PISTE, PERTE, RIEN, INSUFFISANT, RETIREE = "PISTE", "PERTE", "RIEN", "INSUFFISANT", "RETIREE_CONTROLE_H0"

from .price_action_review import CODE_REVIEW, CONFIG_FINGERPRINT, CONTROLE_H0  # noqa: E402

_SRC = "src/crypto_signal_intelligence"
REVIEW_FILE = f"{_SRC}/research/price_action_review.py"
REVIEWED_PATHS: tuple[str, ...] = (
    f"{_SRC}/price_action", f"{_SRC}/research/price_action_study.py", f"{_SRC}/research/price_action_h0.py",
    *(f"{_SRC}/research/{m}.py" for m in ("pit_universe", "long_history", "protocol", "experiments", "universe", "factors")),
    f"{_SRC}/forward/costs.py", f"{_SRC}/backtest/metrics.py", f"{_SRC}/patterns/indicators.py",
    f"{_SRC}/patterns/primitives.py", f"{_SRC}/features/loader.py", f"{_SRC}/data/store.py", f"{_SRC}/config.py",
    f"{_SRC}/cli.py", "config/default.toml",
)
INSCRIPTIONS = ("CODE_REVIEW", "CONFIG_FINGERPRINT", "CONTROLE_H0")


class NotReady(RuntimeError):
    """Relecture, configuration ou contrôle H0 non inscrits (ou différents) : exécution refusée, rien n'est compté."""


class AlreadyRun(RuntimeError):
    """L'exécution unique a déjà eu lieu : rien n'est relancé ni compté."""


# --- Signaux d'une paire ------------------------------------------------------------------------------------------------------

def horizon_ns(unit: str) -> int:
    """Fenêtre complète d'un signal après sa décision : placebo le plus tardif + durée maximale."""
    return M.placebo_reach_ns(unit) + M.max_hold_ns(unit)


def in_window(candidate: dict, first_ns: int, end_ns: int) -> bool:
    """Signal à partir de `first_ns`, et toute sa fenêtre (placebos, échéance) close au plus tard à `end_ns`."""
    return first_ns <= candidate["at_ns"] and candidate["at_ns"] + horizon_ns(candidate["unit"]) <= end_ns


def measure_row(hours: M.Hourly, candidate: dict, *, scenarios: Iterable[str], complete: bool = True) -> dict | None:
    """Signal et placebos dans chaque scénario (None si quelque chose reste EN_COURS, seulement en direct)."""
    ident = M.signal_id(candidate["config"], candidate["symbol"], candidate["at"])
    results = {}
    for scenario in scenarios:
        out = M.measure(hours, ident=ident, at=candidate["at_ns"], entry=candidate["entry"], stop=candidate["stop"],
                        objective=candidate["objective"], symbol=candidate["symbol"], scenario=scenario,
                        unit=candidate["unit"], complete=complete)
        if out is None:
            return None
        results[scenario] = out
    return {k: candidate[k] for k in ("config", "symbol", "at", "at_ns", "unit", "entry", "stop", "objective")} | {
        "ident": ident, "detail": candidate["detail"], "results": results}


def pair_rows(bars: D.Bars, symbol: str, *, eligible: Callable[[str, int], bool], first_ns: int, end_ns: int,
              scenarios: Iterable[str], configs: Iterable[str] = D.PAIR_CONFIGS) -> tuple[list[dict], dict]:
    """Signaux mesurés d'une paire (configurations paire par paire) : dans la fenêtre, paire éligible à l'instant du
    signal, discipline (une position active par paire et par configuration, 48 h de repos après la sortie centrale)."""
    scenarios = tuple(scenarios)
    candidates, refusals = D.scan_pair(bars, symbol, tuple(configs))
    hours = M.Hourly.of(bars)
    rows: list[dict] = []
    last_exit: dict[str, int] = {}
    counts: dict[str, dict[str, int]] = {}
    for item in refusals:
        if in_window(item | {"unit": D.UNIT[item["config"]]}, first_ns, end_ns) and eligible(symbol, item["at_ns"]):
            bucket = counts.setdefault(item["config"], {})
            bucket[item["reason"]] = bucket.get(item["reason"], 0) + 1
    for cand in candidates:
        if not in_window(cand, first_ns, end_ns) or not eligible(symbol, cand["at_ns"]):
            continue
        bucket = counts.setdefault(cand["config"], {})
        if cand["at_ns"] < last_exit.get(cand["config"], -(2 ** 62)) + REST_NS:
            bucket["DISCIPLINE"] = bucket.get("DISCIPLINE", 0) + 1
            continue
        row = measure_row(hours, cand, scenarios=scenarios)
        if row is None:
            continue
        bucket["SIGNAL"] = bucket.get("SIGNAL", 0) + 1
        last_exit[cand["config"]] = row["results"][CENTRAL]["exit_ns"]
        rows.append(row)
    return rows, counts


def force_items(bars: D.Bars, symbol: str, events: list[dict], *, eligible: Callable[[str, int], bool], first_ns: int,
                end_ns: int, scenarios: Iterable[str]) -> list[dict]:
    """Lecture d'une paire à chaque événement BTC (éligible à la stabilisation, fenêtre complète) ; pour une paire qui a
    tenu, le signal et ses placebos sont mesurés d'avance (la sélection des 3 se fait ensuite, sur toutes les paires)."""
    if symbol == D.FR_MARKET:
        return []
    scenarios = tuple(scenarios)
    hours = M.Hourly.of(bars)
    out = []
    for k, event in enumerate(events):
        at = event["stabilized"]
        if not (first_ns <= at and at + horizon_ns("4h") <= end_ns) or not eligible(symbol, at):
            continue
        reading = D.force_pair(bars, event)
        if reading is None:
            continue
        item = {"event": k, "symbol": symbol, "reading": reading}
        if reading["held"]:
            item["row"] = measure_row(hours, D.force_candidate(event, symbol, reading), scenarios=scenarios)
        out.append(item)
    return out


def force_rows(events: list[dict], items: list[dict]) -> tuple[list[dict], dict]:
    """Sélection des 3 paires par événement (plus faibles baisses), dans l'ordre des événements, parmi les paires qui
    ont tenu et ne sont ni en position ni en repos FORCE_RELATIVE (discipline appliquée avant la sélection)."""
    by_event: dict[int, dict[str, dict]] = {}
    for item in items:
        by_event.setdefault(item["event"], {})[item["symbol"]] = item
    rows: list[dict] = []
    last_exit: dict[str, int] = {}
    counts = {"EVENEMENTS": len(events), "EVENEMENTS_AVEC_LECTURE": len(by_event), "DISCIPLINE": 0, "SIGNAL": 0}
    for k, event in enumerate(events):
        found = by_event.get(k, {})
        readings = {}
        for symbol, item in found.items():
            if event["stabilized"] < last_exit.get(symbol, -(2 ** 62)) + REST_NS and item["reading"]["held"]:
                counts["DISCIPLINE"] += 1
                continue
            readings[symbol] = item["reading"]
        for cand in D.force_select(event, readings):
            row = dict(found[cand["symbol"]]["row"])
            row["detail"] = cand["detail"]
            last_exit[cand["symbol"]] = row["results"][CENTRAL]["exit_ns"]
            rows.append(row)
            counts["SIGNAL"] += 1
    return rows, counts


# --- Mesures et décision ---------------------------------------------------------------------------------------------------

def scenario_summary(rows: list[dict], scenario: str, *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """R net moyen (IC95 par blocs de 7 jours), excès sur les placebos (IC à 1 − 0,05/5), descriptifs."""
    if not rows:
        return {"n": 0, "days": 0, "r_ci95": None, "placebo_excess_ci": None}
    rows = sorted(rows, key=lambda x: x["at_ns"])
    res = [x["results"][scenario] for x in rows]
    r = np.array([x["r"] for x in res], float)
    times = pd.to_datetime([x["at_ns"] for x in rows], utc=True).to_numpy()
    excess = np.array([np.nan if x["excess"] is None else x["excess"] for x in res], float)
    ok = np.isfinite(excess)
    ci_r, blocks = day_block_ci95(r, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, min_blocks=MIN_BLOCKS)
    ci_x, _ = (day_block_ci(excess[ok], times[ok], block_days=BLOCK_DAYS, samples=samples, seed=seed, level=EXCESS_LEVEL,
                            min_blocks=MIN_BLOCKS) if ok.any() else (None, 0))
    sides = {}
    for side in ("back", "forward"):
        values = np.array([np.nan if x[f"excess_{side}"] is None else x[f"excess_{side}"] for x in res], float)
        fine = np.isfinite(values)
        sides[side] = round(float(values[fine].mean()), 4) if fine.any() else None
    outcomes: dict[str, int] = {}
    for x in res:
        outcomes[x["outcome"]] = outcomes.get(x["outcome"], 0) + 1
    return {"n": int(len(r)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()), "blocks": int(blocks),
            "r_mean": round(float(r.mean()), 4), "r_ci95": ci_r, "win_share": round(float((r > 0).mean()), 4),
            "tp1_rate": round(float(np.mean([x["hits"] >= 1 for x in res])), 4),
            "placebo_excess": round(float(excess[ok].mean()), 4) if ok.any() else None, "placebo_excess_ci": ci_x,
            "placebo_excess_back": sides["back"], "placebo_excess_forward": sides["forward"], "outcomes": outcomes}


def by_year(rows: list[dict]) -> dict[str, dict]:
    """Par année civile (central) : nombre, R moyen, excès moyen ; « positive » = R moyen > 0 ET excès moyen > 0."""
    out: dict[str, dict] = {}
    for year in sorted({pd.Timestamp(x["at_ns"], unit="ns", tz="UTC").year for x in rows}):
        mine = [x["results"][CENTRAL] for x in rows if pd.Timestamp(x["at_ns"], unit="ns", tz="UTC").year == year]
        r = np.array([x["r"] for x in mine], float)
        ex = np.array([x["excess"] for x in mine if x["excess"] is not None], float)
        out[str(year)] = {"n": len(mine), "r_mean": round(float(r.mean()), 4), "r_sum": round(float(r.sum()), 4),
                          "excess": round(float(ex.mean()), 4) if len(ex) else None,
                          "positive": bool(r.mean() > 0 and len(ex) and ex.mean() > 0)}
    return out


def _passes(summaries: dict) -> bool:
    return all(s.get("r_ci95") is not None and s.get("placebo_excess_ci") is not None and s["r_ci95"][0] > 0
               and s["placebo_excess_ci"][0] > 0 for s in summaries.values())


def decide(rows: list[dict], *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """Décision d'une configuration (docs/PRICE_ACTION.md § 4.5) : INSUFFISANT sous 100 signaux ou intervalle non
    calculable ; PISTE si l'IC de l'excès (1 − 0,05/5) ET l'IC95 du R sont > 0, central ET défavorable, et que les
    garde-fous tiennent (même règle sans la meilleure année ; au moins 4 années positives) ; PERTE si l'IC95 du R est
    < 0 dans les deux scénarios ; sinon RIEN."""
    summaries = {s: scenario_summary(rows, s, samples=samples, seed=seed) for s in SCENARIOS}
    years = by_year(rows) if rows else {}
    out = {"scenarios": summaries, "by_year": years, "guards": None}
    central = summaries[CENTRAL]
    if central["n"] < MIN_SIGNALS or any(s.get("r_ci95") is None or s.get("placebo_excess_ci") is None
                                         for s in summaries.values()):
        return out | {"decision": INSUFFISANT}
    if _passes(summaries):
        best = max(years, key=lambda y: years[y]["r_sum"])
        rest = [x for x in rows if str(pd.Timestamp(x["at_ns"], unit="ns", tz="UTC").year) != best]
        without = {s: scenario_summary(rest, s, samples=samples, seed=seed) for s in SCENARIOS}
        positive = sum(1 for y in years.values() if y["positive"])
        guards = {"best_year": best, "without_best_year": {s: {k: without[s].get(k) for k in ("n", "r_mean", "r_ci95",
                                                                                              "placebo_excess", "placebo_excess_ci")}
                                                           for s in SCENARIOS},
                  "holds_without_best_year": _passes(without), "positive_years": positive, "years": len(years)}
        guards["ok"] = bool(guards["holds_without_best_year"] and positive >= MIN_POSITIVE_YEARS)
        return out | {"guards": guards, "decision": PISTE if guards["ok"] else RIEN}
    if all(s["r_ci95"][1] < 0 for s in summaries.values()):
        return out | {"decision": PERTE}
    return out | {"decision": RIEN}


# --- Garde : code relu, configuration, contrôle H0 ------------------------------------------------------------------------

def config_fingerprint(settings: Settings) -> str:
    """Empreinte des valeurs EFFECTIVES (fichier et variables `CSI_*`) des sections lues par le calcul."""
    values = settings.model_dump(mode="json", include=set(CONFIG_SECTIONS))
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def review_file_is_inert(path: Path) -> bool:
    """Le fichier d'inscription ne contient que sa docstring, `from __future__ import annotations` et les affectations
    permises à une chaîne ou None."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" \
                and [a.name for a in node.names] == ["annotations"]:
            continue
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets if isinstance(node, ast.Assign) else [])
        value = getattr(node, "value", None)
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id in INSCRIPTIONS \
                and isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, str)):
            continue
        return False
    return True


def require_clean_and_reviewed(state: str, *, root: Path | None = None, review: str | None = None,
                               settings: Settings | None = None, fingerprint: str | None = None) -> None:
    """Code commité (jamais « +DIRTY ») et identique, sur les chemins relus, au commit `CODE_REVIEW` ; configuration
    effective identique à celle de la relecture. Sinon : exécution refusée."""
    if state.endswith("+DIRTY") or state == "NO_GIT_COMMIT":
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    commit = CODE_REVIEW if review is None else review
    if not commit:
        raise NotReady("relecture leak-auditor du code non inscrite (CODE_REVIEW) : exécution refusée")
    root = root or Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "diff", "--quiet", commit, "HEAD", "--", *REVIEWED_PATHS], cwd=root,
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotReady(f"comparaison au commit relu impossible : {exc}") from None
    if out.returncode == 1:
        raise NotReady(f"code modifié depuis le commit relu {commit[:12]} : nouvelle relecture exigée")
    if out.returncode != 0:
        raise NotReady(f"commit relu {commit[:12]} introuvable ou illisible : {out.stderr.strip()[:200]}")
    inscription = root / REVIEW_FILE
    if inscription.exists() and not review_file_is_inert(inscription):
        raise NotReady("fichier d'inscription de la relecture modifié au-delà des valeurs permises : exécution refusée")
    if settings is not None:
        expected = CONFIG_FINGERPRINT if fingerprint is None else fingerprint
        if not expected:
            raise NotReady("empreinte de la configuration relue non inscrite (CONFIG_FINGERPRINT) : exécution refusée")
        if config_fingerprint(settings) != expected:
            raise NotReady("configuration effective différente de celle de la relecture (fichier ou variables CSI_*) : "
                           "exécution refusée")


def control_of(inscription: str | None) -> dict:
    """Critères du contrôle sous H0 inscrits : fichier présent, empreinte identique ; rend le rapport (configurations
    retenues et retirées)."""
    if not inscription or "#" not in inscription:
        raise NotReady("contrôle sous H0 non inscrit (CONTROLE_H0) : exécution refusée")
    path_text, digest = inscription.rsplit("#", 1)
    path = Path(path_text.strip())
    if not path.exists():
        raise NotReady(f"fichier du contrôle sous H0 introuvable : {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest.strip():
        raise NotReady("fichier du contrôle sous H0 modifié depuis son inscription : exécution refusée")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("study") != STUDY or set(report.get("configs", {})) != set(D.CONFIGS):
        raise NotReady("fichier du contrôle sous H0 d'une autre étude ou incomplet")
    return report


# --- Exécution réelle (unique) ------------------------------------------------------------------------------------------------

def membership_index(members: pd.DataFrame, end: pd.Timestamp) -> dict[str, set[str]]:
    """Top 40 à date par mois (« AAAA-MM » → paires), mois ≤ fin de DEVELOPMENT."""
    members = members[members["month"] <= end]
    return {f"{m:%Y-%m}": set(g["symbol"]) for m, g in members.groupby("month")}


def member_at(by_month: dict[str, set[str]], symbol: str, at_ns: int) -> bool:
    """La paire appartient au top 40 du mois du jour d − 1 (la veille de la décision)."""
    day = pd.Timestamp(at_ns - D.DAY_NS, unit="ns", tz="UTC")
    return symbol in by_month.get(f"{day:%Y-%m}", set())


def development_end_exclusive(settings: Settings) -> pd.Timestamp:
    """Fin de DEVELOPMENT en borne exclusive (2025-07-01 00:00 UTC) : une bougie n'est lue que si elle y est close."""
    from .protocol import development_end
    return pd.Timestamp(development_end(settings)).floor("s") + pd.Timedelta(seconds=1)


def load_development(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h du magasin long, coupées : seules les bougies CLÔTURÉES au plus tard à la fin de DEVELOPMENT."""
    from .long_history import load_long
    frame = load_long(settings, symbol)
    frame = frame.assign(open_time=pd.to_datetime(frame["open_time"], utc=True))
    return frame[frame["open_time"] + pd.Timedelta(hours=1) <= end][D.COLUMNS].reset_index(drop=True)


def _worker(args: tuple) -> dict:
    """Une paire : chargement coupé à la fin de DEVELOPMENT, signaux paire par paire et lectures FORCE_RELATIVE."""
    from ..features.loader import MissingData
    from .derivatives_screen import fingerprint
    settings, symbol, events, by_month, first_ns, end, configs, survivors = args
    end_ns = int(D.to_ns([end])[0])
    if survivors:
        def eligible(s: str, at_ns: int) -> bool:
            return True
    else:
        def eligible(s: str, at_ns: int) -> bool:
            return member_at(by_month, s, at_ns)
    try:
        frame = load_development(settings, symbol, end)
    except MissingData:
        return {"symbol": symbol, "missing": True}
    if frame.empty:
        return {"symbol": symbol, "missing": True}
    bars = D.Bars(frame)
    pair_configs = tuple(c for c in configs if c != D.FORCE_RELATIVE)
    rows, counts = pair_rows(bars, symbol, eligible=eligible, first_ns=first_ns, end_ns=end_ns, scenarios=SCENARIOS,
                             configs=pair_configs)
    items = (force_items(bars, symbol, events, eligible=eligible, first_ns=first_ns, end_ns=end_ns, scenarios=SCENARIOS)
             if D.FORCE_RELATIVE in configs else [])
    return {"symbol": symbol, "rows": rows, "counts": counts, "items": items, "hash": fingerprint(frame),
            "last_bar": str(frame["open_time"].iloc[-1])}


def collect(settings: Settings, symbols: list[str], *, events: list[dict], by_month: dict, end: pd.Timestamp,
            configs: tuple[str, ...], survivors: bool, workers: int, progress: Callable[[str], None]) -> dict:
    """Toutes les paires (au plus 4 processus), puis la sélection FORCE_RELATIVE ; lignes par configuration."""
    first_ns = int(D.to_ns([FIRST_SIGNAL])[0])
    jobs = [(settings, s, events, by_month, first_ns, end, configs, survivors) for s in symbols]
    results = []
    workers = max(1, min(int(workers), MAX_WORKERS))
    if workers == 1:
        for i, job in enumerate(jobs):
            results.append(_worker(job))
            progress(f"{i + 1}/{len(jobs)} {job[1]}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, result in enumerate(pool.map(_worker, jobs)):
                results.append(result)
                progress(f"{i + 1}/{len(jobs)} {result['symbol']}")
    rows: dict[str, list[dict]] = {c: [] for c in configs}
    counts: dict[str, dict] = {c: {} for c in configs}
    items, hashes, missing = [], {}, []
    for result in results:
        if result.get("missing"):
            missing.append(result["symbol"])
            continue
        hashes[result["symbol"]] = result["hash"]
        for row in result["rows"]:
            rows[row["config"]].append(row)
        for config, bucket in result["counts"].items():
            for key, value in bucket.items():
                counts[config][key] = counts[config].get(key, 0) + value
        items += result["items"]
    if D.FORCE_RELATIVE in configs:
        rows[D.FORCE_RELATIVE], counts[D.FORCE_RELATIVE] = force_rows(events, items)
    return {"rows": rows, "counts": counts, "hashes": hashes, "missing": missing}


def _compact(rows: list[dict]) -> list[dict]:
    """Lignes écrites au rapport (sans les listes de placebos détaillées)."""
    out = []
    for row in rows:
        out.append({k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in row.items() if k != "results"}
                   | {"results": {s: {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in r.items()}
                                  for s, r in row["results"].items()}})
    return out


def run(settings: Settings, *, now: datetime, workers: int = 2, progress: Callable[[str], None] | None = None) -> dict:
    """Exécution unique de l'étude historique (gardée). Rend le résumé ; écrit `reports/<run_id>/`."""
    from .pit_universe import load_membership
    from .universe import RESEARCH_UNIVERSE
    say = progress or (lambda _text: None)
    state = code_state()
    require_clean_and_reviewed(state, settings=settings)
    control = control_of(CONTROLE_H0)
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.count_runs(STUDY) > 0:
        raise AlreadyRun("l'étude « price action » a déjà été exécutée : exécution unique, rien n'est relancé")
    kept = tuple(c for c in D.CONFIGS if control["configs"][c]["passes"])
    removed = [c for c in D.CONFIGS if c not in kept]
    end = development_end_exclusive(settings)
    members = load_membership(settings)
    by_month = membership_index(members, end)
    symbols = sorted({s for month, group in by_month.items() if month >= "2018-12" for s in group})
    say("événements BTC")
    btc = D.Bars(load_development(settings, D.FR_MARKET, end))
    events = D.force_events(btc)
    say(f"univers à date : {len(symbols)} paires")
    main = collect(settings, symbols, events=events, by_month=by_month, end=end, configs=kept, survivors=False,
                   workers=workers, progress=say)
    say("contrôle descriptif : 40 paires de recherche (survivantes)")
    surv = collect(settings, list(RESEARCH_UNIVERSE), events=events, by_month={}, end=end, configs=kept, survivors=True,
                   workers=workers, progress=say)
    decisions = {c: decide(main["rows"][c]) for c in kept}
    survivors = {c: {s: scenario_summary(surv["rows"][c], s) for s in SCENARIOS} for c in kept}
    run_id = new_run_id("PRICEACTION")
    program = registry.program_trials() + len(kept)
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {"run_id": run_id, "study": STUDY, "doc": "docs/PRICE_ACTION.md", "n_trials": len(kept),
               "program_trials": program, "configs_kept": list(kept), "configs_removed_h0": removed,
               "decisions": {c: decisions[c]["decision"] for c in kept} | dict.fromkeys(removed, RETIREE),
               "details": decisions, "counts": main["counts"], "survivors_descriptive": survivors,
               "survivors_counts": surv["counts"], "missing_hourly": main["missing"], "events": len(events),
               "symbols": symbols, "code_review": CODE_REVIEW, "controle_h0": CONTROLE_H0, "commit": state}
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (report_dir / "signals.json").write_text(json.dumps({c: _compact(main["rows"][c]) for c in kept}, ensure_ascii=False,
                                                        default=str), encoding="utf-8")
    registry.record(run_id=run_id, created_at=pd.Timestamp(now).isoformat(), kind=KIND,
                    hypothesis="cinq configurations de price action (docs/PRICE_ACTION.md), top 40 à date, contre placebos",
                    strategy=STUDY, strategy_version=1, variant="exécution unique, configurations retenues par le contrôle H0",
                    params=study_params(), period_label="DEVELOPMENT", period_start=str(FIRST_SIGNAL),
                    period_end=end.isoformat(), universe=symbols, data_hashes=main["hashes"], git_commit=state,
                    dependencies=dependency_versions(), seed=SEED, cost_scenario="central+defavorable",
                    simulation_rules={"doc": "docs/PRICE_ACTION.md", "management": "price_action/manage.py"},
                    metrics={"n_trials": len(kept), "program_trials": program, "decisions": summary["decisions"]},
                    status="COMPLETED", report_dir=str(report_dir))
    return summary


def study_params() -> dict:
    """Paramètres figés de l'étude (inscrits au registre)."""
    return {"detect": D.params(),
            "manage": {"hard_stop_factor": M.HARD_STOP_FACTOR, "tp1_share": M.TP1_SHARE, "max_hold_days": M.MAX_HOLD_DAYS,
                       "rest_hours": M.REST_HOURS, "placebos": M.PLACEBOS, "placebo_hours": M.PLACEBO_HOURS,
                       "placebo_days": M.PLACEBO_DAYS, "seed_prefix": M.SEED_PREFIX},
            "study": {"first_signal": str(FIRST_SIGNAL), "excess_level": EXCESS_LEVEL, "block_days": BLOCK_DAYS,
                      "samples": SAMPLES, "seed": SEED, "min_blocks": MIN_BLOCKS, "min_signals": MIN_SIGNALS,
                      "min_positive_years": MIN_POSITIVE_YEARS}}
