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

**Changement de convention (relecture indépendante, 2026-10-01).** La relecture a comparé, de 2021 à
2026, le prix implicite de l'intérêt ouvert (valeur / nombre de contrats) au prix Spot 15 min. Le décalage
n'est pas uniforme :
- jusqu'en 2024-02, la valeur archivée à T est relevée vers T ;
- depuis 2024-03, elle est relevée vers T + 5 à 6 min.

Pour les ratios de comptes, la vérification n'est possible qu'en 2026.

D'où l'hypothèse retenue, prudente dans les deux cas : une valeur archivée à T n'est utilisable qu'à
T + 10 min + 2 s. À la décision, la valeur lue a donc été relevée au plus tard environ 9 minutes avant,
ce qui laisse cette marge au délai de publication de l'API, qui n'est pas mesuré. Un contrôle interne ne
peut pas détecter une hypothèse fausse sur la donnée brute ; il détecte une erreur de code (voir les
mutations ci-dessous). Ces vérifications externes en tiennent lieu.

**Défauts des archives officielles, traités sans rien inventer**
- **Intérêt ouvert à zéro** : par exemple 485 lignes pour BTC, dont une semaine en juillet 2024. Ces
  valeurs, et tout ratio nul ou négatif, deviennent manquantes. Sinon elles créeraient de faux
  « effondrements » de −100 %.
- **Jours absents des fichiers mensuels de prime** : 2021-07-01, du 24 au 27 juillet 2021, 2022-10-02…
  Ils sont repris de l'archive journalière officielle du même jour, publiée et vérifiée.
- **Valeurs figées** : l'intérêt ouvert de BTC reste constant pendant 16 h le 2021-05-21. Ce n'est pas
  détecté ; c'est une limite déclarée.

## Criblage (protocole v3, déclaré le 2026-10-01 avant toute exécution)

Commande : `screen-derivatives`. Code : `research/derivatives_screen.py`, `derivatives/features.py`.

**Question.** Après un événement de positionnement, le rendement Spot futur **brut** dépasse-t-il :
1. la dérive de la paire sur la période où la condition est évaluable ;
2. le seuil des coûts aller-retour, soit 0,26 % en coûts centraux ?

**Données et décisions**
- Les 16 paires, période DEVELOPMENT seulement (jusqu'au 2025-06-30). Le test final réservé n'est pas
  lu, et toutes les séries sont coupées à cette date (un test le vérifie en falsifiant tout ce qui suit).
- **Données complètes exigées.** Chaque paire doit avoir ses trois séries :
  - financement et prime commencés au plus tard le 2021-04-01 ;
  - metrics commencées au plus tard le 2022-01-01 ;
  - toutes poursuivies jusqu'à 2 jours avant la fin de DEVELOPMENT.

  - la prime sans jour incomplet en DEVELOPMENT, hors jour de cotation : les jours absents des fichiers
    mensuels doivent avoir été repris des archives journalières.

  Sinon : refus, aucun essai enregistré. La qualité de chaque série (trous, valeurs manquantes ou
  invalides) et les empreintes des séries lues sont enregistrées avec le résultat.
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
- Financement :
  - ramené à son équivalent sur 8 h (taux × 8 / intervalle en heures), car SOL a eu des intervalles de
    2 h et 4 h en 2022-11 ;
  - heures de règlement arrondies à la minute la plus proche avant la fenêtre de 72 h, sinon leur gigue de
    quelques millisecondes y ferait entrer un 10e règlement.
- Intérêt ouvert : mesuré en **nombre de contrats**, pas en dollars. Sa valeur en dollars baisse dès que le
  prix baisse, ce que OI_FLUSH exige déjà ; en dollars, 27 à 41 % des événements n'étaient pas des baisses
  de contrats (relecture, mesure faite sur les variables seules, sans rendement).

**Conditions (4), toutes de sens contraire au positionnement, effet attendu : excès positif**

| Condition | Événement |
|---|---|
| FUNDING_LOW | financement moyen des 72 dernières heures sous son 10e centile |
| PREMIUM_DISCOUNT | prime moyenne des 24 dernières heures (bougies 1 h closes) sous son 10e centile |
| OI_FLUSH | variation 24 h du nombre de contrats ouverts sous son 10e centile ET prix Spot en baisse sur 24 h |
| ACCOUNTS_SHORT | ratio comptes acheteurs/vendeurs (tous les comptes) sous son 10e centile |

Les conditions sur l'intérêt ouvert et les comptes ne sont évaluables qu'à partir de 2022-03 environ :
archives depuis 2021-12, plus 60 jours d'historique (BTC : plus tôt).

**Mesures**
- **Excès** : rendement de l'événement − moyenne des rendements de la même paire aux décisions où la
  condition est évaluable. La même période de données sert donc de référence : la hausse de 2021 ne se
  compare pas à 2022.
- **Excès transversal** (diagnostic, non exigé) : rendement − moyenne des **autres** paires évaluables au
  même instant, avec au moins 5 autres paires, sinon non défini. Il sépare la sélection entre paires de
  l'effet de moment commun à tout le marché.
- **IC** : Student sur sommes par blocs de jours calendaires (au moins 2 × H et 10 jours, soit 10, 10 et
  14 jours), variance robuste à un retard, au moins 20 blocs avec événements. Niveau corrigé de
  Bonferroni pour les 12 essais : 1 − 0,05/12 ≈ 99,58 %, bilatéral.
- Également rapportés :
  - nombre d'événements, de paires et de jours ;
  - IC du rendement brut ;
  - part des paires et des années à excès positif ;
  - concentration : part de la somme des excès portée par la paire et par l'année les plus lourdes.

**Règle.** Une condition est une **« piste »** si toutes ces conditions sont réunies :
- rendement brut moyen > 0,26 % ;
- borne basse de l'IC de l'excès > 0 ;
- aucune paire ni aucune année ne porte plus de 60 % de la somme des excès.

Le premier critère est une estimation ponctuelle, que la seule dérive franchit à 3 et 7 jours. Une piste
n'est donc **pas** un résultat « au-delà des coûts » : c'est une idée à confirmer. Verdict enregistré :
« N PISTE(S) À CONFIRMER » ou « AUCUNE_PISTE ».

**Essais.** 4 conditions × 3 horizons = **12 essais**, ajoutés aux 626 du programme.

**Audit des fuites, exécuté avant tout résultat** (sinon aucun résultat n'est produit) :
- sur BTC, ETH **et** SOL, tous trois exigés, 3 décisions aux heures de règlement du financement
  (00:00, 08:00, 16:00) : les variables recalculées avec les seules données disponibles, puis avec un
  futur falsifié, doivent être identiques au calcul complet ;
- trois mutations doivent être détectées, chacune par les variables de sa famille : chaque table
  dérivée se croit disponible à l'horodatage brut de la donnée (règlement, ouverture de la bougie,
  heure d'archive).

**Lecture déclarée.**
- Aucune piste : pas d'avantage démontré par cette information. Ce sera écrit tel quel.
- Une piste : un criblage, sur données déjà vues, avec 638 essais au programme. Elle ne deviendrait une
  stratégie qu'avec une fiche, un walk-forward, puis une confirmation sur des données jamais consultées
  (test final réservé, ou observation prospective en shadow).
- Un excès positif mais un excès transversal nul signalerait un effet de moment commun au marché ; il
  serait rapporté comme tel.

**Limites déclarées.**
- Univers actuel (paires survivantes).
- Hypothèses de disponibilité prudentes, mais non mesurées en temps réel sur l'historique.
- Événements qui se chevauchent : une même situation dure plusieurs décisions. Les blocs calendaires en
  tiennent compte, mais une dépendance plus longue que deux blocs rend l'IC trop étroit (simulation du
  ML swing).
- Les regards déjà portés sur le financement du moment dans le tableau de bord ne sont pas des essais.
- Le calibrage de l'IC a été simulé à 95 %, pas au niveau corrigé de 99,58 %. La correction de Bonferroni
  ne porte que sur ces 12 essais, pas sur les 638 du programme.
- Valeurs figées des archives non détectées (exemple ci-dessus).
- Valeurs partielles non nulles après une panne (BTC le 2021-05-22, de 05:15 à 05:40, à 10-90 % du niveau)
  et chutes isolées du ratio de comptes (HBAR le 2023-11-16 à 19:20, NEAR le 2023-04-28 à 18:05) : non
  traitées. Aucune ne tombe aujourd'hui sur la ligne que lit la grille horaire (celle de h − 15 min).
- La dérive d'une paire est calculée sur des décisions qui comprennent les événements eux-mêmes. L'excès
  en est atténué, ce qui va dans le sens prudent.

## Résultats du criblage (2026-10-01)

Exécution unique `SCREEN-20261001T111500Z-60702b` (protocole v3, code `a1e0a49`, données
téléchargées et contrôlées avant l'exécution) : **AUCUNE_PISTE**. 12 essais ; le programme en compte
désormais 638 sur DEVELOPMENT. Audit des fuites réussi sur BTC, ETH et SOL : 0 écart, 3 mutations
détectées. La période finale n'est pas consultée.

| Condition | Excès 1 j [IC 99,58 %] | Excès 3 j | Excès 7 j | Transversal 7 j |
|---|---|---|---|---|
| FUNDING_LOW | +0,18 % [−0,20 ; +0,56] | +0,48 % [−0,58 ; +1,54] | +0,61 % [−1,61 ; +2,83] | −0,09 % |
| PREMIUM_DISCOUNT | −0,01 % [−0,46 ; +0,45] | +0,23 % [−0,97 ; +1,43] | +0,27 % [−1,99 ; +2,53] | −0,02 % |
| OI_FLUSH | +0,50 % [−0,18 ; +1,17] | +0,73 % [−0,85 ; +2,31] | +0,89 % [−2,33 ; +4,10] | +0,36 % |
| ACCOUNTS_SHORT | +0,51 % [−0,26 ; +1,27] | +1,18 % [−0,91 ; +3,28] | +2,15 % [−2,64 ; +6,95] | +0,70 % |

Lecture :
- Au niveau corrigé de Bonferroni (99,58 %), **aucun intervalle de l'excès n'est entièrement au-dessus
  de 0**.
- Les estimations ponctuelles de l'excès sont positives aux trois horizons pour FUNDING_LOW, OI_FLUSH
  et ACCOUNTS_SHORT (de +0,18 % à +2,15 %), ainsi que pour PREMIUM_DISCOUNT à 3 et 7 jours (+0,23 %
  et +0,27 %). Celle de PREMIUM_DISCOUNT à 1 jour est quasi nulle (−0,01 %). Ces estimations
  positives des conditions contraires (comptes vendeurs, purge de l'intérêt ouvert) **ne sont pas une
  preuve**. Les confirmer sur DEVELOPMENT serait du post hoc ; seule une observation prospective
  (shadow) ou la période finale pourrait le faire.
- L'excès transversal (rendement moins la moyenne des autres paires au même instant) va de −0,09 % à
  +0,70 %. Il est plus faible que l'excès sur la dérive de la paire, et aucun de ses intervalles
  n'exclut 0 : rien ne montre une sélection entre paires.
- Concentration : pour 5 des 12 lignes, une seule année porte plus de 60 % de la somme des excès. Ce
  sont les quatre conditions à 7 jours (FUNDING_LOW 78 %, ACCOUNTS_SHORT 64 %, OI_FLUSH 126 %,
  PREMIUM_DISCOUNT 214 %) et PREMIUM_DISCOUNT à 3 jours (122 %). Une part au-dessus de 100 % signifie
  que la somme des autres années est négative.
- Rien ici n'annonce une rentabilité. L'information de positionnement ne démontre pas d'avantage sur
  DEVELOPMENT, comme les bougies seules avant elle.

**Vérification indépendante (2026-10-01).**
- Recalcul de OI_FLUSH et ACCOUNTS_SHORT à 24 h, sans le code du criblage : 7 361 et 16 172
  événements, contre 7 338 et 16 174, avec des excès qui diffèrent de moins de 0,003 point. Une fois
  alignés sur les choix du code, les chiffres sont identiques.
- Cause des 23 événements OI_FLUSH en plus : le code exige aussi 24 heures Spot contiguës pour le
  rendement Spot **passé** de 24 h. Ces événements tombent autour du trou Spot du 2023-03-24. Ce choix
  est désormais écrit ici.
- Registre, niveau des IC, blocs, règle et empreintes des données : conformes. Aucune donnée
  postérieure au 2025-06-30 n'a été lue.

Les conditions restent dans le tableau de bord comme simple description (carte « Marché à terme »).
Aucun signal n'en découle.

## Historique

- 2026-10-01 : données du moment dans le tableau de bord (carte « Marché à terme ») ; liste blanche
  publique du marché à terme ; couverture des archives vérifiée.
- 2026-10-01, v1 : historique (`download-derivatives`), horodatage des archives vérifié contre l'API,
  protocole du criblage déclaré avant toute exécution.
- 2026-10-01, v2 : **avant toute exécution**, après relecture indépendante (aucune fuite trouvée dans les
  jointures, fenêtres et rendements ; 12 essais inchangés).
  - Données : intérêt ouvert et ratios nuls ou négatifs rendus manquants ; jours absents des fichiers
    mensuels de prime repris des archives journalières ; convention des metrics corrigée (changement de
    2024-03, marge de +10 min inchangée).
  - Variables : financement ramené à 8 h ; fenêtre de 72 h insensible à la gigue des règlements.
  - Exécution : données complètes exigées, audit exigé sur les trois paires, empreintes enregistrées.
  - Mesures : excès transversal sans la paire elle-même ; concentration par paire et par année (60 %
    au plus) dans la règle ; IC du rendement brut ; « passe » renommé « piste ».
  - Tests : coupure DEVELOPMENT, données incomplètes, niveau et blocs de l'IC, chaque critère de la
    règle, zéros, gigue, valeur courante exclue des seuils metrics.
- 2026-10-01, v3 : **avant toute exécution**, après une seconde relecture indépendante (aucune fuite ;
  12 essais inchangés).
  - OI_FLUSH mesuré en nombre de contrats, et non en dollars. La carte du tableau de bord donne aussi ses
    variations en contrats.
  - Règlements arrondis à la minute la plus proche (gigue négative couverte).
  - Prime : jours incomplets repris des archives journalières, exigés avant l'exécution.
  - Qualité de chaque série enregistrée avec le résultat ; limites supplémentaires déclarées.
  - Tests : zéro de contrats et du ratio de comptes, gigue négative, début et fin des séries, prime
    incomplète, effet prix exclu.
- 2026-10-01 : **résultats** de l'exécution unique `SCREEN-20261001T111500Z-60702b` : AUCUNE_PISTE
  (12 essais, programme 638). Vérification indépendante conforme. Précision écrite après coup, sans
  changer le calcul : la contiguïté Spot vaut aussi pour le rendement passé de 24 h d'OI_FLUSH.
