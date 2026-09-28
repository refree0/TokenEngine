# 验证百炼识图能力：自动定位 node -> 生成测试图 -> 真实调用 -> 打印结果
param(
    [string]$SkillDir = (Join-Path $env:USERPROFILE ".codex\skills\claude-vision-skill"),
    [string]$NodeExe = ""
)
$ErrorActionPreference = "Stop"

# 1. 找 node：PATH 优先，其次 Codex 自带运行时
if (-not $NodeExe) {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { $NodeExe = $cmd.Source }
}
if (-not $NodeExe) {
    $bundled = Get-ChildItem "$env:USERPROFILE\.cache\codex-runtimes\*\dependencies\node\bin\node.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($bundled) { $NodeExe = $bundled.FullName }
}
if (-not $NodeExe) { Write-Error "未找到 node.exe，请安装 Node.js 或用 -NodeExe 指定路径"; exit 1 }
Write-Host "node  : $NodeExe"

$script = Join-Path $SkillDir "vision.js"
if (-not (Test-Path -LiteralPath $script)) { Write-Error "找不到脚本: $script"; exit 1 }
Write-Host "script: $script"

# 2. 生成测试图（必须够大，小图会导致 OCR 漏字）
$img = Join-Path $env:TEMP "vision-verify.png"
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap(800, 180)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$g.DrawString("Vision Check 24680", (New-Object System.Drawing.Font("Arial", 36)), [System.Drawing.Brushes]::Black, 20, 60)
$bmp.Save($img, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Host "image : $img"

# 3. 真实调用（thinking 类模型可能较慢，给它足够时间）
Write-Host "--- 调用结果 ---"
& $NodeExe $script $img "请逐字读出图片中的全部文字内容"
