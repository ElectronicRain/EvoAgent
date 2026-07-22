$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backend = Start-Process `
    -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') `
    -ArgumentList '-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8000','--reload' `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

try {
    Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    pnpm dev
}
finally {
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id }
}

