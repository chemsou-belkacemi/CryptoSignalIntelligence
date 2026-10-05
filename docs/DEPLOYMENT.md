# Exploitation : PC, VPS ou NAS

La surveillance continue (`run --mode shadow`) analyse chaque clôture 15m et n'exécute aucun ordre.
Elle publie uniquement dans `signals/shadow` tant que l'intégration avec BinanceSpotManager n'est
pas vérifiée.

## Docker Desktop sur ce PC (installé le 2026-09-30)

```powershell
cd C:\Users\chams\Downloads\BinanceSpotManager\CryptoSignalIntelligence
.\scripts\docker-init.ps1        # construit, crée le volume, copie l'état local, démarre
```

- **Tableau de bord** : <http://127.0.0.1:8502/dashboard.html> (ce PC uniquement), rafraîchi à
  chaque cycle et rechargé par le navigateur toutes les 60 s.
- **Ce qui tourne** : `monitor` (surveillance shadow des 16 paires, news en mode observe) et
  `dashboard` (serveur web en lecture seule). Aucun ordre, aucune clé.
- **Copie initiale** : bougies, registre des archives, historique des actualités, registre des
  expériences et signaux Telegram évalués sont copiés de ton dossier local vers le volume. Le
  registre des signaux publiés repart à zéro dans Docker (l'ancien contient des chemins Windows).
  Le script refuse de recopier sur un volume déjà rempli (`-Force` pour écraser).
- **Deux états séparés** : après l'installation, l'état de référence de la surveillance est dans
  Docker. Les commandes lancées en local (`.\.venv\Scripts\python.exe -m crypto_signal_intelligence …`)
  travaillent sur le dossier local ; pour agir sur l'état Docker :
  `docker compose run --rm tools <commande>` (ex. `news --hours 24`, `execution-report`, `backup`).
  Ne pas lancer `run` en local en même temps : les deux publieraient chacun de leur côté.
- **Démarrage automatique** : les conteneurs redémarrent seuls avec Docker Desktop
  (`restart: unless-stopped`). Activer dans Docker Desktop « Start Docker Desktop when you sign in
  to your computer ». Docker Desktop ne tourne que pendant ta session Windows : pour une
  surveillance 24 h/24 sans session ouverte, il faut un VPS ou un NAS.
- **Arrêter / reprendre** : `docker compose stop` puis `docker compose up -d monitor dashboard`.
  Tout désinstaller, données comprises : `docker compose down -v` (irréversible).

## Docker (VPS, NAS, PC) : référence

```powershell
docker compose build
docker compose run --rm tools download        # historique initial (archives vérifiées), une fois
docker compose run --rm tools news-sources --check
docker compose up -d monitor dashboard        # surveillance + tableau de bord (127.0.0.1:8502)
docker compose ps                             # « healthy » = un cycle récent terminé
docker compose logs -f monitor
docker compose run --rm tools backup          # sauvegarde vérifiée dans le volume (backups/)
docker compose stop monitor                   # SIGTERM : le cycle en cours se termine
```

- **Volumes persistants** : tout l'état (bougies, registres, retours, actualités, expériences,
  sauvegardes, état de santé) est dans le volume nommé `csi-state`, monté sur `/srv/csi`. Le code
  de l'image est en lecture seule.
- **Exports Telegram avec photos** : le dossier `./exports` de l'hôte (à créer avec `mkdir -p exports` avant le
  premier démarrage) est monté **en lecture seule** sur `/srv/csi/exports` dans les services `api` et `tools` :
  un sous-dossier par groupe, audité avec ses images depuis le tableau de bord ou par
  `docker compose run --rm tools audit-telegram --dir "/srv/csi/exports/<groupe>" --ocr` ([OCR.md](OCR.md)). Rien
  d'autre du volume n'est exposé à l'hôte. **Gros audits avec images : sur le PC**, pas sur un petit VPS : le
  service `api` monte à 2 Go pendant l'audit (modèles OCR), à côté de `monitor` et de BinanceSpotManager.
- **Limites de ressources** : `monitor` plafonné à 1 Go et 4 CPU (plafond, pas réservation : un cycle ne dure que quelques secondes toutes les 15 min), 2 Go et 1 CPU pour les outils ponctuels. Un
  walk-forward sur 16 paires utilise environ 1 Go : le lancer avec `tools`, un seul à la fois.
- **Arrêt propre** : SIGTERM termine le cycle en cours puis libère le verrou
  (`stop_grace_period: 180s`, car une attente de bougies peut durer 90 s).
- **Redémarrage après panne** : `restart: unless-stopped`. Au redémarrage, seule la dernière bougie
  close est analysée ; les signaux déjà publiés ne sont jamais republiés (même identifiant), et une
  fenêtre d'entrée close ne produit rien.
- **Démarré n'est pas prêt** : le contrôle de santé (`health`) exige un cycle terminé récemment. Il
  n'est donc pas « healthy » avant la première clôture 15m suivant le démarrage (délai de grâce de
  25 minutes), et redevient « unhealthy » si la boucle se fige.
- **Session d'administration fermée** : un conteneur lancé par `docker compose up -d` ne dépend pas
  de la session SSH ou du terminal. À vérifier sur la machine cible : se déconnecter, se
  reconnecter, puis contrôler `docker compose ps` et l'heure du dernier cycle (`health`).
- **Un seul hôte** : ne pas placer le volume sur un partage réseau (SQLite et verrous de fichiers).
- **Dépendances** : `pylock.toml` a été verrouillé sous Windows et ne contient que des roues
  Windows. L'image Linux installe les **mêmes versions**, extraites en contraintes, mais sans
  vérifier les empreintes des roues Linux. Un verrou Linux dédié reste à produire.

## Windows sans Docker

Planificateur de tâches : une tâche « au démarrage », exécutée « que l'utilisateur soit connecté
ou non », qui lance :

```powershell
C:\Users\chams\Downloads\BinanceSpotManager\CryptoSignalIntelligence\.venv\Scripts\python.exe -m crypto_signal_intelligence run --mode shadow
```

avec « Démarrer dans » = le dossier du projet et « Redémarrer en cas d'échec » activé. Le verrou
d'instance empêche un second lancement. Santé : `python -m crypto_signal_intelligence health`.

## État de vérification

| Élément | Statut |
|---|---|
| Arrêt propre, verrou rendu, `health` | TESTÉ (tests unitaires, horloge simulée) |
| Image Docker, cycle réel et santé du conteneur | TESTÉ en local le 2026-09-30 (Docker Desktop, 1 Go, 1 CPU) |
| Fonctionnement après fermeture de session sur un VPS/NAS | NON VÉRIFIÉ : aucune machine cible disponible depuis cet environnement |
