# Registre unifié des prédictions et rapport d'information — déclaré le 2026-10-03 avant exécution

Code : `research/information_report.py`. Tests : `tests/test_information_report.py`. Commande : `csi information-report`.

**Question.** Les prédictions hors échantillon que le projet a déjà produites contiennent-elles une information
mesurable, et où se perd-elle (seuil, coûts, régime, année) ? C'est la séparation demandée : « le modèle
contient-il de l'information ? » d'un côté, « la stratégie la transforme-t-elle en trade ? » de l'autre.

**Ce que ce n'est pas.** Aucun modèle n'est ajusté, aucun verdict n'est rendu, **0 essai**. Toutes ces prédictions
ont déjà été jugées par leur protocole (rejetées) ; les relire par décile, régime ou année est une **description**.
Une cellule qui semble positive n'est qu'une hypothèse : elle doit être pré-inscrite et mesurée sur des données
jamais vues (test final réservé, ou en direct), jamais adoptée sur cette lecture.

## 1. Sources (hors échantillon, déjà enregistrées, DEVELOPMENT)

| Source | Exécution | Contenu |
|---|---|---|
| ML_SWING | `MLS-20261001T085521Z-a04c71` | probabilité de hausse à 7 jours, 16 paires, chaque instant de décision valide |
| ML_INTRADAY | `MLI-20261001T025936Z-0fab30` | probabilité à 4 h, 16 paires |
| ML_SWING_LONG | `MLL-20261001T194110Z-090770` | probabilité à 7 jours, 40 paires, 2017-2025 |
| A, B, C | `WF-20260930T143103Z-4b0da8`, `…150703Z-5eccae`, `…153623Z-7dd6cc` | trades hors échantillon, coûts central / défavorable / stress |
| Volatilité | `VOL-20261002T170500Z-c3bda6` (`REF_SERVICE`) | variance prévue à 1, 3, 7 jours |

## 2. Schéma unifié (`reports/<run_id>/predictions.parquet`)

`source, family, symbol, origin, horizon_h, score, outcome, outcome_unit, outcome_gross, excess, cost_rt_bps,
regime_trend, regime_vol, decision`. Pour le ML : score = probabilité, outcome = rendement net, brut ≈ net + coût
aller-retour (approximation additive déclarée), excès = net − moyenne de toutes les décisions du même instant. Pour
les walk-forwards : outcome = R net central, régimes de l'instant de setup, rendement brut de la simulation.

## 3. Mesures

- **ML** : corrélation de rang quotidienne entre la probabilité et l'excès (IC95 par blocs calendaires de
  max(10, 2 × H) jours) ; excès moyen par décile ; écart décile haut − bas ; corrélation par année ; décile le plus
  confiant : brut, net, excès et IC95 de l'excès ; trades réellement entrés : net et excès.
- **Walk-forwards** : R moyen et IC95 (blocs de 10 jours) par scénario de coûts ; rendement brut et net ; par
  régime de tendance, par régime de volatilité, par année ; médianes MAE / MFE.
- **Volatilité** : étalonnage par décile de la variance prévue (réalisé / prévu), pente et corrélation de
  log réalisé sur log prévu.
- **Multiplicité** : nombre de cellules lues et nombre de fausses alarmes attendues à 5 % sans aucune information,
  inscrits avec le rapport. Le contrôle positif (`POSITIVE_CONTROL.md`) donne la taille minimale détectable de ces
  mesures.

## Historique

- 2026-10-03 : déclaré avant toute exécution.
