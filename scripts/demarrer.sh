#!/usr/bin/env bash
# Démarre tout sur un serveur Linux (VPS) : CryptoSignalIntelligence (le cerveau) puis
# BinanceSpotManager (le bot, Binance Demo). Équivalent de scripts/demarrer.ps1 (Windows).
#
#   ./scripts/demarrer.sh                 # images existantes
#   ./scripts/demarrer.sh --reconstruire  # après une mise à jour du code (git pull)
#
# BinanceSpotManager doit se trouver à côté : ../BinanceSpotManager
# Les ports restent publiés sur 127.0.0.1 du serveur : accès par tunnel SSH (docs/VPS.md).
set -euo pipefail

CSI="$(cd "$(dirname "$0")/.." && pwd)"
BSM="$(cd "$CSI/.." && pwd)/BinanceSpotManager"
REBUILD=""
[ "${1:-}" = "--reconstruire" ] && REBUILD="--build"
[ -f "$BSM/docker-compose.yml" ] || { echo "BinanceSpotManager introuvable : $BSM" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker ne répond pas (sudo systemctl start docker ?)" >&2; exit 1; }

if ! docker network inspect csi-bridge >/dev/null 2>&1; then
    docker network create csi-bridge >/dev/null
    echo "Réseau csi-bridge créé."
fi

for projet in "$CSI" "$BSM"; do
    echo "Démarrage de $(basename "$projet")…"
    (cd "$projet" && docker compose up -d $REBUILD)
done

attendre_sains() {   # $1 = dossier du projet ; 5 minutes au plus
    local fin=$((SECONDS + 300)) etats pas_prets
    while :; do
        etats="$(cd "$1" && docker compose ps --format '{{.Service}}={{.Health}}')"
        pas_prets="$(printf '%s\n' "$etats" | grep -v '=healthy$' | grep . || true)"
        if [ -z "$pas_prets" ]; then echo "$(basename "$1") : tout est sain."; return 0; fi
        if [ "$SECONDS" -ge "$fin" ]; then
            echo "$(basename "$1") : pas encore sain après 5 min : $(echo $pas_prets) (docker compose logs)" >&2
            return 1
        fi
        sleep 5
    done
}

ok=0
attendre_sains "$CSI" || ok=1
attendre_sains "$BSM" || ok=1

cat <<'TEXTE'

Depuis ton PC (PowerShell), ouvre le tunnel puis le navigateur :
  ssh -N -L 8501:127.0.0.1:8501 -L 8502:127.0.0.1:8502 -L 8503:127.0.0.1:8503 utilisateur@ADRESSE_DU_VPS
  Site (bot + page CSI)  : http://127.0.0.1:8501
  Analyse CSI            : http://127.0.0.1:8503/
  Tableau de bord de CSI : http://127.0.0.1:8502/dashboard.html
TEXTE
[ "$ok" -eq 0 ] || echo "Note : la surveillance de CSI n'est « healthy » qu'après son premier cycle (jusqu'à ~16 min)."
