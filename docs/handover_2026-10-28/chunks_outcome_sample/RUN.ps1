# RUN outcome_sample.py: max 100 read-only daily-candle GETs (approved). Refuses on any hash/env mismatch.
& {
  $smpDir = 'C:\audit-reports\readonly_verify'
  $smpFile = "$smpDir\outcome_sample.py"
  $smpWant = 'e292ea76e0dc41a02e892ade54c6ddfd819a3e8334c112a188c40815f86a3ebb'
  if (-not (Test-Path $smpFile)) { Write-Host 'STOPPED: outcome_sample.py is not staged.' -ForegroundColor Red; return }
  $smpGot = (Get-FileHash -Algorithm SHA256 -Path $smpFile).Hash.ToLower()
  if ($smpGot -ne $smpWant) { Write-Host "STOPPED: staged hash $smpGot does not match the reviewed one." -ForegroundColor Red; return }
  $smpEnvFile = 'C:\apps\us-stock-reader\backend\.env'
  $smpIsProd = (Test-Path $smpEnvFile) -and [bool](Select-String -Path $smpEnvFile -Pattern '^\s*APP_ENV\s*=\s*["'']?production["'']?\s*(#.*)?$' -Quiet)
  if (-not $smpIsProd) { Write-Host 'STOPPED: backend\.env does not say APP_ENV=production - nothing run.' -ForegroundColor Red; return }
  $smpPy = 'C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe'
  Push-Location 'C:\apps\us-stock-reader\backend'
  $env:DB_POOL_SIZE = '1'; $env:DB_MAX_OVERFLOW = '0'
  try { & $smpPy $smpFile --out "$smpDir\outcome_sample.json" --max-requests 100 }
  finally { Remove-Item Env:DB_POOL_SIZE, Env:DB_MAX_OVERFLOW -ErrorAction SilentlyContinue; Pop-Location }
}
