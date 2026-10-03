# Contrôle positif de la mesure — déclaré le 2026-10-03 avant toute exécution

Code : `research/positive_control.py`. Tests : `tests/test_positive_control.py`. Commande : `csi positive-control`.

**Pourquoi.** Tous nos protocoles de direction ont conclu « aucun avantage démontré ». On n'a jamais vérifié que
la même mesure dirait « oui » si un avantage existait. Un « non » d'une mesure aveugle ne vaut rien. Le contrôle
positif injecte un avantage **connu** dans les vraies données et regarde si la chaîne de mesure le retrouve, à
quelle taille, et combien de fois elle crie « oui » à tort quand rien n'est injecté.

**Ce que ce n'est pas.** Ni un essai sur le marché, ni une stratégie : les avantages sont **synthétiques** et
plantés. Aucune hypothèse de marché n'est testée ; **0 essai** au programme (l'exécution est inscrite au registre
avec `n_trials = 0`, période DEVELOPMENT, pour la traçabilité). La période finale n'est pas lue.

## 1. Chaîne « criblage » (D à K)

- Données : bougies 1 h du magasin long, 40 paires de recherche, DEVELOPMENT, journées UTC du 2019-01-01 au
  2025-06-30 ; achat à l'ouverture de 01:00, vente à la clôture h jours plus tard (h = 1 et 7), comme les criblages J.
- Événements **aléatoires** : chaque (paire, journée) est un événement avec une probabilité de 10 % (graine par
  répétition). Avantage planté δ ajouté au rendement de chaque événement.
- Mesure : exactement celle des criblages (`screen._row`) : dérive de la paire retirée, IC95 par blocs de jours
  de max(10, 2·h) jours, « passe » = rendement brut > seuil de coûts ET borne basse de l'IC > 0.
- δ à 1 jour : 0 ; 0,10 ; 0,25 ; 0,50 ; 1,00 %. δ à 7 jours : 0 ; 0,50 ; 1 ; 2 ; 4 %. **100 répétitions** par δ.
- Lu : taux de détection (borne basse > 0), taux de « passe », excès estimé moyen contre δ, rendement brut moyen
  à δ = 0 (la dérive du marché, qui n'est pas un avantage).

## 2. Chaîne « décisions ML » (information d'une probabilité)

- Données : les décisions hors échantillon enregistrées des deux systèmes ML retenus : swing
  (`MLS-20261001T085521Z-a04c71`, 99 840 lignes, 7 jours) et intraday (`MLI-20261001T025936Z-0fab30`, 1 959 344
  lignes, 4 h) : chaque (instant, paire) valide, avec sa probabilité `p` et son rendement net observé.
- **Excès** = rendement net − moyenne de toutes les décisions au même instant (dérive retirée, critère 7).
- **Mesure de l'information** : corrélation de rang (Spearman) entre le score et l'excès, à chaque instant sur les
  paires présentes (au moins 5), moyennée par jour ; IC de Student par blocs calendaires de max(10, 2 × H) jours
  (`calendar_mean_ci`), niveau 95 %. Également : excès moyen par décile du score, écart décile haut − décile bas.
- Score planté : à chaque instant, s = ρ · z(excès) + √(1 − ρ²) · ε (z : excès standardisé sur les paires de
  l'instant ; ε gaussien). ρ = 0 ; 0,02 ; 0,05 ; 0,10. **50 répétitions** par ρ ; la corrélation de rang réalisée
  est rapportée.
- Lecture **descriptive** du score réel `p` des deux systèmes, avec la même mesure (aucun verdict : c'est une
  nouvelle lecture de prédictions déjà sélectionnées sur DEVELOPMENT).

## 3. Chaîne « walk-forward » (espérance en R des trades)

- Données : les trades hors échantillon centraux des trois walk-forwards (A `WF-20260930T143103Z-4b0da8`, B
  `WF-20260930T150703Z-5eccae`, C `WF-20260930T153623Z-7dd6cc`).
- Échantillons synthétiques : tirage de blocs de 10 jours de trades réels (dates conservées), R **recentré à 0**
  puis augmenté de δ R. δ = 0 ; 0,05 ; 0,10 ; 0,20 ; 0,30 R. **100 répétitions** par δ.
- Mesure : l'IC95 de l'espérance utilisé par l'admission (`day_block_ci95`, blocs de 10 jours, 2 000 tirages).
- Lu : taux de détection (borne basse > 0) par stratégie et par δ : le plus petit avantage en R que nos
  walk-forwards pouvaient voir.

## 4. Lecture déclarée

- **Fausses alarmes** (δ = 0, ρ = 0) : attendu ≈ 2,5 % (borne basse > 0 d'un IC95 bilatéral). Nettement plus =
  intervalles trop étroits (dépendance mal prise en compte) : nos « non » restent valables, nos rares « oui » non.
- **Puissance** : la taille d'avantage détectée dans 80 % des répétitions est la **taille minimale détectable**
  de chaque chaîne. Un « non » d'un protocole veut dire « pas d'avantage plus grand que cette taille », jamais
  « aucun avantage ».
- **Biais** : l'excès estimé moyen doit retrouver δ ; un écart systématique signale un défaut de mesure.
- Ce contrôle ne teste pas l'**ajustement** des modèles (un LightGBM retrouverait-il une variable faiblement
  informative ?) : limite déclarée, à faire séparément.

## 5. Résultats (`CTRL-20261002T232741Z-7be6ff`, exécuté le 2026-10-02 à 23:27 UTC, commit `0bdb08d`, 0 essai)

### Criblage (≈ 7 100 événements aléatoires par répétition, 100 répétitions)

| Horizon | Avantage planté | Détecté | Excès estimé | Rendement brut moyen |
|---|---|---|---|---|
| 1 j | 0 | **0 %** | +0,01 % | **+0,20 %** |
| 1 j | +0,10 % | 2 % | +0,10 % | +0,29 % |
| 1 j | +0,25 % | 77 % | +0,26 % | +0,45 % |
| 1 j | +0,50 % | **100 %** | +0,50 % | +0,69 % |
| 7 j | 0 | **0 %** | −0,00 % | **+1,32 %** |
| 7 j | +1 % | 9 % | +1,01 % | +2,34 % |
| 7 j | +2 % | **100 %** | +1,97 % | +3,30 % |

### Décisions ML (50 répétitions ; corrélation de rang réalisée ≈ 0,9 ρ)

| Système | ρ = 0 | ρ = 0,02 (corrélation 0,017) | ρ = 0,05 | ρ = 0,10 |
|---|---|---|---|---|
| swing (7 j) | 0 % | **100 %** | 100 % | 100 % |
| intraday (4 h) | **8 %** | **100 %** | 100 % | 100 % |

Score réel `p` (lecture descriptive) : swing **+0,006** [−0,027 ; +0,038] ; intraday **−0,004** [−0,009 ; −0,000].

### Walk-forwards (100 répétitions)

| Stratégie | Trades | δ = 0 | +0,05 R | +0,10 R | +0,20 R |
|---|---|---|---|---|---|
| A | 1 920 | 5 % | 38 % | 78 % | **100 %** |
| B | 3 672 | 2 % | 34 % | **92 %** | 100 % |
| C | 1 479 | 2 % | 21 % | 69 % | **100 %** |

### Lecture

- **La mesure n'est pas aveugle.** Chaque chaîne retrouve un avantage planté, sans biais (l'excès estimé retombe
  sur δ à ±0,03 point près). Nos « non » sont donc des « pas d'avantage plus grand que » :
  - criblages : **+0,5 % par trade à 1 jour, +2 % à 7 jours** (taille détectée à 80 %) ;
  - ML : **corrélation de rang 0,017** (la mesure est très puissante ; les modèles réels sont à 0,006 et −0,004 :
    il n'y a **aucune information directionnelle** à ce niveau) ;
  - walk-forwards : **+0,1 à +0,2 R par trade**.
- **Le piège de la dérive est confirmé** : sans aucun avantage, le rendement brut moyen des achats aléatoires vaut
  +0,20 % à 1 jour et +1,32 % à 7 jours. Le premier dépasse presque le seuil de coûts (0,21 %) : sans le retrait de
  la dérive, la moitié de ces achats au hasard « passeraient ».
- **Fausses alarmes** :
  - criblage : **0 %** (attendu ≈ 2,5 %) : les intervalles des criblages sont **trop prudents** ; on perd de la
    puissance, jamais l'inverse ;
  - ML intraday : **8 %**, un peu au-dessus : l'intervalle quotidien sous-estime la dépendance à 4 h. Le seul
    « signal » réel de cette chaîne (intraday, −0,004, borne haute −0,0002) est de ce niveau : il ne compte pas ;
  - walk-forward : 2 à 5 %, conforme.
- **Conséquence** : un avantage de l'ordre de ce que vendent les groupes et les bots (quelques dixièmes de % par
  trade) est **en dessous de ce que nos criblages pouvaient voir à 7 jours** ; seul un effet net de +0,5 %/jour ou
  une corrélation de rang ≥ 0,02 aurait été vu à coup sûr. Deux corrections au plan : régler les intervalles des
  criblages (trop larges) et de l'intraday (trop étroits) avec une méthode de référence (bootstrap stationnaire,
  bibliothèque `arch`), sous contrôle positif.

## 6. Calibrage des intervalles sous la nulle (déclaré le 2026-10-03, avant exécution)

**Correction de lecture du § 5.** Le « 0 % de fausses alarmes » des criblages ne prouve pas que nos intervalles sont
trop prudents. Les événements du § 1 étaient tirés au hasard, **jour par jour et paire par paire** : sans grappes dans
le temps, la composante commune du marché se moyenne sur presque tous les jours, et la moyenne de leur excès varie
beaucoup moins que pour une vraie condition, qui arrive en grappes (les mêmes jours sur beaucoup de paires). Un
calibrage demande des conditions groupées comme les vraies. Les tailles minimales détectables du § 5 restent valables
pour des conditions peu groupées ; pour des conditions groupées, elles sont plus grandes.

Code : `research/interval_calibration.py` ; commande `csi interval-calibration` ; 0 essai.

- **Criblage.** Drapeaux de trois **conditions réelles** connues à l'instant : J1 (part des achats au marché
  ≥ 90e centile), J2 (≤ 10e centile), et « BTC en baisse de plus de 3 % sur la journée » appliquée à toutes les
  paires (la plus groupée possible). Les rendements à terme (1 et 7 jours) sont remplacés par une **histoire
  rééchantillonnée** par blocs stationnaires de journées entières (toutes les paires ensemble, longueur moyenne
  max(10, 2·h) jours), remise sur le calendrier d'origine : les drapeaux restent à leur place, leur lien avec le
  futur est cassé, la dépendance dans le temps et entre paires est conservée. **200 répétitions** sous la nulle,
  **100** avec un avantage planté (+0,25 % à 1 jour, +1 % à 7 jours).
- **Décisions ML.** Scores aléatoires (nulle exacte) et plantés (ρ = 0,02) sur les décisions réelles du swing et de
  l'intraday ; corrélation de rang quotidienne. 200 et 100 répétitions.
- **Méthodes comparées** :
  - `blocs_de_jours` : celle des criblages (blocs de jours à événement, percentile) ;
  - `calendaire_student` : celle des protocoles ML et volatilité (`calendar_mean_ci`) ;
  - `stationnaire_arch` : bootstrap stationnaire de Politis et Romano (`arch`), sur les sommes et comptes par jour
    calendaire, longueur moyenne de bloc max(10, 2·h) jours, percentile.
- **Lu** : part des bornes basses > 0 et des bornes hautes < 0 sous la nulle (cible 2,5 % chacune) ; part des bornes
  basses > 0 avec l'avantage planté (puissance).
- **Règle déclarée** : pour chaque chaîne, la méthode retenue pour les **protocoles futurs** est celle dont le taux de
  bornes basses > 0 sous la nulle reste dans **[1,5 % ; 4 %]** pour toutes les conditions et tous les horizons, avec
  la plus grande puissance moyenne ; la méthode actuelle est gardée si aucune autre ne fait mieux. Les résultats
  passés ne sont pas recalculés.

## 7. Contrôle positif de l'ajustement (déclaré le 2026-10-03, avant exécution)

Point 4 du plan. Le § 1-3 testait la **mesure** ; ici on teste l'**apprentissage** : un modèle, avec nos réglages et
notre entraînement glissant, retrouve-t-il une variable faiblement informative cachée parmi les vraies ? Code :
`research/fit_control.py` ; commande `csi fit-control` ; 0 essai.

- **Données** : lignes journalières du lot 7 (40 paires, DEVELOPMENT, les sept variables). **Cible** : rendement log à
  7 jours moins la moyenne des paires au même instant (excès, dérive retirée).
- **Variable plantée** : v = ρ · z(cible) + √(1 − ρ²) · ε, ρ = 0 ; 0,02 ; 0,05 ; 0,10 (z : cible standardisée à
  l'instant). Elle s'ajoute aux sept variables réelles.
- **Modèles**, réglages déjà utilisés au programme : LightGBM (15 feuilles, apprentissage 0,05, 200 lignes par
  feuille, 300 arbres) et régression linéaire de référence. Réajustement le 1er de chaque mois depuis 2020, sur les
  lignes dont la cible est connue avant la date (purge), au moins 2 000 lignes.
- **Mesure** : corrélation de rang quotidienne hors échantillon entre la prévision et la cible, IC calendaire par
  blocs de 14 jours ; à côté, la corrélation de la variable plantée seule (le plafond).
- **Lecture déclarée** : si un modèle retrouve à ρ = 0,05 une corrélation proche du plafond, l'apprentissage sait
  extraire une information de cette taille, et l'absence d'information des lots ML ne vient pas d'un modèle trop
  faible ; s'il ne la retrouve qu'à ρ = 0,10, il faut le dire : nos modèles ML ne pouvaient voir qu'une information
  de cette taille, et le « non » des lots ML vaut seulement à ce niveau.

## Historique

- 2026-10-03 : déclaré avant toute exécution.
- 2026-10-02 23:27 UTC (heure du serveur) : exécuté, `CTRL-20261002T232741Z-7be6ff`, 0 essai ; § 5 ajouté.
