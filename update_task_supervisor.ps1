# 以管理员身份更新 TokenEngine Router 计划任务：launcher -> supervisor 守护 + 失败重启 + 无执行时限
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PSScriptRoot\supervisor_router.ps1"'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Set-ScheduledTask -TaskName 'TokenEngine Router' -Action $action -Settings $settings | Out-Null
"updated at $(Get-Date -Format o)" | Out-File (Join-Path $PSScriptRoot 'logs\task_update.log') -Encoding utf8
