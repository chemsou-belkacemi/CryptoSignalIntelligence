#!/usr/bin/env bash
# VPS : importe l'état exporté par scripts/exporter-donnees.ps1 dans les volumes Docker du serveur.
#
#   ./scripts/importer-donnees.sh ~/migration            # refuse d'écraser un volume déjà rempli
#   ./scripts/importer-donnees.sh ~/migration --forcer   # écrase (après vérification !)
#
# À lancer AVANT le premier démarrage (scripts/demarrer.sh), les services arrêtés.
set -euo pipefail

SOURCE="${1:?dossier de migration attendu (ex. ~/migration)}"
FORCE="${2:-}"
CSI="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${IMAGE:-python:3.14-slim}"
PREFIXE="${PREFIXE:-}"   # tests : volumes préfixés pour ne pas toucher aux vrais
VOLUMES="crypto-signal-intelligence_csi-state binance-spot-manager_bsm-data binance-spot-manager_bsm-logs"
SOURCE_ABS="$(cd "$SOURCE" && pwd)"
# Git Bash sous Windows (tests) : Docker Desktop attend un chemin Windows ; sans effet sur Linux.
if command -v cygpath >/dev/null 2>&1; then SOURCE_ABS="$(cygpath -w "$SOURCE_ABS")"; export MSYS_NO_PATHCONV=1; fi

for volume in $VOLUMES; do
    archive="$SOURCE/$volume.tar.gz"
    cible="$PREFIXE$volume"
    if [ ! -f "$archive" ]; then echo "absent : $archive (ignoré)"; continue; fi
    docker volume create "$cible" >/dev/null
    contenu="$(docker run --rm -v "$cible:/v" "$IMAGE" sh -c 'ls -A /v | wc -l')"
    if [ "$contenu" -gt 0 ] && [ "$FORCE" != "--forcer" ]; then
        echo "$cible n'est pas vide : rien n'est écrasé (ajouter --forcer après vérification)." >&2
        exit 1
    fi
    echo "Import de $volume → $cible…"
    docker run --rm --user root -v "$cible:/v" -v "$SOURCE_ABS:/in:ro" "$IMAGE" \
        sh -c "find /v -mindepth 1 -delete && tar xzf /in/$volume.tar.gz --numeric-owner -C /v"
done
# Verrous et états d'exécution du PC : sans objet sur le serveur (ils seraient pris pour un processus actif).
docker run --rm --user root -v "${PREFIXE}crypto-signal-intelligence_csi-state:/v" "$IMAGE" \
    sh -c "rm -f /v/state/run.lock" || true
docker run --rm --user root -v "${PREFIXE}binance-spot-manager_bsm-data:/v" "$IMAGE" \
    sh -c "rm -f /v/bot_worker.lock /v/bot_runtime.json" || true
echo "Import terminé. Démarrer avec : $CSI/scripts/demarrer.sh --reconstruire"
