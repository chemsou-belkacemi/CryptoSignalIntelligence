# Zoo des stratégies : protocole pré-inscrit (déclaré le 2026-10-10, révisé le 2026-10-11, avant tout code, tout comptage et toute exécution)

Ce protocole mesure, avec **une seule méthode fixée ici**, les 130 stratégies `À_TESTER (zoo)` du recensement
[ZOO_CATALOGUE.md](ZOO_CATALOGUE.md), soit **239 tests** (une stratégie × une unité de temps ; 257 avant les décisions de
mise en œuvre du 2026-10-11, voir l'historique). Il est écrit avant tout
code : un autre chantier codera le moteur après relecture. **Aucun calcul n'a été fait pour l'écrire** ; les ordres de
grandeur du § 11 viennent d'hypothèses déclarées et de documents déjà publiés, jamais de données lues pour l'occasion.

Rien ici n'annonce une rentabilité. Le résultat le plus probable, au vu des 873 essais déjà faits et de la puissance
du § 11, est **zéro stratégie validée** ; ce résultat dira « aucune stratégie connue n'a un avantage de cette taille »,
jamais « aucune n'a d'avantage ».

## 0. En bref, pour le propriétaire

- **Quoi.** Les stratégies connues qui ne demandent que des bougies Binance ou des données déjà en magasin :
  croisements de moyennes, Donchian, Ichimoku, MACD, RSI, Bollinger, chandeliers japonais, Fibonacci, pivots, OBV,
  facteurs entre cryptos, rotation, liens avec le S&P 500, le Nasdaq, l'or, le dollar, les taux et le VIX, Fear & Greed,
  FOMC, CPI et NFP, cotations Binance, parité de risque…
- **Comment.** Une seule règle pour toutes : achat à la clôture **suivant** le signal, frais taker (central et
  défavorable), deux formes de mesure seulement. Univers : le **top 40 à date**, cryptos mortes comprises.
- **La hausse du marché ne compte pas comme un talent.** Une règle de transaction est jugée sur ce qu'elle gagne
  **au-delà de la dérive de la même paire** (ce qu'un achat au hasard de même durée aurait gagné) ; une règle qui sort
  parfois en USDT est comparée à un portefeuille **de même exposition moyenne**.
- **Deux étapes.** Découverte sur 2019-2022 (contrôle des fausses découvertes sur les 239 tests à la fois), puis
  validation sur 2023 → mi-2025 des seules candidates, avec garde-fous. La période réservée n'est pas lue.
- **Avant tout.** Le même code tourne une fois sur 200 marchés synthétiques où rien n'est prévisible, sans puis avec
  une hausse constante : une mesure qui y crie trop souvent « candidate » est retirée sans compter d'essai.
- **Ensuite.** Les validées : une lecture de la période réservée (consultation n° 5, sur décision du propriétaire),
  puis un test en direct unique, **F31_ZOO**.
- **Honnêtement.** Le zoo est un **filet à grosses mailles** : il ne voit qu'un avantage net d'environ 0,4 % par jour
  d'exposition au-delà de la dérive, ou 2 % par semaine sur la référence (§ 11). Les avantages publiés sont plus petits.

## 1. Périmètre

- **Dans le zoo** : les lignes `À_TESTER (zoo)` du recensement, aux unités qui y sont écrites ; liste exacte au § 12.
- **En attente de données ou en direct seulement** (14 lignes, § 13) : branchées par un autre chantier ; leur
  pré-inscription précisera leurs propres périodes **avant** tout téléchargement.
- **Hors moteur** (3 lignes, § 14) : LSTM, Transformer et DVOL ; un protocole propre est nécessaire.
- Rien d'autre n'est mesuré. Aucune variante n'est ajoutée après lecture d'un résultat ; une idée nouvelle est un
  nouveau protocole, compté à part.

## 2. Données et univers

- **Bougies 1 h** du magasin long (`<racine>/long_history`, archives officielles de Binance, 262 paires) : toutes les
  paires passées par le top 40 à date et les 40 paires de recherche. Bougies **4 h** et **journalières** agrégées de
  ces bougies 1 h, alignées sur 00:00 UTC, **complètes seulement** (4 et 24 bougies 1 h), comme le criblage K ; une
  bougie agrégée est **datée par l'`available_at` de sa dernière bougie 1 h**. Champs utilisés : ouverture, plus
  haut, plus bas, clôture, volume en devise de cotation, volume acheteur au marché, nombre de transactions,
  `available_at`.
- **Univers** : le **top 40 à date** de `research/pit_universe.py` (`UNIVERSE_PIT.md`) : au 1er de chaque mois, les 40
  paires USDT de plus forte médiane de volume sur les 30 jours précédents, paires retirées de la cote comprises,
  exclusions structurelles et haram du recensement appliquées ; les cryptos `NON_RELEVE` entrent dans cette mesure de
  recherche, jamais dans une liste de trading. Les 8 paires sans historique 1 h (AION, ANT, GAL, JST, LEVER, SC, SKL,
  SUN) sont absentes : déclaré. **Exception déclarée** : EV02 (annonces de cotation) porte sur toutes les paires USDT du
  recensement, bougies journalières de `data/pit/daily.parquet` (clôtures seulement).
- **Appartenance** : un signal n'ouvre une position que si la paire est membre du top 40 **du mois de la décision**.
  Une position ouverte va jusqu'à sa sortie même si la paire quitte le top 40. Une paire qui **entre** dans le top 40
  pendant qu'une règle d'état est vraie ouvre une position à la première décision, comme en début d'étape.
- **Historique de chauffe** : les indicateurs se calculent sur tout l'historique de la paire depuis sa cotation
  (depuis août 2017 pour les plus anciennes) ; un signal n'existe que si la fenêtre de l'indicateur est entièrement
  disponible. La chauffe ne lit que des prix antérieurs à la décision.
- **Paire retirée de la cote** pendant une position : vente à la dernière clôture connue (`COTATION_ARRETEE`), comme
  `LIGNES_DE_TENDANCE.md`. Limite déclarée : la toute fin d'une paire retirée, souvent la pire, est perdue.
- **Trous de données** : une bougie 4 h ou journalière incomplète n'existe pas ; aucune décision sur une bougie
  manquante ; une position est valorisée à sa dernière clôture connue ; **une sortie qui tombe dans un trou s'exécute à
  la première clôture disponible**.
- **Données externes** (magasin de contexte, `CONTEXTE.md`, « Données du zoo », fusionné sur `main` en `ce65673`),
  toujours jointes « au plus tard » sur leur **`available_at`** :

| Série | Tests | `available_at` |
|---|---|---|
| SP500, NASDAQ100 (FRED, `zoo_marches`) | AM01, AM02, AM06 | clôture 16:00 New York + 2 h |
| GLD (`zoo_marches`) | AM03 | 16:00 New York + 2 h |
| DGS10 (FRED H.15) | AM04 | lendemain 16:15 New York + 2 h (plus tardif que `d` + 1 12:00 UTC : retenu) |
| VIXCLS (FRED) | AM08 | 16:15 New York + 2 h |
| DXY_BCE (`zoo_dollar_bce`) | AM07 | 16:00 Francfort + 1 h |
| Fear & Greed (`zoo_fear_greed`, depuis 2018-02) | SE01, SE02 | jour `J` 00:00 UTC + 2 h ; on lit la dernière valeur dont l'`available_at` précède la décision. **Limite** : l'historique d'alternative.me est lu tel que publié aujourd'hui (aucune preuve qu'il n'a pas été recalculé) |
| FOMC, CPI, NFP (`evenements/calendrier_macro`) | CA09, CA10, CA11 | heures de New York converties avec `zoneinfo` ; **dates connues à l'avance** (calendriers publiés l'année précédente) : hypothèse déclarée ici, car l'`available_at` du magasin est posé après l'événement |
| Nouvelles et pleines lunes (`evenements/calendrier_crypto`) | CA13 | calculables à l'avance par construction |
| Annonces de cotation (`evenements/annonces_binance`, catégorie `COTATION_SPOT`) | EV02 | publication + 10 min |

- Les marchés traditionnels sont fermés le week-end : la clôture du vendredi est reprise le samedi et le dimanche
  (jointure « au plus tard », testée dans `tests/test_zoo.py`).
- **Aucune lecture après le 2025-06-30 23:59:59 UTC** : le moteur coupe toutes les séries à
  `research/protocol.FROZEN_DEVELOPMENT_END` dès la lecture et refuse toute borne plus tardive (testé en falsifiant
  ce qui suit).

## 3. Les deux étapes

| Étape | Décisions | Rôle |
|---|---|---|
| **Découverte** | du 2019-01-01 00:00 au 2022-12-31 23:59 UTC | les 239 tests ; contrôle du taux de fausses découvertes |
| **Validation** | du 2023-01-01 00:00 au 2025-06-30 23:59 UTC | seulement les `CANDIDATE` |

- Une position encore ouverte à la fin d'une étape est **vendue à la dernière clôture de l'étape**, frais compris :
  aucun prix postérieur n'entre dans l'étape. **Un signal dont l'entrée tomberait après la fin de l'étape est écarté**
  (compté).
- La validation commence sans position. Une règle d'**état** (par exemple « clôture > SMA 200 ») ouvre une position à
  la première décision où l'état est vrai ; une règle d'**événement** (un croisement, une figure) attend un nouvel
  événement.
- **Les transactions de durée nulle** (entrée et sortie à la même clôture) sont écartées et comptées.
- Le code de découverte refuse toute date postérieure au 2022-12-31 ; la validation n'est calculée que pour les
  candidates, dans une exécution séparée.
- **Limite déclarée** : 2023 → 2025-06 fait partie de DEVELOPMENT et a déjà été parcourue par le programme (873
  essais). Les règles du zoo n'y ont jamais été réglées (paramètres publiés), mais la validation n'est pas vierge.

## 4. Mesure uniforme : deux formes seulement

Chaque test a **une seule forme**, fixée au tableau du § 12. Frais : modèle commun des tests en direct
(`forward/costs.py`), ordres **au marché (taker) des deux côtés** :

| Scénario | Frais par ordre | Écart et glissement par côté | Aller-retour (BTC, ETH / autres) |
|---|---|---|---|
| central | 7,5 pb | 2 pb (BTC, ETH), 5 pb (autres) | 19 pb / 25 pb |
| défavorable | 10 pb | 4 pb, 10 pb | 28 pb / 40 pb |

L'entrée à la clôture suivant le signal porte déjà le retard d'une bougie que `PROTOCOL.md` met dans son scénario
défavorable ; aucun retard n'est ajouté.

### 4.1 Forme (a) : règle de transaction

- **Signal** à la clôture de la bougie `t` de l'unité du test, calculé avec les seules données dont `available_at` est
  antérieur à la décision.
- **Entrée** au prix de **clôture de la bougie `t + 1`** (« clôture suivante »).
- **Sortie** : la règle de sortie de la stratégie, évaluée à chaque clôture `u`, exécutée à la clôture de `u + 1` ; ou,
  au plus tard, à la clôture de la `N`-ième bougie après l'entrée (`N` au § 12) ; ou à la fin de l'étape. Toutes les
  sorties se font à la clôture (aucun stop au toucher dans la bougie, donc aucune ambiguïté d'ordre des prix).
- **Une position par paire et par test** : un signal qui arrive pendant une position du même test sur la même paire
  est ignoré (compté).
- **Mise égale** par position, aucun levier, le reste en USDT sans rendement.
- **Rendements** : chaque position est valorisée à chaque clôture 1 h ; pour chaque position `i` et chaque journée UTC
  `d`, `e(i, d)` = fraction de la journée détenue et `r(i, d)` = rendement simple net de la position sur la journée,
  **rapporté à sa valeur au début de la journée** (au prix d'entrée le jour de l'entrée), multiplié par `e(i, d)` pour
  une journée partielle ; frais d'entrée et de sortie rapportés à la valeur de la position à ce moment. `Σ r / Σ e` est
  ainsi la moyenne des rendements quotidiens par jour d'exposition, dans la même unité que `m(p)`. Le rendement composé
  par transaction reste descriptif. **Test exigé** : sous une dérive constante sans bruit, `μ* = 0` à 1e-12 près pour
  une règle d'état tenue 300 jours (frais nuls).
- **Statistique de décision : excès net par jour d'exposition sur la dérive de la même paire** (convention D à K de
  `SCREENING.md` ; « achat − inconditionnel » de Brock, Lakonishok et LeBaron, 1992). Pour chaque paire `p` et chaque
  étape, `m(p)` = moyenne des rendements simples quotidiens **bruts** de `p` sur les journées UTC où `p` est membre du
  top 40 dans l'étape (moins de 90 journées de membre : moyenne de l'univers à poids égaux de l'étape) ;
  `μ* = Σ_{i,d} [r(i, d) − e(i, d) × m(p(i))] / Σ e`. `m(p)` est une mesure du résultat, calculée après coup sur
  l'étape, et n'entre dans aucun signal ; un achat au hasard de même durée, même paire et même étape a un excès
  d'espérance nulle (avant frais). **Pour EV02**, dont les paires ne sont pas membres, la référence n'est pas `m(p)` :
  c'est, **le même jour**, le rendement brut de l'univers à poids égaux des paires USDT du recensement **cotées depuis
  plus de 180 jours**, `R180(d)` ; `μ*(EV02) = Σ [r(i, d) − e(i, d) × R180(d)] / Σ e`.
- **VO11** (dernière heure de la journée) : `m(p)` = moyenne du rendement brut de la bougie 23:00–24:00 de la paire sur
  ses journées de membre de l'étape (même unité que la position, tenue une heure).
- **Intervalle** : blocs de **7 jours calendaires** consécutifs, sur la série quotidienne `Σ_i [r(i, d) − e(i, d) × m]`
  et l'exposition `Σ_i e(i, d)` ; variance du ratio par la formule de `research/intervals.calendar_mean_ci`
  (covariance entre blocs voisins ajoutée quand elle est positive, jamais retranchée), le nombre d'événements étant
  remplacé par l'exposition ; quantile de Student à `B − 1` degrés de liberté (`B` = blocs avec exposition). `m` est
  traité comme connu, ce qui est **conservateur** d'un facteur ≈ 1 / (1 − f), `f` étant la part des journées de membre
  où la règle est exposée ; l'étalonnage est vérifié sous H0-b (§ 8). Comme la statistique est une valorisation
  quotidienne, deux blocs voisins seulement partagent une même journée : 7 jours suffisent quelle que soit la durée des
  positions. Fonction à écrire et à tester par le chantier de code.
- **Condition jointe** : le rendement net absolu par jour d'exposition, `μ = Σ r / Σ e`, doit être **> 0 en
  estimation dans les deux scénarios** (une règle qui perd de l'argent n'est pas candidate, même si elle perd moins que
  la dérive).
- **Descriptif** (jamais décisif) : `μ` et son intervalle ; décomposition en **choix des paires**
  `Σ e(i, d) × (r(i, d) − U(d))` et **choix des moments** `Σ e(i, d) × (U(d) − Ū)`, `U(d)` étant le rendement brut de
  l'univers à poids égaux le jour `d` et `Ū` sa moyenne sur l'étape ; rendement net **par transaction**, avec un
  intervalle par blocs de max(7, 2 × la plus longue tenue observée) jours ; nombre de transactions, durée moyenne,
  part gagnante, résultats par année et par paire, signaux ignorés ou écartés (position ouverte, paire non membre,
  entrée après la fin de l'étape, durée nulle).
- **Minimums** : au moins 100 transactions et 20 blocs avec exposition dans l'étape, sinon `INSUFFISANT`.

### 4.2 Forme (b) : facteur ou portefeuille

- **Portefeuille long seulement**, rééquilibré à la fréquence standard de la stratégie (§ 12) : **hebdomadaire** pour
  les facteurs et les règles de marché (décision le lundi 00:00 UTC sur les clôtures journalières jusqu'au dimanche,
  échanges à la **clôture du lundi**, tenue jusqu'à la clôture du lundi suivant, comme `XSECTION.md`) ; **quotidienne**
  pour AM01 à AM08 (décision à 00:00 UTC, échange à la clôture du jour) et XS24 (voir ci-dessous) ; **mensuelle** pour PF05 à PF09
  (premier lundi du mois).
- **Exception d'exécution pour MR19 (forme (a)) et XS24** : pour un effet d'un jour, attendre la clôture journalière
  suivante le déplacerait d'une journée entière ; l'entrée se fait donc à la **clôture de la première bougie 1 h dont
  l'ouverture suit l'`available_at` du signal** (la bougie de 01:00 à 02:00 UTC pour un signal de clôture journalière),
  et la sortie **24 h plus tard** (clôture de la bougie 1 h de même heure le lendemain).
- **Facteurs** (XS) : parmi les membres pour lesquels la variable est calculable, le **quintile** du côté de la
  publication citée (⌊n / 5⌋ paires, au moins 3, soit 8 sur 40), à poids égaux.
- **Référence** :
  - **stratégies toujours investies** (XS, PF) : l'**univers à poids égaux** de la stratégie, rééquilibré à la même
    fréquence, mêmes frais : les mêmes membres éligibles pour un facteur ; le top 40 du moment pour PF06, PF07 et PF09 ; pour PF05, qui **est** l'univers à poids égaux, le même panier
    laissé flotter (acheté à poids égaux au premier lundi de chaque trimestre, sans rééquilibrage jusqu'au suivant) ;
  - **rotations entre deux actifs** (RO02 : ETH ou BTC ; RO03 : altcoins à poids égaux ou BTC) : un portefeuille **de
    même composition moyenne**, `f̄ × A + (1 − f̄) × B`, où `f̄` est la part moyenne du temps passée dans `A` (ETH ;
    altcoins) sur l'étape, mesurée après coup, rééquilibré à la même fréquence et avec les mêmes frais ;
  - **règles d'exposition variable** (TR37, TR40, RO04, RO05, RG02, AM01 à AM04, AM06 à AM08, SE02) : la **référence
    d'exposition égale** `f̄ × U + (1 − f̄) × USDT`, où `U` est l'univers de la règle (top 40 à poids égaux, ou BTC pour
    les AM) et `f̄` l'**exposition moyenne de la règle mesurée après coup sur l'étape** (et dans chaque scénario),
    rééquilibrée à la même fréquence et avec les mêmes frais. `f̄` n'entre dans aucun signal. Raison : sans elle, une
    règle qui sort en USDT gagnerait contre l'univers dans toute période qui finit par une baisse, sans aucun talent de
    choix des moments (`TREND_DAILY.md`, `LONG_HORIZON.md`, `METEO_MARCHE.md` § 6).
- **Frais** sur la rotation : somme des variations de poids × (frais + écart et glissement de la paire), à chaque
  rééquilibrage, pour la stratégie comme pour la référence.
- **Statistique de décision** : **excès net hebdomadaire** = rendement net de la stratégie − rendement net de la
  référence, semaine du lundi au lundi (les séries quotidiennes et mensuelles sont valorisées chaque lundi).
- **Intervalle** : `calendar_mean_ci` sur la série hebdomadaire, blocs de **28 jours** (4 semaines).
- **Minimum** : 20 blocs, sinon `INSUFFISANT`. Découverte : 52 blocs ; validation : 32.
- **Descriptif** : excès sur l'univers investi (sans ajustement d'exposition) pour les règles d'exposition variable,
  rendement, volatilité, perte maximale, rotation, exposition moyenne, par année.

### 4.3 p-valeur et sens

- Pour chaque test et chaque scénario : `t` = statistique de décision (`μ*` ou excès hebdomadaire) / erreur type de
  l'intervalle ; **p bilatérale** de Student à `B − 1` degrés de liberté.
- **Sens** : CSI est acheteur. Si la statistique est ≤ 0 dans l'un des deux scénarios, ou si la condition jointe de la
  forme (a) échoue, `p = 1`.
- **p du test** = max(p central, p défavorable) : une découverte doit tenir dans les deux scénarios (test
  intersection-union, valide). En pratique, c'est la p du scénario défavorable.

## 5. Décisions

### 5.1 Découverte → `CANDIDATE`

1. Procédure de **Benjamini-Hochberg au niveau q = 10 %** sur les p des tests exécutés (au plus 239) : tri croissant
   `p(1) ≤ … ≤ p(M)`, `k` = plus grand rang tel que `p(k) ≤ 0,10 × k / M` ; les `k` premiers sont rejetés.
2. Un test rejeté est **`CANDIDATE`** si, en plus, sa statistique de décision reste **> 0 dans les deux scénarios** :
   - **sans sa meilleure année civile** de découverte (celle dont la contribution centrale à la statistique est la plus
     forte) ;
   - **sans sa meilleure paire** (forme (a) et facteurs XS : la paire dont la contribution centrale est la plus forte ;
     sans objet pour les règles de marché à un seul actif ou à l'univers entier, déclaré).

   Ces garde-fous retirent des candidates sans changer le seuil : ils ne peuvent que réduire les fausses découvertes.
3. Les autres : `NON_CANDIDATE` (ou `INSUFFISANT`).
4. **Descriptif** : la procédure de **Benjamini-Yekutieli** (valide sous toute dépendance, plus sévère) est appliquée
   aux mêmes p et rapportée ; elle ne décide de rien.

La procédure de Benjamini-Hochberg contrôle le taux de fausses découvertes sous indépendance ou dépendance positive ;
nos tests partagent le facteur de marché (dépendance surtout positive) mais pas tous (tendance contre retour à la
moyenne). La garantie réelle est celle que mesure le contrôle sous H0 (§ 8), pas le théorème.

### 5.2 Validation → `VALIDÉE`

Avec `m` = nombre de `CANDIDATE` (si `m = 0`, le zoo s'arrête : aucun essai de validation) :

1. intervalle **bilatéral au niveau 1 − 0,05 / m** de la statistique de décision **entièrement > 0** en central **et**
   en défavorable, et, pour la forme (a), la condition jointe (`μ` > 0 dans les deux scénarios) ;
2. **sans sa meilleure année** de validation (parmi 2023, 2024 et le premier semestre 2025), la condition 1 tient
   encore, au même niveau ; minimum de blocs abaissé à 12 pour ce seul garde-fou (forme (b) : sans 2024, il reste
   environ 19 blocs de 4 semaines) ;
3. au moins **2 années sur 3** (2023, 2024, 2025-S1) ont une statistique centrale > 0 ;
4. **concentration** (forme (a) et facteurs XS) : **aucune paire ne fait plus de 60 % du gain net central** (somme
   des contributions positives des paires).

Sinon : `NON_VALIDÉE` (ou `INSUFFISANT`). Une `VALIDÉE` n'est pas « rentable » : elle a battu la dérive de ses paires
(forme (a)) ou sa référence (forme (b)), net de frais, sur une période déjà vue par le programme ; seule la suite
(§ 10) peut la confirmer.

## 6. Placebos : descriptifs seulement

Leçon de `PRICE_ACTION.md` (§ 5.5, § 11) et de `FIGURES_HISTORIQUE.md` : des placebos tirés juste avant un signal
portent le chemin qui l'a formé et faussent l'excès. Ils ne décident donc de rien ; la statistique `μ*` joue déjà le
rôle de l'achat au hasard de même paire et même étape.

- **Forme (a)** : pour chaque transaction, 20 entrées placebo sur la **même paire**, de **même durée** (en bougies),
  tirées uniformément parmi les bougies de la **même étape** où la paire est membre (jamais dans une fenêtre liée au
  signal), mêmes frais ; graine dérivée du test et de la transaction. Rapporté : statistique des placebos et excès
  (signal − placebos), avec intervalle.
- **Forme (b)** : 1 000 portefeuilles tirés au hasard parmi les mêmes membres, même taille (ou même exposition), mêmes
  dates ; rapporté : rang centile de la stratégie.

## 7. Contrôles de causalité et garde d'exécution

- **Causalité, par test** : signaux et poids recalculés avec les seules données antérieures, puis avec un futur
  falsifié, à **au moins 50 instants tirés au hasard** et à **tous les instants de signal** d'un échantillon de
  **3 paires × 1 an** (graine fixe) : identiques au calcul complet.
- **Mutations** à détecter, pour chaque famille de code : un signal qui lit la clôture de la bougie suivante ; une
  bougie 4 h ou journalière datée par son ouverture au lieu de l'`available_at` de sa dernière bougie 1 h ; une série
  externe jointe sur sa date au lieu de son `available_at` ; un **pivot daté à son extrême** (au lieu de sa
  confirmation).
- **Pivots confirmés seulement** : ST01, ST03 à ST05 et AN09 n'utilisent que des pivots ZigZag **confirmés**
  (connus à la bougie de confirmation, `INDICATEURS.md` § 1), jamais le point extrême en cours.
- **Ichimoku** (TR22, TR23) : le nuage applicable à `t` est fait des Senkou calculées à `t − 26` (`INDICATEURS.md`
  § 10.4) ; la Chikou n'est **jamais** utilisée.
- **RG06** (ondelettes), **CH21** (Renko) et **TR48** (point et figure) sont recalculés à chaque décision sur le
  **passé seulement** (aucun filtrage à deux côtés, aucune brique ni colonne révisée après coup) ; la boîte Renko du mois est l'ATR 14 journalier connu au
  1er du mois.
- **AM04** : valeur du jour `d` connue au plus tôt à `d` + 1 12:00 UTC ; l'`available_at` de DGS10, plus tardif, est
  retenu. **AM05** (M2, révisé après coup) est **retiré** du zoo.
- **Définitions** : chaque indicateur testé contre des valeurs calculées à la main ; chandeliers contre TA-Lib si la
  bibliothèque est installée, sinon contre des exemples écrits à la main d'après sa documentation.
- **Code commité exigé** ; empreintes SHA-256 des données lues inscrites ; une seule exécution par étape.
- **Relecture** `leak-auditor` avant le contrôle sous H0.
- **Comptages à blanc** sur la découverte réelle (nombre de signaux par test, **aucun rendement**) : un test qui n'a
  pas 100 transactions possibles ou 20 blocs est retiré `INSUFFISANT_PAR_CONSTRUCTION`, **0 essai**. Publiés en plus,
  avant toute lecture de rendement :
  - **EV02** : (i) les nouvelles paires USDT du recensement sans annonce `COTATION_SPOT` et (ii) les annonces
    `COTATION_SPOT` sans paire USDT correspondante ; tout taux supérieur à 5 % est déclaré ici, avec sa cause ;
  - **CA10, CA11** : contrôle des dates CPI et NFP (jour de la semaine, écart entre deux publications) ; les exceptions
    (publication un autre jour que d'habitude, mois manquant ou doublé, comme l'arrêt de l'administration en 2025)
    sont listées ici.

## 8. Contrôle sous H0 (obligatoire, lancé une fois, avant toute exécution sur données réelles)

### 8.1 Marché synthétique (paramètres fixés ici a priori, aucune estimation sur données réelles)

- **200 répliques indépendantes** (graine `SeedSequence(20261012)`, une sous-graine par réplique et par paire).
- Bougies 1 h du 2017-08-17 au 2025-06-30 pour **100 paires** : BTC et ETH dès le début, 98 altcoins cotés à des dates
  tirées (10 avant 2018, les autres uniformément de 2018-01 à 2024-12), pour reproduire un univers qui passe d'une
  dizaine à 40 membres.
- **Rendements simples martingales** (H0-a) : `r(i, t) = σ(i, t) × s(h) × z(i, t)`, avec `E[r | passé] = 0` exactement.
  - Facteur commun : `z(i, t) = λ(i) u(t) + √(1 − λ(i)²) e(i, t)`, `u` = choc de BTC (λ = 1), ETH λ = 0,85, altcoins
    λ uniforme dans [0,55 ; 0,85] (corrélations de 0,3 à 0,7 avec BTC) ;
  - `u` et `e` : **Student à 4 degrés** de variance 1 (queues épaisses partout, leçon de `METEO_MARCHE.md` § 7.5) ;
  - **troncature** symétrique de `z` à ± min(20 ; 0,9 / (σ s)) : la moyenne reste nulle et |r| ≤ 0,9, donc **le prix
    reste > 0** (un test unitaire le vérifie) ;
  - `σ` : **GJR-GARCH(1, 1)** sur les rendements propres de la paire (α = 0,04 ; γ = 0,04 pour les baisses, **effet de
    levier** ; β = 0,93), volatilité horaire moyenne : BTC 0,6 %, ETH 0,75 %, altcoins log-uniforme dans [0,8 % ; 1,5 %] ;
  - `s(h)` : profil déterministe heure × jour (plus agité vers 14:00 UTC les jours ouvrés, plus calme le week-end),
    moyenne 1.
- **Bougies** : ouverture = clôture précédente ; plus haut = max(ouverture, clôture) × (1 + 0,5 σ s |w₁|), plus bas =
  min(ouverture, clôture) × (1 − 0,5 σ s |w₂|), `w` gaussiens indépendants. Comme l'ouverture est la clôture
  précédente, ce marché ne produit **aucune** figure exigeant un écart d'ouverture (CH06, CH07, CH09, CH14 et CH18,
  `IMPOSSIBLE` au recensement).
- **Volume** en devise de cotation : niveau de popularité de la paire (log-AR(1) journalier, φ = 0,995, innovation
  0,05, niveaux initiaux log-normaux d'écart-type 1) × `s(h)` × (0,5 + |z|) × facteur journalier log-normal
  (σ = 0,5, moyenne 1) : lié à l'ampleur, jamais au sens futur. Part acheteuse au marché = 0,5 + 0,08 tanh(z) + bruit
  (0,03), bornée à [0,05 ; 0,95] (liée au sens **de la même bougie** seulement). Nombre de transactions = volume / taille
  moyenne log-normale.
- **Retraits de la cote** décidés sur le seul passé : au 1er de chaque mois, une altcoin dont le rendement sur 90 jours
  est inférieur à −75 % est retirée avec une probabilité de 0,3, à une date tirée dans le mois ; vente à la dernière
  clôture.
- **Top 40 à date** : la fonction `pit_universe.membership`, inchangée, sur les journées synthétiques.
- **Séries et calendriers externes** (AM, SE, CA09 à CA13) : les **vraies** séries du magasin, inchangées. Elles ne
  portent aucune information sur des cryptos synthétiques indépendantes : la nulle reste exacte, et leur calendrier et
  leur persistance sont réels. **EV02** : un jeu de paires synthétiques supplémentaire, une
  par paire USDT du recensement, **cotée à la date réelle de sa première bougie**, l'annonce à l'heure réelle de son
  annonce `COTATION_SPOT` quand elle existe (sinon 24 h avant) : calendrier réel, prix synthétiques.
- Le **pipeline complet du zoo**, sans aucune différence (signaux, appartenance, formes, `m(p)`, `f̄`, intervalles,
  Benjamini-Hochberg, garde-fous), tourne sur chaque réplique, découverte puis validation.

### 8.2 Trois marchés sous l'hypothèse nulle

- **H0-a, martingale** (§ 8.1) : aucune dérive.
- **H0-b, dérive constante, bloquant** : mêmes répliques, chaque rendement horaire augmenté d'une dérive constante
  fixée ici : **BTC +0,10 % par jour, toutes les autres paires +0,20 % par jour** (réparties également sur les heures).
  Une dérive constante n'est pas une information : une règle ne doit pas y paraître douée. C'est le marché qui teste
  que `μ*` et la référence d'exposition égale retirent bien la dérive.
- **H0-c, régimes, descriptif seulement** : chaîne de Markov à 2 états **commune à toutes les paires**, durée moyenne
  d'un état 180 jours ; état haussier : dérive +0,25 %/jour (BTC +0,15 %), volatilité × 0,8 ; état baissier : dérive
  −0,25 %/jour (BTC −0,15 %), volatilité × 1,25 (valeurs a priori). La persistance des régimes y est une vraie
  information exploitable par une règle de tendance : H0-c dit combien de « candidates » des régimes seuls produiraient,
  sans rien décider.

### 8.3 Critères (calculés à frais nuls : la nulle est alors exactement au bord)

Sur une martingale, une règle causale a un gain brut d'espérance nulle ; avec des frais, son net serait négatif et le
contrôle trop facile. Les critères 1 et 2 se calculent donc **sans frais**, au bord de l'hypothèse nulle, **sur H0-a
et sur H0-b**, avec la statistique de décision (`μ*` pour la forme (a), excès sur la référence pour la forme (b)).

1. **Par test (étalonnage)** : part des 200 répliques où la statistique est > 0 avec p bilatérale ≤ 0,05 (valeur
   nominale 0,025), en découverte **et** en validation, sur H0-a **et** H0-b : **≤ 0,05** chacune. Sinon le test est
   retiré `MESURE_NON_VALIDE`, **0 essai**.
2. **Global** : sur les tests restants, part des 200 répliques qui produisent **au moins une `CANDIDATE`** :
   **≤ 0,10** sur H0-a **et** sur H0-b. Sinon **le zoo n'est pas exécuté** (`INSTRUMENT_NON_VALIDE`), 0 essai.
   Rapporté aussi : part des répliques avec au moins une `VALIDÉE`, nombre moyen de fausses candidates, et, en
   descriptif, **la part des répliques de H0-b où l'ancienne statistique « contre zéro »** (version du 2026-10-10)
   **aurait produit une `CANDIDATE`**.
3. **Puissance mesurée** (avec les vrais frais, sur H0-a) : pour chaque test `s` et chaque effet `g`, on ajoute à chaque
   position de `s` un rendement de `δ` par jour d'exposition (forme (a)) ou à son portefeuille `δ` par semaine
   (forme (b)), les signaux restant ceux de la réplique sans effet, les autres tests restant sous la nulle ; `δ` = `g` +
   le coût central moyen de `s` par jour d'exposition (ou par semaine) mesuré sur la même réplique, pour que **l'effet
   net central vrai vaille `g`**. Effets : forme (a) `g` ∈ {+0,10 ; +0,25 ; +0,50} % par jour d'exposition ; forme (b)
   `g` ∈ {+0,25 ; +0,5 ; +1 ; +2} % par semaine. Rapporté : part des répliques où `s` devient `CANDIDATE`
   (Benjamini-Hochberg recalculé avec `s` modifié), puis `VALIDÉE`.
4. **Test aveugle** : un test dont la puissance de découverte au plus grand effet (+0,50 %/jour ou +2 %/semaine) est
   **< 0,10** est retiré `INSTRUMENT_AVEUGLE`, **0 essai** : il ne pourrait rien voir.
5. **H0-c** et les résultats avec frais réels : descriptifs.

### 8.4 Garde

- Lancé **une seule fois** (`csi zoo controle-h0`), fichier `reports/ZOO-H0-<commit>/criteres.json`, empreinte inscrite
  dans le code du zoo ; l'exécution réelle relit ce fichier et refuse s'il manque ou a changé.
- **Pas de seconde itération.** Seule exception, une fois au plus (comme `PRICE_ACTION.md` § 5.3) : un écart prouvé
  entre le code et ce texte, démontré par un test unitaire écrit avant la relance ; les deux passages sont publiés.
- Si le temps de calcul de 3 × 200 répliques dépasse ce que le PC permet, le chantier de code déclare **avant** le
  passage une réduction (au moins 100 répliques pour H0-a et H0-b, 50 pour H0-c), jamais après.

## 9. Comptage des essais

- Programme avant le zoo (registre lu le 2026-10-10) : **873 essais** sur DEVELOPMENT ; **10 essais FINAL_TEST** déjà
  enregistrés, en **4 consultations** de la période réservée.
- **Découverte** : chaque test exécuté compte **1 essai DEVELOPMENT** (une exécution, `n_trials` = nombre de tests
  exécutés, au plus 239). Les tests retirés par le contrôle sous H0 ou par les comptages à blanc : 0 essai.
- **Validation** : **1 essai de plus** par `CANDIDATE` (une exécution, `n_trials` = `m`).
- **Période réservée** : 1 essai `FINAL_TEST` par `VALIDÉE` lue (`k`).
- **F31_ZOO** : 1 essai au registre des tests en direct.
- **Nouveaux totaux, au plus** : DEVELOPMENT 873 + 239 + `m` = **1 112 + `m`** ; FINAL_TEST **10 + `k`**, en
  5 consultations.

## 10. Après la validation

Les mêmes statistiques qu'en découverte et en validation : `μ*` (avec `m(p)` calculé sur la période lue) et la
condition jointe pour la forme (a) ; excès sur la référence (d'exposition égale ou de même composition moyenne, `f̄` mesuré sur la période lue) pour
la forme (b).

**Paires éligibles à F31.** En découverte, en validation et sur la période réservée, chaque résultat est **aussi**
rapporté restreint aux paires éligibles à F31 (top 40 à date **et** admises par le filtre halal du projet, sans les
`NON_RELEVE`), en descriptif : c'est l'univers que F31 mesurera réellement.

### 10.1 Lecture unique de la période réservée (consultation n° 5)

- Seulement s'il existe au moins une `VALIDÉE`, et **sur décision du propriétaire**, comme les quatre consultations
  précédentes ; `--i-understand-final-test`, consultation enregistrée (`final_test_consultations`).
- Période : du 2025-07-01 à la dernière journée UTC complète avant la lecture (date inscrite au registre avant la
  lecture). Mêmes règles, même code, mêmes frais, toutes les `VALIDÉE` ensemble (`k` stratégies).
- Verdict par stratégie : `CONFIRMÉE` si l'intervalle au niveau 1 − 0,05 / k est > 0 dans les deux scénarios (et la
  condition jointe tient) ; `INFIRMÉE` si l'IC95 est < 0 dans les deux ; sinon `NON_CONCLUANTE`.
- Rappel : la période réservée a déjà été lue quatre fois ; elle n'est plus vierge pour le programme.

### 10.2 Test en direct unique `F31_ZOO`

- **EV02 n'y est que descriptif**, même `VALIDÉE` : une nouvelle cotation n'a pas de relevé halal au moment de
  l'annonce, et F31 ne mesure que des paires admises ; ses résultats en direct sont rapportés sans verdict.
- Toutes les autres `VALIDÉE` non `INFIRMÉE`, **ensemble**, dans un seul test pré-inscrit dans `FORWARD_TESTS.md` par le
  chantier de code avant son démarrage, conforme au cadre commun (gel des empreintes, journal chaîné, frais de
  `forward/costs.py`, aucun message, aucun ordre).
- Univers : top 40 à date **et** liste halal figée au démarrage (les paires `NON_RELEVE` en sont exclues).
- Statistiques : celles du début du § 10, `m(p)` et `f̄` mesurés sur la période du test.
- Durée : **52 semaines**, une seule évaluation à la fin ; revues descriptives à 12 et 26 semaines, sans changement de
  règle. C'est un écart déclaré au cadre commun (12 semaines), imposé par la puissance (§ 11.3).
- Verdict par stratégie : `SUPERIEUR_A_ZERO` si l'intervalle au niveau 1 − 0,05 / k est > 0 dans les deux scénarios
  (condition jointe comprise) ; `INFERIEUR_A_ZERO` si l'IC95 est < 0 dans les deux ; `INSUFFISANT` sous 30 transactions
  ou 20 blocs (forme (a)) ou 12 blocs (forme (b)) ; sinon `NON_DEMONTRE`.
- Rien ne passe en Demo ou en réel sur ce seul verdict : décision du propriétaire.

## 11. Puissance attendue (ordres de grandeur, hypothèses déclarées, non mesurées)

Le contrôle sous H0 (§ 8.3, point 3) mesurera la vraie puissance de chaque test ; ces chiffres ne servent qu'à dire
d'avance ce que le zoo peut voir.

**Hypothèses** : volatilité journalière d'une altcoin ≈ 4,5 % (70 à 100 % par an en 2019-2022), de BTC ≈ 3,5 % ;
corrélation moyenne des rendements journaliers entre altcoins ≈ 0,6 ; écart-type hebdomadaire de l'excès d'un
quintile de 8 paires sur l'univers ≈ 6,4 % (déduit de `XSECTION.md` : demi-largeur de l'IC95 ≈ 0,7 %/semaine sur 339
semaines) ; environ 240 tests, dont très peu porteraient un vrai effet. Retirer la dérive (`μ*`) ne change presque pas
l'erreur type : la dérive est une constante par paire et par étape ; l'intervalle qui traite `m` comme connu est
seulement un peu plus large que nécessaire (facteur ≈ 1 / (1 − f)).

**Seuil de découverte.** Avec 239 tests et un seul vrai effet, Benjamini-Hochberg rejette sous p ≈ 0,10 / 239 ≈
0,0004 (bilatéral), soit |t| ≈ 3,5. Une puissance de 80 % exige donc un effet d'environ **4,4 erreurs types**. Avec
une dizaine de vrais effets, le seuil remonte vers p ≈ 0,004 (|t| ≈ 2,9) : environ 3,7 erreurs types.

### 11.1 Forme (a), découverte (1 461 jours)

| Type de test | Exposition typique | Erreur type de `μ*` | Effet net vu à 80 % |
|---|---|---|---|
| Règle d'état journalière (tendance), ≈ 16 positions par jour | 40 % | ≈ 0,10 %/jour : variance d'une journée ≈ σ² (k + k(k − 1) ρ) = 0,045² × 160, sur 1 461 jours et 23 000 jours d'exposition | ≈ 0,43 %/jour d'exposition |
| Règle d'état 1 h ou 4 h | 40 à 50 % | du même ordre (le facteur de marché domine) | ≈ 0,4 à 0,5 %/jour |
| Événement 1 j tenu 1 à 10 jours (≈ 1 000 à 10 000 transactions) | faible | ≈ 0,06 à 0,07 %/jour | ≈ 0,3 %/jour, soit ≈ 3 % net par transaction de 10 jours |
| Événement 1 h tenu 10 heures (≈ 30 000 transactions) | faible | ≈ 0,11 %/jour (3,8 % par transaction, grappes de 4) | ≈ 0,5 %/jour, soit ≈ 0,2 % net par transaction (0,6 % brut avec les frais défavorables) |
| Momentum intrajournalier VO11, tenu 1 heure (≈ 29 000 transactions) | 1/24 de journée | ≈ 0,3 %/jour | ≈ 1,3 %/jour, soit ≈ 0,05 % net par heure (0,45 % brut avec les frais défavorables) |
| Événements de calendrier CA09 à CA11 (≈ 32 FOMC, ≈ 48 CPI et NFP, tous les membres à la fois) | 1 jour par événement | ≈ 0,5 à 0,65 %/jour (≈ 3,6 % d'écart-type du panier sur 24 h, un tirage par événement) | ≈ 2,3 à 2,8 %/jour : la dérive avant le FOMC publiée pour les actions (≈ 0,5 %) est **invisible** |
| Fear & Greed SE01 (≈ 15 épisodes, tenue 30 jours) | épisodes | ≈ 0,17 %/jour | ≈ 0,75 %/jour, soit ≈ +22 % sur 30 jours au-delà de la dérive |
| Cotations Binance EV02 (≈ 150 à 250 annonces, tenue 30 jours) | faible | ≈ 0,18 %/jour (écart-type journalier ≈ 8 % d'une paire nouvelle) | ≈ 0,8 %/jour |

Puissance de découverte d'un vrai effet de forme (a) (erreur type 0,10 %/jour) : **+0,10 %/jour : ≈ 0** ;
**+0,25 %/jour : ≈ 0,15** ; **+0,50 %/jour : ≈ 0,9**. Pour comparaison, l'ensemble de Donchian de `TREND_DAILY.md`
gagnait de l'ordre de 0,2 % par jour d'exposition **dérive comprise** ; au-delà de la dérive, nettement moins.

**Validation** (913 jours, erreur type × 1,27) : pour `m` = 1, effet vu à 80 % ≈ 2,8 erreurs types ≈ 0,35 à
0,5 %/jour ; `m` = 5 ≈ 0,45 à 0,65 %/jour ; le garde-fou « sans la meilleure année » (≈ 1,5 an) l'alourdit encore.
Probabilité d'être `VALIDÉE` pour un vrai +0,50 %/jour : ≈ 0,75 ; pour +0,25 %/jour : ≈ 0,05.

### 11.2 Forme (b), découverte (208 semaines)

| Type de test | Écart-type de l'excès | Erreur type | Effet vu à 80 % |
|---|---|---|---|
| Facteur (quintile de 8 contre 40) | ≈ 6,4 %/semaine | ≈ 0,44 %/semaine | ≈ 1,9 %/semaine |
| Règle de marché contre la référence d'exposition égale (`f̄` ≈ 0,6) | ≈ 12 % × √(f̄ (1 − f̄)) ≈ 5,9 % | ≈ 0,41 % | ≈ 1,8 %/semaine |
| BTC détenu ou non (AM) contre l'exposition égale (`f̄` ≈ 0,5) | ≈ 9 % × 0,5 ≈ 4,5 % | ≈ 0,31 % | ≈ 1,4 %/semaine |
| Retournement d'un jour (XS24), quotidien | ≈ 6 à 7 %/semaine, rotation forte | ≈ 0,45 % | ≈ 2 %/semaine, frais de rotation compris |
| Allocation mensuelle (PF) contre l'univers | ≈ 3 à 6 % | ≈ 0,2 à 0,4 % | ≈ 1 à 1,8 %/semaine |

Puissance de découverte d'un facteur : **+0,25 %/semaine : ≈ 0** ; **+0,5 : ≈ 0,01** ; **+1 : ≈ 0,1** ; **+2 :
≈ 0,85**. Validation (130 semaines, erreur type ≈ 0,56 %) : effet vu à 80 % ≈ 1,6 %/semaine (`m` = 1) à 1,9 (`m` = 5),
sans la meilleure année ≈ 2 à 2,5 %/semaine. Un excès de +2 %/semaine sur la référence ferait plus que doubler sa
performance chaque année : le zoo ne peut voir qu'une anomalie énorme. Les primes publiées (quelques dixièmes de % par
semaine pour un portefeuille acheteur de grandes cryptos) sont sous ce seuil ; `XSECTION.md` le disait déjà (rien sous
environ 0,7 %/semaine n'était visible sur 6,5 ans).

Cohérent avec le contrôle positif (`POSITIVE_CONTROL.md` § 5 et § 8) : les criblages voyaient +0,5 % à 1 jour et +2 % à
7 jours, et seulement 13 à 30 % du temps +1 % à 7 jours pour des conditions groupées.

### 11.3 Période réservée et F31

- **Période réservée** (≈ 15 mois au moment probable de la lecture : 460 jours, 65 semaines) : erreur type ≈ 0,18 %
  par jour d'exposition (forme (a)) et ≈ 0,8 % par semaine (forme (b)) ; avec `k` = 1, effet vu à 80 % ≈ 0,5 %/jour ou
  2,2 %/semaine.
- **F31_ZOO** (52 semaines) : ≈ 0,2 %/jour (effet vu à 80 % ≈ 0,56 %/jour pour `k` = 1) ; ≈ 0,9 %/semaine (≈ 2,5 %
  par semaine). Sur 12 semaines, la puissance serait quasi nulle : d'où les 52 semaines. L'univers de F31 (paires
  admises seulement) est plus petit que le top 40 : un peu moins de puissance encore. **F31 dira surtout si une
  `VALIDÉE` s'effondre** (`INFERIEUR_A_ZERO`) ; il ne confirmera qu'un effet grand.

### 11.4 Conséquence déclarée

Le zoo couvre beaucoup de stratégies, pas les effets fins : il répond à « une stratégie connue a-t-elle un avantage
énorme et stable au-delà de la dérive ? ». Un « rien » dira « aucune n'a d'avantage plus grand qu'environ 0,4 % par
jour d'exposition au-delà de la dérive, ou 2 % par semaine sur sa référence » ; les effets plus petits restent ni
démontrés ni exclus. Réduire la famille (moins d'unités de temps) aurait gagné peu (le seuil passe de |t| ≈ 3,5 à 3,3
pour 100 tests) au prix de la couverture demandée.

## 12. Tableau des tests du zoo

Conventions : « croise au-dessus » = vrai à `t` et faux à `t − 1` ; **état** = position ouverte tant que la condition
est vraie (entrée quand elle devient vraie, à la première décision de l'étape, ou quand la paire entre dans le top 40) ;
**événement** = une entrée par déclencheur. `N` = plafond de tenue en bougies de l'unité ; « — » = pas de plafond
(sortie par la règle ou fin d'étape). Plafonds a priori : 20 bougies pour les règles de retour à la moyenne dotées
d'une sortie, 10 bougies pour les événements sans sortie (durée de Marshall, Young et Rose, 2006). Paramètres : ceux
du recensement. Les tests marqués « déjà vu » au recensement sont mesurés, mais lus comme non indépendants des
regards passés.

**Conventions de mise en œuvre (déclarées le 2026-10-11, avant tout résultat)** :
- **fin de journée ou de séance** : « vendu à la clôture de 24:00 (ou de fin de séance) » ; la condition de sortie est
  vraie sur les deux dernières bougies 1 h de la journée (ou de la séance), exécutée à la clôture suivante, bornée à
  24:00 (ou à la fin de séance) ; un signal sur l'une de ces deux dernières bougies donne une durée nulle, écartée et
  comptée (MR09, VO01 à VO05, ST06 à ST10, ST14) ;
- **une seule sortie par position** (ST03 à ST05, AN06, AN09) : l'interface rend une condition de sortie par bougie ;
  un nouveau signal arrivé pendant une position (ignoré par le moteur) peut déplacer les niveaux de sortie lus par
  cette condition (nouvelle jambe, nouvelle zone). Approximation déclarée : les niveaux de sortie ne sont pas figés à
  l'entrée ;
- **RG07** : la règle reste « la sinusoïde croise au-dessus de sa version avancée (LeadSine) », écrite au recensement ;
  le sens d'achat d'Ehlers (croisement de la sinusoïde au-dessus de la LeadSine, ou l'inverse selon les éditions) est
  incertain : déclaré, non modifié ;
- **paires absentes** : 7 paires membres du top 40 pendant la découverte n'ont pas d'historique 1 h au magasin long
  (AION, ANT, GAL, JST, SC, SKL, SUN ; LEVER s'y ajoute en validation) : survivance résiduelle déjà déclarée au § 2.

### 12.1 Forme (a) : règles de transaction (207 tests)

| ID | Unités | Type | Entrée (signal à la clôture `t`) | Sortie | N |
|---|---|---|---|---|---|
| TR01 à TR08 | 1h, 4h, 1j | événement | moyenne rapide croise au-dessus de la lente (SMA ou EMA, paires 5/20, 10/50, 20/100, 50/200) | croisement inverse | — |
| TR09 | 1h, 4h, 1j | état | clôture > SMA 50 | clôture ≤ SMA 50 | — |
| TR10 | 1h, 4h, 1j | état | clôture > SMA 200 | clôture ≤ SMA 200 | — |
| TR12 | 1h, 4h, 1j | événement | clôture > plus haut des 20 bougies précédentes | clôture < plus bas des 10 bougies précédentes | — |
| TR13 | 4h, 1j | événement | clôture > plus haut des 55 précédentes | clôture < plus bas des 20 précédentes | — |
| TR18 | 1h, 4h, 1j | événement | clôture croise au-dessus de EMA 20 + 2 ATR 10 | clôture < EMA 20 | — |
| TR19 | 1h, 4h, 1j | état | SuperTrend (10, 3) haussier | bascule baissière | — |
| TR20 | 1h, 4h, 1j | état | SAR (0,02 ; 0,20) sous le prix | SAR au-dessus | — |
| TR21 | 1h, 4h, 1j | état | ADX 14 > 25 et +DI > −DI | +DI < −DI | — |
| TR22 | 1h, 4h, 1j | état | clôture > haut du nuage applicable (Senkou de `t − 26`) | clôture ≤ haut du nuage | — |
| TR23 | 1h, 4h, 1j | événement | Tenkan croise au-dessus de Kijun, clôture > haut du nuage applicable | Tenkan < Kijun | — |
| TR24 | 1h, 4h, 1j | événement | MACD croise au-dessus de son signal | croisement inverse | — |
| TR25 | 1h, 4h, 1j | état | MACD > 0 | MACD ≤ 0 | — |
| TR26 | 1h, 4h, 1j | événement | Aroon haut croise au-dessus d'Aroon bas (25) | croisement inverse | — |
| TR27 | 1h, 4h, 1j | événement | VI+ croise au-dessus de VI− (14) | croisement inverse | — |
| TR28 | 1h, 4h, 1j | événement | TRIX 15 croise au-dessus de son signal 9 | croisement inverse | — |
| TR29 | 1h, 4h, 1j | événement | clôture croise au-dessus de KAMA (10, 2, 30) | clôture < KAMA | — |
| TR30 | 1h, 4h, 1j | état | HMA 20 en hausse (HMA_t > HMA_{t−1}) | HMA en baisse | — |
| TR31, TR32 | 1j | état | clôture ≥ (1 + x) × plus bas des clôtures depuis la dernière sortie (ou le début de l'étape), x = 5 % ou 10 % | clôture ≤ (1 − x) × plus haut atteint depuis l'entrée | — |
| TR33 à TR35 | 1j | événement | clôture > plus haut des 50, 150 ou 200 jours précédents | aucune (tenue fixe) | 10 |
| TR36 | 1j | événement | (max − min) des 20 jours précédents ≤ 10 % du min, et clôture > ce max | aucune | 10 |
| TR48 | 1j | événement | point et figure (boîte 1 % log, retournement 3 boîtes, clôtures) : colonne de X dépasse la précédente colonne de X | colonne de O passe sous la précédente colonne de O | — |
| MR01 | 1j | événement | RSI 2 < 10 et clôture > SMA 200 | clôture > SMA 5 | 20 |
| MR02 | 1h, 4h, 1j | événement | RSI 14 croise au-dessus de 30 | RSI 14 > 70 | 20 |
| MR03 | 1h, 4h, 1j | événement | clôture de `t − 1` < bande basse, clôture de `t` > bande basse (BB 20, 2) | clôture ≥ bande médiane | 20 |
| MR05 | 1h, 4h, 1j | événement | %K croise au-dessus de %D avec %K < 20 (14, 3, 3) | %K > 80 | 20 |
| MR06 | 1h, 4h, 1j | événement | %R 14 croise au-dessus de −80 | %R > −20 | 20 |
| MR07 | 1h, 4h, 1j | événement | CCI 20 croise au-dessus de −100 | CCI > +100 | 20 |
| MR09 | 1h | événement | écart relatif à la VWAP du jour < −2 écarts-types (168 bougies précédentes) | clôture ≥ VWAP du jour, ou dernière bougie de la journée UTC | 24 |
| MR11 | 1h, 4h, 1j | événement | rendement de `t` < −2 × écart-type des 20 rendements précédents | aucune | 10 |
| MR12 | 1j | événement | 3 clôtures consécutives en baisse et clôture > SMA 200 | clôture > SMA 5 | 20 |
| MR19 | 1j (exécution 1h) | événement | IBS = (C − L) / (H − L) < 0,2 ; entrée à la clôture de la 1re bougie 1 h qui s'ouvre après l'`available_at` du signal | 24 h après l'entrée | 24 h |
| MR20 | 1j | événement | clôture > SMA 200 et clôture = plus bas des 7 dernières | clôture = plus haut des 7 dernières | 20 |
| VO01 | 1j (exécution 1h) | événement | 1re clôture 1 h du jour > ouverture du jour + 0,5 × (plus haut − plus bas de la veille) | clôture de la dernière bougie 1 h de la journée UTC | 24 h |
| VO02 | 1j (exécution 1h) | événement | 1re clôture 1 h > ouverture du jour + 0,5 × amplitude Dual Thrust (N = 4) | idem | 24 h |
| VO03 à VO05 | 1h | événement | 1re clôture 1 h de la séance au-dessus du plus haut de la 1re heure de la séance | dernière bougie 1 h de la séance | — |
| VO06, VO07 | 1j | événement | clôture > plus haut d'un jour NR4 (NR7) dans les 2 jours qui le suivent | aucune | 10 |
| VO08 | 1h, 4h, 1j | événement | fin de la compression (BB 20/2 sort de KC 20/1,5 après au moins une bougie dedans) et momentum de Carter > 0 | 2 baisses consécutives du momentum | 20 |
| VO09 | 1h, 4h, 1j | événement | clôture > clôture précédente + 1 × ATR 14 | aucune | 10 |
| VO11 | 1h | événement | rendement de 00:00 à 22:00 UTC > 0 (signal à la clôture de 21:00–22:00) | entrée à 23:00, sortie à la clôture de 23:00–24:00 | 1 |
| CH01, CH03 à CH05, CH08, CH10, CH13, CH17 (8 figures ; CH02, CH11, CH12, CH15, CH16 `IMPOSSIBLE` le 2026-10-11) | 1h, 4h, 1j | événement | figure TA-Lib haussière à `t`, tendance préalable du recensement | aucune | 10 |
| CH20 | 1h, 4h, 1j | événement | 1re bougie Heikin Ashi haussière sans mèche basse après une baissière | 1re HA baissière | — |
| CH21 | 1h, 1j | événement | 2e brique Renko haussière après une baissière (boîte ATR 14 journalier connue au 1er du mois, briques sur le passé seul) | 1re brique baissière | — |
| ST01 | 4h, 1j | état | dernier pivot haut ZigZag **confirmé** > le précédent et dernier pivot bas confirmé > le précédent | un nouveau pivot bas confirmé plus bas que le précédent | — |
| ST03 à ST05 | 1h, 4h, 1j | événement | sur la dernière jambe haussière ZigZag confirmée [B ; H] : 1re clôture > niveau (38,2 / 50 / 61,8 %) après une bougie dont le plus bas l'a touché, avant tout nouveau pivot | clôture ≥ H, ou clôture < B | 20 |
| ST06 | 1h | événement | plus bas ≤ S1 et clôture > S1 (pivots classiques du jour), première fois du jour | clôture ≥ P, sinon fin de journée UTC | 24 |
| ST07 | 1h | événement | 1re clôture du jour > R1 | clôture ≥ R2, sinon fin de journée | 24 |
| ST08 | 1h | événement | plus bas ≤ L3 et clôture > L3 (Camarilla), première fois du jour | clôture ≥ H3, sinon fin de journée | 24 |
| ST09 | 1h | événement | 1re clôture du jour > H4 | fin de journée | 24 |
| ST10 | 1h | événement | plus bas ≤ S1 de Woodie et clôture > S1, première fois du jour | clôture ≥ P, sinon fin de journée | 24 |
| ST11 | 1j | événement | clôture > plus haut des 365 jours précédents | aucune | 20 |
| ST12 | 1j | événement | clôture > plus haut de tout l'historique (≥ 365 jours) | aucune | 20 |
| ST13 | 1h, 4h | événement | clôture croise au-dessus d'un niveau rond majeur | aucune | 10 |
| ST14 | 1h | événement | 1re clôture du jour > plus haut de la veille | fin de journée UTC | 24 |
| ST15 | 4h | événement | 1re clôture de la semaine > plus haut de la semaine précédente | fin de la semaine (dimanche 24:00 UTC) | 42 |
| VF01 | 1h, 4h, 1j | événement | OBV croise au-dessus de sa SMA 20 | croisement inverse | — |
| VF02 | 1h, 4h, 1j | événement | ligne A/D croise au-dessus de sa SMA 20 | croisement inverse | — |
| VF03 | 1h, 4h, 1j | état | oscillateur de Chaikin (3, 10) > 0 | ≤ 0 | — |
| VF04 | 1h, 4h, 1j | événement | MFI 14 croise au-dessus de 20 | MFI > 80 | 20 |
| VF05 | 1h, 4h, 1j | événement | volume > 5 × moyenne des 20 précédentes et clôture > ouverture | aucune | 10 |
| VF16 | 1h, 4h, 1j | état | Elder Force Index (EMA 13) > 0 | ≤ 0 | — |
| VF17 | 1h, 4h, 1j | événement | Klinger (34, 55) croise au-dessus de son signal 13 | croisement inverse | — |
| SE01 | 1j | événement | la dernière valeur du Fear & Greed dont l'`available_at` précède la décision passe sous 25 : achat de chaque membre du top 40 | aucune | 30 |
| CA09 | 1h | événement | date connue à l'avance : achat de chaque membre à la clôture de la **dernière bougie 1 h qui se termine au plus tard 24 h avant** le communiqué du FOMC (14:00 New York, converti en UTC avec `zoneinfo`, heure d'été comprise) ; exception déclarée à la « clôture suivante » | clôture de la **dernière bougie 1 h qui se termine au plus tard à l'heure de publication** | — |
| CA10, CA11 | 1h | événement | idem, publication du CPI (du NFP) à 08:30 New York | idem | — |
| CA13 | 1j | événement | 1re clôture journalière après une nouvelle lune | 1re clôture après la pleine lune suivante | — |
| EV02 | 1j | événement | 1re journée complète de cotation d'une paire annoncée (`COTATION_SPOT`, annonce connue avant la clôture de cette journée) | aucune | 30 |
| RG03 | 1j | état | pente filtrée (Kalman, tendance locale linéaire du log-prix, variances estimées chaque mois sur le passé) > 0 | pente ≤ 0 | — |
| RG04 | 1j | état | Hurst R/S (128 rendements) > 0,5 et rendement 20 j > 0 | condition fausse | — |
| RG05 | 1j | état | entropie de permutation (ordre 3, 64 j) dans le tiers bas de ses 365 j et rendement 20 j > 0 | condition fausse | — |
| RG06 | 1j | état | pente de la tendance de Haar (niveau 3, recalculée à chaque décision sur le passé seul) > 0 | pente ≤ 0 | — |
| RG07 | 4h, 1j | événement | sinusoïde d'Ehlers croise au-dessus de sa version avancée | croisement inverse | — |
| AN06 | 1h, 4h | événement | 1er retour dans un breaker haussier, clôture au-dessus de la zone | clôture sous la zone | 10 |
| AN09 | 4h, 1j | événement | pivots **confirmés** : clôture > sommet de la vague 1 après une vague 2 de 38,2 à 78,6 % restée au-dessus de son départ | clôture ≥ départ + 1,618 × vague 1, ou < creux de la vague 2 | 20 |
| AN13 | 1j | état | swing de Gann haussier (dernier sommet de swing de 2 bougies dépassé) | clôture sous le dernier creux de swing | — |

Total forme (a) : 81 (TR) ; 23 (MR) ; 14 (VO) ; 29 (CH) ; 22 (ST) ; 21 (VF) ; 1 (SE01) ; 4 (CA09 à CA11, CA13) ;
1 (EV02) ; 6 (RG03 à RG07) ; 5 (AN) = **207 tests** (AN05 retiré : doublon de ST05).

### 12.2 Forme (b) : facteurs et portefeuilles (32 tests)

| ID | Fréquence | Portefeuille | Référence |
|---|---|---|---|
| TR37 | hebdomadaire | chaque membre à 1/n si son rendement sur 7 j > 0, sinon sa part en USDT | exposition égale (top 40) |
| TR40 | hebdomadaire | idem sur 84 j | exposition égale (top 40) |
| XS01, XS09, XS10, XS11, XS12, XS14, XS15, XS17, XS18, XS19, XS20, XS23 | hebdomadaire | quintile du côté publié (recensement § 7) | membres éligibles à poids égaux |
| XS24 | quotidienne (exécution 1h) | quintile des plus fortes baisses de la veille ; achat à la clôture de la 1re bougie 1 h qui s'ouvre après l'`available_at` du signal, vente 24 h plus tard | membres éligibles à poids égaux |
| RO02 | hebdomadaire | 100 % ETH si ETHBTC > SMA 50 journalière, sinon 100 % BTC | même composition moyenne : `f̄` ETH + (1 − `f̄`) BTC |
| RO03 | hebdomadaire | altcoins membres à poids égaux si ≥ 75 % d'entre eux battent BTC sur 90 j, sinon 100 % BTC | même composition moyenne : `f̄` altcoins + (1 − `f̄`) BTC |
| RO04 | hebdomadaire | top 40 à poids égaux si plus de la moitié clôture au-dessus de sa SMA 50, sinon USDT | exposition égale (top 40) |
| RO05 | hebdomadaire | top 40 à poids égaux si la corrélation moyenne sur 30 j est sous sa médiane des 365 j précédents, sinon USDT | exposition égale (top 40) |
| RG02 | hebdomadaire | top 40 à poids égaux sauf si la probabilité filtrée de l'état le plus volatil (3 états, BTC, réestimation mensuelle comme `FACTORS.md`) dépasse 0,5 | exposition égale (top 40) |
| SE02 | hebdomadaire | top 40 à poids égaux sauf si la dernière valeur connue du Fear & Greed dépasse 75 | exposition égale (top 40) |
| AM01 à AM04, AM06 à AM08 | quotidienne | 100 % BTC si le signal du recensement § 9 est vrai, sinon USDT | exposition égale (BTC) |
| PF05 | mensuelle | top 40 à poids égaux rééquilibré chaque mois | même panier laissé flotter, renouvelé chaque trimestre |
| PF06 | mensuelle | poids ∝ 1 / volatilité journalière sur 30 j | top 40 à poids égaux |
| PF07 | mensuelle | minimum de variance long seul (covariance 90 j rétrécie de Ledoit-Wolf) | top 40 à poids égaux |
| PF09 | mensuelle | 60 % BTC, 40 % ETH | top 40 à poids égaux |

Total forme (b) : 2 + 13 + 4 + 1 + 1 + 7 + 4 = **32 tests** (13 d'exposition variable, 2 rotations entre deux actifs, 17 toujours investis).

## 13. En attente de données, ou en direct seulement (14 lignes du recensement)

Même méthode que ci-dessus quand les données existeront ; chaque branchement sera pré-inscrit, avec ses propres
périodes si l'historique est plus court que 2019-2025, **avant** tout téléchargement de rendements.

| Lignes | Ce qui manque, ou le choix fait |
|---|---|
| SE04 | vues Wikipédia par crypto (table crypto → article à construire) |
| AM05 | M2 point-in-time (millésimes ALFRED) : retiré du zoo, la série du magasin est révisée après coup |
| DE07, DE08 | **en direct seulement** (choix du 2026-10-11) : archive `metrics` pour les 16 paires de la configuration depuis 2021-12. Une découverte sur 2022 seul, parmi 239 tests, demanderait ≈ 3 %/semaine (52 semaines, quintile de 3 paires sur 16, écart-type de l'excès ≈ 5 %/semaine, erreur type ≈ 0,7 %) : sans objet. En direct sur 52 semaines, seul dans sa famille : effet vu à 80 % ≈ 2 %/semaine, faible aussi (déclaré). DE07 est un **cousin déjà vu** de DE05 et DE06 (`DERIVATIVES.md`, `CONTEXTE_PREDICTION.md`). Une étude historique séparée sur 2022-01 → 2025-06 reste possible plus tard, pré-inscrite et **comptée à part** |
| OC07, OC08 | séries communautaires de CoinMetrics (offre dormante, adresses actives, NVT) |
| EV03, EV06 | archive des annonces de retrait de la cote (la catégorie lue ne contient que les cotations), historique des piratages |
| VF13, VF14, VF15, DE13 | journaux du collecteur (F26 à F29, décision du 2026-10-10 : nouvelle pré-inscription quand la puissance atteindra 50 %) |
| RG10 | instrument de la météo non validé (le Fear & Greed est désormais en magasin) |

## 14. Hors moteur (3 lignes)

| Ligne | Pourquoi |
|---|---|
| ML05 (LSTM de Fischer et Krauss) | 3 × 200 entraînements au contrôle sous H0 hors du budget du PC ; protocole ML propre avec régression linéaire de référence obligatoire (`POSITIVE_CONTROL.md` § 8) |
| ML06 (Transformer) | idem, et aucun réglage standard publié : l'architecture serait à déclarer |
| DE12 (DVOL au plus haut) | déjà pré-inscrit (`OPTIONS_PEUR.md`) ; contrôle sous H0 `INSTRUMENT_TROP_FAIBLE` : ne pas le reprendre ici |

## 15. Ordre d'exécution (rien n'est lancé par ce document)

1. Code du moteur, une fonction pure par stratégie, tests des définitions, des formes, de `μ*`, de la référence
   d'exposition égale, des intervalles et de la causalité (mutations comprises) ; `ruff`, `mypy`.
2. Relecture `leak-auditor` du code et de ce document ; écarts corrigés avant toute exécution.
3. Comptages à blanc (§ 7) : nombre de signaux seulement.
4. Contrôle sous H0 (§ 8), une fois, sur H0-a, H0-b et H0-c ; tests retirés inscrits ici avec leur raison.
5. Découverte (une exécution) ; Benjamini-Hochberg ; candidates inscrites ici.
6. Validation (une exécution) des candidates ; résultats inscrits ici.
7. S'il y a des `VALIDÉE` : décision du propriétaire pour la lecture réservée (§ 10.1), puis pré-inscription et
   démarrage de F31_ZOO (§ 10.2).

`DELIVERY_STATUS.md` et `PLAN_DE_TRAVAIL.md` seront mis à jour par le chantier qui codera le moteur.

## Historique

- 2026-10-10 : protocole déclaré avant tout code, tout comptage et toute exécution (branche `recherche/zoo`) ;
  aucun calcul ; puissance du § 11 tirée d'hypothèses déclarées et de documents publiés.
- 2026-10-11 : relecture leak-auditor, aucun résultat vu. Forme (a) décidée sur l'excès `μ*` au-delà de la dérive de
  la même paire, avec condition jointe sur le rendement absolu (l'ancien § 5.3 « contre zéro » est supprimé) ; forme
  (b) : référence d'exposition égale pour les règles d'exposition variable ; contrôle sous H0 étendu (H0-b à dérive
  constante, bloquant ; H0-c à régimes, descriptif ; troncature qui garde le prix positif) ; garde-fous de concentration
  par paire ; causalité renforcée (50 instants et un échantillon de signaux, pivots confirmés, Ichimoku, bougies
  agrégées datées par leur dernière bougie 1 h, séries externes sur `available_at`) ; précisions (trous, durée nulle,
  entrée après la fin de l'étape, entrée dans le top 40 pendant un état) ; Benjamini-Yekutieli en descriptif ;
  résultats aussi restreints aux paires éligibles à F31 ; données du zoo fusionnées sur `main` (`ce65673`) branchées :
  257 tests au lieu de 247 (8 familles ajoutées, 9 lignes venues de l'attente, CH06, CH07, CH09, CA14 et AM05 retirés) ;
  comptage FINAL_TEST (10 essais) ajouté.
- 2026-10-11 (deuxième relecture, aucun résultat vu) : unité de `μ*` précisée (rendement quotidien rapporté à la valeur
  de début de journée, test exigé sous dérive constante) ; RO02 et RO03 comparés à un portefeuille de même composition
  moyenne ; MR19 et XS24 exécutés à la 1re bougie 1 h après le signal, sortie 24 h plus tard ; EV02 : référence des
  paires cotées depuis plus de 180 jours le même jour, contrôles de correspondance des annonces au comptage à blanc,
  calendrier réel dans le contrôle H0, descriptif seulement dans F31 ; CA09 à CA11 : entrée et sortie bornées par
  l'heure de publication (`zoneinfo`), contrôle des dates au comptage à blanc ; `m` de VO11 sur la bougie 23:00-24:00 ;
  formulation unique pour SE01 et limite de l'historique d'alternative.me ; TR48 calculé sur le passé seulement ;
  DE07 marqué cousin de DE05 et DE06.
- 2026-10-11 (décisions de mise en œuvre avant tout résultat, après le codage des familles ; aucun rendement réel lu) :
  CH02, CH11, CH12, CH15 et CH16 passent `IMPOSSIBLE` (raison 7 : leur définition TA-Lib exige un écart d'ouverture,
  comme CH06, CH07, CH09, CH14 et CH18 ; ils seraient aveugles sous H0) ; AN05 retiré comme doublon de ST05 (zone OTE
  62 % contre Fibonacci 61,8 %, « déjà vu : ST05 ») ; **239 tests** (207 de forme (a), 32 de forme (b)), DEVELOPMENT au
  plus 873 + 239 + `m` ; libellé de PF05 corrigé (mensuel) ; conventions déclarées au § 12 : fin de journée ou de
  séance, sortie unique par position, sens de RG07, paires absentes du magasin 1 h.
