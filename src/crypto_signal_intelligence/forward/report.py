"""Rapport QUOTIDIEN UNIQUE des tests en direct (reports/forward/<jour>.md et .json).

Il dit, pour chaque test : son état, ses dates (démarrage, revue intermédiaire, évaluation), la vérification de
son journal, ses comptes et ses mesures du moment. Avant la date d'évaluation, les mesures sont PROVISOIRES et
ne décident rien ; la revue intermédiaire est descriptive et technique (aucune règle ne change).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from . import datalog, derivlog
from .registry import NOT_STARTED, STOPPED, journal_for, status
from .tests import TESTS

UNLOCKS = ("Calendrier des unlocks : NON FAIT. Aucune source fiable accessible sans abonnement ni clé (vérifié le "
           "2026-10-02 : DefiLlama payant, CryptoRank avec clé, Tokenomist bloqué).")
WARNING = ("Mesures provisoires : rien n'est décidé avant la date d'évaluation inscrite au démarrage. Un test en "
           "direct de 12 semaines ne valide pas une stratégie : il dit seulement ce qui s'est passé sur cette période.")


def build(settings: Settings, *, now: datetime) -> dict:
    report: dict = {"generated_at": pd.Timestamp(now).isoformat(), "tests": [], "warning": WARNING,
                    "unlocks": UNLOCKS}
    for test, module in TESTS:
        state = status(settings, test, now=now)
        journal = journal_for(settings, test.test_id)
        item = {"test_id": test.test_id, "title": test.title, "hypothesis": test.hypothesis,
                "state": state["state"], "journal": journal.verify()}
        if state["state"] != NOT_STARTED:
            start = state["start"]
            item |= {"started_at": start["started_at"], "interim_at": start["interim_at"],
                     "final_at": start["final_at"], "run_id": start["run_id"],
                     "halal_pairs": len(start["halal"]["symbols"])}
            if "stop" in state:
                item["stop"] = state["stop"]
            item["stats"] = module.stats(journal, start, now=now)
            if "verdict" in state:                              # verdict inscrit au journal : il fait foi
                item["stats"]["verdict"] = state["verdict"]["verdict"]
                item["verdict_recorded"] = True
            elif state["state"] == STOPPED:
                item["stats"]["verdict"] = STOPPED
        report["tests"].append(item)
    report["derivatives_log"] = derivlog.summary(settings)
    report["context_log"] = datalog.summary(settings)
    report["forward_trials"] = forward_trials(settings)
    return report


def forward_trials(settings: Settings) -> int:
    """Essais « FORWARD » du registre de ce CSI_ROOT (hors N des lots de recherche, affichés à part)."""
    from ..research.experiments import ExperimentRegistry
    from .registry import PERIOD_LABEL
    if not settings.experiments_db.exists():
        return 0
    return ExperimentRegistry(settings.experiments_db).program_trials(PERIOD_LABEL)


def _fmt(value, pct: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}" if pct else f"{value:+.3f}"


def _context_line(log: dict) -> str:
    last = log.get("last") or {}
    sources = ", ".join(f"{k} ({v})" for k, v in sorted((log.get("latest_by_source") or {}).items())) or "aucune"
    return (f"Jours clos : {log.get('days', 0)} (depuis {log.get('first_day') or '—'}) ; dernier : {last.get('day', '—')}, "
            f"{last.get('sources', 0)} sources, {last.get('errors', 0)} erreurs ; dernier relevé par source : {sources} ; "
            f"journal {'intègre' if (log.get('verified') or {}).get('ok', True) else 'ROMPU'}. Information seulement : "
            "aucune de ces données n'influence un test en cours.")


def markdown(report: dict) -> str:
    lines = [f"# Tests en direct : rapport du {report['generated_at'][:10]}", "", f"> {report['warning']}", ""]
    for item in report["tests"]:
        lines += [f"## {item['test_id']} : {item['title']}", "", f"État : **{item['state']}**.",
                  f"Journal : {'intègre' if item['journal']['ok'] else 'ROMPU : ' + str(item['journal']['reason'])}"
                  f" ({item['journal']['entries']} entrées)."]
        if item["state"] == NOT_STARTED:
            lines += ["", "Pas encore démarré.", ""]
            continue
        lines += [f"Démarré le {item['started_at'][:16]} UTC ; revue intermédiaire le {item['interim_at'][:10]} ; "
                  f"évaluation le {item['final_at'][:10]}. Paires halal figées : {item['halal_pairs']}."]
        if "stop" in item:
            lines.append(f"ARRÊTÉ : {item['stop']['reason']}.")
        stats = item["stats"]
        if "scenarios" in stats:                              # F2 : variantes de sortie sur les mêmes entrées
            lines += [f"Signaux inscrits : {stats['decisions']} ; en attente : {stats['pending']} ; hors screening : "
                      f"{stats['skipped']} ; trous : {stats['gaps']}.", "",
                      "| Variante | Coûts | Remplis | R moyen | Gagnants | Écart à l'origine | IC de l'écart |",
                      "|---|---|---|---|---|---|---|"]
            for scenario, variants in stats["scenarios"].items():
                for variant, s in variants.items():
                    lines.append(f"| {variant} | {scenario} | {s.get('filled', 0)} | {_fmt(s.get('r_mean'))} | "
                                 f"{_fmt(s.get('win_share'), True)} | {_fmt(s.get('diff_mean'))} | "
                                 f"{s.get('diff_ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        lines += [f"Décisions en attente de résolution : {stats['pending']} (horizon principal : "
                  f"{stats.get('pending_primary', '—')}) ; hors screening : {stats['skipped']}.", "",
                  "| Horizon | Lecture | Décisions | Jours | Remplissage | R taker | R maker | Écart | IC95 de l'écart |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for horizon, views in stats["horizons"].items():
            for view, s in views.items():
                lines.append(f"| {horizon} | {view} | {s['n']} | {s['days']} | {_fmt(s.get('fill_rate'), True)} | "
                             f"{_fmt(s.get('taker_r'))} | {_fmt(s.get('maker_r'))} | {_fmt(s.get('diff'))} | "
                             f"{s.get('diff_ci') or '—'} |")
        primary = stats["horizons"].get("24h", {}).get("observe", {})
        if primary.get("break_even_bps") is not None:
            lines += ["", f"Écart d'équilibre (24 h, lecture observe) : {primary['break_even_bps']} pb : le maker "
                          "ne devient meilleur en moyenne que si l'écart et le glissement réels du taker à l'entrée "
                          "dépassent ce coût (estimation ponctuelle)."]
        lines += ["", f"Verdict : **{stats['verdict']}**.", ""]
    log = report["derivatives_log"]
    last = log["last"] or {}
    lines += ["## Relevé du financement et de l'intérêt ouvert (F0_DERIVES)", "",
              f"Jours relevés : {log['days']} (depuis {log['first_day'] or '—'}) ; dernier jour : "
              f"{last.get('day', '—')}, {last.get('pairs', 0)} paires, {last.get('no_perpetual', 0)} sans perpétuel, "
              f"{last.get('errors', 0)} erreurs ; journal {'intègre' if log['verified']['ok'] else 'ROMPU'}.", "",
              "## Relevé des données de contexte (F0_DONNEES)", "",
              _context_line(report.get("context_log") or {}), "",
              report["unlocks"], "",
              f"Essais « FORWARD » enregistrés : {report.get('forward_trials', 0)} (comptés à part des lots de "
              "recherche).", ""]
    return "\n".join(lines)


def path_for(settings: Settings, day: str, suffix: str) -> Path:
    return settings.reports_dir / "forward" / f"{day}.{suffix}"


def write(settings: Settings, *, now: datetime) -> Path:
    report = build(settings, now=now)
    day = pd.Timestamp(now).strftime("%Y-%m-%d")
    target = path_for(settings, day, "md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown(report), encoding="utf-8")
    path_for(settings, day, "json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                               encoding="utf-8")
    return target


def latest(settings: Settings) -> dict | None:
    folder = settings.reports_dir / "forward"
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None

