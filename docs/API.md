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
| `GET /signals/generated?limit=20` | derniers signaux trouvés par les stratégies de CSI (shadow), avec leur statut de validation et `bsm_text` pour un test manuel en Demo |
| `GET /universe` | paires configurées et paires ajoutées par le propriétaire, avec leur état |
| `GET /execution-report` | signaux publiés : backtest, prospectif et Demo, séparés |
| `GET /pairs` | paires analysables (configurées et ajoutées prêtes) et âge de leur dernière bougie ; horizons proposés |
| `GET /models` | dernier verdict de chaque modèle (walk-forward, méta-labeling, ML intraday, criblage) et nombre d'essais du programme |
| `GET /admissions` | avis halal consignés (config/halal_screening.toml) et décisions d'ajout des paires : ajoutées, refusées, indisponibles, à décider ([UNIVERSE.md](UNIVERSE.md)) |
| `POST /admissions/run` | applique le screening : favorables ajoutées (paire USDT négociable), défavorables refusées, douteuses ou inexploitables à décider |
| `POST /admissions/decide-all` | `{}` : ajoute chaque crypto à décider, comme décision du propriétaire |
| `POST /admissions/decide` | `{"symbol": "DOGEUSDT", "decision": "add" \| "refuse"}` : décision du propriétaire, prioritaire et tracée |
| `POST /opportunities/pair` | `{"symbol": "ETHUSDT"}` : les 6 horizons d'une paire en un passage (état du plan, objectif ou stop atteint d'abord, espérance, niveaux) et l'avis simulé des stratégies ; statistiques en échantillon, jamais une proposition d'entrer |
| `GET /derivatives?symbol=X` | positionnement du moment sur le marché à terme USDⓈ-M (financement, prime, intérêt ouvert, ratios) avec le rang de chaque valeur dans son historique récent ; données publiques, relues au plus toutes les 5 min ; information seulement ([DERIVATIVES.md](DERIVATIVES.md)) |
| `GET /volatility`, `GET /volatility?symbol=X` | volatilité prévue du jour : mouvement typique attendu à 1, 3 et 7 jours par paire, comparé aux 7 derniers jours ; ampleur seulement, jamais le sens ([VOLATILITY.md §13](VOLATILITY.md)) |
| `POST /evaluate` | évalue un signal Telegram (voir ci-dessous) ; champs `verdict_basis` (« groupe » ou « geometrie ») et `source_proof` |
| `POST /sources/history` | `{"export": <export Telegram Desktop, texte seul>, "weights": "early" \| "equal"}` : rejoue l'historique d'un groupe, enregistre sa preuve ([EXTERNAL_SIGNALS.md](EXTERNAL_SIGNALS.md)) ; 8 Mo au plus, un bilan à la fois (409 sinon) |
| `GET /sources/history` | dernière preuve sur historique de chaque groupe importé |
| `GET /sources/exports` | sous-dossiers d'`exports/` contenant un `result.json` (export de Telegram Desktop copié par le propriétaire, photos comprises) : messages, images nommées et présentes, état du dernier audit de chacun ; `running` = dossier en cours, `ocr_available` ([OCR.md](OCR.md), « Depuis le tableau de bord ») |
| `POST /sources/exports/audit` | `{"folder": "<nom du sous-dossier>", "weights": "early" \| "equal", "ocr": true}` : audit du dossier **en arrière-plan**, images lues sur la machine de CSI (jamais hors d'`exports/` : nom sans chemin ni « .. »), même preuve et même rapport que la commande ; jeton exigé (403 sans `CSI_API_TOKEN`), un seul bilan à la fois (409), 503 si l'extra « ocr » manque |
| `GET /sources/exports/audit?folder=X` | état (`EN_COURS`, `TERMINE`, `ECHEC`), progression (étape, images lues) et résultat du dernier audit de ce dossier, même forme que `POST /sources/history` |
| `GET /assistant` | assistant de marché ([ASSISTANT.md](ASSISTANT.md), test en direct F18) : contenu de `state/assistant.json` écrit par la surveillance à chaque clôture 4 h (feu, BTC, régimes, appels actifs avec niveaux, score et explication, derniers refus, prochaine évaluation, `resume` de cinq lignes lu par la commande `/etat` du relais) ; shadow, aucun ordre, aucun gain démontré |
| `GET /assistant/outbox` | `{"messages": [{"id", "created_at", "text"}]}` : messages Telegram EN_ATTENTE de l'assistant, du plus ancien au plus récent, 20 au plus ; un message en attente depuis plus de 6 h passe EXPIRE (contrat du service relais) |
| `POST /assistant/sent` | `{"ids": [...]}` → `{"marked": n}` : le relais confirme l'envoi ; jeton exigé (403 sans `CSI_API_TOKEN`) |
| `GET /collecte` | état du collecteur en shadow ([COLLECTE.md](COLLECTE.md)) : contenu de `state/C_ETAT.json` (statut, dernier message et dernière entrée par source, compteurs, dernière erreur) et taille des journaux `C_*.jsonl` du mois ; lecture seule, aucun appel réseau ; information seulement, aucune influence sur les tests, les avis ou BSM |
| `POST /analyze-pair` | `{"symbol": "ETHUSDT", "horizon": "24h"}` : perspective d'une paire (contexte, historique comparable, plan indicatif évalué sur le passé, stratégies en simulation) ; une analyse à la fois, résultat gardé jusqu'à la bougie suivante |
| `POST /refresh-pair` | `{"symbol": "ETHUSDT"}` : télécharge les bougies publiques manquantes de la paire et du contexte BTC (REST public, aucune clé) ; une mise à jour à la fois (409 sinon) |

Pages (même serveur) : `/` (tableau de bord interactif), `/app.js`, `/app.css` ; elles ne contiennent
aucune donnée et ne demandent pas le jeton, les appels qu'elles font le demandent.

### `POST /evaluate`

```json
{"text": "PAIR: ETH/USDT\nENTRY 1: 2687\nT1: 2738\nT2: 2792\nSL: 2644", "source": "Nom du groupe", "record": true}
```

Réponse : verdict (`REFUSE`, `DEFAVORABLE`, `INDETERMINE`, `FAVORABLE`), contrôles, contexte de
marché, géométrie, taux de base, bilan du groupe et **`summary_fr`**, un résumé en français où
chaque chiffre porte sa définition (un taux de base historique n'est jamais la probabilité que CE
signal réussisse). `record: true` (défaut) enregistre l'évaluation : la surveillance la résout
ensuite (TP1, stop, non rempli) et le bilan du groupe se construit. `record: false` pour un essai.

`user_validated: true` signifie que le propriétaire a soumis ce signal lui-même : sa validation
ajoute une paire inconnue à l'univers (voir `docs/UNIVERSE.md`). Le verdict est alors `EN_ATTENTE`
(non enregistré) jusqu'à ce que la surveillance ait téléchargé l'historique ; redemander l'avis
ensuite. Un signal reçu automatiquement (`user_validated: false`, défaut) n'ajoute jamais rien et
reste `REFUSE` hors univers. `GET /universe` liste les paires configurées et ajoutées avec leur état
(`REQUESTED`, `READY`, `FAILED`).

## Sécurité

- Ports publiés sur **127.0.0.1** seulement : l'API n'est pas joignable depuis le réseau local ni
  depuis Internet. Ne jamais publier ce port sur `0.0.0.0` ni l'exposer par un tunnel.
- Jeton facultatif **`CSI_API_TOKEN`** : variable d'environnement (par exemple dans un fichier `.env`
  à côté de `docker-compose.yml`, jamais versionné). S'il est défini, chaque requête doit porter
  `Authorization: Bearer <jeton>` ; le client (BSM) lit le même jeton dans SON environnement.
- Corps limité à 16 Ko, JSON uniquement, méthodes autres que GET/POST refusées, aucune en-tête
  CORS : seule la page servie par CSI elle-même (même origine) l'appelle depuis un navigateur ; un
  autre site ne peut ni lire ses réponses ni lui envoyer du JSON.
- En-tête `Host` contrôlé (127.0.0.1, localhost, csi-api, ou la liste `CSI_API_ALLOWED_HOSTS`) :
  protection contre le « DNS rebinding » (une page piégée qui viserait 127.0.0.1).
- La page applique une politique de sécurité de contenu stricte (aucun script externe ni en ligne,
  pas d'intégration dans un cadre) et n'insère jamais un texte reçu comme du HTML.
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
