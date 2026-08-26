# Iwfm nyomtato-ugynok - Godex cimkenyomtatas
#
# 3 masodpercenkent lekeri a szerverrol a varakozo cimkeket, es raw modban
# a Godex nyomtato 9100-as portjara kuldi oket. A beallitasok a szkript
# melletti print_agent.json-bol jonnek:
#   { "server": "https://backend-....railway.app",
#     "agent_key": "A BEALLITASOKBAN GENERALT KULCS",
#     "printer_ip": "192.168.1.30", "printer_port": 9100 }
#
# Inditas kezzel:  powershell -ExecutionPolicy Bypass -File print_agent.ps1
# Automatikus inditashoz futtasd egyszer a telepites.bat-ot.

$ErrorActionPreference = "Stop"
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
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
if (-not $cfg.agent_key) { Log "HIBA: ures agent_key a print_agent.json-ban"; exit 1 }
$headers = @{ "X-Agent-Key" = $cfg.agent_key }
$port = if ($cfg.printer_port) { [int]$cfg.printer_port } else { 9100 }

Log ("Ugynok elindult - szerver: {0}, nyomtato: {1}:{2}" -f $cfg.server, $cfg.printer_ip, $port)

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
                Send-ToPrinter $job.payload
                Log ("Nyomtatva: {0} ({1})" -f $job.label, $job.id)
            } catch {
                $ok = $false; $err = $_.Exception.Message
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
