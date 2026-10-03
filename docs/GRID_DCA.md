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

## Historique

- 2026-10-03 : déclaré avant toute exécution.
