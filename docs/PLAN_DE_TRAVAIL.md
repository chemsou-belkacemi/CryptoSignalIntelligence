# Plan de travail — état au 2026-10-09 (plan d'octobre 2026 à janvier 2027, validé)

Document vivant : ce qui est fait, ce qui tourne, ce qui reste. Chaque ligne « à faire » se fait comme d'habitude :
déclarée avant, mesurée avec intervalle (et placebos en direct), comptée au programme. Les décisions du
propriétaire sont marquées **[propriétaire]**. Programme au 2026-10-09 : **873 essais** sur DEVELOPMENT ; période
finale réservée consultée **quatre fois** (décisions du propriétaire) : le 2026-10-03 pour la volatilité, le
2026-10-05 pour les lignes de tendance 1 h puis pour l'IA locale qui lit les graphiques, le 2026-10-09 pour le filtre
des signaux Telegram par combinaisons (voie A).

**Plan validé par le propriétaire le 2026-10-03 (« oui pour tout à part le VPS »)** : plus de nouvelle recherche
directionnelle sur les bougies ; rendre utile ce qui marche (volatilité, filtrage des signaux Telegram, boucle Demo).
Détail en § 2 bis. Décision précédente du même jour : points 1 à 4 validés (24 paires / F13 / variante K2, ordre de la liste
« mesure et données », installation d'`arch`, `hftbacktest`, `ccxt`, correction de l'onglet Marché) ; point 5
refusé (aucune donnée payante) ; points 6 à 10 en attente (voir § 5).

## 1. Fait

| Étape | Résultat | Document |
|---|---|---|
| Lots 0-2, A/B/C, criblages D à I, ML intraday / swing / swing long, marché à terme | aucune direction démontrée | `PROTOCOL.md`, `SCREENING.md`, `ML_*.md`, `DERIVATIVES.md`, `FACTORS.md` |
| Volatilité v1 (lot 7) | utile : LightGBM 1-3 j, HAR + BTC 7 j (en service) | `VOLATILITY.md` § 12-13 |
| Plan du 2026-10-02, étapes 1 à 8 | défauts de mesure corrigés ; groupes Telegram mesurés (aucun avantage) ; mission v2 (F1-F9) ; tendance journalière NON_INTERESSANT ; criblage J (rien ; J2 négatif) ; volatilité v2 (moyenne utile à 3 j) ; criblage K (K2 journalier passe : piste) | `FORWARD_TESTS.md`, `TREND_DAILY.md`, `SCREENING.md`, `VOLATILITY.md` § 14-15 |
| Volatilité à toute heure (v3) | utile à 24 h (HAR + profil heure × jour) | `VOLATILITY.md` § 16 |
| Intervalles de rendement | la règle simple est déjà bien étalonnée ; rien de mieux | `QUANTILES.md` |
| Frais centraux 7,5 pb (remise BNB) | appliqué, aucun verdict passé ne change | `PROTOCOL.md` |
| Contrôle positif | la mesure voit : +0,5 %/1 j et +2 %/7 j en criblage, corrélation 0,017 en ML, +0,1-0,2 R en walk-forward | `POSITIVE_CONTROL.md` |
| Registre unifié + rapport d'information | ML : aucune information directionnelle ; walk-forwards : avantage brut nul ; volatilité un peu mal étalonnée aux extrêmes | `INFORMATION_REPORT.md` |
| **A** — 24 paires : F13 (K2 sur 24 paires) et F14 (K2, niveaux par la volatilité prévue) | démarrés le 2026-10-03 ; A, B, C restent sur les 16 paires (banc d'essai et contrôle négatif) | `FORWARD_TESTS.md` |
| **B1** — intervalles sous contrôle positif (`arch`) | aucune méthode ne change le verdict ; criblages prudents (0 à 2 % de fausses alarmes) et peu puissants | `POSITIVE_CONTROL.md` § 6-8 |
| **B2** — univers à date (paires retirées comprises) | criblage K à date : **K2 ne tient pas hors survivance** (+0,01 % à 7 j, contre +2,04 % sur les survivantes) ; F10, F13, F14 lus avec cette réserve ; K1 nettement négatif | `UNIVERSE_PIT.md` |
| **B8 bis** — grille et DCA à date | même conclusion qu'aux survivantes ; « garder » gonflé de 3 points/mois par la survivance | `GRID_DCA.md` |
| **B3** — ticks et carnet (aggTrades) | coûts prudents (petit achat au marché ≈ 0 à 1 pb contre 3 pb supposés) ; règle des bougies optimiste de 2 à 7 % seulement | `TICKS.md` |
| **B4** — contrôle de l'ajustement | LightGBM extrait environ moitié moins qu'une régression linéaire ; une information faible n'est pas récupérée | `POSITIVE_CONTROL.md` |
| **B5** — volatilité v4 (réétalonnage, GARCH) | AUCUNE_AMELIORATION (GARCH pire de 15 à 20 %) | `VOLATILITY.md` § 17 |
| **B6** — saisonnalité (criblage S, 8 conditions) | rien ne passe ; expiration des options = piste après coup seulement | `SCREENING.md` |
| **B7** — portefeuilles hebdomadaires à date | aucun ne bat l'univers ; biais de survivance mesuré (+0,6 %/semaine) ; « paires calmes » close | `XSECTION.md` |
| **B8** — grille et DCA (survivantes) | aucune variante ne fait mieux que garder | `GRID_DCA.md` |
| **B9** — écarts entre bourses (F0_ECARTS) | en service ; nets −16 à −68 pb au premier relevé : pas d'arbitrage pour un particulier | `SPREADS.md` |
| **B10** — signaux Telegram en image | lecture locale branchée sur l'audit des groupes (`--ocr`) ; ignorée au moindre doute | `OCR.md` |
| **B11** — news de risque | classement par mots-clés en observation ; étude d'événements déclarée ; modèle local en attente d'Ollama | `NEWS.md` |
| Onglet Marché | nouveaux essais automatiques comme l'onglet Opportunités | — |
| **Mission du 2026-10-03** — phase 10.2, CNN sur images de graphiques (un essai) | `NE_PASSE_PAS` (AUC 0,506 ; arrêt à l'époque 1 : non-détection avec ce budget) | `CNN.md` |
| Phase 10.1, volatilité réalisée sur bougies de 1 minute (v5, 6 comparaisons) | `AUCUNE_AMELIORATION` : estimations favorables aux minutes (C5 à 3 j : 7 années sur 7), non démontrées | `VOLATILITY.md` § 20 |
| Phases 1.4 et 11 — bibliothèque d'indicateurs et détecteur de figures | F15 démarré le 2026-10-03 (figures classiques comprises) ; bibliothèque § 10 (niveaux, sessions, indicateurs classiques, flux, ICT avancé), utilisée par aucun test | `INDICATEURS.md`, `FORWARD_TESTS.md` |
| Phase 1.3, ajout du 2026-10-03 — données de contexte gratuites | sources validées le 2026-10-04 ; historique téléchargé, relevé quotidien en service | `CONTEXTE.md` |
| Tableau de bord — signaux des stratégies de CSI | tous consultables (pages de 20, filtre par stratégie) | — |
| Carte d'analyse technique (onglet Marché) | supports, résistances, structure, figures, zones, plan indicatif ; information seulement | `ANALYSE_TECHNIQUE.md` |
| **Prédiction, 2026-10-04** — supports et résistances contre niveaux placebo (4 essais) | RIEN : pile ou face comme un niveau au hasard (±1 pt) | `NIVEAUX.md` |
| Données de contexte contre direction de BTC et ETH (21 essais) | RIEN ; flux nets de BTC vers les plateformes : signe régulier (8 années sur 9) non démontré | `CONTEXTE_PREDICTION.md` |
| Primes coréenne et Coinbase des altcoins en coupe (4 essais) | RIEN : aucune information sur la semaine suivante | `XSECTION_PRIMES.md` |
| Figures et méthodes des analystes rejouées sur 2019 → mi-2025 (détecteur de F15, FVG, OB, sweep, BOS, CHoCH, divergences RSI ; 19 essais) | aucune supérieure au hasard ; OB, sweep et divergences RSI perdent ; double creux 75 % de TP1 pour 76 % nécessaires ; placebos tirés avant l'exécution biaisés en 4 h / 1 jour (aussi dans F15) ; piste remarquée après coup : lignes de tendance en 1 h | `FIGURES_HISTORIQUE.md` |
| Gestion du propriétaire sur le double creux (60 % au TP1, 5 TP, stop à l'entrée puis au TP précédent ; 2 essais) | NON_DEMONTRE, identique aux tiers : +0,02 R, 76 % gagnantes, +0,019 % du capital par transaction à 1 % de risque | `FIGURES_HISTORIQUE.md` |
| Stop resserré sur le double creux (moitié au toucher, clôture à 0,4, −3 % fixe ; 3 essais) | NON_DEMONTRE : deux fois plus de stops pour des pertes deux fois plus petites, même résultat par unité de risque ; zéro en 1 h | `FIGURES_HISTORIQUE.md` |
| **Cassures de ligne de tendance en 1 h, confirmation sur 214 paires jamais utilisées** (2 essais) | **PISTE_CONFIRMEE** : +0,12 R sur le hasard [+0,04 ; +0,19] ; GAIN_NON_DEMONTRE de justesse (R +0,09 [+0,02 ; +0,16] en central, [−0,01 ; +0,14] en défavorable) ; 7 années positives ; premier résultat directionnel confirmé hors échantillon | `LIGNES_DE_TENDANCE.md` |
| Lignes de tendance 1 h sur la période réservée (2025-07 → 2026-09, lecture unique, 1 essai) | **NON_CONFIRMEE** : excès +0,04 [−0,06 ; +0,15], R −0,03 après frais ; dépend d'un seul trimestre ; aucun usage, suivi par F15 | `LIGNES_DE_TENDANCE.md` |
| IA locale qui lit les graphiques (Qwen2.5-VL 7b, Gemma 3 12B ; 400 moments de 2025-2026 ; 2 essais) | PAS_MIEUX pour les deux : bon sens 51 % et 47 % (50,5 % de hausses), aucune information démontrée, pas même avant les plus forts mouvements ; pas de test en direct | `IA_GRAPHES.md` |
| **Combinaisons du 2026-10-08** — profil de volume, flux, absorption et votes de 2 à 5 briques (étapes 1 et 2, 20 essais DEV) | RIEN partout ; toutes perdent de −0,06 à −0,16 R après frais | `COMBINAISONS.md` (branche `recherche/combinaisons`) |
| Filtre des signaux Telegram de 2026 par ces briques (voie A, période réservée, lecture unique, 2 essais) | RIEN : FILTRE_3 p = 0,21 puis 0,52 ; FILTRE_4 p = 0,19 puis 0,50 ; les signaux eux-mêmes ≈ 0 R après frais (−0,06 en étude, +0,03 à l'écart) ; voie B (en direct) non lancée **[propriétaire]** | `COMBINAISONS.md` § 11-12 |
| Étude « météo du marché » (feu à 6 dangers, jours rouges contre les autres) | **INSTRUMENT_NON_VALIDE** le 2026-10-09 : la 3e et dernière version de l'outil échoue à deux contrôles synthétiques (queues épaisses, effet de levier) ; étude historique abandonnée, 0 essai, aucune donnée réelle lue ; F17 possible (version Student valide), non démarré **[propriétaire]** | `METEO_MARCHE.md` (branche `recherche/meteo`) |

## 2. En cours

**Tests en direct** (verdicts le 2026-12-25, revue intermédiaire le 2026-11-13) : F1 maker/taker · F2 échelles de
TP · F3 émissions de stablecoins · F4 signaux Telegram en direct · F5 modèle A et feu tricolore · F6 capitulation ·
F7 listings Upbit/Coinbase · F8 filtre de news · F9 purge de l'intérêt ouvert · F10 cassure journalière d'un pivot
(K2) · F11 veto pression vendeuse (J2) · F12 prévisions de volatilité · F13 K2 sur 24 paires · F14 K2 à niveaux
par la volatilité · F16 signaux Telegram en image (démarré le 2026-10-03) · **F15 détecteur de figures** (démarré le
2026-10-03 ; verdict vers mi-mars 2027, figures 1 jour tenues 60 jours). Relevés : F0_ECARTS (écarts entre bourses),
news de risque (étude évaluée si 30 événements), données de contexte (`CONTEXTE.md`).

**Relevé de liquidité en shadow** (demande du propriétaire du 2026-10-08, `LIQUIDITE.md`) : carnet Binance et flux des
klines 1 min à chaque signal Telegram et toutes les 15 min sur les paires suivies ; information seulement, aucune
influence sur les tests, les avis ou BSM. **En service depuis le 2026-10-08** (commit 5b074b1). Protocole de mesure à
pré-inscrire quand il y aura quelques semaines et quelques centaines de signaux relevés.

**Collecteur en shadow** (demande du propriétaire du 2026-10-09, `COLLECTE.md`) : service Docker `collecteur`, séparé
de la surveillance, qui enregistre en continu ce que les bougies ne contiennent pas : liquidations du marché à terme,
carnet depth20 (16 paires + appels actifs de l'assistant), flux des transactions, options Deribit (DVOL, IV ATM,
asymétrie, max pain), attention (pages vues Wikipédia par jour, CoinGecko trending par heure ; Reddit exige une
connexion depuis 2026 : NON_DISPONIBLE). Journaux `C_*.jsonl` en ajout seul, liste fermée
d'adresses (`data/http.py` inchangé), `GET /collecte`, carte « Collecte en shadow ». **Aucun test, aucun seuil,
aucune prédiction** : après 14 jours de journaux, pré-inscription de tests en direct (événement → placebo) sur les
comptages seulement. Google Trends NON_DISPONIBLE. **Liquidations MUET depuis le réseau du PC** (flux dérivés de Binance bloqués, constaté
le 2026-10-09 ; réessai horaire, à vérifier sur le VPS). **Codé et testé le 2026-10-09 (branche `feat/collecteur`), à
déployer** : `docker compose up -d --build` (le service part sans profil) ; champs Deribit à vérifier au déploiement.

**Assistant de marché** (demande du propriétaire du 2026-10-09, `ASSISTANT.md`) : à chaque clôture 4 h UTC, lecture du
marché (feu, BTC), du régime journalier de chaque paire halal suivie par F15, d'une configuration (repli puis reprise
en hausse, rejet du support en range) et de filtres (carnet, volatilité, gain/risque, macro, news, discipline) ; appels
en SHADOW avec niveaux, score et explication, boîte Telegram pour le relais, carte « Assistant » et `GET /assistant`.
Mesuré par le test en direct **F18_ASSISTANT** (pré-inscrit dans `FORWARD_TESTS.md`, 20 placebos à ±84 h) ; **aucun
gain démontré**, verdict attendu `NON_DEMONTRE` ou `INSUFFISANT`. **En service et démarré le 2026-10-09 à 17:52 UTC**
(commit 9c1c162, essai FWD-20261009T175235Z-1a1a9a ; revue intermédiaire le 2026-11-20, évaluation le 2027-01-01).
Les appels et leurs résultats sont envoyés au propriétaire par le bot du relais (`TELEGRAM_RELAY.md`), qui le reconnaît
par ses transferts. Toute retouche de `assistant/*`, `forward/f18.py`, `risk/market_light.py`, `forward/liquidity_log.py`,
`news/risk.py`, `risk/advice.py` ou `data/store.py` arrête F18 (liste dans `ASSISTANT.md`).

**Feu de protection du marché** (demande du propriétaire du 2026-10-08, `METEO_PROTECTION.md`) : VERT / ORANGE /
ROUGE / INCONNU d'après la volatilité prévue de BTC (rang sur 365 jours), BTC contre son EMA50 et la largeur ;
`GET /meteo`, carte de l'onglet Marché, journal quotidien. Outil de gestion du risque, pas une stratégie, **aucun gain
démontré** ; l'étude d'une règle voisine a été abandonnée (instrument non validé), il ne sera mesuré que par son propre
journal (un essai de plus). **En service dans CSI depuis le 2026-10-08** (commit 1e39f6d, historique du rang calculé).
Dépend du journal de F12, qui s'arrête le 2026-12-25 : une autre source sera nécessaire avant.

**BSM** (main 038add5) : garde-fou « Feu de protection CSI » et **tous les liens avec CSI désactivés par défaut** (avis,
conseil de taille, volatilité haute, retour d'exécution), décision du propriétaire du 2026-10-08. Reste
**[propriétaire]** : `git pull` et `make up` sur le VPS.

Aucun calcul de recherche en cours.

## 2 bis. Plan d'octobre 2026 à janvier 2027 (validé le 2026-10-03)

1. **Tests en direct** : rien n'y touche ; revue intermédiaire le 2026-11-13, verdicts le 2026-12-25.
2. **Volatilité, confirmation puis branchement** :
   - **fait le 2026-10-03** : confirmation sur la période finale réservée, lecture unique
     (`VOLC-20261003T101841Z-4d3ffb`, `VOLATILITY.md` § 19) : **H24 confirmée** (prévision horaire à 24 h) ;
     **D7 non concluant**, séquence arrêtée : les prévisions à 1, 3 et 7 jours ne sont pas confirmées ;
   - **fait** : branchement de H24 en **shadow** (`RISK_SHADOW.md`) : ampleur typique sur 24 h et taille relative à
     risque égal, carte « Risque à 24 h » de l'onglet Marché, journal quotidien ; aucune influence ;
   - **fait le 2026-10-06** (demande du propriétaire) : protocole déclaré `RISK_PROTOCOL.md` ; BSM affiche le
     conseil de CSI pour chaque signal automatique, sans l'appliquer (branche BSM `feat/taille-risque-traders`) ;
     lecture unique à 60 positions et pas avant le 2026-11-13 ; données de BSM du VPS nécessaires **[propriétaire]** ;
     abstention pas avant 60 jours d'historique.
3. **Telegram** :
   - **[propriétaire]** créer le deuxième bot et poser son jeton, puis démarrer le relais (guide :
     `TELEGRAM_RELAY.md`) ; le relais, son service Docker et ses tests sont prêts ;
   - **[propriétaire]** refaire les exports de tes groupes **avec les photos** (les trois exports actuels ont été
     faits sans : 348, 9 350 et 251 images manquantes) ; l'audit d'un dossier entier est prêt
     (`audit-telegram --dir … --ocr`) ;
   - ensuite : garder seulement les groupes prouvés sur historique, F4 mesure les autres en direct.
4. **Boucle Demo (lot 4)** : dès que BSM remonte des exécutions, rapport frais réels / glissement / entrées
   manquées, sur au moins 4 semaines.
5. **Branches BSM** : fusionnées et poussées le 2026-10-03 sur demande du propriétaire (`REVUE_BRANCHES_BSM.md`) ;
   le bot tourne avec le code fusionné (image reconstruite le 2026-10-03 à 16:14, après la fusion). BSM tourne
   depuis le 2026-10-02 à 23 h sur un petit VPS (signaux Telegram, Binance Demo) ; le worker du PC est en veille.
   - **2026-10-05** : corrections tirées du journal Telegram du bot (stop au TP précédent, pertes annoncées frais
     compris, TP atteint avant l'achat, reliquats sous les minimums, filtre de History), fusionnées et poussées sur
     demande du propriétaire (BSM `main` c00e089) ;
   - **2026-10-05, branche `feat/suivi-canaux-protection`** (bed5d5d, c640d2f ; 1 128 tests BSM réussis),
     fusionnée et poussée sur demande du propriétaire (BSM `main` f5f8022) : résultats par trader ou canal (nom lu en tête du signal : 867 signaux lisibles
     sur 874 de ses exports), règle « canal perdant » (désactivée par défaut), protection en cas de chute de BTC
     (−3 % en 4 h → entrées automatiques suspendues 6 h, rien n'est vendu), rapport quotidien Telegram, « BSM face
     au marché » sur le Dashboard, achat gardé (mesuré) ou annulé si le TP1 arrive avant. Mise à jour du VPS
     **[propriétaire]** : `git pull` puis `make up`.
   - **2026-10-05, CSI** : même lecture du nom du trader dans `external/parser.group_of` (décision du
     propriétaire ; `EXTERNAL_SIGNALS.md`, Historique), forme canonique stricte après relecture ; en service pour
     F4 et F16 au redémarrage de la surveillance (aucune décision F4/F16 n'existait).
   - **Constat du 2026-10-05** : F4 et F16 n'ont rien reçu depuis leur démarrage (journal : la seule ligne de
     démarrage). Ils lisent la boîte de BSM montée depuis ce PC, vide depuis que BSM tourne sur le VPS, et aucun
     fichier du robot n'a été déposé. Sans source, leur verdict du 2026-12-25 sera vide **[propriétaire]**.
6. **Recherche** : gelée, sauf source d'information nouvelle et gratuite, sur demande du propriétaire, déclarée et
   comptée.
7. **2026-12-25** : un test qui passe est confirmé sur données jamais vues puis en Demo ; si rien ne passe,
   « aucun avantage directionnel » est acté, CSI reste filtre et gestionnaire de risque, rien ne passe en réel.

## 3. À faire après les verdicts

- **Après F2** : échelles de TP dans les stratégies si une échelle gagne.
- **Après F10 et F13** : variantes de K2 (retest, pivot hebdomadaire), seulement si K2 est confirmé en direct ;
  peu probable depuis le criblage à date (K2 ne tient pas hors survivance).
- **Veto K1** (rebond sur support, nettement négatif à date) : à déclarer avant tout usage, s'il sert un jour.
- **Après F11** : veto J2 pré-inscrit si l'excès négatif se confirme.
- **VOTE_V1** : activation seulement avec les composants qui auront passé leur seuil le 2026-12-25.
- **News de risque** : veto candidat seulement si l'étude d'événements le montre, puis nouveau protocole.

## 4. À faire — exécution et produit

- **Signaux en image** — **fait le 2026-10-06** (branche `feat/audit-images-dashboard`) : audit avec lecture des
  images depuis le tableau de bord. Le navigateur n'envoie pas les photos : le dossier de l'export est copié dans
  `exports/` (Docker : `./exports`, lecture seule dans `api` et `tools`), la carte « Bilan d'un groupe » liste les
  dossiers (images présentes sur nommées), lance l'audit en arrière-plan (un seul à la fois, jeton exigé) et montre
  sa progression puis le bilan avec le bloc « Signaux lus sur image » ; même preuve et même rapport que la commande
  (`OCR.md`, « Depuis le tableau de bord » ; `tests/test_api_exports.py`). Reste **[propriétaire]** : fusionner la
  branche, reconstruire l'image (`docker compose up -d --build`), `mkdir -p exports` puis y copier les exports faits
  avec les photos (IN CRYPTO, LEGEND TRADING ; AL-MAHWASHI à refaire en JSON), et vérifier un vrai audit depuis la
  page (non vérifié en Docker). Le contrôle du prix est couvert par le rejeu commun ; le `tickSize` est écarté (il
  change au fil du temps), voir `OCR.md`.

## 5. Décisions qui attendent le propriétaire

- **Fait le 2026-10-06 — relais Telegram** : le propriétaire a créé deux bots (CSI, BSM). Son relais personnel
  (`~/BinanceSpotManager/RelaisTelegram`, compte Telegram, service utilisateur systemd) transfère ses 4 canaux
  (EL MAHWASHI, LEGEND TRADING, IN CRYPTO, WHALE HUNTING) au bot CSI ; le relais de CSI (`telegram-relay`) les
  dépose pour F4/F16 depuis 17:17 UTC. Carte « Relais Telegram » dans l'onglet Suivi. Paires USDT de ces canaux
  ajoutées à l'univers (groupes de confiance halal, `UNIVERSE.md`). Cryptos à une lettre lues (CSI et BSM).
- **[propriétaire] Groupes VIP de son ami** (EL MAHWASHI VIP, IN CRYPTO VIP) : identifiants de conversation à
  ajouter (relais de l'ami, `lister`).
- **[propriétaire] BSM sur le nouveau bot** : pas pour l'instant (choix du 2026-10-06) ; BSM garde l'ancien bot.
- **[propriétaire] Exports avec photos** : IN CRYPTO et LEGEND TRADING reçus le 2026-10-04 (audit avec lecture des
  images en cours) ; **AL-MAHWASHI CRYPTO à refaire au format JSON** (l'export du 2026-10-04 est en HTML).
- **[propriétaire] VPS** : refusé pour CSI le 2026-10-03 (kit prêt dans `VPS.md`) ; BSM y tourne depuis le
  2026-10-02 à 23 h.
- **[propriétaire] Mise à jour du VPS** avec BSM `main` d888506 (le 2026-10-06, l'ami qui gère le VPS n'était pas
  disponible) : branche sécurité (bot muet, perte max du jour, liquidité, commandes Telegram, alertes de connexion,
  stop de secours), corrections du 2026-10-05 (reliquat, statut, prix, rapport), taille selon le risque, trader
  perdant, conseil de taille de CSI, cryptos à une lettre, interrupteurs « toutes les conversations de confiance /
  toutes les cryptos ». Vérifier `docker compose version` (≥ 2.24), `git pull`, `make up`, puis acquitter l'alerte
  critique de FETUSDT. En attendant : listes d'actifs et de conversations remplies à la main dans Settings
  (`~/BinanceSpotManager/actifs_tous_binance.txt`).
- **[propriétaire] Secrets affichés le 2026-10-05** (`docker compose config`) : renouveler la clé Binance Demo,
  le jeton du bot Telegram (`/revoke` dans @BotFather) et `CSI_API_TOKEN`.
- **Source de F4/F16** : réglée le 2026-10-06 par le relais (voir plus haut). Le PC doit rester allumé ; plus
  tard, VPS de 8 Go / 4 vCPU / 80 Go conseillé pour CSI + BSM (le relais garde alors la session Telegram du
  propriétaire sur le serveur).
- **[propriétaire] Fournisseur = trader ou outil** : un indicateur automatique signé du nom d'un trader (« Suhaib
  AlMashhadani Harmonic Indicator ») compte aujourd'hui avec ses appels manuels ; à trancher avant le premier
  événement F4/F16 (relecture du 2026-10-05).
- **[propriétaire] Variantes réunies** (`NAME_ALIASES`) : « AL-MAHWASHI CRYPTO TRADING » = « AL-MAHWASHI CRYPTO »,
  « ALAFIFY TRADING » = « ALAFIFY » ; toute autre fusion se déclare dans cette liste.
- **[propriétaire] Avant la prochaine importation d'historique** (`audit-telegram`, `POST /sources/history`, qui peut
  créer une preuve sur-le-champ) : une preuve lève les vetos de tout signal portant le même nom, quelle que soit la
  conversation ; décider si elle doit aussi être liée à la conversation, sinon une copie ou une imitation du nom
  en hériterait (relectures du 2026-10-05). Aucune preuve n'existe aujourd'hui.
- **[propriétaire] Taille des positions BSM selon le risque** : à discuter (point 4 de la liste du 2026-10-05).
- **[propriétaire] Ollama** (modèle local des news de risque) : installation par `sudo` dans ton terminal,
  `curl -fsSL https://ollama.com/install.sh | sh` puis `ollama pull qwen3:8b` (environ 5 Go, tient dans les 6 Go
  de la carte graphique). Facultatif : les mots-clés tournent sans lui.
- **[propriétaire] Location de BSM** : branche dédiée, verrou Demo conservé, profils de risque, rapport lisible.

## 6. Écartés (avec la raison)

- **Données payantes** (Coinglass, Glassnode, Tardis) : refusées par le propriétaire le 2026-10-03.
- Indicateurs et figures classiques supplémentaires (RSI, MACD, Fibonacci, chandeliers, Ichimoku…) : déjà criblés
  ou équivalents, sans avantage.
- Agents LLM « tout-en-un » (TradingAgents, Vibe-Trading, FinRL…) : aucune preuve reproduite, mémoire du LLM qui
  contamine tout backtest, non reproductibles ; seulement en test en direct si le propriétaire le demande.
- Bots du marché (Freqtrade, Jesse, Alpha Prime…) : backtests en bougies sans file d'attente, optimisation qui
  surapprend ; à mesurer par leurs trades réels contre placebos, jamais à croire sur leurs chiffres.
- Portage spot long + perpétuel court : flux de type intérêt, hors cadre halal.
- `ccxt` en service : ses appels réseau contournent la liste blanche ; installé pour la recherche seulement.
