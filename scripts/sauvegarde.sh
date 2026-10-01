#!/usr/bin/env bash
# VPS : sauvegarde nocturne des deux projets (à lancer par cron, docs/VPS.md).
#
#   ./scripts/sauvegarde.sh                  # → ~/sauvegardes, 14 jours conservés
#   DOSSIER=/mnt/backup GARDER=30 ./scripts/sauvegarde.sh
#
# Le worker du bot est arrêté le temps de copier ses données (quelques secondes, ordres au repos
# chez Binance inchangés) ; la surveillance de CSI est copiée à chaud : ses fichiers sont écrits de
# façon atomique (et le pire cas, une bougie récente, se recharge au cycle suivant).
set -euo pipefail

CSI="$(cd "$(dirname "$0")/.." && pwd)"
BSM="$(cd "$CSI/.." && pwd)/BinanceSpotManager"
DOSSIER="${DOSSIER:-$HOME/sauvegardes}"
GARDER="${GARDER:-14}"
IMAGE="${IMAGE:-python:3.14-slim}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DOSSIER"
DOSSIER_DOCKER="$DOSSIER"
if command -v cygpath >/dev/null 2>&1; then DOSSIER_DOCKER="$(cygpath -w "$DOSSIER")"; export MSYS_NO_PATHCONV=1; fi

sauver() {   # $1 = volume
    docker run --rm -v "$1:/v:ro" -v "$DOSSIER_DOCKER:/out" "$IMAGE" \
        sh -c "tar czf /out/$1-$STAMP.tar.gz --numeric-owner -C /v ."
}

sauver crypto-signal-intelligence_csi-state
(cd "$BSM" && docker compose stop worker >/dev/null)
trap '(cd "$BSM" && docker compose start worker >/dev/null)' EXIT
sauver binance-spot-manager_bsm-data
sauver binance-spot-manager_bsm-logs
(cd "$BSM" && docker compose start worker >/dev/null)
trap - EXIT

find "$DOSSIER" -name '*.tar.gz' -mtime +"$GARDER" -delete
echo "$(date -Is) sauvegarde $STAMP : $(du -sh "$DOSSIER" | cut -f1) dans $DOSSIER"
