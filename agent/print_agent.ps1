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
# Windows PowerShell 5.1 alapbol regi TLS-t hasznal - a szerverhez TLS 1.2 kell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $dir "print_agent.json"
$logPath = Join-Path $dir "print_agent.log"

function Log($msg) {
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

Log ("Ugynok elindult - szerver: {0}, nyomtato: {1}:{2}" -f $cfg.server, $cfg.printer_ip, $port)

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
    try {
        $sumatra = Find-Sumatra
        if ($sumatra) {
            if ($cfg.pdf_printer) {
                & $sumatra -print-to $cfg.pdf_printer -silent -exit-when-done $tmp
            } else {
                & $sumatra -print-to-default -silent -exit-when-done $tmp
            }
            Start-Sleep -Seconds 3
        } else {
            # tartalek: a .pdf-hez tarsitott program PrintTo/Print muvelete
            if ($cfg.pdf_printer) {
                Start-Process -FilePath $tmp -Verb PrintTo -ArgumentList ('"{0}"' -f $cfg.pdf_printer) -WindowStyle Hidden
            } else {
                Start-Process -FilePath $tmp -Verb Print -WindowStyle Hidden
            }
            Start-Sleep -Seconds 10
        }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
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
    Start-Sleep -Seconds 3
}
