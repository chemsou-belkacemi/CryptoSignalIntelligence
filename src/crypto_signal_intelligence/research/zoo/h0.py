"""Contrôle sous H0 du zoo (docs/ZOO.md § 8) : le pipeline COMPLET, sans aucune différence, sur 200 marchés
synthétiques (H0-a martingale, H0-b dérive constante, H0-c régimes), découverte puis validation.

Critères (§ 8.3), calculés à FRAIS NULS sur H0-a et H0-b :
1. par test : part des répliques où la statistique est > 0 avec p bilatérale ≤ 0,05, en découverte ET en validation,
   sur H0-a ET H0-b, ≤ 0,05 chacune ; sinon `MESURE_NON_VALIDE` (0 essai) ;
2. global, sur les tests restants : part des répliques avec au moins une `CANDIDATE` ≤ 0,10 sur H0-a et sur H0-b ;
   sinon `INSTRUMENT_NON_VALIDE` (le zoo n'est pas exécuté) ;
3. puissance (vrais frais, H0-a) : effet `δ = g + coût central moyen` ajouté aux positions (ou au portefeuille) du seul
   test `s`, signaux inchangés ; part des répliques où `s` devient `CANDIDATE` (Benjamini-Hochberg recalculé), puis
   `VALIDÉE` ; ajouter `δ` par jour d'exposition décale la statistique de `δ` sans changer son erreur type ;
4. `INSTRUMENT_AVEUGLE` : puissance de découverte < 0,10 au plus grand effet (0 essai) ;
5. H0-c et résultats avec frais réels : descriptifs ; part des répliques de H0-b où l'ancienne statistique « contre
   zéro » aurait produit une `CANDIDATE` : descriptif.

Lancé UNE SEULE FOIS (`csi zoo controle-h0`) : `reports/ZOO-H0-<commit>/criteres.json`, dont l'empreinte est inscrite
dans `research/zoo_review.py` (`CONTROLE_H0`). Les répliques sont écrites une par une (reprise après une coupure, sans
recalcul de celles déjà faites) ; un mini-contrôle (`--mini`) écrit ailleurs et n'est jamais inscriptible.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ...forward.costs import CENTRAL
from .data import DAY_H, DECOUVERTE, VALIDATION, Bougies, Grille, Paire, SerieExterne
from .decide import (
    ALPHA,
    DECISION,
    INSTRUMENT_AVEUGLE,
    INSTRUMENT_NON_VALIDE,
    MESURE_NON_VALIDE,
    NULS,
    benjamini_hochberg,
    composantes_validation,
    gardes_decouverte,
    p_decision,
)
from .measure import NUL
from .run import Source, mesurer_tout
from .spec import FORME_A, PAR_ID, Test
from .synth import MARCHES, SYMBOLES, Commun, generer_ev02, generer_paire, univers_synthetique

STUDY, CONTROL_KIND = "ZOO", "H0_ZOO_V1"
REPLIQUES = 200
EFFETS_A = (0.0010, 0.0025, 0.0050)          # par jour d'exposition
EFFETS_B = (0.0025, 0.0050, 0.0100, 0.0200)  # par semaine
SEUIL_TEST, SEUIL_GLOBAL, SEUIL_AVEUGLE = 0.05, 0.10, 0.10
MAX_WORKERS = 4
#: Descriptif du rapport H0 (relecture du 2026-10-11) : puissances à lire avec réserve.
NOTES_PUISSANCE = {
    "PF05": "non comparable : la référence (même panier laissé flotter) est identique à la stratégie au début de chaque "
            "trimestre, l'excès varie très peu",
    "PF06": "non comparable : poids proches de la référence à poids égaux une partie du temps (volatilités voisines)",
    "EV02": "puissance optimiste : la volatilité des premiers jours de cotation n'est pas modélisée par le marché "
            "synthétique (paramètres d'altcoin ordinaires)",
}
#: Tests à sortie unique dont un signal ignoré pendant une position peut déplacer les niveaux de sortie (README, 17).
SORTIE_UNIQUE = frozenset({"ST03", "ST04", "ST05", "AN06", "AN09"})


class SourceSynthetique(Source):
    """Marché synthétique d'une réplique : univers à date calculé par `pit_universe.membership` sur les journées
    synthétiques ; séries et calendriers externes RÉELS (inchangés), passés par l'appelant."""
    nom = "synthetique"

    def __init__(self, replique: int, marche: str, *, externes: dict[str, SerieExterne] | None = None,
                 evenements: dict[str, pd.DataFrame] | None = None, n_paires: int | None = None,
                 commun: Commun | None = None, cotations: dict[str, pd.Timestamp] | None = None):
        self.replique, self.marche = replique, marche
        self.cotations = cotations or {}
        self._recensement: dict[str, Bougies] | None = None
        self.commun = commun or Commun.tirer(replique)
        self.externes = externes or {}
        self.evenements = evenements or {}
        self._index = {s: j for j, s in enumerate(SYMBOLES[:n_paires])}
        grids: dict[str, Grille] = {}
        daily: dict[str, Bougies] = {}
        for sym, j in self._index.items():
            pair = generer_paire(self.commun, marche, j)
            if pair is None:
                continue
            grids[sym] = pair.grille
            daily[sym] = pair.unite(DAY_H)
        self._grids, self._daily = grids, daily
        self.symboles = sorted(grids)
        self.univers = univers_synthetique(daily)

    def pre_references(self) -> tuple[dict[str, Grille], dict[str, Bougies], dict[str, str]]:
        return self._grids, self._daily, {}

    def paire(self, symbole: str) -> Paire | None:
        return generer_paire(self.commun, self.marche, self._index[symbole])

    def quotidien_recensement(self) -> dict[str, Bougies]:
        """EV02 : une paire synthétique par paire USDT du recensement, cotée à la date réelle de sa première bougie
        (`synth.generer_ev02`, bougies journalières seulement)."""
        if self._recensement is None:
            self._recensement = generer_ev02(self.commun, self.marche, self.cotations) if self.cotations else {}
        return self._recensement


# --- Une réplique ------------------------------------------------------------------------------------------------

def _sig05(mesure, scenario: str, delta: float = 0.0) -> bool:
    s = mesure.stat(scenario, delta=delta)
    return bool(s is not None and s.stat > 0 and s.p <= 0.05)


def _decouverte(mesure, scenarios, delta: float = 0.0) -> dict:
    p, short, _ = p_decision(mesure, scenarios, delta=delta)
    guards = gardes_decouverte(mesure, scenarios, delta=delta)[0] if not short else False
    return {"p": p, "insuffisant": short, "gardes": guards, "sig05": _sig05(mesure, scenarios[0], delta)}


def _validation(mesure, scenarios, delta: float = 0.0) -> dict:
    parts = composantes_validation(mesure, scenarios, delta=delta)
    if parts.get("insuffisant"):
        return {"insuffisant": True, "sig05": False}
    return {"insuffisant": False, "p": parts["p"], "p_sans_annee": parts["sans_meilleure_annee"]["p"],
            "annees_ok": parts["annees"]["ok"], "concentration_ok": parts["concentration"]["ok"],
            "sig05": _sig05(mesure, scenarios[0], delta)}


def _cout_moyen(mesure) -> float | None:
    """Coût central moyen par unité d'effet planté : par jour d'exposition (forme a) ; par semaine d'exposition PLEINE
    (forme b : l'effet est planté proportionnellement à l'exposition de chaque semaine). Statistique à frais nuls
    moins statistique en central, sur la même réplique, divisée par l'exposition moyenne. None : rien à planter
    (stratégie jamais investie, ou insuffisante)."""
    a, b = mesure.stat(NUL, min_blocs=2, min_transactions=0), mesure.stat(CENTRAL, min_blocs=2, min_transactions=0)
    exposure = mesure.exposition_moyenne()
    if a is None or b is None or exposure <= 1e-9:
        return None
    return (a.stat - b.stat) / exposure


def resumer_marche(mesures: dict, tests: list[Test], marche: str) -> dict:
    out = {}
    for test in tests:
        md, mv = mesures[(test.id, DECOUVERTE.nom)], mesures[(test.id, VALIDATION.nom)]
        entry: dict[str, Any] = {"nul": {"d": _decouverte(md, NULS), "v": _validation(mv, NULS)},
                 "reel": {"d": _decouverte(md, DECISION), "v": _validation(mv, DECISION)},
                 "transactions": {"d": md.n_transactions, "v": mv.n_transactions},
                 "ignores_position": {"d": int(md.compteurs.get("ignores_position_ouverte", 0)),
                                      "v": int(mv.compteurs.get("ignores_position_ouverte", 0))}
                 if test.forme == FORME_A else None}
        if marche == "H0b":
            entry["ancienne_stat_p"] = p_decision(md, NULS, contre_zero=True)[0]
        if marche == "H0a":
            effects = EFFETS_A if test.forme == FORME_A else EFFETS_B
            cd, cv = _cout_moyen(md), _cout_moyen(mv)
            power: dict[str, Any] = {}
            for g in effects:
                if cd is None:
                    power[str(g)] = None          # jamais investie : aucun effet ne peut y être vu
                    continue
                power[str(g)] = {"d": _decouverte(md, DECISION, g + cd),
                                 "v": _validation(mv, DECISION, g + (cv if cv is not None else cd))}
            entry["puissance"] = power
        out[test.id] = entry
    return out


def replique(k: int, test_ids: list[str], marches: tuple[str, ...] = MARCHES, *, n_paires: int | None = None,
             externes: dict | None = None, evenements: dict | None = None, cotations: dict | None = None) -> dict:
    """Une réplique complète (les marchés demandés), réduite à ce dont les critères ont besoin."""
    tests = [PAR_ID[t] for t in test_ids]
    commun = Commun.tirer(k)
    out: dict = {"replique": k, "marches": {}, "secondes": {}}
    for marche in marches:
        start = time.perf_counter()
        source = SourceSynthetique(k, marche, externes=externes, evenements=evenements, n_paires=n_paires,
                                   commun=commun, cotations=cotations)
        mesures, _ = mesurer_tout(tests, source, [DECOUVERTE, VALIDATION], garder_transactions=False)
        out["marches"][marche] = resumer_marche(mesures, tests, marche)
        out["secondes"][marche] = round(time.perf_counter() - start, 1)
    return out


def memoire_pic_mo() -> int:
    """Pic de mémoire résidente du processus courant, en Mo (Linux : ru_maxrss en Kio ; macOS : en octets)."""
    import resource
    import sys
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024)


def _travail(args: tuple) -> tuple[int, str]:
    k, test_ids, marches, out_dir, n_paires, externes, evenements, cotations = args
    path = Path(out_dir) / "repliques" / f"{k:03d}.json"
    if path.exists():
        return k, "deja_faite"
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    summary = replique(k, test_ids, marches, n_paires=n_paires, externes=externes, evenements=evenements,
                       cotations=cotations)
    summary["memoire_pic_mo"] = memoire_pic_mo()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, default=float), encoding="utf-8")
    tmp.replace(path)
    return k, "faite"


# --- Agrégation et critères ------------------------------------------------------------------------------------

def _candidates(entries: dict[str, dict], tests: list[str], key: str = "nul", *, p_field: str | None = None,
                remplace: tuple[str, dict] | None = None) -> set[str]:
    pvalues, guards = {}, {}
    for t in tests:
        d = entries[t][key]["d"] if p_field is None else {"p": entries[t].get(p_field, 1.0),
                                                          "insuffisant": entries[t]["nul"]["d"]["insuffisant"],
                                                          "gardes": entries[t]["nul"]["d"]["gardes"]}
        if remplace is not None and remplace[0] == t:
            d = remplace[1]
        if d is None or d["insuffisant"]:
            continue
        pvalues[t] = d["p"]
        guards[t] = d["gardes"]
    return {t for t in benjamini_hochberg(pvalues) if guards[t]}


def _valide(v: dict | None, m: int) -> bool:
    if not v or v.get("insuffisant") or m <= 0:
        return False
    level = ALPHA / m
    return bool(v["p"] < level and v["p_sans_annee"] < level and v["annees_ok"] and v["concentration_ok"])


def criteres(resumes: list[dict], test_ids: list[str]) -> dict:
    """Critères 1 à 4 du § 8.3 et descriptifs, à partir des résumés de répliques."""
    n = len(resumes)
    per_test: dict[str, dict] = {}
    for t in test_ids:
        shares = {}
        for marche in ("H0a", "H0b"):
            for stage in ("d", "v"):
                hits = [r["marches"][marche][t]["nul"][stage]["sig05"] for r in resumes if marche in r["marches"]]
                shares[f"{marche}_{stage}"] = round(float(np.mean(hits)), 4) if hits else None
        ok = all(v is not None and v <= SEUIL_TEST for v in shares.values())
        per_test[t] = {"critere_1": shares, "passe_critere_1": ok}
    remaining = [t for t in test_ids if per_test[t]["passe_critere_1"]]
    global_: dict = {"repliques": n, "tests_restants_apres_critere_1": len(remaining)}
    for marche in ("H0a", "H0b", "H0c"):
        rows = [r["marches"][marche] for r in resumes if marche in r["marches"]]
        if not rows:
            continue
        any_cand, any_valid, counts, real_any = [], [], [], []
        for entries in rows:
            cands = _candidates(entries, remaining, "nul")
            any_cand.append(bool(cands))
            counts.append(len(cands))
            any_valid.append(any(_valide(entries[t]["nul"]["v"], len(cands)) for t in cands))
            real_any.append(bool(_candidates(entries, remaining, "reel")))
        global_[marche] = {"part_au_moins_une_candidate": round(float(np.mean(any_cand)), 4),
                           "part_au_moins_une_validee": round(float(np.mean(any_valid)), 4),
                           "fausses_candidates_moyennes": round(float(np.mean(counts)), 3),
                           "part_candidate_frais_reels_descriptif": round(float(np.mean(real_any)), 4)}
        if marche == "H0b":
            old = [bool(_candidates(entries, remaining, p_field="ancienne_stat_p")) for entries in rows]
            global_[marche]["part_candidate_ancienne_stat_contre_zero_descriptif"] = round(float(np.mean(old)), 4)
    passes_global = all(global_.get(m, {}).get("part_au_moins_une_candidate", 1.0) <= SEUIL_GLOBAL
                        for m in ("H0a", "H0b"))
    rows_a = [r["marches"]["H0a"] for r in resumes if "H0a" in r["marches"]]
    for t in test_ids:
        power: dict = {}
        effects = EFFETS_A if PAR_ID[t].forme == FORME_A else EFFETS_B
        for g in effects:
            found, valid = [], []
            for entries in rows_a:
                planted = (entries[t].get("puissance") or {}).get(str(g))
                if planted is None or t not in remaining:
                    found.append(False)
                    valid.append(False)
                    continue
                cands = _candidates(entries, remaining, "reel", remplace=(t, planted["d"]))
                found.append(t in cands)
                valid.append(t in cands and _valide(planted["v"], len(cands)))
            power[str(g)] = {"candidate": round(float(np.mean(found)), 4) if found else 0.0,
                             "validee": round(float(np.mean(valid)), 4) if valid else 0.0}
        per_test[t]["puissance"] = power
        top = power[str(max(effects))]["candidate"]
        if not per_test[t]["passe_critere_1"]:
            status = MESURE_NON_VALIDE
        elif top < SEUIL_AVEUGLE:
            status = INSTRUMENT_AVEUGLE
        else:
            status = "RETENU"
        per_test[t]["statut"] = status
        per_test[t]["passes"] = status == "RETENU"
        note = NOTES_PUISSANCE.get(PAR_ID[t].strategie)
        if note:
            per_test[t]["note_puissance"] = note
        if PAR_ID[t].strategie in SORTIE_UNIQUE:
            ignored = [r["marches"]["H0a"][t].get("ignores_position") for r in resumes if "H0a" in r["marches"]]
            ignored = [x["d"] for x in ignored if x]
            per_test[t]["signaux_ignores_pendant_une_position_moyenne_h0a"] = \
                round(float(np.mean(ignored)), 1) if ignored else None
    retained = [t for t, v in per_test.items() if v["passes"]]
    for marche in ("H0a", "H0b", "H0c"):
        rows = [r["marches"][marche] for r in resumes if marche in r["marches"]]
        if rows and marche in global_:
            global_[marche]["part_au_moins_une_candidate_sur_tests_retenus_descriptif"] = round(float(np.mean(
                [bool(_candidates(entries, retained, "nul")) for entries in rows])), 4)
    return {"tests": per_test, "global": global_, "passe_global": passes_global,
            "issue": "RETENU" if passes_global else INSTRUMENT_NON_VALIDE,
            "retenus": sorted(t for t, v in per_test.items() if v["passes"]),
            "retires": {t: v["statut"] for t, v in per_test.items() if not v["passes"]}}


def externes_reels(settings, test_ids: list[str]) -> tuple[dict, dict, dict]:
    """Séries et calendriers RÉELS (inchangés) des tests qui en lisent (AM, SE, CA), coupés au 2025-06-30 (même lecture
    que l'exécution réelle, `reel.externes_pour`) ; sur des cryptos synthétiques indépendantes, la nulle reste exacte.
    EV02 : cotations aux dates RÉELLES des premières bougies du recensement, annonces à l'heure réelle de leur annonce
    `COTATION_SPOT` (sinon 24 h avant la cotation) : la table d'annonces passée aux stratégies est la table synthétique
    (`synth.annonces_ev02`). Rend (séries, événements, cotations)."""
    from .data import HARD_END, charger_quotidien_recensement
    from .reel import externes_pour
    from .synth import annonces_ev02
    tests = [PAR_ID[t] for t in test_ids]
    series, events = externes_pour(settings, tests, HARD_END)
    cotations: dict = {}
    if any(t.famille == "EV" for t in tests):
        daily = charger_quotidien_recensement(settings, HARD_END)
        first = pd.to_datetime(daily.groupby("symbol")["day"].min(), utc=True)
        cotations = {str(s): pd.Timestamp(d) for s, d in first.items()}
        events["annonces_binance"] = annonces_ev02(events.get("annonces_binance", pd.DataFrame()), cotations)
    return series, events, cotations


def executer(out_dir: Path, test_ids: list[str], *, repliques: int = REPLIQUES, workers: int = 2,
             marches: tuple[str, ...] = MARCHES, n_paires: int | None = None, mini: bool = False,
             externes: dict | None = None, evenements: dict | None = None, cotations: dict | None = None,
             commit: str = "",
             now: datetime | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Contrôle sous H0 : répliques (reprise possible), puis critères ; écrit `criteres.json` et son empreinte."""
    say = progress or (lambda _t: None)
    out_dir = Path(out_dir)
    final = out_dir / "criteres.json"
    if final.exists():
        raise FileExistsError(f"contrôle sous H0 déjà conclu ({final}) : lancé une seule fois")
    if not mini:
        for other in sorted(out_dir.parent.glob("ZOO-H0-*/plan.json")):
            if other.parent.resolve() == out_dir.resolve():
                continue
            try:
                started_before = not json.loads(other.read_text(encoding="utf-8")).get("mini", True)
            except (OSError, ValueError):
                started_before = True
            if started_before:
                raise FileExistsError(f"un contrôle sous H0 complet a déjà commencé ({other.parent}) : lancé une seule "
                                      "fois, quel que soit le commit")
    (out_dir / "repliques").mkdir(parents=True, exist_ok=True)
    plan = {"study": STUDY, "control": CONTROL_KIND, "commit": commit, "mini": mini, "repliques": repliques,
            "marches": list(marches), "tests": test_ids, "n_paires": n_paires}
    plan_path = out_dir / "plan.json"
    if plan_path.exists() and json.loads(plan_path.read_text(encoding="utf-8")) != plan:
        raise ValueError("un contrôle différent a déjà commencé dans ce dossier : reprise refusée")
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    started = time.perf_counter()
    jobs = [(k, test_ids, marches, str(out_dir), n_paires, externes, evenements, cotations) for k in range(repliques)]
    workers = max(1, min(workers, MAX_WORKERS))
    if workers == 1:
        for job in jobs:
            say(f"réplique {job[0] + 1}/{repliques} : {_travail(job)[1]}")
    else:
        with ProcessPoolExecutor(workers) as pool:
            futures = [pool.submit(_travail, job) for job in jobs]
            for done, future in enumerate(as_completed(futures), start=1):
                k, state = future.result()
                say(f"réplique {k} : {state} ({done}/{repliques})")
    resumes = [json.loads((out_dir / "repliques" / f"{k:03d}.json").read_text(encoding="utf-8"))
               for k in range(repliques)]
    report = {**plan, "date": (now or datetime.now()).isoformat(), "secondes_totales": round(time.perf_counter() - started, 1),
              "secondes_par_replique": {m: round(float(np.mean([r["secondes"][m] for r in resumes])), 1)
                                        for m in marches},
              "memoire_pic_mo": max(int(r.get("memoire_pic_mo") or 0) for r in resumes),
              "repliques_par_marche": {m: sum(m in r["marches"] for r in resumes) for m in marches},
              **criteres(resumes, test_ids)}
    final.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    report["path"] = str(final)
    report["sha256"] = hashlib.sha256(final.read_bytes()).hexdigest()
    return report
