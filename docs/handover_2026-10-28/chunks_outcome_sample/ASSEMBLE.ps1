# ASSEMBLE outcome_sample.py from the 3 verified parts (SHA-256 checked). Writes ONE new file; executes nothing.
& {
  $ErrorActionPreference = 'Stop'
  $stgWant = 'e292ea76e0dc41a02e892ade54c6ddfd819a3e8334c112a188c40815f86a3ebb'
  $stgDirP = 'C:\audit-reports\readonly_verify\parts_sample'; $stgDst = 'C:\audit-reports\readonly_verify\outcome_sample.py'; $stgTmp = $stgDst + '.staging'
  if (Test-Path $stgDst) { Write-Host "STOPPED: $stgDst already exists (never overwritten). Compare its hash manually." -ForegroundColor Red; return }
  $stgAll = ''
  foreach ($stgN in 1..3) { $stgP = Join-Path $stgDirP "part$stgN.b64"; if (-not (Test-Path $stgP)) { Write-Host "STOPPED: part$stgN.b64 is missing - paste PART $stgN first" -ForegroundColor Red; return }; $stgAll += [IO.File]::ReadAllText($stgP) }
  $stgGz = [Convert]::FromBase64String($stgAll)
  $stgMs = New-Object IO.MemoryStream(,$stgGz); $stgGs = New-Object IO.Compression.GZipStream($stgMs, [IO.Compression.CompressionMode]::Decompress); $stgOut = New-Object IO.MemoryStream; $stgGs.CopyTo($stgOut)
  $stgBytes = $stgOut.ToArray()
  $stgH = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stgBytes)).Replace('-', '').ToLower()
  if ($stgH -ne $stgWant) { Write-Host "STOPPED: assembled content hash $stgH is not the reviewed $stgWant - nothing written" -ForegroundColor Red; return }
  [IO.File]::WriteAllBytes($stgTmp, $stgBytes)
  if ((Get-FileHash $stgTmp -Algorithm SHA256).Hash.ToLower() -ne $stgWant) { Remove-Item $stgTmp -Force; Write-Host 'STOPPED: the file written to disk does not match - removed' -ForegroundColor Red; return }
  Move-Item $stgTmp $stgDst
  Write-Host ("STAGED " + $stgDst + "  " + (Get-Item $stgDst).Length + " bytes  SHA-256 " + (Get-FileHash $stgDst -Algorithm SHA256).Hash.ToLower() + "  expected-match: True") -ForegroundColor Green
  Write-Host 'Nothing was executed. The small part files may now be deleted.'
}
