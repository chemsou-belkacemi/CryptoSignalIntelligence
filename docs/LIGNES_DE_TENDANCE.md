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

- **Paires** : les 214 paires du recensement de l'univers à date (`UNIVERSE_PIT.md` : paires USDT cotées et retirées,
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

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution ; téléchargement des minutes lancé le même jour.
