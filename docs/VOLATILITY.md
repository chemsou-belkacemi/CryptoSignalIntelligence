# Lot 7 — Prévision de volatilité : protocole déclaré avant toute exécution

Protocole **v1**, déclaré le 2026-10-01 **avant toute exécution** : aucun résultat réel n'a été calculé
ni regardé. Toute modification ultérieure est datée en bas (« Historique »).

**Pourquoi.** Tous les essais de prévision de la **direction** ont échoué : 638 essais au programme à la
date de cette déclaration, aucun avantage démontré (stratégies A à C, criblage D à I, ML intraday, ML
swing, marché à terme). L'ampleur des mouvements se prévoit en général mieux que leur sens. Un modèle de
volatilité validé servirait à dimensionner les positions et à régler stops et objectifs, pas à choisir
un sens.

**Ce que ce n'est pas.** Ni une stratégie, ni un signal, ni un ordre. Aucune rentabilité n'est en jeu ni
annoncée : on mesure une **erreur de prévision**.

**Question.** Un modèle prévoit-il la variance réalisée des 1, 3 et 7 prochains jours mieux que la règle
« volatilité récente des 7 derniers jours × √horizon » (M0, la référence à battre) ?

Code : `research/volatility.py`. Tests : `tests/test_volatility.py` (données synthétiques seulement).
Commande : `csi volatility` (branchée après la relecture indépendante). Avant elle, l'exécution (`research.volatility.run`) attendait la relecture
indépendante de ce protocole.

## 1. Données et univers

- Bougies **1 h** du magasin long (`research/long_history.py` : depuis la cotation de chaque paire).
- Univers : les 40 paires de `RESEARCH_UNIVERSE` (`research/universe.py`, figé le 2026-10-01). BTCUSDT
  sert aussi de marché.
- **DEVELOPMENT seulement** : chaque série est coupée aux bougies ouvertes au plus tard le 2025-06-30 à
  23:00 UTC. La période finale n'est pas lue ; un test le vérifie en falsifiant tout ce qui suit.
- Trois colonnes sont lues : `open_time`, `close`, `available_at`. Leur empreinte par paire est
  enregistrée avec le résultat.
- **Données complètes exigées.** Chaque paire de l'univers doit être présente dans le magasin long et
  aller jusqu'à 2 jours avant la fin de DEVELOPMENT. Sinon : refus (`IncompleteData`), rien n'est
  calculé, aucun essai n'est enregistré.
- **Rendement log horaire** : r = ln(clôture / clôture de la bougie précédente). Il n'existe que si la
  bougie de l'heure précédente existe.
- **Ligne journalière** : une par jour et par paire, à 00:00 UTC (clôture de la bougie de 23:00). Elle
  est connue à l'`available_at` de cette bougie. Un jour sans bougie de 23:00 n'a pas de ligne.
- **Origine de prévision** (évaluée) : une ligne journalière d'une paire qui a au moins **400 jours
  d'historique** (origine − ouverture de sa première bougie ≥ 400 jours). Les lignes des 400 premiers
  jours ne sont jamais évaluées ; elles servent à l'entraînement (§5).

## 2. Cible

Pour H ∈ {1, 3, 7} jours : **RV_H** = racine de la somme des carrés des 24·H rendements horaires qui
**suivent** l'origine (le premier est celui de la bougie de 00:00).

- La fenêtre doit être contiguë : une seule bougie manquante, et il n'y a pas de cible.
- Les modèles et les mesures travaillent sur la variance RV_H² (colonne `rv2_<H>`).
- Une variance réalisée nulle (prix immobile sur toute la fenêtre) n'a ni logarithme ni QLIKE fini :
  la ligne n'est ni entraînée ni évaluée.

## 3. Variables

Toutes sont connues à l'origine. Les heures sont comptées sur une grille horaire complète depuis la
première bougie : une variance passée est la moyenne des rendements horaires au carré **connus** de sa
fenêtre, s'ils couvrent au moins **95 %** de la fenêtre (23 heures sur 24, 160 sur 168, 684 sur 720) ;
sinon la variable manque. La fenêtre doit tenir entièrement après la première bougie (30 jours de
cotation pour la variable « mois »).

| Variable | Définition |
|---|---|
| `log_var_d` | log de la variance réalisée horaire moyenne du dernier jour (24 h) |
| `log_var_w` | la même sur les 7 derniers jours (168 h) |
| `log_var_m` | la même sur les 30 derniers jours (720 h) |
| `btc_log_var_d`, `btc_log_var_w`, `btc_log_var_m` | les trois mêmes pour BTC |
| `dow` | jour de la semaine du jour qui commence à l'origine (lundi = 0 … dimanche = 6) |

- « Variance réalisée horaire moyenne » = moyenne des carrés des rendements horaires de la fenêtre. Les
  fenêtres se terminent à la bougie de 23:00, incluse.
- Une variance nulle n'a pas de logarithme : la variable est manquante.
- Les variables de BTC sont jointes vers le passé sur `available_at` : la ligne BTC du même jour,
  seulement si elle est disponible à la décision (au plus 1 h avant). Jamais celle de la veille.
- **Ligne complète** : les sept variables sont connues. Seules les lignes complètes sont entraînées ou
  évaluées.

## 4. Modèles

Aucun réglage d'hyperparamètres : tout est fixé ici. Chaque modèle prévoit F, la variance des H
prochains jours.

| Modèle | Définition |
|---|---|
| **M0** `M0_RECENT_7D` (référence) | variance horaire moyenne des 168 dernières heures × 24·H |
| **M1** `M1_EWMA` | variance journalière lissée, λ = 0,94, × H |
| **M2** `M2_HAR_PAIR` | HAR par paire : régression linéaire (moindres carrés) de log RV_H² sur `log_var_d`, `log_var_w`, `log_var_m` de la paire, avec constante |
| **M3** `M3_HAR_POOLED` | HAR commun : la même régression, toutes paires ensemble |
| **M4** `M4_HAR_POOLED_BTC` | M3 plus les trois log-variances de BTC |
| **M5** `M5_LGBM_POOLED` | LightGBM commun : variables de M4 + `dow` ; 300 arbres, learning_rate 0,05, num_leaves 15, min_child_samples 200, graine du protocole |

Précisions, fixées elles aussi :
- **M1.** Variance journalière = somme des carrés des 24 rendements horaires du jour. Lissage
  s = 0,94 × s précédent + 0,06 × variance du jour, initialisé à la première variance journalière
  connue de la paire. Un jour incomplet laisse s inchangé.
- **Retour du log à la variance (M2 à M5).** Ces modèles prévoient log RV_H². La variance prévue est
  F = exp(log prévu) × c, où c est la moyenne de exp(résidu) sur l'entraînement du modèle (estimateur
  de Duan). Sans ce facteur, exp(log prévu) vise la médiane de RV_H², plus basse que sa moyenne, et le
  QLIKE pénalise fortement une sous-estimation systématique. c est appris sur l'entraînement seulement ;
  c'est l'échelle qui y minimise le QLIKE moyen. M0 et M1 ne sont pas corrigés.
- **M5.** Cible log RV_H², perte quadratique, API native de LightGBM, sans sous-échantillonnage ni arrêt
  précoce, mode déterministe ; `dow` est une variable numérique. Les autres réglages sont ceux par
  défaut de LightGBM.
- **Taille minimale.** Un modèle n'est ajusté qu'avec au moins **100 lignes d'entraînement** (celles de
  la paire pour M2). En dessous, il ne prévoit pas ce mois-là.

M0 est la règle « volatilité récente × √horizon » prise sur 7 jours de bougies 1 h : c'est la fenêtre
des barrières du ML swing. Le plan indicatif du tableau de bord (`outlook/pair.py`) calcule aujourd'hui
sa volatilité sur les 96 dernières bougies 15 min (24 h) ; cette variante n'est pas comparée ici.

## 5. Entraînement glissant causal

- **Réajustement le 1er de chaque mois**, à 00:00 UTC, sur fenêtre croissante : toutes les lignes
  journalières passées, complètes, à cible connue. M2 utilise celles de la paire ; M3 à M5, celles de
  toutes les paires, y compris les lignes des 400 premiers jours de chaque paire.
- **Purge.** Une ligne d'entraînement n'est gardée que si sa fenêtre cible se termine au plus tard à la
  date de réajustement : origine + H jours ≤ date de réajustement. Sa dernière bougie clôture alors
  juste avant cette date ; elle est disponible en même temps que la bougie de décision de la première
  origine du mois. Exemple : au réajustement du 2024-03-01, la dernière ligne d'entraînement à 7 jours
  a pour origine le 2024-02-23.
- Le modèle ajusté au mois m prévoit **toutes les origines du mois m**.
- Première prévision hors échantillon : **2019-01-01**.
- Dernière origine : fin de DEVELOPMENT moins H jours, soit le 2025-06-30, le 2025-06-28 et le
  2025-06-24 pour H = 1, 3 et 7. La cible se termine alors avec la dernière bougie de DEVELOPMENT.
- **Échantillon commun.** Un couple (paire, origine) n'est évalué à l'horizon H que si la ligne est
  complète, la cible connue et que **les six modèles** ont une prévision. Toutes les comparaisons d'un
  horizon portent donc sur les mêmes lignes.

## 6. Mesures

- **Perte principale, QLIKE sur les variances** : L = RV²/F − ln(RV²/F) − 1. Elle vaut 0 quand la
  prévision est exacte ; sous-estimer coûte plus cher que surestimer du même facteur. Elle classe
  correctement des prévisions de variance même quand la variance réalisée n'est qu'une mesure bruitée de
  la vraie variance (Patton, 2011).
- **Perte secondaire** : erreur quadratique de log RV, (ln RV − ln √F)².
- Pour chaque modèle M1 à M5 et chaque horizon : différence de perte avec M0 (modèle − M0 : négative
  quand le modèle fait mieux), **moyennée par jour d'origine sur les paires**, soit une valeur par jour.
- **IC** de la moyenne de ces valeurs journalières : `calendar_mean_ci` (Student sur sommes par blocs de
  jours calendaires, variance robuste à un retard), blocs de max(10, 2·H) jours, soit 10, 10 et 14
  jours, au moins 20 blocs. Niveau corrigé de Bonferroni pour les 15 comparaisons :
  1 − 0,05/15 ≈ 99,67 %, bilatéral.
- « QLIKE moyen » désigne partout la moyenne de ces valeurs journalières : chaque jour pèse autant, quel
  que soit le nombre de paires évaluées ce jour-là.
- Également rapportés : nombre de prévisions, de jours et de paires ; QLIKE moyen du modèle et de M0 ;
  différence par année civile ; part des paires où le modèle fait mieux ; perte secondaire.

## 7. Règle

Un couple (modèle, horizon) est **« PREVISION_UTILE »** si TOUT est vrai :

1. la borne haute de l'IC de la différence de QLIKE est < 0 (le modèle fait mieux que M0) ;
2. le QLIKE moyen est inférieur à celui de M0 dans **au moins 6 années civiles sur 7** (2019 à 2025,
   année de l'origine ; 2025 s'arrête en juin) ;
3. le QLIKE moyen est inférieur à celui de M0 pour **au moins 70 % des paires** (par paire : moyenne sur
   ses origines évaluées ; seules les paires qui ont au moins une origine évaluée comptent) ;
4. la perte secondaire va dans le même sens : erreur de log RV plus faible en moyenne.

Par horizon, le modèle retenu est celui dont le QLIKE moyen est le plus bas parmi les utiles ; à défaut,
« AUCUNE_AMELIORATION ». **Verdict global enregistré** : « PREVISION_UTILE » si un horizon au moins a
un modèle utile, sinon « AUCUNE_AMELIORATION ».

## 8. Essais

5 modèles × 3 horizons = **15 comparaisons déclarées**. Ce ne sont pas des essais de rentabilité, mais
ce sont 15 regards de plus sur DEVELOPMENT : ils sont comptés quand même dans le programme
(`n_trials = 15`, ajoutés aux essais du programme enregistrés au moment de l'exécution : 638 à la date
de cette déclaration, davantage après les protocoles exécutés le même jour).

Chaque exécution est enregistrée au registre des expériences (`kind = VOLATILITY`,
`strategy = VOLATILITY_FORECAST`, période DEVELOPMENT) avec le verdict, les 15 lignes, les empreintes des
données, le commit et les versions. Elle écrit aussi `reports/<run_id>/summary.json` et les prévisions
hors échantillon (`forecasts.parquet`), pour la vérification indépendante.

## 9. Audit des fuites, exécuté avant tout résultat

En échec, aucun résultat n'est produit et aucun essai n'est enregistré (`LeakAuditFailed`).

- Sur BTC, ETH **et** SOL, tous trois exigés, 3 origines tirées avec la graine du protocole.
- Tout ce qui est lu à l'origine (les sept variables, les entrées de M0 et de M1, l'ancienneté de la
  paire) est recalculé avec les seules bougies disponibles à l'origine (`available_at` ≤ celui de la
  bougie de 23:00), puis avec un futur falsifié. Le résultat doit être identique au calcul complet.
- Une **mutation** doit être détectée sur chacune des trois paires : la variable « jour » dont la
  fenêtre avance d'une heure et lit la première heure **après** l'origine.

## 10. Lecture déclarée

- **AUCUNE_AMELIORATION** : aucun modèle testé ne prévoit la variance mieux que M0 selon la règle. Ce
  sera écrit tel quel ; la règle actuelle reste en place.
- **PREVISION_UTILE** : sur DEVELOPMENT, le modèle retenu prévoit la variance réalisée mieux que M0 à cet
  horizon. Cela ne dit **rien sur la direction ni sur la rentabilité** : dimensionner ou placer des
  niveaux avec une meilleure prévision de volatilité ne crée pas d'avantage à lui seul.
- Le modèle retenu est le meilleur de cinq sur des données déjà vues. Avant tout usage, il resterait à le
  confirmer sur des données jamais consultées (période finale réservée ou observation prospective), puis
  à le brancher dans une étape séparée.

## 11. Limites déclarées

- **Univers de survivantes** : paires choisies le 2026-10-01 parmi celles encore cotées. Une paire
  retirée de la cote n'y figure pas.
- **Cibles qui se chevauchent** pour H > 1 : une origine par jour, des fenêtres de 3 et 7 jours. D'où
  les blocs calendaires ; une dépendance plus longue que deux blocs voisins rendrait l'IC trop étroit.
- **Disponibilité des bougies** : `available_at` = clôture + 2 s est une hypothèse, non mesurée sur
  l'historique.
- **Période finale** (après le 2025-06-30) : non lue.
- **Trous de données** : la cible reste exigée contiguë (une heure manquante dans les H jours qui
  suivent supprime l'origine) ; les variables tolèrent 5 % d'heures manquantes. Une interruption de
  plusieurs heures retire encore les jours et semaines qui la contiennent ; un trou dans BTC retire les
  mêmes jours à toutes les paires. Le nombre d'origines évaluées par paire et par horizon est enregistré.
- **Paires renommées** : POL et RENDER (anciennement MATIC et RNDR, d'après les dates de première
  bougie ; non vérifié dans le dépôt) n'ont que l'historique de leur nouveau symbole et aucune origine
  évaluée ; 38 paires comptent au critère des paires.
- **Poids des jours** : une valeur par jour, quel que soit le nombre de paires. En 2019, peu de paires
  ont 400 jours d'historique ; la moyenne du jour y repose sur quelques paires.
- **Critère des paires** : une paire récente, avec peu d'origines, compte autant que BTC. Une paire qui
  n'atteint pas 400 jours d'historique avant la fin de DEVELOPMENT n'a aucune origine : elle n'est pas
  comptée, mais ses lignes entraînent M3 à M5.
- **Perte secondaire** : l'erreur de log RV n'est pas robuste au bruit de mesure de la variance
  réalisée. Le critère 4 peut donc écarter un modèle réellement meilleur ; il ne peut pas en faire
  passer un.
- **M5** : son facteur de retour à la variance vient de résidus d'entraînement, plus petits que hors
  échantillon. Il est donc un peu trop faible, ce qui joue contre M5.
- **Niveau de l'IC** : la correction de Bonferroni porte sur ces 15 comparaisons, pas sur tout le
  programme. Le calibrage de `calendar_mean_ci` a été simulé à 95 %, pas à 99,67 %.
- **Heure d'origine** : 00:00 UTC seulement. Le résultat ne vaut pas pour une prévision faite à une
  autre heure.
- **Référence** : M0 utilise 7 jours de bougies 1 h. Le protocole ne dit rien d'une volatilité mesurée
  sur 24 h de bougies 15 min.
- **Variances nulles** : exclues (§2, §3). Elles sont attendues très rares.

## 12. Résultats (exécution unique du 2026-10-01)

Exécution `VOL-20261001T194742Z-926ff2`, code du commit `81f9f13` (arbre propre), audit des fuites
réussi, 38 paires évaluées (POL et RENDER sans origine), 2 202 à 2 321 jours par horizon de 2019 à
2025-06. 15 comparaisons ; programme : **713 essais**. Période finale non consultée.

**Verdict : PREVISION_UTILE.** Retenus : **M5 LightGBM** à 1 et 3 jours, **M4 HAR + BTC** à 7 jours.

| Horizon | QLIKE de M0 (référence) | Meilleur modèle utile | QLIKE | Écart [IC 99,67 %] |
|---|---|---|---|---|
| 1 jour | 0,492 | M5 LightGBM | 0,424 | −0,068 [−0,119 ; −0,018] |
| 3 jours | 0,437 | M5 LightGBM | 0,344 | −0,092 [−0,158 ; −0,027] |
| 7 jours | 0,448 | M4 HAR + BTC | 0,285 | −0,163 [−0,257 ; −0,070] |

- Les HAR (M2 à M4) ont la meilleure QLIKE à 1 et 3 jours (−0,095 et −0,113, IC entièrement < 0, les
  7 années, presque toutes les paires) mais une erreur de log RV un peu plus grande que M0 : le
  critère 4 les écarte à ces horizons, comme déclaré. M5 passe les quatre critères.
- À 7 jours, les cinq modèles passent les quatre critères ; EWMA lui-même bat M0.
- La référence M0 (volatilité des 7 derniers jours × √horizon) est simple : « mieux que M0 » veut
  dire qu'on prévoit l'ampleur des prochains jours plus finement que par la règle usuelle, pas que la
  prévision est précise.

Ce que ce résultat permet, et ce qu'il ne permet pas :
- il permet de dimensionner une position ou de placer un stop et un objectif selon la volatilité
  prévue, et d'afficher une « volatilité prévue » à côté d'un signal ;
- il ne dit rien de la direction ni de la rentabilité : ce n'est pas un signal d'achat ;
- c'est une sélection sur DEVELOPMENT (15 comparaisons sur des données en partie déjà parcourues) :
  sa confirmation sur la période finale réservée reste à décider par le propriétaire.

## 13. En service (2026-10-01)

La prévision est branchée, comme **information** : aucune décision de CSI n'en dépend.

- `outlook/volatility.py` : une fois par jour (10 minutes après 00:00 UTC), la surveillance calcule pour
  chaque paire de l'univers le **mouvement typique attendu** à 1, 3 et 7 jours, avec les modèles retenus
  ci-dessus (LightGBM à 1 et 3 jours, HAR + BTC à 7 jours), réajustés le 1er de chaque mois comme dans le
  protocole. Même code que la recherche ; les bougies sont celles du magasin de la surveillance, jusqu'à
  maintenant. Résultat dans `state/volatility.json`.
- « Mouvement typique » = écart-type prévu du rendement sur la période (racine de la variance réalisée
  prévue), en %. Il dit l'ampleur, jamais le sens.
- Pas de prévision, et c'est écrit, pour une paire de moins de 400 jours d'historique, sans bougie de
  23:00, ou avec un trou récent : jamais de valeur de remplacement.
- Tableau de bord : carte « Ampleur attendue » dans l'analyse d'une paire, tableau « Prévisions du
  jour » dans l'onglet Marché, et, pour un signal évalué, TP1 et stop exprimés en nombre de mouvements
  prévus. API : `GET /volatility` et `GET /volatility?symbol=X`.
- Rappel : résultat utile sur DEVELOPMENT, non confirmé sur la période finale. Les modèles en service
  apprennent aussi des données récentes (après 2025-06) ; cela ne consulte pas la période finale au sens
  du protocole (aucune mesure d'erreur n'y est faite).

## 14. Protocole v2 — combiner et enrichir (déclaré le 2026-10-02, avant exécution)

Étape 7 du plan de travail validé le 2026-10-02. Code : `research/volatility_v2.py` (le module v1 n'est pas
modifié ; il est en service). Tests : `tests/test_volatility_v2.py`. Commande : `csi volatility-v2`.

**Question.** Un candidat prévoit-il la variance réalisée à 1, 3 et 7 jours mieux que le **modèle en service**
(§ 12-13 : LightGBM à 1 et 3 jours, HAR + BTC à 7 jours) ? La référence n'est plus M0 : « mieux que M0 » est
acquis ; ce qui compte est « mieux que ce qui tourne ».

**Cadre repris sans changement** (§ 1 à 6, 9) : données, univers, origines, cibles, variables du lot 7, purge,
réajustement mensuel sur fenêtre croissante, QLIKE et erreur de log RV, IC calendaires (blocs de max(10, 2·H)
jours, 20 blocs au moins), échantillon commun, réglages de HAR et de LightGBM. La référence est réajustée ici
sur les mêmes lignes que les candidats (lignes complètes au sens v2, légèrement moins nombreuses que celles du
lot 7 quand une variable v2 manque).

**Variables ajoutées**, toutes connues à l'origine, sur la même grille horaire :

| Variable | Définition |
|---|---|
| `neg_share_d`, `neg_share_w`, `neg_share_m` | part négative de la variance : moyenne des carrés des rendements horaires négatifs / moyenne des carrés de tous les rendements, sur 24, 168 et 720 h (couverture ≥ 95 %) |
| `log_park_d`, `log_park_w` | log de la variance de Parkinson moyenne, ln(plus haut / plus bas)² / (4 ln 2) par bougie 1 h, sur 24 et 168 h ; les colonnes `high` et `low` du magasin long sont donc lues (empreintes enregistrées) |
| `log_dvol_var` | log de la variance horaire implicite de l'indice **DVOL** de Deribit pour BTC : (DVOL / 100)² / 8 760, clôture journalière de la journée qui se termine à l'origine (connue à 00:00 UTC, jamais celle du lendemain) ; même valeur pour toutes les paires (variable de marché, comme les variances de BTC) |
| `dvol_spread` | `log_dvol_var` − `log_var_w` (écart implicite − réalisé) |

DVOL : API publique de Deribit (`get_volatility_index_data`, résolution 1 jour, en liste blanche), clôtures
depuis le 2021-03-24, mises en cache dans `data/options/dvol_btc.parquet` ; la série lue est coupée aux journées
commencées avant la fin de DEVELOPMENT et son empreinte est enregistrée.

**Candidats** (aucun nouveau réglage) :

| Candidat | Définition |
|---|---|
| `REF_SERVICE` (référence) | M5 à 1 et 3 jours, M4 à 7 jours, réajustés sur les mêmes lignes |
| `V1_MEAN_M4_M5` | moyenne arithmétique des variances prévues par M4 et M5 |
| `V2_HAR_SEMIVAR` | HAR commun + BTC + les trois parts négatives |
| `V3_LGBM_ENRICHED` | LightGBM commun : variables de M5 + parts négatives + `log_park_d`, `log_park_w` |
| `V4_HAR_DVOL` | HAR commun + BTC + `log_dvol_var` + `dvol_spread`, ajusté et évalué sur les seules lignes à DVOL connu (origines ≥ 2021-03-25) |

- **Échantillon.** Référence, V1, V2 et V3 sont comparés sur l'échantillon commun complet (2019-01-01 → fin de
  DEVELOPMENT, 400 jours d'historique). V4 est comparé à la référence **sur ses propres lignes** (DVOL connu) :
  moins de jours, intervalles plus larges, et c'est déclaré.
- **Règle** : celle du § 7, référence = modèle en service, avec une seule adaptation : le critère des années
  exige « toutes les années couvertes sauf au plus une » (6 sur 7 pour V1 à V3 ; 4 sur 5 pour V4, dont les
  années vont de 2021 à 2025). Verdict par horizon : meilleur QLIKE parmi les candidats utiles, sinon
  `AUCUNE_AMELIORATION` ; verdict global `MIEUX_QUE_SERVICE` si un horizon au moins a un candidat utile.
- **Essais** : 4 candidats × 3 horizons = **12 comparaisons**, comptées au programme ; niveau de Bonferroni
  1 − 0,05/12 ≈ 99,58 %.
- **Audit des fuites avant tout résultat** (§ 9 étendu) : sur BTC, ETH et SOL, 3 origines à DVOL connu par paire ;
  bougies **et** série DVOL tronquées à la décision puis falsifiées après elle ; les 15 entrées v2 (celles du lot 7,
  parts négatives, Parkinson, DVOL) doivent être identiques ; deux mutations à détecter sur chaque paire : la
  fenêtre « jour » avancée d'une heure (lot 7) et la bougie DVOL de la journée qui **commence** à l'origine.
- **Attendu et lecture déclarée.** V1 (combinaison) : gain faible, peut-être mesurable à 1 et 3 jours ; V2 et V3 :
  gain faible ou nul (la littérature trouve les semi-variances utiles surtout à 1 jour sur les indices) ; V4 : sur
  ≈ 1 500 jours, un gain modeste serait indétectable. `AUCUNE_AMELIORATION` laisse le service en l'état ;
  `MIEUX_QUE_SERVICE` ne remplace rien automatiquement : le candidat retenu est le meilleur de quatre sur des
  données déjà vues, à confirmer sur la période finale réservée ou en observation prospective avant tout
  branchement, dans une étape séparée.
- **Limites** : celles du § 11 ; DVOL n'existe que pour BTC et ETH (seul BTC est utilisé) et depuis 2021 ; le
  plus haut et le plus bas d'une bougie 1 h dépendent des cotations réelles de Binance (une mèche aberrante entre
  dans la variance de Parkinson) ; la référence réajustée ici peut différer d'un cheveu de celle du lot 7
  (lignes complètes v2) ; **asymétrie de V4** : la référence apprend sur toutes les lignes complètes (2017 compris)
  alors que V4 n'apprend que sur les lignes à DVOL connu (2021 et après) — un écart V4 − référence mêle l'effet de
  DVOL et celui d'un entraînement plus court, et aucun modèle de contrôle n'est ajouté (ce serait un 13e essai) ;
  la ligne journalière du 2025-07-01 00:00 (clôture de la dernière bougie de DEVELOPMENT) existe mais est inerte
  (aucune cible, jamais entraînée, jamais évaluée, jamais auditée) ; une bougie DVOL de Deribit qui ne commencerait
  pas à 00:00 UTC est refusée, pas arrondie.

## 15. Résultats du protocole v2 (`VOL-20261002T170500Z-c3bda6`, exécuté le 2026-10-02, 12 comparaisons, programme 749)

Code du commit `e27f00e` (arbre propre, après la relecture indépendante), audit des fuites réussi (BTC, ETH, SOL ; 3
origines à DVOL connu + 3 quelconques par paire ; les deux mutations détectées sur chaque paire), 38 paires
évaluées (POL et RENDER sans origine), 2 202 à 2 321 jours par horizon pour V1 à V3, 1 492 à 1 542 pour V4 ; DVOL
BTC : 1 560 clôtures du 2021-03-24 au 2025-06-30, toutes alignées sur 00:00 UTC (empreinte `498ca9f065bd9f4d`).
Période finale non consultée.

**Verdict : `MIEUX_QUE_SERVICE` à 3 jours seulement**, par la combinaison V1 (moyenne de HAR + BTC et LightGBM).

| Candidat | Horizon | QLIKE | Service | Écart [IC 99,58 %] | Années | Paires mieux | Perte secondaire | Utile |
|---|---|---|---|---|---|---|---|---|
| V1 moyenne M4/M5 | 1 j | 0,392 | 0,424 | −0,032 [−0,060 ; −0,004] | 6/6 | 97 % | pire (+0,004) | **non** (critère 4) |
| V2 HAR + semi-variances | 1 j | 0,400 | 0,424 | −0,024 [−0,063 ; +0,016] | 4/6 | 66 % | pire | non |
| V3 LightGBM enrichi | 1 j | 0,428 | 0,424 | +0,004 [−0,022 ; +0,029] | 4/6 | 45 % | mieux | non |
| V4 HAR + DVOL | 1 j | 0,696 | 0,365 | +0,33 [−0,62 ; +1,28] | 2/4 | 32 % | pire | non |
| **V1 moyenne M4/M5** | **3 j** | **0,318** | 0,344 | **−0,027 [−0,045 ; −0,008]** | **7/6** | **100 %** | mieux | **oui** |
| V2 HAR + semi-variances | 3 j | 0,326 | 0,344 | −0,018 [−0,053 ; +0,016] | 7/6 | 89 % | pire | non |
| V3 LightGBM enrichi | 3 j | 0,342 | 0,344 | −0,002 [−0,018 ; +0,014] | 5/6 | 87 % | mieux | non |
| V4 HAR + DVOL | 3 j | 3 574 | 0,290 | aberrant (voir ci-dessous) | 4/4 | 42 % | pire | non |
| V1 moyenne M4/M5 | 7 j | 0,294 | 0,285 | +0,009 [−0,011 ; +0,030] | 2/6 | 29 % | mieux | non |
| V2 HAR + semi-variances | 7 j | 0,286 | 0,285 | +0,001 [−0,006 ; +0,008] | 2/6 | 61 % | mieux | non |
| V3 LightGBM enrichi | 7 j | 0,329 | 0,285 | +0,044 [−0,013 ; +0,101] | 2/6 | 11 % | pire | non |
| V4 HAR + DVOL | 7 j | 0,246 | 0,245 | +0,001 [−0,030 ; +0,032] | 3/4 | 66 % | mieux | non |

Tableau complet et prévisions : `reports/VOL-20261002T170500Z-c3bda6/` (`summary.json`, `forecasts.parquet`).

Lecture :
- **La combinaison des deux modèles en service (V1) fait mieux que LightGBM seul à 3 jours** sur les quatre
  critères : QLIKE −0,027 (≈ 8 % de la perte), intervalle de Bonferroni entièrement sous zéro, les 7 années, les 38
  paires, erreur de log RV plus faible aussi. À 1 jour, le gain de QLIKE est du même ordre et aussi net (6 années
  sur 6, 97 % des paires) mais l'erreur de log RV est légèrement plus grande : le critère 4, déclaré, l'écarte. À
  7 jours, la moyenne est moins bonne que HAR + BTC seul (LightGBM y est le plus faible des deux).
- **Les semi-variances (V2) n'apportent rien de démontrable** ; **l'enrichissement de LightGBM (V3) n'apporte
  rien et dégrade à 7 jours** (plus de variables, mêmes 300 arbres : il apprend du bruit).
- **V4 (DVOL) est inexploitable tel que déclaré** : ses prévisions aberrantes (rapport à la référence jusqu'à
  10¹⁴ à 3 jours, 430 à 1 jour) viennent toutes du **premier mois** (avril-mai 2021), où le modèle est ajusté sur
  266 lignes à DVOL connu (7 journées × 38 paires : le seuil de 100 lignes, pensé pour une paire, ne protège pas
  un modèle commun) et extrapole sur des variables redondantes (`dvol_spread` = `log_dvol_var` − `log_var_w`,
  colinéaire avec les autres entrées). Hors ce mois, V4 vaut la référence à 7 jours (+0,001) et ne la bat
  nulle part. Retirer ce mois ou exiger 30 journées distinctes serait une règle choisie après lecture : ce serait
  un 13e essai, non exécuté. Conclusion honnête : **aucun apport démontré de DVOL**, et un défaut de protocole
  (seuil de lignes pour un modèle commun) à retenir pour toute version suivante.
- **Ce que cela permet** : une prévision à 3 jours un peu plus fine, en moyennant les deux modèles déjà calculés
  (aucun nouveau modèle à entraîner). **Ce que cela ne permet pas** : rien sur la direction ni la rentabilité ; et
  le gain est sélectionné sur DEVELOPMENT (12 comparaisons de plus, 749 au programme) : avant tout branchement,
  confirmation sur des données jamais consultées (période finale réservée, ou observation prospective). Le service
  (`outlook/volatility.py`) ne journalise que le modèle retenu par horizon, pas les deux : la moyenne ne peut pas
  être mesurée après coup sur ses fichiers. C'est l'objet du test en direct **F12_VOL_FORWARD**
  (`FORWARD_TESTS.md`), qui journalise chaque jour toutes les prévisions et mesure leurs QLIKE une fois les
  variances réalisées connues. Rien n'est branché par ce lot.

## 16. Protocole v3 — volatilité à toute heure (déclaré le 2026-10-02, avant exécution)

Piste 2 de l'étude du 2026-10-02 : la volatilité qui sert aux stops et aux objectifs est celle des heures qui
suivent une décision prise à **n'importe quelle heure**, pas seulement à 00:00. Code : `research/volatility_hourly.py`
(le module v1 n'est pas modifié). Tests : `tests/test_volatility_hourly.py`. Commande : `csi volatility-hourly`.

**Question.** À chaque clôture 1 h, un modèle prévoit-il la variance réalisée des **4 h** et des **24 h** suivantes
mieux que la règle du tableau de bord, **R0** = variance horaire moyenne des 24 dernières heures × H (le plan
indicatif d'`outlook/pair.py` lit 96 bougies de 15 min, soit les mêmes 24 heures ; le magasin long n'a que des
bougies 1 h, la règle est donc prise sur bougies 1 h) ?

**Cadre repris du lot 7** (§ 1, 2, 3, 4, 6, 9) : mêmes données, univers, coupure à DEVELOPMENT, données complètes
exigées, rendements log horaires, fenêtres passées tolérantes à 5 % d'heures manquantes, cibles contiguës, HAR et
LightGBM aux mêmes réglages (300 arbres, 15 feuilles, apprentissage 0,05, 200 lignes par feuille, facteur de Duan),
100 lignes d'entraînement au moins, QLIKE et erreur de log RV, IC calendaires (blocs de 10 jours, 20 blocs au
moins), échantillon commun, audit des fuites avant tout résultat.

**Différences déclarées.**
- **Origines** : chaque bougie 1 h close (24 par jour et par paire), à partir du 2019-01-01, après 400 jours
  d'historique de la paire ; variables : log-variances horaires moyennes des **24, 168 et 720** dernières heures
  (fenêtres terminées à la bougie de décision incluse), les trois mêmes pour BTC (jointes vers le passé sur
  `available_at`, au plus 1 h), heure et jour de semaine de l'origine.
- **Cibles** : RV_4² et RV_24² = sommes des carrés des 4 et 24 rendements horaires qui suivent, contiguës.
- **Profil** : moyenne de log RV_H² par case (jour de semaine × heure, 168 cases) sur les lignes d'entraînement du
  réajustement, appliquée à l'entraînement et aux origines prévues (case absente : moyenne générale).
- **Réajustement le 1er de chaque trimestre** (janvier, avril, juillet, octobre), sur une **fenêtre glissante de
  3 ans** de lignes purgées (origine + H heures ≤ date de réajustement), toutes paires ensemble : environ 1 million
  de lignes par ajustement, c'est le volume que le PC supporte en moins de deux heures.
- **Modèles** : `R0_RECENT_24H` (référence) ; `H1_HAR_PROFILE` = HAR commun sur les six log-variances + profil ;
  `H2_LGBM_PROFILE` = LightGBM commun sur les mêmes + heure et jour de semaine.
- **Mesure** : une valeur par jour calendaire (moyenne des pertes de toutes les origines et paires du jour), IC
  de Student par blocs de 10 jours, niveau de Bonferroni 1 − 0,05/4 = 98,75 %.
- **Règle** : celle du § 7 (borne haute < 0, 6 années sur 7, 70 % des paires, perte secondaire) ; verdict
  `PREVISION_UTILE` / `AUCUNE_AMELIORATION` par horizon et global.
- **Essais** : 2 candidats × 2 horizons = **4 comparaisons**, comptées au programme.
- **Audit des fuites** : BTC, ETH, SOL, 3 origines horaires par paire tirées parmi toutes les heures ; données
  tronquées et futur falsifié identiques ; mutation (fenêtre 24 h avancée d'une heure) détectée sur chaque paire.

**Attendu et lecture déclarée.** La saisonnalité intrajournalière (week-end, heures asiatiques) est connue et
forte : H1 devrait battre R0 nettement à 4 h ; à 24 h, l'avantage attendu est plus faible (le profil se moyenne
sur une journée). `PREVISION_UTILE` à 4 h signifierait qu'un stop ou un objectif placé à partir de la volatilité
prévue serait mieux dimensionné qu'avec les 24 dernières heures ; **rien n'est branché** par ce lot, et rien n'en
dit la direction ni la rentabilité.

**Limites déclarées.** Celles du § 11 ; origines voisines très dépendantes (24 par jour, cibles qui se
chevauchent : les blocs calendaires tiennent compte de la dépendance d'un jour à l'autre, pas de plus loin) ; une
seule valeur par jour pèse autant en 2019 (peu de paires) qu'en 2025 ; la règle de 15 min du tableau de bord n'est
pas comparée exactement.

### Résultats du protocole v3 (`VOL-20261002T211837Z-be55c0`, exécuté le 2026-10-02, 4 comparaisons, programme 753)

Code du commit `231f40f` (arbre propre ; relecture indépendante pendant le calcul : aucun défaut faussant la mesure,
deux dettes corrigées ensuite sans effet sur le résultat : alignement du premier trimestre, unité « heures »
explicite dans le registre), audit des fuites réussi (BTC, ETH, SOL, 3 origines horaires chacune, mutation
détectée), 38 paires évaluées, **1,40 million de prévisions par horizon** sur 2 341 à 2 349 jours (2019-01-01 →
2025-06), 26 réajustements trimestriels. Période finale non consultée.

**Verdict : `PREVISION_UTILE` à 24 h** (HAR + profil retenu) ; `AUCUNE_AMELIORATION` à 4 h par la règle.

| Modèle | Horizon | QLIKE | R0 | Écart [IC 98,75 %] | Années | Paires mieux | Erreur de log RV | Utile |
|---|---|---|---|---|---|---|---|---|
| **H1 HAR + profil** | **24 h** | **0,382** | 0,659 | **−0,277 [−0,342 ; −0,212]** | 7/7 | 100 % | plus faible (−0,017) | **oui** |
| H2 LightGBM + profil | 24 h | 0,411 | 0,659 | −0,248 [−0,315 ; −0,182] | 7/7 | 100 % | plus faible (−0,011) | oui |
| H1 HAR + profil | 4 h | 0,728 | 0,905 | −0,176 [−0,217 ; −0,135] | 7/7 | 100 % | **plus grande (+0,055)** | non (critère 4) |
| H2 LightGBM + profil | 4 h | 0,749 | 0,905 | −0,156 [−0,196 ; −0,116] | 7/7 | 100 % | plus grande (+0,042) | non (critère 4) |

Tableau complet et prévisions : `reports/VOL-20261002T211837Z-be55c0/` (`summary.json`, `forecasts_4h.parquet`,
`forecasts_24h.parquet`).

Lecture :
- **La règle « 24 dernières heures » est une mauvaise référence à toute heure** : les deux candidats la battent
  de 20 à 40 % de QLIKE, chaque année de 2019 à 2025, sur chacune des 38 paires, avec des intervalles très loin de
  zéro. L'essentiel vient de deux choses connues et déclarées : la **saisonnalité** (heure et jour de semaine : le
  profil) et la **mémoire longue** (168 h et 720 h, que la règle ignore).
- **À 24 h, H1 (HAR + profil) est utile** sur les quatre critères, et LightGBM n'apporte rien de plus que la
  régression linéaire (le profil capte déjà la saisonnalité).
- **À 4 h, le critère 4 écarte les deux candidats** : leur QLIKE est bien meilleur, mais leur erreur quadratique
  de log RV est plus grande que celle de R0. Comme déclaré au § 11 du lot 7, cette perte secondaire est peu robuste
  au bruit de la variance réalisée, et à 4 h la variance réalisée n'est faite que de **4 rendements horaires** : une
  mesure très bruitée, où une prévision bien centrée en moyenne (QLIKE) peut avoir une plus grande erreur de log.
  La règle était déclarée avant exécution : elle s'applique, et le résultat à 4 h se lit comme « gain de QLIKE
  net, non retenu par la règle », pas comme un échec.
- **Ce que cela permet** : à 24 h, un stop ou un objectif dimensionné sur la volatilité prévue par HAR + profil
  serait mieux calibré que sur les 24 dernières heures ; c'est une prévision d'**ampleur**, rien sur la direction
  ni la rentabilité. **Rien n'est branché** : le plan indicatif du tableau de bord garde sa règle ; la sélection a
  eu lieu sur DEVELOPMENT (4 comparaisons de plus, 753 au programme) et doit être confirmée sur des données jamais
  consultées (période finale réservée, ou observation prospective) avant un branchement dans une étape séparée.

## 17. Protocole v4 — réétalonnage et GARCH (déclaré le 2026-10-03, avant exécution)

Point 5 du plan. Le rapport d'information (`INFORMATION_REPORT.md` § 4) montre des prévisions en service **bien
classées mais mal étalonnées aux extrêmes** : à 1 et 3 jours, quand la prévision est la plus basse, la volatilité
réalisée est 30 à 43 % plus forte ; à 7 jours, la prévision est trop haute d'environ 10 %. Code :
`research/volatility_v4.py` ; commande `csi volatility-v4`.

- **Référence** : `REF_SERVICE`, les prévisions hors échantillon déjà enregistrées par le protocole v2
  (`VOL-20261002T170500Z-c3bda6`) ; mêmes lignes, mêmes variances réalisées.
- **C1_REETALONNE** : log RV² = a + b · log F_service, a et b estimés le 1er de chaque mois sur les lignes dont la
  cible est connue (origine + H jours ≤ date, au moins 500 lignes, toutes paires), retour à la variance par la
  moyenne de exp(résidu).
- **C2_GARCH** : GARCH(1,1) à moyenne nulle (bibliothèque `arch`) sur les rendements log **journaliers** de chaque
  paire (clôtures de 00:00), réestimé le 1er de chaque mois sur les rendements connus avant cette date (au moins 365) ;
  à chaque origine, filtre jusqu'à l'origine avec ces paramètres et prévoit la somme des variances des H jours ;
  ramené à l'échelle de RV² par le rapport moyen RV² / prévision sur le passé purgé (500 lignes au moins). C'est la
  référence classique de la littérature, jamais comparée chez nous.
- **Règle** : celle du § 14 (référence = service, quatre critères, années couvertes − 1). **6 comparaisons**,
  Bonferroni 1 − 0,05/6, comptées au programme.
- **Lecture déclarée** : C1 corrige l'étalonnage sans toucher au classement ; un gain de QLIKE modeste est attendu
  à 1 et 3 jours. Pour C2, l'attendu est un résultat **moins bon** que le service (le GARCH n'utilise que les
  rendements journaliers de la paire, sans BTC ni variances intrajournalières) ; s'il fait mieux, c'est une
  information importante sur nos modèles. Rien n'est branché.

### Résultats du protocole v4 (`VOL-20261003T005910Z-a26dfe`, 6 comparaisons, programme 765)

**Verdict : `AUCUNE_AMELIORATION`** aux trois horizons ; 33 paires, 1 685 à 1 806 jours (lignes où les trois prévisions
existent : le GARCH demande 365 jours de rendements et le réétalonnage 500 lignes connues).

| Candidat | Horizon | QLIKE | Service | Écart [IC 99,17 %] | Années | Paires mieux |
|---|---|---|---|---|---|---|
| C1 réétalonné | 1 j | 0,373 | 0,372 | +0,002 [−0,015 ; +0,018] | 3/5 | 55 % |
| C1 réétalonné | 3 j | 0,299 | 0,292 | +0,007 [−0,007 ; +0,022] | 2/5 | 45 % |
| C1 réétalonné | 7 j | 0,249 | 0,247 | +0,002 [−0,010 ; +0,015] | 4/5 | 70 % |
| C2 GARCH(1,1) | 1 j | 0,430 | 0,372 | +0,058 [−0,005 ; +0,121] | 1/5 | 9 % |
| C2 GARCH(1,1) | 3 j | 0,337 | 0,292 | +0,045 [−0,002 ; +0,092] | 1/5 | 9 % |
| C2 GARCH(1,1) | 7 j | 0,296 | 0,247 | **+0,049 [+0,011 ; +0,087]** | 1/5 | 3 % |

- **Le réétalonnage ne gagne rien** : l'écart d'étalonnage aux extrêmes vu dans le rapport d'information pèse trop
  peu dans la QLIKE moyenne pour qu'une correction se voie. Le service reste tel quel.
- **Le GARCH(1,1), référence classique de la littérature, est nettement moins bon que nos modèles en service**
  (+15 à +20 % de QLIKE, moins bon sur presque toutes les paires, intervalle au-dessus de 0 à 7 jours). C'est une
  confirmation utile : la prévision en service apporte réellement quelque chose au-delà du modèle standard.

## 18. Confirmation sur la période finale réservée (déclarée le 2026-10-03, avant toute lecture)

Décision du propriétaire du 2026-10-03 : la période finale réservée est lue **une seule fois, pour la volatilité
seulement**. C'est le seul résultat positif du programme ; aucune stratégie directionnelle n'est candidate. Code :
`research/volatility_confirm.py`. Tests : `tests/test_volatility_confirm.py`. Commande :
`csi volatility-confirm --i-understand-final-test` (après une répétition `--rehearsal`).

**Ce qui est rejoué, sans aucun réglage nouveau** : les mêmes 40 paires, les mêmes fonctions (variables, modèles,
purge : `volatility.daily_frame`, `fit_at`, `month_forecasts`, `volatility_hourly.hourly_frame`, `fit_at`,
`quarter_forecasts`), le même rythme de réajustement (mensuel pour 1, 3 et 7 jours ; trimestriel, fenêtre de 3 ans,
pour 24 h). Les modèles s'entraînent sur tout le passé purgé connu à chaque réajustement (DEVELOPMENT, puis la
période finale déjà écoulée) : walk-forward strict.

**Fenêtre** : origines du **2025-07-01** au **2026-09-30**, cibles entièrement connues à la bougie de 2026-09-30
23:00 ; rien au-delà n'est lu (un test falsifie tout ce qui suit et exige les mêmes prévisions).

**Cinq comparaisons** :

| Clé | Modèle | Référence | Horizon | Origine |
|---|---|---|---|---|
| D1 | LightGBM groupé (M5) | variance des 7 derniers jours (M0) | 1 jour | en service (§ 12-13) |
| D3 | LightGBM groupé (M5) | M0 | 3 jours | en service |
| D7 | HAR + BTC (M4) | M0 | 7 jours | en service |
| V3 | moyenne de M4 et M5 (en variance) | M5 (le service) | 3 jours | protocole v2 (§ 15), candidat au branchement |
| H24 | HAR + profil heure × jour (H1) | « les 24 dernières heures » (R0) | 24 h | protocole v3 (§ 16), candidat au branchement |

Échantillon commun : les lignes où le modèle et sa référence ont une prévision (M0, M4, M5 pour le journalier ;
R0, H1, H2 pour l'horaire). Sur DEVELOPMENT, il est identique à celui des protocoles d'origine (vérifié à la ligne
près par la relecture du 2026-10-03, prévisions identiques au bit).

**Issue propre de chaque comparaison** :
- `CONFIRME` : borne haute de l'IC **à 95 %** de la différence de QLIKE (moyenne par jour d'origine, blocs
  calendaires du protocole d'origine, au moins 20 blocs) sous 0, différence moyenne négative en 2025
  (juillet-décembre) **et** en 2026, au moins 70 % des paires mieux prévues, erreur de log RV plus faible ;
- `CONTREDIT` : borne basse de l'IC au-dessus de 0 (démontrablement pire que la référence) ;
- `DONNEES_INSUFFISANTES` : pas d'IC (moins de 20 blocs) ;
- `NON_CONCLUANT` : tout le reste.

**Séquence fixe** (procédure hiérarchique, risque global de fausse confirmation ≤ 5 %) : **H24 → D7 → D3 → D1 →
V3**. Une comparaison n'est testée que si toutes les précédentes sont `CONFIRME` ; après le premier échec, les
suivantes sont `NON_TESTE` (leurs chiffres restent affichés, à titre descriptif seulement). L'ordre suit la puissance
estimée sur DEVELOPMENT avant la lecture : part de fenêtres de 15 mois où l'IC à 95 % est sous 0 — H24 22/22, D7
19/22, D3 16/22, D1 15/22, V3 8/22 (estimation optimiste : modèles choisis sur ces données). Cette règle remplace
celle du premier jet (Bonferroni sur 5 à 99 %), corrigée **avant toute lecture** après relecture : avec elle, D3,
D7 et V3 auraient presque sûrement échoué même avec un effet réel (5, 8 et 2 fenêtres sur 22), et la lecture unique
aurait été dépensée pour rien.

**Garde-fous de la lecture** : contrôle de couverture **avant** la consultation (heures d'ouverture seules, au moins
99 % des heures de la fenêtre et la bougie de coupure pour chaque paire ; sinon rien n'est lu ni compté) ;
consultation vérifiée et enregistrée dans une seule transaction, sous verrou d'instance ; code commité exigé.

**Ce que le résultat décide** (seul le **verdict de la séquence** compte ; une issue propre affichée sous
`NON_TESTE` est descriptive et ne décide rien ; chaque horizon est lu séparément) :
- D1, D3, D7 `CONFIRME` : la prévision en service est confirmée hors échantillon ; elle peut servir au risque
  (taille, stop, abstention) en **shadow** d'abord. Sinon rien n'est branché : la prévision reste un affichage, et
  F12 continue de la mesurer en direct.
- V3 `CONFIRME` : la moyenne remplace le service à 3 jours (étape séparée). H24 `CONFIRME` : la prévision horaire
  à 24 h peut servir aux niveaux. Sinon on garde le service.
- `CONTREDIT` sur une comparaison en service, **même seulement descriptif sous `NON_TESTE`** (par prudence) : la
  prévision correspondante est retirée de l'affichage.
- **Portée** : la confirmation porte sur la **spécification** des modèles (variables, ajustement, réajustement)
  telle que la recherche l'a sélectionnée. Le service l'instancie autrement (magasin courant depuis 2021, univers
  du service) : avant tout branchement sur la taille ou les stops, le service est aligné ou son écart mesuré.
- Rien d'autre : aucun seuil n'est choisi sur ce résultat.

**Ce que la lecture coûte** : la période finale cesse d'être vierge pour tout le programme (compteur global des
consultations). Une future stratégie directionnelle ne pourra plus s'y confirmer ; il lui faudra une observation
en direct. La consultation est enregistrée **avant** la lecture des données : un plantage ne donne pas droit à un
second regard ; d'où la répétition préalable, même code, sur avril 2024 → juin 2025 (DEVELOPMENT, rien compté).

## 19. Résultat de la confirmation (`VOLC-20261003T101841Z-4d3ffb`, lecture unique du 2026-10-03)

Période finale lue **une fois** (consultation n° 1 du programme, enregistrée avant la lecture), 40 paires, origines
du 2025-07-01 au 2026-09-30. Répétition préalable `VOLR-20261003T101508Z-580c91` : prévisions identiques au bit à
celles des protocoles d'origine sur toutes les lignes communes. 5 essais comptés sur FINAL_TEST.

| Ordre | Comparaison | Horizon | Écart de QLIKE | IC à 95 % | 2025 / 2026 | Paires mieux | Erreur de log | Issue propre | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | H24 : HAR + profil contre « 24 dernières heures » | 24 h | −0,200 | [−0,259 ; −0,142] | −0,198 / −0,202 | 100 % | mieux | CONFIRME | **CONFIRME** |
| 2 | D7 : HAR + BTC contre M0 | 7 j | −0,119 | [−0,245 ; +0,007] | −0,115 / −0,122 | 98 % | un peu pire | NON_CONCLUANT | **NON_CONCLUANT** |
| 3 | D3 : LightGBM contre M0 | 3 j | −0,078 | [−0,158 ; +0,001] | −0,101 / −0,063 | 100 % | mieux | NON_CONCLUANT | NON_TESTE |
| 4 | D1 : LightGBM contre M0 | 1 j | −0,081 | [−0,168 ; +0,006] | −0,134 / −0,045 | 100 % | mieux | NON_CONCLUANT | NON_TESTE |
| 5 | V3 : moyenne contre le service | 3 j | −0,009 | [−0,018 ; −0,0003] | −0,000 / −0,016 | 95 % | un peu pire | NON_CONCLUANT | NON_TESTE |

**Lecture déclarée** :
- **H24 confirmée** hors échantillon : la prévision horaire à 24 h (HAR + profil heure × jour) fait 37 % de perte
  QLIKE de moins que la règle « 24 dernières heures », chaque année, sur toutes les paires. Elle peut servir aux
  **niveaux** (stop, abstention), en shadow d'abord (§ 18).
- **D7 non conclusive**, la séquence s'arrête : les prévisions journalières en service **ne sont pas confirmées**
  au niveau déclaré. Rien n'est branché à 1, 3 ou 7 jours ; elles restent un affichage et F12 continue de les
  mesurer en direct.
- **Descriptif seulement** (non testé, ne décide rien) : D3, D1 et V3 vont dans le même sens (écart négatif les deux
  années, 95 à 100 % des paires), avec des intervalles qui touchent 0. Rien n'est contredit : aucune prévision
  n'est retirée.
- La période finale n'est plus vierge pour le programme : une stratégie future devra se confirmer en direct.

## 20. Protocole v5 — volatilité réalisée fine, HAR-RV sur bougies de 1 minute (déclaré le 2026-10-03, avant exécution)

Mission du 2026-10-03, phase 10.1 ; choix du propriétaire : la minute plutôt que la seconde. Code :
`research/volatility_rv.py`. Données : magasin minute (`research/minute_history.py`, archives officielles vérifiées).
DEVELOPMENT seulement.

**Question.** La volatilité réalisée calculée à partir des bougies de 1 minute (au lieu des 24 rendements horaires
d'une journée) donne-t-elle de meilleures prévisions que le modèle en service ?

**Variables, connues à l'origine** (00:00 UTC du jour `T`, comme au § 12) : pour chaque journée UTC `D` complète
(au moins 95 % de ses 1 440 minutes) :
- `RV5_D` : somme des carrés des rendements logarithmiques de 5 minutes (clôtures toutes les 5 minutes) ;
- `RV1_D` : somme des carrés des rendements logarithmiques de 1 minute.
Pour `T`, avec `D = T − 1 jour` : `d = RV_D` ; `w` = moyenne des 7 dernières journées (au moins 6 complètes) ;
`m` = moyenne des 30 dernières (au moins 27 complètes). Seules les minutes ouvertes avant `T` sont lues.

**Candidats** (2 variantes déclarées) : `C5_HAR_RV5` et `C1_HAR_RV1`, régression **groupée** (toutes les paires) de
`log RV²_H` (la cible du § 12 : somme des carrés des rendements horaires des H jours suivants) sur
`log d`, `log w`, `log m`, avec retour à la variance par Duan (`volatility.fit_har`), réajustée le 1er de chaque mois
sur le passé purgé (cible terminée au plus tard au réajustement), comme au § 12.

**Référence** : le modèle en service (`REF_SERVICE` : LightGBM groupé à 1 et 3 jours, HAR + BTC à 7 jours), prévisions
hors échantillon du run v2 `VOL-20261002T170500Z-c3bda6`, sur les mêmes origines (échantillon commun : origines où la
référence et les deux candidats ont une prévision).

**Comparaisons** : 2 candidats × 3 horizons (1, 3, 7 jours) = **6**, comptées au registre. Règle et niveau : ceux du
protocole v4 (§ 17, `volatility_v2._row` : IC de la différence de QLIKE au niveau 1 − 0,05/12, plus strict qu'une
correction sur 6, années couvertes moins une, 70 % des paires, erreur de log RV plus faible). Verdict
`MIEUX_QUE_SERVICE` si un candidat est utile à un horizon, sinon `AUCUNE_AMELIORATION`.

**Ce que le résultat décide** : rien pour les tests en cours (figés) ; un candidat utile servirait à la période
suivante, après confirmation en direct (la période finale a déjà été lue une fois).

**Précisions avant exécution (relecture du 2026-10-03, aucune perte calculée)** :
- **entraînement** : toutes les journées passées et purgées où les minutes existent (depuis 2018 selon la paire), pas
  seulement les lignes du service (qui commencent en 2019, 400 jours après la cotation) : sinon janvier 2019 n'avait
  aucun modèle et le premier semestre 2019 quelques centaines de lignes, alors que la référence a tout le passé. La
  cible d'entraînement est recalculée par la fonction du service (`volatility.daily_frame`, `rv2_H`) ; l'exécution est
  refusée (rien compté) si elle diffère au bit près de `realized` sur une ligne du service. L'évaluation porte
  seulement sur les lignes du service ;
- exécution refusée (rien compté) si les variables minute couvrent moins de 99 % des lignes du service d'une paire
  (relevé avant exécution : 99,94 à 100 %) ;
- empreintes inscrites (prévisions du run source, minutes et bougies 1 h coupées) ; registre écrit avant le rapport ;
  `--allow-dirty` enregistre et compte l'essai ;
- à lire avec le résultat : RV1 contient du bruit de microstructure propre à chaque paire, qu'une constante commune
  n'absorbe pas (c'est l'hypothèse testée) ; la référence a été choisie sur DEVELOPMENT (avantage pour elle) ;
- étiquette corrigée : l'IC de `volatility_v2._row` est au niveau 1 − 0,05/12 = 99,58 % ; l'affichage « 99,17 % » du
  § 17 était faux (le verdict v4 ne change pas).

**Résultat (exécution unique, `VOL-20261003T164450Z-8ae122`, 2026-10-03, essais 793 à 798) : `AUCUNE_AMELIORATION`.**
38 paires, 2 201 à 2 320 jours d'origines par horizon.

| Candidat | Horizon | QLIKE | Service | Écart | IC (99,58 %) | Années mieux | Paires mieux |
|---|---|---|---|---|---|---|---|
| C5_HAR_RV5 | 1 j | 0,388 | 0,424 | −0,036 | [−0,074 ; +0,002] | 4 | 87 % |
| C1_HAR_RV1 | 1 j | 0,395 | 0,424 | −0,029 | [−0,066 ; +0,008] | 4 | 68 % |
| C5_HAR_RV5 | 3 j | 0,318 | 0,345 | −0,027 | [−0,060 ; +0,006] | 7 | 97 % |
| C1_HAR_RV1 | 3 j | 0,321 | 0,345 | −0,024 | [−0,054 ; +0,007] | 5 | 82 % |
| C5_HAR_RV5 | 7 j | 0,279 | 0,285 | −0,006 | [−0,015 ; +0,004] | 6 | 84 % |
| C1_HAR_RV1 | 7 j | 0,282 | 0,285 | −0,003 | [−0,017 ; +0,012] | 4 | 74 % |

Lecture : toutes les estimations sont en faveur des minutes (QLIKE plus bas), surtout avec les rendements de
5 minutes, mais aucun intervalle au niveau déclaré n'est entièrement sous 0 : rien n'est démontré, rien n'est branché.
C5 à 3 jours (7 années sur 7, 97 % des paires, borne haute +0,006) est la piste la plus nette ; la confirmer
demanderait une nouvelle déclaration et un suivi en direct (la période finale est déjà lue).

## Historique

- 2026-10-01, v1 : protocole déclaré avant toute exécution.
- 2026-10-02, protocole v2 (§ 14, module `research/volatility_v2.py`, le v1 reste en service) : déclaré avant toute
  exécution ; aucun résultat v2 calculé ni regardé.
- 2026-10-02 (17:05 UTC), protocole v2 exécuté : `VOL-20261002T170500Z-c3bda6`, 12 comparaisons (programme 749), MIEUX_QUE_SERVICE
  à 3 jours par la combinaison V1 ; § 15 ajouté ; rien n'est branché.
- 2026-10-02, protocole v3 (§ 16, volatilité à toute heure) : déclaré avant exécution puis exécuté le soir même,
  `VOL-20261002T211837Z-be55c0`, 4 comparaisons (programme 753), PREVISION_UTILE à 24 h (HAR + profil) ; résultats
  inscrits au § 16 ; rien n'est branché.
- 2026-10-01, v1 complétée avant toute exécution, après la relecture indépendante : grille horaire et
  variances passées tolérantes à 5 % d'heures manquantes (avant : une heure de maintenance retirait
  30 jours d'origines, soit environ 21 % des jours de 2019 à 2025) ; exécution refusée sur du code non
  commité (commit du code exécuté enregistré) ; refus si un horizon a moins de 20 blocs de jours ;
  nombre de fils enregistré ; test de la jointure BTC vers le passé ; commande `csi volatility` et
  verdict au tableau de bord ; paires renommées déclarées.
- 2026-10-03, protocole v4 (§ 17) : déclaré puis exécuté (`VOL-20261003T005910Z-a26dfe`, 6 comparaisons, programme 765), AUCUNE_AMELIORATION.
- 2026-10-03, protocole v5 (§ 20) : corrigé avant exécution après relecture, puis exécuté une fois
  (`VOL-20261003T164450Z-8ae122`, programme 798) : `AUCUNE_AMELIORATION`, estimations favorables aux minutes non démontrées.
