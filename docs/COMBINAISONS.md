# Combiner les briques : profil de volume, flux d'ordres, vote et filtre des signaux Telegram (déclaré le 2026-10-07, avant tout code, téléchargement et exécution)

**Statut : pré-enregistrement.** Écrit le 2026-10-07, avant tout code, tout téléchargement et tout calcul sur des
données réelles. Aucun résultat de ce programme n'existe ; aucun chiffre ci-dessous n'est une mesure de ces briques,
sauf ceux qui sont cités d'études précédentes (avec leur document). Toute modification ultérieure est datée en bas
(« Historique ») et ne peut porter que sur des points techniques, jamais après avoir vu un résultat de l'étape
concernée. Version relue le même jour (relecture `leak-auditor`, décisions du propriétaire), avant tout code.

**Demande du propriétaire (2026-10-07)** : « on doit essayer volume profile et order flow, mais aussi combiner tout ce
qui peut être utile pour augmenter le score ; si une stratégie ne suffit pas, peut-être en combiner 2, 3 ou 4 ». Plan
en 3 étapes validé le même jour. Cette demande rouvre explicitement, pour ce programme seulement, la recherche
directionnelle sur les bougies que le plan du 2026-10-03 avait arrêtée (`PLAN_DE_TRAVAIL.md`).

**Décisions du propriétaire (2026-10-07), inscrites avant tout code :**
- **Étape 3 : « Les deux ».** Voie A : une consultation déclarée de la période réservée pour le filtre de ses signaux
  Telegram de 2026 (2 essais sur FINAL_TEST, consultation n° 4). Voie B : un nouveau test en direct sur les signaux
  de son relais, dans un nouveau module ; F4 et F16 restent intacts.
- **J2 : non.** La pression vendeuse J2 reste mesurée en direct par F11 uniquement ; elle n'entre pas dans ce
  programme.
- **Ordre** : les étapes 1 et 2 tournent d'abord sur DEVELOPMENT. Si l'étape 2 confirme une règle sur les paires C,
  sa lecture finale éventuelle est décidée par le propriétaire **avant** la voie A de l'étape 3, et faite le cas
  échéant avant elle ; sinon, la voie A suit directement (§ 9).

## 0. En bref, pour le propriétaire

- **Étape 1 — cinq briques nouvelles, une par une** : rebond sur le POC du profil de volume de 30 jours, rebond sur le
  bas de la zone de valeur (VAL), reprise du VWAP ancré au dernier creux, divergence haussière de CVD (prix plus bas,
  flux acheteur plus haut), absorption (forte vente au marché sans baisse). Chacune est jouée seule, avec **la même
  transaction pour toutes** (achat au marché, stop à 2 ATR, sorties par tiers à 1, 2 et 3 R, 60 heures au plus),
  contre deux sortes d'achats au hasard : n'importe quand sur la paire, et **dans le même régime** (même année, même
  tendance, même volatilité, même BTC). Le second dit si la brique choisit le bon **moment**, pas seulement le bon
  marché.
- **Étape 2 — les combinaisons** : les 5 briques nouvelles + 4 briques connues (cassure de ligne de tendance 1 h,
  tendance EMA 200, volatilité calme, BTC haussier) votent. Trois seuils de vote (au moins 2, 3 ou 4 votes), un
  modèle simple (régression logistique, réentraîné chaque année sur le passé seulement) et la référence sans filtre.
- **Où l'on juge** : étapes 1 et 2 sur les 40 paires de recherche (2019 → 2025-06), puis **confirmation, en une seule
  fois, sur les 223 paires jamais utilisées pour ces briques**. La période réservée (depuis le 2025-07-01) reste
  fermée pour ces étapes.
- **Étape 3 — filtre de tes signaux Telegram** (tu as choisi les deux voies) : garder seulement les signaux où au
  moins 3 (ou 4) briques sur 5 sont d'accord (tendance, place de l'entrée dans le profil de volume, flux des dernières
  24 h, stop comparé à la volatilité, régime de BTC), jouer ta gestion « stop suiveur », et comparer à des signaux
  tirés au hasard dans le même groupe et le même mois. **Voie A** sur tes exports de 2026 (consultation déclarée
  de la période réservée), **voie B** en direct sur les signaux du relais.
- **Ce qu'il faut attendre, honnêtement** : les idées voisines déjà testées ont donné « rien » (rebond sur support K1,
  supports et résistances contre niveaux placebo, VWAP journalier H, divergences RSI, sweeps). La seule piste
  confirmée sur d'autres paires (lignes de tendance 1 h, +0,12 R sur le hasard) ne s'est pas retrouvée sur la période
  réservée. Combiner des briques sans information ne crée pas d'information ; un vote peut seulement concentrer un
  peu d'information éparse. Le résultat le plus probable est « rien ». Ce protocole est fait pour que, si quelque
  chose existe, on le voie, et que, si rien n'existe, on ne se raconte pas d'histoire.

## 1. Cadre commun aux étapes 1 et 2

### 1.1 Règles

- **DEVELOPMENT seulement** (jusqu'au 2025-06-30 23:59:59 UTC, `research/protocol.py`, `FROZEN_DEVELOPMENT_END`).
  Aucune donnée postérieure n'est lue, ni téléchargée pour ces étapes.
- **Aucun réglage** : tous les paramètres sont fixés dans ce document, repris de `INDICATEURS.md` quand ils y
  existent, sinon choisis ici a priori ; aucun n'est choisi en regardant un résultat. Aucun paramètre ne bouge entre
  les paires de recherche et les paires de confirmation.
- **Liste fermée** : les 9 briques du § 2 sont les seules. Aucune brique n'est ajoutée, retirée, inversée (sens du
  vote) ou redéfinie après un résultat, quel qu'il soit. Une brique « inverse » à l'étape 1 vote quand même dans le
  sens déclaré à l'étape 2 (le modèle logistique, lui, apprend les signes sur le passé).
- **Comptes insuffisants** : si une règle n'atteint pas les minimums écrits (§ 1.7), elle est `INSUFFISANT` ; aucune
  définition, aucun seuil, aucune fenêtre n'est changé pour « avoir assez d'événements ».
- **Tout essai est compté** dans `program_trials` (§ 6). Au 2026-10-07, le registre compte **853 essais** sur
  DEVELOPMENT, **8 essais** sur la période finale et **3 consultations** de la période réservée.
- **Une exécution unique par étape** (§ 9), après la relecture indépendante (`leak-auditor`) du code, les contrôles
  du § 1.8 et une répétition sur données synthétiques. Une seule tâche lourde à la fois, 4 processus au plus.

### 1.2 Données et univers

- **Paires de recherche (R)** : les 40 paires de `RESEARCH_UNIVERSE` (survivantes, biais déclaré : il gonfle le R
  moyen, beaucoup moins l'excès sur des placebos tirés sur la même paire).
- **Paires de confirmation (C)** : les 223 paires passées au moins une fois par le top 40 à date, hors des 40
  (`trendline_confirmation.universe`, recensement de `UNIVERSE_PIT.md`, paires retirées de la cote comprises) ; 214
  ont des bougies 1 h dans la période. Elles n'ont servi à aucune étude de profil de volume, de VWAP ancré, de CVD ni
  d'absorption. Elles ont servi aux lignes de tendance (`LIGNES_DE_TENDANCE.md`), au criblage K à date, aux
  portefeuilles hebdomadaires et à la grille / DCA : **la brique TRENDLINE n'y est pas neuve** (§ 4.6).
- **Bougies** : 1 h du magasin long (`research/long_history.py`, archives officielles, colonnes `open`, `high`, `low`,
  `close`, `base_volume`, `taker_buy_base_volume`, `number_of_trades`, `available_at`) ; exécution sur les bougies
  1 minute du magasin minute (déjà téléchargées jusqu'à la fin de DEVELOPMENT pour les 40 + 214 paires ; couverture
  99,8 à 100 %). **Aucun téléchargement nouveau** n'est nécessaire pour les étapes 1 et 2. Le contrôle de couverture
  des minutes de `trendline_confirmation.coverage` (99 % des heures, fin au plus un jour avant la dernière heure) est
  repris : une paire qui échoue bloque l'exécution (rien n'est compté).
- **Période des déclencheurs** : `figures_history.in_period`, importé tel quel : clôture de la bougie de décision au
  plus tôt le **2019-01-01** et au moins **90 jours** après la première bougie 1 h de la paire, et **80 heures**
  (20 + 60 bougies 1 h) avant la fin de DEVELOPMENT. Pour un achat au marché, les 20 premières bougies (validité d'un
  ordre limite) ne servent pas : la marge est simplement plus prudente que nécessaire, gardée pour réutiliser la
  fonction sans la modifier. BTCUSDT est lue sur la même période pour la brique BTC.

### 1.3 Grille de décision et causalité

- Une décision possible à chaque **clôture 1 h** `t` de chaque paire. **Partout**, une brique n'utilise que les
  bougies dont l'`available_at` précède la décision (clôture + 1 ms + latence) ; jamais `open_time` ni
  `open_time + durée` pour décider de ce qui est connu. BTC est jointe vers le passé sur `available_at`.
- Chaque définition du § 2 dit **à quelle bougie elle est connue**. Un événement « connu à `t` » ne lit aucune bougie
  d'indice supérieur à `t`.
- **Ordre** : posé à la première minute qui suit la clôture de `t` plus 2 secondes de latence
  (`figures_history.order_window`).

### 1.4 Transaction commune (la même pour toutes les briques, combinaisons et placebos)

Chaque brique a sa « façon de jouer » chez les analystes ; ici, **toutes partagent la même transaction**, pour trois
raisons : les votes de l'étape 2 doivent porter sur une même transaction ; un achat au marché est strictement
symétrique avec les placebos (pas d'effet « attendre que le prix revienne à l'ordre limite », pas de correction
maker / taker) ; et aucun paramètre de sortie ne peut être choisi brique par brique.

- **Entrée** : achat **au marché** à l'ouverture de la minute d'exécution (`figures_history.Setup` avec
  `entry=None`, comme `SWEEP` et `RSI_DIV`). L'« entrée » est l'ouverture de cette minute, avant glissement.
- **Stop** : `C_t − 2 × ATR_t` (ATR de Wilder 14 en 1 h à la bougie de décision, `INDICATEURS.md`), posé à la
  détection, fixe. Justification : deux bougies moyennes sous la clôture, hors du bruit d'une heure ; les frais
  aller-retour (≈ 0,21 % en central) y pèsent environ 0,1 R. Fixé a priori, non réglé.
- **Objectifs** : sortie par **tiers** à entrée + 1, 2 et 3 R (R = entrée − stop), posés à l'exécution.
- **Durée** : **60 heures** au plus, comptées en temps depuis l'exécution ; reste vendu à la clôture.
- Minute où le stop et un objectif sont touchés : **stop d'abord** (pas de bougies 1 s en 2019-2025). Minute qui
  ouvre au stop ou dessous avant l'exécution : ordre annulé (propriété reprise de F15). Géométrie invalide (stop à
  moins de 0,1 % sous l'entrée) : écartée, comptée.
- **Frais** : modèle commun (`forward/costs.py`), **central** (7,5 pb par ordre) et **défavorable** (10 pb) ; entrée
  et stop au marché (taker, glissement), objectifs en limite (maker), échéance au marché.
- **R** = résultat net / (entrée − stop) : le risque prévu.
- Paire retirée de la cote avant la fin de l'horizon : reste vendu à la dernière clôture (`COTATION_ARRETEE`, gardée,
  comme dans `LIGNES_DE_TENDANCE.md`).
- Simulation : `figures_history.play` (copie compilée de `f15.simulate`, prouvée identique), importé sans changement.

**Deux ensembles de transactions par règle** :
- **Ensemble « tous les déclencheurs »** : chaque déclencheur est joué comme une transaction indépendante, même s'il
  en chevauche une autre sur la même paire. **C'est sur lui que se mesurent les excès** (décision « excès », § 1.7,
  comparaisons des § 4.4 et 4.6). Le chevauchement rend les transactions dépendantes ; l'intervalle par blocs de
  jours (§ 1.6) en tient compte.
- **Ensemble « une position par paire »** : un déclencheur qui arrive pendant qu'une transaction de la **même règle**
  est ouverte sur la même paire est ignoré (compté « position ouverte »). C'est ce qu'un compte réel pourrait jouer :
  **il sert à la décision « gain »** (R moyen) et au descriptif. Causal : à `t`, on sait seulement si la transaction
  précédente est déjà sortie (sortie ≤ `t`) ; jamais sa date de sortie future (test de mutation, § 1.8).

### 1.5 Placebos et excès

**a) Placebos sur tout l'historique (excès « uniforme »).** Pour chaque transaction exécutée : **20 achats au
marché** sur la même paire à des minutes tirées uniformément, sans remise, sur toute la période utilisable de la paire
(début des déclencheurs de la paire → fin de ses données dans DEVELOPMENT moins 60 heures ; graine déduite de
l'identifiant du déclencheur), mêmes distances de stop et d'objectifs en pourcentage, même sortie par tiers, même
durée, mêmes frais. Minute tirée dans un trou de plus de 10 minutes : placebo inutilisable. Algorithme de
`trendline_confirmation.placebo_minutes` / `with_uniform_placebos`, **réimplémenté dans le module de cette étude avec
son propre identifiant** (`COMBINAISONS`) : la fonction d'origine lit son identifiant `TRENDLINE_CONFIRMATION` dans une
constante du module ; un test vérifie que la réimplémentation, appelée avec cet identifiant, redonne exactement ses
tirages.

**b) Placebos appariés sur le régime (excès « de timing »).** Pour chaque transaction exécutée : 20 achats au marché
sur **la même paire**, **la même année civile**, à des clôtures 1 h où les trois briques d'état ont **les mêmes
valeurs** qu'au déclencheur (`TENDANCE`, `VOL_CALME`, `BTC_HAUSSIER`, la valeur « absente » comptant comme une
valeur). Candidats : toutes les clôtures 1 h de la paire qui satisfont `in_period`, sauf l'heure du déclencheur ;
tirage sans remise (graine déduite de l'identifiant) ; ordre à la première minute après la clôture tirée + 2 s, comme
un déclencheur ; mêmes distances en pourcentage, même sortie, même durée, mêmes frais. Moins de 20 candidats : tous
s'il y en a au moins 5 ; moins de 5 : pas de placebo apparié (transaction exclue de l'excès de timing, comptée).
L'année plutôt que le trimestre : environ 1 000 heures candidates par case (8 cases d'états) contre 250, donc des
placebos assez nombreux même pour les paires récentes.

Les heures candidates **comprennent** l'horizon de la transaction (les 60 heures qui suivent le déclencheur) et les
heures des autres déclencheurs de la paire : rien n'est retiré autour (déclaré ; prudent sous l'hypothèse d'un effet
réel, car des placebos tirés pendant ou juste après un bon moment le partagent en partie et réduisent l'excès). Les
transactions sans placebo apparié sont exclues de l'**excès de timing seulement** : la `PISTE` porte donc sur deux
ensembles légèrement différents (excès uniforme sur toutes les transactions exécutées, excès de timing sur celles qui
ont des placebos appariés) ; les deux nombres sont donnés.

- **Excès uniforme** = R − moyenne des R des placebos a) ; **excès de timing** = R − moyenne des R des placebos b).
  Entrée au marché des deux côtés : aucune correction de frais d'entrée.
- **Part « régime »** = excès uniforme − excès de timing : ce que la règle gagne en achetant dans de bons régimes
  (tendance, BTC, volatilité) plutôt qu'au bon moment dans un régime. **Elle ne se confirme que dans le temps (test en
  direct), jamais sur les paires C** : les régimes de BTC sont les mêmes pour toutes les paires, et les années de
  DEVELOPMENT sont les mêmes sur R et sur C. Elle est décrite, jamais décisive.
- **Réserve déclarée** : les deux sortes de placebos lisent l'historique postérieur à la décision (jusqu'à un an pour
  b, six ans pour a) ; l'excès mesure l'information du moment choisi, pas un gain réalisable. Seule la décision
  « gain » (R moyen) parle d'argent.

### 1.6 Mesure et intervalles

Pour chaque règle et chaque scénario de frais : transactions, R net moyen, excès uniforme et excès de timing, chacun
avec son intervalle.
- **Intervalle qui décide** : tirage par blocs de jours présents (jours avec au moins une exécution ;
  `backtest.metrics.day_block_ci`, 10 000 tirages, graine **20261007**, au moins 10 blocs) : blocs de **28 jours** pour
  les briques seules et `REF_TOUS` ; blocs de **91 jours** pour les règles qui lisent des états (`VOTE_2`, `VOTE_3`,
  `VOTE_4`, `LOGIT`), dont les transactions se suivent pendant des régimes de plusieurs semaines. Sur les paires C
  (§ 4.5), **100 000 tirages** (niveaux plus exigeants, queues mieux estimées).
- **Limite déclarée** : un intervalle par percentiles du bootstrap **couvre moins** que son niveau nominal quand les
  blocs sont peu nombreux (blocs de 91 jours : environ 26 sur 2019 → 2025-06, environ 18 sur la période de `LOGIT`) ;
  les intervalles des règles à états sont donc un peu optimistes, ce que le contrôle nul (§ 1.8) aide à mesurer.
- **Intervalle non calculable** (moins de 10 blocs) : `INSUFFISANT`, jamais une autre longueur de blocs.
- **Contrôle de sensibilité déclaré, non décisif** : tirage croisé jours × paires (blocs de jours et paires tirés
  indépendamment, chaque transaction pesée par le produit des multiplicités de son bloc et de sa paire), même nombre
  de tirages. Si cet intervalle contient 0 alors que celui qui décide ne le contient pas, la décision est inscrite
  avec la mention « fragile au tirage par paires ».
- Niveaux : § 6.

### 1.7 Décisions et garde-fous contre le surajustement

**Deux décisions par règle**, comme `trendline_confirmation.py` (`N_TRIALS = 2`) :

1. **Excès** (sur l'ensemble « tous les déclencheurs ») : `PISTE` si l'intervalle de l'**excès uniforme** ET celui de
   l'**excès de timing** sont entièrement au-dessus de 0, en frais centraux ET défavorables, et si les garde-fous
   ci-dessous tiennent ; `PISTE_FRAGILE` si les intervalles sont au-dessus de 0 mais qu'un garde-fou manque (inscrit,
   non envoyé en confirmation) ; `INVERSE` si l'intervalle de l'excès de timing est entièrement sous 0 dans les deux
   scénarios ; `INSUFFISANT` (§ ci-dessous) ; `RIEN` sinon. Le double critère ne demande pas de correction de plus :
   il est plus sévère que chacun seul.
2. **Gain** (sur l'ensemble « une position par paire ») : `GAIN_DEMONTRE` si l'intervalle du R moyen est entièrement
   au-dessus de 0 en central ET en défavorable ; `PERTE_DEMONTREE` s'il est entièrement sous 0 dans les deux ;
   `GAIN_NON_DEMONTRE` sinon.

**Garde-fous** (exigés pour une `PISTE`, sur l'excès de timing) :
1. **au moins 300 transactions** et 10 blocs ; sinon `INSUFFISANT` (et `INSUFFISANT` aussi pour le gain sous 30
   transactions « une position par paire ») ;
2. **sans le meilleur 1 %** des transactions (les 1 % d'excès les plus grands retirés), l'excès moyen reste > 0 ;
3. **concentration** : la paire qui apporte le plus ne pèse pas plus de **25 %** de la somme des excès, l'année qui
   apporte le plus pas plus de **50 %** ;
4. **régularité** : excès moyen > 0 dans au moins **4 des 7 années** (2019 à 2025, 2025 réduite au premier semestre) ;
   pour le modèle logistique, dans au moins **3 des 5 plis** ;
5. **frais défavorables** : décision prise dans les deux scénarios (déjà dans le critère) ;
6. pour le modèle logistique, **au moins 100 transactions dans chaque pli compté**, et au moins 4 plis sur 5 qui
   atteignent ce nombre (leçon de `ML_INTRADAY.md` : un pli à 14 transactions ne compte pas) ; sinon `INSUFFISANT`.

### 1.8 Contrôles avant toute exécution sur données réelles

- **Causalité, brique par brique** : recalcul sur données tronquées à des coupures tirées au hasard (et juste après
  la confirmation d'un pivot, juste après 00:00 UTC pour le profil) et avec le futur falsifié (prix, volumes, nombre de
  transactions) : événements et états connus avant la coupure identiques. **Tests de mutation** qui doivent être
  détectés : profil du jour `d` qui inclut une bougie du jour `d` ; divergence CVD datée à `j2` au lieu de `j2 + 2` ;
  part de centrage du `delta` ou centile d'absorption qui inclut la bougie `t` (absence de `shift(1)`) ; VWAP ancré
  sur un pivot pas encore confirmé ; reprise du VWAP comptée avant que l'ancre soit connue ; EMA de BTC jointe sur
  `open_time` ; **règle « une position par paire » qui lit la date de sortie d'une transaction encore ouverte**.
- **Contrôle sous l'hypothèse nulle** (données synthétiques seulement, même code de bout en bout, au moins 120 marches
  de 3 ans par cas) : marches aléatoires sans mémoire, avec une part d'achats « taker » tirée indépendamment des
  rendements, dans les quatre cas de `LIGNES_DE_TENDANCE.md` (volatilité constante, régimes de volatilité, tendances
  par régimes de 30 jours, hausse puis chute), plus le cas **« facteur commun »** : chaque paire = β × BTC + bruit
  propre (β tiré dans [0,5 ; 1,5]), BTC en régimes de dérive de ±0,4 %/jour.
  - Cas valides par type de règle : pour l'**excès de timing**, tous les cas, pour toutes les règles. Pour l'**excès
    uniforme**, les règles qui lisent des états (`VOTE_k`, `LOGIT`) ne sont pas jugées dans les cas à dérive
    persistante (tendances par régimes, hausse puis chute, facteur commun) : elles y gagnent « légitimement » en
    choisissant les régimes haussiers ; leur excès uniforme y est seulement comparé à l'excès théorique de la règle
    d'état seule, décrit.
  - **Critère** pour chaque règle et chaque cas valide : `|z| < 3` ET `|biais| ≤ 0,02 R` (biais = excès moyen
    mesuré).
  - **En cas d'échec**, règle écrite d'avance, **par type d'excès** : l'exécution a lieu, mais pour cette règle la
    `PISTE` (et la `CONFIRMEE`) exige que la borne basse de l'excès uniforme dépasse son **biais uniforme maximal**, et
    que celle de l'excès de timing dépasse son **biais de timing maximal**, avec **biais maximal = max(0, plus grand
    biais mesuré sur les cas valides de cette règle)** (même logique que `LIGNES_DE_TENDANCE.md`). Les biais sont
    inscrits ici avant l'exécution.
  - **Test « facteur commun »**, deux témoins non comptés :
    - **témoin 1, apparié** : achat à chaque clôture où `BTC_HAUSSIER` est vrai. Il doit y donner un excès uniforme
      > 0 (borne basse > 0), sinon le cas synthétique ne contient pas d'effet de régime et l'exécution n'a pas lieu tant
      que le simulateur n'est pas corrigé. Son excès de timing est ≈ 0 par construction : **non concluant**, puisque le
      témoin est apparié sur son propre état ;
    - **témoin 2, non apparié** : achat à chaque clôture où le **rendement de BTC sur les 7 derniers jours** (168
      clôtures 1 h, connues à `t`) est > 0. Choisi plutôt que l'indicateur de régime caché du simulateur, parce qu'il
      est observable et que ce genre d'état corrélé mais non apparié existe dans les vraies données. Son excès de
      timing mesure la **fuite de régime résiduelle** que les trois états appariés ne retirent pas ; il est inscrit
      ici avant l'exécution et compte parmi les biais de timing des règles à états (`VOTE_k`, `LOGIT`) pour la règle de
      repli ci-dessus.
- **Contrôles positifs** (informatifs, non bloquants) : effet injecté de +0,15 R en moyenne sur les 60 heures qui
  suivent (1) une divergence CVD synthétique, (2) un vote à 3 au moins : part des simulations où la règle sort
  `PISTE` ; donne la puissance réelle de l'instrument.
- **Comptages avant l'étude**, inscrits ici avant l'exécution, sans aucune exécution simulée ni aucun R : nombre de
  déclencheurs par brique et par vote, **par année et par combinaison d'états** ; nombre d'heures candidates aux
  placebos appariés par paire, année et case d'états ; part des déclencheurs sans placebo apparié ; nombre
  d'événements `CVD_DIV` et part des heures où `FLUX` voterait oui, **par année** (contrôle du centrage, § 2 :
  **descriptif seulement**, la définition de `deltaC` ne change plus quoi que montrent ces comptages).

## 2. Les briques (définitions exactes et causales)

Notations de `INDICATEURS.md` : bougies 1 h clôturées `i`, `O, H, L, C, V` (volume en devise de base), `TB` = volume
de base acheté au marché, `N` = nombre de transactions (`number_of_trades`). « Événement à `t` » = connu à la clôture
de `t`. Une brique non calculable (pas assez d'historique, trou) ne vote pas (ni oui ni non : elle est absente), et
le nombre de cas est donné. Comparaisons : celles écrites (« ≤ », « ≥ » inclusives ; « < », « > » strictes).
Centiles et médianes : `numpy.quantile` / `numpy.median` sur les valeurs valides, méthode par défaut (« linear »).

**Delta centré (correction de la relecture).** La part acheteuse de Binance est structurellement un peu sous 0,5 et
varie selon les années (mélange maker / taker) ; le `delta` brut du § 7 (`2 · TB − V`) dérive donc, et une somme de
`delta` > 0 dépendrait de l'époque. Ici : **`s̃_i = Σ TB_j / Σ V_j`** pour `j = i − 720` à `i − 1` (part acheteuse
**pondérée par le volume** des 720 bougies précédentes, décalée d'une bougie : la bougie `i` n'y entre pas) ; au
moins **684 bougies valides** (présentes, `V_j > 0`) dans la fenêtre, sinon `s̃_i` n'existe pas et les briques qui en
dépendent sont **absentes** à `i` ; **`deltaC_i = TB_i − s̃_i · V_i`** (achats au marché au-delà de leur part
habituelle). Pondérée par le volume, la somme des `deltaC` sur la fenêtre de référence elle-même vaut 0. `CVD_DIV` et
`FLUX` utilisent `deltaC`. Écart à `INDICATEURS.md` § 7 déclaré ici, avant tout résultat ; **plus aucun choix** sur
cette définition après les comptages du § 1.8.

### 2.1 Briques nouvelles (étape 1), toutes de type « événement »

| Brique | Définition | Connue à |
|---|---|---|
| `VP_POC` | **Profil du jour** `P_d` (§ 8 de `INDICATEURS.md`, `patterns.volume.volume_profile`, 50 tranches, zone de valeur 70 %) calculé à 00:00 UTC du jour `d` sur les 720 bougies 1 h ouvertes dans `[d − 30 j ; d)`, au moins 684 présentes et toutes disponibles avant la première décision du jour `d` ; il sert aux bougies ouvertes le jour `d`. Rebond : `C_{t−1} > haut_POC`, `L_t ≤ haut_POC` et `C_t > haut_POC` (`haut_POC` = borne haute de la tranche du POC). **Un seul événement par paire et par jour.** | `t` |
| `VP_VAL` | Même profil `P_d`. Rebond sur le bas de la zone de valeur : `C_{t−1} > VAL`, `L_t ≤ VAL`, `C_t > VAL`. Un seul événement par paire et par jour. | `t` |
| `AVWAP_RECLAIM` | Ancre `a` = dernier **pivot bas ZigZag** 1 h (m = 3,0 ATR, § 1), d'indice `a` et connu à la bougie `k_a` ; VWAP ancré depuis `a` (§ 8, prix typique `(H + L + C)/3`, `patterns.volume.anchored_vwap`). Reprise : **première** bougie `t` telle que `t − 1 ≥ k_a`, `C_{t−1} ≤ AVWAP_{t−1}` et `C_t > AVWAP_t`, l'ancre `a` étant encore la dernière connue à `t`. Une reprise qui aurait eu lieu avant `k_a` n'existe pas (l'ancre n'était pas connue) et ne consomme pas l'ancre. **Un seul événement par ancre.** | `t` |
| `CVD_DIV` | Deux pivots bas **fractals** consécutifs (k = 2) `j1 < j2`, `5 ≤ j2 − j1 ≤ 60`, `L_{j2} < L_{j1}` et `Σ deltaC_i` pour `i` de `j1 + 1` à `j2` **> 0** (flux acheteur centré plus fort entre les deux creux : la divergence RSI du § 10.4 avec le CVD centré à la place du RSI). | `j2 + 2` |
| `ABSORPTION` | À `t` : `N_t ≥ 100` transactions ; **référence** = les bougies valides parmi les 720 précédentes (`t − 720` à `t − 1`), une bougie valide ayant `N_j ≥ 100` et `V_j > 0` ; au moins **360 bougies valides**, sinon la brique est absente à `t` ; part acheteuse `TB_t / V_t` **≤ 10e centile** des parts des bougies de référence ; `V_t ≥` médiane des `V` des bougies de référence (« forte » vente au marché) ; `H_t > L_t` et `(C_t − L_t) / (H_t − L_t) ≥ 2/3` (clôture dans le tiers haut : pas de baisse). | `t` |

Remarques déclarées :
- le profil est recalculé une fois par jour (et non à chaque heure) : c'est le « profil de 30 jours » demandé, et le
  calcul reste faisable ; la bougie ouverte à 23:00 du jour `d` utilise encore `P_d` ;
- `ABSORPTION` : 10 % et la médiane sont des seuils a priori (le 10e centile est celui de J2 et de F11, sur 30 jours
  d'heures). **100 transactions au moins** : sous ce nombre, la part acheteuse d'une heure dépend d'un ou deux gros
  ordres (bruit de cotation sur les paires peu actives, surtout en 2019) ; seuil fixé a priori, appliqué aussi à la
  référence pour comparer la bougie à des heures de même nature ; 360 sur 720 (la moitié) : une paire calme la nuit
  garde une référence, une paire presque sans échanges n'en a pas. **Part en devise de
  base** (`TB / V`, comme le CVD du § 7) : sur une heure, le prix varie peu et la part en devise de cotation
  (`TBQ / QV`, celle de J2 et de F11) en diffère très peu ; on garde la même devise que le reste du flux de ce
  document ;
- les idées voisines déjà mesurées : `K1` (rebond sur support confirmé, négatif), `NIVEAUX.md` (rejet / cassure, pile
  ou face), `H` (reprise du VWAP journalier, rien), `RSI_DIV` (perd seule). Elles ne sont pas rejouées.

### 2.2 Briques connues (entrent à l'étape 2 et à l'étape 3)

| Brique | Type | Définition | Connue à |
|---|---|---|---|
| `TRENDLINE` | événement | Cassure haussière d'une ligne de tendance descendante, 1 h, détecteur de F15 (`f15.detect_frame`, § 9.3 d'`INDICATEURS.md`), datée à sa bougie de détection, exactement comme dans `LIGNES_DE_TENDANCE.md`. Seule la date sert : ses règles de transaction propres (retest, objectifs en hauteur) ne sont pas utilisées ici. **Choisie après avoir vu les 40 paires sur 2019-2025** : ses résultats sur R ne prouvent rien, seul C compte pour elle (et C l'a déjà vue, § 4.6). | bougie de détection |
| `TENDANCE` | état | `C_t > EMA200_t` (EMA des clôtures 1 h de la paire, § 10.4, départ par la moyenne simple des 200 premières). | `t` |
| `VOL_CALME` | état | Variance réalisée horaire moyenne des 24 dernières heures ≤ celle des 720 dernières heures (rendements log horaires, fenêtres couvertes à 95 % au moins, définitions de `VOLATILITY.md` § 3 et § 16). Sens choisi **a priori, sans preuve** : ne pas acheter en pleine flambée de volatilité (krachs, liquidations). | `t` |
| `BTC_HAUSSIER` | état | Clôture de BTCUSDT du dernier **jour UTC complet** (bougies 1 h agrégées, jour complet seulement) > EMA50 de ces clôtures journalières ; connue à l'`available_at` de la bougie de 23:00, jointe vers le passé. | `t` |

### 2.3 Écartées (déclaré maintenant)

- **Pression vendeuse J2** : **non** (décision du propriétaire du 2026-10-07, conforme à `PROTOCOL.md`). Elle reste
  mesurée en direct par F11 uniquement ; aucune option ne la réintroduit dans ce programme.
- **Marché à terme** (financement, intérêt ouvert) : écarté. Son historique ne couvre pas la période (financement
  depuis 2021-04, intérêt ouvert et comptes depuis 2022-03 environ, téléchargé pour 16 paires seulement ; beaucoup de
  paires C n'ont pas de perpétuel, ou l'ont eu tard). Une brique absente la moitié du temps rendrait le vote
  dépendant de la disponibilité des données. Aucun essai.
- **Prévision de volatilité HAR + profil** (la seule confirmée) : non utilisée aux étapes 1 et 2 (le stop est déjà
  proportionnel à l'ATR) ; l'étape 3 utilise une mesure simple (§ 5.3).
- Toute autre idée (OB, FVG, sweep, harmoniques, niveaux, sessions, etc.) : hors programme. Liste fermée.

## 3. Étape 1 — les briques nouvelles, une par une

**Question.** Chacune des 5 briques du § 2.1, jouée avec la transaction commune, fait-elle mieux qu'un achat au hasard
de même géométrie sur la même paire, et au bon moment dans un même régime ?

- **Recherche (R)** : les 5 briques sur les 40 paires. 5 règles × 2 décisions = **10 essais**, niveau
  **1 − 0,05/10** (99,5 %). Décisions du § 1.7.
- Les briques `PISTE` sur R sont confirmées sur C **avec celles de l'étape 2, en une seule exécution** (§ 4.5).
- **Descriptif, hors décision** : par année ; part « régime » ; issues (stop, TP1, TP2, TP3, échéance) contre les
  placebos (biaisées sous les régimes de volatilité, pour mémoire) ; transactions ignorées « position ouverte » ;
  paire et année qui apportent le plus et résultat sans elles ; intervalle croisé jours × paires.
- **Quoi qu'il arrive** (rien, inverse, piste) : les 5 briques entrent à l'étape 2 telles quelles.

## 4. Étape 2 — les combinaisons

### 4.1 Déclencheurs et votes

- **Déclencheur** : à la clôture `t`, au moins une brique « événement » (5 nouvelles + `TRENDLINE`) se produit. Deux
  événements à la même heure sur la même paire = un seul déclencheur.
- **Votes à `t`** : chaque brique événement vote « oui » si elle s'est produite dans les **24 dernières heures** (de
  `t − 23` à `t`, la brique qui déclenche comprise) ; chaque brique état vote « oui » si elle est vraie à `t`.
  9 votants au plus. Une brique absente (§ 2) ne vote pas.
- Les votes ont tous le même poids. Le sens de chaque vote est celui du § 2, jamais retourné.

### 4.2 Les 5 règles

| Règle | Achat au déclencheur si… |
|---|---|
| `REF_TOUS` (référence) | toujours (aucun filtre : tous les déclencheurs) |
| `VOTE_2` | au moins 2 votes |
| `VOTE_3` | au moins 3 votes |
| `VOTE_4` | au moins 4 votes |
| `LOGIT` | le modèle logistique (§ 4.3) prévoit une probabilité de gain au-dessus du taux de base de son entraînement |

Toutes jouent la transaction commune (§ 1.4). Les déclencheurs de `VOTE_4` sont inclus dans ceux de `VOTE_3`, eux-mêmes
dans ceux de `VOTE_2`, eux-mêmes dans ceux de `REF_TOUS` ; ceux de `LOGIT` aussi sont inclus dans `REF_TOUS`
(ensembles « tous les déclencheurs »).

### 4.3 Le modèle logistique

- **Lignes** : tous les déclencheurs du § 4.1 (ensemble « tous les déclencheurs »), chacun avec sa transaction commune
  simulée. **Cible** : `y = 1` si le R net en frais centraux est > 0.
- **Variables** : les 9 votes (0 ou 1), et rien d'autre ; une brique absente vaut 0. Pour chaque pli, si une brique
  est absente sur plus de 1 % des lignes **de l'entraînement de ce pli**, une variable « absente » (0 / 1) est ajoutée
  pour elle dans ce pli (même règle appliquée aux lignes de test du pli).
- **Modèle** : régression logistique L2, `C = 1,0`, solveur `lbfgs`, 1 000 itérations au plus (`scikit-learn`, valeurs
  par défaut), sans interaction, sans recherche d'hyperparamètres.
- **Walk-forward annuel** : réajustement le **1er janvier** de chaque année `Y`, fenêtre croissante, sur les lignes
  dont la **sortie** est antérieure au 1er janvier de `Y` (purge : aucune issue d'entraînement ne chevauche le test ;
  les votes « actifs dans les 24 h » d'une ligne de test ne lisent que des événements connus avant elle, jamais une
  issue). Plis de test : **2021, 2022, 2023, 2024, 2025 (premier semestre)** = 5 plis ; premier entraînement sur
  2019-2020. Au moins 2 000 lignes et les deux classes, sinon le pli n'a pas de modèle (compté en échec, jamais
  exclu).
- **Décision** : acheter si `p̂ >` taux de lignes gagnantes de l'entraînement (un seul seuil, fixé ainsi, sans
  optimisation).
- **`TRENDLINE` dans le modèle** : choisie après avoir vu R sur 2019-2025 ; sur R, `LOGIT` (comme les votes) est donc
  en partie contaminé par elle ; seul C compte (§ 4.6).
- **Sur les paires C** : le modèle du pli `Y` est celui ajusté sur les paires **R** (lignes sorties avant le
  1er janvier de `Y`), appliqué tel quel aux déclencheurs des paires C de l'année `Y` : hors échantillon à la fois
  dans le temps et dans les paires.
- Descriptif : coefficients par pli (signe et stabilité), AUC et score de Brier contre le taux de base, part des
  déclencheurs retenus.

### 4.4 Décision sur les paires de recherche

- 5 règles × 2 décisions = **10 essais**, niveau **1 − 0,05/10** (99,5 %). Décisions et garde-fous du § 1.7.
- Les votes sont jugés sur 2019 → 2025-06 ; `LOGIT` sur ses 5 plis (2021 → 2025-06). Descriptif : les votes sur
  2021 → 2025-06, pour comparer à `LOGIT` sur la même période.
- **Le filtre doit apporter quelque chose** (critère 7 de `PROTOCOL.md`) : une règle `VOTE_k` ou `LOGIT` n'est une
  `PISTE` que si, en plus, la **différence d'excès de timing** « règle − `REF_TOUS` » (moyenne sur les déclencheurs
  gardés moins moyenne sur tous les déclencheurs, ensembles emboîtés, intervalle par le même tirage de blocs appliqué
  aux deux ensembles à la fois) a une **borne basse > 0 au même niveau** (1 − 0,05/10), en central et en défavorable.
  Sinon, ce qui marche est le déclencheur, pas le vote. Condition de plus dans la même décision : pas d'essai de plus.
  - Les blocs d'une différence ont la longueur de la règle comparée (**91 jours**).
  - Pour `LOGIT`, `REF_TOUS` est **restreint aux mêmes années** (2021 → 2025-06), sur R comme sur C.
  - Différence non calculable (moins de 300 transactions de la règle, ou moins de 10 blocs) : `INSUFFISANT`.
- **À prévoir, déclaré** : comme les placebos appariés ont les mêmes états que le déclencheur, l'excès de timing d'une
  règle à états ne peut venir que de l'**interaction événement × régime** (un événement plus informatif dans certains
  régimes que dans d'autres) ; le simple fait d'acheter dans de bons régimes n'y compte pas. `VOTE_k` passera donc
  rarement la différence avec `REF_TOUS`, même si la part « régime » est grande.

### 4.5 Confirmation sur les paires jamais utilisées (étapes 1 et 2 ensemble)

- Après l'étape 2 sur R, toutes les règles `PISTE` sur R — `m₁` briques de l'étape 1 et `m₂` règles de l'étape 2 —
  sont rejouées **sans aucun changement** sur les paires C, **en une seule exécution**.
- **Un seul Bonferroni** : 2 décisions par règle, `2 · (m₁ + m₂)` essais, niveau **1 − 0,05 / (2 · (m₁ + m₂))** pour
  chaque intervalle, **100 000 tirages** (§ 1.6).
- Décision « excès » : `CONFIRMEE` si les intervalles de l'excès uniforme ET de l'excès de timing sont entièrement
  au-dessus de 0 en central et en défavorable, avec les garde-fous 1, 2, **4** (excès de timing > 0 au moins 4 années
  sur 7) et 5 du § 1.7 (concentration décrite) ; pour une règle de l'étape 2, la différence avec `REF_TOUS` du § 4.4
  au niveau de C ; et le § 4.6. `INVERSE` si l'intervalle de l'excès de timing est sous 0 ; `NON_CONFIRMEE` sinon
  (non démontré, pas une réfutation) ; `INSUFFISANT` selon le § 1.7. Décision « gain » comme au § 1.7.
- Aucune règle `PISTE` sur R : pas d'exécution sur C, rien de compté.
- Descriptif : paires cotées contre retirées ; mois où la paire est dans le top 40 ; part « régime » (non
  confirmable ici, § 1.5).

### 4.6 TRENDLINE n'est neuve nulle part

`TRENDLINE` a été remarquée sur les 40 paires (après coup) puis mesurée sur les paires C (+0,12 R sur le hasard) et
sur la période réservée (non confirmée). Une combinaison qui la contient ne peut donc pas être « confirmée » par
elle. Condition de `CONFIRMEE` sur C pour toute règle de l'étape 2 : le sous-ensemble emboîté de ses transactions
**où `TRENDLINE` ne vote pas** a un excès de timing dont la **borne basse est > 0 au niveau de C**
(1 − 0,05 / (2 · (m₁ + m₂))), en central et en défavorable. Sinon : `NON_CONFIRMEE` (« la confirmation viendrait de
la ligne de tendance déjà connue »).

## 5. Étape 3 — filtrer les signaux Telegram du propriétaire

### 5.1 Question

Garder seulement les signaux de tes groupes où les briques sont d'accord améliore-t-il le **R par signal** (ta
gestion « stop suiveur ») par rapport à garder le même nombre de signaux au hasard, dans le même groupe et le même
mois ? Deux voies, décidées par le propriétaire : **A** sur les exports de 2026 (§ 5.2 à 5.8), **B** en direct
(§ 5.9). Mêmes briques, mêmes filtres, même gestion, même mesure.

### 5.2 Signaux (voie A)

- **Source** : les trois exports Telegram Desktop (`imports/telegram/ChatExport_2026-10-02 (3|4|5)/result.json` :
  AL-MAHWASHI CRYPTO, IN CRYPTO, LEGEND TRADING), lus par `external.audit.read_telegram_export`, **texte seulement**
  (pas d'images), même parseur (empreinte inscrite), mêmes doublons par groupe (7 jours). Les fichiers du robot
  (BotHistory, mahwashiVip, IncryptoVip) ne sont pas pris : un mois seulement, en double avec les exports, sans les
  numéros de messages qui montrent les suppressions.
- **Refus** (déjà mort, déjà joué, périmé, sans bougies) : mêmes règles que `audit.measure`, mais **refaits dans le
  module de cette étude sur `available_at`** : la bougie de référence est la dernière bougie dont l'`available_at`
  est ≤ l'heure de réception. `audit.py` (ligne 327) prend `open_time + STEP ≤ réception`, sans la latence ; il n'est
  pas modifié.
- **Doublons, dans cet ordre** : (1) d'abord les doublons **par groupe** sur 7 jours (règle d'`audit.py`) ; (2) puis,
  parmi les signaux restants, les doublons **entre groupes** : plusieurs signaux de groupes différents sur la **même
  paire le même jour UTC** de réception, seul le premier reçu est gardé (les autres sont exclus et comptés), pour
  qu'un même mouvement de marché ne compte pas plusieurs fois.
- **Coupure fixée maintenant : 2026-10-07 00:00 UTC.** Un signal n'est mesuré que si
  `réception + 24 h + 30 jours ≤ coupure` (réception au plus tard le 2026-09-06 00:00 UTC), **par la date, jamais par
  l'étiquette « provisoire »** (`audit.measure` la met toujours à `False` pour le stop suiveur). Les signaux plus
  récents sont exclus et comptés.
- Un signal dont une brique n'est pas calculable (paire cotée depuis moins de 30 jours, trou) est exclu et compté.
- **Compté le 2026-10-07 sans aucun prix ni résultat** : 732 messages lisibles comme signaux, tous de **2026**
  (2 en janvier, 1 en mars, puis 151 en avril, 124 en mai, 83 en juin, 68 en juillet, 150 en août, 138 en septembre,
  15 en octobre), 30 noms de groupes (les plus fréquents : ALMAHWASHI CRYPTO 151, SUHAIB ALMASHHADANI 83, LEGEND
  TRADING 78, HAMZAWY 77, ABOYASEEIN 73, ALAFIFY 58). Après la coupure, les doublons et les refus, attendu : **300 à
  450 signaux mesurables**.

### 5.3 Les 5 briques au moment de la réception

Toutes calculées sur les bougies 1 h dont l'`available_at` est ≤ l'heure de réception (`date_unixtime`) :

| Brique | Vote « oui » si… |
|---|---|
| `TENDANCE` | `C > EMA200` en 1 h de la paire (dernière bougie disponible), comme au § 2.2 |
| `PROFIL` | l'entrée 1 du signal est **≥ VAL** du **dernier profil disponible à la réception** (§ 2.1) : `P_d` seulement si toutes ses bougies (la dernière est celle de 23:00 du jour `d − 1`) ont un `available_at` ≤ réception, sinon `P_{d−1}` ; on n'achète pas sous la zone de valeur |
| `FLUX` | somme des `deltaC` (§ 2) des 24 dernières bougies 1 h disponibles > 0 (acheteurs au marché au-delà de leur part habituelle sur la journée) |
| `STOP_VOL` | distance du stop `ln(entrée 1 / stop)` **≥ σ24**, avec `σ24 = √(24 × moyenne des rendements log horaires au carré des 168 dernières heures)` : le stop est au-delà du mouvement ordinaire d'une journée. Mesure simple (composante hebdomadaire du HAR), pas la prévision HAR + profil confirmée, déclaré |
| `BTC_HAUSSIER` | comme au § 2.2 |

### 5.4 Filtres (2 essais)

- `FILTRE_3` : garder le signal si **au moins 3 briques sur 5** votent oui.
- `FILTRE_4` : garder le signal si **au moins 4 briques sur 5** votent oui.

### 5.5 Gestion et mesure

- **Ta gestion par défaut** (`external/trailing.py`, `[external] management = "stop_suiveur"`) : ordre limite à
  l'entrée 1 valable 24 h ; ventes aux 5 premiers objectifs (33 / 27 / 20 / 13 / 7 %) ; TP1 → stop à l'entrée 1, TP3
  → TP1, TPk → TP(k−2) ; stop remonté appliqué à la bougie suivante ; 30 jours au plus ; bougies 15 min ouvertes
  après la réception ; frais centraux et défavorables.
- **R par signal** = résultat net / (entrée 1 − stop du signal) ; un signal **jamais rempli vaut 0 R** (le filtre
  décide avant de savoir s'il sera rempli : aucune sélection sur l'avenir).
- **Gain du filtre** = R moyen des signaux gardés − R moyen de tous les signaux de la même partie.

### 5.6 Placebo, partie tenue à l'écart et décision

- **Partie d'étude / partie tenue à l'écart, fixées maintenant** : pour chaque groupe, signaux mesurables triés par
  heure de réception ; les **30 % les plus récents** (arrondi vers le bas) forment la partie tenue à l'écart ; un
  groupe de moins de 4 signaux va entièrement dans la partie d'étude.
- **Cases de stratification** : **groupe × mois civil** (UTC, mois de la réception), dans chaque partie. Le mois
  plutôt que la semaine ISO : avec 300 à 450 signaux répartis sur une trentaine de groupes, des cases d'une semaine ne
  contiendraient presque toujours qu'un signal. Une case est **utile**, pour un filtre donné, si elle contient **au
  moins 2 signaux** et si le filtre y garde **au moins 1 signal sans les garder tous** ; les autres cases n'apportent
  aucune variation au placebo et **sortent du test** (décrites seulement).
- **Placebo** : 10 000 tirages ; dans chaque case utile, on tire au hasard le même nombre de signaux que le filtre y
  garde (un filtre ne peut gagner ni en choisissant le meilleur groupe, ni en choisissant les bons mois du marché).
  Statistique : R moyen des signaux gardés **dans les cases utiles** ; p = (1 + nombre de tirages dont le R moyen est
  ≥ celui du filtre) / (1 + 10 000). Graine 20261007.
- **Comptages avant tout R**, inscrits dans le rapport avant le calcul des résultats : votes « oui » par brique, signaux
  gardés par filtre, par partie et par groupe, nombre de cases utiles et de signaux dans ces cases.
- **Minimums, comptés dans les cases utiles seulement** : moins de **100 signaux en cases utiles** dans la partie
  d'étude, ou moins de **40** dans la partie tenue à l'écart : `INSUFFISANT` pour cette partie.
- **Décision** (une par filtre ; **2 essais**) :
  - partie d'étude : piste si p ≤ **0,05/2**, avec au moins 100 signaux en cases utiles, dont au moins 30 gardés sur
    au moins 10 jours ;
  - partie tenue à l'écart, seulement pour un filtre en piste : `FILTRE_CONFIRME` si p ≤ **0,05** (unilatéral, le
    sens étant fixé par la partie d'étude), avec au moins 40 signaux en cases utiles dont au moins 20 gardés ;
    `INSUFFISANT` en dessous ; `NON_CONFIRME` sinon ;
  - `FILTRE_NEFASTE` (descriptif) si p ≥ 0,975 dans la partie d'étude : le filtre écarte plutôt les bons signaux ;
  - `RIEN` sinon.
- **Le gain en argent est descriptif, sans verdict** : R moyen des signaux gardés avec ses intervalles (blocs de
  7 jours de réception, 10 000 tirages) à 90 % et 95 %, central et défavorable. Même un `FILTRE_CONFIRME` ne rend pas
  les signaux rentables.
- Descriptif : chaque brique seule (sans décision) ; part de signaux gardés par groupe ; taux de remplissage gardés
  contre écartés ; résultats en frais défavorables ; doublons entre groupes.

### 5.7 Puissance de l'étape 3 (réaliste)

Hypothèse non mesurée : écart-type du R par signal de l'ordre de 1 R (pertes vers −1 R, gains jusqu'à +2 à +3 R).
Environ 210 à 315 signaux mesurables dans la partie d'étude et 90 à 135 dans la partie tenue à l'écart ; en supposant
que 60 à 80 % d'entre eux tombent dans des cases utiles, cela donne environ **130 à 250** signaux utiles dans la partie
d'étude et **55 à 110** dans la partie tenue à l'écart. Avec un filtre qui en garde environ 40 %, l'erreur type du gain
du filtre vaut environ 0,08 à 0,11 R dans la partie d'étude et 0,12 à 0,17 R dans la partie tenue à l'écart. Seul un
gain d'au moins **0,25 à 0,3 R par signal** a de bonnes chances (environ 80 %) d'être vu dans la partie d'étude, et
d'au moins **0,3 à 0,4 R** dans la partie tenue à l'écart. La partie tenue à l'écart peut aussi tomber sous 40
signaux utiles (`INSUFFISANT`), surtout pour `FILTRE_4`. Les signaux d'un même jour réduisent encore la puissance.
Un `RIEN` voudra dire « pas d'effet de cette taille », pas « aucun effet ».

### 5.8 Voie A : consultation déclarée de la période réservée

Tous les signaux exportés datent de 2026, dans la période réservée. Décision du propriétaire du 2026-10-07 : la voie A
est une **consultation déclarée** (registre `final_test_consultations`, `--i-understand-final-test`), **consultation
n° 4** (numérotée n° 5 si une lecture finale d'une règle de l'étape 2 a lieu avant, § 9), restreinte aux instants des
signaux Telegram, **2 essais sur FINAL_TEST**. Ensuite, la période réservée ne peut plus juger neutrement les briques
de l'étape 3 (`TENDANCE`, profil, flux, volatilité, `BTC_HAUSSIER`). Téléchargement nécessaire (après la relecture
du code) : bougies 1 h (avec volume taker et nombre de transactions) et 15 min des paires des signaux, **à partir du
2025-11-01** (pour que l'EMA200 et les fenêtres de 744 heures, 720 de référence plus 24 de flux, soient pleines dès
janvier 2026), jusqu'à la coupure du 2026-10-07 00:00 UTC. Ordre interne : contrôle de complétude des bougies → consultation inscrite → briques et
comptages (§ 5.6) → résultats → rapport. Panne après l'inscription : règle de `LIGNES_DE_TENDANCE.md` (une seule
reprise si aucun chiffre n'a été produit).

### 5.9 Voie B : test en direct sur les signaux du relais

Décision du propriétaire du 2026-10-07. Le même filtre (§ 5.3 à 5.6 : mêmes briques calculées en direct sur les
bougies disponibles à la réception, mêmes seuils, même gestion, même R, mêmes doublons dans le même ordre, même
placebo par cases **groupe × mois civil**, mêmes cases utiles, même minimum de 100 signaux en cases utiles, repris
tels quels dans sa pré-inscription) est mesuré sur les signaux texte que le relais Telegram dépose depuis le 2026-10-06, **reçus
après le démarrage du test**. **Nouveau module** et nouvelle pré-inscription (numéro de test attribué à ce moment) ;
F4 et F16 restent intacts (codes et sections gelés, non modifiés) ; le nouveau module lit le dépôt du relais sans y
écrire. Pas de partie tenue à l'écart : toutes les données sont nouvelles ; décision au niveau 0,05/2 pour les deux
filtres. Verdict quand 300 signaux mesurables sont résolus, ou à une date fixée dans sa pré-inscription si ce nombre
n'est pas atteint (`INSUFFISANT` sinon). Ses essais sont comptés par ce test, pas sur DEVELOPMENT ni FINAL_TEST. Elle
ne dépend pas des étapes 1 et 2 et peut démarrer dès sa pré-inscription relue.

## 6. Essais et comparaisons multiples

Deux décisions par règle (excès, gain), comme `trendline_confirmation.py` ; chaque exécution enregistre `n_trials`
en conséquence.

| Étape | Données | Règles | Essais (`n_trials`) | Niveau de chaque intervalle |
|---|---|---|---|---|
| 1 — briques seules | paires R, DEVELOPMENT | 5 | 10 | 1 − 0,05/10 |
| 2 — combinaisons | paires R, DEVELOPMENT | 5 | 10 | 1 − 0,05/10 |
| Confirmation des étapes 1 et 2, en une fois | paires C, DEVELOPMENT | m₁ + m₂ (0 à 10) | 2 · (m₁ + m₂) (0 à 20) | 1 − 0,05 / (2 · (m₁ + m₂)) |
| 3 — voie A | signaux de 2026, période réservée | 2 filtres | 2 (FINAL_TEST) | p ≤ 0,05/2, puis p ≤ 0,05 sur la partie tenue à l'écart |
| 3 — voie B | signaux du relais, en direct | 2 filtres | comptés par le test en direct | p ≤ 0,05/2 |

- **Point de départ vérifié** : le total actuel de 8 essais sur FINAL_TEST est juste. Il se décompose en 5 pour la
  volatilité (`VOLC-20261003T101841Z-4d3ffb`), 1 pour les lignes de tendance (`TRNF-20261004T234307Z-c15d40`) et
  1 + 1 pour l'IA locale (deux exécutions `IABI`). Les lignes de tendance ne comptent qu'**1 essai** : c'est la règle
  choisie par le propriétaire avant la lecture (`LIGNES_DE_TENDANCE.md`, « Règle de décision choisie par le
  propriétaire » : « 1 essai compté sur la période finale (au lieu de 2) »).
- **Au plus 40 essais sur DEVELOPMENT** (853 → 893 au plus) et **2 sur FINAL_TEST** (8 → 10), plus ce qu'une lecture
  finale éventuelle d'une règle de l'étape 2 compterait selon sa propre déclaration.
- **Correction déclarée** : Bonferroni à l'intérieur de chaque ligne du tableau ; les conditions supplémentaires d'une
  décision (excès de timing en plus de l'excès uniforme, différence avec `REF_TOUS`, sous-ensemble sans `TRENDLINE`)
  la rendent plus sévère sans compter d'essai de plus. La protection contre les ~890 essais du programme ne vient pas
  d'un seuil (0,05/890 serait hors d'atteinte), mais de la **confirmation sur des paires jamais utilisées**, avec des
  règles figées avant de les voir. Une `PISTE` sur les 40 paires seules n'est qu'un indice.

## 7. Puissance des étapes 1 et 2 (réaliste)

- **Nombre d'événements** (ordre de grandeur estimé sans données ; le vrai comptage sera inscrit avant l'exécution,
  § 1.8) : sur les 40 paires, quelques milliers à quelques dizaines de milliers de déclencheurs par brique nouvelle
  (pour comparaison, `RSI_DIV` 1 h : 13 966 ; `TRENDLINE` 1 h : 1 698 ; un rebond « un par jour au plus » sur 40 paires
  et 6,5 ans ne peut pas dépasser environ 95 000). Les paires C donnent environ 3 à 4 fois plus d'événements
  (6 628 déclencheurs `TRENDLINE` 1 h contre 1 698).
- **Comptes insuffisants** : moins de 300 transactions (ou moins de 100 dans un pli de `LOGIT`) donne `INSUFFISANT`,
  sans redéfinition (§ 1.1).
- **Précision attendue** : sur les paires C, avec 5 600 transactions, l'intervalle à 97,5 % de l'excès des lignes de
  tendance faisait ±0,075 R. À 99,5 % et avec 2 000 à 10 000 transactions, compter **±0,08 à ±0,16 R** au mieux. Les
  intervalles réels seront **plus larges** que cette estimation : déclencheurs chevauchants (ensemble « tous les
  déclencheurs »), blocs de 91 jours pour les règles à états (peu de blocs : 26 sur 6,5 ans), excès de timing calculé
  sur des placebos plus proches du déclencheur. Donc seul un excès de timing d'environ **0,1 à 0,2 R par transaction**
  a une chance raisonnable d'être vu ; la meilleure piste du programme était à +0,12 R.
- **Frais** : avec un stop à 2 ATR (1,5 à 2,5 % pour une altcoin en 1 h), l'aller-retour central coûte environ 0,1 R ;
  les placebos le paient aussi. Pour un gain, il faut un excès supérieur à ce que les placebos perdent (−0,04 à
  −0,08 R dans l'étude des lignes de tendance).
- **Le modèle logistique** n'est jugé que sur 4,5 ans (2021 → 2025-06) : moins de transactions, intervalle plus
  large.

## 8. Lectures déclarées et suites

- **Rien** (aucune brique ni combinaison `CONFIRMEE` sur C) : le profil de volume et le flux approché par les bougies
  n'apportent pas d'information mesurable, seuls ou votés, avec cette transaction. Aucun usage, aucune nouvelle
  brique ajoutée « pour voir » : ce serait un nouveau programme, à déclarer et à compter.
- **Piste** (`PISTE` sur R, `NON_CONFIRMEE` sur C) : hasard probable sur 40 paires ; aucun usage.
- **Confirmée** (`CONFIRMEE` sur C) : la règle choisit le bon moment, à régime égal, sur des paires jamais vues. Sans
  `GAIN_DEMONTRE`, elle ne paie pas les frais. Avec `GAIN_DEMONTRE` : candidate à un **test en direct pré-inscrit**
  (nouveau module, signaux shadow), qui seul peut confirmer aussi la part « régime » ; jamais présentée comme rentable
  avant.
- **La période réservée reste fermée pour les étapes 1 et 2.** Elle a déjà été consultée trois fois (volatilité,
  lignes de tendance, IA locale) et sert de période de validation pour tout le programme ; pour une règle qui contient
  `TRENDLINE`, elle n'est plus un juge neutre (`LIGNES_DE_TENDANCE.md`). Une **lecture unique** de la seule meilleure
  règle confirmée (la plus grande borne basse de l'excès de timing sur C, choix fixé maintenant) n'aura lieu que sur
  **décision explicite du propriétaire**, avec son propre pré-enregistrement, une répétition sur DEVELOPMENT et une
  relecture, dans l'ordre du § 9.
- Ce que ce programme ne mesure pas : la rentabilité d'un portefeuille (taille, corrélations entre paires, capital
  immobilisé), l'exécution réelle sur Binance Demo, les signaux en image.

## 9. Ordre d'exécution (rien ne bloque)

1. Code et tests (§ 10) → relecture `leak-auditor` du code → contrôles du § 1.8 et comptages inscrits ici.
2. **Étape 1 sur R** (exécution unique, 10 essais).
3. **Étape 2 sur R** (exécution unique, 10 essais).
4. **Confirmation sur C** de toutes les règles `PISTE` des étapes 1 et 2 (exécution unique, `2 · (m₁ + m₂)` essais) ;
   sautée s'il n'y en a aucune.
5. **Si une règle est `CONFIRMEE` sur C** : le propriétaire décide de sa lecture finale éventuelle **avant** la voie A.
   S'il la veut, elle est pré-enregistrée et faite d'abord (sur la période réservée, coupure à fixer dans sa
   déclaration), puis la voie A suit. S'il ne la veut pas, ou si aucune règle n'est confirmée, **la voie A suit
   directement**, sans autre attente.
6. **Voie A de l'étape 3** (consultation déclarée, 2 essais sur FINAL_TEST).
7. **Voie B de l'étape 3** : indépendante ; elle démarre dès sa pré-inscription et la relecture de son module, en
   parallèle de tout ce qui précède (elle ne lit que des signaux et des bougies postérieurs à son démarrage).

## 10. Mise en œuvre prévue (après la relecture de ce document)

- Nouveau module `research/combinations.py` (briques, votes, modèle, placebos, mesures), tests
  `tests/test_combinations.py` (données synthétiques seulement : causalité, mutations, hypothèse nulle, facteur commun,
  exemples vérifiables à la main de chaque brique, dont la reprise du VWAP avant et après la confirmation de l'ancre),
  commandes `csi combinaisons-briques`, `csi combinaisons-votes`, `csi combinaisons-confirmation`,
  `csi combinaisons-telegram` ; un module séparé, sous `forward/`, pour la voie B.
- **Importés sans changement** : `patterns/volume.py`, `patterns/primitives.py`, `patterns/indicators.py`,
  `forward/f15.py` (gelé), `research/figures_history.py` (`play`, `in_period`, `order_window`, `Setup`),
  `research/trendline_confirmation.py` (`universe`, `coverage`), `external/audit.py` (`read_telegram_export`),
  `external/parser.py`, `external/trailing.py`, `backtest/metrics.py`. **Réimplémentés ici** : le tirage des placebos
  uniformes (identifiant propre, § 1.5) et le filtrage des signaux sur `available_at` (§ 5.2). Aucun module gelé d'un
  test en direct n'est modifié (`tests/test_frozen_running_tests.py`).
- Registre : types `COMBO_BRIQUES`, `COMBO_VOTES`, `COMBO_CONFIRMATION`, `COMBO_TELEGRAM` ; `n_trials` du § 6 ;
  empreintes des bougies et du parseur ; commit propre exigé.

## Historique

- 2026-10-07 : déclaré avant tout code, tout téléchargement et toute exécution (demande et plan du propriétaire du
  même jour). Seul calcul fait sur des données réelles : le comptage des messages Telegram lisibles par mois et par
  groupe (§ 5.2), sans aucun prix ni résultat ; et la lecture du registre (853 essais sur DEVELOPMENT, 8 sur la
  période finale, 3 consultations).
- 2026-10-07 : corrections de la relecture leak-auditor et décisions du propriétaire, aucun résultat vu.
  - Décisions du propriétaire : étape 3 par les deux voies (A : consultation n° 4, 2 essais sur FINAL_TEST ;
    B : test en direct, nouveau module) ; J2 retiré ; ordre d'exécution du § 9.
  - Placebos appariés sur la paire, l'année et les trois états ; excès de timing exigé pour `PISTE` et `CONFIRMEE` ;
    part « régime » confirmable seulement dans le temps.
  - Contrôle nul : cas valides par type de règle, `|z| < 3` et `|biais| ≤ 0,02 R`, règle en cas d'échec, test
    « facteur commun ».
  - Deux décisions par règle (excès, gain) : 10 + 10 essais sur R. Confirmation des étapes 1 et 2 en une seule
    exécution sur C, avec un seul Bonferroni.
  - Excès sur tous les déclencheurs ; « une position par paire » seulement pour le gain et le descriptif.
    Différences emboîtées (vote contre référence, sans `TRENDLINE`) avec une borne basse > 0. Mutation sur la date de
    sortie.
  - Étape 3 : placebo par groupe × semaine ISO ; doublons entre groupes ; comptages avant tout R ; exclusion par la
    date avec une coupure fixée ; `available_at` ; gain descriptif (2 essais).
  - `deltaC` centré pour `CVD_DIV` et `FLUX` ; `ABSORPTION` à 100 transactions au moins, égalités et devise
    précisées.
  - `LOGIT` : règle des valeurs manquantes calculée par pli ; `TRENDLINE` déclarée choisie après coup ; contrôle
    positif sur un vote.
  - Reprise du VWAP datée après la confirmation de l'ancre.
  - Garde-fou des années exigé sur C. Blocs de 91 jours pour les règles à états. Tirage jours × paires en contrôle de
    sensibilité.
  - Placebos uniformes réimplémentés avec l'identifiant de cette étude ; horizon aligné sur `in_period` (80 h).
  - Comptes insuffisants sans redéfinition ; intervalles réels plus larges que l'estimation.
- 2026-10-07 : deuxième relecture leak-auditor, aucun résultat vu. Quatre choix qui auraient été faits après coup sont
  fixés maintenant :
  - **A.** Étape 3 : cases **groupe × mois civil** ; définition des cases utiles ; minimums comptés dans ces cases
    (100 signaux dans la partie d'étude, 40 dans la partie tenue à l'écart) ; puissance refaite ; mêmes règles pour
    la voie B.
  - **B.** `deltaC` centré sur la part acheteuse pondérée par le volume, avec 684 bougies valides ; contrôle par
    année descriptif seulement.
  - **C.** Référence d'`ABSORPTION` limitée aux bougies d'au moins 100 transactions, avec 360 bougies valides sur
    720.
  - **D.** Témoin non apparié (rendement de BTC sur 7 jours) dans le test « facteur commun » ; le témoin apparié est
    déclaré non concluant ; règle de repli par type d'excès, biais maximal = max(0, plus grand biais mesuré).

  Précisions :
  - **E.** Les heures candidates des placebos appariés comprennent l'horizon et les autres déclencheurs ; la `PISTE`
    porte sur deux ensembles légèrement différents.
  - **F.** Pour `LOGIT`, `REF_TOUS` est restreint aux mêmes années ; blocs de 91 jours pour les différences ;
    `INSUFFISANT` si l'intervalle n'est pas calculable.
  - **G.** 100 000 tirages sur C ; sous-couverture du bootstrap par percentiles déclarée.
  - **H.** Dernier profil disponible à la réception.
  - **I.** Téléchargement à partir du 2025-11-01.
  - **J.** Ordre des doublons.
  - **K.** Total FINAL_TEST de 8 vérifié (§ 6).
  - **L.** L'excès de timing des règles à états ne peut venir que de l'interaction événement × régime.
