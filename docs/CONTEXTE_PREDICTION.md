# Les données de contexte annoncent-elles la direction de BTC et ETH ? (déclaré le 2026-10-04, avant code et exécution)

Demande du propriétaire du 2026-10-04 (« avancer dans la prédiction ») : après environ 800 essais sur les bougies
seules sans direction démontrée, tester des informations **autres que le prix**, gratuites, téléchargées le même jour
(`CONTEXTE.md`). Code : `research/context_screen.py` ; tests : `tests/test_context_screen.py` ; commande :
`csi context-screen`. DEVELOPMENT seulement (jusqu'au 2025-06-30). Information seulement : aucun résultat ne change un
test en cours ni ne publie de signal.

## Les 7 variables (définitions fixées ici, aucune n'est ajustée ensuite)

Jour de décision `T` = 00:00 UTC (clôture de la journée `T − 1`). « Jour `d` » = journée UTC de la donnée. Retard de
publication : les données révisées ou publiées le lendemain (flux, stablecoins, ratios, Wikipédia) ne sont lues que
jusqu'au jour `T − 2` ; les primes, faites de bougies déjà closes, jusqu'au jour `T − 1`.

| Code | Variable | Calcul | Retard | Actif visé |
|---|---|---|---|---|
| `BTC_NETFLOW` | flux nets de BTC vers les plateformes | somme sur 7 jours de (entrées − sorties) ÷ stock sur les plateformes | `T − 2` | BTC |
| `ETH_NETFLOW` | idem pour ETH | idem | `T − 2` | ETH |
| `CB_PREMIUM` | prime Coinbase BTC | moyenne sur 7 jours de (clôture BTC-USD Coinbase ÷ clôture BTCUSDT Binance − 1) | `T − 1` | BTC |
| `KR_PREMIUM` | prime coréenne BTC | moyenne sur 7 jours de (clôture Upbit en wons ÷ wons par dollar BCE ÷ clôture Binance − 1) | `T − 1` | BTC |
| `STABLE_GROWTH` | croissance des stablecoins | encours du jour `T − 2` ÷ encours du jour `T − 9` − 1 | `T − 2` | BTC |
| `TOP_TRADERS` | positionnement des top traders BTC | moyenne sur 7 jours du ratio long/short des positions des top traders (archives Binance, dernière valeur du jour) | `T − 2` | BTC |
| `WIKI_ATTENTION` | attention du public | moyenne sur 7 jours des vues de la page Wikipédia « Bitcoin » | `T − 2` | BTC |

Une moyenne sur 7 jours exige au moins 5 jours présents. Le taux BCE d'un jour ouvré sert jusqu'au suivant (report vers
l'avant seulement).

**Mise à l'échelle sans regard sur le futur.** Chaque jour `T`, la valeur est classée parmi les valeurs des 365 jours de
décision précédents (au moins 180 présentes) : rang `p` entre 0 et 1. **Tiers haut** : `p ≥ 2/3` ; **tiers bas** :
`p ≤ 1/3`.

## Mesure

Rendement logarithmique de l'actif visé (clôtures Binance) de la décision à `T + H − 1` jours, pour **H = 1, 3 et
7 jours** : entrée à l'ouverture du jour `T`, prise égale à la clôture de `T − 1` (marché continu), sortie à la clôture
du jour `T + H − 1`. Seuls les jours dont la sortie est dans DEVELOPMENT comptent.

Pour chaque variable et chaque horizon : **écart = rendement moyen quand la variable est dans son tiers haut −
rendement moyen quand elle est dans son tiers bas**. IC par tirage de blocs de 56 jours calendaires (10 000 tirages,
graine 20261004), niveau **1 − 0,05/21** (7 variables × 3 horizons = **21 comparaisons**, 21 essais au registre) ; au
moins 100 jours dans chaque tiers et 20 blocs.

**Lecture** pour chaque comparaison :

- `HAUSSE_SI_HAUT` si l'IC de l'écart est entièrement au-dessus de 0 (variable haute → l'actif monte plus) ;
- `BAISSE_SI_HAUT` s'il est entièrement en dessous ;
- `RIEN` sinon.

Descriptif, hors verdict : rendement moyen de chaque tiers et de tous les jours ; nombre de jours ; corrélation de rang
entre `p` et le rendement ; signe de l'écart par année et sur chaque moitié de la période ; écart avec une entrée
retardée d'un jour (clôture de `T`). Un résultat non nul serait une **piste**, pas une stratégie : il faudrait ensuite un
test déclaré, coûts compris, en direct (la période finale a déjà été lue une fois). Le sens économique attendu n'entre
pas dans le verdict (test bilatéral).

## Non testé (déclaré)

Base futures/spot (40 jours seulement avant juillet 2025) ; ratios long/short des altcoins (30 jours) ; primes des
altcoins (étude séparée, en coupe, à déclarer plus tard) ; macro (taux, M2, S&P 500) ; données relevées seulement
depuis le 2026-10-04 (dominances, options, TVL).

Causalité testée (falsifier les données postérieures à la décision ne change aucune variable ni aucun rang) ;
relecture indépendante avant l'exécution unique.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
