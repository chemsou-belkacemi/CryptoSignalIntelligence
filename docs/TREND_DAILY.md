# Suivi de tendance journalier (ensemble de Donchian) : pré-enregistrement

**Statut : DÉCLARÉ avant toute exécution (2026-10-02).** Étape 4 du plan de travail validé par le propriétaire :
reproduire, sur notre univers et avec notre modèle de frais, le seul résultat long-only de la littérature récente
que la recherche du 2026-10-02 a jugé solide (ensembles de tendance journaliers avec ciblage de volatilité,
Zarattini 2025). Commité avant le code des mesures et avant toute exécution ; toute modification ultérieure est
datée en bas.

**Budget : 2 essais exactement** (ENSEMBLE, ENSEMBLE_VOL), comptés dans le registre au moment de l'exécution.

## 0. Ce que ce lot n'est pas indépendant de

- Le lot 7 (FACTORS_WEEKLY) a testé des tendances hebdomadaires (28 et 56 jours) et une exposition selon la
  volatilité réalisée ; le lot 8 v2 (`docs/LONG_HORIZON.md`), un vote de tendance à 4, 12 et 26 semaines avec la
  volatilité prévue : tous `AUCUNE_PISTE` ou `NON_INTERESSANT`. Ce lot change la fréquence (journalière), la
  règle (canaux de Donchian avec sortie asymétrique) et le panier (10 paires). Il n'est pas indépendant : le
  Sharpe déflaté compte tous les essais du programme (719 avant celui-ci).
- L'effet attendu d'un filtre de tendance est une perte maximale réduite par une exposition réduite, déjà vu ;
  la référence statique à exposition égale est là pour le neutraliser.

## 1. Données

- Bougies 1 h du magasin long (depuis la cotation), journées 00:00–24:00 UTC valides si ≥ 20 bougies, comme
  FACTORS (`research/factors.py` : panneau, éligibilité). DEVELOPMENT seulement (jusqu'au 2025-06-30). La période
  finale n'est pas lue.
- Univers : les paires de l'univers de recherche (40) admises par le screening halal (liste exportée au lancement,
  inscrite dans l'essai) ; à chaque jour, éligibles si 85 journées valides sur 90 et médiane du volume sur 30 jours
  ≥ 1 M$ (point-in-time).
- Décision chaque jour à 00:00 UTC sur les clôtures journalières de la veille (bougie de 23:00), exécution à
  l'ouverture de la bougie 1 h de 01:00 du même jour (ordre limite supposé rempli à ce prix), valorisation
  quotidienne à 01:00. Première décision le 2019-04-01 (100 jours de canal disponibles), dernière le 2025-06-29.
- Coûts : modèle commun (frais 7,5 points de base par ordre + écart et glissement 2 points pour BTC et ETH,
  5 points pour les autres, par côté) ; scénario défavorable : frais 10 points, écart doublé, exécution un jour
  plus tard.

## 2. Règles FIXÉES (aucune grille, aucune optimisation)

| Élément | Valeur | Justification |
|---|---|---|
| Canaux | 20, 50 et 100 jours de clôtures | les trois horizons usuels des ensembles de Donchian (court, moyen, long) |
| Entrée | clôture du jour strictement au-dessus du plus haut des L clôtures précédentes | règle de Donchian classique |
| Sortie | clôture strictement en dessous du plus bas des L/2 clôtures précédentes (10, 25, 50) | sortie asymétrique, plus rapide que l'entrée ; convention courante |
| Position d'un canal | 1 entre une entrée et la sortie suivante, 0 sinon ; fermée toute journée sans clôture | long seul |
| Ensemble | moyenne des trois positions : 0, 1/3, 2/3 ou 1 | diversification temporelle, pas de choix d'horizon |
| Panier | BTC et ETH s'ils sont éligibles, puis les plus liquides éligibles, 10 paires au plus | « top des plus liquides », point-in-time |
| Poids (ENSEMBLE) | position de l'ensemble × 1/10 | parts égales, plafond 100 % |
| Poids (ENSEMBLE_VOL) | × min(1, 0,50 / σ_20) avec σ_20 la volatilité réalisée annualisée des 20 derniers rendements journaliers | même cible que le modèle A (50 %), volatilité réalisée (pas prévue : ce lot ne dépend pas du protocole de volatilité) |
| Bande | un actif n'est échangé que si son poids s'écarte de la cible de plus de 5 points ou s'il entre ou sort | limite les frais des rééquilibrages journaliers |

## 3. Références (même panier, frais inclus, même période)

- **STATIQUE** (référence du verdict, une par variante) : l'exposition moyenne de la variante, répartie à parts
  égales sur les membres du panier du jour (le reste en stablecoin, sans rendement) ; connue en fin de période,
  étalon, pas une stratégie.
- Information : buy-and-hold du panier (parts égales, rééquilibré chaque jour avec la bande), BTC conservé.

## 4. Mesures

Rendement annualisé, volatilité annualisée (rendements journaliers × √365), Sharpe (journalier × √365), Sharpe
déflaté (Bailey et López de Prado, N = essais du programme au moment de l'exécution), perte maximale et sa durée,
transactions, frais (% du capital initial), exposition moyenne, par validation annuelle (juillet → juin, mêmes
blocs que le lot 8) et sur toute la période ; central et défavorable.

## 5. Critères de décision (ceux du lot 8 §7, référence statique)

Un essai est **« intéressant »** si, en central ET en défavorable :
1. intervalle de l'écart de Sharpe avec sa référence statique (blocs circulaires de 56 jours tirés ensemble,
   20 000 tirages, niveau 1 − 0,025/2) entièrement au-dessus de 0 ET Sharpe déflaté ≥ 0,95 (quasi inatteignable
   avec 719 essais : critère secondaire) ; **ou**
2. rendement annualisé ≥ 80 % de celui de la référence (si positive) ET perte maximale ≤ 60 % de celle de la
   référence ET perte plus faible dans au moins 5 validations annuelles sur 7.

Verdicts : `INTERESSANT_RISQUE_AJUSTE`, `INTERESSANT_PERTE_REDUITE`, `NON_INTERESSANT`. « Intéressant » n'est pas
« validé » : seule la période finale réservée ou un suivi en direct pourrait confirmer.

## 6. Audit des fuites, avant tout résultat

Poids recalculés à 4 décisions tirées au hasard avec les seules bougies antérieures, puis avec un futur falsifié :
identiques ; mutation : un signal lu sur la clôture du lendemain doit changer les poids. Code commité exigé. Sans
audit réussi, aucun essai n'est enregistré.

## 7. Attendu et limites déclarées

- Attendu : `NON_INTERESSANT`. La littérature citée travaille sur un univers plus large, des coûts plus bas et
  une période plus favorable ; les rééquilibrages journaliers coûtent cher avec notre modèle de frais.
- Survivantes : univers choisi le 2026-10-01 ; « les plus liquides » d'aujourd'hui.
- Clôtures seulement (pas de plus hauts et plus bas intrajournaliers) ; ordres limites supposés remplis au prix de
  01:00 ; environ 2 300 décisions très autocorrélées (blocs de 56 jours).

## Historique

- 2026-10-02 : déclaré avant exécution ; aucun code de mesure, aucun résultat.
