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
            if hasattr(module, "analyst_overlap"):              # F15 : comparaison à trois (descriptive)
                overlap = module.analyst_overlap(settings, journal)
                item["stats"]["analyst_overlap"] = {"figures": overlap["figures"], "analyst_signals": overlap["analyst_signals"],
                                                    "same_pair_24h": len(overlap["same_pair_24h"])}
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


SINGLE_IDS = ("F20_BASE_RETEST", "F21_SQUEEZE", "F22_FORCE_RELATIVE", "F23_INSIDE_DAY", "F24_SORTIE_BASE_LONGUE")


def _singles_section(items: list[dict]) -> list[str]:
    """Une seule section pour les tests séparés F20 à F24 (une configuration chacun, quota propre) : une ligne par test."""
    lines = ["## F20 à F24 : tests séparés « price action » (une configuration chacun, quota propre)", "",
             "| Test | Configuration | État | Journal | Évaluations (tardives) | Candidats | Appels (aussi dans F19) | Résolus | "
             "Trous | En cours | R net (central) | IC 99 % du R | R net (défavorable) | Excès avant (descr.) | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for item in items:
        journal = "intègre" if item["journal"]["ok"] else "ROMPU"
        stats = item.get("stats")
        if not stats or not stats.get("price_action_single"):
            lines.append(f"| {item['test_id']} | — | {item['state']} | {journal} | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        c, a = stats["scenarios"].get("central", {}), stats["scenarios"].get("defavorable", {})
        lines.append(f"| {item['test_id']} | {stats['config']} | {item['state']} | {journal} | {stats['evaluations']} "
                     f"({stats['late']}) | {stats['candidates']} | {stats['calls']} ({stats['also_in_f19']}) | {stats['resolved']} | "
                     f"{stats['gaps']} | {stats['pending']} | {_fmt(c.get('r_mean'))} | {c.get('r_ci_decision') or '—'} | "
                     f"{_fmt(a.get('r_mean'))} | {_fmt(c.get('placebo_excess_forward'))} | {stats['verdict']} |")
    lines += ["", "Mêmes règles que F19, une configuration par test avec son propre quota de 5 appels par jour ; une partie "
              "des appels sont aussi ceux de F19 (mêmes données, verdicts corrélés). Shadow : aucun ordre ; aucun gain "
              "démontré. Verdict sur le R net seul (`INSUFFISANT` sous 30 appels résolus).", ""]
    return lines


def markdown(report: dict) -> str:
    lines = [f"# Tests en direct : rapport du {report['generated_at'][:10]}", "", f"> {report['warning']}", ""]
    singles = [item for item in report["tests"] if item["test_id"] in SINGLE_IDS]
    for item in report["tests"]:
        if item["test_id"] in SINGLE_IDS:                     # F20 à F24 : une section pour les cinq
            if item is singles[0]:
                lines += _singles_section(singles)
            continue
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
        if stats.get("price_action"):                         # F19 : cinq configurations « price action » contre placebos
            lines += [f"Évaluations 4 h : {stats['evaluations']} (tardives : {stats['late']}) ; candidats : {stats['candidates']} ; "
                      f"appels : {stats['calls']} ({stats['calls_per_week']} par semaine ; résolus {stats['resolved']}, trous "
                      f"{stats['gaps']}, en cours {stats['pending']}) ; refus par raison : {stats['refusals_by_reason'] or 'aucun'}.", "",
                      "| Configuration | Candidats | Appels | Résolus | R net (central) | IC 99 % du R | R net (défavorable) | "
                      "Excès global (descr.) | Excès avant (descr.) | Verdict |", "|---|---|---|---|---|---|---|---|---|---|"]
            verdicts = stats["verdict"] if isinstance(stats["verdict"], dict) else {}
            for config, g in stats["configs"].items():
                c, a = g["scenarios"].get("central", {}), g["scenarios"].get("defavorable", {})
                lines.append(f"| {config} | {g['candidates']} | {g['calls']} | {g['resolved']} | {_fmt(c.get('r_mean'))} | "
                             f"{c.get('r_ci_decision') or '—'} | {_fmt(a.get('r_mean'))} | {_fmt(c.get('placebo_excess'))} | "
                             f"{_fmt(c.get('placebo_excess_forward'))} | {verdicts.get(config, g['verdict'])} |")
            lines += ["", "Shadow : aucun ordre ; aucun gain démontré. Verdict par configuration sur le R net seul "
                      "(`INSUFFISANT` sous 30 appels résolus) ; placebos descriptifs.", ""]
            continue
        if stats.get("assistant"):                            # F18 : assistant de marché contre placebos
            lines += [f"Évaluations 4 h : {stats['evaluations']} (silence : {stats['silent']}) ; candidats : {stats['candidates']} ; "
                      f"appels : {stats['calls']} ({stats['calls_per_week']} par semaine ; résolus {stats['resolved']}, trous "
                      f"{stats['gaps']}, en cours {stats['pending']}) ; refus par raison : {stats['refusals_by_reason'] or 'aucun'}.", "",
                      "| Coûts | Appels résolus | Jours | R moyen | IC95 du R | Gagnants | TP1 | TP2 | Pire série | Excès placebos | IC de l'excès |",
                      "|---|---|---|---|---|---|---|---|---|---|---|"]
            for scenario, s in stats["scenarios"].items():
                lines.append(f"| {scenario} | {s.get('n', 0)} | {s.get('days', 0)} | {_fmt(s.get('r_mean'))} | {s.get('r_ci95') or '—'} | "
                             f"{_fmt(s.get('win_share'), True)} | {_fmt(s.get('tp1_rate'), True)} | {_fmt(s.get('tp2_rate'), True)} | "
                             f"{s.get('worst_streak', '—')} | {_fmt(s.get('placebo_excess'))} | {s.get('placebo_excess_ci') or '—'} |")
            for regime, g in stats["by_regime"].items():
                lines.append(f"- {regime} : {g['calls']} appel(s), {g['resolved']} résolu(s), R moyen central "
                             f"{_fmt(g['central'].get('r_mean'))} (descriptif).")
            lines += ["", "Shadow : aucun ordre ; aucun gain démontré.", f"Verdict : **{stats['verdict']}**.", ""]
            continue
        if "groups" in stats and "overall" in stats:          # F15 : détecteur de figures contre placebos
            whole = stats["overall"]["scenarios"].get("central", {})
            lines += [f"Figures : {stats['figures']} (haussières {stats['bull']}, baissières {stats['bear']}, géométrie invalide "
                      f"{stats['invalid']}) ; ordres : {stats['orders']} (exécutés {stats['executed']}, annulés {stats['cancelled']}, "
                      f"trous {stats['gaps']}, en cours {stats['pending']} ; ordres distincts {stats.get('distinct_orders', '—')}, "
                      f"exécutés distincts {stats.get('distinct_executed', '—')}) ; ensemble, central : {whole.get('n', 0)} résolues, "
                      f"R moyen {_fmt(whole.get('r_mean'))} {whole.get('r_ci95') or ''}, excès sur les placebos "
                      f"{_fmt(whole.get('placebo_excess'))} {whole.get('placebo_excess_ci') or ''}.", "",
                      "| Famille / unité | Figures haussières | Exécutées | R moyen (central) | Excès placebos | TP1 atteint |",
                      "|---|---|---|---|---|---|"]
            for name, g in stats["groups"].items():
                c = g["central"]
                lines.append(f"| {name} | {g['bull_figures']} | {g['executed']} | {_fmt(c.get('r_mean'))} | "
                             f"{_fmt(c.get('placebo_excess'))} | {_fmt((c.get('tp_reached') or {}).get('TP1'), True)} |")
            overlap = stats.get("analyst_overlap") or {}
            lines += ["", f"Comparaison aux analystes (descriptive) : {overlap.get('figures', 0)} ordres du détecteur, "
                      f"{overlap.get('analyst_signals', 0)} signaux joués de F4 et F16, {overlap.get('same_pair_24h', 0)} "
                      "paires figure–signal sur la même paire à moins de 24 h.",
                      "", "Familles et unités : descriptif seulement.", f"Verdict : **{stats['verdict']}**.", ""]
            continue
        if "news_items" in stats:                             # F8 : filtre de news sur A
            per = stats["scenarios"]
            lines += [f"News avec terme : {stats['news_items']} (appliquées : {stats['applied']}, ambiguës : {stats['ambiguous']}) ; "
                      f"par actif : {stats['by_asset'] or 'aucun'} ; jours valorisés : {stats['days']} ; jours-actifs coupés : "
                      f"{stats['masked_days']} ; jours sans F5 : {stats['skipped_days']} ; cohérence avec A de F5 : "
                      f"{len(stats['f5_consistency']['mismatches'])} écart(s).", "",
                      "| Coûts | Portefeuille | Jours | Rendement | Perte max | Transactions |", "|---|---|---|---|---|---|"]
            for scenario, variants in per.items():
                for variant, s in variants.items():
                    if isinstance(s, dict):
                        lines.append(f"| {scenario} | {variant} | {s.get('days', 0)} | {_fmt(s.get('return'), True)} | "
                                     f"{_fmt(s.get('max_drawdown'), True)} | {s.get('trades', '—')} |")
                lines.append(f"| {scenario} | A + news − A | | {_fmt(variants.get('A_NEWS_minus_A'), True)} | | |")
            lines += ["", f"Verdict : **{stats['verdict']}**.", ""]
            continue
        if "listings" in stats:                               # F7 : listings
            lines += [f"Listings : {stats['listings']} (joués : {stats['decisions']}, comptés : {stats['counted']}, par source : "
                      f"{stats['by_source']}) ; latence médiane : {stats['latency_median_min'] if stats['latency_median_min'] is not None else '—'} min ; "
                      f"en attente : {stats['pending']} ; trous : {stats['gaps']} ; erreurs de source : {stats['source_errors']}.", "",
                      "| Coûts | Horizon | Résolus | Rendement net | IC | Gagnants | Relatif à BTC | IC |", "|---|---|---|---|---|---|---|---|"]
            for scenario, horizons in stats["scenarios"].items():
                for horizon, s in horizons.items():
                    lines.append(f"| {scenario} | {horizon} | {s.get('n', 0)} | {_fmt(s.get('r_mean'), True)} | {s.get('r_ci') or '—'} | "
                                 f"{_fmt(s.get('win_share'), True)} | {_fmt(s.get('relative_r_mean'), True)} | {s.get('relative_r_ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "comparisons" in stats:                            # F12 : prévisions de volatilité en direct
            lines += [f"Prévisions journalisées : {stats['checks']} ; résolues : {stats['resolved']} ; en attente : {stats['pending']}.", "",
                      "| Horizon | Candidat | Référence | Attendu | Jours | QLIKE candidat | QLIKE référence | Écart | IC | Verdict |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for c in stats["comparisons"].values():
                lines.append(f"| {c['horizon']} | {c['candidate']} | {c['reference']} | {c['expected']} | {c['days']} | "
                             f"{_fmt(c.get('qlike_candidate'))} | {_fmt(c.get('qlike_reference'))} | {_fmt(c.get('diff'))} | "
                             f"{c.get('ci') or '—'} | {c['verdict']} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "without_forecast" in stats:                       # F14 : niveaux par la volatilité prévue, en R
            lines += [f"Contrôles : {stats['checks']} ; événements : {stats['events']} (joués : {stats['decisions']}, sans prévision : "
                      f"{stats['without_forecast']}) ; par actif : {stats['by_asset'] or 'aucun'} ; en attente : {stats['pending']} ; "
                      f"trous : {stats['gaps']}.", "",
                      "| Coûts | Comparaison | Résolus | R de l'achat | Écart moyen (R) | Écarts positifs | IC |", "|---|---|---|---|---|---|---|"]
            for scenario, comparisons in stats["scenarios"].items():
                for name, s in comparisons.items():
                    lines.append(f"| {scenario} | {name} | {s.get('n', 0)} | {_fmt(s.get('event_r'))} | {_fmt(s.get('mean'))} | "
                                 f"{_fmt(s.get('win_share'), True)} | {s.get('ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "checks" in stats and "latest" not in stats:       # F9, F10, F11 : contrôle quotidien, achat contre placebos
            lines += [f"Contrôles : {stats['checks']} ; événements : {stats['events']} ; par actif : {stats['by_asset'] or 'aucun'} ; "
                      f"en attente : {stats['pending']} ; trous : {stats['gaps']}.", "",
                      "| Coûts | Horizon | Événements | Rendement achat | Rendement placebos | Excès | Gagnants | IC de l'excès |",
                      "|---|---|---|---|---|---|---|---|"]
            for scenario, horizons in stats["scenarios"].items():
                for horizon, s in horizons.items():
                    lines.append(f"| {scenario} | {horizon} | {s.get('n', 0)} | {_fmt(s.get('event_r'), True)} | "
                                 f"{_fmt(s.get('placebo_r'), True)} | {_fmt(s.get('excess'), True)} | {_fmt(s.get('win_share'), True)} | "
                                 f"{s.get('excess_ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "checks" in stats:                                 # F6 : capitulation
            lines += [f"Contrôles : {stats['checks']} sur {stats.get('days', '—')} jour(s) ; derniers indicateurs : {stats.get('latest')} ; "
                      f"événements : {stats['events']} ({stats.get('by_status')}) ; par actif : {stats['by_asset']} ; en attente : "
                      f"{stats['pending']} ; trous : {stats['gaps']}.", "",
                      "| Coûts | Horizon | Événements | Rendement achat | Rendement placebos | Excès | Gagnants | IC de l'excès |",
                      "|---|---|---|---|---|---|---|---|"]
            for scenario, horizons in stats["scenarios"].items():
                for horizon, s in horizons.items():
                    lines.append(f"| {scenario} | {horizon} | {s.get('n', 0)} | {_fmt(s.get('event_r'), True)} | "
                                 f"{_fmt(s.get('placebo_r'), True)} | {_fmt(s.get('excess'), True)} | {_fmt(s.get('win_share'), True)} | "
                                 f"{s.get('excess_ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "light_share" in stats:                            # F5 : modèle A en direct, feu tricolore, statique
            lines += [f"Décisions hebdomadaires : {stats['decisions']} ; jours valorisés : {stats['days']} ; feu : "
                      f"{stats['light_days']} ; actifs rouges : {stats['red_assets'] or 'aucun'} ; conformité : "
                      f"{stats['conformity']['status']} ({stats['conformity']['decisions']} décisions recalculées, "
                      f"{len(stats['conformity']['mismatches'])} écart(s)) ; exposition statique : {stats['static_exposure']}.", "",
                      "| Coûts | Portefeuille | Jours | Rendement | Vol. annualisée | Perte max | Transactions | Frais | Exposition |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for scenario, variants in stats["scenarios"].items():
                for variant, s in variants.items():
                    if not isinstance(s, dict):
                        continue
                    lines.append(f"| {scenario} | {variant} | {s.get('days', 0)} | {_fmt(s.get('return'), True)} | "
                                 f"{_fmt(s.get('volatility_annual'), True)} | {_fmt(s.get('max_drawdown'), True)} | "
                                 f"{s.get('trades', '—')} | {s.get('fees_pct', '—')} % | {_fmt(s.get('exposure'), True)} |")
                lines.append(f"| {scenario} | A + feu − A | | {_fmt(variants.get('A_FEU_minus_A'), True)} | | | | | |")
            lines += ["", "Suivi du comportement : aucune validation sur 3 mois.", f"Verdict : **{stats['verdict']}**.", ""]
            continue
        if "providers" in stats:                              # F4 : signaux Telegram en direct, par fournisseur
            lines += [f"Messages : {stats['signals']} (joués : {stats['decisions']} ; comptés : {stats['counted']}, "
                      f"{stats['by_status']}) ; en attente : {stats['pending']} ; non mesurables : {stats['unplayable']} ; "
                      f"modifiés : {stats['edited']} ; versions du parseur : {stats['parser_codes']}.", "",
                      "| Fournisseur | Coûts | Messages | Jouables | Résolus | Jours | R moyen | IC95 du R | Gagnants | Pire série | "
                      "Écart même moment | Excès placebos | IC de l'excès | Verdict |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for name, block in stats["providers"].items():
                for scenario, s in block["scenarios"].items():
                    lines.append(f"| {name} | {scenario} | {block['signals']} | {_fmt(block.get('halal_share'), True)} | "
                                 f"{s.get('n', 0)} | {s.get('days', 0)} | {_fmt(s.get('r_mean'))} | {s.get('r_ci95') or '—'} | "
                                 f"{_fmt(s.get('win_share'), True)} | {s.get('worst_streak', '—')} | {_fmt(s.get('same_moment_diff'))} | "
                                 f"{_fmt(s.get('placebo_excess'))} | {s.get('placebo_excess_ci') or '—'} | {block['verdict']} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
        if "mints" in stats:                                  # F3 : événements on-chain, achat contre placebos
            lines += [f"Créations inscrites : {stats['mints']} (ignorées, ponts : {stats['mints_ignored']} ; comptées : "
                      f"{stats['mints_usd']} M$) ; événements : {stats['events']} ({stats['by_status']}) ; décisions "
                      f"USDT/USDC : {stats['by_stablecoin']} ; en attente : {stats['pending']} ; trous : {stats['gaps']} ; "
                      f"latence médiane : {stats['latency_median_min'] if stats['latency_median_min'] is not None else '—'} min ; "
                      f"erreurs de source : {stats['source_errors']}.", "",
                      "| Coûts | Horizon | Événements | Rendement achat | Rendement placebos | Excès | Gagnants | IC de l'excès |",
                      "|---|---|---|---|---|---|---|---|"]
            for scenario, horizons in stats["scenarios"].items():
                for horizon, s in horizons.items():
                    lines.append(f"| {scenario} | {horizon} | {s.get('n', 0)} | {_fmt(s.get('event_r'), True)} | "
                                 f"{_fmt(s.get('placebo_r'), True)} | {_fmt(s.get('excess'), True)} | "
                                 f"{_fmt(s.get('win_share'), True)} | {s.get('excess_ci') or '—'} |")
            lines += ["", f"Verdicts : **{stats['verdict']}**.", ""]
            continue
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

