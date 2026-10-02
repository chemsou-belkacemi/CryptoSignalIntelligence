# Plan de travail — état au 2026-10-03

Document vivant : ce qui est fait, ce qui tourne, ce qui reste, dans l'ordre proposé. Chaque ligne « à faire » se
fait comme d'habitude : déclarée avant, mesurée avec intervalle (et placebos en direct), comptée au programme. Les
décisions du propriétaire sont marquées **[propriétaire]**. Programme au 2026-10-03 : **759 essais** sur
DEVELOPMENT ; période finale réservée **jamais consultée**.

## 1. Fait

| Étape | Résultat | Document |
|---|---|---|
| Lots 0-2, A/B/C, criblages D à I, ML intraday / swing / swing long, marché à terme, portefeuilles hebdomadaires | aucune direction démontrée | `PROTOCOL.md`, `SCREENING.md`, `ML_*.md`, `DERIVATIVES.md`, `FACTORS.md` |
| Volatilité v1 (lot 7) | utile : LightGBM 1-3 j, HAR + BTC 7 j (en service) | `VOLATILITY.md` § 12-13 |
| Plan du 2026-10-02, étapes 1 à 8 | défauts de mesure corrigés ; groupes Telegram mesurés (aucun avantage) ; mission v2 (F1-F9) ; tendance journalière NON_INTERESSANT ; criblage J (rien ; J2 négatif) ; volatilité v2 (moyenne utile à 3 j) ; criblage K (K2 journalier passe : piste) | `FORWARD_TESTS.md`, `TREND_DAILY.md`, `SCREENING.md`, `VOLATILITY.md` § 14-15 |
| Volatilité à toute heure (v3) | utile à 24 h (HAR + profil heure × jour) | `VOLATILITY.md` § 16 |
| Intervalles de rendement | la règle simple est déjà bien étalonnée ; rien de mieux | `QUANTILES.md` |
| Frais centraux 7,5 pb (remise BNB) | appliqué, aucun verdict passé ne change | `PROTOCOL.md` |
| Contrôle positif | la mesure voit : +0,5 %/1 j et +2 %/7 j en criblage, corrélation 0,017 en ML, +0,1-0,2 R en walk-forward | `POSITIVE_CONTROL.md` |
| Registre unifié + rapport d'information | ML : aucune information directionnelle ; walk-forwards : avantage brut nul ; volatilité un peu mal étalonnée aux extrêmes | `INFORMATION_REPORT.md` |

## 2. En cours (tests en direct, verdicts le 2026-12-25, revue intermédiaire le 2026-11-13)

F1 maker/taker · F2 échelles de TP · F3 émissions de stablecoins · F4 signaux Telegram en direct · F5 modèle A et
feu tricolore · F6 capitulation · F7 listings Upbit/Coinbase · F8 filtre de news · F9 purge de l'intérêt ouvert ·
F10 cassure journalière d'un pivot (K2) · F11 veto pression vendeuse (J2) · F12 prévisions de volatilité.

## 3. À faire — mesure et données (gratuit), dans cet ordre

1. **Régler les intervalles sous contrôle positif** : les criblages sont trop prudents (0 % de fausses alarmes
   pour 2,5 % attendus), l'intraday un peu trop étroit (8 %). Méthode de référence : bootstrap stationnaire et
   tests de Diebold-Mariano de la bibliothèque **`arch`** (licence NCSA) ; gain : plus de puissance sans plus de
   fausses alarmes.
2. **Univers à date** (paires retirées de la cote, archives Binance) : enlever le biais de survivance de tous les
   backtests. Condition pour croire un résultat limite.
3. **Ticks et carnet** (archives publiques Binance : aggTrades, bookTicker, bookDepth, liquidations) + moteur
   **`hftbacktest`** (MIT) : vrai glissement, file d'attente des ordres limites, maker sur l'historique, delta de
   volume cumulé, balayages de liquidité.
4. **Contrôle positif de l'ajustement** : un LightGBM retrouve-t-il une variable faiblement informative plantée ?
   (limite déclarée du contrôle actuel).
5. **Réétalonnage de la volatilité** (déciles bas sous-estimés à 1-3 j, 7 j surestimé d'environ 10 %) : protocole
   v4 pré-inscrit, avec les références GARCH/HAR d'`arch`.
6. **Saisonnalité directionnelle** (heure, jour, fin de mois, heures de financement, expirations d'options) :
   criblage pré-inscrit.
7. **Momentum transversal hebdomadaire** sur univers large et à date (DUAL_MOM28 avait raté de peu).
8. **Stratégies sans prédiction** (grid, DCA) par régime, coûts honnêtes : la catégorie des bots du marché.
9. **Écarts entre bourses et primes** (Coinbase, coréenne) avec **`ccxt`** (MIT) **en lecture seule** : relevé à
   la minute, réponse à la question « arbitrage pour un particulier ».
10. **Signaux images** : OCR local des captures Telegram (60 % des signaux) branché sur F4 et l'audit des groupes.
11. **Classement de news par LLM local** (hack, retrait, réglementation) en observation, comme veto de risque.
12. **Après F2** : échelles de TP dans les stratégies si une échelle gagne. **Après F10** : variantes de K2
   (retest, pivot hebdomadaire). **Après F11** : veto J2 pré-inscrit si l'excès négatif se confirme.

## 4. À faire — exécution et produit

- **Boucle Demo** : BSM n'a encore remonté aucune exécution ; dès qu'il y en a, rapport d'exécution (frais réels
  contre 7,5 pb, glissement, entrées manquées). Lot 4 : observation prolongée théorie / Demo.
- **Deuxième jeton de bot Telegram** **[propriétaire]** pour la source en direct de F4.
- **VPS** **[propriétaire]** : migration CSI + BSM (kit dans `VPS.md`), un seul bot à la fois.
- **Location de BSM** **[propriétaire]** : branche dédiée, verrou Demo conservé, profils de risque, rapport lisible.
- **VOTE_V1** : activation seulement avec les composants qui auront passé leur seuil le 2026-12-25.

## 5. Décisions qui attendent le propriétaire

- **Branchements de volatilité** (moyenne à 3 j, HAR + profil à 24 h) : après F12 ou après la période finale.
- **Période finale réservée** : la consulter un jour (une seule fois) ou la garder vierge.
- **Branches BSM** `feat/csi-v2-drop` et `feat/signal-routing` : à jour localement, ni fusionnées ni poussées.
- **Données payantes** : Coinglass ou Glassnode (≈ 50 $/mois, positionnement et flux à l'échelle des semaines) ;
  Tardis seulement si on va vers l'intraday ou l'exécution.

## 6. Écartés (avec la raison)

- Indicateurs et figures classiques supplémentaires (RSI, MACD, Fibonacci, chandeliers, Ichimoku…) : déjà criblés
  ou équivalents, sans avantage.
- Agents LLM « tout-en-un » (TradingAgents, Vibe-Trading, FinRL…) : aucune preuve reproduite, mémoire du LLM qui
  contamine tout backtest, non reproductibles ; seulement en test en direct si le propriétaire le demande.
- Bots du marché (Freqtrade, Jesse, Alpha Prime…) : backtests en bougies sans file d'attente, optimisation qui
  surapprend ; à mesurer par leurs trades réels contre placebos, jamais à croire sur leurs chiffres.
- Portage spot long + perpétuel court : flux de type intérêt, hors cadre halal.
