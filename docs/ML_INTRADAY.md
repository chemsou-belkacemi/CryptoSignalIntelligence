# Lot 5 bis — ML intraday : protocole déclaré avant toute exécution

Demande du propriétaire : comparer LightGBM et XGBoost à une référence simple (régression
logistique) pour prédire des mouvements intraday sur 15 min enrichies du contexte 1 h et 4 h,
séparer modèle / règles d'entrée-sortie / taille des positions / exécution, valider de façon
chronologique avec une période finale indépendante, mesurer le résultat d'un **portefeuille** et le
comparer à des références ; puis étendre au swing. Spot, sans levier, **aucun ordre** : CSI produit au
plus des signaux, l'exécution reste à BinanceSpotManager sur Demo.

Ce document est commité **avant** la première exécution. Toute modification ultérieure est datée en
bas, avec sa raison, et ne peut jamais être motivée par un résultat de la période finale.
**La conclusion « aucune stratégie testée ne démontre d'avantage exploitable » est un résultat
valable et attendu comme possible.** Priorités déclarées : contrôles de risque et cohérence des
cibles d'abord ; un second modèle (méta-filtre) n'est ajouté que si son bénéfice hors entraînement
justifie sa complexité.

## 0. Évaluation de Freqtrade + FreqAI (documentation officielle, consultée le 2026-10-01)

| Point | Constat (freqtrade.io/en/stable) | Conséquence pour CSI |
|---|---|---|
| Modèles | LightGBM, XGBoost, CatBoost, PyTorch, scikit-learn | même famille de modèles ici |
| Réentraînement | fenêtre glissante `train_period_days` / `backtest_period_days` ; cibles recalculées par fenêtre | repris (fenêtres glissantes) |
| Fuites | variables calculées une fois sur toute la plage, « à l'utilisateur de vérifier » ; découpage interne `test_size` sans purge ni embargo documentés ; `lookahead-analysis` ne vérifie que les trades effectivement pris | insuffisant au regard du protocole CSI (purge, embargo, causalité testée par mutation) |
| Période finale, essais | aucun verrou de période finale, aucun compteur d'essais | absents : ce sont des garde-fous centraux ici |
| Exécution | Freqtrade est un bot d'exécution (mode réel avec clés) | **incompatible** avec la règle « CSI ne contient aucun code d'ordre » |
| Installation | Python ≥ 3.11, TA-Lib, Docker fortement recommandé sous Windows ; backtest adaptatif « très long » | lourd, sans gain de rigueur |

**Décision** : ne pas l'utiliser comme base. Pipeline construit dans CSI (données causales, coûts,
purge, période finale verrouillée, registre des essais déjà en place), avec LightGBM et XGBoost comme
bibliothèques. Freqtrade pourra servir plus tard de **contre-vérification indépendante** en conteneur
séparé, en backtest seulement, sans clé.

## 1. Données et variables (toutes connues à l'instant de décision)

- Univers : les 16 paires de la configuration ; bougies **15 min clôturées** (point de départ
  configurable), décision à la clôture de t, entrée au plus tôt à l'**ouverture de t+1**.
- Contexte **1 h** (paire et BTC) : jointure `available_at` existante ; contexte de plus de 2 h → inconnu.
- Contexte **4 h** : reconstruit à partir des bougies 1 h **complètes** (00, 04, 08… UTC), disponible à
  l'`available_at` de sa dernière bougie 1 h ; plus de 8 h → inconnu. Une bougie 4 h en formation n'est
  jamais visible.
- Trou de données dans les 96 dernières bougies → ligne exclue (fenêtres d'indicateurs douteuses).
- Contexte 1 h ou 4 h inconnu (périmé ou absent) → **aucune décision** sur la ligne, en validation comme
  en service (§8) ; l'entraînement garde ces lignes (variables absentes).
- **Familles de variables** (mesurées séparément, §6) :

| Famille | Variables |
|---|---|
| prix | rendements 1, 2, 4, 8, 16, 32, 96 bougies ; volatilités réalisées 16 et 96 ; ATR % ; RSI ; distances et pente d'EMA ; Bollinger ; position dans le range 32 |
| volume | z-score et ratio du volume de base |
| transactions | part des achats agressifs (taker) et sa moyenne 4 bougies ; nombre de trades relatif |
| contexte | 1 h (rendement 24 h, pente, ATR, distance EMA 50, régimes) et 4 h (rendements 24 h et 7 j, pente, ATR, distance EMA 50) |
| marché | BTC 1 h (rendement 24 h, ATR, pente) |
| calendrier | heure et jour (sinus, cosinus) |

Les variables « transactions » sont des **agrégats des bougies Binance** (volume acheteur agressif,
nombre de trades), pas un flux d'ordres tick par tick : les archives `aggTrades` (plusieurs Go) sont
une extension possible si cette famille apporte quelque chose.

**Audit des fuites (exécuté avant toute sélection, l'exécution s'arrête en échec sinon)** : sur les
données réelles, pour des instants tirés au hasard, les variables recalculées avec **seulement** les
données disponibles à l'instant de décision, puis avec un **futur falsifié**, doivent être identiques à
celles du calcul complet ; un test de **mutation** (4 h joint sur l'heure d'ouverture) doit être détecté.

## 2. Cibles (deux étiquetages, cohérents avec les règles de sortie et les coûts)

Horizons : **H ∈ {2, 4, 8, 16} bougies = 30 min, 1 h, 2 h, 4 h**. Entrée à l'ouverture de t+1 ; frais
10 pb par côté, glissement et demi-spread du scénario **central** (≈ 0,26 % aller-retour). Étiquette :
**rendement net > 0**. Une cible qui traverse un trou de données est invalide.

| Cible | Sortie (= règle de sortie de la stratégie qui l'utilise) |
|---|---|
| **horizon fixe (FH)** | clôture de t+H |
| **triple barrière (TB)** | première barrière touchée : objectif `entrée × (1 + 1,5 σ_H)`, stop `entrée × (1 − 1,5 σ_H)`, sinon clôture de t+H ; σ_H = volatilité réalisée 96 bougies × √H (connue à la décision) |

Règles d'exécution de la triple barrière (conservatrices, identiques pour l'étiquette et le
backtest) : stop et objectif touchés dans la même bougie → **stop** ; ouverture au-delà du stop →
sortie à l'ouverture ; ouverture au-delà de l'objectif → sortie à l'objectif (pas mieux) ; coûts de
marché appliqués aux deux sorties. Une stratégie entraînée sur une cible sort **selon cette même
cible** : aucune stratégie n'apprend une cible et en exécute une autre.

**Chevauchement** : une ligne d'entraînement toutes les H bougies par paire ; **purge** au plus tard de
la barrière verticale (t+H) : aucune ligne d'entraînement ou d'étalonnage dont t+H dépasse le début du
bloc suivant ; en validation, seules les décisions dont t+H reste dans la validation comptent.

## 3. Modèles, étalonnage, espérance et abstention

| Famille | Configurations (fixées, pas d'arrêt précoce) |
|---|---|
| Référence | régression logistique L2 (numpy) |
| LightGBM | num_leaves ∈ {15, 63} × n_estimators ∈ {200, 500} ; learning_rate 0,05, min_child_samples 200, bagging 0,8, colonnes 0,8 |
| XGBoost | max_depth ∈ {3, 6} × n_estimators ∈ {200, 500} ; eta 0,05, min_child_weight 50, subsample 0,8, colonnes 0,8 |

- Chaque fenêtre d'entraînement de 12 mois est coupée en **10 mois d'ajustement + 2 mois
  d'étalonnage** (purgés). Les probabilités sont **étalonnées** (Platt : logistique sur le logit) sur
  ces 2 mois ; la calibration est **vérifiée** en validation (Brier contre le taux de base, erreur
  d'étalonnage par déciles, AUC). Moins de 200 lignes d'étalonnage, ou une seule classe → pli en échec
  (aucun trade, Sharpe nul).
- **Espérance nette** : E = p × gain moyen net des gagnants + (1 − p) × perte moyenne nette des
  perdants (moyennes de la période d'ajustement, coûts inclus). Hypothèse déclarée : l'ampleur des
  gains et des pertes ne dépend pas de p.
- **Décision** : entrer si E > marge m, **m ∈ {0 ; 0,05 % ; 0,10 %}** ; sinon **abstention**. Entre
  candidats simultanés, priorité à la plus forte espérance.
- **Grille principale** : 2 cibles × 4 horizons × 9 modèles × 3 marges = **216 essais**.

## 4. Validation, stabilité, variantes, période finale

- **Sélection, sur DEVELOPMENT seulement** (2021-01 → 2025-06) : fenêtres glissantes, entraînement
  12 mois, validation 6 mois, pas de 6 mois → **7 validations** (2022-01 → 2025-06).
- **Règle de stabilité** (un « système » = configuration et ses éventuelles variantes) : Sharpe > 0
  dans **au moins 70 % des validations évaluées** (5 sur 7) et au moins 200 trades au total. Parmi les
  systèmes admissibles, le retenu maximise le **Sharpe médian par validation**.
- **Système de référence** des variantes : le meilleur admissible, à défaut le meilleur Sharpe médian
  (diagnostic). Variantes, toutes comptées comme essais :
  - **familles de variables** (6), le **calendrier présent dans chacune** pour isoler une seule famille
    par comparaison : prix ; prix + volume ; prix + transactions ; prix + volume + transactions ;
    + contexte (= tout sauf le marché) ; **sans calendrier** (= tout sauf le calendrier) ; tout = la
    référence ;
  - **méta-filtre** (1) : logistique L2 entraînée sur les **signaux hors entraînement** du modèle
    principal (validations précédentes, sorties antérieures au début de la validation jugée ; au moins
    300 signaux et 10 cas minoritaires par variable), variables : p, E, volatilité, régimes, BTC,
    momentum, volume ; règle : garder le signal si la probabilité méta dépasse le taux de gain de son
    entraînement ;
  - **abstention de même sévérité** (1) : dans chaque validation, **soumettre** autant de signaux que
    le méta-filtre, ceux de plus forte espérance (les limites peuvent en refuser différemment). Le
    nombre vient du méta-filtre du même pli, sans ses issues ; le seuil ainsi fixé sur tout le pli n'est
    pas causal dans le temps : c'est une **référence de comparaison**, jamais un système retenu.
- Une variante n'est **conservée** que si elle bat la référence dans au moins 70 % des validations où
  elle est évaluable (au moins 3). Le méta-filtre doit en plus battre l'abstention de même sévérité de
  la même façon ; sinon le second modèle n'est pas ajouté.
- **Ordre d'introduction** (contribution de chaque changement isolée) : familles d'abord ; si une
  famille est conservée, la meilleure devient la référence ; le méta-filtre est ensuite comparé à cette
  référence. Un pli dont l'ajustement échoue compte comme un Sharpe nul et des rendements nuls (jamais
  exclu, ni de la sélection ni de l'analyse). Un pli où le méta-filtre n'a pas encore d'historique
  soumet tous les candidats (comme la référence) : il compte dans la stabilité et le Sharpe médian du
  système, et n'est exclu que de la comparaison méta-filtre / référence.
- **Total déclaré : 224 essais**, comptés dans `program_trials`.
- **Aucun système admissible → conclusion « aucun avantage démontré », sans consulter la période
  finale** (elle reste vierge pour une idée future).
- **Estimation honnête, sur FINAL_TEST** (2025-07-01 → date d'exécution), consultée **une seule
  fois** et enregistrée (`--i-understand-final-test`), seulement si un système est admissible et après
  la vérification en bougies 1 min (§5) : système figé, réentraîné tous les 6 mois sur les 12 mois
  précédents (même règle), joué sur toute la période finale. Les résultats de sélection sont
  optimistes par construction (meilleur de 224) ; seul ce chiffre juge l'approche. Verrous : refus
  après une première consultation ; refus si le **code ou la configuration qui décident** (empreintes
  enregistrées à la sélection) ont changé, ou si le code n'est pas commité ; refus tant que la
  vérification 1 min n'est pas implémentée et réussie (un fichier écrit à la main ne suffit pas).

## 5. Stratégie, limites de risque centralisées, exécution et hypothèses du moteur

- **Couches séparées** : modèle → probabilité étalonnée → espérance ; **entrée** : E > m ;
  **sortie** : celle de la cible (§2) ; **taille et limites** : module commun `risk/exposure.py` ;
  **exécution** : BinanceSpotManager (jamais CSI).
- **Limites centralisées** (`[risk]` de la configuration, communes à **toutes** les stratégies et
  à tous les actifs, positions déjà ouvertes comprises) : 10 % du capital réalisé par position ;
  5 positions au plus ; exposition totale ≤ 50 % ; exposition par paire ≤ 10 % (une position par paire,
  toutes stratégies confondues) ; exposition par stratégie ≤ 50 % ; perte du jour ≥ 3 % → plus
  d'entrée jusqu'au lendemain UTC. Spot, sans levier. Le swing (§10) utilisera le même registre.
- **Hypothèses du moteur** (déclarées) : remplissage complet au prix d'ouverture de t+1 corrigé du
  glissement et du demi-spread (pas de carnet d'ordres, pas de remplissage partiel) ; sorties selon §2
  corrigées de même ; ordre intra-bougie inconnu → hypothèse défavorable ; capital suivi en réalisé
  (pas de valorisation intra-position) ; tailles faibles devant les volumes 15 min (pas d'impact) ;
  pas de funding (spot).
- **Robustesse** (système retenu ou de référence, en validation et, le cas échéant, sur la période
  finale) : coûts **défavorables** et **stress** (avec une bougie de retard, comme configurés) ; coûts
  centraux avec **une bougie de retard** ; **bougies 1 min** : entrée au prix de la première minute
  suivant l'ouverture de t+1 (latence réaliste) et ordre réel stop/objectif dans la bougie, sur les
  trades réellement pris (obligatoire avant toute consultation du test final ; le téléchargement 1 min
  n'est activé que pour ce contrôle).

## 6. Mesures, incertitude, analyses, apport des familles

- Rendement net, CAGR, **perte maximale du portefeuille**, **Sharpe** (journalier annualisé), nombre de
  trades, **rotation**, gain moyen par trade, taux de gain, part d'abstention.
- **Agrégation** : les rendements journaliers de chaque validation (capital de la validation) sont mis
  bout à bout — équivalent à un compte unique, la taille des positions étant proportionnelle au capital.
- **Incertitude adaptée aux séries temporelles** : bootstrap **par blocs circulaires de 10 jours** des
  rendements journaliers du portefeuille (IC95 du Sharpe et du rendement total) et bootstrap par blocs
  de jours du gain moyen par trade.
- **Dépendance aux trades exceptionnels** : rendement et gain moyen **sans le 1 % des meilleurs trades**
  et **sans les 10 meilleurs** ; part du gain brut venant des 10 meilleurs trades.
- **Par période et contexte** : année, trimestre, paire, régime 1 h (tendance, volatilité), BTC en
  hausse ou en baisse sur 24 h.
- **Références** : liquidités, **BTC acheté et gardé**, **univers à parts égales acheté et gardé**,
  **entrées au hasard** (même nombre de candidats, mêmes règles ; 200 tirages : distribution du Sharpe).
- **Apport des familles** : Sharpe, AUC et score de Brier par validation pour chaque variante (§4) ;
  la comparaison prix + volume contre prix + transactions répond directement à la question « volume
  contre transactions ».
- **Cibles** : pour chaque horizon, modèle et marge, Sharpe par validation en horizon fixe contre
  triple barrière.

## 7. Critères (sur FINAL_TEST, déclarés avant)

L'approche n'est retenue (statut VALIDATED ; aucune exécution automatique pour autant) que si :
1. rendement net du portefeuille > 0 et IC95 du gain moyen par trade (blocs de jours) > 0 ;
2. Sharpe au-dessus du 95e centile des entrées au hasard ;
3. Sharpe supérieur à celui de BTC acheté et gardé **ou** perte maximale au moins deux fois plus
   faible pour un rendement positif ;
4. au moins 100 trades, aucune paire ni trimestre > 60 % du gain ;
5. résultat encore positif en coûts défavorables et avec une bougie de retard ;
6. gain moyen encore positif **sans le 1 % des meilleurs trades**.
Sinon : REJECTED (INCONCLUSIVE si moins de 100 trades).

## 8. Surveillance, suspension, positions existantes (si l'approche est un jour retenue)

- **Âge du modèle** : réentraînement tous les 6 mois ; au-delà de 7 mois sans réentraînement →
  suspension des nouvelles entrées.
- **Qualité des données** : bougie 15 min attendue absente ou contexte 1 h/4 h inconnu → aucune entrée
  sur la paire ; plus de 4 paires sans données → suspension globale.
- **Dégradation** : sur les 100 derniers trades prospectifs, IC95 du gain moyen entièrement < 0, ou
  perte du portefeuille > 1,5 × la perte maximale de la période finale → suspension jusqu'à revue.
- **Calibration** : écart moyen entre probabilité prédite et fréquence observée > 10 points sur les
  200 derniers setups → suspension.
- **Positions existantes** : jamais coupées par une suspension ; elles vont à leur sortie prévue ; une
  coupure de données au moment de la sortie est signalée et gérée par le bot. Les limites (§5)
  comptent toujours les positions ouvertes.
- **Reprise après interruption, synchronisation, doublons** : côté CSI, publication idempotente
  (`SIGNAL_ID` / `IDEMPOTENCY_KEY`, registre, réconciliation après crash, verrou d'instance, setups
  anciens → EXPIRED) ; côté BinanceSpotManager, état vérifié par un audit dédié (DELIVERY_STATUS) avant
  tout branchement.
- **Enregistrement** : toutes les exécutions, **échecs compris** (statut FAILED), avec paramètres,
  versions et empreintes des données.
- **Journal de chaque décision** (`decisions.parquet` du rapport, système analysé, toutes les
  validations) : paire, instant, empreinte des données, empreinte du modèle, probabilité étalonnée,
  espérance nette, marge, décision (ENTER, ABSTAIN, refus par les limites et sa raison, FILTERED),
  coûts aller-retour, résultat observé (le rendement qu'aurait eu l'entrée, même non prise).
- **Versions reproductibles** : exécution refusée si le code n'est pas commité (sauf essai local
  déclaré `--allow-dirty`, enregistré comme tel) ; commit, versions des dépendances (`pylock.toml`,
  LightGBM, XGBoost), graine, empreintes des données **effectivement utilisées** (après coupure à la
  fin de période) et modèles sauvegardés avec leur SHA-256.

## 9. Ce qui n'est pas fait

Aucun signal publié, aucun branchement à la surveillance, aucun ordre. Un VALIDATED ouvrirait
seulement une phase de signaux shadow prospectifs, puis une décision du propriétaire.

## 10. Swing (étape suivante, protocole séparé)

Après l'évaluation intraday : positions de plusieurs jours, cibles et entraînements distincts (4 h /
1 jour), LightGBM, XGBoost et éventuellement CatBoost, **limites d'exposition communes** (même module
`risk/exposure.py`). Protocole écrit avant exécution, comme celui-ci.

## 11. Expériences avancées (après le prototype, une à la fois)

Priorité au prototype évaluable (§1-9). Ensuite, chaque expérience change **un seul** élément par
rapport au même système de référence (celui du prototype), fait l'objet d'un additif à ce document
commité **avant** son exécution, et ses essais sont comptés. Ordre prévu :

1. **Univers reproductible dans le passé** (le plus important pour la validité) : à chaque
   réentraînement, paires USDT spot alors cotées (présence des archives officielles, retraits de cote
   compris : les paires retirées restent dans l'historique jusqu'à leur dernière bougie), classées par
   volume en USDT des 30 jours précédents, connu à cette date ; une position sur une paire retirée sort
   à la dernière cotation, avec une pénalité de coûts. Le filtre halal du propriétaire ne s'applique
   qu'aux paires qu'il a validées : son effet est mesuré séparément.
2. **Fenêtres d'apprentissage et fréquences de réentraînement** : entraînement 6, 12 ou 24 mois ou
   ancré ; réentraînement tous les 1, 3 ou 6 mois ; comparés sur les mêmes validations, dans l'ordre
   chronologique.
3. **Modèles par actif contre modèle commun** à toutes les paires (même cible, mêmes variables).
4. **Bougies fondées sur l'activité** (volume ou montant échangé) contre bougies temporelles,
   éventuellement : reconstruction à partir des bougies 1 min, décisions aux clôtures d'activité.
5. **Procédure référence / candidat** (dès qu'un système est en observation) : le candidat tourne en
   shadow à côté de la référence ; remplacement seulement si, sur au moins 100 décisions prospectives
   chacun et 8 semaines, le candidat a un meilleur Sharpe dans au moins 70 % des fenêtres de 2 semaines
   et un écart de gain moyen dont l'IC95 (blocs de jours) est > 0 ; la version précédente (modèle,
   données, commit) reste archivée et **revient automatiquement** si la surveillance (§8) suspend le
   nouveau système dans ses 4 premières semaines.

## Historique

- 2026-10-01, v1 (commit 69f7f6c) : version initiale, avant toute exécution.
- 2026-10-01, v2 (commit 4780d2f, avant toute exécution) : abstention et sélection par espérance
  nette, étalonnage vérifié, stabilité, familles de variables, hypothèses du moteur, robustesse,
  analyses par période et contexte, surveillance et suspension, conclusion « aucun avantage » sans
  consulter la période finale.
- 2026-10-01, v3 (avant toute exécution) : demandes du propriétaire — cible triple barrière cohérente
  avec la sortie et les coûts, méta-filtre comparé à une abstention de même sévérité, limites
  d'exposition centralisées (actifs, stratégies, positions ouvertes), bootstrap par blocs et
  dépendance aux trades exceptionnels (critère 6), audit des fuites exécuté avant sélection, variante
  prix + transactions, reprise et doublons audités. 108 → 224 essais déclarés.
- 2026-10-01, v4 (avant toute exécution) : demandes du propriétaire — journal de chaque décision,
  versions reproductibles (refus du code non commité, empreintes des données après coupure, modèles
  archivés), ordre d'introduction des variantes, pli en échec = Sharpe nul ; expériences avancées
  déclarées pour après le prototype (§11 : univers point-in-time, fenêtres et réentraînement, modèle
  par actif ou commun, bougies d'activité, procédure référence/candidat avec retour arrière).
- 2026-10-01, v5 (avant toute exécution, après relecture `leak-auditor` : aucune fuite trouvée) :
  calendrier présent dans chaque variante de familles et variante « sans calendrier » (au lieu d'une
  « + marché » qui retirait le calendrier sans le dire) ; pli en échec compté à rendement nul dans
  l'analyse ; plis du méta-filtre sans historique comptés dans sa stabilité ; aucune décision sans
  contexte 1 h / 4 h connu (comme en service) ; étalonnage minimal de 200 lignes déclaré ; plis
  enchaînés sans remise à l'échelle ; verrous de la période finale (consultation unique, empreintes du
  code et de la configuration, vérification 1 min réelle) ; abstention de même sévérité précisée.
