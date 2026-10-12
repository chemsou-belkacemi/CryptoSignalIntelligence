# Filtre de marché sur les signaux WHALE HUNTING : règle déclarée avant tout calcul

Demande du propriétaire du 2026-10-12 : « comment améliorer les résultats qu'on a ? où est le problème ? comment
éviter le hasard ? ». Le bilan WHALE HUNTING du 2026-10-11 (corrigé : ordres limites publiés au-dessus de TP1
compris) ne montre aucun gain démontré : la tranche TP1 rapporte +0,17 % par trade, l'intervalle contient 0, la
1re moitié est positive et la 2de négative. Une explication possible : les signaux d'achat gagnent quand le marché
monte et perdent quand il baisse. Ce document fixe **une seule** règle de filtre, **avant** de la calculer.

> **Ce test est une indication, pas une preuve.** Il porte sur 6 semaines de signaux (2026-08-28 → 2026-10-10) :
> un filtre de marché n'y change d'état que quelques fois. Quel que soit le résultat, la règle n'est pas adoptée
> sur cette base : seule la mesure en direct (§ 5) peut la valider. CSI ne passe aucun ordre.

## 1. Données (déjà connues, aucune nouvelle source)

- Signaux : export Telegram Desktop « WHALE HUNTING » du 2026-10-11 (`ChatExport_2026-10-11/result.json`),
  lu par le même lecteur que le bilan du 2026-10-11 (`reports/WHALE-AUDIT-20261011/whale_slices.py`), avec la
  correction : un signal publié alors que le prix est déjà au-dessus de TP1 est un achat limite en attente d'un
  repli ; il est simulé comme les autres.
- Bougies : Binance Spot publiques 15 min (simulation), 1 h (BTC et feu).
- Simulation inchangée : gestion du propriétaire (TP1 → stop à l'entrée, TPk → stop à TP(k−2)), remplissage s'il
  faut traverser l'entrée, coûts du scénario central de CSI, suivi 30 jours au plus. Trades encore ouverts et
  ordres jamais remplis : exclus.

## 2. La règle (une seule, aucun seuil choisi sur ces données)

Un signal publié à l'instant `t` est **gardé** si les deux conditions sont vraies à `t` :

1. **BTC au-dessus de sa moyenne 20 jours** : clôture de la dernière journée UTC **complète** de BTCUSDT
   (24 bougies 1 h dont `available_at` ≤ `t`, clôture = celle de la bougie de 23:00) **strictement supérieure** à
   la moyenne simple des 20 dernières clôtures journalières complètes (elle comprise).
2. **Pas de tempête** : le feu de protection du marché (`risk/market_light.py`, `docs/METEO_PROTECTION.md`),
   calculé par son propre code avec `now = t` sur les données du conteneur, n'est **pas ROUGE**. INCONNU compte
   comme « pas ROUGE », comme dans le feu lui-même ; le nombre de signaux concernés est publié.

Sinon le signal est **écarté**. Une donnée manquante pour la condition 1 écarte le signal du test (compté à part).

Origine : 20 jours est la moyenne courte la plus courante chez les analystes ; le feu est l'outil déjà en service,
aucun de ses seuils n'est touché. Aucune autre variante (10, 50 ou 200 jours, ORANGE exclu…) n'est calculée.

## 3. Mesure

- **Répartition des ventes** : l'échelle de BSM (33/27/20/13/7 % de TP1 à TP5 ; renormalisée si le signal a moins
  de 5 TP), fixée avant ces données. Descriptif seulement : 100 % à TP1, et 50 % à TP1 puis 12,5 % de TP2 à TP5.
- **Statistique principale** : D = moyenne (nette, par trade) des signaux gardés − moyenne des signaux écartés.
- **Intervalle** : bootstrap par blocs de journée UTC de publication, 4 000 tirages, graine 7, 95 %.
- **Population principale** : tous les trades terminés (ordres limites au-dessus de TP1 compris, comme le bilan
  du 2026-10-11). Population secondaire : sans eux (ce que BSM accepte d'exécuter).
- Publiés aussi : nombre de trades et moyenne de chaque groupe avec son intervalle, et le même calcul sur chaque
  moitié de la période (descriptif).
- **Épisodes** : un épisode est une suite de journées consécutives où le filtre garde (ou écarte) ; leur nombre
  est publié par groupe.

## 4. Verdict (fixé ici)

- **TROP PEU POUR CONCLURE** si un des deux groupes a moins de **100 trades** ou moins de **5 épisodes** ;
- sinon **INDICE FAVORABLE** si l'intervalle de D est entièrement au-dessus de 0 **et** la moyenne des gardés est
  positive ;
- sinon **INDICE DÉFAVORABLE** si l'intervalle de D est entièrement au-dessous de 0 ;
- sinon **RIEN DE DÉMONTRÉ**.

Même « INDICE FAVORABLE » n'est pas une preuve : les signaux d'une même période sont liés entre eux (le blocage
par journée ne retire pas la persistance du marché sur plusieurs jours), et la période est courte.

Comptage : c'est une mesure sur une source externe ; selon `EXTERNAL_SIGNALS.md`, elle ne compte pas dans les
essais de recherche de CSI. Elle est le **test n° 1** de ce filtre ; toute variante future serait un test de
plus, inscrit ici. Le calcul est fait **une seule fois** ; une erreur de code corrigée après coup est signalée ici
avec son effet.

## 5. Ce qui décidera vraiment

La mesure en direct sur les signaux WHALE HUNTING reçus par le relais Telegram, à partir de la date de mise en
service du relais pour ce canal : même règle, même statistique, au moins 100 trades terminés par groupe et
3 mois. Tant qu'elle n'est pas faite, la règle reste une hypothèse.

Scripts (dans `reports/WHALE-FILTRE-20261012/`, hors git), figés avant le calcul par leur empreinte SHA-256 :

| Script | Rôle | SHA-256 |
|---|---|---|
| `whale_slices.py` | simulation (copie du 2026-10-11 avec la correction au-dessus de TP1) | `9fcd5734ee9d556adae70699783593dce6ad140732d80fe30ab28dc74a73cf85` |
| `filtre_conteneur.py` | feu et moyenne 20 jours de BTC à chaque instant de publication, dans le conteneur, sans écriture | `90c06c57da55236540f63ce4285f77baa7e5930000d8564d0fcf7bd9c1cdea06` |
| `filtre_bilan.py` | groupes, D, intervalles, épisodes, verdict | `eed91317951139447923a8837f7c4f31190185df8ee8c64ede2ebb04f547add6` |

## 6. Résultat (calcul unique du 2026-10-12)

Empreintes des trois scripts vérifiées avant exécution. Sorties dans `reports/WHALE-FILTRE-20261012/`
(`whale.pkl`, `filtre.json`, `bilan.json`). La simulation reproduit le bilan corrigé du 2026-10-11 : 1 560
signaux, 1 431 trades terminés (127 jamais remplis, 2 encore ouverts).

**Verdict déclaré : TROP PEU POUR CONCLURE** (les deux populations). La période ne contient que **4 épisodes**
où le filtre garde et **3** où il écarte (5 exigés) ; les écartés sont 146 trades (115 sans les ordres au-dessus
de TP1), tous dans la 2de moitié.

**Le feu n'a rien filtré.** À 1 323 des 1 431 instants il est INCONNU : la prévision de volatilité de F12
n'existait pas encore (F12 a démarré après la plupart de ces signaux) ; aux 108 autres il est VERT. Jamais ROUGE.
Comme déclaré, INCONNU compte « pas ROUGE » : le test porte donc **en fait sur la seule condition BTC > moyenne
20 jours**. Le feu n'est pas évalué par ce test.

Chiffres (descriptifs ; gain net moyen par trade, échelle de BSM, gestion du propriétaire ; IC95 par blocs de
journée) :

| Population | Gardés | Écartés | D = gardés − écartés |
|---|---|---|---|
| Principale (1 431) | 1 285 trades, **+0,22 %** [−0,34 ; +0,60] | 146 trades, **−0,59 %** [−1,64 ; +0,62] | **+0,81 %** [−0,62 ; +1,99] |
| Sans ordres au-dessus de TP1 (1 110) | 995 trades, +0,19 % [−0,43 ; +0,61] | 115 trades, −0,94 % [−1,94 ; +0,58] | +1,13 % [−0,61 ; +2,33] |

Par moitié (principale) : 1re moitié, tout est gardé (BTC au-dessus de sa moyenne), +0,62 % [+0,44 ; +0,78] ;
2de moitié, gardés −0,29 % [−1,07 ; +0,51], écartés −0,59 %. **Même gardés, les signaux de la 2de moitié ne
gagnent pas** : le filtre sépare surtout deux périodes de marché, il ne fait pas d'une période perdante une
période gagnante. Répartitions descriptives (100 % à TP1 ; 50 % puis 12,5 %) : mêmes conclusions, D ≈ +0,81 %.

Ce que ça veut dire : le sens est celui attendu (les signaux pris quand BTC est sous sa moyenne 20 jours ont
perdu davantage), mais l'intervalle de D contient 0 et la période n'a pas assez de changements de marché pour
trancher. Rien n'est adopté. La règle reste à mesurer en direct (§ 5) ; la volatilité du feu sera alors
disponible (F12 tourne chaque jour).
