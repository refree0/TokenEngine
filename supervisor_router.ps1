# TokenEngine Router Supervisor (ASCII-only for PowerShell 5.1 compatibility)
# Polls /health every 15s; if router is down or hung, kills stale instances and restarts it.
# Launched hidden at user logon by scheduled task "TokenEngine Router".
$ErrorActionPreference = 'SilentlyContinue'
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$PY   = 'pythonw'
$LOG  = Join-Path $ROOT 'logs\supervisor.log'

function Log($m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
    try { Add-Content -Path $LOG -Value $line -Encoding UTF8 } catch {}
}

# Single-instance guard via global named mutex
$mutex = New-Object System.Threading.Mutex($false, 'Global\TokenEngineSupervisor')
if (-not $mutex.WaitOne(0)) {
    Log 'another supervisor instance holds the mutex, exit'
    exit
}

Log "supervisor started (pid $PID)"
while ($true) {
    Start-Sleep -Seconds 15
    $healthy = $false
    try {
        $r = & curl.exe -s --noproxy "*" --max-time 8 http://127.0.0.1:8317/health
        if ($r -match '"ok"\s*:\s*true') { $healthy = $true }
    } catch {
        Log "health probe error: $_"
    }

    if ($healthy) { continue }

    Log 'router unhealthy, restarting...'
    # Kill every stale router process (hung or duplicate) to avoid SO_REUSEADDR conflicts
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match 'TokenEngine\\router\.py' } |
        ForEach-Object { Log "killing stale router pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
    Start-Process -FilePath $PY -ArgumentList "$PSScriptRoot\router.py" -WorkingDirectory $ROOT
    Log 'router launch issued, waiting...'
    Start-Sleep -Seconds 10
    try {
        $r2 = & curl.exe -s --noproxy "*" --max-time 8 http://127.0.0.1:8317/health
        if ($r2 -match '"ok"\s*:\s*true') { Log 'router recovered OK' }
        else { Log "router still unhealthy after restart: $r2" }
    } catch { Log "post-restart probe error: $_" }
}
