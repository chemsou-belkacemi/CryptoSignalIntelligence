# Protocole de validation (fixé le 2026-09-30, avant toute optimisation)

## Périodes

| Période | Bornes | Usage |
|---|---|---|
| DEVELOPMENT | 2021-01-01 → 2025-06-30 | Développement, backtests de référence, walk-forward (lot 2). |
| FINAL_TEST | 2025-07-01 → aujourd'hui | Réservé. Optuna, choix de features, seuils et sélection n'y accèdent jamais. |

La consultation du test final exige `--i-understand-final-test` et est enregistrée
(`experiments/experiments.sqlite3`, table `final_test_consultations`). Après la
première consultation, cette période devient une validation : réserver une
nouvelle période future pour tout test ultérieur. Le compteur est **global** : dès qu'une
stratégie a consulté le test final, ce qu'on y a vu peut orienter les suivantes.

**Note de transparence du 2026-10-01.** Pendant la mise au point du tableau de bord (perspective
d'une paire), des statistiques descriptives calculées sur tout l'historique, période finale comprise
mais MÉLANGÉE au reste (environ 22 % de l'échantillon, jamais isolée), ont été affichées pour ETH/USDT
(horizons 1 h à 7 jours) et SOL/USDT (3 jours) : fréquences de hausse et résultats d'un plan générique
d'achat (stop 1 σ, objectif 1,5 σ) par régime 1 h. Aucune stratégie, aucun système ML ni aucune
sélection n'y a été évalué. Correctif immédiat (relecture `leak-auditor`) : le tableau de bord n'utilise
plus que l'historique dont l'issue est connue avant la fin de DEVELOPMENT (testé en falsifiant tout ce
qui suit). Ce regard n'est pas compté comme une consultation du test final, puisque rien n'y a été jugé.
Le même jour, le taux de base des signaux Telegram (`external/base_rate.py`, méthode `LIMIT_ALIGNED_V3`)
a été coupé de la même façon.

**Décisions du 2026-10-01** (déléguées par le propriétaire : « débrouille-toi, c'est toi ») :
1. le test final reste **vierge** : la sélection ML intraday (docs/ML_INTRADAY.md §12) a un système
   « admissible » selon sa règle, mais qui échoue déjà en validation aux critères du §7 ; le consulter le
   consommerait presque sûrement pour un REJECTED ;
2. le regard décrit ci-dessus n'est **pas** compté comme une consultation ;
3. tout outil descriptif (tableau de bord, taux de base Telegram) n'utilise que l'historique dont
   l'issue est connue avant la fin de DEVELOPMENT.

Les bornes sont figées dans le code (`research/protocol.py`, `FROZEN_DEVELOPMENT_END`) : un
`development_end` plus tardif (fichier de configuration ou `CSI_PROTOCOL__DEVELOPMENT_END`) est
refusé au lieu de lire en silence le test final ; plus tôt reste permis.

Les trades ouverts près de la fin d'une période ne voient pas les prix suivants :
ils sont marqués CENSORED.


**Consultations enregistrées** :
- **n° 1, 2026-10-03** (`VOLC-20261003T101841Z-4d3ffb`, stratégie `VOLATILITY_CONFIRMATION`) : décision du
  propriétaire, une seule lecture pour confirmer les prévisions de volatilité (`VOLATILITY.md` § 18-19), protocole
  déclaré, relu et répété sur DEVELOPMENT avant la lecture. Depuis, la période finale est une période de
  validation pour tout le programme ; aucune stratégie directionnelle ne peut plus s'y confirmer « à l'aveugle ».
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
   (sinon le critère échoue, et la conclusion n'est pas embellie).

Sinon : **INCONCLUSIVE** (échantillon insuffisant, IC contenant 0) ou **REJECTED**
(E[R] ≤ 0 en coûts centraux). Aucune règle « N trades = validé » : ces seuils sont
un minimum, pas une preuve.

Précision du critère 1 (2026-09-30, avant le premier walk-forward) : « intègres » =
aucune anomalie structurelle (doublon, ordre, alignement, OHLC incohérent, valeur non
finie, bougie ouverte) sur DEVELOPMENT, et test de causalité vert sur données réelles à
trois coupures. Les trous de l'historique Binance sont comptés dans le rapport ; ils ne
sont pas « non expliqués » car toute décision est bloquée pendant `gap_block_bars`
bougies après un trou (veto DATA_GAP).

## Révisions après l'audit look-ahead et overfitting (2026-09-30)

Faites APRÈS les premiers résultats (A, B, C rejetées) ; chacune rend le protocole plus prudent
et aucune n'a été choisie pour changer un verdict.

- **Critère 7** : un filtre dont le retrait améliore l'E[R] hors échantillon fait échouer le
  critère. Le retirer puis relancer sur les mêmes fenêtres reviendrait à choisir la règle après
  avoir vu le hors-échantillon : la version sans filtre est une **nouvelle hypothèse**, à juger
  sur une période non vue.
- **IC95 des backtests** : bootstrap par blocs de 10 **jours** consécutifs (date d'entrée), comme
  le criblage et le taux de base externe, au lieu de blocs de 10 trades (les trades d'un même jour
  sur des paires corrélées ne sont pas indépendants ; l'ancien IC était trop étroit).
- **R** : toujours rapporté au risque **prévu** (limite − stop), comme le signal publié, le taux
  de base et le retour d'exécution ; auparavant, le simulateur divisait par le risque réalisé, ce
  qui gonflait |R| quand l'ouverture remplissait sous la limite.
- **Coûts défavorables** : une bougie de retard à l'entrée (`extra_entry_delay_bars = 1`) ; en
  direct, le signal part ≈ 40 s après la clôture. Le scénario central garde 0.
- **Exécution simulée** : ouverture au niveau du stop ou dessous à l'entrée → deux traversées du
  spread (achat puis stop-market) ; stop remonté touché dans la bougie même du TP → sortie
  pessimiste au nouveau stop, cas compté ambigu.
- **Nombre d'essais du programme** : chaque walk-forward et criblage enregistre
  `program_trials`, la somme des essais faits sur DEVELOPMENT par tout le programme (grilles,
  conditions × horizons, variantes de backtest). Plus ce nombre grandit, plus un résultat isolé
  risque d'être un hasard ; à titre indicatif, un seuil de Bonferroni serait 0,05 / ce nombre.
  Au 2026-09-30, le registre compte 21 exécutions sur DEVELOPMENT (5 backtests, 14 walk-forwards,
  2 criblages), soit 215 essais au sens de `program_trials`. Ces 21 exécutions portent
  `NO_GIT_COMMIT` : elles précèdent le premier commit du dépôt. Au soir du 2026-10-02, le
  programme compte **753 essais** sur DEVELOPMENT (walk-forwards, criblages D à K, ML intraday,
  swing et swing long, marché à terme, portefeuilles hebdomadaires, lot 8 et sa v2, suivi de
  tendance journalier, protocoles de volatilité v1 à v3), plus les 6 comparaisons du protocole des
  intervalles s'il s'enregistre ; la période finale n'a jamais été lue (0 consultation au registre).
  Seules les prévisions de volatilité (ampleur, jamais le sens) ont passé leurs règles déclarées ;
  les deux pistes directionnelles encore ouvertes (cassure journalière d'un pivot haut, veto après
  une pression vendeuse extrême) ne sont mesurées qu'en direct, par des tests pré-inscrits
  (`docs/FORWARD_TESTS.md`, F10 et F11), jamais par un nouvel essai sur DEVELOPMENT.
- **Test de causalité** : coupure sur la disponibilité (`available_at`) de chaque unité de temps,
  tous les volumes falsifiés ; un test de mutation vérifie qu'une jointure sur `open_time` (bougie
  1h en formation) est détectée.
- **Biais de survivance (non corrigé, déclaré)** : l'univers a été choisi en 2026 parmi les paires
  encore cotées et liquides. Effet estimé : quelques centièmes de R par trade en faveur du
  backtest ; sans effet sur des stratégies rejetées, décisif pour un résultat limite.

## Révision des frais centraux (2026-10-03)

- **Frais par ordre du scénario central : 7,5 pb au lieu de 10 pb**, à partir du 2026-10-03. Le propriétaire paie
  ses frais en BNB (remise de 25 % sur le tarif Spot de base) ; 10 pb surestimaient son coût réel. Seuil aller-retour
  des criblages : **0,21 %** au lieu de 0,26 % (2 × 7,5 pb de frais + 2 × 3 pb de glissement et demi-spread).
- Défavorable inchangé (10 pb : remise absente, solde BNB épuisé, environnement Demo sans remise) ; stress inchangé
  (15 pb). Le modèle de frais des tests en direct (`forward/costs.py`, gelé) prenait déjà 7,5 pb en central.
- **Rien n'est recalculé** : chaque exécution enregistrée garde le scénario de coûts de son époque (registre, rapport).
  Effet estimé sur les verdicts passés, recalculé à la main et non enregistré : A, B et C gagneraient environ 0,03 à
  0,05 R par trade et restent rejetées (intervalles négatifs) ; aucune condition des criblages D à K ne change de
  verdict (E à 4 h, +0,11 % brut, reste sous 0,21 %) ; ce n'est pas un nouvel essai.
- **Effet en direct, daté** : le chemin de décision (`signals/analyze.py`, veto sur le RR net central), les plans
  indicatifs du tableau de bord, l'évaluation des signaux externes et le rejeu prospectif lisent ce scénario. Les
  signaux shadow et les plans émis à partir du 2026-10-03 passent le veto un peu plus souvent ; c'est inscrit dans la
  section « Démarrages » de `docs/FORWARD_TESTS.md` pour F1 et F2, dont les mesures utilisent leur propre modèle de
  frais gelé.
- **Vérification en Demo** : le rapprochement d'exécution (`feedback/reconcile.py`) compare le taux de frais réel de
  chaque signal exécuté à l'hypothèse centrale et signale un écart de plus de 0,5 pb, ou des frais payés en BNB ou
  dans l'actif acheté (non convertis). Au 2026-10-03, BSM n'a encore remonté aucune exécution.

## Traçabilité

Chaque exécution enregistre : hypothèse, période, univers, empreinte des données,
commit git, versions, graine, paramètres, coûts, règles de simulation, métriques,
statut. Les essais ratés et abandonnés sont conservés et comptés.
