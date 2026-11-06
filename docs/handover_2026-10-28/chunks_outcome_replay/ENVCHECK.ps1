# READ-ONLY: is EARNINGS_LEGACY_SYNC set anywhere the service could see it? Prints only that one name/value; changes nothing.
& {
  $chkEnvFile = 'C:\apps\us-stock-reader\backend\.env'
  $inFile = (Test-Path $chkEnvFile) -and [bool](Select-String -Path $chkEnvFile -Pattern '^\s*EARNINGS_LEGACY_SYNC\s*=' -Quiet)
  if ($inFile) { (Select-String -Path $chkEnvFile -Pattern '^\s*EARNINGS_LEGACY_SYNC\s*=').Line | ForEach-Object { Write-Host ("backend\.env line: " + $_) } }
  $svcExtra = $null
  try { $svcExtra = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\USStockReader\Parameters' -ErrorAction Stop).AppEnvironmentExtra } catch { }
  $inSvc = $false
  if ($svcExtra) { $inSvc = [bool](@($svcExtra) | Where-Object { $_ -match '^\s*EARNINGS_LEGACY_SYNC\s*=' }) }
  $inMachine = [bool][Environment]::GetEnvironmentVariable('EARNINGS_LEGACY_SYNC', 'Machine')
  Write-Host ("EARNINGS_LEGACY_SYNC in backend\.env : " + $inFile)
  Write-Host ("EARNINGS_LEGACY_SYNC in service environment (NSSM AppEnvironmentExtra) : " + $inSvc)
  Write-Host ("EARNINGS_LEGACY_SYNC in machine environment : " + $inMachine)
  Write-Host ("RESULT: legacy sync is " + $(if ($inFile -or $inSvc -or $inMachine) { 'POSSIBLY ENABLED - inspect the value above' } else { 'DISABLED (unset => default off)' }))
}
