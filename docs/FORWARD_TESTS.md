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

## Démarrages

Historique des démarrages et des arrêts. Cette section est hors empreinte : on y ajoute, on n'y modifie rien.

- 2026-10-02 00:34 UTC : **F1_MAKER_TAKER démarré** dans le conteneur de surveillance (essai FWD-20261002T003429Z-b4ac75, commit dd50b4f). 166 paires halal figées. Revue intermédiaire le 2026-11-13, fin du recueil et évaluation le 2026-12-25. Relu trois fois avant démarrage (verdict final : GO).
- 2026-10-02 : relevé F0_DERIVES en service (premier jour : 160 paires, 6 sans perpétuel, aucune erreur).
