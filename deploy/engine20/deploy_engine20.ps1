<#
  Smart Move Engine20 snapshot: deploys the release overlay (commit of branch release/smart-move-engine20, built on production commit 38ca61d) onto USStockReader.
  Run it YOURSELF on the VPS, in an elevated PowerShell. It never sends a provider request.

    .\deploy_engine20.ps1 -Action Preflight -ManifestSha256 <sha256 of MANIFEST.sha256>      read-only checks
    .\deploy_engine20.ps1 -Action Deploy    -ManifestSha256 <sha256 of MANIFEST.sha256>      preflight, backup, deploy, restart, health checks; rolls back by itself on ANY failure
    .\deploy_engine20.ps1 -Action Rollback  -BackupDir <dir>                                  manual rollback from a backup folder printed by Deploy

  The prediction snapshot is NOT part of this package (it is private). Put the two snapshot files in -SnapshotDir (default C:\deploy\private\engine20_snapshots);
  they are accepted only if their SHA-256 values equal the ones pinned below.

  Test switches (used only for the offline rehearsal): -NoRestart -NoHealth -SimulateHealthFailure
#>
[CmdletBinding()]
param(
  [ValidateSet('Preflight', 'Deploy', 'Rollback')][string]$Action = 'Preflight',
  [string]$App = 'C:\apps\us-stock-reader',
  [string]$Package = $PSScriptRoot,
  [string]$SnapshotDir = 'C:\deploy\private\engine20_snapshots',
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

# backend\main.py of the production commit 38ca61d (line endings normalised to LF) and the same file after the 3-line patch
$ExpectedMainLf = 'cabb4aac9b9d554ebed14de99a378d0314e9d44468cc3057b809e393db3cd865'
$PatchedMainLf  = 'b65fbd0e916d258108dd506921a3c267039ba558da0faf152ab27f2c58e36d35'
# SHA-256 of the two private snapshot files (a hash reveals nothing about the content); the expected prediction counts are read from the verified private index, never stored here
$SnapshotName = 'engine20_snapshot_20261103T082908Z_c96df752.json'
$SnapshotSha  = 'c96df752f5d3f944b211b4cc625b51b5ca58bb91905c5799fb5051e5012352de'
$IndexSha     = '5a84b12ef41ddea3c70bbd949b59b10af02fb53000a66747561aa688f1a551d6'
$script:ExpectedCounts = @{}
$Anchor = 'app.include_router(api_router)'
$PatchLf = "`n# Smart Move Engine20 snapshot (read-only; the session gate below covers it like every /api route). Serves a file snapshot; no provider code.`nfrom smart_move_engine20_routes import router as _sm_engine20_router  # noqa: E402`napp.include_router(_sm_engine20_router)`n"

$Backend = Join-Path $App 'backend'
$MainPy = Join-Path $Backend 'main.py'
$EnvFile = Join-Path $Backend '.env'
$Dist = Join-Path $App 'frontend\dist'
$PkgDist = 'frontend_dist'
$DataDir = Join-Path $App 'smart_move_data\engine20'
$NewPy = @('smart_move_engine20_routes.py', 'smart_move_engine20_snapshot.py')

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

function Test-PrivateSnapshot {
  $sf = Join-Path $SnapshotDir $SnapshotName; $ix = Join-Path $SnapshotDir 'snapshots.jsonl'
  foreach ($p in @($sf, $ix)) { if (-not (Test-Path $p)) { throw "private snapshot file missing: $p (copy it to the VPS first; it is not in GitHub)" } }
  if ((Sha $sf) -ne $SnapshotSha) { throw 'the snapshot file does not match the approved SHA-256' }
  if ((Sha $ix) -ne $IndexSha) { throw 'snapshots.jsonl does not match the approved SHA-256' }
  $idx = Get-Content $ix | ForEach-Object { ConvertFrom-Json $_ } | Select-Object -Last 1
  if ($idx.file -ne $SnapshotName -or $idx.sha256 -ne $SnapshotSha) { throw 'snapshots.jsonl does not describe the approved snapshot' }
  $script:ExpectedCounts = @{}
  foreach ($p in $idx.counts.PSObject.Properties) { if ($p.Name -ne 'PENDING_REFRESH') { $script:ExpectedCounts[$p.Name] = [int]$p.Value } }
  Log 'private snapshot files verified (approved SHA-256); expected counts loaded from the private index'
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

# $full = also check the new routes; otherwise only the production basics (used after a rollback)
function Test-Health([bool]$full) {
  if ($SimulateHealthFailure) { if ($full) { throw 'simulated health failure' } else { Log 'health (baseline) skipped: rehearsal'; return } }
  $base = "http://127.0.0.1:$Port"; $pw = Get-Pw; $h = @{ Authorization = "Bearer $pw" }
  $m = Get-Status "$base/api/admin/flow-metrics" $h
  if ($m.code -ne 200 -or ((ConvertFrom-Json $m.body).max_inflight -ne 2)) { throw "max_inflight is not 2 (HTTP $($m.code))" }
  Log 'health: max_inflight = 2'
  $o = Get-Status "$base/api/options/flow/AAPL?limit=5" $h
  if ($o.code -ne 200 -or ((ConvertFrom-Json $o.body).status -ne 'OK')) { throw "Options Flow is not OK (HTTP $($o.code))" }
  Log 'health: Options Flow OK'
  $u = Get-Status "$base/api/smart-move/engine20/latest" @{}
  if ($full) {
    if ($u.code -ne 401) { throw "unauthenticated engine20 route returned $($u.code), expected 401" }
    $a = Get-Status "$base/api/smart-move/engine20/latest" $h
    if ($a.code -ne 200) { throw "authenticated engine20 route returned $($a.code)" }
    $j = ConvertFrom-Json $a.body
    if ($j.status -ne 'OK' -or $j.is_live_signal -ne $false) { throw 'engine20 route: status not OK or is_live_signal not false' }
    foreach ($k in $script:ExpectedCounts.Keys) { if ([int]$j.counts.$k -ne $script:ExpectedCounts[$k]) { throw "engine20 count $k = $($j.counts.$k), expected $($script:ExpectedCounts[$k])" } }
    Log 'health: engine20 route 401 unauthenticated; authenticated OK, historical snapshot, counts equal the private index'
  } elseif ($u.code -eq 200) { throw 'engine20 route still answers 200 after rollback' }
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
  Test-PrivateSnapshot
  foreach ($p in @($MainPy, $EnvFile, $Dist, (Join-Path $Dist 'index.html'))) { if (-not (Test-Path $p)) { throw "not found: $p" } }
  $cur = ShaLf $MainPy
  if ($cur -eq $PatchedMainLf) { throw 'main.py already contains the Engine20 patch (nothing to do)' }
  if ($cur -ne $ExpectedMainLf) { throw "main.py is not the production commit 38ca61d version (LF sha256 $cur). Stop: the live file differs, the patch has not been validated against it." }
  Log 'main.py matches production commit 38ca61d'
  foreach ($f in $NewPy) { if (Test-Path (Join-Path $Backend $f)) { throw "$f already exists in the backend" } }
  if (-not $NoRestart) { if ((Get-Service $Service).Status -ne 'Running') { throw "$Service is not Running" } }
  $drive = (Get-Item $App).PSDrive; if ($drive.Free -lt 300MB) { throw 'less than 300 MB free' }
  if (-not $NoHealth) { Test-Health $false; Log 'health (baseline) OK before the change' }
  Log 'PREFLIGHT OK'
}

function Invoke-Rollback([string]$bk) {
  Log "ROLLBACK from $bk"
  if (-not (Test-Path $bk)) { throw "backup folder not found: $bk" }
  Copy-Item (Join-Path $bk 'main.py') $MainPy -Force
  Copy-Item (Join-Path $bk 'env.bak') $EnvFile -Force
  foreach ($f in $NewPy) { $p = Join-Path $Backend $f; if (Test-Path $p) { Remove-Item $p -Force } }
  if (Test-Path (Join-Path $bk 'dist')) { if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }; Move-Item (Join-Path $bk 'dist') $Dist }
  if ((ShaLf $MainPy) -ne $ExpectedMainLf) { throw 'rollback: main.py does not match the original after restore' }
  Restart-Svc
  if (-not $NoHealth) { Test-Health $false }
  Log 'ROLLBACK COMPLETE: main.py, .env, frontend\dist and the backend files are as before'
}

function Invoke-Deploy {
  Invoke-Preflight
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss'); $bk = Join-Path $App "_backup_engine20_$stamp"
  New-Item -ItemType Directory $bk | Out-Null
  Copy-Item $MainPy (Join-Path $bk 'main.py'); Copy-Item $EnvFile (Join-Path $bk 'env.bak')
  Log "backup written: $bk"
  try {
    foreach ($f in $NewPy) { Copy-Item (Join-Path $Package "backend\$f") (Join-Path $Backend $f) }
    New-Item -ItemType Directory -Force $DataDir | Out-Null
    Copy-Item (Join-Path $SnapshotDir $SnapshotName) $DataDir -Force; Copy-Item (Join-Path $SnapshotDir 'snapshots.jsonl') $DataDir -Force
    $idx = Get-Content (Join-Path $DataDir 'snapshots.jsonl') | ForEach-Object { ConvertFrom-Json $_ } | Select-Object -Last 1
    if ((Sha (Join-Path $DataDir $idx.file)) -ne $idx.sha256) { throw 'snapshot file does not match its recorded hash' }
    Log 'snapshot copied and hash verified'
    $raw = [IO.File]::ReadAllText($MainPy); $eol = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lf = $raw.Replace("`r`n", "`n"); $needle = $Anchor + "`n"
    if (([regex]::Matches($lf, [regex]::Escape($needle))).Count -ne 1) { throw 'insertion point not found exactly once in main.py' }
    $lf = $lf.Replace($needle, $needle + $PatchLf)
    [IO.File]::WriteAllText($MainPy, $lf.Replace("`n", $eol), (New-Object Text.UTF8Encoding($false)))
    if ((ShaLf $MainPy) -ne $PatchedMainLf) { throw 'patched main.py does not match the validated result' }
    Log 'main.py patched (3 lines) and matches the validated result'
    $envText = [IO.File]::ReadAllText($EnvFile)
    if ($envText -notmatch '(?m)^\s*SMART_MOVE_ENGINE20_DIR\s*=') {
      if ($envText.Length -gt 0 -and -not $envText.EndsWith("`n")) { $envText += "`r`n" }
      [IO.File]::WriteAllText($EnvFile, $envText + "SMART_MOVE_ENGINE20_DIR=$DataDir`r`n", (New-Object Text.UTF8Encoding($false)))
    }
    Log '.env: SMART_MOVE_ENGINE20_DIR set'
    Move-Item $Dist (Join-Path $bk 'dist'); Copy-Item -Recurse (Join-Path $Package $PkgDist) $Dist
    Log 'frontend\dist swapped (the old one is in the backup)'
    Restart-Svc
    if (-not $NoHealth) { Test-Health $true }
    Log "DEPLOY OK. Backup kept at $bk (delete it only after you are satisfied)"
  } catch {
    $why = $_.Exception.Message
    Log "FAILURE: $why  -> rolling back automatically"
    Invoke-Rollback $bk
    throw "DEPLOY FAILED AND WAS ROLLED BACK: $why"
  }
}

switch ($Action) {
  'Preflight' { Invoke-Preflight }
  'Deploy'    { Invoke-Deploy }
  'Rollback'  { if (-not $BackupDir) { throw '-BackupDir is required' }; Invoke-Rollback $BackupDir }
}
