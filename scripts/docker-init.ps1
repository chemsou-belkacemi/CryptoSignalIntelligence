# Installe CryptoSignalIntelligence dans Docker Desktop (surveillance shadow + tableau de bord).
#
# 1. construit l'image ;
# 2. crée le volume persistant (par Compose, avec ses étiquettes) ;
# 3. copie l'état local utile dans le volume : bougies, registre des archives, historique des
#    actualités, registre des expériences, signaux Telegram évalués. Les archives zip brutes ne sont
#    pas copiées (re-téléchargeables) ; le registre des signaux publiés non plus (il contient des
#    chemins Windows absolus) : la surveillance Docker repart d'un registre vide ;
# 4. démarre la surveillance et le tableau de bord (http://127.0.0.1:8502/dashboard.html).
#
# Refuse d'écraser un volume qui contient déjà des bougies, sauf avec -Force.
# Usage (depuis le dossier du projet) :  .\scripts\docker-init.ps1

param([switch]$Force)
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$volume = "crypto-signal-intelligence_csi-state"
Set-Location $project

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" }
}

Invoke-Checked "docker compose build" { docker compose build }
Invoke-Checked "création du volume" { docker compose run --rm tools doctor --offline | Out-Null }

$existing = docker run --rm -v "${volume}:/srv/csi" crypto-signal-intelligence:latest sh -c "find /srv/csi/data/candles -name '*.parquet' 2>/dev/null | wc -l"
if ([int]$existing -gt 0 -and -not $Force) {
    Write-Host "Le volume contient déjà $existing séries de bougies : rien n'est copié (utiliser -Force pour écraser)."
} else {
    if (-not (Test-Path (Join-Path $project "data\candles"))) {
        throw "Aucune donnée locale : lancer d'abord 'download' en local, ou 'docker compose run --rm tools download'."
    }
    $copy = "set -e; mkdir -p /srv/csi/data/raw /srv/csi/signals; cp -a /src/data/candles /srv/csi/data/; " +
            "[ -f /src/data/raw/archives.sqlite3 ] && cp /src/data/raw/archives.sqlite3 /srv/csi/data/raw/ || true; " +
            "for d in news experiments; do [ -d /src/`$d ] && cp -a /src/`$d /srv/csi/ || true; done; " +
            "for f in external.sqlite3 feedback.sqlite3; do [ -f /src/signals/`$f ] && cp /src/signals/`$f /srv/csi/signals/ || true; done; " +
            "chown -R 10002:10002 /srv/csi; du -sh /srv/csi/data"
    Invoke-Checked "copie de l'état local" {
        docker run --rm --user root -v "${volume}:/srv/csi" -v "${project}:/src:ro" crypto-signal-intelligence:latest sh -c $copy
    }
}

Invoke-Checked "démarrage" { docker compose up -d monitor dashboard }
docker compose ps
Write-Host ""
Write-Host "Tableau de bord : http://127.0.0.1:8502/dashboard.html"
Write-Host "Santé : 'healthy' après le premier cycle (au plus ~16 minutes)."
