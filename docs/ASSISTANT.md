# Assistant de marché (shadow, mesuré par le test en direct F18_ASSISTANT)

Demande du propriétaire du 2026-10-09 : un assistant qui lit le marché comme un trader prudent et qui n'appelle que
rarement, avec une explication, des niveaux précis et un score, envoyé sur Telegram par le service relais.

> **Shadow : aucun ordre.** CSI ne passe, ne modifie ni n'annule aucun ordre ; l'assistant n'écrit ni dans
> `SignalRegistry` ni dans `signals/` (le test F2 n'est pas touché). **Aucun gain démontré** : chaque appel est mesuré
> en direct par le test F18 contre 20 placebos, et le verdict attendu, au vu de tout le programme, est `NON_DEMONTRE`
> ou `INSUFFISANT`. Le score classe les appels, il ne décide de rien. Rien ici n'est une probabilité ni une
> promesse.

Code : `assistant/rules.py` (règles pures), `assistant/evaluate.py` (lectures et évaluation), `assistant/state.py`
(état pour le propriétaire), `assistant/outbox.py` (boîte Telegram), `forward/f18.py` (test en direct). Tous ces
modules sont **gelés** par F18 à son démarrage : changer une constante ou un texte arrête le test pour de bon
(refaire = nouveau test, nouvel essai). Pré-inscription : `FORWARD_TESTS.md`, section `F18_ASSISTANT`.

## 1. Quand et sur quoi

- **Quand** : à chaque clôture 4 h UTC (00/04/08/12/16/20), au passage horaire de la surveillance qui suit, dès que la
  bougie 1 h qui clôture le bloc est en magasin (latence comprise dans `available_at`). Une clôture n'est évaluée
  qu'une fois ; une clôture manquée (machine éteinte, magasin en retard) n'est jamais rejouée après coup.
- **Univers** : les paires de la liste halal figée au démarrage de F15 (166 paires), lues dans le magasin de F15
  (`forward_figures/data`, bougies 1 h tenues à jour chaque heure par F15), **en lecture seule**. L'assistant ne
  télécharge rien. 4 h et 1 jour sont agrégés depuis 00:00 UTC, blocs complets seulement.
- **Causalité** : seules les bougies clôturées à la clôture évaluée `t` et connues à l'instant du passage entrent ;
  la journée de référence est la dernière journée UTC complète (la veille). Falsifier les bougies postérieures à `t`
  ne change ni l'évaluation ni les niveaux (test `test_causality_future_candles_change_nothing`).

## 2. Les règles, dans l'ordre

### 0. Marché entier

| Lecture | Source | Effet |
|---|---|---|
| Feu de protection ROUGE | `risk/market_light.current` aux données connues à `t` | silence total |
| Feu ORANGE | idem | appels marqués « taille réduite » |
| Feu INCONNU | idem | on continue avec la règle BTC seule, marqué |
| BTC clôture journalière ≤ EMA50 journalière | BTCUSDT du magasin (sinon du magasin de la surveillance) | silence total |
| BTC inconnu (journée incomplète, historique court) | idem | silence (prudence) |

### 1. Régime de la paire (journalier)

EMA20 et EMA50 des clôtures journalières, départ par moyenne simple (comme `risk/market_light.ema`), sur 200 jours
de bougies lus ; au moins 60 journées complètes, sinon la paire est INDECIS.

| Régime | Condition |
|---|---|
| HAUSSE | clôture > EMA50 **et** EMA20 > EMA50 **et** EMA50 d'aujourd'hui > EMA50 d'il y a 3 jours |
| BAISSE | clôture < EMA50 **et** EMA20 < EMA50 → rien |
| RANGE | ni l'un ni l'autre **et** couloir clair sur 30 jours (ci-dessous) |
| INDECIS | tout le reste → rien |
| NON_EVALUABLE | bougie 4 h de clôture ou journée de la veille absente du magasin |

**Couloir clair** : pivots ZigZag 4 h (m = 2,5 ATR, `patterns/primitives.zigzag`) des 30 derniers jours, pivots bas
regroupés à 0,5 ATR(4 h) en supports, pivots hauts en résistances (`technical/analysis.cluster_levels`) ; support =
le plus proche sous la clôture touché ≥ 2 fois, résistance = la plus proche au-dessus touchée ≥ 2 fois ; hauteur
(résistance − support) ≥ 4 ATR journaliers (ATR14 de Wilder).

### 2. Configurations (jamais de cassure)

**HAUSSE — « repli puis reprise »**
- zone de valeur = [min(EMA20 journalière, support 4 h le plus proche sous la clôture) ; max(EMA20 journalière, VAL
  du profil de volume quote des 30 derniers jours, `patterns/volume.volume_profile`)] ; sans support 4 h, la borne
  basse est l'EMA20 ;
- le plus bas des 5 dernières bougies 4 h est dans la zone **et** la bougie qui vient de clôturer clôture au-dessus de
  la zone **et** son volume quote > moyenne des 20 bougies 4 h précédentes ;
- entrée = clôture ; stop = plus bas du repli − 0,1 ATR(4 h) ; TP1 = entrée + 1 R ; TP2 = résistance 4 h la plus
  proche, ou entrée + 2 R sans résistance (voir le filtre gain/risque : ce cas est alors refusé).

**RANGE — « rejet du support »**
- une des 3 dernières bougies 4 h a touché le support (plus bas ≤ support + 0,25 ATR(4 h)) **et** la bougie qui vient
  de clôturer clôture au-dessus du support avec un volume quote > moyenne des 20 précédentes ;
- entrée = clôture ; stop = plus bas de la mèche − 0,1 ATR(4 h) ; TP1 = entrée + 1 R ; TP2 = résistance du couloir.

**Refus communs** : résistance à moins de 1 R au-dessus de l'entrée (`RESISTANCE_PROCHE`) ; stop collé au prix.
Niveaux arrondis au pas de cotation quand il est connu (stop vers le bas, objectifs vers le haut ; sinon 8 chiffres
significatifs).

### 3. Filtres (tous vrais, sinon refus avec la raison)

| Filtre | Règle | Raison de refus |
|---|---|---|
| Liquidité à l'instant | carnet `/api/v3/depth` (1 000 niveaux, client public en lecture seule), lu **seulement** pour les candidats qui ont passé tout le reste ; `liquidity_log.book_metrics` : écart < 0,3 %, glissement d'un achat de 500 USDT < 0,2 %, achats ≥ ventes à ±1 % | `CARNET_INJOIGNABLE`, `CARNET_ECART`, `CARNET_GLISSEMENT`, `CARNET_DESEQUILIBRE` |
| Stop cohérent avec la volatilité | (entrée − stop)/entrée entre 1 et 3 × le mouvement attendu sur 24 h : prévision H24 de F12 (`risk/advice`, inscrite au plus tard à `t`) pour les 16 paires qui l'ont, sinon écart-type des rendements 1 h des 7 derniers jours × √24 (source marquée) | `STOP_TROP_SERRE`, `STOP_TROP_LARGE`, `VOLATILITE_INCONNUE` |
| Gain/risque | plan net ≥ 1,5 R : ½ à TP1 + ½ à TP2, frais et glissement taker aller-retour (scénario central de `forward/costs`) | `GAIN_RISQUE` |
| Timing macro | aucun événement de `forward/light.macro_events` dont le jour UTC recouvre [t − 2 h ; t + 2 h] | `TIMING_MACRO` |
| News | aucune news de risque (`news/risk.risk_items`) des 24 h précédant `t`, connue à `t`, visant la paire (base dans les actifs étiquetés, ou base/symbole en mot entier du titre) ; base illisible → refus | `NEWS_RISQUE`, `NEWS_INJOIGNABLES` |
| Discipline | un seul appel actif par paire ; 48 h de repos par paire après la sortie d'un appel ; 3 appels par jour UTC au plus sur tout l'univers, les mieux classés d'abord | `APPEL_ACTIF`, `REPOS_48H`, `QUOTA_JOUR` |

Les appels actifs et les repos sont lus dans le journal de F18 : un appel sans RESOLUTION est rejoué sur les
bougies ≤ t pour savoir s'il est encore ouvert ou depuis quand il est sorti.

### 4. Score (0-100, affiché, ne décide pas)

| Composante | Points | Formule |
|---|---|---|
| Touches du niveau | 0-25 | 25 × min(1, touches / 5) (support du couloir en RANGE, support 4 h de la zone en HAUSSE) |
| Déséquilibre du carnet à ±1 % | 0-25 | 25 × min(1, max(0, (achats − ventes)/(achats + ventes)) / 0,5) |
| Volume de la bougie de confirmation | 0-20 | 20 × min(1, max(0, (multiple − 1) / 2)) : 3 × la moyenne = 20 |
| Distance au stop / mouvement H24 | 0-15 | 15 × min(1, max(0, (ratio − 1) / 2)) : ratio 1 = 0, ratio 3 = 15 |
| BTC au-dessus de son EMA20 journalière | 0 ou 15 | |

### 5. Gestion de chaque appel (identique pour les placebos)

- entrée au prix de clôture 4 h, frais et glissement taker (`forward/costs`, central et défavorable) ;
- **stop à la clôture** : perdu si une clôture 4 h ≤ stop ; sortie à cette clôture ;
- **stop de secours dur** à entrée − 1,5 × (entrée − stop), touché intrabar sur bougie 1 h : sortie à ce niveau (ou à
  l'ouverture si elle est déjà dessous) ;
- **TP1** : moitié à +1 R, touché intrabar sur bougie 1 h ; puis stop de clôture remonté à l'entrée (le stop de secours
  ne bouge pas) ; **TP2** : l'autre moitié ;
- **10 jours** au plus, puis sortie à la clôture de la dernière bougie 1 h ;
- **prudence** : une bougie 1 h qui touche à la fois un objectif et le stop de secours compte le stop ; toutes les
  sorties sont comptées au marché (taker) ;
- R = résultat net / (entrée − stop). Bougies 1 h manquantes : appel EN_COURS ; 2 jours après la fin de la fenêtre
  des placebos, `TROU` (hors mesure).

### 6. Mesure (F18)

20 placebos par appel : entrées au marché sur la même paire à des clôtures 1 h tirées sans remise dans [t − 84 h ;
t + 84 h] hors [t − 4 h ; t + 4 h], graine `sha256("F18_ASSISTANT:" + call_id)`, même géométrie en %, même gestion,
mêmes frais. Métrique principale : R net moyen (IC95 par blocs de 7 jours) ; secondaire : excès sur les placebos
(niveau 1 − 0,05/2). Verdict `forward/f4.verdict` : `INSUFFISANT` sous 30 appels résolus ou 10 jours ;
`SUPERIEUR_AU_HASARD` si les deux intervalles sont > 0 dans les deux scénarios ; `INFERIEUR_AU_HASARD` si l'IC du R
est < 0 dans les deux ; sinon `NON_DEMONTRE`. Revue intermédiaire à 42 jours, évaluation à 84 jours. Journal :
EVALUATION (par passage : feu, BTC, régimes, candidats, refus par raison, discipline), APPEL (décision complète,
explication, placebos), RESOLUTION, VERDICT, CLOTURE.

## 3. Paramètres figés

| Paramètre | Valeur | Paramètre | Valeur |
|---|---|---|---|
| Bougies 1 h lues | 200 jours | Journées complètes minimum | 60 |
| EMA | 20 et 50, pente sur 3 jours | Couloir | 30 jours, pivots à 0,5 ATR(4 h), 2 touches, 4 ATR journaliers |
| Repli | plus bas des 5 bougies 4 h | Touche du support | 3 bougies, 0,25 ATR(4 h) |
| Stop | plus bas − 0,1 ATR(4 h) | Volume | > moyenne des 20 bougies 4 h |
| Résistance proche | < 1 R : refus | Plan net | ≥ 1,5 R (½ TP1 + ½ TP2, taker) |
| Carnet | écart < 0,3 %, 500 USDT < 0,2 %, déséquilibre ≥ 0 à ±1 % | Stop / mouvement H24 | entre 1 et 3 |
| Volatilité réalisée | 7 jours, 1 h × √24 | Macro | jour de l'événement ± 2 h |
| News | 24 h | Discipline | 1 actif par paire, 48 h, 3 par jour UTC |
| Stop de secours | 1,5 R | TP1 | moitié à +1 R |
| Durée | 10 jours | Placebos | 20, ±5…84 h |
| Verdict | 30 résolus, 10 jours, IC95 blocs de 7 jours, 10 000 tirages, graine 20261009 | Trou | 2 jours après la fenêtre |

Aucun de ces nombres n'a été réglé sur un résultat : ils viennent de la demande du propriétaire ou de conventions déjà
en service (ATR14, ZigZag 2,5 ATR, EMA50, blocs de 7 jours). Rien ne sera réglé après coup.

## 4. Choix faits là où la demande était impossible ou ambiguë (option la plus prudente)

- **« TP1 ≥ 1,5 R net »** est impossible puisque TP1 = +1 R par construction : le filtre porte sur le **plan net**
  (½ à TP1 + ½ à TP2, frais taker aller-retour) ≥ 1,5 R. Conséquence : un appel HAUSSE sans résistance (TP2 = +2 R)
  est toujours refusé `GAIN_RISQUE` ; il faut une résistance à ≈ 2,25 R ou plus.
- **Événements macro** : le calendrier ne donne que le jour, pas l'heure ; tout le jour UTC de l'événement est tenu
  pour sensible (plus 2 h de chaque côté de minuit).
- **Base de news illisible** : refus (`NEWS_INJOIGNABLES`), comme pour le carnet. Une base vide (aucune news) ne
  refuse rien.
- **BTC inconnu** (journée incomplète) : silence.
- **Sorties au marché partout**, y compris aux objectifs, et stop de secours gardé à −1,5 R après TP1.
- **Feu lu aux données connues à `t`** (pas à l'instant du passage) pour rester causal ; il peut donc différer de
  la carte « Météo » calculée à l'instant.
- **Pas de cotation** : les 16 paires de la configuration l'ont ; les autres sont arrondies à 8 chiffres
  significatifs. `data.tick_size` n'est pas gelé par F18.
- **Prix d'entrée** : la clôture 4 h, alors que l'appel n'est connu que quelques minutes plus tard ; le glissement
  taker est compté, mais aucun prix réel d'exécution n'existe (limite déclarée dans la pré-inscription).

## 5. Ce qui était déjà réfuté, ce qui est nouveau

Réfuté sur l'historique (`FIGURES_HISTORIQUE.md`, `SCREENING.md`, `LIGNES_DE_TENDANCE.md`, `PLAN_DE_TRAVAIL.md` § 6) :
les niveaux seuls (supports, résistances, chiffres ronds), le repli sur une EMA en 15 minutes, les votes de
signaux, les cassures de pivots. L'assistant ne rejoue aucune de ces règles isolément : il exige un contexte (feu,
BTC, régime), une configuration confirmée par le volume, un carnet liquide, un stop à l'échelle de la volatilité
attendue, un timing propre et une discipline. C'est cette **combinaison** qui est nouvelle et qui est mesurée ; rien
ne dit qu'elle fera mieux que le hasard.

## 6. Comment le lire

- **Carte « Assistant »** en tête de l'onglet Marché : feu, BTC, régimes, appels actifs (paire, régime, entrée, stop,
  TP1, TP2, R latent, score, explication), derniers refus, résumé.
- **`csi assistant etat`** : le même état en terminal ; **`csi assistant evaluer --now <iso>`** : évaluation à blanc
  d'une clôture (rien n'est journalisé, aucun message, aucun état), avec `--magasin <dossier>` pour lire un autre
  magasin de bougies et `--sans-carnet` pour ne pas interroger Binance.
- **API** : `GET /assistant` (contenu de `state/assistant.json`, avec `resume` : cinq lignes), `GET /assistant/outbox`
  (messages EN_ATTENTE, 20 au plus, du plus ancien au plus récent), `POST /assistant/sent` `{"ids": [...]}` →
  `{"marked": n}`. Un message en attente depuis plus de 6 h passe EXPIRE.
- **Telegram** (via le service relais) : un message par APPEL (paire, régime, configuration, entrée, stop de clôture
  4 h, stop de secours, TP1, TP2, R, score, explication) et un par RESOLUTION, toujours terminés par « Shadow : aucun
  ordre. Test en direct F18, aucun gain démontré. »
- **Rapport quotidien des tests en direct** : section F18 (évaluations, candidats, appels, refus par raison, R moyen
  et intervalles, taux de TP1/TP2, pire série, par régime).

## 7. Comptage à blanc avant le démarrage (aucun résultat de transaction)

Balayage du 2026-10-09 sur les 16 paires de la configuration, 60 jours de clôtures 4 h du magasin local
(`csi assistant evaluer` à blanc, feu INCONNU, carnet non interrogé) : 5 760 lectures de régime (3 480 HAUSSE,
1 387 BAISSE, 893 INDECIS, 0 RANGE), 92 configurations validées, refus : 45 résistance proche, 38 stop trop serré,
5 gain/risque, 1 stop trop large ; 3 candidats arrivés au carnet ; 76 clôtures de silence (BTC sous son EMA50). Soit
≈ 1 appel par mois pour 16 paires ; sur 166 paires, quelques appels par semaine au plus. `INSUFFISANT` est probable.
