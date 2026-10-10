"""Données réelles du zoo : comptage à blanc (aucun rendement) et exécution unique gardée (docs/ZOO.md § 7, § 9).

- `comptage` : sur la découverte réelle, par test, nombres de signaux, de transactions, exposition et blocs (AUCUN
  prix au-delà des heures et de l'appartenance n'entre dans ces nombres), plus les contrôles publiés avant toute
  lecture de rendement : dates CPI et NFP (CA10, CA11), correspondance des annonces de cotation (EV02) ;
- `executer_decouverte` / `executer_validation` : une exécution par étape, refusée sans relecture, configuration,
  contrôle H0 et comptage inscrits (`review.py`) ; registre des essais comme le § 9.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ...config import Settings
from ..experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .data import (
    DECOUVERTE,
    HARD_END,
    VALIDATION,
    Etape,
    Paire,
    borne_lecture,
    charger_evenements,
    charger_paire,
    charger_quotidien_recensement,
    charger_serie,
    charger_univers,
    eligibles_f31,
    mois_de,
    recensement,
)
from .decide import CANDIDATE, decouverte, valider
from .h0 import STUDY
from .measure import MesureA, MesureB, descriptif_a, descriptif_b, placebos_a
from .run import Source, compter, mesurer_paires, mesurer_portefeuilles, references
from .spec import FORME_A, FORME_B, PAR_ID, TESTS, Test

KIND = "ZOO"
STRATEGIES = {"decouverte": "ZOO_DECOUVERTE", "validation": "ZOO_VALIDATION"}
NOTE = "comptes seulement : aucun rendement, aucun R, aucun résultat de transaction"


class AlreadyRun(RuntimeError):
    """Étape déjà exécutée : exécution unique."""


class SourceMagasin(Source):
    """Magasin long (bougies 1 h, paires retirées comprises), coupé à la borne de l'étape dès la lecture."""
    nom = "magasin"

    def __init__(self, settings: Settings, etape: Etape, tests: list[Test]):
        self.settings = settings
        self.fin = borne_lecture(etape.fin_exclusive, settings)
        self.univers = charger_univers(settings, self.fin)
        months = range(int(mois_de(np.array([etape.debut_ns]))[0]), int(mois_de(np.array([etape.fin_ns]))[0]) + 1)
        self.symboles = sorted(set().union(*(self.univers.membres_du_mois(m) for m in months)))
        self.externes, self.evenements = externes_pour(settings, tests, self.fin)
        self.manquantes: list[str] = []

    def paire(self, symbole: str) -> Paire | None:
        pair = charger_paire(self.settings, symbole, self.fin)
        if pair is None and symbole not in self.manquantes:
            self.manquantes.append(symbole)
        return pair


def externes_pour(settings: Settings, tests: list[Test], fin: pd.Timestamp) -> tuple[dict, dict]:
    families = {t.famille for t in tests}
    series, events = {}, {}
    if "AM" in families:
        for key in ("SP500", "NASDAQ100", "GLD", "DGS10", "VIXCLS"):
            series[f"zoo_marches:{key}"] = charger_serie(settings, "zoo_marches", key, fin)
        series["zoo_dollar_bce:DXY_BCE"] = charger_serie(settings, "zoo_dollar_bce", "DXY_BCE", fin)
    if "SE" in families:
        series["zoo_fear_greed:fear_greed"] = charger_serie(settings, "zoo_fear_greed", "fear_greed", fin)
    if "CA" in families:
        events["calendrier_macro"] = charger_evenements(settings, "calendrier_macro", fin)
        events["calendrier_crypto"] = charger_evenements(settings, "calendrier_crypto", fin)
    if "EV" in families:
        events["annonces_binance"] = charger_evenements(settings, "annonces_binance", fin)
    return series, events


# --- Contrôles publiés avec le comptage ---------------------------------------------------------------------------

def controle_dates_macro(events: pd.DataFrame, debut: pd.Timestamp, fin: pd.Timestamp) -> dict:
    """CA10, CA11 : jour de la semaine (New York) et écart entre publications ; exceptions listées (publication un
    autre jour que d'habitude, mois manquant ou doublé, écart hors de 20 à 40 jours)."""
    out = {}
    usual = {"NFP": {4}, "CPI": {1, 2, 3}}
    for name in ("CPI", "NFP"):
        part = events[events["event"] == name].copy()
        times = pd.to_datetime(part["event_time"], utc=True)
        part = part[(times >= debut) & (times < fin)]
        local = pd.to_datetime(part["event_time"], utc=True).dt.tz_convert("America/New_York").sort_values()
        local = local.reset_index(drop=True)
        months = local.dt.tz_localize(None).dt.to_period("M")
        counts = months.value_counts()
        all_months = pd.period_range(debut.tz_localize(None).to_period("M"), (fin - pd.Timedelta(seconds=1))
                                     .tz_localize(None).to_period("M"), freq="M")
        gaps = local.diff().dt.days
        out[name] = {
            "publications": int(len(local)),
            "jours_semaine": {str(k): int(v) for k, v in local.dt.day_name().value_counts().items()},
            "autre_jour_que_d_habitude": [str(d.date()) for d in local if d.weekday() not in usual[name]],
            "mois_manquants": [str(m) for m in all_months if counts.get(m, 0) == 0],
            "mois_doubles": [str(m) for m, c in counts.items() if c > 1],
            "ecarts_hors_20_40_jours": [f"{local.iloc[i - 1].date()} → {local.iloc[i].date()} ({int(gaps.iloc[i])} j)"
                                        for i in range(1, len(local)) if not 20 <= gaps.iloc[i] <= 40],
            "heures_locales": {str(k): int(v) for k, v in local.dt.strftime("%H:%M").value_counts().items()},
        }
    return out


_TOKEN = re.compile(r"\b[A-Z0-9]{2,12}\b")


def controle_annonces(census: pd.DataFrame, daily: pd.DataFrame, annonces: pd.DataFrame, debut: pd.Timestamp,
                      fin: pd.Timestamp) -> dict:
    """EV02 : (i) nouvelles paires USDT du recensement (première bougie dans [début ; fin[) sans annonce
    `COTATION_SPOT` ; (ii) annonces `COTATION_SPOT` sans paire USDT correspondante. Taux > 5 % déclarés."""
    bases_all = set(census["base"].astype(str))
    spot = annonces[annonces["category"] == "COTATION_SPOT"].copy()
    times = pd.to_datetime(spot["event_time"], utc=True)
    spot = spot[(times >= debut - pd.Timedelta(days=30)) & (times < fin)]

    def bases_of(row) -> set[str]:
        explicit = {t for t in str(row.get("tickers") or "").split(",") if t}
        return explicit or (set(_TOKEN.findall(str(row.get("detail") or ""))) & bases_all)

    announced: dict[str, pd.Timestamp] = {}
    orphans = []
    for _, row in spot.iterrows():
        bases = bases_of(row)
        when = pd.Timestamp(row["event_time"])
        matched = [b for b in bases if b in bases_all]
        if not matched:
            orphans.append(f"{when.date()} : {str(row.get('detail'))[:80]}")
        for b in matched:
            announced[b] = min(announced.get(b, when), when)
    kept = census[census["excluded"].isna()]
    first = pd.to_datetime(daily.groupby("symbol")["day"].min(), utc=True)
    new_pairs = [s for s in kept["symbol"] if s in first.index and debut <= first[s] < fin]
    base_of = dict(zip(census["symbol"], census["base"].astype(str), strict=True))
    without = sorted(s for s in new_pairs if base_of[s] not in announced)
    spot_in = spot[pd.to_datetime(spot["event_time"], utc=True) >= debut]
    rate_i = len(without) / len(new_pairs) if new_pairs else 0.0
    orphans_in = [o for o in orphans if o[:10] >= str(debut.date())]
    rate_ii = len(orphans_in) / len(spot_in) if len(spot_in) else 0.0
    return {"nouvelles_paires": len(new_pairs), "sans_annonce": without, "taux_sans_annonce": round(rate_i, 4),
            "annonces_cotation_spot": int(len(spot_in)), "annonces_sans_paire": orphans_in,
            "taux_annonces_sans_paire": round(rate_ii, 4),
            "au_dela_de_5_pourcent": {"i": rate_i > 0.05, "ii": rate_ii > 0.05},
            "note": "titres sans ticker entre parenthèses : correspondance par mots du titre (bases du recensement)"}


# --- Comptage à blanc -------------------------------------------------------------------------------------------

def comptage(settings: Settings, tests: list[Test], *, out_dir: Path, now: datetime,
             progress: Callable[[str], None] | None = None) -> dict:
    """Comptage à blanc sur la découverte réelle : comptes seulement, contrôles de dates et d'annonces ; écrit
    `comptage.json` et rend son empreinte (à inscrire dans `zoo_review.COMPTAGE` quand toutes les familles y sont)."""
    say = progress or (lambda _t: None)
    coded = [t for t in tests if t.etat_code == "code"]
    source = SourceMagasin(settings, DECOUVERTE, coded)
    counts = compter(coded, source, DECOUVERTE, progress=say)
    events = charger_evenements(settings, "calendrier_macro", DECOUVERTE.fin_exclusive)
    full_events = charger_evenements(settings, "calendrier_macro", HARD_END)
    census = recensement(settings)
    daily = charger_quotidien_recensement(settings, HARD_END)
    annonces = charger_evenements(settings, "annonces_binance", HARD_END)
    report = {
        "study": STUDY, "etape": "decouverte", "commit": code_state(), "date": now.isoformat(), "note": NOTE,
        "partiel": len(coded) < len(TESTS), "tests_comptes": len(coded),
        "tests_a_coder": sorted(t.id for t in TESTS if t.etat_code != "code"),
        "paires": len(source.symboles), "paires_sans_historique_1h": sorted(source.manquantes),
        "tests": counts,
        "insuffisants_par_construction": sorted(t for t, c in counts.items() if c["insuffisant_par_construction"]),
        "controle_cpi_nfp": {"decouverte": controle_dates_macro(events, DECOUVERTE.debut, DECOUVERTE.fin_exclusive),
                             "validation": controle_dates_macro(full_events, VALIDATION.debut, VALIDATION.fin_exclusive)},
        "controle_ev02": {"decouverte": controle_annonces(census, daily, annonces, DECOUVERTE.debut,
                                                          DECOUVERTE.fin_exclusive),
                          "validation": controle_annonces(census, daily, annonces, VALIDATION.debut,
                                                          VALIDATION.fin_exclusive)},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "comptage.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["path"] = str(path)
    report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report


# --- Exécution réelle unique ------------------------------------------------------------------------------------

def _resume_mesure(mesure, decision) -> dict:
    return {"statut": decision.statut, "p": decision.p, "stats": decision.stats, "gardes": decision.gardes,
            "descriptif": descriptif_a(mesure) if isinstance(mesure, MesureA) else descriptif_b(mesure)}


def _descriptif(mesure: MesureA | MesureB, eligible: frozenset[str]) -> dict:
    return descriptif_a(mesure, eligible) if isinstance(mesure, MesureA) else descriptif_b(mesure)


def executer_decouverte(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None) -> dict:
    from .review import comptage_inscrit, controle_inscrit, exiger_codes, exiger_relecture
    say = progress or (lambda _t: None)
    state = code_state()
    exiger_relecture(state, settings=settings)
    control = controle_inscrit()
    counted = comptage_inscrit()
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.count_runs(STRATEGIES["decouverte"]) > 0:
        raise AlreadyRun("découverte du zoo déjà exécutée : exécution unique")
    if counted.get("partiel"):
        from .review import NotReady
        raise NotReady("comptage inscrit partiel : toutes les familles doivent être comptées")
    withdrawn = set(counted["insuffisants_par_construction"])
    kept = [t for t in control["retenus"] if t not in withdrawn]
    exiger_codes(kept)
    tests = [PAR_ID[t] for t in kept]
    source = SourceMagasin(settings, DECOUVERTE, tests)
    recount = compter(tests, source, DECOUVERTE, progress=say)
    differ = [t for t in kept if recount[t].get("compteurs", {}).get("transactions") !=
              counted["tests"][t].get("compteurs", {}).get("transactions")]
    if differ:
        from .review import NotReady
        raise NotReady(f"comptage différent de celui inscrit ({', '.join(differ[:5])}) : aucun rendement calculé")
    refs = references(source, [DECOUVERTE], avec_vo11=any(t.reference == "meme_heure" for t in tests), progress=say)
    form_a = [t for t in tests if t.forme == FORME_A]
    measured, positions = mesurer_paires(form_a, list(refs.grilles), source, refs, [DECOUVERTE],
                                         garder_positions=True, progress=say)
    mesures: dict[str, MesureA | MesureB] = {t: m for (t, _), m in measured.items()}
    mesures |= {t: m for (t, _), m in mesurer_portefeuilles([t for t in tests if t.forme == FORME_B], refs,
                                                            [DECOUVERTE], source).items()}
    decisions, summary = decouverte(mesures)
    eligible = eligibles_f31(recensement(settings))
    details = {}
    for test_id, mesure in mesures.items():
        details[test_id] = _resume_mesure(mesure, decisions[test_id])
        if isinstance(mesure, MesureA):
            details[test_id]["descriptif"] = descriptif_a(mesure, eligible)
            details[test_id]["placebos"] = placebos_a(mesure, positions.get((test_id, DECOUVERTE.nom), {}),
                                                      refs.grilles, refs.jours[DECOUVERTE.nom])
    run_id = new_run_id("ZOO-DECOUVERTE")
    n_trials = len(tests)
    program = registry.program_trials() + n_trials
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run_id, "study": STUDY, "etape": "decouverte", "doc": "docs/ZOO.md", "n_trials": n_trials,
              "program_trials": program, "retires_h0": control["retires"],
              "insuffisants_par_construction": sorted(withdrawn), "bh": summary,
              "candidates": summary["candidates"], "tests": details, "commit": state,
              "paires_sans_historique_1h": source.manquantes}
    (report_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="zoo des stratégies : découverte (Benjamini-Hochberg à 10 %, docs/ZOO.md)",
                    strategy=STRATEGIES["decouverte"], strategy_version=1, variant="exécution unique",
                    params={"tests": kept}, period_label="DEVELOPMENT", period_start=str(DECOUVERTE.debut),
                    period_end=str(DECOUVERTE.fin), universe=source.symboles, data_hashes=refs.empreintes,
                    git_commit=state, dependencies=dependency_versions(), seed=20261012,
                    cost_scenario="central+defavorable", simulation_rules={"doc": "docs/ZOO.md § 4"},
                    metrics={"n_trials": n_trials, "program_trials": program, "candidates": summary["candidates"]},
                    status="COMPLETED", report_dir=str(report_dir))
    return result


def executer_validation(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None) -> dict:
    from .review import NotReady, exiger_codes, exiger_relecture
    say = progress or (lambda _t: None)
    state = code_state()
    exiger_relecture(state, settings=settings)
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.count_runs(STRATEGIES["validation"]) > 0:
        raise AlreadyRun("validation du zoo déjà exécutée : exécution unique")
    runs = [r for r in registry.recent(500) if r["strategy"] == STRATEGIES["decouverte"]]
    if not runs:
        raise NotReady("aucune découverte enregistrée : validation impossible")
    discovery = registry.get(runs[0]["run_id"])
    if discovery is None:
        raise NotReady("découverte enregistrée illisible")
    summary = json.loads((Path(discovery["report_dir"]) / "summary.json").read_text(encoding="utf-8"))
    candidates = [t for t, d in summary["tests"].items() if d["statut"] == CANDIDATE]
    if not candidates:
        raise NotReady("aucune CANDIDATE en découverte : le zoo s'arrête, aucun essai de validation (RIEN)")
    exiger_codes(candidates)
    tests = [PAR_ID[t] for t in candidates]
    source = SourceMagasin(settings, VALIDATION, tests)
    refs = references(source, [VALIDATION], avec_vo11=any(t.reference == "meme_heure" for t in tests), progress=say)
    measured, _ = mesurer_paires([t for t in tests if t.forme == FORME_A], list(refs.grilles), source, refs,
                                 [VALIDATION], progress=say)
    mesures: dict[str, MesureA | MesureB] = {t: m for (t, _), m in measured.items()}
    mesures |= {t: m for (t, _), m in mesurer_portefeuilles([t for t in tests if t.forme == FORME_B], refs,
                                                            [VALIDATION], source).items()}
    verdicts = valider(mesures)
    run_id = new_run_id("ZOO-VALIDATION")
    m = len(candidates)
    program = registry.program_trials() + m
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    eligible = eligibles_f31(recensement(settings))
    result = {"run_id": run_id, "study": STUDY, "etape": "validation", "n_trials": m, "program_trials": program,
              "decouverte": discovery["run_id"], "niveau": 1 - 0.05 / m,
              "tests": {t: {"statut": v.statut, "details": v.details, "descriptif": _descriptif(mesures[t], eligible)}
                        for t, v in verdicts.items()},
              "validees": sorted(t for t, v in verdicts.items() if v.statut == "VALIDÉE"), "commit": state}
    (report_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="zoo des stratégies : validation des CANDIDATE (docs/ZOO.md § 5.2)",
                    strategy=STRATEGIES["validation"], strategy_version=1, variant="exécution unique",
                    params={"candidates": candidates}, period_label="DEVELOPMENT", period_start=str(VALIDATION.debut),
                    period_end=str(VALIDATION.fin), universe=source.symboles, data_hashes=refs.empreintes,
                    git_commit=state, dependencies=dependency_versions(), seed=20261012,
                    cost_scenario="central+defavorable", simulation_rules={"doc": "docs/ZOO.md § 5.2"},
                    metrics={"n_trials": m, "program_trials": program, "validees": result["validees"]},
                    status="COMPLETED", report_dir=str(report_dir))
    return result

