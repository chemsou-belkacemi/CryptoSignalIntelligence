# Retour d'exécution du consommateur — format JSONL, version 2

Le consommateur (BinanceSpotManager, sur **Binance Demo**) écrit ce qui s'est réellement passé
pour chaque signal reçu : un objet JSON par ligne, fichier UTF-8, ajout en fin de fichier.
Ce projet importe ces événements (`import-feedback --file …`), les dédoublonne sur `event_id`
et les rapproche des signaux publiés (`execution-report`). Il ne pilote aucun ordre.

Implémentation : `feedback/schema.py` (validation stricte), `feedback/store.py` (importation),
`feedback/reconcile.py` (état par signal, rejeu prospectif, écarts, rapport).

Version 2 (2026-09-30) : ajout de `ORDER_PLACED`, `MARKET_EXIT_FILLED` et de `exit_policy_hash`
obligatoire sur `RECEIVED`.

## Événement

```json
{"event_id": "BSM-20260930T101503Z-0001", "signal_id": "CSI-20260930T100002Z-1A2B3C4D",
 "event_type": "ENTRY_FILLED", "occurred_at": "2026-09-30T10:15:03Z", "environment": "DEMO",
 "producer": "BinanceSpotManager", "symbol": "SOLUSDT", "quantity": "0.83", "price": "119.15",
 "quote_quantity": "98.89", "fee": "0.0989", "fee_asset": "USDT", "order_id": "123456789",
 "target_index": null, "reason": null, "exit_policy_hash": null}
```

| Champ | Obligatoire | Règle |
|---|---|---|
| event_id | oui | unique chez le producteur ; une réémission du même id est ignorée à l'importation |
| signal_id | oui | `SIGNAL_ID` du signal TXT V3 ; un id inconnu est importé mais signalé |
| event_type | oui | voir la chaîne d'états ci-dessous |
| occurred_at | oui | UTC à la seconde, `YYYY-MM-DDTHH:MM:SSZ` : moment de l'événement chez le courtier, pas de l'écriture |
| environment | oui | `DEMO` uniquement (tout autre refusé) |
| producer | oui | nom du programme, ex. `BinanceSpotManager` |
| symbol | oui | paire Spot USDT/USDC |
| quantity, price | ORDER_PLACED et tout remplissage | ORDER_PLACED : quantité commandée et prix limite ; remplissage : quantité de base **réellement remplie** et prix moyen ; nombres en chaînes |
| quote_quantity | optionnel | montant en devise de cotation, sinon `quantity × price` |
| fee, fee_asset | ensemble | frais réels du remplissage et leur devise. **Inconnus : les omettre**, jamais 0 (CSI signale alors `FRAIS_INCONNUS`) |
| order_id | ORDER_PLACED | identifiant d'ordre chez le courtier (optionnel ailleurs) |
| target_index | TP_FILLED | numéro du TP (1 à 4) |
| reason | REJECTED, CANCELLED, MARKET_EXIT_FILLED | motif lisible |
| exit_policy_hash | RECEIVED (et seulement lui) | empreinte de la politique que le consommateur a reconnue et appliquera |

Clés inconnues : refusées. Nombres en chaînes décimales (point), jamais NaN/Infinity.

## Chaîne d'états

Fichier publié (registre CSI) → `RECEIVED` (message lu et accepté) → `ORDER_PLACED` (ordre
d'entrée envoyé) → `ENTRY_PARTIAL` / `ENTRY_FILLED` (achats réels) → `TP_FILLED`, `STOP_FILLED`,
`MARKET_EXIT_FILLED` (ventes réelles) → `CLOSED` (plus aucune position ni ordre).

- `REJECTED` : refusé avec motif (expiré à réception, écart de prix, doublon, politique inconnue,
  statut non DEMO_ELIGIBLE, solde, filtre…).
- `EXPIRED` : entrée jamais remplie avant `ENTRY_EXPIRES_AT`. `CANCELLED` : annulation, avec motif.
- `MARKET_EXIT_FILLED` : vente au marché hors TP et hors stop (stop refusé puis vente au marché,
  fermeture manuelle). CSI la compte comme un écart à la politique.
- Signal en attente de confirmation manuelle chez BSM (branche `feat/signal-routing`) : aucun
  événement tant que le propriétaire n'a pas décidé ; s'il ne confirme pas, `REJECTED` n'arrive qu'à
  `EXPIRES_AT` + 60 s, avec les codes de revue en motif. Un signal non `DEMO_ELIGIBLE` peut être
  confirmé à la main : il produit alors `RECEIVED` et des remplissages comme les autres.
- Sans aucun événement, l'état est **UNKNOWN** : ni un refus, ni une perte. Un signal shadow est
  `NOT_CONSUMED`.
- Le consommateur tient son propre registre durable des `SIGNAL_ID` et des `IDEMPOTENCY_KEY`
  traités. Une retransmission garde le même `SIGNAL_ID` et ne produit jamais deux exécutions.
  Un état incertain ne justifie jamais la fabrication d'un nouveau signal.

## Ce que ce projet en fait

`execution-report` affiche par signal trois mesures séparées, jamais additionnées :

- **backtest** : E[R] hors échantillon du dernier walk-forward de la stratégie, dans la variante
  alignée sur la politique du signal (`profil_BSM` pour une politique `BSM_*`) ;
- **prospectif** : rejeu du signal sur les bougies postérieures à sa décision, avec les règles de
  remplissage du simulateur et la politique de sortie du signal ;
- **Demo observée** : état reconstruit, PnL net en devise de cotation, R par rapport au risque
  prévu `ENTRY_1 − STOP_LOSS`.

La colonne « écarts » explique les différences : politique différente (empreinte), délai de
réception, décalage de l'heure et du prix d'entrée par rapport au prospectif, entrée manquée,
remplissage hors théorie, taille partielle, sortie au marché hors politique, frais inconnus,
théorie ambiguë (TP et stop dans la même bougie). Un prix qui touche l'entrée n'est pas la preuve
d'un remplissage.
