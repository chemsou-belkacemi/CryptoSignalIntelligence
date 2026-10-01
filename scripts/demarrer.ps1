# Démarre tout : CryptoSignalIntelligence (le cerveau) puis BinanceSpotManager (le bot, Binance Demo).
#
# 1. crée le réseau Docker partagé csi-bridge s'il manque ;
# 2. démarre CSI (surveillance, tableau de bord, API) puis BSM (interface et worker) ;
# 3. attend que chaque service soit « healthy » (5 min au plus) ;
# 4. affiche les adresses utiles.
#
# Usage (depuis le dossier de CSI) :
#   .\scripts\demarrer.ps1              # démarre avec les images existantes
#   .\scripts\demarrer.ps1 -Reconstruire  # reconstruit les images d'abord (après une mise à jour du code)
# BinanceSpotManager doit se trouver à côté : ..\BinanceSpotManager

param([switch]$Reconstruire)
$ErrorActionPreference = "Stop"
$csi = Split-Path -Parent $PSScriptRoot
$bsm = Join-Path (Split-Path -Parent $csi) "BinanceSpotManager"
if (-not (Test-Path (Join-Path $bsm "docker-compose.yml"))) { throw "BinanceSpotManager introuvable : $bsm" }

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker ne répond pas : lancer Docker Desktop puis recommencer." }

docker network inspect csi-bridge *> $null
if ($LASTEXITCODE -ne 0) {
    docker network create csi-bridge | Out-Null
    Write-Host "Réseau csi-bridge créé."
}

function Start-Projet([string]$nom, [string]$dossier) {
    Write-Host "Démarrage de $nom…"
    Push-Location $dossier
    try {
        $arguments = @("compose", "up", "-d")
        if ($Reconstruire) { $arguments += "--build" }
        docker @arguments
        if ($LASTEXITCODE -ne 0) { throw "$nom : échec du démarrage (code $LASTEXITCODE)" }
    } finally { Pop-Location }
}

function Wait-Sains([string]$nom, [string]$dossier) {
    Push-Location $dossier
    try {
        $limite = (Get-Date).AddMinutes(5)
        do {
            $etats = docker compose ps --format "{{.Service}}={{.Health}}" | Where-Object { $_ }
            $pasPrets = @($etats | Where-Object { $_ -notmatch "=healthy$" })
            if ($pasPrets.Count -eq 0) { Write-Host "$nom : tout est sain ($($etats -join ', '))."; return $true }
            Start-Sleep -Seconds 5
        } while ((Get-Date) -lt $limite)
        Write-Warning "$nom : pas encore sain après 5 min : $($pasPrets -join ', '). Voir : docker compose logs"
        return $false
    } finally { Pop-Location }
}

Start-Projet "CSI" $csi
Start-Projet "BSM" $bsm
$okCsi = Wait-Sains "CSI" $csi
$okBsm = Wait-Sains "BSM" $bsm

Write-Host ""
Write-Host "Ton site (bot Binance Demo + page CSI) : http://127.0.0.1:8501"
Write-Host "Analyse CSI (paire, signal, suivi)     : http://127.0.0.1:8503/"
Write-Host "Tableau de bord de CSI                 : http://127.0.0.1:8502/dashboard.html"
if (-not ($okCsi -and $okBsm)) {
    Write-Host "Note : la surveillance de CSI n'est « healthy » qu'après son premier cycle (jusqu'à ~16 min)."
}
