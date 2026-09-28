$ErrorActionPreference = 'SilentlyContinue'
# 重启 TokenEngine Router：先杀全部 router 进程，再启动一个（防多实例抢占 8317）
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'router\.py' }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Output ("stopped " + $procs.Count + " router process(es)")
}
Start-Sleep -Seconds 2
Start-Process -FilePath 'pythonw' `
  -ArgumentList "$PSScriptRoot\router.py" -WorkingDirectory $PSScriptRoot
Start-Sleep -Seconds 3
$c = Get-NetTCPConnection -LocalPort 8317 -State Listen
if ($c) { Write-Output ("router started, pid=" + ($c | Select-Object -First 1 -ExpandProperty OwningProcess)) }
else { Write-Output "router FAILED to start" }
