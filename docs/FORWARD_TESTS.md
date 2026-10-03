# Tests en direct : pré-inscriptions

Mission du propriétaire du 2026-10-02 : construire maintenant les tests en direct, pour avoir des résultats dans
2 à 3 mois. Ce document est la pré-inscription de chaque test. Il est écrit et commité AVANT le démarrage du test.

## Cadre commun

**Cadre halal, prioritaire sur tout le reste.**
- Spot seulement : aucun short, levier, marge, contrat à terme, perpétuel, option ni token à levier.
- Aucun rendement assimilable à un intérêt : ni Simple Earn, ni staking, ni prêt, ni Launchpool, ni produit
  « Earn ».
- Le stablecoin est une simple position d'attente, sans rendement.
- Les données du marché à terme (financement, intérêt ouvert) sont lues comme information seulement.
- Le filtre halal s'applique en amont de chaque test, avec `config/halal_screen.yaml` et `forward/halal.py`.
  - **Source de vérité** : la décision du propriétaire déjà enregistrée dans CSI (table `pair_admissions`).
  - **Paire de la configuration sans décision** : admise seulement si elle est FAVORABLE au relevé des sources.
  - **Crypto absente** : exclue.
  - **Exclusions structurelles**, quelle que soit la décision : stablecoins, tokens adossés ou wrapped, tokens à
    levier.
  - **Gel** : la liste admise est figée au démarrage de chaque test ; une décision prise ensuite vaut pour les
    tests suivants.

**Règles techniques.**
- Rien n'est réel. CSI ne passe aucun ordre et n'utilise aucune clé, même en lecture seule : toutes les données
  sont publiques. Tout est simulé et journalisé.
- **Démarrage.** La commande `csi forward start <ID>` se lance DANS le conteneur de surveillance :

  ```
  docker compose exec monitor python -m crypto_signal_intelligence forward start <ID>
  ```

  C'est là que vivent le journal et le registre. L'image doit avoir été construite depuis un commit propre, avec
  `--build-arg CSI_CODE_COMMIT=<commit>` ; sans commit connu, le démarrage est refusé.
- **Gel.** Au démarrage, trois empreintes sont figées dans le journal du test :
  - **document** : la section « Cadre commun » et la section du test, de leur titre jusqu'au titre suivant ;
  - **paramètres** : les paramètres du test et les valeurs de configuration que lisent ses règles ;
  - **code** : les modules entiers qui mesurent et décident, constantes comprises, et les fonctions réutilisées.

  Chaque passage les recalcule, y compris après la date d'évaluation, tant que des décisions se résolvent. À la
  moindre différence, le test est ARRÊTÉ définitivement : le refaire, c'est pré-inscrire un nouveau test, sous un
  nouvel identifiant, compté comme un nouvel essai.

  L'orchestration (`forward/runner.py` : planification des passages, verrou, rapport) n'est pas gelée : elle ne
  fixe ni les règles, ni la mesure, ni le gel lui-même.
- **Verdict et clôture.** À la date d'évaluation, une fois l'horizon principal résolu, le verdict est inscrit au
  journal une seule fois (entrée VERDICT) : c'est lui qui fait foi. Quand toutes les décisions sont résolues,
  l'entrée CLOTURE termine le test : il n'est plus ni contrôlé ni calculé.

  Si le document est introuvable, par exemple dans une image mal construite, une alerte est écrite au journal de
  service, sans arrêt.
- **Essais.** Chaque démarrage est un essai du registre des expériences, avec la période « FORWARD ».
  - Ce registre est celui du volume de surveillance (`/srv/csi/experiments`).
  - Ces essais ne regardent pas les données de développement : ils ne s'ajoutent donc pas au N du Sharpe déflaté
    des lots de recherche.
  - Leur nombre est affiché à part, dans le rapport quotidien.
- **Journal** (`forward/journal.py`, `<CSI_ROOT>/forward/<ID>.jsonl`). Il fonctionne en ajout seul : chaque
  entrée est horodatée en UTC, contient les données d'entrée brutes et la décision, et porte une empreinte
  SHA-256 chaînée à l'entrée précédente. `csi forward status` vérifie toute la chaîne.
  - **Ligne coupée par un arrêt brutal** : elle reste dans le fichier. L'ajout suivant la signale par une entrée
    LIGNE_TRONQUEE, avec son empreinte, chaînée à la dernière entrée valide.
- **Un seul passage à la fois.** Un verrou de fichier, partagé par la surveillance et `csi forward run`,
  empêche deux processus d'inscrire la même décision. Les mesures ne comptent de toute façon qu'une fois chaque
  décision.
- **Frais**, identiques partout (`forward/costs.py`) :
  - 0,075 % par ordre ;
  - plus écart et glissement de 0,02 % par côté pour BTC et ETH, 0,05 % pour les autres paires ;
  - scénario défavorable : frais de 0,10 %, écart et glissement doublés.

  L'écart et le glissement sont des HYPOTHÈSES : les bougies ne les mesurent pas. Un test dont la conclusion
  dépendrait de leur valeur doit le dire, et en donner le seuil.
- **Rapport quotidien unique** : `reports/forward/<jour>.md` et `.json`, mis à jour à chaque passage horaire à
  partir de 01:00 UTC, et la carte « Tests en direct » de l'onglet Suivi.
- **Calendrier** : revue intermédiaire à 6 semaines, descriptive et technique, sans aucun changement de règle ;
  évaluation à 12 semaines, à la date inscrite au journal lors du démarrage.
- **Continuité** : un jour où la machine est éteinte reste un trou, jamais comblé après coup avec des valeurs
  reconstruites.
- **Portée** : un test en direct de 12 semaines ne valide pas une stratégie. Il dit ce qui s'est passé sur cette
  période, avec son intervalle.

## F0_DERIVES : relevé quotidien du financement et de l'intérêt ouvert

Ce n'est pas un test : c'est une collecte de données, sans hypothèse et sans essai.

Chaque jour après 00:10 UTC, pour chaque paire de la liste halal admise qui a un contrat perpétuel USDⓈ-M sur
Binance, le relevé enregistre au journal `F0_DERIVES` :
- la ligne brute de `/fapi/v1/premiumIndex` : prix de marque, indice, dernier financement, prochain règlement ;
- les règlements de financement des 26 dernières heures ;
- les 100 dernières valeurs horaires de l'intérêt ouvert, soit environ 4 jours.

Le perpétuel est cherché sous le même nom, ou préfixé « 1000 ». Toutes ces données sont publiques, en lecture
seule, via `fapi.binance.com`.

Binance ne garde que 30 jours d'historique horaire de l'intérêt ouvert. Ce relevé est la seule façon d'en
constituer un historique plus long. Si un jour est manqué, le relevé suivant le rattrape avec de vraies valeurs de
Binance, dans la limite de ces 4 jours.

Le jour n'est clos que si l'appel commun a réussi : sinon, un nouvel essai a lieu au passage suivant, sans
réinscrire les paires déjà relevées. Code : `forward/derivlog.py`.

## F0_DONNEES : relevé quotidien des données de contexte

Ce n'est pas un test : une collecte de données publiques, sans hypothèse ni essai (mission, phase 1.3). Aucune de ces
données n'influence un test en cours ; une phase qui voudra s'en servir le pré-inscrira. Sources validées par le
propriétaire le 2026-10-02, toutes gratuites et sans clé, lues par un client limité à leurs seules adresses
(`forward/sources.py`).

| Donnée | Source |
|---|---|
| Volatilité implicite BTC et ETH (indice DVOL) | Deribit, API publique |
| Indice Fear & Greed | alternative.me |
| Clôtures du Nasdaq 100 | FRED (série NASDAQ100), sinon l'API publique de Nasdaq (source notée à chaque relevé) |
| Indice dollar | recalculé avec la formule publique d'ICE aux taux de référence de la BCE (pas la cotation ICE) |
| Parité USDT et USDC | Kraken (milieu achat-vente), contrôle par Bitstamp |
| Liquidations 24 h, BTC et ETH | OKX seule : PARTIEL, une bourse parmi d'autres |
| ATR et ADX journaliers (Wilder, 14 jours) | calculés sur les bougies 1 h Binance déjà stockées, journées UTC complètes |
| Corrélation 30 jours BTC / Nasdaq 100 | calculée : rendements journaliers sur les séances communes |
| Écart achat-vente et profondeur à ±1 % | carnet public Binance Spot (`/api/v3/depth`, autorisé par le propriétaire le 2026-10-02, client séparé en lecture seule) : 1 000 niveaux, 5 000 pour BTC et ETH ; profondeur marquée « tronquée » si le carnet lu n'atteint pas ±1 % |

Chaque jour UTC après 00:10 : une entrée par source au journal `F0_DONNEES`, puis les indicateurs. Une source en
échec est réessayée à chaque passage ; le jour est clos quand tout a réussi, ou à 20:00 UTC. Un jour manqué reste
un trou. Code : `forward/datalog.py`.

## Calendrier des unlocks : non fait

Aucune source fiable n'est accessible sans abonnement ni clé (vérifié le 2026-10-02) :

| Source | Réponse |
|---|---|
| DefiLlama | « emissions » : 402, offre payante |
| CryptoRank | 401, clé exigée |
| Tokenomist | bloqué par Cloudflare |

Le propriétaire ne veut aucun abonnement payant sans gain démontré : cette collecte n'est pas faite.

## F1_MAKER_TAKER : ordre limite (maker) contre ordre au marché (taker) à l'entrée

**Hypothèse.** Entrer par un ordre limite placé au dernier prix connu quand l'ordre part, valable une heure,
donne en moyenne un meilleur résultat net par décision que l'ordre au marché. Cela doit tenir une fois comptées les décisions où
l'ordre limite n'est pas rempli, et MÊME SANS compter d'écart supposé à l'entrée du taker.

**Événements.**
- Les décisions sont les plans indicatifs que le suivi en direct (`outlook/tracking.py`) enregistre chaque jour
  après 00:10 UTC.
- Ils portent sur chaque paire suivie et chaque horizon de 24 heures, 3 jours et 7 jours.
- Chaque plan est un achat, avec un stop et un objectif relatifs à l'entrée, et une sortie à l'horizon.
- Seuls comptent les plans enregistrés après le démarrage, sur une paire de la liste halal figée, et dont l'entrée
  précède la date d'évaluation. Les autres sont seulement comptés (« hors screening »).
- Le test ne juge pas la qualité des plans : il compare deux façons d'entrer sur les MÊMES décisions.
- **Le code qui fabrique les plans n'est pas gelé.** C'est le module `outlook/pair.py` et l'enregistrement
  quotidien. Son empreinte est inscrite dans chaque décision, et le rapport dit combien de versions ont servi.

**Règles** (bougies de 15 minutes).
- **Heure d'entrée.** Un plan n'existe qu'à son heure d'enregistrement, en général 10 à 35 minutes après sa
  décision ; c'est l'heure réelle de son inscription dans la base du suivi. Les deux entrées commencent donc à la
  première bougie qui s'ouvre à cette heure ou après ; c'est `bars[0]`. Chaque décision inscrit ce retard et
  l'empreinte de la ligne du plan.
- **Taker.** C'est exactement la règle du plan (`tracking.replay_plan`) :
  - entrée à l'ouverture de `bars[0]` ;
  - stop au contact, ou à l'ouverture si elle est déjà sous le stop ;
  - objectif seulement s'il est dépassé ;
  - stop d'abord si les deux sont touchés dans la même bougie ;
  - sinon sortie à la clôture de la dernière bougie de l'horizon.
- **Maker.** Ordre limite au dernier prix connu quand l'ordre part : la clôture de la bougie qui se termine à
  l'heure d'entrée. Il est valable 4 bougies, soit une heure. Ce prix et sa bougie sont inscrits à la résolution.
  Une limite prise à la décision serait vieille de 15 à 30 minutes : le maker raterait mécaniquement les hausses
  et achèterait trop cher les baisses.
  - **Remplissage.** L'ordre n'est rempli que si le plus bas d'une de ces bougies passe STRICTEMENT sous
    `limite × (1 − traversée)`.
  - **Bougie du remplissage.** On suppose le pire : le stop est touché si le plus bas l'atteint, et l'objectif
    n'y est jamais compté.
  - **Ensuite**, mêmes règles que le taker, jusqu'à la même fin d'horizon.
  - **Non rempli** : aucune position, R = 0.
- **Sorties.** Le stop et l'objectif sont relatifs au prix d'entrée de chacun, avec le même pourcentage, donc le
  même risque.
- **Résolution.** Chaque entrée est rejouée SANS COÛT quand l'horizon est écoulé, sur les bougies arrivées après
  elle. Le journal garde l'issue et le rapport brut prix de sortie / prix d'entrée, et les coûts sont appliqués
  ensuite par formule. Si des bougies manquent encore, on attend ; 2 jours après l'horizon, la décision est notée
  « trou ».
- **Quatre lectures des coûts.** Seules les deux premières décident.

| Lecture | Remplissage du maker | Frais (les deux) | Écart d'entrée du taker | Écart de sortie (les deux) |
|---|---|---|---|---|
| observe | traversée nulle | 0,075 % | AUCUN | central |
| robuste | traversée de l'écart défavorable (0,04 % BTC/ETH, 0,10 % autres) | 0,075 % | AUCUN | central |
| modele_central (information) | traversée nulle | 0,075 % | central | central |
| modele_defavorable (information) | traversée défavorable | 0,10 % | défavorable | défavorable |

  - La lecture « observe » ne contient que ce que les bougies montrent : les décisions non remplies, le pire cas
    dans la bougie du remplissage et la différence de prix d'entrée.
  - La lecture « robuste » est défavorable AU MAKER : son ordre est supposé en fin de file. Seul le remplissage
    change : des frais plus élevés avantageraient le bras qui trade le moins, c'est-à-dire le maker.
  - Les lectures « modèle » appliquent le modèle de frais commun, où le taker paie l'écart supposé. Elles sont
    données à titre d'information seulement.

**Paramètres** (figés dans le code, `forward/f1.py`) :

| Paramètre | Valeur |
|---|---|
| Unité de temps | 15 minutes (`data.setup_timeframe`, gelée) |
| Horizons | 24 h, 3 j, 7 j |
| Horizon principal | 24 h |
| Prix limite | clôture de la bougie qui se termine à l'heure d'entrée |
| Validité | 4 bougies |
| Traversée | nulle (observe), écart défavorable (robuste) |
| Trou constaté | 2 jours après l'horizon |
| Minimum pour conclure | 30 jours et 500 décisions sur l'horizon principal |
| Rééchantillonnage | 10 000 tirages, graine 20261002 |

**Métrique.**
- Par décision : D = R maker − R taker, en R du plan.
- Moyenne de D et son IC95 par blocs de jours de la longueur de l'horizon (1, 3 ou 7 jours), avec au moins
  10 blocs. En sensibilité, le même IC est calculé avec des blocs de 7 jours.
- Rapportés aussi :
  - le taux de remplissage ;
  - le R moyen de chaque façon d'entrer ;
  - l'écart sur les seules décisions remplies ;
  - le coût des non-remplis, c'est-à-dire le R moyen que le taker a obtenu sur les décisions que le maker n'a
    pas eues ;
  - l'écart séparément pour BTC et ETH d'une part, et les autres paires d'autre part.
- **L'écart d'équilibre**, en lecture « observe », est le coût d'entrée du taker en points de base à partir
  duquel le maker devient meilleur en moyenne. Il vaut 0 si le maker est déjà meilleur sans écart, et il n'est
  pas calculé au-delà de 500 points de base.

**Seuil de décision**, sur l'horizon principal, à la date d'évaluation, une fois toutes ses décisions résolues :
- `INSUFFISANT` : moins de 30 jours ou moins de 500 décisions, ou intervalle non calculable ;
- `MAKER_MEILLEUR` : IC95 de la moyenne de D entièrement au-dessus de 0, en lecture « observe » ET en lecture
  « robuste » ;
- `TAKER_MEILLEUR_SANS_ECART` : IC95 entièrement en dessous de 0 en lecture « observe ». La conclusion dit alors
  que le maker ne devient meilleur que si l'écart et le glissement réels du taker dépassent l'écart d'équilibre ;
- sinon `PAS_DE_DIFFERENCE_DEMONTREE`, avec l'écart d'équilibre.

Les horizons de 3 et 7 jours sont descriptifs.

**Date d'évaluation.**
- **Fin du recueil** : 84 jours (12 semaines) après le démarrage. Les plans dont l'entrée tombe après cette date
  ne comptent pas.
- **Revue intermédiaire** : à 42 jours.
- **Verdict** : il tombe dès que toutes les décisions de 24 heures sont résolues, soit environ un jour après la
  fin du recueil. Ce délai monte à trois jours au plus si des bougies manquent. Il est alors inscrit au journal
  une fois pour toutes.
- **Inscription des dates** : les dates exactes sont inscrites au journal par `csi forward start` et reportées
  dans la section « Démarrages » ci-dessous.

**Puissance attendue.**
- Environ 100 à 150 décisions par jour et par horizon, soit environ 84 blocs journaliers sur l'horizon principal.
- Les décisions d'un même jour sont corrélées entre paires : le nombre effectif d'observations est plus proche
  du nombre de jours que du nombre de décisions.
- Avec 84 blocs, l'IC95 en percentiles couvre un peu moins de 95 % ; la sensibilité en blocs de 7 jours le
  signale si les jours se suivent.
- La puissance sera calculée à la revue intermédiaire, à partir de la dispersion observée des écarts journaliers.
  Ce calcul est descriptif et ne change aucune règle.

**Limites déclarées.**
- Les bougies de 15 minutes ne disent pas l'ordre des prix à l'intérieur d'une bougie : on prend le pire cas
  pour le maker.
- La place dans la file d'attente est inconnue : la lecture « robuste » suppose la fin de file.
- Les plans sont indicatifs, ce ne sont pas des signaux publiés.
- Le retard entre décision et enregistrement du plan rend l'entrée plus tardive que la décision.
- Les données du marché Spot public ne sont pas celles de Binance Demo.

## F2_ECHELLES : signaux de CSI, 1 objectif contre échelles de 2 à 7 objectifs

Demande du propriétaire du 2026-10-02 : suivre pendant des semaines les signaux de CSI joués avec plusieurs
objectifs.

**Hypothèse.** Sur les mêmes entrées, une échelle de k objectifs (k = 2 à 7) jouée avec la gestion « stop suiveur »
donne un R net moyen différent de la règle à un objectif avec laquelle les stratégies de CSI ont été jugées. Ces
stratégies sont REJETÉES (espérance négative après frais) : le test mesure l'effet de la SORTIE, il ne réhabilite
aucune entrée. Une échelle ne crée pas d'avantage à partir d'entrées qui n'en ont pas : la réponse attendue est
« pas de différence démontrée ».

**Événements.** Chaque signal du registre shadow de CSI (`signals/registry.sqlite3`) créé après le démarrage, sur
une paire de la liste halal figée ; les autres sont seulement comptés. Toutes les variantes utilisent la MÊME
entrée : ordre limite à l'entrée 1, à partir de la première bougie de 15 minutes qui s'ouvre à la création du
signal ou après, sur les seules bougies entièrement avant l'expiration de l'entrée du signal (`ENTRY_EXPIRES_AT`).
En direct, le signal est créé environ une minute après la clôture de décision : la fenêtre commence donc une
bougie plus tard que dans le walk-forward, qui supposait l'ordre posé à la clôture.

**Règles** (bougies de 15 minutes, moteur `external/trailing.simulate`, celui des signaux Telegram).
- `origine` : l'objectif unique du signal, stop fixe, sortie à la clôture après 96 bougies (24 heures) : la sortie
  avec laquelle le walk-forward a jugé la stratégie.
- `echelle_k` (k = 2 à 7) : objectifs à 1, 2, …, k fois le risque (entrée − stop) au-dessus de l'entrée ; parts
  vendues décroissantes (k, k−1, …, 1) ; stop à l'entrée après TP1, puis à TP(k−2) après TPk, appliqué dès la bougie
  suivante ; sortie au plus tard après 672 bougies (7 jours).
- Remplissage, stop au contact, objectif seulement s'il est dépassé, pire cas dans la bougie : règles du moteur.
- Résolution quand toutes les variantes sont terminées ou non remplies, sur les bougies arrivées après le signal ;
  « trou » si les bougies manquent encore 2 jours après la fin de l'horizon.

**Paramètres** (figés dans le code, `forward/f2.py`) :

| Paramètre | Valeur |
|---|---|
| Variantes | origine, echelle_2 à echelle_7 |
| Durée maximale | 24 h (origine), 7 jours (échelles) |
| Frais | modèle commun ; central et défavorable |
| Minimum pour conclure | 100 signaux remplis |
| Comparaisons | 6, niveau de chaque intervalle 1 − 0,05/6 |
| Rééchantillonnage | 10 000 tirages par blocs de 7 jours (au moins 8 blocs), graine 20261003 |
| Gel | modules f2, costs, registry, journal ; moteur de rejeu, lecture des signaux, règle d'expiration de l'entrée, magasin de bougies, paramètres des stratégies et unité de temps |

**Métrique.** Par signal rempli et par variante, R net (en risque initial du signal). Pour chaque échelle : écart
apparié au R de l'origine sur les mêmes signaux, moyenne et intervalle par blocs de 7 jours, au niveau
1 − 0,05/6. Rapportés aussi : R moyen, part gagnante et nombre de remplis de chaque variante, et l'intervalle
à 95 % du R moyen de l'origine.

**Seuil de décision**, pour chaque échelle, à la date d'évaluation et une fois tous les signaux résolus :
- `INSUFFISANT` : moins de 100 signaux remplis, ou intervalle non calculable ;
- `MEILLEURE` : intervalle de l'écart entièrement au-dessus de 0 en central ET en défavorable ;
- `MOINS_BONNE` : entièrement en dessous de 0 dans les deux ;
- sinon `PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** Fin du recueil 84 jours (12 semaines) après le démarrage ; revue intermédiaire à 42 jours ;
verdict une fois le dernier signal résolu, en général 7 jours après la fin du recueil (9 jours si des bougies
manquent), inscrit une fois au journal.

**Nombre d'événements attendu (estimation NON vérifiée).** Le registre ne comptait que 9 signaux au démarrage,
sur un jour et demi, et le taux de remplissage n'est pas mesuré : de l'ordre de 5 à 10 signaux par jour dont un
tiers à la moitié remplis donnerait 150 à 400 signaux remplis en 12 semaines ; moins de 100 donnera `INSUFFISANT`.
Les signaux d'un même jour sont corrélés : le nombre effectif d'observations est plus proche du nombre de semaines
(environ 12 blocs de 7 jours) ; à si peu de blocs, un intervalle par rééchantillonnage à 99,2 % est un peu trop
étroit : une conclusion limite doit être lue comme telle.

**Limites déclarées.** Les entrées viennent de stratégies rejetées ; les objectifs en multiples du risque sont une
règle simple, pas celle d'un analyste ; l'origine sort à 24 h et les échelles à 7 jours : un écart peut venir de
la durée d'exposition autant que de l'échelle ; bougies de 15 minutes (ordre des prix dans la bougie inconnu) ; marché Spot
public, pas Binance Demo. Un résultat ne valide aucune stratégie.

## F3_STABLECOINS : achat de BTC après une grosse émission de stablecoins

Phase 7 de la mission du 2026-10-02.

**Hypothèse.** Une création d'USDT ou d'USDC d'au moins 100 M$, en une transaction ou cumulée sur 1 heure,
représente de l'argent frais qui précède une hausse de BTC : un achat simulé de BTC après sa détection fait mieux,
net de frais, que des achats placebo aux mêmes heures dans les 30 jours précédents. Réponse attendue à 12 semaines,
avec une quinzaine d'événements : « insuffisant » ou « pas de différence démontrée ».

**Événements.** Créations lues sur les chaînes publiques, sans clé, à chaque cycle de la surveillance (toutes les
15 minutes) :

| Stablecoin | Chaîne | Lecture | Création |
|---|---|---|---|
| USDT | Tron | TronGrid, événements `Issue` du contrat `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t` | montant de l'événement |
| USDT | Ethereum | nœud public (`ethereum-rpc.publicnode.com`), événements `Issue(uint256)` du contrat `0xdAC1…1ec7` | montant de l'événement |
| USDC | Ethereum | même nœud, événements `Mint(address,address,uint256)` du contrat `0xA0b8…eB48` | montant, émetteur noté |

- Une création est COMPTÉE si elle vaut au moins 1 M$ et, pour l'USDC, si l'émetteur n'est pas un pont CCTP de
  Circle (`0xc492…e907`, `0xfd78…D002`) : ces créations correspondent à des USDC brûlés sur une autre chaîne, pas à
  de l'argent frais. Les créations non comptées sont inscrites au journal avec leur raison.
- Un **instant qualifiant** : le cumul des créations comptées du MÊME stablecoin sur l'heure qui précède atteint
  100 M$.
- Un **événement** : un instant qualifiant à 24 h ou plus du dernier instant qualifiant, tous stablecoins
  confondus ; les instants qualifiants suivants à moins de 24 h le prolongent (un seul événement). Les créations
  antérieures au démarrage ne comptent pas ; une création lue tardivement ne crée pas de second événement à moins
  de 24 h d'un événement déjà inscrit.
- **Détection** : l'heure du passage qui lit la création. **Latence** = détection − heure du bloc ; journalisée.
  Au-delà de 120 minutes (machine éteinte, source en panne), l'événement est `TARDIF` : compté, jamais joué. Un
  événement avant le démarrage ou après la fin du recueil est `HORS_FENETRE`.
- Journalisé pour chaque création : montant, stablecoin, chaîne, transaction, heure du bloc, émetteur, heure de
  détection, latence ; pour chaque événement : instant, cumul, créations, décision et jours placebo.

**Règles.**
- **Achat** : BTCUSDT, au prix d'ouverture de la première bougie de 1 minute (Binance Spot public) qui s'ouvre à
  DÉTECTION + 5 minutes ou après ; jamais à l'heure de l'émission. Une seule entrée, au marché.
- **Sorties** : vente au marché au prix d'ouverture de la première bougie de 1 minute qui s'ouvre 24 h, puis 72 h,
  après l'heure d'entrée (deux horizons, deux achats indépendants de même entrée).
- **Placebos** : 20 achats de BTC à la même heure (entrée − d jours, d tiré sans remise parmi 1 à 30, graine déduite
  de l'identifiant de l'événement : tirage reproductible, inscrit au journal à la décision), mêmes durées, mêmes
  frais.
- **Frais** : modèle commun, central et défavorable, payés à l'achat et à la vente (écart et glissement de BTC).
- **Résolution** : 72 h après l'entrée, à partir des bougies 1 minute publiques ; si une bougie voulue n'existe que
  plus de 10 minutes après l'heure demandée (bourse à l'arrêt), l'événement est un `TROU`, compté, exclu de la
  mesure ; si la source ne répond pas, nouvel essai à chaque passage, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f3.py`) :

| Paramètre | Valeur |
|---|---|
| Seuil | 100 M$ cumulés sur 1 h, par stablecoin ; créations comptées à partir de 1 M$ |
| Séparation des événements | 24 h depuis le dernier instant qualifiant |
| Latence d'entrée | 5 min après la détection ; détection à plus de 120 min : TARDIF |
| Fenêtre relue à chaque passage | 3 h (Tron), 1 000 blocs (Ethereum) |
| Horizons | 24 h et 72 h |
| Placebos | 20, dans les 30 jours précédents, même heure |
| Minimum pour conclure | 10 événements résolus par horizon |
| Comparaisons | 2 (un par horizon), niveau de chaque intervalle 1 − 0,05/2 |
| Rééchantillonnage | 10 000 tirages par blocs de 3 jours (au moins 8 blocs), graine 20261004 |
| Gel | modules f3, costs, registry, journal ; lecture des chaînes (`forward/sources.py` : `tron_events`, `eth_logs`, `eth_block_number`, `eth_block_timestamp`, `eth_rpc`), intervalle par blocs ; adresse REST des bougies |

**Métrique.** Par événement joué et par horizon : rendement net de l'achat moins la moyenne des rendements nets de
ses 20 placebos (« excès »). Moyenne de l'excès sur les événements et intervalle par blocs de 3 jours au niveau
1 − 0,05/2. Rapportés aussi : rendement moyen des achats, des placebos, part des événements qui battent leurs
placebos, nombre d'événements par stablecoin, latence médiane, créations ignorées, erreurs de source.

**Seuil de décision**, par horizon, à la date d'évaluation et une fois tous les événements résolus :
- `INSUFFISANT` : moins de 10 événements résolus, ou intervalle non calculable ;
- `EXCES_POSITIF` : intervalle de l'excès entièrement au-dessus de 0 en central ET en défavorable ;
- `EXCES_NEGATIF` : entièrement en dessous de 0 dans les deux ;
- sinon `PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** Fin du recueil 84 jours (12 semaines) après le démarrage ; revue intermédiaire à 42 jours ;
verdict une fois le dernier événement résolu (au plus 3 jours après la fin du recueil, 5 si la source des bougies
manque), inscrit une fois au journal.

**Nombre d'événements attendu (estimation, sources sondées le 2026-10-02).** Sur les 90 jours précédant la
pré-inscription, TronGrid montre 5 créations d'USDT d'un milliard chacune, dont 3 en une heure : 3 événements, soit
environ 3 en 12 semaines. Sur Ethereum, 14 heures de blocs lues sur le nœud public (4 plages de 1 000 blocs sur 6,
2 refusées par le nœud) montrent 47 créations d'USDC par l'émetteur de Circle (`0x5b61…47d7`, 108 M$ au total,
la plus grosse 50 M$, 13 d'au moins 1 M$), un cumul sur 1 h de 56 M$ au plus et AUCUN instant qualifiant ; les
ponts CCTP (`0xfd78…D002` : 2 137 créations, 102 M$ ; `0xc492…e907` : 141, 13 M$) sont exclus ; un quatrième
émetteur (`0x2222…c205`, 93 créations, 4 M$, la plus grosse 1,9 M$) est compté et inscrit avec son adresse. Aucune
création d'USDT sur Ethereum dans ces plages. Ordre de grandeur attendu : 5 à 20 événements en 12 semaines, les
jours de forte demande d'USDC pouvant en ajouter. À ce nombre, l'intervalle est large : une conclusion
`INSUFFISANT` ou `PAS_DE_DIFFERENCE_DEMONTREE` est l'issue la plus probable, et c'est une réponse. Le nœud public
refuse parfois une requête (quota) : l'erreur est journalisée et la lecture reprend au passage suivant ; une
création manquée plus de 2 heures devient `TARDIF`.

**Limites déclarées.** Une création n'est pas une vente : l'argent « frais » peut rester en trésorerie ; les
placebos partagent l'heure mais pas le contexte de marché ; deux horizons sur le même achat sont corrélés ; une
détection toutes les 15 minutes ajoute jusqu'à 15 minutes à la latence d'un vrai bot ; Tron et Ethereum seulement
(pas Solana ni les autres chaînes) ; 12 semaines ne valident rien, elles comptent et mesurent.

## F4_TELEGRAM : signaux Telegram reçus en direct, achat au premier prix, gestion du propriétaire, contre placebos

Phase 3 de la mission du 2026-10-02 (partie « en direct » ; l'historique exporté est traité par `csi audit-telegram`,
avec son biais de suppression déclaré).

**Hypothèse.** Un signal d'achat spot d'un fournisseur Telegram, acheté au premier prix après sa réception et géré
avec le stop suiveur du propriétaire, rapporte en moyenne plus, en R net, qu'un achat au même moment sur le même actif
gardé aussi longtemps, et que 20 achats de même géométrie à des moments tirés au hasard dans les 30 jours
précédents. Les audits sur l'historique (2 729 messages, 2026-04 → 2026-10) n'ont montré aucun fournisseur avec
un gain démontré : la réponse attendue est « non démontré » ou « insuffisant ».

**Événements.** Chaque message reçu après le démarrage par l'une des deux sources (`forward/telegram_live.py`),
dédoublonné sur son identifiant :
- la boîte de réception de BinanceSpotManager (`signals.sqlite3`, montée en lecture seule dans le conteneur de
  surveillance) : messages des conversations Telegram autorisées, horodatés à la réception par son bot ;
- le dossier de dépôt `imports/telegram/live/` (listes JSON du robot du propriétaire : identifiant, conversation,
  texte brut, heure de réception), rempli à la main ou par `POST /telegram/live`.

Un message est **joué** (DECISION) s'il est lisible par le parseur de CSI (`external/parser.py`, port du parseur
de BSM), long au comptant, avec une entrée, un stop et au moins un objectif, sur une paire de la liste halal figée,
et si son entrée précède la fin du recueil. Sinon il est **compté** avec sa raison : `ILLISIBLE`,
`SHORT_OU_LEVIER` (short, vente initiale, levier, futures, marge), `HORS_SCREENING`, `HORS_FENETRE`. Un message
signalé modifié par la source est inscrit comme tel ; les suppressions ne sont pas détectables. Le parseur n'est
pas gelé : son empreinte est inscrite dans chaque décision et le rapport dit combien de versions ont servi.

**Règles** (bougies de 1 minute publiques de Binance Spot, téléchargées à la résolution et conservées dans le
magasin ; moteur `external/trailing.simulate`).
- **Entrée** : au marché, à l'ouverture de la première bougie de 1 minute qui s'ouvre à réception + 60 s ou après ;
  prix effectif = ouverture × (1 + écart et glissement). Si cette bougie n'existe que plus de 10 minutes après
  l'heure voulue : `TROU`. Si l'ouverture est déjà sous le stop : `INVALIDE` ; déjà au-dessus du premier objectif :
  `DEJA_JOUE` (comptés, jamais mesurés).
- **Gestion** : stop et objectifs du signal (5 premiers objectifs, parts décroissantes 5, 4, 3, 2, 1), stop au
  contact, stop à l'entrée effective après TP1 puis à TP(k−2) après TPk, dès la bougie suivante ; pire cas dans la
  bougie ; sortie au plus tard après 43 200 bougies (30 jours), reste vendu à la clôture. La mention de clôture du
  stop (« 4h ») est ignorée : stop au contact.
- **Comparaison 1, même moment** : achat au même prix effectif, vendu au marché à la clôture de la bougie de sortie
  de la transaction du signal (même actif, même durée) ; écart en R.
- **Comparaison 2, placebos** : 20 achats du même actif à des décalages distincts tirés sans remise entre 1 440 et
  43 200 minutes avant l'entrée (graine déduite de l'identifiant du signal, inscrits à la décision), stop et
  objectifs au même rapport au prix d'entrée que ceux du signal, même gestion, mêmes frais ; excès = R du signal −
  moyenne des R des placebos (placebos encore ouverts : résolution différée ; placebo sans bougie : ignoré).
- **Frais** : modèle commun, central et défavorable, payés à chaque exécution.
- **Résolution** : quand la transaction du signal et ses placebos sont terminés ; trou constaté 2 jours après
  l'horizon si des bougies manquent ou si la source ne répond plus.

**Paramètres** (figés dans le code, `forward/f4.py`) :

| Paramètre | Valeur |
|---|---|
| Latence d'entrée | 60 s après la réception, bougies de 1 min |
| Objectifs | 5 au plus, parts 5/4/3/2/1 ; stop suiveur à 2 objectifs |
| Durée maximale | 43 200 bougies (30 jours) |
| Placebos | 20, décalages de 1 à 30 jours, mêmes niveaux relatifs |
| Minimum pour conclure, par fournisseur | 30 signaux résolus sur 10 jours |
| Comparaisons | 2 (même moment, placebos) ; intervalle de l'excès au niveau 1 − 0,05/2 ; intervalle du R à 95 % |
| Rééchantillonnage | 10 000 tirages par blocs de 7 jours (au moins 8 blocs), graine 20261005 |
| Téléchargement | au plus 60 pages de 1 000 bougies par paire et par passage |
| Gel | modules f4, telegram_live, costs, registry, journal ; moteur de rejeu, intervalle par blocs, téléchargement et normalisation des bougies, magasin ; adresse REST et latence supposée |

**Métrique.** Par fournisseur et pour l'ensemble, par scénario de frais : nombre de messages, part jouable (halal
et lisible), signaux résolus, jours, R net moyen avec son intervalle à 95 %, part gagnante, pire série de pertes,
durée moyenne de détention, écart moyen à l'achat au même moment, excès moyen sur les placebos avec son intervalle,
part des signaux qui battent leurs placebos ; comptes des messages illisibles, short ou levier, hors screening,
modifiés ; trous, invalides, déjà joués.

**Seuil de décision**, par fournisseur, à la date d'évaluation et une fois toutes les décisions résolues :
- `INSUFFISANT` : moins de 30 signaux résolus ou moins de 10 jours, ou un intervalle non calculable ;
- `SUPERIEUR_AU_HASARD` : intervalle du R moyen ET intervalle de l'excès sur les placebos entièrement au-dessus de
  0, en central ET en défavorable ;
- `INFERIEUR_AU_HASARD` : intervalle du R moyen entièrement en dessous de 0 dans les deux scénarios ;
- sinon `NON_DEMONTRE`.

**Date d'évaluation.** Fin du recueil 84 jours (12 semaines) après le démarrage ; revue intermédiaire à 42 jours ;
verdict une fois le dernier signal résolu (au plus 30 jours après la fin du recueil), inscrit une fois au journal.

**Nombre d'événements attendu (estimation NON vérifiée).** Le robot du propriétaire a reçu 186 signaux en
septembre 2026 (5 sources, 121 exploitables) et sa boîte BSM dépend des conversations autorisées : de l'ordre de
100 à 300 signaux joués en 12 semaines, répartis sur quelques fournisseurs ; seuls ceux qui dépassent 30 signaux
résolus auront un verdict. Les signaux d'un même jour et d'un même fournisseur sont corrélés : l'intervalle par
blocs de 7 jours en tient compte, au prix d'une largeur importante.

**Limites déclarées.** Marché Spot public, pas Binance Demo ; aucune suppression détectée (un fournisseur qui
efface ses pertes n'est pas visible ici non plus, mais ses messages déjà reçus restent comptés) ; la gestion est
celle du propriétaire, pas celle de l'analyste ; les placebos partagent la géométrie, pas le contexte ; bougies
1 min (ordre des prix dans la minute inconnu) ; une paire sans bougies 1 min (retrait de la cote) finit en trou ;
12 semaines ne valident rien.

## F5_MODELE_A : modèle A en direct (lot 8 v2), avec et sans feu tricolore, contre l'allocation statique

Phases 2 (suivi en direct) et 8 (feu tricolore) de la mission du 2026-10-02. Le backtest du lot 8 v2
(`docs/LONG_HORIZON.md` §11, essai LONG-20261002T155625Z-967d85) a jugé A, A + funding et B `NON_INTERESSANT` :
ce suivi vérifie le **comportement réel** de A et l'effet du feu, il ne valide rien et ne décide d'aucune mise en
production. B n'est pas suivi (non retenu).

**Hypothèse.** Descriptive : les décisions hebdomadaires de A en direct sont reproduites exactement à partir de
leurs entrées journalisées (conformité), et l'effet du feu tricolore (A + feu moins A) ainsi que la part de jours
rouges, orange et verts sont mesurés sur 12 semaines. Aucun seuil de performance : sur 3 mois, A fait peu de
transactions et un écart de rendement ne prouverait rien.

**Événements et règles.**
- **Univers** : paires de l'univers de recherche (40) admises par la liste halal figée au démarrage.
- **Décision de A** : chaque lundi, au premier passage après 00:10 UTC, sur les bougies 1 h closes avant lundi
  00:00 (journées, éligibilité par volume passé, votes à 4, 12 et 26 semaines, panier BTC, ETH + 3 plus liquides)
  et la volatilité prévue à 7 jours du jour (prévision quotidienne de CSI, `outlook/volatility`, σ̂ annualisée =
  mouvement à 7 jours × √(365/7)) : poids 1/5 × min(1, 0,50 / σ̂) pour un actif à au moins 2 votes sur 3. Les
  règles sont celles de `research/long_horizon` (v2) ; ce code de recherche n'est pas gelé : son empreinte est
  inscrite dans chaque décision, avec celle du modèle de volatilité.
- **Exécution** : à l'ouverture de la bougie 1 h de 01:00 du lundi, au premier passage après 01:10 ; bande de
  tolérance de 5 points (un actif n'est échangé que si son poids s'écarte de la cible de plus de 5 points ou s'il
  entre ou sort) ; frais du modèle commun, central et défavorable, par côté.
- **Feu du jour**, calculé au premier passage après 01:10 sur des informations connues à 00:00 UTC :
  - ROUGE si décision de la Fed, CPI ou NFP américain **le jour UTC même** (calendrier FIGÉ dans
    `forward/light.py`, sources : calendrier FOMC et calendriers du BLS relevés le 2026-10-02) ; ou prévision à
    7 jours de BTC dans les 10 % les plus hauts de ses 365 derniers jours (historique recalculé au démarrage avec
    les mêmes modèles, puis complété chaque jour ; rang inconnu sans 100 valeurs) ; ou financement moyen 7 jours
    du perpétuel BTC (relevé F0_DERIVES) au-dessus de 0,05 % par 8 h ; ou USDT ou USDC à plus de 0,5 % de sa
    parité (relevé F0_DONNEES, dernière valeur) ;
  - ROUGE pour un actif : titre d'annonce Binance « Binance Will Delist … » citant son code (catalogue
    Delisting de l'API publique des annonces ; contrats à terme et retraits de paires exclus) ;
  - ORANGE (si pas rouge) : samedi ou dimanche UTC ; dernier vendredi du mois ; maintenance ou mise à niveau
    annoncée par Binance pour le jour même ou le lendemain (catalogue Maintenance) ;
  - VERT sinon. Les entrées (rang, financement, parités, titres lus, erreurs de source) sont journalisées avec le feu.
- **Trois portefeuilles simulés**, valorisés chaque jour à l'ouverture de 01:00 (actif sans prix du jour : valeur
  conservée, aucun échange) :
  - `A` : les cibles du lundi, avec la bande ;
  - `A_FEU` : le lundi vert, les cibles de A ; un jour ROUGE : aucune entrée, positions réduites de moitié le
    premier jour rouge d'une série (pas de nouvelle réduction les jours rouges suivants) ; un jour ORANGE : aucun
    échange, positions conservées ; retour aux cibles de A au premier lundi vert ; actif rouge : vendu en entier ;
  - `STATIQUE` : 32,25 % du portefeuille (exposition moyenne de A dans l'essai cité) répartis à parts égales
    entre les membres du panier de la semaine, échangé seulement quand la composition change.

**Paramètres** (figés dans le code, `forward/f5.py` et `forward/light.py`) :

| Paramètre | Valeur |
|---|---|
| Décision / exécution | lundi 00:00 UTC (passage après 00:10) / ouverture de 01:00 (passage après 01:10) |
| Modèle | vote 2 sur 3 (28, 84, 182 jours), σ_cible 0,50, panier 5, bande 0,05 |
| Feu : volatilité | 10 % les plus hauts sur 365 jours |
| Feu : financement | > 0,05 % par 8 h, moyenne 7 jours |
| Feu : parité | > 0,5 % |
| Feu : calendrier | 11 dates figées (NFP, CPI, Fed) d'octobre 2026 à janvier 2027 |
| Rouge | réduction de moitié le premier jour, aucune entrée |
| Exposition statique | 0,3225 (essai LONG-20261002T155625Z-967d85, scénario central) |
| Gel | modules f5, light, costs, registry, journal, outlook.volatility ; magasin de bougies ; paires de la configuration |

**Métrique.** Par scénario de frais et par portefeuille : rendement total, volatilité annualisée des rendements
quotidiens, perte maximale, transactions, frais, exposition ; écart de rendement A + feu − A ; part de jours
rouges, orange, verts et leurs raisons ; actifs rouges ; conformité : chaque décision recalculée à partir de ses
entrées journalisées (votes, σ̂, panier) doit redonner ses poids.

**Seuil de décision.** Aucun seuil de performance. À la date d'évaluation : `SUIVI_TERMINE_CONFORME` si 100 %
des décisions sont reproduites, sinon `SUIVI_TERMINE_NON_CONFORME` (avec les semaines en écart). Les mesures sont
rapportées telles quelles, avec la mention « 3 mois ne valident rien ».

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict le lendemain de la
dernière valorisation, inscrit une fois au journal.

**Nombre d'événements attendu.** 12 décisions hebdomadaires, 84 valorisations, 8 jours rouges certains par le
calendrier (plus les jours de volatilité, financement ou parité), environ 24 jours de week-end orange.

**Limites déclarées.** Ouvertures de 01:00 sans impact de marché ; feu calculé sur des relevés quotidiens
(parité : dernière valeur, non intrajournalière) ; annonces lues par leurs titres seulement ; le calendrier macro
figé ne suit pas un changement de date du BLS ou de la Fed ; le code de recherche n'est pas gelé (empreinte par
décision) ; 12 semaines, aucune validation.

## F6_CAPITULATION : achat de BTC ou d'ETH après une capitulation du marché à levier

Phase 6 de la mission du 2026-10-02.

**Hypothèse.** Après une liquidation massive des positions à levier, les ventes forcées poussent le prix trop bas
et un rebond suit : un achat simulé après le signal fait mieux, net de frais, que 20 achats placebo du même actif
dans les 90 jours précédents, à 3 et 7 jours. Sur l'historique gratuit de développement (4 ans et demi), les
trois conditions n'ont été réunies que 4 fois (1 sur BTC, 3 sur ETH) : **environ 0,2 événement attendu en
12 semaines** ; l'issue attendue est `INSUFFISANT`, et c'est une réponse. Aucun backtest indicatif n'est lancé
(il coûterait un essai pour 4 événements).

**Règles.**
- Chaque jour, pour BTC et ETH (s'ils sont dans la liste halal figée), dès que le relevé F0_DERIVES du jour est
  inscrit (passage après 00:10 UTC) : un contrôle journalisé avec les trois indicateurs calculés sur des
  informations connues à 00:00 UTC :
  - financement moyen des règlements des 24 dernières heures (relevé du jour) **strictement négatif** ;
  - intérêt ouvert horaire : dernière valeur à 00:00 ou avant **inférieure d'au moins 15 %** à celle de 72 h plus
    tôt (même relevé) ;
  - clôture journalière (bougie 1 h de 23:00) de la veille **inférieure d'au moins 10 %** à celle de 3 jours
    plus tôt.
- Les trois réunies : événement. Pas de nouvel événement sur le même actif dans les 7 jours (compté
  `DELAI_7_JOURS`). Événement avant le démarrage ou après la fin du recueil : hors fenêtre.
- **Achat** simulé au premier prix (ouverture de la première bougie de 1 minute) après l'heure du calcul ;
  **sorties** au marché à 3 jours et à 7 jours (premières bougies après ces heures) ; frais du modèle commun,
  central et défavorable.
- **Placebos** : 20 achats du même actif aux mêmes heures, à des jours tirés sans remise entre 1 et 90 jours
  avant (graine déduite de l'événement, inscrits à la décision), mêmes durées, mêmes frais ; excès = rendement de
  l'achat − moyenne des placebos.
- Résolution 7 jours après l'entrée ; bougie voulue plus de 10 minutes en retard : `TROU` ; source muette :
  nouvel essai, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f6.py`) :

| Paramètre | Valeur |
|---|---|
| Actifs | BTCUSDT, ETHUSDT |
| Conditions | financement 24 h < 0 ; intérêt ouvert −15 % sur 3 jours ; prix −10 % sur 3 jours |
| Délai entre deux événements | 7 jours par actif |
| Horizons | 3 jours, 7 jours |
| Placebos | 20, de 1 à 90 jours avant, même heure |
| Minimum pour conclure | 10 événements résolus par horizon |
| Comparaisons | 2, niveau 1 − 0,05/2 ; rééchantillonnage 10 000 tirages par blocs de 7 jours, graine 20261006 |
| Gel | modules f6, costs, registry, journal ; intervalle par blocs ; magasin de bougies |

**Métrique.** Par horizon et scénario : événements résolus, rendement net moyen de l'achat, des placebos, excès
moyen et son intervalle, part des événements qui battent leurs placebos ; contrôles journaliers, derniers
indicateurs, événements par actif, en délai, en attente, trous.

**Seuil de décision**, par horizon : `INSUFFISANT` (moins de 10 résolus ou intervalle non calculable) ;
`EXCES_POSITIF` (intervalle entièrement au-dessus de 0 en central ET en défavorable) ; `EXCES_NEGATIF` (entièrement
en dessous dans les deux) ; sinon `PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu (7 jours après la fin du recueil au plus, 9 si des bougies manquent).

**Limites déclarées.** Intérêt ouvert et financement d'une seule bourse (Binance USDⓈ-M) ; seuils fixés par la
mission, pas calibrés ; très peu d'événements ; 12 semaines ne valident rien.

## F7_LISTINGS : achat après une annonce de listing d'Upbit ou l'apparition d'un produit chez Coinbase

Phase 5 de la mission du 2026-10-02.

**Hypothèse.** Un actif négociable sur Binance Spot et admis par le screening, acheté au premier prix après la
détection d'un listing chez Upbit ou Coinbase, rapporte en moyenne un rendement net positif à 24 h et à 7 jours,
en absolu et relativement à BTC. Attendu : 1 ou 2 événements en 12 semaines, donc `INSUFFISANT`.

**Événements.**
- **Upbit** : annonces publiques (API `api-manager.upbit.com`, catégorie « 거래 »), titres de la forme
  « …(SYMBOLE) 신규 거래지원 안내 … » (nouveau support de trading), lues toutes les 10 minutes ; heure d'annonce =
  `listed_at` ; latence = détection − annonce, journalisée.
- **Coinbase** : liste publique des produits (`api.exchange.coinbase.com/products`) ; une nouvelle devise de base
  en ligne par rapport au relevé précédent est un listing (Coinbase n'a pas d'API gratuite pour ses annonces :
  la détection se fait à la mise en ligne, plus tardive ; heure d'annonce inconnue, journalisée comme telle).
- Un listing dont au moins un actif a sa paire USDT dans la liste halal figée (donc négociable sur Binance Spot)
  est **joué** ; les autres sont comptés avec leur raison. Annonce antérieure au démarrage : ignorée.

**Règles.** Achat simulé au premier prix Binance (ouverture de la première bougie de 1 minute) **après la
détection**, jamais avant (la mission demande annonce + 60 s ; une lecture toutes les 10 minutes ne le permet pas
sans regarder le passé : la latence réelle est journalisée) ; sorties au marché à 24 h et à 7 jours ; frais du
modèle commun ; BTC acheté et vendu aux mêmes instants pour le rendement relatif. Résolution 7 jours après
l'entrée, mêmes règles de trou que F6.

**Paramètres** (figés dans le code, `forward/f7.py`) : horizons 24 h et 7 jours ; minimum 10 événements résolus
par horizon ; 2 comparaisons au niveau 1 − 0,05/2 ; rééchantillonnage 10 000 tirages par blocs de 7 jours,
graine 20261007 ; gel : modules f7, costs, registry, journal, intervalle par blocs.

**Métrique.** Par horizon et scénario : listings joués, rendement net moyen avec son intervalle, part gagnante,
rendement relatif à BTC avec son intervalle ; listings comptés par source, latence médiane, trous, erreurs de
source.

**Seuil de décision**, par horizon : `INSUFFISANT` ; `RENDEMENT_POSITIF` (intervalle du rendement entièrement
au-dessus de 0 en central ET en défavorable) ; `RENDEMENT_NEGATIF` ; sinon `PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le
dernier listing résolu.

**Limites déclarées.** Titres lus par un motif coréen fixe (un changement de formulation d'Upbit rend le test
muet, ce que le rapport montre) ; détection Coinbase tardive ; aucun placebo (la mission n'en demande pas) ;
l'effet « listing » est connu et largement exploité : un rendement positif ne serait pas une découverte.

## F8_NEWS : filtre de news à mots-clés appliqué au modèle A en direct

Phase 4 de la mission du 2026-10-02. **Aucun backtest** : comparaison en direct seulement, A contre A + news.

**Hypothèse.** Descriptive : mettre à zéro pendant 7 jours l'exposition d'un actif visé par une news de gravité 3
change le résultat du modèle A en direct ; le nombre de coupures, les actifs touchés et l'écart A + news − A sont
mesurés, sans seuil.

**Sources.** Les actualités que CSI collecte déjà toutes les 15 minutes, sans modèle de langage (`news/` :
flux RSS de CoinDesk, Cointelegraph, Decrypt, The Block, annonces Binance, Fed, BCE, SEC), texte brut conservé avec
ses révisions pour tester plus tard un autre classifieur.

**Règles.**
- **Liste de termes FIGÉE** (`forward/f8.py`), anglais et français : termes de vol (hack, hacked, exploit,
  exploited, drained, stolen, theft, piratage, piraté, exploité, vol de fonds, volé, siphonné…) et autres termes
  de gravité 3 (delisting, delist, lawsuit, sues, charged, indicted, insolvency, bankruptcy, withdrawals suspended
  ou halted, depeg, rug pull, exit scam ; radiation, poursuite, inculpé, mis en examen, insolvabilité, faillite,
  retraits suspendus, décrochage de la parité, arnaque de sortie…). Un changement de liste = un nouveau test.
- **Association prudente** (décision du propriétaire du 2026-10-02) : le terme doit être dans le **titre** ; l'actif
  (code exact ou nom complet, détection lexicale de `news/assets.py`, non gelée, empreinte inscrite) doit être
  cité **avant** le terme ; un seul actif admis cité avant le terme ; les termes de vol ne s'appliquent **jamais**
  à BTC ni à ETH. Tout autre cas avec un terme est journalisé `AMBIGU`, sans effet.
- **Effet** : dans le portefeuille `A_NEWS`, exposition à zéro sur l'actif pendant 7 jours à partir du jour UTC où
  la news a été vue (vente de la position le jour même, aucune entrée pendant 7 jours, puis retour aux cibles).
- **Rejeu** : chaque jour après 01:20 UTC, le portefeuille `A` et le portefeuille `A_NEWS` rejouent les décisions
  hebdomadaires et les prix d'ouverture de 01:00 journalisés par F5_MODELE_A (même bande, mêmes frais) ; la
  valeur de `A` doit coïncider avec celle de F5 (contrôle inscrit). Jour sans valorisation de F5 : compté, sauté.

**Paramètres** (figés dans le code) : liste de termes ; actifs protégés BTC, ETH ; effet 7 jours ; variantes A et
A_NEWS ; gel : modules f8, costs, registry, journal ; fonctions de portefeuille de F5.

**Métrique.** News avec terme (appliquées, ambiguës, par actif, termes vus), jours de valorisation, jours-actifs
coupés, rendement, volatilité, perte maximale, transactions et frais de A et de A + news (central et défavorable),
écart A + news − A, cohérence avec la valeur de A chez F5.

**Seuil de décision.** Aucun seuil de performance : `SUIVI_TERMINE` avec le nombre de coupures et l'écart, à la date
d'évaluation.

**Date d'évaluation.** Celle de F5_MODELE_A (84 jours après son démarrage) ; verdict le lendemain de la dernière
valorisation.

**Limites déclarées.** Classifieur à mots-clés : faux positifs et faux négatifs certains ; titres seulement ;
dépend de la collecte (une source en panne est visible dans l'état des sources, pas ici) ; un modèle de langage
local reste une option non implémentée sans accord du propriétaire.

## F9_OI_FLUSH : achat après une purge de l'intérêt ouvert, en direct

Étape 5 du plan de travail validé le 2026-10-02. Le criblage du marché à terme (`docs/DERIVATIVES.md`,
SCREEN-20261001T111500Z-60702b, 12 essais) a conclu `AUCUNE_PISTE` ; les estimations ponctuelles de OI_FLUSH
étaient positives sans intervalle au-dessus de 0. Ce test mesure la condition en direct, sans rien changer à
sa définition.

**Hypothèse.** Quand l'intérêt ouvert d'une paire (nombre de contrats du perpétuel USDⓈ-M de Binance) chute en
24 h sous son 10e centile historique et que le Spot baisse le même jour, un achat simulé le lendemain matin fait
mieux, net de frais, que 20 achats placebo de la même paire aux mêmes heures dans les 30 jours précédents, à 24 h
et 72 h. Réponse attendue : « pas de différence démontrée ».

**Événements et règles.**
- Paires : les 16 de la configuration (`data.symbols`) admises par la liste halal figée, toutes avec un perpétuel.
- Chaque jour, dès que le relevé F0_DERIVES du jour couvre au moins la moitié de ces paires (passage après
  00:10 UTC) : un contrôle journalisé avec, par paire, la variation de l'intérêt ouvert entre la dernière valeur
  horaire à 00:00 ou avant et celle de 24 h plus tôt (même relevé), et la variation de la clôture journalière
  (bougie 1 h de 23:00) de la veille sur celle de l'avant-veille.
- Événement : variation d'OI **≤ seuil de la paire** ET variation Spot **< 0**. Seuils FIGÉS, calculés le
  2026-10-02 sur la période de développement (2021 → 2025-06-30) à partir du magasin des dérivés (archives
  publiques, dernière valeur de chaque jour) : BTC −4,41 %, ETH −4,44 %, SOL −5,83 %, XRP −5,77 %, NEAR −6,00 %,
  AVAX −5,64 %, HBAR −8,44 %, LINK −5,83 %, XLM −6,68 %, ADA −5,46 %, TRX −7,35 %, FIL −4,97 %, ALGO −6,54 %,
  DOT −3,97 %, ATOM −5,63 %, ETC −6,82 %. Une paire peut déclencher plusieurs jours de suite (aucun délai : la
  définition du criblage n'en a pas).
- Achat simulé au premier prix (ouverture de la première bougie de 1 minute) après le calcul ; sorties au marché
  à 24 h et 72 h ; frais du modèle commun, central et défavorable.
- Placebos : 20 achats de la même paire aux mêmes heures, 1 à 30 jours avant (graine déduite de l'événement),
  mêmes durées, mêmes frais ; excès = rendement de l'achat − moyenne des placebos.
- Résolution 72 h après l'entrée ; bougie voulue plus de 10 minutes en retard : `TROU` ; source muette : nouvel
  essai, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f9.py`) : seuils ci-dessus ; horizons 24 h et 72 h ; 20 placebos de 1
à 30 jours ; minimum **30 événements résolus** par horizon ; 2 comparaisons au niveau 1 − 0,05/2 ;
rééchantillonnage 10 000 tirages par blocs de 7 jours (au moins 8 blocs), graine 20261008 ; gel : modules f9,
costs, registry, journal, intervalle par blocs, magasin de bougies, paires de la configuration.

**Métrique.** Par horizon et scénario : événements résolus, jours distincts, rendement net moyen de l'achat et des
placebos, excès moyen et son intervalle, part des événements qui battent leurs placebos ; événements par paire,
contrôles, en attente, trous.

**Seuil de décision**, par horizon : `INSUFFISANT` (moins de 30 résolus ou intervalle non calculable) ;
`EXCES_POSITIF` (intervalle entièrement au-dessus de 0 en central ET en défavorable) ; `EXCES_NEGATIF` ; sinon
`PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu (3 jours après la fin du recueil au plus, 5 si des bougies manquent).

**Nombre d'événements attendu.** Un 10e centile donne environ un jour sur dix par paire, dont une partie avec un
Spot en baisse : de l'ordre de 60 à 120 événements en 12 semaines, très corrélés entre paires (les purges sont
des jours de marché) ; l'intervalle par blocs de 7 jours en tient compte.

**Limites déclarées.** Intérêt ouvert d'une seule bourse, en nombre de contrats (pas en dollars) ; seuils d'une
autre période (2021-2025) ; événements corrélés ; 12 semaines ne valident rien.

## F10_PIVOT_BREAK_1D : achat après une cassure journalière d'un pivot haut confirmé, en direct

Suite du criblage K (`docs/SCREENING.md`, `SCREEN-20261002T170108Z-e89978`, étape 8 du plan). La condition K2 en
bougies journalières a passé la règle du criblage sur DEVELOPMENT (+2,04 % d'excès à 7 jours, IC95 [+0,19 ;
+4,55], 4 années sur 7) : une piste contaminée par le fait d'avoir été vue. Ce test la mesure en direct, sans rien
changer à sa définition (les fonctions du criblage sont gelées avec le test).

**Hypothèse.** Quand la clôture journalière d'une paire passe pour la première fois au-dessus de son dernier pivot
haut confirmé, un achat simulé le lendemain matin fait mieux, net de frais, que 20 achats placebo de la même paire
aux mêmes heures dans les 30 jours précédents, à 24 h et 168 h. Réponse attendue : « pas de différence démontrée ».

**Événements et règles.**
- Paires : celles de la configuration (`data.symbols`) admises par la liste halal figée.
- Chaque jour, après 00:10 UTC (la bougie 1 h de 23:00 doit être rangée) : un contrôle journalisé avec, par paire,
  les journées complètes (24 bougies 1 h, blocs alignés UTC) des 130 derniers jours, reconstruites comme au
  criblage ; une paire avec moins de **110 journées** complètes, ou dont la veille manque, est « non évaluable »
  ce jour-là (inscrit).
- Pivot haut : plus haut d'une journée i strictement au-dessus des **k = 3** journées précédentes et au moins égal
  à celles des 3 journées suivantes ; confirmé à la clôture de i + 3, utilisable à partir de la journée suivante ;
  niveau = dernier pivot confirmé, expiré après 100 journées.
- Événement : la veille (dernière journée close) est la **première clôture au-dessus du niveau** (clôture de
  l'avant-veille ≤ niveau) ; **un seul événement par niveau** (fonction `events_of` du criblage, gelée).
- Achat simulé au premier prix (ouverture de la première bougie de 1 minute) après le calcul ; sorties au marché à
  24 h et 168 h ; frais du modèle commun, central et défavorable.
- Placebos : 20 achats de la même paire aux mêmes heures, 1 à 30 jours avant (graine déduite de l'événement),
  mêmes durées, mêmes frais ; excès = rendement de l'achat − moyenne des placebos.
- Résolution 168 h après l'entrée ; bougie voulue plus de 10 minutes en retard : `TROU` ; source muette : nouvel
  essai, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f10.py`) : k = 3, niveau de 100 journées au plus, 110 journées
exigées, 130 jours lus ; horizons 24 h et 168 h ; 20 placebos de 1 à 30 jours ; minimum **30 événements résolus**
par horizon ; 2 comparaisons au niveau 1 − 0,05/2 ; rééchantillonnage 10 000 tirages par blocs de 7 jours (au moins
8 blocs), graine 20261010 ; gel : modules f10, costs, registry, journal, fonctions `aggregate`, `pivot_levels`,
`events_of` du criblage K, intervalle par blocs, magasin de bougies, paires de la configuration.

**Métrique.** Par horizon et scénario : événements résolus, jours distincts, rendement net moyen de l'achat et des
placebos, excès moyen et son intervalle, part des événements qui battent leurs placebos ; événements par paire,
contrôles, paires évaluables, en attente, trous.

**Seuil de décision**, par horizon : `INSUFFISANT` (moins de 30 résolus ou intervalle non calculable) ;
`EXCES_POSITIF` (intervalle entièrement au-dessus de 0 en central ET en défavorable) ; `EXCES_NEGATIF` ; sinon
`PAS_DE_DIFFERENCE_DEMONTREE`. L'horizon principal est 168 h (celui du criblage) ; 24 h est secondaire.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu (7 jours après la fin du recueil au plus, 9 si des bougies manquent).

**Nombre d'événements attendu.** Au criblage, environ 11 événements par paire et par an : de l'ordre de 35 à 45
sur 16 paires en 12 semaines, très corrélés entre paires (les cassures sont des jours de marché) ; `INSUFFISANT`
est probable si le marché est calme, et c'est une réponse acceptable.

**Limites déclarées.** Entrée vers 00:10-00:25 UTC au lieu de l'ouverture de 00:00 du criblage ; 16 paires au lieu
de 40 ; blocs journaliers alignés UTC ; événements corrélés ; 12 semaines ne valident rien, et un `EXCES_POSITIF`
sur deux comparaisons après 737 essais au programme resterait une indication, pas une preuve.

## F11_SELL_PRESSURE_VETO : achat le lendemain d'une pression vendeuse extrême, en direct (veto attendu)

Piste **après coup** du criblage J (`docs/SCREENING.md`, `SCREEN-20261002T165546Z-0994a2`) : la condition J2 (part
des achats au marché ≤ 10e centile des 365 journées précédentes) avait un excès négatif à 1 jour (−0,34 %,
IC95 [−0,57 ; −0,12]) et à 7 jours (−1,45 % [−2,73 ; −0,17]) sans avoir été déclarée comme veto. Elle n'entre
dans aucune règle ; ce test mesure en direct si l'observation tient sur des données jamais vues.

**Hypothèse.** Quand la part des achats au marché (taker) dans le volume en USDT d'une journée tombe sous son 10e
centile figé, un achat simulé le lendemain matin fait **moins bien**, net de frais, que 20 achats placebo de la
même paire aux mêmes heures dans les 30 jours précédents, à 24 h et 168 h. Réponse attendue : `EXCES_NEGATIF`
(veto justifié) ; `PAS_DE_DIFFERENCE_DEMONTREE` enterrerait la piste.

**Événements et règles.**
- Paires : les 16 de la configuration (`data.symbols`) admises par la liste halal figée.
- Chaque jour, après 00:10 UTC : un contrôle journalisé avec, par paire, la part des achats au marché de la veille
  (somme de `taker_buy_quote_volume` / somme de `quote_volume` sur les bougies 1 h ouvertes de 00:00 à 23:00 ; au
  moins 20 bougies, volume > 0, sinon « non évaluable »), la même définition que `research.flow_screen.daily_flow`.
- Événement : part de la veille **≤ seuil de la paire**. Seuils FIGÉS, calculés le 2026-10-02 sur les 365 dernières
  journées de DEVELOPMENT (2024-07-01 → 2025-06-30, magasin long), 10e centile : BTC 0,4570, ETH 0,4681, SOL 0,4692,
  XRP 0,4652, NEAR 0,4704, AVAX 0,4622, HBAR 0,4521, LINK 0,4447, XLM 0,4470, ADA 0,4629, TRX 0,4548, FIL 0,4523,
  ALGO 0,4669, DOT 0,4536, ATOM 0,4521, ETC 0,4313. Une paire peut déclencher plusieurs jours de suite.
- Achat simulé au premier prix (ouverture de la première bougie de 1 minute) après le calcul ; sorties au marché à
  24 h et 168 h ; frais du modèle commun, central et défavorable.
- Placebos : 20 achats de la même paire aux mêmes heures, 1 à 30 jours avant (graine déduite de l'événement),
  mêmes durées, mêmes frais ; excès = rendement de l'achat − moyenne des placebos.
- Résolution 168 h après l'entrée ; bougie voulue plus de 10 minutes en retard : `TROU` ; source muette : nouvel
  essai, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f11.py`) : seuils ci-dessus ; journée valide à 20 bougies ; horizons
24 h et 168 h ; 20 placebos de 1 à 30 jours ; minimum **30 événements résolus** par horizon ; 2 comparaisons au
niveau 1 − 0,05/2 ; rééchantillonnage 10 000 tirages par blocs de 7 jours (au moins 8 blocs), graine 20261011 ;
gel : modules f11, costs, registry, journal, intervalle par blocs, magasin de bougies, paires de la configuration.

**Métrique.** Par horizon et scénario : événements résolus, jours distincts, rendement net moyen de l'achat et des
placebos, excès moyen et son intervalle, part des événements qui battent leurs placebos ; événements par paire,
contrôles, paires évaluables, en attente, trous.

**Seuil de décision**, par horizon : `INSUFFISANT` (moins de 30 résolus ou intervalle non calculable) ;
`EXCES_NEGATIF` (intervalle entièrement sous 0 en central ET en défavorable : veto justifié) ; `EXCES_POSITIF`
(intervalle entièrement au-dessus de 0 : la piste est contredite) ; sinon `PAS_DE_DIFFERENCE_DEMONTREE`. Un
`EXCES_NEGATIF` sur 12 semaines n'installe aucun veto automatiquement : il justifierait de le pré-inscrire comme
règle de veto pour la période suivante, décision du propriétaire.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu (7 jours après la fin du recueil au plus, 9 si des bougies manquent).

**Nombre d'événements attendu.** Un 10e centile donne environ un jour sur dix par paire si le régime de 2024-2025
tient : de l'ordre de 100 à 140 événements en 12 semaines sur 16 paires, très corrélés entre paires (les journées
de ventes sont des journées de marché).

**Limites déclarées.** Piste choisie après avoir vu DEVELOPMENT (c'est précisément pourquoi elle est testée en
direct) ; seuils d'une autre période et d'un seul marché (Binance) ; une part d'achats structurellement plus basse
en 2026 (changement de mix maker/taker) déclencherait trop souvent, ce que le nombre d'événements révélera ;
12 semaines ne valident rien.

## F12_VOL_FORWARD : prévisions de volatilité retenues sur DEVELOPMENT, mesurées en direct

Les seuls résultats positifs du programme sont des prévisions d'**ampleur** : lot 7 (`VOL-20261001T194742Z-926ff2`),
v2 (`VOL-20261002T170500Z-c3bda6`, moyenne de HAR + BTC et LightGBM meilleure à 3 jours) et v3
(`VOL-20261002T211837Z-be55c0`, HAR + profil heure × jour meilleur que la règle des 24 h). Tous sont des sélections
sur DEVELOPMENT ; le service ne journalise que le modèle retenu par horizon. Ce test journalise **toutes** les
prévisions chaque jour et mesure leurs erreurs sur des données jamais vues, sans rien sélectionner.

**Hypothèse.** En direct, sur les paires de la configuration : (1) la moyenne de HAR + BTC et LightGBM prévoit la
variance réalisée à 3 jours mieux que LightGBM seul ; (2) HAR + profil prévoit la variance des 24 h suivantes mieux
que la règle des 24 dernières heures ; (3) LightGBM à 3 jours et HAR + BTC à 7 jours font mieux que la règle des
7 jours. Réponse attendue : `CONFIRME` pour ces trois ; `PAS_DE_DIFFERENCE` pour la moyenne à 1 et 7 jours.

**Règles.**
- Paires : celles de la configuration (`data.symbols`) admises par la liste halal figée, évaluables si leurs
  variables sont complètes et qu'elles ont 400 jours d'historique (règles du lot 7 et de v3).
- Chaque jour après 00:10 UTC, à l'origine 00:00 : prévisions de variance par `M0_RECENT_7D`, `M4_HAR_POOLED_BTC`,
  `M5_LGBM_POOLED` et `V1_MEAN_M4_M5` à 1, 3 et 7 jours (modèles du lot 7, fonctions de recherche gelées, réajustés
  le **1er du mois** sur les bougies 1 h du magasin de la surveillance jusqu'à l'origine), et par `R0_RECENT_24H`,
  `H1_HAR_PROFILE`, `H2_LGBM_PROFILE` à 4 h et 24 h (modèles de v3, réajustés le **1er du trimestre** sur 3 ans
  glissants). Une entrée `PREVISION` par jour.
- Résolution 7 jours (+ 2 h) après l'origine : variance réalisée de chaque horizon lue par les mêmes fonctions de
  recherche (cible contiguë : une bougie manquante → pas de valeur) et perte **QLIKE** de chaque prévision ; une
  entrée `RESOLUTION` par jour.

**Paramètres** (figés dans le code, `forward/f12.py`) : **7 comparaisons** (candidat contre référence) : moyenne
contre LightGBM à 3 j (attendu `CONFIRME`), à 1 j et 7 j (attendu `PAS_DE_DIFFERENCE`, à 7 j contre HAR + BTC) ;
HAR + profil contre la règle des 24 h à 24 h (attendu `CONFIRME`) et à 4 h (attendu `CONFIRME` : QLIKE net sur
DEVELOPMENT, non retenu par la règle du § 7) ; LightGBM contre la règle des 7 jours à 3 j et HAR + BTC à 7 j
(attendu `CONFIRME`) ; différence de QLIKE moyennée par jour sur les paires ; intervalle de Student par
**blocs de 10 jours** calendaires (au moins 6 blocs), niveau 1 − 0,05/7 ; minimum **60 jours** résolus ; gel : modules f12,
registry, journal ; fonctions `daily_frame`, `complete_rows`, `fit_at`, `month_forecasts` du lot 7, `hourly_frame`,
`complete_rows`, `fit_at`, `quarter_forecasts` de v3, `calendar_mean_ci`, magasin de bougies, paires de la
configuration. Sources : `VOL-20261001T194742Z-926ff2`, `VOL-20261002T170500Z-c3bda6`, `VOL-20261002T211837Z-be55c0`.

**Métrique.** Par comparaison : jours résolus, QLIKE moyen du candidat et de la référence, différence moyenne et son
intervalle ; prévisions journalisées, résolues, en attente, paires évaluables par jour.

**Seuil de décision**, par comparaison : `INSUFFISANT` (moins de 60 jours ou intervalle non calculable) ;
`CONFIRME` (borne haute < 0 : le candidat fait mieux) ; `INFIRME` (borne basse > 0) ; sinon `PAS_DE_DIFFERENCE`.
Un `CONFIRME` ne branche rien automatiquement : il autorise le propriétaire à décider un branchement (v2 à 3 jours :
moyenne des deux modèles déjà calculés ; v3 à 24 h : volatilité horaire pour les stops et objectifs).

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois la dernière
origine résolue (7 jours après la fin du recueil).

**Limites déclarées.** 16 paires au lieu de 38 ; 12 semaines d'un seul régime ; une valeur par jour, paires
corrélées ; les modèles horaires sont réajustés sur le magasin de la surveillance (bougies depuis 2021), pas sur le
magasin long ; aucune mesure de rentabilité, aucune direction.

## F13_PIVOT_BREAK_1D_24 : la condition de F10 sur les 24 autres paires de recherche, en direct

Plan de travail du 2026-10-03, validé par le propriétaire. F10 mesure la cassure journalière d'un pivot haut
confirmé (K2, seule condition qui a passé un criblage) sur les 16 paires de la configuration, avec environ 35 à 45
événements attendus en 12 semaines : un verdict `INSUFFISANT` est probable. Ce test applique **exactement les mêmes
règles** aux 24 autres paires de recherche, pour avoir, avec F10, la mesure sur 40 paires.

**Hypothèse.** Sur ces 24 paires, quand la clôture journalière passe pour la première fois au-dessus du dernier
pivot haut confirmé, un achat simulé le lendemain matin fait mieux, net de frais, que 20 achats placebo de la même
paire aux mêmes heures dans les 30 jours précédents, à 24 h et 168 h. Réponse attendue : « pas de différence
démontrée ».

**Règles.** Celles de F10, sans exception : les fonctions de F10 (lecture du jour, placebos, rendement net,
résolution, mesures, verdict) sont **réutilisées telles quelles** et gelées avec ce test. Seule la liste des paires
change : APT, ARB, BCH, DASH, DOGE, EGLD, FET, ICP, IOTA, LTC, NEO, OP, POL, QNT, RENDER, ROSE, SEI, SUI, TAO, THETA,
TIA, VET, XTZ, ZEC (toutes contre USDT, les 40 paires de `research/universe.py` moins les 16 de la configuration,
toutes admises par le screening halal au 2026-10-03), figée dans `forward/f13.py`. Les bougies 1 h de ces paires
sont rafraîchies à chaque cycle de 15 minutes par la surveillance.

**Paramètres** (figés : `forward/f13.py` et `forward/f10.py`) : ceux de F10 (k = 3, 110 journées, 130 jours lus,
24 h et 168 h, 20 placebos de 1 à 30 jours, **30 événements résolus** minimum, 2 comparaisons au niveau 1 − 0,05/2,
10 000 tirages par blocs de 7 jours, graine de F10) et la liste des 24 paires ; gel : modules f13, f10, costs,
registry, journal, fonctions du criblage K, intervalle par blocs, magasin de bougies.

**Métrique.** Celle de F10 : par horizon et scénario, événements résolus, jours distincts, rendement net de l'achat
et des placebos, excès moyen et son intervalle, part des événements qui battent leurs placebos ; événements par
paire, contrôles, paires évaluables, en attente, trous.

**Seuil de décision.** Celui de F10, par horizon : `INSUFFISANT` (moins de 30 résolus ou intervalle non calculable) ;
`EXCES_POSITIF` (intervalle au-dessus de 0 en central ET en défavorable) ; `EXCES_NEGATIF` ; sinon
`PAS_DE_DIFFERENCE_DEMONTREE`. Lecture commune avec F10 déclarée maintenant : la condition ne sera dite
« confirmée en direct » que si F10 **et** F13 sont tous deux `EXCES_POSITIF` à 168 h (une seule chance de
confirmation, pas deux ; F14 regarde les mêmes événements avec d'autres sorties et ne compte pas dans cette
lecture).

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu.

**Nombre d'événements attendu.** Environ 11 par paire et par an au criblage : de l'ordre de 50 à 65 en 12 semaines
sur 24 paires, corrélés entre paires.

**Limites déclarées.** Démarrage environ 22 heures après F10 (périodes presque identiques) ; paires moins liquides
(coûts « autres » du modèle commun) ; ces 24 paires figuraient dans le criblage K (DEVELOPMENT) : ce n'est pas un
hors-échantillon par actif, c'est un hors-échantillon dans le temps, comme F10. Comme pour F10, les placebos de 1 à
6 jours avant l'événement couvrent dans leur fenêtre la journée de cassure, qui monte par construction (environ un
placebo sur cinq) : la comparaison aux placebos est prudente. Les intervalles regroupent des blocs de 7 jours
**ayant un événement** : il faut au moins 50 jours distincts avec événement ; les cassures se groupent dans les
journées de hausse, un verdict `INSUFFISANT` reste plausible.

## F14_PIVOT_BREAK_VOL_LEVELS : les événements K2 avec niveaux placés par la volatilité prévue, en direct

Plan de travail du 2026-10-03, validé par le propriétaire. Les modèles de volatilité ne disent pas le sens ; ce
test les utilise pour ce qu'ils savent faire : placer les niveaux.

**Hypothèse.** Sur les 40 paires de recherche, un achat après une cassure journalière d'un pivot haut confirmé,
avec un stop à 1 σ̂ et un objectif à 1,5 σ̂ (σ̂ : mouvement typique prévu à 3 jours par la prévision en service),
fait mieux, net de frais et en R, (1) que 20 achats placebo de même géométrie et (2) que le même achat sorti à date
fixe (168 h). Réponse attendue : « pas de différence démontrée » pour les deux.

**Événements et règles.**
- Événements : la condition de F10 (fonctions gelées de F10) sur les 40 paires (les 16 de la configuration et les
  24 de F13), contrôle après 00:10 UTC, une fois par jour, **seulement après l'écriture de la prévision de
  volatilité du jour** (`state/volatility.json`, origine du jour) ; une paire sans prévision ce jour-là donne un
  événement inscrit « sans prévision », non joué.
- σ̂ = `move_pct` à 3 jours de la prévision en service (LightGBM du lot 7), divisé par 100, lu à la détection ;
  l'origine, l'exécution source et les modèles du fichier sont inscrits avec l'événement. Si la prévision du jour
  n'est pas écrite à 23:00 UTC, le contrôle a lieu quand même et les événements sont inscrits « sans prévision »
  (une cassure n'émet qu'un événement par niveau : sinon elle serait perdue sans trace).
- Achat au premier prix (ouverture de la première bougie de 1 minute) après la détection ; **stop** à entrée ×
  (1 − 1,0 σ̂), **objectif** à entrée × (1 + 1,5 σ̂) ; sinon sortie au marché à 168 h.
- Chemin : bougies 1 h du magasin de la surveillance, de l'heure pleine qui suit l'entrée jusqu'à 168 h ; ouverture
  sous le stop → sortie à l'ouverture ; ouverture au-dessus de l'objectif → sortie au prix de l'objectif (ordre
  limite posé d'avance) ; stop et objectif dans la même bougie → stop. Frais du modèle commun (objectif : ordre
  limite, frais seulement ; stop et sortie à date : ordre au marché). Toutes les bougies 1 h attendues doivent être
  là (première, dernière et sans trou), sinon `TROU`.
- R = rendement net / (1,0 σ̂). Placebos : 20 achats de la même paire aux mêmes heures 1 à 30 jours avant (graine
  déduite de l'événement), **même σ̂** que l'événement, mêmes règles. Référence appariée : le même événement sorti à
  date fixe (168 h), exprimé en R du même risque.
- Résolution 168 h après l'entrée ; bougie 1 min plus de 10 minutes en retard ou trou dans les bougies 1 h : `TROU` ;
  source muette : nouvel essai, trou constaté 2 jours après l'horizon.

**Paramètres** (figés dans le code, `forward/f14.py`) : 40 paires listées ; σ̂ à 3 jours ; stop 1,0 σ̂, objectif
1,5 σ̂ ; 168 h ; 20 placebos de 1 à 30 jours ; minimum **30 événements résolus** ; 2 comparaisons (contre les
placebos, contre la sortie à date) au niveau 1 − 0,05/2 ; 10 000 tirages par blocs de 7 jours (au moins 8 blocs),
graine 20261014 ; échéance de la prévision 23:00 UTC ; gel : modules f14, f13, f10, costs, registry, journal,
`outlook.volatility` (source de σ̂), fonctions `daily_frame`, `complete_rows`, `fit_at`, `month_forecasts` du lot 7,
fonctions du criblage K, intervalle par blocs, magasin de bougies.

**Métrique.** Par comparaison et scénario : événements résolus, R moyen de l'achat, écart moyen (contre les
placebos, contre la sortie à date) et son intervalle, part des écarts positifs ; événements par paire, sans
prévision, en attente, trous.

**Seuil de décision**, par comparaison : `INSUFFISANT` (moins de 30 résolus ou intervalle non calculable) ;
`EXCES_POSITIF` (intervalle au-dessus de 0 en central ET en défavorable) ; `EXCES_NEGATIF` ; sinon
`PAS_DE_DIFFERENCE_DEMONTREE`.

**Date d'évaluation.** 84 jours après le démarrage (revue intermédiaire à 42 jours) ; verdict une fois le dernier
événement résolu (7 jours après la fin du recueil au plus, 9 si des bougies manquent).

**Nombre d'événements attendu.** La somme de F10 et F13 : environ 85 à 110 en 12 semaines, moins ceux sans
prévision (paires de moins de 400 jours d'historique : aucune parmi les 40).

**Limites déclarées.** σ̂ vient d'un modèle sélectionné sur DEVELOPMENT (non confirmé sur la période finale) ; les
multiples 1,0 et 1,5 sont ceux du plan du tableau de bord, choisis sans optimisation ; la première heure partielle
après l'entrée (≈ 40 min) et les minutes entre la dernière bougie 1 h et la sortie à 168 h (≈ 15 min) ne sont pas
surveillées ; placebos de même σ̂ que l'événement, pas de leur propre jour ; le LightGBM « commun » en service est
réajusté chaque mois sur l'univers du moment (les paires ajoutées par le screening y entrent) : σ̂ n'est pas une
définition fixe ; mêmes réserves que F13 sur les placebos proches de la cassure et le nombre de jours à événement.

## VOTE_V1 : modèle de vote, définition pré-enregistrée (phase 9, NON évalué)

Défini maintenant, conformément à la mission ; **aucun code d'évaluation, aucune mesure sur les 12 semaines en
cours** (ses composants y sont sélectionnés). Il démarrera en direct sur la période SUIVANTE, uniquement avec les
composants qui auront individuellement passé leur seuil ; un composant sans seuil passé n'entre pas.

- **Composants** (poids égaux) et leur vote quotidien, calculé à 00:00 UTC sur des informations connues :
  1. **Tendance de A** (F5) : +1 pour un actif détenu par A cette semaine (≥ 2 votes sur 3), −1 sinon ;
  2. **Capitulation** (F6, seuil `EXCES_POSITIF` à 3 ou 7 jours) : +1 pour BTC et ETH pendant les 7 jours qui
     suivent un événement, 0 sinon ;
  3. **Émission de stablecoins** (F3, seuil `EXCES_POSITIF` à 24 h ou 72 h) : +1 pour BTC pendant les 72 h qui
     suivent un événement joué, 0 sinon ;
  4. **Filtre de news** (F8, retenu seulement si le propriétaire valide son apport) : −1 pour un actif coupé
     (7 jours), 0 sinon.
- **Règle de combinaison, fixée maintenant** : pour chaque actif, score = somme des votes des composants actifs ;
  exposition = poids de A × max(0, score) / (nombre de composants actifs), plafonnée au poids de A ; un score ≤ 0
  = stablecoin. Avec A seul actif, le vote est A.
- **Pré-inscription** : avant tout démarrage, une section « F9_VOTE » sera écrite avec les composants retenus
  (ceux ayant passé leur seuil), la période, le modèle de frais commun et la référence (A seul), puis figée
  comme les autres tests. Jusque-là, rien n'est calculé.

## Démarrages

Historique des démarrages et des arrêts. Cette section est hors empreinte : on y ajoute, on n'y modifie rien.

- 2026-10-02 00:34 UTC : **F1_MAKER_TAKER démarré** dans le conteneur de surveillance (essai FWD-20261002T003429Z-b4ac75, commit dd50b4f). 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil et évaluation le 2026-12-25. Relu trois fois avant démarrage (verdict final : GO).
- 2026-10-02 10:45 UTC : **F2_ECHELLES démarré** dans le conteneur de surveillance (essai FWD-20261002T104515Z-87ed9d, commit 76bff87). 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25. Relu avant démarrage (6 corrections appliquées).
- 2026-10-02 : relevé F0_DERIVES en service (premier jour : 160 paires, 6 sans perpétuel, aucune erreur).
- 2026-10-02 15:32 UTC : **F3_STABLECOINS démarré** dans le conteneur de surveillance (essai FWD-20261002T153210Z-fe5857, commit e18e985). 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25. Sources sondées le jour même (TronGrid, nœud Ethereum public, signatures contrôlées).
- 2026-10-02 15:52 UTC : **F4_TELEGRAM démarré** dans le conteneur de surveillance (essai FWD-20261002T155203Z-02f161, commit 9e19948). 166 paires halal figées. Boîte de BSM montée en lecture seule (vide au démarrage : son bot n'a encore rien reçu) ; dépôt du robot vide. Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25.
- 2026-10-02 16:18 UTC : **F5_MODELE_A** (essai FWD-20261002T161825Z-d27ebd), **F6_CAPITULATION** (FWD-20261002T161827Z-d9ffb2), **F7_LISTINGS** (FWD-20261002T161830Z-9c9ce5) et **F8_NEWS** (FWD-20261002T161832Z-0c1dc4) **démarrés** dans le conteneur de surveillance, commit ea01aa1, 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25. Note : l'image précédente (commit 3436e43, 16:11 → 16:18 UTC) importait des modules absents ; aucun passage des tests en direct n'a eu lieu pendant ces 7 minutes (aucune décision perdue : les passages suivants reprennent les données).
- 2026-10-02 16:24 UTC : **F9_OI_FLUSH démarré** dans le conteneur de surveillance (essai FWD-20261002T162403Z-ce3679, commit c811151). 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25.
- 2026-10-02 17:11 UTC : **F10_PIVOT_BREAK_1D démarré** dans le conteneur de surveillance (essai FWD-20261002T171111Z-21c572, commit 724415e). 166 paires halal figées (16 paires de la configuration évaluées). Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25.
- 2026-10-02 21:13 UTC : **F11_SELL_PRESSURE_VETO démarré** dans le conteneur de surveillance (essai FWD-20261002T211314Z-c9426a, commit be7621c). 166 paires halal figées (16 paires de la configuration évaluées). Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25.
- 2026-10-02 21:34 UTC : **F12_VOL_FORWARD démarré** dans le conteneur de surveillance (essai FWD-20261002T213424Z-f232c2, commit cedede2). 166 paires halal figées (16 paires de la configuration évaluées). Revue intermédiaire le 2026-11-13, fin du recueil le 2026-12-25.
- 2026-10-03 : **frais centraux de la configuration passés de 10 à 7,5 pb par ordre** (remise BNB, `docs/PROTOCOL.md`). Aucune empreinte ne change (la configuration des coûts n'est gelée par aucun test). Effet daté sur les ENTRÉES de deux tests : F2 (signaux shadow : le veto sur le RR net central laisse passer un peu plus de signaux à partir de cette date) et F1 (plans indicatifs dont le choix s'appuie sur les coûts centraux). Leurs mesures utilisent leur propre modèle de frais gelé (`forward/costs.py`, 7,5 pb central) et restent comparables ; le rapport final distinguera les décisions d'avant et d'après.
