# Lot 8 — Horizons longs et ciblage de volatilité : pré-enregistrement

**Statut : DÉCLARÉ avant toute exécution, validé par le propriétaire le 2026-10-02.** Commité avant le code des
modèles et avant toute exécution ; toute modification ultérieure est datée en bas.

Demande du propriétaire (2026-10-02) : changer d'approche après 713 essais. Prédire la direction à court terme
à partir de l'OHLCV ne marche pas ici, les frais détruisent les stratégies fréquentes, et la volatilité est la
seule chose prévisible (VOLATILITY_FORECAST : PREVISION_UTILE). D'où : horizons longs, peu de rotations,
exploitation de la prévision de volatilité. Spot, long ou stablecoin, sans levier, **aucun ordre**.

**Budget : 3 essais exactement** (A, A + filtres, B). Programme : 713 → 716.

## 0. Ce que ce lot n'est PAS indépendant de (à lire avant tout résultat)

- Le lot 7 (FACTORS_WEEKLY, AUCUNE_PISTE) a déjà testé une tendance par paire (TSMOM 28 et 56 jours), une
  exposition selon la volatilité réalisée (VOLMAN) et un portefeuille faible volatilité hebdomadaire (LOWVOL_K5),
  sur la même période. Le modèle A combine des idées proches : la différence est la volatilité **prévue** (au
  lieu de réalisée), un horizon de tendance plus long (12 semaines), un univers restreint et une bande de
  tolérance. Le modèle B diffère de LOWVOL_K5 par la détention de 6 mois. Ces essais ne sont donc pas
  indépendants des précédents ; le Sharpe déflaté compte les 716 essais.
- Le lot 7 a montré que les filtres de tendance **réduisent la perte maximale** (−48 % à −71 % contre −82 %)
  sans gain de Sharpe démontré. Le critère « drawdown nettement plus faible » ci-dessous peut donc être rempli
  par un mécanisme déjà observé : ce serait une confirmation, pas une découverte.

## 1. Données communes

- Bougies 1 h du magasin long (depuis la cotation), journées 00:00–24:00 UTC valides si ≥ 20 bougies, comme
  FACTORS (`research/factors.py` : panneau, éligibilité, simulation hebdomadaire). DEVELOPMENT seulement
  (jusqu'au 2025-06-30). La période finale n'est pas lue.
- Décision le lundi 00:00 UTC, exécution à l'ouverture de la bougie 1 h de 01:00 (ordre limite supposé rempli à
  ce prix), valorisation quotidienne à 01:00.
- **Volatilité prévue** : prévisions HORS ÉCHANTILLON du protocole VOLATILITY_FORECAST (réajustement mensuel
  sur le seul passé, `research/volatility.walk_forward`), horizon **7 jours**, modèle retenu (HAR + BTC). Elles
  commencent au 2019-01-01 : **première décision le lundi 2019-01-07**, dernière le 2025-06-23 (blocs de 7
  validations annuelles comme FACTORS : 2019-01 → 2019-06 puis juillet → juin).
- **Coûts** (demande du propriétaire) : frais 7,5 points de base par ordre (BNB) + écart et glissement selon la
  liquidité : 2 points pour BTC et ETH, 5 points pour les autres paires, par côté. Scénario défavorable : frais
  10 points (sans BNB), écart et glissement doublés, exécution un jour plus tard.

## 2. Modèle A — tendance long / stablecoin avec ciblage de volatilité

Paramètres FIXÉS (aucune grille, aucune optimisation) :

| Élément | Valeur | Justification |
|---|---|---|
| Univers | BTC, ETH + les 3 autres paires les plus liquides de l'univers de recherche à la date (médiane du volume journalier sur 30 jours), éligibles comme FACTORS (≥ 85 journées valides sur 90, ≥ 1 M$/jour) et disposant d'une volatilité prévue à la date (le modèle exige 400 jours d'historique) ; sinon la suivante dans l'ordre de liquidité. BTC ou ETH sans prévision : non détenu cette semaine-là | demande : univers restreint aux plus liquides ; 5 actifs au plus |
| Signal | rendement des **84 jours** (12 semaines) > 0 → actif détenu, sinon sa part en stablecoin | « plusieurs semaines à quelques mois » ; 3 mois est l'horizon intermédiaire classique des CTA (Moskowitz, Ooi, Pedersen 2012 ; Hurst, Ooi, Pedersen 2017) ; 28 et 56 jours déjà testés au lot 7 |
| Taille | poids de base 1/5 par actif ; poids = 1/5 × min(1, σ_cible / σ̂ᵢ), σ̂ᵢ = volatilité prévue à 7 jours (annualisée) | inversement proportionnel à la volatilité prévue, plafond 100 % au total (pas de levier) |
| σ_cible | médiane des volatilités prévues de BTC sur toutes les décisions passées (au moins 26, sinon σ_cible = σ̂ de BTC) | même règle causale que VOLMAN (lot 7), déclarée sans regarder les résultats |
| Rééquilibrage | hebdomadaire ; un actif n'est échangé que si son poids cible s'écarte de son poids actuel de plus de **5 points** du portefeuille, ou si le signal change de sens | bande de tolérance contre les micro-ajustements ; 5 points ≈ 25 fois le coût aller-retour |

## 3. Variante A + filtres (UN seul essai)

1. **Exclusion pré-unlock : NON IMPLÉMENTÉE.** Aucune source fiable de calendrier historique des unlocks
   n'existe dans le projet ; les sources publiques connues sont payantes ou incomplètes pour le passé. Comme
   l'univers de A est BTC, ETH et les plus liquides, l'effet attendu serait de toute façon faible. Signalé.
2. **Funding extrême** : moyenne du financement du perpétuel USDⓈ-M de l'actif sur les 7 derniers jours
   (données publiques, connues à la décision, `derivatives/`). Si elle dépasse **0,05 % par 8 h** (5 fois le
   taux de référence de Binance, 0,01 %), le poids de l'actif est **divisé par deux** pour la semaine. Avant la
   cotation du perpétuel (BTC : septembre 2019), pas de filtre. L'historique du financement sera complété depuis
   la cotation de chaque perpétuel (archives officielles) avant l'exécution.

## 4. Modèle B — portefeuille basse volatilité long-only

| Élément | Valeur | Justification |
|---|---|---|
| Univers | paires éligibles comme FACTORS (liquides à la date) | demande |
| Sélection | tous les **26 lundis** (≈ 6 mois) : les **5** paires éligibles de plus faible volatilité réalisée journalière sur **180 jours** (au moins 170 journées valides) | « historique de 6 à 12 mois », « portefeuille concentré » ; la fenêtre basse de l'intervalle pour garder assez de paires éligibles en 2019 |
| Détention | 26 semaines, parts égales, sans rééquilibrage entre deux sélections | « détention de 6 à 12 mois » |
| Stop | une position est vendue si sa clôture journalière passe **25 %** sous son prix d'achat ; sa part reste en stablecoin jusqu'à la sélection suivante | « stop-loss simple de protection » ; 25 % ≈ un mouvement de 2 à 3 écarts-types mensuels d'une crypto peu volatile |
| Rien d'éligible | 100 % stablecoin | demande |

## 5. Références (même panier, frais inclus, même période)

- **A et A + filtres** : buy-and-hold du même panier (les 5 actifs éligibles de A à chaque date, parts égales,
  rééquilibré chaque lundi seulement quand un actif entre ou sort du panier), mêmes coûts.
- **B** : buy-and-hold à parts égales de toutes les paires éligibles, renouvelé aux mêmes dates de sélection
  (tous les 26 lundis), mêmes coûts ; à titre d'information, le même panier de 5 que B sans stop.
- Information : BTC acheté et conservé.

## 6. Mesures rapportées

Rendement annualisé, volatilité annualisée, Sharpe (rendements hebdomadaires, taux sans risque nul), **Sharpe
déflaté**, perte maximale, durée de la plus longue perte (jours entre un sommet et son dépassement), nombre de
transactions, frais totaux payés (en % du capital initial), exposition moyenne. Par validation annuelle et sur
toute la période ; scénario central et défavorable.

**Sharpe déflaté** (Bailey et López de Prado, 2014) : probabilité que le vrai Sharpe dépasse SR₀, le maximum
attendu par hasard parmi N = 716 essais : SR₀ = √V × ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e))), γ constante
d'Euler-Mascheroni, V = variance de l'estimateur du Sharpe hebdomadaire sous l'hypothèse nulle (1/T, T nombre
de semaines), corrigée de l'asymétrie et de l'aplatissement des rendements du modèle. Les Sharpe des 713 essais
passés ne sont pas comparables entre eux (horizons, unités différents) : V n'est pas estimée sur eux.

## 7. Critères de décision (fixés ici)

Un modèle est **« intéressant »** si, en scénario central ET défavorable :
1. **Meilleur ajusté au risque** : intervalle de l'écart de Sharpe avec sa référence (rééchantillonnage apparié
   par blocs de 8 semaines, 50 000 tirages, niveau 1 − 0,025/3) entièrement au-dessus de 0, ET Sharpe déflaté
   ≥ 0,95 ;
   **OU**
2. **Rendement comparable, perte nettement plus faible** : rendement annualisé au moins égal à 80 % de celui de
   la référence (si celle-ci est positive) ET perte maximale au plus égale à 60 % de celle de la référence ET
   perte maximale plus faible dans au moins 5 validations annuelles sur 7.

Verdicts enregistrés : `INTERESSANT_RISQUE_AJUSTE`, `INTERESSANT_PERTE_REDUITE` ou `NON_INTERESSANT`. Comme
pour tous les lots, « intéressant » n'est pas « validé » : la confirmation ne pourrait venir que de la période
finale réservée ou d'un suivi en direct.

## 8. Audit des fuites, avant tout résultat

Réutilise celui de FACTORS (décisions recalculées sur bougies tronquées et futur falsifié, mutation détectée) ;
en plus : les prévisions de volatilité utilisées à une décision doivent provenir d'un modèle ajusté avant cette
décision (contrôle du mois de réajustement), et le financement doit être connu à la décision (latence déclarée).
Code commité exigé (même refus que FACTORS). Sans audit réussi, aucun essai n'est enregistré.

## 9. Limites déclarées

- Survivantes : univers de recherche choisi le 2026-10-01 ; les paires « les plus liquides » d'aujourd'hui.
- Peu de données : environ 336 semaines, deux grands cycles ; les intervalles seront larges.
- Ordres limites supposés remplis au prix de 01:00, sans impact de marché.
- Unlocks non filtrés (pas de source historique fiable).
- Non-indépendance avec le lot 7 (§0).

## 10. Version 2 (mission du propriétaire du 2026-10-02, phase 2) — amendement avant toute exécution

La version 1 n'a jamais été exécutée (aucun essai consommé : relecture indépendante en cours, puis mission
du propriétaire changeant les règles). Les décisions ci-dessous remplacent les points correspondants des §2 à §7 ;
le reste est inchangé. Commité avant la première exécution.

- **Ce qui diffère du lot 7.** Le lot 7 (VOLMAN) dimensionnait l'exposition sur la volatilité **réalisée** ; A
  utilise la volatilité **prévue** à 7 jours (VOLATILITY_FORECAST, seul résultat validé), une cible fixe et un
  vote de tendance à trois horizons. A est donc lancé.
- **Signal de A** : vote de trois horizons FIXÉS, 4, 12 et 26 semaines (28, 84 et 182 jours) ; un actif est
  détenu si au moins **2 des 3** rendements passés sont strictement positifs (un horizon sans historique ne vote
  pas). Sans optimisation.
- **Volatilité cible de A : 50 % par an, FIGÉE.** Proche de la volatilité historique de BTC : le modèle reste
  presque entièrement investi en période calme et réduit son exposition en période agitée. Poids = 1/5 ×
  min(1, 0,50 / σ̂ᵢ) ; plafond d'exposition totale 100 % (5 actifs × 1/5, jamais de levier). Remplace la médiane
  causale des σ̂ de BTC.
- **Univers** : paires de l'univers de recherche **admises par le screening halal** (décisions du propriétaire
  dans CSI, `config/halal_screen.yaml`, liste exportée au lancement et inscrite dans l'essai), éligibles à chaque
  date par leur volume passé (point-in-time, comme FACTORS) ; sans stablecoins, tokens adossés, wrapped ni à
  levier.
- **Deuxième référence, obligatoire, pour A et B : allocation STATIQUE** au même panier, égale à l'exposition
  moyenne du modèle sur la période (le reste en stablecoin, sans rendement), à parts égales entre les membres,
  échangée seulement quand la composition change ; mêmes frais. C'est la référence du verdict. Le buy-and-hold
  du panier (§5) reste rapporté pour information. L'exposition moyenne est connue en fin de période : c'est un
  étalon, pas une stratégie réalisable.
- **Filtre funding de A** : la part des couples (décision, actif) où il agit est comptée avant toute
  exécution ; s'il s'active **moins de 2 % du temps**, l'essai « A + filtres » n'est **pas lancé** (2 essais au
  lieu de 3) et la part est rapportée.
- **B** : mêmes exclusions ; après un stop à −25 %, la part reste en stablecoin **jusqu'au rééquilibrage
  suivant** (déjà la règle du §4) ; le nombre de rééquilibrages indépendants (sélections tous les 26 lundis) est
  rapporté.
- **Critère réaliste** : « perte réduite » (§7, point 2) **contre la référence statique**. Le critère ajusté au
  risque (§7, point 1) exige un Sharpe déflaté ≥ 0,95 : avec 716 essais et plus, c'est quasi inatteignable ; il
  reste calculé et rapporté, sans être le critère attendu.
- **Suivi en direct** après le backtest : A (et B s'il est retenu « intéressant »), décisions hebdomadaires
  simulées et valorisation quotidienne, 3 mois. Sur 3 mois, peu de transactions : ce suivi vérifie le
  **comportement réel** du modèle, il ne le VALIDE pas ; le rapport le dit.
- **Audit des fuites** : inchangé, avec une mutation renforcée (σ̂ de la semaine suivante, décuplé).

**Budget : 3 essais au plus** (A, A + filtres si le funding s'active assez, B), comptés dans le registre au
moment de l'exécution (716 essais avant, selon le registre).

## 11. Résultats de la version 2 (essai LONG-20261002T155625Z-967d85, 2026-10-02)

Exécuté une fois, après audit des fuites réussi (mutation détectée), code commité (1c60f86), univers : les 40 paires
de recherche, toutes admises par le screening ; 338 décisions du 2019-01-07 au 2025-06-23 ; **3 essais consommés
(programme : 716 → 719)**. Le filtre funding s'active 5,6 % du temps : l'essai « A + filtres » a été lancé. B a
fait 13 sélections.

| Modèle (central) | Rendement/an | Vol. | Sharpe | Sharpe déflaté | Perte max | Transactions | Frais | Exposition |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | +21,5 % | 29 % | 0,81 | 0,12 | −41 % | 425 | 10,5 % | 32 % |
| A + funding | +20,6 % | 28 % | 0,81 | 0,12 | −42 % | 453 | 10,3 % | 31 % |
| STATIQUE A (référence) | +18,4 % | 26 % | 0,78 | 0,12 | −46 % | 498 | 3,6 % | 30 % |
| Buy-and-hold du panier (information) | +36,7 % | 78 % | 0,79 | 0,13 | −88 % | 498 | 20,7 % | 94 % |
| B | +72,7 % | 76 % | 1,08 | 0,33 | −69 % | 105 | 23,4 % | 100 % |
| STATIQUE B (référence) | +79,8 % | 83 % | 1,10 | 0,35 | −72 % | 95 | 26,1 % | 100 % |
| BTC conservé (information) | +66,1 % | 61 % | 1,14 | 0,40 | −77 % | 1 | 0,1 % | 100 % |

**Verdicts : A, A + funding et B sont `NON_INTERESSANT`** face à leur référence statique, en central comme en
défavorable :
- A : écart de Sharpe +0,04, intervalle [−0,58 ; +0,64] (contient 0) ; perte max −41 % contre −46 % : pas réduite
  à 60 % de celle de la référence ; 5 validations annuelles sur 7 plus favorables. Le rendement est comparable
  (121 % de la référence) : A fait ce que fait son exposition moyenne, pas mieux.
- A + funding : même tableau (écart +0,03, [−0,62 ; +0,66]).
- B : écart de Sharpe −0,02, [−0,30 ; +0,28] ; perte −69 % contre −72 %. Le stop à −25 % n'a pas réduit la
  perte ; B sans stop fait légèrement mieux que B.
- Face au buy-and-hold du panier (information), A réduit la perte max de −88 % à −41 % : c'est le mécanisme déjà
  observé au lot 7 (une exposition réduite réduit la perte), que la référence statique reproduit à 30 %
  d'exposition. Aucune découverte.

Conséquences : **aucun modèle n'est « intéressant » ; B n'est pas retenu pour le suivi en direct**. Le suivi en
direct de A (test F5_MODELE_A, docs/FORWARD_TESTS.md) vérifie son comportement réel et l'effet du feu tricolore
(phase 8) ; il ne valide rien, et l'allocation statique y est jouée à l'exposition moyenne de A ici mesurée
(32,25 %). Le Sharpe déflaté reste très bas (0,12 à 0,35 avec 719 essais) : comme écrit avant l'exécution, il
n'est pas un critère atteignable.

## Historique

- 2026-10-02 : proposition, en attente de validation ; aucun code, aucun résultat.
- 2026-10-02 : validé par le propriétaire ; précision avant tout code : un actif de A doit avoir une volatilité
  prévue à la date (400 jours d'historique) ; il est sinon remplacé par le suivant le plus liquide.
- 2026-10-02 : version 2 (§10) écrite d'après la mission du propriétaire, avant toute exécution ; la version 1 n'a
  consommé aucun essai.
