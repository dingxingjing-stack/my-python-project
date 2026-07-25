# ===== 查找项目内含疑似 API Key 的文件 =====
$root = "C:\Users\dingx\Desktop\music-website-skill-backup"

Write-Host "=== 1. 包含 nvapi- 的文件 ===" -ForegroundColor Cyan
Get-ChildItem $root -Recurse -File | Select-String "nvapi-" -SimpleMatch | ForEach-Object { $_.Path } | Sort-Object -Unique

Write-Host "`n=== 2. 包含 sk- (疑似 API Key) 的文件 ===" -ForegroundColor Cyan
Get-ChildItem $root -Recurse -File | Select-String "sk-[a-zA-Z0-9]{20,}" | ForEach-Object { "$($_.Path):$($_.LineNumber)" } | Sort-Object -Unique

Write-Host "`n=== 3. 包含 your- 或 change-me (占位符) 的文件 ===" -ForegroundColor Cyan
Get-ChildItem $root -Recurse -File | Select-String "your-|change-me" -SimpleMatch | ForEach-Object { $_.Path } | Sort-Object -Unique

Write-Host "`n=== 4. .env 文件列表 ===" -ForegroundColor Cyan
Get-ChildItem $root -Recurse -Filter ".env*" | ForEach-Object { $_.FullName }

Write-Host "`n=== 5. 系统环境变量中含 NVIDIA/RELAY/DEEPSEEK 的 ===" -ForegroundColor Cyan
Get-ChildItem Env: | Where-Object { $_.Name -match "NVIDIA|RELAY|DEEPSEEK|OPENAI" } | Format-Table Name, Value
