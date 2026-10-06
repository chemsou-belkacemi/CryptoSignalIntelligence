# Univers de paires — avis halal relevés le 2026-10-01

Configuration : `[data].symbols` et `[data.tick_size]` dans `config/default.toml`.

**Ce projet ne certifie la conformité religieuse d'aucun actif.** Les classements ci-dessous
viennent de trois services de screening publics, aux méthodologies différentes, relevés le
2026-10-01. Ils se contredisent parfois et évoluent chaque mois. La décision finale appartient à
l'utilisateur, selon le référent qu'il suit ; la liste se modifie dans la configuration.

**Méthode du relevé (2026-10-01).** Chaque page est téléchargée telle quelle, puis ses listes sont
extraites directement du HTML, sans résumé automatique. L'instantané des trois listes, avec
l'empreinte SHA-256 de chaque page, est dans `docs/universe_sources/2026-10-01.json` ; le fichier
`config/halal_screening.toml` en est dérivé mécaniquement, et un test vérifie qu'il lui correspond
(`tests/test_admission.py`).

**Corrections par rapport au relevé du 2026-09-30.** Ce premier relevé avait été fait par lecture
résumée des pages et comportait des erreurs :
- sur la page d'IFG, la colonne « IFG Holding? » (IFG en détient-il ?) avait été confondue avec la
  colonne « Halal? ». SOL était noté « ambigu » alors qu'IFG répond « Yes » ; UNI était noté « haram »
  alors qu'IFG répond « Yes » ;
- plusieurs cryptos notées « favorables » ne sont listées halal que par une seule source (AR, FLOW,
  GRT, KSM, MINA, ONE, QTUM, STX, ZIL). La règle en demande deux : elles repassent « à décider ».

Les 16 paires de la configuration restent toutes favorables.

## Sources consultées

| Code | Source | Méthodologie affichée | Date affichée |
|---|---|---|---|
| HS | [HalalSignalz — crypto pass list](https://www.halalsignalz.com/crypto-passlist) | Crypto Shariah Screening Framework (Mufti Faraz Adam, 2021), mensuel ; 20 cryptos passent, les autres ne sont pas listées | octobre 2026 |
| SB | [SharifBot — halal coins](https://sharifbot.com/pages/halal-coins) | AAOIFI Shariah Standard 17 ; 122 halal, 85 en zone grise | sept. 2026 |
| IFG | [Islamic Finance Guru — crypto](https://www.islamicfinanceguru.com/crypto) | analyse maison ; 60 cryptos, colonne « Halal? » : 53 oui, 7 non | non datée |

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
| SOLUSDT | ✔ | ✔ | ✔ | 2020-08 | 0.01 | 289 |
| XRPUSDT | ✔ | ✔ | ✔ | 2018-05 | 0.0001 | 286 |
| NEARUSDT | ✔ | ✔ | ✔ | 2020-10 | 0.001 | 204 |
| AVAXUSDT | ✔ | ✔ | ✔ | 2020-09 | 0.001 | 118 |
| HBARUSDT | ✔ | ✔ | ✔ | 2019-09 | 0.00001 | 88 |
| LINKUSDT | ✔ | ✔ | ✔ | 2019-01 | 0.001 | 84 |
| XLMUSDT | ✔ | ✔ | ✔ | 2018-05 | 0.0001 | 44 |
| ADAUSDT | ✔ | ✔ | ✔ | 2018-04 | 0.0001 | 42 |
| TRXUSDT | – | ✔ | ✔ | 2018-06 | 0.0001 | 22 |
| FILUSDT | ✔ | ✔ | ✔ | 2020-10 | 0.0001 | 17 |
| ALGOUSDT | ✔ | ✔ | ✔ | 2019-06 | 0.0001 | 16 |
| DOTUSDT | ✔ | ✔ | ✔ | 2020-08 | 0.001 | 13 |
| ATOMUSDT | ✔ | ✔ | ✔ | 2019-04 | 0.001 | 7 |
| ETCUSDT | – | ✔ | ✔ | 2018-06 | 0.01 | 6 |

Les colonnes « Cotée depuis », « Pas de prix » et « Volume 24 h » datent du 2026-09-30. Points
d'attention : TRX (écosystème de jeux d'argent selon certains avis ; HS ne la liste pas), ETC (HS ne la
liste pas), ATOM et ETC proches du seuil de liquidité. IFG répond « Yes » pour SOL, mais indique ne pas
en détenir.

## Relevé complet du 2026-10-01 (240 cryptos)

Le relevé couvre les cryptos qui ont une paire USDT négociable sur Binance Spot et qu'une source au
moins liste, plus les paires liquides (volume médian sur 7 jours d'au moins 5 M$) qu'aucune source ne
liste. Les stablecoins et les monnaies fiduciaires sont exclus. Détail par crypto :
`config/halal_screening.toml`.

| Statut | Nombre | Cryptos |
|---|---|---|
| Favorable (au moins 2 sources halal, aucune réserve) | 30 | ADA, ALGO, APT, ARB, ATOM, AVAX, BCH, BTC, DOT, EGLD, ETC, ETH, FIL, HBAR, IOTA, LINK, LTC, NEAR, NEO, POL, RENDER, SOL, SUI, TAO, THETA, TRX, VET, XLM, XRP, XTZ |
| Défavorable (« Halal? No » chez IFG) | 6 | AAVE, ENA, FTT, HYPE, MKR, ONDO |
| Douteuse (zone grise chez SharifBot) | 84 | voir le fichier |
| Inexploitable : une seule source halal | 98 | voir le fichier |
| Inexploitable : aucune source, mais liquide | 22 | BABY, CAKE, CRV, ETHFI, INJ, JUP, LDO, MARSCOIN, MORPHO, MSTRB, MUBARAK, NIL, NVDAB, PENGU, PLUME, PUMP, RAY, SNDKB, VTHO, WLD, WLFI, XPL |

Les favorables hors configuration sont ajoutées à l'univers évaluable par la règle d'admission
ci-dessous. Les douteuses et les inexploitables vont dans « Cryptos à décider ». Elles n'entrent pas
dans les protocoles de recherche déjà exécutés, qui gardent les 16 paires de la configuration.

Cryptos listées par une source mais sans paire USDT négociable sur Binance Spot le 2026-10-01 :
BGB, CRO, CSPR, DAI, EOS, KAS, LEO, MKR, MNT, OKB, OM, TON, XMR.

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

## Admission selon l'avis halal (règle du propriétaire, 2026-10-01)

Demande du propriétaire : « ajoute toutes ces cryptos favorables avec de l'USDT ; favorable : ajout
direct ; défavorable : refus ; douteux : ne se transmet pas directement, il doit me notifier ; avis
inexploitable aussi, avec un bouton pour choisir ».

- **Avis.** `config/halal_screening.toml` reprend, crypto par crypto, l'avis de chacune des trois sources
  relevées le 2026-10-01. Une crypto absente du fichier est « inexploitable ». Ce projet ne certifie
  rien.
- **Statut**, déduit mécaniquement (`external/admission.py`) :

  | Statut | Condition | Ce que fait CSI |
  |---|---|---|
  | FAVORABLE | halal pour au moins 2 sources, aucune douteuse ni haram | ajout direct de la paire USDT, si elle se négocie sur Binance Spot |
  | DEFAVORABLE | haram pour une source au moins | refus |
  | DOUTEUX | douteuse pour une source au moins, ou avis contradictoires | à décider par le propriétaire |
  | INEXPLOITABLE | moins de 2 sources, ou screening non consigné | à décider par le propriétaire |

- **Ajout en masse.**
  - Commande : `admit-halal`, ou le bouton « Appliquer le screening » de l'onglet Suivi du tableau de
    bord.
  - Elle s'applique aux cryptos du fichier, hors configuration, en paire USDT.
  - Une paire ajoutée suit le cycle habituel : la surveillance télécharge son historique, une paire par
    cycle, puis la paire devient prête.
  - Une crypto favorable sans paire négociable est « indisponible ».
- **Signal reçu automatiquement** (relève Telegram de BinanceSpotManager), pour une crypto hors univers :
  - favorable : ajoutée ;
  - défavorable : refusée (avis REFUSE) ;
  - douteuse ou inexploitable : rien n'est ajouté, et l'avis reste **EN_ATTENTE**. BinanceSpotManager
    n'exécute pas un avis EN_ATTENTE : le signal n'est donc **pas transmis**. La crypto apparaît « à
    décider ».
- **Soumission à la main par le propriétaire.** Elle vaut toujours sa décision : la paire est ajoutée,
  même s'il l'avait refusée auparavant par bouton (nouvelle décision, tracée). Seule exception : une
  crypto défavorable reste refusée, par bouton comme à la main. Pour lever ce refus, il faut modifier le
  fichier des avis.
- **Paires ajoutées avant la règle** (ancien mode test, qui acceptait tout) : la règle s'applique
  rétroactivement. Une paire soumise à la main (motif « signal soumis à la main ») compte comme décision
  du propriétaire. Une autre, douteuse ou inexploitable, repasse « à décider » et ses signaux restent
  EN_ATTENTE jusqu'à la décision ; une défavorable est refusée.
- **Crypto douteuse ou inexploitable sans paire négociable** sur Binance Spot (XMR, QNT) : « indisponible »,
  rien à décider. Une panne de Binance pendant la vérification n'enregistre rien (« injoignable »,
  à relancer).
- **Notification et décision.**
  - Un badge « N cryptos à décider » s'affiche en haut du tableau de bord.
  - Dans l'onglet Suivi, les « Cryptos à décider » sont rangées en trois groupes, du plus étayé au moins
    étayé : une seule source halal sans réserve, zone grise pour une source, aucune source.
  - Chaque ligne a ses boutons Ajouter et Refuser ; chaque groupe a un bouton d'ajout groupé ; un bouton
    « Tout ajouter (ma décision) » couvre toute la liste (`POST /admissions/decide-all`). Les paires USDC
    se décident aussi.
  - La décision est tracée « par propriétaire », et la règle ne l'écrase jamais.
  - Telegram : CSI n'a pas de bot. Il lui faudrait un jeton, donc un secret, alors que CSI n'en utilise
    aucun. La notification passe par le tableau de bord. Un relais par le bot de BinanceSpotManager reste
    possible plus tard, sur demande.
- **Où.** Table `pair_admissions` de `signals/external.sqlite3`, à côté de `user_pairs`. API :
  `GET /admissions`, `POST /admissions/run`, `POST /admissions/decide` (docs/API.md).
- Ces paires servent à l'évaluation des signaux, au tableau de bord et à la surveillance des signaux
  externes. Elles n'entrent pas dans les protocoles de recherche, qui gardent l'univers de la
  configuration.

## Groupes de confiance halal (décision du propriétaire, 2026-10-06)

Le propriétaire : « les paires reçues de ces groupes sont fiables et halal, ajoute-les automatiquement si elles
sont sur Binance ». Groupes : EL MAHWASHI, LEGEND TRADING, IN CRYPTO, WHALE HUNTING ; les deux groupes VIP de son
ami (EL MAHWASHI VIP, IN CRYPTO VIP) dès que leurs identifiants sont connus. Liste : `config/default.toml`,
`[external] halal_trusted_groups`.

- Un groupe est reconnu **par l'identifiant de sa conversation Telegram d'origine**, que Telegram pose sur un message
  transféré et que le relais transmet (`POST /telegram/live`) ; **jamais par un nom écrit dans le message**, que
  n'importe quel canal peut copier (relecture du 2026-10-06). Une copie de texte (groupe qui interdit le transfert)
  n'a pas cet identifiant : rien n'est ajouté.
- Une **paire USDT** publiée par l'un de ces groupes est ajoutée comme **décision du propriétaire** (motif « groupe de
  confiance halal … ») si elle se négocie sur Binance Spot.
- Elle reste **refusée** si le propriétaire a refusé cette crypto (sur n'importe quelle paire : BNB, PEPE, SHIB…) ou
  si elle est **défavorable** au screening (UNI, AAVE, MKR, ENA). Une décision du propriétaire n'est jamais remplacée.
- Il faut le jeton de l'API (`CSI_API_TOKEN`, obligatoire en Docker) ; au plus 10 s de vérifications Binance par
  dépôt (le reste attend le prochain signal de la paire).
- **Tests en direct** : F4 et F16 gardent la liste figée à leur démarrage. Limite connue : la prévision de
  volatilité en service (lue par F5 et F14) s'ajuste sur toutes les paires prêtes de l'univers ; chaque ajout, de
  cette règle comme des précédentes, change un peu son panel en cours de mois (F14 déclare un réajustement mensuel
  « sur l'univers du moment »). Le code de cette prévision est gelé par F5 et F14 : non modifié.

## Modifier l'univers

1. Ajouter ou retirer la paire dans `[data].symbols` et son pas de prix dans `[data.tick_size]`
   (valeur `tickSize` du filtre `PRICE_FILTER` de `exchangeInfo`).
2. `doctor` : vérifie que le pas configuré correspond au marché.
3. `download` : complète l'historique. Les résultats de walk-forward antérieurs restent valables
   pour leur propre univers, indiqué dans chaque rapport.
