$ErrorActionPreference = 'SilentlyContinue'
# 若 8317 已在监听则不重复启动（避免 SO_REUSEADDR 多实例抢占）
$existing = Get-NetTCPConnection -LocalPort 8317 -State Listen
if ($existing) { exit }
Start-Process -FilePath 'pythonw' `
  -ArgumentList "$PSScriptRoot\router.py" -WorkingDirectory $PSScriptRoot
