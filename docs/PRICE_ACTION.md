# Étude « price action » : cinq configurations, mesurées sur l'historique et en direct (F19)

> **2026-10-10 : le contrôle sous H0 n° 1 (§ 5.5) a fait échouer les cinq configurations sur l'excès (biais des placebos
> tirés avant le signal). Amendement du même jour (§ 11), décidé sur du synthétique seulement, aucune donnée réelle lue :
> la décision repose sur le R net seul, les placebos deviennent descriptifs, et un contrôle sous H0 n° 2 revalide le
> nouveau critère avant toute exécution. Les sections 4.3 à 4.6 et 5.2 à 5.3 sont remplacées par le § 11 là où il le dit.**
> **Contrôle n° 2 (§ 11.6) : aucun faux `PISTE`, mais puissance < 0,50 pour les cinq configurations →
> `INSTRUMENT_TROP_FAIBLE` partout, 0 essai historique, exécution refusée ; F19 les mesure quand même.**

Demande du propriétaire du 2026-10-10. Protocole **déclaré le 2026-10-10, avant tout calcul sur des données réelles** :
aucun R, aucun excès sur des données de marché n'a été regardé pour écrire ce texte. Seuls ont été regardés des
**comptes de candidats** (synthétiques au § 5.4, sur le magasin local au § 9), jamais un résultat de transaction.

> **Shadow : aucun ordre.** CSI ne passe, ne modifie ni n'annule aucun ordre. L'étude n'écrit ni dans
> `SignalRegistry` ni dans `signals/`. **Aucun gain n'est annoncé ni démontré** : au vu de tout le programme (plus de 840
> essais sur DEVELOPMENT sans avantage directionnel démontré, cassures de pivots K2 qui ne tiennent pas hors biais de
> survivance, figures et méthodes d'analystes rejouées sans avantage), le résultat attendu est `RIEN`, `PERTE` ou
> `INSUFFISANT` pour chaque configuration. Rien ici n'est une probabilité ni une promesse.

Code : `price_action/detect.py` (détecteur pur, partagé), `price_action/manage.py` (gestion et placebos, partagés),
`research/price_action_study.py` (étude historique, exécution unique gardée), `research/price_action_h0.py` (contrôle
sous H0), `research/price_action_review.py` (inscriptions), `price_action/evaluate.py`, `state.py`, `outbox.py` et
`forward/f19.py` (test en direct). Tests : `tests/test_price_action.py`, `tests/test_price_action_h0.py` (lent),
`tests/test_forward_f19.py`.

## 0. En bref

- **Cinq configurations long seulement** : base puis cassure puis retest (4 h), sortie de compression (4 h), force
  relative après une chute de BTC (4 h), cassure d'une journée intérieure (1 jour), sortie d'une base longue (1 jour).
- **Un seul code de détection** pour l'historique et le direct : des fonctions pures sur des bougies closes.
- **Deux mesures** : l'historique (DEVELOPMENT 2019-01 → 2025-06-30, top 40 à date, paires retirées comprises,
  exécution unique après relecture) et le direct (F19_PRICE_ACTION, 84 jours).
- **Même gestion et mêmes placebos** dans les deux : stop à la clôture de l'unité, stop de secours à −1,5 R, moitié à
  +1 R, reste à l'objectif ; 20 placebos de même géométrie à des moments voisins (±84 h ou ±15 jours).
- **Avant toute donnée réelle** : un contrôle sous l'hypothèse nulle (marché synthétique sans information) ; une
  configuration qui y échoue est retirée de l'étude, avec 0 essai.

## 1. Hypothèses

Pour chaque configuration k (cinq tests, corrigés ensemble) : un signal de k, géré avec les règles communes (§ 3),
rapporte en moyenne un **R net > 0** après frais, et **plus** que 20 entrées placebo de même géométrie sur la même paire
à des moments voisins (§ 4.3). L'hypothèse nulle de chaque test : le signal ne vaut pas mieux qu'une entrée de même
géométrie prise au hasard autour de lui.

Ce qui est déjà réfuté et n'est pas repris tel quel : les cassures de pivots seules (K2 : `UNIVERSE_PIT.md`), les
niveaux seuls, les figures classiques (`FIGURES_HISTORIQUE.md`). Ce qui est nouveau : la base serrée confirmée par le
volume puis un retest tenu (`BASE_RETEST`), la compression de volatilité (`SQUEEZE`), la force relative pendant une
chute du marché (`FORCE_RELATIVE`), la journée intérieure en tendance (`INSIDE_DAY`), la base longue (`SORTIE_BASE_LONGUE`).
`BASE_RETEST` et `SORTIE_BASE_LONGUE` restent des **cassures** : la réfutation de K2 à date rend un résultat positif peu
probable.

## 2. Les cinq configurations (règles exactes, long seulement)

Bougies 1 h closes ; 4 h et 1 jour agrégés depuis 00:00 UTC, **blocs complets seulement** (une heure manquante retire le
bloc). ATR = ATR14 de Wilder (`patterns/primitives.atr`) ; EMA partant de la moyenne simple (`patterns/indicators.ema`).
« Volume » = volume quote ; « moyenne des 20 précédentes » exclut la bougie courante. **Tendance haussière
journalière** : sur la dernière journée complète, clôture > EMA50 et EMA20 > EMA50, avec au moins 60 journées
complètes. La décision est prise à une **clôture** ; l'entrée se fait à ce prix de clôture (frais et glissement taker).
Les fenêtres « N bougies » comptent les bougies présentes (pas le calendrier).

### 2.1 `BASE_RETEST` (unité 4 h)

1. **Base** : à la bougie 4 h `i`, les 10 bougies précédentes tiennent dans une hauteur (plus haut − plus bas) ≤ 1,5 ×
   l'ATR14 journalier de la dernière journée complète à la clôture de `i` ; la base est ensuite **étendue en arrière**
   bougie par bougie tant que la hauteur y tient, jusqu'à 180 bougies (30 jours, garde technique).
2. **Cassure** : clôture de `i` > haut de la base (étendue) **et** volume de `i` > 1,5 × la moyenne des 20 précédentes.
3. **Retest** : dans les 10 bougies 4 h qui suivent `i`, une bougie dont le plus bas ≤ haut de base + 0,25 × ATR 4 h
   (ATR de la bougie de cassure), **sans aucune clôture sous le haut de base** depuis la cassure (une clôture dessous
   annule la configuration).
4. **Entrée** : la première bougie, à partir de celle du retest et toujours dans ces 10 bougies, qui « repart » :
   clôture > ouverture **et** clôture > clôture précédente. Entrée à sa clôture.
5. **Stop de clôture** = haut de base − 0,25 × ATR 4 h. **Objectif** = haut de base + hauteur de la base ; **refus** si
   l'objectif est à moins de +1,5 R de l'entrée (`OBJECTIF_INSUFFISANT`).

### 2.2 `SQUEEZE` (unité 4 h)

1. Tendance haussière journalière (lue à la clôture de la bougie de sortie).
2. **Compression** : Bollinger (moyenne simple 20, ± 2 écarts-types de population des 20 clôtures) entièrement à
   l'intérieur de Keltner (EMA20 ± 1,5 × ATR20 de Wilder) pendant au moins 6 bougies 4 h consécutives.
3. **Sortie** : la **première** clôture 4 h au-dessus de la Bollinger haute depuis que la compression a atteint 6
   bougies (dans la compression ou sur la bougie qui la suit immédiatement), avec un volume > 1,5 × la moyenne des 20
   précédentes. Une première sortie sans ce volume ne donne rien pour cette compression.
4. **Entrée** = cette clôture ; **stop de clôture** = plus bas des 6 bougies qui la précèdent ; **objectif** = +2 R.

### 2.3 `FORCE_RELATIVE` (unité 4 h, marché entier)

1. **Événement BTC** : première clôture 1 h `E` où BTCUSDT a perdu ≥ 5 % sur 24 h glissantes
   (clôture(E) / clôture(E − 24 h) − 1 ≤ −5 %), la clôture 1 h précédente ne l'ayant pas fait. **Début de la chute**
   `F = E − 24 h`. Un nouveau début pendant une chute en cours en fait partie.
2. **Stabilisation** `S` : en suivant les bougies 4 h de BTC clôturées après `F`, on tient la bougie qui a fait le plus
   bas de la chute ; `S` est la première clôture 4 h (au plus tôt la première ≥ `E`) au-dessus du plus haut de cette
   bougie, sans nouveau plus bas (une bougie qui fait un nouveau plus bas devient la référence). Sans stabilisation
   dans les 30 jours qui suivent `E`, l'événement est abandonné (garde technique).
3. **« A tenu »** : aucune clôture 4 h de la paire, entre `F` et `S`, sous son plus bas des 10 jours précédant `F`.
   Données exigées : la bougie 1 h qui clôture à `F`, au moins 200 bougies 1 h sur les 10 jours, et **toutes** les
   bougies 4 h de la chute (sinon la paire n'est pas lue).
4. **Entrée** = clôture 4 h de la paire à `S` ; **stop de clôture** = plus bas de la paire pendant la chute (bougies 1 h
   de `F` à `S`) − 0,25 × ATR 4 h (de la bougie à `S`) ; **objectif** = +2 R.
5. **Au plus 3 paires par événement** : parmi celles qui ont tenu (BTCUSDT exclue : c'est la référence), celles dont la
   **baisse pendant la chute** (plus bas de la chute / clôture à `F` − 1) est la plus faible ; égalité : ordre
   alphabétique. Une paire en position ou en repos `FORCE_RELATIVE` (§ 3) est écartée **avant** ce choix.

### 2.4 `INSIDE_DAY` (unité 1 jour)

1. Tendance haussière journalière lue sur la journée intérieure.
2. **Journée intérieure** `J` : plus haut(J) ≤ plus haut(J − 1) et plus bas(J) ≥ plus bas(J − 1), deux journées UTC
   consécutives ; `J − 1` est la **mère**.
3. **Entrée** : la première **clôture 4 h** au-dessus du plus haut de la mère dans les 48 h qui suivent la clôture de
   `J` (12 clôtures 4 h). Voir le § 8, choix 1 (la demande disait « clôture 1 h »).
4. **Stop de clôture journalière** = plus bas de la mère ; **refus** si entrée − stop > 3 × ATR14 journalier (de `J`)
   (`STOP_TROP_LARGE`) ; **objectif** = +2 R.

### 2.5 `SORTIE_BASE_LONGUE` (unité 1 jour)

1. **Entrée** à la clôture journalière `B` si : clôture(B) > plus haut des 90 journées précédentes **et** volume(B) >
   1,5 × la moyenne des 20 journées précédentes.
2. **Base** : les 30 journées qui précèdent `B` ont une hauteur (plus haut − plus bas) ≤ 25 % de la clôture de la
   journée `B − 1` ; la base est étendue en arrière tant qu'elle y tient (180 jours au plus) ; clôture(B) > haut de la
   base (étendue).
3. **Stop de clôture journalière** = milieu de la base ; **objectif** = max(haut de base + hauteur, entrée + 2 R)
   (§ 8, choix 2).

### 2.6 Tableau des paramètres (figés, déclarés a priori)

| Paramètre | Valeur | Paramètre | Valeur |
|---|---|---|---|
| ATR | 14, Wilder (4 h et journalier) | Tendance journalière | clôture > EMA50, EMA20 > EMA50, ≥ 60 journées |
| Volume de confirmation | > 1,5 × moyenne des 20 précédentes | Bougies | closes, blocs complets, 00:00 UTC |
| Base (BR) | ≥ 10 bougies 4 h, ≤ 1,5 ATR journalier, extension ≤ 180 | Retest (BR) | 10 bougies, ≤ haut + 0,25 ATR 4 h |
| Stop (BR) | haut de base − 0,25 ATR 4 h | Objectif (BR) | haut + hauteur, refus < 1,5 R |
| Bollinger (SQ) | 20, 2 écarts-types (population) | Keltner (SQ) | EMA20 ± 1,5 × ATR20 |
| Compression (SQ) | ≥ 6 bougies 4 h | Stop / objectif (SQ) | plus bas des 6 / +2 R |
| Chute BTC (FR) | ≤ −5 % sur 24 h (clôtures 1 h) | « A tenu » (FR) | plus bas des 10 jours d'avant ; ≥ 200 bougies 1 h |
| Stop / objectif (FR) | plus bas de la chute − 0,25 ATR 4 h / +2 R | Paires par événement (FR) | 3 (plus faibles baisses) |
| Garde de chute (FR) | 30 jours | Entrée (ID) | 1re clôture 4 h > mère dans les 48 h |
| Stop (ID) | bas de la mère, refus > 3 ATR journalier | Objectif (ID) | +2 R |
| Base longue (LB) | ≥ 30 jours, ≤ 25 % de la clôture, extension ≤ 180 | Plus haut (LB) | 90 journées, plus haut des plus hauts |
| Stop (LB) | clôture sous le milieu de la base | Objectif (LB) | max(haut + hauteur, +2 R) |
| Stop de secours | entrée − 1,5 R, intrabar 1 h | TP1 | moitié à +1 R, puis stop de clôture à l'entrée |
| Durée maximale | 10 jours (4 h), 30 jours (1 jour) | Discipline | 1 position par paire et configuration, 48 h de repos |
| Placebos (4 h) | 20, clôtures 1 h à t ± 5…84 h | Placebos (1 jour) | 20, t ± 2…15 jours (même heure) |
| Graine des placebos | sha256("PRICE_ACTION:" + id) | Identifiant | sha256("PRICE_ACTION:config:paire:instant")[:16] |
| IC du R | 95 %, blocs de 7 jours, 10 000 tirages, graine 20261010, ≥ 8 blocs | IC de l'excès | 1 − 0,05/5 = 99 % |
| Signaux minimum | 100 (historique), 30 résolus (direct) | Garde-fous | sans la meilleure année ; ≥ 4 années positives |

Aucun de ces nombres n'a été réglé sur un résultat. Ils viennent de la demande du propriétaire, ou de conventions déjà
en service (ATR14, EMA20/50, blocs de 7 jours, 20 placebos), ou ce sont des gardes techniques (180, 30 jours).
Un ajustement a priori par configuration reste permis **une seule fois**, sur les seuls comptages à blanc du § 9.

## 3. Gestion commune (historique, contrôle H0 et direct, signal et placebos)

- **Entrée** au prix de clôture de la décision, frais et glissement taker du modèle commun `forward/costs.py`
  (scénarios central et défavorable).
- **Stop à la clôture** de l'unité de la configuration : à chaque clôture 4 h UTC (configurations 4 h) ou à 00:00 UTC
  (configurations journalières), si la clôture ≤ stop de clôture, sortie à cette clôture.
- **Stop de secours dur** à entrée − 1,5 R, touché si un plus bas 1 h ≤ niveau : sortie au niveau (ou à l'ouverture si
  elle est déjà dessous).
- **TP1** : moitié à +1 R (plus haut 1 h ≥ TP1), puis le stop de clôture remonte à l'entrée (le stop de secours ne
  bouge pas) ; **objectif** : l'autre moitié.
- **Durée maximale** : 10 jours (4 h) ou 30 jours (1 jour) ; sortie à la clôture de la dernière bougie 1 h.
- **Prudence** : une bougie 1 h qui touche à la fois un objectif et le stop de secours compte le stop ; dans une même
  bougie, l'ordre est secours, TP1, objectif, stop de clôture. Toutes les sorties sont comptées au marché (taker).
- **R** = résultat net (ventes nettes − achat net) / (entrée − stop).
- **Discipline** : une seule position active par paire **et par configuration** ; 48 h de repos après la sortie
  (scénario central) ; dans l'historique, un signal qui tombe pendant une position ou un repos est écarté (compté).
- Même règle que `assistant/rules.simulate` (F18), mais écrite à part (`price_action/manage.py`) parce que celle-ci fixe
  le stop de clôture à 4 h et la durée à 10 jours ; un test vérifie que les deux donnent le même R en 4 h.

## 4. Étude historique (DEVELOPMENT seulement)

### 4.1 Univers et données

- **Principal : top 40 à date** (`research/pit_universe.py`, `UNIVERSE_PIT.md`) : un signal d'instant `t` ne compte que
  si sa paire appartient au top 40 du **mois du jour `t − 1 jour`** (la veille de la décision ; le top d'un mois est
  calculé sur les 30 journées qui finissent la veille du 1er). **Paires retirées ou renommées comprises.**
- **Bougies 1 h du magasin long** (`long_history`), **coupées** : seules les bougies closes au plus tard le
  2025-07-01 00:00 UTC (fin de DEVELOPMENT) sont lues. 8 paires passées par le top n'ont pas d'historique 1 h (AION,
  ANT, GAL, JST, LEVER, SC, SKL, SUN) : absentes, déclaré.
- BTCUSDT (même magasin) fournit les événements de `FORCE_RELATIVE`.
- **Contrôle descriptif** : les mêmes règles sur les **40 paires de recherche** (survivantes, `RESEARCH_UNIVERSE`), sans
  condition d'appartenance ; descriptif seulement, aucun verdict.

### 4.2 Période et fenêtres

- Signaux à partir du **2019-01-01** ; un signal n'entre que si **toute sa fenêtre** (placebo le plus tardif + durée
  maximale : 84 h + 10 jours, ou 15 + 30 jours) est close au plus tard le 2025-07-01 00:00 UTC. Rien d'après le
  2025-06-30 n'est lu. Les données d'avant 2019 servent à amorcer les indicateurs.
- **Heures absentes** (pannes de Binance) : sautées dans l'historique (données définitives). **Paire retirée de la
  cote** pendant une position : sortie à la dernière clôture connue (`FIN_DE_COTATION`), pour le signal comme pour un
  placebo ; on garde ainsi la fin d'une paire morte, souvent la pire, au lieu de la perdre.

### 4.3 Placebos (rôle décisif retiré le 2026-10-10 : descriptifs seulement, § 11)

Pour chaque signal, **20 entrées au marché** sur la même paire, tirées sans remise, graine
`sha256("PRICE_ACTION:" + id)` :
- configurations 4 h : clôtures 1 h à `t + k` heures, k ∈ [−84 ; −5] ∪ [5 ; 84] ;
- configurations journalières : `t + k` jours (même heure que `t`), k ∈ [−15 ; −2] ∪ [2 ; 15].

Même géométrie en pourcentage du prix d'entrée (stop, TP1, stop de secours, objectif), même gestion, mêmes frais. Un
placebo dont la bougie d'entrée manque est écarté. Excès = R du signal − moyenne des R des placebos utilisables. En
descriptif, l'excès est donné séparément sur les placebos **arrière** et **avant** (biais déclarés : les placebos
arrière partagent le chemin qui a formé la configuration, `FIGURES_HISTORIQUE.md` ; les placebos avant, le contexte
des jours suivants ; la fenêtre symétrique est faite pour équilibrer les deux, et le contrôle H0 le mesure).

### 4.4 Métriques, par configuration (remplacé par § 11.2)

R net moyen, central et défavorable, avec un **IC95 par blocs de 7 jours** (`backtest/metrics.day_block_ci95`, 10 000
tirages, graine 20261010, au moins 8 blocs) ; **excès** moyen sur les placebos avec un intervalle au niveau
**1 − 0,05/5 = 99 %** (`day_block_ci`). Descriptifs : part gagnante, taux de TP1, issues, excès arrière et avant,
nombre et R moyen par année, signaux écartés par la discipline, refus de géométrie.

### 4.5 Décision, par configuration (version initiale, remplacée par § 11.1)

- `INSUFFISANT` : moins de **100 signaux** (central), ou un intervalle non calculable ;
- `PISTE` : borne basse de l'IC de l'excès (99 %) **et** borne basse de l'IC95 du R moyen > 0, **en central et en
  défavorable**, **et** les garde-fous du § 4.6 tiennent ;
- `PERTE` : borne haute de l'IC95 du R moyen < 0 dans les deux scénarios ;
- sinon `RIEN` (y compris une piste qui échoue aux garde-fous).

### 4.6 Garde-fous d'une piste (appliqués au R net seul depuis le § 11.1)

- **Sans sa meilleure année** : l'année civile dont la somme des R (central) est la plus forte est retirée ; les deux
  conditions de `PISTE` doivent encore tenir, dans les deux scénarios.
- **Au moins 4 années positives** parmi les années civiles 2019 à 2025 qui ont des signaux (2025 : premier semestre
  seulement) ; une année est positive si son R moyen central > 0 **et** son excès moyen central > 0.

### 4.7 Essais

**5 essais** au registre (`research/experiments.py`, période DEVELOPMENT, `n_trials` = configurations retenues par le
contrôle H0), comptés à l'exécution, une seule fois. Une configuration retirée par le contrôle H0 compte 0 essai. Le
contrôle descriptif des survivantes est dans la même exécution, sans verdict, et n'est pas compté à part (déclaré).

### 4.8 Ce que voudront dire les résultats

- `PISTE` : une piste à confirmer, pas un résultat : il faudrait la période réservée (déjà consultée deux fois, donc
  plus vierge) et le direct (F19). Aucun usage en trading sans décision du propriétaire.
- `RIEN` : pas d'avantage visible avec cette puissance. `PERTE` : la configuration perd de façon démontrée après frais.
- `INSUFFISANT` : trop peu de signaux pour conclure.

## 5. Contrôle sous l'hypothèse nulle (avant toute donnée réelle)

### 5.1 Marché synthétique

`research/price_action_h0.py`, modèle de `tests/test_assistant_h0.py` :
- **100 paires × 6 ans de signaux** (bougies 1 h du 2018-07-01 au 2024-12-31 ; les six premiers mois amorcent les
  indicateurs ; signaux de 2019-01-01 à fin 2024, même règle de fenêtre qu'au § 4.2) et **un BTC synthétique** pour
  `FORCE_RELATIVE` ;
- **rendements simples martingales** : r_t = σ_t z_t, E[r_t | passé] = 0 ; volatilité variable GARCH(1,1)
  (α = 0,05, β = 0,94 ; σ moyen horaire tiré log-uniforme entre 0,4 % et 1,2 % par paire, 0,6 % pour BTC) ; z gaussien
  pour les paires 0 à 49 et BTC, Student à 4 degrés (variance 1) pour les paires 50 à 99 (queues épaisses, leçon de N2t
  dans `METEO_MARCHE.md` § 7.5) ; mèches proportionnelles à σ_t ;
- **volume** lognormal × (0,5 + |z|) × un facteur journalier lognormal (σ = 0,5) : lié à l'ampleur, jamais au sens ;
- graine 20261010 (`SeedSequence`, une sous-graine par paire) ; tout est déterministe ;
- **détecteur, discipline, gestion et placebos EXACTS** de l'étude (`pair_rows`, `force_items`, `force_rows`), frais
  du scénario central, toutes les paires « membres » tout le temps.

Sur ce marché, rien n'est prévisible : l'espérance du R net est celle des frais, et l'excès doit rester proche de 0.

### 5.2 Critères du contrôle n° 1, par configuration (critère d'excès abandonné ; contrôle n° 2 au § 11.4)

- au moins **100 signaux** synthétiques (sinon non jugeable : échec) ;
- **|excès moyen| ≤ 0,05 R** ;
- **couverture ≥ 0,90** : les paires sont réparties en G groupes (paire i → groupe i mod G), avec
  G = min(20, max(3, n // 100)) pour qu'un groupe compte environ 100 signaux (n = signaux synthétiques de la
  configuration) ; dans chaque groupe, l'intervalle de l'excès au niveau de l'étude (99 %, blocs de 7 jours, 10 000
  tirages, au moins 8 blocs) doit contenir 0 ; couverture = part des G groupes qui le contiennent, **un groupe sans
  intervalle calculable comptant comme non couvert**. Avec G ≤ 9, une couverture ≥ 0,90 exige que **tous** les groupes
  contiennent 0 ; pour les configurations rares (G de 3 à 7), l'estimation repose sur peu de groupes et le critère est
  peu informatif : il est appliqué tel quel.

### 5.3 Conséquence

Une configuration qui échoue à un seul critère est **retirée de l'étude**, avec **0 essai**, et écrite comme telle au
§ 5.5. Le contrôle est lancé **une seule fois** (`csi price-action controle-h0`), ses critères sont écrits dans
`reports/PRICE_ACTION-H0-<commit>/criteres.json`, et ce fichier est inscrit avec son empreinte
(`price_action_review.CONTROLE_H0`) : l'exécution réelle le relit et refuse s'il a changé. Il n'y a pas de seconde
itération pour « faire passer » une configuration. Seule exception, une fois au plus (comme `METEO_MARCHE.md` § 7.6) :
un **écart prouvé entre le code et ce texte** relevé par la relecture, démontré par un test unitaire écrit avant la
relance ; le contrôle est alors relancé une fois et les deux passages sont publiés.

**Puissance du critère d'excès, déclarée avant le passage.** L'erreur type de l'excès moyen est d'environ
1,3 R / √n (écart-type du R d'une transaction supposé ≈ 1,3 R, hypothèse non mesurée). D'après les seuls comptages de
candidats synthétiques (§ 5.4 : avant discipline, `BASE_RETEST` 5 124, `INSIDE_DAY` 3 458, `SQUEEZE` 664,
`SORTIE_BASE_LONGUE` 441, `FORCE_RELATIVE` au plus 756), un biais nul passerait le critère |excès| ≤ 0,05 R avec une
probabilité d'environ 0,99 (`BASE_RETEST`), 0,96 (`INSIDE_DAY`), 0,65 (`SQUEEZE`), 0,55 (`SORTIE_BASE_LONGUE`) et 0,70
(`FORCE_RELATIVE`) : pour les trois configurations rares, **même sans aucun biais**, le critère échoue par le seul hasard
une fois sur trois à une fois sur deux. Le critère est celui du propriétaire ; il est appliqué tel quel (option
prudente : une configuration que l'instrument ne peut pas valider n'est pas testée).

### 5.4 Journal du générateur synthétique (comptages seulement, aucun R ni excès regardé)

| # | Date | Générateur | Ce qui a été regardé | Constat | Décision |
|---|---|---|---|---|---|
| 1 | 2026-10-10 | σ constant (0,8 %), volume lognormal i.i.d. | comptes de candidats sur 1 paire × 3 ans | `SQUEEZE` 0 et `SORTIE_BASE_LONGUE` 0 : une marche à σ constant ne se comprime presque jamais | volatilité GARCH (martingale conservée) |
| 2 | 2026-10-10 | GARCH, Student sur la moitié des paires, volume × (0,5 + \|z\|) | comptes sur 10 paires × 6,5 ans | `BASE_RETEST` 323, `INSIDE_DAY` 353, `SQUEEZE` 55, `SORTIE_BASE_LONGUE` 0 ; BTC : 266 événements | facteur de volume journalier (sans lui, un volume journalier, somme de 24 heures i.i.d., ne dépasse jamais 1,5 × sa moyenne) |
| 3 | 2026-10-10 | idem + facteur journalier lognormal σ = 0,5 (essais à 0,4 et 0,6 : 39 et 60 candidats `SORTIE_BASE_LONGUE` sur 10 paires) | comptes sur 10 paires | toutes les configurations ont des candidats | **générateur figé** (valeur ronde 0,5 entre les deux) |
| 4 | 2026-10-10 | générateur figé | comptes de candidats dans la fenêtre sur les 100 paires (avant discipline, aucune simulation) ; jours distincts | `BASE_RETEST` 5 124, `INSIDE_DAY` 3 458, `SQUEEZE` 664, `SORTIE_BASE_LONGUE` 441 ; BTC : 252 événements (au plus 756 signaux `FORCE_RELATIVE`) ; avec 20 groupes fixes de 5 paires, un groupe des trois configurations rares n'aurait que 20 à 40 signaux, soit moins de 8 blocs de 7 jours : intervalle non calculable par construction | critère de couverture dimensionné sur ces comptes : G = min(20, max(3, n // 100)) groupes (§ 5.2) |

Aucun R, aucun excès, aucune couverture n'a été calculé avant le passage inscrit du § 5.5.

### 5.5 Résultats du contrôle sous H0 (passage unique du 2026-10-10)

`csi price-action controle-h0 --workers 4`, code du commit `fa7b872` (propre), `CSI_ROOT` = dépôt principal, aucune
variable `CSI_*` de calcul. Fichier : `reports/PRICE_ACTION-H0-fa7b872f4ccd/criteres.json`, SHA-256
`ca7252a9645b61d995f56d228b6f7e3401b3b72d63c59037468540977f26cccf` (inscrit dans `price_action_review.CONTROLE_H0`).
Marché synthétique : 100 paires, 266 événements BTC (252 avec au moins une lecture). Frais centraux. R et excès en R.

| Configuration | Signaux | R moyen (σ) | Excès moyen (erreur type) | IC 99 % de l'excès, toutes paires | Excès arrière | Excès avant | Couverture (groupes) | Issue |
|---|---|---|---|---|---|---|---|---|
| `BASE_RETEST` | 4 210 | −0,182 (1,45) | **−0,424** (0,022) | [−0,478 ; −0,370] | −0,865 | +0,017 | 0,00 (20) | **ÉCHEC** |
| `SQUEEZE` | 645 | −0,022 (1,13) | **−0,307** (0,028) | [−0,371 ; −0,248] | −0,676 | +0,054 | 0,00 (6) | **ÉCHEC** |
| `FORCE_RELATIVE` | 756 | −0,052 (1,10) | **−0,204** (0,029) | [−0,268 ; −0,144] | −0,467 | +0,075 | 0,43 (7) | **ÉCHEC** |
| `INSIDE_DAY` | 2 507 | −0,037 (1,21) | **−0,271** (0,021) | [−0,322 ; −0,218] | −0,567 | +0,028 | 0,45 (20) | **ÉCHEC** |
| `SORTIE_BASE_LONGUE` | 325 | −0,002 (0,94) | **−0,373** (0,031) | [−0,430 ; −0,320] | −0,744 | −0,004 | 0,00 (3) | **ÉCHEC** |

Signaux écartés par la discipline : 914, 19, 532, 951 et 116 ; refus de géométrie : 1 108 objectifs sous 1,5 R
(`BASE_RETEST`), 24 stops trop larges (`INSIDE_DAY`).

**Verdict, à la lettre du § 5.3 : les cinq configurations échouent (excès et couverture) ; elles sont toutes retirées
de l'étude, avec 0 essai. L'étude historique est abandonnée : aucune donnée réelle n'a été lue et ne le sera pas.**
`csi price-action executer` refuse désormais de tourner (toutes les configurations retirées). Pas de seconde
itération : l'exception « bug » du § 5.3 ne s'applique pas (voir ci-dessous, c'est la méthode, pas un écart au texte).

**Lecture.** Les R moyens sont ceux des frais (−0,18 R pour `BASE_RETEST`, dont le stop serré rend les frais lourds en
R ; proches de 0 ailleurs) : le détecteur et la gestion se comportent comme attendu sur un marché sans information.
L'échec vient **entièrement des placebos arrière** : excès arrière de −0,47 à −0,86 R, excès avant de −0,004 à +0,075 R.
Les cinq configurations exigent une montée juste avant la décision (cassure d'une base, clôture au-dessus de la
Bollinger, cassure de la mère, plus haut de 90 jours, rebond après la chute) ; un placebo pris 5 à 84 h (ou 2 à 15 jours)
avant entre plus bas et sa gestion contient cette montée, que le signal, lui, n'a pas encore : il « sait » ce qui va
arriver par rapport à sa propre entrée. C'est le biais déjà décrit dans `FIGURES_HISTORIQUE.md`, ici bien plus fort que
pour l'assistant (F18, ±0,03 R au même contrôle), dont la configuration (repli puis reprise) est presque symétrique.
Avec ces placebos, l'excès d'un signal sans aucune valeur serait de −0,2 à −0,4 R : la mesure ne peut pas trancher.

**Ce qui n'est pas fait, et pourquoi.** Ne garder que les placebos avant, ou changer la fenêtre, serait une nouvelle
méthode décidée APRÈS avoir vu ce contrôle : ce serait régler l'instrument jusqu'à ce qu'il passe (leçon de
`METEO_MARCHE.md` § 7.5-7.6). Rien n'est donc relancé ici. Toute suite (par exemple une étude « placebos avant
seulement », avec son propre contrôle H0 et ses propres essais) est une **décision du propriétaire**, à pré-inscrire
comme une nouvelle étude.

**Conséquence pour F19** (même gestion, mêmes placebos) : l'intervalle de l'excès y porte le même biais négatif. Un
verdict `SUPERIEUR_AU_HASARD` (qui exige un excès > 0) y est donc presque impossible par construction ; `INFERIEUR_AU_HASARD`
ne regarde que le R (non biaisé) et reste valable ; `NON_DEMONTRE` est l'issue attendue. Déclaré dans la section
`F19_PRICE_ACTION` de `FORWARD_TESTS.md`. F19 n'est pas démarré : son démarrage reste une décision du propriétaire.

## 6. Ordre et garde d'exécution

1. Protocole (ce texte), commité avant le code de mesure.
2. Code et tests unitaires (sans réseau).
3. Comptages à blanc (§ 9) ; ajustement a priori éventuel, une fois.
4. Contrôle sous H0 lancé une fois, inscrit (§ 5).
5. **Relecture `leak-auditor` obligatoire** (lancée par le propriétaire), puis inscription par un commit qui ne touche
   que `research/price_action_review.py` : `CODE_REVIEW` (commit relu), `CONFIG_FINGERPRINT` (empreinte des sections
   `data` et `protocol` de la configuration effective), `CONTROLE_H0` (chemin du fichier de critères + « # » + SHA-256).
6. **Exécution unique** : `csi price-action executer --executer` (4 processus au plus). Refus sans `--executer`, avec un
   code non commité, différent du commit relu sur les chemins surveillés (`REVIEWED_PATHS` : `price_action/`, l'étude,
   le contrôle, l'univers à date, le magasin long, le protocole, le registre, les frais, les intervalles, les
   indicateurs, la configuration, la CLI), avec une autre configuration, sans contrôle H0 inscrit, ou si l'étude a déjà
   été exécutée (registre). Les configurations retirées par le contrôle H0 ne sont ni calculées ni comptées.

## 7. Test en direct `F19_PRICE_ACTION` (résumé ; pré-inscription complète dans `FORWARD_TESTS.md`)

- Le **même détecteur** est appelé à **chaque clôture 4 h UTC** (dont la clôture journalière de 00:00), sur la liste
  halal figée au démarrage de F15, bougies 1 h du magasin de F15 en lecture seule, 300 jours d'historique ; passage
  horaire aligné comme F18 ; au-delà de **30 min** de retard, l'évaluation est inscrite `late` et aucun appel n'est
  émis.
- **5 appels par jour UTC au plus**, toutes configurations, les plus récents d'abord ; à instant égal (tous les
  candidats d'une clôture), l'ordre de leur identifiant sha256, neutre entre configurations et entre paires (§ 8,
  choix 18). Mêmes placebos, même gestion, mêmes frais ; niveaux arrondis au pas de cotation quand il est
  connu (entrée, TP1 et objectif vers le haut ; stop et secours vers le bas).
- Verdict **par configuration** sur le R net seul (§ 11.5, `forward/f19.verdict`) ; `INSUFFISANT` sous 30 résolus.
  Revue à 42 jours, évaluation à 84 jours, 1 essai FORWARD.
- Messages Telegram dans une boîte séparée `state/price_action_outbox.json` (identifiants `pa:`), servis par
  `GET /assistant/outbox` (fusion chronologique avec ceux de l'assistant, 20 au plus) et marqués par
  `POST /assistant/sent`. État `state/price_action.json`, route `GET /price-action`, carte « Price action » dans
  l'onglet Marché.

## 8. Choix faits là où la demande était ambiguë (option la plus prudente)

1. **`INSIDE_DAY` entre à une clôture 4 h, pas 1 h.** Le direct n'évalue qu'aux clôtures 4 h ; pour que l'historique et
   le direct appliquent exactement la même règle, l'entrée est la première **clôture 4 h** au-dessus de la mère dans
   les 48 h. Placebos à `t ± k` jours, à la même heure que l'entrée.
2. **`SORTIE_BASE_LONGUE`, « objectif = hauteur reportée, au moins 2 R »** : objectif = max(haut + hauteur, entrée + 2 R).
   Le lire comme un refus sous 2 R rendrait la configuration impossible : avec le stop au milieu de la base, R ≥ la
   moitié de la hauteur, donc la hauteur reportée vaut au plus 2 R.
3. **« 25 % du prix »** = 25 % de la clôture de la dernière journée de la base ; **« nouveau plus haut de 90 jours »** =
   clôture au-dessus du plus **haut** des 90 journées précédentes (lecture la plus stricte).
4. **Bases étendues au maximum** en arrière tant qu'elles tiennent (lecture du trader : le haut de la base est le haut
   de toute la consolidation), avec une garde de 180 bougies ou 180 jours.
5. **`BASE_RETEST`** : ATR 4 h de la bougie de cassure ; la bougie du retest peut être celle de l'entrée si elle
   repart ; retest **et** entrée dans les 10 bougies qui suivent la cassure ; une clôture sous le haut de base annule.
6. **Keltner** = EMA20 ± 1,5 × ATR20 de Wilder ; **Bollinger** avec l'écart-type de population.
7. **`FORCE_RELATIVE`** : la chute commence 24 h avant le déclenchement ; les 10 jours de référence précèdent ce
   début ; « baisse sur la chute » = plus bas de la chute rapporté à la clôture au début ; BTC exclue des candidates ;
   données complètes exigées ; discipline appliquée **avant** le choix des 3 (dans l'historique comme en direct) ; garde
   de 30 jours sans stabilisation.
8. **Appartenance** au top 40 du mois de la veille de la décision (`t − 1 jour`).
9. **Paires retirées** : sortie à la dernière clôture connue plutôt qu'un signal perdu (la fin d'une paire morte est
   gardée).
10. **Trous** : sautés dans l'historique (données définitives) ; en direct, `EN_COURS` puis `TROU` 2 jours après la
    fenêtre (comme F18).
11. **Garde-fous** : « meilleure année » = plus forte somme des R centraux ; « année positive » = R moyen ET excès moyen
    centraux > 0 ; 4 années positives au moins parmi celles qui ont des signaux.
12. **Contrôle H0** : volatilité GARCH et queues de Student (toujours des martingales) pour que les compressions et les
    bases existent ; au moins 100 signaux exigés pour juger ; couverture sur 20 groupes de 5 paires.
13. **Pas d'arrondi au pas de cotation dans l'historique** (pas inconnus à l'époque) ; arrondi en direct.
14. **Amorçage des moyennes** : le direct lit 300 jours, l'historique toute la série ; l'EMA50 et l'ATR de Wilder
    oublient leur départ (poids < 0,3 % après 200 jours) : écart négligeable, déclaré.
15. **Intervalle de l'excès en direct** au niveau 1 − 0,05/5 (cinq configurations), comme l'historique (F18 : 1 − 0,05/2).
16. **Contrôle H0 lancé avant la relecture** (ordre demandé par le propriétaire) ; s'il faut corriger le code relu, voir
    l'exception du § 5.3.
17. **Comptages à blanc du § 9** : ils lisent les 60 derniers jours du magasin local (2026), donc la période réservée,
    **pour des comptes de candidats seulement** (aucun prix de sortie, aucun R), comme F18 ; c'est hors de l'étude
    historique, qui ne lit rien après le 2025-06-30.
18. **Quota de 5 appels par jour, « les plus récents d'abord »** : tous les candidats d'une même clôture ont le même
    instant ; les départager par l'ordre des configurations favoriserait toujours `BASE_RETEST` quand le quota est
    atteint (≈ 17 candidats par jour attendus sur 166 paires, § 9). L'ordre retenu à instant égal est celui de
    l'identifiant sha256 du signal : reproductible et neutre entre configurations et entre paires.

## 9. Comptages à blanc (aucun résultat de transaction)

Le 2026-10-10, `csi price-action comptages --debut 2026-08-01T00:00 --fin 2026-09-30T20:00` : les 16 paires du magasin
local, 366 clôtures 4 h (61 jours), par le chemin du direct (détecteur sur 300 jours, données closes à chaque
clôture), **sans discipline ni quota**. Seuls des comptes ont été regardés, jamais un R ni un prix de sortie.

| Configuration | Candidats | Par jour (moyenne) | Par jour (maximum) | Refus de géométrie |
|---|---|---|---|---|
| `BASE_RETEST` | 41 | 0,67 | 6 | 8 objectifs sous 1,5 R |
| `SQUEEZE` | 14 | 0,23 | 4 | — |
| `FORCE_RELATIVE` | **0** | 0 | 0 | — |
| `INSIDE_DAY` | 30 | 0,49 | 6 | 3 stops au-delà de 3 ATR journaliers |
| `SORTIE_BASE_LONGUE` | 16 | 0,26 | 4 | — |

- **Aucune configuration au-delà de 20 par jour.**
- **`FORCE_RELATIVE` : 0 candidat parce qu'il n'y a eu aucun événement BTC (−5 % sur 24 h) dans la fenêtre** (0 début,
  0 stabilisation : marché calme). **Aucun ajustement** : abaisser le seuil pour faire apparaître des événements dans
  une fenêtre calme serait un réglage sur la période réservée, et la règle du propriétaire (−5 % sur 24 h) est
  explicite. Conséquence déclarée : en direct, `FORCE_RELATIVE` dépend de la survenue de chutes de BTC pendant les 84
  jours ; `INSUFFISANT` est probable. Sur DEVELOPMENT, les chutes de BTC sont nombreuses (2019-2022).
- **Aucun paramètre n'a donc été ajusté sur comptages.**
- Ordre de grandeur en direct : ≈ 1,7 candidat par jour pour 16 paires, soit de l'ordre de 17 par jour pour les 166
  paires de F15 (extrapolation grossière) : le **quota de 5 appels par jour** sera souvent atteint ; avec la
  discipline, de l'ordre de 300 à 400 appels en 84 jours, répartis sur les configurations (§ 8, choix 18).

## 10. Limites déclarées

- Bougies 1 h : l'ordre des événements dans une heure est inconnu (règle de prudence : le stop d'abord).
- Écart et glissement sont des hypothèses du modèle commun ; aucune exécution réelle n'existe.
- Les blocs de 7 jours laissent une corrélation résiduelle (signaux de plusieurs paires les mêmes jours, positions de
  10 à 30 jours à cheval sur deux blocs) : intervalles possiblement trop étroits.
- Placebos voisins : ils partagent le régime de marché du signal ; l'excès ne se lit jamais seul (la décision exige
  aussi un R moyen > 0).
- Top 40 à date : 8 paires sans historique 1 h ; le top est défini par le volume, pas par le cadre halal du direct.
- Le direct (F19) porte sur la liste halal figée de F15 (choisie en 2026) : biais de sélection des survivantes.
- 12 semaines de direct ne valident rien.

## 11. Amendement du 2026-10-10 : décision au R net seul, placebos descriptifs, contrôle sous H0 n° 2

Décision du coordinateur, par délégation du propriétaire, prise après le contrôle n° 1 (§ 5.5) qui n'a porté que sur
du synthétique ; **aucune donnée réelle n'a été lue** avant cet amendement. Les cinq configurations, leur gestion et
leurs placebos ne changent pas (code de `price_action/detect.py` et `manage.py` inchangé) ; seul le critère de
décision change, et il est revalidé par un nouveau contrôle sous H0 avant toute exécution.

### 11.1 Décision par configuration (remplace le § 4.5)

- `INSUFFISANT` : moins de **100 signaux** (central), ou un intervalle non calculable ;
- `PISTE` : l'intervalle du **R net moyen** au niveau **1 − 0,05/5 = 99 %** (blocs de 7 jours, 10 000 tirages, graine
  20261010, au moins 8 blocs ; `day_block_ci`) est entièrement > 0, **en central ET en défavorable**, et les garde-fous
  tiennent ;
- `PERTE` : l'IC95 du R net moyen est entièrement < 0 dans les deux scénarios ;
- sinon `RIEN` (y compris une piste qui échoue aux garde-fous).

**Garde-fous** (§ 4.6, appliqués au R net seul) : la condition de `PISTE` doit encore tenir sans l'année civile dont la
somme des R nets centraux est la plus forte, dans les deux scénarios ; et au moins 4 années positives (R net moyen
central > 0) parmi les années civiles qui ont des signaux.

**Justification.** Sur une martingale, l'espérance du gain brut d'une position est nulle quelle que soit la gestion
(temps d'arrêt borné : 10 ou 30 jours) ; le R net vaut donc moins les frais. Le test « R net > 0 » est prudent et ne
dépend d'aucun placebo. Il ne dit pas qu'une configuration fait mieux qu'une entrée au hasard au même moment : il dit
qu'elle a gagné, net de frais, plus que zéro, sur cette période (la dérive du marché y entre ; le top 40 à date et les
paires retirées limitent le biais de survivance, sans l'annuler).

### 11.2 Métriques (remplace le § 4.4)

R net moyen, central et défavorable, avec l'intervalle de décision (99 %) et l'IC95 ; descriptifs : part gagnante,
taux de TP1, issues, nombre et R net moyen par année, signaux écartés par la discipline, refus de géométrie.

### 11.3 Placebos : descriptifs seulement (remplace le rôle des placebos du § 4.3)

Mêmes 20 placebos, mêmes graines. On rapporte l'**excès « avant »** (placebos à t + 5 → t + 84 h, ou t + 2 → t + 15
jours) et l'**excès global**, avec la mention du biais des placebos arrière mesuré au contrôle n° 1 (§ 5.5 : excès
global de −0,20 à −0,42 R sur un marché sans information, excès avant de −0,004 à +0,075 R). **Ils ne servent jamais à
décider.**

### 11.4 Contrôle sous H0 n° 2 (déclaré avant son passage, lancé une seule fois)

- **Marché** : le même qu'au § 5.1 (100 paires, BTC synthétique, graine 20261010, générateur figé du § 5.4), même
  pipeline exact (`collect`), **deux scénarios de coûts** (central et défavorable).
- **Répliques** : 200 sous-échantillons de **40 paires** tirées sans remise parmi les 100 (graine 20261011), taille
  voisine du top 40 de l'étude. Chaque réplique applique la décision du § 11.1 (garde-fous compris) aux signaux de ses 40
  paires ; pour `FORCE_RELATIVE`, aux signaux choisis sur les 100 paires et restreints aux 40. Des répliques entières
  (200 marchés) seraient possibles mais le coordinateur a demandé le même marché : ce sont donc des **sous-échantillons
  chevauchants** (deux répliques partagent en moyenne 16 paires), corrélés entre eux : l'estimation des taux est moins
  précise que 200 tirages indépendants (déclaré).
- **Faux `PISTE`** : part des 200 répliques décidées `PISTE`. Critère : **≤ 0,02** (= 0,05/5 × 2). Sinon **ÉCHEC** :
  configuration retirée de l'étude, 0 essai. En descriptif, plus sévère (au bord de l'hypothèse nulle) : part des
  répliques dont l'intervalle 99 % du R **brut** (frais nuls) est > 0.
- **Contrôle positif** : pour chaque signal synthétique, les bougies 1 h qui suivent la décision, sur toute la durée de
  détention, sont multipliées par (1 + δ)^k (k = rang de l'heure après la décision : dérive ajoutée en rendement simple),
  avec δ = m × (entrée − stop) / entrée ; m (en R par heure), le même pour tous les signaux d'une configuration, est
  calibré par dichotomie pour que le **R net moyen central de tous les signaux synthétiques de la configuration vaille
  +0,15 R** (« +0,15 R net vrai par signal ») : plus petit m (à 1e-7 près) qui l'atteint, la moyenne avançant par
  petits sauts ; la valeur obtenue est inscrite. Les signaux restent ceux du passage sans dérive (discipline non
  recalculée) ; les placebos ne sont pas modifiés (ils ne décident de rien). **Puissance** = part des 200 mêmes
  répliques décidées `PISTE`. Critère : **≥ 0,50**, sinon **`INSTRUMENT_TROP_FAIBLE`** : 0 essai historique pour cette
  configuration, mais F19 la mesure quand même en direct.
- **Essais** : une configuration n'est exécutée et comptée que si ses faux `PISTE` ≤ 0,02 **et** sa puissance ≥ 0,50.
- Fichier `reports/PRICE_ACTION-H0N2-<commit>/criteres.json`, inscrit dans `price_action_review.CONTROLE_H0` avec son
  empreinte (il remplace l'inscription du contrôle n° 1, que l'exécution refuse désormais). Pas de seconde itération.

### 11.5 F19 (remplace le seuil de décision de la section `F19_PRICE_ACTION`)

Verdict par configuration sur le **R net seul** : `INSUFFISANT` sous 30 appels résolus ou 10 jours (ou intervalle non
calculable) ; `SUPERIEUR_A_ZERO` si l'intervalle 1 − 0,05/5 du R net moyen est > 0 en central ET en défavorable ;
`INFERIEUR_A_ZERO` si l'IC95 est < 0 dans les deux ; sinon `NON_DEMONTRE`. L'excès « avant » et l'excès global sont
descriptifs. `forward/f4.verdict` n'est plus utilisé par F19.

### 11.6 Résultats du contrôle sous H0 n° 2 (passage unique du 2026-10-10)

`csi price-action controle-h0 --workers 4`, code du commit `a3f8ff8` (propre), `CSI_ROOT` = dépôt principal, 3 min 40 s.
Fichier : `reports/PRICE_ACTION-H0N2-a3f8ff8bbb22/criteres.json`, SHA-256
`d494a8a37ce6e99ae440da04466e3aacbb2d25812270f23b1648827e6882c5c6` (inscrit dans `price_action_review.CONTROLE_H0`).
Même marché que le n° 1 (mêmes signaux : 4 210, 645, 756, 2 507, 325). 200 sous-échantillons de 40 paires.

| Configuration | R net H0 (central / défav.) | Faux `PISTE` (H0) | Décisions H0 | R brut : IC 99 % > 0 (descr.) | Dérive m (R/h) | R net avec dérive (central / défav.) | Puissance | Issue |
|---|---|---|---|---|---|---|---|---|
| `BASE_RETEST` | −0,182 / −0,299 | **0,000** | 200 PERTE | 0,005 | 0,0171 | +0,150 / +0,033 | **0,010** | `INSTRUMENT_TROP_FAIBLE` |
| `SQUEEZE` | −0,022 / −0,061 | **0,000** | 196 RIEN, 4 PERTE | 0,015 | 0,0024 | +0,154 / +0,114 | **0,040** | `INSTRUMENT_TROP_FAIBLE` |
| `FORCE_RELATIVE` | −0,052 / −0,103 | **0,000** | 179 RIEN, 21 PERTE | 0,015 | 0,0017 | +0,150 / +0,098 | **0,045** | `INSTRUMENT_TROP_FAIBLE` |
| `INSIDE_DAY` | −0,037 / −0,066 | **0,000** | 170 RIEN, 30 PERTE | 0,015 | 0,0012 | +0,151 / +0,121 | **0,285** | `INSTRUMENT_TROP_FAIBLE` |
| `SORTIE_BASE_LONGUE` | −0,002 / −0,014 | **0,000** | 194 RIEN, 4 PERTE, 2 INSUFFISANT | 0,025 | 0,0003 | +0,151 / +0,140 | **0,080** | `INSTRUMENT_TROP_FAIBLE` |

**Verdict, à la lettre du § 11.4 :** la règle au R net seul ne donne **aucun faux `PISTE`** (0 sur 200 partout ; même le
R brut, au bord de l'hypothèse nulle, ne passe que dans 0,5 à 2,5 % des sous-échantillons), mais elle est **trop faible
pour les cinq configurations** : avec +0,15 R net vrai par signal, `PISTE` sort dans 1 à 28,5 % des sous-échantillons
seulement (critère : 50 %). Les cinq sont **`INSTRUMENT_TROP_FAIBLE`** : **0 essai historique**, `csi price-action
executer` refuse (aucune configuration validée) ; l'étude historique n'est pas exécutée, aucune donnée réelle lue.
**F19 les mesure quand même en direct**, comme prévu par l'amendement (§ 11.5).

**Lecture.** Trois causes, toutes visibles sur ces chiffres et déclarées sans rien relancer :
- **le scénario défavorable** : la règle exige l'intervalle > 0 en défavorable aussi ; pour `BASE_RETEST`, dont le stop
  est serré, les frais défavorables coûtent ≈ 0,12 R de plus et ramènent +0,15 à +0,03 R : presque jamais > 0 ;
- **la taille d'un sous-échantillon** (40 paires, de l'ordre de 130 à 1 700 signaux) face à un écart-type d'environ
  1 à 1,5 R, au niveau 99 % ;
- **les garde-fous** (sans la meilleure année, 4 années positives), qui s'ajoutent.
Pour qu'une piste à +0,15 R se voie, il faudrait plus de signaux, ou un effet plus grand ; ce n'est pas réglé ici (pas de
seconde itération). La suite est une décision du propriétaire ou du coordinateur.

## 12. Tests séparés F20 à F24 (demande du 2026-10-10, après le démarrage de F19)

En plus de F19, qui reste tel quel, chaque configuration est mesurée **seule** par son propre test en direct :
`F20_BASE_RETEST`, `F21_SQUEEZE`, `F22_FORCE_RELATIVE`, `F23_INSIDE_DAY`, `F24_SORTIE_BASE_LONGUE` (pré-inscriptions
complètes dans `FORWARD_TESTS.md`, code `forward/pa_single.py` et `forward/f20.py` … `f24.py`).

- **Mêmes règles que F19** : même détecteur, même enchaînement que `evaluate.evaluate` (vérifié par un test : mêmes
  candidats que F19 avant quota, à discipline égale, y compris le choix des 3 de `FORCE_RELATIVE`), même gestion, mêmes
  frais, mêmes placebos en descriptif, même verdict au R net seul (`f19.verdict`, correctif de 5 tests gardé), même
  retard maximal de 30 min sur l'horloge réelle, même univers et même magasin.
- **Seule différence : le quota.** 5 appels par jour UTC **par test**, pour sa seule configuration ; une position active
  par paire ; discipline lue dans le journal du test.
- **Placebos** : graine `sha256("<TEST_ID>:" + call_id)` ; l'identifiant d'appel est celui de F19, ce qui fait
  reconnaître un appel aussi fait par F19 (`aussi_dans_F19: true`, pas de message Telegram en double).
- **Une seule détection par clôture et par passage** pour les cinq tests, lue paire par paire puis gardée en mémoire
  jusqu'à la fin du passage. Mesure du 2026-10-10 sur 166 paires synthétiques (440 jours de bougies 1 h au format du
  magasin, clôture du 2026-10-10 04:00, processus neufs, RSS maximal) : F19 seul 303 Mo (+175 Mo au-dessus de
  l'import), F20 à F24 seuls 229 Mo (+101 Mo), **F19 puis F20 à F24 dans le même passage : 303 Mo, soit +0 Mo sur
  F19 seul** (la lecture paire par paire réutilise la mémoire déjà prise par F19) ; ≈ 4,5 s pour les cinq tests.
- **Boîte Telegram** `state/price_action_single_outbox.json` (identifiants `ps:`), fusionnée avec celles de
  l'assistant et de F19 par `GET /assistant/outbox` ; état court `state/price_action_single.json`, ligne par test dans
  la carte « Price action » et dans `GET /price-action` (`separate_tests`), section commune dans le rapport quotidien.
- **Ce que cela ne change pas** : les cinq hypothèses restent les mêmes (pas de nouvelle correction de multiplicité) ;
  les verdicts de F19 et de F20 à F24 sont fortement corrélés (mêmes appels en partie) ; la puissance reste faible
  (§ 11.6) ; 23 tests en direct sont pré-inscrits ; 5 essais FORWARD de plus au démarrage. Aucun gain démontré.

## Historique

- 2026-10-10 : protocole déclaré avant tout calcul sur données réelles (seuls des comptes de candidats synthétiques ont
  été regardés, § 5.4).
- 2026-10-10 : code et tests ; critère de couverture du contrôle H0 dimensionné sur les comptes synthétiques (§ 5.4,
  ligne 4) ; quota départagé par l'identifiant (§ 8, choix 18) ; comptages à blanc (§ 9) : aucun ajustement.
- 2026-10-10 : contrôle sous H0 lancé une fois (commit `fa7b872`) : **les cinq configurations échouent** (biais des
  placebos arrière, § 5.5) ; toutes retirées, 0 essai, étude historique abandonnée ; `executer` refuse. Relecture
  `leak-auditor` toujours demandée (code, contrôle, F19) avant toute autre décision.
- 2026-10-10 : **contrôle H0 n° 1 en échec sur l'excès, à cause du biais des placebos arrière ; critère de décision
  changé sur synthétique seulement, aucune donnée réelle lue** (§ 11, décision du coordinateur par délégation du
  propriétaire) : décision au R net seul (IC 99 % > 0 dans les deux scénarios), placebos descriptifs, contrôle H0 n° 2
  (faux `PISTE` ≤ 0,02, puissance ≥ 0,50 à +0,15 R net) déclaré avant son passage ; F19 jugé sur le R net seul.
- 2026-10-10 : contrôle sous H0 n° 2 lancé une fois (commit `a3f8ff8`) : faux `PISTE` 0/200 pour les cinq configurations,
  puissance à +0,15 R net 0,010 / 0,040 / 0,045 / 0,285 / 0,080 → **`INSTRUMENT_TROP_FAIBLE` partout**, 0 essai
  historique, `executer` refuse ; inscrit dans `CONTROLE_H0`. Relecture `leak-auditor` à faire.
- 2026-10-10 : corrections de la relecture du coordinateur avant le démarrage de F19 : magasin de F15 tenu à jour par
  la surveillance après la fin de F15 tant que F18 ou F19 en dépend ; lecture du direct paire par paire (colonnes utiles
  seulement) ; retard mesuré sur l'horloge réelle ; `pairs_with_close` inscrit ; seuil réel (50 jours) et biais des
  `TROU` déclarés dans la section F19 ; mémoire de la surveillance portée à 3 Gio. Détecteur, gestion et placebos
  inchangés (contrôles H0 toujours valables).
- 2026-10-10 : tests séparés **F20 à F24** pré-inscrits (§ 12, `FORWARD_TESTS.md`) : une configuration par test, mêmes
  règles que F19, quota propre de 5 appels par jour ; F19 inchangé ; non démarrés (relecture du propriétaire d'abord).
