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

## Historique

- 2026-10-03 : déclaré avant toute exécution.
