"""Interface en ligne de commande : affichage uniquement, aucune logique métier ici."""
from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

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
                          workers: int = typer.Option(6, help="Paires téléchargées en parallèle"),
                          verbose: bool = False):
    """Historique LONG (bougies 1 h depuis la cotation de chaque paire) dans un magasin séparé, pour la recherche
    à basse fréquence. Les protocoles déjà exécutés gardent leur magasin (depuis 2021-01)."""
    from .research.long_history import download_long
    settings = _settings(verbose)
    symbols = [s.upper() for s in symbol] if symbol else list(settings.data.symbols)
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
             verbose: bool = False):
    """Lot 5 ter : ML swing de 1 à 7 jours (docs/ML_SWING.md) — sélection avec la règle v6, puis estimation unique."""
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
