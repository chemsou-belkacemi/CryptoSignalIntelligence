"""Collecteur (docs/COLLECTE.md) : relevé continu, en shadow, de données publiques de marché hors bougies.

Service séparé de la surveillance (`csi collecteur`, conteneur `collecteur`). Cinq sources : liquidations du marché à
terme, carnet d'ordres Spot, flux des transactions, options Deribit, attention publique (Reddit, Google Trends).
Journaux en ajout seul `<root>/forward/C_<SOURCE>-AAAA-MM.jsonl` (empreintes chaînées de `forward/journal.py`).

Relevé en shadow : aucune influence sur les tests en direct, les avis ou BinanceSpotManager ; aucun pouvoir
prédictif revendiqué. Aucune clé, aucun ordre : le client réseau (`collect/net.py`) n'accepte qu'une liste fermée
d'adresses publiques en lecture seule, et `data/http.py` n'est pas touché.
"""
