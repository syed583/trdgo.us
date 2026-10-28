# ASSEMBLE verify_earnings_readonly_v4.py from the 2 verified parts (SHA-256 checked). Writes ONE new file; executes nothing.
& {
  $ErrorActionPreference = 'Stop'
  $stgWant = 'b277a7457fdf9c5db2b6b6b9701efd2186621467c38deb0980f1d59fa48e7a19'
  $stgDirP = 'C:\audit-reports\readonly_verify\parts_v4'; $stgDst = 'C:\audit-reports\readonly_verify\verify_earnings_readonly_v4.py'; $stgTmp = $stgDst + '.staging'
  if (Test-Path $stgDst) { Write-Host "STOPPED: $stgDst already exists (never overwritten). Compare its hash manually." -ForegroundColor Red; return }
  $stgAll = ''
  foreach ($stgN in 1..2) { $stgP = Join-Path $stgDirP "part$stgN.b64"; if (-not (Test-Path $stgP)) { Write-Host "STOPPED: part$stgN.b64 is missing - paste PART $stgN first" -ForegroundColor Red; return }; $stgAll += [IO.File]::ReadAllText($stgP) }
  $stgGz = [Convert]::FromBase64String($stgAll)
  $stgMs = New-Object IO.MemoryStream(,$stgGz); $stgGs = New-Object IO.Compression.GZipStream($stgMs, [IO.Compression.CompressionMode]::Decompress); $stgOut = New-Object IO.MemoryStream; $stgGs.CopyTo($stgOut)
  $stgBytes = $stgOut.ToArray()
  $stgH = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stgBytes)).Replace('-', '').ToLower()
  if ($stgH -ne $stgWant) { Write-Host "STOPPED: assembled content hash $stgH is not the reviewed $stgWant - nothing written" -ForegroundColor Red; return }
  [IO.File]::WriteAllBytes($stgTmp, $stgBytes)
  if ((Get-FileHash $stgTmp -Algorithm SHA256).Hash.ToLower() -ne $stgWant) { Remove-Item $stgTmp -Force; Write-Host 'STOPPED: the file written to disk does not match - removed' -ForegroundColor Red; return }
  Move-Item $stgTmp $stgDst
  Write-Host ("STAGED " + $stgDst + "  " + (Get-Item $stgDst).Length + " bytes  SHA-256 " + (Get-FileHash $stgDst -Algorithm SHA256).Hash.ToLower() + "  expected-match: True") -ForegroundColor Green
  Write-Host 'Nothing was executed. The small part files in parts_v4 may now be deleted.'
}
