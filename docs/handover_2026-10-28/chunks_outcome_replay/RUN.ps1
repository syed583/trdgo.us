# RUN outcome_replay.py: NO provider calls; one SELECT in a READ ONLY transaction. Refuses on any hash/env mismatch.
& {
  $smpDir = 'C:\audit-reports\readonly_verify'
  $smpFile = "$smpDir\outcome_replay.py"
  $smpWant = 'bfa4465a0783edefa4bda02cd6ccad29274bea31ce6602da07de5d6d69ebfa1b'
  if (-not (Test-Path $smpFile)) { Write-Host 'STOPPED: outcome_replay.py is not staged.' -ForegroundColor Red; return }
  if (-not (Test-Path "$smpDir\outcome_sample.json")) { Write-Host 'STOPPED: outcome_sample.json is missing.' -ForegroundColor Red; return }
  $smpGot = (Get-FileHash -Algorithm SHA256 -Path $smpFile).Hash.ToLower()
  if ($smpGot -ne $smpWant) { Write-Host "STOPPED: staged hash $smpGot does not match the reviewed one." -ForegroundColor Red; return }
  $smpEnvFile = 'C:\apps\us-stock-reader\backend\.env'
  $smpIsProd = (Test-Path $smpEnvFile) -and [bool](Select-String -Path $smpEnvFile -Pattern '^\s*APP_ENV\s*=\s*["'']?production["'']?\s*(#.*)?$' -Quiet)
  if (-not $smpIsProd) { Write-Host 'STOPPED: backend\.env does not say APP_ENV=production - nothing run.' -ForegroundColor Red; return }
  $smpPy = 'C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe'
  Push-Location 'C:\apps\us-stock-reader\backend'
  $env:DB_POOL_SIZE = '1'; $env:DB_MAX_OVERFLOW = '0'
  try { & $smpPy $smpFile --sample "$smpDir\outcome_sample.json" --out "$smpDir\outcome_replay.json" }
  finally { Remove-Item Env:DB_POOL_SIZE, Env:DB_MAX_OVERFLOW -ErrorAction SilentlyContinue; Pop-Location }
}
