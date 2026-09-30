"""Rapport Markdown lisible d'un lot de backtest (reports/<batch_id>/report.md)."""
from __future__ import annotations

COMMON_LIMITS = """- **Signaux indépendants**, pas un portefeuille : aucun partage de capital entre paires,
  pas de limite de positions simultanées. Les rendements ne s'additionnent pas en capital.
- **Univers non point-in-time** : paires choisies aujourd'hui (cotation, liquidité et
  screening actuels), donc biais de survie et de sélection sur l'historique ; aucune
  généralisation aux cryptos absentes de l'univers (docs/UNIVERSE.md).
- **Coûts hypothétiques** : frais, glissement et demi-spread fixés par scénario ; le spread
  historique n'est pas reconstitué. Ce ne sont pas les tarifs réels d'un compte.
- **Données de marché réel** (Binance Spot public) ; les prix et remplissages Binance Demo diffèrent.
- **Remplissages OHLCV** : ordre intrabougie inconnu, convention pessimiste ; les cas
  ambigus sont comptés et une borne optimiste est fournie.
- **Profil théorique** (variantes `base`, ablations) : un TP limite, un SL fixe, sortie au bout de
  `max_hold_bars`. La variante `profil_BSM` rejoue les mêmes signaux selon le profil observé de
  BinanceSpotManager (docs/BSM_PROFILE.md) ; latence de détection des remplissages et
  décalage du stop-limite non modélisés.
- **Drawdown sur trades clos** en R, pas en mark-to-market.
"""

LIMITS = ("## Limites connues de cette simulation\n\n" + COMMON_LIMITS
          + "- Backtest sur toute la période, sans recalibrage : **pas une validation hors échantillon** "
            "(voir la commande `walk-forward`).\n")


def _fmt(value, suffix=""):
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3f}{suffix}" if abs(value) < 100 else f"{value:.1f}{suffix}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(f"{v:.3f}" for v in value) + "]"
    return f"{value}{suffix}"


def render_markdown(payload: dict) -> str:
    period = payload["period"]
    lines = [
        f"# Backtest {payload['strategy']} v{payload['strategy_version']} — {payload['batch_id']}",
        "",
        f"- Période : **{period['label']}** du {period['start'][:10]} au {period['end'][:10]}",
        f"- Paires : {', '.join(payload['symbols'])}",
        f"- Verdict : **{payload['verdict']}** (le verdict vient du walk-forward, pas de ce backtest)",
    ]
    if payload["final_test_consultations"]:
        lines.append(f"- ⚠ Test final consulté {payload['final_test_consultations']} fois : "
                     "ce n'est plus un test vierge, réserver une nouvelle période future.")
    lines += ["", "## Résultats par variante et scénario de coûts", "",
              "| Variante | Coûts | Candidats | Remplis | Expirés | Clos | Censurés | Gagnants | E[R] | IC95 E[R] "
              "| E[R] optimiste | PF (R) | DD (R) | Net moy. % | Expo % | Trades/mois | Ambigus |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in payload["results"]:
        s = r["summary"]
        lines.append("| " + " | ".join(str(x) for x in [
            r["variant"], r["scenario"], s["candidates"], s["entries_filled"], s["entries_expired"],
            s["trades_closed"], s["trades_censored"], _fmt(s.get("win_rate")), _fmt(s.get("expectancy_r")),
            _fmt(s.get("expectancy_r_ci95_block_bootstrap")), _fmt(s.get("expectancy_r_optimistic_bound")),
            _fmt(s.get("profit_factor_r")), _fmt(s.get("max_drawdown_r_closed_trades")),
            _fmt(s.get("avg_net_return_pct")), _fmt(s.get("exposure_pct")), _fmt(s.get("trades_per_month")),
            s.get("ambiguous_trades", "–")]) + " |")
    lines += ["", "## Références", "", "| Référence | Rendement % | DD mark-to-market % | Exposition % |",
              "|---|---|---|---|"]
    for name, b in payload["baselines"].items():
        lines.append(f"| {name} | {_fmt(b.get('return_pct'))} | {_fmt(b.get('max_drawdown_pct_mark_to_market'))} "
                     f"| {_fmt(b.get('exposure_pct'))} |")
    lines.append("")
    lines.append("Une stratégie peu exposée ne poursuit pas le même objectif qu'un buy-and-hold investi en "
                 "permanence : comparer aussi l'exposition et le drawdown, pas seulement le rendement.")
    base = next((r for r in payload["results"] if r["variant"] == "base" and r["scenario"] == "central"), None)
    if base and base["summary"].get("trades_closed"):
        s = base["summary"]
        lines += ["", "## Détail — base, coûts centraux", "",
                  f"Sorties : {s['exit_reasons']}  ", f"MAE moyen {s['mae_r_avg']} R, MFE moyen {s['mfe_r_avg']} R, "
                  f"durée moyenne {s['avg_bars_held']} bougies", ""]
        for title, key in (("Par paire", "by_symbol"), ("Par tendance 1h", "by_trend_regime"),
                           ("Par volatilité 1h", "by_volatility_regime"), ("Par année", "by_year")):
            lines += [f"**{title}**", "", "| Groupe | Trades | E[R] | Gagnants |", "|---|---|---|---|"]
            lines += [f"| {k} | {v['trades']} | {v['expectancy_r']} | {v['win_rate']} |" for k, v in s[key].items()]
            lines.append("")
    if base:
        lines += ["## Motifs NO_TRADE (base, coûts centraux)", ""]
        lines += [f"- {k} : {v}" for k, v in base["summary"]["no_trade_reasons"].items()]
        lines.append("")
    lines.append(LIMITS)
    return "\n".join(lines)
