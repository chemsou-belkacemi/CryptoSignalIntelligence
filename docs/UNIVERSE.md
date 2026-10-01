# Univers de paires — vérifié le 2026-09-30

Configuration : `[data].symbols` et `[data.tick_size]` dans `config/default.toml`.

**Ce projet ne certifie la conformité religieuse d'aucun actif.** Les classements ci-dessous
viennent de trois services de screening publics, aux méthodologies différentes, consultés le
2026-09-30. Ils se contredisent parfois et évoluent chaque mois. La décision finale appartient à
l'utilisateur, selon le référent qu'il suit ; la liste se modifie dans la configuration.

## Sources consultées

| Code | Source | Méthodologie affichée | Date affichée |
|---|---|---|---|
| HS | [HalalSignalz — crypto pass list](https://www.halalsignalz.com/crypto-passlist) | Crypto Shariah Screening Framework (Mufti Faraz Adam, 2021), mensuel | juin 2026 |
| SB | [SharifBot — halal coins](https://sharifbot.com/pages/halal-coins) | AAOIFI Shariah Standard 17 | sept. 2026 |
| IFG | [Islamic Finance Guru — crypto](https://www.islamicfinanceguru.com/crypto) | analyse maison | non datée |

Algorand dispose en plus d'une certification de la Shariyah Review Bureau (citée par plusieurs
sources secondaires, non vérifiée à la source ici).

## Règle de sélection (appliquée mécaniquement)

1. **Screening** : classée halal par au moins deux des trois sources, et par aucune comme
   douteuse (« grey area ») ou haram.
2. **Disponibilité** : paire USDT en statut `TRADING` sur Binance Spot (API publique
   `exchangeInfo`).
3. **Historique** : première bougie mensuelle au plus tard en 2021-06, pour disposer d'au moins
   un an d'entraînement avant la première fenêtre de test du walk-forward (2022-07).
4. **Liquidité** : volume 24 h en USDT d'au moins 5 M$ le jour de la vérification (seuil
   `liquidity_min_quote_volume_24h`).

## Paires retenues (16)

| Paire | HS | SB | IFG | Cotée depuis | Pas de prix | Volume 24 h (M$) |
|---|---|---|---|---|---|---|
| BTCUSDT | ✔ | ✔ | ✔ | 2017-08 | 0.01 | 1126 |
| ETHUSDT | ✔ | ✔ | ✔ | 2017-08 | 0.01 | 854 |
| SOLUSDT | ✔ | ✔ | ambigu (halal et non sur la même page) | 2020-08 | 0.01 | 289 |
| XRPUSDT | ✔ | ✔ | ✔ | 2018-05 | 0.0001 | 286 |
| NEARUSDT | ✔ | ✔ | ✔ | 2020-10 | 0.001 | 204 |
| AVAXUSDT | ✔ | ✔ | – | 2020-09 | 0.001 | 118 |
| HBARUSDT | ✔ | ✔ | ✔ | 2019-09 | 0.00001 | 88 |
| LINKUSDT | ✔ | ✔ | – | 2019-01 | 0.001 | 84 |
| XLMUSDT | ✔ | ✔ | ✔ | 2018-05 | 0.0001 | 44 |
| ADAUSDT | ✔ | ✔ | ✔ | 2018-04 | 0.0001 | 42 |
| TRXUSDT | – | ✔ | ✔ | 2018-06 | 0.0001 | 22 |
| FILUSDT | ✔ | ✔ | ✔ | 2020-10 | 0.0001 | 17 |
| ALGOUSDT | ✔ | ✔ | ✔ | 2019-06 | 0.0001 | 16 |
| DOTUSDT | ✔ | ✔ | ✔ | 2020-08 | 0.001 | 13 |
| ATOMUSDT | ✔ | ✔ | ✔ | 2019-04 | 0.001 | 7 |
| ETCUSDT | – | ✔ | ✔ | 2018-06 | 0.01 | 6 |

Points d'attention signalés par au moins une source : TRX (écosystème de jeux d'argent selon
certains avis), SOL (IFG contradictoire), ATOM et ETC proches du seuil de liquidité.

## Exclues, avec le motif

- **Screening insuffisant ou avis négatif** : LTC, BCH (une seule source) ; BNB, ICP, QNT, ZEC,
  DASH, SEI, ROSE, FET (douteux SB) ; UNI (haram IFG) ; DOGE, SHIB, PEPE, XMR (une source, ou avis
  contradictoires) ; AAVE, MKR, ENA (haram IFG, intérêt/produits synthétiques).
- **Historique trop court** (cotation Binance après 2021-06), à réexaminer au lot 4 : POL (2024-09,
  3 sources), RENDER (2024-07, 3 sources), APT (2022-10), ARB (2023-03), SUI, TIA, OP, ICP (2021-05
  et douteux SB).
- **Liquidité < 5 M$ le 2026-09-30** malgré un screening favorable : XTZ (0,7), VET (1,8), IOTA
  (3,3), THETA (0,7), NEO (0,4), EGLD (0,4), AR (3,5), KSM, ONE, ZIL, QTUM, MINA, FLOW, STX, GRT.
- **Non cotée ou suspendue sur Binance Spot** (`BREAK`/absente) : MATIC (remplacé par POL), TON,
  EOS, XMR, KDA, CSPR, QNT, ISLM.

## Limites techniques de cet univers

- **Biais de survie et de sélection** : choisir aujourd'hui des paires cotées, liquides et
  screenées aujourd'hui, puis les tester sur 2021-2025, favorise mécaniquement les survivantes.
  Les rapports le mentionnent ; aucune généralisation aux paires absentes.
- **Pas de prix et volumes d'aujourd'hui** : Binance modifie parfois le pas de prix ; les niveaux
  historiques sont arrondis au pas actuel. Les volumes ne servent qu'au filtre de liquidité
  courant, jamais à filtrer un backtest ancien.
- **Contexte de marché** : BTCUSDT 1h reste le contexte commun (`btc_*`) pour toutes les paires.
- **Coût** : chaque paire ajoute deux historiques (15m et 1h) à télécharger, soit environ deux à
  trois minutes par paire au premier `download`, puis quelques secondes par mise à jour.

## Paires ajoutées par le propriétaire (validation manuelle, 2026-09-30)

Règle décidée par le propriétaire : **un signal qu'il soumet lui-même vaut validation de la paire**
(il a jugé l'actif acceptable). CSI l'ajoute alors définitivement à l'univers évaluable, sans
screening supplémentaire — ce projet ne certifie rien, la décision est la sienne, tracée avec sa
date et son motif. Les signaux reçus **automatiquement** (relève Telegram du worker de
BinanceSpotManager) n'ajoutent jamais de paire : personne ne les a validés.

- Soumission manuelle = page Avis CSI ou signal collé sur la page Signaux de BSM (`user_validated`),
  commande `evaluate-signal` (option `--add-pair`, activée par défaut), `scripts/evaluer-signal.ps1`.
- À la première soumission, CSI vérifie que la paire existe sur Binance Spot (pas de prix lu sur l'API
  publique, aucune clé), l'enregistre en `REQUESTED` et rend l'avis **EN_ATTENTE** (non enregistré).
  La surveillance (`run`) télécharge ensuite l'historique 15m et 1h entre deux cycles, une paire par
  cycle, seulement s'il reste au moins six minutes avant la clôture suivante ; la paire passe `READY`
  et le signal peut être réévalué (avis normal, enregistré). Trois échecs de téléchargement → `FAILED`,
  relancée par une nouvelle soumission manuelle.
- Ces paires servent à l'évaluation des signaux externes et à leur résolution ; elles n'entrent pas
  dans les walk-forwards, qui gardent l'univers de la configuration.
- Où : table `user_pairs` de `signals/external.sqlite3` (état Docker : volume `csi-state`).
  Consulter : `universe` (ou `GET /universe` de l'API) ; retirer : `universe --forget PAIRE`.
- **Ajout automatique de toute paire** (`[external] auto_add_pairs`, ou `CSI_EXTERNAL__AUTO_ADD_PAIRS` ; Docker : `CSI_AUTO_ADD_PAIRS`) : toute paire soumise est ajoutée, même par un signal reçu automatiquement, **sans validation du propriétaire** ; le motif enregistré le dit. **Activé par défaut dans Docker depuis le 2026-10-01, par choix du propriétaire** ; `CSI_AUTO_ADD_PAIRS=false` dans le `.env` de CSI pour revenir à la règle stricte (seule sa soumission manuelle vaut validation).

## Modifier l'univers

1. Ajouter ou retirer la paire dans `[data].symbols` et son pas de prix dans `[data.tick_size]`
   (valeur `tickSize` du filtre `PRICE_FILTER` de `exchangeInfo`).
2. `doctor` : vérifie que le pas configuré correspond au marché.
3. `download` : complète l'historique. Les résultats de walk-forward antérieurs restent valables
   pour leur propre univers, indiqué dans chaque rapport.
