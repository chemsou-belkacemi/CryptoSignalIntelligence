# Criblage des familles D à I (2026-09-30)

Commande : `screen`. Code : `research/screen.py`. Registre : expériences de type `SCREEN`.

## Pourquoi un criblage

Sur les 16 paires, les stratégies A, B et C sont REJECTED. En décomposant leur espérance, on voit
qu'**avant frais elles n'ont pratiquement aucun avantage** (A ≈ +0,01 R, B ≈ −0,06 R, C ≈ +0,03 R) ;
les coûts (0,11 à 0,24 R par trade) font le reste. Construire une stratégie complète (stops, cibles,
walk-forward de 40 minutes) sur une entrée sans avantage brut ne peut pas créer cet avantage. Le
criblage vérifie donc d'abord, pour chaque condition d'entrée, si le rendement futur brut dépasse
la dérive de la paire et le seuil des coûts.

## Protocole

- 16 paires, période DEVELOPMENT seulement (jusqu'au 2025-06-30) : le test final réservé n'est pas lu,
  BTC de contexte compris (avant l'audit du 2026-09-30, BTC hors univers était lu en entier ; sans
  effet sur les deux criblages faits, BTC étant dans l'univers).
- Événement évalué à la clôture ; entrée à l'ouverture suivante ; sortie à la clôture après 1 h, 4 h
  ou 24 h. Aucun stop, aucune cible. Pour G et I (1h), l'entrée se faisait à la clôture de la bougie
  de classement jusqu'au 2026-09-30 ; elle se fait depuis à l'ouverture suivante, comme D, E, F, H.
- Les classements G et I ne portent que sur l'univers demandé ; BTC sert de facteur à I sans être
  candidat.
- Excès = rendement − moyenne inconditionnelle de la même paire au même horizon.
- IC95 de la moyenne pondérée par événement, par tirage de blocs de 10 jours consécutifs.
- « Passe » = rendement brut moyen > seuil de coûts aller-retour (0,26 % en coûts centraux jusqu'au 2026-10-02, 0,21 % ensuite : `PROTOCOL.md`) ET borne
  basse de l'IC95 de l'excès > 0.
- 6 conditions × 3 horizons = 18 essais : un seul intervalle qui exclut zéro de peu peut être un hasard.

Définitions exactes des conditions : `CONDITIONS` dans `research/screen.py` (reprises dans la sortie
de la commande).

## Résultats (`SCREEN-20260930T093435Z-2874e7`)

| Condition | Horizon | Événements | Rendement brut | Excès | IC95 excès | Paires > 0 | Passe |
|---|---|---|---|---|---|---|---|
| D breakout retest | 4 h | 36 000 | −0,00 % | −0,04 % | [−0,08 ; +0,01] | 25 % | non |
| E failed breakdown reclaim | 1 h | 27 230 | +0,03 % | +0,03 % | [−0,01 ; +0,05] | 81 % | non |
| E failed breakdown reclaim | 4 h | 27 230 | +0,11 % | +0,08 % | [+0,01 ; +0,15] | 94 % | non (sous le seuil de coûts) |
| E failed breakdown reclaim | 24 h | 27 216 | +0,36 % | +0,16 % | [−0,12 ; +0,42] | 81 % | non |
| F squeeze breakout | 24 h | 29 298 | −0,06 % | −0,26 % | [−0,51 ; −0,00] | 0 % | non |
| H VWAP journalier | 24 h | 74 270 | +0,28 % | +0,09 % | [−0,12 ; +0,29] | 88 % | non |
| G force relative (top 3) | 24 h | 29 403 | +0,32 % | +0,11 % | [−0,14 ; +0,35] | 69 % | non |
| I momentum résiduel (top 3) | 24 h | 28 989 | +0,27 % | +0,05 % | [−0,19 ; +0,30] | 60 % | non |

Tableau complet (18 lignes) : `reports/SCREEN-20260930T093435Z-2874e7/summary.json`.

## Lecture

- **Aucune condition ne passe.** D (retest de cassure), F (compression puis cassure) et H (VWAP) n'ont
  pas d'avantage ; F est même défavorable sur toutes les paires à 24 h.
- G et I (force relative, momentum résiduel) ont des rendements bruts positifs à 24 h, mais c'est la
  hausse générale : après retrait de la dérive, l'excès n'est pas distinguable de zéro.
- **E (retour au-dessus d'un support enfoncé)** est la seule condition positive de façon cohérente
  (94 % des paires, 80 % des années à 4 h), mais son rendement brut à 4 h (+0,11 %) reste sous le seuil
  de coûts (0,26 %). Avec 18 essais, un IC qui exclut zéro de si peu est un indice faible.
- Conséquence : aucune fiche ni walk-forward n'est lancé automatiquement. E pourrait être étudiée sur
  un horizon plus long ou avec une sortie qui laisse courir, mais cette idée vient d'avoir vu les données
  de DEVELOPMENT : son walk-forward sur la même période serait contaminé ; seule une confirmation sur
  une période jamais consultée (test final réservé, ou observation prospective en shadow) compterait.

## Criblage à horizons longs (déclaré le 2026-10-01, avant exécution)

Hypothèse : à 1 h, 4 h et 24 h, les frais aller-retour (0,26 %) dépassent l'avantage brut ; sur 3 à 7
jours, le mouvement attendu est plus grand et les frais pèsent moins. C'est le dernier levier non testé
avec les conditions existantes.

- Commande : `screen --horizon 72 --horizon 168` (3 et 7 jours), mêmes six conditions D à I, mêmes
  règles (entrée à l'ouverture suivante, sortie à la clôture de t+h, dérive retirée, DEVELOPMENT seul).
- IC95 par blocs de max(10 jours, 2 × horizon) : 14 jours à 7 jours, les rendements se chevauchant.
- 6 conditions × 2 horizons = **12 essais** de plus au programme (`program_trials`).
- « Passe » inchangé : rendement brut moyen > seuil de coûts ET borne basse de l'IC95 de l'excès > 0.
  Une condition qui passe ne devient une stratégie qu'avec une fiche, un walk-forward durci et une
  confirmation sur données non vues.

### Résultat (`SCREEN-20261001T015122Z-8aa8f6`, 12 essais) : rien ne passe

| Condition | 72 h : excès moyen % [IC95] | 168 h : excès moyen % [IC95] |
|---|---|---|
| D retest de cassure | +0,08 [−0,49 ; +0,68] | +0,13 [−1,33 ; +1,72] |
| E retour sur support | −0,10 [−0,79 ; +0,57] | −0,25 [−1,85 ; +1,30] |
| F compression puis cassure | −0,30 [−0,94 ; +0,35] | −0,19 [−1,63 ; +1,33] |
| H reprise du VWAP | +0,05 [−0,54 ; +0,67] | +0,05 [−1,39 ; +1,63] |
| G force relative top 3 | +0,14 [−0,51 ; +0,78] | −0,04 [−1,47 ; +1,37] |
| I momentum résiduel top 3 | +0,08 [−0,65 ; +0,84] | −0,23 [−1,83 ; +1,45] |

- Le rendement brut moyen dépasse les frais à ces horizons (+0,3 à +1,5 %), mais c'est la **dérive du
  marché** (hausse 2021-2025) : acheter à n'importe quel moment rapportait autant. Une fois la dérive
  retirée, l'excès est nul, avec des intervalles très larges des deux côtés.
- Allonger l'horizon ne crée donc pas d'avantage de **timing** avec ces conditions ; il ne fait que
  laisser passer la tendance générale, qui n'est pas un signal.
- Programme : 266 essais sur DEVELOPMENT.

## Criblage J : flux d'ordres, offre nouvelle, valeur on-chain (déclaré le 2026-10-02, avant exécution)

Étape 6 du plan de travail validé le 2026-10-02. Journées UTC du magasin long (bougies 1 h agrégées), univers de
recherche admis par le screening halal, DEVELOPMENT seul (jusqu'au 2025-06-30), première journée le 2019-01-01.
Une condition vraie le jour d est connue à d+1 00:00 : achat à l'ouverture de 01:00 de d+1, vente à la clôture de
23:00 h−1 jours plus tard (h = 1, 7, 30 jours) ; excès = rendement − dérive de la paire sur le même horizon.

| Condition | Définition |
|---|---|
| J1_FLOW_BUY_TOP | part des achats au marché (taker) dans le volume en USDT du jour ≥ 90e centile des 365 journées précédentes de la paire (au moins 200) |
| J2_FLOW_SELL_BOTTOM | part ≤ 10e centile (pression vendeuse, achat à contre-courant) |
| J3_NEW_SUPPLY_VETO | paire cotée depuis 30 à 180 jours (offre nouvelle) : condition de **veto** |
| J4_MVRV_LOW | MVRV de BTC ou d'ETH (CoinMetrics, API communautaire gratuite, `CapMVRVCur`) ≤ 20e centile des 730 jours précédents (au moins 365) ; valeur du jour d supposée connue à d+2 |
| J5_MVRV_HIGH_VETO | MVRV ≥ 80e centile : condition de **veto** |

- 5 conditions × 3 horizons = **15 essais** de plus au programme.
- IC95 de l'excès par blocs de max(10 jours, 2 × horizon) ; « passe » (J1, J2, J4) = rendement brut moyen > seuil
  de coûts aller-retour (0,26 %) ET borne basse de l'IC95 > 0 ; « veto justifié » (J3, J5) = borne haute de l'IC95
  < 0. Audit des fuites avant tout résultat : drapeaux recalculés avec les seules bougies antérieures à 4 jours
  tirés au hasard (identiques), mutation (part d'achats lue sur le lendemain) détectée.
- Fenêtre dégénérée (centiles haut et bas égaux, série constante) : aucun drapeau, ni haut ni bas.
- Dérive retirée : celle de la paire sur toute la période (convention D à I), sauf pour **J3**, où la dérive de
  référence est le rendement moyen, au même horizon, des (paire, journée) où la paire a plus de 180 jours de
  cotation : la dérive propre d'une paire jeune serait faite de sa fenêtre de veto et tirerait l'excès vers zéro.
- Audit des fuites : la mutation est passée par la **même coupe** que les drapeaux honnêtes (calcul tronqué contre
  calcul complet) ; si elle n'y faisait aucune différence, l'audit ne verrait pas une fuite et refuse de conclure.
- Attendu : rien ne passe à 1 jour (coûts) ; les conditions de veto peuvent se justifier (une paire récente ou un
  MVRV élevé précèdent souvent des rendements faibles), ce qui ne serait pas une stratégie, seulement un filtre.
  15 tests à 95 % sans correction de multiplicité : un seul « passe » se lit comme une piste, jamais comme un résultat.
- À 30 jours, l'intervalle exige 10 blocs de 60 journées à événement ; pour J4 et J5 (BTC et ETH seuls, ≈ 20 % des
  journées), il sera probablement incalculable : ligne sans verdict, c'est attendu et ce n'est pas un « passe ».
- Limites : MVRV n'existe que pour BTC et ETH (régimes de plusieurs mois, très peu d'observations indépendantes), et
  la série est la version téléchargée le 2026-10-02 (CoinMetrics peut la réviser ; empreinte enregistrée) ; la part
  des achats au marché est celle de Binance seule ; les « 30 à 180 jours » dépendent de la date de cotation sur
  Binance, pas de l'émission du token ; **univers de survivantes** : les cotations retirées manquent, ce qui biaise J3
  vers « veto non justifié » — un J3 justifié tient a fortiori, un J3 non justifié ne conclut rien.

### Résultat (`SCREEN-20261002T165546Z-0994a2`, 15 essais, programme 733) : rien ne passe, aucun veto justifié

Code du commit `99559a0` (après la relecture), audit des fuites réussi (4 journées recalculées identiques, mutation
détectée à travers la coupe), 40 paires admises, 2 374 journées, MVRV BTC/ETH 3 103 jours chacun (empreintes
enregistrées).

| Condition | 1 j : excès % [IC95] | 7 j : excès % [IC95] | 30 j : excès % [IC95] | Paires > 0 |
|---|---|---|---|---|
| J1 achats au marché ≥ 90e centile (6 500 év.) | −0,11 [−0,34 ; +0,11] | +0,11 [−1,28 ; +1,57] | +0,53 [−6,20 ; +8,23] | 35–50 % |
| J2 achats au marché ≤ 10e centile (7 200 év.) | **−0,34 [−0,57 ; −0,12]** | **−1,45 [−2,73 ; −0,17]** | −4,40 [−9,92 ; +2,70] | 8–18 % |
| J3 offre nouvelle, veto (4 400 év., 30 paires) | +0,33 [−0,02 ; +0,68] | +2,91 [+0,13 ; +6,10] | +15,3 [−2,0 ; +35,4] | 47–57 % |
| J4 MVRV bas (820 év., BTC et ETH) | −0,18 [−0,55 ; +0,09] | −1,02 [−3,48 ; +0,85] | −3,45 (IC incalculable) | 0 % |
| J5 MVRV haut, veto (1 360 év., BTC et ETH) | +0,20 [−0,06 ; +0,47] | +1,30 [−0,43 ; +3,18] | +4,67 [−4,48 ; +14,47] | 100 % |

Tableau complet : `reports/SCREEN-20261002T165546Z-0994a2/summary.json`.

- **Aucune condition ne passe** (J1, J2, J4) ; **aucun veto n'est justifié** (J3, J5 : borne haute jamais < 0).
- J2 est la seule ligne dont l'intervalle exclut zéro, et dans le **mauvais sens** : acheter après une journée de
  forte pression vendeuse rapporte moins que la dérive de la paire à 1 et 7 jours (8 à 18 % des paires au-dessus de
  zéro). Ce n'était pas une condition de veto déclarée : c'est une observation après coup, à inscrire comme
  **piste de veto** (« pas d'achat le lendemain d'une pression vendeuse extrême ») et rien d'autre ; elle n'entre
  dans aucune règle sans un test sur données non vues.
- J3 va à l'inverse d'un veto : les paires récentes ont fait **mieux** que les paires âgées (+2,9 % à 7 jours,
  intervalle juste au-dessus de zéro). C'est exactement le biais de survivance déclaré (les cotations qui ont mal
  tourné ont été retirées de la cote et manquent) : ni veto, ni signal, ligne inexploitable.
- J4 et J5 (MVRV) : deux actifs, des régimes de plusieurs mois ; l'intervalle est incalculable à 30 jours pour J4
  comme annoncé, et rien n'est distinguable de zéro ailleurs. Un MVRV élevé n'a pas précédé de rendements plus
  faibles sur 2019-2025.
- Conséquence : aucune fiche, aucun walk-forward ; pas de filtre ajouté. Programme : 733 essais sur DEVELOPMENT.

## Criblage K : pivots confirmés (déclaré le 2026-10-02, avant exécution)

Étape 8 du plan de travail (seul criblage de géométrie retenu par l'étude du 2026-10-02 ; Fibonacci, XABCD et
triangles écartés : ≥ 15 essais pour des équivalents de B, D, F nuls). Code : `research/pivot_screen.py` ;
commande `csi screen-pivot --universe-file …` ; tests `tests/test_pivot_screen.py`.

- Bougies **4 h** et **1 jour** reconstruites des bougies 1 h du magasin long (blocs alignés UTC, seulement les
  blocs complets : 4 et 24 bougies) ; univers admis par le screening halal ; DEVELOPMENT seul, événements à partir
  du 2019-01-01.
- **Pivot** : plus haut (plus bas) d'une bougie i strictement au-dessus (au-dessous) des k = 3 bougies qui la
  précèdent et au moins égal à celles des k = 3 bougies qui la suivent ; il n'existe qu'à la clôture de la bougie
  i + 3 et n'est **utilisable qu'à partir de la bougie suivante**. Un niveau est le dernier pivot confirmé ; il
  expire après 100 bougies.
- K1_SUPPORT_BOUNCE : à la clôture de t, le plus bas de t touche le support (≤ support × 1,005), la clôture est
  au-dessus du support et haussière (clôture > ouverture), la clôture de t − 1 était au-dessus du support ; **un seul
  événement par niveau**.
- K2_RESISTANCE_BREAK : première clôture au-dessus de la résistance (clôture de t − 1 ≤ résistance) ; un seul
  événement par niveau.
- Entrée à l'ouverture de t + 1, sortie à la clôture de t + h : h = 6 bougies de 4 h (24 h) et 7 bougies journalières
  (7 jours). Excès = rendement − dérive de la paire au même horizon ; IC95 par blocs de max(10 jours, 2 × horizon) ;
  « passe » = rendement brut moyen > seuil de coûts ET borne basse de l'IC95 > 0.
- 2 conditions × 2 cadres = **4 essais** de plus au programme ; un seul « passe » se lit comme une piste.
- Audit des fuites avant tout résultat : événements recalculés avec les seules bougies antérieures à 4 instants tirés
  au hasard (identiques jusqu'à la coupe) ; mutation : un pivot utilisé dès sa bougie i (avant ses 3 bougies de
  confirmation) doit changer les événements à travers la coupe.
- Attendu : rien ne passe (équivalents D et E nuls à 1–24 h) ; la seule nouveauté est le cadre 1 jour.
- Limites : survivantes ; blocs de 4 h et 1 jour alignés UTC (un autre alignement donnerait d'autres pivots) ;
  k = 3 et 100 bougies sont des choix a priori, non optimisés, et ne seront pas retouchés après lecture.

### Résultat (`SCREEN-20261002T170108Z-e89978`, 4 essais, programme 737) : une ligne passe, à lire comme une piste

Code du commit `f122845`, audit des fuites réussi (8 coupes juste après un pivot tiré au hasard, niveaux et
événements identiques, mutation détectée sur les deux cadres), 40 paires admises, 290 à 2 843 bougies journalières
par paire.

| Condition | Cadre, horizon | Événements | Rendement brut | Excès | IC95 excès | Paires > 0 | Années > 0 | Passe |
|---|---|---|---|---|---|---|---|---|
| K1 rebond sur support | 4 h, 24 h | 11 317 | +0,04 % | −0,14 % | [−0,37 ; +0,09] | 33 % | 2/7 | non |
| K1 rebond sur support | 1 j, 7 j | 1 361 | +0,17 % | −1,18 % | [−2,41 ; +0,53] | 28 % | 2/7 | non |
| K2 cassure de résistance | 4 h, 24 h | 14 518 | +0,37 % | +0,18 % | [−0,07 ; +0,45] | 75 % | 4/7 | non |
| K2 cassure de résistance | 1 j, 7 j | 2 350 | +3,35 % | **+2,04 %** | **[+0,19 ; +4,55]** | 78 % | 4/7 | **oui** |

Tableau complet : `reports/SCREEN-20261002T170108Z-e89978/summary.json`.

- **Le rebond sur support (K1) n'a aucun avantage**, sur aucun cadre : excès négatif, un tiers des paires au-dessus
  de zéro. Acheter un support « confirmé » ne vaut pas mieux qu'acheter n'importe quand.
- **La cassure d'une résistance confirmée en bougies journalières (K2, 1 j)** passe la règle déclarée : +3,35 % brut
  sur 7 jours (loin au-dessus des 0,26 % de coûts), +2,04 % d'excès sur la dérive de la paire, borne basse de
  l'IC95 à +0,19 %, 78 % des paires au-dessus de zéro. En 4 h, la même idée est positive mais ne passe pas
  (borne basse −0,07 %).
- **Lecture honnête, déclarée avant le run** : un seul « passe » se lit comme une piste. La borne basse est à
  0,19 % pour 737 essais au programme ; seules 4 années sur 7 sont positives (le gain tient aux années de hausse
  2019, 2021, 2023, 2024 ; il est négatif en 2020, 2022 et 2025) ; les 2 350 événements de 7 jours se chevauchent
  (blocs de 14 jours, 40 paires fortement corrélées) ; l'univers est celui des survivantes ; et l'équivalent D
  (cassure-retest en 15 min / 1 h) était nul. Cette condition est **contaminée** par le fait d'avoir été vue sur
  DEVELOPMENT : aucune fiche, aucun walk-forward, aucun signal. La seule suite honnête est une confirmation sur
  des données jamais consultées : test en direct pré-inscrit (voir `FORWARD_TESTS.md`, F10 si inscrit) ou période
  finale réservée, décision du propriétaire.
- Programme : 737 essais sur DEVELOPMENT.

## Criblage K à date (déclaré le 2026-10-03, avant exécution)

Les conditions K1 et K2, mêmes règles, sur le **top 40 à date** (paires retirées de la cote comprises) : déclaration complète, lecture et limites dans [UNIVERSE_PIT.md](UNIVERSE_PIT.md) § 2. 4 essais. Question unique : K2 à 1 jour tient-il hors biais de survivance ?

## Historique

- `SCREEN-20260930T093324Z-20101b` : premier passage, **intervalles faux** (moyenne pondérée par jour
  alors que la moyenne affichée l'est par événement). Conservé dans le registre, à ne pas utiliser.
  Corrigé et testé (`test_confidence_interval_brackets_the_reported_event_weighted_mean`).
