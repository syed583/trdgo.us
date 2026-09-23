<#
.SYNOPSIS
    Register US-Stock Reader as a Windows service.

.DESCRIPTION
    Run from an administrator PowerShell, in the checkout root:

        powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1

    The service binds to 127.0.0.1 only. Nothing reaches it from outside the
    machine until a reverse proxy is put in front, which is deliberate: the
    app should never be the thing listening on a public port.
#>

param(
    [string]$ServiceName = "USStockReader",
    [string]$Root = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an administrator PowerShell."
}

$python = Join-Path $Root "backend\.venv\Scripts\python.exe"
$backend = Join-Path $Root "backend"
$logs = Join-Path $Root "logs"

if (-not (Test-Path $python)) {
    throw "No virtual environment at $python. Create it first: python -m venv backend\.venv"
}
if (-not (Test-Path (Join-Path $backend ".env"))) {
    throw "No backend\.env. Copy backend\.env.example to backend\.env and fill it in."
}
if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
    throw "No frontend build. Run: cd frontend; npm ci; npm run build"
}
New-Item -ItemType Directory -Force -Path $logs | Out-Null

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Service exists -- stopping and removing it first."
    nssm stop $ServiceName confirm | Out-Null
    nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 2
}

# One worker on purpose: the app keeps its caches, its provider budget and
# its request pacing in process memory. A second worker would hold a second
# copy of all three and spend the subscription twice as fast for no gain on
# a single-user app.
$arguments = "-m uvicorn main:app --host 127.0.0.1 --port $Port --workers 1 " +
             "--proxy-headers --forwarded-allow-ips 127.0.0.1"

nssm install $ServiceName $python $arguments
nssm set $ServiceName AppDirectory $backend
nssm set $ServiceName DisplayName "US-Stock Reader"
nssm set $ServiceName Description "Personal US-equity options intelligence."
nssm set $ServiceName Start SERVICE_AUTO_START
nssm set $ServiceName AppStdout (Join-Path $logs "service.out.log")
nssm set $ServiceName AppStderr (Join-Path $logs "service.err.log")
# Roll the logs at 10 MB rather than letting them grow until the disk is the
# reason the app stopped.
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateBytes 10485760
nssm set $ServiceName AppExit Default Restart
nssm set $ServiceName AppRestartDelay 5000

nssm start $ServiceName
Start-Sleep -Seconds 4

$state = (Get-Service -Name $ServiceName).Status
Write-Host ""
Write-Host "Service $ServiceName is $state."

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10 | Out-Null
    Write-Host "The app answered without asking for a password -- ACCESS_PASSWORD is empty." -ForegroundColor Yellow
} catch [System.Net.WebException] {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Host "The app is up and asking for the password. That is correct." -ForegroundColor Green
    } else {
        Write-Host "No answer on port $Port. Check logs\service.err.log" -ForegroundColor Red
    }
}
