# Grille et DCA, stratégies sans prédiction (point 8 du plan) — déclaré le 2026-10-03 avant exécution

Code : `research/grid_dca.py`. Tests : `tests/test_grid_dca.py`. Commande : `csi grid-dca` (`--point-in-time` pour
l'univers à date).

**Pourquoi.** C'est la catégorie des bots du marché (grille, DCA) : elles ne prédisent pas le sens, elles vivent des
allers-retours du prix (grille) ou étalent l'entrée (DCA). Leur risque est connu : une grille garde les achats du
haut quand le marché baisse ; un DCA « moyenne à la baisse » une crypto qui ne remonte pas. Personne n'a mesuré chez
nous si elles font mieux que de simplement garder la crypto, ou que de rester en liquidités, après frais.

## Règles figées

- Bougies 1 h du magasin long, DEVELOPMENT, mois civils de janvier 2019 à juin 2025, mois avec au moins 95 % des
  bougies. Capital 1 par (paire, mois), remis à zéro chaque mois ; liquidation au marché à la clôture du mois.
- **Ordres limites remplis seulement si le prix les TRAVERSE** (plus bas < prix d'achat, plus haut > prix de vente),
  jamais au simple contact (règle tirée de la mesure sur les transactions, `TICKS.md`). Frais maker à chaque ordre :
  7,5 pb en central, 10 pb en défavorable ; liquidation au marché : + 3 pb (central) ou 8 pb (défavorable).

| Variante | Règle |
|---|---|
| G1_GRILLE_10 | achats à −2, −4, …, −10 % sous l'ouverture du mois (capital réparti en 5), chaque achat rempli place une vente un pas plus haut (+2 %), puis le niveau se reforme |
| G2_GRILLE_20 | idem à −4, −8, …, −20 %, pas de 4 % |
| D1_DCA_PALIERS | un quart du capital à l'ouverture du mois, puis un quart à chaque nouvelle baisse de 5 % sous le dernier achat (4 tranches au plus) |
| D2_DCA_PRISE_DE_GAIN | D1, et tout est vendu dès que le prix dépasse le coût moyen + 3 % ; le cycle recommence |

- **Référence** : « garder » = achat à l'ouverture du mois (limite, frais maker), vente à la clôture du mois.
- **4 essais** (une mesure par variante ; les deux scénarios de coûts et les deux univers sont des lectures de la
  même variante, l'univers à date n'étant pas un nouveau réglage).

## Mesures et lecture déclarée

- Par variante et scénario : rendement mensuel moyen et IC (Student, blocs calendaires de 90 jours), écart à
  « garder » et son IC, pire mois, écart-type mensuel (la variante contre « garder »), ordres par mois, exposition
  maximale moyenne ; ventilation **descriptive** par régime du mois (BTC en hausse > 5 %, en baisse < −5 %, plat),
  lu après coup, qui ne sert à aucune décision.
- **Lecture** : une variante « fait mieux que garder » si l'IC de l'écart est au-dessus de 0 en central ET en
  défavorable ; « fait mieux que les liquidités » si l'IC de son rendement est au-dessus de 0. Le seul usage attendu
  honnête est le **risque** : une grille ou un DCA réduisent l'exposition moyenne, donc la perte du pire mois, au prix
  d'un rendement moindre en hausse. Ce n'est un avantage que si l'écart de rendement est plus petit que ce qu'une
  simple exposition réduite de même taille aurait coûté, ce qui est lu à partir de l'exposition moyenne.
- **Univers** : d'abord les 40 paires de recherche (survivantes) puis, dès qu'il est construit, le top 40 **à date**
  (paires retirées comprises) : le DCA est la stratégie la plus exposée au biais de survivance (il achète davantage
  ce qui baisse, y compris ce qui finit retiré de la cote).

## Résultats sur les 40 paires de recherche (`GRID-20261003T010525Z-cb125c`, 4 essais, programme 777)

2 347 paire-mois (40 paires, janvier 2019 à juin 2025). « Garder » : +6,78 % par mois en moyenne (écart-type 45,6 %,
pire mois −67 %) — moyenne gonflée par la survivance, voir la lecture à date.

| Variante | Rendement mensuel (central) [IC] | Écart à « garder » [IC] | Écart-type | Pire mois | Rendement / écart-type | Ordres par mois |
|---|---|---|---|---|---|---|
| Garder | +6,78 % | — | 45,6 % | −67,0 % | 0,149 | 2 |
| G1 grille ±10 % | +0,52 % [−2,08 ; +3,12] | −6,26 % [−13,88 ; +1,36] | 13,1 % | −57,3 % | 0,040 | 44 |
| G2 grille ±20 % | +0,88 % [−1,04 ; +2,81] | −5,90 % [−14,07 ; +2,27] | 10,4 % | −54,7 % | 0,085 | 18 |
| D1 DCA par paliers | +2,60 % [−2,49 ; +7,68] | −4,19 % [−9,43 ; +1,05] | 24,8 % | −64,3 % | 0,105 | 3 |
| D2 DCA + prise de gain | +1,61 % [−3,39 ; +6,62] | **−5,17 % [−9,66 ; −0,68]** | 20,7 % | −57,8 % | 0,078 | 35 |

Par régime du mois (descriptif, central), écart à « garder » : en **baisse** (702 paire-mois) +4 à +14 % (les grilles
perdent bien moins) ; en **hausse** (1 133) −12 à −23 % (elles ratent la hausse) ; **plat** (512) −0,4 à +4 %.

Lecture (règles déclarées) :
- **Aucune variante ne fait mieux que garder**, et aucune ne fait démontrablement mieux que les liquidités (tous les
  intervalles de rendement couvrent 0). Le DCA avec prise de gain fait démontrablement moins bien que garder.
- **Elles réduisent le risque, mais moins bien qu'une position réduite.** Leur rendement par unité d'écart-type
  (0,04 à 0,11) est plus faible que celui de « garder » (0,15) : garder une plus petite position en crypto donne,
  pour le même risque, un meilleur rendement qu'une grille ou un DCA. Leurs pires mois restent proches de celui de
  « garder » (−55 à −64 % contre −67 %) : une baisse continue piège la grille comme le DCA.
- Ce qu'elles font réellement : **parier sur un marché plat ou baissier** (gain en baisse, perte en hausse). Sur une
  période globalement haussière (2019-2025), c'est perdant. Ce n'est pas un avantage, c'est un choix de régime, et nos
  prévisions ne savent pas prévoir le régime.

## Historique

- 2026-10-03 : déclaré avant toute exécution.
- 2026-10-03 : exécuté sur les survivantes (`GRID-20261003T010525Z-cb125c`) ; lecture à date à venir.
