# Intervalles de rendement et abstention — protocole v1, déclaré le 2026-10-02 avant toute exécution

Piste 3 de l'étude du 2026-10-02. Code : `research/quantiles.py`. Tests : `tests/test_quantiles.py`. Commande :
`csi quantiles`. Registre : expériences de type `QUANTILES`, stratégie `RETURN_QUANTILES`.

**Pourquoi.** Aucun essai de prévision de la direction n'a démontré d'avantage (753 essais au programme à cette
date). L'ampleur se prévoit (lot 7, v2). L'étape suivante naturelle n'est pas un sens, mais des **intervalles** : si
l'on sait dire « dans 3 jours, le rendement sera entre −6 % et +5 % avec 90 % de chances », on peut dimensionner une
position et, surtout, **s'abstenir** quand l'intervalle est trop large pour les coûts. Un intervalle n'est pas un
signal : rien ici ne dit le sens ni la rentabilité.

**Question.** À 00:00 UTC, les quantiles **5, 25, 75 et 95 %** du rendement log des **1, 3 et 7** prochains jours
sont-ils mieux prévus par un modèle que par la règle Q0 « σ̂ en service × quantile empirique passé du rendement
standardisé » ?

## 1. Données et lignes

- Cadre du lot 7 (`docs/VOLATILITY.md` § 1 et 3) : bougies 1 h du magasin long, 40 paires, DEVELOPMENT seulement,
  données complètes exigées, lignes journalières à 00:00 UTC avec les sept variables du lot 7 (log-variances jour,
  semaine, mois de la paire et de BTC, jour de semaine), lignes complètes seulement.
- **Rendement réalisé** r_H = ln(clôture de 23:00 située H jours après l'origine / clôture à l'origine) ; manquant
  si l'une des deux bougies manque.
- **σ̂ en service** : racine de la variance prévue par `REF_SERVICE` dans les prévisions hors échantillon du
  protocole v2 (`reports/VOL-20261002T170500Z-c3bda6/forecasts.parquet` : LightGBM à 1 et 3 jours, HAR + BTC à
  7 jours, réajustés chaque mois, échantillon commun à partir du 2019-01-01, 38 paires). Les lignes évaluées sont
  celles où σ̂ existe ; l'empreinte de la table lue est enregistrée.
- Variables des modèles : les sept du lot 7 + `log_sigma` = ln σ̂. Rendement standardisé z = r_H / σ̂.

## 2. Modèles (aucun réglage nouveau)

| Modèle | Définition |
|---|---|
| **Q0_SERVICE_EMPIRIQUE** (référence) | q_α = σ̂ × ẑ_α, ẑ_α = quantile empirique α de z sur l'entraînement purgé (toutes paires ensemble), réajusté le 1er de chaque mois |
| **C1_LGBM_QUANTILE** | LightGBM commun à perte pinball, un modèle par quantile (objectif `quantile`, 300 arbres, 15 feuilles, apprentissage 0,05, 200 lignes par feuille, variables ci-dessus), cible r_H |
| **C2_CONFORME_ADAPTATIF** | Q0 dont le niveau de chaque quantile est ajusté en ligne, par paire : α_eff ← α_eff + γ (α − 1{z_s ≤ seuil_s}) pour chaque origine s dont le rendement vient d'être connu (s + H jours ≤ t), γ = 0,005, α_eff borné à [0,001 ; 0,999] ; q = σ̂ × ẑ(α_eff) avec la fonction quantile ẑ de l'entraînement du mois |

- **Réajustement** le 1er de chaque mois, fenêtre croissante, **purge** : une ligne d'entraînement n'est gardée que
  si son rendement est connu à la date de réajustement (origine + H jours ≤ date). 100 lignes au moins.
- Le conforme adaptatif fixe les niveaux de l'origine t **avant** de lire r_t ; l'origine t n'entre dans les mises
  à jour qu'une fois résolue. État et file d'attente se transmettent d'un mois à l'autre.
- Première origine évaluée : 2019-01-01 au plus tôt, en pratique le premier mois où l'entraînement purgé compte
  100 lignes (lu après coup : 2019-02-01 à 1 jour, 2019-03-01 à 3 et 7 jours) ; dernière : fin de DEVELOPMENT moins
  H jours. Échantillon commun : origines où les trois modèles prévoient les quatre quantiles.

## 3. Mesures

- **Perte principale** : pinball totale = Σ_α max(α (r − q_α), (α − 1)(r − q_α)) sur les quatre quantiles.
  Différence avec Q0 moyennée par jour d'origine sur les paires (une valeur par jour), IC de Student par blocs
  calendaires de max(10, 2·H) jours, 20 blocs au moins, niveau de Bonferroni 1 − 0,05/6 ≈ 99,17 %.
- **Couverture** : part des jours (moyenne des paires) où r tombe dans [q₅, q₉₅] (cible 90 %) et dans [q₂₅, q₇₅]
  (cible 50 %), par année civile. Celles de Q0 sont rapportées aussi (`baseline_rows`).
- Également : part des paires où le modèle fait mieux, nombre de prévisions, de jours, de paires.

## 4. Règle

Un couple (modèle, horizon) est **« INTERVALLES_UTILES »** si TOUT est vrai :
1. borne haute de l'IC de la différence de pinball < 0 ;
2. couverture 90 % dans ± 3 points de 90 % dans **au moins 6 années sur 7** ;
3. couverture 50 % dans ± 3 points de 50 % dans au moins 6 années sur 7 ;
4. pinball plus faible que Q0 pour au moins 70 % des paires.

Par horizon, le modèle retenu est celui de pinball la plus basse parmi les utiles, sinon `AUCUNE_AMELIORATION` ;
verdict global `INTERVALLES_UTILES` si un horizon au moins a un modèle utile.

## 5. Essais

2 candidats × 3 horizons = **6 comparaisons**, comptées au programme (`n_trials = 6`). Enregistrement :
`reports/<run_id>/summary.json` et `quantiles_<H>d.parquet` (rendements et 12 quantiles prévus), empreintes des
séries et de la table σ̂, commit, versions.

## 6. Audit des fuites, avant tout résultat

- **Cibles** : sur BTC, ETH et SOL (à défaut les trois premières paires), à 3 origines tirées par paire, le
  rendement à terme recalculé sur les bougies tronquées à l'origine est inconnu ; tronquées juste avant la clôture
  finale, il reste inconnu ; dès qu'elle existe, il est égal au rendement calculé à la main.
- **Conforme adaptatif** : pour une paire et chaque horizon, les niveaux à l'origine t ne changent pas quand les
  rendements standardisés des origines non encore résolues (s + H > t) sont falsifiés ; la même vérification doit
  échouer avec la mutation (lecture d'une origine non résolue, lag 0).
- Première version de l'audit (exécution du 2026-10-02) : coupe à l'origine et comparaison complet / tronqué des
  niveaux, mutation détectée par simple différence ; la relecture indépendante l'a jugée trop faible (vraie par
  construction) et elle a été renforcée comme ci-dessus APRÈS l'exécution, sans effet sur les résultats.
- Les variables du lot 7 et σ̂ ont leur propre audit (lot 7 § 9, v2 § 14), non répété.

## 7. Attendu et lecture déclarée

- Q0 est déjà une bonne règle (σ̂ est un modèle validé sur DEVELOPMENT) ; le gain attendu de C1 est faible, et
  surtout sur les queues (5 et 95 %) par l'asymétrie ; C2 devrait surtout corriger la **couverture** année par
  année (régimes), pas la pinball moyenne.
- `AUCUNE_AMELIORATION` laisse tout en l'état. `INTERVALLES_UTILES` ne branche rien : le gagnant est le meilleur de
  deux sur des données déjà vues (6 comparaisons de plus) ; avant tout usage (abstention, dimensionnement), une
  confirmation sur des données jamais consultées.

## 8. Limites déclarées

- σ̂ vient d'un protocole déjà sélectionné sur DEVELOPMENT (v2 : REF_SERVICE y est le modèle en service, non le
  gagnant de v2) ; les lignes sont celles de son échantillon.
- Rendements qui se chevauchent à 3 et 7 jours ; survivantes ; une valeur par jour quel que soit le nombre de paires.
- Le conforme adaptatif met à jour avec un retard égal à l'horizon ; γ = 0,005 est un choix a priori (environ 200
  observations pour déplacer un niveau d'un point), non optimisé.
- Les quantiles de C1 ne sont pas contraints à être croissants (α < β ⇒ q_α ≤ q_β), et ceux de C2 non plus (niveaux
  ajustés indépendamment) : un croisement est compté tel quel dans la pinball et fait sortir le rendement de
  l'intervalle dans la couverture (il coûte, il n'aide jamais).
- Le seuil de 100 lignes vaut pour un modèle commun : au premier mois (107 lignes à 1 jour), LightGBM ne peut faire
  aucune coupure (200 lignes par feuille) et prédit le quantile inconditionnel ; cela pénalise C1 au début.
- L'échantillon σ̂ de v2 exige une cible de variance contiguë (fenêtre future sans heure manquante) : quelques
  journées de panne sont exclues, pour les trois modèles également.

## 9. Résultats (`QTL-20261002T212535Z-0626e5`, exécuté le 2026-10-02, 6 comparaisons, programme 759)

Code du commit `ef4cd1c` (arbre propre ; relecture indépendante pendant le calcul : aucun défaut faussant la mesure,
audit interne renforcé ensuite), audit des fuites réussi (ADA, ALGO, APT, 3 origines chacune, mutation détectée),
38 paires, 2 142 à 2 289 jours, σ̂ de `VOL-20261002T170500Z-c3bda6` (171 901 lignes, empreinte enregistrée).
Période finale non consultée.

**Verdict : `AUCUNE_AMELIORATION`** aux trois horizons.

| Modèle | Horizon | Pinball | Q0 | Écart [IC 99,17 %] | Années à 90 % | Années à 50 % | Paires mieux | Utile |
|---|---|---|---|---|---|---|---|---|
| Q0 (référence) | 1 j | 0,0421 | — | — | 7/7 | 5/7 | — | — |
| C1 LightGBM quantile | 1 j | 0,0443 | 0,0421 | +0,0021 [+0,0013 ; +0,0030] | 0/7 (≈ 82 %) | 0/7 (≈ 42 %) | 0 % | non |
| C2 conforme adaptatif | 1 j | 0,0423 | 0,0421 | +0,0001 [+0,0000 ; +0,0002] | 7/7 | 7/7 | 13 % | non |
| Q0 (référence) | 3 j | 0,0733 | — | — | 7/7 | 6/7 | — | — |
| C1 LightGBM quantile | 3 j | 0,0793 | 0,0733 | +0,0060 [+0,0038 ; +0,0082] | 0/7 (≈ 80 %) | 0/7 (≈ 41 %) | 0 % | non |
| C2 conforme adaptatif | 3 j | 0,0741 | 0,0733 | +0,0008 [+0,0001 ; +0,0014] | 7/7 | 6/7 | 3 % | non |
| Q0 (référence) | 7 j | 0,1149 | — | — | 7/7 | 6/7 | — | — |
| C1 LightGBM quantile | 7 j | 0,1274 | 0,1149 | +0,0126 [+0,0069 ; +0,0183] | 0/7 (≈ 77 %) | 0/7 (≈ 40 %) | 3 % | non |
| C2 conforme adaptatif | 7 j | 0,1178 | 0,1149 | +0,0029 [+0,0010 ; +0,0048] | 7/7 | 5/7 | 5 % | non |

Tableau complet et quantiles prévus : `reports/QTL-20261002T212535Z-0626e5/` (`summary.json`, `quantiles_<H>d.parquet`).

Lecture :
- **La règle simple Q0 est la meilleure des trois** : avec la volatilité en service et les quantiles empiriques du
  rendement standardisé, l'intervalle à 90 % couvre 88 à 92 % des jours **chaque année** de 2019 à 2025, aux trois
  horizons ; celui à 50 % couvre 46 à 53 % (dans ± 3 points 5 à 6 années sur 7). C'est un résultat utile en soi :
  les intervalles de Q0 sont **honnêtes**, et ils ne demandent aucun modèle de plus.
- **LightGBM quantile (C1) est nettement pire partout** : pinball plus élevée (+5 à +11 %, intervalles entièrement
  au-dessus de zéro, 0 % des paires), intervalles trop étroits (≈ 80 % de couverture pour 90 % visé, ≈ 41 % pour
  50 %). Entraîné sur une fenêtre croissante, il sous-estime les queues ; les variables n'ajoutent rien à σ̂.
- **Le conforme adaptatif (C2) fait ce qu'il promet, et c'est tout** : il ramène la couverture à 50 % dans la
  tolérance toutes les années à 1 jour (7/7 contre 5/7 pour Q0), au prix d'une pinball très légèrement plus élevée
  (+0,3 % à +2,6 %, intervalles au-dessus de zéro) : la règle le refuse, comme déclaré. Il pourrait servir comme
  **garde-fou de calibration** (corriger une dérive de couverture année par année), pas comme un meilleur
  prédicteur.
- **Ce que cela permet** : des intervalles de rendement à 1, 3 et 7 jours à couverture tenue, calculables depuis
  les prévisions en service et un tableau de quantiles de z ; **rien n'est branché** (abstention et
  dimensionnement sont une étape séparée, à décider par le propriétaire, et les couvertures absolues restent
  optimistes du fait de la sélection de σ̂ sur DEVELOPMENT). Rien sur la direction ni la rentabilité.

## Historique

- 2026-10-02 : protocole déclaré avant toute exécution.
- 2026-10-02 (21:25 UTC) : exécuté, `QTL-20261002T212535Z-0626e5`, 6 comparaisons (programme 759), `AUCUNE_AMELIORATION` ;
  § 9 ajouté ; corrections de la relecture inscrites (§ 2, § 6, § 8) et audit interne renforcé après coup.
