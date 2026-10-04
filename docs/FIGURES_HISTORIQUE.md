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
  précédents, y est beaucoup moins sensible.
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

## Règles de transaction (celles de F15, § 9.5, sans autre différence que les quatre déclarées)

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
   cas tirés au hasard à la fonction gelée appelée avec `resolver=None, late=True` (résultat, prix d'exécution, issue,
   R au bit près à 1e-9).
4. **Univers et période** : les 40 paires de recherche sur DEVELOPMENT, au lieu de la liste halal en direct.

**Placebos** (ceux de F15) : pour chaque transaction exécutée, 20 achats au marché sur la même paire à des moments tirés
sans remise entre 1 et 30 jours avant l'exécution (`f15.placebo_offsets` sur l'identifiant du déclencheur), mêmes
distances de stop et d'objectifs en pourcentage, même sortie, même durée, mêmes frais (taker à l'entrée). Excès =
R du déclencheur − moyenne des R des placebos utilisables.

## Mesure et verdict

Pour chaque méthode (les trois unités de temps ensemble) et pour l'ensemble F15, dans chaque scénario de frais :
nombre de transactions exécutées, R net moyen et son intervalle, excès moyen sur les placebos et son intervalle.
Intervalles : tirage par blocs de **28 jours** (`metrics.day_block_ci`, 10 000 tirages, graine 20261004, au moins 10
blocs), au niveau **1 − 0,05/19** (19 comparaisons, 19 essais au registre). Les tenues de 60 jours des figures 1 jour
dépassent les blocs : intervalle peut-être trop étroit pour elles (déclaré).

Verdict (repris de F15) :
- `SUPERIEUR_AU_HASARD` : intervalle du R moyen ET intervalle de l'excès entièrement au-dessus de 0, en central ET en
  défavorable ;
- `INFERIEUR_AU_HASARD` : intervalle du R moyen entièrement sous 0 dans les deux scénarios ;
- `INSUFFISANT` : moins de 30 transactions exécutées ou intervalle non calculable ;
- `NON_DEMONTRE` sinon.

Descriptif, hors verdict : par unité de temps et par année ; signe de l'intervalle de l'excès seul ; part des
transactions qui touchent le premier objectif (« taux de réussite » au sens des canaux) pour la méthode et pour ses
placebos, et part nécessaire pour ne pas perdre avec sa géométrie ; part gagnante ; parts des objectifs 1, 2, 3 ;
ordres exécutés et annulés (motifs), géométries invalides ; ordres distincts (paire, unité, départ, entrée à 0,1 %
près) ; R moyen sans la paire qui contribue le plus.

**Lecture.** Un `SUPERIEUR_AU_HASARD` ne serait qu'une piste sur DEVELOPMENT (survivance, corrélations) : à confirmer
en direct (F15 pour les familles du détecteur ; un test en direct à déclarer pour une méthode du B) avant tout usage,
jamais présenté comme rentable. Un `NON_DEMONTRE` dit que, sur 6 ans et 40 paires, la méthode jouée mécaniquement ne
se distingue pas d'un achat au hasard de même géométrie, frais compris.

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

La part des ordres limites exécutée n'est pas estimée. Toutes les méthodes auront bien plus de 30 transactions : un
`NON_DEMONTRE` ne viendra pas d'un manque d'événements, mais il peut venir de leur corrélation (déclencheurs
simultanés, mêmes jours de marché), d'où les blocs de 28 jours. `FLAG` et `CUP_HANDLE` sont les plus minces.
Minutes : couverture de 99,8 à 100 % des minutes attendues selon la paire (maintenances de Binance) ; BTC, ETH, LTC
et NEO ont des bougies 1 h dès 2017 mais des minutes seulement depuis 2018-01 : la borne du 2019-01-01 leur laisse
un an de minutes avant le premier déclencheur.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
