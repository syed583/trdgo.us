param([string]$Root = "C:\apps\us-stock-reader", [int]$Days = 14, [string]$OutDir = "C:\audit-reports")
# READ-ONLY. Runs prod_readonly_history.py with the application's own venv. The script opens ONE read-only database connection
# (the server rejects writes), executes only SELECT/SHOW, never prints the connection string, and writes a JSON report to $OutDir.
# It changes no trade, installs nothing, restarts nothing.
$ErrorActionPreference = "Stop"
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "prod_readonly_history.py"
if (-not (Test-Path $py)) { Write-Host "STOPPED: no python at $py" -ForegroundColor Red; exit 2 }
if (-not (Test-Path $script)) { Write-Host "STOPPED: prod_readonly_history.py is not next to this script" -ForegroundColor Red; exit 2 }
Write-Host ("script SHA-256: " + (Get-FileHash $script -Algorithm SHA256).Hash)
& $py $script --root $Root --days $Days --out $OutDir
exit $LASTEXITCODE
