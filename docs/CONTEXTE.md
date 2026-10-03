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
| `fred` | S&P 500, taux à 10 ans, M2 mensuel et hebdomadaire | SP500, DGS10, M2SL, WM2NS | S&P 10 ans (licence S&P), le reste des décennies | FRED |
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
  plateforme et seulement les portefeuilles déclarés) : reporté, à confirmer par le propriétaire.
- Google Trends (pas d'API, cookie exigé, robots refusés) : remplacé par les vues Wikipédia.
- Gap CME (site CME interdit la lecture automatique, Yahoo non officiel ; le contrat se négocie désormais aussi le
  week-end : la notion de gap n'a plus de sens net).
- Cotation de l'or LBMA (retirée de FRED) : approximations GLD et PAXG.
- Historique de la dominance, des secteurs et des options : payant (CoinGecko Analyst 129 $/mois, Tardis options
  700 $/mois au 2026-10-04) ; à reconsidérer seulement si un test sur données gratuites montre un effet.

## Historique

- 2026-10-04 : module, magasin, relevé quotidien et téléchargement de l'historique ; sources validées par le
  propriétaire le même jour.
