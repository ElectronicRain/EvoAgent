$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dist = Join-Path $projectRoot 'frontend\dist\index.html'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python virtual environment not found. Run scripts\setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath $dist)) {
    Push-Location (Join-Path $projectRoot 'frontend')
    try { pnpm.cmd run build } finally { Pop-Location }
}

$address = Get-NetIPAddress -AddressFamily IPv4 `
    | Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } `
    | Sort-Object InterfaceMetric `
    | Select-Object -First 1 -ExpandProperty IPAddress

Write-Host 'EvoAgent 科研协作服务正在启动。' -ForegroundColor Cyan
Write-Host "本机访问：http://127.0.0.1:8000" -ForegroundColor Green
if ($address) {
    Write-Host "局域网成员访问：http://${address}:8000" -ForegroundColor Green
}
Write-Warning '局域网模式不提供公网 TLS。仅在可信校园网/实验室网络内使用，并通过 Windows 防火墙限制访问范围。'
Set-Location -LiteralPath $projectRoot
& $python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
