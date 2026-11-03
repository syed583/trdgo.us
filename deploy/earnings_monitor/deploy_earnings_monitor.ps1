<#
  Earnings Monitor release: adds the read-only monitor API, its dashboard page and the (disabled) scheduler to USStockReader. Run it YOURSELF on the VPS, in an elevated PowerShell. It never sends a provider request,
  never enables the scheduler (no environment variable, no approval file is created) and never changes scoring.

    .\deploy_earnings_monitor.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>
    .\deploy_earnings_monitor.ps1 -Action Deploy    -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>      backup, install, restart, health checks; rolls back by itself on ANY failure
    .\deploy_earnings_monitor.ps1 -Action Update    -Package $PWD.Path -ManifestSha256 <sha256 of MANIFEST.sha256>      replaces the monitor's code and the frontend of an ALREADY installed monitor; main.py and .env are not touched,
                                                                                                                         the scheduler mode (OFF or CACHE_ONLY) must be the same afterwards; rolls back by itself on ANY failure
    .\deploy_earnings_monitor.ps1 -Action Rollback  -Package $PWD.Path -BackupDir <dir>                                  manual rollback from the backup folder Deploy / Update printed

  Always pass -Package explicitly (PowerShell does not fill $PSScriptRoot in a param default when started with -File).
  Test switches (offline rehearsal only): -NoRestart -NoHealth -SimulateHealthFailure -SkipManifestAnchor
#>
[CmdletBinding()]
param(
  [ValidateSet('Preflight', 'Deploy', 'Update', 'Rollback')][string]$Action = 'Preflight',
  [string]$App = 'C:\apps\us-stock-reader',
  [Parameter(Mandatory = $true)][string]$Package,
  [string]$ManifestSha256 = '',
  [switch]$SkipManifestAnchor,
  [string]$Service = 'USStockReader',
  [int]$Port = 8000,
  [string]$BackupDir = '',
  [switch]$NoRestart,
  [switch]$NoHealth,
  [switch]$SimulateHealthFailure
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

function Invoke-Rollback([string]$bk) {
  Log "ROLLBACK from $bk"
  if (-not (Test-Path $bk)) { throw "backup folder not found: $bk" }
  $isUpdate = Test-Path (Join-Path $bk 'update.marker')
  Copy-Item (Join-Path $bk 'main.py') $MainPy -Force
  $mod = Join-Path $Backend $PkgMod; if (Test-Path $mod) { Remove-Item $mod -Recurse -Force }
  if ($isUpdate) { Copy-Item -Recurse (Join-Path $bk $PkgMod) $mod }
  if (Test-Path (Join-Path $bk 'dist')) { if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }; Move-Item (Join-Path $bk 'dist') $Dist }
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
  Copy-Item $MainPy (Join-Path $bk 'main.py'); Copy-Item -Recurse (Join-Path $Backend $PkgMod) (Join-Path $bk $PkgMod); Set-Content (Join-Path $bk 'update.marker') 'update' -Encoding ascii
  Log "backup written: $bk"
  try {
    Remove-Item (Join-Path $Backend $PkgMod) -Recurse -Force
    Copy-Item -Recurse (Join-Path $Package "backend\$PkgMod") (Join-Path $Backend $PkgMod)
    Log 'monitor code replaced (main.py and .env untouched)'
    Move-Item $Dist (Join-Path $bk 'dist'); Copy-Item -Recurse (Join-Path $Package $PkgDist) $Dist
    Log 'frontend\dist swapped (the old one is in the backup)'
    Restart-Svc
    if (-not $NoHealth) {
      Test-Health $true
      $until = (Get-Date).AddSeconds(150)
      do { $modeAfter = Get-MonitorMode; if ($modeAfter -eq $modeBefore) { break }; Start-Sleep -Seconds 5 } while ((Get-Date) -lt $until)
      if ($modeAfter -ne $modeBefore) { throw "the scheduler mode changed from $modeBefore to $modeAfter" }
      Log "monitor still reports scheduling $modeAfter"
    }
    Log "UPDATE OK. Backup kept at $bk (delete it only after you are satisfied)."
  } catch {
    $why = $_.Exception.Message
    Log "FAILURE: $why  -> rolling back automatically"
    Invoke-Rollback $bk
    throw "UPDATE FAILED AND WAS ROLLED BACK: $why"
  }
}

switch ($Action) {
  'Preflight' { Invoke-Preflight }
  'Update'    { Invoke-Update }
  'Deploy'    { Invoke-Deploy }
  'Rollback'  { if (-not $BackupDir) { throw '-BackupDir is required' }; Invoke-Rollback $BackupDir }
}
