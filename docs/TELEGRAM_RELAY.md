# Telegram : deuxième bot (F4 en direct) et exports avec photos

Deux choses que seul le propriétaire peut faire. Tout le reste est prêt dans CSI.

## 1. Deuxième bot : la source en direct de F4

**Pourquoi un deuxième bot.** Un bot Telegram n'accepte qu'un seul lecteur `getUpdates`. Celui de
BinanceSpotManager est déjà pris par son worker ; s'en servir aussi pour CSI ferait que les deux se volent les
messages. Le deuxième bot n'est lu que par le **relais** de CSI (`relay/telegram.py`), qui recopie chaque message
dans l'entrée en direct de F4 (`POST /telegram/live`). Le relais n'évalue rien, ne décide rien, ne passe aucun
ordre. Le jeton n'existe que dans ce service : le reste de CSI n'a toujours aucun secret.

### Ce que tu fais (une fois)

1. **Créer le bot** : dans Telegram, ouvrir `@BotFather`, envoyer `/newbot`, choisir un nom et un identifiant
   (finissant par `bot`). BotFather répond avec le **jeton** (`123456789:AA…`). Ne le colle nulle part ailleurs
   que dans le fichier `.env` ci-dessous.
2. **Le laisser lire les groupes** : dans `@BotFather`, `/setprivacy` → choisir le bot → **Disable**. Sans ça, un
   bot ne voit dans un groupe que les commandes qui lui sont adressées.
3. **Lui donner les messages**, au choix :
   - l'ajouter aux **groupes** dont tu veux mesurer les signaux (s'ils acceptent les bots) ; pour un **canal**, il
     faut l'ajouter comme **administrateur** (sans aucun droit à cocher) ;
   - ou, si le groupe refuse les bots (souvent le cas des groupes VIP payants), **transférer** les signaux au bot
     en message privé. Le relais garde alors le canal d'origine comme source quand Telegram le donne.
4. **Poser le jeton** dans le fichier `.env` de CSI (à côté de `docker-compose.yml`, jamais versionné) :
   ```
   CSI_TELEGRAM_RELAY_TOKEN=<le jeton donné par BotFather>
   ```
   puis `chmod 600 .env`. Facultatif, plus tard : `CSI_TELEGRAM_RELAY_CHATS=-100123,-100456` pour n'accepter que
   ces conversations (les identifiants s'affichent dans le journal du relais, voir 6).
5. **Démarrer le relais** (il est éteint par défaut) :
   ```bash
   cd ~/BinanceSpotManager/CryptoSignalIntelligence
   docker compose --profile telegram up -d telegram-relay
   ```
6. **Vérifier** :
   ```bash
   docker compose logs -f telegram-relay        # « relais Telegram démarré », puis « N relayée(s) » à chaque message
   ```
   Puis, dans le tableau de bord (onglet Suivi, F4) ou avec `docker compose exec monitor python -m crypto_signal_intelligence forward report`, les groupes apparaissent au passage
   suivant de F4 (toutes les 10 minutes environ).

**À ne pas faire** : mettre le jeton du bot de BinanceSpotManager (`BSM_TELEGRAM_BOT_TOKEN`) à la place ; les deux
lecteurs se voleraient les messages et BSM raterait des signaux.

### Ce que le relais fait exactement

- Lit les messages, messages modifiés et publications de canal reçus par le bot (`getUpdates`, attente longue).
- Garde le **texte**, ou la **légende** d'une image ; une image sans légende n'est pas relayée (F4 lit du texte ;
  son code est gelé jusqu'au 2026-12-25).
- Heure de réception = arrivée du message chez le bot (`date`, ou `edit_date` pour une modification) ; une
  modification est un nouvel élément marqué « modifié ».
- Dépose par lots de 50 dans `POST /telegram/live` (jeton `CSI_API_TOKEN` s'il est défini). Si l'API CSI est
  arrêtée, les messages attendent dans le volume du relais et repartent au passage suivant : **rien n'est perdu**.
- Seule adresse appelée hors CSI : `https://api.telegram.org`, sans redirection. Le jeton n'est jamais écrit ni
  journalisé (masqué dans les erreurs).

Code : `src/crypto_signal_intelligence/relay/telegram.py`. Tests : `tests/test_telegram_relay.py`.

## 2. Exports avec photos : audit de tes groupes

Les signaux publiés **en image** ne sont lus que si l'export contient les photos.

### Ce que tu fais, pour chaque groupe

1. Telegram **Desktop** → ouvrir le groupe → menu **⋮** → **Exporter l'historique du chat**.
2. Cocher **Photos** (décocher vidéos, fichiers, messages vocaux : inutiles et lourds). Format : **JSON**.
   Période : tout l'historique.
3. Ranger chaque export dans son propre dossier sous `CryptoSignalIntelligence/imports/telegram/exports/` :
   ```
   imports/telegram/exports/
     NomDuGroupe1/result.json + photos/
     NomDuGroupe2/result.json + photos/
   ```
   (Le dossier `imports/` n'est jamais versionné.)

### Ce que CSI fait ensuite

```bash
cd ~/BinanceSpotManager/CryptoSignalIntelligence
.venv/bin/python -m crypto_signal_intelligence audit-telegram --dir imports/telegram/exports --ocr
```

- Chaque export est lu ; un export sans photos est signalé (« refaire l'export en cochant Photos »).
- Les signaux texte et les signaux lus sur image sont rejoués sur les bougies publiques Binance, avec les coûts.
- Les signaux lus sur image ont **leur propre bilan** et n'entrent **jamais** dans la preuve d'un groupe (taux
  d'erreur de la lecture hors échantillon inconnu) ; au moindre doute une image est ignorée et comptée comme
  illisible. Détails : [OCR.md](OCR.md).
- Un groupe n'est « prouvé sur son historique » qu'avec 50 signaux texte résolus sur 20 jours, un gain moyen
  positif avec certitude (IC95) et au plus une petite part de messages supprimés ([EXTERNAL_SIGNALS.md](EXTERNAL_SIGNALS.md)).
