# Plan de travail — état au 2026-10-04 (plan d'octobre 2026 à janvier 2027, validé)

Document vivant : ce qui est fait, ce qui tourne, ce qui reste. Chaque ligne « à faire » se fait comme d'habitude :
déclarée avant, mesurée avec intervalle (et placebos en direct), comptée au programme. Les décisions du
propriétaire sont marquées **[propriétaire]**. Programme au 2026-10-04 : **798 essais** sur DEVELOPMENT ; période
finale réservée consultée **une fois**, le 2026-10-03, pour la volatilité seulement (décision du
propriétaire).

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

## 2. En cours

**Tests en direct** (verdicts le 2026-12-25, revue intermédiaire le 2026-11-13) : F1 maker/taker · F2 échelles de
TP · F3 émissions de stablecoins · F4 signaux Telegram en direct · F5 modèle A et feu tricolore · F6 capitulation ·
F7 listings Upbit/Coinbase · F8 filtre de news · F9 purge de l'intérêt ouvert · F10 cassure journalière d'un pivot
(K2) · F11 veto pression vendeuse (J2) · F12 prévisions de volatilité · F13 K2 sur 24 paires · F14 K2 à niveaux
par la volatilité · F16 signaux Telegram en image (démarré le 2026-10-03) · **F15 détecteur de figures** (démarré le
2026-10-03 ; verdict vers mi-mars 2027, figures 1 jour tenues 60 jours). Relevés : F0_ECARTS (écarts entre bourses),
news de risque (étude évaluée si 30 événements), données de contexte (`CONTEXTE.md`).

Aucun calcul de recherche en cours.

## 2 bis. Plan d'octobre 2026 à janvier 2027 (validé le 2026-10-03)

1. **Tests en direct** : rien n'y touche ; revue intermédiaire le 2026-11-13, verdicts le 2026-12-25.
2. **Volatilité, confirmation puis branchement** :
   - **fait le 2026-10-03** : confirmation sur la période finale réservée, lecture unique
     (`VOLC-20261003T101841Z-4d3ffb`, `VOLATILITY.md` § 19) : **H24 confirmée** (prévision horaire à 24 h) ;
     **D7 non concluant**, séquence arrêtée : les prévisions à 1, 3 et 7 jours ne sont pas confirmées ;
   - **fait** : branchement de H24 en **shadow** (`RISK_SHADOW.md`) : ampleur typique sur 24 h et taille relative à
     risque égal, carte « Risque à 24 h » de l'onglet Marché, journal quotidien ; aucune influence ;
   - à venir : après quelques semaines de journal, protocole déclaré pour passer taille et stop à BSM en Demo
     **[propriétaire]** ; abstention pas avant 60 jours d'historique.
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
   le bot tourne avec le code fusionné (image reconstruite le 2026-10-03 à 16:14, après la fusion).
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

- **Signaux en image** : lecture des images depuis le tableau de bord (aujourd'hui : terminal seulement ; il
  faudrait envoyer le dossier de l'export entier). Le contrôle du prix est couvert par le rejeu commun ; le
  `tickSize` est écarté (il change au fil du temps), voir `OCR.md`.

## 5. Décisions qui attendent le propriétaire

- **[propriétaire] Deuxième bot Telegram** : jeton dans `.env`, puis `docker compose --profile telegram up -d
  telegram-relay` (`TELEGRAM_RELAY.md`).
- **[propriétaire] Exports avec photos** : IN CRYPTO et LEGEND TRADING reçus le 2026-10-04 (audit avec lecture des
  images en cours) ; **AL-MAHWASHI CRYPTO à refaire au format JSON** (l'export du 2026-10-04 est en HTML).
- **[propriétaire] VPS** : refusé pour l'instant (2026-10-03) ; kit prêt dans `VPS.md`.
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
