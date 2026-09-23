<#
.SYNOPSIS
    Pull, rebuild, test, restart.

.DESCRIPTION
    Stops before restarting if the tests fail: a service that restarts into
    a broken build is worse than one that keeps serving the old one.
#>

param(
    [string]$ServiceName = "USStockReader",
    [string]$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
)

$ErrorActionPreference = "Stop"
Set-Location $Root

Write-Host "==> pulling"
git pull --ff-only

Write-Host "==> python dependencies"
& "$Root\backend\.venv\Scripts\pip.exe" install -q -r "$Root\backend\requirements.txt"

Write-Host "==> frontend"
Push-Location "$Root\frontend"
npm ci --silent
npm run build
Pop-Location

Write-Host "==> tests"
Push-Location "$Root\backend"
& "$Root\backend\.venv\Scripts\python.exe" -m pytest -q
$failed = $LASTEXITCODE
Pop-Location
if ($failed -ne 0) {
    Write-Host "Tests failed -- the running service was left alone." -ForegroundColor Red
    exit 1
}

Write-Host "==> restarting"
Restart-Service $ServiceName
Start-Sleep -Seconds 4
Write-Host ((Get-Service $ServiceName).Status)
