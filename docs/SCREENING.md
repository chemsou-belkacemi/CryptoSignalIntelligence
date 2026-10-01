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
- « Passe » = rendement brut moyen > seuil de coûts aller-retour (0,26 % en coûts centraux) ET borne
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

## Historique

- `SCREEN-20260930T093324Z-20101b` : premier passage, **intervalles faux** (moyenne pondérée par jour
  alors que la moyenne affichée l'est par événement). Conservé dans le registre, à ne pas utiliser.
  Corrigé et testé (`test_confidence_interval_brackets_the_reported_event_weighted_mean`).
