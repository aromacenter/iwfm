# Iwfm nyomtato-ugynok - Godex cimkenyomtatas + irodai PDF-nyomtatas
#
# 3 masodpercenkent lekeri a szerverrol a varakozo feladatokat:
#  - cimke (EZPL): raw modban a Godex nyomtato 9100-as portjara megy
#  - PDF (pl. elismerveny, munkalap): a Windows-on beallitott nyomtatora megy
#    (igy TELEFONROL inditott nyomtatas is az irodai nyomtaton landol)
# A beallitasok a szkript melletti print_agent.json-bol jonnek:
#   { "server": "https://backend-....railway.app",
#     "agent_key": "A BEALLITASOKBAN GENERALT KULCS",
#     "printer_ip": "192.168.1.30", "printer_port": 9100,
#     "pdf_printer": "Samsung ML-3310 Series",   <- ures/hianyzo = alapertelmezett nyomtato
#     "sumatra_path": "" }                       <- opcionalis: SumatraPDF.exe teljes utja
#
# PDF-nyomtatashoz a legmegbizhatobb a SumatraPDF (ingyenes, hordozhato is jo):
# ha telepitve van (vagy a sumatra_path meg van adva), azzal nyomtatunk;
# kulonben az Adobe Reader "PrintTo" muveletevel probalkozunk.
#
# Inditas kezzel:  powershell -ExecutionPolicy Bypass -File print_agent.ps1
# Automatikus inditashoz futtasd egyszer a telepites.bat-ot.

$ErrorActionPreference = "Stop"
# ONFRISSITES: a szerver ennel nagyobb verziot hirdetve uj szkriptet ad,
# az ugynok leallitja magat, az orszem pedig az ujjal inditja ujra.
$script:AGENT_VERSION = 2
# Windows PowerShell 5.1 alapbol regi TLS-t hasznal - a szerverhez TLS 1.2 kell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $dir "print_agent.json"
$logPath = Join-Path $dir "print_agent.log"

function Log($msg) {
    # Log-rotacio: 5 MB felett a regi log felulirodik (a gep ne teljen meg)
    try {
        if ((Test-Path $logPath) -and ((Get-Item $logPath).Length -gt 5MB)) {
            Move-Item -Force $logPath ($logPath + ".old")
        }
    } catch {}
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logPath -Value $line -Encoding utf8
}

if (-not (Test-Path $configPath)) {
    Log "HIBA: nincs print_agent.json - masold a print_agent.json.minta alapjan!"
    exit 1
}

# Egypeldanyos zar: ha mar fut egy ugynok, ez a peldany csendben kilep
# (ket peldany minden cimket ketszer nyomtatna).
$script:mutex = New-Object System.Threading.Mutex($false, "Global\IwfmPrintAgent")
if (-not $script:mutex.WaitOne(0)) {
    Log "Mar fut egy ugynok-peldany - ez a peldany kilep."
    exit 0
}
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
if (-not $cfg.agent_key) { Log "HIBA: ures agent_key a print_agent.json-ban"; exit 1 }
$headers = @{ "X-Agent-Key" = $cfg.agent_key }
$port = if ($cfg.printer_port) { [int]$cfg.printer_port } else { 9100 }

Log ("Ugynok elindult (v{0}) - szerver: {1}, nyomtato: {2}:{3}" -f $script:AGENT_VERSION, $cfg.server, $cfg.printer_ip, $port)
$script:lastUpdateCheck = Get-Date

# SumatraPDF keresese: config-utvonal, majd a szokasos telepitesi helyek
function Find-Sumatra {
    if ($cfg.sumatra_path -and (Test-Path $cfg.sumatra_path)) { return $cfg.sumatra_path }
    $candidates = @(
        (Join-Path $dir "SumatraPDF.exe"),
        "$env:ProgramFiles\SumatraPDF\SumatraPDF.exe",
        "${env:ProgramFiles(x86)}\SumatraPDF\SumatraPDF.exe",
        "$env:LocalAppData\SumatraPDF\SumatraPDF.exe"
    )
    foreach ($p in $candidates) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

function Print-Pdf([string]$payloadB64, [string]$label) {
    $tmp = Join-Path $env:TEMP ("iwfm-print-" + [guid]::NewGuid().ToString("N") + ".pdf")
    [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($payloadB64))
    # GLS-cimke kulon nyomtatora mehet (pl. Brother cimkezo): "gls_printer" a
    # print_agent.json-ban; uresen a szokasos pdf_printer / alapertelmezett.
    $target = $cfg.pdf_printer
    if ($label -like "GLS*" -and $cfg.gls_printer) { $target = $cfg.gls_printer }
    try {
        $sumatra = Find-Sumatra
        if ($sumatra) {
            if ($target) {
                & $sumatra -print-to $target -silent -exit-when-done $tmp
            } else {
                & $sumatra -print-to-default -silent -exit-when-done $tmp
            }
            Start-Sleep -Seconds 3
        } else {
            # tartalek: a .pdf-hez tarsitott program PrintTo/Print muvelete.
            # A megnyitott olvaso-folyamatot a nyomtatas utan LEZARJUK -
            # kulonben minden nyomtatas utan ott maradna egy Adobe Reader,
            # ami iden mar lassithatja a gepet.
            $p = $null
            if ($target) {
                $p = Start-Process -FilePath $tmp -Verb PrintTo -ArgumentList ('"{0}"' -f $target) -WindowStyle Hidden -PassThru
            } else {
                $p = Start-Process -FilePath $tmp -Verb Print -WindowStyle Hidden -PassThru
            }
            Start-Sleep -Seconds 10
            try {
                if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
            } catch {}
        }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Check-SelfUpdate {
    # Onfrissites: ha a szerveren ujabb verzioju szkript van, letoltjuk,
    # a sajat fajlunkat lecsereljuk es kilepunk - az orszem (watchdog)
    # 5 percen belul az UJ szkripttel indit ujra. Biztonsag: csak ervenyes,
    # ugynok-jelzovel ellatott tartalmat irunk ki, ideiglenes fajlon at.
    try {
        $info = Invoke-RestMethod -Uri "$($cfg.server)/api/print-agent/script" `
            -Headers $headers -Method Get -TimeoutSec 20
        if (-not $info.version -or [int]$info.version -le $script:AGENT_VERSION) { return }
        $content = $info.script
        if (-not $content -or $content.Length -lt 1000 -or $content -notlike "*IwfmPrintAgent*") {
            Log ("Onfrissites kihagyva: gyanus tartalom (v{0})" -f $info.version)
            return
        }
        $self = $PSCommandPath
        if (-not $self) { $self = Join-Path $dir "print_agent.ps1" }
        $tmpFile = $self + ".new"
        [IO.File]::WriteAllText($tmpFile, $content, (New-Object Text.UTF8Encoding $false))
        Move-Item -Force $tmpFile $self
        Log ("ONFRISSITES: v{0} -> v{1} letoltve - ujraindulok az orszemmel." -f $script:AGENT_VERSION, $info.version)
        $script:mutex.ReleaseMutex()
        exit 0
    } catch {
        Log ("Onfrissites-ellenorzes nem sikerult (nem gond): {0}" -f $_.Exception.Message)
    }
}

function Send-ToPrinter([string]$payload) {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect($cfg.printer_ip, $port, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne(5000)) {
        $client.Close()
        throw "a nyomtato ($($cfg.printer_ip):$port) nem erheto el"
    }
    $client.EndConnect($async)
    try {
        $stream = $client.GetStream()
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($payload)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        Start-Sleep -Milliseconds 500
    } finally {
        $client.Close()
    }
}

# Indulaskor is ellenorizzuk, van-e ujabb szkript a szerveren
Check-SelfUpdate

while ($true) {
    try {
        $resp = Invoke-RestMethod -Uri "$($cfg.server)/api/print-agent/jobs" `
            -Headers $headers -Method Get -TimeoutSec 15
        foreach ($job in $resp.jobs) {
            $ok = $true; $err = $null
            try {
                if ($job.kind -eq "pdf") {
                    Print-Pdf $job.payload $job.label
                } else {
                    Send-ToPrinter $job.payload
                }
                Log ("Nyomtatva: {0} ({1})" -f $job.label, $job.id)
            } catch {
                $err = $_.Exception.Message
                if ($err -like "*nem erheto el*") {
                    # a nyomtato most nincs elerheto halozaton - a feladat a
                    # sorban marad, kesobb ujraprobaljuk (nem jelezzuk hibanak)
                    Log ("Nyomtato nem erheto el - a sorban marad: {0}" -f $job.label)
                    Start-Sleep -Seconds 30
                    continue
                }
                $ok = $false
                Log ("NYOMTATASI HIBA: {0} - {1}" -f $job.label, $err)
            }
            $body = @{ ok = $ok; error = $err } | ConvertTo-Json
            Invoke-RestMethod -Uri "$($cfg.server)/api/print-agent/jobs/$($job.id)" `
                -Headers $headers -Method Post -ContentType "application/json" `
                -Body $body -TimeoutSec 15 | Out-Null
        }
    } catch {
        Log ("Szerver-hiba (ujraprobalom): {0}" -f $_.Exception.Message)
        Start-Sleep -Seconds 20
    }
    # Onfrissites-ellenorzes 10 percenkent (indulaskor is lefutott)
    if (((Get-Date) - $script:lastUpdateCheck).TotalMinutes -ge 10) {
        $script:lastUpdateCheck = Get-Date
        Check-SelfUpdate
    }
    Start-Sleep -Seconds 3
}
