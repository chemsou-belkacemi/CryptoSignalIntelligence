# Faire tourner CSI et le bot 24 h/24 sur un VPS

Guide pas à pas pour passer du PC à un serveur loué (VPS) : CSI (le cerveau) et BinanceSpotManager
(le bot, **Binance Demo uniquement**) dans Docker, démarrage automatique, accès privé, sauvegardes.

## 1. Choisir le VPS

| Besoin | Valeur mesurée (2026-10-01) | Conseillé |
|---|---|---|
| RAM en fonctionnement | ≈ 0,7 Go (5 conteneurs) | **4 Go** (8 Go pour lancer aussi les recherches lourdes : walk-forward, ML) |
| Processeur | quelques secondes de calcul toutes les 15 min | 2 vCPU |
| Disque | 1,5 Go d'images + 0,4 Go de données + ≈ 4,5 Go de sauvegardes (14 nuits) | 40 Go SSD |
| Système | Docker | Ubuntu 24.04 LTS |
| Lieu | Binance refuse certains pays (dont les États-Unis) | **Europe** (France, Allemagne, Finlande) |

Exemples : Hetzner (CX22, Allemagne/Finlande), OVHcloud (VPS, France), Scaleway. Prendre une
connexion par **clé SSH** plutôt que par mot de passe.

## 2. Préparer le serveur (une seule fois)

Connecté en SSH (`ssh root@ADRESSE`), créer un utilisateur, puis installer Docker :

```bash
adduser csi && usermod -aG sudo csi                     # puis se reconnecter : ssh csi@ADRESSE
sudo apt update && sudo apt -y upgrade
curl -fsSL https://get.docker.com | sudo sh              # Docker Engine + Compose (script officiel)
sudo usermod -aG docker $USER && newgrp docker
sudo apt -y install unattended-upgrades git              # mises à jour de sécurité automatiques
sudo ufw allow OpenSSH && sudo ufw --force enable        # seul SSH est ouvert sur Internet
timedatectl                                              # « System clock synchronized: yes » (Binance l'exige)
```

## 3. Récupérer le code et les secrets

```bash
mkdir -p ~/BinanceSpotManager && cd ~/BinanceSpotManager
git clone https://github.com/chemsou-belkacemi/CryptoSignalIntelligence.git
git clone https://github.com/chemsou-belkacemi/BinanceSpotManager.git
```

Les deux fichiers `.env` (clés Binance Demo, jeton Telegram, jeton de l'API CSI) ne sont **pas** dans
git. Les copier depuis le PC (PowerShell, dossier `C:\Users\chams\Downloads\BinanceSpotManager`) :

```powershell
scp .\CryptoSignalIntelligence\.env csi@ADRESSE:~/BinanceSpotManager/CryptoSignalIntelligence/.env
scp .\BinanceSpotManager\.env csi@ADRESSE:~/BinanceSpotManager/BinanceSpotManager/.env
```

Puis, sur le serveur : `chmod 600 ~/BinanceSpotManager/*/.env` (lisibles par toi seul).

## 4. Déménager les données du PC (positions, historique, bilans des groupes)

**Règle absolue : un seul bot à la fois.** Deux bots sur le même compte Demo géreraient deux fois
les mêmes positions, et deux lecteurs Telegram se voleraient les messages.

1. Sur le PC (PowerShell, dossier CSI) : `.\scripts\exporter-donnees.ps1`. Le script **arrête** le
   bot et CSI, exporte leurs données dans `.\migration\`, et les laisse arrêtés.
2. Copier vers le serveur : `scp -r .\migration csi@ADRESSE:~/`
3. Sur le serveur : `~/BinanceSpotManager/CryptoSignalIntelligence/scripts/importer-donnees.sh ~/migration`
   (refuse d'écraser des données existantes ; retire les verrous du PC).

Sans migration (départ de zéro) : sauter cette étape ; CSI retéléchargera l'historique au premier
lancement (`docker compose run --rm tools download`, environ 30 min pour 16 paires).

## 5. Démarrer

```bash
chmod +x ~/BinanceSpotManager/CryptoSignalIntelligence/scripts/*.sh
~/BinanceSpotManager/CryptoSignalIntelligence/scripts/demarrer.sh --reconstruire
```

Les conteneurs redémarrent seuls après une panne ou un redémarrage du serveur (`restart:
unless-stopped`, Docker lancé au démarrage). Ils continuent après la fermeture de la session SSH.

## 6. Ouvrir le site depuis le PC (tunnel SSH)

Le site n'a pas de mot de passe : il n'est **jamais** ouvert sur Internet (ports liés à `127.0.0.1`
du serveur). On y accède par un tunnel chiffré, depuis PowerShell sur le PC :

```powershell
ssh -N -L 8501:127.0.0.1:8501 -L 8502:127.0.0.1:8502 -L 8503:127.0.0.1:8503 csi@ADRESSE
```

Laisser cette fenêtre ouverte, puis ouvrir **http://127.0.0.1:8501** (le site) et
http://127.0.0.1:8502/dashboard.html (tableau de bord de CSI), http://127.0.0.1:8503/ (analyse CSI). Fermer la fenêtre ferme l'accès, pas
les programmes. Ne jamais remplacer `127.0.0.1` par `0.0.0.0` dans les `docker-compose.yml`.

## 7. Sauvegardes automatiques

```bash
crontab -e
# chaque nuit à 3 h 30 : sauvegarde des deux projets, 14 jours conservés
30 3 * * * /home/csi/BinanceSpotManager/CryptoSignalIntelligence/scripts/sauvegarde.sh >> /home/csi/sauvegarde.log 2>&1
```

Le worker du bot est arrêté quelques secondes pendant la copie (ses ordres au repos chez Binance ne
bougent pas). Copier de temps en temps `~/sauvegardes` ailleurs (PC, stockage externe) : une
sauvegarde sur le même serveur ne protège pas d'une perte du serveur. Restaurer = `importer-donnees.sh`
sur le dossier des archives (renommées sans la date) avec `--forcer`, services arrêtés.

## 8. Mettre à jour

```bash
cd ~/BinanceSpotManager/CryptoSignalIntelligence && git pull
cd ~/BinanceSpotManager/BinanceSpotManager && git pull
~/BinanceSpotManager/CryptoSignalIntelligence/scripts/demarrer.sh --reconstruire
```

## 9. Vérifier que tout va bien

- Site → page **CSI** : voyant vert « CSI fonctionne ».
- `cd ~/BinanceSpotManager/CryptoSignalIntelligence && docker compose ps` : tout « healthy ».
- Journaux : `docker compose logs --tail 50 monitor` (CSI), `docker compose logs --tail 50 worker` (bot).
- Mémoire : `docker stats --no-stream`.

Travaux de recherche lourds sur le serveur (seulement avec 8 Go de RAM, un à la fois) :
`docker compose run --rm tools walk-forward` (limité à 2 Go par `mem_limit`).
