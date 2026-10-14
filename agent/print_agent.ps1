# Iwfm nyomtato-ugynok v3 - Godex cimkenyomtatas + irodai PDF-nyomtatas
#
# v3: PEHELYSULYU mukodes - a gep teljesitmenyet nem befolyasolja:
#  - LONG-POLL: a szerver tartja a kerest max 25 mp-ig es azonnal valaszol,
#    ha munka jon -> oriank ~100 keres a korabbi ~1200 helyett, a varakozas
#    a halozaton "alszik", nem a processzoron
#  - egyetlen ujrahasznositott HttpClient az Invoke-RestMethod helyett
#    (nincs PSObject-varazslas es kapcsolat-ujraepites minden hivasnal)
#  - BelowNormal prioritas + idonkenti szemetgyujtes (nem hizik a memoria)
#  - az orszem-feladatot 15 percre ritkitja es -NoProfile inditasra allitja
#
# A beallitasok a szkript melletti print_agent.json-bol jonnek:
#   { "server": "https://backend-....railway.app",
#     "agent_key": "A BEALLITASOKBAN GENERALT KULCS",
#     "printer_ip": "192.168.1.30", "printer_port": 9100,
#     "pdf_printer": "Samsung ML-3310 Series",   <- ures = alapertelmezett
#     "sumatra_path": "",                        <- opcionalis: SumatraPDF.exe utja
#     "gls_printer": "" }                        <- GLS-cimkek kulon nyomtatoja
#
# Inditas kezzel:  powershell -ExecutionPolicy Bypass -File print_agent.ps1
# Automatikus inditashoz futtasd egyszer a telepites.bat-ot.

$ErrorActionPreference = "Stop"
# ONFRISSITES: a szerver ennel nagyobb verziot hirdetve uj szkriptet ad,
# az ugynok leallitja magat, az orszem pedig az ujjal inditja ujra.
$script:AGENT_VERSION = 3
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

# Egypeldanyos zar ELORE: ha mar fut egy ugynok, az orszem altal inditott uj
# peldany a leheto legolcsobban, azonnal kilep (semmit nem tolt be).
$script:mutex = New-Object System.Threading.Mutex($false, "Global\IwfmPrintAgent")
if (-not $script:mutex.WaitOne(0)) { exit 0 }

if (-not (Test-Path $configPath)) {
    Log "HIBA: nincs print_agent.json - masold a print_agent.json.minta alapjan!"
    exit 1
}
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
if (-not $cfg.agent_key) { Log "HIBA: ures agent_key a print_agent.json-ban"; exit 1 }
$port = if ($cfg.printer_port) { [int]$cfg.printer_port } else { 9100 }

# A gep teljesitmenyet nem vesszuk el: hatterbe soroljuk magunkat.
try { (Get-Process -Id $PID).PriorityClass = "BelowNormal" } catch {}

# Egyetlen, ujrahasznositott HTTP-kliens (keep-alive) - ez a v3 lelke.
Add-Type -AssemblyName System.Net.Http
$script:http = New-Object System.Net.Http.HttpClient
$script:http.Timeout = [TimeSpan]::FromSeconds(45)
[void]$script:http.DefaultRequestHeaders.TryAddWithoutValidation("X-Agent-Key", [string]$cfg.agent_key)
# Verzio-jelentes: a szerveren latszik, hogy a frissites letelepult-e
[void]$script:http.DefaultRequestHeaders.TryAddWithoutValidation("X-Agent-Version", [string]$script:AGENT_VERSION)

function Http-GetJson([string]$url) {
    $resp = $script:http.GetAsync($url).GetAwaiter().GetResult()
    try {
        if (-not $resp.IsSuccessStatusCode) { throw ("HTTP {0}" -f [int]$resp.StatusCode) }
        $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return ($text | ConvertFrom-Json)
    } finally { $resp.Dispose() }
}

function Http-PostJson([string]$url, [string]$json) {
    $content = New-Object System.Net.Http.StringContent($json, [Text.Encoding]::UTF8, "application/json")
    $resp = $script:http.PostAsync($url, $content).GetAwaiter().GetResult()
    try {
        if (-not $resp.IsSuccessStatusCode) { throw ("HTTP {0}" -f [int]$resp.StatusCode) }
    } finally { $resp.Dispose(); $content.Dispose() }
}

Log ("Ugynok elindult (v{0}) - szerver: {1}, nyomtato: {2}:{3}" -f $script:AGENT_VERSION, $cfg.server, $cfg.printer_ip, $port)
$script:lastUpdateCheck = Get-Date
$script:lastGc = Get-Date

# Az utemezett feladatok korszerusitese (best effort): -NoProfile inditas +
# az orszem 5 perc helyett 15 percenkent - igy a watchdog-inditasok sem
# terhelik a gepet. Ha nincs hozza jog, marad a regi (nem hiba).
function Ensure-Tasks {
    try {
        $tr = 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
        schtasks /create /f /tn "Iwfm nyomtato-ugynok" /sc onlogon /tr $tr 2>$null | Out-Null
        schtasks /create /f /tn "Iwfm nyomtato-ugynok orszem" /sc minute /mo 15 /tr $tr 2>$null | Out-Null
        Log "Utemezett feladatok frissitve (-NoProfile, orszem 15 percenkent)."
    } catch {
        Log ("Feladat-frissites kihagyva (nem gond): {0}" -f $_.Exception.Message)
    }
}
Ensure-Tasks

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
    # GLS-cimke kulon nyomtatora mehet (pl. Brother cimkezo)
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
            # tartalek: a .pdf-hez tarsitott program PrintTo/Print muvelete;
            # a megnyitott olvaso-folyamatot a nyomtatas utan LEZARJUK.
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
    # a sajat fajlunkat lecsereljuk es kilepunk - az orszem az UJ szkripttel
    # indit ujra. Biztonsag: csak ervenyes, ugynok-jelzovel ellatott
    # tartalmat irunk ki, ideiglenes fajlon at.
    try {
        $info = Http-GetJson "$($cfg.server)/api/print-agent/script"
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
        # LONG-POLL: a szerver max 25 mp-ig tartja a kerest, es AZONNAL
        # valaszol, ha munka erkezik - kozben ez a folyamat a halozaton
        # varakozik, processzort nem hasznal.
        $resp = Http-GetJson "$($cfg.server)/api/print-agent/jobs?wait=25"
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
                    # a nyomtato most nincs halozaton - a feladat a sorban
                    # marad, kesobb ujraprobaljuk (nem jelezzuk hibanak)
                    Log ("Nyomtato nem erheto el - a sorban marad: {0}" -f $job.label)
                    Start-Sleep -Seconds 30
                    continue
                }
                $ok = $false
                Log ("NYOMTATASI HIBA: {0} - {1}" -f $job.label, $err)
            }
            $body = @{ ok = $ok; error = $err } | ConvertTo-Json
            Http-PostJson "$($cfg.server)/api/print-agent/jobs/$($job.id)" $body
        }
    } catch {
        Log ("Szerver-hiba (ujraprobalom): {0}" -f $_.Exception.Message)
        Start-Sleep -Seconds 20
    }
    # Onfrissites-ellenorzes 30 percenkent (indulaskor is lefutott)
    if (((Get-Date) - $script:lastUpdateCheck).TotalMinutes -ge 30) {
        $script:lastUpdateCheck = Get-Date
        Check-SelfUpdate
    }
    # Memoria-takaritas 10 percenkent - a hosszan futo PowerShell ne hizzon.
    if (((Get-Date) - $script:lastGc).TotalMinutes -ge 10) {
        $script:lastGc = Get-Date
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    # A szerver mar vart 25 mp-ot - ket keres kozott eleg egy pici szunet.
    Start-Sleep -Seconds 1
}
