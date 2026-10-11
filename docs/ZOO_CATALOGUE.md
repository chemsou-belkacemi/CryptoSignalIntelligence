# Zoo des stratégies : recensement des stratégies (rédigé le 2026-10-10, révisé le 2026-10-11, avant tout code et toute exécution)

Programme « zoo des stratégies » : avoir mesuré d'ici 2027 les stratégies connues et réalisables par CSI
(Binance Spot, achat seul, cadre halal), avec une seule méthode fixée d'avance. Ce catalogue recense les stratégies,
leurs paramètres standard publiés et leur statut ; le protocole pré-inscrit de celles qui restent à mesurer est
[ZOO.md](ZOO.md).

**Aucun calcul n'a été fait pour écrire ce document.** Aucun rendement, aucun comptage de signaux, aucune lecture de
données réelles : il recopie des verdicts déjà inscrits dans les documents du programme et décrit des règles.
Rien ici n'annonce une rentabilité ; une stratégie « à tester » n'est pas une piste.

## Légende

**Statuts** (un seul par ligne, le plus avancé ; un test en direct qui suit une mesure historique est cité dans la
colonne « Renvoi ») :

| Statut | Sens |
|---|---|
| `TESTÉ` | mesuré sur l'historique par un protocole déclaré d'avance ; document et verdict cités |
| `EN_DIRECT` | pas de mesure historique, mais un test en direct pré-inscrit et démarré (`FORWARD_TESTS.md`) |
| `À_TESTER (zoo)` | ne demande que des bougies Binance ou des données déjà en magasin : mesuré par [ZOO.md](ZOO.md) |
| `À_TESTER (attente)` | données manquantes, test en direct pré-inscrit mais non démarré, ou mesure en direct seulement (historique trop court) : branché par un autre chantier |
| `À_TESTER (hors moteur)` | données disponibles, mais la mesure exige un protocole propre (coût du contrôle sous H0, pré-inscription déjà faite ailleurs) |
| `IMPOSSIBLE` | raison donnée (liste ci-dessous) |
| `DOUBLON` | presque les mêmes signaux qu'un test du zoo (« déjà vu : … ») : retiré du zoo, mesuré par l'autre |

**Raisons de `IMPOSSIBLE`.** Les quatre de la demande, plus trois ajoutées par prudence et déclarées ici :
1. **hors cadre** : exige la vente à découvert, le levier, un dérivé ou un rendement assimilable à un intérêt (non halal,
   et CSI est en achat seul) ;
2. **payant** : données historiques payantes (refusées par le propriétaire le 2026-10-03) ;
3. **latence** : exige une exécution à haute fréquence ;
4. **sans historique** : aucune série historique gratuite (relevée seulement depuis 2026) ;
5. **trop rare** (ajout) : moins d'une dizaine d'événements possibles sur DEVELOPMENT, aucune mesure statistique ;
6. **non mécanisable** (ajout) : aucune règle mécanique standard publiée ; toute version serait un réglage choisi par nous ;
7. **sans objet** (ajout) : la règle n'a pas de sens sur un marché continu 24 h sur 24, ou elle suppose un avantage
   que rien n'a démontré.

**Paramètres.** Ceux de la publication d'origine ou, à défaut, la valeur par défaut des plateformes usuelles ; quand
aucun standard n'existe, la colonne le dit (« a priori ») et la valeur est fixée ici, une fois, sans regard sur les
données. **Unités** : 1h, 4h, 1j (bougies 4 h et journalières agrégées des bougies 1 h, alignées sur 00:00 UTC).
Règle des unités pour le zoo : les indicateurs génériques sont mesurés sur les trois unités ; les règles définies en
jours par leur auteur (Connors, Turtle, Crabel, Williams, filtres d'Alexander, plus haut de 52 semaines…) sur 1j
seulement.

**Renvois.** Chemins relatifs à `docs/` ; `COMBINAISONS.md` et `METEO_MARCHE.md` sont sur les branches
`recherche/combinaisons` et `recherche/meteo`. Identifiants d'exécution entre parenthèses.

---

## 1. Tendance et cassures (TR)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| TR01 | Croisement de moyennes simples 5/20 | pratique ; Brock, Lakonishok et LeBaron (1992) pour la famille | SMA 5 croise SMA 20 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR02 | Croisement de moyennes exponentielles 5/20 | pratique | EMA 5 / EMA 20 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR03 | Croisement SMA 10/50 | pratique | SMA 10 / SMA 50 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR04 | Croisement EMA 10/50 | pratique | EMA 10 / EMA 50 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR05 | Croisement SMA 20/100 | pratique | SMA 20 / SMA 100 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR06 | Croisement EMA 20/100 | pratique | EMA 20 / EMA 100 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR07 | « Croix dorée » SMA 50/200 | pratique ; BLL (1992) | SMA 50 / SMA 200 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR08 | Croisement EMA 50/200 | pratique | EMA 50 / EMA 200 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR09 | Prix au-dessus de sa SMA 50 (par paire) | BLL (1992), règle VMA (1, 50) | clôture > SMA 50 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR10 | Prix au-dessus de sa SMA 200 (par paire) | BLL (1992), VMA (1, 200) ; Faber (2007), moyenne de 10 mois | clôture > SMA 200 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR11 | Régime de marché : BTC au-dessus de sa SMA 200 (ou 100) → panier investi, sinon USDT | Faber (2007) appliqué au marché | SMA 200 et SMA 100 journalières de BTC, décision hebdomadaire | 1j | `TESTÉ` | `FACTORS.md` § 10 (`FACT-20261001T192318Z-2ff650`) : REGIME_SMA200 et REGIME_SMA100 `AUCUNE_PISTE` |
| TR12 | Donchian 20/10 (Turtle, système 1) | Donchian ; règles des Turtles publiées par Faith (2003) | entrée : clôture > plus haut 20 bougies ; sortie : clôture < plus bas 10 bougies | 1h, 4h, 1j | `À_TESTER (zoo)` | **déjà vu** : en 1j, c'est le canal 20 de l'ensemble TR14 (même entrée, même sortie à 10) ; voisin testé : stratégie A (15 min, + volume) REJECTED |
| TR13 | Donchian 55/20 (Turtle, système 2) | idem | entrée 55, sortie 20 | 4h, 1j | `À_TESTER (zoo)` | **déjà vu** : en 1j, cousin du canal 50 / sortie 25 de TR14 |
| TR14 | Ensemble de Donchian 20/50/100, avec et sans ciblage de volatilité | Zarattini et al. (2025) | canaux 20/50/100 j, sorties L/2, 10 paires les plus liquides, cible 50 % | 1j | `TESTÉ` | `TREND_DAILY.md` § 8 (`TREND-20261002T162611Z-1c66eb`) : `NON_INTERESSANT` (×2) |
| TR15 | Cassure du plus haut 20 bougies avec volume 1,5 × (stratégie A) | fiche `strategies/DONCHIAN_VOLUME_BREAKOUT.md` | grille de recalibrage de 18 combinaisons | 15 min | `TESTÉ` | `PROTOCOL.md` (walk-forward, 16 paires) : REJECTED ; `ML.md` : méta-filtre `NOT_USEFUL` |
| TR16 | Cassure puis retest (D) | pratique | plus haut 20 bougies, retest ≤ +0,1 % dans 8 bougies | 15 min | `TESTÉ` | `SCREENING.md` (`SCREEN-20260930T093435Z-2874e7`, `SCREEN-20261001T015122Z-8aa8f6`) : rien ne passe |
| TR17 | Cassure de résistance d'un pivot confirmé (K2) | pratique (pivots fractals k = 3) | 1re clôture au-dessus du dernier pivot haut confirmé, horizon 24 h / 7 j | 4h, 1j | `TESTÉ` | `SCREENING.md` K (passe sur les survivantes) puis `UNIVERSE_PIT.md` § 2 : **ne tient pas à date** ; F10, F13, F14 en direct |
| TR18 | Cassure du canal de Keltner | Keltner (1960), version moderne EMA + ATR | EMA 20 ± 2 × ATR 10 ; sortie clôture < EMA 20 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR19 | SuperTrend | popularisé par O. Seban | ATR 10, multiplicateur 3 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR20 | Parabolic SAR | Wilder (1978) | pas 0,02, maximum 0,20 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR21 | Filtre ADX / DMI | Wilder (1978) | ADX 14 > 25 et +DI > −DI ; sortie +DI < −DI | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR22 | Ichimoku : clôture au-dessus du nuage | Hosoda (1969) | 9 / 26 / 52, nuage décalé de 26 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR23 | Ichimoku : croisement Tenkan / Kijun au-dessus du nuage | Hosoda (1969) | 9 / 26 / 52 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR24 | MACD : croisement de la ligne de signal (≡ histogramme qui passe au-dessus de 0) | Appel (1979) ; histogramme : Aspray (1986) | 12 / 26 / 9 | 1h, 4h, 1j | `À_TESTER (zoo)` | l'histogramme est la différence MACD − signal : son passage de 0 est ce croisement (une seule ligne) |
| TR25 | MACD : croisement de la ligne zéro | Appel (1979) | 12 / 26 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR26 | Aroon : Aroon haut croise au-dessus d'Aroon bas | Chande (1995) | 25 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR27 | Vortex : VI+ croise au-dessus de VI− | Botes et Siepman (2010) | 14 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR28 | TRIX : croisement de sa ligne de signal | Hutson (1983) | TRIX 15, signal 9 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR29 | KAMA : clôture croise au-dessus | Kaufman (1995) | 10 / 2 / 30 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR30 | Moyenne de Hull : la pente devient positive | Hull (2005) | HMA 20 (a priori : pas de standard unique) | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| TR31 | Règle de filtre 5 % | Alexander (1961) ; Fama et Blume (1966) | achat après +5 % depuis le dernier creux, sortie après −5 % depuis le dernier sommet (clôtures) | 1j | `À_TESTER (zoo)` | — |
| TR32 | Règle de filtre 10 % | idem | x = 10 % | 1j | `À_TESTER (zoo)` | — |
| TR33 | Cassure de support-résistance (TRB) 50 jours | BLL (1992) | clôture > plus haut des 50 jours précédents, tenue 10 jours | 1j | `À_TESTER (zoo)` | **déjà vu** : cousin de K2 (TR17), F10 et F13 (cassure d'un plus haut en 1j) |
| TR34 | TRB 150 jours | BLL (1992) | 150, tenue 10 jours | 1j | `À_TESTER (zoo)` | **déjà vu** : idem TR33 |
| TR35 | TRB 200 jours | BLL (1992) | 200, tenue 10 jours | 1j | `À_TESTER (zoo)` | **déjà vu** : idem TR33 |
| TR36 | Cassure de canal étroit | Sullivan, Timmermann et White (1999) | n = 20 j, largeur (plus haut − plus bas) ≤ 10 % ; a priori dans la grille de STW (pas de standard unique) | 1j | `À_TESTER (zoo)` | — |
| TR37 | Momentum temporel 1 semaine (par paire, long ou USDT) | Moskowitz, Ooi et Pedersen (2012) ; Liu et Tsyvinski (2021) | rendement 7 j > 0 | 1j | `À_TESTER (zoo)` | — |
| TR38 | Momentum temporel 4 semaines | idem | 28 j | 1j | `TESTÉ` | `FACTORS.md` § 10 : TSMOM_L28 `AUCUNE_PISTE` |
| TR39 | Momentum temporel 8 semaines | idem | 56 j | 1j | `TESTÉ` | `FACTORS.md` § 10 : TSMOM_L56 `AUCUNE_PISTE` |
| TR40 | Momentum temporel 12 semaines | Hurst, Ooi et Pedersen (2017) | 84 j | 1j | `À_TESTER (zoo)` | seul dans le vote du modèle A (TR41), jamais seul |
| TR41 | Vote de tendance 4/12/26 semaines avec volatilité prévue (modèle A) | CTA ; lot 8 | 2 votes sur 3, cible 50 % | 1j | `TESTÉ` | `LONG_HORIZON.md` § 11 (`LONG-20261002T155625Z-967d85`) : `NON_INTERESSANT` ; F5 en direct |
| TR42 | Double momentum (classement + rendement absolu) | Antonacci (2014) | top 5 ou 8 sur 28 j si rendement > 0 | 1j | `TESTÉ` | `FACTORS.md` (DUAL_MOM28) et `XSECTION.md` (DOUBLE_MOM_4S, à date) : aucun ne bat l'univers |
| TR43 | Ciblage de volatilité | Moreira et Muir (2017) ; Zarattini (2025) | exposition × min(1, cible / σ) | 1j | `TESTÉ` | `FACTORS.md` H5, `TREND_DAILY.md` (ENSEMBLE_VOL), `LONG_HORIZON.md` : réduit la perte par l'exposition, sans gain démontré ; F5 en direct |
| TR44 | Régime de marché : rendement de BTC sur 28 j > 0 | MOP (2012) appliqué au marché | 28 j | 1j | `TESTÉ` | `FACTORS.md` : REGIME_MOM28 `AUCUNE_PISTE` |
| TR45 | Repli sur EMA 20 en tendance (stratégie B) | fiche `strategies/EMA_PULLBACK_CONTINUATION.md` | grille de 12 combinaisons | 15 min | `TESTÉ` | `PROTOCOL.md` : REJECTED ; `ML.md` : `NOT_USEFUL` |
| TR46 | Cassure d'une ligne de tendance descendante | pratique des analystes ; détecteur de F15 | `INDICATEURS.md` § 9.3 | 1h (4h, 1j) | `TESTÉ` | `FIGURES_HISTORIQUE.md` : `NON_DEMONTRE` ; `LIGNES_DE_TENDANCE.md` : `PISTE_CONFIRMEE` sur 214 paires puis **`NON_CONFIRMEE`** sur la période réservée ; F15 en direct |
| TR47 | Compression de Bollinger puis cassure (F) | pratique | largeur BB 20 dans le quintile bas des 500 bougies | 15 min | `TESTÉ` | `SCREENING.md` : rien ne passe (excès négatif à 24 h) |
| TR48 | Point et figure : cassure d'un double sommet | Dorsey (2007) | boîte de 1 % (échelle logarithmique), retournement de 3 boîtes, sur les clôtures ; achat à la cassure d'un double sommet, sortie à la cassure d'un double creux | 1j | `À_TESTER (zoo)` | — |

## 2. Retour à la moyenne (MR)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| MR01 | RSI 2 de Connors | Connors et Alvarez (2008) | RSI 2 < 10 et clôture > SMA 200 ; sortie clôture > SMA 5 | 1j | `À_TESTER (zoo)` | — |
| MR02 | RSI 14 en 30 / 70 | Wilder (1978) | entrée : RSI 14 remonte au-dessus de 30 ; sortie : RSI 14 > 70 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| MR03 | Excès de Bollinger : réintégration de la bande basse | Bollinger (2001) | BB 20, 2 écarts-types ; sortie à la bande médiane | 1h, 4h, 1j | `À_TESTER (zoo)` | voisin testé : stratégie C (15 min, filtres de range) REJECTED |
| MR04 | Réintégration de range (stratégie C) | fiche `strategies/RANGE_REENTRY.md` | grille de 6 combinaisons | 15 min | `TESTÉ` | `PROTOCOL.md` : REJECTED ; `ML.md` : `NOT_USEFUL` |
| MR05 | Stochastique : %K croise %D sous 20 | Lane (années 1950) | 14 / 3 / 3, seuils 20 / 80 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| MR06 | Williams %R remonte au-dessus de −80 | Williams (1973) | 14, sortie au-dessus de −20 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| MR07 | CCI remonte au-dessus de −100 | Lambert (1980) | 20, sortie au-dessus de +100 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| MR08 | Reprise du VWAP journalier (H) | pratique | VWAP ancré à 00:00 UTC, 3 clôtures sous puis une au-dessus | 15 min | `TESTÉ` | `SCREENING.md` : rien ne passe |
| MR09 | Écart à la VWAP journalière | pratique | écart relatif (clôture / VWAP du jour − 1) divisé par l'écart-type de cet écart sur les 168 bougies précédentes < −2 ; sortie au retour à la VWAP (a priori) | 1h | `À_TESTER (zoo)` | — |
| MR10 | Reprise de la VWAP ancrée au dernier creux | pratique | ancre = dernier pivot ZigZag 1h | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.1 (`CMB1-20261008T182200Z-db1ea4`) : AVWAP_RECLAIM `GAIN_NON_DEMONTRE` |
| MR11 | z-score du rendement d'une bougie < −2 (retournement court) | Jegadeesh (1990), Lehmann (1990) | écart-type des 20 rendements précédents ; tenue 10 bougies | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| MR12 | Trois clôtures en baisse au-dessus de la SMA 200 | Connors et Alvarez (2008) | 3 clôtures consécutives en baisse, clôture > SMA 200 ; sortie clôture > SMA 5 | 1j | `À_TESTER (zoo)` | — |
| MR13 | Achat après un gap down | pratique (actions) | ouverture < plus bas de la veille | — | `IMPOSSIBLE` | raison 7 : marché continu, l'ouverture est la clôture précédente ; raison 4 pour l'écart du CME (source refusée, `CONTEXTE.md`) |
| MR14 | Retour au-dessus d'un support enfoncé (E, « failed breakdown ») | pratique | support = plus bas t−43..t−4 | 15 min | `TESTÉ` | `SCREENING.md` : excès positif cohérent mais **sous les coûts** ; rien ne passe à 3 et 7 j |
| MR15 | Rebond sur support d'un pivot confirmé (K1) | pratique | pivots k = 3 | 4h, 1j | `TESTÉ` | `SCREENING.md` K : nul ; `UNIVERSE_PIT.md` § 2 : **nettement négatif à date** |
| MR16 | Rejet ou rebond sur supports et résistances mécaniques, contre niveaux placebo | Osler (2000) | pivots ZigZag, barrières ±1 ATR | 1h, 4h | `TESTÉ` | `NIVEAUX.md` (`LEVELS-20261004T012546Z-a3b017`) : `RIEN` (pile ou face, ±1 pt) |
| MR17 | Divergence RSI haussière | pratique | RSI 14, pivots fractals k = 2 | 1h, 4h, 1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` (`FIGH-20261004T102820Z-479ac6`) : `INFERIEUR_AU_HASARD` (R −0,11) |
| MR18 | Repli puis reprise en tendance, rejet du support en range (assistant) | demande du propriétaire | `ASSISTANT.md` § 2-3 | 4h | `EN_DIRECT` | F18_ASSISTANT (démarré le 2026-10-09) |
| MR19 | IBS (« internal bar strength ») bas | Pagonidis (2014) | IBS = (C − L) / (H − L) < 0,2 ; entrée à la clôture de la 1re bougie 1 h qui s'ouvre après la disponibilité du signal, sortie 24 h plus tard | 1j (exécution 1h) | `À_TESTER (zoo)` | — |
| MR20 | « Double 7s » | Connors et Alvarez (2009) | clôture > SMA 200 et clôture = plus bas des 7 dernières ; sortie quand la clôture = plus haut des 7 dernières | 1j | `À_TESTER (zoo)` | — |

## 3. Volatilité et range (VO)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| VO01 | Cassure de volatilité de Larry Williams | Williams (1999) ; version k = 0,5 répandue en crypto | achat si le prix dépasse ouverture du jour + 0,5 × amplitude de la veille ; sortie à la clôture du jour | 1j (exécution 1h) | `À_TESTER (zoo)` | — |
| VO02 | Dual Thrust | M. Chalek ; version publique | N = 4 j, K = 0,5 ; amplitude = max(PHH − PCB, PCH − PBB) ; sortie en fin de journée | 1j (exécution 1h) | `À_TESTER (zoo)` | — |
| VO03 | Cassure de la première heure, séance asiatique | Crabel (1990), « opening range breakout » | séance 00:00–08:00 UTC (`INDICATEURS.md` § 10.2) ; sortie en fin de séance | 1h | `À_TESTER (zoo)` | les effets horaires seuls sont testés (S6, S7, `SCREENING.md`) |
| VO04 | Cassure de la première heure, séance de Londres | idem | 07:00–16:00 UTC | 1h | `À_TESTER (zoo)` | — |
| VO05 | Cassure de la première heure, séance de New York | idem | 13:00–22:00 UTC | 1h | `À_TESTER (zoo)` | — |
| VO06 | NR4 : cassure de la journée la plus étroite des 4 | Crabel (1990) | amplitude minimale des 4 derniers jours | 1j | `À_TESTER (zoo)` | — |
| VO07 | NR7 | Crabel (1990) | 7 jours | 1j | `À_TESTER (zoo)` | — |
| VO08 | Compression TTM (« squeeze ») | Carter (2005) | BB 20/2 à l'intérieur de KC 20/1,5, puis sortie avec momentum > 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | **déjà vu** : en 4h, voisin de `SQUEEZE` mesuré en direct (F21) ; F (15 min) testé |
| VO09 | Cassure de l'ATR | Wilder (1978) | clôture > clôture précédente + 1 × ATR 14 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VO10 | Cinq configurations « price action » (base et retest, compression, force relative, journée intérieure, base longue) | demande du propriétaire | `PRICE_ACTION.md` § 2 | 4h, 1j | `EN_DIRECT` | historique : `INSTRUMENT_TROP_FAIBLE`, 0 essai (`PRICE_ACTION.md` § 11.6) ; F19 et F20 à F24 démarrés le 2026-10-10 |
| VO11 | Momentum intrajournalier | Gao, Han, Li et Zhou (2018) ; version crypto Wen et al. (2022) | rendement de 00:00 à 22:00 UTC > 0 → achat pour la dernière heure de la journée (adapté : l'entrée à la clôture suivante impose 23:00 → 24:00) | 1h | `À_TESTER (zoo)` | — |

## 4. Chandeliers japonais (CH)

Définitions de TA-Lib (fonctions `CDL…`, réglages par défaut), qui codifient Nison (1991). Pour les figures de
retournement, tendance préalable baissière exigée : clôture sous la SMA 10 à la première bougie de la figure (Marshall,
Young et Rose, 2006) ; pour les figures de continuation (CH10, CH15), tendance préalable haussière (clôture > SMA 10).

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| CH01 | Marteau | Nison (1991) | `CDLHAMMER` | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH02 | Marteau inversé | idem | `CDLINVERTEDHAMMER` | — | `IMPOSSIBLE` | raison 7 (2026-10-11) : la définition TA-Lib exige un écart d'ouverture, inexistant quand l'ouverture est la clôture précédente (comme CH06, CH07, CH09, CH14, CH18) ; aucune sous H0, aveugle |
| CH03 | Avalement haussier | idem | `CDLENGULFING` > 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH04 | Harami haussier | idem | `CDLHARAMI` > 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH05 | Harami en croix | idem | `CDLHARAMICROSS` > 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH06 | Étoile du matin | idem | `CDLMORNINGSTAR`, pénétration 0,3 | — | `IMPOSSIBLE` | raison 7 : TA-Lib exige un écart entre le corps de la 1re bougie et celui de l'étoile ; l'ouverture étant la clôture précédente en 24/7, l'écart n'existe pas (le marché sous H0, où l'ouverture est la clôture précédente, n'en produirait aucune) |
| CH07 | Étoile du matin doji | idem | `CDLMORNINGDOJISTAR`, pénétration 0,3 | — | `IMPOSSIBLE` | raison 7 : idem CH06 |
| CH08 | Doji libellule | idem | `CDLDRAGONFLYDOJI` | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH09 | Ligne pénétrante | idem | `CDLPIERCING` | — | `IMPOSSIBLE` | raison 7 : la 2e bougie doit ouvrir sous le plus bas de la 1re, impossible quand l'ouverture est la clôture précédente (aucune sous H0 non plus) |
| CH10 | Trois soldats blancs | idem | `CDL3WHITESOLDIERS` | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH11 | Trois intérieurs haussiers | idem | `CDL3INSIDE` > 0 | — | `IMPOSSIBLE` | raison 7 (2026-10-11) : la définition TA-Lib exige un écart d'ouverture, inexistant quand l'ouverture est la clôture précédente (comme CH06, CH07, CH09, CH14, CH18) ; aucune sous H0, aveugle |
| CH12 | Trois extérieurs haussiers | idem | `CDL3OUTSIDE` > 0 | — | `IMPOSSIBLE` | raison 7 (2026-10-11) : la définition TA-Lib exige un écart d'ouverture, inexistant quand l'ouverture est la clôture précédente (comme CH06, CH07, CH09, CH14, CH18) ; aucune sous H0, aveugle |
| CH13 | Ceinture haussière | idem | `CDLBELTHOLD` > 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH14 | Bébé abandonné haussier | idem | `CDLABANDONEDBABY` | — | `IMPOSSIBLE` | raison 7 : exige un écart de prix entre bougies, impossible quand l'ouverture est la clôture précédente |
| CH15 | Trois méthodes ascendantes | idem | `CDLRISEFALL3METHODS` > 0 | — | `IMPOSSIBLE` | raison 7 (2026-10-11) : la définition TA-Lib exige un écart d'ouverture, inexistant quand l'ouverture est la clôture précédente (comme CH06, CH07, CH09, CH14, CH18) ; aucune sous H0, aveugle |
| CH16 | Pigeon voyageur | idem | `CDLHOMINGPIGEON` | — | `IMPOSSIBLE` | raison 7 (2026-10-11) : la définition TA-Lib exige un écart d'ouverture, inexistant quand l'ouverture est la clôture précédente (comme CH06, CH07, CH09, CH14, CH18) ; aucune sous H0, aveugle |
| CH17 | Plus bas identiques | idem | `CDLMATCHINGLOW` | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH18 | Kicker haussier | idem | `CDLKICKING` | — | `IMPOSSIBLE` | raison 7 : exige un écart de prix (comme CH14) |
| CH19 | Doji simple | idem | `CDLDOJI` | — | `IMPOSSIBLE` | raison 6 : figure d'indécision, aucune règle d'achat publiée |
| CH20 | Heikin Ashi : première bougie haussière sans mèche basse | Valcu (2004) | bougies HA standard ; sortie à la première HA baissière | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| CH21 | Renko : retournement haussier de briques | Nison (1994) | boîte = ATR 14 journalier (figé à chaque début de mois), construit sur les clôtures ; achat sur la 2e brique haussière après une baissière, sortie sur une brique baissière | 1h, 1j | `À_TESTER (zoo)` | — |

## 5. Structure (ST)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| ST01 | Théorie de Dow : plus hauts et plus bas ascendants | Dow, Hamilton (1922), Rhea (1932) ; Brown, Goetzmann et Kumar (1998) | pivots ZigZag (`INDICATEURS.md` § 1, m = 2,5 / 2,0 ATR) ; long tant que dernier haut > haut précédent et dernier bas > bas précédent | 4h, 1j | `À_TESTER (zoo)` | — |
| ST02 | Cassure de structure (BOS) et changement de caractère (CHoCH) | ICT / SMC | `INDICATEURS.md` § 5 | 1h, 4h, 1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` : BOS et CHOCH `NON_DEMONTRE` |
| ST03 | Retracement de Fibonacci 38,2 % | pratique | dernière jambe haussière du ZigZag ; achat à la 1re clôture qui repasse au-dessus du niveau après l'avoir touché ; sortie au sommet de la jambe ou sous son creux | 1h, 4h, 1j | `À_TESTER (zoo)` | écarté du criblage K (`SCREENING.md`), jamais mesuré seul |
| ST04 | Retracement de Fibonacci 50 % | pratique | idem, 50 % | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| ST05 | Retracement de Fibonacci 61,8 % | pratique | idem, 61,8 % | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| ST06 | Points pivots classiques : rebond sur S1 | pratique des parquets | pivots journaliers `INDICATEURS.md` § 10.4 ; sortie à P | 1h | `À_TESTER (zoo)` | — |
| ST07 | Points pivots classiques : cassure de R1 | idem | sortie à R2 ou à la fin de la journée | 1h | `À_TESTER (zoo)` | — |
| ST08 | Camarilla : rebond sur L3 | N. Scott (1989) | L3 = C − 1,1 × (H − L) / 4 ; sortie à H3 | 1h | `À_TESTER (zoo)` | — |
| ST09 | Camarilla : cassure de H4 | idem | H4 = C + 1,1 × (H − L) / 2 ; sortie à la fin de la journée | 1h | `À_TESTER (zoo)` | — |
| ST10 | Pivots de Woodie : rebond sur S1 | K. Wood | P = (H + L + 2 × C) / 4, S1 = 2P − H ; sortie à P | 1h | `À_TESTER (zoo)` | — |
| ST11 | Cassure du plus haut de 52 semaines | George et Hwang (2004), version par paire | clôture > plus haut des 365 jours précédents ; tenue 20 jours | 1j | `À_TESTER (zoo)` | **déjà vu** : cousin de K2 (TR17), F10 et F13 |
| ST12 | Nouveau plus haut historique | pratique | clôture > plus haut de toute l'histoire Binance de la paire (au moins 365 jours) ; tenue 20 jours | 1j | `À_TESTER (zoo)` | — |
| ST13 | Chiffres ronds : clôture au-dessus d'un niveau majeur | Osler (2003) | niveaux majeurs `INDICATEURS.md` § 10.3 ; tenue 10 bougies | 1h, 4h | `À_TESTER (zoo)` | — |
| ST14 | Cassure du plus haut de la veille | pratique | PDH (`INDICATEURS.md` § 10.1) ; sortie à la fin de la journée | 1h | `À_TESTER (zoo)` | — |
| ST15 | Cassure du plus haut de la semaine précédente | pratique | PWH ; sortie à la fin de la semaine | 4h | `À_TESTER (zoo)` | — |

## 6. Volume et flux (VF)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| VF01 | OBV croise au-dessus de sa moyenne | Granville (1963) ; famille OBV de STW (1999) | SMA 20 de l'OBV | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF02 | Ligne d'accumulation / distribution croise sa moyenne | Chaikin | SMA 20 de la ligne A/D | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF03 | Oscillateur de Chaikin passe au-dessus de 0 | Chaikin | EMA 3 − EMA 10 de la ligne A/D | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF04 | MFI remonte au-dessus de 20 | Quong et Soudack (1989) | 14, sortie au-dessus de 80 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF05 | Choc de volume haussier | pratique ; `INDICATEURS.md` § 10.5 | volume > 5 × moyenne des 20 bougies précédentes et clôture > ouverture ; tenue 10 bougies | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF06 | Profil de volume : rebond sur le POC | Steidlmayer (Market Profile) | profil 30 j, 50 tranches | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.1 : VP_POC `GAIN_NON_DEMONTRE` |
| VF07 | Profil de volume : rebond sur le bas de la zone de valeur | idem | zone de valeur 70 % | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.1 : VP_VAL `PERTE_DEMONTREE` |
| VF08 | Divergence du CVD | pratique | CVD centré, pivots fractals | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.1 : CVD_DIV `PERTE_DEMONTREE` |
| VF09 | Absorption (vente forte, clôture haute) | pratique | part acheteuse ≤ 10e centile, clôture dans le tiers haut | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.1 : ABSORPTION `PERTE_DEMONTREE` |
| VF10 | Votes et modèle logistique sur les briques | demande du propriétaire | `COMBINAISONS.md` § 4 | 1h | `TESTÉ` | `COMBINAISONS.md` § 11.2 : tous `PERTE_DEMONTREE` ou `GAIN_NON_DEMONTRE` (−0,06 à −0,16 R) |
| VF11 | Part des achats au marché haute (J1) | pratique | ≥ 90e centile des 365 jours | 1j | `TESTÉ` | `SCREENING.md` J (`SCREEN-20261002T165546Z-0994a2`) : rien ne passe |
| VF12 | Part des achats au marché basse (J2) | pratique | ≤ 10e centile | 1j | `TESTÉ` | `SCREENING.md` J : excès **négatif** (piste de veto après coup) ; F11 en direct |
| VF13 | Baleines : solde des gros ordres au marché sur 1 h | pratique | `FORWARD_TESTS.md` F28 | flux | `À_TESTER (attente)` | F28 pré-inscrit, non démarré (puissance 0 à 4 %), journaux du collecteur |
| VF14 | Mur d'acheteurs dans le carnet | pratique | F26 | carnet | `À_TESTER (attente)` | F26 pré-inscrit, non démarré ; carnet sans historique gratuit |
| VF15 | Retrait de la liquidité acheteuse (veto) | pratique | F27 | carnet | `À_TESTER (attente)` | F27 : contrôle H0 **en échec**, non démarrable |
| VF16 | Elder Force Index passe au-dessus de 0 | Elder (1993) | EMA 13 de (C − C₋₁) × volume ; sortie sous 0 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |
| VF17 | Oscillateur de Klinger croise sa ligne de signal | Klinger (1997) | 34 / 55, signal 13 | 1h, 4h, 1j | `À_TESTER (zoo)` | — |

## 7. Facteurs en coupe (XS)

Version achat seul : portefeuille du quintile choisi par la publication de référence (le côté acheteur du facteur)
contre l'univers à poids égaux. Le sens est celui de la référence citée ; il n'est pas choisi sur nos données.

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| XS01 | Momentum 1 semaine (gagnants) | Liu, Tsyvinski et Wu (2022) | rendement 7 j, quintile haut, hebdomadaire | 1j | `À_TESTER (zoo)` | — |
| XS02 | Momentum 2 semaines | LTW (2022) | 14 j | 1j | `TESTÉ` | `FACTORS.md` § 10 : MOM_L14_K3/K5 `AUCUNE_PISTE` (survivantes, biais favorable) |
| XS03 | Momentum 4 semaines | LTW (2022) | 28 j | 1j | `TESTÉ` | `XSECTION.md` (`XSEC-20261003T010820Z-bb356b`, à date) : MOM_4S ne bat pas l'univers ; `FACTORS.md` MOM_L28 |
| XS04 | Momentum 8 semaines | LTW (2022) | 56 j | 1j | `TESTÉ` | `FACTORS.md` : MOM_L56 `AUCUNE_PISTE` |
| XS05 | Retournement 1 semaine (perdants) | Jegadeesh (1990) ; Bianchi, Babiak et Dickerson (2022) | 7 j, quintile bas | 1j | `TESTÉ` | `FACTORS.md` : REVERSAL_K5 fait **moins bien** que l'univers (IC < 0) |
| XS06 | Force relative ajustée de la volatilité (G) | pratique | top 3 sur 7 j, toutes les 4 h | 1h | `TESTÉ` | `SCREENING.md` : rien ne passe |
| XS07 | Momentum résiduel (I) | Blitz, Huij et Martens (2011) | top 3, bêta 30 j | 1h | `TESTÉ` | `SCREENING.md` : rien ne passe |
| XS08 | Taille (capitalisation) | LTW (2022) | quintile des plus petites capitalisations | — | `IMPOSSIBLE` | raison 2 : capitalisation historique par crypto payante (CoinGecko : 365 jours au plus ; `CONTEXTE.md`, « Données du zoo ») ; CoinMetrics gratuit pour BTC et ETH seulement ; les instantanés historiques de CoinMarketCap demanderaient de racler des pages web, ce que le projet ne fait pas ; le volume (XS09) est la seule approximation gratuite |
| XS09 | Volume en dollars faible | LTW (2022) | volume des 28 j, quintile bas | 1j | `À_TESTER (zoo)` | — |
| XS10 | Illiquidité d'Amihud | Amihud (2002) | moyenne de abs(rendement) / volume en dollars sur 28 j, quintile haut | 1j | `À_TESTER (zoo)` | — |
| XS11 | MAX faible (anti-loterie) | Bali, Cakici et Whitelaw (2011) ; Grobys et Junttila (2021) | plus fort rendement journalier des 28 j, quintile bas | 1j | `À_TESTER (zoo)` | — |
| XS12 | Volatilité idiosyncratique faible | Ang, Hodrick, Xing et Zhang (2006) | écart-type des résidus d'une régression sur BTC, 28 j, quintile bas | 1j | `À_TESTER (zoo)` | — |
| XS13 | Faible volatilité totale | Blitz et van Vliet (2007) | volatilité 28 à 180 j | 1j | `TESTÉ` | `FACTORS.md` LOWVOL_K5 `AUCUNE_PISTE` ; `XSECTION.md` CALMES : excès nul ; `LONG_HORIZON.md` B `NON_INTERESSANT` |
| XS14 | Bêta faible à BTC (« betting against beta », achat seul) | Frazzini et Pedersen (2014) | bêta des rendements journaliers sur BTC, 90 j, quintile bas | 1j | `À_TESTER (zoo)` | — |
| XS15 | Asymétrie faible | Harvey et Siddique (2000) ; Amaya et al. (2015) | asymétrie des rendements journaliers sur 28 j, quintile bas | 1j | `À_TESTER (zoo)` | — |
| XS16 | NVT (valeur du réseau / transactions) | Kalichkin, Woo | quintile bas | — | `IMPOSSIBLE` | raison 2 : données on-chain par crypto payantes ; gratuites pour BTC et ETH seulement (voir OC08) |
| XS17 | Proximité du plus haut historique | analogue crypto de George et Hwang (2004) | clôture / plus haut de toute l'histoire, quintile haut ; **paires cotées depuis au moins 365 jours** seulement | 1j | `À_TESTER (zoo)` | — |
| XS18 | Proximité du plus haut de 52 semaines | George et Hwang (2004) | clôture / plus haut 365 j, quintile haut | 1j | `À_TESTER (zoo)` | — |
| XS19 | Choc de volume hebdomadaire | Gervais, Kaniel et Mingelgrin (2001) | volume de la semaine / moyenne des 10 semaines précédentes, quintile haut | 1j | `À_TESTER (zoo)` | — |
| XS20 | Flux acheteur hebdomadaire | pratique (piste de l'étude du 2026-10-02) | part des achats au marché sur 7 j, quintile haut | 1j | `À_TESTER (zoo)` | **déjà vu** : même variable que J1 et J2 (`SCREENING.md`, par paire et par jour). Le sens « acheter le flux acheteur » vient de l'hypothèse de J1, déclarée avant le criblage J ; J2 (vu après coup : pression vendeuse → moins bien) va dans le même sens : sens contaminé, déclaré |
| XS21 | Primes coréenne et Coinbase des altcoins | pratique | niveau et saut, tiers haut − tiers bas | 1j | `TESTÉ` | `XSECTION_PRIMES.md` (`XPREM-20261004T092024Z-dff748`) : `RIEN` (×4) |
| XS22 | Âge de cotation : offre nouvelle (veto) | pratique | cotée depuis 30 à 180 j | 1j | `TESTÉ` | `SCREENING.md` J3 : inverse d'un veto, inexploitable (survivance) |
| XS23 | Distance aux moyennes mobiles (MAD) | Avramov, Kaplanski et Subrahmanyam (2021) | SMA 21 / SMA 200 des clôtures journalières, quintile haut, hebdomadaire | 1j | `À_TESTER (zoo)` | — |
| XS24 | Retournement d'un jour en coupe | Jegadeesh (1990), Lehmann (1990), à la fréquence journalière | rendement de la journée, quintile bas ; achat à la clôture de la 1re bougie 1 h qui s'ouvre après la disponibilité du signal, vente 24 h plus tard | 1j (exécution 1h) | `À_TESTER (zoo)` | — |

## 8. Rotation et marché (RO)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| RO01 | Dominance de BTC | pratique | seuils de dominance | — | `IMPOSSIBLE` | raison 2 : historique payant (CoinGecko : 365 jours, `/global/market_cap_chart` réservé aux abonnés ; CoinPaprika : instantané seulement) ; racler les instantanés de CoinMarketCap n'est pas fait ; relevé depuis le 2026-10-04 |
| RO02 | Rotation ETH / BTC | pratique | ETH si ETHBTC > SMA 50 journalière, sinon BTC | 1j | `À_TESTER (zoo)` | — |
| RO03 | Indice « altseason » | blockchaincenter.net | altcoins à poids égaux si au moins 75 % du top (hors BTC) bat BTC sur 90 j, sinon BTC ; adapté du top 50 au top 40 | 1j | `À_TESTER (zoo)` | — |
| RO04 | Largeur du marché | pratique ; composante `LARGEUR` de `METEO_MARCHE.md` | investi (univers à poids égaux) si plus de la moitié du top 40 clôture au-dessus de sa SMA 50, sinon USDT | 1j | `À_TESTER (zoo)` | jamais mesurée seule (étude météo abandonnée, 0 essai) |
| RO05 | Corrélation moyenne | pratique | investi si la corrélation moyenne par paires du top 40 (rendements journaliers, 30 j) est sous sa médiane des 365 j précédents (a priori : corrélation haute = marché en stress) | 1j | `À_TESTER (zoo)` | — |
| RO06 | Rotation sectorielle (catégories) | pratique | — | — | `IMPOSSIBLE` | raison 2 : historique des catégories payant (`CONTEXTE.md`) |

## 9. Autres marchés (AM)

Hypothèse commune : l'appétit pour le risque des marchés traditionnels se transmet à BTC le lendemain. Sens fixé a
priori ; un seul sens par marché.

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| AM01 | S&P 500 en tendance → BTC détenu | pratique | rendement de l'indice SP500 sur 20 séances > 0 (FRED, `zoo_marches`, connu à 16:00 New York + 2 h) | 1j | `À_TESTER (zoo)` | `CONTEXTE.md`, « Données du zoo » |
| AM02 | S&P 500 la veille → BTC le lendemain | pratique | rendement de SP500 de la dernière séance connue > 0 | 1j | `À_TESTER (zoo)` | idem |
| AM03 | Or en hausse → BTC détenu | récit « or numérique » | rendement de GLD sur 20 séances > 0 (`zoo_marches`, 16:00 New York + 2 h) | 1j | `À_TESTER (zoo)` | idem |
| AM04 | Taux à 10 ans américain en baisse → BTC détenu | pratique | variation de DGS10 sur 20 séances < 0 ; valeur du jour `d` connue au plus tôt à `d` + 1 12:00 UTC ; l'`available_at` de DGS10 (`d` + 1, 16:15 New York + 2 h), plus tardif, est retenu | 1j | `À_TESTER (zoo)` | idem |
| AM05 | Masse monétaire M2 en hausse → BTC détenu | « liquidité mondiale », pratique | croissance de M2SL sur 3 mois > 0 | 1j | `À_TESTER (attente)` | **retiré du zoo** (relecture du 2026-10-11) : M2 est révisé après coup et le magasin n'a que les valeurs d'aujourd'hui ; une version point-in-time exige les millésimes ALFRED |
| AM06 | Nasdaq 100 en tendance → BTC | pratique | rendement de NASDAQ100 (FRED, 16:00 New York + 2 h) sur 20 séances > 0 | 1j | `À_TESTER (zoo)` | `CONTEXTE.md`, « Données du zoo » |
| AM07 | Dollar en baisse → BTC | pratique | variation de l'indice dollar recalculé DXY_BCE (`zoo_dollar_bce`, 16:00 Francfort + 1 h) sur 20 jours ouvrés < 0 | 1j | `À_TESTER (zoo)` | idem (approximation de la cotation ICE, déclarée) |
| AM08 | VIX bas → BTC | pratique | VIXCLS (FRED, 16:15 New York + 2 h) sous sa médiane des 365 jours précédents | 1j | `À_TESTER (zoo)` | idem |
| AM09 | Données de contexte contre la direction de BTC et ETH (flux, primes, stablecoins, top traders, Wikipédia) | pratique | `CONTEXTE_PREDICTION.md` | 1j | `TESTÉ` | `CONTEXTE_PREDICTION.md` (`CTXP-20261004T015026Z-30e516`) : `RIEN` (×21) |

## 10. Calendrier (CA)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| CA01 | Week-end | littérature des effets de calendrier | samedi 00:00 UTC, 48 h | 1h | `TESTÉ` | `SCREENING.md` S (`SCREEN-20261003T010221Z-bea15e`) : S1 rien |
| CA02 | Lundi | Caporale et Plastun (2019) | lundi 00:00, 24 h | 1h | `TESTÉ` | S2 : rien |
| CA03 | Tournant du mois | Ariel (1987) | J−2 avant la fin du mois, 72 h | 1h | `TESTÉ` | S3 : sans intervalle calculable |
| CA04 | Heures de règlement du financement | pratique | 00, 08, 16 UTC, 4 h | 1h | `TESTÉ` | S4 : légèrement **négatif** |
| CA05 | Expiration mensuelle des options (Deribit) | pratique | dernier vendredi 08:00 UTC, 24 h | 1h | `TESTÉ` | S5 : sans intervalle calculable ; piste après coup seulement |
| CA06 | Ouverture américaine | pratique | 14:00 UTC, 4 h | 1h | `TESTÉ` | S6 : rien |
| CA07 | Séance asiatique | pratique | 00:00 UTC, 8 h | 1h | `TESTÉ` | S7 : rien |
| CA08 | Meilleure heure apprise de l'année passée | pratique | 365 j | 1h | `TESTÉ` | S8 : rien (ne se répète pas) |
| CA09 | Dérive avant les décisions du FOMC | Lucca et Moench (2015) | achat de tous les membres à la clôture de la dernière bougie 1 h qui se termine au plus tard 24 h avant le communiqué (14:00 New York, `zoneinfo`), vente à la clôture de la dernière bougie 1 h qui se termine au plus tard à l'heure du communiqué ; réunions programmées seulement | 1h | `À_TESTER (zoo)` | `calendrier_macro` ; **date connue à l'avance** (calendrier publié l'année précédente) : hypothèse déclarée, car l'`available_at` du magasin est posé après l'événement |
| CA10 | Publication de l'indice des prix (CPI) | pratique ; fenêtre de CA09 a priori | même fenêtre que CA09, publication à 08:30 New York ; dates contrôlées au comptage à blanc (jour de la semaine, écarts) | 1h | `À_TESTER (zoo)` | dates des millésimes ALFRED (`calendrier_macro`) ; date publiée à l'avance par le BLS : hypothèse déclarée |
| CA11 | Publication de l'emploi (NFP) | idem | idem | 1h | `À_TESTER (zoo)` | idem |
| CA12 | Halving de BTC | pratique | achat au halving | 1j | `IMPOSSIBLE` | raison 5 : deux événements dans DEVELOPMENT (2020-05, 2024-04 ; table `calendrier_crypto`) |
| CA13 | Cycle lunaire | Yuan, Zheng et Zhu (2006) | achat à la clôture qui suit la nouvelle lune, vente à celle qui suit la pleine lune | 1j | `À_TESTER (zoo)` | phases de `calendrier_crypto` (calcul astronomique, connues à l'avance par construction) |
| CA14 | « Halloween » (investi de novembre à avril) | Bouman et Jacobsen (2002) | univers investi du 1er novembre au 30 avril, USDT sinon | 1j | `IMPOSSIBLE` | raison 5 : quatre cycles semestriels en découverte, deux et demi en validation |

## 11. Sentiment et attention (SE)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| SE01 | Fear & Greed passe sous 25 (peur extrême) → achat | alternative.me ; pratique contrarienne | la dernière valeur dont l'`available_at` précède la décision (`zoo_fear_greed`) passe sous 25 ; achat de tous les membres du top 40 ; tenue 30 jours (a priori) | 1j | `À_TESTER (zoo)` | historique depuis 2018-02 (`CONTEXTE.md`, « Données du zoo ») ; limite : historique d'alternative.me lu tel que publié aujourd'hui |
| SE02 | Fear & Greed au-dessus de 75 (avidité extrême) → USDT | idem | top 40 à poids égaux, sauf quand la dernière valeur connue dépasse 75 | 1j | `À_TESTER (zoo)` | idem |
| SE03 | Attention du public (vues Wikipédia « Bitcoin ») | Liu et Tsyvinski (2021) | moyenne 7 j, tiers haut / bas | 1j | `TESTÉ` | `CONTEXTE_PREDICTION.md` : WIKI_ATTENTION `RIEN` |
| SE04 | Attention par crypto (vues Wikipédia en coupe) | idem | quintile de la hausse des vues | 1j | `À_TESTER (attente)` | pages par crypto non téléchargées (API Wikimedia gratuite, correspondance crypto → article à construire) |
| SE05 | Entrée dans les « trending » de CoinGecko | pratique | F30 | 1h | `IMPOSSIBLE` | raison 4 : relevé depuis le 2026-10-09 seulement ; F30 pré-inscrit, contrôle H0 en échec |
| SE06 | Recherches Google | Liu et Tsyvinski (2021) | — | — | `IMPOSSIBLE` | raison 4 : pas d'API, robots refusés (`CONTEXTE.md`) |
| SE07 | Reddit | pratique | — | — | `IMPOSSIBLE` | raison 4 : connexion exigée depuis 2026 (`COLLECTE.md`) |
| SE08 | X / Twitter | pratique | — | — | `IMPOSSIBLE` | raison 2 : API payante |
| SE09 | Filtre de news à mots-clés | pratique | gravité 3, exposition coupée 7 j | 15 min | `EN_DIRECT` | F8_NEWS ; étude d'événements déclarée (`NEWS.md`) |
| SE10 | Sentiment des news lu par un LLM, sur l'historique | pratique | — | — | `IMPOSSIBLE` | raison 6 : la mémoire du LLM contamine tout backtest (`PLAN_DE_TRAVAIL.md` § 6) |

## 12. Marché à terme public (DE)

Lu comme information seulement : aucun contrat n'est jamais traité.

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| DE01 | Financement bas (contrarien) | pratique | moyenne 72 h sous le 10e centile | 1j | `TESTÉ` | `DERIVATIVES.md` (`SCREEN-20261001T111500Z-60702b`) : `AUCUNE_PISTE` |
| DE02 | Financement chaud (veto) | pratique | > 0,05 % par 8 h sur 7 j | 1j | `TESTÉ` | `LONG_HORIZON.md` § 11 : A + funding `NON_INTERESSANT` |
| DE03 | Prime du perpétuel en décote | pratique | 24 h sous le 10e centile | 1j | `TESTÉ` | `DERIVATIVES.md` : `AUCUNE_PISTE` |
| DE04 | Purge de l'intérêt ouvert | pratique | variation 24 h sous le 10e centile et spot en baisse | 1j | `TESTÉ` | `DERIVATIVES.md` : `AUCUNE_PISTE` ; F9 en direct |
| DE05 | Comptes majoritairement vendeurs | pratique | ratio comptes sous le 10e centile | 1j | `TESTÉ` | `DERIVATIVES.md` : `AUCUNE_PISTE` |
| DE06 | Positionnement des gros traders (BTC) | pratique | moyenne 7 j du ratio, tiers | 1j | `TESTÉ` | `CONTEXTE_PREDICTION.md` : TOP_TRADERS `RIEN` |
| DE07 | Ratios des comptes et des gros traders des altcoins, en coupe | pratique | quintile | 1j | `À_TESTER (attente)` | **en direct seulement** (choix du 2026-10-11) : archive `metrics` en magasin pour les 16 paires de la configuration depuis 2021-12 ; aucune découverte sur 2019-2022 possible, et une découverte sur 2022 seul n'aurait aucune puissance (`ZOO.md` § 13) ; **déjà vu** : cousin de DE05 et DE06 ; une étude historique séparée sur 2022-01 → 2025-06 reste possible plus tard, comptée à part |
| DE08 | Ratio acheteurs / vendeurs agressifs du perpétuel | pratique | quintile | 1j | `À_TESTER (attente)` | idem DE07 |
| DE09 | Base trimestrielle spot / à terme | pratique | base annualisée basse | 1j | `À_TESTER (attente)` | bougies des contrats trimestriels à télécharger (magasin : 500 jours seulement) |
| DE10 | Cascade de liquidations | pratique | F25 | 1h | `IMPOSSIBLE` | raison 4 : aucun historique gratuit ; F25 pré-inscrit, non démarré |
| DE11 | Capitulation du marché à levier | pratique | F6 | 1j | `EN_DIRECT` | F6_CAPITULATION (≈ 0,2 événement par 12 semaines) |
| DE12 | DVOL au plus haut de 30 jours (peur sur les options) | pratique | `OPTIONS_PEUR.md` | 1j | `À_TESTER (hors moteur)` | DVOL en magasin, mais contrôle H0 `INSTRUMENT_TROP_FAIBLE` (puissance 0,005), 0 essai |
| DE13 | Asymétrie 25-delta des options BTC | pratique | F29 | 1j | `À_TESTER (attente)` | F29 pré-inscrit, non démarré ; journaux du collecteur |
| DE14 | Ratio puts / calls, « max pain » | pratique | — | 1j | `IMPOSSIBLE` | raison 2 : historique payant ; relevé depuis le 2026-10-04 |
| DE15 | Portage spot long + perpétuel court | pratique | — | — | `IMPOSSIBLE` | raison 1 : vente de perpétuel, flux de type intérêt (`PLAN_DE_TRAVAIL.md` § 6) |

## 13. On-chain gratuit (OC)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| OC01 | Flux nets de BTC vers les plateformes | pratique | somme 7 j / stock, tiers | 1j | `TESTÉ` | `CONTEXTE_PREDICTION.md` : BTC_NETFLOW `RIEN` (8 années sur 9 de même signe, non démontré) |
| OC02 | Flux nets d'ETH | idem | idem | 1j | `TESTÉ` | ETH_NETFLOW `RIEN` |
| OC03 | Croissance de l'encours des stablecoins | pratique | 7 j | 1j | `TESTÉ` | STABLE_GROWTH `RIEN` |
| OC04 | Grosse émission de stablecoins → BTC | pratique | ≥ 100 M$ en 1 h | 15 min | `EN_DIRECT` | F3_STABLECOINS |
| OC05 | MVRV bas | pratique | ≤ 20e centile 730 j | 1j | `TESTÉ` | `SCREENING.md` J4 : rien |
| OC06 | MVRV haut (veto) | pratique | ≥ 80e centile | 1j | `TESTÉ` | J5 : veto non justifié |
| OC07 | Offre dormante, « actifs perdus » (BTC) | pratique | — | 1j | `À_TESTER (attente)` | séries communautaires de CoinMetrics à vérifier et télécharger |
| OC08 | Adresses actives, NVT de BTC et d'ETH | pratique | NVT sur 90 j | 1j | `À_TESTER (attente)` | idem |
| OC09 | On-chain des altcoins | pratique | — | — | `IMPOSSIBLE` | raison 2 : payant |

## 14. Événements (EV)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| EV01 | Annonce de listing chez Upbit ou Coinbase | pratique | achat au premier prix, 24 h et 7 j | 10 min | `EN_DIRECT` | F7_LISTINGS |
| EV02 | Annonce de cotation spot sur Binance | pratique | annonces `COTATION_SPOT` (`annonces_binance`, connues à la publication + 10 min) ; achat de la paire USDT annoncée à la clôture suivant sa 1re journée complète de cotation ; tenue 30 jours | 1j | `À_TESTER (zoo)` | univers d'exception : toutes les paires USDT du recensement (exclusions structurelles et haram), bougies journalières de `data/pit/daily.parquet` (clôtures) ; les renommages n'ont pas d'annonce `COTATION_SPOT` ; référence : paires cotées depuis plus de 180 jours, le même jour ; **descriptif seulement dans F31** (pas de relevé halal au moment de l'annonce) |
| EV03 | Annonce de retrait de la cote (veto) | pratique | — | 1j | `À_TESTER (attente)` | archive des annonces de Binance non collectée |
| EV04 | Déblocages de jetons | pratique | — | — | `IMPOSSIBLE` | raison 2 : sources payantes ou fermées (`FORWARD_TESTS.md`, « Calendrier des unlocks : non fait ») |
| EV05 | « Airdrops » | pratique | — | — | `IMPOSSIBLE` | raison 4 : aucun historique gratuit fiable |
| EV06 | Piratages | pratique | achat après la chute, ou veto | 1j | `À_TESTER (attente)` | historique gratuit à vérifier ; news collectées depuis le 2026-09-30 seulement |
| EV07 | News de risque (gravité 3) | pratique | `NEWS.md` | 15 min | `EN_DIRECT` | F8 et étude d'événements à 30 événements |

## 15. Régimes et modèles (RG)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| RG01 | Chaîne de Markov cachée à 2 états (≡ changement de régime de Hamilton) | Hamilton (1989) | 2 états gaussiens sur BTC, réestimation mensuelle | 1j | `TESTÉ` | `FACTORS.md` : REGIME_HMM `AUCUNE_PISTE` |
| RG02 | Chaîne de Markov cachée à 3 états | Ang et Bekaert (2002) | 3 états gaussiens sur les rendements journaliers de BTC, ordonnés par la variance (comme `FACTORS.md`) ; univers investi sauf si la probabilité filtrée de l'état le plus volatil dépasse 0,5 | 1j | `À_TESTER (zoo)` | — |
| RG03 | Filtre de Kalman (tendance locale linéaire) | Harvey (1989) | pente filtrée du log-prix > 0 ; variances estimées par vraisemblance sur le seul passé | 1j | `À_TESTER (zoo)` | — |
| RG04 | Exposant de Hurst | Hurst (1951), Peters (1994) | R/S sur 128 rendements journaliers > 0,5 et rendement 20 j > 0 (a priori) | 1j | `À_TESTER (zoo)` | — |
| RG05 | Entropie | Bandt et Pompe (2002) | entropie de permutation (ordre 3, 64 j) dans le tiers bas de ses 365 j et rendement 20 j > 0 (a priori) | 1j | `À_TESTER (zoo)` | — |
| RG06 | Ondelettes | pratique | tendance de Haar niveau 3 (débruitée), pente > 0 (a priori) | 1j | `À_TESTER (zoo)` | — |
| RG07 | Cycles : sinusoïde d'Ehlers | Ehlers (2001) | réglages de l'auteur ; achat quand la sinusoïde croise au-dessus de sa version avancée | 4h, 1j | `À_TESTER (zoo)` | — |
| RG08 | GARCH en signal (exposition selon la volatilité prévue) | Bollerslev (1986) ; Moreira et Muir (2017) | GARCH(1,1) | 1j | `TESTÉ` | équivalent du ciblage de volatilité (TR43) ; GARCH comme prévision : `VOLATILITY.md` § 17, `AUCUNE_AMELIORATION` |
| RG09 | Prévision de volatilité (ampleur, pas le sens) | HAR (Corsi, 2009) | `VOLATILITY.md` | 1h, 1j | `TESTÉ` | `VOLATILITY.md` § 19 : **H24 confirmée** sur la période réservée ; outil de risque (`RISK_SHADOW.md`), pas une stratégie de sens ; F12 en direct |
| RG10 | Météo du marché à 6 dangers (jouer ou rester en USDT) | demande du propriétaire | `METEO_MARCHE.md` § 4 | 1j | `À_TESTER (attente)` | `INSTRUMENT_NON_VALIDE` (contrôles synthétiques en échec), 0 essai ; F17 possible, non démarré ; Fear & Greed sans historique en magasin |

## 16. Apprentissage (ML)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| ML01 | Méta-étiquetage logistique de A, B, C | López de Prado (2018) | `ML.md` | 15 min | `TESTÉ` | `ML.md` : `NOT_USEFUL` (×3) |
| ML02 | LightGBM, XGBoost, logistique en intraday | pratique | `ML_INTRADAY.md` | 15 min | `TESTÉ` | `ML_INTRADAY.md` § 12 : admissible par la règle, **aucun avantage démontré** |
| ML03 | LightGBM, CatBoost, logistique à 1-7 jours | pratique | `ML_SWING.md`, `ML_SWING_LONG.md` | 1j | `TESTÉ` | `AUCUN_AVANTAGE_DEMONTRE` (×2) ; corrélation de rang ≈ 0 (`INFORMATION_REPORT.md`) |
| ML04 | Régressions (linéaire, logistique) de référence | — | idem ; `COMBINAISONS.md` LOGIT | 1h, 1j | `TESTÉ` | mêmes documents ; LOGIT `PERTE_DEMONTREE` ou `GAIN_NON_DEMONTRE` |
| ML05 | LSTM en coupe | Fischer et Krauss (2018) | séquences de 240 rendements standardisés, 25 unités, dropout 0,1, RMSprop, arrêt précoce 10 | 1j | `À_TESTER (hors moteur)` | protocole ML propre (référence linéaire obligatoire, `POSITIVE_CONTROL.md` § 8) ; 200 entraînements au contrôle H0 hors budget |
| ML06 | Transformer | pratique | aucun réglage standard publié | 1j | `À_TESTER (hors moteur)` | idem, plus le choix d'architecture à déclarer |
| ML07 | CNN sur images de graphiques | Jiang, Kelly et Xiu (2023) | version 20 jours | 1j | `TESTÉ` | `CNN.md` (`CNN-20261003T163025Z-70e39d`) : `NE_PASSE_PAS` |
| ML08 | IA de vision locale lisant les graphiques | pratique | Qwen2.5-VL 7b, Gemma 3 12B | 1h–1j | `TESTÉ` | `IA_GRAPHES.md` : `PAS_MIEUX` (×2) |
| ML09 | Apprentissage par renforcement | pratique | aucun réglage de référence | — | `IMPOSSIBLE` | raison 6 : non standardisable, chaque choix (récompense, état, architecture) est un réglage |
| ML10 | Agents LLM « tout-en-un » | TradingAgents, FinRL… | — | — | `IMPOSSIBLE` | raison 6 : mémoire du LLM, non reproductible (`PLAN_DE_TRAVAIL.md` § 6) |
| ML11 | Abstention par intervalles de rendement | pratique | `QUANTILES.md` | 1j | `TESTÉ` | `QUANTILES.md` § 9 : `AUCUNE_AMELIORATION` |

## 17. Portefeuille (PF)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| PF01 | DCA par paliers | pratique des bots | un quart puis un quart tous les −5 % | 1h | `TESTÉ` | `GRID_DCA.md` (survivantes et à date) : ne fait pas mieux que garder |
| PF02 | DCA avec prise de gain | idem | + vente au coût moyen + 3 % | 1h | `TESTÉ` | `GRID_DCA.md` : **moins bien** que garder (IC < 0) |
| PF03 | Grille ±10 % | idem | pas de 2 % | 1h | `TESTÉ` | `GRID_DCA.md` : ne fait pas mieux que garder |
| PF04 | Grille ±20 % | idem | pas de 4 % | 1h | `TESTÉ` | idem |
| PF05 | Rééquilibrage à poids égaux (« volatility harvesting ») | Fernholz et Shay (1982) ; Booth et Fama (1992) | poids égaux rééquilibrés chaque mois, contre les mêmes paires laissées flotter | 1j | `À_TESTER (zoo)` | — |
| PF06 | Parité de risque (inverse de la volatilité) | Maillard, Roncalli et Teïletche (2010), version naïve | poids ∝ 1 / volatilité 30 j, mensuel | 1j | `À_TESTER (zoo)` | — |
| PF07 | Minimum de variance, long seul | Clarke, de Silva et Thorley (2006) ; Ledoit et Wolf (2004) | covariance 90 j rétrécie, poids ≥ 0, mensuel | 1j | `À_TESTER (zoo)` | — |
| PF08 | Kelly fractionnaire | Kelly (1956) | demi-Kelly | — | `IMPOSSIBLE` | raison 7 : exige une espérance positive prévue ; sans avantage démontré, la taille de Kelly est nulle |
| PF09 | BTC / ETH 60 / 40 | pratique | rééquilibré chaque mois | 1j | `À_TESTER (zoo)` | — |
| PF10 | Panier basse volatilité tenu 6 mois avec stop −25 % (B du lot 8) | lot 8 | `LONG_HORIZON.md` § 4 | 1j | `TESTÉ` | `LONG_HORIZON.md` § 11 : `NON_INTERESSANT` |

## 18. Exécution et frais (EX)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| EX01 | Ordre limite (maker) contre ordre au marché (taker) à l'entrée | pratique | F1 | 15 min | `EN_DIRECT` | F1_MAKER_TAKER |
| EX02 | Remise BNB sur les frais | Binance | −25 % : 7,5 pb par ordre | — | `TESTÉ` | `PROTOCOL.md`, révision du 2026-10-03 : appliquée ; vérification des frais réels en Demo à venir |
| EX03 | Découpage des ordres | pratique | — | — | `TESTÉ` | `TICKS.md` : un petit achat au marché coûte ≈ 0 à 1 pb ; découper est sans objet à la taille d'un particulier |
| EX04 | Échelles d'objectifs (2 à 7 TP) | pratique des groupes | F2 | 15 min | `EN_DIRECT` | F2_ECHELLES |
| EX05 | Stops resserrés, à la clôture, fixes | pratique | `FIGURES_HISTORIQUE.md` | 1h–1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` (`DBLS-20261004T125331Z-7338fa`) : `NON_DEMONTRE` (×3) |
| EX06 | Arbitrage entre bourses | pratique | net des frais d'un particulier | 10 min | `TESTÉ` | `SPREADS.md` : écarts nets de −16 à −68 pb, pas d'arbitrage |
| EX07 | Tenue de marché (« market making ») | pratique | — | — | `IMPOSSIBLE` | raison 3 : latence et remises réservées aux gros comptes |
| EX08 | Arbitrage triangulaire | pratique | — | — | `IMPOSSIBLE` | raison 3 : latence |

## 19. Méthodes d'analystes (AN)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| AN01 | Fair value gap | ICT | `INDICATEURS.md` § 2 | 1h, 4h, 1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` : FVG `NON_DEMONTRE` |
| AN02 | Order block | ICT | § 3 | 1h, 4h, 1j | `TESTÉ` | OB `INFERIEUR_AU_HASARD` |
| AN03 | Liquidity sweep | ICT | § 4 | 1h, 4h, 1j | `TESTÉ` | SWEEP `INFERIEUR_AU_HASARD` |
| AN04 | Configuration ICT complète (sweep puis MSS avec FVG) | ICT | § 9.4 | 1h, 4h, 1j | `TESTÉ` | ICT `NON_DEMONTRE` ; F15 en direct |
| AN05 | Zone OTE (62–79 %) | ICT | `INDICATEURS.md` § 10.6 ; achat à la 1re clôture qui repasse au-dessus de 62 % après un contact dans la zone | 1h, 4h, 1j | `DOUBLON` | déjà vu : ST05 (Fibonacci 61,8 %, mêmes signaux à peu près) ; retiré du zoo le 2026-10-11 |
| AN06 | Breaker block haussier | ICT | `INDICATEURS.md` § 10.6 ; achat au premier retour dans la zone clôturé au-dessus | 1h, 4h | `À_TESTER (zoo)` | — |
| AN07 | « Spring » de Wyckoff | Wyckoff (années 1930) | équivalents mécaniques : retour au-dessus d'un support enfoncé, sweep | 15 min, 1h | `TESTÉ` | E (`SCREENING.md`, sous les coûts) et SWEEP (`FIGURES_HISTORIQUE.md`, inférieur au hasard) |
| AN08 | Méthode de Wyckoff complète (phases) | Wyckoff | — | — | `IMPOSSIBLE` | raison 6 : lecture des phases non mécanisable |
| AN09 | Elliott mécanique : entrée en vague 3 | Elliott (1938) ; version ZigZag | pivots ZigZag : jambe 1 haussière, repli 2 de 38,2 à 78,6 % qui reste au-dessus du départ de 1, achat à la clôture au-dessus du sommet de 1 ; sortie à 1,618 × la jambe 1 ou sous le creux 2 | 4h, 1j | `À_TESTER (zoo)` | — |
| AN10 | Comptage d'Elliott complet | Elliott | — | — | `IMPOSSIBLE` | raison 6 : comptage subjectif |
| AN11 | Harmoniques Gartley et ABCD | Gartley (1935), Carney (1999) | `INDICATEURS.md` § 9.1 | 1h, 4h, 1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` : `NON_DEMONTRE` ; F15 |
| AN12 | Harmoniques Bat, Butterfly, Crab | Carney | idem | 1h, 4h, 1j | `TESTÉ` | `INSUFFISANT` (ordres rarement remplis) ; F15 |
| AN13 | Graphique de swing de Gann | Gann ; version 2 bougies | swing haussier : 2 plus hauts consécutifs plus hauts ; long tant que le dernier sommet de swing est dépassé | 1j | `À_TESTER (zoo)` | — |
| AN14 | Angles de Gann, carré de 9 | Gann | — | — | `IMPOSSIBLE` | raison 6 : dépend d'une échelle prix / temps arbitraire |
| AN15 | Figures chartistes (tête-épaules inverse, double creux, drapeau, coupe avec anse, triangles) | Edwards et Magee | `INDICATEURS.md` § 9.2, 9.6 | 1h, 4h, 1j | `TESTÉ` | `FIGURES_HISTORIQUE.md` : `NON_DEMONTRE` ou `INSUFFISANT` ; F15 en direct |
| AN16 | Gestion du propriétaire sur le double creux (60 % au TP1, 5 TP) | propriétaire | `FIGURES_HISTORIQUE.md` | 1h–1j | `TESTÉ` | `NON_DEMONTRE`, identique aux tiers |

## 20. Signaux de tiers (TI)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| TI01 | Signaux des groupes Telegram du propriétaire | groupes | gestion du propriétaire | — | `TESTÉ` | audits d'historique : aucun fournisseur avec un gain démontré (`FORWARD_TESTS.md`, F4, hypothèse) ; F4 et F16 en direct |
| TI02 | Filtre des signaux Telegram par les briques | `COMBINAISONS.md` § 5 | FILTRE_3, FILTRE_4 | — | `TESTÉ` | `COMBINAISONS.md` § 11-12 (période réservée, consultation n° 4) : `RIEN` |
| TI03 | Copie de traders (« copy trading » Binance) | Binance | — | — | `IMPOSSIBLE` | raison 4 : positions historiques non publiques ; raison 1 : surtout des contrats à levier |
| TI04 | Signaux d'IA ou de bots tiers | marché | — | — | `IMPOSSIBLE` | raison 4 : aucun historique vérifiable (`PLAN_DE_TRAVAIL.md` § 6) |
| TI05 | Assistant de marché de CSI | CSI | `ASSISTANT.md` | 4h | `EN_DIRECT` | F18_ASSISTANT |

## 21. Hors cadre (HC)

| ID | Stratégie | Source | Paramètres standard publiés | Unités | Statut | Renvoi / raison |
|---|---|---|---|---|---|---|
| HC01 | Paires cointégrées | Gatev, Goetzmann et Rouwenhorst (2006) | — | — | `IMPOSSIBLE` | raison 1 : vente à découvert |
| HC02 | Facteurs long-court | LTW (2022) | — | — | `IMPOSSIBLE` | raison 1 ; leur côté acheteur est mesuré en § 7 |
| HC03 | Stratégies d'options (achat ou vente) | pratique | — | — | `IMPOSSIBLE` | raison 1 : options exclues du cadre |
| HC04 | Vente à découvert en tendance baissière | pratique | — | — | `IMPOSSIBLE` | raison 1 ; un contexte baissier donne NO_TRADE |
| HC05 | Staking, Earn, prêts | Binance | — | — | `IMPOSSIBLE` | raison 1 : rendement assimilable à un intérêt |
| HC06 | Grilles et DCA sur contrats à levier | bots du marché | — | — | `IMPOSSIBLE` | raison 1 |

---

## Comptes

Comptés sur les tableaux ci-dessus (une ligne = une stratégie, ses unités de temps comprises), après la révision
du 2026-10-11 :

| Statut | Stratégies |
|---|---|
| `TESTÉ` | 89 |
| `EN_DIRECT` | 10 |
| `À_TESTER (zoo)` | 130 |
| `À_TESTER (attente)` | 14 |
| `À_TESTER (hors moteur)` | 3 |
| `IMPOSSIBLE` | 45 |
| `DOUBLON` | 1 |
| **Total** | **292** |

`À_TESTER` en tout : 147. Les 130 stratégies `À_TESTER (zoo)` donnent **239 tests** (une stratégie × une unité
de temps), protocole dans [ZOO.md](ZOO.md).

Par famille (T = `TESTÉ`, D = `EN_DIRECT`, Z = zoo, A = attente, H = hors moteur, I = `IMPOSSIBLE`, V = `DOUBLON`) :

| Famille | Total | T | D | Z | A | H | I | V | Tests du zoo |
|---|---|---|---|---|---|---|---|---|---|
| 1. Tendance et cassures (TR) | 48 | 14 | 0 | 34 | 0 | 0 | 0 | 0 | 83 |
| 2. Retour à la moyenne (MR) | 20 | 7 | 1 | 11 | 0 | 0 | 1 | 0 | 23 |
| 3. Volatilité et range (VO) | 11 | 0 | 1 | 10 | 0 | 0 | 0 | 0 | 14 |
| 4. Chandeliers (CH) | 21 | 0 | 0 | 10 | 0 | 0 | 11 | 0 | 29 |
| 5. Structure (ST) | 15 | 1 | 0 | 14 | 0 | 0 | 0 | 0 | 22 |
| 6. Volume et flux (VF) | 17 | 7 | 0 | 7 | 3 | 0 | 0 | 0 | 21 |
| 7. Facteurs en coupe (XS) | 24 | 9 | 0 | 13 | 0 | 0 | 2 | 0 | 13 |
| 8. Rotation et marché (RO) | 6 | 0 | 0 | 4 | 0 | 0 | 2 | 0 | 4 |
| 9. Autres marchés (AM) | 9 | 1 | 0 | 7 | 1 | 0 | 0 | 0 | 7 |
| 10. Calendrier (CA) | 14 | 8 | 0 | 4 | 0 | 0 | 2 | 0 | 4 |
| 11. Sentiment et attention (SE) | 10 | 1 | 1 | 2 | 1 | 0 | 5 | 0 | 2 |
| 12. Marché à terme public (DE) | 15 | 6 | 1 | 0 | 4 | 1 | 3 | 0 | 0 |
| 13. On-chain gratuit (OC) | 9 | 5 | 1 | 0 | 2 | 0 | 1 | 0 | 0 |
| 14. Événements (EV) | 7 | 0 | 2 | 1 | 2 | 0 | 2 | 0 | 1 |
| 15. Régimes et modèles (RG) | 10 | 3 | 0 | 6 | 1 | 0 | 0 | 0 | 7 |
| 16. Apprentissage (ML) | 11 | 7 | 0 | 0 | 0 | 2 | 2 | 0 | 0 |
| 17. Portefeuille (PF) | 10 | 5 | 0 | 4 | 0 | 0 | 1 | 0 | 4 |
| 18. Exécution et frais (EX) | 8 | 4 | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| 19. Méthodes d'analystes (AN) | 16 | 9 | 0 | 3 | 0 | 0 | 3 | 1 | 5 |
| 20. Signaux de tiers (TI) | 5 | 2 | 1 | 0 | 0 | 0 | 2 | 0 | 0 |
| 21. Hors cadre (HC) | 6 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 |
| **Total** | **292** | **89** | **10** | **130** | **14** | **3** | **45** | **1** | **239** |

Lecture : sur 292 stratégies recensées, 89 ont déjà un verdict historique (aucune n'a d'avantage démontré après
frais ; seule la prévision de volatilité, RG09, qui ne dit pas le sens, est confirmée), 10 sont mesurées seulement
en direct, 45 sont hors de portée, 1 est un doublon et 147 restent à mesurer, dont 130 par le zoo. Le mot « exhaustif » de la
première version est retiré : c'est un recensement, auquel la relecture a déjà ajouté huit familles oubliées.

## Historique

- 2026-10-10 : catalogue rédigé avant tout code et toute exécution (branche `recherche/zoo`), à partir des verdicts
  inscrits dans les documents cités ; aucun calcul.
- 2026-10-11 : relecture leak-auditor, aucun résultat vu. « Exhaustif » retiré ; ajouts : point et figure (TR48), IBS
  (MR19), Double 7s (MR20), momentum intrajournalier (VO11), Elder Force Index (VF16), oscillateur de Klinger (VF17),
  distance aux moyennes mobiles (XS23), retournement d'un jour en coupe (XS24) ; CH06, CH07, CH09 et CA14 passent
  `IMPOSSIBLE` ; doublons marqués « déjà vu » (TR12, TR13, TR33 à TR35, ST11, VO08, XS20) ; XS17 exige 365 jours de
  cotation ; données du zoo fusionnées sur `main` (`ce65673`, `CONTEXTE.md`, « Données du zoo ») : SE01, SE02, AM06,
  AM07, AM08, CA09, CA10, CA11 et EV02 passent au zoo ; AM05 (M2 révisé) retiré ; DE07 et DE08 en direct seulement.
- 2026-10-11 (deuxième relecture, aucun résultat vu) : exécution de MR19 et XS24 à la 1re bougie 1 h après le signal ;
  fenêtres de CA09 à CA11 bornées par l'heure de publication ; formulation unique de SE01 et limite de l'historique
  d'alternative.me ; EV02 : référence des paires cotées depuis plus de 180 jours, descriptif seulement dans F31 ; DE07
  cousin déjà vu de DE05 et DE06. Comptes inchangés (292 stratégies, 257 tests).
- 2026-10-11 (décisions de mise en œuvre avant tout résultat, après le codage des familles ; aucun rendement réel lu) :
  CH02, CH11, CH12, CH15, CH16 passent `IMPOSSIBLE` (raison 7 : écart d'ouverture exigé, comme CH06, CH07, CH09, CH14,
  CH18) ; AN05 passe `DOUBLON` (déjà vu : ST05) ; nouveau statut `DOUBLON` ; comptes : 130 stratégies au zoo,
  **239 tests** (292 stratégies inchangées).
