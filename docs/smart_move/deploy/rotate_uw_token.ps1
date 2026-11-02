<#
  Unusual Whales token rotation helper for TRDGO-US (local machine). NOT run by any automated process.

  The new token is typed into a hidden prompt (SecureString). It is never passed on a command line, never written to history, never printed, never put in Git. Only its length and an 8-hex SHA-256
  fingerprint are shown. Nothing here contacts Unusual Whales.

  Steps (run each one yourself, in this order):
    1) Session  - holds the new token in THIS PowerShell window only (process environment variable). It takes precedence over backend\.env, so the old file stays untouched until the new token is proven.
         . .\rotate_uw_token.ps1 -Session
    2) Check    - shows the source, length, fingerprint and whether it differs from the old token (no provider request).
         python backend\smart_move_secret_check.py --old-fingerprint <fingerprint of the OLD token>
    3) (separate authorization) one connectivity test, then
    4) Persist  - writes the new token into backend\.env (atomic replace) so every later window uses it.
         .\rotate_uw_token.ps1 -Persist
    5) Clean up - removes stale files that still hold the OLD token (backend\.env.bak) after you confirm the new token works.
         .\rotate_uw_token.ps1 -RemoveOldBackups

  Run -Session with a leading dot-space (". ") so the variable stays in your window. -Persist and -RemoveOldBackups read the token again from the hidden prompt / the file as needed.
#>
param(
  [switch]$Session,
  [switch]$Persist,
  [switch]$RemoveOldBackups,
  [string]$EnvFile = "C:\Users\initi\Downloads\US-STOCKS\backend\.env"
)

function Get-Fingerprint([string]$t) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $b = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($t))
  (($b | ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 8)
}

function Read-NewToken {
  $sec = Read-Host -Prompt "Paste the NEW Unusual Whales token (hidden)" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  $plain = $plain.Trim().Trim('"').Trim("'")
  if ($plain.Length -lt 20 -or $plain -match "\s") { throw "That does not look like a token (too short or contains spaces). Nothing was changed." }
  return $plain
}

if ($Session) {
  $t = Read-NewToken
  $env:UNUSUAL_WHALES_API_KEY = $t
  Write-Host ("Session variable set. length={0} fingerprint={1} (this window only; backend\.env unchanged)" -f $t.Length, (Get-Fingerprint $t))
  $t = $null
  return
}

if ($Persist) {
  $t = if ($env:UNUSUAL_WHALES_API_KEY) { $env:UNUSUAL_WHALES_API_KEY } else { Read-NewToken }
  if (-not (Test-Path $EnvFile)) { throw "Env file not found: $EnvFile" }
  $lines = Get-Content -LiteralPath $EnvFile
  $found = $false
  $out = foreach ($ln in $lines) { if ($ln -match '^\s*UNUSUAL_WHALES_API_KEY\s*=') { $found = $true; "UNUSUAL_WHALES_API_KEY=$t" } else { $ln } }
  if (-not $found) { $out = @($out) + "UNUSUAL_WHALES_API_KEY=$t" }
  $tmp = "$EnvFile.rotating"
  Set-Content -LiteralPath $tmp -Value $out -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $EnvFile -Force
  Write-Host ("backend\.env updated. length={0} fingerprint={1}" -f $t.Length, (Get-Fingerprint $t))
  $t = $null
  return
}

if ($RemoveOldBackups) {
  $bak = Join-Path (Split-Path $EnvFile) ".env.bak"
  if (Test-Path $bak) {
    $answer = Read-Host "Delete $bak (it holds the OLD token)? type YES to confirm"
    if ($answer -eq "YES") { Remove-Item -LiteralPath $bak; Write-Host "removed $bak" } else { Write-Host "left in place" }
  } else { Write-Host "no .env.bak found" }
  return
}

Write-Host "Choose one of: -Session, -Persist, -RemoveOldBackups (see the header of this file)."
