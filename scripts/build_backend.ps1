$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$binaryDir = Join-Path $projectRoot 'frontend\src-tauri\binaries'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python virtual environment not found. Run scripts\setup.ps1 first.'
}

New-Item -ItemType Directory -Force -Path $binaryDir | Out-Null
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name 'evoagent-backend-x86_64-pc-windows-msvc' `
    --distpath $binaryDir `
    --workpath (Join-Path $projectRoot 'backend\build') `
    --specpath (Join-Path $projectRoot 'backend') `
    --collect-all 'pydantic_settings' `
    --collect-all 'uvicorn' `
    --hidden-import 'aiosqlite' `
    --hidden-import 'sqlalchemy.dialects.sqlite.aiosqlite' `
    (Join-Path $projectRoot 'backend_entry.py')

Write-Host "Backend sidecar created at $binaryDir" -ForegroundColor Green
