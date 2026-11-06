# ASSEMBLE deploy_uw_fix_38ca61d.ps1 from the 3 verified parts (SHA-256 checked). Writes ONE new file; executes nothing.
& {
  $ErrorActionPreference = 'Stop'
  $stgWant = '0504524b80c8af6e2307e93a3b4c5f802828c6319b1086c2bad867f4c3dba4dd'
  $stgDirP = 'C:\audit-reports\deploy_uw_fix_parts'; $stgDir = 'C:\audit-reports\deploy_uw_fix'
  $stgDst = Join-Path $stgDir 'deploy_uw_fix_38ca61d.ps1'; $stgTmp = $stgDst + '.staging'
  if (Test-Path $stgDst) { Write-Host "STOPPED: $stgDst already exists (never overwritten). Compare its hash manually." -ForegroundColor Red; return }
  $stgAll = ''
  foreach ($stgN in 1..3) { $stgP = Join-Path $stgDirP "part$stgN.b64"; if (-not (Test-Path $stgP)) { Write-Host "STOPPED: part$stgN.b64 is missing - paste PART $stgN first" -ForegroundColor Red; return }; $stgAll += [IO.File]::ReadAllText($stgP) }
  $stgGz = [Convert]::FromBase64String($stgAll)
  $stgMs = New-Object IO.MemoryStream(,$stgGz); $stgGs = New-Object IO.Compression.GZipStream($stgMs, [IO.Compression.CompressionMode]::Decompress); $stgOut = New-Object IO.MemoryStream; $stgGs.CopyTo($stgOut)
  $stgBytes = $stgOut.ToArray()
  $stgH = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stgBytes)).Replace('-', '').ToLower()
  if ($stgH -ne $stgWant) { Write-Host "STOPPED: assembled content hash $stgH is not the reviewed $stgWant - nothing written" -ForegroundColor Red; return }
  New-Item -ItemType Directory -Force -Path $stgDir | Out-Null
  [IO.File]::WriteAllBytes($stgTmp, $stgBytes)
  if ((Get-FileHash $stgTmp -Algorithm SHA256).Hash.ToLower() -ne $stgWant) { Remove-Item $stgTmp -Force; Write-Host 'STOPPED: the file written to disk does not match - removed' -ForegroundColor Red; return }
  Move-Item $stgTmp $stgDst
  $stgT = $null; $stgE = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($stgDst, [ref]$stgT, [ref]$stgE)
  Write-Host ("STAGED " + $stgDst + "  " + (Get-Item $stgDst).Length + " bytes  SHA-256 " + (Get-FileHash $stgDst -Algorithm SHA256).Hash.ToLower() + "  expected-match: True  parse-errors: " + $stgE.Count) -ForegroundColor Green
  Write-Host 'Nothing was executed. The small part files in deploy_uw_fix_parts may now be deleted.'
}
