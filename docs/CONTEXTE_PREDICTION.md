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

## Résultat (exécution unique, `CTXP-20261004T015026Z-30e516`, 2026-10-04, essais 803 à 823) : **RIEN** sur les 21

Écart de rendement de l'actif entre les jours où la variable est dans son tiers haut et ceux où elle est dans son tiers
bas (en %, horizon 7 jours ; IC au niveau 1 − 0,05/21) :

| Variable | Écart à 7 j | IC | Depuis 2020 | Années de même signe | Verdict |
|---|---|---|---|---|---|
| BTC_NETFLOW (entrées nettes de BTC) | −1,14 | [−4,25 ; +2,05] | −0,43 | 8 sur 9 négatives | RIEN |
| ETH_NETFLOW | −1,75 | [−5,23 ; +2,11] | −0,23 | 7 sur 9 négatives | RIEN |
| CB_PREMIUM | +1,43 | [−1,60 ; +4,44] | +1,39 | 4 sur 7 positives | RIEN |
| KR_PREMIUM | +0,54 | [−2,51 ; +4,02] | — | mitigé | RIEN |
| STABLE_GROWTH (depuis 2021-07) | +0,95 | [−1,91 ; +3,63] | +0,95 | 1 sur 3 positive | RIEN |
| TOP_TRADERS | −1,32 | [−5,43 ; +2,27] | −1,32 | 3 sur 4 négatives | RIEN |
| WIKI_ATTENTION | +2,89 | [−1,81 ; +7,60] | +3,01 | 4 sur 5 positives | RIEN |

À 1 et 3 jours : mêmes signes, intervalles contenant 0 partout.

**Lecture.** Aucune variable ne sépare démontrablement les bons jours des mauvais : les intervalles sont larges, parce
que BTC et ETH ne fournissent qu'une série chacun, dominée par 3 ou 4 cycles haussiers et baissiers. Les signes sont
pour la plupart ceux qu'on attend (BTC qui entre sur les plateformes → semaine plus faible ; prime Coinbase, attention
du public → semaine plus forte), mais rien ne dépasse le bruit au niveau déclaré, et les variables d'engouement
(Wikipédia, prime Coinbase) recoupent l'élan des prix déjà testé sans avantage net de coûts. La régularité la plus nette
est celle des **flux nets de BTC** (8 années sur 9 négatives à 7 jours), avec deux réserves : l'écart depuis 2020 est
petit (−0,4 %) et CoinMetrics étiquette les adresses des plateformes après coup ; une suite exigerait les relevés du
jour (RELEVE) sur plusieurs mois. Rien n'est branché.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
- 2026-10-04, relecture avant l'exécution (aucun écart de rendement calculé sur les données réelles ; seules les séries
  de contexte et les prix ont été regardés) :
  - **`STABLE_GROWTH`** : la série de DefiLlama est incomplète avant mi-2020 (0,06 Md$ au 2018-07-01, 4,2 Md$ au
    2020-01-01 alors que l'USDT seul dépassait 2 Md$ dès 2018 ; « croissances » hebdomadaires de ×273, −75 %, +100 %
    dues aux ajouts de couverture et aux migrations de chaîne). Données lues à partir du 2020-07-01, décisions à partir
    du 2021-07-01 (toutes les fenêtres de rang dans la période couverte) ; niveau de test inchangé (0,05/21) ;
  - **`CB_PREMIUM` et `KR_PREMIUM`** : avant 2020, elles contiennent l'écart USDT/USD (prime Coinbase de −5,4 à +6,2 %
    en 2018, −4,6 % lors de la décote de l'USDT en octobre 2018) et mesurent alors surtout le stress sur Tether ; un
    verdict non nul ne vaut piste que si l'écart depuis 2020 (`diff_since_2020_pct`, ajouté pour toutes les variables)
    a le même signe ; lire aussi l'entrée retardée d'un jour ;
  - **`BTC_NETFLOW` et `ETH_NETFLOW`** : CoinMetrics reconstruit l'historique avec les adresses de plateformes connues
    aujourd'hui (le stock est exactement la somme des flux) ; le retard couvre la publication, pas cet étiquetage après
    coup ; un résultat sur ces variables devra être confirmé sur les lignes RELEVE (relevé du jour, depuis le
    2026-10-04) avant toute suite ;
  - somme des flux sur 7 jours complets (pas de somme sur 5 jours ; aucun jour manquant dans la période) ;
  - tests renforcés (pic au jour d → première variation exactement au jour de décision attendu pour chaque variable,
    prix irréguliers, BCE sans week-end, sorties dans DEVELOPMENT par horizon, clôtures après le 2025-06-30 sans
    effet) ; empreintes sha256 des fichiers du magasin inscrites au rapport ;
  - à lire avec le programme : 823 essais après cette étude ; 6 variables sur 7 visent BTC et plusieurs mesurent
    l'engouement des particuliers (corrélées) : plusieurs succès simultanés comptent comme une seule piste ;
    `TOP_TRADERS` a un trou de données du 2021-12-30 au 2022-12-13 (champ vide dans les archives Binance).
