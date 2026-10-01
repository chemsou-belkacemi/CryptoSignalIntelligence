# Lot 5 ter — ML swing (plusieurs jours) : protocole déclaré avant toute exécution

Demande du propriétaire : après l'intraday, des positions de plusieurs jours, avec LightGBM, XGBoost et
CatBoost, et une limite d'exposition commune aux stratégies. Spot, sans levier, **aucun ordre** : au
plus des signaux, l'exécution reste à BinanceSpotManager sur Demo.

Ce document est commité **avant** la première exécution ; toute modification ultérieure est datée en
bas. La conclusion « aucune stratégie testée ne démontre d'avantage exploitable » reste un résultat
valable. Il reprend tous les principes du protocole intraday (docs/ML_INTRADAY.md : causalité, purge,
étalonnage, espérance nette et abstention, limites centralisées, journal de chaque décision, versions
reproductibles, échecs enregistrés, surveillance) et y ajoute les **leçons** de sa sélection (§12 de ce
document-là) : un minimum de trades par validation, et les critères de coûts défavorables, de dépendance
aux meilleurs trades et de concentration appliqués **dès la sélection**.

## 1. Données, décisions, variables

- Série de base : bougies **1 h clôturées** des 16 paires (jointure 15 min ↔ 1 h déjà validée ; aucune
  unité nouvelle n'est activée). Bougies 4 h et 1 jour reconstruites à partir des bougies 1 h
  **complètes**, disponibles à l'`available_at` de leur dernière heure.
- **Décisions toutes les 4 heures** (clôture des bougies 1 h de 03:00, 07:00… UTC, soit 6 par jour et
  par paire) ; entrée au plus tôt à l'**ouverture de la bougie 1 h suivante**.
- Trou de données dans les 168 dernières heures → aucune décision ni ligne d'entraînement.
- Familles de variables (toutes connues à la décision) :

| Famille | Variables |
|---|---|
| prix | rendements 1, 4, 24, 72, 168 h ; volatilités réalisées 24 h et 7 j ; ATR % ; RSI ; distances aux EMA 20, 50, 200 ; pente de l'EMA 50 ; Bollinger ; position dans le range 7 j ; recul depuis le plus haut 30 j |
| volume | z-score du volume horaire (7 j) ; volume 24 h rapporté à la moyenne des 30 jours précédents |
| transactions | part des achats agressifs sur 24 h ; nombre de trades 24 h rapporté aux 30 jours précédents |
| contexte | 4 h : rendements 24 h et 7 j, pente et distance à l'EMA 50 ; 1 jour : rendements 7, 30 et 90 j, distance à l'EMA 50, ATR % |
| marché | BTC : rendements 24 h, 7 j et 30 j, volatilité réalisée 7 j |
| coupe | **transversale** (à la même décision, parmi les paires de l'univers) : rang du rendement 7 j et 30 j, écart du rendement 7 j à la médiane |
| calendrier | jour de la semaine et créneau de 4 h (sinus, cosinus) |

## 2. Cibles

- Horizons : **H ∈ {1 jour, 3 jours, 7 jours}** (24, 72, 168 bougies 1 h).
- Horizon fixe : achat à l'ouverture de t+1, vente à la clôture de t+H. Triple barrière : objectif et
  stop à ±1,5 σ_H de l'entrée (σ_H : volatilité réalisée 1 h sur 7 jours × √H), stop touché au contact,
  objectif seulement s'il est **dépassé**, stop et objectif dans la même bougie → stop, ouverture au-delà
  d'une barrière → sortie à l'ouverture (stop) ou à l'objectif. Coûts du scénario central aux deux
  remplissages. Étiquette : rendement net > 0. La stratégie sort selon la cible qu'elle a apprise.
- Chevauchement : lignes d'entraînement toutes les max(1, H/16) décisions (les cibles se chevauchent
  dans l'entraînement, ce qui est déclaré ; aucune ne franchit une frontière de bloc) ; **purge** par la
  barrière verticale entre ajustement, étalonnage et validation.

## 3. Modèles, étalonnage, abstention

| Famille | Configurations (fixées, sans arrêt précoce) |
|---|---|
| Référence | régression logistique L2 |
| LightGBM | num_leaves ∈ {15, 63} ; 300 arbres, learning_rate 0,05, min_child_samples 100, bagging 0,8, colonnes 0,8 |
| XGBoost | max_depth ∈ {3, 6} ; 300 arbres, eta 0,05, min_child_weight 20, subsample 0,8, colonnes 0,8 |
| CatBoost | depth ∈ {4, 6} ; 400 itérations, learning_rate 0,05, l2_leaf_reg 3 |

Étalonnage de Platt sur les **3 derniers mois** de l'entraînement (purgés) ; espérance nette
E = p × gain moyen + (1 − p) × perte moyenne (période d'ajustement) ; entrée si E > m avec
**m ∈ {0 ; 0,25 % ; 0,50 %}**, sinon abstention. Grille : 2 cibles × 3 horizons × 7 modèles × 3 marges
= **126 essais**.

## 4. Validation et sélection (DEVELOPMENT seulement)

- Entraînement **ancré** (depuis 2021-01-01) : le swing a peu de décisions indépendantes, une fenêtre
  glissante de 12 mois en laisserait trop peu ; la comparaison des fenêtres reste l'expérience avancée 2.
- **6 validations** de 6 mois : 2022-07 → 2025-06.
- Portefeuille simulé avec les **limites centralisées** (`risk/exposure.py`, section `[risk]`) : 10 % par
  position, 5 positions, 50 % au total, une position par paire, perte du jour 3 % ; c'est le même registre
  que toute autre stratégie (aucune autre n'étant retenue, la simulation ne contient que le swing).
- **Règle d'admission v6** — un système n'est admissible que si TOUT est vrai :
  1. Sharpe > 0 dans au moins 5 des 6 validations, une validation à **moins de 20 trades** comptant comme
     non positive ;
  2. au moins 150 trades au total ;
  3. IC95 (blocs de jours) du gain moyen par trade sur les validations enchaînées **entièrement > 0** ;
  4. gain moyen par trade > 0 en **coûts défavorables** (avec une bougie de retard) ;
  5. gain moyen > 0 **sans le 1 % des meilleurs trades** ;
  6. aucune paire ni aucune validation ne porte plus de 60 % du gain total.
  Parmi les admissibles, le retenu maximise le Sharpe médian par validation.
- Variantes, une à la fois sur la référence (le meilleur admissible, à défaut le meilleur Sharpe médian,
  en diagnostic), comptées comme essais : familles (8 : prix ; prix + volume ; prix + transactions ; prix
  + volume + transactions ; sans contexte ; sans marché ; sans coupe transversale ; sans calendrier),
  puis méta-filtre et abstention de même sévérité (2). Une variante n'est conservée que si elle bat la
  référence dans au moins 70 % des validations évaluables ET satisfait elle-même la règle v6.
- **Total déclaré : 136 essais.**
- Aucun système admissible → conclusion « aucun avantage démontré » ; le test final n'est pas consulté.

## 5. Test final, vérification fine, critères

- Le test final (2025-07-01 → aujourd'hui) est **commun à tout le programme** et reste vierge. Il n'est
  consulté (une seule fois, `--i-understand-final-test`, enregistré) que pour un système admissible,
  après la **vérification en bougies 15 min** : entrée à la clôture de la première bougie 15 min qui
  suit l'ouverture (latence de 15 min), gain moyen encore > 0 sur les validations.
- Critères sur le test final : ceux du §7 du protocole intraday (rendement et IC du gain moyen > 0 ;
  Sharpe au-delà du 95e centile du hasard ; mieux que BTC acheté-gardé en Sharpe ou en perte maximale ;
  au moins 100 trades et aucune paire ni trimestre > 60 % ; positif en coûts défavorables ; positif sans
  le 1 % des meilleurs trades).

## 6. Mesures, références, analyses

Comme le protocole intraday (§6) : rendement, CAGR, perte maximale, Sharpe, trades, rotation, gain moyen
et taux de gain ; IC par blocs circulaires (blocs d'au moins deux fois l'horizon) ; dépendance aux
trades exceptionnels ; références BTC et univers achetés-gardés, entrées au hasard ; analyse par année,
trimestre, paire et contexte ; journal de chaque décision ; modèles archivés avec leur empreinte.

## Historique

- 2026-10-01, v1 : version initiale, avant toute exécution.
