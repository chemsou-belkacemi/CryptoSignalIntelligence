# Données de contexte gratuites (phase 1.3, ajout au plan du 2026-10-03)

Sources recensées et vérifiées le 2026-10-03, **validées par le propriétaire le 2026-10-04** (socle complet, ajout de
`/futures/data/basis` à `data/http.py` ; Farside reporté ; Wikipédia à la place de Google Trends). Code :
`src/crypto_signal_intelligence/context/` ; magasin : `data/context/<série>.parquet` ; commandes : `csi context-backfill`
(historique, une fois) et `csi context-status` ; relevé quotidien dans la surveillance, une fois par jour après
03:00 UTC.

**Information seulement.** Aucune de ces données n'influence un test en cours, une décision ou un signal. Un futur
test qui voudrait s'en servir doit le déclarer dans son pré-enregistrement, avant de démarrer.

## Deux sortes de lignes

- `HISTORIQUE` : téléchargé après coup. Valeurs **telles que publiées aujourd'hui** : une série révisée (M2, flux
  CoinMetrics « flash ») y apparaît corrigée, ce qu'on ne savait pas le jour même. Un test sur l'historique doit donc
  appliquer un **retard de publication prudent** (M2 : au moins 45 jours après la fin du mois ; CoinMetrics : J+2 ;
  FRED quotidien : J+1 ; DefiLlama : J+1) et le déclarer.
- `RELEVE` : valeur vue le jour du relevé, **jamais réécrite** (la première vue gagne). C'est la seule lecture
  point-in-time d'une série révisée ; elle commence le 2026-10-04.

## Séries

| Série | Contenu | Clés | Historique gratuit | Source (conditions) |
|---|---|---|---|---|
| `flows` | entrées, sorties, stock sur les plateformes (unités natives) | btc, eth | depuis 2011 | CoinMetrics community (CC BY-NC, attribution ; version gratuite : BTC et ETH seulement) |
| `market_global` | capitalisation totale, dominances BTC / ETH / USDT, TOTAL2, TOTAL3, volume | global | **aucun** (payant) : relevé seulement | CoinGecko (attribution) ; ~21 800 actifs, total différent de TradingView |
| `market_categories` | capitalisation et volume : IA, agents IA, mèmes, layer 1, DeFi, stablecoins | catégorie | **aucun** : relevé seulement | CoinGecko |
| `options` | ratio puts/calls (intérêt ouvert, volume), max pain et intérêt ouvert des 3 prochaines échéances (> 24 h) | BTC, ETH | **aucun** (payant) : relevé seulement | Deribit public |
| `basis` | base, taux, taux annualisé, prix du futur et de l'indice | BTCUSDT / ETHUSDT × trimestre courant, suivant, perpétuel | 500 jours | Binance futures public |
| `ratios` | ratio long/short des comptes et des positions des top traders, part acheteuse | paires halal avec perpétuel | 30 jours (API) | Binance futures public |
| `ratios_archive` | intérêt ouvert, ratios (comptes, top traders), ratio des volumes takers : dernière ligne de chaque jour | BTCUSDT, ETHUSDT | depuis 2020-09 | archives publiques Binance |
| `binance_daily` | clôture et volume en devise de cotation, journées UTC | paires halal, ETHBTC, PAXGUSDT (or tokenisé) | complet | Binance spot public |
| `coinbase` | clôture BASE-USD et volume | paires halal cotées sur Coinbase | depuis 2017 | Coinbase Exchange public |
| `upbit` | clôture KRW-BASE et valeur échangée en wons | paires halal cotées sur Upbit | plusieurs années | Upbit public |
| `ecb` | taux de référence (1 EUR = x) du won et du dollar | KRW, USD | depuis 2017 | BCE |
| `protocols` | revenus et frais journaliers (USD) | 15 jetons de protocole (table `fetch.DEFILLAMA`, vérifiée) | depuis le lancement | DefiLlama |
| `protocols_tvl` | TVL actuelle (USD) | mêmes jetons | **aucun** au quotidien (fichiers de 10 Mo) : relevé seulement | DefiLlama |
| `stablecoins` | stablecoins indexés sur le dollar en circulation | all | plusieurs années | DefiLlama |
| `macro` | taux à 10 ans du Trésor américain ; M2 mensuel corrigé (= M2SL) et hebdomadaire non corrigé (= WM2NS) ; clôture de l'ETF SPY (approximation du S&P 500) | UST10Y, M2SL, WM2NS, SPY | taux depuis 1990, M2 depuis 1959, SPY 10 ans | Trésor américain, Fed (publication H.6), Nasdaq |
| `fred` | (ancienne source, conservée dans le code) | — | — | FRED : le téléchargement CSV ne répond plus depuis le 2026-10-02 |
| `gold_gld` | clôture de l'ETF GLD (approximation de l'or) | GLD | depuis 2017 | Nasdaq public |
| `wikipedia` | vues quotidiennes (filtre « utilisateurs », quelques robots passent) | Bitcoin, Cryptocurrency | depuis 2015-07 | Wikimedia (User-Agent conforme à leur règle des robots) |

Primes calculées (`context/views.py`) :
- **Prime Coinbase** = clôture Coinbase BASE-USD / clôture Binance BASEUSDT − 1 (1 USDT compté pour 1 USD : écart
  réel de l'ordre de 0,1 %).
- **Prime coréenne** = (clôture Upbit en wons / wons par dollar BCE) / clôture Binance − 1 ; taux BCE reporté
  jusqu'au jour ouvré suivant.

## Reporté (pas de source gratuite fiable)

- Flux ETF Bitcoin et Ether (Farside : droits réservés, refusé par le propriétaire le 2026-10-04 ; SoSoValue : clé).
- Réserves de stablecoins sur les plateformes (CoinMetrics refuse sans abonnement ; DefiLlama : 28 à 43 Mo par
  plateforme et seulement les portefeuilles déclarés) : **reporté, décision du propriétaire du 2026-10-04**.
- Google Trends (pas d'API, cookie exigé, robots refusés) : remplacé par les vues Wikipédia.
- Gap CME (site CME interdit la lecture automatique, Yahoo non officiel ; le contrat se négocie désormais aussi le
  week-end : la notion de gap n'a plus de sens net).
- Cotation de l'or LBMA (retirée de FRED) : approximations GLD et PAXG.
- Historique de la dominance, des secteurs et des options : payant (CoinGecko Analyst 129 $/mois, Tardis options
  700 $/mois au 2026-10-04) ; à reconsidérer seulement si un test sur données gratuites montre un effet.

## Données du zoo (2026-10)

Ajout du 2026-10-10 pour le programme « zoo des stratégies » : les données **gratuites et publiques** qui
manquaient. Code : `context/zoo.py` (lecteurs, `available_at`, magasin, qualité), `context/calendrier.py` (règles
pures : FOMC, millésimes, halvings, lunes, annonces), `context/zoo_net.py` (**liste fermée dédiée**, sur le modèle de
`collect/net.py` : préfixes exacts, refus avant tout appel réseau, mots interdits, pause de 2 s exigée par le
robots.txt de FRED). Ni `data/http.py` ni la liste du relevé quotidien (`forward/sources.ALLOWED`) ne sont élargis.
Commandes : `csi zoo-download [--series …]` (une série par option, toutes par défaut) et `csi zoo-status`
(profondeur, doublons, jours manquants, plus long trou, bornes d'unité, `available_at`, recoupement avec le
calendrier figé de `forward/light.py`, lu et jamais modifié). Tests : `tests/test_zoo.py` (aucun réseau).

**Information seulement**, comme le reste de ce document : rien n'entre dans un test en cours, une décision ou un
signal ; aucun rendement n'a été lu ni calculé (téléchargement et contrôle de qualité seulement).

Magasins : séries numériques dans `data/context/<série>.parquet` (format long, lignes HISTORIQUE, plus une colonne
**`available_at`**) ; événements dans `data/context/evenements/<nom>.parquet` (une ligne par événement :
`event_time`, `available_at`, `scheduled`, `detail`, `category`, `tickers`). Les séries de marché et le calendrier
sont re-téléchargeables en entier : aucun relevé quotidien n'est ajouté à la surveillance.

| Source | Série (magasin) | Clés | Historique obtenu le 2026-10-10 | `available_at` (latence prudente) | Statut |
|---|---|---|---|---|---|
| FRED, CSV public `fredgraph.csv` sans clé | `zoo_marches` | SP500, NASDAQ100, NASDAQCOM | 2017-01-03 → 2026-10-09 (≈ 2 457 jours ouvrés) | clôture 16:00 New York + 2 h (heure d'été comprise) | MARCHE (SP500 : 10 ans au plus, licence S&P) |
| FRED | `zoo_marches` | VIXCLS | 2017-01-03 → 2026-10-08 | 16:15 New York + 2 h | MARCHE |
| FRED (H.15) | `zoo_marches` | DGS10 (taux à 10 ans, % par an) | 2017-01-03 → 2026-10-08 | lendemain 16:15 New York + 2 h | MARCHE |
| FRED (H.10, hebdomadaire) | `zoo_marches` | DTWEXBGS (dollar large), DTWEXAFEGS (dollar contre économies avancées) | 2017-01-03 → 2026-10-02 | mardi de la semaine suivante, 16:15 New York + 2 h | MARCHE |
| Nasdaq public (client existant `forward/sources`) | `zoo_marches` | GLD (ETF or, approximation de l'or) | 2017-01-03 → 2026-10-09 | clôture 16:00 New York + 2 h | MARCHE |
| BCE, taux de référence | `zoo_dollar_bce` | DXY_BCE (indice dollar recalculé, formule ICE), USD, JPY, GBP, CAD, SEK, CHF | 2017-01-02 → 2026-10-09 (2 501 jours ouvrés BCE) | 16:00 Francfort + 1 h | MARCHE (approximation : taux de 14:15 CET, pas la cotation ICE) |
| alternative.me | `zoo_fear_greed` | fear_greed (0-100) | 2018-02-01 → 2026-10-10 (3 170 jours, 4 jours absents chez la source) | J 00:00 UTC + 2 h | MARCHE (déjà lu en direct par `forward/sources`, pas encore en historique) |
| CoinMetrics community | `zoo_capitalisations` | btc, eth (capitalisation en dollars) | BTC 2010-07-18, ETH 2015-08-08 → 2026-10-09, sans trou | J + 2 jours 00:00 UTC | MARCHE |
| CoinGecko public | — | capitalisation totale, dominance BTC | `/coins/{id}/market_chart` : **365 jours** au plus (« Public API users are limited to … the past 365 days ») ; `/global/market_cap_chart` : réservé aux abonnés | — | NON_DISPONIBLE en historique ; relevé quotidien déjà en service depuis le 2026-10-04 (`market_global` : total, dominances BTC/ETH/USDT, TOTAL2, TOTAL3) |
| CoinPaprika public | — | capitalisation totale, dominance BTC | `/v1/global` : instantané seulement ; historique journalier refusé avant J−365 (« not allowed in this plan ») | — | NON_DISPONIBLE (doublon du relevé CoinGecko) |
| Binance futures, routes publiques `/futures/data/*` | — | ratios comptes (tous, gros traders), positions des gros traders, intérêt ouvert, ratio des volumes takers | API : **30 jours** (`startTime` à J−60 refusé, `-1130`) | — | inutile : tout est dans l'archive officielle `metrics` déjà téléchargée |
| Archives publiques Binance `data/futures/um/daily/metrics` (existant, `csi download-derivatives --dataset metrics`) | `data/derivatives/binance_um/metrics` | `oi`, `oi_value`, `top_accounts_ratio` (= topLongShortAccountRatio), `top_positions_ratio`, `accounts_ratio`, `taker_ratio` ; pas de 5 min | 16 paires de la configuration : 2021-12-01 → 2026-10-09 (BTC depuis 2021-01 en magasin, archive depuis 2020-09) ; mis à jour le 2026-10-10 | horodatage + 10 min + 2 s (docs/DERIVATIVES.md) | MARCHE (archive publiée chaque jour : aucun relevé supplémentaire nécessaire ; `data/http.py` non modifié) |
| Fed, pages publiques du calendrier FOMC | `evenements/calendrier_macro` | FOMC | 2017-02-01 → 2027-12-08 (97 : réunions programmées, non programmées, votes par notation ; réunions annulées écartées ; dates futures = calendrier officiel) | communiqué 14:00 New York + 1 h ; non programmé : 23:59 New York + 1 h | MARCHE |
| ALFRED (millésimes publics de FRED), séries CPIAUCNS et PAYEMS | `evenements/calendrier_macro` | CPI, NFP | CPI 2017-01-18 → 2026-09-11 (116) ; NFP 2017-01-06 → 2026-10-02 (117) ; 2025 : 11 publications chacun (arrêt de l'administration) | 08:30 New York + 1 h | MARCHE (millésime = jour de publication ; une correction à moins de 7 jours est écartée : NFP 2020-05-11) |
| BLS (calendriers officiels) | — | CPI, NFP | — | — | NON_DISPONIBLE : HTTP 403 pour tout robot (y compris avec un User-Agent identifié) ; calendrier FRED : depuis 2025 seulement ; API FRED : clé |
| Blocs Bitcoin (vérifiés sur mempool.space, table figée dans le code) | `evenements/calendrier_crypto` | HALVING | 4 halvings : 2012-11-28, 2016-07-09, 2020-05-11, 2024-04-20 (heure du bloc) | heure du bloc + 1 h | MARCHE |
| Calcul astronomique local (Meeus, chapitre 49), sans réseau | `evenements/calendrier_crypto` | NOUVELLE_LUNE, PLEINE_LUNE | 2017-01 → 2027-12 (136 + 136), écart < 1 min sur les éclipses de 2017, 2024 et 2025 | instant de la phase (calculable d'avance : un test qui s'en sert à l'avance le déclare) | MARCHE |
| Binance, liste publique des annonces (catégorie 48 « New Cryptocurrency Listing ») | `evenements/annonces_binance` | une ligne par annonce : titre, heure, catégorie (COTATION_SPOT, NOUVELLES_PAIRES, FUTURES, PROGRAMME, MARGE_COLLATERAL, AUTRE), symboles entre parenthèses | 2017-07-21 → 2026-10-07 (2 279 annonces) | heure de publication + 10 min | MARCHE (titres seulement ; classement par règle fixe, testée) |
| Stooq (CSV) | — | indices, or | — | — | NON_DISPONIBLE : défi JavaScript anti-robot (preuve de travail), pas de contournement |
| FRED, cotation LBMA de l'or (GOLDAMGBD228NLBM, GOLDPMGBD228NLBM) | — | or | — | — | NON_DISPONIBLE : séries retirées (HTTP 404) ; remplacées par GLD et PAXGUSDT (`binance_daily`) |
| Déblocages de jetons (DefiLlama « emissions », Tokenomist) | — | calendriers de déblocage | — | — | NON_DISPONIBLE : API réservée aux abonnés (`api.llama.fi/emissions` : HTTP 402 ; Tokenomist : clé) ; les fichiers du site de DefiLlama ne sont pas une API publique, et leur calendrier est réécrit après coup (pas point-in-time) |

Remarques de causalité :
- **Marchés fermés le week-end** : la clôture du vendredi n'est connue qu'après 16:00 New York + 2 h ; une jointure
  « au plus tard » sur `available_at` reprend ensuite cette valeur pendant le samedi et le dimanche (testé).
- **Calendriers** : les dates des réunions FOMC et des publications du BLS sont annoncées l'année précédente, mais
  les sources lues ne prouvent pas à quelle heure ; `available_at` est donc posé APRÈS l'événement. Un test qui veut
  « FOMC demain » doit déclarer l'hypothèse du calendrier publié à l'avance dans son pré-enregistrement.
- **Valeurs HISTORIQUE** telles que publiées aujourd'hui : les indices et les taux ne sont pas révisés ; le dollar
  large de la Fed peut l'être (révisions rares) ; CoinMetrics peut l'être (déjà déclaré plus haut).
- **FRED « muet »** depuis le 2026-10-02 : c'est le User-Agent du relevé quotidien que FRED coupe, pas le service
  (constaté le 2026-10-10). La liste fermée du zoo utilise un User-Agent identifié avec l'adresse du projet, accepté.
  Le relevé quotidien n'est pas modifié (son remplaçant Trésor / Fed / Nasdaq reste en service).

## Historique

- 2026-10-04 : module, magasin, relevé quotidien et téléchargement de l'historique ; sources validées par le
  propriétaire le même jour.
- 2026-10-04 : FRED muet (`fredgraph.csv`) ; remplacé, sur accord du propriétaire, par le Trésor américain (taux à
  10 ans), la Fed (M2, publication H.6, mêmes valeurs que FRED vérifiées) et l'ETF SPY sur Nasdaq ; verrou
  d'écriture du magasin (le téléchargement de l'historique avait écrasé le journal du relevé du jour).
- 2026-10-10 : « Données du zoo » : marchés traditionnels (FRED, GLD), indice dollar recalculé (BCE), Fear & Greed,
  capitalisations BTC et ETH, calendrier FOMC / CPI / NFP / halvings / lunes, annonces de cotation Binance ; liste
  fermée dédiée ; archive `metrics` du marché à terme mise à jour (aucune route ajoutée à `data/http.py`).
