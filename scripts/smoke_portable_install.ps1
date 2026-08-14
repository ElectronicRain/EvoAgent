param(
    [string]$ReleaseRoot = "",
    [string]$SmokeRoot = "",
    [string]$ProfileRoot = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path $projectRoot ".tmp\tauri-target-2.1.0-portable\release"
}
if (-not $SmokeRoot) {
    $SmokeRoot = Join-Path $projectRoot ".tmp\portable-install-smoke"
}
if (-not $ProfileRoot) {
    $ProfileRoot = Join-Path $projectRoot ".tmp\portable-profile"
}

New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $SmokeRoot "binaries") -Force | Out-Null
New-Item -ItemType Directory -Path $ProfileRoot -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $ReleaseRoot "evoagent-desktop.exe") -Destination $SmokeRoot -Force
Copy-Item -LiteralPath (Join-Path $ReleaseRoot "evoagent-backend.exe") -Destination $SmokeRoot -Force
Copy-Item -LiteralPath (Join-Path $ReleaseRoot "binaries\tectonic.exe") -Destination (Join-Path $SmokeRoot "binaries\tectonic.exe") -Force

$previousLocalAppData = $env:LOCALAPPDATA
$previousAppData = $env:APPDATA
$desktopProcess = $null
try {
    $env:LOCALAPPDATA = $ProfileRoot
    $env:APPDATA = Join-Path $ProfileRoot "Roaming"
    $desktopProcess = Start-Process -FilePath (Join-Path $SmokeRoot "evoagent-desktop.exe") -WindowStyle Hidden -PassThru

    $health = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
            break
        }
        catch {
            # The packaged Python service needs a few seconds on first launch.
        }
    }
    if (-not $health -or $health.status -ne "healthy") {
        throw "The packaged backend did not become healthy."
    }

    $databasePath = Join-Path $ProfileRoot "EvoAgent\evoagent.db"
    if (-not (Test-Path -LiteralPath $databasePath)) {
        throw "The clean-profile database was not created at $databasePath."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $SmokeRoot "binaries\tectonic.exe"))) {
        throw "The bundled Tectonic executable is missing."
    }

    [PSCustomObject]@{
        Status = "passed"
        ServiceVersion = $health.version
        SmokeRoot = $SmokeRoot
        ProfileRoot = $ProfileRoot
        DatabasePath = $databasePath
        DatabaseBytes = (Get-Item -LiteralPath $databasePath).Length
        TectonicBytes = (Get-Item -LiteralPath (Join-Path $SmokeRoot "binaries\tectonic.exe")).Length
    }
}
finally {
    if ($desktopProcess -and -not $desktopProcess.HasExited) {
        Stop-Process -Id $desktopProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Get-Process evoagent-backend -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$SmokeRoot*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $previousLocalAppData
    $env:APPDATA = $previousAppData
}
