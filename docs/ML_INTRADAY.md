# Lot 5 bis — ML intraday : protocole déclaré avant toute exécution (2026-10-01)

Demande du propriétaire : comparer LightGBM et XGBoost à une référence simple (régression
logistique) pour prédire des mouvements intraday sur 15 min enrichies du contexte 1 h et 4 h,
séparer modèle / règles d'entrée-sortie / taille des positions / exécution, valider de façon
chronologique avec une période finale indépendante, mesurer le résultat d'un **portefeuille** et le
comparer à des références ; puis étendre au swing. Spot, sans levier, **aucun ordre** : CSI produit au
plus des signaux, l'exécution reste à BinanceSpotManager sur Demo.

Ce document est écrit et commité **avant** la première exécution. Toute modification ultérieure est
datée ici, avec sa raison, et ne peut pas être motivée par un résultat de la période finale.

## 0. Évaluation de Freqtrade + FreqAI (documentation officielle, consultée le 2026-10-01)

| Point | Constat (freqtrade.io/en/stable) | Conséquence pour CSI |
|---|---|---|
| Modèles | LightGBM, XGBoost, CatBoost, PyTorch, scikit-learn | même famille de modèles ici |
| Réentraînement | fenêtre glissante `train_period_days` / `backtest_period_days` ; cibles recalculées par fenêtre | repris (fenêtres glissantes) |
| Fuites | variables calculées une fois sur toute la plage, « à l'utilisateur de vérifier » ; découpage interne `test_size` sans purge ni embargo documentés ; `lookahead-analysis` ne vérifie que les trades effectivement pris | insuffisant au regard du protocole CSI (purge, embargo, causalité testée par mutation) |
| Période finale, essais | aucun verrou de période finale, aucun compteur d'essais | absents : ce sont des garde-fous centraux ici |
| Exécution | Freqtrade est un bot d'exécution (mode réel avec clés) | **incompatible** avec la règle « CSI ne contient aucun code d'ordre » |
| Installation | Python ≥ 3.11, TA-Lib, Docker fortement recommandé sous Windows ; backtest adaptatif « très long » | lourd, sans gain de rigueur |

**Décision** : ne pas l'utiliser comme base. Le pipeline est construit dans CSI (données causales,
coûts, purge, période finale verrouillée, registre des essais déjà en place), avec LightGBM et
XGBoost comme bibliothèques. Freqtrade pourra servir plus tard de **contre-vérification
indépendante** en conteneur séparé, en backtest seulement, sans clé.

## 1. Données et variables (toutes connues à l'instant de décision)

- Univers : les 16 paires de la configuration ; bougies **15 min clôturées** (point de départ
  configurable), décision à la clôture de t, entrée au plus tôt à l'**ouverture de t+1**.
- Contexte **1 h** (paire et BTC) : jointure `available_at` existante.
- Contexte **4 h** : bougies 4 h reconstruites à partir des bougies 1 h **complètes** (00, 04, 08…
  UTC), disponibles à l'`available_at` de leur dernière bougie 1 h ; une bougie 4 h en formation
  n'est jamais visible. Couvert par un test de causalité et un test de mutation.
- Variables (environ 45) : rendements passés 15 min (1, 2, 4, 8, 16, 32, 96 bougies), volatilités
  réalisées (16, 96), ATR en %, RSI 14, distances et pentes d'EMA 20/50, position dans les bandes de
  Bollinger, position dans le range des 32 dernières bougies, volume relatif et z-score, part des
  achats agressifs (taker), nombre de trades relatif ; contexte 1 h et 4 h (rendement 24 h / 7 j,
  pente, ATR, distance à l'EMA 50, régimes) ; BTC (rendement 24 h, ATR, pente) ; heure et jour (sin,
  cos). Normalisation éventuelle calculée sur l'entraînement seul.

## 2. Cibles

- Horizons étudiés : **H ∈ {2, 4, 8, 16} bougies = 30 min, 1 h, 2 h, 4 h**.
- Rendement net d'un aller-retour : achat à l'ouverture de t+1, vente à la clôture de t+H, frais
  10 pb par côté, glissement et demi-spread du scénario **central** (≈ 0,26 % aller-retour).
- Cible de classification : **rendement net > 0** (le mouvement couvre les coûts).
- Cibles chevauchantes : purge et embargo de H bougies à chaque frontière ; lignes d'entraînement
  échantillonnées avec un pas de H bougies par paire (cibles non chevauchantes dans une paire).

## 3. Modèles et grilles (fixées ici, pas d'arrêt précoce)

| Famille | Configurations |
|---|---|
| Référence | régression logistique L2 (numpy, déjà testée) |
| LightGBM | num_leaves ∈ {15, 63} × n_estimators ∈ {200, 500} ; learning_rate 0,05, min_child_samples 200, bagging 0,8, colonnes 0,8 |
| XGBoost | max_depth ∈ {3, 6} × n_estimators ∈ {200, 500} ; eta 0,05, min_child_weight 50, subsample 0,8, colonnes 0,8 |

Graines fixes, 4 fils de calcul au plus. Seuil d'entrée : probabilité au-dessus du quantile
**q ∈ {90 %, 95 %, 99 %}** des prédictions de l'**entraînement** (aucun seuil appris sur le test).
Essais : 4 horizons × 9 configurations × 3 seuils = **108 essais**, comptés dans `program_trials`.

## 4. Validation (chronologique, glissante, période finale réservée)

- **Sélection, sur DEVELOPMENT seulement** (2021-01 → 2025-06) : fenêtres glissantes, entraînement
  12 mois, validation 6 mois, pas de 6 mois → 7 validations (2022-01 → 2025-06). Purge/embargo de H
  bougies entre entraînement et validation. Chaque configuration est jugée sur ses 7 validations
  enchaînées ; la configuration retenue maximise le **Sharpe journalier du portefeuille** (section 5)
  sur l'ensemble des validations, avec au moins 200 trades. Les résultats de toutes les
  configurations sont publiés (LightGBM / XGBoost / logistique comparés, pas seulement la gagnante).
- **Estimation honnête, sur FINAL_TEST** (2025-07-01 → date d'exécution), consultée **une seule
  fois** et enregistrée (`--i-understand-final-test`) : la configuration retenue, figée, est
  réentraînée tous les 6 mois sur les 12 mois précédents (même règle qu'en sélection) et jouée sur
  toute la période finale. Ce chiffre seul dit si l'approche tient ; les résultats de sélection sont
  optimistes par construction (meilleure de 108).

## 5. Stratégie, taille des positions, exécution (couches séparées)

- **Modèle** → probabilité. **Règle d'entrée** : probabilité ≥ seuil retenu ; **sortie** : à la clôture
  de t+H (sortie temporelle, sans stop dans cette version).
- **Taille** : 10 % du capital par position ; **limites configurables** : au plus 5 positions (50 %
  d'exposition totale), une position par paire, perte du jour ≥ 3 % → plus d'entrée jusqu'au
  lendemain UTC.
- **Exécution simulée** : ouverture de t+1 et clôture de t+H, coûts du scénario (central ; défavorable
  publié en plus), spot, sans levier, sans funding (pas de perpétuels).

## 6. Mesures et références

Rendement net, CAGR, **perte maximale du portefeuille**, **Sharpe** (journalier annualisé), nombre de
trades, **rotation** (montant échangé / capital moyen, par an), gain moyen par trade, stabilité par
année, trimestre et paire. Références sur la même période : **liquidités** (0), **BTC acheté et
gardé**, **univers à parts égales acheté et gardé**, et **entrées au hasard** (même nombre de trades,
même horizon, mêmes limites ; 200 tirages : distribution du Sharpe).

## 7. Critères (sur FINAL_TEST, déclarés avant)

L'approche n'est retenue (statut VALIDATED, aucune exécution automatique pour autant) que si :
1. rendement net du portefeuille > 0 et IC95 du gain moyen par trade (blocs de jours) > 0 ;
2. Sharpe au-dessus du 95e centile des entrées au hasard ;
3. Sharpe supérieur à celui de BTC acheté et gardé sur la même période **ou** perte maximale au moins
   deux fois plus faible pour un rendement positif ;
4. au moins 100 trades, et aucune paire ni trimestre > 60 % du gain.
Sinon : REJECTED (ou INCONCLUSIVE si moins de 100 trades). Une approche retenue passe ensuite en
signaux shadow prospectifs, puis en Demo, jamais directement en argent réel.

## 8. Swing (étape suivante, protocole séparé)

Après évaluation de l'intraday : positions de plusieurs jours, cibles et entraînements distincts
(bougies 4 h / 1 jour), LightGBM, XGBoost et éventuellement CatBoost, avec une **limite d'exposition
commune** aux stratégies intraday et swing. Protocole écrit avant exécution, comme celui-ci.

## Historique

- 2026-10-01 : version initiale, avant toute exécution.
