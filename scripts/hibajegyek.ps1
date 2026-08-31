# Hibajegyek lehuzasa / lezarasa az X-presso peldanyrol (operator API).
# A WFM_OPERATOR_TOKEN a Railway-rol jon, es SOSEM kerul a kepernyore.
#
# Hasznalat:
#   powershell -File scripts\hibajegyek.ps1                  # nyitott jegyek + kepek
#   powershell -File scripts\hibajegyek.ps1 -Statusz new     # csak adott statusz
#   powershell -File scripts\hibajegyek.ps1 -Lezar <id> -Megjegyzes "Javitva a ..."
param(
    [string]$Lezar,
    [string]$Megjegyzes = "",
    [string]$Statusz = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$base = "https://backend-production-f124.up.railway.app"

# token beszerzese a linkelt backend-projektbol — kepernyore nem irjuk
# (a railway CLI stderr-zaja miatt itt nem Stop modban futunk)
Push-Location (Join-Path $PSScriptRoot "..\backend")
$ErrorActionPreference = "Continue"
$kv = cmd /c "railway variables --kv 2>nul"
$ErrorActionPreference = "Stop"
Pop-Location
$line = $kv | Where-Object { $_ -match '^WFM_OPERATOR_TOKEN=' } | Select-Object -First 1
if (-not $line) { Write-Error "Nem talalom a WFM_OPERATOR_TOKEN-t (railway link?)"; exit 1 }
$tok = $line.ToString().Split("=", 2)[1].Trim()
$headers = @{ "X-Operator-Token" = $tok }

if ($Lezar) {
    $body = @{ status = "resolved"; resolution_note = $Megjegyzes } |
        ConvertTo-Json -Compress
    $out = Invoke-RestMethod -Method Patch -Uri "$base/api/operator/bugs/$Lezar" `
        -Headers $headers -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($body))
    Write-Output ("LEZARVA (resolved): " + $out.id + " - " + $out.description.Substring(0, [Math]::Min(60, $out.description.Length)))
    exit 0
}

$statuses = if ($Statusz) { @($Statusz) } else { @("new", "confirmed", "reopened") }
$bugs = @()
foreach ($s in $statuses) {
    $bugs += Invoke-RestMethod -Uri "$base/api/operator/bugs?status=$s" -Headers $headers
}
if ($bugs.Count -eq 0) { Write-Output "Nincs nyitott hibajegy. :)"; exit 0 }

$shotDir = Join-Path $env:TEMP "iwfm-hibak"
New-Item -ItemType Directory -Force $shotDir | Out-Null

$sev = @{ blocker = "BLOKKOLO"; major = "SULYOS"; minor = "KISEBB"; cosmetic = "KOZMETIKAI" }
foreach ($b in $bugs) {
    Write-Output "=================================================="
    Write-Output ("HIBAJEGY: " + $b.id)
    $sevName = if ($sev.ContainsKey($b.severity)) { $sev[$b.severity] } else { $b.severity }
    Write-Output ("Statusz: " + $b.status + " | Sulyossag: " + $sevName + " | Bejelento: " + $b.reporter_name + " | " + $b.created_at)
    Write-Output ("Oldal: " + $b.page_url)
    if ($b.user_agent) { Write-Output ("Bongeszo: " + $b.user_agent) }
    if ($b.fix_group) { Write-Output ("Koteg: " + $b.fix_group) }
    Write-Output "Leiras:"
    Write-Output $b.description
    if ($b.has_screenshot) {
        $file = Join-Path $shotDir ($b.id + ".png")
        Invoke-WebRequest -Uri "$base/api/operator/bugs/$($b.id)/screenshot" `
            -Headers $headers -OutFile $file | Out-Null
        Write-Output ("Kepernyokep: " + $file)
    }
    Write-Output ""
}
Write-Output ("OSSZESEN: " + $bugs.Count + " nyitott jegy")
