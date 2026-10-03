# Portefeuilles hebdomadaires entre cryptos, à date (point 7 du plan) — déclaré le 2026-10-03 avant exécution

Code : `research/xsection.py`. Tests : `tests/test_xsection.py`. Commande : `csi xsection`.

**Pourquoi.** Le momentum transversal est la piste directionnelle la mieux documentée en crypto (le double momentum
avait raté de peu au lot 7, sur des survivantes). Le contrôle de l'ajustement a fait apparaître une seconde
hypothèse : les paires calmes finissent plus souvent devant la semaine suivante (classement), alors que la moyenne
arithmétique favorise les paires agitées. Ce protocole mesure les deux comme des **portefeuilles réels**, en
rendement arithmétique net, et sur l'**univers à date** (paires retirées comprises).

## Règles figées

- Bougies **journalières** de toutes les paires USDT (`data/pit/daily.parquet`, `UNIVERSE_PIT.md`), DEVELOPMENT, du
  premier lundi de 2019 à juin 2025 (≈ 340 semaines).
- Chaque **lundi** : univers = top 40 du mois (à date) ; signaux sur les clôtures jusqu'au dimanche inclus ; achat à la
  **clôture du lundi** (un jour de retard) et détention jusqu'à la clôture du lundi suivant ; poids égaux.
- **MOM_4S** : les 8 paires au plus fort rendement sur 28 jours. **DOUBLE_MOM_4S** : les mêmes, seulement celles dont le
  rendement est positif (le reste en liquidités). **CALMES** : les 8 paires à la plus faible volatilité journalière sur
  28 jours.
- **Référence** : la moyenne à poids égaux de toutes les paires de l'univers du moment.
- Coûts : 7,5 pb de frais + 5 pb de glissement par côté, sur la rotation (somme des variations de poids).
- Une paire retirée en cours de semaine est valorisée à sa dernière clôture connue (déclaré : peut être optimiste,
  la vente pouvant être impossible au moment du retrait).
- **Même mesure sur les 40 paires de recherche (survivantes)** : l'écart entre les deux lectures mesure le biais de
  survivance. **6 essais** (3 portefeuilles × 2 univers).

## Mesures et lecture déclarée

- Par portefeuille et univers : rendement hebdomadaire moyen net, Sharpe annualisé, perte maximale, excès sur la
  référence et son IC (Student, blocs de 56 jours), années positives, rotation.
- Un portefeuille « fait mieux que l'univers » si l'IC de son excès est au-dessus de 0 **sur l'univers à date** ; la
  lecture sur les survivantes ne sert qu'à mesurer le biais.
- Attendu : le momentum gagne surtout dans les grandes hausses et souffre des retournements ; sur l'univers à date,
  les paires mortes pèsent sur la référence comme sur les portefeuilles. Pour CALMES : rang meilleur mais moyenne
  plus faible ; l'excès arithmétique attendu est **négatif ou nul**, avec une perte maximale plus faible.

## Résultat (`XSEC-20261003T010820Z-bb356b`, programme : 783 essais)

339 semaines, du 2019-01-07 à juin 2025. Rendements hebdomadaires **nets** ; excès sur la moyenne de l'univers du
moment, IC à 95 % (Student, blocs de 56 jours).

**Univers à date** (la lecture qui décide) :

| Portefeuille | Moyenne / sem. | Sharpe | Perte max. | Excès / sem. | IC de l'excès | Années + | Rotation / sem. |
|---|---|---|---|---|---|---|---|
| MOYENNE (référence) | +0,82 % | 0,50 | −90,5 % | — | — | 5/7 | — |
| MOM_4S | +1,04 % | 0,62 | −92,0 % | +0,22 % | [−0,46 ; +0,91] | 4/7 | 0,80 |
| DOUBLE_MOM_4S | +1,11 % | 0,73 | −87,8 % | +0,29 % | [−0,44 ; +1,03] | 4/7 | 0,70 |
| CALMES | +0,89 % | 0,61 | −82,2 % | +0,07 % | [−0,46 ; +0,60] | 4/7 | 0,43 |

**Survivantes** (mesure du biais seulement) :

| Portefeuille | Moyenne / sem. | Sharpe | Perte max. | Excès / sem. | IC de l'excès |
|---|---|---|---|---|---|
| MOYENNE | +1,42 % | 0,88 | −82,2 % | — | — |
| MOM_4S | +1,78 % | 1,04 | −84,8 % | +0,36 % | [−0,07 ; +0,79] |
| DOUBLE_MOM_4S | +1,94 % | 1,27 | −61,0 % | +0,52 % | [−0,12 ; +1,16] |
| CALMES | +1,25 % | 0,86 | −81,0 % | −0,17 % | [−0,73 ; +0,38] |

**Lecture déclarée : aucun portefeuille ne fait mieux que l'univers à date** (les trois IC contiennent 0).

- **Biais de survivance mesuré** : la simple moyenne passe de +0,82 % à +1,42 % par semaine quand on ne garde que
  les paires encore cotées aujourd'hui, et la perte maximale du double momentum de −88 % à −61 %. Une étude faite
  sur les survivantes seules aurait vu un double momentum presque significatif (IC [−0,12 ; +1,16]) : c'est
  exactement le piège que l'univers à date devait écarter.
- **Momentum** : estimations positives sur les deux univers, mais l'intervalle est large et 4 années sur 7
  seulement sont positives. Rien d'exploitable démontré ; ce n'est pas une preuve d'absence non plus : la
  demi-largeur des intervalles (environ 0,7 % par semaine) dit qu'un excès plus petit que cela ne pouvait pas être
  vu sur 6,5 ans.
- **CALMES** : excès arithmétique nul, comme attendu, et perte maximale plus faible (−82 % contre −90 %). Le
  meilleur classement des paires calmes ne se transforme pas en gain moyen. Hypothèse close.
- **Tous ces portefeuilles perdent 82 à 92 % au pire moment** : investir en continu dans un panier d'altcoins
  reste un pari sur le marché entier, quelle que soit la sélection.

## Historique

- 2026-10-03 : déclaré avant toute exécution ; exécuté le même jour (6 essais, programme 783). Contrôle après coup :
  les mêmes chiffres sont retrouvés en relisant l'univers nettoyé (stablecoins et tokens à levier exclus), sans
  nouvel essai.
