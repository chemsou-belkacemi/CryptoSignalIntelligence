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

## Historique

- 2026-10-03 : déclaré avant toute exécution.
