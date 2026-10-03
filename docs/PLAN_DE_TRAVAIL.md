# Plan de travail — état au 2026-10-03 (après « 1 à 4 ok, 5 non, 6 à 10 attendre »)

Document vivant : ce qui est fait, ce qui tourne, ce qui reste. Chaque ligne « à faire » se fait comme d'habitude :
déclarée avant, mesurée avec intervalle (et placebos en direct), comptée au programme. Les décisions du
propriétaire sont marquées **[propriétaire]**. Programme au 2026-10-03 : **783 essais** sur DEVELOPMENT ; période
finale réservée **jamais consultée**.

Décision du propriétaire du 2026-10-03 : points 1 à 4 validés (24 paires / F13 / variante K2, ordre de la liste
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

## 2. En cours

**Tests en direct** (verdicts le 2026-12-25, revue intermédiaire le 2026-11-13) : F1 maker/taker · F2 échelles de
TP · F3 émissions de stablecoins · F4 signaux Telegram en direct · F5 modèle A et feu tricolore · F6 capitulation ·
F7 listings Upbit/Coinbase · F8 filtre de news · F9 purge de l'intérêt ouvert · F10 cassure journalière d'un pivot
(K2) · F11 veto pression vendeuse (J2) · F12 prévisions de volatilité · F13 K2 sur 24 paires · F14 K2 à niveaux
par la volatilité. Relevés : F0_ECARTS (écarts entre bourses), news de risque (étude évaluée si 30 événements).

**Calcul en cours** : **B2, univers à date**. Téléchargement de l'historique 1 h des 223 paires hors univers
(dont 71 retirées de la cote), environ une paire par minute. Ensuite, dans l'ordre :
1. reconstruire l'appartenance mensuelle avec les exclusions historiques (stablecoins, tokens à levier) ;
2. criblage K à date (4 essais, déclaré dans `UNIVERSE_PIT.md`) : K2 tient-il hors biais de survivance ?
3. grille et DCA à date (`csi grid-dca --point-in-time`, même déclaration que sur les survivantes).

## 3. À faire après les verdicts

- **Après F2** : échelles de TP dans les stratégies si une échelle gagne.
- **Après F10 et F13** : variantes de K2 (retest, pivot hebdomadaire), seulement si K2 est confirmé en direct.
- **Après F11** : veto J2 pré-inscrit si l'excès négatif se confirme.
- **VOTE_V1** : activation seulement avec les composants qui auront passé leur seuil le 2026-12-25.
- **News de risque** : veto candidat seulement si l'étude d'événements le montre, puis nouveau protocole.

## 4. À faire — exécution et produit

- **Boucle Demo** : BSM n'a encore remonté aucune exécution ; dès qu'il y en a, rapport d'exécution (frais réels
  contre 7,5 pb, glissement, entrées manquées). Lot 4 : observation prolongée théorie / Demo.
- **Signaux en image** : contrôle du prix de l'image contre le prix Binance à l'heure du message, et niveaux
  multiples du `tickSize` ; lecture des images depuis le tableau de bord (aujourd'hui : terminal seulement).

## 5. Décisions qui attendent le propriétaire

- **[propriétaire] Branches BSM** `feat/csi-v2-drop` et `feat/signal-routing` : à jour localement, ni fusionnées ni
  poussées (point 6, en attente).
- **[propriétaire] Branchements de volatilité** (moyenne à 3 j, HAR + profil à 24 h) : après F12 ou après la
  période finale (point 7, en attente).
- **[propriétaire] Période finale réservée** : la consulter un jour (une seule fois) ou la garder vierge (point 8,
  en attente).
- **[propriétaire] Deuxième jeton de bot Telegram** pour la source en direct de F4 (point 9, en attente).
- **[propriétaire] VPS** : migration CSI + BSM (kit dans `VPS.md`), un seul bot à la fois (point 10, en attente).
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
