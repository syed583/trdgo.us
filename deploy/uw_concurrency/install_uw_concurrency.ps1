<#
Stage B: shared Unusual Whales concurrency coordination. Replaces ONE application file (backend\unusualwhales_service.py) and adds three new backend modules. By itself the new code is INERT:
nothing changes until the environment switches below are set, so the release can be installed and verified first and switched on later.

  Preflight  verifies the kit (manifest anchor + every file hash) and the target (the file to replace must be exactly the known version); changes nothing.
  Install    backs up the file it replaces, copies the files, optionally (-ConfigureSlots) adds UW_MACHINE_SLOTS=1 and UW_SLOT_DIR to backend\.env, optionally (-Restart) restarts the service and checks health, rolling back by itself on failure.
  Rollback   restores the backed-up file, removes the files it added and the .env lines it added (optionally restarts).

Never touches the lease broker (its router is not mounted by this release), the Earnings Monitor, the approval file or any limit. Sends no request to any provider.
  powershell -ExecutionPolicy Bypass -File .\install_uw_concurrency.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('Preflight', 'Install', 'Rollback')][string]$Action,
  [Parameter(Mandatory)][string]$Package,
  [string]$ManifestSha256 = '',
  [string]$App = 'C:\apps\us-stock-reader',
  [string]$Service = 'USStockReader',
  [int]$Port = 8000,
  [string]$SlotDir = 'C:\apps\us-stock-reader\uw_slots',
  [string]$BackupDir = '',
  [switch]$ConfigureSlots,
  [switch]$Restart,
  [switch]$NoHealth,
  [switch]$SkipRestartCommand,
  [switch]$SimulateHealthFailure
)
$ErrorActionPreference = 'Stop'
function Log([string]$m) { Write-Host ("{0}  {1}" -f (Get-Date).ToUniversalTime().ToString('HH:mm:ss\Z'), $m) }
function Sha([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }
function ShaN([string]$p) {                                   # SHA-256 of the file with CRLF normalised to LF (the application files may be stored either way)
  $b = [IO.File]::ReadAllBytes($p); $s = [Text.Encoding]::UTF8.GetString($b).Replace("`r`n", "`n")
  $sha = [Security.Cryptography.SHA256]::Create(); -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($s)) | ForEach-Object { $_.ToString('x2') })
}
$Backend = Join-Path $App 'backend'; $EnvFile = Join-Path $Backend '.env'; $Marker = Join-Path $App 'uw_concurrency_install.json'
$mf = Join-Path $Package 'MANIFEST.sha256'
if (-not (Test-Path -LiteralPath $mf)) { throw 'MANIFEST.sha256 missing in the package' }
if ($Action -ne 'Rollback') {
  if (-not $ManifestSha256) { throw '-ManifestSha256 is required: the SHA-256 of MANIFEST.sha256 you were given' }
  if ((Sha $mf) -ne $ManifestSha256.ToLower()) { throw 'MANIFEST.sha256 does not match the SHA-256 you were given: this is not the approved package' }
}
$entries = Get-Content -LiteralPath $mf | Where-Object { $_.Trim() } | ForEach-Object { $h, $rel = $_ -split '\s+', 2; [pscustomobject]@{ Hash = $h.ToLower(); Rel = $rel.Trim() } }
$files = @($entries | Where-Object { $_.Rel -like 'payload/*' } | ForEach-Object { [pscustomobject]@{ Hash = $_.Hash; Rel = $_.Rel.Substring(8) } })   # the files to install; the other manifest lines (this script, EXPECTED_OLD.txt) are only verified
$replaces = @{}; $rp = Join-Path $Package 'EXPECTED_OLD.txt'
if (Test-Path -LiteralPath $rp) { Get-Content -LiteralPath $rp | Where-Object { $_.Trim() } | ForEach-Object { $h, $rel = $_ -split '\s+', 2; $replaces[$rel.Trim()] = $h.ToLower() } }
function Get-Pw { $m = Select-String -Path $EnvFile -Pattern '^\s*ACCESS_PASSWORD\s*=(.*)$' | Select-Object -First 1; if (-not $m) { throw 'ACCESS_PASSWORD not found in .env' }; $m.Matches[0].Groups[1].Value.Trim().Trim([char]34).Trim([char]39) }
function Get-Status([string]$url, $headers) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $headers -TimeoutSec 60; return @{ code = [int]$r.StatusCode; body = $r.Content } } catch { $c = 0; if ($_.Exception.Response) { $c = [int]$_.Exception.Response.StatusCode }; return @{ code = $c; body = '' } } }
function Test-Health([int]$expectInflight) {
  if ($NoHealth) { Log 'health skipped (-NoHealth)'; return $expectInflight }
  if ($SimulateHealthFailure) { throw 'simulated health failure' }
  $base = "http://127.0.0.1:$Port"; $h = @{ Authorization = "Bearer $(Get-Pw)" }
  $m = Get-Status "$base/api/admin/flow-metrics" $h
  if ($m.code -ne 200) { throw "flow-metrics HTTP $($m.code)" }
  $mi = (ConvertFrom-Json $m.body).max_inflight
  if ($expectInflight -ge 0 -and $mi -ne $expectInflight) { throw "max_inflight changed from $expectInflight to $mi" }
  Log "health: max_inflight = $mi (unchanged)"
  $o = Get-Status "$base/api/options/flow/AAPL?limit=5" $h
  if ($o.code -ne 200 -or ((ConvertFrom-Json $o.body).status -ne 'OK')) { throw "Options Flow is not OK (HTTP $($o.code))" }
  Log 'health: Options Flow OK'
  $e = Get-Status "$base/api/smart-move/engine20/latest" $h
  if ($e.code -ne 200) { throw "the Engine20 snapshot route no longer answers (HTTP $($e.code))" }
  Log 'health: Engine20 snapshot route still OK'
  $u = Get-Status "$base/api/earnings-monitor/status" @{}
  if ($u.code -ne 401) { throw "monitor route unauthenticated returned $($u.code), expected 401" }
  Log 'health: monitor API still requires login'
  return $mi
}
function Restart-Svc {
  if ($SkipRestartCommand) { Log 'restart command skipped (-SkipRestartCommand: rehearsal only)'; return }
  $nssm = Get-Command nssm -ErrorAction SilentlyContinue
  if (-not $nssm) { throw 'nssm not found in PATH' }
  Log "restarting $Service"; & nssm restart $Service | Out-Null
  $until = (Get-Date).AddSeconds(120)
  do { Start-Sleep -Seconds 3; $up = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue } while (-not $up -and (Get-Date) -lt $until)
  if (-not $up) { throw "port $Port is not listening 120 s after the restart" }
  Start-Sleep -Seconds 5; Log "$Service is listening on $Port"
}
function Check-Kit {
  foreach ($e in $entries) { $s = Join-Path $Package ($e.Rel -replace '/', '\'); if (-not (Test-Path -LiteralPath $s)) { throw "kit file missing: $($e.Rel)" }; if ((Sha $s) -ne $e.Hash) { throw "kit file hash mismatch: $($e.Rel)" } }
  Log "kit OK: $($entries.Count) files match the manifest, $($files.Count) to install"
}
function Plan {
  $add = @(); $rep = @(); $same = 0
  foreach ($e in $files) {
    $t = Join-Path $App ($e.Rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $t)) { $add += $e; continue }
    $cur = ShaN $t
    if ($cur -eq $e.Hash -or (Sha $t) -eq $e.Hash) { $same++; continue }
    if ($replaces.ContainsKey($e.Rel) -and $cur -eq $replaces[$e.Rel]) { $rep += $e; continue }
    throw "$($e.Rel) is neither the known old version nor the new one (unexpected version on the server); refusing to touch it"
  }
  Log "plan: add $($add.Count), replace $($rep.Count), already identical $same"
  return @{ add = $add; rep = $rep }
}

if ($Action -eq 'Preflight' -or $Action -eq 'Install') {
  if (-not (Test-Path -LiteralPath (Join-Path $Backend 'main.py'))) { throw "$App does not look like the application (backend\main.py missing)" }
  Check-Kit; $plan = Plan
  if ($Action -eq 'Preflight') { Log 'PREFLIGHT OK (nothing was changed)'; return }
}
if ($Action -eq 'Install') {
  $baseInflight = -1
  if (-not $NoHealth -and -not $SimulateHealthFailure) { $baseInflight = Test-Health -1 }
  $ts = (Get-Date).ToString('yyyyMMdd_HHmmss_fff'); $bk = Join-Path $App ("_backup_uw_concurrency_" + $ts); New-Item -ItemType Directory -Force -Path $bk | Out-Null
  $prev = if (Test-Path -LiteralPath $Marker) { Get-Content -Raw -LiteralPath $Marker | ConvertFrom-Json } else { $null }     # a second run must not forget what the first one did
  $rec = [ordered]@{ installed_at = (Get-Date).ToUniversalTime().ToString('o'); manifest_sha256 = (Sha $mf); backup = $(if ($prev) { $prev.backup } else { $bk }); replaced = @(); added = @(); env_keys_added = @() }
  if ($prev) { $rec.replaced = @($prev.replaced | Where-Object { $_ }); $rec.added = @($prev.added | Where-Object { $_ }); $rec.env_keys_added = @($prev.env_keys_added | Where-Object { $_ }) }
  try {
    foreach ($e in $plan.rep) { $t = Join-Path $App ($e.Rel -replace '/', '\'); $b = Join-Path $bk ($e.Rel -replace '/', '\'); New-Item -ItemType Directory -Force -Path (Split-Path $b) | Out-Null; Copy-Item -LiteralPath $t -Destination $b; $rec.replaced = @($rec.replaced) + $e.Rel }
    foreach ($e in @($plan.rep) + @($plan.add)) {
      $t = Join-Path $App ($e.Rel -replace '/', '\'); New-Item -ItemType Directory -Force -Path (Split-Path $t) | Out-Null
      Copy-Item -LiteralPath (Join-Path $Package ('payload\' + ($e.Rel -replace '/', '\'))) -Destination $t -Force
      if ((Sha $t) -ne $e.Hash) { throw "copy verification failed for $($e.Rel)" }
      if ($plan.add -contains $e) { $rec.added = @($rec.added) + $e.Rel }
    }
    Log "files installed (backup of the replaced file: $bk)"
    if ($ConfigureSlots) {
      New-Item -ItemType Directory -Force -Path $SlotDir | Out-Null
      $cur = if (Test-Path -LiteralPath $EnvFile) { Get-Content -LiteralPath $EnvFile } else { @() }
      foreach ($line in @('UW_MACHINE_SLOTS=1', "UW_SLOT_DIR=$SlotDir")) { $key = $line.Split('=')[0]; if (-not ($cur | Where-Object { $_ -match "^\s*$key\s*=" })) { Add-Content -LiteralPath $EnvFile -Value $line -Encoding ascii; $rec.env_keys_added = @($rec.env_keys_added) + $key } }
      Log "machine-wide slots configured: $($rec.env_keys_added -join ', ')"
    } else { Log 'machine-wide slots NOT configured (-ConfigureSlots not given): the new code is inert' }
    ($rec | ConvertTo-Json) | Set-Content -LiteralPath $Marker -Encoding ascii
    if ($Restart) { Restart-Svc; [void](Test-Health $baseInflight) } else { Log 'restart skipped: the service keeps running the old code until you restart it (separate step)' }
  } catch {
    Log "FAILURE: $($_.Exception.Message)  -> rolling back automatically"
    & $PSCommandPath -Action Rollback -Package $Package -App $App -Service $Service -Port $Port -BackupDir $rec.backup -NoHealth:$true -Restart:$Restart -SkipRestartCommand:$SkipRestartCommand
    throw "INSTALL FAILED AND WAS ROLLED BACK: $($_.Exception.Message)"
  }
  Log "INSTALL OK. Backup kept at $bk (delete it only after you are satisfied)."
}
if ($Action -eq 'Rollback') {
  if (-not (Test-Path -LiteralPath $Marker)) { throw 'no install record: nothing to roll back' }
  $rec = Get-Content -Raw -LiteralPath $Marker | ConvertFrom-Json
  $bk = if ($BackupDir) { $BackupDir } else { $rec.backup }
  foreach ($rel in @($rec.replaced)) { $b = Join-Path $bk ($rel -replace '/', '\'); if (Test-Path -LiteralPath $b) { Copy-Item -LiteralPath $b -Destination (Join-Path $App ($rel -replace '/', '\')) -Force; Log "restored $rel" } }
  $byRel = @{}; foreach ($e in $files) { $byRel[$e.Rel] = $e.Hash }
  foreach ($rel in @($rec.added)) { $t = Join-Path $App ($rel -replace '/', '\'); if (Test-Path -LiteralPath $t) { if ($byRel.ContainsKey($rel) -and (Sha $t) -ne $byRel[$rel]) { Log "kept (modified since install): $rel" } else { Remove-Item -LiteralPath $t -Force; Log "removed $rel" } } }
  if ($rec.env_keys_added -and (Test-Path -LiteralPath $EnvFile)) {
    $keep = Get-Content -LiteralPath $EnvFile | Where-Object { $l = $_; -not (@($rec.env_keys_added) | Where-Object { $l -match "^\s*$_\s*=" }) }
    Set-Content -LiteralPath $EnvFile -Value $keep -Encoding ascii; Log "removed .env lines: $(@($rec.env_keys_added) -join ', ')"
  }
  Remove-Item -LiteralPath $Marker -Force
  if ($Restart) { Restart-Svc; [void](Test-Health -1) }
  Log 'ROLLBACK COMPLETE: the previous unusualwhales_service.py is back and nothing of this release remains.'
}
