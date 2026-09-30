"""Rapport Markdown d'un walk-forward (reports/<run_id>/report.md)."""
from __future__ import annotations

from .backtest_report import COMMON_LIMITS, _fmt

VERDICT_TEXT = {
    "VALIDATED_OOS": "tous les critères d'admission sont satisfaits hors échantillon",
    "INCONCLUSIVE": "E[R] centrale positive, mais au moins un critère n'est pas satisfait",
    "REJECTED": "E[R] nette hors échantillon <= 0 en coûts centraux",
}

WALK_FORWARD_LIMITS = """- **Période DEVELOPMENT uniquement** : le test final réservé n'est pas consulté. Cette période a
  déjà servi au backtest de référence du lot 1 (stratégie A) : la grille de A a été fixée après
  avoir vu ce backtest (les critères d'admission l'étaient avant) ; celles de B et C avant tout résultat.
- **Sélection sans abstention** : chaque fenêtre est jouée avec la meilleure combinaison
  d'entraînement, même si son score est négatif.
- **Verdict ≠ promotion** : le statut de la configuration n'est pas modifié automatiquement.
"""


def _params(values: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in values.items())


def render_markdown(payload: dict) -> str:
    period, config = payload["period"], payload["config"]
    windows = payload["windows"]
    lines = [
        f"# Walk-forward {payload['strategy']} v{payload['strategy_version']} — {payload['run_id']}",
        "",
        f"- Hypothèse : {payload['hypothesis']}",
        f"- Période : **{period['label']}** du {period['start'][:10]} au {period['end'][:10]} ; "
        f"paires : {', '.join(payload['symbols'])}",
        f"- Fenêtres : entraînement ancré d'au moins {config['train_min_months']} mois, test de "
        f"{config['test_months']} mois, {len(windows)} fenêtres ; purge : un trade d'entraînement doit être "
        "clos avant le début du test",
        f"- Recalibrage : grille {_params(payload['grid'])} = **{payload['n_trials']} essais** ; sélection par "
        f"plateau (E[R] moyenne avec les voisines de grille), au moins {config['min_train_trades']} trades "
        f"d'entraînement ; valeurs v1 : {_params(payload['defaults'])}",
        f"- **Verdict : {payload['verdict']}** — {VERDICT_TEXT.get(payload['verdict'], '')}",
        "",
        "## Critères d'admission (docs/PROTOCOL.md)",
        "",
        "| # | Critère | Résultat | Détail |",
        "|---|---|---|---|",
    ]
    lines += [f"| {c['number']} | {c['label']} | {'✔' if c['passed'] else '✘'} | {c['detail']} |"
              for c in payload["criteria"]]
    lines += ["", "## Fenêtres de test", "",
              "| Test | Paramètres retenus | Sélection | Trades entr. | E[R] entr. | Plateau | Trades test | E[R] test |",
              "|---|---|---|---|---|---|---|---|"]
    for w in windows:
        lines.append(f"| {w['test_start'][:10]} → {w['test_end'][:10]} | {_params(w['chosen'])} | {w['selection']} "
                     f"| {w['train_trades']} | {_fmt(w['train_expectancy_r'])} | {_fmt(w['plateau_score'])} "
                     f"| {w.get('oos_trades', 0)} | {_fmt(w.get('oos_expectancy_r'))} |")
    lines += ["", "## Agrégat hors échantillon (signaux indépendants)", "",
              "| Variante | Coûts | Candidats | Clos | Expirés | Gagnants | E[R] | IC95 E[R] | E[R] optimiste | PF (R) "
              "| DD (R) | Net moy. % | Expo % | Trades/mois |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, s in payload["oos"].items():
        variant, scenario = label.split("/")
        lines.append("| " + " | ".join(str(x) for x in [
            variant, scenario, s["candidates"], s["trades_closed"], s["entries_expired"], _fmt(s.get("win_rate")),
            _fmt(s.get("expectancy_r")), _fmt(s.get("expectancy_r_ci95_block_bootstrap")),
            _fmt(s.get("expectancy_r_optimistic_bound")), _fmt(s.get("profit_factor_r")),
            _fmt(s.get("max_drawdown_r_closed_trades")), _fmt(s.get("avg_net_return_pct")),
            _fmt(s.get("exposure_pct")), _fmt(s.get("trades_per_month"))]) + " |")
    lines += ["", "`v1_fixe` : paramètres v1 sans recalibrage, sur les mêmes fenêtres (référence). "
              "`profil_BSM` : mêmes signaux, sorties selon le profil observé de BinanceSpotManager "
              "(TP au marché sur déclenchement, stop fixe, aucune sortie temporelle ; docs/BSM_PROFILE.md).", "",
              "## Références sur la période hors échantillon", "",
              "| Référence | Rendement % | DD mark-to-market % | Exposition % |", "|---|---|---|---|"]
    for name, b in payload["baselines"].items():
        lines.append(f"| {name} | {_fmt(b.get('return_pct'))} | {_fmt(b.get('max_drawdown_pct_mark_to_market'))} "
                     f"| {_fmt(b.get('exposure_pct'))} |")
    base = payload["oos"].get("base/central", {})
    if base.get("trades_closed"):
        lines += ["", "## Détail — base, coûts centraux", "", f"Sorties : {base['exit_reasons']}  ",
                  f"MAE moyen {base['mae_r_avg']} R, MFE moyen {base['mfe_r_avg']} R, "
                  f"durée moyenne {base['avg_bars_held']} bougies", ""]
        for title, key in (("Par paire", "by_symbol"), ("Par année", "by_year"), ("Par tendance 1h", "by_trend_regime"),
                           ("Par volatilité 1h", "by_volatility_regime")):
            lines += [f"**{title}**", "", "| Groupe | Trades | E[R] | Gagnants |", "|---|---|---|---|"]
            lines += [f"| {k} | {v['trades']} | {v['expectancy_r']} | {v['win_rate']} |" for k, v in base[key].items()]
            lines.append("")
    if base:
        lines += ["## Motifs NO_TRADE (base, coûts centraux, fenêtres de test)", ""]
        lines += [f"- {k} : {v}" for k, v in base["no_trade_reasons"].items()]
        lines.append("")
    lines += ["## Limites connues", "", COMMON_LIMITS + WALK_FORWARD_LIMITS]
    return "\n".join(lines)
