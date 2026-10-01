# Lot 5 quater — ML swing long (2017-2025, 40 paires) : protocole déclaré avant toute exécution

Demande du propriétaire (2026-10-01) : « télécharger toutes les données possibles, par exemple du bitcoin
du tout début, et les entraîner au max ». L'historique long est téléchargé (bougies 1 h depuis la
cotation de chaque paire, magasin séparé `long_history/`). Ce protocole rejoue le **protocole ML swing**
(docs/ML_SWING.md, v3, règle d'admission v6) sur tout cet historique et sur l'univers de recherche de
40 paires, avec une grille réduite et deux fois plus de validations. Spot, sans levier, **aucun ordre** :
ce protocole mesure, il ne publie rien.

Ce document est commité **avant** la première exécution ; toute modification ultérieure est datée en
bas. La conclusion « aucun avantage démontré » reste un résultat valable. Il ne répète pas ce qui est
déjà écrit dans docs/ML_SWING.md (variables, cibles, modèles, règle v6, moteur) : il déclare ce qui
change et ce que l'historique long impose. Identité : `strategy_id` **`ML_SWING_LONG`**, essais
`ML_SWING_LONG_SELECT` / `ML_SWING_LONG_FINAL`, exécutions `MLL-…`, module
`src/crypto_signal_intelligence/ml/swing/long.py`.

Pourquoi cette piste : la sélection swing portait sur 16 paires et 4,5 ans (2021-2025, 6 validations).
Ici, l'entraînement remonte au **2017-08-17** (première bougie de BTCUSDT sur Binance Spot), les
validations couvrent six ans, et l'univers compte 40 paires. Plus de données ne créent pas un avantage ;
elles rendent seulement un faux positif un peu moins probable, et un vrai petit avantage un peu plus
visible.

## 1. Données et univers

- **Historique long** : bougies 1 h depuis la cotation de chaque paire (`research/long_history.py`,
  magasin `long_history/`), lues par `load_long`. Aucune bougie 15 min : le magasin 15 min commence le
  2021-01-01.
- **Coupées à la fin de DEVELOPMENT** (2025-06-30 23:59:59, `development_end`) : la préparation de la
  sélection ne lit jamais au-delà, quelle que soit la date demandée (clôture forcée dans le code). Seule
  l'estimation unique (`final`) lit plus loin, après les verrous du moteur (§6).
- **Univers figé le 2026-10-01** (`research/universe.py`, 40 paires) : les 30 cryptos favorables au
  relevé halal du jour, plus les 10 acceptées par le propriétaire. Le magasin doit contenir chaque paire
  jusqu'à 2 jours de la coupure, sinon la préparation refuse (magasin incomplet).
- **Réglages normaux** pour tout le reste : registre d'expériences, rapports, coûts (scénarios central,
  défavorable, stress), limites de risque centralisées, graine. Seul le chargement des bougies utilise le
  magasin long ; le début d'historique des réglages (2021-01-01) ne s'applique pas à ce programme, qui
  déclare le sien (champ `history_start` du `Program`).
- **Appartenance à la date.** Une paire n'a de lignes qu'à partir du moment où, avec les seules
  données connues à la décision :
  - pas de trou de données dans les 168 dernières heures (entraînement et décision), et, pour une
    DÉCISION de validation seulement, contexte connu (`h4_ret_6`, `d1_ret_7`, `btc_ret_24`) : règle du
    swing, une ligne d'entraînement peut avoir un contexte incomplet (effet quasi nul : 720 heures de
    cotation sont exigées) ;
  - elle est **liquide** : volume moyen en USDT par jour sur les 30 jours précédant la décision
    (somme des `quote_volume` des **720** bougies 1 h clôturées à la décision, la bougie de la décision
    comprise, divisée par 30) **≥ 1 000 000 $**. Une heure sans bougie compte pour un volume nul ; une
    paire cotée depuis moins de 30 jours est inconnue, donc exclue. Chaque valeur ne dépend que de ses
    720 heures.
  - Sinon la ligne n'existe pas : ni ligne d'entraînement, ni décision, ni terme de la référence
    « moyenne de toutes les décisions au même instant » du critère 7. Les lignes illiquides sont retirées
    du tableau de décisions avant que le moteur ne le voie : une seule population partout.
  - Les cibles recalculées sous un autre scénario de coûts ou de retard passent par la même définition
    des lignes (`decision_rows`) : elles restent alignées sur le tableau de décisions (testé).
- **Coupe transversale** (rangs des rendements 7 j et 30 j, écart à la médiane) : calculée **avant** le
  filtre de liquidité, parmi toutes les paires cotées à l'instant de la décision, liquides ou non.
- Peu de paires au début : 4 cotées avant 2018, 11 avant 2019, 18 avant la première validation, 22 avant
  2020, 29 avant 2021, 40 depuis septembre 2024.
- Taille attendue : 1,8 million de bougies 1 h sur DEVELOPMENT, soit **au plus 450 000 lignes de
  décision** (une sur quatre), avant le filtre de liquidité, dont l'effet n'a pas été mesuré avant
  l'exécution.

## 2. Décisions, variables, cibles

Tout est celui du swing (docs/ML_SWING.md §1 et §2, `ml/swing/dataset.py`), sans aucune nouvelle logique
de décision :
- décisions toutes les 4 heures (clôture des bougies 1 h de 03:00, 07:00… UTC), entrée au plus tôt à
  l'ouverture de la bougie 1 h suivante ;
- les 7 familles de variables (prix, volume, transactions, contexte 4 h / 1 jour, marché BTC, coupe
  transversale, calendrier), toutes connues à la décision ;
- cibles **horizon fixe** et **triple barrière** (±1,5 σ_H), coûts du scénario central aux deux
  remplissages, cible invalide si sa fenêtre traverse un trou ;
- **horizons H ∈ {3 jours, 7 jours}** (72 et 168 bougies 1 h). L'horizon de 1 jour du swing est retiré,
  par décision prise ici avant exécution : à 1 jour, les coûts pèsent le plus lourd face au mouvement
  attendu, et la grille réduite garde les deux horizons les plus longs ;
- une ligne d'entraînement toutes les ⌊H/16⌋ décisions (4 et 10), purge par la barrière verticale.

## 3. Modèles et grille (fixée ici)

La configuration la plus simple de chaque famille du swing, hyperparamètres inchangés. La grille a été
fixée **sans regarder** les résultats par configuration de la sélection swing (seule sa conclusion
globale, « aucun avantage démontré », est connue).

| Famille | Configuration (celle du swing, docs/ML_SWING.md §3) |
|---|---|
| Référence | `logistic_l2` : régression logistique L2 |
| LightGBM | `lgbm_leaves15_n300` : 15 feuilles, 300 arbres |
| XGBoost | `xgb_depth3_n300` : profondeur 3, 300 arbres |
| CatBoost | `catboost_depth4_n400` : profondeur 4, 400 itérations |

Étalonnage de Platt sur les 3 derniers mois de l'entraînement (purgés), espérance nette, entrée si
E > m avec **m ∈ {0 ; 0,25 %}**. Grille : 2 cibles × 2 horizons × 4 modèles × 2 marges = **32 essais**,
plus les 8 variantes de familles de variables et les 2 filtres (méta-filtre, abstention de même
sévérité) du moteur, identiques au swing : **42 essais** déclarés (`Program.declared_trials`, testé).

Raisons du choix, a priori : H = 1 jour retiré (le plus coûteux en frais et le plus bruité) ; marge de
0,50 % retirée parce qu'elle réduit le nombre de trades par validation, déjà faible sur les premières
validations où peu de paires existent (une validation à moins de 20 trades compte comme non positive) ;
la configuration la plus simple de chaque famille de modèles. **Transparence** : les résultats par
configuration de la sélection swing (`reports/MLS-20261001T085521Z-a04c71/`) étaient accessibles quand
cette grille a été écrite ; l'auteur déclare n'en avoir lu que la conclusion globale (aucun système
stable, AUC médiane 0,51), ce que rien d'autre ne garantit. Avec deux valeurs par axe, tout système
retenu sera « au bord de la grille ».

## 4. Validations

- Entraînement **ancré au 2017-08-17** : les premières décisions utilisables arrivent 30 jours plus tard,
  avec BTC et ETH seulement.
- **12 validations de 6 mois**, de **2019-07-01** à 2025-06-30 (2019-07 → 2019-12, …, 2025-01 →
  2025-06). Étalonnage sur les 3 mois qui précèdent chaque validation ; première période d'étalonnage
  2019-04-01 → 2019-06-30.
- Le moteur reçoit ces dates par deux champs du `Program` ajoutés pour ce protocole : `history_start`
  (ancrage, plis, `period_start` enregistré au registre) et `first_valid_start` (la première validation,
  parce que 2017-08-17 plus un nombre entier de mois ne tombe pas un premier du mois). Les deux sont
  optionnels ; sans eux, ML_INTRADAY et ML_SWING se comportent exactement comme avant (testé).
- Portefeuille simulé avec les limites centralisées (`[risk]`) : les mêmes qu'au swing.
- Les premières validations reposent sur peu de paires (18 cotées au 2019-07-01, moins de liquides) et
  peu de lignes d'ajustement ; elles comptent comme les autres.

## 5. Règle d'admission

La **règle stricte v6 du swing, inchangée** (`RULE` de `ml/swing/protocol.py`, docs/ML_SWING.md §4) :
1. Sharpe > 0 dans au moins 70 % des validations, soit **9 des 12** ; une validation à moins de
   20 trades ou de 20 périodes d'entrée distinctes (max(1 jour, H) : 20 fenêtres de 3 jours, ou
   20 semaines sur 26) ne compte pas ;
2. au moins 150 trades au total ;
3. IC95 du gain moyen par trade entièrement > 0 (Student sur sommes par blocs de jours calendaires,
   au moins 20 blocs avec trades) ;
4. gain moyen > 0 en coûts défavorables, avec une bougie de retard ;
5. gain moyen > 0 sans le 1 % des meilleurs trades ;
6. aucune paire ni validation au-delà de 60 % du gain ;
7. IC95 de l'excès sur la moyenne de toutes les décisions valides **et liquides** au même instant
   entièrement > 0.

Variantes (8 familles, méta-filtre, abstention de même sévérité) et conservation : comme au swing (bat la
référence dans au moins 70 % des validations ET satisfait la règle). Aucun système admissible →
« aucun avantage démontré », la période finale n'est pas consultée. **« Admissible » n'est jamais
« validé »** : c'est une sélection en échantillon, sur des données en partie déjà parcourues.

## 6. Test final, vérification fine, critères

- La période finale (2025-07-01 → aujourd'hui) est commune à tout le programme et reste vierge ; le
  compteur global du registre n'en montre aucune consultation au 2026-10-01. Une seule consultation,
  avec `--i-understand-final-test`, enregistrée ; il faut un système admissible, le même code et les mêmes
  réglages qu'à la sélection, et la vérification en bougies 15 min.
- **Vérification en bougies 15 min** : non construite (elle ne l'est que si une sélection retient un
  système). Le magasin 15 min commence en 2021 : elle ne pourrait couvrir que les validations
  2021-2025, jamais 2019-2020. Cette limite est déclarée ici ; si une sélection retient un système, la
  vérification sera écrite et documentée avant toute consultation de la période finale.
- Seule l'estimation unique lit des bougies postérieures à DEVELOPMENT (`prepare_final`), et seulement
  après les verrous ci-dessus. Critères sur la période finale : ceux du swing (§5).

## 7. Audit des fuites, avant tout résultat

Sans audit réussi, aucun essai n'est évalué et l'exécution est enregistrée comme échec.
- **L'audit du swing** (`ml/swing/protocol.py`) : pour BTC, ETH et SOL, 4 décisions tirées avec la graine,
  variables de la paire recalculées avec les seules données disponibles à la décision, puis avec un
  futur falsifié, identiques au calcul complet ; une bougie 4 h jointe sur son ouverture et une bougie
  1 jour visible après sa première heure (mutations) doivent être détectées chacune par les variables de
  sa famille ; la coupe transversale à une décision ne change pas quand toutes les paires sont coupées à
  cette décision.
- **Propre à ce programme** :
  - le volume du filtre de liquidité à une décision ne change pas quand les bougies sont coupées ou
    falsifiées après cette décision (4 décisions par paire auditée), et une fenêtre décalée d'un jour
    vers le futur (mutation) doit être détectée ;
  - la population préparée (heures de décision et volume de liquidité de chaque paire) est exactement
    celle que donne la fonction de liquidité contrôlée ci-dessus ;
  - aucune bougie chargée n'est postérieure à la fin de DEVELOPMENT des réglages (et non à la date
    que la préparation s'est donnée) ;
  - la sélection est refusée si la fin de DEVELOPMENT ne donne pas exactement 12 validations.
- Les empreintes des séries lues (après coupure), des modules qui décident et des réglages sont
  enregistrées.

## 8. Limites déclarées

- **Survivantes.** L'univers est choisi aujourd'hui : une crypto retirée de la cote ou écartée du relevé
  halal depuis n'y figure pas. L'appartenance à la date (cotation, contexte, liquidité) ne corrige que
  la disponibilité ; elle ne recrée pas l'univers qu'on aurait choisi à l'époque. Ce biais favorise un
  portefeuille long seul, **et aussi la sélection entre paires (critère 7)** : les paires effondrées puis
  retirées manquent, et les survivantes de 2019-2020 sont connues pour leur parcours ultérieur. Au
  swing, un penchant persistant vers les mêmes paires passait le critère 7 dans 5 à 11 % des tirages
  simulés (17 % avec une demi-vie de 140 jours) ; sur 6 ans, ce taux n'est pas simulé.
- **Liquidité peu sélective.** Le filtre retire 3,7 % des décisions (17 % en 2019, 6 % en 2020,
  presque rien ensuite ; surtout DOGE, THETA, DASH, IOTA, HBAR) : l'appartenance à la date revient
  presque à la date de cotation.
- **Paires renommées.** Quelques paires semblent n'avoir que l'historique de leur nouveau symbole
  (POL depuis 2024-09, RENDER depuis 2024-07, EGLD depuis 2020-09, VET depuis 2018-07) : leur passé
  sous l'ancien nom manque (non vérifié dans le dépôt).
- **Référence « univers à parts égales »** du rapport : faussée quand une paire est cotée en cours de
  validation ; elle n'entre dans aucun critère.
- **Début étroit.** Peu de paires avant 2019 ; les premières validations reposent sur 10 à 20 paires,
  moins encore de liquides, et peu de lignes d'ajustement.
- **Coûts constants.** Le scénario central vaut pour toute la période, alors que les écarts de prix
  étaient plus larges en 2017-2019 : les premières validations sont probablement trop favorables.
- **Coupe transversale** calculée sur toutes les paires cotées, liquides ou non.
- **Liquidité** : seuil fixe de 1 M$ par jour sur toute la période, sans correction pour la taille du
  marché de l'époque ; une heure sans bougie compte pour un volume nul.
- **Trous de données.** Les interruptions de Binance datent presque toutes de 2017-2021 (27 des 28 trous
  des bougies 1 h de BTCUSDT sur DEVELOPMENT). Après chaque trou, aucune décision pendant 168 heures et
  aucune cible qui le traverse : les années 2018-2020 perdent plusieurs semaines de décisions.
- **Non-indépendance.** La période 2021-2025 a déjà été parcourue par plus de 638 essais du programme
  (stratégies A-C, criblage D-I, méta-labeling, ML intraday, ML swing sur 16 paires avec les mêmes
  variables et les mêmes cibles, criblage dérivés) : les validations 2021-2025 ne sont pas indépendantes
  de ce qui a déjà été vu. Le registre compte ces essais ; le rapport rappelle leur nombre.
- **Pas de vérification 15 min** possible avant 2021 (§6).
- **Hasard.** Si le signe du Sharpe de chaque validation était un pile ou face, un système obtiendrait
  au moins 9 validations positives sur 12 avec une probabilité de **7,3 %** (299/4096, calculée dans le
  code et testée). Sur 32 systèmes de base, cela ferait environ 2,3 systèmes « stables » par hasard s'ils
  étaient indépendants ; ils ne le sont pas, et un portefeuille long seul en marché haussier en obtient
  davantage. D'où les critères 3 à 7, qui restent une sélection en échantillon. Les taux de faux positifs
  des IC (critères 3 et 7, docs/ML_SWING.md) ont été simulés pour 3 ans de validations, pas pour 6.
- **Règle v6** : elle réduit les faux positifs sans les supprimer. Critère 7 : sélection entre paires
  seulement, le timing pur est rejeté par construction.
- **Moteur** : remplissage complet à l'ouverture de la bougie 1 h suivante, ordre intra-bougie
  défavorable, capital réalisé, pas d'impact de marché ; limite de perte journalière presque inerte
  (positions jusqu'à 7 jours, perte latente non suivie) ; Platt sur 3 mois (à 7 jours, une douzaine de
  semaines indépendantes) ; gain et perte moyens de l'espérance estimés sur l'entraînement ancré, qui
  inclut 2017 et 2021 (marchés très haussiers).
- **Aucun ordre, aucun signal publié** : un système retenu n'ouvrirait qu'une phase shadow prospective,
  après la période finale.

## 9. Exécution

- Commande : `csi ml-swing select --long`, puis,
  seulement si un système est admissible et vérifié, `csi ml-swing final --long --i-understand-final-test`.
- Un travail lourd à la fois, à basse priorité ; code commité (versions reproductibles), sinon
  `--allow-dirty` enregistré comme tel. Chaque exécution, échec compris, compte ses essais dans le
  registre.
- Résultats : `reports/MLL-…/` (report.md, summary.json, grid.csv, calibration.csv, variants.csv,
  decisions.parquet, leak_audit.json, modèles archivés) ; aucun chiffre ne sera copié ici avant la
  première exécution. Les résultats seront ajoutés dans un §10 daté, sans modifier les sections 1 à 8.

## Historique

- 2026-10-01, v1 : version initiale, avant toute exécution. Décisions prises avant d'écrire le code :
  données et univers, liquidité, 12 validations ancrées, grille de 42 essais, règle v6 inchangée,
  audit, réserves.
- 2026-10-01, v1 (avant toute exécution, après la relecture indépendante) : l'audit contrôle aussi la
  population préparée et la coupure par rapport à la fin de DEVELOPMENT des réglages ; refus si le
  nombre de validations n'est pas 12 ; déclarations complétées (survivantes et critère 7, liquidité peu
  sélective, raisons de la grille et accès aux résultats du swing, contexte des lignes d'entraînement,
  paires renommées, bord de grille, référence à parts égales).
