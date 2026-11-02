<#
  Production token update helper (run ON THE VPS by the authorized administrator, only AFTER localhost has been verified and the owner has approved the production change).
  The new token is typed into a hidden prompt (SecureString): never on a command line, never in history, never printed. Only length / fingerprint / last four characters are shown.
  It does NOT restart the service unless -Restart is given (restart only after the owner approves it). It contacts nothing.

  Typical order (each step is a separate switch so nothing happens by surprise):
    .\vps_uw_token_update.ps1 -UpdateEnv [-SetMaxInflight 2]    # backs up .env, writes the new token (and optionally UW_MAX_INFLIGHT) - no restart
    .\vps_uw_token_update.ps1 -ClearOverride                    # ONLY if the audit showed an override row: deletes that one settings row after you type YES
    .\vps_uw_credential_audit.ps1                               # re-run: fingerprints must match localhost
    .\vps_uw_token_update.ps1 -Restart                          # after owner approval: Restart-Service USStockReader after you type YES
  Rollback: -Rollback restores the newest .env.pre_rotation_* backup. NOTE: the backup holds the OLD (revoked) token, so rollback only helps for non-token settings; a failed new token is fixed by
  entering the right token again, not by restoring the old one.
#>
param([switch]$UpdateEnv, [int]$SetMaxInflight = 0, [switch]$ClearOverride, [switch]$Restart, [switch]$Rollback, [string]$AppDir = "C:\apps\us-stock-reader\backend")

function Fp([string]$t) { $sha = [System.Security.Cryptography.SHA256]::Create(); (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($t)) | ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 8) }
function Read-NewToken {
  $sec = Read-Host -Prompt "Paste the NEW Unusual Whales token (hidden)" -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { $p = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
  $p = $p.Trim().Trim('"').Trim("'")
  if ($p.Length -lt 20 -or $p -match "\s") { throw "Not a valid-looking token. Nothing was changed." }
  return $p
}
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) { throw "Not found: $envFile" }

if ($UpdateEnv) {
  $t = Read-NewToken
  $bak = "$envFile.pre_rotation_{0:yyyyMMdd_HHmmss}" -f (Get-Date)
  Copy-Item -LiteralPath $envFile -Destination $bak
  $lines = Get-Content -LiteralPath $envFile
  $out = New-Object System.Collections.Generic.List[string]
  $gotKey = $false; $gotCap = $false
  foreach ($ln in $lines) {
    if ($ln -match '^\s*UNUSUAL_WHALES_API_KEY\s*=') { $out.Add("UNUSUAL_WHALES_API_KEY=$t"); $gotKey = $true }
    elseif ($SetMaxInflight -gt 0 -and $ln -match '^\s*UW_MAX_INFLIGHT\s*=') { $out.Add("UW_MAX_INFLIGHT=$SetMaxInflight"); $gotCap = $true }
    else { $out.Add($ln) }
  }
  if (-not $gotKey) { $out.Add("UNUSUAL_WHALES_API_KEY=$t") }
  if ($SetMaxInflight -gt 0 -and -not $gotCap) { $out.Add("UW_MAX_INFLIGHT=$SetMaxInflight") }
  $tmp = "$envFile.rotating"
  Set-Content -LiteralPath $tmp -Value $out -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $envFile -Force
  "backup: $bak (holds the OLD token - delete it once production is verified)"
  "new .env: length={0} fingerprint={1} last4={2}" -f $t.Length, (Fp $t), $t.Substring($t.Length - 4)
  $t = $null
  "The service has NOT been restarted. If the audit shows an override row, run -ClearOverride (the override wins over .env)."
  return
}

if ($ClearOverride) {
  $a = Read-Host "Delete the admin provider-key override row from the production settings table? type YES"
  if ($a -ne "YES") { "left in place"; return }
  $py = Join-Path $AppDir ".venv\Scripts\python.exe"; if (-not (Test-Path $py)) { $py = "python" }
  $tmp = Join-Path $env:TEMP ("uw_override_clear_{0}.py" -f [guid]::NewGuid().ToString("N"))
  @'
import os, sys
sys.path.insert(0, os.getcwd())
from database import SessionLocal
from models_user import StrategySetting
db = SessionLocal()
try:
    n = db.query(StrategySetting).filter(StrategySetting.key == "provider.unusualwhales.api_key").delete()
    db.commit()
    print("override rows deleted:", n)
finally:
    db.close()
'@ | Set-Content -LiteralPath $tmp -Encoding UTF8
  Push-Location $AppDir
  try { & $py $tmp 2>&1 | ForEach-Object { if ($_ -match "postgres|password|://") { "(output suppressed)" } else { $_ } } } finally { Pop-Location; Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
  return
}

if ($Restart) {
  $a = Read-Host "Restart service USStockReader now? Production will be interrupted for a few seconds. type YES"
  if ($a -eq "YES") { Restart-Service -Name "USStockReader"; Get-Service -Name "USStockReader" | ForEach-Object { "USStockReader: $($_.Status)" } } else { "not restarted" }
  return
}

if ($Rollback) {
  $b = Get-ChildItem -LiteralPath $AppDir -Filter ".env.pre_rotation_*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $b) { "no backup found"; return }
  $a = Read-Host "Restore $($b.Name) over .env? (it holds the OLD token) type YES"
  if ($a -eq "YES") { Copy-Item -LiteralPath $b.FullName -Destination $envFile -Force; "restored" } else { "left in place" }
  return
}
"Choose one of: -UpdateEnv, -ClearOverride, -Restart, -Rollback (see the header)."
