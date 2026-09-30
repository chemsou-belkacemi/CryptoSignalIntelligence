# Données : sources, hypothèses, contrôles

## Sources

| Source | Usage | Unité d'horodatage |
|---|---|---|
| Binance Public Data (`data.binance.vision`), archives mensuelles puis quotidiennes | historique | ms jusqu'aux archives de décembre 2024, **µs à partir du 2025-01-01** |
| REST public `data-api.binance.vision/api/v3/klines` | complément récent, pages de 1000 | ms |

L'unité est déclarée par adaptateur puis vérifiée par ordre de grandeur ; un écart
est une erreur, jamais une conversion devinée. Chaque archive est vérifiée par son
fichier `.CHECKSUM` (SHA-256), conservée brute dans `data/raw/`, et son hash est
enregistré (`data/raw/archives.sqlite3`). `download --recheck-archives` détecte une
archive republiée avec un contenu différent.

MARKET_DATA_SOURCE = `BINANCE_SPOT_PUBLIC` (marché réel). Ce ne sont **pas** les prix
ni les remplissages de Binance Demo.

## Hypothèse de disponibilité

Aucune heure de réception historique n'est connue. Hypothèse :
`available_at = open_time + intervalle + 2 s` (configurable). L'heure de
téléchargement n'est jamais utilisée comme disponibilité historique.

## Contrôles (`data-quality`)

Unicité, chronologie, alignement sur l'intervalle, OHLC cohérents, prix > 0, volumes ≥ 0,
volumes taker ≤ volumes totaux, valeurs finies, bougie non clôturée, trous, âge.
Les lignes invalides vont en quarantaine (`data/quarantine/`) avec leur motif.
**Aucun forward-fill.** Un trou bloque les décisions pendant `gap_block_bars` bougies
(motif DATA_GAP).

## Réconciliation

Chaque mise à jour recharge les 3 dernières bougies par REST. Une archive vérifiée
prime sur une ligne REST pour la même bougie ; les écarts sont comptés.

## Limites

- Le spread et le carnet historiques ne sont pas reconstitués.
- Univers non point-in-time : BTC/ETH choisis aujourd'hui.
- 4h et 5m sont déclarés mais désactivés tant que leurs jointures n'ont pas été validées.
