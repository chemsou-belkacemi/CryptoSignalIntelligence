# Figures et méthodes des analystes rejouées sur l'historique, contre placebos (déclaré le 2026-10-04, avant code et exécution)

Demande du propriétaire du 2026-10-04 : « pourquoi attendre mars ? tu ne peux pas prendre des anciennes données et
simuler comme s'il n'y avait pas d'après ? » et « les FVG, les autres ICT / Smart Money Concepts, les figures et
méthodes d'analyse ». Le détecteur de figures de F15 (`INDICATEURS.md` § 9) n'a jamais été joué sur le passé : il a été
mis directement en test en direct (F15, verdict vers mi-mars 2027). Ici, on rejoue **sur DEVELOPMENT** ce même
détecteur, sans y toucher, plus les éléments ICT/SMC pris seuls et les divergences RSI, chaque méthode jugée
séparément, frais compris, contre des achats au hasard de même géométrie. F15 continue en direct, sans changement : il
confirmera ou non sur des données que personne n'a vues.

Code : `research/figures_history.py` (le détecteur, `patterns/` et `forward/f15.py` sont **importés, jamais
modifiés**) ; tests : `tests/test_figures_history.py` ; commande : `csi figures-history`. DEVELOPMENT seulement
(jusqu'au 2025-06-30 23:59:59 UTC).

## Données et univers

- **40 paires de recherche** (`RESEARCH_UNIVERSE`), toutes cotées aujourd'hui. **Biais de survivance déclaré** :
  il gonfle le R moyen (paires qui ont survécu) ; l'excès sur les placebos, mesuré sur la même paire dans les 30 jours
  précédents, y est moins sensible, mais pas immunisé pour les méthodes qui achètent un repli (ordres limites sous le
  prix, tenues jusqu'à 60 jours en 1 jour) : sur des paires survivantes, un repli est plus souvent suivi d'une reprise,
  et les placebos, pris avant, ne portent pas ce conditionnement.
- **Détection** sur les bougies 1 h du magasin long (archives officielles), coupées à la fin de DEVELOPMENT ; 4 h et
  1 jour agrégés par `f15.aggregate` (alignés sur 00:00 UTC, bougies complètes seulement).
- **Exécution** sur les bougies 1 minute du magasin minute (`minute_history`, depuis 2018-01 ou le mois de cotation),
  coupées à la fin de DEVELOPMENT.
- **Période** : un déclencheur compte si la clôture de sa bougie de détection est au plus tôt le 2019-01-01 ET au
  moins **90 jours** après la première bougie 1 h de la paire (pivots et ATR établis, 30 jours de minutes pour les
  placebos), et si tout son horizon (20 bougies de validité de l'ordre + 60 bougies de tenue) se termine dans
  DEVELOPMENT.

## Méthodes (18) et ensemble F15

**A. Les 12 familles du détecteur de F15**, exactement (`f15.detect_frame`, définitions § 9 et § 9.6, niveaux du
§ 9.5) : `GARTLEY`, `BAT`, `BUTTERFLY`, `CRAB`, `ABCD`, `TRIANGLE`, `TRENDLINE`, `ICT` (sweep puis MSS avec FVG),
`HEAD_SHOULDERS` (inverse), `DOUBLE` (creux), `FLAG`, `CUP_HANDLE` ; unités 1 h, 4 h, 1 jour (ZigZag m = 3,0 / 2,5 /
2,0 ATR). Figures haussières jouables seulement ; géométries invalides comptées. Une figure = sa famille, son unité,
sa paire et ses pivots (identité de F15).

**B. Les éléments ICT/SMC pris seuls et les divergences RSI** : déclencheurs définis au § 2 à § 5 et § 10.4 de
`INDICATEURS.md` (`patterns/smc.py`, `patterns/indicators.py`, importés sans changement), mêmes unités de temps ;
**règles de transaction nouvelles, fixées ici avant le code**, sur le modèle de la famille `ICT` du § 9.5 (stop sous
la structure moins 0,25 ATR, objectifs à 1, 2 et 3 R) :

| Méthode | Déclencheur (haussier) | Entrée | Stop | Objectifs |
|---|---|---|---|---|
| `FVG` | FVG haussier (§ 2), connu à `i` | ordre limite au haut de la zone (`L_i`) | bas de la zone (`H_{i−2}`) − 0,25 × ATR_i | entrée + 1, 2 et 3 R |
| `OB` | order block haussier (§ 3), connu à `j` | ordre limite au haut de la zone (`H_k`) | bas de la zone (`L_k`) − 0,25 × ATR_j | entrée + 1, 2 et 3 R |
| `SWEEP` | sweep haussier (§ 4) à `i` | **au marché**, première minute après la clôture de `i` | `L_i` − 0,25 × ATR_i | entrée + 1, 2 et 3 R |
| `BOS` | cassure haussière de type BOS (§ 5) à `i` | ordre limite au niveau cassé (retest) | dernier pivot bas fractal connu avant `i` − 0,25 × ATR_i | entrée + 1, 2 et 3 R |
| `CHOCH` | cassure haussière de type CHoCH (§ 5, MSS compris) à `i` | ordre limite au niveau cassé (retest) | dernier pivot bas fractal connu avant `i` − 0,25 × ATR_i | entrée + 1, 2 et 3 R |
| `RSI_DIV` | divergence RSI haussière régulière (§ 10.4), connue à `j2 + 2` | **au marché**, première minute après cette clôture | `L_j2` − 0,25 × ATR_{j2+2} | entrée + 1, 2 et 3 R |

R = entrée − stop ; ATR de Wilder 14 de l'unité de temps. Pour une entrée au marché, l'« entrée » est l'ouverture de
la minute d'exécution (avant glissement), comme pour les placebos ; stop et objectifs sont posés à ce moment.
Géométrie invalide (stop à moins de 0,1 % sous l'entrée, ou au-dessus) : écartée, comptée. Aucune information
postérieure à la bougie de détection n'est lue (la date de comblement des FVG calculée par `smc` n'est pas utilisée).
Aucun filtre (tendance, volume, premium / discount) : chaque élément est testé nu, comme on l'enseigne.

Hors étude : les niveaux du § 10.1 à 10.3 (déjà testés, `NIVEAUX.md` : RIEN) et les indicateurs de tendance et de
momentum classiques (moyennes, MACD, Bollinger, Ichimoku, Supertrend) déjà présents sous une forme ou une autre dans les
stratégies A à C et les criblages D à S (`SCREENING.md`).

**C. Ensemble F15** : les 12 familles du A mises en commun : l'hypothèse de F15 rejouée sur le passé.

## Règles de transaction (celles de F15, § 9.5, sans autre différence que les cinq déclarées)

Ordre limite d'achat valable 20 bougies de l'unité de temps, exécuté seulement si le prix traverse la limite, annulé
si une minute ouvre au stop ou dessous avant l'exécution, exécuté au marché à l'ouverture si la première minute ouvre
déjà sous la limite ; sortie par tiers aux trois objectifs ; stop fixe ; 60 bougies au plus, comptées en temps depuis
l'exécution, le reste vendu à la clôture ; frais du modèle commun (`forward/costs.py`), central et défavorable ;
R = résultat net / (entrée prévue − stop). Plusieurs déclencheurs simultanés sur une paire sont tous joués et comptés
séparément (corrélés, déclaré).

Différences avec F15, déclarées :
1. **Pas de départage à la seconde** (pas de bougies 1 s en 2019-2025) : une minute où le stop et un objectif sont
   touchés tous les deux sort au stop (règle de prudence du § 9.5), pour les figures comme pour les placebos.
2. **Ordre posé** à la première minute qui suit la clôture de la bougie de détection plus la latence de 2 s (pas de
   délai d'inscription, pas de figure `late`).
3. **Simulation** par une copie compilée (numba) de `f15.simulate`, prouvée identique par test sur des milliers de
   cas tirés au hasard à la fonction gelée appelée avec `resolver=None, late=True` (statut, prix d'exécution, issue,
   R arrondi à 6 décimales comme F15 : égalité exacte).
4. **Univers et période** : les 40 paires de recherche sur DEVELOPMENT, au lieu de la liste halal en direct.
5. **Départ du ZigZag** : la première bougie 1 h de la paire (F15 en direct part du 2025-08-01). Le ZigZag dépend de
   son point de départ jusqu'à ses premiers pivots ; l'échauffement de 90 jours couvre cette dépendance.

Propriétés de la simulation de F15 reprises telles quelles (code gelé, déclarées) : une minute qui ouvre au stop après
la pose annule l'ordre (un ordre réel aurait été exécuté puis stoppé ; rare en minutes) ; un ordre dont la fenêtre tombe
dans un trou de minutes sort « ordre expiré » ; les objectifs ne sont pas regardés dans la minute d'exécution
(nécessaire pour un ordre limite, légèrement pessimiste pour un achat au marché : placebos, `SWEEP`, `RSI_DIV`).

**Placebos** (ceux de F15) : pour chaque transaction exécutée, 20 achats au marché sur la même paire à des moments tirés
sans remise entre 1 et 30 jours avant l'exécution (`f15.placebo_offsets` sur l'identifiant du déclencheur), mêmes
distances de stop et d'objectifs en pourcentage, même sortie, même durée, mêmes frais (taker à l'entrée). Excès =
R du déclencheur − moyenne des R des placebos utilisables.

**Excès à frais d'entrée égaux** (ajouté à la relecture, avant l'exécution ; c'est lui qui porte le verdict) : un
placebo achète au marché et paie l'écart et le glissement, qu'un ordre limite exécuté en maker ne paie pas. Comme les
sorties ne dépendent pas du prix d'exécution, ce handicap vaut exactement `market × (1 + frais) / risque relatif` en R
(risque relatif = (entrée − stop) / entrée). Il est retiré de l'excès quand le déclencheur entre en maker (ordre limite
traversé) ; rien n'est retiré pour un achat au marché ni pour un ordre exécuté à l'ouverture de la première minute.
Sans cette correction, le hasard paraîtrait battu sous l'hypothèse nulle (de l'ordre de 0,01 à 0,09 R selon la
méthode, et le double en défavorable). L'excès brut est gardé en descriptif, comparable à F15 en direct, qui porte le
même biais (code gelé : son excès devra être lu avec cette réserve).

## Mesure et verdict

Pour chaque méthode (les trois unités de temps ensemble) et pour l'ensemble F15, dans chaque scénario de frais :
nombre de transactions exécutées, R net moyen et son intervalle, excès moyen à frais d'entrée égaux et son
intervalle. Intervalles : tirage par blocs de **28 jours présents dans l'échantillon** (jours ayant au moins une
exécution ; `metrics.day_block_ci`, 10 000 tirages, graine 20261004, au moins 10 blocs, donc au moins 253 jours
distincts), au niveau **1 − 0,05/19** (19 comparaisons, 19 essais au registre). Les tenues de 60 jours des figures
1 jour dépassent les blocs : intervalle peut-être trop étroit pour elles (déclaré).

Verdict (repris de F15) :
- `SUPERIEUR_AU_HASARD` : intervalle du R moyen ET intervalle de l'excès à frais d'entrée égaux entièrement au-dessus
  de 0, en central ET en défavorable ;
- `INFERIEUR_AU_HASARD` : intervalle du R moyen entièrement sous 0 dans les deux scénarios ;
- `INSUFFISANT` : moins de 30 transactions exécutées ou intervalle non calculable ;
- `NON_DEMONTRE` sinon.

Descriptif, hors verdict : par unité de temps et par année ; excès brut et son intervalle ; signe de l'intervalle de
l'excès seul ; part des transactions qui touchent le premier objectif (« taux de réussite » au sens des canaux) pour la
méthode et pour ses placebos ; **seuil simplifié** = part du premier objectif qui équilibrerait une sortie unique au
premier objectif ou au stop, sans frais (risque / (risque + gain au premier objectif)), repère seulement (la vraie
sortie se fait par tiers, avec frais) ; part gagnante ; parts des objectifs 1, 2, 3 ; part des exécutions en maker ;
ordres exécutés et annulés (motifs), géométries invalides ; ordres distincts (paire, unité, départ, entrée à 0,1 %
près) ; R moyen et excès sans la paire qui contribue le plus ; part du total (R et excès) apportée par la paire et
par l'année qui en apportent le plus (règle des 60 % d'`admission`, lue ici à titre descriptif).

**Lecture.** Un `SUPERIEUR_AU_HASARD` ne serait qu'une piste sur DEVELOPMENT (survivance, corrélations) : à confirmer
en direct (F15 pour les familles du détecteur ; un test en direct à déclarer pour une méthode du B) avant tout usage,
jamais présenté comme rentable. Un `NON_DEMONTRE` dit que, sur 6 ans et 40 paires, la méthode jouée mécaniquement ne
se distingue pas d'un achat au hasard de même géométrie, frais compris.

Ce que l'excès mesure : pour les méthodes à **ordre limite**, l'effet de la méthode ET celui d'attendre que le prix
revienne à la limite (une exécution conditionnée par une baisse, que des placebos au marché tirés sans condition ne
reproduisent pas) ; seuls `SWEEP` et `RSI_DIV`, achetés au marché comme les placebos, sont strictement symétriques.
Un excès positif d'une méthode à ordre limite dirait « mieux que d'acheter au hasard », pas « grâce à la figure
seule ».

## Nombre de déclencheurs attendu

Compté le 2026-10-04 avec les fonctions de détection, avant le code de l'étude et sans aucun résultat de transaction
(ni exécution, ni R) : déclencheurs haussiers dans la période, 40 paires, 1 h / 4 h / 1 jour.

| Méthode | 1 h | 4 h | 1 jour | Total |
|---|---|---|---|---|
| `TRIANGLE` | 14 559 | 5 385 | 1 382 | 21 326 |
| `ABCD` | 8 928 | 3 003 | 753 | 12 684 |
| `DOUBLE` | 2 035 | 705 | 183 | 2 923 |
| `TRENDLINE` | 1 698 | 657 | 198 | 2 553 |
| `ICT` | 1 679 | 427 | 55 | 2 161 |
| `HEAD_SHOULDERS` | 1 383 | 493 | 116 | 1 992 |
| `CRAB` | 1 189 | 463 | 86 | 1 738 |
| `BUTTERFLY` | 791 | 265 | 77 | 1 133 |
| `BAT` | 808 | 255 | 49 | 1 112 |
| `GARTLEY` | 583 | 210 | 38 | 831 |
| `CUP_HANDLE` | 195 | 73 | 11 | 279 |
| `FLAG` | 33 | 79 | 79 | 191 |
| **Ensemble F15** | | | | **48 923** (+ 120 géométries invalides) |
| `SWEEP` | 75 598 | 19 335 | 2 847 | 97 780 |
| `FVG` | 40 080 | 10 364 | 1 692 | 52 136 |
| `BOS` | 36 471 | 9 385 | 1 541 | 47 397 |
| `CHOCH` | 35 803 | 8 889 | 1 407 | 46 099 |
| `OB` | 27 996 | 7 135 | 1 266 | 36 397 |
| `RSI_DIV` | 13 966 | 3 347 | 537 | 17 850 |

La part des ordres limites exécutée n'est pas estimée. **`FLAG` (191 déclencheurs) sera `INSUFFISANT` par
construction et `CUP_HANDLE` (279) presque sûrement** : l'intervalle demande au moins 253 jours distincts avec une
exécution (connu d'avance, déclaré). Les 16 autres méthodes et l'ensemble F15 auront assez d'événements : un
`NON_DEMONTRE` y viendra de l'absence d'effet net ou de la corrélation des déclencheurs (simultanés, mêmes jours de
marché), d'où les blocs de 28 jours.
Minutes : couverture de 99,8 à 100 % des minutes attendues selon la paire (maintenances de Binance) ; BTC, ETH, LTC
et NEO ont des bougies 1 h dès 2017 mais des minutes seulement depuis 2018-01 : la borne du 2019-01-01 leur laisse
un an de minutes avant le premier déclencheur.

## Résultat (exécution unique, `FIGH-20261004T102820Z-479ac6`, 2026-10-04, essais 828 à 846) : **aucune méthode supérieure au hasard** ; `OB`, `SWEEP` et `RSI_DIV` perdent

Scénario central ; R par transaction (1 R = la perte au stop) ; intervalles au niveau 1 − 0,05/19 ; « TP1 » = part
des transactions qui touchent le premier objectif, pour la méthode / ses placebos / le seuil simplifié.

| Méthode | Exécutées | R moyen | IC du R | Excès (frais égaux) | IC de l'excès | TP1 méthode / placebos / seuil | Verdict |
|---|---|---|---|---|---|---|---|
| `TRENDLINE` | 2 175 | +0,077 | [−0,069 ; +0,223] | +0,152 | [+0,047 ; +0,259] | 60 % / 54 % / 59 % | `NON_DEMONTRE` |
| `HEAD_SHOULDERS` | 1 570 | +0,076 | [−0,068 ; +0,214] | +0,068 | [−0,034 ; +0,183] | 59 % / 56 % / 58 % | `NON_DEMONTRE` |
| `DOUBLE` | 2 455 | +0,021 | [−0,069 ; +0,107] | −0,013 | [−0,086 ; +0,058] | 75 % / 74 % / 76 % | `NON_DEMONTRE` |
| `TRIANGLE` | 16 853 | +0,000 | [−0,107 ; +0,117] | +0,049 | [−0,031 ; +0,130] | 48 % / 46 % / 51 % | `NON_DEMONTRE` |
| `ICT` | 1 266 | −0,034 | [−0,202 ; +0,149] | +0,074 | [−0,082 ; +0,240] | 46 % / 44 % / 50 % | `NON_DEMONTRE` |
| `GARTLEY` | 363 | −0,130 | [−0,322 ; +0,102] | −0,065 | [−0,232 ; +0,100] | 43 % / 47 % / 50 % | `NON_DEMONTRE` |
| `ABCD` | 4 007 | −0,203 | [−0,385 ; +0,007] | +0,062 | [−0,097 ; +0,233] | 20 % / 20 % / 21 % | `NON_DEMONTRE` |
| `BAT`, `BUTTERFLY`, `CRAB` | 168, 239, 87 | −0,118, +0,006, −0,384 | — | — | — | — | `INSUFFISANT` |
| `FLAG`, `CUP_HANDLE` | 158, 238 | +0,236, +0,091 | — | — | — | — | `INSUFFISANT` (annoncé) |
| **Ensemble F15** | 29 579 | −0,019 | [−0,134 ; +0,103] | +0,053 | [−0,031 ; +0,137] | 47 % / 46 % / 50 % | `NON_DEMONTRE` |
| `FVG` | 41 744 | −0,065 | [−0,142 ; +0,016] | +0,027 | [−0,030 ; +0,087] | 52 % / 49 % / 50 % | `NON_DEMONTRE` |
| `CHOCH` | 40 041 | −0,061 | [−0,148 ; +0,036] | +0,013 | [−0,061 ; +0,090] | 46 % / 46 % / 50 % | `NON_DEMONTRE` |
| `BOS` | 41 608 | −0,049 | [−0,148 ; +0,048] | −0,078 | [−0,165 ; +0,006] | 45 % / 47 % / 50 % | `NON_DEMONTRE` |
| `OB` | 20 749 | −0,101 | [−0,181 ; −0,019] | −0,035 | [−0,103 ; +0,035] | 50 % / 50 % / 50 % | `INFERIEUR_AU_HASARD` |
| `RSI_DIV` | 17 843 | −0,106 | [−0,180 ; −0,025] | +0,070 | [+0,012 ; +0,133] | 48 % / 46 % / 50 % | `INFERIEUR_AU_HASARD` |
| `SWEEP` | 97 588 | −0,208 | [−0,266 ; −0,147] | +0,022 | [−0,028 ; +0,074] | 49 % / 48 % / 50 % | `INFERIEUR_AU_HASARD` |

Défavorable : mêmes conclusions (R plus bas de 0,02 à 0,11 ; `ABCD` et `FVG` y ont un IC du R entièrement négatif).

**Lecture.**
- Aucune méthode, jouée mécaniquement avec ses règles, n'a un gain démontré après frais sur 6 ans et 40 paires (et le
  biais de survivance joue en leur faveur). `OB`, `SWEEP` et `RSI_DIV` perdent de façon démontrée (−0,10 à −0,21 R par
  transaction).
- **Taux de réussite** : le double creux touche son premier objectif 75 % du temps… et ne gagne rien, parce que sa
  géométrie (premier objectif proche, stop loin) demande 76 % ; un achat au hasard de même géométrie le touche 74 % du
  temps. Même chose pour la ligne de tendance (60 % contre 59 % nécessaires) et la tête-épaules (59 % contre 58 %).
  Un pourcentage de réussite seul ne dit rien.
- Les harmoniques (`BAT`, `BUTTERFLY`, `CRAB`) sont `INSUFFISANT` faute de jours d'exécution : leur ordre limite au haut
  de la PRZ est rarement atteint (5 à 21 % des ordres). Ce n'était pas annoncé dans la déclaration (seuls `FLAG` et
  `CUP_HANDLE` l'étaient) : erreur de prévision, sans effet sur les autres verdicts.

**Contrôle de l'instrument après l'exécution (marches aléatoires sans mémoire, aucune donnée réelle).** Le même code,
lancé sur 12 marches aléatoires synthétiques de 3 ans (rendements minute indépendants : rien n'y est prévisible),
donne des « excès » qui ne sont pas nuls pour les unités longues. Exemples : `SWEEP` 1 jour +0,39 R, `RSI_DIV` 4 h +0,13
et 1 jour +0,42, `ABCD` 1 jour +1,5, `BOS` 1 jour −0,70, `OB` 1 jour −0,55. Les placebos sont tirés dans les 30 jours
**avant** l'exécution, c'est-à-dire pendant le mouvement qui a formé le déclencheur (la baisse avant une divergence ou
un sweep) ; leurs résultats sont donc conditionnés par ce chemin, même sans aucune mémoire des prix. Sur les vraies
données, la plupart des cases suivent ce biais (l'excès de `RSI_DIV`, +0,04 / +0,17 / +0,29 en 1 h / 4 h / 1 jour,
est de l'ordre du biais : −0,03 / +0,13 / +0,42). Conséquences :
- les **verdicts restent valables** : `SUPERIEUR_AU_HASARD` exige aussi un R moyen positif, et `INFERIEUR_AU_HASARD`
  ne regarde que le R, que ce biais ne touche pas ;
- l'**excès sur les placebos ne doit pas être lu seul**, surtout en 4 h et en 1 jour ; l'excès de `RSI_DIV` ne montre
  pas d'information propre ;
- la même construction des placebos est celle de **F15** (et de F4, F16) en direct : leur excès porte le même biais
  (code et section gelés, non modifiés) ; leurs verdicts exigent aussi un R moyen positif ;
- excès réel moins excès sous l'hypothèse nulle, par méthode et unité (36 cases, erreur type par paire-mois) : une
  seule case ressort nettement, **`TRENDLINE` en 1 h : +0,21 R (z = 3,7)** ; les autres sont entre −2,1 et +2,5, ce que
  le hasard donne sur 36 cases. Son R moyen en 1 h est de +0,08 [−0,01 ; +0,16] (95 %), non démontré. Remarquée
  **après** l'exécution : c'est une piste à déclarer et à tester sur d'autres données, pas un résultat.
  Script : `figh_null.py` (bloc-notes de la session) ; non commité.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
- 2026-10-04, relecture indépendante avant l'exécution (`leak-auditor` ; causalité vérifiée par troncature sur les
  18 méthodes × 3 unités, copie compilée vérifiée sur 348 transactions × 20 placebos × 2 scénarios, aucune
  exécution ni R sur les vraies données) :
  - **corrigé** : excès à frais d'entrée égaux (handicap maker / taker des placebos), qui porte désormais le verdict ;
    l'excès brut reste en descriptif ; tests ajoutés (écart exact, et excès corrigé nul sur une marche sans dérive) ;
  - **déclaré** : `FLAG` et `CUP_HANDLE` presque sûrement `INSUFFISANT` (253 jours distincts nécessaires) ; ce que
    l'excès mesure pour les ordres limites ; survivance nuancée pour les achats de repli ; départ du ZigZag ;
    propriétés reprises de F15 (annulation à l'ouverture au stop, trous de minutes, minute d'exécution) ;
  - **ajouté au descriptif** : parts de la paire et de l'année qui apportent le plus (R et excès), R moyen sans la
    paire la plus influente, part des exécutions en maker, seuil simplifié défini ;
  - placebos calculés dans le même ordre d'opérations que F15 (`q0 · stop / entrée`) ; tests élargis (troncature en
    1 h / 4 h / 1 jour, comparaison avec `f15.resolve_one` en 4 h et avec un grand trou de données).
- 2026-10-04 : exécution unique `FIGH-20261004T102820Z-479ac6` (essais 828 à 846, programme 846) ; contrôle de
  l'instrument sur marches aléatoires ensuite (biais des placebos tirés avant l'exécution en 4 h et 1 jour).
