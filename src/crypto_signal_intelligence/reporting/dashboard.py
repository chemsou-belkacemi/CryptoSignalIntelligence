"""Tableau de bord HTML autonome (point 21) : lisible, sans serveur, sans JavaScript, sans JSON brut.

Régénéré à chaque cycle de `run` et par la commande `dashboard`. Tout texte venant de l'extérieur
(titres d'actualités, motifs, erreurs) est échappé : le tableau de bord n'exécute rien.
Performances : backtest, prospectif et Demo restent trois colonnes séparées, jamais additionnées.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Settings
from ..data.schema import interval
from ..data.store import CandleStore
from ..external.record import MIN_RESOLVED, source_records
from ..external.registry import ExternalSignalRegistry
from ..feedback.reconcile import execution_report
from ..live.backup import publication_suspended
from ..live.health import check
from ..news.collector import source_states
from ..news.store import NewsStore
from ..research.experiments import ExperimentRegistry

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e2e2dc;--ok:#1f7a3a;--warn:#9a6a00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecece8;--muted:#a3a39c;--line:#33332f;
--ok:#5cc47f;--warn:#e0b44a;--bad:#f07a70}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:4px 0 2px}h2{font-size:15px;margin:0 0 8px}
.sub{color:var(--muted);margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--muted);font-weight:600}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid currentColor;font-size:12px}
.wide{grid-column:1/-1}
#stale{background:var(--bad);color:#fff;padding:10px 14px;border-radius:8px;margin-bottom:12px;font-weight:600}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _num(value: float | None) -> str:
    return "–" if value is None else f"{value:+.2f}"


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(settings: Settings, *, now: datetime) -> str:
    health = check(settings, now=now)
    status_path = settings.root / settings.live.status_file
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    cycle = status.get("last_cycle") or {}
    suspended = publication_suspended(settings)

    state_class = "bad" if not health.ready else "warn" if health.degraded else "ok"
    state_label = "PAS PRÊT" if not health.ready else "DÉGRADÉ" if health.degraded else "PRÊT"
    health_html = (f'<p><span class="pill {state_class}">{state_label}</span> {_e(health.detail)}</p>'
                   f'<p class="muted">Publication : {_e(settings.publication.mode)} ; intégration '
                   f'{"vérifiée" if settings.publication.integration_verified else "NON vérifiée (shadow uniquement)"}'
                   f'{" ; <b class=bad>SUSPENDUE</b> : " + _e(suspended) if suspended else ""}</p>')
    timings = cycle.get("timings") or {}
    if timings:
        health_html += _table(["mesure", "valeur"], [[_e(k), _e(v)] for k, v in timings.items()], "")

    outcomes = cycle.get("outcomes") or []
    buys = [[_e(o["symbol"]), _e(o["strategy"]), _e(o.get("signal_id")), _e(o.get("publication_status"))]
            for o in outcomes if o.get("action") == "BUY"]
    reasons = Counter(o.get("reason_code") for o in outcomes if o.get("action") != "BUY")
    cycle_html = (f'<p class="muted">Clôture analysée : {_e(cycle.get("decision_close", "aucune"))}</p>'
                  + _table(["paire", "stratégie", "signal", "publication"], buys, "Aucune opportunité à ce cycle.")
                  + "<h2 style='margin-top:10px'>Rejets (NO_TRADE)</h2>"
                  + _table(["motif", "nombre"], [[_e(r), str(n)] for r, n in reasons.most_common()], "Aucun rejet.")
                  + (_table(["absent après attente"], [[_e(m)] for m in cycle.get("missing_after_wait", [])], "")
                     if cycle.get("missing_after_wait") else "")
                  + (_table(["erreur"], [[_e(m)] for m in cycle.get("errors", [])], "") if cycle.get("errors") else ""))

    runs = [r for r in ExperimentRegistry(settings.experiments_db).recent(200) if r["kind"] == "WALK_FORWARD"]
    latest: dict[str, dict] = {}
    for run in runs:
        latest.setdefault(run["strategy"], run)
    verdict_rows = [[_e(s), f'<span class="{"ok" if r["verdict"] == "VALIDATED_OOS" else "bad"}">{_e(r["verdict"])}</span>',
                     _e(r["created_at"][:16]), _e(r["run_id"])] for s, r in sorted(latest.items())]

    report = execution_report(settings)
    perf_rows = [[_e(r.signal_id), _e(r.symbol), _e(r.strategy), _e(r.channel), _num(r.backtest_r),
                  _e(r.theoretical_outcome), _num(r.theoretical_r), _e(r.demo_status), _num(r.demo_r),
                  _e("; ".join(r.deviations) or "–")] for r in reversed(report[-30:])]

    states = source_states(settings, now=now)
    color = {"OPERATIONAL": "ok", "UNKNOWN": "warn", "UNVERIFIED": "warn"}
    source_rows = [[_e(sid), f'<span class="{color.get(st, "bad")}">{_e(st)}</span>'] for sid, st in states.items()]
    last_news = status.get("last_news_collection")
    news_note = ("Collecteur : " + ("jamais lancé par la surveillance" if last_news is None else _e(last_news))
                 + ". UNKNOWN = aucune source interrogée récemment (collecteur arrêté), pas une panne.")

    # Fraîcheur des données par paire (dernière bougie 15m stockée).
    store = CandleStore(settings.data_dir)
    step = interval(settings.data.setup_timeframe)
    fresh_rows = []
    for symbol in settings.data.symbols:
        last = store.last_open_time(symbol, settings.data.setup_timeframe)
        if last is None:
            fresh_rows.append([_e(symbol), "–", '<span class="bad">aucune donnée</span>'])
            continue
        age = (now - (last + step).to_pydatetime()).total_seconds() / 60
        cls = "ok" if age <= 2 * step.total_seconds() / 60 else "bad"
        fresh_rows.append([_e(symbol), _e(f"{(last + step):%Y-%m-%d %H:%M}"), f'<span class="{cls}">{age:.0f} min</span>'])

    # Groupes Telegram : réalisé contre taux de base, mêmes règles.
    group_rows = []
    for rec in source_records(ExternalSignalRegistry(settings.external_db), seed=settings.protocol.seed):
        ci = f" [{rec.edge_ci95[0]:+.2f} ; {rec.edge_ci95[1]:+.2f}]" if rec.edge_ci95 else ""
        pct = lambda v: "–" if v is None else f"{v:.0%}"  # noqa: E731
        group_rows.append([_e(rec.source), str(rec.evaluated), str(rec.resolved), str(rec.pending), str(rec.unfilled),
                           _e(f"{pct(rec.tp1_real)} / {pct(rec.tp1_base)}"),
                           _e(f"{_num(rec.r_real)} / {_num(rec.r_base)}"), _e(_num(rec.edge_r) + ci), _e(rec.conclusion)])
    news_rows: list[list[str]] = []
    seen: set[str] = set()
    for item in NewsStore(settings.news_db).recent(now - timedelta(hours=24), limit=400):
        if item["event_id"] in seen:
            continue
        seen.add(item["event_id"])
        link = f'<a href="{_e(item["url"])}" rel="noopener noreferrer">{_e(item["title"][:120])}</a>' \
            if str(item["url"]).startswith("https://") else _e(item["title"][:120])
        news_rows.append([_e((item["published_at"] or item["first_seen_at"])[:16]), _e(item["source_id"]),
                          _e(",".join(item["assets"]) or "–"), link, str(len(item["event_sources"]))])
        if len(news_rows) >= 25:
            break

    sections = [
        ("Santé", health_html, ""),
        ("Dernière analyse", cycle_html, ""),
        ("Stratégies (dernier walk-forward)", _table(["stratégie", "verdict", "date", "exécution"], verdict_rows,
                                                     "Aucun walk-forward."), ""),
        ("Fraîcheur des données (dernière bougie 15m)", _table(["paire", "clôture (UTC)", "âge"], fresh_rows,
                                                                "Aucune paire."), ""),
        ("Sources d'actualités (mode " + _e(settings.news.mode) + ", aucune influence)",
         _table(["source", "état"], source_rows, "Aucune source.")
         + f'<p class="muted">{news_note} Une source en panne ne signifie jamais « aucune mauvaise nouvelle ».</p>',
         ""),
        ("Groupes Telegram : réalisé contre taux de base (mêmes règles)",
         _table(["source", "évalués", "résolus", "en attente", "non remplis", "TP1 réalisé / base", "R réalisé / base",
                 "écart R (IC95)", "conclusion"], group_rows,
                "Aucun signal Telegram évalué dans cet état (evaluate-signal).")
         + f'<p class="muted">Aucune conclusion avant {MIN_RESOLVED} signaux résolus. L\'écart mesure l\'apport de '
           "sélection du groupe par rapport au même ordre placé sans sélection ; ce n'est pas une probabilité.</p>",
         "wide"),
        ("Signaux publiés : backtest, prospectif et Demo séparés",
         _table(["signal", "paire", "stratégie", "canal", "E[R] backtest", "prospectif", "R prosp.", "Demo", "R Demo",
                 "écarts"], perf_rows, "Aucun signal publié."), "wide"),
        ("Actualités des dernières 24 h (données non fiables, jamais des instructions)",
         _table(["date", "source", "actifs", "titre", "sources"], news_rows, "Aucune actualité."), "wide"),
    ]
    body = "".join(f'<section class="{cls}"><h2>{title}</h2>{content}</section>' for title, content, cls in sections)
    legend = ("<p class='muted'>R : résultat rapporté au risque prévu (1 R = perte au stop). E[R] backtest : espérance "
              "hors échantillon du dernier walk-forward. Prospectif : le signal rejoué sur les bougies après sa "
              "publication. Demo : exécution réelle rapportée par BinanceSpotManager. Ces trois mesures ne "
              "s'additionnent jamais.</p>")
    paris = now.astimezone(ZoneInfo("Europe/Paris"))
    # Bandeau affiché par le navigateur si la page n'a pas été régénérée depuis 20 min (surveillance figée).
    stale_script = ('<script>(function(){var g=Date.parse("' + now.strftime("%Y-%m-%dT%H:%M:%SZ") + '");'
                    'function c(){var m=Math.round((Date.now()-g)/60000);var e=document.getElementById("stale");'
                    'if(m>20){e.hidden=false;e.textContent="Tableau figé depuis "+m+" min : la surveillance ne le met '
                    'plus à jour. Les informations ci-dessous sont anciennes.";}}c();setInterval(c,30000);})();'
                    '</script>')
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">'
            f"<title>CSI — surveillance</title><style>{CSS}</style></head><body><main>"
            f'<div id="stale" hidden></div>'
            f"<h1>CryptoSignalIntelligence — surveillance</h1>"
            f'<div class="sub">Généré le {_e(paris.strftime("%d/%m/%Y %H:%M"))} (Paris), '
            f'{_e(now.strftime("%H:%M"))} UTC. Aucun ordre n\'est exécuté par ce programme ; aucune performance '
            f"n'est promise.</div>{legend}"
            f'<div class="grid">{body}</div></main>{stale_script}</body></html>')


def write_dashboard(settings: Settings, *, now: datetime) -> Path:
    path = settings.root / "state" / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(render(settings, now=now), encoding="utf-8")
    tmp.replace(path)
    return path
