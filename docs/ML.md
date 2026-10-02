# Lot 5 — apprentissage (méta-labeling), protocole fixé avant la première exécution

Question : sur les setups qu'une stratégie trouve, un modèle sait-il distinguer à l'avance ceux qui
finiront gagnants, assez bien pour qu'une stratégie qui ne prend que ceux-là gagne de l'argent après
frais ?

Commande : `ml-evaluate [--strategy ID]` (travail lourd : priorité basse, mémoire plafonnée).
Code : `src/crypto_signal_intelligence/ml/` ; tests : `tests/test_ml.py` (données synthétiques).

## Historique du protocole

- **v1** (commit 3230ce8, 2026-10-01) : premier protocole. Sa première exécution s'est arrêtée avant
  tout résultat (plafond mémoire trop bas, corrigé à 6 Go de mémoire engagée) : **aucun résultat n'a
  été produit ni lu sous v1**.
- **v2** (2026-10-01, avant toute exécution réussie) : renforcé après la relecture du sous-agent
  `leak-auditor` (méthode jugée saine : pas de look-ahead, purge et intervalles corrects ; à compléter
  sur la mesure). Ajouts : stratégie filtrée re-simulée, coûts défavorables, concentration, intégrité,
  minimum d'événements par variable, convergence tracée, masque « BTC périmé », tests de jointure et
  de causalité.

## Protocole v2

| Élément | Choix |
|---|---|
| Jeu de données | setups de la stratégie, paramètres v1 déclarés, coûts centraux, période DEVELOPMENT ; trades remplis et clos seulement |
| Étiquette | R net > 0 (frais et glissement compris) |
| Variables (25) | volatilité (ATR / prix), RSI, distances aux EMA 20/50 et au plus haut Donchian, volume relatif, rendements 1 et 4 bougies, volatilité réalisée, position dans les bandes de Bollinger, pente, volatilité, rendement 24 h et distance à l'EMA 50 du contexte 1h, BTC (rendement 24 h, volatilité, pente ; inconnu si périmé, comme pour la stratégie), régimes (tendance, volatilité), heure (sinus, cosinus), géométrie du trade (stop en ATR, objectif en R) — toutes lues à l'instant de DÉCISION |
| Modèle | régression logistique L2 (λ = 1, pratiquement non pénalisée à ces tailles), variables standardisées sur l'entraînement seul ; convergence tracée par fenêtre |
| Validation | fenêtres du walk-forward (ancrées, 6 tests de 6 mois) ; entraînement = trades dont le setup ET la sortie précèdent le test (purge) ; fenêtre jugée seulement si la classe minoritaire compte au moins 10 trades par variable (250) |
| Règle de tri | garder un setup si sa probabilité prédite dépasse le taux de gain de l'entraînement ; **aucun seuil optimisé** |
| Stratégie filtrée | la stratégie RE-SIMULÉE fenêtre par fenêtre avec le modèle comme veto, au même endroit que la surveillance : un setup refusé libère la paire pour le suivant (le sous-ensemble des trades gardés n'est pas la stratégie déployable) |
| Essais | un par stratégie, comptés dans `program_trials` |

## Critères (agrégat hors échantillon, IC95 par blocs de 10 jours)

1. données intègres et causalité vérifiée sur données réelles (même contrôle que le walk-forward) ;
2. au moins 300 setups testés et 3 fenêtres jugées (sinon INSUFFICIENT_DATA) ;
3. score de Brier meilleur que le taux de base : IC95 de l'écart entièrement > 0 ;
4. les setups gardés font mieux que l'ensemble : IC95 de l'écart entièrement > 0 ;
5. stratégie filtrée : E[R] net > 0 en coûts centraux, IC95 entièrement > 0 ;
6. stratégie filtrée : E[R] net > 0 en coûts défavorables ;
7. stratégie filtrée : aucune paire ni année ne porte plus de 60 % du PnL en R.

Tous vrais → **USEFUL_OOS** ; sinon **NOT_USEFUL**. Les tests synthétiques montrent que le protocole
conclut USEFUL_OOS quand une relation réelle existe, et NOT_USEFUL sur du bruit, sous des coûts
défavorables perdants ou quand le gain vient d'une seule paire.

## Réserves (écrites avant de voir le moindre résultat)

- **Pseudo hors échantillon** : ces fenêtres ont déjà servi à juger et rejeter ces stratégies, et les
  variables ont été choisies en connaissant le criblage D–I. Le programme compte plus de 250 essais
  sur DEVELOPMENT ; trois de plus ici.
- Un USEFUL_OOS **ne branche rien** : il exige une confirmation sur des données non vues (signaux
  shadow prospectifs résolus après la date du modèle, ou une consultation unique et enregistrée du
  test final), puis une promotion explicite par le propriétaire. `ML_PROBABILITY` reste `NONE`.
- L'étiquette « R net > 0 » n'est pas l'objectif E[R] : la règle tend à écarter les setups à objectif
  lointain (moins de puissance, pas de biais favorable).
- La probabilité, si elle était un jour publiée, serait la fréquence prédite de « R net > 0 » pour un
  setup de cette stratégie, avec sa cible, son horizon et sa calibration (contrat V3) ; jamais une
  promesse de gain.

## Résultats (protocole v2, 2026-10-01) : NOT_USEFUL pour les trois stratégies

| Stratégie | Setups testés | AUC moy. fenêtres | Setups gardés vs tous (E[R]) | Stratégie filtrée re-simulée, coûts centraux | Défavorables | Rapport |
|---|---|---|---|---|---|---|
| A `DONCHIAN_VOLUME_BREAKOUT` | 6 949 | ≈ 0,52 | −0,26 → −0,21 R (44 % gardés) | 3 270 trades, −0,21 R [−0,28 ; −0,14] | −0,27 R | `ML-20261001T013037Z-a16247` |
| B `EMA_PULLBACK_CONTINUATION` | 13 034 | ≈ 0,51 | −0,28 → −0,21 R (31 %) | 4 926 trades, −0,22 R [−0,29 ; −0,16] | −0,27 R | `ML-20261001T014037Z-af84b5` |
| C `RANGE_REENTRY` | 2 378 | ≈ 0,60 | −0,27 → −0,16 R (42 %) | 993 trades, −0,16 R [−0,28 ; −0,05] | −0,24 R | `ML-20261001T015034Z-50ab66` |

- Critère 4 vrai pour les trois : le modèle trie un peu (les setups gardés perdent moins), mais le
  score de Brier ne bat jamais le taux de base (critère 3) et la stratégie filtrée reste perdante,
  intervalle entièrement négatif (critères 5 à 7). Aucun filtre statistique sur ces variables ne
  transforme ces règles en stratégie gagnante après frais.
- Cohérent avec la décomposition du lot 2 : pas d'avantage brut à trier. Le levier restant est
  l'horizon (criblage à 3 et 7 jours, docs/SCREENING.md), pas un modèle plus complexe sur les mêmes setups.
- Programme : 254 essais sur DEVELOPMENT après ces trois exécutions. Pic mémoire : 4,3 Go engagés
  (plafond 6 Go), 1,9 Go en RAM.
