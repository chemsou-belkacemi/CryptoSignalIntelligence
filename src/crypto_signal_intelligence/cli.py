"""Interface en ligne de commande : affichage uniquement, aucune logique métier ici."""
from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from .config import load_settings

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="CryptoSignalIntelligence : données → recherche → analyse → validation → signaux TXT. "
                       "Aucune exécution d'ordre.")
console = Console()


def _setup_logging(settings, verbose: bool) -> None:
    logs = settings.root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "csi.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)


def _settings(verbose: bool = False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Configuration invalide :[/red] {exc}")
        raise typer.Exit(2) from None
    _setup_logging(settings, verbose)
    return settings


def _now() -> datetime:
    return datetime.now(UTC)


@app.command()
def doctor(offline: bool = typer.Option(False, help="Ne pas tester l'accès réseau public")):
    """Vérifie environnement, dépendances, configuration, droits, réseau et stockage."""
    from . import doctor as diagnostics
    settings = _settings()
    table = Table("Contrôle", "État", "Détail")
    failures = 0
    for check in diagnostics.run(settings, network=not offline):
        state = "[green]OK[/green]" if check.ok else ("[red]ÉCHEC[/red]" if check.blocking else "[yellow]AVERT.[/yellow]")
        failures += (not check.ok) and check.blocking
        table.add_row(check.name, state, check.detail)
    console.print(table)
    raise typer.Exit(1 if failures else 0)


@app.command()
def download(symbol: list[str] = typer.Option(None, help="Paire(s) ; défaut : toutes + BTCUSDT (contexte)"),
             timeframe: list[str] = typer.Option(None, help="Timeframe(s) ; défaut : setup et contexte"),
             recheck_archives: bool = typer.Option(False, help="Revérifie les checksums des archives déjà ingérées"),
             verbose: bool = False):
    """Télécharge/complète l'historique : archives vérifiées puis REST récent."""
    from .data.pipeline import download as run_download
    settings = _settings(verbose)
    symbols = [s.upper() for s in symbol] if symbol else list(dict.fromkeys([*settings.data.symbols, "BTCUSDT"]))
    frames = timeframe or [settings.data.setup_timeframe, settings.data.context_timeframe]
    for tf in frames:
        if tf not in settings.data.enabled_timeframes:
            console.print(f"[red]{tf} non activé[/red] (jointures temporelles non validées).")
            raise typer.Exit(2)
    now = _now()
    for sym in symbols:
        for tf in frames:
            with console.status(f"{sym} {tf}…") as status:
                summary = run_download(settings, sym, tf, now=now, recheck_archives=recheck_archives,
                                       progress=lambda name, s=status, sy=sym, t=tf: s.update(f"{sy} {t} : {name}"))
            report = summary.report
            if report is None:
                console.print(f"[bold]{sym} {tf}[/bold] : aucune bougie stockée")
                continue
            console.print(
                f"[bold]{sym} {tf}[/bold] : {report.rows} bougies {(report.first_open_time or '–')[:16]} → "
                f"{(report.last_open_time or '–')[:16]} | archives +{summary.archives_ingested} "
                f"(déjà {summary.archives_skipped}, révisées {summary.archives_revised}) | REST {summary.rest_rows} "
                f"| écarts réconciliés {summary.reconciled_changes} | quarantaine {summary.quarantined} "
                f"| trous {len(report.gaps)} ({report.missing_bars} bougies)")
            if summary.archives_missing:
                console.print(f"  archives non publiées (complétées par REST) : {', '.join(summary.archives_missing)}")


@app.command("download-derivatives")
def download_derivatives(dataset: list[str] = typer.Option(None, "--dataset",
                                                           help="funding | premium | metrics ; défaut : les trois"),
                         symbol: list[str] = typer.Option(None, "--symbol", help="Paire(s) ; défaut : l'univers"),
                         verbose: bool = False):
    """Historique PUBLIC du marché à terme USDⓈ-M (archives vérifiées par SHA-256) : financement, prime,
    intérêt ouvert et ratios. Information sur le positionnement ; aucun contrat n'est négocié (docs/DERIVATIVES.md)."""
    from .derivatives.history import download as run_download
    settings = _settings(verbose)
    symbols = [s.upper() for s in symbol] if symbol else None
    with console.status("marché à terme…") as status:
        summaries = run_download(settings, datasets=dataset or None, symbols=symbols, now=_now(),
                                 progress=lambda text: status.update(f"marché à terme : {text}"))
    for s in summaries:
        q = s.quality
        console.print(f"[bold]{s.dataset} {s.symbol}[/bold] : {q.get('rows', 0)} lignes {str(q.get('first'))[:16]} → "
                      f"{str(q.get('last'))[:16]} | archives +{s.ingested} (déjà {s.skipped}, révisées {s.revised}) "
                      f"| absentes {len(s.missing)} | rejetées {len(s.rejected)} | trous {q.get('gaps', 0)} "
                      f"(le plus long {q.get('largest_gap_hours')} h) | valeurs manquantes {q.get('missing_values', 0)}")
        for line in s.rejected[:5]:
            console.print(f"  [red]rejetée[/red] {line}")


@app.command("screen-derivatives")
def screen_derivatives(verbose: bool = False):
    """Criblage du positionnement sur le marché à terme (docs/DERIVATIVES.md) : 4 conditions × 3 horizons,
    DEVELOPMENT seulement, audit des fuites d'abord. Information ; aucun signal n'en découle."""
    from .research.derivatives_screen import IncompleteData, LeakAuditFailed, run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("criblage du marché à terme…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"criblage : {text}"))
    except (LeakAuditFailed, IncompleteData) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Criblage du marché à terme — {result.run_id}")
    console.print(f"Audit des fuites : réussi ({', '.join(result.leak_audit['checked_pairs'])}) ; essais : "
                  f"{result.n_trials} ; programme : {result.program_trials} ; seuil de coûts : {result.cost_hurdle_pct} % ; "
                  f"niveau des IC : {result.level:.2%}")
    for r in result.rows:
        mark = "[yellow]piste[/yellow]" if r.lead else "non"
        console.print(f"  {r.condition} {r.horizon_h} h : {r.events} événements ({r.pairs} paires) | brut "
                      f"{r.mean_return_pct} % {r.ci_return_pct} | excès {r.mean_excess_pct} % {r.ci_excess_pct} | "
                      f"transversal {r.mean_cross_excess_pct} % {r.ci_cross_excess_pct} | concentration paire "
                      f"{r.max_pair_share}, année {r.max_year_share} | {mark}")
    console.print("Une piste n'est pas un avantage : criblage sur données déjà vues (docs/DERIVATIVES.md).")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("admit-halal")
def admit_halal(verbose: bool = False):
    """Applique le screening halal (config/halal_screening.toml) aux paires USDT hors configuration : favorables
    ajoutées, défavorables refusées, douteuses ou inexploitables à décider (tableau de bord, onglet Suivi)."""
    from collections import Counter

    from .external.admission import admit_all
    settings = _settings(verbose)
    with console.status("vérification des paires sur Binance Spot…"):
        results = admit_all(settings, now=_now())
    for r in results:
        color = {"AJOUTEE": "green", "REFUSEE": "red", "A_DECIDER": "yellow"}.get(r["decision"], "dim")
        console.print(f"  [{color}]{r['decision']}[/{color}] {r['symbol']} — {r['reason']}")
    console.print(f"Bilan : {dict(Counter(r['decision'] for r in results))} (les paires ajoutées sont téléchargées "
                  "par la surveillance, une par cycle). Ce projet ne certifie rien : avis de sources publiques.")


@app.command("download-long")
def download_long_command(symbol: list[str] = typer.Option(None, "--symbol", help="Paire(s) ; défaut : l'univers de la configuration"),
                          research: bool = typer.Option(False, "--research", help="L'univers de recherche figé (40 paires, research/universe.py)"),
                          workers: int = typer.Option(6, help="Paires téléchargées en parallèle"),
                          verbose: bool = False):
    """Historique LONG (bougies 1 h depuis la cotation de chaque paire) dans un magasin séparé, pour la recherche
    à basse fréquence. Les protocoles déjà exécutés gardent leur magasin (depuis 2021-01)."""
    from .research.long_history import download_long
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    symbols = ([s.upper() for s in symbol] if symbol else
               list(RESEARCH_UNIVERSE) if research else list(settings.data.symbols))
    with console.status("historique long…") as status:
        rows = download_long(settings, symbols, now=_now(), workers=workers,
                             progress=lambda text: status.update(f"historique long : {text}"))
    for r in rows:
        if "error" in r:
            console.print(f"[red]{r['symbol']}[/red] : {r['error']}")
            continue
        console.print(f"[bold]{r['symbol']}[/bold] : cotée le {r['listed']}, {r['rows']} bougies 1 h "
                      f"{str(r['first'])[:10]} → {str(r['last'])[:16]} | archives +{r['archives']} "
                      f"(absentes {r['archives_missing']}) | trous {r['gaps']} ({r['missing_bars']} bougies) "
                      f"| quarantaine {r['quarantined']}")


@app.command("factors")
def factors_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                    verbose: bool = False):
    """Portefeuilles hebdomadaires (docs/FACTORS.md) : 18 règles fixes face à leur référence, sur l'historique long,
    DEVELOPMENT seulement, audit des fuites d'abord. Mesure seulement : aucun signal ni ordre n'en découle."""
    from .research.factors import DirtyCode, IncompleteData, LeakAuditFailed, run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("portefeuilles hebdomadaires…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"portefeuilles : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, IncompleteData, DirtyCode) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Portefeuilles hebdomadaires — {result.run_id}")
    coverage = result.coverage
    console.print(f"Audit des fuites : réussi ({len(result.leak_audit['decisions'])} décisions, "
                  f"{result.leak_audit['trials_checked']} portefeuilles) ; {coverage['decisions']} décisions du "
                  f"{coverage['first'][:10]} au {coverage['last'][:10]} ; essais : {result.n_trials} ; programme : "
                  f"{result.program_trials} ; niveau des IC : {result.level:.2%}")
    for key, b in result.benchmarks.items():
        console.print(f"  référence {key} : rendement annualisé {b['annual_return']}, Sharpe {b['sharpe']}, perte "
                      f"maximale {b['max_drawdown']} | défavorable : Sharpe {b['adverse']['sharpe']}")
    table = Table("Essai", "Réf.", "Sharpe", "Écart", "IC de l'écart", "Validations", "Défavorable", "Perte max.",
                  "Investi", "Piste")
    for r in result.rows:
        table.add_row(r["key"], r["benchmark"], f"{r['sharpe']:.2f}", f"{r['sharpe_diff']:+.2f}",
                      str(r["sharpe_diff_ci"]), f"{r['folds_better']}/7", f"{r['adverse_sharpe_diff']:+.2f}",
                      f"{r['max_drawdown']:.0%} (réf. {r['benchmark_max_drawdown']:.0%})",
                      f"{r['invested_share']:.0%}", "[yellow]à confirmer[/yellow]" if r["lead"] else "non")
    console.print(table)
    console.print(f"[bold]Verdict : {result.verdict}[/bold]")
    console.print("Une piste n'est pas un avantage démontré : 18 variantes comparées sur des données déjà parcourues, "
                  "univers de survivantes (docs/FACTORS.md §7 et §9).")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("screen-flow")
def screen_flow_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                        universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                        verbose: bool = False):
    """Étape 6 du plan (docs/SCREENING.md, criblage J) : flux d'ordres, offre nouvelle (veto) et MVRV on-chain, à 1, 7 et
    30 jours ; 15 essais, DEVELOPMENT seulement, audit des fuites d'abord."""
    from .research.factors import DirtyCode
    from .research.flow_screen import LeakAuditFailed, run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
    try:
        with console.status("criblage J…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"criblage J : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, DirtyCode, RuntimeError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Criblage J — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; seuil {result.cost_hurdle_pct} %")
    table = Table("Condition", "Horizon", "Événements", "Paires", "Rendement", "Excès", "IC95 excès", "Passe", "Veto justifié")
    for r in result.rows:
        table.add_row(r.condition, f"{r.horizon_h // 24} j", str(r.events), str(r.pairs), f"{r.mean_return_pct}", f"{r.mean_excess_pct}",
                      f"{r.ci95_excess_pct}", "oui" if r.beats_costs else "non", "oui" if r.veto_justified else "non")
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("screen-pivot")
def screen_pivot_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                         universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                         verbose: bool = False):
    """Étape 8 du plan (docs/SCREENING.md, criblage K) : rebond sur pivot bas confirmé et cassure de pivot haut confirmé, en
    bougies 4 h et 1 jour ; 4 essais, DEVELOPMENT seulement, audit des fuites d'abord."""
    from .research.factors import DirtyCode
    from .research.pivot_screen import LeakAuditFailed, run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
    try:
        with console.status("criblage K…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"criblage K : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, DirtyCode, RuntimeError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Criblage K — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; seuil {result.cost_hurdle_pct} %")
    table = Table("Condition", "Horizon", "Événements", "Paires", "Rendement", "Excès", "IC95 excès", "Paires > 0", "Passe")
    for r in result.rows:
        table.add_row(r.condition, f"{r.horizon_h} h", str(r.events), str(r.pairs), f"{r.mean_return_pct}", f"{r.mean_excess_pct}",
                      f"{r.ci95_excess_pct}", f"{r.pairs_positive_share}", "oui" if r.beats_costs else "non")
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("trend-daily")
def trend_daily_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                        universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                        verbose: bool = False):
    """Étape 4 du plan (docs/TREND_DAILY.md) : ensemble de canaux de Donchian journaliers, long seul, avec ou sans
    ciblage de volatilité réalisée, contre une allocation STATIQUE ; 2 essais, DEVELOPMENT seulement, audit d'abord."""
    from .research.factors import DirtyCode
    from .research.trend_daily import LeakAuditFailed, run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
        console.print(f"Univers : {len(symbols)} paires de recherche admises par le screening (sur {len(RESEARCH_UNIVERSE)}).")
    try:
        with console.status("tendance journalière…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"tendance journalière : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, DirtyCode, RuntimeError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Tendance journalière — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials}")
    table = Table("Modèle", "Scénario", "Rendement/an", "Volatilité", "Sharpe", "Sharpe déflaté", "Perte max.", "Transactions", "Frais", "Exposition")
    for scenario, models in result.models.items():
        for name, m in models.items():
            table.add_row(name, scenario, f"{(m['annual_return'] or 0) * 100:.1f} %", f"{(m['volatility'] or 0) * 100:.1f} %",
                          f"{m['sharpe']:.2f}", f"{m['deflated_sharpe'] if m['deflated_sharpe'] is not None else '—'}",
                          f"{m['max_drawdown'] * 100:.0f} %", str(m["trades"]), f"{m['fees_pct']:.1f} %", f"{m['average_exposure'] * 100:.0f} %")
    console.print(table)
    for name, verdict in result.verdicts.items():
        console.print(f"{name} (référence {verdict['reference']}) : {verdict['verdict']}")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("long-horizon")
def long_horizon_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                         universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal ; l'univers de recherche y est restreint"),
                         verbose: bool = False):
    """Lot 8 v2 (docs/LONG_HORIZON.md) : A (vote de 3 horizons + volatilité prévue, cible 50 %), A + funding si le
    filtre s'active assez, B (basse volatilité 6 mois, stop), contre une allocation STATIQUE au même panier, frais
    inclus ; 3 essais au plus, DEVELOPMENT seulement, audit d'abord."""
    from .research.factors import DirtyCode
    from .research.long_horizon import LeakAuditFailed, run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
        console.print(f"Univers : {len(symbols)} paires de recherche admises par le screening (sur {len(RESEARCH_UNIVERSE)}).")
    try:
        with console.status("horizons longs…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"horizons longs : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, DirtyCode, RuntimeError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Horizons longs — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials}")
    table = Table("Modèle", "Scénario", "Rendement/an", "Volatilité", "Sharpe", "Sharpe déflaté", "Perte max.",
                  "Durée de perte", "Transactions", "Frais", "Exposition")
    for scenario, models in result.models.items():
        for key, m in models.items():
            table.add_row(key, scenario, f"{m['annual_return']:.1%}" if m["annual_return"] is not None else "–",
                          f"{m['volatility']:.1%}", f"{m['sharpe']:.2f}", str(m["deflated_sharpe"]),
                          f"{m['max_drawdown']:.0%}", f"{m['drawdown_days']} j", str(m["trades"]),
                          f"{m['fees_pct']:.2f} %", f"{m['average_exposure']:.0%}")
    console.print(table)
    for key, v in result.verdicts.items():
        console.print(f"[bold]{key}[/bold] (référence {v['reference']}) : {v['verdict']}")
    console.print("Unlocks : " + result.coverage["unlock_filter"])
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("volatility")
def volatility_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                       verbose: bool = False):
    """Prévision de volatilité (docs/VOLATILITY.md) : 5 modèles contre « volatilité des 7 derniers jours » à 1, 3
    et 7 jours, 40 paires, historique long, DEVELOPMENT seulement, audit des fuites d'abord. Erreur de prévision
    seulement : ni direction, ni rentabilité, ni ordre."""
    from .research.volatility import DirtyCode, IncompleteData, LeakAuditFailed, run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("prévision de volatilité…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"volatilité : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, IncompleteData, DirtyCode) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Prévision de volatilité — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; "
                  f"niveau des IC : {result.level:.2%}")
    table = Table("Modèle", "Horizon", "Jours", "Paires", "QLIKE", "Référence", "Écart", "IC de l'écart", "Années",
                  "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} j", str(r.days), str(r.pairs), str(r.qlike), str(r.qlike_baseline),
                      str(r.qlike_diff), str(r.ci_qlike_diff), str(r.years_better), str(r.pairs_better_share),
                      "[yellow]oui[/yellow]" if r.useful else "non")
    console.print(table)
    console.print(f"[bold]Verdict : {result.verdict}[/bold] ; retenu par horizon : {result.selected}")
    console.print("Une prévision utile sur DEVELOPMENT n'est pas validée : elle ne dit rien de la direction ni de la "
                  "rentabilité (docs/VOLATILITY.md).")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("volatility-v2")
def volatility_v2_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                          verbose: bool = False):
    """Prévision de volatilité, protocole v2 (docs/VOLATILITY.md § 14) : 4 candidats (combinaison, semi-variances,
    Parkinson, DVOL) contre le modèle EN SERVICE à 1, 3 et 7 jours, DEVELOPMENT seulement, audit des fuites d'abord.
    Erreur de prévision seulement : ni direction, ni rentabilité, ni ordre."""
    from .research.volatility import DirtyCode, IncompleteData, LeakAuditFailed
    from .research.volatility_v2 import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("prévision de volatilité v2…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"volatilité v2 : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, IncompleteData, DirtyCode) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Prévision de volatilité v2 — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; "
                  f"niveau des IC : {result.level:.2%}")
    table = Table("Candidat", "Horizon", "Jours", "Paires", "QLIKE", "Service", "Écart", "IC de l'écart", "Années",
                  "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} j", str(r.days), str(r.pairs), str(r.qlike), str(r.qlike_baseline),
                      str(r.qlike_diff), str(r.ci_qlike_diff), f"{r.years_better}/{getattr(r, 'years_needed', 0)}", str(r.pairs_better_share),
                      "[yellow]oui[/yellow]" if r.useful else "non")
    console.print(table)
    console.print(f"[bold]Verdict : {result.verdict}[/bold] ; retenu par horizon : {result.selected}")
    console.print("Un candidat meilleur sur DEVELOPMENT ne remplace rien automatiquement (docs/VOLATILITY.md § 14).")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("volatility-hourly")
def volatility_hourly_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                              verbose: bool = False):
    """Prévision de volatilité à toute heure, protocole v3 (docs/VOLATILITY.md § 16) : HAR et LightGBM avec profil
    heure × jour contre la règle « 24 dernières heures », à 4 h et 24 h après chaque clôture 1 h ; 4 comparaisons,
    DEVELOPMENT seulement, audit des fuites d'abord. Erreur de prévision seulement : ni direction, ni rentabilité."""
    from .research.volatility import DirtyCode, IncompleteData, LeakAuditFailed
    from .research.volatility_hourly import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("volatilité à toute heure…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"volatilité horaire : {text}"),
                         allow_dirty=allow_dirty)
    except (LeakAuditFailed, IncompleteData, DirtyCode) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Volatilité à toute heure — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; "
                  f"niveau des IC : {result.level:.2%}")
    table = Table("Modèle", "Horizon", "Jours", "Paires", "QLIKE", "Référence", "Écart", "IC de l'écart", "Années", "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} h", str(r.days), str(r.pairs), str(r.qlike), str(r.qlike_baseline),
                      str(r.qlike_diff), str(r.ci_qlike_diff), str(r.years_better), str(r.pairs_better_share),
                      "[yellow]oui[/yellow]" if r.useful else "non")
    console.print(table)
    console.print(f"[bold]Verdict : {result.verdict}[/bold] ; retenu par horizon : {result.selected}")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("quantiles")
def quantiles_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                      verbose: bool = False):
    """Intervalles de rendement (docs/QUANTILES.md) : LightGBM quantile et conforme adaptatif contre « σ̂ en service ×
    quantiles empiriques », quantiles 5/25/75/95 % à 1, 3 et 7 jours ; 6 comparaisons, DEVELOPMENT seulement, audit
    des fuites d'abord. Perte de prévision seulement : ni direction, ni rentabilité, ni ordre."""
    from .research.quantiles import LeakAuditFailed, run
    from .research.volatility import DirtyCode, IncompleteData
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("intervalles de rendement…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"intervalles : {text}"), allow_dirty=allow_dirty)
    except (LeakAuditFailed, IncompleteData, DirtyCode) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Intervalles de rendement — {result.run_id}")
    console.print(f"Audit des fuites : réussi ; essais : {result.n_trials} ; programme : {result.program_trials} ; niveau des IC : {result.level:.2%}")
    table = Table("Modèle", "Horizon", "Jours", "Paires", "Pinball", "Référence", "Écart", "IC de l'écart", "Années 90 %", "Années 50 %", "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} j", str(r.days), str(r.pairs), str(r.pinball), str(r.pinball_baseline), str(r.pinball_diff),
                      str(r.ci_pinball_diff), str(r.years_covered_90), str(r.years_covered_50), str(r.pairs_better_share),
                      "[yellow]oui[/yellow]" if r.useful else "non")
    console.print(table)
    console.print(f"[bold]Verdict : {result.verdict}[/bold] ; retenu par horizon : {result.selected}")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("positive-control")
def positive_control_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                             universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                             verbose: bool = False):
    """Contrôle positif de la mesure (docs/POSITIVE_CONTROL.md) : un avantage synthétique est planté dans les vraies
    données ; on mesure si les chaînes criblage, décisions ML et walk-forward le retrouvent, et leurs fausses alarmes.
    0 essai au programme, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.positive_control import run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
    try:
        with console.status("contrôle positif…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"contrôle positif : {text}"),
                         allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Contrôle positif — {result.run_id}")
    table = Table("Chaîne", "Groupe", "Avantage planté", "Détection", "Passe / estimation")
    for r in result.screen:
        table.add_row("criblage", f"{r['horizon_days']} j", f"{r['delta_pct']} %", f"{r['detection_rate']:.0%}",
                      f"passe {r['pass_rate']:.0%} ; excès estimé {r['mean_excess_estimate_pct']} % ; brut {r['mean_raw_return_pct']} %")
    for r in result.ml:
        table.add_row("décisions ML", r["system"], f"ρ {r['rho']}", f"{r['detection_rate']:.0%}", f"corrélation de rang réalisée {r['realized_rank_ic']}")
    for r in result.walk_forward:
        table.add_row("walk-forward", r["strategy"], f"{r['delta_r']} R", f"{r['detection_rate']:.0%}", f"{r['trades']} trades")
    console.print(table)
    for label, info in result.ml_real.items():
        console.print(f"Modèle réel {label} : corrélation de rang {info['rank_ic']} {info['rank_ic_ci95']} ; décile haut − bas {info['top_minus_bottom_pct']} %")
    console.print(f"Tailles minimales détectables (80 %) : {result.minimum_detectable}")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("information-report")
def information_report_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                               verbose: bool = False):
    """Registre unifié des prédictions hors échantillon et rapport d'information DESCRIPTIF (docs/INFORMATION_REPORT.md) :
    décisions ML, trades des walk-forwards, prévisions de volatilité. Aucun ajustement, aucun verdict, 0 essai."""
    from .research.factors import DirtyCode
    from .research.information_report import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("rapport d'information…") as status:
            report = run(settings, now=_now(), progress=lambda text: status.update(f"information : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Rapport d'information — {report.run_id}")
    table = Table("Source ML", "Lignes", "Corrélation de rang", "IC95", "Décile haut − bas", "Décile haut : brut / net / excès", "Trades entrés : net / excès")
    for name, s in report.ml.items():
        top, entered = s["top_decile"], s["entered"]
        table.add_row(name, str(s["rows"]), str(s["rank_ic"]), str(s["rank_ic_ci95"]), f"{s['top_minus_bottom_pct']} %",
                      f"{top['gross_pct']} / {top['net_pct']} / {top['excess_pct']} % {top['excess_ci95_pct']}",
                      f"{entered['n']} : {entered['net_pct']} / {entered['excess_pct']} %")
    console.print(table)
    table = Table("Walk-forward", "Central R [IC95]", "Défavorable R", "Stress R", "Brut / net %", "Par tendance")
    for name, s in report.walk_forward.items():
        sc = s["scenarios"]
        table.add_row(name, f"{sc['central']['r_mean']} {sc['central']['r_ci95']}", str(sc["adverse"]["r_mean"]), str(sc["stress"]["r_mean"]),
                      f"{sc['central']['gross_return_pct']} / {sc['central']['net_return_pct']}",
                      " · ".join(f"{k} {v['r_mean']} ({v['trades']})" for k, v in s["by_trend"].items()))
    console.print(table)
    console.print(f"Volatilité : {report.volatility}")
    console.print(f"Cellules lues : {report.cells} ; fausses alarmes attendues à 5 % sans information : {report.expected_false_alarms}. "
                  "Lecture descriptive : aucune piste n'est retenue sans pré-inscription et données non vues.")
    console.print(f"Rapport : {settings.reports_dir / report.run_id / 'summary.json'}")


@app.command("interval-calibration")
def interval_calibration_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                                 universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                                 verbose: bool = False):
    """Calibrage des intervalles sous la nulle (docs/POSITIVE_CONTROL.md § 6) : conditions réelles groupées, histoire
    rééchantillonnée par blocs ; méthode actuelle contre bootstrap stationnaire (arch). 0 essai."""
    from .research.factors import DirtyCode
    from .research.interval_calibration import run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
    try:
        with console.status("calibrage des intervalles…") as status:
            result = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"calibrage : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Calibrage des intervalles — {result.run_id}")
    table = Table("Chaîne", "Horizon / système", "Condition", "Planté", "Méthode", "Bornes basses > 0", "Bornes hautes < 0")
    for r in result.screen:
        table.add_row("criblage", f"{r['horizon_days']} j", r["condition"], f"{r['planted_pct']} %", r["method"], f"{r['low_above_zero']:.1%}", f"{r['high_below_zero']:.1%}")
    for r in result.ml:
        table.add_row("ML", r["system"], "score aléatoire", f"ρ {r['rho']}", r["method"], f"{r['low_above_zero']:.1%}", f"{r['high_below_zero']:.1%}")
    console.print(table)
    console.print(f"Choix (règle déclarée) : {result.choice}")
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("figures-backfill")
def figures_backfill_command(verbose: bool = False):
    """Remplit le magasin des bougies 1 h du test F15 (paires de la liste halal en vigueur, depuis le 2025-08-01 ;
    archives vérifiées puis REST). À lancer une fois avant le démarrage de F15, dans le conteneur de surveillance."""
    from .data.pipeline import download
    from .forward import f15
    from .forward.halal import admitted
    settings = _settings(verbose)
    symbols = sorted(admitted(settings).symbols)
    failed = []
    for symbol in symbols:
        try:
            download(f15.figure_settings(settings), symbol, "1h")
            console.print(f"{symbol} : prêt")
        except Exception as exc:  # noqa: BLE001 - une paire en échec n'arrête pas les autres
            failed.append(symbol)
            console.print(f"[red]{symbol} : {type(exc).__name__}[/red]")
    console.print(f"Paires : {len(symbols)} ; échecs : {len(failed)} {failed}")


@app.command("minute-history")
def minute_history_command(verbose: bool = False):
    """Télécharge les bougies 1 MINUTE des 40 paires de recherche (magasin séparé, archives vérifiées), pour la
    volatilité réalisée fine (phase 10.1). Données publiques seulement ; environ 6 Go."""
    from .research.minute_history import download_minutes
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    results = download_minutes(settings, list(RESEARCH_UNIVERSE), progress=lambda text: console.print(text))
    failed = [r for r in results if r.get("error")]
    console.print(f"Paires : {len(results)} ; échecs : {len(failed)} {[r['symbol'] for r in failed]}")


@app.command("pit-universe")
def pit_universe_command(verbose: bool = False, no_hourly: bool = typer.Option(False, "--no-hourly", help="Sans télécharger l'historique 1 h des paires hors univers")):
    """Univers à date (docs/UNIVERSE_PIT.md) : recensement des paires USDT cotées et retirées, bougies journalières,
    top 40 mensuel causal, historique 1 h des paires hors univers. Données publiques, lecture seule, DEVELOPMENT."""
    from .research.pit_universe import build
    settings = _settings(verbose)
    _heavy_job(settings)
    with console.status("univers à date…") as status:
        report = build(settings, progress=lambda text: status.update(f"univers à date : {text}"), download_hourly=not no_hourly)
    cov = report["coverage"]
    console.print(f"Paires USDT recensées : {report['symbols_listed']} ; gardées : {report['symbols_kept']} ; exclusions : {report['excluded']}")
    console.print(f"Places du top 40 (mois × place) : {cov['member_months']} ; parts : {cov['shares']}")
    console.print(f"Paires hors univers entrées au moins une fois : {len(cov['extra_symbols'])} (dont retirées ou renommées : {len(cov['extra_delisted'])})")
    failed = [d for d in report.get("hourly_downloads", []) if d.get("error")]
    console.print(f"Historique 1 h téléchargé : {len(report.get('hourly_downloads', [])) - len(failed)} ; échecs : {len(failed)}")


@app.command("screen-pivot-pit")
def screen_pivot_pit_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                             verbose: bool = False):
    """Criblage K sur l'univers à date (docs/UNIVERSE_PIT.md § 2) : K1 et K2, 4 h et 1 jour, top 40 du mois de
    l'événement, paires retirées comprises. 4 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.pit_universe import run_k_pit
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("criblage K à date…") as status:
            payload = run_k_pit(settings, now=_now(), progress=lambda text: status.update(f"criblage K à date : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Criblage K à date — {payload['run_id']}")
    table = Table("Condition", "Horizon", "Événements", "Paires", "Rendement", "Excès", "IC95 excès", "Paires > 0", "Passe")
    for r in payload["rows"]:
        table.add_row(r["condition"], f"{r['horizon_h']} h", str(r["events"]), str(r["pairs"]), str(r["mean_return_pct"]), str(r["mean_excess_pct"]),
                      str(r["ci95_excess_pct"]), str(r["pairs_positive_share"]), "oui" if r["beats_costs"] else "non")
    console.print(table)
    console.print(f"Paires sans historique 1 h : {payload['missing_hourly']} ; programme : {payload['program_trials']} essais")


@app.command("ticks")
def ticks_command(verbose: bool = False):
    """Coûts d'exécution mesurés sur les transactions (docs/TICKS.md) : écart, petit achat au marché après 1, 5 et 40 s,
    remplissage réel d'un ordre limite ; archives publiques aggTrades, 6 paires × 12 journées. 0 essai."""
    from .research.ticks import run
    settings = _settings(verbose)
    _heavy_job(settings)
    with console.status("transactions…") as status:
        result = run(settings, progress=lambda text: status.update(f"transactions : {text}"))
    table = Table("Paire", "Écart médian (pb)", "Achat +1 s (pb)", "Achat +40 s (pb)", "Limite 0 % / 15 min : bougie · traversé · touché seul.")
    for symbol in result.pairs:
        fill = result.limit_fill.get(symbol, {}).get("0.0_15", {})
        table.add_row(symbol, str(result.spread_bps[symbol]["median"]), str(result.market_buy_bps[symbol]["1s"]["mean"]),
                      str(result.market_buy_bps[symbol]["40s"]["mean"]),
                      f"{fill.get('bougie_dit_rempli')} · {fill.get('traverse_certain')} · {fill.get('touche_seulement')}")
    console.print(table)
    console.print(f"Erreurs : {result.errors or 'aucune'}")


@app.command("fit-control")
def fit_control_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                        verbose: bool = False):
    """Contrôle positif de l'ajustement (docs/POSITIVE_CONTROL.md § 7) : LightGBM et une régression linéaire retrouvent-ils
    une variable faiblement informative plantée parmi les variables du lot 7 ? 0 essai."""
    from .research.factors import DirtyCode
    from .research.fit_control import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("contrôle de l'ajustement…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"ajustement : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("ρ planté", "Modèle", "Variable seule (plafond)", "Hors échantillon", "IC95", "Vu")
    for r in result.rows:
        table.add_row(str(r["rho"]), r["model"], str(r["planted_alone"]["rank_ic"]), str(r["oos"]["rank_ic"]), str(r["oos"]["ci95"]),
                      "oui" if r["oos"]["detected"] else "non")
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("volatility-v4")
def volatility_v4_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                          verbose: bool = False):
    """Prévision de volatilité, protocole v4 (docs/VOLATILITY.md § 17) : prévisions en service réétalonnées et GARCH(1,1)
    contre le service, à 1, 3 et 7 jours ; 6 comparaisons, DEVELOPMENT seulement."""
    from .research.volatility import DirtyCode
    from .research.volatility_v4 import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("volatilité v4…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"volatilité v4 : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Candidat", "Horizon", "Jours", "Paires", "QLIKE", "Service", "Écart", "IC", "Années", "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} j", str(r.days), str(r.pairs), str(r.qlike), str(r.qlike_baseline), str(r.qlike_diff),
                      str(r.ci_qlike_diff), f"{r.years_better}/{getattr(r, 'years_needed', 0)}", str(r.pairs_better_share), "oui" if r.useful else "non")
    console.print(table)
    console.print(f"Verdict : {result.verdict} ; retenu : {result.selected} ; rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("volatility-v5")
def volatility_v5_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ, consomme l'essai unique"),
                          verbose: bool = False):
    """Prévision de volatilité, protocole v5 (docs/VOLATILITY.md § 20) : HAR sur la volatilité réalisée des bougies de
    1 minute (5 min et 1 min) contre le service ; 6 comparaisons, DEVELOPMENT seulement."""
    from .research.volatility import DirtyCode
    from .research.volatility_rv import IncompleteMinutes, run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("volatilité v5…") as status:
            result = run(settings, now=_now(), progress=lambda text: status.update(f"volatilité v5 : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError, IncompleteMinutes) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Candidat", "Horizon", "Jours", "Paires", "QLIKE", "Service", "Écart", "IC", "Années", "Paires mieux", "Utile")
    for r in result.rows:
        table.add_row(r.model, f"{r.horizon_days} j", str(r.days), str(r.pairs), str(r.qlike), str(r.qlike_baseline),
                      str(r.qlike_diff), str(r.ci_qlike_diff), str(r.years_better), str(r.pairs_better_share), "oui" if r.useful else "non")
    console.print(table)
    console.print(f"Verdict : {result.verdict} ; retenu : {result.selected} ; rapport : {settings.reports_dir / result.run_id / 'summary.json'}")


@app.command("meteo-historique")
def meteo_history_command(force: bool = typer.Option(False, "--force", help="Recalculer même si l'historique est "
                                                     "complet ou si une tentative a eu lieu il y a moins de 20 h"),
                          verbose: bool = False):
    """Feu de protection du marché (docs/METEO_PROTECTION.md) : calcule UNE fois l'historique du rang de volatilité
    (365 jours de prévisions H24 de BTC, mêmes fonctions gelées que F12). Travail lourd (≈ 1 min, ≈ 0,9 Go) : à lancer
    dans le conteneur tools, jamais dans la surveillance. Lecture des bougies locales seulement, aucun ordre."""
    from .risk.market_light import ensure_vol_history
    settings = _settings(verbose)
    _heavy_job(settings)
    with console.status("historique du rang de volatilité…"):
        out = ensure_vol_history(settings, now=_now(), force=force)
    if out is None:
        console.print("Rien à faire : historique déjà complet, ou tentative de moins de 20 h (--force pour recalculer).")
        return
    console.print(f"Historique du rang : {out['values']} jours calculés ({out['before']} présents avant).")


@app.command("volatility-confirm")
def volatility_confirm_command(
        rehearsal: bool = typer.Option(False, "--rehearsal", help="Répétition sur la fin de DEVELOPMENT (aucune donnée finale lue, rien compté)"),
        i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                     help="Lecture UNIQUE de la période finale (enregistrée, jamais refaite)"),
        allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Répétition locale sur du code non commité"),
        verbose: bool = False):
    """Confirmation des prévisions de volatilité sur la période finale réservée (docs/VOLATILITY.md § 18) : 5
    comparaisons déjà choisies sur DEVELOPMENT, une seule lecture, enregistrée avant de lire."""
    from .research.protocol import FinalTestLocked
    from .research.volatility import DirtyCode, IncompleteData, LeakAuditFailed
    from .research.volatility_confirm import run
    settings = _settings(verbose)
    if allow_dirty and not rehearsal:
        console.print("[red]--allow-dirty n'est permis que pour la répétition.[/red]")
        raise typer.Exit(2)
    _heavy_job(settings)
    try:
        with console.status("confirmation de la volatilité…") as status:
            result = run(settings, now=_now(), allow_final_test=i_understand_final_test, rehearsal=rehearsal,
                         progress=lambda text: status.update(f"confirmation : {text}"), allow_dirty=allow_dirty)
    except (FinalTestLocked, DirtyCode, LeakAuditFailed, IncompleteData) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    title = "RÉPÉTITION sur DEVELOPMENT (rien compté)" if result.rehearsal else "Période finale (lecture unique)"
    table = Table("Ordre", "Modèle", "Référence", "Horizon", "Jours", "Paires", "Écart QLIKE", "IC 95 %", "Par année",
                  "Paires mieux", "Issue propre", "Verdict (séquence)", title=title)
    for v in result.verdicts:
        table.add_row(v.key, v.model, v.reference, v.horizon, str(v.days), str(v.pairs), str(v.qlike_diff), str(v.ci),
                      str(v.by_year), str(v.pairs_better_share), v.outcome, v.verdict)
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / result.run_id / 'summary.json'}"
                  + ("" if result.rehearsal else f" ; consultations de la période finale (tout le programme) : {result.consultations_total}"))


@app.command("screen-seasonality")
def screen_seasonality_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                               universe_file: str = typer.Option(None, "--universe-file", help="JSON {\"symbols\": [...]} : paires admises par le screening halal"),
                               verbose: bool = False):
    """Criblage S (docs/SCREENING.md) : week-end, lundi, tournant du mois, financement, expiration d'options, séances,
    meilleure heure apprise ; 8 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.seasonality_screen import run
    from .research.universe import RESEARCH_UNIVERSE
    settings = _settings(verbose)
    _heavy_job(settings)
    symbols = None
    if universe_file:
        with open(universe_file, encoding="utf-8") as handle:
            admitted = set(json.load(handle).get("symbols", []))
        symbols = [s for s in RESEARCH_UNIVERSE if s in admitted]
    try:
        with console.status("criblage S…") as status:
            payload = run(settings, now=_now(), symbols=symbols, progress=lambda text: status.update(f"criblage S : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    console.rule(f"Criblage S — {payload['run_id']}")
    table = Table("Condition", "Durée", "Événements", "Rendement", "Excès", "IC95 excès", "Paires > 0", "Années > 0", "Passe")
    for r in payload["rows"]:
        table.add_row(r["condition"], f"{r['horizon_h']} h", str(r["events"]), str(r["mean_return_pct"]), str(r["mean_excess_pct"]),
                      str(r["ci95_excess_pct"]), str(r["pairs_positive_share"]), str(r["years_positive_share"]), "oui" if r["beats_costs"] else "non")
    console.print(table)
    console.print(f"Programme : {payload['program_trials']} essais")


@app.command("grid-dca")
def grid_dca_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                     point_in_time: bool = typer.Option(False, "--point-in-time", help="Univers à date (top 40 du mois, paires retirées comprises)"),
                     verbose: bool = False):
    """Grille et DCA (docs/GRID_DCA.md) : quatre variantes figées contre « garder » et les liquidités, par mois, frais
    maker, ordres remplis seulement si le prix les traverse ; 4 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.grid_dca import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("grille et DCA…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"grille et DCA : {text}"), allow_dirty=allow_dirty,
                          point_in_time=point_in_time)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Variante", "Coûts", "Paire-mois", "Rendement %", "IC", "Garder %", "Écart %", "IC écart", "Pire mois % (garder)", "Exposition")
    for variant, scenarios in payload["summary"].items():
        for scenario, s in scenarios.items():
            table.add_row(variant, scenario, str(s["pair_months"]), str(s["ret_pct"]), str(s["ret_ci_pct"]), str(s["hold_pct"]), str(s["excess_vs_hold_pct"]),
                          str(s["excess_ci_pct"]), f"{s['worst_month_pct']} ({s['hold_worst_month_pct']})", str(s["mean_max_exposure"]))
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("cnn-charts")
def cnn_charts_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                       verbose: bool = False):
    """CNN sur images de graphiques (docs/CNN.md) : images de 20 journées des paires du top 40 à date, 8 plus fortes
    probabilités contre le panier ; un essai, DEVELOPMENT seulement. Demande l'extra `cnn` (torch)."""
    from .research.cnn_charts import run
    from .research.factors import DirtyCode
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("CNN sur images…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"CNN : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError, ModuleNotFoundError, ValueError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Portefeuille", "Semaines", "Moyenne/sem. %", "Sharpe", "Perte max %", "Excès/sem. %", "IC excès", "Années > 0", "Rotation")
    for name, r in payload["rows"].items():
        table.add_row(name, str(r["weeks"]), str(r["weekly_mean_pct"]), str(r["sharpe"]), str(r["max_drawdown_pct"]),
                      str(r["excess_weekly_pct"]), str(r["excess_ci_pct"]), r["years_positive"], str(r["turnover_weekly"]))
    console.print(table)
    console.print(f"Classement : {payload['ranking']}")
    console.print(f"Verdict : {payload['verdict']} ; rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; "
                  f"programme : {payload['program_trials']} essais")


@app.command("context-backfill")
def context_backfill_command(only: list[str] = typer.Option(None, "--only", help="Séries à télécharger (toutes par défaut)"),
                             archives: bool = typer.Option(True, help="Archives journalières des ratios BTC et ETH depuis 2020-09"),
                             verbose: bool = False):
    """Télécharge une fois l'historique GRATUIT des données de contexte (docs/CONTEXTE.md) dans data/context/ :
    information seulement, aucune influence sur un test en cours. Lignes HISTORIQUE (valeurs telles que publiées
    aujourd'hui, révisions comprises)."""
    from .context.collect import backfill
    settings = _settings(verbose)
    _heavy_job(settings)
    out = backfill(settings, now=_now(), only=only or None, archives=archives, progress=lambda text: console.print(text))
    table = Table("Série", "Lignes", "Clés présentes", "Erreurs")
    for name, r in out.items():
        if isinstance(r, dict):
            table.add_row(name, str(r.get("rows", "—")), str(r.get("present", "—")), str(r.get("error") or len(r.get("errors") or {})))
    console.print(table)


@app.command("context-status")
def context_status_command(verbose: bool = False):
    """État du magasin des données de contexte : séries, lignes, première et dernière date, relevés du jour."""
    from .context.collect import load_state
    from .context.store import summary
    settings = _settings(verbose)
    table = Table("Série", "Sorte", "Lignes", "Clés", "Première", "Dernière")
    for r in summary(settings):
        table.add_row(r["series"], r["kind"], str(r["rows"]), str(r["keys"]), r["first"], r["last"])
    console.print(table)
    days = load_state(settings).get("days", {})
    if days:
        last = sorted(days)[-1]
        console.print(f"Dernier relevé : {last} ; séries réussies : {len(days[last]['done'])} ; erreurs : {days[last]['errors'] or 'aucune'}")


@app.command("level-reaction")
def level_reaction_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                           verbose: bool = False):
    """Supports et résistances mécaniques contre niveaux placebo (docs/NIVEAUX.md) : rejet et cassure, 1 h et 4 h,
    40 paires de recherche ; 4 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.level_reaction import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("niveaux…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"niveaux : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Unité · événement", "Réels", "Placebos", "Taux réel", "Taux placebo", "Écart", "IC", "Années +", "Paires +", "Verdict")
    for name, r in payload["rows"].items():
        table.add_row(name, str(r.get("real_events")), str(r.get("placebo_events")), str(r.get("real_rate")), str(r.get("placebo_rate")),
                      str(r.get("diff")), str(r.get("ci")), str(r.get("years_positive")), str(r.get("pairs_positive_share")),
                      r.get("verdict", "descriptif"))
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("context-screen")
def context_screen_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                           verbose: bool = False):
    """Données de contexte contre direction de BTC et ETH (docs/CONTEXTE_PREDICTION.md) : 7 variables, 1, 3 et
    7 jours ; 21 essais, DEVELOPMENT seulement."""
    from .research.context_screen import run
    from .research.factors import DirtyCode
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("contexte…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"contexte : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Variable · horizon", "Jours", "Tiers haut %", "Tiers bas %", "Tous %", "Écart %", "IC", "Corr. rang",
                  "Moitiés", "Entrée retardée", "Verdict")
    for name, r in payload["rows"].items():
        table.add_row(name, str(r.get("days")), str(r.get("mean_top_pct")), str(r.get("mean_bottom_pct")), str(r.get("mean_all_pct")),
                      str(r.get("diff_pct")), str(r.get("ci_pct")), str(r.get("rank_corr")), str(r.get("diff_halves_pct")),
                      str(r.get("diff_delayed_entry_pct")), r["verdict"])
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("figures-history")
def figures_history_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                            workers: int = typer.Option(4, "--workers", help="Paires traitées en parallèle (sans effet sur le résultat)"),
                            verbose: bool = False):
    """Figures du détecteur de F15, éléments ICT/SMC pris seuls et divergences RSI rejoués sur DEVELOPMENT, contre
    placebos (docs/FIGURES_HISTORIQUE.md) : 19 essais."""
    from .research.factors import DirtyCode
    from .research.figures_history import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("figures…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"figures : {text}"),
                          allow_dirty=allow_dirty, workers=workers)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Méthode", "Exécutées", "R moyen", "IC", "Excès (frais égaux)", "IC", "R défav.", "Excès défav. IC",
                  "TP1 / placebos", "Verdict")
    for name, row in payload["rows"].items():
        c, a = row["scenarios"]["central"], row["scenarios"]["defavorable"]
        tp1 = f"{c.get('tp_reached', {}).get('TP1')} / {c.get('placebo_tp1')}" if c.get("n") else "-"
        table.add_row(name, str(c.get("n")), str(c.get("r_mean")), str(c.get("r_ci")), str(c.get("excess_adj_mean")),
                      str(c.get("excess_adj_ci")), str(a.get("r_mean")), str(a.get("excess_adj_ci")), tp1, row["verdict"])
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("double-management")
def double_management_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                              verbose: bool = False):
    """Gestion du propriétaire (60 % au TP1, 5 objectifs, stop à l'entrée puis au TP précédent) rejouée sur les doubles
    creux de la première exécution (docs/FIGURES_HISTORIQUE.md) : 2 essais."""
    from .research.double_management import run
    from .research.factors import DirtyCode
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("double creux…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"double creux : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Répartition", "n", "R moyen", "IC", "Tiers", "Écart vs tiers IC", "Excès (frais égaux) IC", "R défav.",
                  "TP1..TP5", "% / trade", "Verdict")
    for name, row in payload["rows"].items():
        c, a = row["scenarios"]["central"], row["scenarios"]["defavorable"]
        tps = "/".join(f"{v:.0%}" for v in c["tp_reached"].values())
        table.add_row(name, str(c["n"]), str(c["r_mean"]), str(c["r_ci"]), str(c["r_thirds_mean"]), str(c["vs_thirds_ci"]),
                      str(c["excess_adj_ci"]), str(a["r_mean"]), tps, str(c["pct_per_trade_mean"]), row["verdict"])
    console.print(table)
    console.print(f"{payload['trades']} transactions, {payload['trades_per_day']} par jour calendaire ; rapport : "
                  f"{settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("double-stops")
def double_stops_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                         verbose: bool = False):
    """Stop resserré sur le double creux (moitié au toucher, clôture à 0,4, −3 % fixe ; docs/FIGURES_HISTORIQUE.md) :
    3 essais."""
    from .research.double_stops import run
    from .research.factors import DirtyCode
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("stops…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"stops : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Variante", "Risque méd. %", "R / risque", "IC", "Écart vs réf. % pos. IC", "Excès IC", "Stop avant TP1",
                  "Perte moy. %", "% position", "Verdict")
    for name, row in payload["rows"].items():
        c = row["scenarios"]["central"]
        table.add_row(name, str(c["risk_pct_median"]), str(c["r_mean"]), str(c["r_ci"]), str(c["vs_reference_pct_ci"]),
                      str(c["excess_adj_ci"]), f"{c['stopped_before_tp1']:.0%}", str(c["stopped_loss_pct_mean"]),
                      str(c["pct_position_mean"]), row["verdict"])
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("trendline-confirmation")
def trendline_confirmation_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                                   workers: int = typer.Option(4, "--workers", help="Paires traitées en parallèle (sans effet sur le résultat)"),
                                   verbose: bool = False):
    """Cassures de ligne de tendance en 1 h sur 214 paires jamais utilisées, placebos tirés sur tout l'historique
    (docs/LIGNES_DE_TENDANCE.md) : 2 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.trendline_confirmation import IncompleteMinutes, run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("lignes de tendance…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"lignes de tendance : {text}"),
                          allow_dirty=allow_dirty, workers=workers)
    except (DirtyCode, FileNotFoundError, IncompleteMinutes) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    result = payload["result"]
    table = Table("Scénario", "Transactions", "Paires", "R moyen", "IC", "Excès (frais égaux)", "IC", "TP1 / placebos")
    for name, c in result["scenarios"].items():
        table.add_row(name, str(c.get("n")), str(c.get("pairs")), str(c.get("r_mean")), str(c.get("r_ci")),
                      str(c.get("uexcess_adj_mean")), str(c.get("uexcess_adj_ci")),
                      f"{c.get('tp_reached', {}).get('TP1')} / {c.get('uplacebo_tp', {}).get('TP1')}")
    console.print(table)
    console.print(f"Décision : piste {result['decision']['piste']} ; gain {result['decision']['gain']}")
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("trendline-final")
def trendline_final_command(
        rehearsal: bool = typer.Option(False, "--rehearsal", help="Répétition sur la fin de DEVELOPMENT (aucune donnée réservée lue, rien compté)"),
        i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                     help="Lecture UNIQUE de la période réservée (enregistrée, jamais refaite)"),
        allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Répétition locale sur du code non commité"),
        workers: int = typer.Option(4, "--workers", help="Paires traitées en parallèle (sans effet sur le résultat)"),
        verbose: bool = False):
    """Cassures de ligne de tendance en 1 h confirmées sur la période réservée (docs/LIGNES_DE_TENDANCE.md) : décision
    du propriétaire du 2026-10-05, une seule lecture enregistrée avant le calcul."""
    from .research.factors import DirtyCode
    from .research.protocol import FinalTestLocked
    from .research.trendline_confirmation import IncompleteMinutes
    from .research.trendline_final import AlreadyConsulted, run
    settings = _settings(verbose)
    if allow_dirty and not rehearsal:
        console.print("[red]--allow-dirty n'est permis que pour la répétition.[/red]")
        raise typer.Exit(2)
    _heavy_job(settings)
    try:
        with console.status("lignes de tendance, période réservée…") as status:
            payload = run(settings, now=_now(), allow_final_test=i_understand_final_test, rehearsal=rehearsal,
                          progress=lambda text: status.update(f"période réservée : {text}"), allow_dirty=allow_dirty,
                          workers=workers)
    except (DirtyCode, FinalTestLocked, AlreadyConsulted, IncompleteMinutes, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    result = payload["result"]
    title = "RÉPÉTITION sur DEVELOPMENT (rien compté)" if payload["rehearsal"] else "Période réservée (lecture unique)"
    table = Table("Scénario", "Transactions", "Paires", "R moyen", "IC", "Excès (frais égaux)", "IC", title=title)
    for name, c in result["scenarios"].items():
        table.add_row(name, str(c.get("n")), str(c.get("pairs")), str(c.get("r_mean")), str(c.get("r_ci")),
                      str(c.get("uexcess_adj_mean")), str(c.get("uexcess_adj_ci")))
    console.print(table)
    console.print(f"Décision : piste {result['decision']['piste']} (unilatéral 5 %) ; gain {result['decision']['gain']} ; "
                  f"consultations du programme : {payload['consultations_total']}")
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'}")


@app.command("ia-bias")
def ia_bias_command(model: str = typer.Option(..., "--model", help="Modèle Ollama local déclaré (qwen2.5vl:7b ou gemma3:12b)"),
                    rehearsal: bool = typer.Option(False, "--rehearsal", help="Répétition technique : 10 moments de DEVELOPMENT, ni rendement ni registre"),
                    i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                                 help="Lecture de la période réservée (une seule par modèle, enregistrée)"),
                    allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Répétition locale sur du code non commité"),
                    verbose: bool = False):
    """IA locale qui lit les graphiques contre la règle EMA 50 / 200, 400 moments de 2025-2026 (docs/IA_GRAPHES.md) :
    1 essai par modèle ; consultation n° 3 de la période réservée (autorisation du propriétaire)."""
    from .research.factors import DirtyCode
    from .research.ia_bias import AlreadyConsulted, ServerFailure, run
    from .research.protocol import FinalTestLocked
    settings = _settings(verbose)
    if allow_dirty and not rehearsal:
        console.print("[red]--allow-dirty n'est permis que pour la répétition.[/red]")
        raise typer.Exit(2)
    _heavy_job(settings)
    try:
        with console.status("IA locale…") as status:
            payload = run(settings, model=model, now=_now(), allow_final_test=i_understand_final_test, rehearsal=rehearsal,
                          progress=lambda text: status.update(f"IA locale : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, ValueError, FinalTestLocked, AlreadyConsulted, ServerFailure) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    if payload.get("rehearsal"):
        console.print(f"RÉPÉTITION {payload['model']} : {payload['n']} moments, avis {payload['biases']}, "
                      f"illisibles {payload['unreadable_share']}, {payload['seconds_median']} s par graphique (médiane)")
        return
    r = payload["result"]
    console.print(f"Modèle {payload['model']} ({payload['digest']}) — {r['n']} moments, {r['blocks']} blocs de 2 semaines")
    console.print(f"Écart de rendement signé IA − règle : {r['decision']['diff_mean_pct']} % {r['decision']['ci_pct']} ; "
                  f"IA seule {r['ia_signed_mean_pct']} % {r['decision']['ia_alone_ci_pct']} → {r['decision']['verdict']}")
    console.print(f"Règle : {r['rule_signed_mean_pct']} % ; bons sens IA {r['ia_right_share']} / règle {r['rule_right_share']} "
                  f"(part en hausse {r['share_up']}) ; illisibles {r['unreadable_share']}")
    console.print(f"Avis : {r['biases']} ; accord avec la règle {r['agreement_with_rule']}")
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'}")


@app.command("xsection-premium")
def xsection_premium_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Code non commité : enregistré et COMPTÉ"),
                             verbose: bool = False):
    """Primes coréenne et Coinbase des altcoins en coupe hebdomadaire (docs/XSECTION_PRIMES.md) : 4 essais,
    DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.xsection_premium import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("primes…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"primes : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Variable", "Semaines", "Paires (méd.)", "Écart haut − bas %", "IC", "Haut − moy. %", "Bas − moy. %",
                  "Corr. rang", "Moitiés", "Verdict")
    for name, r in payload["rows"].items():
        table.add_row(name, str(r.get("weeks")), str(r.get("pairs_median")), str(r.get("mean_spread_pct")), str(r.get("ci_pct")),
                      str(r.get("top_minus_all_pct")), str(r.get("bottom_minus_all_pct")), str(r.get("rank_corr_mean")),
                      str(r.get("spread_halves_pct")), r["verdict"])
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("xsection")
def xsection_command(allow_dirty: bool = typer.Option(False, "--allow-dirty", help="Essai local sur du code non commité (enregistré comme tel)"),
                     verbose: bool = False):
    """Portefeuilles hebdomadaires à date (docs/XSECTION.md) : momentum, double momentum, paires calmes contre la moyenne
    de l'univers, sur l'univers à date et sur les survivantes ; 6 essais, DEVELOPMENT seulement."""
    from .research.factors import DirtyCode
    from .research.xsection import run
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("portefeuilles hebdomadaires…") as status:
            payload = run(settings, now=_now(), progress=lambda text: status.update(f"portefeuilles : {text}"), allow_dirty=allow_dirty)
    except (DirtyCode, FileNotFoundError) as exc:
        console.print(f"[red]Aucun résultat :[/red] {exc}")
        raise typer.Exit(3) from None
    table = Table("Univers", "Portefeuille", "Semaines", "Moyenne/sem. %", "Sharpe", "Perte max %", "Excès/sem. %", "IC excès", "Années > 0")
    for universe, rows in payload["rows"].items():
        for name, r in rows.items():
            table.add_row(universe, name, str(r["weeks"]), str(r["weekly_mean_pct"]), str(r["sharpe"]), str(r["max_drawdown_pct"]),
                          str(r["excess_weekly_pct"]), str(r["excess_ci_pct"]), r["years_positive"])
    console.print(table)
    console.print(f"Rapport : {settings.reports_dir / payload['run_id'] / 'summary.json'} ; programme : {payload['program_trials']} essais")


@app.command("spreads")
def spreads_command(verbose: bool = False):
    """Résumé du relevé des écarts entre bourses et de la prime coréenne (docs/SPREADS.md) : écart brut moyen et maximal,
    part des relevés à écart NET positif après les frais d'un particulier. Lecture seule."""
    from .forward.spreads import summary
    settings = _settings(verbose)
    out = summary(settings)
    table = Table("Bourse / actif", "Relevés", "Écart brut moyen (pb)", "Écart brut max (pb)", "Relevés à écart net > 0")
    for key, s in sorted(out["pairs"].items()):
        table.add_row(key, str(s["n"]), str(s["gross_mean_bps"]), str(s["gross_max_bps"]), f"{s['net_positive_share']:.1%}")
    console.print(table)
    console.print(f"Relevés : {out['snapshots']} ; prime coréenne (pb) : {out['korea']}")


@app.command("data-quality")
def data_quality(symbol: str = typer.Option(..., help="Paire, ex. BTCUSDT"),
                 timeframe: str = typer.Option(None, help="Défaut : timeframe de setup")):
    """Rapport de qualité des données stockées (aucune modification)."""
    from .data.quality import assess
    from .features.loader import MissingData, load_candles
    settings = _settings()
    tf = timeframe or settings.data.setup_timeframe
    try:
        frame = load_candles(settings, symbol.upper(), tf)
    except MissingData as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    report = assess(frame, symbol.upper(), tf, _now())
    data = report.to_dict()
    gaps = data.pop("gaps")
    console.print_json(json.dumps(data, ensure_ascii=False))
    if gaps:
        table = Table("Avant la bougie", "Bougies manquantes", title=f"{len(gaps)} trou(s)")
        for gap in gaps[:30]:
            table.add_row(gap["before_open_time"], str(gap["missing_bars"]))
        console.print(table)
        if len(gaps) > 30:
            console.print(f"… {len(gaps) - 30} autres")


def _heavy_job(settings) -> None:
    """Travail lourd (point 4) : priorité CPU basse et plafond mémoire, pour ne pas gêner `run` ni le PC."""
    import atexit

    from .live.priority import heavy_job, peak_memory_mb
    limit = settings.live.heavy_job_max_memory_mb
    applied = heavy_job(limit)
    if applied["memory_limited"]:
        console.print(f"[dim]Travail lourd : priorité basse, mémoire engagée plafonnée à {limit} Mo "
                      "(live.heavy_job_max_memory_mb).[/dim]")

    def report_peak() -> None:
        peak = peak_memory_mb()
        if peak:
            console.print(f"[dim]Pic mémoire : {peak['committed_peak_mb']} Mo engagés (plafond {limit or 'aucun'}), "
                          f"{peak['ram_peak_mb']} Mo en RAM.[/dim]")

    atexit.register(report_peak)


@contextmanager
def _exclusive(settings):
    """Verrou d'instance (le même que la surveillance) pour toute commande qui publie ou réconcilie :
    jamais deux écrivains sur le registre ni un `.tmp` supprimé pendant qu'un autre l'écrit."""
    from .live.lock import InstanceAlreadyRunning, InstanceLock
    try:
        lock = InstanceLock(settings.root / settings.live.lock_file)
        lock.acquire()
    except InstanceAlreadyRunning as exc:
        console.print(f"[red]Refusé :[/red] {exc}. Arrêter la surveillance d'abord (une seule instance publie).")
        raise typer.Exit(3) from None
    try:
        yield
    finally:
        lock.release()


def _strategies(requested: list[str] | None) -> list[str]:
    from .strategies.registry import STRATEGIES
    names = [s.upper() for s in requested] if requested else list(STRATEGIES)
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        console.print(f"[red]Stratégie(s) inconnue(s) : {', '.join(unknown)}[/red] ; disponibles : {', '.join(STRATEGIES)}")
        raise typer.Exit(2)
    return names


@app.command()
def analyze(symbol: list[str] = typer.Option(None, help="Paire(s) ; défaut : configuration"),
            strategy: list[str] = typer.Option(None, help="Stratégie(s) ; défaut : toutes"),
            mode: str = typer.Option("shadow", help="shadow uniquement tant que l'intégration n'est pas vérifiée"),
            refresh: bool = typer.Option(True, help="Met à jour les données avant analyse"),
            verbose: bool = False):
    """Analyse la dernière bougie clôturée ; publie en shadow si BUY."""
    from .data.pipeline import download as run_download
    from .signals.analyze import analyze as run_analyze
    settings = _settings(verbose)
    strategies = _strategies(strategy)
    if mode != "shadow":
        console.print("[red]Seul le mode shadow est disponible : intégration non vérifiée (INTEGRATION_UNVERIFIED).[/red]")
        raise typer.Exit(2)
    symbols = [s.upper() for s in symbol] if symbol else settings.data.symbols
    now = _now()
    if refresh:
        for sym in dict.fromkeys([*symbols, "BTCUSDT"]):
            for tf in (settings.data.setup_timeframe, settings.data.context_timeframe):
                with console.status(f"mise à jour {sym} {tf}…"):
                    run_download(settings, sym, tf, now=now)
    now = _now()
    console.print("[yellow]INTEGRATION_UNVERIFIED[/yellow] : sortie vers signals/shadow uniquement.")
    with _exclusive(settings):
        for name in strategies:
            for sym in symbols:
                out = run_analyze(settings, sym, name, now=now)
                if out.action == "BUY":
                    console.print(f"[green]{sym} BUY[/green] {name} ({out.publication_status}) → {out.signal_path}")
                else:
                    console.print(f"{sym} {name} NO_TRADE [bold]{out.reason_code}[/bold] "
                                  f"(bougie {out.decision_time:%Y-%m-%d %H:%M} UTC) — {'; '.join(out.details)}")


@app.command()
def backtest(strategy: str = typer.Option("DONCHIAN_VOLUME_BREAKOUT"),
             period: str = typer.Option("development", help="development | final-test"),
             i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                          help="Consulter le test final réservé (enregistré)"),
             scenario: list[str] = typer.Option(None, help="Scénarios de coûts ; défaut : tous"),
             no_ablation: bool = typer.Option(False, help="Ne pas lancer les variantes sans filtre"),
             verbose: bool = False):
    """Backtest de référence local (signaux indépendants), scénarios de coûts et ablations."""
    from .research.backtest_run import run
    from .research.protocol import FinalTestLocked
    settings = _settings(verbose)
    _heavy_job(settings)
    try:
        with console.status("simulation…") as status:
            batch = run(settings, strategy, period_label=period, now=_now(), allow_final_test=i_understand_final_test,
                        scenarios=scenario or None, ablations=not no_ablation,
                        progress=lambda text: status.update(f"simulation {text}"))
    except FinalTestLocked as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    table = Table("Variante", "Coûts", "Clos", "Expirés", "Gagnants", "E[R]", "IC95 E[R]", "E[R] optim.",
                  "PF", "DD (R)", "Expo %", title=f"{batch.strategy} — {batch.period_label} "
                  f"{batch.period_start:%Y-%m-%d} → {batch.period_end:%Y-%m-%d} (signaux indépendants)")
    for r in batch.results:
        s = r.summary
        table.add_row(r.variant, r.scenario, str(s["trades_closed"]), str(s["entries_expired"]),
                      str(s.get("win_rate", "–")), str(s.get("expectancy_r", "–")),
                      str(s.get("expectancy_r_ci95_block_bootstrap", "–")),
                      str(s.get("expectancy_r_optimistic_bound", "–")), str(s.get("profit_factor_r", "–")),
                      str(s.get("max_drawdown_r_closed_trades", "–")), str(s["exposure_pct"]))
    console.print(table)
    for name, b in batch.baselines.items():
        console.print(f"  référence {name} : {b}")
    console.print(f"Verdict : NOT_EVALUATED (voir la commande walk-forward). Rapport : {batch.report_dir / 'report.md'}")


@app.command("walk-forward")
def walk_forward(strategy: list[str] = typer.Option(None, help="Stratégie(s) ; défaut : toutes"),
                 verbose: bool = False):
    """Walk-forward purgé sur DEVELOPMENT : recalibrage sur le passé, test sur la fenêtre suivante, verdict."""
    from .research.walk_forward import run
    settings = _settings(verbose)
    _heavy_job(settings)
    for name in _strategies(strategy):
        with console.status(f"{name}…") as status:
            result = run(settings, name, now=_now(), progress=lambda text, n=name: status.update(f"{n} : {text}"))
        table = Table("Test", "Paramètres retenus", "Trades entr.", "E[R] entr.", "Trades test", "E[R] test",
                      title=f"{name} v{result.strategy_version} — walk-forward {result.run_id}")
        for w in result.windows:
            table.add_row(f"{w['test_start'][:10]} → {w['test_end'][:10]}",
                          ", ".join(f"{k}={v}" for k, v in w["chosen"].items()), str(w["train_trades"]),
                          str(w["train_expectancy_r"] and round(w["train_expectancy_r"], 3)), str(w.get("oos_trades")),
                          str(w.get("oos_expectancy_r")))
        console.print(table)
        aggregate = Table("Variante", "Clos", "Gagnants", "E[R]", "IC95 E[R]", "PF", "DD (R)",
                          title="Agrégat hors échantillon")
        for label, s in result.summaries.items():
            aggregate.add_row(label, str(s["trades_closed"]), str(s.get("win_rate", "–")),
                              str(s.get("expectancy_r", "–")), str(s.get("expectancy_r_ci95_block_bootstrap", "–")),
                              str(s.get("profit_factor_r", "–")), str(s.get("max_drawdown_r_closed_trades", "–")))
        console.print(aggregate)
        for c in result.criteria:
            mark = "[green]✔[/green]" if c.passed else "[red]✘[/red]"
            console.print(f"  {mark} {c.number}. {c.label} — {c.detail}")
        color = {"VALIDATED_OOS": "green", "REJECTED": "red"}.get(result.verdict.value, "yellow")
        console.print(f"[bold {color}]Verdict : {result.verdict.value}[/bold {color}] — "
                      f"rapport : {result.report_dir / 'report.md'}\n")


@app.command("ml-evaluate")
def ml_evaluate(strategy: list[str] = typer.Option(None, help="Stratégie(s) ; défaut : toutes"),
                verbose: bool = False):
    """Lot 5 : un modèle logistique sait-il trier les setups d'une stratégie, hors échantillon ? (docs/ML.md)"""
    from .ml.meta import run
    settings = _settings(verbose)
    _heavy_job(settings)
    for name in _strategies(strategy):
        with console.status(f"{name}…") as status:
            result = run(settings, name, now=_now(), progress=lambda text, n=name: status.update(f"{n} : {text}"))
        oos = result.oos
        console.rule(f"Méta-labeling {name} — {result.run_id}")
        for w in result.windows:
            console.print(f"  {w['test_start'][:10]} → {w['test_end'][:10]} : entraînement {w['train_trades']}, "
                          f"test {w['test_trades']}" + (f", AUC {w['test_auc']:.3f}, gardés {w['kept_share']:.0%}"
                                                        if w.get("test_auc") is not None else
                                                        f" ({w.get('skipped', '')})"))
        if oos.get("trades"):
            console.print(f"  Agrégat : {oos['trades']} trades, AUC {oos['auc']}, Brier {oos['brier_model']} contre "
                          f"{oos['brier_base_rate']} (taux de base) ; E[R] tous {oos['expectancy_r_all']}, gardés "
                          f"{oos['expectancy_r_kept']} ({oos['kept_share']:.0%})")
        for c in result.criteria:
            mark = "[green]✔[/green]" if c["passed"] else "[red]✘[/red]"
            console.print(f"  {mark} {c['number']}. {c['label']} — {c['detail']}")
        color = "green" if result.verdict == "USEFUL_OOS" else "red" if result.verdict == "NOT_USEFUL" else "yellow"
        console.print(f"[bold {color}]Verdict : {result.verdict}[/bold {color}] — rapport : "
                      f"{result.report_dir / 'report.md'} (aucune influence sur les signaux)\n")


def _ml_program(protocol, label: str, stage: str, *, final_test: bool, selection_run: str | None,
                allow_dirty: bool, verbose: bool) -> None:
    """Sélection (DEVELOPMENT) ou estimation unique (FINAL_TEST) d'un programme ML du moteur commun."""
    from .ml.engine import ADMISSIBLE
    from .research.protocol import FinalTestLocked
    settings = _settings(verbose)
    _heavy_job(settings)
    if stage == "select":
        with console.status("sélection…") as status:
            result = protocol.select(settings, now=_now(), allow_dirty=allow_dirty,
                                     progress=lambda text: status.update(f"{label} : {text}"))
        payload = result.payload
        console.rule(f"{label} — {result.run_id}")
        console.print(f"Audit des fuites : {'réussi' if result.leak_audit['passed'] else 'ÉCHEC'} ; essais : "
                      f"{payload['n_trials']} ; programme : {payload['program_trials']} ; systèmes stables : "
                      f"{payload.get('stable_count')} ; admissibles : {payload['admissible_count']}")
        for row in payload["top_systems"][:5]:
            console.print(f"  {row['key']} : {row['positive_folds']}/{row['evaluated_folds']} validations > 0, "
                          f"{row['trades']} trades, Sharpe médian {row['median_sharpe']}")
        color = "green" if result.conclusion == ADMISSIBLE else "red"
        console.print(f"[bold {color}]{result.conclusion}[/bold {color}] — rapport : {result.report_dir / 'report.md'}")
        return
    if stage != "final":
        console.print("[red]Étape inconnue[/red] : select | final")
        raise typer.Exit(2)
    try:
        payload = protocol.final(settings, now=_now(), allow_final_test=final_test, selection_run=selection_run,
                                 progress=lambda text: console.print(f"[dim]{text}[/dim]"))
    except FinalTestLocked as exc:
        console.print(f"[yellow]Période finale non consultée :[/yellow] {exc}")
        raise typer.Exit(3) from None
    for c in payload["criteria"]:
        console.print(f"  {'[green]✔[/green]' if c['passed'] else '[red]✘[/red]'} {c['number']}. {c['label']}")
    console.print(f"[bold]Verdict : {payload['verdict']}[/bold] (consultations de la période finale : "
                  f"{payload['final_test_consultations']})")


@app.command("ml-intraday")
def ml_intraday(stage: str = typer.Argument("select", help="select (DEVELOPMENT) | final (FINAL_TEST, une fois)"),
                i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                             help="Consulter la période finale (enregistré)"),
                selection_run: str = typer.Option(None, help="Sélection de référence pour `final` ; défaut : la dernière"),
                allow_dirty: bool = typer.Option(False, help="Accepter du code non commité (essai local, enregistré)"),
                verbose: bool = False):
    """Lot 5 bis : ML intraday (docs/ML_INTRADAY.md) — sélection glissante, puis estimation unique."""
    from .ml.intraday import protocol
    _ml_program(protocol, "ML intraday", stage, final_test=i_understand_final_test, selection_run=selection_run,
                allow_dirty=allow_dirty, verbose=verbose)


@app.command("ml-swing")
def ml_swing(stage: str = typer.Argument("select", help="select (DEVELOPMENT) | final (FINAL_TEST, une fois)"),
             i_understand_final_test: bool = typer.Option(False, "--i-understand-final-test",
                                                          help="Consulter la période finale (enregistré)"),
             selection_run: str = typer.Option(None, help="Sélection de référence pour `final` ; défaut : la dernière"),
             allow_dirty: bool = typer.Option(False, help="Accepter du code non commité (essai local, enregistré)"),
             long: bool = typer.Option(False, "--long", help="Historique long 2017-2025, 40 paires, 3 à 7 jours "
                                                             "(docs/ML_SWING_LONG.md) : programme ML_SWING_LONG"),
             verbose: bool = False):
    """Lot 5 ter : ML swing de 1 à 7 jours (docs/ML_SWING.md) — sélection avec la règle v6, puis estimation unique.
    Avec --long : le même protocole rejoué sur l'historique long (docs/ML_SWING_LONG.md)."""
    if long:
        from .ml.swing import long as protocol_long
        _ml_program(protocol_long, "ML swing long", stage, final_test=i_understand_final_test,
                    selection_run=selection_run, allow_dirty=allow_dirty, verbose=verbose)
        return
    from .ml.swing import protocol
    _ml_program(protocol, "ML swing", stage, final_test=i_understand_final_test, selection_run=selection_run,
                allow_dirty=allow_dirty, verbose=verbose)


@app.command()
def screen(horizon: list[int] = typer.Option(None, "--horizon", help="Horizon(s) de sortie en heures ; défaut : 1, 4, 24"),
           verbose: bool = False):
    """Criblage brut des familles D à I (période DEVELOPMENT) : avantage après dérive et au-delà des coûts ?"""
    from .research.screen import CONDITIONS, HORIZONS_HOURS, run
    settings = _settings(verbose)
    _heavy_job(settings)
    with console.status("criblage…") as status:
        result = run(settings, now=_now(), progress=lambda s: status.update(f"criblage : {s}"),
                     horizons=tuple(horizon) if horizon else HORIZONS_HOURS)
    table = Table("condition", "horizon", "événements", "paires", "rendement moyen %", "excès moyen %",
                  "IC95 excès %", "paires > 0", "années > 0", "passe",
                  title=f"Criblage {result.run_id} — seuil de coûts {result.cost_hurdle_pct:.2f} % aller-retour, "
                        f"{result.n_trials} essais")
    for r in result.rows:
        ci = f"[{r.ci95_excess_pct[0]:+.3f} ; {r.ci95_excess_pct[1]:+.3f}]" if r.ci95_excess_pct else "–"
        table.add_row(r.condition, f"{r.horizon_h} h", str(r.events), str(r.pairs),
                      f"{r.mean_return_pct:+.3f}" if r.mean_return_pct is not None else "–",
                      f"{r.mean_excess_pct:+.3f}" if r.mean_excess_pct is not None else "–", ci,
                      f"{r.pairs_positive_share:.0%}" if r.pairs_positive_share is not None else "–",
                      f"{r.years_positive_share:.0%}" if r.years_positive_share is not None else "–",
                      "[green]oui[/green]" if r.beats_costs else "non")
    console.print(table)
    for name, text in CONDITIONS.items():
        console.print(f"[dim]{name} : {text}[/dim]")
    console.print("« passe » = rendement brut moyen > seuil de coûts ET borne basse de l'IC95 de l'excès > 0. "
                  "Ce n'est pas une stratégie validée : seulement une idée qui mérite une fiche et un walk-forward.")


@app.command("validate-causality")
def validate_causality(symbol: str = typer.Option("ETHUSDT"), strategy: str = typer.Option("DONCHIAN_VOLUME_BREAKOUT"),
                       cuts: int = typer.Option(5, help="Nombre d'instants de coupure")):
    """Vérifie que features et décisions passées ne changent pas si le futur change."""
    from .features.loader import load_inputs
    from .strategies import registry
    from .validation.causality import check
    settings = _settings()
    inputs = load_inputs(settings, symbol.upper())
    times = inputs["setup"]["open_time"]
    positions = [int(len(times) * f) for f in [0.3 + 0.6 * i / max(cuts - 1, 1) for i in range(cuts)]]
    cut_times = [pd.Timestamp(times.iloc[p]) for p in positions]
    results = check(settings, registry.build(strategy, settings.strategies), inputs, symbol.upper(), cut_times)
    table = Table("Coupure", "Lignes comparées", "Features divergentes", "Décisions divergentes", "État")
    for r in results:
        table.add_row(r.cut, str(r.rows_compared), ", ".join(r.feature_mismatches) or "0", str(r.decision_mismatches),
                      "[green]OK[/green]" if r.ok else "[red]FUITE ?[/red]")
    console.print(table)
    raise typer.Exit(0 if all(r.ok for r in results) else 1)


@app.command()
def report(run_id: str = typer.Option(None, "--run-id", help="Identifiant d'exécution ; vide = liste récente")):
    """Affiche une expérience enregistrée, ou la liste des plus récentes."""
    from .research.experiments import ExperimentRegistry
    settings = _settings()
    registry = ExperimentRegistry(settings.experiments_db)
    if not run_id:
        table = Table("run_id", "date", "type", "stratégie", "variante", "période", "coûts", "statut", "verdict")
        for row in registry.recent():
            table.add_row(*(str(row[k] or "–") for k in ("run_id", "created_at", "kind", "strategy", "variant",
                                                         "period_label", "cost_scenario", "status", "verdict")))
        console.print(table)
        return
    run = registry.get(run_id)
    if run is None:
        console.print(f"[red]Exécution inconnue : {run_id}[/red]")
        raise typer.Exit(2)
    console.print_json(json.dumps(run, ensure_ascii=False, default=str))


VERDICT_TEXT = {
    "REFUSE": "signal non évaluable (voir les contrôles)",
    "DEFAVORABLE": "au moins un veto, ou espérance nette négative de cette géométrie sur l'historique",
    "INDETERMINE": "aucun veto, mais l'historique ne permet pas de conclure (échantillon, IC contenant 0)",
    "FAVORABLE": "aucun veto et espérance nette positive de cette géométrie sur l'historique",
}
VERDICT_COLOR = {"REFUSE": "red", "DEFAVORABLE": "red", "INDETERMINE": "yellow", "FAVORABLE": "green",
                 "EN_ATTENTE": "cyan"}
VERDICT_TEXT["EN_ATTENTE"] = ("paire ajoutée à l'univers sur validation du propriétaire ; historique en cours de "
                              "téléchargement par la surveillance : réévaluer dans quelques minutes (non enregistré)")


def _print_evaluation(ev) -> None:
    console.rule(f"Signal externe {ev.signal.symbol or '?'} — source « {ev.source} »")
    for check in ev.checks:
        mark = "[green]✔[/green]" if check.ok else "[red]✘[/red]"
        console.print(f"  {mark} {check.label} — {check.detail}")
    ctx, geo, rate = ev.context, ev.geometry, ev.base_rate
    if ctx:
        btc = ctx["btc_ret_24h_pct"]
        console.print(f"  Contexte : close {ctx['close']:g}, ATR14 {ctx['atr_pct']:.2f} %, RSI14 "
                      f"{f'{ctx['rsi14']:.1f}' if ctx['rsi14'] is not None else '–'}, tendance 1h {ctx['trend_1h']}, "
                      f"volatilité {ctx['volatility_1h']}, liquidité {ctx['liquidity_1h']}, "
                      f"BTC 24 h {f'{btc:+.2f} %' if btc is not None else '–'}")
    if geo:
        effective = geo.get("entry_effective", geo["entry"])
        obtained = f" (obtenue {effective:g})" if effective != geo["entry"] else ""
        console.print(f"  Géométrie : entrée {geo['entry']:g}{obtained}, stop {geo['stop']:g} (−{geo['stop_pct']:.2f} % = "
                      f"{geo['stop_atr']:.2f} ATR), TP1 {geo['targets'][0]:g} (+{geo['tp1_pct']:.2f} %), RR bruts "
                      f"{', '.join(f'{x:.2f}' for x in geo['rr_gross'])}, RR net TP1 {geo['rr_net_tp1_central']:.2f}")
    if rate and rate.samples:
        ci, tp_ci = rate.expectancy_r_ci95, rate.tp_first_ci95
        console.print(f"  Taux de base ({ev.signal.symbol}, {rate.regime}, {rate.samples} ordres remplis sur "
                      f"{rate.emitted} simulés, soit {rate.fill_rate:.0%} ; horizon {rate.horizon_bars} bougies) : "
                      f"TP1 avant SL [bold]{rate.tp_first:.0%}[/bold]"
                      + (f" [IC95 {tp_ci[0]:.0%} ; {tp_ci[1]:.0%}]" if tp_ci else "")
                      + f", SL d'abord {rate.sl_first:.0%}, ni l'un ni l'autre {rate.timeout:.0%} ; espérance nette "
                      f"{rate.expectancy_r:+.3f} R par ordre rempli"
                      + (f" [IC95 {ci[0]:+.3f} ; {ci[1]:+.3f}]" if ci else
                         f" (intervalle impossible : {rate.blocks} blocs de {rate.block_days} j, 10 requis)"))
        console.print("  (le même ordre limite rejoué à chaque bougie du passé, SANS sélection, après coûts : une "
                      "référence, pas une prédiction de ce signal)")
    stats = ev.source_stats
    if stats and stats["resolved"]:
        console.print(f"  Source : {stats['evaluated']} évalués, {stats['resolved']} résolus — TP1 réalisé "
                      f"{stats['realized_tp1_rate']:.0%} vs base {stats['base_tp1_rate']:.0%}, R réalisé moyen "
                      f"{stats['realized_r']:+.3f} vs base {stats['base_expectancy_r']:+.3f}")
    elif stats:
        console.print(f"  Source : {stats['evaluated']} évalué(s), aucun encore résolu (lancer resolve-signals plus tard)")
    for warning in ev.warnings:
        console.print(f"  [yellow]⚠ {warning}[/yellow]")
    color = VERDICT_COLOR[ev.verdict]
    console.print(f"[bold {color}]Avis : {ev.verdict}[/bold {color}] — {VERDICT_TEXT[ev.verdict]}"
                  + (f" — enregistré {ev.record_id}" if ev.record_id else ""))


@app.command()
def perspective(symbol: str = typer.Argument(..., help="Paire, ex. ETHUSDT"),
                horizon: str = typer.Option("24h", help="1h, 4h, 12h, 24h, 3j ou 7j"),
                refresh: bool = typer.Option(True, help="Télécharge d'abord les bougies manquantes (données publiques)"),
                verbose: bool = False):
    """Perspective d'une paire (comme le tableau de bord) : historique comparable, plan indicatif, avis. N'exécute rien."""
    from .data.pipeline import download as run_download
    from .outlook.pair import HORIZONS, OutlookError, pair_outlook
    settings = _settings(verbose)
    symbol = symbol.upper().replace("/", "")
    if refresh:
        for sym, tf in ((symbol, settings.data.setup_timeframe), (symbol, settings.data.context_timeframe),
                        ("BTCUSDT", settings.data.context_timeframe)):
            with console.status(f"mise à jour {sym} {tf}…"):
                run_download(settings, sym, tf, now=_now())
    try:
        result = pair_outlook(settings, symbol, horizon, now=_now())
    except OutlookError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    c, plan = result["context"], result["plan"]
    console.rule(f"{symbol} — horizon {result['horizon_label']}")
    console.print(f"Prix {c['close']} (bougie {c['decision_time'][:16]} UTC, il y a {c['data_age_minutes']} min) ; "
                  f"tendance 1 h {c['trend_1h']}, volatilité {c['volatility_1h']} ; BTC 24 h {c['btc_ret_24h_pct']} %")
    table = Table("horizon", "hausse (fréquence)", "intervalle corrigé", "hausse, tous moments", "gain net moyen %",
                  "8 cas sur 10 entre (%)", "exemples", title=f"Historique jusqu'au {result['history_end']}")
    for row in result["overview"]:
        p_up, mean = row["p_up"], row["mean_net"]
        table.add_row(row["label"], f"{(p_up['value'] or 0) * 100:.1f} %",
                      f"{p_up['ci'][0] * 100:.1f} – {p_up['ci'][1] * 100:.1f}" if p_up["ci"] else "non fiable",
                      f"{(row['p_up_all_moments']['value'] or 0) * 100:.1f} %", f"{(mean['value'] or 0) * 100:+.2f}",
                      f"{(row['q10_gross'] or 0) * 100:+.1f} à {(row['q90_gross'] or 0) * 100:+.1f}",
                      f"{row['samples']} · {p_up['blocks']} blocs ({'même régime' if row['regime_conditioned'] else 'tous régimes'})")
    console.print(table)
    if "unavailable" in plan:
        console.print(f"Plan indicatif indisponible : {plan['unavailable']}")
    else:
        console.print(f"Plan indicatif (achat) : entrée ≈ {plan['entry_reference']}, stop {plan['stop']} ({plan['stop_pct']} %), "
                      f"objectif {plan['target']} (+{plan['target_pct']} %) ; dans le passé : objectif d'abord "
                      f"{plan.get('tp_first', 0) * 100:.1f} %, stop d'abord {plan.get('sl_first', 0) * 100:.1f} %, espérance "
                      f"{plan.get('expectancy_r')} R (intervalle {plan.get('expectancy_r_ci')}), écart aux conditions "
                      f"actuelles {plan.get('excess_r')} R (intervalle {plan.get('excess_r_ci')})")
    color = {"HISTORIQUE_DEFAVORABLE": "red", "DONNEES_ANCIENNES": "red"}.get(plan["state"], "yellow")
    console.print(f"[bold {color}]État : {plan['state']}[/bold {color}] — {result['definitions']['etat']}")
    console.print(f"[dim]{result['warning']} Choix d'horizon : {', '.join(HORIZONS)}.[/dim]")


@app.command("evaluate-signal")
def evaluate_signal(text: str = typer.Option(None, "--text", help="Texte du signal ; sinon --file, sinon entrée standard"),
                    file: str = typer.Option(None, "--file", help="Fichier UTF-8 contenant le signal"),
                    source: str = typer.Option("inconnu", help="Nom du groupe ou de la source"),
                    refresh: bool = typer.Option(True, help="Met à jour les données de la paire avant analyse"),
                    no_record: bool = typer.Option(False, "--no-record", help="Ne pas enregistrer l'évaluation"),
                    add_pair: bool = typer.Option(True, "--add-pair/--no-add-pair",
                                                  help="Un signal soumis à la main vaut validation de la paire : "
                                                       "ajoutée à l'univers si elle n'y est pas (défaut)"),
                    verbose: bool = False):
    """Évalue un signal externe (groupe Telegram) : vetos, contexte, taux de base, avis. N'exécute rien."""
    from pathlib import Path

    from .data.pipeline import download as run_download
    from .external.evaluate import evaluate as run_evaluate
    from .external.parser import parse
    from .external.universe import universe_symbols
    settings = _settings(verbose)
    raw = text or (Path(file).read_text(encoding="utf-8") if file else sys.stdin.read())
    if not raw.strip():
        console.print("[red]Aucun texte de signal (--text, --file ou entrée standard).[/red]")
        raise typer.Exit(2)
    parsed = parse(raw)
    if refresh and parsed.ok and parsed.symbol in universe_symbols(settings):
        now = _now()
        for sym, tf in ((parsed.symbol, settings.data.setup_timeframe), (parsed.symbol, settings.data.context_timeframe),
                        ("BTCUSDT", settings.data.context_timeframe)):
            with console.status(f"mise à jour {sym} {tf}…"):
                run_download(settings, sym, tf, now=now)
    _print_evaluation(run_evaluate(settings, raw, source=source, now=_now(), record=not no_record,
                                   user_validated=add_pair))


@app.command()
def universe(forget: str = typer.Option(None, "--forget", help="Retire une paire ajoutée par le propriétaire")):
    """Univers évaluable : paires configurées et paires ajoutées par le propriétaire (signal soumis à la main)."""
    from .external.universe import UserUniverse
    settings = _settings()
    store = UserUniverse(settings.external_db)
    if forget:
        symbol = forget.upper()
        console.print(f"{symbol} retirée de l'univers du propriétaire (bougies conservées sur disque)."
                      if store.forget(symbol) else f"{symbol} n'est pas une paire ajoutée par le propriétaire.")
    console.print(f"Configuration ({len(settings.data.symbols)}) : {', '.join(settings.data.symbols)}")
    table = Table("paire", "état", "pas de prix", "demandée le", "prête le", "tentatives", "dernière erreur",
                  title="Paires ajoutées par le propriétaire (un signal soumis à la main vaut validation)")
    for row in store.all():
        table.add_row(row["symbol"], row["status"], row["tick_size"], row["requested_at"][:16],
                      (row["ready_at"] or "–")[:16], str(row["attempts"]), row["last_error"] or "–")
    console.print(table)


@app.command("resolve-signals")
def resolve_signals(refresh: bool = typer.Option(True, help="Met à jour les données des paires concernées"),
                    verbose: bool = False):
    """Résout les signaux externes en attente avec les bougies stockées (TP1, SL, TIMEOUT, UNFILLED)."""
    from .data.pipeline import download as run_download
    from .external.registry import ExternalSignalRegistry, resolve_pending
    settings = _settings(verbose)
    registry = ExternalSignalRegistry(settings.external_db)
    pending = registry.pending()
    if not pending:
        console.print("Aucun signal externe en attente.")
        return
    if refresh:
        now = _now()
        for sym in dict.fromkeys(row["symbol"] for row in pending):
            with console.status(f"mise à jour {sym}…"):
                run_download(settings, sym, settings.data.setup_timeframe, now=now)
    console.print(resolve_pending(settings, registry, now=_now()))


@app.command("audit-telegram")
def audit_telegram(file: str = typer.Option(None, "--file", help="Export JSON de Telegram Desktop (result.json)"),
                   folder: str = typer.Option(None, "--dir", help="Dossier d'exports : chaque result.json trouvé dessous (un export par "
                                              "groupe, photos comprises), audités ensemble"),
                   bsm_inbox: str = typer.Option(None, "--bsm-inbox", help="Boîte signals.sqlite3 de BinanceSpotManager (messages reçus en direct)"),
                   source: str = typer.Option("", help="Nom du groupe si l'export ou le texte ne le donne pas"),
                   weights: str = typer.Option("early", help="Parts vendues à chaque objectif : early | equal"),
                   ocr: bool = typer.Option(False, "--ocr", help="Lire aussi les signaux publiés en image (dossier de "
                                            "l'export ; extra « ocr »), au moindre doute l'image est ignorée"),
                   verbose: bool = False):
    """Bilan mesuré d'un groupe Telegram sur son historique : chaque signal passé est rejoué sur les bougies
    publiques, selon trois conventions (TP1 au contact, stop à la clôture du signal, tous les objectifs comme BSM).
    Mesure d'une source externe : aucun ordre, aucune promesse pour le prochain signal."""
    from .external.audit import (
        ALL,
        CONVENTION_LABELS,
        CONVENTIONS,
        audit,
        chart_reader,
        read_bsm_inbox,
        read_telegram_export,
        save_history,
        write_report,
    )
    from .external.exports import export_images
    settings = _settings(verbose)
    if sum(bool(x) for x in (file, folder, bsm_inbox)) != 1:
        console.print("[red]Donner --file (un export), --dir (un dossier d'exports) OU --bsm-inbox (boîte de BinanceSpotManager).[/red]")
        raise typer.Exit(2)
    try:
        if file or folder:
            exports = [Path(file)] if file else sorted(Path(folder).rglob("result.json"))
            if not exports:
                raise ValueError(f"aucun result.json sous {folder}")
            reader = None
            if ocr:
                with console.status("chargement du lecteur d'images…"):
                    reader = chart_reader()
            items = []
            for export in exports:
                with open(export, encoding="utf-8") as handle:
                    payload = json.load(handle)
                try:
                    with console.status(f"lecture de {export.parent.name}" + (" (images comprises)…" if ocr else "…")):
                        found = read_telegram_export(payload, images_dir=export.parent, image_reader=reader)
                except ValueError as exc:
                    if file:
                        raise
                    console.print(f"[yellow]{export.parent.name} : ignoré, pas un export de Telegram Desktop ({exc})[/yellow]")
                    continue
                named, present = export_images(payload, export.parent)      # même calcul que le tableau de bord
                console.print(f"{export.parent.name} : {len(found)} message(s) ; images : {present} présente(s) sur {named}"
                              + (" — export fait SANS les photos : le refaire en cochant « Photos »" if named and not present else ""))
                items += found
            if ocr:
                console.print(f"Images lues comme signaux : {sum(i.from_image for i in items)}")
        else:
            items = read_bsm_inbox(Path(bsm_inbox))
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"[red]Historique illisible :[/red] {exc}")
        raise typer.Exit(2) from None
    with console.status("rejeu des signaux…") as status:
        report = audit(settings, items, now=_now(), source=source, weights=weights,
                       progress=lambda text: status.update(f"rejeu : {text}"))
    for name, block in report.summary.items():
        console.rule(f"{'Tous les groupes' if name == ALL else name} — {block['messages']} message(s)")
        console.print(f"Statuts : {block['statuts']}" + (
            f" ; TP1 moyen +{block['tp1_pct_moyen']} %, stop moyen −{block['stop_pct_moyen']} % : il faut "
            f"{block['part_tp1_pour_etre_a_zero']:.0%} de TP1 pour être à zéro, avant frais" if "tp1_pct_moyen" in block else ""))
        for convention in CONVENTIONS:
            c = block["conventions"][convention]
            detail = (f"{c['part_gagnants']:.0%} gagnants, R moyen {c['r_moyen']:+.2f} (total {c['r_total']:+.1f} R), "
                      f"IC95 {c['ic95'] or 'indisponible'}" if c["resolus"] else "aucun signal résolu")
            if "r_moyen_avec_ouvertes" in c:
                detail += f" ; avec les positions ouvertes au dernier prix : R moyen {c['r_moyen_avec_ouvertes']:+.2f} (provisoire)"
            console.print(f"  [bold]{CONVENTION_LABELS[convention]}[/bold] : {c['resolus']} résolus, {c['en_cours']} en "
                          f"cours, {c['non_remplis']} non remplis | {detail}\n    → {c['conclusion']}")
        images = block.get("images")
        if images:
            console.print(f"  [bold]Signaux lus sur image[/bold] (à part : ni dans le bilan ci-dessus, ni dans la preuve) : "
                          f"{images['lues']} lus, {images['ignorees']} ignorés (lecture douteuse), {images['mesurees']} mesurés")
            for convention in CONVENTIONS:
                c = images["conventions"][convention]
                if c["resolus"]:
                    console.print(f"    {CONVENTION_LABELS[convention]} : {c['resolus']} résolus, {c['part_gagnants']:.0%} "
                                  f"gagnants, R moyen {c['r_moyen']:+.2f}, IC95 {c['ic95'] or 'indisponible'}")
        study = block.get("gestions")
        if study:
            console.print(f"  [bold]Gestions comparées[/bold] ({study['variants']} variantes, {study['signals']} signaux) : "
                          f"{study['conclusion']}")
            for row in study.get("top_on_choice", []):
                console.print(f"    {row['label']} : {row['r_mean_choice']:+.2f} R (choix) → {row['r_mean_confirm']:+.2f} R "
                              "(confirmation)")
        proof = block["preuve"]
        console.print(("[green]" if proof["proven"] else "[yellow]") + f"Avis lié au groupe : {proof['text']}"
                      + ("[/green]" if proof["proven"] else "[/yellow]"))
    for note in report.notes:
        console.print(f"[dim]- {note}[/dim]")
    save_history(settings, report)
    console.print(f"Rapport : {write_report(settings, report)}")


@app.command()
def sources(recent: int = typer.Option(10, help="Nombre de derniers signaux affichés")):
    """Bilan par source des signaux externes : avis donnés, issues résolues, réalisé contre taux de base."""
    from .external.record import MIN_RESOLVED, source_records
    from .external.registry import ExternalSignalRegistry
    settings = _settings()
    registry = ExternalSignalRegistry(settings.external_db)
    table = Table("source", "évalués", "doublons", "refusés", "attente", "non remplis", "résolus", "TP1 réalisé / base",
                  "R réalisé / base", "écart (IC95)", "conclusion",
                  title=f"Bilan par source : réalisé contre taux de base, mêmes règles ; conclusion à partir de "
                        f"{MIN_RESOLVED} signaux résolus")
    pct = lambda v: f"{v:.0%}" if v is not None else "–"  # noqa: E731
    num = lambda v: f"{v:+.3f}" if v is not None else "–"  # noqa: E731
    for s in source_records(registry, seed=settings.protocol.seed):
        ci = f" [{s.edge_ci95[0]:+.2f} ; {s.edge_ci95[1]:+.2f}]" if s.edge_ci95 else ""
        table.add_row(s.source, str(s.evaluated), str(s.duplicates), str(s.refused), str(s.pending), str(s.unfilled),
                      str(s.resolved), f"{pct(s.tp1_real)} / {pct(s.tp1_base)}", f"{num(s.r_real)} / {num(s.r_base)}",
                      num(s.edge_r) + ci, s.conclusion)
    console.print(table)
    latest = Table("id", "reçu", "source", "paire", "entrée", "stop", "TP1", "avis", "TP1 base", "issue", "R")
    for row in registry.recent(recent):
        latest.add_row(row["id"], row["received_at"][:16], row["source"], row["symbol"], f"{row['entry'] or '–'}",
                       f"{row['stop'] or '–'}", f"{row['tp1'] or '–'}", row["verdict"], pct(row["p_tp1"]),
                       row["outcome"], num(row["outcome_r"]))
    console.print(latest)


@app.command("import-feedback")
def import_feedback(file: str = typer.Option(..., "--file", help="Fichier JSONL d'événements (docs/FEEDBACK_FORMAT.md)"),
                    verbose: bool = False):
    """Importe les événements d'exécution renvoyés par le consommateur (Binance Demo)."""
    from pathlib import Path

    from .feedback.store import FeedbackStore
    from .signals.outbox import SignalRegistry
    settings = _settings(verbose)
    known = {row["signal_id"] for row in SignalRegistry(settings.signals_db, settings.publication_dir()).rows()}
    summary = FeedbackStore(settings.feedback_db).import_jsonl(Path(file), now=_now(), known_signal_ids=known)
    console.print_json(json.dumps(summary.to_dict(), ensure_ascii=False))
    if summary.invalid:
        raise typer.Exit(1)


@app.command("execution-report")
def execution_report_command():
    """Par signal publié : issue théorique (rejeu), publication (shadow/outbox), exécution Demo observée."""
    from collections import Counter

    from .feedback.reconcile import execution_report
    settings = _settings()
    rows = execution_report(settings)
    if not rows:
        console.print("Aucun signal publié.")
        return
    def num(value: float | None) -> str:
        return f"{value:+.2f}" if value is not None else "–"

    table = Table("signal", "paire", "stratégie", "politique", "créé", "entrées jusqu'à", "canal", "entrée", "stop",
                  "TP1", "E[R] backtest", "prospectif", "R prosp.", "Demo", "R Demo", "écarts",
                  title="Backtest, prospectif et Demo observé : trois mesures séparées, jamais additionnées")
    for r in rows:
        table.add_row(r.signal_id, r.symbol, r.strategy, r.exit_policy, r.created_at[:16], r.entry_expires_at[11:16],
                      r.channel, r.entry, r.stop, r.tp1, num(r.backtest_r), r.theoretical_outcome,
                      num(r.theoretical_r), r.demo_status, num(r.demo_r), "\n".join(r.deviations) or "–")
    console.print(table)
    console.print(f"Prospectif : {dict(Counter(r.theoretical_outcome for r in rows))} ; "
                  f"Demo observé : {dict(Counter(r.demo_status for r in rows))}")
    for strategy in sorted({r.strategy for r in rows}):
        mine = [r for r in rows if r.strategy == strategy]
        prosp = [r.theoretical_r for r in mine if r.theoretical_r is not None]
        demo = [r.demo_r for r in mine if r.demo_r is not None]
        console.print(f"{strategy} : backtest {num(mine[-1].backtest_r)} R ({mine[-1].backtest_source}) | "
                      f"prospectif {num(sum(prosp) / len(prosp) if prosp else None)} R sur {len(prosp)} clos | "
                      f"Demo {num(sum(demo) / len(demo) if demo else None)} R sur {len(demo)} clos")
    console.print("UNKNOWN = aucune confirmation du consommateur (ni refus, ni perte) ; NOT_CONSUMED = shadow.")


def _print_cycle(report) -> None:
    published = report.published()
    console.print(f"[bold]clôture {report.decision_close:%Y-%m-%d %H:%M}Z[/bold] : {report.counts()} ; "
                  f"publiés {len(published)} ; durées {report.timings}")
    for o in published:
        console.print(f"  [green]BUY[/green] {o.symbol} {o.strategy} → {o.signal_path} ({o.publication_status})")
    for missing in report.missing_after_wait:
        console.print(f"  [yellow]absent après attente[/yellow] : {missing}")
    for error in report.errors:
        console.print(f"  [red]erreur[/red] : {error}")


@app.command()
def scan(mode: str = typer.Option("shadow", help="shadow uniquement tant que l'intégration n'est pas vérifiée"),
         no_refresh: bool = typer.Option(False, "--no-refresh", help="Analyse les données locales sans les compléter"),
         verbose: bool = False):
    """UN cycle : complète les bougies manquantes puis analyse toutes les paires et stratégies."""
    from .live.scanner import scan_cycle
    settings = _settings(verbose)
    if mode != settings.publication.mode:
        console.print(f"[red]mode {mode} refusé[/red] : la configuration publie en {settings.publication.mode}.")
        raise typer.Exit(2)
    with _exclusive(settings):
        _print_cycle(scan_cycle(settings, now=_now(), refresh=not no_refresh))


@app.command()
def run(mode: str = typer.Option("shadow", help="shadow uniquement tant que l'intégration n'est pas vérifiée"),
        max_cycles: int = typer.Option(0, help="0 : jusqu'à l'arrêt (Ctrl+C)"), verbose: bool = False):
    """Surveillance continue : un cycle à chaque clôture 15m, jusqu'à l'arrêt. Une seule instance."""
    from .live.lock import InstanceAlreadyRunning
    from .live.scanner import run_forever
    settings = _settings(verbose)
    if mode != settings.publication.mode:
        console.print(f"[red]mode {mode} refusé[/red] : la configuration publie en {settings.publication.mode}.")
        raise typer.Exit(2)
    console.print(f"Surveillance {mode} de {len(settings.data.symbols)} paires ; état : "
                  f"{settings.root / settings.live.status_file} ; arrêt : Ctrl+C")
    import signal as signals

    stop = {"requested": False}

    def request_stop(signum, frame):   # SIGTERM (Docker, systemd) : on termine le cycle en cours puis on sort
        stop["requested"] = True
        console.print("arrêt demandé : fin du cycle en cours")

    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signals, name):
            signals.signal(getattr(signals, name), request_stop)
    try:
        cycles = run_forever(settings, max_cycles=max_cycles or None, on_cycle=_print_cycle,
                             should_stop=lambda: stop["requested"])
    except InstanceAlreadyRunning as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(3) from None
    except KeyboardInterrupt:
        console.print("arrêt demandé")
        return
    console.print(f"{cycles} cycle(s) effectué(s)")


@app.command()
def health():
    """Santé de la surveillance : code 0 si prêt (cycle récent), 1 sinon (utilisable par Docker/systemd)."""
    from .live.health import check
    settings = _settings()
    result = check(settings, now=_now())
    console.print(("[yellow]" if result.degraded else "[green]") + result.detail if result.ready
                  else f"[red]{result.detail}[/red]")
    raise typer.Exit(0 if result.ready else 1)


@app.command()
def dashboard(open_browser: bool = typer.Option(False, "--open", help="Ouvre le fichier dans le navigateur")):
    """Tableau de bord HTML (santé, analyse, rejets, signaux, news, verdicts) : state/dashboard.html."""
    from .reporting.dashboard import write_dashboard
    settings = _settings()
    path = write_dashboard(settings, now=_now())
    console.print(f"tableau de bord : {path}")
    if open_browser:
        import webbrowser
        webbrowser.open(path.as_uri())


@app.command("news-sources")
def news_sources(check: bool = typer.Option(False, "--check", help="Interroge chaque source et la vérifie")):
    """Sources d'actualités : état (UNVERIFIED, OPERATIONAL, DOWN), dernier succès, dernière erreur."""
    from .news.collector import source_states, verify_sources
    from .news.store import NewsStore
    settings = _settings()
    now = _now()
    verified = verify_sources(settings, now=now) if check else {}
    states, health = source_states(settings, now=now), NewsStore(settings.news_db).health()
    table = Table("source", "catégorie", "état", "vérification", "dernier succès", "dernière erreur",
                  title=f"Actualités — mode {settings.news.mode} (observe : aucune influence sur les signaux)")
    for source in settings.news.sources:
        row = health.get(source.source_id, {})
        detail = verified.get(source.source_id, (None, row.get("verification_detail") or "–"))[1]
        state = states[source.source_id]
        color = {"OPERATIONAL": "green", "DOWN": "red"}.get(state, "yellow")
        table.add_row(source.source_id, source.category, f"[{color}]{state}[/{color}]", detail,
                      (row.get("last_success_at") or "–")[:16], (row.get("last_error") or "–")[:60])
    console.print(table)
    console.print("Une source DOWN ou UNVERIFIED ne signifie jamais « aucune mauvaise nouvelle ».")


@app.command("news-collect")
def news_collect():
    """Un passage de collecte sur toutes les sources actives (sans LLM)."""
    from .news.collector import collect
    settings = _settings()
    if settings.news.mode == "off":
        console.print("news.mode = off : aucune collecte.")
        return
    summary = collect(settings, now=_now())
    for s in summary.sources:
        status = f"[red]{s.error}[/red]" if s.error else f"{s.fetched} lus, {s.new} nouveaux, {s.revised} corrigés"
        console.print(f"{s.source_id} : {status}")


@app.command()
def news(hours: int = typer.Option(24, help="Fenêtre en heures (première réception)"),
         asset: str = typer.Option(None, help="Filtre par actif, ex. BTC"), limit: int = 50):
    """Actualités récentes regroupées par événement (données non fiables, jamais des instructions)."""
    from datetime import timedelta

    from .news.store import NewsStore
    settings = _settings()
    items = NewsStore(settings.news_db).recent(_now() - timedelta(hours=hours), asset=asset.upper() if asset else None,
                                               limit=limit * 4)
    seen_events: set[str] = set()
    table = Table("reçu", "publié", "source", "actifs", "titre", "reprises", title=f"Actualités des {hours} h")
    for item in items:
        if item["event_id"] in seen_events:
            continue
        seen_events.add(item["event_id"])
        table.add_row(item["first_seen_at"][:16], (item["published_at"] or "–")[:16], item["source_id"],
                      ",".join(item["assets"]) or "–", item["title"][:90] + (" [corrigé]" if item["revision"] else ""),
                      str(len(item["event_sources"])))
        if len(seen_events) >= limit:
            break
    console.print(table)


@app.command("news-risk")
def news_risk(days: int = typer.Option(7, help="Fenêtre en jours (première réception)"),
              llm: str = typer.Option(None, help="Modèle LOCAL Ollama (ex. qwen3:8b) : étiquette aussi par le modèle et compare"),
              study: bool = typer.Option(False, "--study", help="Étude d'événements déclarée (docs/NEWS.md) sur les bougies publiques")):
    """News de risque (piratage, retrait de la cote, réglementation, perte de parité, panne, faillite), en OBSERVATION :
    aucune influence sur les signaux."""
    from datetime import timedelta

    from .news.risk import (
        KEYWORDS,
        LocalModel,
        agreement,
        event_study,
        label_pending,
        public_bars,
        risk_items,
    )
    settings = _settings()
    now = _now()
    done = label_pending(settings, now=now)
    console.print(f"Mots-clés : {done['labelled']} article(s) étiqueté(s), dont {done['risk']} à risque.")
    if llm:
        model = LocalModel(llm)
        with console.status(f"classement par le modèle local {llm}…"):
            done = label_pending(settings, now=now, model=model, limit=500)
        console.print(f"Modèle local : {done['labelled']} étiqueté(s), {done['failed']} sans réponse valide "
                      "(modèle absent ou réponse hors format : aucune étiquette, jamais de supposition).")
        result = agreement(settings, model.method)
        table = Table("catégorie", "mots-clés", "modèle", "les deux", title=f"Accord sur {result['articles']} article(s)")
        for name, row in result["categories"].items():
            table.add_row(name, str(row["mots_cles"]), str(row["modele"]), str(row["les_deux"]))
        console.print(table)
    items = risk_items(settings, since=now - timedelta(days=days), method=KEYWORDS)
    table = Table("reçu", "source", "catégories", "actifs", "titre", title=f"News de risque des {days} derniers jours (mots-clés)")
    for item in items[-60:]:
        table.add_row(item["first_seen_at"][:16], item["source_id"], ",".join(item["categories"]),
                      ",".join(item["assets"]) or "–", item["title"][:80])
    console.print(table)
    if study:
        with console.status("étude d'événements…"):
            result = event_study(settings, now=now, bars_for=public_bars(settings))
        for horizon, block in result["summary"].items():
            console.print(f"{horizon} : {block}")
        console.print("Rendement de l'actif moins BTC, de l'ouverture de l'heure suivant la réception. Lecture : docs/NEWS.md.")
    console.print("Observation seulement : le mode « gate » reste refusé, aucune news ne bloque ni ne crée de signal.")


@app.command()
def backup(dest: str = typer.Option("backups", help="Dossier des sauvegardes (relatif à la racine du projet)"),
           with_data: bool = typer.Option(False, "--with-data", help="Inclut les bougies (volumineux, re-téléchargeables)")):
    """Sauvegarde cohérente : registres, retours d'exécution, expériences, actualités, signaux, configuration."""
    from pathlib import Path

    from .live.backup import create_backup, verify_backup
    settings = _settings()
    target = Path(dest) if Path(dest).is_absolute() else settings.root / dest
    archive = create_backup(settings, target, now=_now(), with_data=with_data)
    manifest = verify_backup(archive)
    console.print(f"sauvegarde vérifiée : {archive} ({len(manifest['files'])} fichiers)")


@app.command()
def restore(file: str = typer.Option(..., "--file", help="Archive produite par `backup`"),
            yes: bool = typer.Option(False, "--yes", help="Confirme le remplacement de l'état courant")):
    """Restaure une sauvegarde vérifiée ; l'état courant est d'abord sauvegardé ; publication SUSPENDUE ensuite."""
    from pathlib import Path

    from .live.backup import BackupError, restore_backup, verify_backup
    from .live.lock import InstanceAlreadyRunning
    settings = _settings()
    try:
        manifest = verify_backup(Path(file))
    except (BackupError, OSError) as exc:
        console.print(f"[red]sauvegarde refusée :[/red] {exc}")
        raise typer.Exit(2) from None
    if not yes:
        console.print(f"Sauvegarde valide du {manifest['created_at']} ({len(manifest['files'])} fichiers). "
                      "Relancer avec --yes pour remplacer l'état courant (il sera d'abord sauvegardé).")
        raise typer.Exit(1)
    try:
        result = restore_backup(settings, Path(file), now=_now())
    except InstanceAlreadyRunning as exc:
        console.print(f"[red]{exc}[/red] : arrêter la surveillance avant de restaurer.")
        raise typer.Exit(3) from None
    console.print(f"{result.restored_files} fichiers restaurés ; état précédent : {result.safety_backup}")
    console.print("[yellow]Publication suspendue[/yellow] jusqu'à réconciliation avec le consommateur : "
                  "`publication-resume --yes`.")


@app.command("publication-resume")
def publication_resume(yes: bool = typer.Option(False, "--yes", help="Confirme la réconciliation avec le consommateur")):
    """Lève la suspension de publication après une restauration (réconciliation d'abord)."""
    from .feedback.reconcile import execution_report
    from .live.backup import publication_suspended, resume_publication
    from .signals.outbox import SignalRegistry
    settings = _settings()
    reason = publication_suspended(settings)
    if reason is None:
        console.print("aucune suspension en cours")
        return
    registry = SignalRegistry(settings.signals_db, settings.publication_dir())
    pending = [r for r in registry.rows() if r["status"] == "PENDING"]
    unknown = [r for r in execution_report(settings) if r.channel == "outbox" and r.demo_status == "UNKNOWN"]
    console.print(f"suspension : {reason}")
    console.print(f"registre : {len(pending)} publication(s) en attente (terminées seulement après --yes) ; "
                  f"signaux outbox sans confirmation du consommateur : {len(unknown)}")
    for row in unknown:
        console.print(f"  {row.signal_id} {row.symbol} {row.strategy} (entrées jusqu'à {row.entry_expires_at[:16]})")
    if not yes:
        console.print("Vérifier le registre du consommateur (mêmes SIGNAL_ID / IDEMPOTENCY_KEY), puis relancer avec --yes.")
        raise typer.Exit(1)
    with _exclusive(settings):
        resume_publication(settings)
        console.print(f"publication reprise ; registre : {registry.reconcile()}")


@app.command("exit-policies")
def exit_policies_command(write: bool = typer.Option(False, "--write",
                                                     help="Réécrit config/exit_policies.json (registre partagé)")):
    """Politiques de sortie partagées avec l'exécuteur : identifiant, empreinte et règles."""
    from .backtest.exits import policies_document
    settings = _settings()
    document = policies_document()
    table = Table("politique", "empreinte", "TP", "stop", "ordre stop", "sortie temporelle",
                  title="Le backtest et l'exécuteur doivent reconnaître le même identifiant ET la même empreinte")
    for pid, entry in document["policies"].items():
        rules = entry["rules"]
        stop_order = rules["stop_order"] + (f" −{rules['stop_limit_offset_bps']} pb" if rules["stop_limit_offset_bps"] else "")
        table.add_row(pid, entry["hash"], rules["tp_execution"], rules["stop_rule"], stop_order,
                      "oui" if rules["time_exit"] else "non")
    console.print(table)
    if write:
        path = settings.root / "config" / "exit_policies.json"
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        console.print(f"écrit : {path}")


@app.command("signals-reconcile")
def signals_reconcile():
    """Termine les publications interrompues (PENDING) et nettoie les .tmp orphelins."""
    from .live.backup import publication_suspended
    from .signals.outbox import SignalRegistry
    settings = _settings()
    with _exclusive(settings):
        counts = SignalRegistry(settings.signals_db, settings.publication_dir()).reconcile(
            publish=publication_suspended(settings) is None)
    console.print(counts)


forward_app = typer.Typer(no_args_is_help=True,
                          help="Tests EN DIRECT pré-inscrits (docs/FORWARD_TESTS.md) : simulés et journalisés, aucun ordre.")
app.add_typer(forward_app, name="forward")


@forward_app.command("start")
def forward_start(test_id: str = typer.Argument(..., help="Identifiant pré-inscrit, par exemple F1_MAKER_TAKER")):
    """Démarre un test pré-inscrit : empreintes figées, liste halal figée, essai enregistré. Une seule fois."""
    from .forward.halal import HalalNotValidated
    from .forward.registry import AlreadyStarted, DirtyCode, NotPreregistered, start
    from .forward.tests import BY_ID
    settings = _settings()
    if test_id not in BY_ID:
        console.print(f"[red]Test inconnu :[/red] {test_id} (connus : {', '.join(BY_ID)})")
        raise typer.Exit(2)
    try:
        data = start(settings, BY_ID[test_id][0], now=_now())
    except (NotPreregistered, AlreadyStarted, DirtyCode, HalalNotValidated) as exc:
        console.print(f"[red]Démarrage refusé :[/red] {exc}")
        raise typer.Exit(3) from None
    console.print(f"{test_id} démarré le {data['started_at']} (essai {data['run_id']}, commit {data['commit'][:12]}) ; "
                  f"revue intermédiaire le {data['interim_at'][:10]}, évaluation le {data['final_at'][:10]} ; "
                  f"{len(data['halal']['symbols'])} paires halal figées.")


@forward_app.command("status")
def forward_status():
    """État de chaque test et intégrité de son journal."""
    from .forward import derivlog
    from .forward.registry import journal_for, status
    from .forward.tests import TESTS
    settings = _settings()
    table = Table("Test", "État", "Démarrage", "Évaluation", "Journal")
    for test, _ in TESTS:
        state = status(settings, test, now=_now())
        check = journal_for(settings, test.test_id).verify()
        start = state["start"] or {}
        table.add_row(test.test_id, state["state"], str(start.get("started_at", "—"))[:16],
                      str(start.get("final_at", "—"))[:10],
                      f"intègre ({check['entries']})" if check["ok"] else f"[red]ROMPU : {check['reason']}[/red]")
    console.print(table)
    log = derivlog.summary(settings)
    console.print(f"Relevé des dérivés : {log['days']} jours ; journal "
                  f"{'intègre' if log['verified']['ok'] else 'ROMPU : ' + str(log['verified']['reason'])}.")


@forward_app.command("run")
def forward_run():
    """Un passage complet maintenant (gel vérifié, décisions, résolutions, relevé des dérivés, rapport du jour)."""
    from .forward.runner import daily
    settings = _settings()
    console.print(daily(settings, now=_now(), force=True))


@forward_app.command("report")
def forward_report():
    """Écrit le rapport du jour (reports/forward/<jour>.md) et l'affiche."""
    from .forward.report import write
    settings = _settings()
    path = write(settings, now=_now())
    console.print(path.read_text(encoding="utf-8"))


assistant_app = typer.Typer(no_args_is_help=True,
                            help="Assistant de marché (docs/ASSISTANT.md) : appels en SHADOW mesurés par le test en direct "
                                 "F18 ; aucun ordre, aucun gain démontré.")
app.add_typer(assistant_app, name="assistant")


@assistant_app.command("evaluer")
def assistant_evaluate(now: str = typer.Option(None, "--now", help="Instant de l'évaluation (ISO, UTC) ; défaut : maintenant"),
                       magasin: Path = typer.Option(None, "--magasin", help="Dossier de bougies à lire à la place du magasin "
                                                                           "de F15 (vérification à la main hors conteneur)"),
                       sans_carnet: bool = typer.Option(False, "--sans-carnet", help="Ne pas interroger le carnet Binance "
                                                                                     "(candidats refusés CARNET_INJOIGNABLE)"),
                       verbose: bool = False):
    """Évaluation À BLANC de la dernière clôture 4 h UTC : rien n'est journalisé, aucun message déposé, aucun état
    écrit. Lecture seule des bougies, du feu, de la prévision et des news ; carnet public pour les seuls survivants."""
    from .assistant import evaluate as ev
    from .assistant import rules as R
    from .data.store import CandleStore
    settings = _settings(verbose)
    moment = pd.Timestamp(now, tz="UTC") if now and pd.Timestamp(now).tzinfo is None else (pd.Timestamp(now) if now else pd.Timestamp(_now()))
    at = ev.closing_time(moment)
    store = CandleStore(magasin) if magasin else ev.store_for(settings)
    symbols = ev.universe(settings)
    if magasin:
        present = sorted(p.name for p in (Path(magasin) / "candles" / "binance" / "spot").glob("*") if (p / "1h.parquet").exists())
        symbols = [s for s in symbols if s in present] or present
    if not symbols:
        console.print("[red]Aucune paire à évaluer[/red] (ni journal F15, ni liste halal validée, ni magasin).")
        raise typer.Exit(3)
    book = (lambda symbol: None) if sans_carnet else None
    with console.status(f"évaluation à blanc de la clôture {at:%Y-%m-%d %H:%M} UTC sur {len(symbols)} paires…"):
        out = ev.run(settings, at=at, now=moment, discipline={"active": {}, "rest_until": {}, "calls_today": 0},
                     store=store, symbols=symbols, book=book)
    console.print(f"Clôture évaluée : {out['at']} (données connues à {out['evaluated_at']}) ; feu {out['light']['color']} ; "
                  f"BTC {'au-dessus' if out['btc'].get('above_ema50') else 'sous'} son EMA50 journalière"
                  + (f" ; SILENCE : {out['silence']}" if out["silence"] else f" ; taille {out['size']}"))
    console.print(f"Régimes : {out['regimes']} ; candidats : {out['candidates']} ; refus : {out['refusals_by_reason'] or 'aucun'}")
    table = Table("Paire", "Régime", "Lecture", "Configuration / refus")
    for symbol, p in sorted(out["pairs"].items()):
        setup = p.get("setup") or {}
        table.add_row(symbol, p["regime"], p["reason"][:70], (p.get("refusal") or setup.get("reason") or "")[:60])
    console.print(table)
    for r in out["refusals"]:
        console.print(f"- {r['symbol']} ({r['regime']}, {r['setup']}) : {r['reason']} — {r['detail']}")
    for c in out["calls"]:
        console.print(f"[bold]APPEL {c['symbol']}[/bold] {c['regime']} {c['setup']} : entrée {c['entry']:.8g}, stop {c['stop']:.8g} "
                      f"(secours {c['hard_stop']:.8g}), TP1 {c['tp1']:.8g}, TP2 {c['tp2']:.8g} ({c['r_tp2']:.2f} R), score {c['score']}")
        for line in c["explanation"]:
            console.print(f"  {line}")
    console.print(f"[dim]{ev.NOTE} Évaluation à blanc : rien n'est journalisé. Règles : {R.TEST_ID}, docs/ASSISTANT.md.[/dim]")


@assistant_app.command("etat")
def assistant_state():
    """Dernier état de l'assistant (state/assistant.json) : résumé, appels actifs, derniers refus, boîte Telegram."""
    from .assistant import outbox, state
    settings = _settings()
    data = state.read(settings)
    console.print(data.get("resume", ""))
    if not data.get("available"):
        console.print(f"[yellow]{data.get('reason', '')}[/yellow]")
        return
    table = Table("Paire", "Régime", "Configuration", "Entrée", "Stop", "TP1", "TP2", "R latent", "Score")
    for c in data.get("active_calls") or []:
        table.add_row(c["symbol"], c["regime"], c["setup"], f"{c['entry']:.8g}", f"{c['stop']:.8g}", f"{c['tp1']:.8g}",
                      f"{c['tp2']:.8g}", "—" if c.get("latent_r") is None else f"{c['latent_r']:+.2f}", f"{c['score']:.0f}")
    console.print(table)
    for r in (data.get("last_refusals") or [])[-10:]:
        console.print(f"- {r.get('at', '')[:16]} {r['symbol']} ({r['regime']}) : {r['reason']} — {r.get('detail', '')[:100]}")
    console.print(f"Boîte Telegram : {outbox.counts(settings)}")
    console.print(f"[dim]{data.get('note', '')}[/dim]")
