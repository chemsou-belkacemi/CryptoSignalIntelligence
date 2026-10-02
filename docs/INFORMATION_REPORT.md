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

## 4. Résultats (`INFO-20261002T233003Z-a12430`, commit `e98d232`, 0 essai) — lecture descriptive

102 cellules lues : **≈ 5 fausses alarmes attendues** à 5 % sans aucune information. Aucune cellule n'est retenue.

**ML : aucune information directionnelle** (corrélation de rang quotidienne entre p et l'excès du même instant).

| Source | Lignes | Corrélation [IC95] | Décile haut − bas | Décile le plus confiant : brut / net / excès | Trades entrés : net / excès |
|---|---|---|---|---|---|
| swing 7 j | 99 840 | +0,006 [−0,027 ; +0,038] | −0,09 % | −0,39 / −0,65 / −0,15 % | 443 : +1,31 / +0,25 % |
| intraday 4 h | 1 959 344 | −0,004 [−0,009 ; −0,000] | +0,01 % | +0,06 / −0,20 / +0,01 % | 1 769 : +0,05 / +0,09 % |
| swing long 7 j | 355 032 | +0,011 [−0,015 ; +0,037] | −0,35 % | +0,20 / −0,06 / −0,15 % | 1 203 : +2,45 / +1,05 % |

- Les déciles ne sont pas ordonnés (le plus confiant n'est pas le meilleur) et les corrélations changent de
  signe d'une année à l'autre. Le contrôle positif montre que 0,017 aurait été vu à coup sûr : **l'information
  est absente, pas mal mesurée.**
- Les trades **réellement entrés** du swing long ont un excès de +1,05 % (1 203 trades) : sélection par le seuil
  ET par les limites de risque (positions maximales, exposition), sans intervalle ici ; c'est une cellule parmi
  102, déjà jugée par son protocole (rejeté). Hypothèse seulement.

**Walk-forwards : l'avantage brut est nul, les frais font la perte.**

| Stratégie | R central [IC95] | Défavorable | Stress | Rendement brut / net par trade |
|---|---|---|---|---|
| A cassure Donchian | −0,11 [−0,19 ; −0,04] | −0,13 | −0,15 | +0,01 % / −0,19 % |
| B repli EMA | −0,16 [−0,22 ; −0,10] | −0,18 | −0,17 | −0,11 % / −0,31 % |
| C retour en range | −0,19 [−0,27 ; −0,11] | −0,27 | −0,40 | +0,02 % / −0,18 % |

- Par régime de volatilité, la seule cellule non négative est C en **volatilité haute** (453 trades, brut +0,37 %,
  R −0,02 [−0,17 ; +0,13]) : hypothèse seulement. Toutes les années sont négatives pour les trois stratégies.
- Les régimes de tendance sont constants par stratégie (A et B en BULL, C en RANGE : ce sont leurs filtres), donc
  sans information ici.

**Volatilité : bien classée, un peu mal étalonnée aux extrêmes** (réalisé / prévu par décile de la prévision).

| Horizon | Décile le plus bas | Médian | Plus haut | Pente log | Corrélation log |
|---|---|---|---|---|---|
| 1 j | 1,43 | ≈ 1,02 | 1,00 | 0,95 | 0,73 |
| 3 j | 1,31 | ≈ 1,00 | 0,92 | 0,93 | 0,72 |
| 7 j | 0,99 | ≈ 0,90 | 0,86 | 0,99 | 0,72 |

- À 1 et 3 jours, quand la prévision est la plus basse, la volatilité réalisée est 30 à 43 % plus forte (retour
  vers la moyenne sous-estimé) ; à 7 jours, la prévision est trop haute d'environ 10 % partout. Piste d'un
  réétalonnage, à pré-inscrire (F12 mesure déjà ces prévisions en direct).

## Historique

- 2026-10-03 : déclaré avant toute exécution.
- 2026-10-02 23:30 UTC (heure du serveur) : exécuté, `INFO-20261002T233003Z-a12430` ; § 4 ajouté.
