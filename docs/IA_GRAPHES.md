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

Les deux ont été entraînés **avant** la période testée. Ils ne peuvent pas connaître la suite des graphiques montrés,
et le nom de la paire et les dates sont masqués de toute façon. Les empreintes Ollama des modèles sont enregistrées
avec chaque exécution.

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
- `PAS_MIEUX` ou `MOINS_BIEN_QUE_LA_REGLE` pour les deux : l'IA locale n'apporte rien de plus qu'une règle de
  moyennes. Pas de test en direct de l'IA locale ; l'API Claude reste une option non testée, à la décision du
  propriétaire.

**Comptage.** 1 essai par modèle exécuté, inscrit au registre au label `FINAL_TEST` (les moments lisent la période
réservée), avec la consultation n° 3 inscrite avant le premier calcul.

## Historique

- 2026-10-05 : déclaré avant tout code et toute exécution ; démonstration (18 graphiques) faite avant, décrite
  ci-dessus.
