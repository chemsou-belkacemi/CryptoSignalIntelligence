# Cassures de ligne de tendance en 1 h : confirmation sur 214 paires jamais utilisées (déclaré le 2026-10-04, avant code et exécution)

**D'où vient la piste.** Dans `FIGURES_HISTORIQUE.md` (exécution `FIGH-20261004T102820Z-479ac6`, 40 paires de
recherche), aucune méthode d'analyste n'a de gain démontré. Après l'exécution, le contrôle de l'instrument sur des
marchés aléatoires a montré que les placebos tirés dans les 30 jours avant l'exécution sont biaisés en 4 h et 1 jour ;
une fois ce biais retiré, une seule case ressortait nettement : **`TRENDLINE` en 1 h, +0,21 R par transaction de mieux
que le hasard (z = 3,7)**, pour un R moyen de +0,08 non démontré. Elle a été remarquée **après** avoir vu les
résultats : c'est une piste contaminée sur les 40 paires. Demande du propriétaire du 2026-10-04 (« oui ») : la tester
sur d'autres données.

Code : `research/trendline_confirmation.py` (détecteur de F15, exécution et placebos de `figures_history.py`, importés
sans changement) ; tests : `tests/test_trendline_confirmation.py` ; commande : `csi trendline-confirmation`.
DEVELOPMENT seulement. **2 essais.**

## Données

- **Paires** : les paires du recensement de l'univers à date (`UNIVERSE_PIT.md` : paires USDT cotées et retirées,
  moins stablecoins, tokens adossés ou à levier, cryptos jugées haram, exclusions historiques) passées au moins une fois
  par le top 40 mensuel, **hors des 40 paires de recherche**. Elles n'ont servi à **aucune** étude de figures ; elles ont
  servi à d'autres criblages (K à date, portefeuilles hebdomadaires, grille / DCA). Environ 50 sont retirées de la cote
  avant la fin de DEVELOPMENT : le biais de survivance des 40 paires n'existe pas ici.
- Bougies 1 h du magasin long et bougies 1 minute du magasin minute (archives officielles vérifiées, téléchargées le
  2026-10-04 **jusqu'à la fin de DEVELOPMENT seulement**), coupées au 2025-06-30 23:59:59 UTC.

## Déclencheurs et transactions (identiques à l'étude des figures)

`TRENDLINE` en 1 h seulement, du détecteur de F15 (`f15.detect_frame`, ZigZag m = 3 ATR, § 9.3 et § 9.5 de
`INDICATEURS.md`) : ordre limite d'achat à la valeur de la ligne à la cassure (retest), valable 20 bougies ; stop au
plus bas entre `P3` et la cassure moins 0,25 ATR ; sortie par tiers à entrée + ⅓, ⅔ et 1 × hauteur ; 60 heures au plus ;
frais du modèle commun, central et défavorable ; exécution sur les minutes (copie compilée de `f15.simulate`,
`figures_history.play`) ; minute ambiguë : stop d'abord. Période : détection au plus tôt le 2019-01-01 et 90 jours
après la première bougie 1 h de la paire, horizon entier dans DEVELOPMENT. Paire retirée de la cote avant la fin de
l'horizon : reste vendu à la dernière clôture (issue `COTATION_ARRETEE`, gardée). Comptage avant le code, sans
résultat de transaction : **6 540 déclencheurs**, dont 1 145 quand la paire est dans le top 40 du mois.

## Placebos : tirés sur tout l'historique de la paire (nouveauté)

Pour chaque transaction exécutée, **20 achats au marché sur la même paire à des minutes tirées uniformément, sans
remise, sur toute sa période utilisable** (de la date de début des déclencheurs de la paire jusqu'à la fin de ses
données dans DEVELOPMENT moins 60 heures ; graine déduite de l'identifiant), mêmes distances de stop et d'objectifs en
pourcentage, même sortie par tiers, même durée, mêmes frais (taker à l'entrée) ; minute tirée dans un trou de plus de
10 minutes : placebo inutilisable. Contrairement aux placebos de F15 (1 à 30 jours avant l'exécution, pendant le
mouvement qui forme la figure), ils ne dépendent pas du chemin qui précède la cassure : sous l'hypothèse nulle (prix sans
mémoire), leur excès moyen est nul.

**Excès à frais d'entrée égaux** (comme dans `FIGURES_HISTORIQUE.md`) : R de la transaction − moyenne des R des placebos
− `market × (1 + frais) / risque relatif` si l'entrée est maker.

**Contrôle de l'instrument avant l'exécution** (sur données synthétiques, aucune donnée réelle) : le même code sur des
marches aléatoires sans mémoire doit donner un excès moyen compatible avec 0 (moins de 2 erreurs types) ; sinon,
l'exécution n'a pas lieu tant que l'instrument n'est pas corrigé. Résultat inscrit ci-dessous avant l'exécution.

**Résultat du contrôle (2026-10-04, avant l'exécution, 40 marches aléatoires de 3 ans, même code
`trendline_confirmation.pair_rows`)** : 997 transactions `TRENDLINE` 1 h ; excès à frais égaux sur les placebos tirés
sur tout l'historique **−0,034 R (erreur type 0,028, z = −1,2)** : compatible avec 0, **l'exécution peut avoir lieu**.
Pour mémoire, sur les mêmes marches : excès avec les placebos de F15 +0,007 (z = 0,3) ; R moyen −0,062 (les frais).
Script : `trend_null.py` (bloc-notes de la session).

## Décision (2 comparaisons, niveau 1 − 0,05/2)

Intervalles : tirage par blocs de 28 jours présents (`metrics.day_block_ci`, 10 000 tirages, graine 20261004, au
moins 10 blocs).

1. **La piste** : excès à frais d'entrée égaux sur les placebos tirés sur tout l'historique.
   `PISTE_CONFIRMEE` si l'intervalle est entièrement au-dessus de 0 en central ET en défavorable ; `INVERSE` s'il est
   entièrement en dessous dans les deux ; sinon `NON_CONFIRMEE`.
2. **Le gain** : R net moyen. `GAIN_DEMONTRE` si l'intervalle est entièrement au-dessus de 0 dans les deux
   scénarios ; `PERTE_DEMONTREE` s'il est entièrement en dessous dans les deux ; sinon `GAIN_NON_DEMONTRE`.

Moins de 30 transactions ou intervalle non calculable : `INSUFFISANT`.

Descriptif, hors décision : sous-ensemble « paire dans le top 40 du mois » ; paires cotées contre retirées ; par
année ; part des transactions qui touchent TP1, TP2, TP3, contre les placebos ; issues ; excès avec les placebos de F15
(1 à 30 jours avant) pour comparaison ; paire qui apporte le plus et résultat sans elle ; part des exécutions en maker.

**Lecture déclarée.**
- `PISTE_CONFIRMEE` + `GAIN_DEMONTRE` : une cassure de ligne de tendance en 1 h, jouée mécaniquement, gagne sur des
  paires jamais vues et bat le hasard. Ce serait le premier résultat de ce genre du programme : à mettre en test en
  direct déclaré (la famille est déjà inscrite par F15, sans verdict propre) avant tout usage, jamais présenté comme
  rentable avant.
- `PISTE_CONFIRMEE` seule : la cassure contient de l'information, mais pas assez pour payer les frais avec ces
  objectifs et ce stop.
- `NON_CONFIRMEE` : la case des 40 paires était du hasard (une sur 36).

## Ajouts de la relecture, avant l'exécution (2026-10-04, `leak-auditor` ; mesure et décision inchangées)

- **Minutes complètes exigées** (point bloquant de la relecture) : pour chaque paire qui a au moins un déclencheur, les
  bougies 1 minute doivent couvrir au moins **99 %** des heures 1 h de la période utile (30 jours avant le premier
  déclencheur possible jusqu'à la dernière heure) et ne pas s'arrêter plus d'un jour avant la dernière heure ; sinon
  l'exécution est **refusée et rien n'est compté** (paire absente ou tronquée = biais possible). Couverture par paire
  gardée dans le rapport. L'exécution attend aussi la fin du téléchargement sans échec.
- **Paires** : 223 paires du top 40 à date hors des 40 ; 8 n'ont pas d'historique 1 h (AION, ANT, GAL, JST, LEVER, SC,
  SKL, SUN : aucun déclencheur possible), 215 en ont, 214 ont des heures dans la période, 211 au moins un déclencheur ;
  6 540 déclencheurs valides sur 6 628.
- **Placebos, référence rétrospective** : tirés sur toute la période de la paire, ils lisent des données postérieures à
  la décision (jusqu'à 6 ans). Le déclencheur et ses niveaux restent causaux ; l'excès n'est pas un gain réalisable,
  seule la décision « gain » en parle. Contrôle sur les vraies heures (sans aucun résultat) : les déclencheurs ne se
  regroupent pas dans le temps (position moyenne dans la fenêtre des placebos 0,49 ; part par année proche de la part
  du temps couvert par les placebos, à moins d'un point près chaque année).
- **Contrôle sous l'hypothèse nulle étendu** (120 marches de 3 ans par cas, même `pair_rows`) :

  | Cas synthétique | Excès, placebos sur tout l'historique | Excès, placebos de F15 | TP1 transactions / placebos |
  |---|---|---|---|
  | volatilité constante (contrôle déclaré, 40 marches) | −0,034 (z −1,2) | +0,007 (z 0,3) | — |
  | régimes de volatilité ×0,5 / 1 / 2 sur 20 jours | +0,014 (z 0,7) | +0,050 (z 2,6) | 61 % / 54 % |
  | tendances ±0,4 %/jour par régimes de 30 jours | −0,003 (z −0,2) | +0,037 (z 2,2) | 62 % / 63 % |
  | hausse puis chute, avec régimes de volatilité | +0,014 (z 0,8) | +0,058 (z 3,1) | 60 % / 54 % |

  L'instrument de décision tient (biais de +0,014 R au plus : un intervalle dont la borne basse tombe entre 0 et 0,02
  sera signalé). En revanche, **la comparaison des taux d'objectifs (TP1, TP2, TP3) contre les placebos est biaisée**
  d'environ +6 points dès que la volatilité change de régime (non interprétable comme information), et **l'excès
  contre les placebos de F15 est biaisé de +0,04 à +0,06 R** : la piste des 40 paires (+0,21 R, mesurée contre ces
  placebos puis corrigée du biais de volatilité constante) est probablement surestimée d'environ 0,05 R, en plus de
  l'effet du gagnant (1 case sur 36).
- **Sélection sur la popularité future** : l'univers est « passé au moins une fois par le top 40 » jusqu'en 2025-06 ;
  14 % des déclencheurs précèdent la première entrée de leur paire dans ce top. Ce n'est pas le biais de survivance
  des retraits (absent ici), mais une sélection sur l'avenir qui touche surtout la décision « gain » ; descriptif
  ajouté : avant / après la première entrée dans le top 40.
- **Drapeaux** : `top40` est causal (appartenance au 1er du mois, calculée sur les jours d'avant) ; « paire retirée »
  est rétrospectif (45 paires), descriptif seulement.
- Concentration par **année** ajoutée au descriptif (part du total) ; 4 déclencheurs seulement ont un horizon qui
  dépasse la dernière bougie d'une paire retirée ; ordre non exécuté avant la fin des données : `TROU` (exclu) ;
  symboles réutilisés et longs trous (LUNA, STRAX, CVC) : aucune transaction ni placebo ne traverse un trou, le
  détecteur si (4 déclencheurs), négligeable ; intervalles par blocs un peu étroits (les placebos d'une paire partagent
  son historique), effet faible.

## Résultat (exécution unique, `TRND-20261004T142935Z-6702de`, 2026-10-04, essais 852 et 853) : **`PISTE_CONFIRMEE`**, **`GAIN_NON_DEMONTRE`** (de justesse)

223 paires, 8 sans historique 1 h, 211 avec des déclencheurs, minutes couvertes à 100 % pour toutes (aucun refus) ;
6 628 déclencheurs : 5 598 exécutés sur 209 paires, 942 annulés, 88 géométries invalides. Intervalles à 97,5 %.

| Scénario | R moyen | IC | Excès sur les placebos (frais égaux) | IC | Placebos (R moyen) | Gagnantes |
|---|---|---|---|---|---|---|
| central | **+0,088** | **[+0,016 ; +0,161]** | **+0,117** | **[+0,044 ; +0,192]** | −0,041 | 52 % |
| défavorable | +0,066 | [−0,006 ; +0,140] | **+0,117** | **[+0,044 ; +0,193]** | −0,076 | 51 % |

- **La piste est confirmée** : sur des paires jamais utilisées, une cassure de ligne de tendance en 1 h fait mieux qu'un
  achat au hasard de même géométrie, de +0,12 R par transaction (borne basse +0,044, au-dessus du biais maximal de
  l'instrument mesuré sous l'hypothèse nulle, +0,014). Plus faible que sur les 40 paires (+0,21, surestimé comme prévu).
- **Le gain n'est pas démontré au sens déclaré** : démontré en frais centraux (borne basse +0,016), pas en frais
  défavorables (borne basse −0,006). Lecture déclarée : « la cassure contient de l'information » ; aucun usage avant un
  test en direct.
- Descriptif (95 %) : **les 7 années sont positives** (R de +0,03 en 2022 à +0,15 en 2020 ; excès de +0,04 à +0,16) ;
  la paire qui apporte le plus (LAZIO) pèse 4,7 % du total, l'année qui apporte le plus (2024) 29 % ; paires retirées
  R +0,15 [+0,07 ; +0,24] (1 134 transactions) et paires encore cotées +0,07 [0,00 ; +0,15] ; quand la paire est dans le
  top 40 du mois, R +0,13 [+0,05 ; +0,21], excès +0,17 ; avant sa première entrée dans le top 40, R +0,08 [−0,02 ; +0,17]
  (753 transactions) ; issues : stop 32 %, TP3 25 %, sortie au temps après TP1 ou TP2 23 %, sortie au temps 8 % ; 92 %
  des exécutions en maker. Les taux d'objectifs contre les placebos (59 % contre 52 % au TP1) et l'excès contre les
  placebos de F15 (+0,14) sont donnés pour mémoire : biaisés, voir ci-dessus.

## Confirmation sur la période réservée (déclaré le 2026-10-05, avant code, téléchargement et lecture)

**Décision du propriétaire du 2026-10-05** : « j'autorise l'ouverture de la période réservée pour les lignes de
tendance ». **Deuxième consultation** de la période réservée du programme (la première, le 2026-10-03, ne portait que
sur la volatilité). Enregistrée **avant** toute lecture ; une seule exécution ; après elle, ces 15 mois ne servent plus
de juge neutre pour une stratégie de direction. Code : `research/trendline_final.py` ; tests :
`tests/test_trendline_final.py` ; commande : `csi trendline-final --i-understand-final-test` (répétition :
`--rehearsal`). **2 essais** comptés sur la période finale.

**Ce qui est testé, sans aucun changement** : le même déclencheur (`TRENDLINE` 1 h du détecteur de F15), les mêmes
transactions (`figures_history.play`), les mêmes placebos tirés uniformément sur la période utilisable de la paire, le
même excès à frais d'entrée égaux, la même décision à deux comparaisons au niveau 1 − 0,05/2.

- **Période** : déclencheurs dont la clôture de détection est au plus tôt le **2025-07-01 00:00 UTC**, et 90 jours après
  la première bougie 1 h de la paire ; tout l'horizon (20 + 60 bougies) avant la coupure du **2026-09-30 23:59:59 UTC**.
  La détection lit l'historique d'avant (pivots), comme en direct.
- **Paires** : toutes les paires passées au moins une fois par le top 40 à date **avant le 2025-07-01** (263 paires,
  recensement de `UNIVERSE_PIT.md`, connu au début de la période ; les 40 paires de recherche en font partie), qui ont
  des bougies 1 h dans la période. Les paires retirées pendant la période comptent jusqu'à leur retrait ; les paires
  cotées après le 2025-06 n'y sont pas (univers figé au début de la période).
- **Placebos** : 20 minutes tirées uniformément sur [max(2025-07-01, première heure + 90 jours) ; min(coupure,
  dernière minute) − 60 heures] de la paire.
- **Complétude avant la consultation** : pour chaque paire qui a des bougies 1 h de juin 2025 à la coupure, les
  bougies 1 minute doivent couvrir au moins 99 % de ces heures et ne pas s'arrêter plus d'un jour avant la dernière ;
  sinon **rien n'est lu ni consulté** (le contrôle ne regarde que la présence des bougies, aucun prix).
- **Ordre** : contrôle de complétude → consultation enregistrée au registre (refus si cette stratégie a déjà consulté)
  → calcul → enregistrement. **Répétition** du même code sur 2024-04-01 → 2025-06-30 (DEVELOPMENT), rien d'enregistré.
- **Nombre attendu** (estimé sans rien lire de la période : 7 à 8 déclencheurs par paire et par an sur DEVELOPMENT ;
  environ 205 paires sur 15 mois) : environ **2 000 déclencheurs**, 1 700 exécutions. Intervalles par blocs de 28 jours
  présents : environ 16 blocs.

**Décision** (identique à DEVELOPMENT) : `PISTE_CONFIRMEE` / `NON_CONFIRMEE` / `INVERSE` (excès à frais égaux, central
et défavorable) ; `GAIN_DEMONTRE` / `GAIN_NON_DEMONTRE` / `PERTE_DEMONTREE` (R moyen, central et défavorable) ;
`INSUFFISANT` sous 30 transactions ou 10 blocs. Descriptif : 40 paires de recherche contre les autres ; mois où la
paire est dans le top 40 ; paires retirées ; par trimestre ; paire et trimestre qui apportent le plus.

**Lecture déclarée.**
- `PISTE_CONFIRMEE` + `GAIN_DEMONTRE` : deuxième confirmation, dans le temps cette fois ; la méthode devient candidate
  à des signaux shadow puis à BSM sur **Binance Demo**, sur décision du propriétaire, avec le suivi en direct (F17).
- `PISTE_CONFIRMEE` seule : l'information tient dans le temps, le gain après frais reste à montrer.
- `NON_CONFIRMEE` : non démontré sur 15 mois, pas une réfutation (voir la puissance plus bas) ; `INVERSE` : l'avantage mesuré sur 2019-2025 ne tient pas sur 2025-2026 ; aucun usage dans les deux cas.

### Avant la lecture : répétition, relecture et puissance (2026-10-05, rien de la période réservée n'a été lu)

- **Répétition** (`TRNR-20261004T225859Z-d6a45a`, 2024-04-01 → 2025-06-30, DEVELOPMENT, rien compté) : le code tourne de
  bout en bout ; 2 017 transactions sur 228 paires ; excès +0,171 [−0,002 ; +0,343], R +0,077 [−0,095 ; +0,250] : avec
  la règle déclarée, ce serait `NON_CONFIRMEE` et `GAIN_NON_DEMONTRE` malgré un excès plus grand que sur tout DEVELOPMENT.
- **Puissance** : sur 15 mois, la demi-largeur des intervalles est d'environ 0,15 à 0,17 R (blocs de 7, 14 ou 28 jours :
  pas de différence, la dépendance entre transactions est réelle). Si l'excès réel vaut +0,12 R (estimation de
  DEVELOPMENT), la chance d'obtenir `PISTE_CONFIRMEE` est d'environ **25 à 35 %**, celle de `GAIN_DEMONTRE` d'environ
  **10 %**. Découpage descriptif du rapport de DEVELOPMENT en fenêtres de 15 mois (relecture) : 2021-10 → 2022-12
  +0,03 (non confirmé), 2023-01 → 2024-03 +0,15 (confirmé), 2024-04 → 2025-06 +0,14 (non confirmé).
- **Lecture corrigée de `NON_CONFIRMEE`** : « non démontré sur 15 mois (puissance ≈ 30 %), **pas une réfutation** ;
  aucun usage ». Seul `INVERSE` réfuterait. La phrase « l'avantage ne tient pas » ci-dessus est remplacée par celle-ci.
- **Choix de la règle de décision laissé au propriétaire** avant toute lecture (garder la règle déclarée, passer à une
  seule comparaison unilatérale plus puissante, ou attendre que la période réservée s'allonge) : inscrit ci-dessous
  dès sa réponse, avant la lecture.
- **Procédure** : lecture seulement après la fin du téléchargement des minutes **sans échec** et une répétition sur le
  code final ; pas en même temps qu'une autre tâche lourde ; 4 processus au plus.
- **Panne après la consultation** (relecture) : toute erreur après l'inscription de la consultation est inscrite au
  registre (`FAILED`, 2 essais comptés par prudence). Règle fixée maintenant : si **aucun chiffre** n'a été produit,
  **une seule reprise** est permise, du même calcul, défaut technique seul corrigé et relu, enregistrée comme nouvelle
  consultation ; sinon, la période est perdue pour cette stratégie.
- Corrections de code de la relecture : rapport écrit avant l'inscription au registre ; univers limité aux mois
  d'appartenance antérieurs au 2025-07-01 ; test des bornes des placebos sur les bornes réellement utilisées ; cumul
  des essais de la période finale dans le rapport (5 pour la volatilité + 2).
- **Retiré du descriptif** : « mois où la paire est dans le top 40 » (l'appartenance s'arrête au 2025-06-01 : non
  calculable sur la période).

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution ; téléchargement des minutes lancé le même jour.
- 2026-10-04 : relecture indépendante avant l'exécution ; ajouts ci-dessus (garde de couverture des minutes, déclarations).
- 2026-10-04 : téléchargement terminé (214 paires, 0 échec) ; exécution unique `TRND-20261004T142935Z-6702de` (essais 852 et 853, programme 853).
- 2026-10-05 : confirmation sur la période réservée déclarée avant code, téléchargement et lecture (décision du propriétaire).
- 2026-10-05 : répétition, relecture indépendante et calcul de puissance avant la lecture ; corrections inscrites ; choix de la règle laissé au propriétaire.
