# STAGE apply_sql.py (SHA-256 checked). Writes ONE new file; executes nothing; touches no database, service or other file.
$ErrorActionPreference = "Stop"
$want = "cd9ff7c6729ce6ce29f9bfb1b540ba33a6db845c72ec787590ae0fdf569356e1"
$dir = "C:\audit-reports\phaseC"; $dst = Join-Path $dir "apply_sql.py"; $tmp = Join-Path $dir "apply_sql.py.staging"
$b64 = @'
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
'@
if (-not (Test-Path $dir)) { Write-Host "STOPPED: $dir does not exist" -ForegroundColor Red; return }
if (Test-Path $dst) { Write-Host "STOPPED: $dst already exists (never overwritten). Compare its hash manually." -ForegroundColor Red; return }
$gz = [Convert]::FromBase64String(($b64 -replace "\s", ""))
$ms = New-Object IO.MemoryStream(,$gz); $gs = New-Object IO.Compression.GZipStream($ms, [IO.Compression.CompressionMode]::Decompress); $out = New-Object IO.MemoryStream; $gs.CopyTo($out)
$bytes = $out.ToArray()
$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace("-", "").ToLower()
if ($h -ne $want) { Write-Host ("STOPPED: content hash " + $h + " is not the reviewed " + $want + " - nothing written") -ForegroundColor Red; return }
[IO.File]::WriteAllBytes($tmp, $bytes)
$h2 = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
if ($h2 -ne $want) { Remove-Item $tmp -Force; Write-Host "STOPPED: the file written to disk does not match - removed" -ForegroundColor Red; return }
Move-Item $tmp $dst
Write-Host ("STAGED " + $dst + "  " + (Get-Item $dst).Length + " bytes  SHA-256 " + (Get-FileHash $dst -Algorithm SHA256).Hash.ToLower() + "  expected-match: True")
Write-Host "Nothing was executed. Review (read-only): Select-String -Path $dst -Pattern 'DATABASE_URL|--apply|DROP|DELETE|TRUNCATE'"
