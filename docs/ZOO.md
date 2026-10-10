# Zoo des stratégies : protocole pré-inscrit (déclaré le 2026-10-10, avant tout code, tout comptage et toute exécution)

Ce protocole mesure, avec **une seule méthode fixée ici**, les 124 stratégies `À_TESTER (zoo)` du catalogue
[ZOO_CATALOGUE.md](ZOO_CATALOGUE.md), soit **247 tests** (une stratégie × une unité de temps). Il est écrit avant tout
code : un autre chantier codera le moteur après relecture. **Aucun calcul n'a été fait pour l'écrire** ; les ordres de
grandeur du § 11 viennent d'hypothèses déclarées et de documents déjà publiés, jamais de données lues pour l'occasion.

Rien ici n'annonce une rentabilité. Le résultat le plus probable, au vu des 873 essais déjà faits et de la puissance
du § 11, est **zéro stratégie validée** ; ce résultat dira « aucune stratégie connue n'a un avantage de cette taille »,
jamais « aucune n'a d'avantage ».

## 0. En bref, pour le propriétaire

- **Quoi.** Toutes les stratégies connues qui ne demandent que des bougies Binance ou des données déjà en magasin :
  croisements de moyennes, Donchian, Ichimoku, MACD, RSI, Bollinger, chandeliers japonais, Fibonacci, pivots, OBV,
  facteurs entre cryptos, rotation, liens avec le S&P 500, l'or et les taux, cycles, parité de risque…
- **Comment.** Une seule règle pour toutes : achat à la clôture **suivant** le signal, frais taker (central et
  défavorable), deux formes de mesure seulement (une règle de transaction contre zéro, ou un portefeuille contre
  l'univers). Univers : le **top 40 à date**, cryptos mortes comprises.
- **Deux étapes.** Découverte sur 2019-2022 (contrôle des fausses découvertes sur les 247 tests à la fois), puis
  validation sur 2023 → mi-2025 des seules candidates, avec garde-fous. La période réservée n'est pas lue.
- **Avant tout.** Le même code tourne une fois sur 200 marchés synthétiques où rien n'est prévisible : une mesure qui
  y crie trop souvent « candidate » est retirée sans compter d'essai.
- **Ensuite.** Les validées : une lecture de la période réservée (consultation n° 5, sur décision du propriétaire),
  puis un test en direct unique, **F31_ZOO**.
- **Honnêtement.** Le zoo est un **filet à grosses mailles** : il ne voit qu'un avantage net d'environ 0,4 % par jour
  d'exposition ou 2 % par semaine sur l'univers (§ 11). Les avantages publiés sont plus petits.

## 1. Périmètre

- **Dans le zoo** : les lignes `À_TESTER (zoo)` du catalogue, aux unités qui y sont écrites ; liste exacte au § 12.
- **En attente de données** (22 lignes, § 13) : branchées par un autre chantier, avec la même méthode, quand les
  données existeront ; leur pré-inscription précisera leurs propres périodes **avant** tout téléchargement.
- **Hors moteur** (3 lignes, § 14) : LSTM, Transformer et DVOL ; un protocole propre est nécessaire.
- Rien d'autre n'est mesuré. Aucune variante n'est ajoutée après lecture d'un résultat ; une idée nouvelle est un
  nouveau protocole, compté à part.

## 2. Données et univers

- **Bougies 1 h** du magasin long (`<racine>/long_history`, archives officielles de Binance, 262 paires) : toutes les
  paires passées par le top 40 à date et les 40 paires de recherche. Bougies **4 h** et **journalières** agrégées de
  ces bougies 1 h, alignées sur 00:00 UTC, **complètes seulement** (4 et 24 bougies 1 h), comme le criblage K.
  Champs utilisés : ouverture, plus haut, plus bas, clôture, volume en devise de cotation, volume acheteur au marché,
  nombre de transactions, `available_at`.
- **Univers** : le **top 40 à date** de `research/pit_universe.py` (`UNIVERSE_PIT.md`) : au 1er de chaque mois, les 40
  paires USDT de plus forte médiane de volume sur les 30 jours précédents, paires retirées de la cote comprises,
  exclusions structurelles et haram du recensement appliquées ; les cryptos `NON_RELEVE` entrent dans cette mesure de
  recherche, jamais dans une liste de trading. Les 8 paires sans historique 1 h (AION, ANT, GAL, JST, LEVER, SC, SKL,
  SUN) sont absentes : déclaré.
- **Appartenance** : un signal n'ouvre une position que si la paire est membre du top 40 **du mois de la décision**.
  Une position ouverte va jusqu'à sa sortie même si la paire quitte le top 40.
- **Historique de chauffe** : les indicateurs se calculent sur tout l'historique de la paire depuis sa cotation
  (depuis août 2017 pour les plus anciennes) ; un signal n'existe que si la fenêtre de l'indicateur est entièrement
  disponible. La chauffe ne lit que des prix antérieurs à la décision.
- **Paire retirée de la cote** pendant une position : vente à la dernière clôture connue (`COTATION_ARRETEE`), comme
  `LIGNES_DE_TENDANCE.md`. Limite déclarée : la toute fin d'une paire retirée, souvent la pire, est perdue.
- **Trous de données** : une bougie 4 h ou journalière incomplète n'existe pas ; aucune décision sur une bougie
  manquante ; une position est valorisée à sa dernière clôture connue.
- **Données externes** (AM01 à AM05) : séries `macro` (SPY, taux à 10 ans, M2SL) et `gold_gld` du magasin de contexte
  (`CONTEXTE.md`), lignes `HISTORIQUE`, avec un retard prudent : une valeur du jour de séance `d` n'est connue qu'à
  `d + 1` 00:00 UTC ; M2 mensuel : 45 jours après la fin du mois.
- **Aucune lecture après le 2025-06-30 23:59:59 UTC** : le moteur coupe toutes les séries à
  `research/protocol.FROZEN_DEVELOPMENT_END` dès la lecture et refuse toute borne plus tardive (testé en falsifiant
  ce qui suit).

## 3. Les deux étapes

| Étape | Décisions | Rôle |
|---|---|---|
| **Découverte** | du 2019-01-01 00:00 au 2022-12-31 23:59 UTC | les 247 tests ; contrôle du taux de fausses découvertes |
| **Validation** | du 2023-01-01 00:00 au 2025-06-30 23:59 UTC | seulement les `CANDIDATE` |

- Une position encore ouverte à la fin d'une étape est **vendue à la dernière clôture de l'étape**, frais compris :
  aucun prix postérieur n'entre dans l'étape.
- La validation commence sans position. Une règle d'**état** (par exemple « clôture > SMA 200 ») ouvre une position à
  la première décision où l'état est vrai ; une règle d'**événement** (un croisement, une figure) attend un nouvel
  événement.
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

- **Signal** à la clôture de la bougie `t` de l'unité du test, calculé avec les seules bougies dont `available_at` est
  antérieur à la décision.
- **Entrée** au prix de **clôture de la bougie `t + 1`** (« clôture suivante »).
- **Sortie** : la règle de sortie de la stratégie, évaluée à chaque clôture `u`, exécutée à la clôture de `u + 1` ; ou,
  au plus tard, à la clôture de la `N`-ième bougie après l'entrée (`N` au § 12) ; ou à la fin de l'étape. Toutes les
  sorties se font à la clôture (aucun stop au toucher dans la bougie, donc aucune ambiguïté d'ordre des prix).
- **Une position par paire et par test** : un signal qui arrive pendant une position du même test sur la même paire
  est ignoré (compté).
- **Mise égale** par position, aucun levier, le reste en USDT sans rendement.
- **Statistique de décision : rendement net par jour d'exposition.** Chaque position est valorisée à chaque clôture
  1 h ; pour chaque position `i` et chaque journée UTC `d`, `r(i, d)` = rendement simple net de la position sur la
  journée (frais d'entrée le jour de l'entrée, frais de sortie le jour de la sortie) et `e(i, d)` = fraction de la
  journée détenue. Statistique : `μ = Σ r / Σ e` (en % par jour d'exposition).
- **Intervalle** : blocs de **7 jours calendaires** consécutifs ; dans chaque bloc, somme des `r` et somme des `e` ;
  variance du ratio par la formule de `research/intervals.calendar_mean_ci` (covariance entre blocs voisins ajoutée
  quand elle est positive, jamais retranchée), le nombre d'événements étant remplacé par l'exposition ; quantile de
  Student à `B − 1` degrés de liberté (`B` = blocs avec exposition). Comme la statistique est une valorisation
  quotidienne, deux blocs voisins seulement partagent une même journée de marché : 7 jours suffisent quelle que soit
  la durée des positions. Fonction à écrire et à tester par le chantier de code (sous la nulle simulée, 2 à 3 % de
  bornes basses > 0 pour 2,5 % visés, comme `calendar_mean_ci`).
- **Descriptif** (jamais décisif) : rendement net **par transaction**, avec un intervalle par blocs de
  max(7, 2 × la plus longue tenue observée) jours ; nombre de transactions, durée moyenne, part gagnante, résultats
  par année, signaux ignorés (position ouverte, paire non membre).
- **Minimums** : au moins 100 transactions et 20 blocs avec exposition dans l'étape, sinon `INSUFFISANT`.

### 4.2 Forme (b) : facteur ou portefeuille

- **Portefeuille long seulement**, rééquilibré à la fréquence standard de la stratégie (§ 12) : **hebdomadaire** pour
  les facteurs et les règles de marché (décision le lundi 00:00 UTC sur les clôtures journalières jusqu'au dimanche,
  échanges à la **clôture du lundi**, tenue jusqu'à la clôture du lundi suivant, comme `XSECTION.md`) ; **quotidienne**
  pour AM01 à AM05 (décision à 00:00 UTC, échange à la clôture du jour) ; **mensuelle** pour PF05 à PF09 (premier lundi
  du mois).
- **Facteurs** (XS) : parmi les membres pour lesquels la variable est calculable, le **quintile** du côté de la
  publication citée (⌊n / 5⌋ paires, au moins 3, soit 8 sur 40), à poids égaux.
- **Référence** : l'**univers à poids égaux** de la stratégie, rééquilibré à la même fréquence, mêmes frais : les mêmes
  membres éligibles pour un facteur ; le top 40 du moment pour une règle de marché ; BTC conservé pour AM01 à AM05
  (l'univers de ces stratégies est BTC) ; BTC et ETH à parts égales pour RO02 ; pour PF05, qui **est** l'univers à
  poids égaux, la référence est le même panier laissé flotter (acheté à poids égaux au premier lundi de chaque
  trimestre, sans rééquilibrage jusqu'au suivant). Ces trois exceptions sont déclarées ici.
- **Frais** sur la rotation : somme des variations de poids × (frais + écart et glissement de la paire), à chaque
  rééquilibrage, pour la stratégie comme pour la référence.
- **Statistique de décision** : **excès net hebdomadaire** = rendement net de la stratégie − rendement net de la
  référence, semaine du lundi au lundi (les séries quotidiennes et mensuelles sont valorisées chaque lundi).
- **Intervalle** : `calendar_mean_ci` sur la série hebdomadaire, blocs de **28 jours** (4 semaines).
- **Minimum** : 20 blocs, sinon `INSUFFISANT`. Découverte : 52 blocs ; validation : 32.
- **Descriptif** : rendement, volatilité, perte maximale, rotation, exposition moyenne, par année.

### 4.3 p-valeur et sens

- Pour chaque test et chaque scénario : `t` = estimation / erreur type de l'intervalle ; **p bilatérale** de Student à
  `B − 1` degrés de liberté.
- **Sens** : CSI est acheteur. Si l'estimation est ≤ 0 dans l'un des deux scénarios, `p = 1`.
- **p du test** = max(p central, p défavorable) : une découverte doit tenir dans les deux scénarios (test
  intersection-union, valide). En pratique, c'est la p du scénario défavorable.

## 5. Décisions

### 5.1 Découverte → `CANDIDATE`

1. Procédure de **Benjamini-Hochberg au niveau q = 10 %** sur les p des tests exécutés (au plus 247) : tri croissant
   `p(1) ≤ … ≤ p(M)`, `k` = plus grand rang tel que `p(k) ≤ 0,10 × k / M` ; les `k` premiers sont rejetés.
2. Un test rejeté est **`CANDIDATE`** si, en plus, son estimation reste **> 0 dans les deux scénarios sans sa meilleure
   année civile** de découverte (celle dont la somme des rendements nets centraux est la plus forte). Ce garde-fou
   retire des candidates sans changer le seuil : il ne peut que réduire les fausses découvertes.
3. Les autres : `NON_CANDIDATE` (ou `INSUFFISANT`).

La procédure de Benjamini-Hochberg contrôle le taux de fausses découvertes sous indépendance ou dépendance positive ;
nos tests partagent le facteur de marché (dépendance surtout positive) mais pas tous (tendance contre retour à la
moyenne). La garantie réelle est celle que mesure le contrôle sous H0 (§ 8), pas le théorème.

### 5.2 Validation → `VALIDÉE`

Avec `m` = nombre de `CANDIDATE` (si `m = 0`, le zoo s'arrête : aucun essai de validation) :

1. intervalle **bilatéral au niveau 1 − 0,05 / m** de la statistique de décision **entièrement > 0** en central **et**
   en défavorable ;
2. **sans sa meilleure année** de validation (parmi 2023, 2024 et le premier semestre 2025, celle dont la somme des
   rendements nets centraux est la plus forte), la condition 1 tient encore, au même niveau ; minimum de blocs abaissé
   à 12 pour ce seul garde-fou (forme (b) : sans 2024, il reste environ 19 blocs de 4 semaines) ;
3. au moins **2 années sur 3** (2023, 2024, 2025-S1) ont une estimation centrale > 0.

Sinon : `NON_VALIDÉE` (ou `INSUFFISANT`). Une `VALIDÉE` n'est pas « rentable » : elle a gagné, net de frais, sur une
période déjà vue par le programme ; seule la suite (§ 10) peut la confirmer.

### 5.3 Ce que « contre zéro » veut dire (forme (a))

La forme (a) décide contre zéro, pas contre un achat au hasard : la **dérive du marché** y entre. Une règle de
tendance exposée surtout pendant les hausses peut passer sans aucun talent de choix des moments (piège décrit dans
`METEO_MARCHE.md` § 6). Deux protections seulement : la découverte (2019-2022) contient la grande baisse de 2022, et
les placebos du § 6 disent, en descriptif, ce qu'un achat au hasard de même durée aurait gagné. Une `VALIDÉE` de forme
(a) dont l'excès sur les placebos est nul sera présentée comme telle : un pari sur la dérive.

## 6. Placebos : descriptifs seulement

Leçon de `PRICE_ACTION.md` (§ 5.5, § 11) et de `FIGURES_HISTORIQUE.md` : des placebos tirés juste avant un signal
portent le chemin qui l'a formé et faussent l'excès. Ils ne décident donc de rien.

- **Forme (a)** : pour chaque transaction, 20 entrées placebo sur la **même paire**, de **même durée** (en bougies),
  tirées uniformément parmi les bougies de la **même étape** où la paire est membre (jamais dans une fenêtre liée au
  signal), mêmes frais ; graine dérivée du test et de la transaction. Rapporté : statistique des placebos et excès
  (signal − placebos), avec intervalle.
- **Forme (b)** : 1 000 portefeuilles tirés au hasard parmi les mêmes membres, même taille, mêmes dates ; rapporté :
  rang centile de la stratégie.

## 7. Contrôles de causalité et garde d'exécution

- **Causalité, par test** : signaux et poids recalculés à 8 instants tirés au hasard avec les seules bougies
  antérieures, puis avec un futur falsifié : identiques au calcul complet. **Mutation** : un signal qui lit la clôture
  de la bougie suivante doit être détecté, pour chaque famille de code (moyennes, oscillateurs, figures, pivots,
  facteurs, appartenance au top 40).
- **Définitions** : chaque indicateur testé contre des valeurs calculées à la main ; chandeliers contre TA-Lib si la
  bibliothèque est installée, sinon contre des exemples écrits à la main d'après sa documentation.
- **Code commité exigé** ; empreintes SHA-256 des données lues inscrites ; une seule exécution par étape.
- **Relecture** `leak-auditor` avant le contrôle sous H0.
- **Comptages à blanc** sur la découverte réelle (nombre de signaux par test, **aucun rendement**) : un test qui n'a
  pas 100 transactions possibles ou 20 blocs est retiré `INSUFFISANT_PAR_CONSTRUCTION`, **0 essai** (attendu pour
  certaines figures exigeant un écart entre corps, CH06 et CH07).

## 8. Contrôle sous H0 (obligatoire, lancé une fois, avant toute exécution sur données réelles)

### 8.1 Marché synthétique (paramètres fixés ici a priori, aucune estimation sur données réelles)

- **200 répliques indépendantes** (graine `SeedSequence(20261012)`, une sous-graine par réplique et par paire).
- Bougies 1 h du 2017-08-17 au 2025-06-30 pour **100 paires** : BTC et ETH dès le début, 98 altcoins cotés à des dates
  tirées (10 avant 2018, les autres uniformément de 2018-01 à 2024-12), pour reproduire un univers qui passe d'une
  dizaine à 40 membres.
- **Rendements simples martingales** : `r(i, t) = σ(i, t) × s(h) × z(i, t)`, avec `E[r | passé] = 0` exactement.
  - Facteur commun : `z(i, t) = λ(i) u(t) + √(1 − λ(i)²) e(i, t)`, `u` = choc de BTC (λ = 1), ETH λ = 0,85, altcoins
    λ uniforme dans [0,55 ; 0,85] (corrélations de 0,3 à 0,7 avec BTC) ;
  - `u` et `e` : **Student à 4 degrés** de variance 1 (queues épaisses partout, leçon de `METEO_MARCHE.md` § 7.5),
    tronqués symétriquement à ±20 (moyenne nulle conservée) ;
  - `σ` : **GJR-GARCH(1, 1)** sur les rendements propres de la paire (α = 0,04 ; γ = 0,04 pour les baisses, **effet de
    levier** ; β = 0,93), volatilité horaire moyenne : BTC 0,6 %, ETH 0,75 %, altcoins log-uniforme dans [0,8 % ; 1,5 %] ;
  - `s(h)` : profil déterministe heure × jour (plus agité vers 14:00 UTC les jours ouvrés, plus calme le week-end),
    moyenne 1 ;
- **Bougies** : ouverture = clôture précédente ; plus haut = max(ouverture, clôture) × (1 + 0,5 σ s |w₁|), plus bas =
  min(ouverture, clôture) × (1 − 0,5 σ s |w₂|), `w` gaussiens indépendants.
- **Volume** en devise de cotation : niveau de popularité de la paire (log-AR(1) journalier, φ = 0,995, innovation
  0,05, niveaux initiaux log-normaux d'écart-type 1) × `s(h)` × (0,5 + |z|) × facteur journalier log-normal
  (σ = 0,5, moyenne 1) : lié à l'ampleur, jamais au sens futur. Part acheteuse au marché = 0,5 + 0,08 tanh(z) + bruit
  (0,03), bornée à [0,05 ; 0,95] (liée au sens **de la même bougie** seulement). Nombre de transactions = volume / taille
  moyenne log-normale.
- **Retraits de la cote** décidés sur le seul passé : au 1er de chaque mois, une altcoin dont le rendement sur 90 jours
  est inférieur à −75 % est retirée avec une probabilité de 0,3, à une date tirée dans le mois ; vente à la dernière
  clôture.
- **Top 40 à date** : la fonction `pit_universe.membership`, inchangée, sur les journées synthétiques.
- **Séries externes** (AM01 à AM05) : les **vraies** séries du magasin, inchangées. Elles ne portent aucune
  information sur des cryptos synthétiques indépendantes : la nulle reste exacte, et leur calendrier et leur persistance
  sont réels.
- Le **pipeline complet du zoo**, sans aucune différence (signaux, appartenance, formes, intervalles, Benjamini-Hochberg,
  garde-fous), tourne sur chaque réplique, découverte puis validation.

### 8.2 Critères (calculés à frais nuls : la nulle est alors exactement au bord, `E[net] = 0`)

Sur une martingale, une règle causale a un gain brut d'espérance nulle ; avec des frais, son net serait négatif et le
contrôle trop facile. Le contrôle se fait donc **sans frais**, au bord de l'hypothèse nulle (comme le descriptif « R
brut » de `PRICE_ACTION.md` § 11.4).

1. **Par test (étalonnage)** : part des 200 répliques où l'estimation est > 0 avec p bilatérale ≤ 0,05 (valeur
   nominale 0,025), en découverte **et** en validation : **≤ 0,05** chacune. Sinon le test est retiré
   `MESURE_NON_VALIDE`, **0 essai**.
2. **Global** : sur les tests restants, part des 200 répliques qui produisent **au moins une `CANDIDATE`** :
   **≤ 0,10**. Sinon **le zoo n'est pas exécuté** (`INSTRUMENT_NON_VALIDE`), 0 essai. Rapporté aussi : part des
   répliques avec au moins une `VALIDÉE`, et nombre moyen de fausses candidates.
3. **Puissance mesurée** (avec les vrais frais) : pour chaque test `s` et chaque effet `g`, on ajoute à chaque position
   de `s` un rendement de `δ` par jour d'exposition (forme (a)) ou à son portefeuille `δ` par semaine (forme (b)), les
   signaux restant ceux de la réplique sans effet, les autres tests restant sous la nulle ; `δ` = `g` + le coût central
   moyen de `s` par jour d'exposition (ou par semaine) mesuré sur la même réplique, pour que **l'effet net central vrai
   vaille `g`**. Effets : forme (a) `g` ∈ {+0,10 ; +0,25 ; +0,50} % par jour d'exposition ; forme (b)
   `g` ∈ {+0,25 ; +0,5 ; +1 ; +2} % par semaine. Rapporté : part des répliques où `s` devient `CANDIDATE` (Benjamini-
   Hochberg recalculé avec `s` modifié), puis `VALIDÉE`.
4. **Test aveugle** : un test dont la puissance de découverte au plus grand effet (+0,50 %/jour ou +2 %/semaine) est
   **< 0,10** est retiré `INSTRUMENT_AVEUGLE`, **0 essai** : il ne pourrait rien voir.
5. Résultats rapportés aussi avec les frais réels (descriptif).

### 8.3 Garde

- Lancé **une seule fois** (`csi zoo controle-h0`), fichier `reports/ZOO-H0-<commit>/criteres.json`, empreinte inscrite
  dans le code du zoo ; l'exécution réelle relit ce fichier et refuse s'il manque ou a changé.
- **Pas de seconde itération.** Seule exception, une fois au plus (comme `PRICE_ACTION.md` § 5.3) : un écart prouvé
  entre le code et ce texte, démontré par un test unitaire écrit avant la relance ; les deux passages sont publiés.
- Si le temps de calcul de 200 répliques dépasse ce que le PC permet, le chantier de code déclare **avant** le passage
  une réduction (au moins 100 répliques), jamais après.

## 9. Comptage des essais

- Programme avant le zoo : **873 essais** sur DEVELOPMENT, **4 consultations** de la période réservée (registre lu le
  2026-10-10).
- **Découverte** : chaque test exécuté compte **1 essai DEVELOPMENT** (une exécution, `n_trials` = nombre de tests
  exécutés, au plus 247). Les tests retirés par le contrôle sous H0 ou par les comptages à blanc : 0 essai.
- **Validation** : **1 essai de plus** par `CANDIDATE` (une exécution, `n_trials` = `m`).
- **Période réservée** : 1 essai `FINAL_TEST` par `VALIDÉE` lue.
- **F31_ZOO** : 1 essai au registre des tests en direct.
- Au plus : 873 + 247 + m essais sur DEVELOPMENT.

## 10. Après la validation

### 10.1 Lecture unique de la période réservée (consultation n° 5)

- Seulement s'il existe au moins une `VALIDÉE`, et **sur décision du propriétaire**, comme les quatre consultations
  précédentes ; `--i-understand-final-test`, consultation enregistrée (`final_test_consultations`).
- Période : du 2025-07-01 à la dernière journée UTC complète avant la lecture (date inscrite au registre avant la
  lecture). Mêmes règles, même code, mêmes frais, toutes les `VALIDÉE` ensemble (`k` stratégies).
- Verdict par stratégie : `CONFIRMÉE` si l'intervalle au niveau 1 − 0,05 / k est > 0 dans les deux scénarios ;
  `INFIRMÉE` si l'IC95 est < 0 dans les deux ; sinon `NON_CONCLUANTE`.
- Rappel : la période réservée a déjà été lue quatre fois ; elle n'est plus vierge pour le programme.

### 10.2 Test en direct unique `F31_ZOO`

- Toutes les `VALIDÉE` non `INFIRMÉE`, **ensemble**, dans un seul test pré-inscrit dans `FORWARD_TESTS.md` par le
  chantier de code avant son démarrage, conforme au cadre commun (gel des empreintes, journal chaîné, frais de
  `forward/costs.py`, aucun message, aucun ordre).
- Univers : top 40 à date **et** liste halal figée au démarrage (les paires `NON_RELEVE` en sont exclues).
- Durée : **52 semaines**, une seule évaluation à la fin ; revues descriptives à 12 et 26 semaines, sans changement de
  règle. C'est un écart déclaré au cadre commun (12 semaines), imposé par la puissance (§ 11.3).
- Verdict par stratégie : `SUPERIEUR_A_ZERO` si l'intervalle au niveau 1 − 0,05 / k est > 0 dans les deux scénarios ;
  `INFERIEUR_A_ZERO` si l'IC95 est < 0 dans les deux ; `INSUFFISANT` sous 30 transactions ou 20 blocs (forme (a)) ou
  12 blocs (forme (b)) ; sinon `NON_DEMONTRE`.
- Rien ne passe en Demo ou en réel sur ce seul verdict : décision du propriétaire.

## 11. Puissance attendue (ordres de grandeur, hypothèses déclarées, non mesurées)

Le contrôle sous H0 (§ 8.2, point 3) mesurera la vraie puissance de chaque test ; ces chiffres ne servent qu'à dire
d'avance ce que le zoo peut voir.

**Hypothèses** : volatilité journalière d'une altcoin ≈ 4,5 % (70 à 100 % par an en 2019-2022), de BTC ≈ 3,5 % ;
corrélation moyenne des rendements journaliers entre altcoins ≈ 0,6 ; écart-type hebdomadaire de l'excès d'un
quintile de 8 paires sur l'univers ≈ 6,4 % (déduit de `XSECTION.md` : demi-largeur de l'IC95 ≈ 0,7 %/semaine sur 339
semaines) ; environ 247 tests, dont très peu porteraient un vrai effet.

**Seuil de découverte.** Avec 247 tests et un seul vrai effet, Benjamini-Hochberg rejette sous p ≈ 0,10 / 247 ≈
0,0004 (bilatéral), soit |t| ≈ 3,5. Une puissance de 80 % exige donc un effet d'environ **4,4 erreurs types**. Avec
une dizaine de vrais effets, le seuil remonte vers p ≈ 0,004 (|t| ≈ 2,9) : environ 3,7 erreurs types.

### 11.1 Forme (a), découverte (1 461 jours)

| Type de test | Exposition typique | Erreur type de `μ` | Effet net vu à 80 % |
|---|---|---|---|
| Règle d'état journalière (tendance), ≈ 16 positions par jour | 40 % | ≈ 0,10 %/jour : variance d'une journée ≈ σ² (k + k(k − 1) ρ) = 0,045² × 160, sur 1 461 jours et 23 000 jours d'exposition | ≈ 0,43 %/jour d'exposition |
| Règle d'état 1 h ou 4 h | 40 à 50 % | du même ordre (le facteur de marché domine) | ≈ 0,4 à 0,5 %/jour |
| Événement 1 j tenu 10 jours (≈ 1 000 transactions) | faible | ≈ 0,07 %/jour (14 % par transaction, grappes de 2 à 3 paires) | ≈ 0,3 %/jour, soit ≈ 3 % net par transaction |
| Événement 1 h tenu 10 heures (≈ 30 000 transactions) | faible | ≈ 0,11 %/jour (3,8 % par transaction, grappes de 4) | ≈ 0,5 %/jour, soit ≈ 0,2 % net par transaction (0,6 % brut avec les frais défavorables) |

Puissance de découverte d'un vrai effet de forme (a) (erreur type 0,10 %/jour) : **+0,10 %/jour : ≈ 0** ;
**+0,25 %/jour : ≈ 0,15** ; **+0,50 %/jour : ≈ 0,9**. Pour comparaison, l'ensemble de Donchian de `TREND_DAILY.md`
gagnait de l'ordre de 0,2 % par jour d'exposition, dérive comprise.

**Validation** (913 jours, erreur type × 1,27) : pour `m` = 1, effet vu à 80 % ≈ 2,8 erreurs types ≈ 0,35 à
0,5 %/jour ; `m` = 5 ≈ 0,45 à 0,65 %/jour ; le garde-fou « sans la meilleure année » (≈ 1,5 an) l'alourdit encore.
Probabilité d'être `VALIDÉE` pour un vrai +0,50 %/jour : ≈ 0,75 ; pour +0,25 %/jour : ≈ 0,05.

### 11.2 Forme (b), découverte (208 semaines)

| Type de test | Écart-type de l'excès | Erreur type | Effet vu à 80 % |
|---|---|---|---|
| Facteur (quintile de 8 contre 40) | ≈ 6,4 %/semaine | ≈ 0,44 %/semaine | ≈ 1,9 %/semaine |
| Règle de marché (investi ou USDT contre l'univers), dehors ≈ 40 % du temps | ≈ 12 % × √0,4 ≈ 7,6 % | ≈ 0,53 % | ≈ 2,3 %/semaine |
| BTC détenu ou non (AM), dehors ≈ 50 % | ≈ 6,4 % | ≈ 0,44 % | ≈ 1,9 %/semaine |
| Allocation mensuelle (PF) contre l'univers | ≈ 3 à 6 % | ≈ 0,2 à 0,4 % | ≈ 1 à 1,8 %/semaine |

Puissance de découverte d'un facteur : **+0,25 %/semaine : ≈ 0** ; **+0,5 : ≈ 0,01** ; **+1 : ≈ 0,1** ; **+2 :
≈ 0,85**. Validation (130 semaines, erreur type ≈ 0,56 %) : effet vu à 80 % ≈ 1,6 %/semaine (`m` = 1) à 1,9 (`m` = 5),
sans la meilleure année ≈ 2 à 2,5 %/semaine. Un excès de +2 %/semaine sur l'univers ferait plus que doubler sa
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
  par semaine). Sur 12 semaines, la puissance serait quasi nulle : d'où les 52 semaines. **F31 dira surtout si une
  `VALIDÉE` s'effondre** (`INFERIEUR_A_ZERO`) ; il ne confirmera qu'un effet grand.

### 11.4 Conséquence déclarée

Le zoo est exhaustif par le nombre de stratégies, pas par la finesse : il répond à « une stratégie connue a-t-elle un
avantage énorme et stable ? ». Un « rien » dira « aucune n'a d'avantage plus grand qu'environ 0,4 % par jour
d'exposition, ou 2 % par semaine sur l'univers » ; les effets plus petits restent ni démontrés ni exclus. Réduire la
famille (moins d'unités de temps) aurait gagné peu (le seuil passe de |t| ≈ 3,5 à 3,3 pour 100 tests) au prix de
l'exhaustivité demandée.

## 12. Tableau des tests du zoo

Conventions : « croise au-dessus » = vrai à `t` et faux à `t − 1` ; **état** = position ouverte tant que la condition
est vraie (entrée quand elle devient vraie, ou à la première décision de l'étape) ; **événement** = une entrée par
déclencheur. `N` = plafond de tenue en bougies de l'unité ; « — » = pas de plafond (sortie par la règle ou fin
d'étape). Plafonds a priori : 20 bougies pour les règles de retour à la moyenne dotées d'une sortie, 10 bougies pour
les événements sans sortie (durée de Marshall, Young et Rose, 2006). Paramètres : ceux du catalogue.

### 12.1 Forme (a) : règles de transaction (219 tests)

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
| TR22 | 1h, 4h, 1j | état | clôture > haut du nuage applicable | clôture ≤ haut du nuage | — |
| TR23 | 1h, 4h, 1j | événement | Tenkan croise au-dessus de Kijun, clôture > haut du nuage | Tenkan < Kijun | — |
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
| MR01 | 1j | événement | RSI 2 < 10 et clôture > SMA 200 | clôture > SMA 5 | 20 |
| MR02 | 1h, 4h, 1j | événement | RSI 14 croise au-dessus de 30 | RSI 14 > 70 | 20 |
| MR03 | 1h, 4h, 1j | événement | clôture de `t − 1` < bande basse, clôture de `t` > bande basse (BB 20, 2) | clôture ≥ bande médiane | 20 |
| MR05 | 1h, 4h, 1j | événement | %K croise au-dessus de %D avec %K < 20 (14, 3, 3) | %K > 80 | 20 |
| MR06 | 1h, 4h, 1j | événement | %R 14 croise au-dessus de −80 | %R > −20 | 20 |
| MR07 | 1h, 4h, 1j | événement | CCI 20 croise au-dessus de −100 | CCI > +100 | 20 |
| MR09 | 1h | événement | écart relatif à la VWAP du jour < −2 écarts-types (168 bougies précédentes) | clôture ≥ VWAP du jour, ou dernière bougie de la journée UTC | 24 |
| MR11 | 1h, 4h, 1j | événement | rendement de `t` < −2 × écart-type des 20 rendements précédents | aucune | 10 |
| MR12 | 1j | événement | 3 clôtures consécutives en baisse et clôture > SMA 200 | clôture > SMA 5 | 20 |
| VO01 | 1j (exécution 1h) | événement | 1re clôture 1 h du jour > ouverture du jour + 0,5 × (plus haut − plus bas de la veille) | clôture de la dernière bougie 1 h de la journée UTC | 24 h |
| VO02 | 1j (exécution 1h) | événement | 1re clôture 1 h > ouverture du jour + 0,5 × amplitude Dual Thrust (N = 4) | idem | 24 h |
| VO03 à VO05 | 1h | événement | 1re clôture 1 h de la séance au-dessus du plus haut de la 1re heure de la séance | dernière bougie 1 h de la séance | — |
| VO06, VO07 | 1j | événement | clôture > plus haut d'un jour NR4 (NR7) dans les 2 jours qui le suivent | aucune | 10 |
| VO08 | 1h, 4h, 1j | événement | fin de la compression (BB 20/2 sort de KC 20/1,5 après au moins une bougie dedans) et momentum de Carter > 0 | 2 baisses consécutives du momentum | 20 |
| VO09 | 1h, 4h, 1j | événement | clôture > clôture précédente + 1 × ATR 14 | aucune | 10 |
| CH01 à CH13, CH15 à CH17 (16 figures) | 1h, 4h, 1j | événement | figure TA-Lib haussière à `t`, tendance préalable du catalogue | aucune | 10 |
| CH20 | 1h, 4h, 1j | événement | 1re bougie Heikin Ashi haussière sans mèche basse après une baissière | 1re HA baissière | — |
| CH21 | 1h, 1j | événement | 2e brique Renko haussière après une baissière (boîte ATR 14 journalier du mois) | 1re brique baissière | — |
| ST01 | 4h, 1j | état | dernier pivot haut ZigZag > le précédent et dernier pivot bas > le précédent | un nouveau pivot bas plus bas que le précédent | — |
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
| CA13 | 1j | événement | 1re clôture journalière après une nouvelle lune (éphémérides calculées) | 1re clôture après la pleine lune suivante | — |
| RG03 | 1j | état | pente filtrée (Kalman, tendance locale linéaire du log-prix, variances estimées chaque mois sur le passé) > 0 | pente ≤ 0 | — |
| RG04 | 1j | état | Hurst R/S (128 rendements) > 0,5 et rendement 20 j > 0 | condition fausse | — |
| RG05 | 1j | état | entropie de permutation (ordre 3, 64 j) dans le tiers bas de ses 365 j et rendement 20 j > 0 | condition fausse | — |
| RG06 | 1j | état | pente de la tendance de Haar (niveau 3, calculée sur le passé seul) > 0 | pente ≤ 0 | — |
| RG07 | 4h, 1j | événement | sinusoïde d'Ehlers croise au-dessus de sa version avancée | croisement inverse | — |
| AN05 | 1h, 4h, 1j | événement | dans la dernière jambe haussière ZigZag : contact de la zone 62–79 % puis 1re clôture > 62 % | clôture ≥ sommet de la jambe, ou < son creux | 20 |
| AN06 | 1h, 4h | événement | 1er retour dans un breaker haussier, clôture au-dessus de la zone | clôture sous la zone | 10 |
| AN09 | 4h, 1j | événement | clôture > sommet de la vague 1 après une vague 2 de 38,2 à 78,6 % restée au-dessus de son départ | clôture ≥ départ + 1,618 × vague 1, ou < creux de la vague 2 | 20 |
| AN13 | 1j | état | swing de Gann haussier (dernier sommet de swing de 2 bougies dépassé) | clôture sous le dernier creux de swing | — |

Total forme (a) : 24 + 6 + 3 + 2 + 39 (TR18 à TR30) + 2 + 3 + 1 (TR) ; 21 (MR) ; 13 (VO) ; 53 (CH) ; 22 (ST) ;
15 (VF) ; 1 (CA13) ; 6 (RG03 à RG07) ; 8 (AN) = **219 tests**.

### 12.2 Forme (b) : facteurs et portefeuilles (28 tests)

| ID | Fréquence | Portefeuille | Référence |
|---|---|---|---|
| TR37 | hebdomadaire | chaque membre à 1/n si son rendement sur 7 j > 0, sinon sa part en USDT | univers à poids égaux |
| TR40 | hebdomadaire | idem sur 84 j | idem |
| XS01, XS09, XS10, XS11, XS12, XS14, XS15, XS17, XS18, XS19, XS20 | hebdomadaire | quintile du côté publié (catalogue § 7) | membres éligibles à poids égaux |
| RO02 | hebdomadaire | 100 % ETH si ETHBTC > SMA 50 journalière, sinon 100 % BTC | BTC et ETH à parts égales |
| RO03 | hebdomadaire | altcoins membres à poids égaux si ≥ 75 % d'entre eux battent BTC sur 90 j, sinon 100 % BTC | top 40 à poids égaux |
| RO04 | hebdomadaire | top 40 à poids égaux si plus de la moitié clôture au-dessus de sa SMA 50, sinon USDT | idem |
| RO05 | hebdomadaire | top 40 à poids égaux si la corrélation moyenne sur 30 j est sous sa médiane des 365 j précédents, sinon USDT | idem |
| CA14 | hebdomadaire | top 40 à poids égaux de novembre à avril, USDT de mai à octobre | idem |
| RG02 | hebdomadaire | top 40 à poids égaux sauf si la probabilité filtrée de l'état le plus volatil (3 états, BTC, réestimation mensuelle comme `FACTORS.md`) dépasse 0,5 | idem |
| AM01 à AM05 | quotidienne | 100 % BTC si le signal du catalogue § 9 est vrai, sinon USDT | BTC conservé |
| PF05 | mensuelle | top 40 à poids égaux rééquilibré chaque semaine | même panier laissé flotter, renouvelé chaque trimestre |
| PF06 | mensuelle | poids ∝ 1 / volatilité journalière sur 30 j | top 40 à poids égaux |
| PF07 | mensuelle | minimum de variance long seul (covariance 90 j rétrécie de Ledoit-Wolf) | idem |
| PF09 | mensuelle | 60 % BTC, 40 % ETH | idem |

## 13. En attente de données (22 lignes du catalogue)

Même méthode que ci-dessus quand les données existeront ; chaque branchement sera pré-inscrit, avec ses propres
périodes si l'historique est plus court que 2019-2025, **avant** tout téléchargement de rendements.

| Lignes | Données manquantes |
|---|---|
| SE01, SE02 | historique de l'indice Fear & Greed (alternative.me, une requête) |
| SE04 | vues Wikipédia par crypto (table crypto → article à construire) |
| AM06, AM07, AM08 | Nasdaq 100, taux BCE des 6 devises du DXY, VIX |
| CA09, CA10, CA11 | calendriers historiques du FOMC, du CPI et du NFP (le BLS refuse les clients automatisés) |
| DE07, DE08 | archives « metrics » des membres du top 40 (depuis 2021-12 seulement) |
| DE09 | bougies des contrats trimestriels |
| OC07, OC08 | séries communautaires de CoinMetrics (offre dormante, adresses actives, NVT) |
| EV02, EV03, EV06 | table des renommages de Binance, archive des annonces de retrait, historique des piratages |
| VF13, VF14, VF15, DE13 | journaux du collecteur (F26 à F29, décision du 2026-10-10 : nouvelle pré-inscription quand la puissance atteindra 50 %) |
| RG10 | instrument de la météo non validé ; Fear & Greed |

## 14. Hors moteur (3 lignes)

| Ligne | Pourquoi |
|---|---|
| ML05 (LSTM de Fischer et Krauss) | 200 entraînements au contrôle sous H0 hors du budget du PC ; protocole ML propre avec régression linéaire de référence obligatoire (`POSITIVE_CONTROL.md` § 8) |
| ML06 (Transformer) | idem, et aucun réglage standard publié : l'architecture serait à déclarer |
| DE12 (DVOL au plus haut) | déjà pré-inscrit (`OPTIONS_PEUR.md`) ; contrôle sous H0 `INSTRUMENT_TROP_FAIBLE` : ne pas le reprendre ici |

## 15. Ordre d'exécution (rien n'est lancé par ce document)

1. Code du moteur, une fonction pure par stratégie, tests des définitions, des formes, des intervalles et de la
   causalité (mutations comprises) ; `ruff`, `mypy`.
2. Relecture `leak-auditor` du code et de ce document ; écarts corrigés avant toute exécution.
3. Comptages à blanc (§ 7) : nombre de signaux seulement.
4. Contrôle sous H0 (§ 8), une fois ; tests retirés inscrits ici avec leur raison.
5. Découverte (une exécution) ; Benjamini-Hochberg ; candidates inscrites ici.
6. Validation (une exécution) des candidates ; résultats inscrits ici.
7. S'il y a des `VALIDÉE` : décision du propriétaire pour la lecture réservée (§ 10.1), puis pré-inscription et
   démarrage de F31_ZOO (§ 10.2).

`DELIVERY_STATUS.md` et `PLAN_DE_TRAVAIL.md` seront mis à jour par le chantier qui codera le moteur.

## Historique

- 2026-10-10 : protocole déclaré avant tout code, tout comptage et toute exécution (branche `recherche/zoo`) ;
  aucun calcul ; puissance du § 11 tirée d'hypothèses déclarées et de documents publiés.
