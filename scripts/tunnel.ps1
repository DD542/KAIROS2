# Lance (ou relance) le tunnel Cloudflare qui expose Kairos sur Internet.
# Usage :  .\scripts\tunnel.ps1        (depuis le dossier kairos)
# L'URL publique change a chaque relance — elle s'affiche ci-dessous.
# Le tunnel vit tant que la fenetre/le processus cloudflared tourne et que le PC est allume.

$root = Split-Path -Parent $PSScriptRoot
$exe  = Join-Path $root "tools\cloudflared.exe"
$log  = Join-Path $root "tools\tunnel.log"

if (-not (Test-Path $exe)) {
    Write-Host "cloudflared.exe introuvable - telechargement..."
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $exe
}

# stoppe un eventuel tunnel deja lance
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item $log -ErrorAction SilentlyContinue

Start-Process -FilePath $exe -ArgumentList "tunnel","--url","http://localhost:5000" -WindowStyle Hidden -RedirectStandardError $log
Write-Host "Tunnel en cours de creation..."
Start-Sleep -Seconds 12

$content = Get-Content $log -Raw -ErrorAction SilentlyContinue
if ($content -match "https://[a-z0-9-]+\.trycloudflare\.com") {
    Write-Host ""
    Write-Host "  URL PUBLIQUE : $($Matches[0])" -ForegroundColor Green
    Write-Host "  Mot de passe : celui de KAIROS_PASSWORD dans .env"
    Write-Host ""
    Write-Host "  (garde le PC allume ; pour arreter : Get-Process cloudflared | Stop-Process)"
} else {
    Write-Host "URL pas encore prete - relis le log : $log"
}