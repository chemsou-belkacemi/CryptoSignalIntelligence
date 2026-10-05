# IA locale qui lit les graphiques : son avis sur les 72 heures suivantes bat-il une règle simple ? (déclaré le 2026-10-05, avant code et exécution)

**Origine.** Le propriétaire veut savoir si une IA qui lit les graphiques peut prédire mieux que les méthodes déjà
testées. Il a choisi une IA **locale** (gratuite, sur la carte graphique du PC) plutôt qu'une API payante. Une
démonstration du 2026-10-05 a été faite sur 18 graphiques de 2025-2026 : 6 avant de fortes chutes ou de forts envols,
choisis après coup, et 12 au hasard. Elle a été vue par le propriétaire et n'est pas comptée comme résultat. Le modèle 7b
y suivait surtout la tendance des moyennes : il disait « baissier » avant les 4 envols, sa lecture du RSI était souvent
fausse, et ses « achats » n'avaient pas de niveaux. Demande du propriétaire (« oui lance ») : un test sérieux hors
ligne avant tout test en direct. Code : `research/ia_bias.py` ; tests : `tests/test_ia_bias.py` ; commande :
`csi ia-bias --model <modèle>`.

## Modèles

Deux modèles locaux servis par Ollama (127.0.0.1 seulement), chacun jugé séparément :
- `qwen2.5vl:7b`, publié le 2025-01-28 ;
- `gemma3:12b`, publié le 2025-03-12. Il dépasse la mémoire de la carte graphique : une partie tourne sur le processeur.

Les deux ont été entraînés **avant** la période testée : ils ne peuvent pas connaître la suite des graphiques
montrés. Le nom de la paire et les dates ne sont pas écrits, mais la paire et la période restent **en partie
reconnaissables** (prix réels, niveaux de la période précédente qui trahissent le calendrier) : sans fuite du futur
pour autant, puisque tous les moments sont postérieurs à la publication des deux modèles. Les empreintes Ollama des
modèles et la version du serveur sont enregistrées avec chaque exécution.

## Moments testés

- **400 moments** tirés au hasard (graine 20261005), uniformément parmi les clôtures de bougies 4 h des **40 paires
  de recherche** entre le **2025-04-01** et le **2026-09-27** (la suite de 72 h tombe avant le 2026-09-30). Chaque
  moment exige au moins 250 bougies 4 h d'historique avant lui. Biais de survivance déclaré : il touche de la même
  façon l'IA et la règle.
- **Période réservée** : de 2025-07 à 2026-09, les moments tombent dans la période réservée.
  - Le test est donc enregistré comme **consultation n° 3** (stratégie `IA_LOCALE`), sur autorisation du
    propriétaire (« oui lance », 2026-10-05, au test proposé sur 2025-2026).
  - La démonstration du même jour avait déjà montré 18 graphiques de cette période.
  - Ce test ne sert pas de juge neutre pour une autre stratégie.

## Ce que voit l'IA (seulement ce qui est connu au moment de la décision)

- **Un graphique anonyme** : 120 dernières bougies 4 h, moyennes 50 et 200, 3 supports et 3 résistances, zones FVG
  et order blocks, figures détectées, volume et RSI. Ni paire ni date.
- **Les chiffres de la carte d'analyse de CSI** :
  - dernière clôture et ATR ;
  - supports et résistances, avec leur nombre de touches ;
  - structure (BOS / CHoCH) ;
  - RSI et moyennes ;
  - zones FVG et OB ;
  - niveaux du jour, de la semaine et du mois précédents ;
  - chiffres ronds ;
  - figures récentes, avec leurs niveaux ;
  - volume des 6 dernières bougies comparé à la moyenne des 60 ;
  - part des achats agressifs sur 24 h.
- **La question** (texte fixe dans le code) : analyse en 4 phrases au plus, puis le **biais pour les 72 heures
  suivantes** :
  - `HAUSSIER` : plus haut dans 72 h ;
  - `BAISSIER` : plus bas ;
  - `NEUTRE` : pas d'avis.
- **Réglages** : réponse en JSON imposé, température 0, graine fixe. Une réponse illisible est comptée
  `ILLISIBLE` et traitée comme neutre.

## Mesure

- **Rendement suivant** : `r72` = clôture 72 h après la décision ÷ clôture à la décision − 1, sur les bougies 1 h.
  Pas de frais : c'est une question de prévision.
- **Règle de comparaison (la « règle simple »)** : `HAUSSIER` si la moyenne exponentielle 50 des clôtures 4 h est
  au-dessus de la 200, sinon `BAISSIER`. C'est ce que l'IA semblait faire dans la démonstration.
- **Score** : +1 (haussier), −1 (baissier), 0 (neutre ou illisible). **Rendement signé** = score × `r72` : le
  rendement moyen obtenu en suivant l'avis (mesure d'information sur le sens, pas une stratégie : la vente à découvert
  n'est pas dans le cadre).
- **Comparaison décisive (1 par modèle)** : `D` = moyenne du rendement signé de l'IA − moyenne du rendement signé de
  la règle, sur les mêmes moments. Intervalle par tirage des **semaines calendaires** des décisions (10 000 tirages,
  graine 20261005), au niveau **97,5 %** (2 modèles).
  - `MIEUX_QUE_LA_REGLE` si l'intervalle est entièrement au-dessus de 0 ;
  - `MOINS_BIEN_QUE_LA_REGLE` s'il est entièrement en dessous ;
  - sinon `PAS_MIEUX`.

**Descriptif, hors décision.**
- Rendement signé de l'IA seul, avec son intervalle : porte-t-elle une information, même si elle ne bat pas la
  règle ?
- Part de bons sens, avis neutres exclus. À lire à côté de la part des moments en hausse, sinon un modèle qui dit
  toujours « baissier » paraît bon dans un marché qui baisse.
- Répartition des avis, accord avec la règle, rendement moyen quand l'IA dit « haussier » comparé à tous les moments
  (lecture « achat seulement »).
- Avis avant les plus forts mouvements (`|r72|` > 10 %), réponses illisibles, temps par graphique.

**Puissance déclarée.** 400 moments répartis sur environ 78 semaines, avec des mouvements à 72 h de l'ordre de 8 %
d'écart type. Un avantage de moins d'environ 1,5 point de rendement à 72 h sur la règle ne sera probablement pas
détecté.

**Lecture déclarée.**
- `MIEUX_QUE_LA_REGLE` pour un modèle : candidat à un test en direct déclaré (F17), seul juge d'une utilité réelle.
- `PAS_MIEUX` ou `MOINS_BIEN_QUE_LA_REGLE` pour les deux : **aucun avantage de cette taille détecté** sur la
  règle des moyennes (la puissance ne permet pas de dire « rien du tout »). Pas de test en direct de l'IA locale ;
  l'API Claude reste une option non testée, à la décision du propriétaire.

**Comptage.** 1 essai par modèle exécuté, inscrit au registre au label `FINAL_TEST` (les moments lisent la période
réservée), avec la consultation n° 3 inscrite avant le premier calcul.

## Corrections de la relecture, avant l'exécution (2026-10-05, `leak-auditor` ; aucune réponse de l'IA obtenue)

**Corrigé (bloquant) :**
- **Panne du serveur** : une panne n'est plus jamais comptée comme une réponse. Après 3 tentatives, le calcul
  s'arrête, est inscrit `FAILED` au registre (1 essai compté par prudence), et rien n'est mis en cache. La reprise est
  permise : les réponses déjà obtenues sont gardées, et aucune nouvelle consultation n'est inscrite. `ERREUR`
  (serveur) et `ILLISIBLE` (modèle) sont distingués ; la réponse brute est gardée ; la longueur de réponse est bornée
  (600 tokens).
- **Lecture de la période réservée** : drapeau `--i-understand-final-test` obligatoire, une seule lecture par modèle
  (refus si un essai terminé existe), `--allow-dirty` réservé à une **répétition technique**. Cette répétition porte
  sur 10 moments de janvier à mars 2025 (DEVELOPMENT) : aucun rendement n'y est calculé et rien n'est enregistré.
- **Cache des réponses** sous `state/ia_cache/` (ignoré par git), lu avant la consultation, une ligne tronquée
  ignorée. Sa clé hache la consigne remplie, les options, le schéma, le modèle et l'image.
- **Verdict** : `MIEUX_QUE_LA_REGLE` exige que l'intervalle de l'écart avec la règle **ET** celui du rendement signé
  de l'IA seule soient au-dessus de 0. Sans cela, une IA toujours « neutre » battait une règle perdante.

**Déclaré :**
- **Intervalle par blocs de 2 semaines** au lieu des semaines : les fenêtres de 72 h chevauchent deux semaines, et la
  simulation de la relecture donnait 3,5 % de faux positifs au lieu de 2,5 %.
- **Réponses illisibles** : plus de 2 % donne `INSUFFISANT` ; lecture descriptive sans les moments illisibles.
- **Consigne et règle** choisies après la démonstration sur 18 graphiques de la période, dont 6 choisis après coup :
  les moments à moins de 72 h d'un moment de la démonstration (même paire) sont comptés, avec une lecture
  descriptive sans eux.
- **Descriptif ajouté avant toute lecture** : par trimestre, et BTC / ETH contre les autres paires.
- **Latence** : le rendement part de la clôture, alors que l'IA répond en 15 à 60 secondes. C'est acceptable pour
  une question de prévision.
- La carte de CSI calcule ses moyennes avec `ind.ema`, le graphique et la règle avec la moyenne exponentielle de
  pandas : l'écart est négligeable avec plus de 1 200 bougies d'historique. Le graphique montre les 4 figures du JSON.

## Résultats

### `qwen2.5vl:7b` (`IABI-20261005T013044Z-067d7b`, 2026-10-05, consultation n° 3, 1 essai) : **`PAS_MIEUX`**

400 moments, 39 blocs de 2 semaines, aucune réponse illisible, 25 s par graphique (médiane).

| Mesure (rendement à 72 h) | IA 7b | Règle EMA 50 / 200 |
|---|---|---|
| Rendement signé moyen (suivre l'avis) | +0,25 % [−0,53 ; +1,08] | −0,19 % |
| Écart IA − règle | +0,44 % [−0,68 ; +1,53] | — |
| Bon sens (avis neutres exclus) | 50,9 % | 47,8 % |
| Part des moments en hausse | 50,5 % | 50,5 % |

- **Avis** : baissier 234, haussier 149, neutre 17 ; d'accord avec la règle 66 % du temps.
- **Avant les plus forts mouvements** (`|r72|` > 10 %) :
  - avant 36 fortes hausses, l'IA disait baissier 22 fois et haussier 13 fois ;
  - avant 20 fortes baisses, baissier 13 fois et haussier 6 fois.
- **Lecture « achat seulement »** : rendement moyen à 72 h de +0,62 % quand l'IA dit haussier, contre +0,24 % sur
  tous les moments (non démontré).
- **Par trimestre** : le rendement signé de l'IA va de −0,9 % à +2,3 %. BTC / ETH −1,3 % (18 moments), autres paires
  +0,3 %. Sans les 3 moments proches de la démonstration : +0,16 %.

**Lecture déclarée** : aucun avantage de cette taille détecté sur la règle. L'IA seule ne montre pas d'information
non plus : son intervalle contient 0, et elle a le bon sens une fois sur deux, comme une pièce.

### `gemma3:12b` (`IABI-20261005T043510Z-4033fb`, 2026-10-05, même consultation, 1 essai) : **`PAS_MIEUX`**

400 moments (les mêmes), aucune réponse illisible, 44 s par graphique (médiane).

| Mesure (rendement à 72 h) | IA Gemma 12B | Règle EMA 50 / 200 |
|---|---|---|
| Rendement signé moyen (suivre l'avis) | −0,46 % [−1,18 ; +0,24] | −0,19 % |
| Écart IA − règle | −0,27 % [−1,52 ; +0,96] | — |
| Bon sens (avis neutres exclus) | 46,8 % | 47,8 % |

- **Avis** : haussier 188, baissier 160, neutre 52 ; d'accord avec la règle 52 % du temps.
- **Avant les plus forts mouvements** : avant 36 fortes hausses, haussier 17 fois et baissier 18 fois ; avant 20
  fortes baisses, haussier 9 fois et baissier 7 fois.
- **Lecture « achat seulement »** : −0,05 % à 72 h quand l'IA dit haussier, contre +0,24 % sur tous les moments.
- **Par trimestre** : de −1,5 % à +1,5 %.

### Conclusion

Aucun des deux modèles locaux ne fait mieux que la règle des moyennes. **Aucun ne porte d'information à lui seul** :
le 7b a le bon sens 51 % du temps, Gemma 47 %, pour 50,5 % de moments en hausse. La lecture d'un graphique et de
toute la carte d'analyse de CSI par une IA locale ne prévoit pas la direction à 72 h, pas même avant les plus forts
mouvements. Comme déclaré : pas de test en direct de l'IA locale. L'API Claude (modèle bien plus grand) reste non
testée, à la décision du propriétaire ; rien dans ce résultat ne la rend prometteuse.

Registre : consultations de la période réservée 3 ; essais sur la période finale 8 (5 volatilité, 1 lignes de
tendance, 2 IA) ; DEVELOPMENT 853.

## Historique

- 2026-10-05 : déclaré avant tout code et toute exécution ; démonstration (18 graphiques) faite avant, décrite
  ci-dessus.
- 2026-10-05 : relecture indépendante avant l'exécution ; corrections et déclarations ci-dessus.
- 2026-10-05 : répétition technique (0 % d'illisibles ; 21 s et 42 s par graphique) ; exécution de `qwen2.5vl:7b` : PAS_MIEUX.
- 2026-10-05 : exécution de `gemma3:12b` : PAS_MIEUX ; conclusion inscrite.
