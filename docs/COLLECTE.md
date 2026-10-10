# Collecteur en shadow : données publiques de marché hors bougies (2026-10-09)

Code : `src/crypto_signal_intelligence/collect/` (`net.py`, `base.py`, `liquidations.py`, `carnet.py`, `flux.py`,
`options.py`, `attention.py`, `service.py`). Tests : `tests/test_collect.py`. Commande : `csi collecteur` (service
Docker `collecteur`, séparé de la surveillance). Journaux (ajout seul, empreintes chaînées de `forward/journal.py`) :
`<CSI_ROOT>/forward/C_<SOURCE>-AAAA-MM.jsonl`, un fichier par source et par mois. État : `<CSI_ROOT>/state/C_ETAT.json`,
route `GET /collecte`, carte « Collecte en shadow » dans l'onglet Suivi.

**Relevé en shadow : aucune influence sur les tests en direct, les avis ou BinanceSpotManager, aucun pouvoir prédictif
revendiqué.** Le collecteur enregistre, c'est tout. Il ne calcule aucun seuil, ne déclenche rien, ne note aucun signal
et n'écrit jamais dans les journaux `F*.jsonl`, dans `signals/` ni dans `state/assistant*` (vérifié par un test).
Pourquoi enregistrer : ces données (liquidations, carnet, flux de transactions, options, attention) n'ont pas
d'historique gratuit ; pour les mettre un jour à l'épreuve en direct, il faut les avoir relevées AVANT, sur des données
que personne n'a vues.

## Sécurité : liste fermée d'adresses, aucune clé, aucun ordre

`data/http.py` (bougies, carnet REST, marché à terme REST) n'est **pas élargi**. Les flux WebSocket et les hôtes hors
Binance passent par `collect/net.py`, qui refuse toute adresse hors de cette liste **avant** tout appel réseau (test :
`test_closed_list_refuses_everything_else_before_any_call`) :

| Adresse (préfixe exact) | Usage |
|---|---|
| `wss://stream.binance.com:9443/` | flux publics Spot : `<sym>@depth20@1000ms`, `<sym>@aggTrade` |
| `wss://fstream.binance.com/` | flux public du marché à terme : `/market/ws/!forceOrder@arr` |
| `https://www.deribit.com/api/v2/public/` | API publique de Deribit (indice, DVOL, résumé des options) |
| `https://wikimedia.org/api/rest_v1/metrics/pageviews/` | pages vues de Wikipédia (API REST publique de Wikimedia) |
| `https://api.coingecko.com/api/v3/search/trending` | pièces « trending » de CoinGecko (sans clé ; rien d'autre de CoinGecko) |

Reddit (`www`, `api`, `old` : 403 ou page de connexion, constaté le 2026-10-09) et Google Trends n'y figurent pas :
NON_DISPONIBLE (voir la source 5), aucune adresse ouverte pour rien ; une adresse Reddit est refusée par test.

Pas de `http://`, pas de `ws://`, pas d'identifiants dans l'adresse, et les mots `private`, `account`, `signature`,
`apikey`, `listenkey`… sont refusés partout (sécurité en profondeur : la liste fermée les exclut déjà). Aucun
en-tête d'authentification n'existe dans le code. Jamais `.env`. CSI ne passe aucun ordre.

## Les cinq sources et le format exact des entrées

Chaque ligne des journaux est une entrée de `forward/journal.py` : `{"seq", "at", "kind", "data", "prev", "hash"}`.
Les champs ci-dessous sont ceux de `data`. Heures en ISO UTC ; USDT arrondis à l'unité sauf mention.

### 1. LIQUIDATIONS — `C_LIQUIDATIONS-AAAA-MM.jsonl`

Flux `!forceOrder@arr` (toutes les paires USDⓈ-M ; seules les paires cotées en USDT sont gardées). Côté `SELL` = une
position **longue** est liquidée, `BUY` = une position **courte**. Notionnel = prix moyen × quantité exécutée.

- `LIQ_MINUTE` (seulement si le notionnel de la minute > 0) : `minute`, `n`, `notional_usdt`, `long_liq_usdt`,
  `short_liq_usdt`, `pairs_count`, `pairs` = `{paire: [n, notionnel, longs liquidés, courts liquidés]}` pour les
  **10** paires les plus liquidées de la minute, `others` = `{pairs, n, notional_usdt, long_liq_usdt, short_liq_usdt}`
  pour le reste.
- `LIQ_GROS` : chaque ordre d'un notionnel **≥ 100 000 USDT** : `symbol`, `side`, `long_liquidated`, `price`,
  `quantity`, `notional_usdt` (2 décimales), `time`.
- `LIQ_RESUME` (par heure) : `hour`, `n`, `notional_usdt`, `long_liq_usdt`, `short_liq_usdt`, `long_share`,
  `minutes_with_liquidations`, `pairs`, `top` (5 paires : `symbol`, `n`, `notional_usdt`).

**Adresse corrigée le 2026-10-10.** Du 2026-10-09 au 2026-10-10, le flux restait muet : ce n'était **pas** un blocage
par pays (testé par VPN depuis la Suisse, le Japon et Singapour : même silence), mais un **changement d'adresse chez
Binance**. Les flux publics du marché à terme USD-M sont désormais servis sous `wss://fstream.binance.com/market/ws/…`
(ou `/market/stream?streams=…`) ; l'ancien chemin `/ws/…` se connecte et ne livre plus rien. Avec la nouvelle adresse,
les liquidations arrivent depuis la France sans VPN (première reçue en 9 s, vérifié le 2026-10-10). La détection du
silence reste en place : connecté sans **aucun** message en **5 min** → statut `MUET`, réessai une fois par heure,
rien dans `errors` ; le contrôle de santé ne compte une source `MUET` ni comme vivante ni comme une panne. Si
Binance change encore d'adresse, c'est ce statut qui le signalera.

**Limite** : Binance ne publie dans ce flux qu'**un ordre par seconde et par paire** (le plus gros de la seconde) : les
comptes et notionnels sont des **minimums**. Une minute n'est écrite que 3 s après sa fin, à l'arrivée du message
suivant : sur un flux silencieux, l'entrée attend le prochain message (son `minute` reste exact, son `at` est plus
tardif).

### 2. CARNET — `C_CARNET-AAAA-MM.jsonl`

Flux combiné `<sym>@depth20@1000ms` (20 meilleurs niveaux de chaque côté, une fois par seconde) pour les 16 paires de
`data.symbols` **plus** les paires des appels actifs de l'assistant lues dans `state/assistant.json` (lecture seule,
relu toutes les 5 min, reconnexion si la liste change), **40 paires au plus**. Échantillon toutes les **5 s** par paire
sur le dernier carnet reçu (rien si le dernier message a plus de 15 s).

- `CARNET_5S` : `time`, `symbol`, `bid`, `ask`, `spread_pct` (5 décimales), `bid_0.5`, `ask_0.5`, `imb_0.5`,
  `bid_1`, `ask_1`, `imb_1` (USDT cumulés et déséquilibre (achats − ventes) / (achats + ventes) à ±0,5 % et ±1 % du
  milieu), `truncated` = `{"0.5": [achats, ventes], "1": [achats, ventes]}` (vrai = les 20 niveaux ne couvrent pas
  la bande : la profondeur est un **minimum**).
  Écrite **seulement** si, par rapport à la dernière entrée écrite de la paire, l'écart ou une profondeur
  (`bid_0.5`, `ask_0.5`, `bid_1`, `ask_1`) change de plus de **10 %**, ou si `imb_1` bouge de plus de 0,10 (absolu :
  un rapport n'a pas de sens près de zéro) ; **au moins 10 min entre deux entrées d'une même paire** et **6 au plus
  par paire et par heure calendaire**. Sans ces deux bornes le journal explose : sur BTC et ETH, depth20 est tronqué,
  ses 20 niveaux changent de plus de 10 % en permanence et les écritures partiraient en rafale au début de chaque
  heure. `CARNET_5S` est donc un **échantillon** espacé, pas la mesure.
- `CARNET_RESUME` (par heure et par paire) : `hour`, `symbol`, `samples`, `written` (entrées `CARNET_5S` de cette
  heure calendaire), `max_writes_per_hour`, `stats` = `{spread_pct, bid_0.5, ask_0.5, bid_1, ask_1, imb_1: {min, max,
  mean}}` sur **tous** les échantillons de 5 s. **C'est la mesure de référence du carnet** ; une étude future part
  de là.

**Limite** : depth20 suffit pour des bornes, pas pour une profondeur exacte : pour BTC ou ETH, ±0,5 % dépasse presque
toujours les 20 niveaux (`truncated` vrai). Le relevé REST à 1 000 niveaux reste celui de `forward/liquidity_log.py`
(toutes les 15 min). Les deux ne se remplacent pas.

### 3. FLUX — `C_FLUX-AAAA-MM.jsonl`

Flux `<sym>@aggTrade` pour les 16 paires. `m` vrai = l'acheteur est le teneur de marché, donc le **taker a vendu**.

- `FLUX_MINUTE` (une entrée par minute, toutes les paires) : `minute`, `taker_buy_usdt`, `taker_sell_usdt` (totaux),
  `pairs` = `{paire: [achats taker USDT, ventes taker USDT, nombre, déséquilibre 1 min, déséquilibre 5 min, nombre de
  gros ordres, USDT des gros ordres]}` (ordre `flux.FIELDS`, non répété dans l'entrée). Déséquilibre = (achats −
  ventes) / (achats + ventes), 3 décimales ; celui à 5 min sur les 5 dernières minutes closes (`null` tant qu'il n'y
  en a pas 5 depuis le démarrage).
- `FLUX_GROS` : transaction agrégée d'un notionnel **≥ 100 000 USDT pour BTCUSDT et ETHUSDT, ≥ 50 000 USDT pour les
  autres paires** (table `flux.LARGE_USDT_BY_PAIR`, déclarée ici : à 50 000 USDT, BTC et ETH tapaient le plafond en
  continu) : `symbol`, `taker_side`, `price`, `quantity`, `notional_usdt`, `time` ; au plus **3 par paire et par
  minute** (les suivantes sont comptées dans `FLUX_MINUTE`). Une minute sans transaction compte (0, 0) dans le
  déséquilibre à 5 min et produit quand même sa `FLUX_MINUTE` (zéros).

### 4. OPTIONS — `C_OPTIONS-AAAA-MM.jsonl`

Deribit REST public, toutes les **15 min** (hh:00:20, :15:20, :30:20, :45:20), BTC puis ETH, trois lectures :
`get_index_price` (`btc_usd`), `get_volatility_index_data` (DVOL, `resolution=3600` — en secondes, donc des bougies
horaires — sur les 3 dernières heures → dernière clôture), `get_book_summary_by_currency` (`kind=option`).

- `OPTIONS_15M` (une entrée par devise) : `currency`, `time`, `index_price`, `dvol` = `{value, at}`, `instruments`,
  `put_call_oi`, `oi_calls`, `oi_puts`, `oi_unit` (« monnaie de base »), `atm_iv_30d` = `{expiry, days, strike,
  iv_pct, legs}` (échéance la plus proche de 30 jours, strike le plus proche du sous-jacent, moyenne des `mark_iv` du
  call et du put), `skew_25d` = `{expiry, put_iv_pct, call_iv_pct, put_strike, call_strike, skew_pts}` (put − call en
  points de volatilité, même échéance), `delta_method`, `max_pain` = `{expiry, days, strike, oi}` (première échéance
  à plus de 24 h, formule de `context/fetch.py`), `errors` = `{lecture: raison}` pour ce qui a échoué (les autres
  champs restent).

**Limites.** Le résumé ne donne pas les grecs : le **delta est approché** par Black-Scholes avec `mark_iv` et un taux
nul (dit dans `delta_method`), les strikes retenus sont écrits pour que ce soit vérifiable ; `skew_25d` est `null` si
aucune option n'approche le delta 0,25 à 0,10 près. **À VÉRIFIER AU DÉPLOIEMENT** : les noms de champs et de
paramètres viennent de la documentation publique de Deribit (lue le 2026-10-09 : `mark_iv`, `underlying_price`,
`open_interest`, `resolution` ∈ {1, 60, 3600, 43200, 1D} en secondes, `index_name=btc_usd`) ; les tests n'ont vu que des
réponses fictives. Si Deribit change, l'entrée garde les données brutes qui marchent et note l'erreur.

### 5. ATTENTION — `C_ATTENTION-AAAA-MM.jsonl`

Comptes et symboles seulement, jamais de contenu.

- **Wikipédia** (pages vues par les lecteurs humains, API REST publique de Wikimedia, User-Agent descriptif exigé) :
  une fois par **jour**, au passage de 06:02 UTC, pour la **veille** (données du jour complètes). Une page par base de
  la configuration quand elle existe, d'après la **table figée** `attention.PAGES` (`BTC` → `Bitcoin`, `ETH` →
  `Ethereum`, `SOL` → `Solana_(blockchain_platform)`, `XRP` → `XRP_Ledger`, `DOGE` → `Dogecoin`, … 16 bases + `CRYPTO`
  → `Cryptocurrency`) ; une page que Wikipédia ne connaît pas (404) est **ignorée** et listée dans `missing` (à
  corriger dans la table, pas inventée). Une demande par page, espacées de 0,5 s (17 demandes par jour).
  `ATTENTION_WIKI_J` : `day`, `views` = `{base: n}`, `total`, `missing`, `errors`, `source`. Un redémarrage ne refait
  pas une veille déjà relevée (dernier `day` lu dans le journal).
- **CoinGecko « trending »** (`/api/v3/search/trending`, public, sans clé ; limite ≈ 10-30 demandes/min, une par
  **heure** ici ; **rien d'autre** de CoinGecko n'est dans la liste). `ATTENTION_TRENDING_H` : `hour`, `coins` =
  symboles en majuscules, **15 au plus**, dans l'ordre de CoinGecko ; `in_universe` = ceux qui sont des bases de la
  configuration. Ni nom, ni rang, ni image, ni NFT, ni catégorie.
- **Reddit : NON_DISPONIBLE (connexion exigée depuis 2026)** : `www.reddit.com` et `api.reddit.com` répondent 403,
  `old.reddit.com` renvoie vers la page de connexion (constaté depuis cette machine le 2026-10-09) ; la lecture
  publique sans compte n'existe plus. Retiré de la liste fermée ; l'état porte `reddit: NON_DISPONIBLE`.
- **Google Trends : NON_DISPONIBLE** (état : `trends: NON_DISPONIBLE`). La seule bibliothèque connue, `pytrends`,
  repose sur une API non officielle qui casse régulièrement **et** fait ses propres appels réseau, hors du client à
  liste fermée : aucun chemin de code, aucune dépendance.

## Service

- `csi collecteur` lance les cinq sources dans des tâches `asyncio` (bibliothèque `websockets`, extra `[collect]`
  de `pyproject.toml`, installée dans l'image). Un **verrou d'instance** (`state/collecteur.lock`, `live/lock.py`)
  interdit deux collecteurs. Priorité CPU basse (`nice +10`, `live/priority.py`).
- **Reconnexion** : une coupure (flux fermé, panne réseau, exception) est inscrite dans l'état, puis la source est
  relancée après une attente exponentielle **1 s → 2 → 4 → … → 60 s**, remise à 1 s après 5 min de fonctionnement
  stable. Ping WebSocket toutes les 20 s. Une adresse refusée ou une bibliothèque absente met la source en
  `NON_DISPONIBLE` sans la relancer ; les autres continuent. Le carnet qui se reconnecte parce que la liste de paires
  a changé passe `RECHARGEMENT` : ni erreur ni attente. Un flux connecté sans aucun message en 5 min (liquidations,
  voir la source 1) passe `MUET` et n'est réessayé qu'une fois par heure.
- **Horloge en recul** (NTP, machine réveillée) : le journal refuse une entrée antérieure à la précédente ; elle est
  ré-horodatée à `précédente + 1 ms` avec `clock_adjusted: true` et `clock_at` (l'heure lue), la source continue.
- **État** `state/C_ETAT.json`, réécrit au plus toutes les 10 s et au moins toutes les 30 s : par source `status`
  (`DEMARRAGE`, `EN_SERVICE`, `RECONNEXION`, `RECHARGEMENT`, `MUET`, `NON_DISPONIBLE`, `ARRETE`), `last_message_at`, `last_entry_at`,
  `messages`, `entries`, `errors`, `last_error` (sans secret : il n'y en a aucun), `reconnections`, `next_retry_s`,
  `bytes_month`, `detail` ; plus `written_at`, `started_at`, `priority_lowered`, `places_orders: false`.
- **Santé** : `csi collecteur-health --max-age 300` (code 0 si `C_ETAT.json` a été réécrit depuis moins de 5 min et
  qu'aucune source n'est tombée — `NON_DISPONIBLE`, `ARRETE` — sans qu'une autre vive ; `MUET` n'est ni vivante ni une
  panne) ; c'est le `healthcheck` du service Docker.
- **Arrêt** : SIGTERM/SIGINT → `stop` posé, toutes les tâches annulées (l'annulation interrompt les `async for` des
  flux et les attentes des sources REST), état écrit (`ARRETE` pour chaque source), verrou libéré ; testé : retour en
  moins d'une seconde sur des flux muets (`stop_grace_period: 30s`).
- **Rotation** : mensuelle, par le nom du fichier (le mois de l'horodatage de l'entrée). Rien n'est supprimé.

## Débit estimé (à vérifier sur `bytes_month` dans l'état après quelques jours)

Enveloppe d'une entrée (numéro, heure, nature, empreintes chaînées) ≈ 230 octets.

| Source | Entrées | Estimation |
|---|---|---|
| FLUX | 1 440 `FLUX_MINUTE`/jour (16 paires, ≈ 1,3 Ko avec l'enveloppe) + `FLUX_GROS` (≥ 100 000 USDT sur BTC/ETH, ≥ 50 000 ailleurs, plafonné 3/paire/min) | ≈ 1,9 Mo/jour (**56 Mo/mois**) + gros ordres : BTC/ETH à 100 000 USDT ≈ quelques centaines/jour, altcoins rares → ≈ 5 à 15 Mo/mois → **60 à 70 Mo/mois** |
| LIQUIDATIONS | ≤ 1 440 `LIQ_MINUTE`/jour (≈ 1 Ko, 10 paires + reste) + `LIQ_GROS` (rares) + 24 résumés | ≤ 1,5 Mo/jour → **20 à 45 Mo/mois** selon la part des minutes avec liquidation |
| CARNET | ≤ 6 `CARNET_5S`/paire/heure, espacées de 10 min (≈ 420 o) + 24 résumés/paire (≈ 550 o, la référence) | 19 paires (16 + appels actifs) : ≤ 1,4 Mo/jour → **≤ 42 Mo/mois** ; 40 paires : ≤ 85 Mo/mois |
| OPTIONS | 192 entrées/jour (≈ 0,9 Ko) | **≈ 5 Mo/mois** |
| ATTENTION | 24 `ATTENTION_TRENDING_H` (≈ 0,4 Ko) + 1 `ATTENTION_WIKI_J` (≈ 0,6 Ko) par jour | **< 1 Mo/mois** |

Total attendu **≈ 130 à 165 Mo/mois** avec 19 paires au carnet ; la cible est < 150 Mo/mois, le carnet étant
désormais borné (10 min entre deux `CARNET_5S`). Si une source dépasse, on agrège davantage, dans cet ordre :
`flux.LARGE_USDT_BY_PAIR` (100 000 USDT pour d'autres paires), `carnet.MAX_WRITES_PER_HOUR` (6 → 3),
`liquidations.TOP_PAIRS` (10 → 5). Aucune de ces constantes n'est un réglage de stratégie.

## Ce qui n'est PAS fait

- **Aucun test, aucun seuil, aucune prédiction** : rien ici ne dit qu'une liquidation, un carnet déséquilibré, un
  gros ordre, une asymétrie d'options ou un pic d'attention annonce quoi que ce soit.
- Aucune lecture par la surveillance, l'assistant, les avis, le feu de protection ou BSM : les journaux `C_*` ne sont
  lus que par `GET /collecte` (taille) et, plus tard, par l'analyse pré-inscrite.
- Pas d'historique : la collecte commence au premier démarrage, rien n'est reconstitué.
- Pas de Reddit ni de Google Trends (voir plus haut), pas d'autre bourse que Binance et Deribit ; les liquidations
  restent vides tant que les flux dérivés sont bloqués depuis le réseau du collecteur (`MUET`).

## Plan : après 14 jours, pré-inscription de tests en direct

Rien n'est ajouté à `FORWARD_TESTS.md` aujourd'hui. Quand les journaux auront **14 jours**, des tests en direct
seront **pré-inscrits** (nouvelle section, empreintes gelées, placebos), sur les **comptages seulement** :
« événement → placebo », par exemple : une heure où le notionnel liquidé dépasse un rang élevé de son propre
historique, un déséquilibre du carnet au-delà d'un rang, une heure de gros ordres, un pic de mentions ; mesure =
rendement de la paire (ou de BTC) sur 1 h / 4 h / 24 h après l'événement contre 20 placebos à la même heure les
jours précédents, avec intervalle par blocs. Les seuils seront choisis sur ces 14 premiers jours **et figés avant**
le début du test ; les 14 jours ne compteront pas dans la mesure. Verdict attendu, comme pour les autres : le plus
probable est « rien ».
