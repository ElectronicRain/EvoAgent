$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source

Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $python -m venv .venv
}
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'

Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
pnpm install
Write-Host 'EvoAgent development environment is ready.' -ForegroundColor Green

