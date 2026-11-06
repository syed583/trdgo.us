# PATCH staged outcome_diag.py (v1) -> outcome_diag_v2.py: ONE exact text replacement, SHA-256 checked before and after.
# Writes ONE new file; executes nothing; never overwrites.
& {
  $ErrorActionPreference = 'Stop'
  $pchDir = 'C:\audit-reports\readonly_verify'; $pchSrc = Join-Path $pchDir 'outcome_diag.py'; $pchDst = Join-Path $pchDir 'outcome_diag_v2.py'
  $pchWantSrc = '37f1f56cfc1ad9ba5eb0800bc6237eb613a78e0c8baae9930e8f1f1b6d1ace34'; $pchWantDst = '2b4a669b5f86cf0695798d3996e4c3097c652a9c442ed4962b5a9f2d4f841535'
  if (-not (Test-Path $pchSrc)) { Write-Host 'STOPPED: outcome_diag.py is not staged.' -ForegroundColor Red; return }
  if (Test-Path $pchDst) { Write-Host "STOPPED: $pchDst already exists (never overwritten)." -ForegroundColor Red; return }
  $pchGot = (Get-FileHash -Algorithm SHA256 -Path $pchSrc).Hash.ToLower()
  if ($pchGot -ne $pchWantSrc) { Write-Host "STOPPED: staged file hash $pchGot is not the reviewed $pchWantSrc." -ForegroundColor Red; return }
  $pchUtf8 = New-Object Text.UTF8Encoding($false)
  $pchText = [IO.File]::ReadAllText($pchSrc, $pchUtf8)
  $pchOld = @('    if isinstance(v, str):', '        v = datetime.fromisoformat(v.replace("Z", "+00:00"))', '    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)') -join "`n"
  $pchNew = @('    if isinstance(v, (int, float)):                       # swr_cache.updated_at is epoch seconds (double precision)', '        return datetime.fromtimestamp(float(v), tz=timezone.utc)', '    if isinstance(v, str):', '        v = datetime.fromisoformat(v.replace("Z", "+00:00"))', '    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)') -join "`n"
  if ($pchText.IndexOf($pchOld) -lt 0 -or $pchText.IndexOf($pchOld) -ne $pchText.LastIndexOf($pchOld)) { Write-Host 'STOPPED: the text to replace was not found exactly once.' -ForegroundColor Red; return }
  $pchBytes = $pchUtf8.GetBytes($pchText.Replace($pchOld, $pchNew))
  $pchH = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($pchBytes)).Replace('-', '').ToLower()
  if ($pchH -ne $pchWantDst) { Write-Host "STOPPED: patched content hash $pchH is not the reviewed $pchWantDst - nothing written." -ForegroundColor Red; return }
  [IO.File]::WriteAllBytes($pchDst, $pchBytes)
  Write-Host ("PATCHED " + $pchDst + "  " + (Get-Item $pchDst).Length + " bytes  SHA-256 " + (Get-FileHash $pchDst -Algorithm SHA256).Hash.ToLower() + "  expected-match: True") -ForegroundColor Green
}
