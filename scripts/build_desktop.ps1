$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriConfigPath = Join-Path $projectRoot 'frontend\src-tauri\tauri.conf.json'
$version = [string]((Get-Content -LiteralPath $tauriConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).version)
$env:CARGO_TARGET_DIR = Join-Path $projectRoot "frontend\.tmp\tauri-target-$version"
$env:CI = 'true'
& (Join-Path $PSScriptRoot 'build_backend.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Backend build failed with exit code $LASTEXITCODE."
}
Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
pnpm.cmd desktop:build
if ($LASTEXITCODE -ne 0) {
    throw "Desktop build failed with exit code $LASTEXITCODE."
}
Write-Host "Desktop v$version created at $env:CARGO_TARGET_DIR\release" -ForegroundColor Green
