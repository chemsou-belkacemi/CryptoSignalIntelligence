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

## Historique

- 2026-10-01, v1 : protocole déclaré avant toute exécution.
- 2026-10-01, v1 complétée avant toute exécution, après la relecture indépendante : grille horaire et
  variances passées tolérantes à 5 % d'heures manquantes (avant : une heure de maintenance retirait
  30 jours d'origines, soit environ 21 % des jours de 2019 à 2025) ; exécution refusée sur du code non
  commité (commit du code exécuté enregistré) ; refus si un horizon a moins de 20 blocs de jours ;
  nombre de fils enregistré ; test de la jointure BTC vers le passé ; commande `csi volatility` et
  verdict au tableau de bord ; paires renommées déclarées.
