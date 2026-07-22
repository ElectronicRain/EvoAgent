$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build_backend.ps1')
Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
pnpm desktop:build
