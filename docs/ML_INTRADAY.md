# Lot 5 bis — ML intraday : protocole déclaré avant toute exécution

Demande du propriétaire : comparer LightGBM et XGBoost à une référence simple (régression
logistique) pour prédire des mouvements intraday sur 15 min enrichies du contexte 1 h et 4 h,
séparer modèle / règles d'entrée-sortie / taille des positions / exécution, valider de façon
chronologique avec une période finale indépendante, mesurer le résultat d'un **portefeuille** et le
comparer à des références ; puis étendre au swing. Spot, sans levier, **aucun ordre** : CSI produit au
plus des signaux, l'exécution reste à BinanceSpotManager sur Demo.

Ce document est commité **avant** la première exécution. Toute modification ultérieure est datée en
bas, avec sa raison, et ne peut jamais être motivée par un résultat de la période finale.
**La conclusion « aucune stratégie testée ne démontre d'avantage exploitable » est un résultat
valable et attendu comme possible.**

## 0. Évaluation de Freqtrade + FreqAI (documentation officielle, consultée le 2026-10-01)

| Point | Constat (freqtrade.io/en/stable) | Conséquence pour CSI |
|---|---|---|
| Modèles | LightGBM, XGBoost, CatBoost, PyTorch, scikit-learn | même famille de modèles ici |
| Réentraînement | fenêtre glissante `train_period_days` / `backtest_period_days` ; cibles recalculées par fenêtre | repris (fenêtres glissantes) |
| Fuites | variables calculées une fois sur toute la plage, « à l'utilisateur de vérifier » ; découpage interne `test_size` sans purge ni embargo documentés ; `lookahead-analysis` ne vérifie que les trades effectivement pris | insuffisant au regard du protocole CSI (purge, embargo, causalité testée par mutation) |
| Période finale, essais | aucun verrou de période finale, aucun compteur d'essais | absents : ce sont des garde-fous centraux ici |
| Exécution | Freqtrade est un bot d'exécution (mode réel avec clés) | **incompatible** avec la règle « CSI ne contient aucun code d'ordre » |
| Installation | Python ≥ 3.11, TA-Lib, Docker fortement recommandé sous Windows ; backtest adaptatif « très long » | lourd, sans gain de rigueur |

**Décision** : ne pas l'utiliser comme base. Pipeline construit dans CSI (données causales, coûts,
purge, période finale verrouillée, registre des essais déjà en place), avec LightGBM et XGBoost comme
bibliothèques. Freqtrade pourra servir plus tard de **contre-vérification indépendante** en conteneur
séparé, en backtest seulement, sans clé.

## 1. Données et variables (toutes connues à l'instant de décision)

- Univers : les 16 paires de la configuration ; bougies **15 min clôturées** (point de départ
  configurable), décision à la clôture de t, entrée au plus tôt à l'**ouverture de t+1**.
- Contexte **1 h** (paire et BTC) : jointure `available_at` existante ; contexte de plus de 2 h → inconnu.
- Contexte **4 h** : reconstruit à partir des bougies 1 h **complètes** (00, 04, 08… UTC), disponible à
  l'`available_at` de sa dernière bougie 1 h ; plus de 8 h → inconnu. Une bougie 4 h en formation n'est
  jamais visible (test de causalité + test de mutation).
- Trou de données dans les 96 dernières bougies → ligne exclue (fenêtres d'indicateurs douteuses).
- **Familles de variables** (mesurées séparément, §6) :

| Famille | Variables |
|---|---|
| prix | rendements 1, 2, 4, 8, 16, 32, 96 bougies ; volatilités réalisées 16 et 96 ; ATR % ; RSI ; distances et pente d'EMA ; Bollinger ; position dans le range 32 |
| volume | z-score et ratio du volume de base |
| transactions | part des achats agressifs (taker) et sa moyenne 4 bougies ; nombre de trades relatif |
| contexte | 1 h (rendement 24 h, pente, ATR, distance EMA 50, régimes) et 4 h (rendements 24 h et 7 j, pente, ATR, distance EMA 50) |
| marché | BTC 1 h (rendement 24 h, ATR, pente) |
| calendrier | heure et jour (sinus, cosinus) |

## 2. Cibles et chevauchement

- Horizons : **H ∈ {2, 4, 8, 16} bougies = 30 min, 1 h, 2 h, 4 h**.
- Rendement net d'un aller-retour : achat à l'ouverture de t+1, vente à la clôture de t+H, frais
  10 pb par côté, glissement et demi-spread du scénario **central** (≈ 0,26 % aller-retour). Une cible
  qui traverse un trou de données est invalide.
- Classification : **rendement net > 0** (le mouvement couvre les coûts).
- Chevauchement : une ligne d'entraînement toutes les H bougies par paire (cibles disjointes dans une
  paire) ; **purge** : aucune ligne d'entraînement dont la sortie dépasse le début de la validation ;
  l'étalonnage (§3) est purgé de la même façon.

## 3. Modèles, étalonnage, espérance et abstention

| Famille | Configurations (fixées, pas d'arrêt précoce) |
|---|---|
| Référence | régression logistique L2 (numpy) |
| LightGBM | num_leaves ∈ {15, 63} × n_estimators ∈ {200, 500} ; learning_rate 0,05, min_child_samples 200, bagging 0,8, colonnes 0,8 |
| XGBoost | max_depth ∈ {3, 6} × n_estimators ∈ {200, 500} ; eta 0,05, min_child_weight 50, subsample 0,8, colonnes 0,8 |

- Chaque fenêtre d'entraînement de 12 mois est coupée en **10 mois d'ajustement + 2 mois
  d'étalonnage** (purgés). Les probabilités sont **étalonnées** (Platt : logistique sur le logit) sur
  ces 2 mois ; la calibration est **vérifiée** en validation (Brier, erreur d'étalonnage par déciles).
- **Espérance nette** d'un setup : E = p × gain moyen net des gagnants + (1 − p) × perte moyenne nette
  des perdants (moyennes de la période d'ajustement, coûts inclus).
- **Décision** : entrer si E > marge m, avec **m ∈ {0 ; 0,05 % ; 0,10 %}** ; sinon **abstention**.
  Entre candidats simultanés, priorité à la plus forte espérance.
- Essais : 4 horizons × 9 configurations × 3 marges = **108**, comptés dans `program_trials`.

## 4. Validation (glissante, stable, période finale réservée)

- **Sélection, sur DEVELOPMENT seulement** (2021-01 → 2025-06) : fenêtres glissantes, entraînement
  12 mois, validation 6 mois, pas de 6 mois → **7 validations** (2022-01 → 2025-06).
- **Stabilité exigée** : une configuration n'est admissible que si son portefeuille a un Sharpe > 0
  dans **au moins 5 des 7 validations** et au moins 200 trades au total. Parmi les admissibles, la
  retenue maximise le **Sharpe médian par validation** (pas l'agrégat, sensible à une période
  exceptionnelle). Une variante (famille de variables, §6) n'est conservée que si elle améliore le
  Sharpe dans au moins 5 des 7 validations.
- **Aucune admissible → conclusion « aucun avantage démontré », sans consulter la période finale**
  (elle reste vierge pour une idée future).
- **Estimation honnête, sur FINAL_TEST** (2025-07-01 → date d'exécution), consultée **une seule
  fois** et enregistrée (`--i-understand-final-test`) : configuration figée, réentraînée tous les 6 mois
  sur les 12 mois précédents (même règle), jouée sur toute la période finale. Les résultats de
  sélection sont optimistes par construction (meilleure de 108) ; seul ce chiffre juge l'approche.

## 5. Stratégie, taille, exécution (couches séparées) et hypothèses du moteur

- **Modèle** → probabilité étalonnée → espérance. **Entrée** : E > m. **Sortie** : clôture de t+H
  (sortie temporelle, sans stop dans cette version).
- **Taille** : 10 % du capital réalisé par position ; **limites configurables** : 5 positions au plus
  (50 % d'exposition totale), une position par paire, perte du jour ≥ 3 % → plus d'entrée jusqu'au
  lendemain UTC. Spot, sans levier.
- **Hypothèses du moteur** (déclarées) : remplissage complet au prix d'ouverture de t+1 corrigé du
  glissement et du demi-spread (pas de carnet d'ordres, pas de remplissage partiel) ; sortie au prix de
  clôture de t+H corrigé de même ; capital suivi en réalisé (pas de valorisation intra-position) ;
  positions indépendantes de la liquidité (tailles faibles devant les volumes 15 min) ; pas de funding.
- **Vérifications de robustesse** (configuration retenue, sur la période finale et sur les validations) :
  coûts **défavorables** et **stress** ; **une bougie de retard** à l'entrée ; **bougies 1 min** : entrée
  au prix de la première minute suivant l'ouverture de t+1 (latence réaliste de publication), sur les
  trades réellement pris.

## 6. Mesures, analyses, apport des familles

- Rendement net, CAGR, **perte maximale du portefeuille**, **Sharpe** (journalier annualisé), nombre de
  trades, **rotation**, gain moyen par trade (IC par blocs de jours), taux de gain, part de
  l'abstention.
- **Par période et contexte** : année, trimestre, paire, régime 1 h (tendance, volatilité), BTC en
  hausse ou en baisse sur 24 h.
- **Références** : liquidités, **BTC acheté et gardé**, **univers à parts égales acheté et gardé**,
  **entrées au hasard** (même nombre de candidats, mêmes règles ; 200 tirages : distribution du Sharpe).
- **Apport des familles** (sur la configuration retenue, en validation) : prix seul ; prix + volume ;
  prix + volume + transactions ; + contexte ; + marché ; tout. Chaque variante compte comme un essai.

## 7. Critères (sur FINAL_TEST, déclarés avant)

L'approche n'est retenue (statut VALIDATED ; aucune exécution automatique pour autant) que si :
1. rendement net du portefeuille > 0 et IC95 du gain moyen par trade (blocs de jours) > 0 ;
2. Sharpe au-dessus du 95e centile des entrées au hasard ;
3. Sharpe supérieur à celui de BTC acheté et gardé **ou** perte maximale au moins deux fois plus
   faible pour un rendement positif ;
4. au moins 100 trades, aucune paire ni trimestre > 60 % du gain ;
5. résultat encore positif en coûts défavorables et avec une bougie de retard.
Sinon : REJECTED (INCONCLUSIVE si moins de 100 trades).

## 8. Surveillance, suspension, positions existantes (si l'approche est un jour retenue)

- **Âge du modèle** : réentraînement tous les 6 mois ; au-delà de 7 mois sans réentraînement →
  suspension des nouvelles entrées.
- **Qualité des données** : bougie 15 min attendue absente ou contexte 1 h/4 h inconnu → aucune entrée
  sur la paire (règle déjà active dans la surveillance) ; plus de 4 paires sans données → suspension
  globale.
- **Dégradation** : sur les 100 derniers trades (prospectifs), gain moyen dont l'IC95 est entièrement
  < 0, ou perte du portefeuille > 1,5 × la perte maximale de la période finale → suspension des
  nouvelles entrées jusqu'à revue.
- **Calibration** : écart moyen entre probabilité prédite et fréquence observée > 10 points sur les
  200 derniers setups → suspension.
- **Positions existantes** : jamais coupées par une suspension ; elles vont à leur sortie prévue (t+H) ;
  une coupure de données au moment de la sortie est signalée et gérée par le bot (sortie au marché
  dès le retour des données).
- **Enregistrement** : toutes les exécutions, **échecs compris**, avec paramètres, versions et
  empreintes des données (registre des expériences).

## 9. Swing (étape suivante, protocole séparé)

Après l'évaluation intraday : positions de plusieurs jours, cibles et entraînements distincts (4 h /
1 jour), LightGBM, XGBoost et éventuellement CatBoost, **limite d'exposition commune** aux stratégies
intraday et swing. Protocole écrit avant exécution, comme celui-ci.

## Historique

- 2026-10-01, v1 (commit 69f7f6c) : version initiale, avant toute exécution.
- 2026-10-01, v2 (avant toute exécution) : demandes du propriétaire — abstention et sélection par
  espérance nette, étalonnage et vérification de calibration, stabilité sur 5 validations sur 7,
  apport des familles de variables (prix, volume, transactions…), hypothèses du moteur, robustesse
  (coûts défavorables, retard, bougies 1 min), analyses par période et contexte, surveillance et
  suspension, conclusion « aucun avantage » sans consulter la période finale.
