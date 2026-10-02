# Intervalles de rendement et abstention — protocole v1, déclaré le 2026-10-02 avant toute exécution

Piste 3 de l'étude du 2026-10-02. Code : `research/quantiles.py`. Tests : `tests/test_quantiles.py`. Commande :
`csi quantiles`. Registre : expériences de type `QUANTILES`, stratégie `RETURN_QUANTILES`.

**Pourquoi.** Aucun essai de prévision de la direction n'a démontré d'avantage (749 essais au programme à cette
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
- Première origine évaluée : 2019-01-01 ; dernière : fin de DEVELOPMENT moins H jours. Échantillon commun : origines
  où les trois modèles prévoient les quatre quantiles.

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

- **Cibles** : à 3 origines tirées par paire (3 paires), le rendement à terme recalculé sur les bougies tronquées à
  l'origine est inconnu (le futur n'est pas lu) et le calcul complet est reproductible.
- **Conforme adaptatif** : pour une paire et chaque horizon, les niveaux à l'origine t calculés sur les seules
  origines ≤ t sont identiques aux niveaux complets ; la mutation (mise à jour avec une origine non encore
  résolue) doit changer des niveaux.
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
- Les quantiles de C1 ne sont pas contraints à être croissants (α < β ⇒ q_α ≤ q_β) : un croisement est compté
  tel quel dans la pinball et dans la couverture.

## Historique

- 2026-10-02 : protocole déclaré avant toute exécution.
