<#
  READ-ONLY credential audit for the PRODUCTION server (Windows VPS, service USStockReader, app folder C:\apps\us-stock-reader). Run it ON THE VPS, in a fresh PowerShell window, as the person
  authorized to administer production. It changes nothing, restarts nothing, contacts nothing outside the machine (no Unusual Whales request, no network call except the app's own database read below).

  It prints only lengths, SHA-256 fingerprints (8 hex characters) and the last four characters of each token - never a token, a password or a database URL.
  Paste its output back so localhost can be compared with production.

  Usage:   .\vps_uw_credential_audit.ps1 [-AppDir C:\apps\us-stock-reader\backend]
#>
param([string]$AppDir = "C:\apps\us-stock-reader\backend")

function Fp([string]$t) { $sha = [System.Security.Cryptography.SHA256]::Create(); (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($t)) | ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 8) }
function Describe([string]$label, [string]$t) {
  if ([string]::IsNullOrWhiteSpace($t)) { "{0,-46} not set" -f $label } else { "{0,-46} length={1} fingerprint={2} last4={3}" -f $label, $t.Length, (Fp $t), $t.Substring([Math]::Max(0, $t.Length - 4)) }
}
function KeyIn([string]$file, [string]$name) {
  $v = $null
  foreach ($ln in (Get-Content -LiteralPath $file -ErrorAction SilentlyContinue)) { if ($ln -match ("^\s*" + $name + "\s*=\s*(.*)$")) { $v = $Matches[1].Trim().Trim('"').Trim("'") } }
  return $v
}

"== Unusual Whales credential audit (read only) =="
"time: $(Get-Date -Format s)   host: $env:COMPUTERNAME"
if (-not (Test-Path $AppDir)) { "App folder not found: $AppDir"; return }

"-- .env files in the app folder that hold the key"
Get-ChildItem -LiteralPath $AppDir -Filter ".env*" -Force -ErrorAction SilentlyContinue | ForEach-Object {
  $v = KeyIn $_.FullName "UNUSUAL_WHALES_API_KEY"
  if ($v) { Describe ("{0} ({1:yyyy-MM-dd})" -f $_.Name, $_.LastWriteTime) $v }
}
"-- settings that cap provider concurrency / identify the environment (values, no secrets)"
foreach ($n in "APP_ENV", "UW_MAX_INFLIGHT") { "{0,-46} {1}" -f $n, ($(KeyIn (Join-Path $AppDir ".env") $n)) }

"-- process / machine / user environment variables"
foreach ($scope in "Process", "User", "Machine") { Describe "env UNUSUAL_WHALES_API_KEY [$scope]" ([Environment]::GetEnvironmentVariable("UNUSUAL_WHALES_API_KEY", $scope)) }

"-- service"
Get-Service -Name "USStockReader" -ErrorAction SilentlyContinue | ForEach-Object { "USStockReader: $($_.Status), start type $($_.StartType)" }

"-- admin provider-key override stored in the production database (read only; unique variable names, nothing echoed)"
$py = Join-Path $AppDir ".venv\Scripts\python.exe"; if (-not (Test-Path $py)) { $py = "python" }
$audit_tmp = Join-Path $env:TEMP ("uw_override_probe_{0}.py" -f [guid]::NewGuid().ToString("N"))
@'
import hashlib, os, sys
sys.path.insert(0, os.getcwd())
from database import SessionLocal
from models_user import StrategySetting
db = SessionLocal()
try:
    row = db.query(StrategySetting).filter(StrategySetting.key == "provider.unusualwhales.api_key").first()
    v = (row.value or "").strip() if row else ""
    print("override row:", "none" if not v else "length=%d fingerprint=%s last4=%s" % (len(v), hashlib.sha256(v.encode()).hexdigest()[:8], v[-4:]))
finally:
    db.close()
'@ | Set-Content -LiteralPath $audit_tmp -Encoding UTF8
Push-Location $AppDir
try { & $py $audit_tmp 2>&1 | ForEach-Object { if ($_ -match "postgres|password|://") { "(output suppressed: contained connection details)" } else { $_ } } } finally { Pop-Location; Remove-Item -LiteralPath $audit_tmp -Force -ErrorAction SilentlyContinue }

"-- scheduled tasks on this server that mention the app"
Get-ScheduledTask -ErrorAction SilentlyContinue | ForEach-Object { $t = $_; $txt = ($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "; if ($txt -match "us-stock-reader|smart_move|signals") { "{0} [{1}]" -f $t.TaskName, $t.State } }
"== end =="
"Interpretation: the override row, if present, WINS over .env in the web application. All three fingerprints (.env, override, and localhost) must match after rotation."
