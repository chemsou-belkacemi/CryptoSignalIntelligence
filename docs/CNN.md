# CNN sur images de graphiques — un seul essai (déclaré le 2026-10-03, avant toute exécution)

Mission du 2026-10-03, phase 10.2. Inspiré de Jiang, Kelly et Xiu (« (Re-)Imag(in)ing Price Trends », Journal of
Finance, 2023), adapté au spot long seulement. Code : `research/cnn_charts.py` ; tests :
`tests/test_cnn_charts.py` ; commande : `csi cnn-charts`. DEVELOPMENT seulement ; PC, sans service payant.
**Un essai** au registre ; aucune recherche d'architecture ni d'hyperparamètres.

## Données et images

- Bougies **journalières** (journées UTC complètes, 24 heures) agrégées à partir des bougies 1 h du magasin long
  (`research/long_history.py`), qui contient les paires passées par le top 40 à date (`UNIVERSE_PIT.md`) ; une paire
  sans historique 1 h est sautée (8 paires, déclarées dans `UNIVERSE_PIT.md`).
- Univers de chaque semaine : le **top 40 à date du mois** (`data/pit/membership.parquet`, exclusions structurelles et
  halal comprises, paires retirées de la cote comprises).
- **Une image par actif et par semaine** : les 20 journées qui finissent le dimanche (clôture du dimanche incluse),
  sans aucun indicateur. Format fixe **64 × 60 pixels**, noir et blanc : 3 colonnes par journée (ouverture à gauche,
  barre plus bas–plus haut au milieu, clôture à droite) ; prix sur les 51 lignes du haut, mis à l'échelle du plus bas
  et du plus haut des 20 journées ; une ligne vide ; volume (en USDT) sur les 12 lignes du bas, à l'échelle du plus
  gros volume des 20 journées. Une image n'est faite que si les 20 journées sont complètes.

## Cible et découpage temporel

- **Cible** : rendement de la semaine suivante, de la clôture du lundi à celle du lundi suivant (un jour de retard sur
  l'image, comme dans `XSECTION.md`), **supérieur à la moyenne de l'univers** cette semaine-là (classement entre
  actifs, pas direction absolue) : 1 ou 0.
- **Univers mesuré** d'une semaine : les paires du top 40 du mois qui ont une image complète ; la moyenne de la cible et
  le panier portent sur ces paires-là (précisé avant exécution).
- **Entraînement** : semaines du 2019-01-07 (premier lundi de 2019, comme `XSECTION.md`) à celle dont la cible finit
  au plus tard le 2022-12-26 (précisé avant exécution). Arrêt précoce sur un tirage aléatoire de
  30 % des **semaines** d'entraînement (graine fixe), comme dans l'article.
- **Validation (mesure)** : semaines dont l'image commence après la fin de la dernière cible d'entraînement, avec
  4 semaines d'écart (première semaine mesurée : 2023-01-30), jusqu'à la fin de DEVELOPMENT (juin 2025). Aucun
  chevauchement entre une fenêtre d'image ou une cible de validation et une cible d'entraînement ; le code le vérifie
  et refuse de tourner sinon. Aucune donnée postérieure au dimanche n'entre dans une image (testé).

## Modèle (fixé, inspiré de l'article, version 20 jours)

Trois blocs [convolution 5 × 3 (64, 128 puis 256 canaux), normalisation par lot, LeakyReLU (pente 0,01), max-pooling
2 × 1], puis dropout 0,5 et une couche linéaire à 2 sorties ; initialisation de Xavier ; Adam, taux 1e-5, lots de 128,
entropie croisée ; 50 époques au plus, arrêt après 2 époques sans baisse de la perte d'arrêt précoce ; graine
20261003 ; un seul modèle (l'article en moyennait 5 : déclaré).

## Stratégie et mesure

Chaque lundi de la période de validation : détenir à poids égaux les **8 actifs** de l'univers à la plus forte
probabilité « au-dessus de la moyenne », achat à la clôture du lundi, vente au lundi suivant ; coûts du modèle de
`XSECTION.md` (7,5 pb de frais + 5 pb de glissement par côté, sur la rotation). Références : le **panier** de
l'univers à poids égaux (« garder ») et la **référence statique** (allocation au panier égale à l'exposition moyenne
de la stratégie : toujours investie, donc égale au panier ; déclaré).

Mesures : rendement hebdomadaire net, Sharpe, perte maximale, **excès sur le panier** et son IC (Student, blocs de
56 jours), années positives, rotation ; qualité de classement (exactitude, AUC, corrélation de rang des probabilités
avec les rendements), à titre descriptif.

**Lecture déclarée** : `PASSE` si l'IC de l'excès hebdomadaire sur le panier est entièrement au-dessus de 0 ; sinon
`NE_PASSE_PAS`. Un `PASSE` n'est qu'une piste : suivi en direct sur la période suivante avant toute conclusion (la
période finale a déjà été lue une fois).

## Historique

- 2026-10-03 : déclaré avant toute exécution.
- 2026-10-03 : code écrit (`research/cnn_charts.py`, extra `cnn` : torch 2.14.1, version CPU) ; précisions avant
  exécution : première semaine d'entraînement, univers mesuré = paires imagées.
