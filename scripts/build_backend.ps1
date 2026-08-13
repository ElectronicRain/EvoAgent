$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$binaryDir = Join-Path $projectRoot 'frontend\src-tauri\binaries'
$tectonicSource = Join-Path $projectRoot '.tmp\tectonic-0.17.0-windows\tectonic.exe'
$tectonicTarget = Join-Path $binaryDir 'tectonic.exe'
$buildRuntimeRoot = Join-Path $projectRoot '.tmp\desktop-build'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python virtual environment not found. Run scripts\setup.ps1 first.'
}

$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUSERBASE = Join-Path $buildRuntimeRoot 'python-user'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRuntimeRoot 'pyinstaller'
New-Item -ItemType Directory -Force -Path (Join-Path $env:PYTHONUSERBASE 'Python312\site-packages') | Out-Null
New-Item -ItemType Directory -Force -Path $binaryDir | Out-Null
if (-not (Test-Path -LiteralPath $tectonicSource)) {
    throw 'Bundled Tectonic runtime not found. Run scripts\prepare_tectonic.ps1 first.'
}
Copy-Item -LiteralPath $tectonicSource -Destination $tectonicTarget -Force
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
if ($LASTEXITCODE -ne 0) {
    throw "Backend sidecar build failed with exit code $LASTEXITCODE."
}

Write-Host "Backend sidecar created at $binaryDir" -ForegroundColor Green
Write-Host "Bundled LaTeX runtime copied to $tectonicTarget" -ForegroundColor Green
