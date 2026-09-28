$r = @()
$r += 'START ' + (Get-Date -Format o)
try {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "$PSScriptRoot\launch_router.ps1"'
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN,$env:USERNAME) -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask -TaskName 'TokenEngine Router' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
  $r += 'TASK REGISTERED'
} catch { $r += 'TASK ERROR: ' + $_.Exception.Message }
try {
  New-NetFirewallRule -DisplayName 'TokenEngine 8317 (Tailscale only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8317 -RemoteAddress 100.64.0.0/10 -Profile Any -ErrorAction Stop | Out-Null
  $r += 'FIREWALL CREATED'
} catch { $r += 'FIREWALL ERROR: ' + $_.Exception.Message }
$r | Set-Content (Join-Path $PSScriptRoot 'logs\setup_result.txt') -Encoding UTF8
