# Combiner les briques : profil de volume, flux d'ordres, vote et filtre des signaux Telegram (déclaré le 2026-10-07, avant tout code, téléchargement et exécution)

**Statut : pré-enregistrement.** Écrit le 2026-10-07, avant tout code, tout téléchargement et tout calcul sur des
données réelles. Aucun résultat de ce programme n'existe ; aucun chiffre ci-dessous n'est une mesure de ces briques,
sauf ceux qui sont cités d'études précédentes (avec leur document). Toute modification ultérieure est datée en bas
(« Historique ») et ne peut porter que sur des points techniques, jamais après avoir vu un résultat de l'étape
concernée.

**Demande du propriétaire (2026-10-07)** : « on doit essayer volume profile et order flow, mais aussi combiner tout ce
qui peut être utile pour augmenter le score ; si une stratégie ne suffit pas, peut-être en combiner 2, 3 ou 4 ». Plan
en 3 étapes validé le même jour. Cette demande rouvre explicitement, pour ce programme seulement, la recherche
directionnelle sur les bougies que le plan du 2026-10-03 avait arrêtée (`PLAN_DE_TRAVAIL.md`).

## 0. En bref, pour le propriétaire

- **Étape 1 — cinq briques nouvelles, une par une** : rebond sur le POC du profil de volume de 30 jours, rebond sur le
  bas de la zone de valeur (VAL), reprise du VWAP ancré au dernier creux, divergence haussière de CVD (prix plus bas,
  flux acheteur plus haut), absorption (forte vente au marché sans baisse). Chacune est jouée seule, avec **la même
  transaction pour toutes** (achat au marché, stop à 2 ATR, sorties par tiers à 1, 2 et 3 R, 60 heures au plus),
  contre des achats au hasard de même géométrie.
- **Étape 2 — les combinaisons** : les 5 briques nouvelles + 4 briques connues (cassure de ligne de tendance 1 h,
  tendance EMA 200, volatilité calme, BTC haussier) votent. Trois seuils de vote (au moins 2, 3 ou 4 votes) et un
  modèle simple (régression logistique, réentraîné chaque année sur le passé seulement). **5 essais**, pas plus.
- **Étape 3 — filtre de tes signaux Telegram** : garder seulement les signaux où au moins 3 (ou 4) briques sur 5 sont
  d'accord (tendance, place du prix dans le profil de volume, flux des dernières 24 h, stop comparé à la volatilité,
  régime de BTC), jouer ta gestion « stop suiveur », et comparer au même nombre de signaux tirés au hasard.
- **Où l'on juge** : étapes 1 et 2 sur les 40 paires de recherche (2019 → 2025-06), puis **confirmation sur les 223
  paires jamais utilisées pour ces briques** (comme les lignes de tendance). La période réservée (depuis le
  2025-07-01) **reste fermée**.
- **Point dur à décider par toi** : tous tes signaux Telegram exportés datent de **2026**, donc de la période
  réservée. L'étape 3 ne peut pas se faire sans l'ouvrir (§ 5.8) : soit tu l'autorises explicitement (comptée comme
  une consultation), soit on la fait **en direct** sur les signaux que le relais reçoit depuis le 2026-10-06.
- **Ce qu'il faut attendre, honnêtement** : les idées voisines déjà testées ont donné « rien » (rebond sur support K1,
  supports et résistances contre niveaux placebo, VWAP journalier H, divergences RSI, sweeps). La seule piste
  confirmée sur d'autres paires (lignes de tendance 1 h, +0,12 R sur le hasard) ne s'est pas retrouvée sur la période
  réservée. Combiner des briques sans information ne crée pas d'information ; un vote peut seulement concentrer un
  peu d'information éparse. Le résultat le plus probable est « rien ». Ce protocole est fait pour que, si quelque
  chose existe, on le voie, et que, si rien n'existe, on ne se raconte pas d'histoire.

## 1. Cadre commun aux étapes 1 et 2

### 1.1 Règles

- **DEVELOPMENT seulement** (jusqu'au 2025-06-30 23:59:59 UTC, `research/protocol.py`, `FROZEN_DEVELOPMENT_END`).
  Aucune donnée postérieure n'est lue, ni téléchargée pour ces étapes.
- **Aucun réglage** : tous les paramètres sont fixés dans ce document, repris de `INDICATEURS.md` quand ils y
  existent, sinon choisis ici a priori ; aucun n'est choisi en regardant un résultat. Aucun paramètre ne bouge entre
  les paires de recherche et les paires de confirmation.
- **Liste fermée** : les 9 briques du § 2 sont les seules. Aucune brique n'est ajoutée, retirée, inversée (sens du
  vote) ou redéfinie après un résultat, quel qu'il soit. Une brique « inverse » à l'étape 1 vote quand même dans le
  sens déclaré à l'étape 2 (le modèle logistique, lui, apprend les signes sur le passé).
- **Tout essai est compté** dans `program_trials` (§ 6). Au 2026-10-07, le registre compte **853 essais** sur
  DEVELOPMENT, **8 essais** sur la période finale et **3 consultations** de la période réservée.
- **Une exécution unique par étape**, après la relecture indépendante (`leak-auditor`), les contrôles du § 1.8 et une
  répétition sur données synthétiques. Une seule tâche lourde à la fois, 4 processus au plus.

### 1.2 Données et univers

- **Paires de recherche (R)** : les 40 paires de `RESEARCH_UNIVERSE` (survivantes, biais déclaré : il gonfle le R
  moyen, beaucoup moins l'excès sur les placebos tirés sur la même paire).
- **Paires de confirmation (C)** : les 223 paires passées au moins une fois par le top 40 à date, hors des 40
  (`trendline_confirmation.universe`, recensement de `UNIVERSE_PIT.md`, paires retirées de la cote comprises) ; 214
  ont des bougies 1 h dans la période. Elles n'ont servi à aucune étude de profil de volume, de VWAP ancré, de CVD ni
  d'absorption. Elles ont servi aux lignes de tendance (`LIGNES_DE_TENDANCE.md`), au criblage K à date, aux
  portefeuilles hebdomadaires et à la grille / DCA : **la brique TRENDLINE n'y est pas neuve** (§ 4.6).
- **Bougies** : 1 h du magasin long (`research/long_history.py`, archives officielles, colonnes `open`, `high`, `low`,
  `close`, `base_volume`, `taker_buy_base_volume`, `available_at`) ; exécution sur les bougies 1 minute du magasin
  minute (déjà téléchargées jusqu'à la fin de DEVELOPMENT pour les 40 + 214 paires ; couverture 99,8 à 100 %). **Aucun
  téléchargement nouveau** n'est nécessaire pour les étapes 1 et 2. Le contrôle de couverture des minutes de
  `trendline_confirmation.coverage` (99 % des heures, fin au plus un jour avant la dernière heure) est repris : une
  paire qui échoue bloque l'exécution (rien n'est compté).
- **Période des déclencheurs** : clôture de la bougie de décision au plus tôt le **2019-01-01** et au moins **90 jours**
  après la première bougie 1 h de la paire ; horizon complet (60 heures) avant la fin de DEVELOPMENT. BTCUSDT est
  lue sur la même période pour la brique BTC.

### 1.3 Grille de décision et causalité

- Une décision possible à chaque **clôture 1 h** `t` de chaque paire. Une brique n'utilise que les bougies 1 h dont
  l'`available_at` précède la décision (clôture + 1 ms + latence), jamais `open_time` pour une jointure. BTC est
  jointe vers le passé sur `available_at`.
- Chaque définition du § 2 dit **à quelle bougie elle est connue**. Un événement « connu à `t` » ne lit aucune bougie
  d'indice supérieur à `t`.
- **Ordre** : posé à la première minute qui suit la clôture de `t` plus 2 secondes de latence (`figures_history`,
  différence 2 avec F15).

### 1.4 Transaction commune (la même pour toutes les briques, combinaisons et placebos)

Chaque brique a sa « façon de jouer » chez les analystes ; ici, **toutes partagent la même transaction**, pour trois
raisons : les votes de l'étape 2 doivent porter sur une même transaction ; un achat au marché est strictement
symétrique avec les placebos (pas d'effet « attendre que le prix revienne à l'ordre limite », pas de correction
maker / taker) ; et aucun paramètre de sortie ne peut être choisi brique par brique.

- **Entrée** : achat **au marché** à l'ouverture de la minute d'exécution (`figures_history.Setup` avec
  `entry=None`, comme `SWEEP` et `RSI_DIV`). L'« entrée » est l'ouverture de cette minute, avant glissement.
- **Stop** : `C_t − 2 × ATR_t` (ATR de Wilder 14 en 1 h à la bougie de décision, `INDICATEURS.md`), posé à la
  détection, fixe. Justification : deux bougies moyennes sous la clôture, hors du bruit d'une heure ; les frais
  aller-retour (≈ 0,21 % en central) y pèsent environ 0,1 R. Fixé a priori, non réglé.
- **Objectifs** : sortie par **tiers** à entrée + 1, 2 et 3 R (R = entrée − stop), posés à l'exécution.
- **Durée** : **60 heures** au plus, comptées en temps depuis l'exécution ; reste vendu à la clôture.
- Minute où le stop et un objectif sont touchés : **stop d'abord** (pas de bougies 1 s en 2019-2025). Minute qui
  ouvre au stop ou dessous avant l'exécution : ordre annulé (propriété reprise de F15). Géométrie invalide (stop à
  moins de 0,1 % sous l'entrée) : écartée, comptée.
- **Frais** : modèle commun (`forward/costs.py`), **central** (7,5 pb par ordre) et **défavorable** (10 pb) ; entrée
  et stop au marché (taker, glissement), objectifs en limite (maker), échéance au marché.
- **R** = résultat net / (entrée − stop) : le risque prévu.
- **Une position par paire et par règle** : un déclencheur qui arrive pendant qu'une transaction de la **même règle**
  (brique seule, ou combinaison) est ouverte sur la même paire est ignoré (compté « position ouverte »). Causal : la
  sortie de la transaction précédente est connue avant `t` ou elle ne l'est pas.
- Paire retirée de la cote avant la fin de l'horizon : reste vendu à la dernière clôture (`COTATION_ARRETEE`, gardée,
  comme dans `LIGNES_DE_TENDANCE.md`).
- Simulation : `figures_history.play` (copie compilée de `f15.simulate`, prouvée identique), importé sans changement.

### 1.5 Placebos et excès

- Pour chaque transaction exécutée : **20 achats au marché** sur la même paire à des minutes tirées uniformément, sans
  remise, sur **toute la période utilisable de la paire** (début des déclencheurs de la paire → fin de ses données
  dans DEVELOPMENT moins 60 heures ; graine déduite de l'identifiant du déclencheur), mêmes distances de stop et
  d'objectifs en pourcentage, même sortie par tiers, même durée, mêmes frais. Minute tirée dans un trou de plus de
  10 minutes : placebo inutilisable. C'est le tirage de `trendline_confirmation.placebo_minutes` /
  `with_uniform_placebos`, réutilisé avec un identifiant d'étude propre.
- **Pas** de placebos tirés dans les 30 jours avant l'exécution : ce tirage est biaisé en 4 h et 1 jour, et il dépend
  du chemin qui forme le déclencheur (contrôle du 2026-10-04, `FIGURES_HISTORIQUE.md`).
- **Excès** = R de la transaction − moyenne des R de ses placebos utilisables. Entrée au marché des deux côtés :
  aucune correction de frais d'entrée n'est nécessaire.
- **Réserve déclarée** : ces placebos lisent l'historique postérieur à la décision ; l'excès mesure l'information du
  moment choisi, pas un gain réalisable. Seule la décision « gain » (R moyen) parle d'argent.

### 1.6 Mesure et intervalles

Pour chaque règle et chaque scénario de frais : transactions exécutées, R net moyen et son intervalle, excès moyen et
son intervalle. Intervalles : tirage par blocs de **28 jours présents** (jours avec au moins une exécution ;
`backtest.metrics.day_block_ci`, 10 000 tirages, graine **20261007**, au moins 10 blocs). Niveaux : § 6.

### 1.7 Garde-fous contre le surajustement (exigés pour une « piste », pas seulement décrits)

Une règle dont l'intervalle de l'excès est au-dessus de 0 n'est une `PISTE` que si, en plus :

1. **au moins 300 transactions exécutées** et 10 blocs ;
2. **sans le meilleur 1 %** des transactions (les 1 % d'excès les plus grands retirés), l'excès moyen reste > 0 ;
3. **concentration** : la paire qui apporte le plus ne pèse pas plus de **25 %** de la somme des excès, l'année qui
   apporte le plus pas plus de **50 %** ;
4. **régularité** : excès moyen > 0 dans au moins **4 des 7 années** (2019 à 2025, 2025 réduite au premier semestre) ;
   pour le modèle logistique, dans au moins **3 des 5 plis** ;
5. **frais défavorables** : la décision est prise en central **et** en défavorable (les deux intervalles au-dessus de
   0) ;
6. pour le modèle logistique, **au moins 100 transactions dans chaque pli compté**, et au moins 4 plis sur 5 qui
   atteignent ce nombre (leçon de `ML_INTRADAY.md` : un pli à 14 transactions ne compte pas) ; sinon `INSUFFISANT`.

Intervalle au-dessus de 0 mais un garde-fou manqué : `PISTE_FRAGILE` (inscrit, non envoyé en confirmation).

### 1.8 Contrôles avant toute exécution sur données réelles

- **Causalité, brique par brique** : recalcul sur données tronquées à des coupures tirées au hasard (et juste après
  la confirmation d'un pivot, juste après 00:00 UTC pour le profil) et avec le futur falsifié (prix et volumes) :
  événements et états connus avant la coupure identiques. **Tests de mutation** qui doivent être détectés : profil
  du jour `d` qui inclut une bougie du jour `d` ; divergence CVD datée à `j2` au lieu de `j2 + 2` ; centile
  d'absorption qui inclut la bougie `t` ; VWAP ancré sur un pivot pas encore confirmé ; EMA de BTC jointe sur
  `open_time`.
- **Contrôle sous l'hypothèse nulle** (données synthétiques seulement, même code de bout en bout) : marches
  aléatoires sans mémoire, avec une part d'achats « taker » tirée indépendamment des rendements, dans les quatre cas
  de `LIGNES_DE_TENDANCE.md` (volatilité constante, régimes de volatilité, tendances par régimes, hausse puis
  chute), au moins 120 marches de 3 ans par cas. Exigé : pour chaque brique, chaque vote et le modèle logistique,
  excès moyen à moins de 2 erreurs types de 0 dans chaque cas. Sinon l'exécution n'a pas lieu tant que l'instrument
  n'est pas corrigé. Résultat inscrit ici avant l'exécution, avec le biais maximal mesuré (une borne basse réelle
  entre 0 et ce biais sera signalée).
- **Contrôle positif** (informatif, non bloquant) : même chose avec un effet injecté (dérive de +0,15 R en moyenne sur
  les 60 heures qui suivent une divergence CVD synthétique) : part des simulations où la brique sort `PISTE`, pour
  connaître la puissance réelle de l'instrument.
- **Comptage avant l'étude** : nombre de déclencheurs par brique (et de votes par seuil) compté avec les seules
  fonctions de détection, sans aucune exécution ni aucun R, inscrit ici avant l'exécution (comme dans
  `FIGURES_HISTORIQUE.md`).

## 2. Les briques (définitions exactes et causales)

Notations de `INDICATEURS.md` : bougies 1 h clôturées `i`, `O, H, L, C, V`, `TB` = volume de base acheté au marché,
`delta_i = 2 · TB_i − V_i` (§ 7). « Événement à `t` » = connu à la clôture de `t`. Une brique non calculable (pas
assez d'historique, trou) ne vote pas (ni oui ni non : elle est absente), et le nombre de cas est donné.

### 2.1 Briques nouvelles (étape 1), toutes de type « événement »

| Brique | Définition | Connue à |
|---|---|---|
| `VP_POC` | **Profil du jour** `P_d` (§ 8 de `INDICATEURS.md`, `patterns.volume.volume_profile`, 50 tranches, zone de valeur 70 %) calculé à 00:00 UTC du jour `d` sur les 720 bougies 1 h ouvertes dans `[d − 30 j ; d)`, au moins 684 présentes ; il sert à toutes les bougies ouvertes le jour `d`. Rebond : `C_{t−1} > haut_POC`, `L_t ≤ haut_POC` et `C_t > haut_POC` (`haut_POC` = borne haute de la tranche du POC). **Un seul événement par paire et par jour.** | `t` |
| `VP_VAL` | Même profil `P_d`. Rebond sur le bas de la zone de valeur : `C_{t−1} > VAL`, `L_t ≤ VAL`, `C_t > VAL`. Un seul événement par paire et par jour. | `t` |
| `AVWAP_RECLAIM` | Ancre `a` = dernier **pivot bas ZigZag** 1 h (m = 3,0 ATR, § 1) **connu au plus tard à `t − 1`** ; VWAP ancré (§ 8, prix typique `(H + L + C)/3`, `patterns.volume.anchored_vwap`) depuis `a`. Reprise : `C_{t−1} ≤ AVWAP_{t−1}` et `C_t > AVWAP_t`, même ancre pour les deux, `t − 1 > a`. **Un seul événement par ancre** (la première reprise). | `t` |
| `CVD_DIV` | Divergence haussière de CVD, miroir exact de la divergence RSI du § 10.4 : deux pivots bas **fractals** consécutifs (k = 2) `j1 < j2`, `5 ≤ j2 − j1 ≤ 60`, `L_{j2} < L_{j1}` et `CVD_{j2} > CVD_{j1}`, c'est-à-dire `Σ delta_i` pour `i` de `j1 + 1` à `j2` **> 0** (indépendant de l'ancre du CVD). | `j2 + 2` |
| `ABSORPTION` | À `t` : part acheteuse `TB_t / V_t` **≤ 10e centile** des parts des 720 bougies précédentes (`t − 720` à `t − 1`, au moins 684 valides) ; `V_t ≥` médiane des `V` de ces mêmes 720 bougies (« forte » vente au marché) ; `H_t > L_t` et `(C_t − L_t) / (H_t − L_t) ≥ 2/3` (clôture dans le tiers haut : pas de baisse). | `t` |

Remarques déclarées :
- le profil est recalculé une fois par jour (et non à chaque heure) : c'est le « profil de 30 jours » demandé, et le
  calcul reste faisable ; une bougie ouverte à 23:00 du jour `d` clôture à `d + 1` 00:00 et utilise encore `P_d` ;
- `CVD_DIV` : la part acheteuse de Binance est souvent un peu sous 0,5 (seuils de F11 vers 0,45 à 0,47) ; une somme de
  `delta` positive entre deux creux est donc plus exigeante qu'un « CVD moins négatif » ; c'est la définition du § 7,
  gardée telle quelle ;
- `ABSORPTION` : 10 % et la médiane sont des seuils a priori (le 10e centile est celui de J2 et de F11, sur 30 jours
  d'heures au lieu de 365 journées) ;
- les idées voisines déjà mesurées : `K1` (rebond sur support confirmé, négatif), `NIVEAUX.md` (rejet / cassure, pile
  ou face), `H` (reprise du VWAP journalier, rien), `RSI_DIV` (perd seule). Elles ne sont pas rejouées.

### 2.2 Briques connues (entrent à l'étape 2 et à l'étape 3)

| Brique | Type | Définition | Connue à |
|---|---|---|---|
| `TRENDLINE` | événement | Cassure haussière d'une ligne de tendance descendante, 1 h, détecteur de F15 (`f15.detect_frame`, § 9.3 d'`INDICATEURS.md`), datée à sa bougie de détection, exactement comme dans `LIGNES_DE_TENDANCE.md`. Seule la date sert : ses règles de transaction propres (retest, objectifs en hauteur) ne sont pas utilisées ici. | bougie de détection |
| `TENDANCE` | état | `C_t > EMA200_t` (EMA des clôtures 1 h de la paire, § 10.4, départ par la moyenne simple des 200 premières). | `t` |
| `VOL_CALME` | état | Variance réalisée horaire moyenne des 24 dernières heures ≤ celle des 720 dernières heures (rendements log horaires, fenêtres couvertes à 95 % au moins, définitions de `VOLATILITY.md` § 3 et § 16). Sens choisi **a priori, sans preuve** : ne pas acheter en pleine flambée de volatilité (krachs, liquidations). | `t` |
| `BTC_HAUSSIER` | état | Clôture de BTCUSDT du dernier **jour UTC complet** (bougies 1 h agrégées, jour complet seulement) > EMA50 de ces clôtures journalières ; connue à la disponibilité de la bougie de 23:00, jointe vers le passé. | `t` |

### 2.3 Écartées ou conditionnelles (déclaré maintenant)

- **Pression vendeuse J2** (part acheteuse de la veille ≤ 10e centile des 365 journées précédentes,
  `flow_screen.daily_flow`) : `PROTOCOL.md` (2026-10-02) dit que ce veto « n'est mesuré qu'en direct (F11), jamais par
  un nouvel essai sur DEVELOPMENT ». **Par défaut, J2 n'entre pas.** Option, seulement si le propriétaire lève
  explicitement cette règle avant l'exécution de l'étape 2 : un veto J2 appliqué aux combinaisons envoyées en
  confirmation, mesuré **sur les paires C seulement** (où J2 n'a jamais été mesuré) : comparaison appariée des mêmes
  transactions avec et sans veto ; « veto justifié » si l'excès des transactions retirées a un intervalle entièrement
  sous 0 ; **1 essai**. F11 continue en direct, sans changement.
- **Marché à terme** (financement, intérêt ouvert) : écarté. Son historique ne couvre pas la période (financement
  depuis 2021-04, intérêt ouvert et comptes depuis 2022-03 environ, téléchargé pour 16 paires seulement ; beaucoup de
  paires C n'ont pas de perpétuel, ou l'ont eu tard). Une brique absente la moitié du temps rendrait le vote
  dépendant de la disponibilité des données. Aucun essai.
- **Prévision de volatilité HAR + profil** (la seule confirmée) : non utilisée aux étapes 1 et 2 (le stop est déjà
  proportionnel à l'ATR) ; l'étape 3 utilise une mesure simple (§ 5.3).
- Toute autre idée (OB, FVG, sweep, harmoniques, niveaux, sessions, etc.) : hors programme. Liste fermée.

## 3. Étape 1 — les briques nouvelles, une par une

**Question.** Chacune des 5 briques du § 2.1, jouée avec la transaction commune, fait-elle mieux qu'un achat au hasard
de même géométrie sur la même paire ?

- **Recherche (R)** : les 5 briques sur les 40 paires. **5 essais**, niveau **1 − 0,05/5** (99 %).
- Décision par brique :
  - `PISTE` : intervalle de l'excès entièrement au-dessus de 0 en central **et** en défavorable, et les garde-fous du
    § 1.7 ;
  - `PISTE_FRAGILE` : intervalle au-dessus de 0, garde-fou manqué ;
  - `INVERSE` : intervalle entièrement sous 0 dans les deux scénarios ;
  - `INSUFFISANT` : moins de 30 transactions ou intervalle non calculable ;
  - `RIEN` sinon.
  - À part, le gain : `GAIN_DEMONTRE` si l'intervalle du R moyen est au-dessus de 0 dans les deux scénarios,
    `PERTE_DEMONTREE` s'il est en dessous, `GAIN_NON_DEMONTRE` sinon.
- **Confirmation (C)** : chaque brique `PISTE` sur R est rejouée **sans aucun changement** sur les paires C.
  `m₁` = nombre de briques envoyées (0 à 5) ; **m₁ essais**, niveau **1 − 0,05/m₁**. `CONFIRMEE` si l'intervalle de
  l'excès est entièrement au-dessus de 0 en central et en défavorable, avec les garde-fous 1, 2 et 5 du § 1.7 (la
  concentration est décrite) ; `INVERSE` s'il est sous 0 ; `NON_CONFIRMEE` sinon (non démontré, pas une réfutation) ;
  décision « gain » comme ci-dessus.
- **Descriptif, hors décision** : par année ; paires cotées contre retirées (C) ; part des issues (stop, TP1, TP2,
  TP3, échéance) contre les placebos (biaisée sous les régimes de volatilité, pour mémoire) ; transactions ignorées
  « position ouverte » ; part des déclencheurs quand `TENDANCE` est vraie ; paire et année qui apportent le plus et
  résultat sans elles ; quand la paire est dans le top 40 du mois (C).
- **Quoi qu'il arrive** (rien, inverse, piste) : les 5 briques entrent à l'étape 2 telles quelles.

## 4. Étape 2 — les combinaisons

### 4.1 Déclencheurs et votes

- **Déclencheur** : à la clôture `t`, au moins une brique « événement » (5 nouvelles + `TRENDLINE`) se produit. Deux
  événements à la même heure sur la même paire = un seul déclencheur.
- **Votes à `t`** : chaque brique événement vote « oui » si elle s'est produite dans les **24 dernières heures** (de
  `t − 23` à `t`, la brique qui déclenche comprise) ; chaque brique état vote « oui » si elle est vraie à `t`.
  9 votants au plus. Une brique absente (§ 2) ne vote pas.
- Les votes ont tous le même poids. Le sens de chaque vote est celui du § 2, jamais retourné.

### 4.2 Les 5 règles (5 essais, et pas plus)

| Règle | Achat au déclencheur si… |
|---|---|
| `REF_TOUS` (référence) | toujours (aucun filtre : tous les déclencheurs) |
| `VOTE_2` | au moins 2 votes |
| `VOTE_3` | au moins 3 votes |
| `VOTE_4` | au moins 4 votes |
| `LOGIT` | le modèle logistique (§ 4.3) prévoit une probabilité de gain au-dessus du taux de base de son entraînement |

Toutes jouent la transaction commune (§ 1.4), avec la règle « une position par paire » propre à chacune.

### 4.3 Le modèle logistique

- **Lignes** : tous les déclencheurs du § 4.1 (sans la règle « une position par paire »), chacun avec sa transaction
  commune simulée. **Cible** : `y = 1` si le R net en frais centraux est > 0.
- **Variables** : les 9 votes (0 ou 1), et rien d'autre ; une brique absente vaut 0 et une variable « absente » par
  brique concernée est ajoutée si une brique manque sur plus de 1 % des lignes (déclaré maintenant, sans regarder les
  données).
- **Modèle** : régression logistique L2, `C = 1,0`, solveur `lbfgs`, 1 000 itérations au plus (`scikit-learn`, valeurs
  par défaut), sans interaction, sans recherche d'hyperparamètres.
- **Walk-forward annuel** : réajustement le **1er janvier** de chaque année `Y`, fenêtre croissante, sur les lignes
  dont la **sortie** est antérieure au 1er janvier de `Y` (purge : aucune issue d'entraînement ne chevauche le test).
  Plis de test : **2021, 2022, 2023, 2024, 2025 (premier semestre)** = 5 plis ; premier entraînement sur 2019-2020.
  Au moins 2 000 lignes et les deux classes, sinon le pli n'a pas de modèle (compté en échec, jamais exclu).
- **Décision** : acheter si `p̂ >` taux de lignes gagnantes de l'entraînement (un seul seuil, fixé ainsi, sans
  optimisation), puis règle « une position par paire » sur les achats retenus.
- **Sur les paires C** : le modèle du pli `Y` est celui ajusté sur les paires **R** (lignes sorties avant le
  1er janvier de `Y`), appliqué tel quel aux déclencheurs des paires C de l'année `Y` : hors échantillon à la fois
  dans le temps et dans les paires.
- Descriptif : coefficients par pli (signe et stabilité), AUC et score de Brier contre le taux de base, part des
  déclencheurs retenus.

### 4.4 Décision sur les paires de recherche

- **5 essais**, niveau **1 − 0,05/5**. Mêmes catégories qu'à l'étape 1 (`PISTE`, `PISTE_FRAGILE`, `INVERSE`,
  `INSUFFISANT`, `RIEN` ; gain à part), mêmes garde-fous (§ 1.7).
- Les votes sont jugés sur 2019 → 2025-06 ; `LOGIT` sur ses 5 plis (2021 → 2025-06). Descriptif : les votes sur
  2021 → 2025-06, pour comparer à `LOGIT` sur la même période.
- **Le filtre doit apporter quelque chose** (critère 7 de `PROTOCOL.md`) : une règle `VOTE_k` ou `LOGIT` n'est une
  `PISTE` que si son excès moyen est aussi **plus grand que celui de `REF_TOUS`** (estimation ponctuelle ; écart et
  son intervalle donnés). Sinon, ce qui marche est le déclencheur, pas le vote.

### 4.5 Confirmation sur les paires jamais utilisées

- Chaque règle `PISTE` sur R (0 à 5) est rejouée **sans aucun changement** sur les paires C. `m₂` règles envoyées :
  **m₂ essais**, niveau **1 − 0,05/m₂**. Décision comme au § 3 (`CONFIRMEE`, `NON_CONFIRMEE`, `INVERSE`,
  `INSUFFISANT` ; gain à part), avec en plus le § 4.6.
- Option J2 (§ 2.3), seulement sur décision explicite du propriétaire avant l'étape 2 : **1 essai**.

### 4.6 TRENDLINE n'est neuve nulle part

`TRENDLINE` a été remarquée sur les 40 paires (après coup) puis mesurée sur les paires C (+0,12 R sur le hasard) et
sur la période réservée (non confirmée). Une combinaison qui la contient ne peut donc pas être « confirmée » par
elle seule. Règle ajoutée maintenant : sur C, une règle n'est `CONFIRMEE` que si, en plus, l'excès moyen de ses
transactions **où `TRENDLINE` ne vote pas** est > 0 (estimation ponctuelle, donnée avec son intervalle). Sinon :
`NON_CONFIRMEE` (« la confirmation viendrait de la ligne de tendance déjà connue »).

## 5. Étape 3 — filtrer les signaux Telegram du propriétaire

### 5.1 Question

Garder seulement les signaux de tes groupes où les briques sont d'accord améliore-t-il le **R par signal** (ta
gestion « stop suiveur ») par rapport à garder le même nombre de signaux au hasard ?

### 5.2 Signaux

- **Source** : les trois exports Telegram Desktop (`imports/telegram/ChatExport_2026-10-02 (3|4|5)/result.json` :
  AL-MAHWASHI CRYPTO, IN CRYPTO, LEGEND TRADING), lus par `external.audit.read_telegram_export`, **texte seulement**
  (pas d'images), même parseur (empreinte inscrite), mêmes doublons (par groupe, 7 jours), mêmes refus (illisible,
  déjà mort, déjà joué, périmé, paire sans bougies). Les fichiers du robot (BotHistory, mahwashiVip, IncryptoVip)
  ne sont pas pris : un mois seulement, en double avec les exports, sans les numéros de messages qui montrent les
  suppressions.
- **Compté le 2026-10-07 sans aucun prix ni résultat** : 732 messages lisibles comme signaux, tous de **2026**
  (2 en janvier, 1 en mars, puis 151 en avril, 124 en mai, 83 en juin, 68 en juillet, 150 en août, 138 en septembre,
  15 en octobre), 30 noms de groupes (les plus fréquents : ALMAHWASHI CRYPTO 151, SUHAIB ALMASHHADANI 83, LEGEND
  TRADING 78, HAMZAWY 77, ABOYASEEIN 73, ALAFIFY 58). Après doublons et refus, l'étude du 2026-10-02 en mesurait
  environ 380 ; attendu ici : **400 à 600 signaux mesurables**.
- Un signal n'est mesuré que si sa fenêtre d'entrée (24 h) et ses 30 jours de suivi sont terminés avant la coupure des
  données (fixée au téléchargement, inscrite) ; sinon il est exclu et compté.
- Un signal dont une brique n'est pas calculable (paire cotée depuis moins de 30 jours, trou) est exclu et compté.

### 5.3 Les 5 briques au moment de la réception

Toutes calculées sur les bougies 1 h **clôturées et disponibles avant l'heure de réception** (`date_unixtime`),
jointure sur `available_at` :

| Brique | Vote « oui » si… |
|---|---|
| `TENDANCE` | `C > EMA200` en 1 h de la paire (dernière bougie close), comme au § 2.2 |
| `PROFIL` | l'entrée 1 du signal est **≥ VAL** du profil `P_d` du jour de réception (§ 2.1) : on n'achète pas sous la zone de valeur |
| `FLUX` | somme des `delta` des 24 dernières bougies 1 h closes > 0 (acheteurs au marché majoritaires sur la journée) |
| `STOP_VOL` | distance du stop `ln(entrée 1 / stop)` **≥ σ24**, avec `σ24 = √(24 × moyenne des rendements log horaires au carré des 168 dernières heures)` : le stop est au-delà du mouvement ordinaire d'une journée. Mesure simple (composante hebdomadaire du HAR), pas la prévision HAR + profil confirmée, déclaré |
| `BTC_HAUSSIER` | comme au § 2.2 |

### 5.4 Filtres (2 essais)

- `FILTRE_3` : garder le signal si **au moins 3 briques sur 5** votent oui.
- `FILTRE_4` : garder le signal si **au moins 4 briques sur 5** votent oui.

### 5.5 Gestion et mesure

- **Ta gestion par défaut** (`external/trailing.py`, `[external] management = "stop_suiveur"`) : ordre limite à
  l'entrée 1 valable 24 h ; ventes aux 5 premiers objectifs (33 / 27 / 20 / 13 / 7 %) ; TP1 → stop à l'entrée 1, TP3
  → TP1, TPk → TP(k−2) ; stop remonté appliqué à la bougie suivante ; 30 jours au plus ; bougies 15 min clôturées
  après la réception ; frais centraux et défavorables.
- **R par signal** = résultat net / (entrée 1 − stop du signal) ; un signal **jamais rempli vaut 0 R** (le filtre
  décide avant de savoir s'il sera rempli : aucune sélection sur l'avenir).
- **Gain du filtre** = R moyen des signaux gardés − R moyen de tous les signaux de la même partie.

### 5.6 Placebo et partie tenue à l'écart

- **Partie d'étude / partie tenue à l'écart, fixées maintenant** : pour chaque groupe, signaux mesurables triés par
  heure de réception ; les **30 % les plus récents** (arrondi vers le bas) forment la partie tenue à l'écart ; un
  groupe de moins de 4 signaux va entièrement dans la partie d'étude.
- **Placebo** : 10 000 sous-ensembles tirés au hasard **dans la même partie**, avec **le même nombre de signaux par
  groupe** que le filtre en garde (un filtre ne peut pas gagner en choisissant simplement le meilleur groupe) ;
  p = (1 + nombre de tirages dont le R moyen est ≥ celui du filtre) / (1 + 10 000). Graine 20261007. C'est
  équivalent à permuter les votes des briques entre signaux d'un même groupe.
- **Décision** :
  - partie d'étude : `FILTRE_PISTE` si p ≤ **0,05/2** (2 filtres), au moins 30 signaux gardés sur au moins 10 jours ;
  - partie tenue à l'écart, seulement pour un filtre `FILTRE_PISTE` : `FILTRE_CONFIRME` si p ≤ **0,05** (unilatéral,
    le sens étant fixé par la partie d'étude) et au moins 20 signaux gardés ; `INSUFFISANT` sous 20 ;
    `NON_CONFIRME` sinon ;
  - `FILTRE_NEFASTE` (descriptif) si p ≥ 0,975 dans la partie d'étude : le filtre écarte plutôt les bons signaux ;
  - `RIEN` sinon.
- **Même un `FILTRE_CONFIRME` ne rend pas les signaux rentables** : le R moyen des signaux gardés est donné avec son
  intervalle (blocs de 7 jours de réception, 10 000 tirages) ; `GAIN_DEMONTRE` seulement si cet intervalle est
  au-dessus de 0 en central et en défavorable, dans la partie tenue à l'écart.
- Descriptif : chaque brique seule (sans décision) ; part de signaux gardés par groupe ; taux de remplissage gardés
  contre écartés ; résultats en frais défavorables.

### 5.7 Puissance de l'étape 3 (réaliste)

Hypothèse : écart-type du R par signal de l'ordre de 1 R (pertes vers −1 R, gains jusqu'à +2 à +3 R). Avec environ 300
à 420 signaux dans la partie d'étude et un filtre qui en garde 30 à 50 %, l'erreur type du gain du filtre est
d'environ 0,07 R : seul un gain d'au moins **0,2 R par signal** a de bonnes chances d'être vu ; dans la partie tenue à
l'écart (120 à 180 signaux), d'au moins **0,3 R**. Les signaux d'un même jour suivent le même marché : la puissance
réelle est plus faible. Un `RIEN` voudra dire « pas d'effet de cette taille », pas « aucun effet ».

### 5.8 L'étape 3 touche la période réservée : décision du propriétaire

Tous les signaux exportés datent de 2026, **dans la période réservée** (depuis le 2025-07-01). Calculer les briques au
moment de ces signaux et mesurer leur effet, c'est juger des règles de CSI sur cette période. Deux voies, au choix du
propriétaire, **avant** tout calcul de l'étape 3 :

- **A — consultation déclarée** : l'étape 3 est inscrite comme consultation de la période réservée (registre
  `final_test_consultations`, `--i-understand-final-test`), restreinte aux instants des signaux Telegram, **2 essais**
  sur la période finale. Ensuite, la période réservée ne peut plus juger neutrement les briques de l'étape 3
  (`TENDANCE`, profil, flux, `BTC_HAUSSIER`). Si une lecture finale d'une combinaison de l'étape 2 est envisagée
  (§ 8), elle doit avoir lieu **avant** l'étape 3. Téléchargement nécessaire : bougies 1 h (avec volume taker) et
  15 min des paires des signaux, de 2025-12 à la coupure.
- **B — en direct** : pré-inscrire le même filtre (§ 5.3 à 5.6, mêmes seuils) comme test en direct sur les signaux
  que le relais Telegram dépose depuis le 2026-10-06 (entrée et gestion du § 5.5 ; F4, lui, achète au premier prix),
  dans un **nouveau module** (F4 et F16 sont gelés et ne changent pas). Aucune donnée réservée n'est lue ; il faut plusieurs mois pour 300 signaux.

Sans réponse du propriétaire, l'étape 3 n'est pas exécutée.

## 6. Essais et comparaisons multiples

| Étape | Données | Essais | Niveau de chaque intervalle |
|---|---|---|---|
| 1 — briques seules | paires R, DEVELOPMENT | 5 | 1 − 0,05/5 |
| 1 — confirmation | paires C, DEVELOPMENT | m₁ (0 à 5) | 1 − 0,05/m₁ |
| 2 — combinaisons | paires R, DEVELOPMENT | 5 | 1 − 0,05/5 |
| 2 — confirmation | paires C, DEVELOPMENT | m₂ (0 à 5) | 1 − 0,05/m₂ |
| 2 — option J2 (si levée par le propriétaire) | paires C, DEVELOPMENT | 1 | 1 − 0,05 |
| 3 — filtre Telegram (voie A) | signaux de 2026, période réservée | 2 | p ≤ 0,05/2, puis p ≤ 0,05 sur la partie tenue à l'écart |

- **Au plus 21 essais sur DEVELOPMENT** (853 → 874 au plus) et 2 sur la période finale (voie A). Chaque exécution
  enregistre son `program_trials`.
- **Correction déclarée** : Bonferroni à l'intérieur de chaque ligne du tableau ; la protection contre les ~870 essais
  du programme ne vient pas d'un seuil (0,05/870 serait hors d'atteinte), mais de la **confirmation sur des paires
  jamais utilisées**, avec des règles figées avant de les voir. Une `PISTE` sur les 40 paires seules n'est qu'un
  indice.

## 7. Puissance des étapes 1 et 2 (réaliste)

- **Nombre d'événements** (ordre de grandeur estimé sans données ; le vrai comptage sera inscrit avant l'exécution,
  § 1.8) : sur les 40 paires, quelques milliers à quelques dizaines de milliers de déclencheurs par brique nouvelle
  (pour comparaison, `RSI_DIV` 1 h : 13 966 ; `TRENDLINE` 1 h : 1 698 ; un rebond « un par jour au plus » sur 40 paires
  et 6,5 ans ne peut pas dépasser environ 95 000) ; la règle « une position par paire » en retire une partie. Les
  paires C donnent environ 3 à 4 fois plus d'événements (6 628 déclencheurs `TRENDLINE` 1 h contre 1 698).
- **Précision attendue** : sur les paires C, avec 5 600 transactions, l'intervalle à 97,5 % de l'excès des lignes de
  tendance faisait ±0,075 R. À 99 % et avec 2 000 à 10 000 transactions, compter **±0,07 à ±0,15 R**. Donc seul un
  excès d'environ **0,1 R par transaction ou plus** a une chance raisonnable d'être vu ; c'est l'ordre de grandeur de
  la meilleure piste du programme (+0,12 R), et plus que tout ce que les niveaux, supports et VWAP ont montré.
- **Frais** : avec un stop à 2 ATR (1,5 à 2,5 % pour une altcoin en 1 h), l'aller-retour central coûte environ 0,1 R ;
  les placebos le paient aussi. Pour un gain, il faut un excès supérieur à ce que les placebos perdent (−0,04 à
  −0,08 R dans l'étude des lignes de tendance).
- **Le modèle logistique** n'est jugé que sur 4,5 ans (2021 → 2025-06) : moins de transactions, intervalle plus
  large.
- Les transactions d'un même jour sur des paires corrélées ne sont pas indépendantes : les blocs de 28 jours en
  tiennent compte, et l'intervalle s'élargit d'autant.

## 8. Lectures déclarées et suites

- **Rien** (aucune brique ni combinaison `CONFIRMEE` sur C) : le profil de volume et le flux approché par les bougies
  n'apportent pas d'information mesurable, seuls ou votés, avec cette transaction. Aucun usage, aucune nouvelle
  brique ajoutée « pour voir » : ce serait un nouveau programme, à déclarer et à compter.
- **Piste** (`PISTE` sur R, `NON_CONFIRMEE` sur C) : hasard probable sur 40 paires ; aucun usage.
- **Confirmée** (`CONFIRMEE` sur C) : la règle contient de l'information sur des paires jamais vues. Sans
  `GAIN_DEMONTRE`, elle ne paie pas les frais. Avec `GAIN_DEMONTRE` : candidate à un **test en direct pré-inscrit**
  (nouveau module, signaux shadow), jamais présentée comme rentable avant.
- **La période réservée reste fermée.** Elle a déjà été consultée trois fois (volatilité, lignes de tendance, IA
  locale) et sert de période de validation pour tout le programme ; pour une règle qui contient `TRENDLINE`, elle
  n'est plus un juge neutre (`LIGNES_DE_TENDANCE.md`). Une **lecture unique**, plus tard, de la seule meilleure règle
  confirmée (la plus grande borne basse de l'excès sur C, règle fixée maintenant) n'aura lieu que sur **décision
  explicite du propriétaire**, avec son propre pré-enregistrement, une répétition sur DEVELOPMENT et une relecture.
  La voie recommandée reste le test en direct, sur des données que personne n'a encore vues.
- Ce que ce programme ne mesure pas : la rentabilité d'un portefeuille (taille, corrélations entre paires, capital
  immobilisé), l'exécution réelle sur Binance Demo, les signaux en image.

## 9. Mise en œuvre prévue (après la relecture de ce document)

- Nouveau module `research/combinations.py` (briques, votes, modèle, mesures), tests `tests/test_combinations.py`
  (données synthétiques seulement : causalité, mutations, hypothèse nulle, exemples vérifiables à la main de chaque
  brique), commandes `csi combinaisons-briques`, `csi combinaisons-votes`, `csi combinaisons-telegram`.
- **Importés sans changement** : `patterns/volume.py`, `patterns/primitives.py`, `patterns/indicators.py`,
  `forward/f15.py` (gelé), `research/figures_history.py`, `research/trendline_confirmation.py`,
  `research/flow_screen.py`, `external/audit.py`, `external/trailing.py`, `backtest/metrics.py`. Aucun module gelé
  d'un test en direct n'est modifié (`tests/test_frozen_running_tests.py`).
- Registre : types `COMBO_BRIQUES`, `COMBO_VOTES`, `COMBO_TELEGRAM` ; empreintes des bougies et du parseur ; commit
  propre exigé.
- Ordre : code et tests → relecture `leak-auditor` → contrôles du § 1.8 inscrits ici → comptage des déclencheurs
  inscrit ici → exécution unique de l'étape 1 → étape 2 → étape 3 selon la décision du § 5.8.

## Historique

- 2026-10-07 : déclaré avant tout code, tout téléchargement et toute exécution (demande et plan du propriétaire du
  même jour). Seul calcul fait sur des données réelles : le comptage des messages Telegram lisibles par mois et par
  groupe (§ 5.2), sans aucun prix ni résultat ; et la lecture du registre (853 essais sur DEVELOPMENT, 8 sur la
  période finale, 3 consultations).
