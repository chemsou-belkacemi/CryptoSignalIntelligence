"""Moteur du zoo des stratégies (docs/ZOO.md, protocole pré-inscrit ; docs/ZOO_CATALOGUE.md, recensement).

Une seule méthode pour les 239 tests : bougies 1 h du magasin long (4 h et journalières agrégées, datées par la
dernière bougie 1 h), top 40 à date, achat à la clôture suivant le signal, frais taker de `forward/costs.py`, deux
formes de mesure (règle de transaction jugée sur l'excès `μ*` au-delà de la dérive de la paire ; facteur ou
portefeuille jugé sur l'excès hebdomadaire sur sa référence).

Modules :
- `data` : lectures coupées au 2025-06-30, agrégation, univers à date, séries externes sur `available_at` ;
- `spec` : registre déclaratif des 239 tests et interface unique des stratégies ;
- `indicators` : indicateurs purs (numpy), testés à la main ;
- `positions` : positions d'une règle (aucun rendement : sert aussi au comptage à blanc) ;
- `measure` : rendements quotidiens `r(i, d)`, `e(i, d)`, `μ*`, références, intervalles, garde-fous ;
- `decide` : Benjamini-Hochberg, Benjamini-Yekutieli, validation, verdicts ;
- `synth` et `h0` : marché synthétique et contrôle sous H0 (§ 8) ;
- `causality` : recalcul tronqué et futur falsifié, mutations ;
- `review` : garde d'exécution (relecture, configuration, contrôle H0, comptage inscrits) ;
- `run` : comptage à blanc, pipeline commun au contrôle H0 et à l'exécution réelle.

Aucun ordre, aucune clé, aucune lecture après le 2025-06-30. Rien ici n'annonce une rentabilité.
"""
