# API locale de CSI (lecture et évaluation)

Petite API HTTP pour que l'interface de BinanceSpotManager (ou tout outil local) interroge CSI sans
passer par la ligne de commande. **Aucune route ne crée, modifie ou annule un ordre** ; aucune ne
demande de clé Binance. Code : `src/crypto_signal_intelligence/api/server.py`.

## Démarrer

- Docker (recommandé) : service `api` de `docker-compose.yml`, démarré avec les autres
  (`docker compose up -d`). Adresse sur ce PC : `http://127.0.0.1:8503`. Depuis un autre projet
  Compose (BSM) : réseau Docker `csi-bridge`, adresse `http://csi-api:8503`.
- Sans Docker : `.\.venv\Scripts\python.exe -m crypto_signal_intelligence.api` (127.0.0.1:8503).
  Attention : l'API lit alors l'état LOCAL (`data/`, `state/`…), pas celui du volume Docker.

## Routes (JSON, UTF-8)

| Route | Rôle |
|---|---|
| `GET /health` | état de la surveillance : dernier cycle, prêt ou non (`places_orders` vaut toujours `false`) |
| `GET /strategies` | dernier walk-forward de chaque stratégie : verdict, E[R] et IC95 hors échantillon |
| `GET /sources` | bilan de chaque groupe Telegram contre le taux de base (aucune conclusion avant 20 signaux résolus sur 10 jours) |
| `GET /signals/recent?limit=20` | dernières évaluations de signaux externes et leur issue |
| `GET /execution-report` | signaux publiés : backtest, prospectif et Demo, séparés |
| `POST /evaluate` | évalue un signal Telegram (voir ci-dessous) |

### `POST /evaluate`

```json
{"text": "PAIR: ETH/USDT\nENTRY 1: 2687\nT1: 2738\nT2: 2792\nSL: 2644", "source": "Nom du groupe", "record": true}
```

Réponse : verdict (`REFUSE`, `DEFAVORABLE`, `INDETERMINE`, `FAVORABLE`), contrôles, contexte de
marché, géométrie, taux de base, bilan du groupe et **`summary_fr`**, un résumé en français où
chaque chiffre porte sa définition (un taux de base historique n'est jamais la probabilité que CE
signal réussisse). `record: true` (défaut) enregistre l'évaluation : la surveillance la résout
ensuite (TP1, stop, non rempli) et le bilan du groupe se construit. `record: false` pour un essai.

## Sécurité

- Ports publiés sur **127.0.0.1** seulement : l'API n'est pas joignable depuis le réseau local ni
  depuis Internet. Ne jamais publier ce port sur `0.0.0.0` ni l'exposer par un tunnel.
- Jeton facultatif **`CSI_API_TOKEN`** : variable d'environnement (par exemple dans un fichier `.env`
  à côté de `docker-compose.yml`, jamais versionné). S'il est défini, chaque requête doit porter
  `Authorization: Bearer <jeton>` ; le client (BSM) lit le même jeton dans SON environnement.
- Corps limité à 16 Ko, JSON uniquement, méthodes autres que GET/POST refusées, aucune en-tête
  CORS : l'API est appelée par le serveur de l'interface, jamais directement par un navigateur.
- Erreur interne : message générique au client, détail dans le journal de CSI seulement.

## Intégration prévue dans l'interface de BinanceSpotManager

À faire dans BSM, après fusion de sa branche d'intégration par le propriétaire :

1. un client HTTP (`csi_client.py`) : adresse `BSM_CSI_API_URL` (défaut `http://csi-api:8503` en
   Docker), jeton `CSI_API_TOKEN`, délai court ; CSI injoignable = « avis CSI indisponible », jamais
   un blocage de l'interface ;
2. une page « Avis CSI » : coller un signal, voir le verdict et son explication, bilan des groupes,
   état de CSI et verdicts des stratégies ;
3. sur chaque signal Telegram reçu : l'avis CSI affiché à côté du signal. Règle de prudence : l'avis
   CSI peut seulement **retenir** un signal (« À confirmer » si DEFAVORABLE, refus si REFUSE) ; il ne
   rend jamais un signal automatique, aucune stratégie n'ayant d'avantage démontré ;
4. `docker-compose.yml` de BSM : service `ui` sur le réseau externe `csi-bridge`.
