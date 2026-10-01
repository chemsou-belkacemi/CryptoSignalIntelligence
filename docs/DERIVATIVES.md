# Données publiques du marché à terme (positionnement)

Demande du propriétaire (2026-10-01) : « ajoute des données publiques ». Les bougies Spot ont été testées
sous tous les angles (stratégies A à C, criblage des familles D à I, ML intraday, ML swing : 626 essais,
aucun avantage démontré). Ce lot ajoute une **source d'information différente** : le positionnement des
autres participants sur le marché à terme USDⓈ-M de Binance.

## Règles

- **Lecture seule de données publiques.** Aucune clé, aucun ordre, aucun compte. La liste blanche de
  `data/http.py` n'ouvre que des routes publiques de marché :
  - financement ;
  - prime ;
  - intérêt ouvert ;
  - ratios acheteurs/vendeurs ;
  - archives `/data/futures/um/`.

  Les routes d'ordres, de compte, de levier et de flux utilisateur restent refusées avant tout appel
  réseau (`tests/test_data.py`).
- **Aucun contrat à terme n'est négocié.** Ces données servent d'information. Les signaux de CSI restent
  des achats Spot de l'univers validé par le propriétaire (`docs/UNIVERSE.md`). Si le propriétaire ne
  souhaite pas utiliser des données de produits dérivés, même comme simple information, ce lot se retire
  sans toucher au reste.
- **Rien n'entre dans une décision** tant qu'un protocole déclaré à l'avance n'a pas démontré un avantage
  hors échantillon.

## Ce qui est lu

| Donnée | Route publique | Historique en archives | Sens |
|---|---|---|---|
| Financement | `/fapi/v1/fundingRate`, `/fapi/v1/premiumIndex` | mensuel, depuis 2020 (HBAR : 2021-03) | taux payé à chaque règlement par les acheteurs aux vendeurs s'il est positif (l'inverse s'il est négatif) |
| Prime du perpétuel | `/fapi/v1/premiumIndexKlines` | bougies 1 h, mensuel, depuis 2020 | écart du perpétuel sur l'indice Spot |
| Intérêt ouvert | `/futures/data/openInterestHist` (30 derniers jours) | « metrics » 5 min, journalier, depuis 2021-12 (BTC : 2020-09) | valeur des contrats ouverts |
| Comptes acheteurs/vendeurs | `/futures/data/globalLongShortAccountRatio` | dans « metrics » | tous les comptes |
| Gros comptes | `/futures/data/topLongShortPositionRatio` | dans « metrics » | positions des plus gros comptes |
| Achats/ventes agressifs | `/futures/data/takerlongshortRatio` | dans « metrics » | ordres au marché |

Les 16 paires de l'univers ont toutes un contrat perpétuel. La couverture a été vérifiée le 2026-10-01
par requêtes publiques.

## Tableau de bord : carte « Marché à terme »

Dans l'onglet « Analyser une paire », sous la situation actuelle :
- les valeurs du moment de la paire ;
- pour chacune, son **rang** dans son propre historique récent : part des valeurs de la fenêtre
  strictement inférieures ; la fenêtre est de 90 jours pour le financement, 30 jours pour la prime et
  environ 20 jours pour le reste ;
- une période en cours, non terminée, n'est jamais comptée.

La carte est une description, jamais un signal. Elle relit les données au plus toutes les 5 minutes
(`[derivatives] live_cache_seconds`). Route : `GET /derivatives?symbol=ETHUSDT` (docs/API.md).

## Historique et disponibilité (recherche)

Hypothèses déclarées, prudentes, puisqu'aucune heure de publication réelle n'est connue :
- financement connu 1 minute après son règlement (`funding_latency_seconds`) ;
- prime connue comme une bougie : fin de l'heure + 2 s ;
- valeurs « metrics » de 5 minutes connues **10 min + 2 s** après leur horodatage d'archive
  (`metrics_latency_seconds` = 602) ; la vérification est détaillée ci-dessous.

Les jointures se font vers le passé, sur `available_at`, avec un âge maximal. Le contrôle « données
tronquées / futur falsifié » et les tests de mutation sont exigés, comme pour les bougies.

### Vérification de l'horodatage des archives « metrics » (2026-10-01)

La même heure du 2026-09-29 a été comparée entre l'archive journalière et l'API publique. Résultat :
- **intérêt ouvert et ratios** : la valeur archivée à 08:00 est celle que l'API date de 08:05. L'archive
  avance donc chaque valeur de 5 minutes ; la lire « à son heure » ferait voir le futur ;
- **achats/ventes agressifs** : la valeur archivée à T est celle de l'API à T, et couvre la période qui
  commence à T, donc elle n'est connue qu'à T + 5 min.

D'où l'hypothèse retenue, doublement prudente : une valeur archivée à T n'est utilisable qu'à
T + 10 min + 2 s. Un contrôle interne ne peut pas détecter une hypothèse fausse sur la donnée brute ; il
détecte une erreur de code (voir les mutations ci-dessous). Cette vérification externe en tient lieu.

## Criblage (protocole v1, déclaré le 2026-10-01 avant toute exécution)

Commande : `screen-derivatives`. Code : `research/derivatives_screen.py`, `derivatives/features.py`.

**Question.** Après un événement de positionnement, le rendement Spot futur **brut** dépasse-t-il :
1. la dérive de la paire sur la période où la condition est évaluable ;
2. le seuil des coûts aller-retour, soit 0,26 % en coûts centraux ?

**Données et décisions**
- Les 16 paires, période DEVELOPMENT seulement (jusqu'au 2025-06-30). Le test final réservé n'est pas
  lu, et toutes les séries sont coupées à cette date.
- Décision toutes les 4 h, à la clôture des bougies Spot 1 h (00:00, 04:00… UTC).
- Entrée à l'ouverture de la bougie suivante, sortie à la clôture de t+H, H ∈ {1 j, 3 j, 7 j}. Aucun stop
  ni objectif. Une fenêtre Spot non contiguë n'a pas de rendement.
- Les variables ne lisent que des lignes dont `available_at` précède celui de la décision, avec un âge
  maximal :
  - financement : 9 h ;
  - prime : 2 h ;
  - metrics : 30 min sur une grille horaire, la grille elle-même jointe avec 1 h au plus.
- Seuils : 10e centile de la variable sur les **90 jours précédents**, valeur courante exclue. Il faut au
  moins 60 jours d'historique ; sinon la condition n'est pas évaluable.

**Conditions (4), toutes de sens contraire au positionnement, effet attendu : excès positif**

| Condition | Événement |
|---|---|
| FUNDING_LOW | financement moyen des 72 dernières heures sous son 10e centile |
| PREMIUM_DISCOUNT | prime moyenne des 24 dernières heures (bougies 1 h closes) sous son 10e centile |
| OI_FLUSH | variation 24 h de l'intérêt ouvert sous son 10e centile ET prix Spot en baisse sur 24 h |
| ACCOUNTS_SHORT | ratio comptes acheteurs/vendeurs (tous les comptes) sous son 10e centile |

Les conditions sur l'intérêt ouvert et les comptes ne sont évaluables qu'à partir de 2022-03 environ :
archives depuis 2021-12, plus 60 jours d'historique (BTC : plus tôt).

**Mesures**
- **Excès** : rendement de l'événement − moyenne des rendements de la même paire aux décisions où la
  condition est évaluable. La même période de données sert donc de référence : la hausse de 2021 ne se
  compare pas à 2022.
- **Excès transversal** (diagnostic, non exigé) : rendement − moyenne de toutes les paires évaluables au
  même instant. Il sépare la sélection entre paires de l'effet de moment commun à tout le marché.
- **IC** : Student sur sommes par blocs de jours calendaires (au moins 2 × H et 10 jours, soit 10, 10 et
  14 jours), variance robuste à un retard, au moins 20 blocs avec événements. Niveau corrigé de
  Bonferroni pour les 12 essais : 1 − 0,05/12 ≈ 99,58 %, bilatéral.
- Également rapportés : nombre d'événements, de paires et de jours, part des paires et des années à
  excès positif.

**Règle.** « Passe » = rendement brut moyen > 0,26 % ET borne basse de l'IC de l'excès > 0.

**Essais.** 4 conditions × 3 horizons = **12 essais**, ajoutés aux 626 du programme.

**Audit des fuites, exécuté avant tout résultat** (sinon aucun résultat n'est produit) :
- sur BTC, ETH et SOL, 3 décisions aux heures de règlement du financement (00:00, 08:00, 16:00), les
  variables recalculées avec les seules données disponibles, puis avec un futur falsifié, doivent être
  identiques au calcul complet ;
- trois mutations doivent être détectées, chacune par les variables de sa famille : chaque table
  dérivée se croit disponible à l'horodatage brut de la donnée (règlement, ouverture de la bougie,
  heure d'archive).

**Lecture déclarée.**
- Aucune condition ne passe : pas d'avantage démontré par cette information. Il sera écrit tel quel.
- Une condition qui passe reste une **piste** : un criblage, sur données déjà vues, avec 638 essais au
  programme. Elle ne deviendrait une stratégie qu'avec une fiche, un walk-forward, puis une confirmation
  sur des données jamais consultées (test final réservé, ou observation prospective en shadow).
- Un excès positif mais un excès transversal nul signalerait un effet de moment commun au marché ; il
  serait rapporté comme tel.

**Limites déclarées.**
- Univers actuel (paires survivantes).
- Hypothèses de disponibilité prudentes, mais non mesurées en temps réel sur l'historique.
- Événements qui se chevauchent : une même situation dure plusieurs décisions. Les blocs calendaires en
  tiennent compte, mais une dépendance plus longue que deux blocs rend l'IC trop étroit (simulation du
  ML swing).
- Les regards déjà portés sur le financement du moment dans le tableau de bord ne sont pas des essais.

## Suite

1. Téléchargement de l'historique (archives vérifiées par SHA-256), puis contrôle de qualité.
2. Relecture indépendante du code du criblage, puis exécution unique, puis résultats ci-dessous, quels
   qu'ils soient.

## Historique

- 2026-10-01 : données du moment dans le tableau de bord (carte « Marché à terme ») ; liste blanche
  publique du marché à terme ; couverture des archives vérifiée.
- 2026-10-01, v1 : historique (`download-derivatives`), horodatage des archives vérifié contre l'API,
  protocole du criblage déclaré avant toute exécution.
