# Uj X-admin ugyfel-peldany felhuzasa a Railway-re — futtasd a repo gyokerebol:
#   powershell -File scripts\uj-ugyfel.ps1 -Nev "kave-kft"
#
# Amit csinal:
#   1. uj Railway-projekt (a nev alapjan) + Postgres adatbazis
#   2. backend- es frontend-service letrehozasa, titkos kulcsok generalasa
#      (a kulcsok CSAK a Railway-re kerulnek, a kepernyon nem jelennek meg)
#   3. deploy mindket service-re, majd kiirja a belepesi linket
#
# Utana: nyisd meg a frontend URL-t → "Elso inditas" → a beuzemelo varazslo
# vegigvezet (cegadatok, telephely, kollegak). A licenc-savot a Beallitasok →
# Licenc fulon allitsd be (alapbol korlatlan!).

param(
    [Parameter(Mandatory = $true)][string]$Nev
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "backend\railway.json"))) {
    Write-Error "A szkriptet a repo scripts\ mappajabol futtasd (backend/ nem talalhato)."
}

function Invoke-Retry([scriptblock]$Block, [string]$Label, [int]$Tries = 5) {
    foreach ($i in 1..$Tries) {
        try { & $Block; return } catch {
            if ($i -eq $Tries) { throw "$Label sikertelen $Tries probalkozas utan: $_" }
            Write-Host "  $Label ujraprobalas ($i)..." -ForegroundColor Yellow
            Start-Sleep -Seconds 6
        }
    }
}

Write-Host "== 1/4 Railway-projekt: $Nev ==" -ForegroundColor Cyan
Invoke-Retry { railway init --name $Nev | Out-Null } "railway init"
Invoke-Retry { railway add --database postgres | Out-Null } "postgres hozzaadasa"

Write-Host "== 2/4 Titkos kulcsok generalasa (nem jelennek meg) ==" -ForegroundColor Cyan
$py = Join-Path $repo "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$secret = & $py -c "import secrets;print(secrets.token_urlsafe(48))"
$encKey = & $py -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
$bootTok = & $py -c "import secrets;print(secrets.token_urlsafe(24))"

Write-Host "== 3/4 Backend-service ==" -ForegroundColor Cyan
Set-Location (Join-Path $repo "backend")
Invoke-Retry { railway add --service backend | Out-Null } "backend service"
Invoke-Retry { railway link --service backend | Out-Null } "backend link"
Invoke-Retry {
    railway variables --service backend `
        --set "WFM_ENVIRONMENT=production" `
        --set "WFM_SECRET_KEY=$secret" `
        --set "WFM_ENC_KEY=$encKey" `
        --set "WFM_BOOTSTRAP_TOKEN=$bootTok" `
        --set "WFM_COOKIE_SECURE=true" | Out-Null
} "backend valtozok"
Invoke-Retry { railway up --ci | Out-Null } "backend deploy"
$backendUrl = (railway domain --service backend 2>$null | Select-String -Pattern "https://\S+").Matches.Value
if (-not $backendUrl) { $backendUrl = Read-Host "Backend URL (Railway dashboardrol)" }

Write-Host "== 4/4 Frontend-service ==" -ForegroundColor Cyan
Set-Location (Join-Path $repo "frontend")
Invoke-Retry { railway add --service frontend | Out-Null } "frontend service"
Invoke-Retry { railway link --service frontend | Out-Null } "frontend link"
Invoke-Retry {
    railway variables --service frontend --set "API_PROXY_TARGET=$backendUrl" | Out-Null
} "frontend valtozok"
Invoke-Retry { railway up --ci | Out-Null } "frontend deploy"
$frontendUrl = (railway domain --service frontend 2>$null | Select-String -Pattern "https://\S+").Matches.Value

# A backend CORS-hoz kell a frontend origin — utolag allitjuk, majd ujraindul
Set-Location (Join-Path $repo "backend")
if ($frontendUrl) {
    Invoke-Retry {
        railway variables --service backend --set "WFM_FRONTEND_ORIGIN=$frontendUrl" | Out-Null
    } "frontend origin beallitasa"
}

Set-Location $repo
Write-Host ""
Write-Host "KESZ! 🎉" -ForegroundColor Green
Write-Host "  Frontend:  $frontendUrl"
Write-Host "  Backend:   $backendUrl"
Write-Host ""
Write-Host "Kovetkezo lepesek:"
Write-Host "  1. Nyisd meg a frontendet → 'Elso inditas' → admin-fiok letrehozasa"
Write-Host "     (a bootstrap-token a Railway dashboardon: WFM_BOOTSTRAP_TOKEN)"
Write-Host "  2. A beuzemelo varazslo vegigvezet a cegadatokon"
Write-Host "  3. Beallitasok → Licenc: allitsd be az ugyfel savjat (S/M/L/XL) es az ervenyesseget"
