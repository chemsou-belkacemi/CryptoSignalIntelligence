# Météo du marché : jouer ou ne pas jouer (déclaré le 2026-10-08, avant tout code, téléchargement et calcul)

**Statut : pré-enregistrement.** Écrit le 2026-10-08, avant tout code, tout téléchargement et tout calcul sur des prix.
Révisé deux fois le même jour, toujours avant tout code et tout calcul : deux relectures `leak-auditor` (verdict « à
corriger » les deux fois, aucune fuite nouvelle à la deuxième) et décision du propriétaire (voir « Historique »).
**La marge Δ_min (§ 5.6) reste à valider par le propriétaire.** Aucun résultat de ce programme n'existe. Les chiffres qui suivent ont
trois origines possibles : ils sont cités d'études précédentes (avec leur document), ce sont des hypothèses déclarées
comme telles, ou ils viennent de lectures de métadonnées (registre des essais, dates de cotation), sans aucun prix.
Toute modification ultérieure est datée en bas. Elle ne peut porter que sur des points techniques, et jamais après
avoir vu un résultat de l'étape concernée.

**Demande du propriétaire (2026-10-08).** Avant de chercher quoi acheter, savoir s'il faut jouer du tout : « ces
jours-ci le marché est merdique, tout le monde perd, donc le conseil général doit dire de ne pas prendre de risque ».
Achat seulement (Spot, pas de vente à découvert, cadre halal). Trois sorties : **vert** (on peut jouer), **orange**
(taille réduite, seulement les meilleurs signaux), **rouge** (rien). L'avis doit tenir compte de plusieurs données à la
fois : structure de BTC et du marché, volatilité, sentiment, positionnement des dérivés, news, liquidité, résultats
récents des signaux.

**Décision du propriétaire (2026-10-08, après la relecture) : « Les deux ».**
1. Un **feu de protection** est mis en service tout de suite, **en dehors de cette étude**. C'est un outil de gestion du
   risque, sans aucun gain annoncé, construit par un autre chantier.
2. **Cette étude**, corrigée pour devenir **informative** : une seule question décisive, un critère d'équivalence pour
   qu'un résultat nul dise quelque chose, et une période étendue à la baisse de 2018.

**Lu avant d'écrire** : `FORWARD_TESTS.md` (F0, F3, F5, F8, F9, F12, VOTE_V1), `CONTEXTE.md`,
`CONTEXTE_PREDICTION.md`, `SCREENING.md` (J2, J4, J5, K), `DERIVATIVES.md`, `VOLATILITY.md` (§ 16 et § 19),
`RISK_SHADOW.md`, `LONG_HORIZON.md` (§ 11), `TREND_DAILY.md`, `COMBINAISONS.md`, `UNIVERSE_PIT.md`, `PROTOCOL.md`,
`research/protocol.py`, et le registre `experiments/` en lecture seule.

**Résultats des combinaisons (`CMB1-20261008T182200Z-db1ea4`, `CMB2-20261008T182516Z-a2e56a`).** Ils n'avaient pas
été lus pour la première version de ce document. Au moment de cette révision, ils sont **connus** : le propriétaire
les connaît, et le coordinateur les a résumés (« rien » partout). Ils n'ont eu **aucune influence sur les
composantes** : aucune n'a été ajoutée, retirée ni changée à cause d'eux. Toutes les composantes et tous les seuils du
§ 3 sont ceux de la première version (commit `0d845bb`), écrite avant que ces résultats soient connus. Seules leurs
définitions techniques ont été corrigées, à la demande de la relecture.

## 0. En bref, pour le propriétaire

- **Ce que c'est.** Chaque jour à 00:10 UTC, un feu calculé avec ce qu'on sait à cette heure-là. On compte combien de
  « dangers » sont allumés parmi 6 :
  1. BTC clôture sous sa moyenne de 50 jours ;
  2. moins d'un tiers du top 40 du moment clôture au-dessus de sa propre moyenne de 50 jours ;
  3. la volatilité prévue de BTC pour les 24 h est dans les 10 % les plus hautes de l'année écoulée ;
  4. le Fear & Greed est sous 25 (« peur extrême ») ;
  5. le financement des perpétuels BTC est « chaud » (plus de 0,05 % par 8 h en moyenne sur 7 jours) ;
  6. les achats faits au hasard la semaine passée ont été parmi les 20 % pires de l'année (« tout le monde perd »).

  **0 ou 1 danger : vert. 2 : orange. 3 ou plus : rouge.** Rien n'est réglé : ces seuils sont écrits ici et ne bougent
  plus.
- **Une seule question décide.** Sur 2020 → mi-2025, dans un même trimestre, les jours rouges sont-ils pires que des
  jours tirés au hasard dans ce même trimestre ? On le mesure sur un panier égal du **top 40 du moment** (paires
  retirées de la cote comprises) tenu 24 h, sans frais. Chaque rendement est divisé par la volatilité prévue de la
  paire. Tout le reste est du descriptif : pire perte, achats au hasard en R, résultats après frais, composante par
  composante.
- **Ce que vaut le feu se mesure par jour sauté.** Si les jours rouges sont pires que les autres jours d'un écart
  **Δ**, chaque jour rouge sauté évite cet écart. Δ ne dépend pas du nombre de jours rouges.
- **Un « rien » dira quelque chose, si tu fixes la marge.** Il faut décider maintenant à partir de quel écart Δ
  « ça vaudrait la peine ». **C'est toi qui choisis**, dans le tableau du § 5.6 : environ 0,2 %, 0,4 % ou 0,6 % par
  jour sauté pour une altcoin typique. Si le résultat est nul, la conclusion sera « les jours rouges ne sont pas pires
  de plus de X % par jour ». Ce chiffre X sera donné **dans tous les cas**.
- **Attention** : avec 5 ans d'historique, on ne peut pas certifier qu'un écart de 0,2 % par jour (le prix d'un
  aller-retour) est absent. L'instrument est trop peu précis pour ça. Une marge aussi petite ferait arrêter l'étude
  avant de la lancer (§ 7.2), sans rien compter.
- **Le piège.** Un feu qui dit rouge quand le marché baisse a toujours l'air génial après coup. Il saute 2022 et garde
  2021. N'importe quelle moyenne mobile le ferait, et 5 ans ne contiennent que deux grands cycles. C'est pour ça que la
  comparaison se fait **dans le même trimestre** : le feu doit choisir les mauvais jours **à l'intérieur** d'une
  période, pas seulement deviner que 2022 était baissière. On ajoute aussi une variante qui part de **2018**, pour
  avoir une deuxième grande baisse.
- **Le feu de protection et cette étude, c'est différent.** Le feu de protection tourne déjà. C'est une ceinture de
  sécurité, pas une conclusion. Cette étude mesurera ce qu'il vaut, s'il suit la même règle (§ 1).
- **Ce qu'on sait déjà.** Le sens du marché n'a jamais été prévu dans ce projet (873 essais). Données de contexte,
  marché à terme, sentiment, MVRV, filtres de tendance, combinaisons : rien ne bat le hasard. La seule chose démontrée,
  c'est l'**ampleur** des mouvements à 24 h, pas leur sens. Ajuster la taille selon cette ampleur (`RISK_SHADOW.md`)
  reste le seul outil de risque démontré.
- **Ce qu'on peut espérer, honnêtement.** Le résultat le plus probable est « le feu vaut au plus X », ou « ni démontré
  ni exclu ». La vraie preuve viendra du **temps** : le test en direct F17 tourne plusieurs années, avec des points
  d'arrêt fixés d'avance.
- **Coût en essais.** 2 essais sur DEVELOPMENT (la question principale et sa variante 2018), 5 au plus. La période
  réservée reste fermée. 1 essai au registre des tests en direct pour F17.
- **Ce qu'on te demande.**
  1. Valider ce protocole.
  2. **Choisir la marge Δ** (§ 5.6, tableau) : tant qu'elle n'est pas choisie, rien ne s'exécute.
  3. Valider le téléchargement de l'historique du Fear & Greed : même source et même adresse que le relevé quotidien
     déjà validé le 2026-10-02, en un seul appel.
  4. Plus tard, décider du modèle appris (§ 9) et du démarrage de F17 (§ 11).

## 1. Le feu de protection et cette étude : la différence

- **Le feu de protection** (autre chantier, en service dès maintenant) est un **outil de gestion du risque**. Il
  propose de réduire ou de suspendre les achats certains jours. Il n'annonce aucun gain, et sa mise en service **n'est
  pas une conclusion de cette étude** : il n'est démontré par rien.
  - C'est une précaution que le propriétaire choisit en connaissant son coût possible : rater des jours qui auraient
    été bons.
  - Son affichage doit le dire (« protection, non démontrée »).
- **Cette étude** mesure la règle du § 4, figée ici.
  - **Si le feu de protection utilise exactement cette règle** (mêmes composantes, mêmes seuils, même heure), l'étude et
    F17 diront **ce qu'il vaut** : utile, sans effet utile (au plus Δ), nuisible (`INVERSE`), ou on ne sait pas.
  - **S'il utilise une autre règle**, cette étude ne dit rien de lui, et il faudrait le mesurer à part.
- **Aucune influence dans un sens comme dans l'autre.**
  - L'existence du feu de protection ne change rien à ce protocole : ni composante, ni seuil, ni mesure.
  - Ses résultats en service ne sont pas des résultats de cette étude.
  - Les achats que mesure F17 sont des achats génériques tirés au hasard et les signaux reçus par le relais. Ils ne
    dépendent pas de ce que le propriétaire fait en suivant le feu de protection.

## 2. Ce qui existe déjà, et où se place la météo

| Existant | Ce qu'il fait | Rapport avec la météo |
|---|---|---|
| **F5_MODELE_A**, feu tricolore (en direct depuis le 2026-10-02) | Feu par **événements**. ROUGE si Fed, CPI ou NFP le jour même (calendrier figé d'octobre 2026 à janvier 2027), prévision de volatilité de BTC **à 7 jours** dans les 10 % les plus hautes de 365 jours, financement BTC sur 7 jours au-dessus de 0,05 % par 8 h, ou USDT / USDC à plus de 0,5 % de leur parité. ROUGE par actif s'il y a une annonce de retrait de la cote. ORANGE le week-end, le dernier vendredi du mois, ou en cas de maintenance. Appliqué au seul modèle A, **descriptif**, sans seuil de performance, 12 semaines. | La météo reprend deux seuils de F5 (financement à 0,05 %, rang de volatilité dans les 10 % les plus hauts), mais avec la **seule prévision confirmée** (24 h, HAR + profil) au lieu de celle à 7 jours, qui n'est pas confirmée. Elle ajoute l'état du marché. Elle laisse de côté ce qui n'a pas d'historique ou n'est pas un état du marché. Elle est **jugée**, sur l'historique puis en direct. F5 n'est pas touché. F17 relit son feu en lecture seule. |
| **VOTE_V1** (défini, non évalué) | Vote de composants qui auront passé leur seuil en direct. | Indépendant, non modifié. |
| **RISK_SHADOW** | Ampleur prévue à 24 h, taille relative à risque égal. | Même famille de prévision. Le dimensionnement par la volatilité reste l'outil démontré. |
| **CONTEXTE_PREDICTION** (`CTXP-20261004T015026Z-30e516`) | Flux, primes, croissance des stablecoins, top traders, Wikipédia contre la direction de BTC. | **RIEN** sur 21. Les stablecoins restent hors règle (§ 3.2). |
| **SCREENING J4 / J5**, **J2** | MVRV ; pression vendeuse. | Rien ; J2 mesurée par F11 seulement. Hors météo. |
| **DERIVATIVES** (`SCREEN-20261001T111500Z-60702b`) | Positionnement à terme. | **AUCUNE_PISTE**. Le financement chaud entre comme danger (sens opposé, repris de F5). Intérêt ouvert écarté. |
| **F3**, **F8**, **F0** | Émissions, news, relevés quotidiens. | Historique trop court : relevés en descriptif par F17. |
| **LONG_HORIZON § 11**, **TREND_DAILY § 8**, **COMBINAISONS** | Filtres de tendance, briques et votes. | Leçons reprises (§ 3.4) : exposition égale, placebos appariés, part « régime » confirmable seulement dans le temps, contrôles synthétiques, comptages avant exécution. |

## 2 bis. Règles communes (rétablies de la première version, commit `0d845bb`)

- **DEVELOPMENT seulement**, jusqu'au 2025-06-30 23:59:59 UTC (`research/protocol.py`, `FROZEN_DEVELOPMENT_END`).
  Toute série plus longue sur disque (contexte, dérivés, Fear & Greed, recensement du top 40) est coupée à la lecture.
  Un test vérifie que falsifier tout ce qui suit la coupure ne change rien.
- **Aucun réglage.** Les composantes, leurs seuils, la règle du feu, les mesures et les placebos sont fixés ici. Rien
  ne bouge après les comptages ni après un résultat, quel qu'il soit. **Liste fermée** : aucune composante ajoutée,
  retirée ou inversée. La seule valeur encore ouverte est la marge Δ (§ 5.6). Elle est choisie par le propriétaire
  **avant** les contrôles synthétiques et avant toute donnée réelle, puis inscrite ici.
- **Tout essai est compté** dans `program_trials` (§ 12). Au 2026-10-08, le registre compte **873 essais** sur
  DEVELOPMENT (62 exécutions), **8** sur FINAL_TEST et **3 consultations** de la période réservée. Ce total sera
  recompté au moment de l'exécution.
- **Empreintes au registre** : chaque exécution inscrit les empreintes des bougies (magasin long, recensement du top
  40), du Fear & Greed, du financement, des prévisions recalculées, du code (commit propre et relu) et de la
  configuration effective, avec `n_trials` et `n_descriptive`.
- **Exécution unique par type** (§ 15), après la relecture `leak-auditor` du code, les contrôles du § 7 et les
  comptages inscrits. **Une seule tâche lourde à la fois**, 4 processus au plus.
- **Cadre halal.** Achats Spot seulement. Les données du marché à terme servent d'information. Aucun ordre, aucune
  clé, aucun secret. `data/http.py` n'est pas élargi. **Aucun module gelé d'un test en direct n'est modifié**
  (`tests/test_frozen_running_tests.py`).
- **Seul téléchargement nouveau** : l'historique quotidien du Fear & Greed (alternative.me, `/fng/` avec `limit=0`).
  C'est la même adresse que le relevé F0, déjà dans la liste blanche du client `forward/sources.py` qu'utilise aussi
  `context/`. Il est rangé comme série `HISTORIQUE` du magasin de contexte, avec son empreinte, et il attend l'accord
  du propriétaire.

## 3. Les entrées

### 3.1 Retenues (6 composantes)

**Instant de décision du jour `d`** : `T_d = d 00:10:00 UTC`. Une valeur n'entre que si son `available_at` (ou l'heure
de connaissance déclarée ci-dessous) est ≤ `T_d`. « Jour `d − 1` » = la journée UTC complète qui précède. Clôture
journalière = clôture de la bougie 1 h de 23:00 (`available_at` ≈ `d` 00:00:02), ou bougie journalière publique dont
la clôture + latence ≤ `T_d`. Une journée tirée des bougies 1 h n'est complète que si ses 24 bougies sont présentes.

| Code | Danger si… | Calcul exact | Connu à | Données, depuis |
|---|---|---|---|---|
| `BTC_STRUCTURE` | BTC sous sa moyenne | Clôture journalière de BTCUSDT de `d − 1` **≤** EMA50 des clôtures journalières (α = 2/51, départ par la moyenne simple des 50 premières), soit le contraire exact de `BTC_HAUSSIER` (`COMBINAISONS.md` § 2.2). Jour `d − 1` incomplet : absente. | `T_d` | magasin long 1 h, depuis 2017-08 |
| `LARGEUR` | marché large en baisse | **Top 40 à date** (`research/pit_universe.py`, `UNIVERSE_PIT.md`) : membres du mois de `d − 1`, calculés au 1er du mois sur les 30 journées précédentes, paires retirées de la cote comprises. Bougies journalières publiques de `data/pit/daily.parquet`. Parmi les membres **éligibles** (au moins 50 clôtures journalières jusqu'à `d − 1`, clôture de `d − 1` présente), part de ceux dont la clôture de `d − 1` est **>** leur EMA50 journalière. Danger si la part est **< 1/3**. Moins de **10** membres éligibles : absente (variante 2018 : **5**, § 5.8). | `T_d` | top 40 à date depuis 2018-01 |
| `VOL_HAUTE` | agitation | Prévision **H1_HAR_PROFILE à 24 h** de BTC à l'origine `d` 00:00 (§ 3.3). Elle est **recalculée** pour toutes les origines dont les **entrées** sont complètes, sans aucun filtre sur la cible. Réajustement trimestriel (`fit_at` du protocole v3, importé sans changement). Rang `p` = part, **strictement inférieure**, des prévisions de BTC à 00:00 des 365 jours précédents (`d − 365` à `d − 1`, au moins 300), **calculées par la même instance du modèle** que la valeur du jour. Danger si `p ≥ 0,90`. | `T_d` | prévisions possibles depuis fin 2018 |
| `PEUR_EXTREME` | panique | Fear & Greed d'alternative.me horodaté **`d − 1`** 00:00 UTC. En direct, si cette valeur n'est pas publiée à `T_d`, on prend `d − 2` (inscrit). Danger si la valeur est **< 25**. Absente : composante absente. | voir § 3.3 | depuis 2018-02-01 (à télécharger) |
| `FINANCEMENT_CHAUD` | levier en surchauffe | Moyenne des règlements du perpétuel BTCUSDT (USDⓈ-M) dont l'heure + 1 min est dans `]T_d − 7 j ; T_d]`, au moins 18 sur 21. Danger si elle est **> 0,05 % par 8 h** (seuil de F5 et de `LONG_HORIZON.md` § 3). | règlement + 1 min | archives depuis 2020-01 |
| `PERTES_RECENTES` | « tout le monde perd » | `m_d` = **R brut** moyen (sans frais) des achats génériques `S1` (§ 5.3) dont le **jour d'entrée** est entre `d − 10` et `d − 4`, au moins 70 achats. Tous sont sortis avant `T_d` (§ 3.3). Rang `p` = part, strictement inférieure, des `m` des 365 jours précédents (au moins 300). Danger si `p < 0,20`. | `T_d` | magasin minute des 40 paires, depuis 2019 |

Sens des composantes, fixé **a priori**, sans preuve. La baisse, la largeur faible, la panique, l'agitation, la
surchauffe et les pertes récentes sont des dangers pour un acheteur : c'est l'intuition du propriétaire. L'hypothèse
contraire est connue (les meilleurs jours suivent souvent les pires) : le test est **bilatéral**.

### 3.2 Écartées

| Entrée demandée | Pourquoi elle n'entre pas dans la règle | Où elle reste |
|---|---|---|
| Intérêt ouvert | Archives des altcoins depuis 2021-12 seulement (BTC : 2020-09). Valeurs à zéro et horodatages décalés (`DERIVATIVES.md`). Aucun sens « danger » établi d'avance : la purge se lit plutôt comme un achat. | F9 ; F17 en descriptif |
| Liquidations | Une bourse (OKX), relevée seulement depuis le 2026-10-02. | F17 en descriptif |
| Ratios acheteurs / vendeurs | 30 jours par l'API ; archives de BTC et d'ETH trouées (2021-12-30 → 2022-12-13). | — |
| News (mots-clés de risque) | Collecte depuis le 2026-09-30. | F8, `NEWS.md` ; F17 en descriptif |
| Profondeur des carnets | Relevée depuis le 2026-10-02 (F0). | F17 en descriptif |
| Émissions de stablecoins (F3) | Lecture des chaînes depuis le 2026-10-02. | F3 ; F17 en descriptif |
| Encours des stablecoins (DefiLlama) | Série incomplète avant mi-2020 : elle n'est lue qu'**à partir du 2020-07-01** (`CONTEXTE_PREDICTION.md`). La croissance sur 7 jours (jour `T − 2` contre `T − 9`) existe donc à partir du **2020-07-10**. C'est le rang sur 365 jours de `CONTEXTE_PREDICTION` qui imposait 2021-07 ; il n'est pas utilisé ici. Déjà testée contre la direction de BTC : **RIEN**. | modèle appris (§ 9) ; descriptif |
| Volumes échangés | Sens inconnu d'avance : panique ou euphorie. | modèle appris (§ 9) ; descriptif |
| Parité USDT / USDC, retrait de la cote | Trop rares pour être mesurés ; pas de relevé point-in-time avant 2026-10. | règles de bon sens, hors mesure |
| Calendrier macro, week-end, maintenance | Calendrier de F5 figé d'octobre 2026 à janvier 2027 seulement. Le week-end n'est pas un état du marché. | F5 ; jour de semaine en descriptif |
| DVOL, MVRV, macro | DVOL relevé depuis le 2026-10 et redondant avec la volatilité prévue ; MVRV nul (J4 / J5) ; macro jamais testée. | — |

### 3.3 Causalité, en détail

- **Clôtures** : `available_at` ≤ `T_d`, jamais `open_time`. La journée `d` ne sert jamais à décider le jour `d`.
- **Rangs** : la fenêtre de 365 jours s'arrête à `d − 1`.
- **Prévision H24 (correction de la relecture).**
  - Le fichier du protocole v3 ne garde que les origines dont la cible des 24 h suivantes est complète. Il manque ainsi
    une vingtaine de jours de BTC **pour une raison future** (bougie absente après l'origine). Il n'est donc **pas
    utilisé**.
  - Les prévisions de 00:00 sont **recalculées** pour toutes les origines dont les entrées (variances des 24, 168 et
    720 h, BTC, heure, jour de semaine) sont complètes. Elles sont faites avec les modèles `fit_at` réajustés le 1er de
    chaque trimestre sur les lignes purgées (origine + 24 h ≤ date de réajustement, fenêtre glissante de 3 ans), comme
    en v3.
  - **Mutation exigée** : supprimer ou falsifier les bougies postérieures à `T_d` ne change **ni la présence ni la
    valeur** de la prévision ni de son rang.
  - **Base et valeur, même instance** : la base de rang (365 jours précédents) est recalculée avec l'instance du
    modèle qui donne la valeur du jour. Ces prévisions de la base sont faites par un modèle ajusté après elles, mais
    seulement sur des données antérieures à `T_d` : c'est causal, et c'est la même règle qu'en direct (§ 11). Il n'y a
    donc **pas de saut de niveau à l'intérieur de la base**. En revanche, la valeur du jour peut sauter le jour d'un
    réajustement. Les bascules de `VOL_HAUTE` le jour d'un réajustement sont comptées en descriptif.
  - **Jour de semaine** : le profil heure × jour de la prévision met les week-ends plus bas. `VOL_HAUTE` s'allume donc
    surtout en semaine. Les placebos tournent par multiples de 7 jours (§ 5.4) : la composition par jour de semaine du
    feu est conservée, et cet effet ne peut pas créer d'excès. Un contrôle synthétique le vérifie (`N4`, § 7.2).
  - La prévision couvre 00:00 → 24:00 du jour `d`, la fenêtre du feu va de 00:10 à 00:10 : léger décalage, déclaré.
- **Fear & Greed** :
  - alternative.me horodate chaque valeur à 00:00 UTC de son jour, et son heure réelle de publication n'est pas
    connue. Sur l'historique, la valeur horodatée `d − 1` est supposée connue à `T_d`. En direct, la règle « `d − 2` si
    `d − 1` manque à 00:10 » s'applique.
  - Les journaux F0 (relevé à 00:10 depuis le 2026-10-02) diront quel horodatage est réellement disponible à cette
    heure. C'est un contrôle de **métadonnées**, sans prix ni résultat.
  - **Révisions historiques** : une valeur réécrite après coup ne se voit pas. C'est une **limite déclarée**. Le relevé
    F0 (jamais réécrit) permettra de comparer les jours communs.
  - **Descriptif** : nombre de jours où la valeur est entre **23 et 27**, donc où un jour de décalage ou une petite
    révision pourrait faire basculer la composante.
- **Financement** : règlement connu 1 minute après son heure (`DERIVATIVES.md`).
- **Pertes récentes** : un achat du jour `d − 4` entre au plus tard à `T_{d−3}` et dure au plus 60 h. Il est donc
  sorti au plus tard à `d − 1` 12:10. **Aucune sélection sur la sortie** : tous les achats des jours `d − 10` à `d − 4`
  sont connus. Calcul en **R brut** (sans frais : voir § 5.5).
- **Jour sans feu** : si deux composantes ou plus sont absentes, le jour est `SANS_FEU`. Il sort des mesures et il est
  compté. Il reste un trou de l'index calendaire (§ 5.4).

### 3.4 Composantes déjà regardées sur DEVELOPMENT (déclaré)

Le feu n'est pas indépendant de ce qu'on a déjà vu :
- **Financement à 0,05 %** : c'est le filtre de « A + funding » (`LONG_HORIZON.md` § 11 : actif 5,6 % du temps,
  `NON_INTERESSANT`).
- **Moyennes et filtres de tendance** : filtres de tendance de `TREND_DAILY.md` (Donchian, `NON_INTERESSANT`), vote de
  tendance du modèle A, TSMOM de FACTORS, `TENDANCE` (EMA200 1 h) et `BTC_HAUSSIER` (EMA50 journalière) de
  `COMBINAISONS.md` (rien).
- **Volatilité calme** : `VOL_CALME` de `COMBINAISONS.md`.
- **Top 40 à date** : criblage K à date.
- **Fear & Greed** : jamais mesuré sur l'historique dans ce projet.

Le comportement de ces composantes sur 2019-2025 est donc en partie connu. Le résultat historique de cette étude est
un **indice**. Seul le temps (F17) peut confirmer.

## 4. La règle du feu (fixée, non optimisée)

- `D_d` = nombre de composantes présentes en danger, `A_d` = nombre de composantes absentes.
- `A_d ≥ 2` : `SANS_FEU`. Sinon : **VERT** si `D_d ≤ 1`, **ORANGE** si `D_d = 2`, **ROUGE** si `D_d ≥ 3`.
- Poids égaux, aucun veto seul. Le feu du jour `d` vaut pour tout achat **décidé** entre `T_d` et `T_{d+1}`.
- Pourquoi un comptage : aucun paramètre appris, chacun peut le recalculer à la main, et un danger isolé ne suffit pas.
  « 3 sur 6 » est le premier seuil qui exige que des familles différentes soient d'accord. Il est fixé quelle que
  soit la part de rouge que montreront les comptages.
- **Orange** : demi-taille pour les achats génériques, mesurée en descriptif seulement. « Seulement les meilleurs
  signaux » n'a pas de définition testable sur des achats au hasard.
- **Épisode rouge** : une suite maximale de jours consécutifs de l'index calendaire qui sont ROUGES ou `SANS_FEU`,
  contenant au moins un jour rouge, et bordée par des jours verts ou orange. Les jours `SANS_FEU` à l'intérieur ne
  coupent pas l'épisode, mais ne comptent pas comme jours rouges. Un épisode qui ne contient que des jours `SANS_FEU`
  n'existe pas.

## 5. Ce qu'on mesure (DEVELOPMENT)

### 5.1 Période et blocs

- **Question principale** : du **lundi 2020-02-03 au dimanche 2025-06-22**, soit 1 967 jours = 281 semaines. Avant
  2020-02, le financement et les rangs sur 365 jours ne sont pas pleins. Après, un achat générique de 60 h sortirait de
  DEVELOPMENT.
- **Blocs « trimestre »** : blocs de **13 semaines** (91 jours) qui commencent un lundi, à partir du 2020-02-03. Cela
  donne 21 blocs pleins et un dernier bloc de 8 semaines. Un dernier bloc de moins de 4 semaines serait fusionné avec
  le précédent.
- **Années** (régularité, descriptif) : un bloc appartient à l'année de son lundi de départ.

### 5.2 La question décisive (une seule)

**Dans un même bloc de 13 semaines, les jours rouges sont-ils pires que des jours tirés au hasard dans ce bloc, pour
un achat générique du marché, sans frais, en unités de volatilité prévue ?**

- **Série décisive `S2N`** : chaque jour `d`, achat à parts égales des paires **éligibles** le jour `d`, à
  l'ouverture de la bougie 1 h de `d` 01:00, et vente à l'ouverture de `d + 1` 01:00.
  - **Panier = top 40 à date** (correction de la relecture, contre la survivance) : les membres du mois de `d − 1`
    (`research/pit_universe.py`), paires retirées de la cote comprises.
    - Les membres sans bougies 1 h dans le magasin long (environ 9 paires du recensement) sont exclus et comptés.
    - Les paires de recherche et les paires C ont leurs bougies 1 h dans ce magasin.
  - **Éligibilité** : membre du mois, au moins **90 jours** depuis la première bougie 1 h (règle de FACTORS), ouverture
    de `d` 01:00 présente, et prévision H24 calculable à l'origine `d` 00:00 (entrées complètes, § 3.3).
  - **`MIN_HISTORY_DAYS = 400`** (`research/volatility.py`, ligne 66) **ne s'applique pas** au `σ̂` des paires. C'était
    une règle d'**évaluation** du protocole v3 (origines notées), pas une condition des entrées : `training_rows` et
    `complete_rows` ne l'exigent pas.
    - L'appliquer retirerait du panier les paires jeunes, souvent dans le top 40, et recréerait un biais.
    - Déclaré : la calibration de la prévision pour une paire de moins de 400 jours n'a pas été vérifiée en v3. La
      part du panier dans ce cas est comptée en descriptif.
  - **Rendement brut** (sans frais) de chaque paire, divisé par sa **volatilité prévue** `σ̂_{i,d}`.
  - Résultat du jour `y_d` = moyenne de ces rendements normalisés. **Unité : σ** (« écarts-types prévus »).
- **Définition exacte de `σ̂`** :
  - `σ̂_{i,d} = √(exp(x_{i,d} · β) × s)`. `x · β` est la prévision de log RV²₂₄ du HAR + profil, et `s` le facteur de
    Duan de l'instance (moyenne de exp(résidu) sur l'entraînement, `research/volatility.py`, `Har.predict`). Même
    modèle et mêmes réajustements que pour `VOL_HAUTE`.
  - Le facteur de Duan corrige le biais de exp(E log) pour la **variance**.
  - La racine d'une variance sans biais sous-estime un peu l'écart-type (inégalité de Jensen). C'est un facteur commun
    à toutes les paires d'une même instance, déclaré.
  - Les blocs de 13 semaines ne tombent pas sur les réajustements (1er janvier, avril, juillet, octobre) : un bloc peut
    contenir deux instances, donc un petit changement d'échelle. Déclaré, et compté aux comptages.
- **Univers d'entraînement du modèle** : les **40 paires de recherche** et BTC, comme en v3 (fenêtre glissante de 3 ans,
  lignes purgées). Il est appliqué tel quel aux autres membres du top 40, comme un modèle appliqué hors de son
  univers.
- **Sortie quand une bougie manque (correction de la relecture)** :
  - si l'ouverture de `d + 1` 01:00 manque, vente à la **prochaine ouverture disponible** ;
  - si la paire n'a plus aucune bougie (retrait de la cote), vente à sa **dernière clôture** (`COTATION_ARRETEE`,
    gardée) ;
  - une paire n'est jamais retirée du panier à cause d'une donnée future. Seule l'absence de l'ouverture de `d` 01:00,
    constatée à l'heure de l'achat, fait qu'elle n'est pas achetée ce jour-là ;
  - un test vérifie les trois cas.
- **Politique** : `SANS_ROUGE`, on joue tous les jours sauf les jours rouges. Sa valeur se mesure **par jour rouge
  sauté**.
- **Statistique, bloc par bloc (correction de la deuxième relecture)** :
  - dans chaque bloc `b`, le contraste `c_b` = moyenne de `y` sur les jours non rouges − moyenne de `y` sur les jours
    rouges ;
  - **Δ̂** = Σ `ω_b · c_b` / Σ `ω_b`, avec `ω_b = n_{b,R} · n_{b,NR} / (n_{b,R} + n_{b,NR})` (poids d'une différence
    stratifiée), compté sur les jours rouges et non rouges **du feu observé** du bloc ;
  - les blocs sans jour rouge ou sans jour non rouge ont un poids nul ;
  - **Δ > 0 veut dire que les jours rouges sont pires.** Δ ne dépend pas de la part de rouge : c'est l'écart par jour
    rouge, à l'intérieur des blocs.
- **Placebo `P_T`** (§ 5.4) : chaque tirage recalcule les `c_b` avec les couleurs tournées et les **mêmes poids**
  `ω_b`. Les jours devenus trous par la rotation sont simplement absents des moyennes du bloc.
  - **Δ_exc** = Δ̂ − moyenne exacte du placebo. Elle est calculée bloc par bloc, en énumérant les 12 rotations permises
    de chaque bloc de 13 semaines, puisque les poids sont fixes.
  - Le placebo de Δ n'est pas exactement nul : la dépendance entre jours voisins et les rotations par semaines
    entières le décalent. C'est pour ça qu'on le retire.

### 5.3 Séries descriptives (aucune décision)

- **`S1` — `HASARD_40`, en R** : chaque jour `d`, **20 achats** tirés au hasard (graine `METEO:HASARD:<jour>:<k>`,
  dérivée de 20261008). La paire est tirée uniformément parmi les **40 paires de recherche** éligibles le jour `d`
  (règle de FACTORS : 85 journées valides sur 90, médiane du volume ≥ 1 M$, 90 jours de cotation ; magasin minute),
  et la minute uniformément parmi les
  minutes qui ouvrent dans `]T_d ; T_{d+1}]`. Chaque achat suit la **transaction de référence** de l'annexe A. Achat
  inutilisable (trou de plus de 10 minutes, géométrie invalide) : compté, pas remplacé ; un jour avec moins de 10 achats
  utilisables sort de `S1`. R **brut** et R **net** (central et défavorable), **mêmes tirages** pour les trois. `S1` sert
  aussi à `PERTES_RECENTES` (R brut).
- **`S2` — panier 24 h en %**, brut et net (frais du modèle commun, mêmes tirages et mêmes paires pour les deux
  scénarios), avec la même règle de sortie qu'en § 5.2.
- **`S2N` avec une autre normalisation** : `σ24_168` (§ 5.8), pour comparer à la variante 2018.

### 5.4 Placebos : sauter autant de jours, au hasard, par semaines entières

Les placebos déplacent le **feu** et laissent les résultats en place. Ils gardent la part de jours rouges, la
structure des épisodes et la **composition par jour de semaine**.

- **Index calendaire** : un jour par date. Les jours `SANS_FEU` sont des trous.
  - La couleur placebo d'un jour `t` est celle du jour `t − u`, avec un décalage `u` **multiple de 7 jours**.
  - Si la source ou la cible est un trou, la cible sort de ce tirage : elle n'est ni jouée ni sautée.
  - La statistique est définie **bloc par bloc** avec des poids fixes (§ 5.2). Un nombre de jours un peu différent dans
    un tirage change seulement les moyennes du bloc, jamais les poids.
  - Le nombre de trous est donné aux comptages.
- **`P_T` — dans le bloc de 13 semaines (décisif)** : rotation circulaire à l'intérieur de chaque bloc, de
  `u ∈ {7, 14, …, n_b − 7}` jours (`n_b` = longueur du bloc), tirée indépendamment pour chaque bloc. **Chaque bloc
  garde ses jours rouges** et chaque jour de semaine garde les siens. Marquer un bloc entier en rouge ne rapporte rien.
  - p-valeurs par 10 000 tirages (graine 20261008) : `p_haut` = (1 + nombre de tirages ≥ feu) / 10 001, et `p_bas` de
    même avec ≤.
  - Moyenne exacte du placebo : énumération des 12 rotations de chaque bloc (§ 5.2).
- **`P_G` — décalage global (descriptif)** : rotation de toute la période de `u` = 91 à `N − 91` jours, par pas de
  7 jours (liste exhaustive). Ce placebo mesure **régime + timing**. Il est seulement décrit.
- **« Bruit du hasard »** : percentiles 2,5 et 97,5 des placebos centrés. Ce que le hasard seul donne.

### 5.5 Pourquoi brut et normalisé (correction de la relecture)

En R net, les frais pèsent davantage les jours calmes, où le stop est proche. Sauter les jours agités change donc le R
net moyen **sans aucune information** sur le sens. En % brut, sauter les jours agités change la dispersion. La
statistique décisive est donc **brute** et **divisée par la volatilité prévue** : un jour calme et un jour agité pèsent
pareil. Le résultat net (ce que l'argent verrait) est donné en descriptif.

**Biais déclaré de la normalisation (μ / σ̂).** Les jours rouges sont souvent des jours où la volatilité prévue est
haute (`VOL_HAUTE`, et l'effet de levier : la volatilité monte après les baisses).
- Dans un bloc baissier, une même baisse en % pèse donc **moins** en σ un jour rouge qu'un jour calme : les jours
  rouges **paraissent moins mauvais** qu'en %.
- Ce biais tire Δ vers 0. Il rend la supériorité plus difficile (prudent), mais il rend l'**équivalence plus facile**
  (pas prudent).
- Pour le voir :
  - Δ en % brut (non normalisé) est toujours donné à côté, en descriptif ;
  - le cas synthétique `N3c` (§ 7.2) mesure ce biais quand la volatilité monte avec les baisses ;
  - une `EQUIVALENT_NUL` est accompagnée de la borne haute en % brut. Si celle-ci dépasse la marge traduite en %, le
    rapport le dit (« équivalent en σ, pas en % »).

### 5.6 Décision : supériorité, équivalence, ou ni l'un ni l'autre

**La marge Δ_min : « à partir de quel écart, par jour rouge, ça vaut la peine de sauter les jours rouges »**
**(À VALIDER PAR LE PROPRIÉTAIRE, non figée).**

La marge porte sur **Δ**, l'écart entre jours rouges et autres jours à l'intérieur d'un bloc (§ 5.2). C'est ce que
chaque jour rouge sauté fait gagner ou perdre. Elle ne dépend pas de la part de rouge.
- La première version fixait δ = 0,03 σ **par jour joué**. Avec environ 20 % de jours rouges, cela faisait environ
  0,15 σ **par jour rouge**, soit environ 0,6 % par jour sur une altcoin à 4 % : ce n'est pas « trop petit pour
  servir ».
- Cette valeur avait été choisie pour être atteignable. Elle est retirée.

| Candidat | Δ_min en σ | ≈ en % par jour rouge sauté (altcoin médiane, σ̂₂₄ ≈ 4 %) | ≈ en R (stop 2 ATR 1 h ≈ 2,6 %) | Lecture | Puissance d'équivalence estimée (principale / variante) | Puissance de supériorité à Δ = Δ_min (principale) |
|---|---|---|---|---|---|---|
| A | 0,05 | ≈ 0,2 % | ≈ 0,08 R | le prix d'un aller-retour (0,21 %) | ≈ 0 / ≈ 0 | ≈ 0,16 |
| B | 0,10 | ≈ 0,4 % | ≈ 0,15 R | deux allers-retours | ≈ 0,2 / ≈ 0,4 | ≈ 0,48 |
| C | 0,15 | ≈ 0,6 % | ≈ 0,23 R | trois allers-retours | ≈ 0,8 / ≈ 0,9 | ≈ 0,82 |

- **Hypothèses** (aucune donnée réelle) :
  - σ̂₂₄ médiane ≈ 4 % : supposée. La vraie médiane du panier sera donnée dans le rapport.
  - ATR 1 h ≈ 1,6 × σ̂₂₄ / √24 ≈ 1,3 %, donc 1 R ≈ 2,6 %.
  - La traduction en R suppose un achat tenu 24 h sans stop ni objectif. Une transaction à stop et objectifs n'en voit
    qu'une partie (`COMBINAISONS.md` : environ un quart d'une dérive étalée sur 60 h).
- **Formules de puissance** :
  - erreur type `SE ≈ s_y · √(1/n_R + 1/n_NR) · κ`, avec `s_y ≈ 0,72 σ` (moyenne de 40 paires corrélées à 0,5) et
    `κ = 1,3` (dépendance, plan dans les blocs) ;
  - `n_R ≈ 0,20 · n` jours rouges, avec `n` = 1 967 jours (principale) ou 2 667 (variante), d'où `SE` ≈ 0,052 σ et
    ≈ 0,045 σ ;
  - puissance d'équivalence si Δ vrai = 0 : `2 Φ((Δ_min − 1,645 · SE) / SE) − 1` ;
  - puissance de supériorité si Δ vrai = Δ_min : `Φ(Δ_min / SE − 1,96)` ;
  - avec 15 % de rouge, ces puissances baissent (par exemple ≈ 0,64 et ≈ 0,72 pour C) ; avec 25 %, elles montent.
    Le contrôle positif (§ 7.2) les **remplace** par des valeurs mesurées.
- **Conséquence honnête.** Avec la règle d'arrêt du § 7.2 (les deux puissances ≥ 0,50), **A** arrêterait sûrement
  l'étude avant toute exécution (`INSTRUMENT_TROP_FAIBLE`). **B** serait à la limite. **C** passerait probablement.
  Cinq ans d'historique ne peuvent pas certifier qu'un écart du prix d'un aller-retour est absent.
- **Le propriétaire choisit A, B ou C** (ou une autre valeur, motivée) **avant** les contrôles synthétiques. La valeur
  est alors inscrite ici, avec la date, et ne bouge plus.
- **Toujours rapporté, quel que soit le verdict** : la borne haute de l'intervalle à 90 % de Δ_exc, en σ, en % par
  jour pour l'altcoin médiane réelle du panier, en % brut non normalisé et en R. Formulation : « **les jours rouges ne
  sont pas pires de plus de X % par jour** ». La borne basse est rapportée aussi (« pas meilleurs de plus de Y »).

**Intervalle de Δ_exc** :
- tirage **stratifié** : dans chaque bloc de 13 semaines, on tire des fenêtres circulaires de **4 semaines** (28 jours
  consécutifs, début tiré uniformément parmi les jours du bloc, circulaire dans le bloc) ;
- on les met bout à bout jusqu'à 91 jours (le dernier bloc, plus court, à sa longueur), avec les couples (couleur,
  résultat) des jours tirés ;
- `c_b` est recalculé, puis Δ̂ avec les mêmes poids ;
- 10 000 tirages, graine 20261008. La moyenne du placebo est retranchée comme une constante. Le contrôle nul (§ 7.2)
  vérifie la couverture.

**Résultats possibles** (niveau global **0,05** ; « Δ_min » = la valeur choisie par le propriétaire) :
- **`PERSISTANCE_INFRA_TRIMESTRIELLE`** : `p_haut` ≤ 0,025 (soit 0,05 en bilatéral) et les garde-fous tiennent. À
  l'intérieur d'une même période, les jours rouges sont pires.
- **`PERSISTANCE_FRAGILE`** : le seuil est atteint mais un garde-fou manque (inscrit, pas de confirmation).
- **`INVERSE`** : `p_bas` ≤ 0,025. Les jours rouges sont **meilleurs** : sauter ces jours fait rater les rebonds.
- **`EQUIVALENT_NUL`** (« les jours rouges ne sont pas pires de plus de Δ_min ») : deux tests unilatéraux à 0,05.
  L'intervalle à 90 % de Δ_exc est entièrement dans `[−Δ_min ; +Δ_min]`.
- Si `PERSISTANCE` et `EQUIVALENT_NUL` tombent ensemble : « effet présent mais inférieur à Δ_min », sans usage
  pratique.
- **`NON_CONCLUANT`** : ni l'un ni l'autre (« ni démontré ni exclu »).
- **`INSUFFISANT`** : minimums non atteints (G1).
- **`INSTRUMENT_TROP_FAIBLE`** : décidé avant toute donnée réelle (§ 7.2). Rien n'est exécuté ni compté.
- **Garde-fous** (exigés pour `PERSISTANCE_INFRA_TRIMESTRIELLE`) :
  1. **G1 — minimums** : au moins 60 jours rouges, 8 épisodes rouges et 6 blocs utiles (au moins un jour rouge et un
     jour non rouge). Sinon `INSUFFISANT`.
  2. **G2 — sans le meilleur épisode rouge** : les jours de l'épisode rouge qui apporte le plus à Δ_exc deviennent des
     **trous** (comme des jours `SANS_FEU`, dans le feu et dans les placebos), et Δ_exc est recalculé avec les mêmes
     règles. Il doit rester > 0.
  3. **G3 — régularité** : Δ_exc > 0 dans au moins 4 des 6 années (parmi les années qui ont un bloc utile ; s'il y en
     a moins de 6, dans plus de la moitié).
  4. **G4 — 2022** : Δ_exc sur les seuls blocs de 2022 est > 0. Dans l'année baissière, le feu doit choisir les pires
     jours.

  Les garde-fous ne s'appliquent pas à l'équivalence : une équivalence n'annonce aucun effet.

### 5.7 Verdict global (si les mesures divergent)

Seules la question principale et sa variante (§ 5.8) décident. Les mesures descriptives (`S1`, `S2` net, `P_G`, pire
perte, composantes) **ne changent jamais le verdict**, même quand elles vont dans un autre sens. On les signale alors
comme « divergence descriptive ».

Le verdict porte sur **le feu à 6 composantes sur 2020-02 → 2025-06**. La variante juge une **autre règle**, le feu
réduit `FEU_R4`, sur 2018-03 → 2025-06. Cette période contient à **74 %** celle de la principale (1 967 jours sur
2 667). Ce n'est donc ni « le feu » ni « deux périodes indépendantes ». C'est un complément : « la version réduite du
feu tient-elle aussi en ajoutant la baisse de 2018 ? ».

| Question principale (feu à 6, 2020-2025) | Hypothèse alors testée par la variante (`FEU_R4`, 2018-2025) | Issue de la variante | Verdict global |
|---|---|---|---|
| `PERSISTANCE_INFRA_TRIMESTRIELLE` | supériorité, même sens (`p_haut` ≤ 0,025) | atteinte | **Piste** ; la version réduite tient aussi avec 2018. Confirmation en direct. |
| `PERSISTANCE_INFRA_TRIMESTRIELLE` | idem | non atteinte | **Piste fragile** : la version réduite ne la retrouve pas avec 2018 |
| `EQUIVALENT_NUL` | équivalence à Δ_min | atteinte | **Les jours rouges ne sont pas pires de plus de Δ_min**, et la version réduite non plus avec 2018 |
| `EQUIVALENT_NUL` | idem | non atteinte | Pas pires de plus de Δ_min sur 2020-2025 ; pas établi pour la version réduite avec 2018 |
| `INVERSE` | infériorité, même sens (`p_bas` ≤ 0,025) | atteinte | **Nuisible** ; la version réduite aussi avec 2018 |
| `INVERSE` | idem | non atteinte | **Nuisible** sur 2020-2025 ; pas retrouvé pour la version réduite avec 2018 |
| `NON_CONCLUANT`, `INSUFFISANT`, `PERSISTANCE_FRAGILE` | aucune (décrite seulement) | — | **Ni démontré ni exclu** |
| `INSTRUMENT_TROP_FAIBLE` | aucune | — | Rien n'est exécuté ni compté |

### 5.8 Variante déclarée : 2018 → 2025, avec la deuxième grande baisse (1 essai)

- **Pourquoi** : 2020-2025 ne contient qu'une grande baisse (2022). 2018 est l'autre.
- **Période** : du lundi 2018-03-05 au dimanche 2025-06-22, soit 2 667 jours = 381 semaines, 29 blocs de 13 semaines et
  un dernier bloc de 4 semaines.
- **Feu réduit `FEU_R4`**, aux seules composantes disponibles en 2018 :
  - `BTC_STRUCTURE` ;
  - `LARGEUR` au top 40 à date ;
  - `PEUR_EXTREME` (depuis 2018-02) ;
  - `PERTES_RECENTES` (achats `S1` depuis 2019 ; absente tant que sa base de rang n'a pas 300 valeurs, soit jusqu'à
    fin 2019 environ).

  Règle : `SANS_FEU` si deux composantes ou plus sont absentes ; sinon **ROUGE** si au moins **2** sont en danger (la
  moitié de 4, comme 3 sur 6), **ORANGE** si 1, **VERT** si 0.
- **Minimum de `LARGEUR` abaissé à 5 membres éligibles** : début 2018, Binance n'avait que 6 à 8 paires USDT de plus
  de 50 jours (comptage de métadonnées, sans prix : 6 au 2018-03-01, 7 au 2018-06-01, 16 au 2018-09-01, 40 au
  2020-02-01). Avec 10, la variante perdrait le premier semestre 2018. La largeur de 2018 porte donc sur quelques
  grandes paires : déclaré.
- **Normalisation** : la prévision H24 n'existe pas en 2018. Il faut assez de lignes d'entraînement, et BTC n'est coté
  sur Binance que depuis 2017-08. On utilise donc `σ24_168` = racine de 24 × la moyenne des carrés des rendements log
  horaires des 168 dernières heures, connues à 00:00 (au moins 160 présentes). C'est une approximation déclarée,
  appliquée à toute la période de la variante.
- **Panier** : le **top 40 à date**, comme la principale. Membres du mois avec bougies 1 h, au moins 90 jours de
  cotation, `σ24_168` calculable. En 2018, il ne contient que 6 à 16 paires (comptage ci-dessus).
- **Redondance déclarée.** En 2018, `FEU_R4` est **presque un filtre de tendance** : structure de BTC, largeur de
  quelques grandes paires qui suivent BTC, et peur extrême qui suit le prix. `PERTES_RECENTES` est absente jusqu'à fin
  2019. Ce que la variante ajoute, c'est surtout « un filtre de tendance choisit-il les jours à l'intérieur des blocs
  de 2018 ? ».
- **Même décision, même marge Δ_min** (ici en unités de `σ24_168`), mêmes placebos et mêmes garde-fous. G4 vaut pour
  2018 **et** pour 2022.
- **Procédure séquentielle fixée** : la variante n'est **jugée** au niveau 0,05 que si la question principale a conclu
  (`PERSISTANCE_INFRA_TRIMESTRIELLE`, `EQUIVALENT_NUL` ou `INVERSE`). Elle teste alors **l'hypothèse qui correspond à
  cette issue** (tableau du § 5.7) : supériorité dans le même sens, équivalence, ou infériorité dans le même sens.
  L'erreur globale reste à 0,05. Sinon, elle est décrite (estimation et intervalle), sans verdict. Dans tous les cas,
  elle compte pour **1 essai**.
- **Descriptif à part** : la sous-période **2018-03-05 → 2020-02-02** seule (700 jours, 7 blocs de 13 semaines et un
  reste de 9 semaines), c'est-à-dire la seule partie qui n'est pas dans la principale. On donne Δ_exc et le bruit du
  hasard, sans verdict.

### 5.9 Descriptif (calculé avec la mesure, jamais après coup)

- **Sans p-valeur ni verdict.** Chaque comparaison descriptive donne Δ_exc (ou l'excès) et le « bruit du hasard ».
- **`n_descriptive`** : le registre (`research/experiments.py`) ne le connaît pas aujourd'hui. Il **devra
  l'implémenter au moment du code** (champ inscrit avec `n_trials`, sans effet sur `program_trials`), **avec un
  test**.
- Le rapport annoncera le **nombre attendu de fausses alertes descriptives** : environ 5 % des bandes du hasard à 95 %
  seront dépassées par hasard seul, soit environ `0,05 × n_descriptive`. Un dépassement descriptif n'est jamais une
  piste.
- **Contenu** :
  - `P_G` sur `S2N` (régime + timing) ;
  - pire perte de `S1` en R cumulés et de `S2`, contre `P_G` ;
  - `S1` (brut, net) et `S2` (brut, net) contre `P_T` et `P_G` ;
  - `FEU_COMPLET` (1 / ½ / 0) ;
  - résultat des jours orange ;
  - couleurs et résultats par année ;
  - gains manqués dans les phases de hausse (2020-T4 à 2021-T1, 2023-T4 à 2024-T1, nommées après coup) ;
  - jour de semaine ; bascules de `VOL_HAUTE` aux réajustements ; jours du Fear & Greed entre 23 et 27 ;
  - accord entre composantes ; épisodes rouges (dates, durée, résultat) ;
  - **résultat net moyen** de `SANS_ROUGE` avec son intervalle (la météo ne rend rien rentable).
- **Chaque composante seule** est décrite comme politique « sauter ses jours de danger ». Un protocole fondé sur une
  composante isolée **ne serait testé qu'en direct**, jamais sur ces données.
- **Références** (ex-(b), maintenant descriptives) : les transactions de `REF_TRENDLINE_1H` (cassures de ligne de
  tendance 1 h, 40 paires) et de `REF_K2_1J` (cassures journalières K2 sur les survivantes) décidées un jour rouge,
  contre `P_T`. On sait déjà que K2 a gagné les années de hausse et perdu en 2020, 2022 et 2025 : c'est
  l'illustration du piège du § 6.

## 6. Le piège principal : un filtre « anti-baisse » gagne toujours après coup

- **Le mécanisme.** De 2020 à 2025 : deux grandes hausses, une grande baisse. Toute règle qui suit la tendance est
  rouge surtout en 2022 et verte surtout en 2021. Comparée au fait de « jouer tous les jours », elle a l'air brillante.
  Mais c'est **une seule** baisse, vue après coup.
- **Ce que fait ce protocole** :
  1. **Exposition égale** : les placebos sautent la même part de jours, par semaines entières.
  2. **Même régime** : la question décisive garde les jours rouges de chaque bloc de 13 semaines. Marquer 2022 en
     rouge n'y rapporte rien.
  3. **2018 et 2022** (G4) : dans chaque année baissière, le feu doit encore choisir les pires jours.
  4. **Sans le meilleur épisode rouge** (G2) : une persistance qui ne vient que d'un krach n'en est pas une.
  5. **Contrôle `N3`** (§ 7.2) : sur des marchés synthétiques où la tendance change **d'un bloc à l'autre** mais pas à
     l'intérieur, la question décisive doit rester à 0. Cela vérifie que le placebo retire bien l'effet de régime.
  6. **Le régime ne se confirme que dans le temps** : `P_G` est seulement décrit. F17 seul pourra juger.
- **Le piège inverse** : les jours de panique précèdent souvent les plus forts rebonds. Le test bilatéral le montrera
  (`INVERSE`).

## 7. Contrôles avant toute exécution sur données réelles

### 7.1 Causalité et mutations

Le feu, `S2N` et les normalisations sont recalculés à des jours tirés au hasard, juste après 00:10, sur des données
**tronquées** à `T_d` et avec le **futur falsifié** (prix, volumes, membres du top 40, Fear & Greed, financement,
prévisions). Le résultat doit être identique.

**Mutations qui doivent être détectées :**
- EMA de BTC ou des membres jointe sur `open_time` ;
- clôture de `d` dans `LARGEUR` ;
- membres du top 40 calculés avec le mois en cours ;
- Fear & Greed horodaté `d` ;
- financement sans la minute de latence ;
- **bougies postérieures à `T_d` supprimées ou falsifiées : ni la présence ni la valeur de la prévision H24 ne
  changent** (la mutation « filtre sur la cible » doit être détectée) ;
- rang qui inclut la valeur du jour ;
- base de `VOL_HAUTE` prise d'une autre instance du modèle ;
- `PERTES_RECENTES` avec les achats de `d − 3` à `d − 1` ;
- **paire retirée du panier parce que son ouverture de `d + 1` manque** ;
- rotation qui n'est pas un multiple de 7 ou qui change le nombre de jours rouges d'un bloc ;
- lecture après le 2025-06-30.

### 7.2 Contrôles synthétiques (même code de bout en bout, sauf approximations déclarées)

- **Taille** : au moins **500 simulations par cas**, **20 paires** (choix fixé : 40 paires multiplieraient le temps de
  calcul par deux pour presque rien, car le facteur commun domine ; l'erreur type du panier ne change que d'environ 1 %
  entre 20 et 40 paires corrélées à 0,5). 7,4 ans par simulation (2 ans de rodage non mesurés, puis 5,4 ans mesurés),
  bougies **1 h**.
- **Prix** : paire = β × BTC + bruit propre, β tiré dans [0,5 ; 1,5].
- **Volatilité à mémoire longue** (type HAR) : log-variance journalière de BTC et du bruit propre =
  `c + 0,35 · (veille) + 0,35 · (moyenne sur 5 jours) + 0,25 · (moyenne sur 22 jours) + bruit N(0 ; 0,3²)`, avec
  `c` réglé pour une volatilité journalière moyenne de 3 % (BTC) et de 4 % (bruit propre). Les rendements horaires d'un
  jour sont gaussiens, de variance égale au 1/24e de celle du jour (pas de profil intrajournalier : déclaré).
- **Fear & Greed synthétique** : `FG_d = min(100, max(0, 50 + 40 · tanh(r30_d / s30_d) + e_d))`.
  - `r30_d` = rendement log de BTC sur les 30 jours finissant à `d − 1`.
  - `s30_d` = écart-type des rendements journaliers de ces 30 jours × √30.
  - `e_d = 0,8 · e_{d−1} + N(0 ; 8²)`.
- **Financement synthétique** (par règlement de 8 h, trois par jour) :
  `f = 0,0001 + 0,0004 · max(0, r7 / s7) + N(0 ; 0,0001²)`, avec `r7` et `s7` définis comme ci-dessus sur 7 jours,
  connus au règlement.
- **`VOL_HAUTE` et `σ̂` sur synthétique (approximation déclarée)** :
  - HAR journalier simple (veille, 5 jours, 22 jours, sans profil), réajusté chaque bloc de 13 semaines par moindres
    carrés sur le passé ;
  - même règle « base et valeur de la même instance » ;
  - le HAR + profil horaire réel n'est pas réajusté 500 fois, faute de temps.
- **`PERTES_RECENTES` sur synthétique (approximation déclarée)** : achats de la transaction de référence simulés sur
  les bougies **1 h** (stop et objectifs lus sur le haut et le bas de l'heure, stop d'abord), 20 par jour, R brut.
- **`LARGEUR` et panier sur synthétique** : les 20 paires sont toutes membres du top 40.
- **Cas** :
  - **`N1`** : sans dérive, volatilité constante ;
  - **`N2`** : sans dérive, volatilité à mémoire longue ;
  - **`N3`** : dérive **constante dans chaque bloc de 13 semaines** (±0,3 % par jour, tirée par bloc), volatilité à
    mémoire longue ;
  - **`N3b`** (descriptif) : régimes de dérive de durée aléatoire (exponentielle, moyenne de 60 jours), non alignés
    sur les blocs. Il montre combien de « persistance » un marché à régimes réels produit, sans juger ;
  - **`N3c`** — **effet de levier** : comme `N2`, sans dérive, mais la log-variance du jour reçoit en plus
    `+ 0,5 · max(0, −r_{d−1} / σ_{d−1})` (la volatilité monte après une baisse, pas après une hausse). Ce cas mesure
    le biais μ / σ̂ du § 5.5 et le lien mécanique entre baisses, `VOL_HAUTE` et rouge ;
  - **`N2t`** — **queues épaisses** : comme `N2`, mais les rendements horaires suivent une loi de **Student à 4 degrés
    de liberté**, remise à la variance du jour ;
  - **`N4`** : effet du jour de semaine seul (lundi +0,2 σ, sans autre dérive). Le contrôle « **jour de semaine seul :
    excès nul** » applique un feu rouge le lundi et le mardi seulement : Δ_exc contre `P_T` doit être **exactement
    0** (rotations par multiples de 7). Le feu réel y est aussi jugé comme dans `N1`.
- **Critères, pour la question principale et la variante, dans `N1`, `N2`, `N2t`, `N3`, `N3c` et `N4`** :
  1. **biais** : la moyenne sur les simulations de `z` = Δ_exc / (écart-type du placebo) vérifie **|z moyen| ≤ 0,15** ;
  2. **taux de faux positifs** : part des simulations avec `p_haut` ou `p_bas` ≤ 0,025 ≤ **0,07** (attendu 0,05) ;
  3. **uniformité des p-valeurs** : test de Kolmogorov-Smirnov contre la loi uniforme, p ≥ 0,01 ;
  4. **couverture de l'intervalle à 90 %** de Δ_exc ≥ **0,85**.
- **Échec d'un critère** : la question n'est **ni exécutée ni comptée**. L'instrument est corrigé (point technique,
  daté dans l'historique), puis le contrôle est refait et inscrit.
- **Contrôle positif et règle d'arrêt pour inutilité** (faits **après** le choix de Δ_min par le propriétaire) :
  - **puissance de supériorité** : dans `N3`, on rend les jours rouges du feu synthétique pires de **exactement
    Δ_min** (dérive de −Δ_min · σ ajoutée au rendement de 24 h du panier ces jours-là), en plus du régime. On mesure la
    part des simulations qui concluent `PERSISTANCE_INFRA_TRIMESTRIELLE` ;
  - **puissance d'équivalence** : la part des simulations **`N2`** qui concluent `EQUIVALENT_NUL` ;
  - **les deux doivent être ≥ 0,50.** Sinon : **`INSTRUMENT_TROP_FAIBLE`**, inscrit ici, et **rien n'est exécuté ni
    compté** ;
  - même règle, à part, pour la variante 2018, avec son feu réduit et sa normalisation. Si la principale est
    `INSTRUMENT_TROP_FAIBLE`, la variante n'est pas exécutée non plus (procédure séquentielle).
- Fichiers et chiffres inscrits ici **avant** toute exécution réelle.

### 7.3 Relecture

Relecture `leak-auditor` du code, inscrite par un commit relu, comme pour `COMBINAISONS.md`. Chaque exécution réelle
vérifie que les modules de l'étude et les modules importés n'ont pas changé depuis ce commit, et que le code est
commité.

### 7.4 Comptages d'avant exécution (aucun résultat)

À inscrire ici, sans aucun calcul de rendement ni de R :
- couleurs par année et par bloc ;
- épisodes (nombre, longueur) ;
- trous `SANS_FEU` ;
- part du temps en danger par composante et par année ;
- activations simultanées ;
- blocs utiles ;
- membres éligibles de `LARGEUR` par mois ;
- paires du panier, membres sans bougies 1 h, paires de moins de 400 jours, et blocs qui contiennent deux instances
  du modèle ;
- achats `S1` utilisables (tirages et géométries seulement) ;
- jours du Fear & Greed entre 23 et 27 ;
- mêmes comptages pour la variante.

**Aucune règle ne change d'après ces comptages.**

## 8. Confirmation

- **Paires C** (nécessaire, pas suffisant) :
  - le panier décisif est déjà le top 40 à date (§ 5.2). La confirmation rejoue **la même question, sans changement**,
    sur le **sous-panier des membres qui ne sont pas parmi les 40 paires de recherche** : les paires C de
    `trendline_confirmation.universe`, paires retirées comprises. Ce sont des paires sur lesquelles le modèle de
    volatilité n'a pas été ajusté et qui n'ont servi à aucune mesure de la météo ;
  - faite pour une issue **`PERSISTANCE_INFRA_TRIMESTRIELLE`** de la principale ou du modèle (§ 9). Faite aussi, si
    nécessaire, pour **`EQUIVALENT_NUL`** ou **`INVERSE`** : on vérifie que la même hypothèse tient sans les
    survivantes ;
  - au plus **2 confirmations** en tout (principale, modèle), exécution unique, niveau 0,05 / m (m ≤ 2), 1 essai par
    question rejouée ;
  - les jours sont les mêmes pour toutes les paires : cette étape vérifie seulement que l'effet, ou son absence, ne
    tient pas aux 40 survivantes.
- **Dans le temps** : F17 (§ 11), qui démarre dès sa relecture, quel que soit le résultat historique.
- **Période réservée** : **non lue.** La demande du propriétaire naît de ce qu'il vit en ce moment, donc de cette
  période même : elle n'est pas un juge neutre de la météo. Elle a déjà été consultée 3 fois.

## 9. Étape 2 : modèle appris (seulement sur décision du propriétaire, après l'étape 1)

- **Lignes** : un jour de la période principale. **Cible** : `y = 1` si le `S2N` du jour est < 0.
- **Variables** (8, connues à `T_d`) :
  - les valeurs continues des 6 composantes ;
  - la croissance de l'encours des stablecoins sur 7 jours, avec une variable « absente » avant le 2020-07-10 ;
  - le rang du volume agrégé du top 40 à date sur 7 jours parmi 365 jours.
  - Elles sont centrées et réduites sur l'entraînement seulement.
- **Modèle** : régression logistique L2, `C = 1` (`ml/logistic.py`, comme `COMBINAISONS.md`). Walk-forward annuel à
  fenêtre croissante, réajustement le 1er janvier sur les jours ≤ 28 décembre précédent. Plis : 2022, 2023, 2024,
  2025-S1. Au moins 500 lignes et deux classes, sinon le pli compte comme un échec.
- **Rouge du modèle** : `p̂` au-dessus du quantile `1 − f`, où `f` est la part de rouge de la règle du § 4 dans
  l'entraînement.
- **Même question décisive** (§ 5.6, même Δ_min, blocs de 13 semaines des années 2022 à 2025-S1), **1 essai**, niveau
  0,05. G3 : 3 plis sur 4. Mêmes contrôles et même règle d'arrêt pour inutilité.

## 10. Signaux Telegram de 2026 : retirés comme test

Ce n'est plus un test historique, pour deux raisons. Le raisonnement serait **circulaire** : la demande du propriétaire
vient de ce qu'il a vécu en 2026, avec ces mêmes signaux. Et ces signaux sont déjà utilisés par la voie A de
`COMBINAISONS.md`. Seuls les signaux reçus **après** le démarrage de F17 sont mesurés, en descriptif (`S3`, § 11).

## 11. Test en direct `F17_METEO` : séquentiel, sur plusieurs années

La pré-inscription formelle (nouvelle section de `FORWARD_TESTS.md`, sans toucher au « Cadre commun » ni aux autres
sections) et le module (`forward/f17.py`, nouveau) sont une étape à part, relue avant le démarrage. **F4, F5, F12 et
F16 restent intacts.**

- **Feu** : chaque jour après 00:10 UTC, mêmes 6 composantes, mêmes seuils, même règle.
  - `LARGEUR` : top 40 à date recalculé le 1er de chaque mois par bougies journalières publiques (chemin `klines`, déjà
    dans la liste blanche ; aucun élargissement de `data/http.py`).
  - `VOL_HAUTE` et `σ̂` : **une instance du modèle propre à F17** (fonctions gelées du protocole v3), réajustée le 1er
    de chaque trimestre civil. À chaque réajustement, la base de rang est **recalculée par cette même instance**. Elle
    n'est pas lue dans le journal de F12, qui sert seulement de comparaison descriptive.
    - **Univers d'entraînement** : les 40 paires de recherche admises par la liste halal figée au démarrage, et BTC.
      Les bougies 1 h viennent du magasin de la surveillance, sur une fenêtre glissante de 3 ans (lignes purgées)
      finissant au réajustement.
    - Ces bougies comprennent la période réservée **comme entrées du modèle de volatilité seulement**, comme pour F12
      et F5 (déclaré). Aucune mesure de direction n'y est faite.
    - Le modèle est appliqué tel quel aux autres membres du top 40.
  - **Panier `S2N` en direct** : le top 40 à date du mois. Les bougies 1 h des membres hors magasin sont lues par
    l'API publique (`klines`).
  - Fear & Greed du relevé F0_DONNEES (`d − 1`, sinon `d − 2`).
  - Financement du relevé F0_DERIVES.
  - **`PERTES_RECENTES`** : la base de rang de départ est la **dernière année de DEVELOPMENT** (valeurs `m` de
    2024-06-23 → 2025-06-22, calculées par l'étude). Elle glisse ensuite avec les **seules valeurs mesurées en
    direct** par F17 : les plus anciennes valeurs de DEVELOPMENT sortent à mesure que les valeurs en direct entrent.
    **Aucun achat `S1` n'est jamais calculé sur la période réservée.** Les 10 premiers jours, la composante est
    absente.
- **Critère principal unique** : la question décisive du § 5.6 sur `S2N` en direct (Δ_exc, brut, normalisé, `P_T`
  par blocs de 13 semaines commençant le premier lundi après le démarrage, même Δ_min).
  - **Part d'information** `t = R / R_max`, où `R` = jours rouges cumulés et résolus. **`R_max` = 280 jours rouges**,
    fixé maintenant : environ 4 ans à 70 jours rouges par an, l'ordre de grandeur supposé au § 13. Si `R` dépasse
    `R_max`, `t = 1`.
  - **Analyses** : à chaque anniversaire du démarrage, et dès que `t` atteint 1. **Fin au plus tard 4 ans** après le
    démarrage. La dernière analyse est alors faite avec `t = 1` (tout l'α restant est dépensé).
  - Une analyse n'a lieu que si 60 jours rouges et 6 épisodes au moins sont cumulés. Sinon elle est sautée, et son α
    n'est pas dépensé.
  - **Dépense d'α de Lan-DeMets, forme O'Brien-Fleming**, bilatérale à 0,05 : `α(t) = 2 − 2 Φ(1,96 / √t)`. Les bornes
    `c_k` sur l'échelle normale sont calculées à chaque analyse, à partir des `t` réellement observés, par
    l'intégration récursive habituelle. Pour fixer les idées, avec 4 analyses à `t` = ¼, ½, ¾, 1, elles valent environ
    4,33, 2,96, 2,36, 2,01.
  - **Statistique** : `T_k` = Δ_exc / erreur type, l'erreur type venant du tirage stratifié par fenêtres de 4 semaines
    (§ 5.6). Elle est comparée à la borne **convertie en quantile de Student** à `B_k − 1` degrés de liberté (`B_k` =
    blocs utiles à l'analyse), au même niveau unilatéral que `c_k`.
  - **Supériorité** : `T_k ≥` borne et G2 (sans le meilleur épisode rouge) → `PERSISTANCE_INFRA_TRIMESTRIELLE`.
    **Infériorité** : `T_k ≤ −`borne → `INVERSE`.
  - **Arrêt pour inutilité** (non contraignant) : si l'intervalle **Δ_exc ± c_k · erreur type** (même conversion de
    Student) est entièrement dans `[−Δ_min ; +Δ_min]`, le test s'arrête avec `EQUIVALENT_NUL` (« les jours rouges ne
    sont pas pires de plus de Δ_min »).
  - À la dernière analyse, sans borne franchie ni équivalence : `NON_CONCLUANT`. Sans 60 jours rouges : `INSUFFISANT`.
  - La borne haute de l'intervalle est rapportée à chaque analyse, en % par jour (§ 5.6).
- **`n_trials` au registre FORWARD = 1** : une seule décision annoncée.
- **Descriptif** (aucune décision) :
  - `S1` (20 achats au hasard par jour, brut et net) ;
  - `S2` (net) ;
  - **`S3`** : les signaux texte du relais Telegram reçus après le démarrage, lus sans écriture, avec la gestion du
    propriétaire, les mêmes doublons et les mêmes refus que F4 ;
  - feu de F5 du jour (lecture seule) ;
  - news de risque, profondeur des carnets, liquidations OKX, émissions F3, intérêt ouvert ;
  - **nombre de jours où la valeur `d − 2` du Fear & Greed a servi**, et **part des jours où `PERTES_RECENTES` est
    allumée** (sa base vient en partie de DEVELOPMENT : une part très éloignée de 20 % le montrerait).
- **Calendrier** : revue descriptive à 12 semaines, sans changement ; analyses annuelles ; fin au plus tard 4 ans après
  le démarrage. **Aucune influence** sur les signaux de CSI, sur BinanceSpotManager, sur le feu de protection ou sur un
  autre test.

## 12. Essais et comparaisons multiples

| Étape | Données | Décisions | Essais | Niveau |
|---|---|---|---|---|
| Question principale | top 40 à date, DEVELOPMENT, 2020-02-03 → 2025-06-22 | Δ_exc de `S2N`, `P_T`, supériorité / équivalence | **1** | 0,05 |
| Variante 2018 | top 40 à date, 2018-03-05 → 2025-06-22, `FEU_R4` | hypothèse fixée par l'issue de la principale | **1** | 0,05, procédure séquentielle |
| Modèle appris (sur décision) | 2022 → 2025-S1 | idem | 1 | 0,05 |
| Confirmation sur le sous-panier des paires C (persistance, équivalence ou inverse) | membres hors des 40 paires de recherche | même hypothèse | ≤ 2 | 0,05 / m |
| F17 en direct | FORWARD | critère principal séquentiel | 1 (registre FORWARD) | 0,05 global (Lan-DeMets, forme O'Brien-Fleming) |

- **DEVELOPMENT** : **2 essais sûrs, 5 au plus** (principale, variante, modèle, 2 confirmations). Au 2026-10-08, le
  registre compte 873 essais : on passerait à 875, au plus 878. **Le total sera recompté au moment de l'exécution**,
  puisque d'autres études avancent entre-temps.
- **`INSTRUMENT_TROP_FAIBLE`** (§ 7.2) : 0 essai. Si la principale est arrêtée ainsi, la variante aussi.
- **FINAL_TEST** : 0 (reste à 8). Consultations : aucune.
- **Descriptif** : `n_descriptive` inscrit avec chaque exécution, sans p-valeur. Environ 5 % de fausses alertes
  attendues (§ 5.9).
- Une question décisive par période, des garde-fous plus sévères, et la confirmation dans le temps : un résultat isolé
  sur l'historique, au milieu d'environ 875 essais, reste un indice.

## 13. Puissance réaliste (hypothèses non mesurées ; le contrôle positif les remplace)

- **Hypothèses** : environ 1 967 jours, environ 20 % de rouges (le vrai chiffre viendra des comptages). `S2N` d'un
  jour, d'écart-type d'environ 0,72 σ (paires corrélées à environ 0,5).
- **Erreur type de Δ** : environ 0,052 σ (principale) et 0,045 σ (variante), avec la dépendance (formules et tableau
  du § 5.6).
- **Supériorité** : seul un écart Δ d'environ 0,15 σ (≈ 0,6 % par jour rouge pour une altcoin médiane) a de bonnes
  chances (≈ 0,8) d'être vu. À 0,10 σ, c'est pile ou face.
- **Équivalence** : si l'effet vrai est nul, environ 0 / 0,2 / 0,8 de chances de conclure « pas pires de plus de
  Δ_min » pour Δ_min = 0,05 / 0,10 / 0,15 σ.
- **Ce qu'on ne peut pas dire** : avec 5 ans, impossible de certifier que l'écart est inférieur au prix d'un
  aller-retour.
- **`P_T`** n'utilise que les variations à l'intérieur des blocs : l'erreur réelle sera sans doute plus grande. Le
  contrôle positif le mesurera, et décide (§ 7.2).
- **F17** : environ 70 jours rouges par an. Une conclusion demandera vraisemblablement 2 à 4 ans (`R_max` = 280).

## 14. Ce que voudront dire les résultats

- **Dans tous les cas** : « les jours rouges ne sont pas pires de plus de X % par jour » (borne haute à 90 %).
- **« Pas pires de plus de Δ_min »** (`EQUIVALENT_NUL`) : chaque jour rouge sauté fait gagner moins que Δ_min par
  rapport à sauter un jour tiré au hasard dans le même trimestre.
  - Le feu de protection garde alors son rôle de précaution, sans gain à attendre de ce côté.
  - L'outil démontré reste la taille selon la volatilité.
- **« Ni démontré ni exclu »** : on ne sait pas, et F17 décidera.
- **Piste (persistance)** : à l'intérieur d'une même période, les jours rouges sont pires. Elle doit être confirmée sur
  les paires C puis en direct. Même alors, le résultat net (descriptif) dira si les achats restent perdants après
  frais.
- **Nuisible (`INVERSE`)** : les jours rouges sont meilleurs. Le feu fait rater les rebonds, et le feu de protection
  devrait le dire à son utilisateur. Ce n'est pas un signal d'achat (il faudrait un autre protocole).
- **Divergences descriptives** (par exemple `P_G` favorable mais question principale nulle) : le feu suit la tendance
  sur ces cycles sans choisir les jours. C'est un filtre de tendance de plus, confirmable seulement dans le temps.

## 15. Ordre d'exécution et mise en œuvre prévue

0. **Choix de Δ_min par le propriétaire** (§ 5.6), inscrit ici avec la date. Rien ne s'exécute avant.
1. Relecture de ce document (propriétaire, `leak-auditor`). Accord pour l'historique du Fear & Greed : alternative.me
   `/fng/` avec `limit=0`, même adresse que F0, déjà dans la liste blanche de `forward/sources.py` (aussi utilisée par
   `context/`), rangé comme série `HISTORIQUE` du magasin de contexte, avec son empreinte.
2. Code et tests (synthétiques seulement) :
   - `research/meteo.py` (composantes, feu, prévisions recalculées) ;
   - `research/meteo_study.py` (séries, placebos, décision, exécutions) ;
   - `research/meteo_controls.py` (§ 7.2) ;
   - `tests/test_meteo.py` (causalité, mutations du § 7.1, sortie du panier, exemples à la main de chaque composante,
     rotations par multiples de 7, épisodes avec trous, statistique bloc par bloc, G2 en trous) ;
   - `n_descriptive` dans `research/experiments.py`, avec son test (§ 5.9).

   Commandes : `csi meteo fng-historique | comptages | controles | principale | variante | modele | confirmation`.
   Chaque exécution réelle exige `--executer`, un code commité et relu (`CODE_REVIEW`), les contrôles inscrits, et
   n'a lieu qu'une fois par type.
3. Relecture `leak-auditor` du code → contrôles synthétiques et comptages inscrits ici. La règle d'arrêt pour inutilité
   est appliquée à ce moment.
4. Question principale (`METEO_PRINCIPALE`), puis variante (`METEO_VARIANTE_2018`) : exécutions uniques.
5. Confirmation C (`METEO_CONFIRMATION`) si persistance, et si nécessaire si équivalence ou inverse (≤ 2).
6. Modèle (`METEO_MODELE`) sur décision du propriétaire.
7. F17 : pré-inscription et module à part, relus. Il démarre dès sa relecture, en parallèle.

- **Importés sans changement** :
  - `research/volatility_hourly.py` (`hourly_frame`, `complete_rows`, `fit_at` : prévisions recalculées sans filtre
    sur la cible) ;
  - `research/pit_universe.py` (membres) ;
  - `research/figures_history.py` (`play`), `research/factors.py` (éligibilité) ;
  - `research/trendline_confirmation.py` (`universe`, `coverage`), `research/pivot_screen.py` (K2) ;
  - `research/long_history.py`, `research/minute_history.py` ;
  - les lecteurs de `derivatives/` et de `context/` ;
  - `forward/costs.py`, `backtest/metrics.py`, `ml/logistic.py`, `research/protocol.py`, `research/experiments.py`.
- **Aucun module gelé n'est modifié.**

## 16. Points à faire relire

1. Prévisions H24 recalculées sans filtre sur la cible : mutation « bougies futures supprimées », base et valeur de la
   même instance, bascules aux réajustements.
2. Heure de connaissance et révisions du Fear & Greed ; règle `d − 2` en direct.
3. Rotations par multiples de 7 sur l'index calendaire avec trous. Statistique Δ bloc par bloc à poids fixes. Blocs
   de 13 semaines à la place des trimestres civils.
4. Intervalle de Δ_exc (tirage stratifié par fenêtres de 4 semaines dans chaque bloc, moyenne du placebo retranchée
   comme une constante) et sa couverture dans les contrôles.
5. Marge Δ_min (à choisir par le propriétaire) et normalisation par `σ̂` : biais μ / σ̂ (`N3c`), facteur de Duan,
   inégalité de Jensen, blocs à deux instances ; `σ24_168` dans la variante.
6. Approximations des contrôles synthétiques (HAR journalier simple, achats sur bougies 1 h, pas de profil horaire),
   formules du Fear & Greed et du financement, puissances estimées par formules.
7. Panier et `LARGEUR` au top 40 à date ; `MIN_HISTORY_DAYS` non appliqué au `σ̂` ; minimum de 5 dans la variante 2018.
8. F17 :
   - base de `PERTES_RECENTES` (dernière année de DEVELOPMENT puis glissement en direct) ;
   - instance propre du modèle et son univers d'entraînement ;
   - dépense d'α sur `R_max` = 280, analyses sautées, conversion de Student ;
   - équivalence à ± `c_k` erreurs types.

## Annexe A — Transaction de référence (copie figée de `COMBINAISONS.md` § 1.4, commit `6b32bc6`)

Texte repris tel quel ; seules les phrases propres aux briques et aux votes de cette étude-là sont omises. **Adaptation
déclarée** : pour un achat générique, la « minute d'exécution » est la minute tirée, et la « bougie de décision `t` »
est la dernière bougie 1 h dont l'`available_at` précède l'ouverture de cette minute. `C_t` et `ATR_t` sont ceux de
cette bougie. Seul l'ensemble « tous les déclencheurs » sert (chaque achat est indépendant).

- **Entrée** : achat **au marché** à l'ouverture de la minute d'exécution (`figures_history.Setup` avec `entry=None`,
  comme `SWEEP` et `RSI_DIV`). L'« entrée » est l'ouverture de cette minute, avant glissement.
- **Stop** : `C_t − 2 × ATR_t` (ATR de Wilder 14 en 1 h à la bougie de décision, `INDICATEURS.md`), posé à la
  détection, fixe. Justification : deux bougies moyennes sous la clôture, hors du bruit d'une heure ; les frais
  aller-retour (≈ 0,21 % en central) y pèsent environ 0,1 R. Fixé a priori, non réglé.
- **Objectifs** : sortie par **tiers** à entrée + 1, 2 et 3 R (R = entrée − stop), posés à l'exécution.
- **Durée** : **60 heures** au plus, comptées en temps depuis l'exécution ; reste vendu à la clôture.
- Minute où le stop et un objectif sont touchés : **stop d'abord** (pas de bougies 1 s en 2019-2025). Minute qui ouvre
  au stop ou dessous avant l'exécution : ordre annulé (propriété reprise de F15). Géométrie invalide (stop à moins de
  0,1 % sous l'entrée) : écartée, comptée.
- **Frais** : modèle commun (`forward/costs.py`), **central** (7,5 pb par ordre) et **défavorable** (10 pb) ; entrée et
  stop au marché (taker, glissement), objectifs en limite (maker), échéance au marché.
- **R** = résultat net / (entrée − stop) : le risque prévu. (Ici, le **R brut** est le même calcul sans frais ni
  glissement.)
- Paire retirée de la cote avant la fin de l'horizon : reste vendu à la dernière clôture (`COTATION_ARRETEE`, gardée,
  comme dans `LIGNES_DE_TENDANCE.md`).
- Simulation : `figures_history.play` (copie compilée de `f15.simulate`, prouvée identique), importé sans changement.

## Historique

- 2026-10-08 : déclaré avant tout code, tout téléchargement et tout calcul sur des prix (demande du propriétaire du
  même jour, commit `0d845bb`). Seules lectures sur des données réelles :
  - le registre, en lecture seule (873 essais sur DEVELOPMENT, 8 sur FINAL_TEST, 3 consultations) ;
  - la liste des fichiers des magasins ;
  - le code des sources ;
  - les colonnes `symbol` et `origin` du fichier de prévisions v3.

  Résultats de `CMB1` / `CMB2` non lus à ce moment.
- 2026-10-08 : **relecture `leak-auditor` et décision du propriétaire, aucun résultat vu.**
  - **Décision du propriétaire, « Les deux »** : feu de protection en service hors étude (outil de risque, aucun gain
    annoncé) ; étude corrigée pour devenir informative (§ 1).
  - **Résultats `CMB1` / `CMB2`** : connus du propriétaire (« rien » partout) et rapportés au moment de cette révision.
    Aucune composante ajoutée, retirée ni changée à cause d'eux.
  - **Prévision H24** : le fichier v3 n'est plus utilisé (origines filtrées par une cible future). Les prévisions sont
    recalculées pour toutes les origines aux entrées complètes. Mutation « bougies futures supprimées ou falsifiées ».
    Base et valeur de la même instance. Bascules aux réajustements et effet du jour de semaine déclarés.
  - **`S2`** : plus aucune sortie du panier à cause d'une ouverture future manquante. Vente à la prochaine ouverture
    disponible ou à la dernière clôture (`COTATION_ARRETEE`), avec un test.
  - **Frais en R** : la statistique décisive est **brute, normalisée par la volatilité prévue** (`S2N`). Le net est
    descriptif. `PERTES_RECENTES` en R brut.
  - **Placebos** : rotations par **multiples de 7 jours** sur l'index calendaire, trous `SANS_FEU` ; blocs de
    13 semaines au lieu des trimestres civils ; contrôle « jour de semaine seul : excès nul ».
  - **Contrôle nul** : |z moyen| ≤ 0,15, 500 simulations par cas, test d'uniformité des p-valeurs, couverture de
    l'intervalle, volatilité synthétique à mémoire longue, cas `N3b` descriptif, cas `N4`. En cas d'échec : ni
    exécution ni compte.
  - **Lecture renommée** « persistance infra-trimestrielle », qui doit tenir sans le meilleur épisode rouge.
  - **`LARGEUR`** au top 40 à date.
  - **Fear & Greed** : `d − 2` en direct si `d − 1` manque ; révisions = limite déclarée ; jours entre 23 et 27 en
    descriptif.
  - **Signaux Telegram de 2026** : retirés comme test (§ 10) ; seul `S3` en direct reste, en descriptif.
  - **F17** : base de `PERTES_RECENTES` = dernière année de DEVELOPMENT puis glissement en direct (jamais de calcul
    `S1` sur la période réservée) ; instance propre du modèle pour `VOL_HAUTE` ; `n_trials` = 1 ; séquentiel
    pluriannuel avec bornes d'O'Brien-Fleming et arrêt pour inutilité.
  - **Descriptif** sans p-valeur, avec `n_descriptive` ; `T2`, `T3` et les références passent en descriptif ; une
    composante isolée ne serait testée qu'en direct.
  - **Composantes déjà regardées** sur DEVELOPMENT déclarées (§ 3.4).
  - **Étude rendue informative** : une seule question décisive au niveau 0,05 ; critère d'équivalence δ = 0,03 σ ;
    variante 2018-2025 avec un feu réduit (1 essai, procédure séquentielle) ; règle d'arrêt pour inutilité fondée sur
    la puissance du contrôle positif.
  - **Précisions** :
    - transaction de référence figée (annexe A) ;
    - formules synthétiques du Fear & Greed et du financement ;
    - 20 paires synthétiques ;
    - approximation de `VOL_HAUTE` sur synthétique ;
    - verdict global (§ 5.7) ;
    - mêmes tirages pour les scénarios de frais ;
    - définition d'un épisode avec trous ;
    - date de DefiLlama corrigée (lue depuis le 2020-07-01, croissance sur 7 jours dès le 2020-07-10) ;
    - total d'essais recompté à l'exécution.
  - **Nouveau total** : 2 essais sûrs, 5 au plus sur DEVELOPMENT (au lieu de 7 et 13) ; 0 sur FINAL_TEST ; 1 au
    registre FORWARD.
  - Seule lecture de données faite pour cette révision : le **nombre** de paires USDT de plus de 50 jours dans le
    recensement du top 40 à date (`data/pit/`, colonnes `day`, `symbol`, `month` seulement, aucun prix), pour fixer
    le minimum de `LARGEUR` de la variante 2018.
- 2026-10-08 : **deuxième relecture `leak-auditor` (commit `3438e76`, verdict « à corriger », aucune fuite nouvelle),
  aucun résultat vu.** Corrections :
  - **Marge** : δ « par jour joué » (0,03 σ, choisie pour être atteignable, soit environ 0,15 σ ou 0,6 % par jour
    rouge) est retirée. La marge porte sur **Δ, l'écart par jour rouge à l'intérieur du bloc**, indépendant de la part
    de rouge. Statistique bloc par bloc à poids fixes. Trois candidats (0,05 / 0,10 / 0,15 σ, soit ≈ 0,2 / 0,4 /
    0,6 % par jour et ≈ 0,08 / 0,15 / 0,23 R), avec leurs puissances estimées par formules : **À VALIDER PAR LE
    PROPRIÉTAIRE**, non figée. Borne haute à 90 % toujours rapportée en σ, en % par jour, en % brut et en R.
    § 0 et argument des frais corrigés : le gain se compte par jour rouge sauté.
  - **Contrôle positif** placé à Δ = Δ_min ; règle d'arrêt : puissance de supériorité ≥ 0,50 **et** puissance
    d'équivalence sous `N2` ≥ 0,50, sinon `INSTRUMENT_TROP_FAIBLE` (rien n'est exécuté ni compté), même règle pour la
    variante. Cas `N2t` (Student à 4 degrés de liberté).
  - **Effet de levier** : cas `N3c` (|z moyen| ≤ 0,15) ; biais μ / σ̂ déclaré au § 5.5 (les jours rouges paraissent
    moins mauvais, l'équivalence est rendue plus facile), avec Δ en % brut donné à côté.
  - **Variante 2018** :
    - tableau du § 5.7 reformulé (`FEU_R4` sur une période qui contient à 74 % la principale) ;
    - hypothèse testée fixée pour chaque issue de la principale, `INVERSE` compris (incohérence levée) ;
    - sous-période 2018-03-05 → 2020-02-02 décrite à part ;
    - redondance avec un filtre de tendance déclarée ;
    - panier au top 40 à date.
  - **Survivance** : le panier décisif `S2N` devient le **top 40 à date**. La confirmation C porte sur le sous-panier
    hors des 40 paires de recherche, pour la persistance et, si nécessaire, l'équivalence ou l'inverse (≤ 2 essais).
  - **F17** :
    - dépense d'α de Lan-DeMets (forme O'Brien-Fleming) sur la part d'information (jours rouges / `R_max` = 280) ;
    - quantiles de Student à `B_k − 1` degrés de liberté ;
    - équivalence à ± `c_k` erreurs types ;
    - jours `d − 2` du Fear & Greed et part de `PERTES_RECENTES` allumée comptés en descriptif ;
    - univers d'entraînement de l'instance déclaré.
  - **Règles communes** rétablies (§ 2 bis, reprises de `0d845bb` : coupure des séries avec test de falsification,
    liste fermée, modules gelés, empreintes au registre, une seule tâche lourde à la fois).
  - **Précisions figées** :
    - `MIN_HISTORY_DAYS = 400` non appliqué au `σ̂` des paires (90 jours, comme FACTORS ; déclaré) ;
    - `σ̂ = √(exp(x · β) × facteur de Duan)`, biais de Jensen déclaré ;
    - statistique définie bloc par bloc (pas de dénominateur variable dans les poids) ;
    - G2 : les jours retirés deviennent des trous ;
    - univers d'entraînement du modèle (40 paires de recherche et BTC) ;
    - intervalle par tirage stratifié de fenêtres de 4 semaines dans chaque bloc de 13 semaines.
  - **`n_descriptive`** : à implémenter dans le registre avec un test au moment du code ; environ 5 % de fausses
    alertes descriptives annoncées.
  - **Compte d'essais** inchangé : 2 sûrs, 5 au plus sur DEVELOPMENT (873 → 875, au plus 878, recompté à
    l'exécution) ; 0 sur FINAL_TEST ; 1 au registre FORWARD.
  - Aucune lecture de données réelles pour cette révision (puissances calculées par formules seulement).
