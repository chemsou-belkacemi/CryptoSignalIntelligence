# Les supports et résistances mécaniques prédisent-ils la réaction du prix ? (déclaré le 2026-10-04, avant exécution)

Demande du propriétaire du 2026-10-04 (« avancer dans la prédiction », après la carte d'analyse technique) : savoir si
les niveaux que trace l'analyse technique (`ANALYSE_TECHNIQUE.md`) ont un pouvoir de **prédiction**, ce qui dirait où
placer les objectifs et dans quel sens jouer une cassure. Méthode inspirée d'Osler (2000, « Support for Resistance »,
FRBNY Economic Policy Review) : comparer la réaction du prix aux **vrais niveaux** à sa réaction à des **niveaux
placebo** placés à côté, sur les mêmes paires et les mêmes périodes. Code : `research/level_reaction.py` ; tests :
`tests/test_level_reaction.py` ; commande : `csi level-reaction`. DEVELOPMENT seulement.

Lien avec le programme : le criblage K (`SCREENING.md`) a mesuré le rendement brut après un rebond sur support (K1,
négatif) et après une cassure de pivot (K2, ne tient pas à date), contre la dérive du marché. Ici la question est autre :
la **probabilité** d'un sens de sortie à barrières symétriques, comparée à des niveaux placebo ; aucune règle de
transaction n'est testée.

## Données

40 paires de recherche (`UNIVERSE.md`), bougies 1 h du magasin long, du 2019-01-01 à la fin de DEVELOPMENT
(2025-06-30) ; 4 h agrégées depuis 00:00 UTC (bougies complètes). Deux unités de temps : **1 h** et **4 h**.

## Niveaux réels (définitions de la carte d'analyse, sans aucun réglage nouveau)

À chaque bougie `t`, avec les seuls pivots ZigZag CONNUS à `t` (seuil m × ATR, m = 3,0 en 1 h et 2,5 en 4 h,
`INDICATEURS.md` § 1) parmi les 300 dernières bougies, regroupés quand ils sont à moins de 0,5 ATR_t (niveau = moyenne) :
la **résistance** est le niveau le plus proche au-dessus de la clôture de `t − 1`, le **support** le plus proche en
dessous.

## Niveaux placebo

Pour chaque niveau réel, au moment où il apparaît, deux niveaux placebo : le niveau réel décalé de `+u` et de `−u` ATR,
`u` tiré uniformément dans [1 ; 3] (graine fixe dérivée de la paire et de l'heure), écartés s'ils tombent à moins de
0,5 ATR d'un niveau réel du moment. Ils vivent aussi longtemps que le niveau réel et sont traités exactement comme lui.

## Événements et issues (barrières symétriques de 1 ATR, 24 bougies)

ATR = ATR de Wilder 14 de la bougie de l'événement ; issue mesurée depuis la clôture de cette bougie, sur les bougies
suivantes : « haut d'abord » si un plus haut atteint clôture + 1 ATR avant qu'un plus bas atteigne clôture − 1 ATR,
« bas d'abord » dans le cas inverse ; les deux dans la même bougie, ou aucune dans les 24 bougies : issue **nulle**,
comptée à part et exclue des taux (le nombre est donné).

- **Rejet (résistance)** : première bougie dont le plus haut arrive à moins de 0,1 ATR sous la résistance (ou la
  dépasse) **sans** clôturer au-dessus ; réussite = « bas d'abord ».
- **Cassure (résistance)** : première clôture au-dessus de la résistance + 0,1 ATR ; réussite = « haut d'abord ».
- Miroirs pour les supports (rebond = « haut d'abord », cassure vers le bas = « bas d'abord »), descriptifs seulement
  (CSI est long seulement : seuls les deux événements de résistance décident).

Un même niveau (réel ou placebo) ne produit qu'un événement de chaque type (le premier).

## Mesure et lecture

Pour chaque unité de temps et chaque événement décisionnel : taux de réussite aux niveaux réels et aux niveaux
placebo ; **écart = réel − placebo** ; IC par tirage de blocs de 7 jours (10 000 tirages, graine 20261004), niveau
1 − 0,05/4 (**4 comparaisons** : 2 événements × 2 unités de temps). Lecture :

- `EFFET` si l'IC de l'écart est entièrement au-dessus de 0 ;
- `EFFET_INVERSE` s'il est entièrement en dessous ;
- `RIEN` sinon.

Descriptif : années où l'écart est positif, part des paires, taux bruts, issues nulles. Un `EFFET` de rejet dirait
« placer un objectif juste sous une résistance mécanique est plus sûr qu'ailleurs » ; un `EFFET` de cassure dirait
« une clôture au-dessus d'une résistance annonce plus souvent la suite qu'un franchissement quelconque ». Ni l'un ni
l'autre ne serait une stratégie : il faudrait ensuite un test déclaré, coûts compris, sur données jamais vues ou en
direct (la période finale a déjà été lue une fois).

**4 essais** au registre. Causalité testée (falsifier les bougies après `t` ne change ni les niveaux ni les
événements connus à `t`) ; relecture indépendante avant l'exécution unique.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
