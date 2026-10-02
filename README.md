# CryptoSignalIntelligence

**Données → recherche → analyse → validation → signaux TXT.** Binance Spot, long uniquement
(BUY ou NO_TRADE). Ce projet **ne crée, ne modifie ni n'annule aucun ordre** : il n'a
aucune route vers les endpoints privés Binance (vérifié par test) et n'utilise aucune clé.
L'exécution reste le rôle exclusif de BinanceSpotManager.

Aucune promesse de rendement. Zéro signal est une réponse valable.

Cahier des charges complet : [docs/PROMPT_MAITRE.md](docs/PROMPT_MAITRE.md) (ajouts du 2026-09-30 en section 26).
Ce qui est testé, non vérifié, bloqué ou non implémenté : [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md).

## État : lots 0 à 2 livrés, lots 3 et 4 en cours

| Livré | Fichiers |
|---|---|
| **Lot 2** — stratégies B `EMA_PULLBACK_CONTINUATION` et C `RANGE_REENTRY` + fiches | `strategies/`, [fiche B](docs/strategies/EMA_PULLBACK_CONTINUATION.md), [fiche C](docs/strategies/RANGE_REENTRY.md) |
| **Lot 2** — walk-forward purgé, recalibrage par grille grossière (plateau), agrégat hors échantillon, verdict automatique selon les critères déclarés | `research/walk_forward.py`, `research/admission.py`, [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| **Lot 3 (en cours)** — contrat TXT **V3** : DECISION_AT, ENVIRONMENT, deux expirations (EXPIRES_AT pour le message, ENTRY_EXPIRES_AT pour les entrées), ENTRY_COUNT, RR_REFERENCE, EXIT_POLICY_HASH, NEWS_STATUS, probabilité ML liée à sa cible, son horizon et sa calibration | `signals/`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |
| **Lot 3 (en cours)** — politiques de sortie partagées avec l'exécuteur, identifiées par une empreinte (`config/exit_policies.json`) ; hors shadow, seules les politiques réellement exécutées par le consommateur sont publiées | `backtest/exits.py`, [docs/EXIT_POLICIES.md](docs/EXIT_POLICIES.md) |
| **Lot 3 (en cours)** — retour d'exécution JSONL v2 : message reçu / ordre envoyé / rempli, UNKNOWN sans confirmation, frais réels et devise ; rapport backtest / prospectif (rejeu selon la politique du signal) / Demo avec écarts expliqués | `feedback/`, [docs/FEEDBACK_FORMAT.md](docs/FEEDBACK_FORMAT.md) |
| **Lot 3 (en cours)** — moteur de sorties : TP partiels pondérés, stop remonté (break-even, TP précédent) sans rétroactivité, TP au marché sur déclenchement, sans sortie temporelle ; profil observé de BinanceSpotManager rejoué en variante `profil_BSM` | `backtest/exits.py`, [docs/BSM_PROFILE.md](docs/BSM_PROFILE.md) |
| **Lot 3 (en cours)** — test de contrat contre le vrai parseur de BinanceSpotManager : échec sûr vérifié (un BSM qui ne lit pas le V3 le refuse) ; lecture V3 en cours sur la branche `feat/csi-v2-drop` | `tests/test_bsm_contract.py`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |
| **Lot 4 (en cours)** — `scan` et `run --mode shadow` : cycle à chaque clôture 15m, attente bornée des bougies 15m/1h, REST des seules bougies manquantes, fenêtres en mémoire, verrou d'instance, reprise sans republication, état de santé | `live/`, [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md) |
| **Lot 4 (en cours)** — exploitation : Docker Compose (volume persistant, limites, redémarrage, santé « prêt »), arrêt propre sur SIGTERM, tâche planifiée Windows | `Dockerfile`, `docker-compose.yml`, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **Lot 4 (en cours)** — sauvegarde cohérente, restauration vérifiée, publication suspendue jusqu'à réconciliation ; travaux lourds en priorité CPU basse | `live/backup.py`, `live/priority.py` |
| **Lot 4 (en cours)** — collecte d'actualités sans LLM (8 sources vérifiées), traçabilité, corrections, reprises regroupées, santé des sources, mode observe | `news/`, [docs/NEWS.md](docs/NEWS.md) |
| Évaluation de signaux externes (groupes Telegram) : vetos, contexte, taux de base, registre, résolution, bilan par source | `external/`, [docs/EXTERNAL_SIGNALS.md](docs/EXTERNAL_SIGNALS.md) |
| Univers de 16 paires screenées (halal, disponibilité, historique, liquidité) | [docs/UNIVERSE.md](docs/UNIVERSE.md) |
| Contrats typés (contexte, résultat, signal V3), config TOML, CLI, `doctor` | `domain/`, `signals/schema.py`, `config.py`, `cli.py` |
| Données : archives officielles avec SHA-256, unités ms/µs par source, REST paginé, cache Parquet idempotent, quarantaine, contrôles qualité | `data/`, [docs/DATA.md](docs/DATA.md) |
| Features causales, jointure 15m/1h vers le passé, régimes sur 4 axes | `features/`, `regimes/` |
| Stratégie A `DONCHIAN_VOLUME_BREAKOUT` + fiche d'hypothèse | `strategies/`, [fiche](docs/strategies/DONCHIAN_VOLUME_BREAKOUT.md) |
| Niveaux arrondis au tick, RR recalculés, vetos NO_TRADE codés | `levels/`, `validation/gates.py` |
| Simulation événementielle, 3 scénarios de coûts, ablations, références, registre d'expériences | `backtest/`, `research/`, [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| Sérialiseur/parseur TXT strict, publication atomique + registre + réconciliation, dossier shadow | `signals/`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |

**Intégration avec BinanceSpotManager : INTEGRATION_UNVERIFIED** — `main` de BSM refuse le format
V3 (échec sûr, vérifié par test). La branche `feat/csi-v2-drop` lit le V2 et passe au V3 ; le test de
contrat `tests/test_bsm_contract.py` décidera. Sortie vers `signals/shadow` tant que la branche n'est
pas relue et fusionnée.

## Résultats du lot 2 (2026-09-30) : les trois stratégies sont REJECTED

Walk-forward purgé sur DEVELOPMENT : 6 fenêtres de test de 6 mois (2022-07 → 2025-06), BTC + ETH,
recalibrage sur le passé de chaque fenêtre, agrégat hors échantillon en coûts centraux, signaux
indépendants (pas un portefeuille) :

| Stratégie | Trades clos | E[R] net | IC95 (bootstrap par blocs) | Sans filtre 1h | Verdict |
|---|---|---|---|---|---|
| A `DONCHIAN_VOLUME_BREAKOUT` | 288 | −0,23 R | [−0,45 ; −0,03] | −0,23 R | REJECTED |
| B `EMA_PULLBACK_CONTINUATION` | 68 | −0,12 R | [−0,38 ; −0,02] | +0,01 R (IC contient 0) | REJECTED |
| C `RANGE_REENTRY` | 147 | −0,34 R | [−0,58 ; −0,04] | −0,30 R | REJECTED |

- Chiffres identiques sur trois exécutions indépendantes (`reports/WF-*`, registre `experiments/`).
- Le filtre de tendance 1h n'apporte rien : sans lui, chaque stratégie fait aussi bien ou mieux
  (critère 7 du protocole). Il ne sauve aucune stratégie pour autant.
- Aucune stratégie ne justifie l'envoi de signaux à BinanceSpotManager. `analyze` reste utilisable
  pour observer en `shadow`, avec `VALIDATION_STATUS=RESEARCH`.
- Détail par fenêtre, par paire, par année et par régime : `reports/<run_id>/report.md` ; liste : `report`.

### Même protocole sur l'univers de 16 paires (2026-09-30)

| Stratégie | Trades clos | E[R] net | IC95 | Profil BSM | Sans filtre 1h | Verdict | Rapport |
|---|---|---|---|---|---|---|---|
| A `DONCHIAN_VOLUME_BREAKOUT` | 1 920 | −0,11 R | [−0,18 ; −0,05] | −0,12 R | −0,14 R | REJECTED | `WF-20260930T004823Z-06aea6` |
| B `EMA_PULLBACK_CONTINUATION` | 3 672 | −0,17 R | [−0,22 ; −0,10] | −0,17 R | −0,15 R | REJECTED | `WF-20260930T012708Z-878a6a` |
| C `RANGE_REENTRY` | 1 479 | −0,21 R | [−0,31 ; −0,12] | −0,21 R | −0,21 R | REJECTED | `WF-20260930T083431Z-b29864` |

- Avec 5 à 25 fois plus de trades, les intervalles se resserrent et restent entièrement négatifs :
  ces règles perdent de façon nette, pas par manque d'échantillon.
- Le profil d'exécution de BinanceSpotManager (TP au marché, sans sortie temporelle) ne change
  pratiquement rien.
- Biais de survie : l'univers est choisi aujourd'hui parmi des paires encore cotées ; il flatte
  plutôt les résultats, ce qui renforce le rejet.

### Après l'audit look-ahead et overfitting (2026-09-30, protocole durci)

Mêmes 16 paires, code corrigé (R au risque prévu, IC par blocs de 10 jours, une bougie de retard en
coûts défavorables, gap à l'entrée à deux coûts, stop remonté pessimiste, causalité coupée sur
`available_at` ; détail dans [docs/PROTOCOL.md](docs/PROTOCOL.md)) :

| Stratégie | E[R] central, IC95 | Avant l'audit | Défavorables | Profil BSM | Sans filtre 1h | Verdict | Rapport |
|---|---|---|---|---|---|---|---|
| A | −0,11 R [−0,19 ; −0,04] | −0,11 R [−0,18 ; −0,05] | −0,13 R | −0,11 R | −0,13 R | REJECTED | `WF-20260930T143103Z-4b0da8` |
| B | −0,16 R [−0,22 ; −0,10] | −0,17 R [−0,22 ; −0,10] | −0,18 R | −0,16 R | −0,14 R | REJECTED | `WF-20260930T150703Z-5eccae` |
| C | −0,19 R [−0,27 ; −0,12] | −0,21 R [−0,31 ; −0,12] | −0,27 R | −0,19 R | −0,19 R | REJECTED | `WF-20260930T153623Z-7dd6cc` |

- Verdicts inchangés : aucun biais n'avait fabriqué ces résultats. Le contrôle de causalité renforcé
  passe sur les données réelles des trois stratégies (critère 1).
- Le scénario « stress » s'améliore nettement (A : −0,32 → −0,15 R) : l'ancien calcul divisait par
  le risque RÉALISÉ, ce qui gonflait les pertes quand l'entrée retardée se remplissait sous la limite.
- Moins de trades en coûts défavorables (A : 1 381 → 1 164) : la bougie de retard fait expirer des entrées.
- Programme de recherche : 24 exécutions sur DEVELOPMENT, 251 essais cumulés (`program_trials`).

### Lot 5 : un modèle peut-il trier les setups ? Non (2026-10-01)

Méta-labeling logistique purgé (protocole déclaré avant exécution, [docs/ML.md](docs/ML.md)) : le modèle
trie un peu les setups de A, B et C, mais la stratégie filtrée reste perdante (−0,16 à −0,22 R par
trade, intervalles entièrement négatifs). Verdict NOT_USEFUL pour les trois ; rien n'est branché.

### Horizons longs (3 et 7 jours) : pas d'avantage non plus (2026-10-01)

Les six familles D à I, criblées à 3 et 7 jours : le rendement brut dépasse les frais, mais uniquement
par la hausse générale du marché ; l'excès sur cette dérive est nul. Détail : [docs/SCREENING.md](docs/SCREENING.md).

### ML intraday (lot 5 bis) : admissible selon la règle, mais aucun avantage démontré (2026-10-01)

LightGBM, XGBoost et logistique sur 15 min + contexte 1 h/4 h, 224 essais déclarés avant exécution :
14 systèmes passent la règle de stabilité, mais le retenu reste à +0,05 % par trade avec un IC95 qui
contient 0, devient négatif en coûts défavorables et sans le 1 % des meilleurs trades, et doit tout à
2022. La période finale n'est pas consultée. Détail : [docs/ML_INTRADAY.md §12](docs/ML_INTRADAY.md).

### ML swing (lot 5 ter) : aucun avantage démontré (2026-10-01)

LightGBM, XGBoost, CatBoost et logistique sur bougies 1 h, avec contexte 4 h / 1 jour, BTC et coupe
transversale ; horizons de 1 à 7 jours ; 136 essais déclarés avant exécution, règle stricte v6.
- Aucun système n'est stable, même en ignorant les minimums par validation.
- Les probabilités ne font pas mieux que le taux de base (AUC médiane 0,51).
- La période finale n'est pas consultée.

Détail : [docs/ML_SWING.md §7](docs/ML_SWING.md).

### Positionnement du marché à terme : aucune piste (2026-10-01)

Données publiques du marché à terme USDⓈ-M (financement, prime, intérêt ouvert, ratios de comptes),
quatre conditions déclarées avant exécution, horizons de 1 à 7 jours, 12 essais.
- Aucun intervalle de l'excès (corrigé de Bonferroni) n'est entièrement au-dessus de 0.
- Les estimations positives des conditions contraires ne sont pas une preuve ; l'excès entre paires
  est nul, et plusieurs résultats à 7 jours tiennent à une seule année.
- Programme : 638 essais. La période finale n'est pas consultée.

Détail : [docs/DERIVATIVES.md](docs/DERIVATIVES.md).

### Lot 7 — portefeuilles hebdomadaires : aucune piste (2026-10-01)

Changement de question : au lieu de prédire chaque mouvement, comparer un portefeuille rééquilibré
**une fois par semaine** à sa référence (toutes les paires éligibles à parts égales, ou BTC conservé).
- 18 règles fixes, écrites avant toute exécution : classement par momentum, régimes « investi ou USDT »
  (moyennes mobiles, momentum, chaîne de Markov cachée), tendance par paire, exposition selon la
  volatilité, faible volatilité, retournement.
- Historique long : bougies 1 h depuis la cotation de chaque paire (BTC et ETH depuis août 2017),
  univers de recherche de 40 paires, appartenance mesurée à la date.
- Une « piste » ne serait pas un avantage démontré : il faudrait encore la confirmer sur des données
  jamais consultées.

Résultat (exécution unique, 18 essais, programme 656) : **aucune piste**. Aucun essai ne bat sa
référence avec un intervalle entièrement positif. Les filtres de tendance réduisent la perte maximale
(−48 à −71 % contre −82 %) sans gain de Sharpe démontré. Détail : [docs/FACTORS.md §10](docs/FACTORS.md).

### ML swing sur l'historique long : aucun avantage démontré (2026-10-01)

Même protocole que le swing, rejoué sur 2017-2025 et 40 paires (12 validations, 42 essais) : aucun
système stable (au mieux 7 validations positives sur 12, il en faut 9). Programme : 698 essais.
Détail : [docs/ML_SWING_LONG.md §10](docs/ML_SWING_LONG.md).

### Prévision de volatilité : utile (2026-10-01) — l'ampleur, pas la direction

Premier résultat positif du programme, sur ce qu'on attendait : prévoir **l'ampleur** des mouvements
des 1, 3 et 7 prochains jours. LightGBM (1 et 3 jours) et HAR + BTC (7 jours) battent la règle
« volatilité des 7 derniers jours » sur les 7 années et presque toutes les paires, avec un intervalle
corrigé de Bonferroni. Cela sert à dimensionner les positions et à placer stops et objectifs ; cela ne
dit rien de la direction ni de la rentabilité. **En service** : chaque jour, le tableau de bord donne le
mouvement typique attendu de chaque paire à 1, 3 et 7 jours (onglets Marché et Analyser une paire, et
distances TP1 / stop d'un signal évalué). Détail : [docs/VOLATILITY.md §12-13](docs/VOLATILITY.md).

### Pourquoi elles perdent, et criblage des familles suivantes

Avant frais, A, B et C n'ont pratiquement aucun avantage (≈ +0,01 R, −0,06 R, +0,03 R) : les coûts
font la perte. Le criblage `screen` teste donc d'abord l'avantage BRUT des familles D à I (retest de
cassure, retour sur support, compression, VWAP, force relative, momentum résiduel) sur les 16 paires :
**aucune ne dépasse la dérive ET les coûts**. Seule E (retour au-dessus d'un support enfoncé) montre
un excès positif cohérent, trop faible pour couvrir les frais à 4 h. Détail : [docs/SCREENING.md](docs/SCREENING.md).

## Signaux externes (groupes Telegram)

`evaluate-signal` lit un signal reçu (mêmes formats que BinanceSpotManager), applique les vetos
déterministes, décrit le contexte et donne le **taux de base historique** de la même géométrie
sur la même paire (TP1 avant SL, espérance nette en R), puis un avis REFUSE / DEFAVORABLE /
INDETERMINE / FAVORABLE. Chaque signal est enregistré ; `resolve-signals` mesure ensuite son
issue réelle et `sources` compare chaque groupe à son taux de base. Ce n'est pas une prédiction
du signal : détails et limites dans [docs/EXTERNAL_SIGNALS.md](docs/EXTERNAL_SIGNALS.md).

Paire hors univers : un signal **soumis à la main** par le propriétaire vaut validation de sa paire,
qui est ajoutée définitivement (avis `EN_ATTENTE` le temps de télécharger l'historique, puis avis
normal) ; un signal reçu automatiquement n'ajoute jamais rien. Liste et état : `universe`
([docs/UNIVERSE.md](docs/UNIVERSE.md)).

### Suivi en direct des plans

Chaque jour, CSI enregistre le plan indicatif de chaque paire (1, 3 et 7 jours) et le suit sur les bougies qui
arrivent ensuite. Un plan ne peut devenir « Favorable — prouvé en direct » qu'après au moins 50 plans de même type
terminés sur 20 jours, avec un intervalle de confiance entièrement positif : une preuve sur des données que personne
n'avait vues. Onglet Suivi, carte « Suivi en direct des plans indicatifs ».

### Tests en direct pré-inscrits

Mission du propriétaire du 2026-10-02 ([docs/FORWARD_TESTS.md](docs/FORWARD_TESTS.md)). Chaque test est
pré-inscrit avant son démarrage, puis ses règles sont figées par empreinte : une modification l'arrête pour de bon.
- Le filtre halal s'applique en amont : ce sont les décisions du propriétaire enregistrées dans CSI
  (`config/halal_screen.yaml`).
- Le journal fonctionne en ajout seul, avec des empreintes chaînées.
- Le même modèle de frais s'applique partout.
- Un rapport quotidien unique est écrit dans `reports/forward/`, avec la carte « Tests en direct » de l'onglet
  Suivi.

Tests pré-inscrits : F1_MAKER_TAKER (ordre limite contre ordre au marché à l'entrée), F2_ECHELLES (signaux de CSI
à 1 objectif contre des échelles de 2 à 7 objectifs) et F3_STABLECOINS (achat de BTC après une création d'USDT ou
d'USDC d'au moins 100 M$, lue sur les chaînes publiques, contre 20 achats placebo) et F4_TELEGRAM (signaux Telegram
reçus en direct par le bot de BSM ou déposés par le robot du propriétaire, achetés au premier prix et gérés avec le
stop suiveur, contre un achat au même moment et 20 placebos) et F5_MODELE_A (le modèle A du lot 8 v2 suivi en
direct, avec et sans feu tricolore quotidien, contre une allocation statique ; comportement, pas validation). Le
financement, l'intérêt
ouvert et une dizaine de données de contexte sont aussi relevés chaque jour. Le calendrier des unlocks n'est pas
fait, faute de source gratuite fiable.

```bash
.venv/bin/csi forward status                     # état et intégrité des journaux
.venv/bin/csi forward start F1_MAKER_TAKER       # une seule fois, code commité
.venv/bin/csi forward report                     # rapport du jour
```

### Mesurer un groupe tout de suite

Onglet « Évaluer un signal » du tableau de bord : importer l'export JSON de Telegram Desktop d'un
groupe. CSI rejoue tous ses signaux passés, comme le bot les aurait joués, et dit s'il est prouvé
(assez de signaux, gain moyen positif avec certitude, peu de messages supprimés). Un groupe prouvé
rend ses signaux FAVORABLES. Terminal : `csi audit-telegram --file result.json`. Détail :
[docs/EXTERNAL_SIGNALS.md](docs/EXTERNAL_SIGNALS.md).

## Univers de paires

16 paires USDT depuis le 2026-09-30 : BTC, ETH et 14 paires retenues par un screening halal croisé
sur trois sources publiques, plus des critères de disponibilité, d'historique et de liquidité sur
Binance Spot. Règle, sources, exclusions et limites : [docs/UNIVERSE.md](docs/UNIVERSE.md). Ce
projet ne certifie rien ; la liste se modifie dans `config/default.toml`. Les résultats du lot 2
ci-dessus portent sur BTC + ETH seulement.

## Dans Docker

Sous Ubuntu (Docker Engine et le plugin `docker-compose-v2`) :

```bash
docker network create csi-bridge      # une seule fois : réseau partagé avec BinanceSpotManager
docker compose up -d --build          # surveillance shadow, API et tableau de bord : http://127.0.0.1:8503/
docker compose ps                     # tout doit être « healthy »
```

Tout démarrer d'un coup (CSI puis le bot) : `./scripts/demarrer.sh`, ou `./scripts/demarrer.sh --reconstruire`
après une mise à jour du code. Sous Windows (Docker Desktop) : `.\scripts\docker-init.ps1` et
`.\scripts\demarrer.ps1`.

Détails (état séparé du dossier local, démarrage automatique, arrêt) : [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Serveur 24 h/24 (VPS) : [docs/VPS.md](docs/VPS.md) (installation, migration des données, accès par tunnel
SSH, sauvegardes).

Évaluer un signal Telegram dans l'état Docker (résolu ensuite automatiquement, bilan par groupe au
tableau de bord) : le plus simple est l'onglet « Évaluer un signal » du tableau de bord ; sous Windows,
`.\scripts\evaluer-signal.ps1 -Source "Nom du groupe" -Fichier signal.txt`.

API locale (lecture et évaluation seulement, 127.0.0.1:8503) pour l'interface de BinanceSpotManager :
routes, sécurité et intégration prévue dans [docs/API.md](docs/API.md).

## Tableau de bord interactif de CSI : <http://127.0.0.1:8503/>

Indépendant de BinanceSpotManager (servi par le service `api` de CSI ; sans Docker :
`.venv/bin/python -m crypto_signal_intelligence.api`). Cinq onglets :

- **Analyser une paire** : choisir une paire et un horizon (1 h, 4 h, 12 h, 1 jour, 3 jours, 7 jours).
  CSI montre :
  - la situation actuelle ;
  - ce qui s'est passé historiquement sur cette paire dans le même régime 1 h : fréquence de hausse
    avec son IC, gain net moyen après coûts, fourchette 10-90 % ;
  - un **plan indicatif** (entrée au marché, stop à 1 σ, objectif à 1,5 σ de la volatilité de
    l'horizon), rejoué sur l'historique. Son état est descriptif (« Historique positif — non validé »
    au mieux), jamais une proposition d'entrer ;
  - la carte **Marché à terme** : positionnement du moment (financement, prime, intérêt ouvert, ratios
    acheteurs/vendeurs), données publiques, avec le rang de chaque valeur dans son historique récent ;
    information seulement ([docs/DERIVATIVES.md](docs/DERIVATIVES.md)) ;
  - l'avis de ses stratégies sur la dernière bougie, **en simulation** : rien n'est publié ;
  - la carte **Prévision par modèle**, qui donne les verdicts des programmes ML. Aucun n'étant validé
    hors échantillon, CSI ne donne pas de probabilité « prédite ».

  Adresse directe : `http://127.0.0.1:8503/?paire=ETHUSDT&horizon=24h`. Si les données sont anciennes
  (surveillance arrêtée), un bouton les met à jour depuis les données publiques de Binance.
- **Marché** : toutes les paires à l'horizon choisi (prix, 24 h, régime, fréquence de hausse, état du plan),
  avec un bouton Détail ; adresse directe `http://127.0.0.1:8503/?onglet=marche&lancer=1`.
- **Opportunités** : un bouton analyse toutes les paires de l'univers à tous les horizons (environ
  20 s par paire) et liste trois choses :
  - les plans à historique positif (non validé) ;
  - les plans dont l'objectif a été atteint avant le stop dans au moins la moitié des cas comparables ;
  - les achats simulés des stratégies.

  Ce sont des fréquences passées, jamais des propositions d'entrer. Adresse directe :
  `http://127.0.0.1:8503/?onglet=opportunites&lancer=1`.
- **Évaluer un signal** : coller un signal (Telegram ou écrit à la main) → contrôles, géométrie,
  taux de base de la même géométrie, contexte, avis expliqué ; enregistré pour suivre son issue.
- **Suivi** :
  - santé de la surveillance ;
  - verdicts de tous les modèles (registre de la surveillance, et registre de recherche du PC monté en
    lecture seule) ;
  - **univers par avis halal** : bouton « Appliquer le screening », liste « Cryptos à décider » avec ses
    boutons Ajouter et Refuser ; un badge en haut de page signale ce qui attend ta décision
    ([docs/UNIVERSE.md](docs/UNIVERSE.md)) ;
  - signaux évalués, bilan des groupes, signaux trouvés par les stratégies.

Chaque pourcentage est une fréquence historique définie à côté de sa valeur, jamais une promesse :
aucune stratégie de CSI n'a démontré d'avantage exploitable à ce jour. Même analyse dans le
terminal : `.venv/bin/csi perspective ETHUSDT --horizon 24h`.

## Installation (Ubuntu)

Python 3.14 (paquet `python3.14-venv`). Pas besoin d'activer le venv : on appelle ses programmes.
`pylock.toml` (PEP 751) fige les versions mais ne liste que des roues Windows : sous Linux, on en tire des
contraintes et pip choisit les roues Linux (même méthode que la CI).

```bash
cd ~/BinanceSpotManager/CryptoSignalIntelligence
python3.14 -m venv .venv
.venv/bin/python -c "import tomllib; d = tomllib.load(open('pylock.toml', 'rb')); open('constraints.txt', 'w').write(''.join(f\"{p['name']}=={p['version']}\n\" for p in d['packages'] if 'version' in p))"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c constraints.txt -e ".[dev,ml]"
```

Sous Windows (PowerShell) : `py -m venv .venv`, `.\.venv\Scripts\python.exe -m pip install -r pylock.toml`,
puis `.\.venv\Scripts\python.exe -m pip install --no-deps -e .` ; les commandes ci-dessous s'écrivent alors
`.\.venv\Scripts\python.exe -m crypto_signal_intelligence <commande>`.

## Commandes

```bash
.venv/bin/csi doctor
.venv/bin/csi download                           # toutes les paires, 15m+1h, depuis 2021
.venv/bin/csi download --symbol BTCUSDT --timeframe 15m
.venv/bin/csi data-quality --symbol BTCUSDT
.venv/bin/csi validate-causality --symbol ETHUSDT
.venv/bin/csi backtest --strategy DONCHIAN_VOLUME_BREAKOUT
.venv/bin/csi walk-forward                       # les 3 stratégies, verdict
.venv/bin/csi walk-forward --strategy RANGE_REENTRY
.venv/bin/csi screen                             # criblage brut des familles D à I
.venv/bin/csi analyze --mode shadow              # toutes les stratégies
.venv/bin/csi analyze --symbol BTCUSDT --strategy EMA_PULLBACK_CONTINUATION
.venv/bin/csi perspective ETHUSDT --horizon 24h  # ce qui s'est passé dans des conditions comparables
.venv/bin/csi evaluate-signal --source "Suhaib" --file signal.txt
.venv/bin/csi resolve-signals                    # issues des signaux évalués
.venv/bin/csi sources                            # bilan par groupe Telegram
.venv/bin/csi universe                           # paires configurées + ajoutées par toi
.venv/bin/csi admit-halal                        # applique le screening halal aux paires hors configuration
.venv/bin/csi import-feedback --file feedback.jsonl   # retour Demo du bot
.venv/bin/csi execution-report                   # backtest / prospectif / Demo + écarts
.venv/bin/csi exit-policies                      # politiques de sortie et empreintes
.venv/bin/csi scan                               # un cycle : paires × stratégies, shadow
.venv/bin/csi run --mode shadow                  # surveillance continue (Ctrl+C pour arrêter)
.venv/bin/csi news-sources --check               # vérifie les sources d'actualités
.venv/bin/csi news --hours 24                    # actualités (observe : sans influence)
.venv/bin/csi dashboard --open                   # tableau de bord HTML (state/dashboard.html)
.venv/bin/csi backup                             # sauvegarde vérifiée de l'état (backups/)
.venv/bin/csi restore --file backups/<archive>.zip --yes   # puis publication-resume
.venv/bin/csi report                             # liste des expériences
.venv/bin/csi report --run-id <IDENTIFIANT>
.venv/bin/csi signals-reconcile
```

Recherche (travaux lourds, DEVELOPMENT seulement ; chaque protocole est déclaré avant son exécution) :

```bash
.venv/bin/csi ml-evaluate                        # lot 5 : méta-labeling des setups
.venv/bin/csi ml-intraday select                 # lot 5 bis : ML intraday
.venv/bin/csi ml-swing select                    # lot 5 ter : ML swing
.venv/bin/csi ml-swing select --long             # lot 5 quater : ML swing sur l'historique long (2017-2025, 40 paires)
.venv/bin/csi download-derivatives               # historique public du marché à terme
.venv/bin/csi screen-derivatives                 # criblage du positionnement
.venv/bin/csi download-long --research           # bougies 1 h depuis la cotation, 40 paires de recherche
.venv/bin/csi factors                            # lot 7 : portefeuilles hebdomadaires
.venv/bin/csi volatility                         # lot 7 : prévision de volatilité à 1, 3 et 7 jours
```

Tests et qualité :

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
.venv/bin/mypy
.venv/bin/pyright                # imports et noms introuvables (types : mypy)
```

Les fixtures de test sont **synthétiques** : elles vérifient le code, jamais une performance de marché.

## Pas encore disponible (lots suivants)

Lot 4 : calcul incrémental des indicateurs, plafond de RAM des travaux lourds, vérification sur
un vrai VPS/NAS, observation prolongée (écarts théorie/Demo mesurés). Lot 3 : fusion de la branche BinanceSpotManager
(relecture du propriétaire), deux entrées simulées (Binance Demo uniquement). ML (lot 5) ; agents
(lot 6). Détail et preuves : [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md).
