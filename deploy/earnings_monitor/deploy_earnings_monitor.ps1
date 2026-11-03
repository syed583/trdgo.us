<#
  Earnings Monitor release: adds the read-only monitor API, its dashboard page and the (disabled) scheduler to USStockReader. Run it YOURSELF on the VPS, in an elevated PowerShell. It never sends a provider request,
  never enables the scheduler (no environment variable, no approval file is created) and never changes scoring.

    .\deploy_earnings_monitor.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>
    .\deploy_earnings_monitor.ps1 -Action Deploy    -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>      backup, install, restart, health checks; rolls back by itself on ANY failure
    .\deploy_earnings_monitor.ps1 -Action Update    -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>      replaces the monitor's code and the frontend of an ALREADY installed monitor; main.py and .env are not touched,
                                                                                                                         the scheduler mode (OFF or CACHE_ONLY) must be the same afterwards; rolls back by itself on ANY failure
    .\deploy_earnings_monitor.ps1 -Action RollbackCheck -Package $PWD.Path                                               READ ONLY: runs every check a rollback would run (recorded backup present and intact, installed files unchanged since the install) and changes nothing
    .\deploy_earnings_monitor.ps1 -Action Rollback  -Package $PWD.Path                                                   manual rollback of the LAST recorded install, using only the backup path recorded in monitor_install_record.json
                                                                                                                         (-BackupDir may be given, but must be exactly the recorded path; a backup from before the record existed needs -AcceptUnrecordedBackup)

  Always pass -Package explicitly (PowerShell does not fill $PSScriptRoot in a param default when started with -File).
  Test switches (offline rehearsal only): -NoRestart -NoHealth -SimulateHealthFailure -SkipManifestAnchor
#>
[CmdletBinding()]
param(
  [ValidateSet('Preflight', 'Deploy', 'Update', 'Rollback', 'RollbackCheck')][string]$Action = 'Preflight',
  [string]$App = 'C:\apps\us-stock-reader',
  [Parameter(Mandatory = $true)][string]$Package,
  [string]$ManifestSha256 = '',
  [switch]$SkipManifestAnchor,
  [string]$Service = 'USStockReader',
  [int]$Port = 8000,
  [string]$BackupDir = '',
  [switch]$AcceptUnrecordedBackup,
  [switch]$NoRestart,
  [switch]$NoHealth,
  [switch]$SimulateHealthFailure,
  [switch]$SimulateBadBackup
)
$ErrorActionPreference = 'Stop'

# backend\main.py as left by the Engine20 release (LF-normalised), and the same file after the monitor patch
$ExpectedMainLf = 'b65fbd0e916d258108dd506921a3c267039ba558da0faf152ab27f2c58e36d35'
$PatchedMainLf  = '211b3b57c22289f0e70129aba6826dc2039231e44504aa80d37e4df7a173a0a3'
$Anchor = 'app.include_router(_sm_engine20_router)'
$PatchLf = "`n# Automatic earnings monitor: read-only API (behind the same session gate) and a scheduler that starts ONLY when EARNINGS_MONITOR_ENABLED=1 and does nothing without a valid owner approval file.`nfrom earnings_monitor.routes import router as _earnings_monitor_router  # noqa: E402`napp.include_router(_earnings_monitor_router)`n`n`n@_bg`ndef _earnings_monitor_boot():`n    from earnings_monitor import runtime as _em_runtime`n    _em_runtime.start_if_enabled()`n"

$AllowMonitorOn = ($Action -eq 'Update') -or ($BackupDir -and (Test-Path (Join-Path $BackupDir 'update.marker')))      # an update (or the rollback of one) may find the monitor in cache-only mode; a first install may not
$Backend = Join-Path $App 'backend'
$MainPy = Join-Path $Backend 'main.py'
$EnvFile = Join-Path $Backend '.env'
$Dist = Join-Path $App 'frontend\dist'
$PkgDist = 'frontend_dist'
$PkgMod = 'earnings_monitor'

function Log([string]$m) { Write-Host ((Get-Date).ToUniversalTime().ToString('HH:mm:ss') + 'Z  ' + $m) }
function Sha([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }
function ShaLf([string]$p) {
  $b = [IO.File]::ReadAllBytes($p); $t = [Text.Encoding]::UTF8.GetString($b).Replace("`r`n", "`n")
  $h = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($t)); ([BitConverter]::ToString($h) -replace '-', '').ToLower()
}

function Test-Manifest {
  $mf = Join-Path $Package 'MANIFEST.sha256'
  if (-not (Test-Path $mf)) { throw 'MANIFEST.sha256 missing in the package' }
  if (-not $SkipManifestAnchor) {
    if (-not $ManifestSha256) { throw '-ManifestSha256 is required: the SHA-256 of MANIFEST.sha256 that the owner was given (it anchors the whole package)' }
    if ((Sha $mf) -ne $ManifestSha256.ToLower()) { throw 'MANIFEST.sha256 does not match the SHA-256 you were given: this is not the approved package' }
    Log 'manifest anchor OK'
  }
  $listed = @{}
  foreach ($line in (Get-Content $mf)) {
    if (-not $line.Trim()) { continue }
    $h, $rel = $line -split '\s+', 2
    $listed[$rel.Trim()] = $h.ToLower()
  }
  $bad = @()
  foreach ($rel in $listed.Keys) {
    $p = Join-Path $Package ($rel -replace '/', '\')
    if (-not (Test-Path $p)) { $bad += "missing: $rel"; continue }
    if ((Sha $p) -ne $listed[$rel]) { $bad += "hash differs: $rel" }
  }
  $root = (Resolve-Path $Package).Path.TrimEnd('\')
  foreach ($f in (Get-ChildItem $Package -Recurse -File)) {
    $rel = $f.FullName.Substring($root.Length + 1).Replace('\', '/')
    if ($rel -ne 'MANIFEST.sha256' -and -not $listed.ContainsKey($rel)) { $bad += "not in manifest: $rel" }
  }
  if ($bad.Count) { throw ('package check failed: ' + ($bad[0..([Math]::Min(4, $bad.Count - 1))] -join '; ') + " ($($bad.Count) problems)") }
  Log "package OK: $($listed.Count) files match MANIFEST.sha256"
}

function Get-Pw {
  $m = Select-String -Path $EnvFile -Pattern '^\s*ACCESS_PASSWORD\s*=(.*)$' | Select-Object -First 1
  if (-not $m) { throw 'ACCESS_PASSWORD not found in .env' }
  $m.Matches[0].Groups[1].Value.Trim().Trim([char]34).Trim([char]39)
}
function Get-Status([string]$url, $headers) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $headers -TimeoutSec 40; return @{ code = [int]$r.StatusCode; body = $r.Content } }
  catch { if ($_.Exception.Response) { return @{ code = [int]$_.Exception.Response.StatusCode.value__; body = '' } } else { throw } }
}

# $full = also check the new monitor routes; otherwise only the production basics (used before the change and after a rollback)
function Test-Health([bool]$full) {
  if ($SimulateHealthFailure) { if ($full) { throw 'simulated health failure' } else { Log 'health (baseline) skipped: rehearsal'; return } }
  $base = "http://127.0.0.1:$Port"; $pw = Get-Pw; $h = @{ Authorization = "Bearer $pw" }
  $m = Get-Status "$base/api/admin/flow-metrics" $h
  if ($m.code -ne 200 -or ((ConvertFrom-Json $m.body).max_inflight -ne 2)) { throw "max_inflight is not 2 (HTTP $($m.code))" }
  Log 'health: max_inflight = 2'
  $o = Get-Status "$base/api/options/flow/AAPL?limit=5" $h
  if ($o.code -ne 200 -or ((ConvertFrom-Json $o.body).status -ne 'OK')) { throw "Options Flow is not OK (HTTP $($o.code))" }
  Log 'health: Options Flow OK'
  $e20 = Get-Status "$base/api/smart-move/engine20/latest" $h
  if ($e20.code -ne 200) { throw "the Engine20 snapshot route no longer answers (HTTP $($e20.code))" }
  Log 'health: Engine20 snapshot route still OK'
  $u = Get-Status "$base/api/earnings-monitor/status" @{}
  if ($full) {
    if ($u.code -ne 401) { throw "unauthenticated monitor route returned $($u.code), expected 401" }
    $a = Get-Status "$base/api/earnings-monitor/status" $h
    if ($a.code -ne 200) { throw "authenticated monitor route returned $($a.code)" }
    $j = ConvertFrom-Json $a.body
    if (-not $AllowMonitorOn -and $j.scheduling -ne 'OFF') { throw "the monitor reports scheduling $($j.scheduling), expected OFF (nothing may be switched on by this release)" }
    if ($AllowMonitorOn -and $j.scheduling -eq 'REFRESH') { throw 'the monitor reports provider refresh: this update never enables it' }
    Log "health: monitor API 401 unauthenticated; authenticated OK; scheduling $($j.scheduling)"
  } elseif ($u.code -eq 200) { throw 'monitor route still answers 200 after rollback' }
  Remove-Variable pw, h
}

function Restart-Svc {
  if ($NoRestart) { Log 'restart skipped (-NoRestart)'; return }
  $nssm = (Get-Command nssm -ErrorAction SilentlyContinue)
  if (-not $nssm) { throw 'nssm not found in PATH' }
  Log "restarting $Service"; & nssm restart $Service | Out-Null
  $until = (Get-Date).AddSeconds(120)
  do { Start-Sleep -Seconds 3; $up = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) } while (-not $up -and (Get-Date) -lt $until)
  if (-not $up) { throw "port $Port is not listening 120 s after the restart" }
  Start-Sleep -Seconds 5
  Log "$Service is listening on $Port"
}

function Invoke-Preflight {
  Log "PREFLIGHT  app=$App  package=$Package"
  Test-Manifest
  foreach ($p in @($MainPy, $EnvFile, $Dist, (Join-Path $Dist 'index.html'))) { if (-not (Test-Path $p)) { throw "not found: $p" } }
  $cur = ShaLf $MainPy
  if ($cur -eq $PatchedMainLf) { throw 'main.py already contains the monitor patch (nothing to do)' }
  if ($cur -ne $ExpectedMainLf) { throw "main.py is not the Engine20 release version (LF sha256 $cur). Stop: the live file differs, the patch has not been validated against it." }
  Log 'main.py matches the Engine20 release'
  if (Test-Path (Join-Path $Backend $PkgMod)) { throw "$PkgMod already exists in the backend" }
  $envText = [IO.File]::ReadAllText($EnvFile)
  if ($envText -match '(?m)^\s*EARNINGS_MONITOR_(ENABLED|APPROVAL|DIR)\s*=') { throw '.env already sets an EARNINGS_MONITOR_* variable: this release must go out with the monitor switched off' }
  if (-not $NoRestart) { if ((Get-Service $Service).Status -ne 'Running') { throw "$Service is not Running" } }
  $drive = (Get-Item $App).PSDrive; if ($drive.Free -lt 300MB) { throw 'less than 300 MB free' }
  if (-not $NoHealth) { Test-Health $false; Log 'health (baseline) OK before the change' }
  Log 'PREFLIGHT OK'
}

$RecordPath = Join-Path $App 'monitor_install_record.json'
function Get-StateDigest {
  # one digest of what is installed: main.py, every monitor code file, every frontend file (so a rollback can tell whether the server is still exactly as the recorded install left it)
  $lines = @()
  foreach ($f in @($MainPy) + @(Get-ChildItem -LiteralPath (Join-Path $Backend $PkgMod) -Recurse -File -Filter '*.py') + @(Get-ChildItem -LiteralPath $Dist -Recurse -File)) {
    $full = if ($f -is [string]) { $f } else { $f.FullName }
    $lines += ("{0}:{1}" -f $full.Substring($App.Length), (ShaLf $full))
  }
  $bytes = [Text.Encoding]::UTF8.GetBytes((($lines | Sort-Object) -join "`n"))
  return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)) -replace '-', '').ToLower()
}
function Get-HashMap([string[]]$roots) {
  # relative path -> SHA-256 for every file under the given roots (a root may be a file)
  $map = @{}
  foreach ($r in $roots) { if (Test-Path -LiteralPath $r -PathType Leaf) { $map[$r.Substring($App.Length)] = Sha $r } else { foreach ($f in Get-ChildItem -LiteralPath $r -Recurse -File) { $map[$f.FullName.Substring($App.Length)] = Sha $f.FullName } } }
  return $map
}
function Test-CopyMatchesSource([hashtable]$source, [string]$bk, [string]$bkRootMain, [string]$bkRootMod, [string]$bkRootDist) {
  # every file that was copied or moved into the backup must be byte-identical to the file it came from; returns the number of differences
  $bad = 0
  foreach ($k in $source.Keys) {
    $dst = if ($k -eq $MainPy.Substring($App.Length)) { Join-Path $bk 'main.py' } elseif ($k.StartsWith($Backend.Substring($App.Length) + '\' + $PkgMod + '\')) { Join-Path $bk ($PkgMod + $k.Substring(($Backend.Substring($App.Length) + '\' + $PkgMod).Length)) } elseif ($k.StartsWith($Dist.Substring($App.Length) + '\')) { Join-Path $bk ('dist' + $k.Substring($Dist.Substring($App.Length).Length)) } else { $null }
    if (-not $dst) { continue }
    if ($bkRootDist -eq 'skip' -and $dst.StartsWith((Join-Path $bk 'dist'))) { continue }
    if (-not (Test-Path -LiteralPath $dst) -or (Sha $dst) -ne $source[$k]) { $bad++; Log ("backup differs from its source: " + $k) }
  }
  return $bad
}
function Write-BackupManifest([string]$bk) {
  # every file in the backup with its SHA-256, written INTO the backup, so a rollback can prove the backup is intact before it restores anything
  $rows = foreach ($f in Get-ChildItem -LiteralPath $bk -Recurse -File | Where-Object { $_.Name -ne 'backup_manifest.json' }) { [pscustomobject]@{ path = $f.FullName.Substring($bk.Length); sha256 = (Sha $f.FullName) } }
  @($rows) | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $bk 'backup_manifest.json') -Encoding ascii
}
function Test-BackupManifest([string]$bk) {
  $m = Join-Path $bk 'backup_manifest.json'
  if (-not (Test-Path -LiteralPath $m)) { return $false }
  $entries = Get-Content -LiteralPath $m -Raw | ConvertFrom-Json
  foreach ($r in @($entries)) {
    $f = $bk + $r.path
    if (-not (Test-Path -LiteralPath $f) -or (Sha $f) -ne $r.sha256) { Log ("backup file missing or changed: " + $r.path); return $false }
  }
  return $true
}
function Write-InstallRecord([string]$bk, [string]$kind) {
  $hist = @(); if (Test-Path -LiteralPath $RecordPath) { $old = Get-Content -LiteralPath $RecordPath -Raw | ConvertFrom-Json; $hist = @($old.history) + @($old.last) }
  $last = [pscustomobject]@{ action = $kind; at_utc = (Get-Date).ToUniversalTime().ToString('o'); release_manifest_sha256 = (Sha (Join-Path $Package 'MANIFEST.sha256')); backup_path = $bk
                             backup_manifest_sha256 = (Sha (Join-Path $bk 'backup_manifest.json')); installed_state_digest = (Get-StateDigest) }
  [pscustomobject]@{ last = $last; history = @($hist | Where-Object { $_ }) } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $RecordPath -Encoding ascii
  Log "install recorded in $RecordPath (backup $bk)"
}
function Assert-RecordedRollbackReady([string]$given) {
  # every precondition of a rollback, read only; returns the record or throws (nothing is changed either way)
  $rec = (Get-Content -LiteralPath $RecordPath -Raw | ConvertFrom-Json).last
  if (-not $rec) { throw 'the last recorded install has already been rolled back: there is nothing to roll back' }
  if ($given -and ($given.TrimEnd('\') -ne $rec.backup_path.TrimEnd('\'))) { throw ("-BackupDir is not the recorded backup. Recorded: {0}  Given: {1}" -f $rec.backup_path, $given) }
  if (-not (Test-Path -LiteralPath $rec.backup_path)) { throw "recorded backup folder not found: $($rec.backup_path)" }
  foreach ($need in 'main.py', $PkgMod, 'dist', 'backup_manifest.json') { if (-not (Test-Path -LiteralPath (Join-Path $rec.backup_path $need))) { throw "the recorded backup is incomplete: $need is missing. Stopping, nothing was changed." } }
  if (-not (Test-BackupManifest $rec.backup_path)) { throw 'the recorded backup is not intact: stopping, nothing was changed' }
  if ((Sha (Join-Path $rec.backup_path 'backup_manifest.json')) -ne $rec.backup_manifest_sha256) { throw 'the backup manifest differs from the one recorded at install time: stopping' }
  if ((Get-StateDigest) -ne $rec.installed_state_digest) { throw 'the installed files are not exactly what the recorded install left (something changed since): stopping, nothing was changed. Investigate before any rollback.' }
  return $rec
}
function Invoke-RollbackCheck {
  if (-not (Test-Path -LiteralPath $RecordPath)) { throw 'no monitor_install_record.json: nothing is recorded, so a recorded rollback is not possible' }
  $rec = Assert-RecordedRollbackReady $BackupDir
  $n = @(Get-Content (Join-Path $rec.backup_path 'backup_manifest.json') -Raw | ConvertFrom-Json).Count
  Log ("ROLLBACK CHECK PASSED (read only, nothing was changed): the recorded backup {0} is complete (main.py, monitor code, frontend) and all {1} files match its SHA-256 manifest; the installed files are exactly what the install of {2} left, so a rollback would run." -f $rec.backup_path, $n, $rec.at_utc)
}
function Invoke-RecordedRollback([string]$given) {
  # the manual rollback: only the backup of the last RECORDED install, only if it is intact and the server is still exactly as that install left it
  if (-not (Test-Path -LiteralPath $RecordPath)) {
    if ($given -and $AcceptUnrecordedBackup) { Log 'no install record: using the backup you named because -AcceptUnrecordedBackup was given'; Invoke-Rollback $given; return }
    throw 'no monitor_install_record.json: nothing was installed with this script version. To use an older backup, name it with -BackupDir AND add -AcceptUnrecordedBackup.'
  }
  $rec = Assert-RecordedRollbackReady $given
  Log ("rolling back the install recorded at {0} (release manifest {1})" -f $rec.at_utc, $rec.release_manifest_sha256)
  Invoke-Rollback $rec.backup_path
  $hist = @((Get-Content -LiteralPath $RecordPath -Raw | ConvertFrom-Json).history)
  [pscustomobject]@{ last = $null; history = @($hist + $rec | Where-Object { $_ }); rolled_back_at_utc = (Get-Date).ToUniversalTime().ToString('o'); rolled_back_backup = $rec.backup_path } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $RecordPath -Encoding ascii
}
function Invoke-Rollback([string]$bk) {
  Log "ROLLBACK from $bk"
  if (-not (Test-Path $bk)) { throw "backup folder not found: $bk" }
  $isUpdate = Test-Path (Join-Path $bk 'update.marker')
  Copy-Item (Join-Path $bk 'main.py') $MainPy -Force
  $mod = Join-Path $Backend $PkgMod; if (Test-Path $mod) { Remove-Item $mod -Recurse -Force }
  if ($isUpdate) { Copy-Item -Recurse (Join-Path $bk $PkgMod) $mod }
  if (Test-Path (Join-Path $bk 'dist')) {
    if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
    Copy-Item -Recurse (Join-Path $bk 'dist') $Dist                                    # a COPY: the backup stays complete after a rollback
    foreach ($f in Get-ChildItem -LiteralPath (Join-Path $bk 'dist') -Recurse -File) { $t = Join-Path $Dist $f.FullName.Substring((Join-Path $bk 'dist').Length); if (-not (Test-Path -LiteralPath $t) -or (Sha $t) -ne (Sha $f.FullName)) { throw "rollback: restored frontend file differs from the backup: $t" } }
  }
  if ((ShaLf $MainPy) -ne $(if ($isUpdate) { $PatchedMainLf } else { $ExpectedMainLf })) { throw 'rollback: main.py does not match the original after restore' }
  Restart-Svc
  if (-not $NoHealth) { Test-Health $false }
  Log 'ROLLBACK COMPLETE: main.py, frontend\dist and the backend files are as before'
}

function Invoke-Deploy {
  Invoke-Preflight
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss_fff'); $bk = Join-Path $App "_backup_earnings_monitor_$stamp"
  New-Item -ItemType Directory $bk | Out-Null
  Copy-Item $MainPy (Join-Path $bk 'main.py'); Copy-Item $EnvFile (Join-Path $bk 'env.bak')
  Log "backup written: $bk"
  try {
    Copy-Item -Recurse (Join-Path $Package "backend\$PkgMod") (Join-Path $Backend $PkgMod)
    $raw = [IO.File]::ReadAllText($MainPy); $eol = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lf = $raw.Replace("`r`n", "`n"); $needle = $Anchor + "`n"
    if (([regex]::Matches($lf, [regex]::Escape($needle))).Count -ne 1) { throw 'insertion point not found exactly once in main.py' }
    $lf = $lf.Replace($needle, $needle + $PatchLf)
    [IO.File]::WriteAllText($MainPy, $lf.Replace("`n", $eol), (New-Object Text.UTF8Encoding($false)))
    if ((ShaLf $MainPy) -ne $PatchedMainLf) { throw 'patched main.py does not match the validated result' }
    Log 'main.py patched and matches the validated result'
    Move-Item $Dist (Join-Path $bk 'dist'); Copy-Item -Recurse (Join-Path $Package $PkgDist) $Dist
    Log 'frontend\dist swapped (the old one is in the backup)'
    Restart-Svc
    if (-not $NoHealth) { Test-Health $true }
    Write-BackupManifest $bk
    Write-InstallRecord $bk 'Deploy'
    Log "DEPLOY OK. Backup kept at $bk (delete it only after you are satisfied). The monitor is installed but OFF."
  } catch {
    $why = $_.Exception.Message
    Log "FAILURE: $why  -> rolling back automatically"
    Invoke-Rollback $bk
    throw "DEPLOY FAILED AND WAS ROLLED BACK: $why"
  }
}

function Get-MonitorMode {
  if ($NoHealth -or $SimulateHealthFailure) { return 'UNCHECKED' }
  $pw = Get-Pw; $h = @{ Authorization = "Bearer $pw" }
  $a = Get-Status "http://127.0.0.1:$Port/api/earnings-monitor/status" $h
  if ($a.code -ne 200) { throw "monitor status answers HTTP $($a.code)" }
  $j = ConvertFrom-Json $a.body
  Remove-Variable pw, h
  return [string]$j.scheduling
}

function Invoke-Update {
  Log "UPDATE PREFLIGHT  app=$App  package=$Package"
  Test-Manifest
  foreach ($p in @($MainPy, $EnvFile, $Dist, (Join-Path $Dist 'index.html'), (Join-Path $Backend $PkgMod))) { if (-not (Test-Path $p)) { throw "not found: $p (is the monitor installed?)" } }
  if ((ShaLf $MainPy) -ne $PatchedMainLf) { throw 'main.py is not the version this release expects (the Engine20 release plus the monitor patch). Stop.' }
  if (-not $NoRestart) { if ((Get-Service $Service).Status -ne 'Running') { throw "$Service is not Running" } }
  $drive = (Get-Item $App).PSDrive; if ($drive.Free -lt 300MB) { throw 'less than 300 MB free' }
  $modeBefore = Get-MonitorMode
  if (-not $NoHealth) { Test-Health $false; Log "health (baseline) OK; the monitor reports scheduling $modeBefore" }
  Log 'UPDATE PREFLIGHT OK'
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss_fff'); $bk = Join-Path $App "_backup_monitor_update_$stamp"
  New-Item -ItemType Directory $bk | Out-Null
  $srcMap = Get-HashMap @($MainPy, (Join-Path $Backend $PkgMod), $Dist)
  Copy-Item $MainPy (Join-Path $bk 'main.py'); Copy-Item -Recurse (Join-Path $Backend $PkgMod) (Join-Path $bk $PkgMod); Copy-Item -Recurse $Dist (Join-Path $bk 'dist'); Set-Content (Join-Path $bk 'update.marker') 'update' -Encoding ascii
  if ($SimulateBadBackup) { Add-Content (Join-Path $bk 'main.py') '# corrupted for the rehearsal' }
  Log "backup written: $bk"
  $badCopies = Test-CopyMatchesSource $srcMap $bk $MainPy $PkgMod 'check'
  if ($badCopies) { throw "STOPPED BEFORE ANY CHANGE: the backup is not an exact copy of the installed files ($badCopies file(s) differ). The application was NOT modified; the incomplete backup folder $bk was left in place for inspection." }
  Log ("backup verified BEFORE any change: main.py, all {0} monitor files and all {1} frontend files are byte-identical to the installed files" -f @($srcMap.Keys | Where-Object { $_ -like '*earnings_monitor*' }).Count, @($srcMap.Keys | Where-Object { $_ -like '*\dist\*' }).Count)
  try {
    Remove-Item (Join-Path $Backend $PkgMod) -Recurse -Force
    Copy-Item -Recurse (Join-Path $Package "backend\$PkgMod") (Join-Path $Backend $PkgMod)
    Log 'monitor code replaced (main.py and .env untouched)'
    Remove-Item $Dist -Recurse -Force; Copy-Item -Recurse (Join-Path $Package $PkgDist) $Dist
    Log 'frontend\dist replaced (the old one is in the backup, verified byte-identical before this step)'
    Restart-Svc
    if (-not $NoHealth) {
      Test-Health $true
      $until = (Get-Date).AddSeconds(150)
      do { $modeAfter = Get-MonitorMode; if ($modeAfter -eq $modeBefore) { break }; Start-Sleep -Seconds 5 } while ((Get-Date) -lt $until)
      if ($modeAfter -ne $modeBefore) { throw "the scheduler mode changed from $modeBefore to $modeAfter" }
      Log "monitor still reports scheduling $modeAfter"
    }
    Write-BackupManifest $bk
    Write-InstallRecord $bk 'Update'
    Log "UPDATE OK. Backup kept at $bk (delete it only after you are satisfied). A manual rollback needs no path: it uses the recorded one."
  } catch {
    $why = $_.Exception.Message
    if ($why.StartsWith('STOPPED BEFORE ANY CHANGE')) { throw $why }
    Log "FAILURE: $why  -> rolling back automatically"
    try { Invoke-Rollback $bk } catch { throw ("UPDATE FAILED ($why) AND THE AUTOMATIC ROLLBACK ALSO FAILED ($($_.Exception.Message)). The server may be in a PARTIAL state. The backup is intact at $bk. Do not run anything else; send this output.") }
    throw "UPDATE FAILED AND WAS ROLLED BACK: $why"
  }
}

switch ($Action) {
  'Preflight' { Invoke-Preflight }
  'Update'    { Invoke-Update }
  'Deploy'    { Invoke-Deploy }
  'Rollback'  { Invoke-RecordedRollback $BackupDir }
  'RollbackCheck' { Invoke-RollbackCheck }
}
