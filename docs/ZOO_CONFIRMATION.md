# Confirmation des deux meilleures du zoo : protocole pré-inscrit (déclaré le 2026-10-11, avant tout code et toute exécution)

Le zoo (`ZOO.md` § 16) est clos sans candidate. Ses deux meilleurs tests de la découverte 2019-2022, **TR37_1j** et
**XS17_1j**, sont mesurés **une fois** sur une période qu'ils n'ont jamais vue, avec **la même mesure**, pour savoir si
leur avance est du hasard. Décision du propriétaire du 2026-10-11 (« de 1 à 4 », la 4 étant la rotation
hebdomadaire). Rien ici n'annonce une rentabilité.

## 0. En bref, pour le propriétaire

- **Quoi.** Deux stratégies, déjà codées et contrôlées par le zoo :
  - **TR37** (« rotation hebdomadaire ») : chaque lundi, chaque crypto du top 40 est gardée si elle a monté sur les
    7 derniers jours ; sinon sa part reste en USDT ;
  - **XS17** (« proches du plus haut ») : chaque lundi, on garde le cinquième du top 40 le plus proche de son plus haut
    historique (cryptos cotées depuis au moins un an).
- **Pourquoi elles.** Ce sont les deux seules du zoo avec t > 2 en 2019-2022, chaque année positive. Mais parmi 166
  tests, deux t > 2 sont attendus par le hasard seul : il faut une **autre période** pour trancher.
- **Comment.** Même code, mêmes frais, même comparaison qu'au zoo, sur **2023-01-01 → 2025-06-30**, une seule fois.
- **Honnêtement.** La puissance est faible (≈ 20 à 25 %, § 4) : « non confirmée » ne prouvera pas qu'il n'y a rien,
  mais « confirmée » serait un vrai signal.
- **Ensuite.** Seulement si une stratégie est `VALIDÉE` : test en direct sans argent (§ 6).

## 1. Ce qui a déjà été mesuré (rappel, pour situer)

Les autres idées du 2026-10-11 sont **déjà mesurées** et ne sont pas refaites ici :

| Idée | Où | Résultat |
|---|---|---|
| Garder avec mise selon la volatilité (TR43, RG08) | `FACTORS.md` H5, `TREND_DAILY.md` ENSEMBLE_VOL, `LONG_HORIZON.md` ; F5 en direct | moins de perte, en proportion de l'exposition ; aucun gain par unité d'exposition démontré |
| Rééquilibrage (PF05, PF06, PF07, PF09) | zoo | t = −1,0 ; +1,2 ; +1,5 ; +0,9 : aucun avantage démontré |
| DCA et grilles (PF01 à PF04) | `GRID_DCA.md` | ne font pas mieux que garder ; DCA avec prise de gain démontrablement moins bien |
| Achat dans la peur extrême (SE01), sortie dans l'avidité (SE02) | zoo | t = +0,4 ; −1,6 |

## 2. Tests, période, mesure

- **Tests** : `TR37_1j` et `XS17_1j` exactement tels que codés et relus au commit `fb02f9a` (`research/zoo/spec.py`,
  paramètres publiés : 7 jours ; quintile haut, 365 jours de cotation). **Aucune autre variante**, aucun paramètre
  changé.
- **Période** : l'étape `VALIDATION` du zoo, du 2023-01-01 00:00 au 2025-06-30 23:59:59 UTC (DEVELOPMENT). La période
  réservée n'est pas lue.
- **Mesure** : `ZOO.md` § 4.2 (forme b) sans aucun changement : rééquilibrage le lundi, frais taker central et
  défavorable, statistique = **excès net hebdomadaire** sur la référence (TR37 : exposition égale `f̄ × top 40 +
  (1 − f̄) × USDT` ; XS17 : membres éligibles à poids égaux), intervalle par blocs de 28 jours.
- **Code** : la même fonction de mesure et la même fonction de validation que le zoo (`run.mesurer_tout_detail`,
  `decide.valider`) ; le seul code nouveau est la garde d'exécution et l'écriture du rapport.

## 3. Décision

`m = 2`. Chaque test est **`VALIDÉE`** si les quatre conditions du `ZOO.md` § 5.2 tiennent :

1. intervalle bilatéral au niveau **1 − 0,05 / 2 = 97,5 %** de l'excès entièrement > 0, en central **et** en
   défavorable ;
2. idem **sans sa meilleure année** (2023, 2024, 2025-S1), 12 blocs au moins ;
3. au moins **2 années sur 3** avec un excès central > 0 ;
4. XS17 : **aucune paire ne fait plus de 60 %** du gain net central (sans objet pour TR37, règle de marché).

Sinon `NON_VALIDÉE` (ou `INSUFFISANT` sous 32 blocs). Aucune autre lecture ne décide.

**Sélection déclarée** : les deux tests sont choisis **après** lecture de la découverte (les deux meilleurs t). C'est
pourquoi seule une période qu'ils n'ont pas vue peut décider, et pourquoi l'estimation de la découverte est surestimée
(malédiction du gagnant).

**Limite déclarée** : 2023 → 2025-06 a déjà été parcourue par le programme (1 039 essais), mais jamais avec ces deux
règles (`ZOO_CATALOGUE.md` : aucune mesure antérieure de TR37 ni de XS17).

## 4. Puissance attendue (hypothèses déclarées, aucune donnée lue)

Erreur type hebdomadaire de la découverte (53 blocs) : TR37 ≈ 0,25 %, XS17 ≈ 0,34 %. En validation (32 blocs) :
≈ 0,32 % et ≈ 0,43 %. Seuil bilatéral à 97,5 % avec 31 degrés de liberté : t ≈ 2,36, soit un excès estimé d'au moins
≈ 0,76 %/semaine (TR37) et ≈ 1,0 %/semaine (XS17). Si l'effet vrai était celui de la découverte (0,56 % et 0,69 %,
**optimiste** à cause de la sélection), la puissance de la condition 1 serait ≈ 27 % et ≈ 22 %, moins avec les
conditions 2 à 4. **Déclaré : test peu puissant.** Il ne voit qu'un avantage fort et régulier.

## 5. Garde d'exécution et comptage

- Contrôle sous H0 : celui du zoo (`ZOO-H0-50090007fe2e`, `RETENU`) ; TR37_1j et XS17_1j y sont **retenus** (ni
  aveugles, ni mal calibrés). Aucun nouveau contrôle : la mesure et le code de mesure sont identiques.
- Exécution refusée sans : code commité, relecture `leak-auditor` inscrite (`zoo_review.CONFIRMATION_REVIEW`) et code
  identique depuis, configuration identique à celle du zoo (`CONFIG_FINGERPRINT`), découverte du zoo enregistrée où
  les deux tests ont bien le statut `NON_CANDIDATE` et un t central > 2 (la sélection déclarée), et si la confirmation
  a déjà été exécutée (**exécution unique**).
- **Comptage** : **2 essais** au registre DEVELOPMENT (programme : 1 039 + 2 = 1 041), stratégie `ZOO_CONFIRMATION`.

## 6. Après

- **`VALIDÉE`** : pré-inscription puis démarrage d'un test en direct sans argent (`F31_ROTATION` ou `F32_PLUS_HAUT`),
  rééquilibrage chaque lundi, mêmes frais, même référence ; la lecture de la période réservée reste une décision du
  propriétaire. Une `VALIDÉE` n'est pas « rentable » : elle a battu sa référence, net de frais, sur une période déjà
  vue par le programme.
- **`NON_VALIDÉE`** : la rotation et le facteur « plus haut » rejoignent les autres résultats nuls ; aucun test en
  direct (avec un rééquilibrage par semaine, un an de direct ne verrait rien de plus petit que ce test).

## 7. Résultats

(à remplir après l'exécution unique)

## Historique

- 2026-10-11 : protocole déclaré avant tout code et toute exécution (branche `recherche/zoo-confirmation`).
