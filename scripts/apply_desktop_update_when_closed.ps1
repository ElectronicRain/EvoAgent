$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriConfigPath = Join-Path $projectRoot 'frontend\src-tauri\tauri.conf.json'
$tauriConfig = Get-Content -LiteralPath $tauriConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$tauriConfig.version
$sourceRelease = Join-Path $projectRoot "frontend\.tmp\tauri-target-$version\release"
$targetRelease = Join-Path $projectRoot 'frontend\src-tauri\target\release'
$logPath = Join-Path $projectRoot ".tmp\desktop-update-$version.log"

$sourceDesktop = Join-Path $sourceRelease 'evoagent-desktop.exe'
$sourceBackend = Join-Path $sourceRelease 'evoagent-backend.exe'
$targetDesktop = Join-Path $targetRelease 'evoagent-desktop.exe'
$targetBackend = Join-Path $targetRelease 'evoagent-backend.exe'

if (-not (Test-Path -LiteralPath $sourceDesktop) -or -not (Test-Path -LiteralPath $sourceBackend)) {
    throw "Staged desktop v$version is incomplete: $sourceRelease"
}

$productVersion = (Get-Item -LiteralPath $sourceDesktop).VersionInfo.ProductVersion
if ($productVersion -ne $version) {
    throw "Staged desktop version $productVersion does not match expected version $version."
}

"Waiting for EvoAgent v$version update at $(Get-Date -Format o)" | Set-Content -LiteralPath $logPath -Encoding UTF8
while (Get-Process -Name 'evoagent-desktop', 'evoagent-backend' -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 2
}

$desktopStaging = "$targetDesktop.update"
$backendStaging = "$targetBackend.update"
Copy-Item -LiteralPath $sourceDesktop -Destination $desktopStaging -Force
Copy-Item -LiteralPath $sourceBackend -Destination $backendStaging -Force
Move-Item -LiteralPath $backendStaging -Destination $targetBackend -Force
Move-Item -LiteralPath $desktopStaging -Destination $targetDesktop -Force

$installedVersion = (Get-Item -LiteralPath $targetDesktop).VersionInfo.ProductVersion
if ($installedVersion -ne $version) {
    throw "Desktop update verification failed: expected $version, got $installedVersion."
}
"EvoAgent desktop updated to v$installedVersion at $(Get-Date -Format o)" | Add-Content -LiteralPath $logPath -Encoding UTF8
