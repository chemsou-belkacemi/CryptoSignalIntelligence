# Évalue un signal Telegram avec CryptoSignalIntelligence dans Docker (état de la surveillance).
#
# L'évaluation est enregistrée dans le volume Docker : la surveillance la résout ensuite toute seule
# (TP1, stop, non rempli) et le bilan du groupe apparaît dans le tableau de bord
# (http://127.0.0.1:8502/dashboard.html). Rien n'est exécuté : aucun ordre, aucune clé.
#
# Exemples (depuis le dossier du projet) :
#   .\scripts\evaluer-signal.ps1 -Source "Suhaib" -Fichier signal.txt
#   .\scripts\evaluer-signal.ps1 -Source "ABK"                  # colle le texte, puis Ctrl+Z et Entrée
#   Get-Clipboard | .\scripts\evaluer-signal.ps1 -Source "Cleo" # texte copié dans le presse-papiers

param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$Fichier,
    [switch]$SansEnregistrer,
    [Parameter(ValueFromPipeline = $true)][string[]]$Texte
)
begin { $lignes = New-Object System.Collections.Generic.List[string] }
process { if ($Texte) { $lignes.AddRange($Texte) } }
end {
    $projet = Split-Path -Parent $PSScriptRoot
    Set-Location $projet
    if ($Fichier) {
        $contenu = [IO.File]::ReadAllText((Resolve-Path $Fichier), [Text.Encoding]::UTF8)
    } elseif ($lignes.Count -gt 0) {
        $contenu = $lignes -join "`n"
    } else {
        Write-Host "Colle le signal, puis Ctrl+Z et Entrée :"
        $contenu = [Console]::In.ReadToEnd()
    }
    if (-not $contenu.Trim()) { throw "Signal vide." }
    $arguments = @("compose", "run", "--rm", "-T", "tools", "evaluate-signal", "--source", $Source)
    if ($SansEnregistrer) { $arguments += "--no-record" }
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
    $contenu | docker @arguments
}
