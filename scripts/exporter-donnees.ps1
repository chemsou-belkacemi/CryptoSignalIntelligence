# PC → VPS : exporte l'état Docker des deux projets dans un dossier à copier sur le serveur.
#
# Volumes exportés : crypto-signal-intelligence_csi-state (bougies, registres, signaux, news),
# binance-spot-manager_bsm-data (positions, signaux reçus, commandes, réglages) et _bsm-logs.
#
# IMPORTANT : le bot et la surveillance sont ARRÊTÉS avant la copie (données cohérentes) et RESTENT
# arrêtés : deux bots sur le même compte Demo géreraient les mêmes positions deux fois, et deux
# lecteurs Telegram se voleraient les messages. Ne relancer le PC (scripts\demarrer.ps1) que si la
# mise en service du VPS échoue, jamais en même temps que lui.
#
# Usage (dossier de CSI) :  .\scripts\exporter-donnees.ps1            → .\migration\
#                           .\scripts\exporter-donnees.ps1 -Dossier D:\vers-vps
# Puis :  scp -r .\migration utilisateur@ADRESSE_DU_VPS:~/   (docs/VPS.md)

param([string]$Dossier = "migration")
$ErrorActionPreference = "Stop"
$csi = Split-Path -Parent $PSScriptRoot
$bsm = Join-Path (Split-Path -Parent $csi) "BinanceSpotManager"
$cible = if ([IO.Path]::IsPathRooted($Dossier)) { $Dossier } else { Join-Path $csi $Dossier }
New-Item -ItemType Directory -Force -Path $cible | Out-Null
$image = "crypto-signal-intelligence:latest"
$volumes = @("crypto-signal-intelligence_csi-state", "binance-spot-manager_bsm-data", "binance-spot-manager_bsm-logs")

Write-Host "Arrêt du bot puis de CSI (fin propre des cycles en cours, jusqu'à 3 min)…"
Push-Location $bsm; try { docker compose stop } finally { Pop-Location }
Push-Location $csi; try { docker compose stop } finally { Pop-Location }

foreach ($volume in $volumes) {
    docker volume inspect $volume *> $null
    if ($LASTEXITCODE -ne 0) { Write-Warning "$volume absent : ignoré"; continue }
    Write-Host "Export de $volume…"
    docker run --rm --user root -v "${volume}:/v:ro" -v "${cible}:/out" $image `
        sh -c "tar czf /out/$volume.tar.gz --numeric-owner -C /v ."
    if ($LASTEXITCODE -ne 0) { throw "Export de $volume en échec" }
}
Get-ChildItem $cible -Filter *.tar.gz | Select-Object Name, @{n = "Mo"; e = { [math]::Round($_.Length / 1MB, 1) } } |
    Format-Table -AutoSize
Write-Host "Export terminé dans $cible. Le PC reste ARRÊTÉ (un seul bot à la fois)."
Write-Host "Copier aussi les deux fichiers .env (clés Demo, jeton Telegram, jeton CSI) : ils ne sont pas dans git."
