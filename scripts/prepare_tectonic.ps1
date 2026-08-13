$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $projectRoot '.tmp\tectonic-0.17.0-windows.zip'
$destination = Join-Path $projectRoot '.tmp\tectonic-0.17.0-windows'
$url = 'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip'
$expected = 'f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f'

if (-not (Test-Path -LiteralPath (Join-Path $destination 'tectonic.exe'))) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $archive) | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Tectonic SHA-256 mismatch: $actual" }
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
}
Write-Host "Verified Tectonic runtime: $destination" -ForegroundColor Green
