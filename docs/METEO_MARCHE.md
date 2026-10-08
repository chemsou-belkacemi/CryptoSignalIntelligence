# Météo du marché : jouer ou ne pas jouer (déclaré le 2026-10-08, avant tout code, téléchargement et calcul)

**Statut : pré-enregistrement.** Écrit le 2026-10-08, avant tout code, tout téléchargement et tout calcul sur des prix.
Aucun résultat de ce programme n'existe. Les seuls chiffres qui suivent sont cités d'études précédentes (avec leur
document), sont des hypothèses déclarées comme telles, ou viennent de la lecture du registre des essais. Toute
modification ultérieure est datée en bas (« Historique »). Elle ne peut porter que sur des points techniques, et jamais
après avoir vu un résultat de l'étape concernée.

**Demande du propriétaire (2026-10-08).** Avant de chercher quoi acheter, savoir s'il faut jouer du tout : « ces
jours-ci le marché est merdique, tout le monde perd, donc le conseil général doit dire de ne pas prendre de risque ».
Achat seulement (Spot, pas de vente à découvert, cadre halal). Trois sorties : **vert** (on peut jouer), **orange**
(taille réduite, seulement les meilleurs signaux), **rouge** (rien). L'avis doit tenir compte de plusieurs données à la
fois : structure de BTC et du marché, volatilité, sentiment, positionnement des dérivés, news, liquidité, résultats
récents des signaux.

**Lu avant d'écrire, sans rien en tirer d'après un résultat de ce programme** : `FORWARD_TESTS.md` (F0, F3, F5, F8,
F9, F12, VOTE_V1), `CONTEXTE.md`, `CONTEXTE_PREDICTION.md`, `SCREENING.md` (J2, J4, J5, K), `DERIVATIVES.md`,
`VOLATILITY.md` (§ 16 et § 19), `RISK_SHADOW.md`, `LONG_HORIZON.md` (§ 11), `TREND_DAILY.md`, `COMBINAISONS.md`,
`PROTOCOL.md`, `research/protocol.py`, et le registre `experiments/` en lecture seule. Les résultats des étapes 1 et 2
de `COMBINAISONS.md` (exécutions `CMB1-20261008T182200Z-db1ea4` et `CMB2-20261008T182516Z-a2e56a`, faites le même jour)
**n'ont pas été lus** pour écrire ce document. Aucune composante n'a été choisie d'après eux. Ces études utilisent des
états proches (`BTC_HAUSSIER`, `VOL_CALME`) : si le propriétaire en a vu les résultats, il faut le savoir, et c'est
inscrit dans l'historique.

## 0. En bref, pour le propriétaire

- **Ce que c'est.** Chaque jour à 00:10 UTC, un feu calculé avec ce qu'on sait à cette heure-là. On compte combien de
  « dangers » sont allumés parmi 6 :
  1. BTC clôture sous sa moyenne de 50 jours ;
  2. moins d'un tiers des 40 paires clôturent au-dessus de leur propre moyenne de 50 jours ;
  3. la volatilité prévue de BTC pour les 24 h est dans les 10 % les plus hautes de l'année écoulée ;
  4. le Fear & Greed est sous 25 (« peur extrême ») ;
  5. le financement des perpétuels BTC est « chaud » (plus de 0,05 % par 8 h en moyenne sur 7 jours) ;
  6. les achats faits au hasard la semaine passée ont été parmi les 20 % pires de l'année (« tout le monde perd »).

  **0 ou 1 danger : vert. 2 : orange. 3 ou plus : rouge.** Rien n'est réglé : ces seuils sont écrits ici et ne
  bougeront plus.
- **La question.** De 2020 à mi-2025, si on n'avait **rien acheté les jours rouges**, aurait-on fait mieux qu'en
  sautant **autant de jours, tirés au hasard** ? On le mesure sur des achats génériques : 20 achats au hasard par jour
  sur les 40 paires, et un panier égal des 40 paires tenu 24 h. On mesure le résultat moyen par achat, la pire perte et
  la part de jours sautés.
- **Le piège.** Un feu qui dit « rouge » quand le marché baisse a toujours l'air génial après coup : il saute 2022 et
  garde 2021. Mais n'importe quelle règle qui suit la tendance fait ça sur le passé, et 5 ans ne contiennent que deux
  grands cycles. Donc la décision principale compare le feu à des tirages au hasard **dans le même trimestre** : le feu
  doit choisir les mauvais jours à l'intérieur d'une même période, pas seulement « deviner » que 2022 était baissière.
  On regarde aussi 2022 seule.
- **Ce qu'on sait déjà.** Le sens du marché n'a jamais été prévu dans ce projet (873 essais) : données de contexte,
  marché à terme, sentiment, MVRV, filtres de tendance, rien ne bat le hasard. Les filtres de tendance réduisent la
  perte maximale, mais une exposition réduite au hasard fait aussi bien (`LONG_HORIZON.md`, `TREND_DAILY.md`). La seule
  chose démontrée, c'est l'**ampleur** des mouvements à 24 h, pas leur sens. Ajuster la taille selon cette ampleur
  (`RISK_SHADOW.md`) reste le seul outil de risque démontré.
- **Ce qu'on peut espérer, honnêtement.** Avec 5,4 ans d'historique, même un feu qui aurait parfaitement reconnu 2022
  serait à peine visible statistiquement (§ 13). Le résultat le plus probable est « rien », ou au mieux un « indice »
  qui ne prouve rien. Un « rien » voudra dire « pas d'effet assez gros pour être vu », pas « la météo est inutile ».
  La vraie preuve ne peut venir que du **temps** : un test en direct (F17), qui demandera des mois, plus
  probablement un an.
- **Ce que la météo ne fait pas.** Elle ne dit pas quoi acheter. Elle ne rend aucun signal rentable. Elle ne touche
  ni BinanceSpotManager, ni les signaux de CSI, ni aucun test en cours.
- **Coût en essais.** 5 essais sur DEVELOPMENT pour l'étape principale et 2 pour les références. Au plus 13 en tout
  si tu demandes le modèle appris et si une confirmation sur d'autres paires a lieu : le registre passerait de 873 à
  886 au plus. La période réservée reste fermée : 0 essai de plus sur FINAL_TEST, sauf si tu décides autrement (§ 10
  et § 8.2).
- **Ce qu'on te demande.**
  1. Valider ce protocole.
  2. Valider le téléchargement de l'historique du Fear & Greed : même source et même adresse que le relevé quotidien
     déjà validé le 2026-10-02, en un seul appel.
  3. Plus tard, décider du modèle appris (§ 9), des signaux Telegram de 2026 (§ 10) et du démarrage du test en direct
     (§ 11).

## 1. Ce qui existe déjà, et où se place la météo

| Existant | Ce qu'il fait | Rapport avec la météo |
|---|---|---|
| **F5_MODELE_A**, feu tricolore (en direct depuis le 2026-10-02) | Feu par **événements**. ROUGE si Fed, CPI ou NFP le jour même (calendrier figé d'octobre 2026 à janvier 2027), prévision de volatilité de BTC **à 7 jours** dans les 10 % les plus hautes de 365 jours, financement BTC sur 7 jours au-dessus de 0,05 % par 8 h, ou USDT / USDC à plus de 0,5 % de leur parité. ROUGE par actif s'il y a une annonce de retrait de la cote. ORANGE le week-end, le dernier vendredi du mois, ou en cas de maintenance Binance. Appliqué au seul modèle A, **descriptif**, sans seuil de performance, 12 semaines. | La météo reprend deux idées de F5 avec les mêmes seuils (financement à 0,05 %, rang de volatilité dans les 10 % les plus hauts), mais avec la **seule prévision confirmée** (24 h, HAR + profil) au lieu de celle à 7 jours, qui n'est pas confirmée. Elle ajoute l'état du marché (structure, largeur, sentiment, résultats récents). Elle laisse de côté ce qui n'a pas d'historique ou ne décrit pas un état du marché (calendrier macro, week-end, maintenance, parité, retraits de la cote). Elle est **jugée** contre des placebos, sur l'historique puis en direct, et s'applique à tout achat. F5 n'est pas touché. F17 relira son feu en lecture seule pour comparer (§ 11). |
| **VOTE_V1** (défini, non évalué) | Vote de composants qui auront passé leur seuil en direct (tendance de A, F6, F3, F8). | Indépendant. La météo ne le modifie pas et n'en fait pas partie. |
| **RISK_SHADOW** (affichage) | Ampleur prévue à 24 h, et taille relative à risque égal. | La météo utilise la même prévision (rang de BTC), mais pour dire « danger », pas pour dimensionner. Le dimensionnement par la volatilité reste l'outil démontré. La météo n'en dit rien de plus. |
| **CONTEXTE_PREDICTION** (`CTXP-20261004T015026Z-30e516`) | Flux, primes, croissance des stablecoins, top traders, Wikipédia contre la direction de BTC à 1, 3 et 7 jours. | **RIEN** sur 21 comparaisons. La croissance des stablecoins n'entre donc pas dans la règle (§ 3.2). |
| **SCREENING J4 / J5** (MVRV) | MVRV bas (achat) et MVRV haut (veto). | **Rien**, aucun veto justifié. Écarté. |
| **SCREENING J2**, F11 | Pression vendeuse extrême, piste de veto. | Mesurée en direct par F11 seulement. Hors météo. |
| **DERIVATIVES** (`SCREEN-20261001T111500Z-60702b`) | Financement bas, prime, purge de l'intérêt ouvert, comptes vendeurs. | **AUCUNE_PISTE**. Le financement « chaud » (sens opposé, repris de F5) entre comme danger. L'intérêt ouvert est écarté (§ 3.2). F9 le mesure en direct. |
| **F3**, **F8**, **F0** | Émissions de stablecoins, filtre de news, relevés quotidiens (dérivés, Fear & Greed, profondeur des carnets, liquidations OKX). | Historique trop court (depuis le 2026-09-30 / 10-02). Écartés de la règle et relevés en descriptif par F17 (§ 11). |
| **LONG_HORIZON § 11**, **TREND_DAILY § 8** | Filtres de tendance à l'échelle du portefeuille. | Leçon reprise : la perte maximale baisse surtout parce qu'on est **moins exposé**. Une exposition égale au hasard fait presque pareil. D'où des placebos qui sautent **la même part de jours** (§ 5.4). |
| **COMBINAISONS** | Briques et votes, placebos appariés, contrôle nul synthétique. | Leçons reprises : placebos appariés (ici par trimestre), excès de « timing » distingué de l'excès de « régime », la part régime confirmable seulement dans le temps, garde-fous, contrôle nul et contrôle positif synthétiques, comptages avant l'exécution, et confirmation sur des paires jamais utilisées, nécessaire mais pas suffisante. |

## 2. Règles communes

- **DEVELOPMENT seulement**, jusqu'au 2025-06-30 23:59:59 UTC (`research/protocol.py`, `FROZEN_DEVELOPMENT_END`).
  Toute série plus longue sur disque (contexte, dérivés, Fear & Greed) est coupée à la lecture. Un test vérifie que
  falsifier tout ce qui suit la coupure ne change rien.
- **Aucun réglage.** Les composantes, leurs seuils, la règle du feu, les mesures et les placebos sont fixés ici.
  Rien ne bouge après les comptages ni après un résultat, quel qu'il soit. **Liste fermée** : aucune composante
  ajoutée, retirée ou inversée.
- **Tout essai est compté** dans `program_trials` (§ 12). Au 2026-10-08, le registre compte **873 essais** sur
  DEVELOPMENT (62 exécutions), **8** sur FINAL_TEST et **3 consultations** de la période réservée.
- **Exécution unique par type** (§ 15). Elle a lieu après la relecture `leak-auditor` du code, les contrôles du § 7 et
  les comptages inscrits. Une seule tâche lourde à la fois, 4 processus au plus.
- **Cadre halal.** Achats Spot seulement. Les données du marché à terme servent d'information. Aucun ordre, aucune
  clé, aucun secret. `data/http.py` n'est pas élargi. Aucun module gelé d'un test en direct n'est modifié
  (`tests/test_frozen_running_tests.py`).
- **Seul téléchargement nouveau** : l'historique quotidien du Fear & Greed (alternative.me, `/fng/` avec
  `limit=0`). C'est la même adresse que le relevé F0, déjà dans la liste blanche du client `forward/sources.py`
  qu'utilise aussi `context/`. Il est rangé comme série `HISTORIQUE` du magasin de contexte, avec son empreinte. Il
  attend l'accord du propriétaire.

## 3. Les entrées

### 3.1 Retenues (6 composantes, toutes avec un historique qui couvre la période d'étude)

**Instant de décision du jour `d`** : `T_d = d 00:10:00 UTC`. Une valeur n'entre que si son `available_at` (ou
l'heure de connaissance déclarée ci-dessous) est ≤ `T_d`. « Jour `d − 1` » = la journée UTC complète qui précède.
Clôture journalière = clôture de la bougie 1 h de 23:00 (`available_at` ≈ `d` 00:00:02, donc avant `T_d`). Une
journée n'est complète que si ses 24 bougies 1 h sont présentes.

| Code | Danger si… | Calcul exact | Connu à | Données, depuis |
|---|---|---|---|---|
| `BTC_STRUCTURE` | BTC sous sa moyenne | Clôture journalière de BTCUSDT du jour `d − 1` **≤** EMA50 des clôtures journalières (α = 2/51, départ par la moyenne simple des 50 premières), soit le contraire exact de `BTC_HAUSSIER` (`COMBINAISONS.md` § 2.2). Jour `d − 1` incomplet : composante absente. | `T_d` (dernière bougie : 23:00 de `d − 1`) | magasin long 1 h, BTC depuis 2017-08 |
| `LARGEUR` | marché large en baisse | Parmi les 40 paires de recherche **éligibles** au jour `d − 1` (au moins 50 clôtures journalières complètes, jour `d − 1` complet), part de celles dont la clôture de `d − 1` est **>** leur EMA50 journalière. Danger si la part est **< 1/3**. Moins de 10 paires éligibles : absente. | `T_d` | magasin long 1 h |
| `VOL_HAUTE` | agitation | Prévision **H1_HAR_PROFILE à 24 h** de BTC à l'origine `d` 00:00, hors échantillon : réajustements trimestriels purgés du protocole v3, lus dans `reports/VOL-20261002T211837Z-be55c0/forecasts_24h.parquet` (empreinte inscrite ; c'est le modèle confirmé en § 19 de `VOLATILITY.md`). Rang `p` = part des prévisions de BTC à 00:00 des 365 jours précédents (`d − 365` à `d − 1`, au moins 300 présentes) **strictement inférieures**. Danger si `p ≥ 0,90`. | `T_d` (variables jusqu'à la bougie close à 00:00) | prévisions depuis le 2019-01-01 |
| `PEUR_EXTREME` | sentiment de panique | Fear & Greed d'alternative.me dont l'horodatage est **`d − 1` 00:00 UTC** (un jour de retard par prudence, voir § 3.3). Danger si la valeur est **< 25**. Valeur absente : composante absente. | supposé connu à `d` 00:00, donc avant `T_d` | historique depuis le 2018-02-01 (à télécharger) |
| `FINANCEMENT_CHAUD` | levier en surchauffe | Moyenne des règlements de financement du perpétuel BTCUSDT (USDⓈ-M) dont l'heure de règlement + 1 min est dans `]T_d − 7 j ; T_d]`. Il faut au moins 18 règlements sur 21. Danger si la moyenne est **> 0,05 % par 8 h** (seuil de F5 et de `LONG_HORIZON.md` § 3). | règlement + 1 min (`DERIVATIVES.md`) | archives du financement depuis 2020-01 |
| `PERTES_RECENTES` | « tout le monde perd » | `m_d` = R net moyen (frais centraux) des achats génériques `S1` (§ 5.2) dont le **jour d'entrée** est entre `d − 10` et `d − 4` (7 jours ; tous sont sortis avant `T_d`, voir § 3.3). Il en faut au moins 70. Rang `p` = part des `m` des 365 jours précédents (au moins 300) strictement inférieures à `m_d`. Danger si `p < 0,20`. | `T_d` | magasin minute des 40 paires, depuis 2019 |

Sens des composantes, fixé **a priori**, sans preuve :
- La baisse, la largeur faible, la panique, l'agitation, la surchauffe et les pertes récentes sont des dangers pour un
  acheteur. C'est l'intuition du propriétaire.
- L'hypothèse contraire est connue et plausible : après une peur extrême ou un krach, les meilleurs jours suivent
  souvent les pires. Le test est donc **bilatéral** : un feu qui écarterait les bons jours sort `INVERSE` (§ 5.6).
- Les deux seuils absolus (1/3, 25) et les trois rangs (90 %, 20 %, sur 365 jours) sont des choix a priori, repris de
  F5 quand ils y existent. Ils ne sont pas optimisés.

### 3.2 Écartées (déclaré maintenant)

| Entrée demandée | Pourquoi elle n'entre pas dans la règle | Où elle reste |
|---|---|---|
| Intérêt ouvert | Archives des altcoins depuis 2021-12 seulement (BTC : 2020-09). Valeurs à zéro et horodatages décalés (`DERIVATIVES.md`). **Aucun sens « danger » établi d'avance** : la purge (OI_FLUSH) se lit plutôt comme un achat. | F9 en direct ; F17 en descriptif |
| Liquidations | Une seule bourse (OKX), relevée seulement depuis le 2026-10-02 (F0). Aucun historique. | F17 en descriptif |
| Ratios acheteurs / vendeurs | 30 jours par l'API ; archives de BTC et d'ETH avec un trou du 2021-12-30 au 2022-12-13. | — |
| News (mots-clés de risque) | Collecte depuis le 2026-09-30 seulement. | F8, étude d'événements de `NEWS.md` ; F17 en descriptif |
| Profondeur des carnets | Relevée par F0 depuis le 2026-10-02 seulement. | F17 en descriptif |
| Émissions de stablecoins (F3) | Lecture des chaînes depuis le 2026-10-02 seulement. | F3 ; F17 en descriptif |
| Encours des stablecoins (DefiLlama) | Fiable depuis 2020-07 seulement (`CONTEXTE_PREDICTION.md`), donc absent au début de la période. Déjà testé contre la direction de BTC : **RIEN**. | variable du modèle appris (§ 9) ; descriptif |
| Volumes échangés | Historique complet, mais **sens inconnu d'avance** : un fort volume peut signaler la panique comme l'euphorie, un faible volume le calme. Choisir un sens serait deviner. | variable du modèle appris (§ 9) ; descriptif |
| Parité USDT / USDC, retrait de la cote | Événements trop rares sur 2020-2025 (quelques jours) pour être mesurés. Sans relevé point-in-time avant 2026-10. | règles de bon sens, hors mesure (§ 14) |
| Calendrier macro (Fed, CPI, NFP), week-end, maintenance | Calendrier figé de F5 d'octobre 2026 à janvier 2027 seulement. Le week-end n'est pas un état du marché. | F5 ; le jour de semaine en descriptif |
| Volatilité implicite (DVOL), MVRV, macro (taux, M2, Nasdaq, dollar) | DVOL relevé depuis le 2026-10-02 et redondant avec la volatilité prévue. MVRV : J4 / J5 nuls. Macro : jamais testée, et chaque donnée en plus est une chance de plus de trouver un hasard. | — |

Le propriétaire voulait aussi la liquidité et les news. Elles ne peuvent pas être **jugées** sur l'historique faute de
données. Le test en direct les relève à côté du feu (§ 11). Elles n'entreront dans une règle qu'après un nouveau
protocole.

### 3.3 Causalité, en détail

- **Clôtures journalières** : bougies 1 h dont l'`available_at` est ≤ `T_d`, jamais `open_time`. La journée `d` ne
  sert jamais à décider le jour `d`.
- **Rangs** : la fenêtre de 365 jours s'arrête à `d − 1`. La valeur du jour n'entre pas dans sa propre référence.
- **Fear & Greed** : alternative.me horodate chaque valeur à 00:00 UTC de son jour. L'heure réelle de publication
  n'est pas connue. Prendre la valeur horodatée `d − 1` à `T_d` laisse au moins 24 h 10 de marge. Les journaux F0
  (relevé à 00:10 UTC depuis le 2026-10-02) diront quel horodatage est vraiment disponible à cette heure. C'est un
  contrôle de **métadonnées** seulement, sans prix ni résultat. Révisions possibles : une valeur réécrite après coup
  ne se voit pas dans l'historique. Le relevé F0 (lignes jamais réécrites) permettra de comparer les jours communs.
  Limite déclarée.
- **Prévisions de volatilité** : celles du protocole v3, faites trimestre par trimestre avec des modèles ajustés sur
  les seules origines dont la cible était close avant le réajustement (purge, audit des fuites réussi en v3). La
  prévision couvre 00:00 → 24:00 du jour `d`, et la fenêtre du feu va de 00:10 à 00:10 : léger décalage, déclaré.
- **Financement** : règlement connu 1 minute après son heure, comme dans `DERIVATIVES.md`.
- **Pertes récentes** : un achat générique du jour `d − 4` entre au plus tard à `T_{d−3}` et dure au plus 60 h. Il est
  donc sorti au plus tard à `d − 1` 12:10, avant `T_d`. **Aucune sélection sur la date de sortie** : on prend tous
  les achats des jours `d − 10` à `d − 4`, et ils sont tous connus. Le retard de 3 jours est le prix de cette
  propreté.
- **Jour sans feu** : si deux composantes ou plus sont absentes, le jour est `SANS_FEU`. Il sort de toutes les
  mesures (feu et placebos), et il est compté.

## 4. La règle du feu (fixée, non optimisée)

- `D_d` = nombre de composantes présentes en danger au jour `d`, `A_d` = nombre de composantes absentes.
- `A_d ≥ 2` : `SANS_FEU`. Sinon : **VERT** si `D_d ≤ 1`, **ORANGE** si `D_d = 2`, **ROUGE** si `D_d ≥ 3`.
- Les 6 composantes ont le même poids. Aucune n'a de veto seule.
- Le feu du jour `d` vaut pour tout achat **décidé** entre `T_d` et `T_{d+1}`.
- Pourquoi un comptage plutôt qu'un modèle : il a zéro paramètre appris, chacun peut le recalculer à la main, et un
  seul danger isolé ne suffit pas (les composantes se trompent souvent seules). Le seuil « 3 sur 6 » est le premier
  qui exige que des familles différentes soient d'accord (baisse, agitation, sentiment, résultats). Il est fixé
  maintenant, quelle que soit la part de jours rouges que montreront les comptages (§ 7.4).
- **Orange** : pour les achats génériques, « taille réduite » = demi-taille. « Seulement les meilleurs signaux » n'a
  pas de définition testable sur des achats au hasard. L'orange est donc mesuré en descriptif seulement (§ 5.3).

## 5. Ce qu'on mesure (étape 1, DEVELOPMENT)

### 5.1 Période

Jours de décision du **2020-02-01 au 2025-06-27** (environ 1 975 jours). Avant 2020-02, le financement (archives
depuis 2020-01) et les rangs sur 365 jours ne sont pas pleins. Après le 2025-06-27, un achat générique de 60 h
sortirait de DEVELOPMENT. « Années » de régularité : 2020 (février à décembre), 2021, 2022, 2023, 2024, 2025-S1.

### 5.2 (a) Achats génériques : deux séries de résultats par jour

- **`S1` — `HASARD_40` (en R)** : chaque jour `d`, **20 achats** tirés au hasard (graine `METEO:HASARD:<jour>:<k>`,
  dérivée de 20261008).
  - **Paire** : tirée uniformément parmi les paires de recherche éligibles le jour `d` (règle de FACTORS : au moins
    85 journées valides sur 90, médiane du volume sur 30 jours ≥ 1 M$, au moins 90 jours depuis la première
    bougie 1 h).
  - **Minute** : tirée uniformément parmi les minutes qui ouvrent dans `]T_d ; T_{d+1}]`.
  - **Transaction commune** de `COMBINAISONS.md` § 1.4 : achat au marché à l'ouverture de la minute. Stop = entrée −
    2 × ATR de Wilder 14 de la dernière bougie 1 h disponible à cette minute. Sorties par tiers à 1, 2 et 3 R,
    60 heures au plus, stop d'abord dans une minute ambiguë. Frais du modèle commun (`forward/costs.py`), central et
    défavorable. Simulation `figures_history.play`, importée sans changement.
  - Minute tirée dans un trou de plus de 10 minutes, ou géométrie invalide (stop à moins de 0,1 % de l'entrée) :
    achat inutilisable, compté, **pas remplacé**.
  - **Résultat du jour** = R net moyen des achats utilisables. S'il y en a moins de 10, le jour sort de `S1` (compté).
- **`S2` — `PANIER_24H` (en %)** : chaque jour `d`, achat à parts égales des paires éligibles à l'ouverture de la
  bougie 1 h de `d` 01:00, vente à l'ouverture de `d + 1` 01:00. Frais du modèle commun aux deux côtés : 7,5 pb
  (défavorable 10 pb) plus écart et glissement de 2 pb pour BTC et ETH, 5 pb pour les autres (doublés en
  défavorable). **Résultat du jour** = rendement net moyen du panier. Une paire sans l'une des deux ouvertures sort du
  panier ce jour-là.
- Les deux séries sont fortement liées (même marché). `S1` ressemble à des signaux (stop, objectifs, R). `S2`
  ressemble à de l'argent « au marché ».
- **Biais de survivance**, déclaré : les 40 paires sont celles qui sont encore cotées en 2026. Les jours haussiers y
  sont plus beaux qu'en vrai. La confirmation sur les paires C (§ 8.1), qui comprend des paires retirées de la cote,
  en tient compte en partie.

### 5.3 Politiques

- **`SANS_ROUGE`** (celle qui décide) : on joue tous les jours sauf les jours rouges (poids 1 vert et orange,
  0 rouge).
- **`FEU_COMPLET`** (descriptif) : poids 1 vert, ½ orange, 0 rouge.
- **Statistique « résultat moyen »** = Σ `w_d · n_d · y_d` / Σ `w_d · n_d`, où `w_d` est le poids du jour, `y_d` son
  résultat et `n_d` le nombre d'achats utilisables (`S1`) ou 1 (`S2`). C'est le résultat moyen **par achat
  effectivement fait**.
- **Pire perte** (drawdown) : la plus grande baisse de la somme cumulée des `w_d · y_d` (`S1` en R) ou du capital
  composé (`S2`).
- **Part de jours exclus** : jours rouges / jours avec feu.

### 5.4 Placebos : sauter autant de jours, au hasard

Les placebos déplacent le **feu** et laissent les résultats en place. Ils gardent exactement **la même part** de jours
rouges, et presque la même longueur des séries de jours rouges (les jours rouges viennent par épisodes). C'est la
leçon de `LONG_HORIZON.md` et de `TREND_DAILY.md` : on compare à exposition égale.

- **`P_T` — apparié par trimestre (décisif)** : dans chaque trimestre civil, la suite des couleurs est **tournée en
  rond** d'un décalage tiré uniformément entre 1 et `n_q − 1` (`n_q` = jours avec feu du trimestre), indépendamment
  pour chaque trimestre. **Chaque trimestre garde son nombre de jours rouges.** Un feu qui ne fait que marquer en
  rouge des trimestres entiers ne gagne rien contre ce placebo. Il faut choisir les mauvais jours **à l'intérieur**
  d'une même période. 10 000 tirages, graine 20261008.
- **`P_G` — décalage circulaire global** : toute la suite des couleurs est tournée d'un décalage `u`, pour **tous**
  les décalages possibles de 91 jours à `N − 91` (liste exhaustive, environ 1 790). Les épisodes rouges tombent alors
  n'importe où dans les 5,4 ans. Ce placebo mesure **régime + timing**.
- Les jours `SANS_FEU` restent à leur place et hors mesure dans les deux placebos.
- **p-valeurs** : `p_haut` = (1 + nombre de placebos dont la statistique est ≥ celle du feu) / (1 + nombre de
  placebos). `p_bas` est la même chose avec ≤. Pour la pire perte, « mieux » veut dire **plus faible**.
- **Excès** = statistique du feu − moyenne des placebos. « **Bruit du hasard** » = percentiles 2,5 et 97,5 des
  placebos centrés : ce que le hasard seul donne. C'est ce qu'on montre au propriétaire à côté de l'excès.

### 5.5 Les tests (5 essais)

| Test | Série | Statistique | Placebo | Ce qu'il dit |
|---|---|---|---|---|
| `T1_S1` | `S1` | résultat moyen | `P_T` | le feu choisit-il les mauvais jours **dans** un trimestre (achats au hasard) |
| `T1_S2` | `S2` | résultat moyen | `P_T` | idem pour le panier 24 h |
| `T2_S1` | `S1` | résultat moyen | `P_G` | régime + timing (achats au hasard) |
| `T2_S2` | `S2` | résultat moyen | `P_G` | régime + timing (panier) |
| `T3_S1` | `S1` | pire perte (en R cumulés) | `P_G` | la pire perte baisse-t-elle plus que si l'on sautait autant de jours au hasard |

- Niveau de chaque test : **bilatéral à 0,05/5**, soit `p_haut` ou `p_bas` ≤ **0,005**, en frais centraux **et**
  défavorables.
- La pire perte du panier (`S2`) n'est **pas testée**. Sauter les jours de forte volatilité réduit mécaniquement les
  écarts en %, même sans aucune information sur le sens. C'est le ciblage de volatilité, déjà connu. Elle est
  décrite, avec la même pire perte recalculée sans `VOL_HAUTE` (descriptif). En R (`S1`), la volatilité est
  neutralisée par le stop à 2 ATR. Le contrôle nul (§ 7.2) vérifie qu'il ne reste pas de biais mécanique, par exemple
  des frais qui pèsent plus en R les jours calmes.

### 5.6 Verdicts

- **`T1`** :
  - `PISTE_TIMING` : `p_haut` ≤ 0,005 dans les deux scénarios et tous les garde-fous tiennent.
  - `PISTE_FRAGILE` : le seuil est atteint mais un garde-fou manque (inscrit, pas de confirmation).
  - `INVERSE` : `p_bas` ≤ 0,005 dans les deux scénarios. Le feu écarte les **bons** jours : les meilleurs jours
    suivent les pires.
  - `INSUFFISANT` (§ 5.7), `NON_CALIBRE` (§ 7.2), sinon `RIEN`.
- **`T2`** : `INDICE_REGIME`, `INDICE_FRAGILE`, `INVERSE`, `INSUFFISANT`, `NON_CALIBRE`, `RIEN`, avec les mêmes
  seuils. On l'appelle « indice » et non « piste » parce qu'il repose sur environ deux cycles (2020-2021 et
  2022-2024). Il ne se confirme que **dans le temps** (§ 8).
- **`T3_S1`** : `PERTE_REDUITE` (`p` côté « plus faible » ≤ 0,005 dans les deux scénarios, garde-fous G1, G5 et G6),
  `PERTE_AGGRAVEE` (l'autre côté), `INSUFFISANT`, `NON_CALIBRE`, `RIEN`. C'est aussi un indice de régime.
- **Gain, sans verdict** : le résultat moyen de `SANS_ROUGE` est donné avec son intervalle (tirage par blocs de
  91 jours, 10 000 tirages, 95 %), pour `S1` et `S2`, central et défavorable. Même une `PISTE_TIMING` ne rendrait pas
  les achats rentables. Ce chiffre le montre.

### 5.7 Garde-fous (exigés pour `PISTE_TIMING` et `INDICE_REGIME`)

1. **G1 — minimums** : au moins **60 jours rouges**, au moins **8 épisodes rouges** (suites de jours rouges
   consécutifs) et au moins 1 500 jours dans la série. Pour `T1`, au moins **6 trimestres utiles** (au moins un jour
   rouge et un jour non rouge). Sinon `INSUFFISANT`. Aucune définition n'est changée pour atteindre ces nombres.
2. **G2 — pas un seul moment** : pour `T1`, l'excès reste > 0 sans **le trimestre** qui en apporte le plus. Pour
   `T2`, il reste > 0 sans **l'année** qui en apporte le plus (souvent 2022 : c'est le piège du § 6).
3. **G3 — régularité** (`T1`) : excès > 0 dans au moins **4 des 6 années** (parmi celles qui ont un trimestre utile ;
   s'il y en a moins de 6, dans plus de la moitié).
4. **G4 — 2022** (`T1`) : l'excès calculé sur les seuls trimestres de 2022 est > 0. Dans l'année baissière, le feu
   doit choisir les pires jours, pas seulement être rouge.
5. **G5 — frais défavorables** : la décision tient dans les deux scénarios (déjà dans le seuil).
6. **G6 — calibration** : le test a passé le contrôle nul (§ 7.2), sinon `NON_CALIBRE`.

### 5.8 (b) Règles des études précédentes, comme référence (2 essais)

Deux règles déjà mesurées sur DEVELOPMENT, rejouées **sans changement**. On leur applique le test `T1` (placebo
`P_T`, niveau bilatéral 0,05/2) : leurs transactions décidées un jour rouge sont-elles pires que ce que donnerait un
feu tourné au hasard dans le trimestre ?
- **`REF_TRENDLINE_1H`** : les cassures de ligne de tendance 1 h des 40 paires de recherche, avec leur transaction
  propre (`FIGURES_HISTORIQUE.md`, `LIGNES_DE_TENDANCE.md`), recalculées par le code importé sans changement. R net
  par transaction.
- **`REF_K2_1J`** : les cassures de résistance journalières K2 sur les survivantes (`SCREENING.md`, criblage K),
  rendement à 7 jours moins le seuil de coûts aller-retour de 0,21 %.
- Le jour d'une transaction = le jour du feu dont la fenêtre contient sa décision. G1 adapté : au moins 300
  transactions dont 30 un jour rouge, sinon `INSUFFISANT`. G5 et G6 (contrôle nul fait sur `S1`).
- **Déclaré** : on sait déjà que K2 a gagné les années de hausse (2019, 2021, 2023, 2024) et perdu en 2020, 2022 et
  2025. Un filtre de régime l'aiderait donc forcément sur `P_G`. C'est pour ça que seul `T1` est calculé : cette
  référence illustre le piège du § 6. Ces deux règles ont été choisies après avoir vu DEVELOPMENT : leur résultat est
  un **indice** seulement.

### 5.9 Descriptif (calculé avec la mesure, jamais après coup, aucune décision)

- Pour chaque couleur et chaque année : part de jours, résultat moyen de `S1` et `S2`, pire perte.
- `FEU_COMPLET` contre ses placebos, et le résultat des jours orange.
- **Chaque composante seule**, comme politique « sauter les jours où elle est en danger », contre `P_T` et `P_G` :
  excès et bruit du hasard. **Non décisif.** Aucune composante ne peut être retirée ni gardée seule d'après ce tableau
  sans un nouveau protocole.
- Coût dans les phases de hausse : gains manqués les jours rouges de 2020-T4 à 2021-T1 et de 2023-T4 à 2024-T1
  (phases nommées après coup, pour décrire seulement).
- Par jour de semaine. Accord entre composantes (tableau des activations simultanées). Pour `S1` : issues (stop, TP,
  échéance) par couleur.
- Les épisodes rouges : dates, durée, résultat.

## 6. Le piège principal : un filtre « anti-baisse » gagne toujours après coup

- **Le mécanisme.** De 2020 à 2025, le marché a fait deux grandes hausses (2020-2021, 2023-2024) et une grande baisse
  (2022). Toute règle qui suit la tendance (BTC sous sa moyenne, largeur faible, pertes récentes) est rouge surtout
  en 2022 et verte surtout en 2021. Comparée au fait de « jouer tous les jours », elle a l'air brillante. Mais c'est
  **une seule** baisse, vue après coup, et n'importe quelle moyenne mobile l'aurait « vue ». Ce n'est pas une
  preuve : sur une période où les baisses durent moins longtemps que la moyenne ne met à réagir, la même règle
  sortirait trop tard et rentrerait trop tard.
- **Ce que fait ce protocole** :
  1. **Exposition égale** : tous les placebos sautent la même part de jours. Une règle qui gagne seulement parce
     qu'elle est moins au marché ne gagne rien contre eux.
  2. **Même régime** : le test décisif (`T1`, placebo `P_T`) garde le nombre de jours rouges **de chaque trimestre**.
     Marquer 2022 en rouge n'y rapporte rien : il faut choisir les pires jours à l'intérieur de 2022, et les pires
     jours à l'intérieur de 2021.
  3. **2022 seule** (G4) : dans l'année baissière, le feu doit encore faire mieux que le hasard.
  4. **Sans la meilleure année** (G2 de `T2`) : un indice de régime qui disparaît sans 2022 n'est pas un indice.
  5. **Contrôle nul `N3`** (§ 7.2) : sur des marchés synthétiques où la tendance change **d'un trimestre à l'autre**
     mais pas à l'intérieur, `T1` doit rester à 0. Ça vérifie que le placebo par trimestre retire bien l'effet de
     régime.
  6. **Le régime ne se confirme que dans le temps** (§ 8.2, test en direct). Rejouer sur d'autres paires ne le
     confirme pas : les jours sont les mêmes pour toutes.
- **Le piège inverse**, déclaré aussi : les jours de panique (peur extrême, volatilité record) précèdent souvent les
  plus forts rebonds. Un feu qui saute ces jours peut rater les meilleurs jours. Le test bilatéral le montrera
  (`INVERSE`).

## 7. Contrôles avant toute exécution sur données réelles

### 7.1 Causalité et mutations

- Le feu est recalculé à des jours tirés au hasard, juste après 00:10, sur des données **tronquées** à `T_d` et avec
  le **futur falsifié** (prix, volumes, Fear & Greed, financement, prévisions postérieurs à `T_d`). Couleurs et
  composantes doivent être identiques.
- **Mutations qui doivent être détectées** :
  - EMA de BTC ou des paires jointe sur `open_time` (journée `d` en formation incluse) ;
  - clôture de `d` dans `LARGEUR` ;
  - Fear & Greed horodaté `d` au lieu de `d − 1` ;
  - financement daté à son heure de règlement sans la minute ;
  - prévision de l'origine `d + 1` ;
  - rang qui inclut la valeur du jour ;
  - `PERTES_RECENTES` qui inclut les achats des jours `d − 3` à `d − 1` (sorties non connues) ;
  - placebo `P_T` qui change le nombre de jours rouges d'un trimestre ;
  - lecture d'une donnée postérieure au 2025-06-30.

### 7.2 Contrôle sous l'hypothèse nulle (données synthétiques seulement, même code de bout en bout)

- **Au moins 100 simulations par cas**, 5,4 ans chacune. Il y a 40 paires, ou 20 si le temps de calcul l'impose : ce
  choix est inscrit avec les résultats du contrôle, avant toute exécution réelle. Bougies 1 minute. Chaque paire =
  β × BTC + bruit propre (β tiré dans [0,5 ; 1,5]), comme le cas « facteur commun » de `COMBINAISONS.md`.
- **Composantes synthétiques** : Fear & Greed = fonction du rendement de BTC sur 30 jours plus un bruit (le
  sentiment suit le prix, comme en vrai). Financement = fonction du rendement sur 7 jours plus un bruit. Prévision de
  volatilité = le modèle HAR + profil ajusté par le même code sur la marche. Les autres composantes sont calculées
  telles quelles.
- **Cas** :
  - **`N1`** : rendements sans mémoire, volatilité constante ;
  - **`N2`** : sans mémoire, régimes de volatilité (×0,5 / 1 / 2 sur des périodes de 20 jours) ;
  - **`N3`** : dérive **constante à l'intérieur de chaque trimestre civil** (±0,3 % par jour, tirée par trimestre),
    volatilité en régimes. Ici, un feu qui suit la tendance gagne « légitimement » contre `P_G`. Mais à l'intérieur
    d'un trimestre il n'y a rien à trouver.
- **Critère, par test** : la part des simulations où `p_haut` ou `p_bas` ≤ 0,05 doit être **≤ 0,10** (on attend
  0,05). L'excès moyen doit vérifier |biais| ≤ **0,01 R** (`S1`) ou ≤ **0,02 % par jour** (`S2`). Tous les tests sont
  jugés dans `N1` et `N2`. Dans `N3`, seuls `T1_S1` et `T1_S2` sont jugés. `T2` et `T3` y sont décrits : c'est l'effet
  de régime que le simulateur contient, et ça vérifie qu'il en contient un.
- **En cas d'échec** (règle écrite d'avance) : le test concerné sort `NON_CALIBRE`, sans verdict. Il est quand même
  compté. Aucun seuil n'est déplacé pour le « sauver ».
- **Contrôle positif** (informatif, non bloquant) : dans `N3`, on ajoute une dérive de −0,5 % par jour les jours où le
  feu synthétique est rouge, au-delà du régime. La part des simulations où chaque test sort `PISTE` ou `INDICE` donne
  la **puissance réelle** de l'instrument, qui remplace l'estimation du § 13.
- Résultats inscrits ici (fichier et chiffres) **avant** l'exécution réelle.

### 7.3 Relecture

Relecture `leak-auditor` du code, inscrite par un commit relu, comme pour `COMBINAISONS.md`. Chaque exécution réelle
vérifie que les modules de l'étude et les modules importés n'ont pas changé depuis ce commit, et que le code est
commité.

### 7.4 Comptages d'avant exécution (aucun résultat, aucun R, aucun rendement)

Ils sont inscrits ici avant l'exécution :
- couleurs par année et par trimestre ;
- nombre et longueur des épisodes rouges ;
- jours `SANS_FEU` ;
- part du temps où chaque composante est en danger, par année ;
- activations simultanées des composantes ;
- trimestres utiles ;
- pour `S1`, achats utilisables par jour (les tirages et les géométries, sans les simuler) ;
- taille du panier `S2` ;
- pour (b), nombre de transactions par couleur.

**Aucune règle ne change d'après ces comptages**, même si le rouge fait 2 % ou 60 % des jours. Sous les minimums de
G1, le verdict est `INSUFFISANT`.

## 8. Confirmation

### 8.1 Sur les paires jamais utilisées (nécessaire, pas suffisante)

- Si un test `T1_S1` ou `T2_S1` sort `PISTE_TIMING` ou `INDICE_REGIME` (ou le modèle du § 9), il est rejoué **sans
  changement** sur les **paires C** : les paires passées par le top 40 à date, hors des 40, paires retirées de la cote
  comprises (`trendline_confirmation.universe`, environ 214 avec des bougies). Le feu est le même : il est calculé une
  fois pour le marché. Seuls les achats au hasard de `S1` sont tirés parmi les paires C éligibles à la date. Exécution
  unique, `m` tests, niveau bilatéral 0,05/m.
- Verdicts : `CONFIRMEE_PAIRES`, `NON_CONFIRMEE`, `INVERSE`, `INSUFFISANT`.
- **Pourquoi ce n'est pas suffisant** : les jours sont les mêmes pour toutes les paires et le facteur commun domine.
  Cette étape vérifie que l'effet ne vient pas des seules 40 survivantes (biais de survivance), rien de plus.

### 8.2 Dans le temps (la seule vraie confirmation)

- **Test en direct F17** (§ 11) : c'est la voie choisie par défaut. Il est indépendant du résultat de l'étape 1 et
  peut démarrer dès sa pré-inscription relue, puisque la règle est figée ici.
- **Période réservée** (2025-07-01 → aujourd'hui) : **non lue par défaut, et déconseillée pour cette question.**
  - Ta demande vient de ce que tu vis en ce moment (« ces jours-ci… »), c'est-à-dire de la période réservée
    elle-même. Elle n'est donc pas un juge neutre de la météo.
  - Environ 450 jours, avec peut-être 60 à 100 jours rouges : la puissance y est encore plus faible qu'en § 13.
  - Elle a déjà été consultée 3 fois, et la voie A de `COMBINAISONS.md` doit encore la consulter.

  Si tu la veux quand même : une consultation déclarée, avec son propre pré-enregistrement, `T1_S1` et `T2_S1`
  seulement (2 essais sur FINAL_TEST), une répétition sur DEVELOPMENT et une relecture.

## 9. Étape 2 : modèle appris (seulement si tu le demandes après l'étape 1)

- **Lignes** : un jour `d` de la période. **Cible** : `y = 1` si le résultat du jour de `S1` (frais centraux) est < 0.
- **Variables** (8, toutes connues à `T_d`) :
  - les valeurs **continues** des 6 composantes : écart de BTC à son EMA50, part de `LARGEUR`, rang de `VOL_HAUTE`,
    valeur du Fear & Greed, financement moyen, rang de `PERTES_RECENTES` ;
  - la croissance de l'encours des stablecoins sur 7 jours (DefiLlama, jour `T − 2`, absente avant 2021-07 : variable
    « absente » 0/1 ajoutée) ;
  - le rang du volume agrégé des 40 paires sur 7 jours parmi 365 jours.
  - Centrées-réduites avec la moyenne et l'écart-type de l'entraînement seulement.
- **Modèle** : régression logistique L2, `C = 1` (même implémentation que `COMBINAISONS.md`, `ml/logistic.py`), sans
  recherche d'hyperparamètres.
- **Walk-forward annuel**, fenêtre croissante depuis 2020-02. Réajustement le 1er janvier de `Y` sur les jours dont
  tous les achats sont sortis avant (jour ≤ 28 décembre de `Y − 1`). Plis de test : **2022, 2023, 2024, 2025-S1**. Il
  faut au moins 500 lignes et les deux classes, sinon le pli compte comme un échec.
- **Rouge du modèle** : `p̂` au-dessus du quantile `1 − f` des `p̂` de son entraînement, où `f` est la part de jours
  rouges de la règle du § 4 dans ce même entraînement. Il saute donc à peu près autant de jours que la règle, et la
  comparaison reste à exposition comparable.
- **Tests** : `T1_S1` et `T2_S1` sur 2022 → 2025-06, **2 essais**, niveau bilatéral 0,05/2, mêmes garde-fous (G3 :
  3 plis sur 4). En descriptif : la règle du § 4 sur les mêmes années, et les coefficients par pli (signe et
  stabilité).
- Un modèle qui « bat » la règle sur l'historique n'est pas meilleur pour autant : il a plus de façons de s'ajuster
  au hasard. Seul le temps tranchera.

## 10. (c) Tes signaux Telegram de 2026 : décrit seulement, décision séparée

Rien n'est fait. Voici ce que serait la mesure, si tu la décides :
- **Signaux** : ceux de `COMBINAISONS.md` § 5.2 (exports de 2026, coupure du 2026-10-07, mêmes doublons, mêmes refus
  sur `available_at`), ta gestion « stop suiveur ». R par signal, un signal jamais rempli vaut 0 R.
- **Feu** : celui du jour dont la fenêtre contient la réception.
- **Test** : le R moyen des signaux reçus un jour non rouge. Placebo : le feu tourné en rond **à l'intérieur de chaque
  mois civil** (10 000 tirages), pour garder la part de jours rouges de chaque mois. **1 essai sur FINAL_TEST**,
  bilatéral à 0,05. Minimums : 40 signaux un jour rouge, 100 un autre jour, sinon `INSUFFISANT`.
- **Consultation déclarée** de la période réservée, la n° 5 ou plus (feu de 2026 : bougies, prévisions, Fear & Greed
  et financement de 2025-2026).
- **Puissance** : environ 400 signaux, dont peut-être 80 un jour rouge. Seul un écart d'environ **0,35 R par signal**
  ou plus serait visible.
- **Pas indépendant de la voie A de `COMBINAISONS.md`** : mêmes signaux, et `BTC_HAUSSIER` y est l'inverse exact de
  `BTC_STRUCTURE`. La mesure faite en second n'est plus neutre. C'est à toi de choisir l'ordre, ou d'en garder une
  seule.

## 11. (d) Test en direct `F17_METEO` : décrit ici, pré-inscrit à part

La pré-inscription formelle (nouvelle section de `FORWARD_TESTS.md`, sans toucher au « Cadre commun » ni aux autres
sections) et le module (`forward/f17.py`, nouveau) sont une étape à part, relue avant le démarrage. **F4, F5, F12 et
F16 restent intacts** : on lit leurs journaux en lecture seule.

- **Feu** : chaque jour après 00:10 UTC, mêmes 6 composantes, mêmes seuils, même règle. Les données viennent du
  magasin de la surveillance :
  - bougies 1 h de BTC et des 40 paires de recherche admises par la liste halal figée au démarrage ;
  - prévision H24 de BTC du journal F12 ; son historique de rang sur 365 jours est recalculé au démarrage avec les
    mêmes fonctions gelées, comme F5 l'a fait pour sa prévision à 7 jours ;
  - Fear & Greed du relevé F0_DONNEES (valeur horodatée `d − 1`) ;
  - financement BTC du relevé F0_DERIVES ;
  - pertes récentes : les achats `S1` du module lui-même. La base de rang des 365 premiers jours est recalculée au
    démarrage sur les bougies 1 minute publiques. Ces valeurs servent de référence seulement : elles sont inscrites
    au journal et jamais résumées.
- **Résultats mesurés** :
  - `S1` (20 achats au hasard par jour sur les 40 paires admises) ;
  - `S2` (panier 24 h) ;
  - **`S3`** : les signaux texte du relais Telegram reçus après le démarrage, lus sans écriture, avec ta gestion, les
    mêmes doublons et les mêmes refus que F4.
- **Relevé à côté du feu, en descriptif** : feu de F5 du jour (lecture seule de son journal), news de risque,
  profondeur moyenne des carnets, liquidations OKX, émissions de stablecoins (F3), intérêt ouvert. Rien de tout cela
  ne change la couleur.
- **Décisions à la date du verdict (5)** : `T1` et `T2` pour `S1` et `S2` (pour `T2`, décalages d'au moins 28 jours),
  et `T1` par mois pour `S3`. Niveau bilatéral 0,05/5. L'essai est compté au registre FORWARD.
- **Calendrier** : revue descriptive à 12 semaines, sans aucun changement. Le verdict est rendu quand **60 jours
  rouges en 6 épisodes au moins** sont résolus, au plus tôt le 2027-04-08 et au plus tard le 2027-10-08. Si ces
  minimums ne sont pas atteints : `INSUFFISANT`. Sur un an, environ 70 jours rouges : seul un écart d'environ
  **0,35 R** entre jours rouges et autres jours serait visible sur `S1`. Le plus probable, honnêtement :
  `INSUFFISANT` ou `RIEN`.
- **Aucune influence** sur les signaux de CSI, sur BinanceSpotManager ou sur un autre test. L'afficher au tableau de
  bord comme « météo non démontrée, test en cours » serait une décision séparée du propriétaire (comme
  `RISK_SHADOW.md`).

## 12. Essais et comparaisons multiples

| Étape | Données | Décisions | Essais | Niveau de chaque test |
|---|---|---|---|---|
| 1 — météo, achats génériques | 40 paires, DEVELOPMENT, 2020-02 → 2025-06 | `T1_S1`, `T1_S2`, `T2_S1`, `T2_S2`, `T3_S1` | **5** | bilatéral 0,05/5 |
| (b) références | `REF_TRENDLINE_1H`, `REF_K2_1J` | `T1` | **2** | bilatéral 0,05/2 |
| 2 — modèle appris (sur décision) | `S1`, 2022 → 2025-06 | `T1_S1`, `T2_S1` | 2 | bilatéral 0,05/2 |
| Confirmation sur les paires C (si piste ou indice) | paires C, DEVELOPMENT | les tests retenus de `S1` (règle et modèle) | ≤ 4 | bilatéral 0,05/m |
| (c) Telegram 2026 (sur décision) | période réservée | `T1` par mois | 1 sur FINAL_TEST | bilatéral 0,05 |
| Période réservée (déconseillée, sur décision) | période réservée | `T1_S1`, `T2_S1` | 2 sur FINAL_TEST | bilatéral 0,05/2 |
| (d) F17 en direct | FORWARD | 5 | registre FORWARD | bilatéral 0,05/5 |

- **DEVELOPMENT** : **7** essais sûrs (873 → 880), **13 au plus** (→ 886). **FINAL_TEST** : 0 par défaut (reste à 8).
- La correction de Bonferroni s'applique à l'intérieur de chaque ligne. Les garde-fous rendent chaque décision plus
  sévère sans compter d'essai de plus.
- Avec environ 880 essais au programme, un seuil de 0,05/880 serait hors d'atteinte. La protection ne vient pas d'un
  seuil : elle vient d'une règle **figée avant de voir**, du placebo **dans le même trimestre**, et de la
  confirmation **dans le temps**. Un `PISTE_TIMING` sur l'historique seul n'est qu'un indice.

## 13. Puissance réaliste (hypothèses non mesurées ; le contrôle positif du § 7.2 les remplacera)

- **Hypothèses** : environ 1 975 jours, dont environ 20 % de rouges (environ 400 jours en 15 à 30 épisodes : le vrai
  chiffre viendra des comptages). Écart-type du panier `S2` d'environ 4 % par jour (plus les jours rouges, plus
  agités). Pour `S1`, R d'un achat d'écart-type environ 1 R et corrélation d'environ 0,3 entre achats d'un même jour,
  soit environ 0,6 R pour la moyenne d'un jour.
- **Panier `S2`** : erreur type de l'écart « jours rouges − autres jours » d'environ 0,3 % par jour, avec la
  dépendance. Au niveau 0,005 bilatéral, avec 80 % de chances de le voir, il faut un écart d'environ **1 % par jour**.
  En 2022, les altcoins ont perdu de l'ordre de 0,4 % par jour en moyenne. **Même un feu rouge exactement sur 2022 et
  vert ailleurs serait sous ce seuil.**
- **Achats `S1`** : erreur type d'environ 0,04 R, donc un écart visible d'environ **0,15 R par achat** entre jours
  rouges et autres jours. Le contrôle positif de `COMBINAISONS.md` montre qu'une dérive de prix de 0,15 R sur 60 h
  n'est vue qu'à environ 0,04 R par une transaction à stop et objectifs. Il faudrait donc une dérive des jours rouges
  d'environ −0,5 % par jour, la vitesse d'un vrai marché baissier.
- **`T1`** (dans le trimestre) n'a que les variations à l'intérieur des trimestres pour travailler : il est encore
  moins puissant.
- **Conclusion** : 5,4 ans ne peuvent prouver qu'une météo très tranchée. Un `RIEN` voudra dire « pas d'effet de cette
  taille ». Le test en direct, sur un an, n'est pas plus puissant (§ 11). Une preuve solide demandera plusieurs années
  de feu tenu sans retouche. C'est la raison de figer la règle dès maintenant.

## 14. Ce que voudront dire les résultats

- **`RIEN` partout** : sur 2020-2025, sauter les jours rouges ne fait pas mieux que sauter autant de jours au hasard.
  - Le feu n'est pas démontré. Aucun usage comme conseil.
  - L'outil de risque démontré reste la taille selon l'ampleur prévue (`RISK_SHADOW.md`), qui ne prétend pas savoir
    le sens.
  - Aucune composante ajoutée « pour voir » : ce serait un nouveau programme, à déclarer et à compter.
- **`INDICE_REGIME` seul** : le feu aurait évité plus de mauvais jours que le hasard **sur ces deux cycles**, mais il
  ne choisit pas les jours à l'intérieur d'un trimestre. C'est un filtre de tendance de plus. Il est plausible, mais
  la preuve ne peut venir que du direct, sur des mois à des années.
- **`PISTE_TIMING`** : à l'intérieur d'une même période, les jours rouges sont pires. C'est la seule lecture forte.
  La confirmation sur les paires C est nécessaire, puis le direct (F17) décide. Même alors, le gain moyen (§ 5.6) dira
  si les achats restent perdants après frais : la météo ne rend rien rentable.
- **`INVERSE`** : les jours rouges sont **meilleurs** que le hasard. Les rebonds suivent les paniques. Ne pas
  s'abstenir sur la foi de ce feu. Ce ne serait pas pour autant un signal d'achat (il faudrait un autre protocole).
- **`PERTE_REDUITE`** sans piste ni indice sur le résultat moyen : la pire perte baisse, mais le résultat moyen ne
  change pas. C'est un lissage. À lire avec la leçon de `TREND_DAILY.md` : la volatilité fait souvent ce travail seule.
- **Règles de bon sens hors mesure** (parité d'un stablecoin cassée, retrait de la cote annoncé) : si elles sont un
  jour affichées, elles le sont comme telles, jamais comme un résultat de ce protocole.

## 15. Ordre d'exécution et mise en œuvre prévue

1. Relecture de ce document (propriétaire, `leak-auditor`). Accord pour l'historique du Fear & Greed.
2. Code et tests (synthétiques seulement) :
   - nouveaux modules `research/meteo.py` (composantes, feu) et `research/meteo_study.py` (résultats, placebos,
     tests, verdicts, exécutions) ;
   - `research/meteo_controls.py` (§ 7.2) ;
   - `tests/test_meteo.py` (causalité, mutations du § 7.1, exemples vérifiables à la main de chaque composante et des
     deux placebos).

   Commandes : `csi meteo fng-historique | comptages | controles | feu | references | modele | confirmation`.
   Chaque exécution réelle exige `--executer`, un code commité et relu (`CODE_REVIEW`), les contrôles inscrits, et
   n'a lieu qu'une fois par type.
3. Relecture `leak-auditor` du code → contrôles synthétiques (§ 7.2) et comptages (§ 7.4) inscrits ici.
4. **Étape 1** (`METEO_FEU`, 5 essais) et **références** (`METEO_REFERENCES`, 2 essais) : exécutions uniques.
5. Confirmation sur les paires C (`METEO_CONFIRMATION`) si une piste ou un indice sur `S1` : exécution unique.
6. Étape 2 (`METEO_MODELE`, 2 essais) : seulement sur décision du propriétaire, après l'étape 1. Si elle sort une
   piste ou un indice, sa confirmation C suit en une exécution.
7. **F17_METEO** : pré-inscription et module à part, relus. Il peut démarrer dès sa relecture, en parallèle de tout
   ce qui précède.
8. (c) Telegram 2026 et lecture de la période réservée : seulement sur décision du propriétaire, chacune avec sa
   propre déclaration.

- **Importés sans changement** :
  - `research/figures_history.py` (`play`), `research/factors.py` (éligibilité) ;
  - `research/trendline_confirmation.py` (`universe`, `coverage`), `research/pivot_screen.py` (événements K2) ;
  - `research/long_history.py`, `research/minute_history.py` ;
  - les fonctions de `research/volatility_hourly.py` (contrôles synthétiques) et le fichier de prévisions v3 ;
  - les lecteurs de `derivatives/` et de `context/` ;
  - `forward/costs.py`, `backtest/metrics.py`, `ml/logistic.py`, `research/protocol.py`, `research/experiments.py`.
- **Aucun module gelé n'est modifié.**
- **Registre** : types `METEO_FEU`, `METEO_REFERENCES`, `METEO_MODELE`, `METEO_CONFIRMATION`, avec les `n_trials` du
  § 12, les empreintes des bougies, du fichier de prévisions, du Fear & Greed et du financement, et un commit propre.

## 16. Points à faire relire en priorité (`leak-auditor`)

1. Heure de connaissance du Fear & Greed (horodatage `d − 1` à `d` 00:10) et révisions possibles de l'historique
   d'alternative.me.
2. Les prévisions H24 lues dans le fichier du protocole v3 : purge des réajustements trimestriels, origine 00:00
   contre une fenêtre du feu de 00:10 à 00:10, échantillon commun.
3. `PERTES_RECENTES` construit avec le même générateur que la mesure `S1` : fenêtre `d − 10` à `d − 4`, sorties
   toutes connues, aucun chevauchement avec les achats mesurés du jour `d`. Est-ce que l'autocorrélation des
   résultats crée un biais dans les placebos ?
4. Validité des placebos par rotation (`P_T` et `P_G`) quand la série n'est pas stationnaire. Raccord en bout de
   trimestre. Décalage minimal de 91 jours.
5. Biais mécanique en R : les frais pèsent plus en R les jours calmes, donc sauter les jours agités change le R moyen
   sans information. Le cas `N2` doit le mesurer.
6. `LARGEUR` calculée sur 40 survivantes plutôt que sur le top 40 à date.
7. Le trimestre comme définition du « même régime ». Le cas `N3` suffit-il à le valider ?
8. Démarrage de F17 : bases de rang recalculées sur des bougies de la période réservée (servent d'entrée, jamais de
   mesure).
9. (c) Signaux Telegram : chevauchement avec la voie A de `COMBINAISONS.md`, et neutralité de la période réservée
   pour une demande née de cette période même.
10. Hypothèses de puissance du § 13.

## Historique

- 2026-10-08 : déclaré avant tout code, tout téléchargement et tout calcul sur des prix (demande du propriétaire du
  même jour). Seules lectures sur des données réelles : le registre des essais, en lecture seule (873 essais sur
  DEVELOPMENT, 8 sur FINAL_TEST, 3 consultations), la liste des fichiers des magasins (sans lire de valeur) et le code
  des sources (`forward/sources.py`, `context/fetch.py`) pour vérifier que l'adresse du Fear & Greed est déjà dans la
  liste blanche ; dans le fichier de prévisions v3, les seules colonnes `symbol` et `origin` (BTCUSDT présent, origines
  du 2019-01-01 au 2025-06-30, aucune valeur de prévision lue). Résultats de `CMB1` / `CMB2` non lus.
