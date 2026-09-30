# Protocole de validation (fixé le 2026-09-30, avant toute optimisation)

## Périodes

| Période | Bornes | Usage |
|---|---|---|
| DEVELOPMENT | 2021-01-01 → 2025-06-30 | Développement, backtests de référence, walk-forward (lot 2). |
| FINAL_TEST | 2025-07-01 → aujourd'hui | Réservé. Optuna, choix de features, seuils et sélection n'y accèdent jamais. |

La consultation du test final exige `--i-understand-final-test` et est enregistrée
(`experiments/experiments.sqlite3`, table `final_test_consultations`). Après la
première consultation, cette période devient une validation : réserver une
nouvelle période future pour tout test ultérieur.

Les trades ouverts près de la fin d'une période ne voient pas les prix suivants :
ils sont marqués CENSORED.

## Walk-forward (lot 2)

Précisé le 2026-09-30, avant le premier walk-forward (`config/default.toml`, `[walk_forward]`) :

- **Fenêtres ancrées** sur DEVELOPMENT : entraînement du 2021-01-01 au début du test (au moins
  18 mois), tests successifs de 6 mois sans chevauchement → 6 fenêtres, 2022-07 → 2025-06.
- **Purge** : un trade n'entre dans l'entraînement d'une fenêtre que si son setup ET sa sortie
  précèdent le début du test (aucun résultat d'entraînement ne chevauche le test). Pas
  d'embargo nécessaire : l'entraînement précède toujours le test.
- **Recalibrage** : grille grossière déclarée par chaque stratégie ; budget d'essais = taille
  de la grille, enregistré (`n_trials`). A : `lookback_bars` {20, 40} × `stop_atr` {1,0 ; 1,5 ;
  2,0} × `target_r` {1,5 ; 2,0 ; 3,0} = 18. B : `zone_atr` {0,25 ; 0,5} × `stop_buffer_atr`
  {0,25 ; 0,5} × `target_r` {1,5 ; 2,0 ; 3,0} = 12. C : `bb_k` {2,0 ; 2,5} ×
  `stop_buffer_atr` {0,25 ; 0,5 ; 1,0} = 6.
- **Sélection** : E[R] nette (coûts centraux) moyenne de la combinaison et de ses voisines
  de grille (zone stable plutôt que pic isolé), parmi les combinaisons à au moins 30 trades
  d'entraînement ; à défaut, paramètres v1. Pas d'abstention : la meilleure combinaison est
  jouée même si son score est négatif.
- **Test** : paramètres retenus appliqués à la fenêtre suivante ; décisions limitées à la
  fenêtre, un trade ouvert peut se terminer après, jamais au-delà de DEVELOPMENT. Joués sur
  chaque fenêtre : base (coûts central, défavorable, stress), chaque ablation et extension
  (central), et la v1 sans recalibrage (référence). Seuls ces résultats sont agrégés.
- Le verdict ne modifie pas la configuration ; toute promotion de statut reste explicite.

## Critères d'admission (déclarés avant les tests)

Une stratégie passe **VALIDATED_OOS** seulement si, sur l'agrégat hors échantillon
du walk-forward, TOUS les points suivants sont vrais :

1. données intègres (aucun trou non expliqué dans les fenêtres utilisées) et test de causalité vert ;
2. au moins 150 trades clos, et au moins 3 fenêtres de test contenant des trades ;
3. E[R] net > 0 en coûts **centraux**, borne basse de l'IC95 (bootstrap par blocs) > 0 ;
4. E[R] net > 0 en coûts **défavorables** ;
5. aucune paire ni aucune année ne représente plus de 60 % du PnL total en R ;
6. drawdown en R sur trades clos inférieur à 15 R ;
7. la version avec ses filtres ne fait pas moins bien que la même règle sans filtre
   (sinon le filtre est retiré, pas la conclusion embellie).

Sinon : **INCONCLUSIVE** (échantillon insuffisant, IC contenant 0) ou **REJECTED**
(E[R] ≤ 0 en coûts centraux). Aucune règle « N trades = validé » : ces seuils sont
un minimum, pas une preuve.

Précision du critère 1 (2026-09-30, avant le premier walk-forward) : « intègres » =
aucune anomalie structurelle (doublon, ordre, alignement, OHLC incohérent, valeur non
finie, bougie ouverte) sur DEVELOPMENT, et test de causalité vert sur données réelles à
trois coupures. Les trous de l'historique Binance sont comptés dans le rapport ; ils ne
sont pas « non expliqués » car toute décision est bloquée pendant `gap_block_bars`
bougies après un trou (veto DATA_GAP).

## Traçabilité

Chaque exécution enregistre : hypothèse, période, univers, empreinte des données,
commit git, versions, graine, paramètres, coûts, règles de simulation, métriques,
statut. Les essais ratés et abandonnés sont conservés et comptés.
