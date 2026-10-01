# Lot 5 — apprentissage (méta-labeling), protocole fixé avant la première exécution

Question : sur les setups qu'une stratégie trouve, un modèle sait-il distinguer à l'avance ceux qui
finiront gagnants, assez bien pour qu'en ne gardant que ceux-là on gagne de l'argent après frais ?

Commande : `ml-evaluate [--strategy ID]` (travail lourd : priorité basse, mémoire plafonnée).
Code : `src/crypto_signal_intelligence/ml/` ; tests : `tests/test_ml.py` (données synthétiques).

## Protocole (déclaré le 2026-10-01, avant toute exécution sur données réelles)

| Élément | Choix |
|---|---|
| Jeu de données | setups de la stratégie, paramètres v1 déclarés, coûts centraux, période DEVELOPMENT ; trades remplis et clos seulement |
| Étiquette | R net > 0 (frais et glissement compris) |
| Variables (25) | volatilité (ATR / prix), RSI, distances aux EMA 20/50 et au plus haut Donchian, volume relatif, rendements 1 et 4 bougies, volatilité réalisée, position dans les bandes de Bollinger, pente et volatilité du contexte 1h, rendement 24 h et distance à l'EMA 50 du contexte, BTC (rendement 24 h, volatilité, pente), régimes (tendance, volatilité), heure (sinus, cosinus), géométrie du trade (stop en ATR, objectif en R) — toutes lues à l'instant de décision, mêmes jointures `available_at` que la stratégie |
| Modèle | régression logistique L2 (λ = 1), variables standardisées sur l'entraînement seul |
| Validation | fenêtres du walk-forward (ancrées, 6 tests de 6 mois) ; entraînement = trades dont le setup ET la sortie précèdent le test (purge) |
| Règle de tri | garder un trade si sa probabilité prédite dépasse le taux de gain de l'entraînement ; **aucun seuil optimisé** |
| Essais | un par stratégie, comptés dans `program_trials` |

## Critères (agrégat hors échantillon, IC95 par blocs de 10 jours)

1. au moins 300 trades testés et 3 fenêtres (sinon INSUFFICIENT_DATA) ;
2. score de Brier meilleur que le taux de base : l'IC95 de l'écart est entièrement > 0 ;
3. E[R] net des trades gardés > 0, IC95 entièrement > 0 (coûts centraux) ;
4. les trades gardés font mieux que l'ensemble : IC95 de l'écart entièrement > 0.

Tous vrais → **USEFUL_OOS** ; sinon **NOT_USEFUL**. Les tests synthétiques montrent que le protocole
conclut USEFUL_OOS quand une relation réelle existe, et NOT_USEFUL sur du bruit.

## Ce qu'un verdict change

Rien automatiquement. `ML_PROBABILITY` reste `NONE` dans les signaux tant qu'un modèle n'a pas
passé ces critères **et** été confirmé en prospectif (signaux shadow résolus après la date du
modèle), puis promu explicitement par le propriétaire. La probabilité affichée serait alors la
fréquence prédite de « R net > 0 » pour un setup de cette stratégie, avec sa cible, son horizon et
sa calibration (contrat V3) ; jamais une promesse de gain.

## Résultats

Voir la section suivante (mise à jour après chaque exécution) et `reports/ML-*/`.
