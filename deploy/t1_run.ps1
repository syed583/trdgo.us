# TRDGO-US Stage T runner: the LIVE EVENT STRIP is back on Strategy Trades. The old page pinned and blinked every trade event at the top; the redesign kept only the spoken call-out. A strip now sits directly under the page header, always present so it has room of its own and covers nothing, and holds the last ten events, newest first. Each event shows the SYMBOL first, then what happened - BUY in green, SELL in red, STOP LOSS in red, TARGET in green, SCORE EXIT in amber - then the price, the time in New York, and for an exit its profit in green or loss in red. A new event BLINKS FOR SIX SECONDS and then stays, still; the matching table row flashes for the same six seconds and carries a small badge for as long as the event is on the strip. EVERY EVENT IS A REAL TRADE ROW, found the way the old page found them: by comparing each reading of the trade list with the one before it. The first reading only sets the baseline, so opening the page never announces the whole day as though it had just happened, and a refresh that changes nothing shows nothing - the same event cannot blink twice. ONE LIST NOW DRIVES THE STRIP, THE ROW FLASH AND THE VOICE ALERT, so what is spoken, what blinks and which row lights up cannot disagree; the sound still obeys its own on/off button. TWO THINGS SAID PLAINLY ON THE STRIP ITSELF: a close that is not a stop, target or score exit - the end-of-day close, the bell - is shown as CLOSED rather than dropped or mislabelled; and because the server sends an exit DATE but no exit time of day, an exit shows the New York time this page SAW the change, marked seen, which can trail the exit itself by one refresh. Entries show the server's own entry time. People who have asked their system for less motion get the colours without the pulsing. FRONTEND ONLY: no backend file changes. NEW_UI_USERS is not touched. No scoring, A.27 rule, order logic, MT5, or Smart Move engine, route or scanner change.
# Save as C:\t1_run.ps1 and run from PowerShell:   powershell -NoProfile -ExecutionPolicy Bypass -File C:\t1_run.ps1 -Mode <Mode> [options]
# READ-ONLY modes (nothing in production is changed; the only thing written is a temp folder C:\t1run_<timestamp>):
#   PreCheck      Smart Money Move schedule guard, package download + manifest verification, what would change on the server
#   PostCheck     after an install: every package file is in place, the removed modules are gone, the service answers
#   RecoveryInfo  after an interrupted install: newest backup, verified?, does the server differ?, exact recovery command
# CHANGING modes (each needs ALL of its confirmations, typed exactly):
#   Update    -ConfirmWord YES-INSTALL-T1  -ApproveCommit <40-hex commit>  -ApproveManifest <64-hex manifest SHA-256>
#   Rollback  -ConfirmWord YES-ROLLBACK-T1
#   Recover   -ConfirmWord YES-RECOVER-T1   (only after RecoveryInfo)
# What Update does: backs up every file it will touch (byte-for-byte verified), copies the package's backend files, deletes the
# retired modules, replaces frontend\dist, restarts the service (nssm), waits for /health, checks the Phase 2A scoreboard
# route, the Smart Money Move dashboard route, the ETF strip and release_check.py -- and rolls itself back on ANY failure.
# NEVER touched: backend\.env, backend\smart_move_*, backend\earnings_monitor\, provider_ledger.py, signal_class.py, .provider_ledger\,
# collector_tools\, docs\smart_move\, sm_collector_state\, sm_evidence_store\, smart_move_data\, em_data\, the install records,
# C:\smlive, the database (the app adds six nullable demo_trades columns itself at the first tick), scheduled tasks.
# Provider side effect: the post-install ETF-strip check asks the app for SPY/QQQ/DIA quotes (3 Unusual Whales requests at most,
# served from cache when fresh); nothing else here requests anything from a provider.
param(
  [Parameter(Mandatory = $true)][ValidateSet('PreCheck', 'PostCheck', 'Update', 'Rollback', 'RecoveryInfo', 'Recover')][string]$Mode,
  [string]$ConfirmWord = '',
  [string]$ApproveCommit = '',
  [string]$ApproveManifest = ''
)
$t1Sha    = 'c1601f538c9953ee3f3bb7f4154763c380f39fb8'
$t1Anchor = '6362f0304ccc6e8b4063a40192e693cb461cb9ab20b5e889bf5ba4790b69da09'
$t1App    = 'C:\apps\us-stock-reader'
$t1Svc    = 'USStockReader'
$t1Base   = 'http://localhost:8000'
$t1Tasks  = @('TRDGO-SmartMove-LIVE-6h', 'TRDGO-SmartMove-MondayBMO', 'TRDGO-SmartMove-Attestation-Renew')
$ErrorActionPreference = 'Stop'

function t1Hash([string]$p) {          # LF-normalised SHA-256, the same rule the package manifest uses
  $enc = [Text.Encoding]::GetEncoding(28591)   # Latin-1 round-trips every byte 1:1 (UTF-8 decoding would alter binary files)
  $b = [IO.File]::ReadAllBytes($p); $s = $enc.GetString($b) -replace "`r`n", "`n"
  $h = [Security.Cryptography.SHA256]::Create(); (($h.ComputeHash($enc.GetBytes($s)) | ForEach-Object { $_.ToString('x2') }) -join '')
}
function t1RawHash([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }
function t1Get([string]$path, [int]$timeout = 25) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri ($t1Base + $path) -TimeoutSec $timeout; return @{ code = [int]$r.StatusCode; body = $r.Content } }
  catch { $c = 0; try { $c = [int]$_.Exception.Response.StatusCode } catch { }; return @{ code = $c; body = '' } }
}
function t1Guard {
  # Smart Money Move: refuse when a collection is running, or any scheduled run is due within 30 minutes, or ran in the last 10
  $lock = Join-Path $t1App 'sm_collector_state\collection.lock'
  if (Test-Path -LiteralPath $lock) { throw 'Smart Money Move collection.lock exists (a run is in progress). Nothing was done.' }
  $now = Get-Date
  foreach ($t in $t1Tasks) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "  guard: task $t not found (skipped)"; continue }
    $info = $task | Get-ScheduledTaskInfo
    if ($task.State -eq 'Running') { throw "Smart Money Move task $t is RUNNING. Nothing was done." }
    if ($info.NextRunTime -and ($info.NextRunTime - $now).TotalMinutes -lt 30 -and ($info.NextRunTime - $now).TotalMinutes -gt -1) { throw ("Smart Money Move task $t is due at " + $info.NextRunTime + " (within 30 minutes). Nothing was done.") }
    if ($info.LastRunTime -and ($now - $info.LastRunTime).TotalMinutes -lt 10 -and ($now - $info.LastRunTime).TotalMinutes -ge 0) { throw ("Smart Money Move task $t ran at " + $info.LastRunTime + " (less than 10 minutes ago). Wait. Nothing was done.") }
    Write-Host ("  guard: $t state=" + $task.State + " last=" + $info.LastRunTime + " next=" + $info.NextRunTime + "  OK")
  }
  $kill = Join-Path $t1App 'sm_evidence_store\KILL'
  Write-Host ('  guard: runner KILL present (idle) = ' + (Test-Path -LiteralPath $kill))
}
function t1Protected([string]$rel, [string[]]$prefixes) { foreach ($p in $prefixes) { if ($rel.StartsWith($p)) { return $true } }; return $false }

try {
  if ($t1Sha.Length -ne 40 -or $t1Anchor.Length -ne 64) { throw 'this script is not pinned to a release (placeholders still present). Nothing was done.' }
  if ($Mode -eq 'Update') {
    if ($ConfirmWord -cne 'YES-INSTALL-T1') { throw 'Update not started: it needs -ConfirmWord YES-INSTALL-T1 typed exactly. Nothing was done.' }
    if ($ApproveCommit -cne $t1Sha)         { throw ('Update not started: -ApproveCommit must be exactly the pinned commit ' + $t1Sha + '. Nothing was done.') }
    if ($ApproveManifest -cne $t1Anchor)    { throw ('Update not started: -ApproveManifest must be exactly the pinned manifest SHA-256 ' + $t1Anchor + '. Nothing was done.') }
  }
  if ($Mode -eq 'Rollback' -and $ConfirmWord -cne 'YES-ROLLBACK-T1') { throw 'Rollback not started: it needs -ConfirmWord YES-ROLLBACK-T1 typed exactly. Nothing was done.' }
  if ($Mode -eq 'Recover'  -and $ConfirmWord -cne 'YES-RECOVER-T1')  { throw 'Recover not started: it needs -ConfirmWord YES-RECOVER-T1 typed exactly. Nothing was done.' }
  if (-not (Test-Path -LiteralPath $t1App)) { throw "application folder not found: $t1App" }
  $t1Py = Join-Path $t1App 'backend\.venv\Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $t1Py)) { throw "python venv not found: $t1Py" }
  if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw 'nssm not found in PATH (the service is restarted with nssm)' }
  $t1Rec = Join-Path $t1App 't1_install_record.json'

  # ---------------- the package (every mode except Rollback/Recover needs it; those use the recorded backup)
  $t1Pkg = $null; $t1Root = $null; $t1Files = @(); $t1Removed = @(); $t1Prot = @()
  if ($Mode -in @('PreCheck', 'Update', 'PostCheck')) {
    $t1Work = 'C:\t1run_' + (Get-Date -Format 'yyyyMMdd_HHmmss'); New-Item -ItemType Directory $t1Work | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com/syed583/trdgo.us/archive/" + $t1Sha + ".zip") -OutFile (Join-Path $t1Work 't1.zip')
    Expand-Archive -LiteralPath (Join-Path $t1Work 't1.zip') -DestinationPath $t1Work -Force
    $t1Root = (Get-ChildItem $t1Work -Directory | Select-Object -First 1).FullName
    if ($t1Root -notlike ("*" + $t1Sha)) { throw "unexpected archive folder: $t1Root" }
    $t1Pkg = Join-Path $t1Root 'deploy\intraday_cleanup'
    $man = Join-Path $t1Pkg 'MANIFEST.sha256'
    if (-not (Test-Path -LiteralPath $man)) { throw 'the downloaded release has no deploy\intraday_cleanup\MANIFEST.sha256' }
    if ((t1Hash $man) -ne $t1Anchor) { throw 'the downloaded release does not match the pinned manifest anchor. Nothing was done.' }
    $bad = 0; $n = 0
    foreach ($l in Get-Content -LiteralPath $man) {
      if ($l -match '^([0-9a-f]{64}) \*(.+)$') { $n++; $f = Join-Path $t1Root ($Matches[2].Trim().Replace('/', '\')); if (-not (Test-Path -LiteralPath $f) -or (t1Hash $f) -ne $Matches[1]) { $bad++; Write-Host ("  MISMATCH " + $Matches[2]) } }
    }
    if ($n -lt 100 -or $bad -ne 0) { throw "package files: $n listed, $bad do not match their SHA-256. Nothing was done." }
    $t1Files = @(Get-Content -LiteralPath (Join-Path $t1Pkg 'backend_files.txt') | Where-Object { $_ })
    $t1Removed = @(Get-Content -LiteralPath (Join-Path $t1Pkg 'removed_files.txt') | Where-Object { $_ })
    $t1Prot = @(Get-Content -LiteralPath (Join-Path $t1Pkg 'protected.txt') | Where-Object { $_ })
    foreach ($f in $t1Files + $t1Removed) { if (t1Protected $f $t1Prot) { throw "the package would touch a PROTECTED path ($f). Nothing was done." }; if (-not $f.StartsWith('backend/')) { throw "unexpected package path $f" } }
    # The Smart Money Move dashboard reader lives in the protected earnings_monitor folder (installed by its own
    # kits). This release carries it UNCHANGED, so that ONE file is placed too, pinned here by its LF SHA-256.
    $t1Dash = 'backend/earnings_monitor/smart_live.py'; $t1DashSha = '14971a1f118db37f9e16dbbec8a89b33f672d02b49a74f99b02fe1615bd65b66'
    if ((t1Hash (Join-Path $t1Root $t1Dash.Replace('/', '\'))) -ne $t1DashSha) { throw 'the dashboard reader in the package does not match its pinned SHA-256. Nothing was done.' }
    $t1Files += $t1Dash
    # The monitor's route file (same protected folder, not owned by any kit); pinned the same way, also unchanged.
    $t1Routes = 'backend/earnings_monitor/routes.py'; $t1RoutesSha = '0b0a519e6cf7a12cb40bf22425bad855fb11051d0e4b0c6af7d99cea4aac3623'
    if ((t1Hash (Join-Path $t1Root $t1Routes.Replace('/', '\'))) -ne $t1RoutesSha) { throw 'the monitor routes file in the package does not match its pinned SHA-256. Nothing was done.' }
    $t1Files += $t1Routes
    # the corrected code itself, not just hashes
    $ar = [IO.File]::ReadAllText((Join-Path $t1Root 'backend\api_routes.py'))
    foreach ($must in @('/model-accuracy/scoreboard-legacy', 'modacc:scoreboard:v2', '_with_data_class', '/admin/provider-ledger', '/market/etf-strip')) { if (-not $ar.Contains($must)) { throw "api_routes.py in the package lacks '$must' (the Phase 2A scoreboard / A.9 patches must survive). Nothing was done." } }
    foreach ($gone in @('/ai-trade/board', '/chatgpt-intraday/board', '/claude-pro-intraday/board')) { if ($ar.Contains($gone)) { throw "api_routes.py still contains the removed route $gone. Nothing was done." } }
    $mn = [IO.File]::ReadAllText((Join-Path $t1Root 'backend\main.py'))
    if (-not $mn.Contains('earnings_monitor') -or $mn.Contains('_start_ai_trade_board')) { throw 'main.py in the package is not the expected one (earnings_monitor registration missing or the v1 board loop still present). Nothing was done.' }
    $dt = [IO.File]::ReadAllText((Join-Path $t1Root 'backend\demo_trade_service.py'))
    foreach ($must in @('reason = "CLEANUP"', 'return "EOD_CLOSE"', 'def entry_window(', 'def _fresh_quote(', 'notional_usd')) { if (-not $dt.Contains($must)) { throw "demo_trade_service.py in the package lacks '$must'. Nothing was done." } }
    if (-not (Test-Path -LiteralPath (Join-Path $t1Pkg 'frontend_dist\index.html'))) { throw 'the package has no built frontend. Nothing was done.' }
    $js = (Get-ChildItem -LiteralPath (Join-Path $t1Pkg 'frontend_dist\assets') -Filter '*.js' | ForEach-Object { [IO.File]::ReadAllText($_.FullName) }) -join ''
    if ($js.Contains('/api/ai-trade/') -or $js.Contains('chatgpt-intraday') -or $js.Contains('claude-pro-intraday')) { throw 'the built frontend still references a removed page / route. Nothing was done.' }
    if (-not $js.Contains('etf-strip') -or -not $js.Contains('sm-monday-bmo-view')) { throw 'the built frontend lacks the ETF strip or the Smart Money Move tabs. Nothing was done.' }
    $pkgReq = t1Hash (Join-Path $t1Root 'backend\requirements.txt'); $liveReq = t1Hash (Join-Path $t1App 'backend\requirements.txt')
    Write-Host ("PASS  release $t1Sha downloaded; manifest anchor matches; $n files match; Phase 2A scoreboard + A.9 patches present; removed routes absent; requirements " + $(if ($pkgReq -eq $liveReq) { 'unchanged (no pip step)' } else { 'CHANGED (pip install will run)' }))
  }

  function t1Targets {   # live-path -> package-path for everything the install writes; plus the removals and the dist
    $t = @()
    foreach ($f0 in $t1Files) { $f = [string]$f0; $t += @{ rel = $f; live = (Join-Path $t1App $f.Replace('/', '\')); src = (Join-Path $t1Root $f.Replace('/', '\')) } }
    return $t
  }
  function t1Diff {
    $new = 0; $chg = 0; $same = 0; $rm = 0
    foreach ($t in (t1Targets)) { if (-not (Test-Path -LiteralPath $t.live)) { $new++ } elseif ((t1Hash $t.live) -ne (t1Hash $t.src)) { $chg++ } else { $same++ } }
    foreach ($r in $t1Removed) { if (Test-Path -LiteralPath (Join-Path $t1App $r.Replace('/', '\'))) { $rm++ } }
    return @{ new = $new; changed = $chg; same = $same; to_remove = $rm }
  }
  function t1Backup([string]$dir) {
    New-Item -ItemType Directory -Force $dir | Out-Null
    $entries = @(); $newFiles = @()
    foreach ($t in (t1Targets)) {
      if (Test-Path -LiteralPath $t.live) { $dst = Join-Path $dir $t.rel.Replace('/', '\'); New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item -LiteralPath $t.live -Destination $dst; $entries += @{ path = $t.rel; sha256 = (t1RawHash $t.live) } }
      else { $newFiles += $t.rel }
    }
    foreach ($r in $t1Removed) { $live = Join-Path $t1App $r.Replace('/', '\'); if (Test-Path -LiteralPath $live) { $dst = Join-Path $dir $r.Replace('/', '\'); New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item -LiteralPath $live -Destination $dst; $entries += @{ path = $r; sha256 = (t1RawHash $live) } } }
    $dist = Join-Path $t1App 'frontend\dist'
    if (Test-Path -LiteralPath $dist) { Copy-Item -LiteralPath $dist -Destination (Join-Path $dir 'frontend\dist') -Recurse -Force; foreach ($f in Get-ChildItem -LiteralPath $dist -Recurse -File) { $rel = 'frontend/dist/' + $f.FullName.Substring($dist.Length + 1).Replace('\', '/'); $entries += @{ path = $rel; sha256 = (t1RawHash $f.FullName) } } }
    ($entries | ConvertTo-Json -Depth 3) | Set-Content -LiteralPath (Join-Path $dir 'backup_manifest.json') -Encoding UTF8
    $newFiles | Set-Content -LiteralPath (Join-Path $dir '_new_files.txt') -Encoding UTF8
    # verify byte for byte
    foreach ($e in $entries) { $b = Join-Path $dir (t1PathOf $e).Replace('/', '\'); if (-not (Test-Path -LiteralPath $b) -or (t1RawHash $b) -ne $e.sha256) { throw "backup verification failed for $($e.path). Nothing was changed on the server." } }
    Write-Host ("  backup verified: " + $entries.Count + " files in $dir (" + $newFiles.Count + " package files are new on this server)")
  }
  function t1PathOf($e) { if ($e.path -is [string]) { return [string]$e.path } else { return [string]$e.path.value } }   # older manifests wrote the path as an object (PSPath note properties)
  function t1Restore([string]$dir) {
    $entries = Get-Content -LiteralPath (Join-Path $dir 'backup_manifest.json') -Raw | ConvertFrom-Json
    foreach ($e in @($entries)) { $b = Join-Path $dir (t1PathOf $e).Replace('/', '\'); if (-not (Test-Path -LiteralPath $b) -or (t1RawHash $b) -ne $e.sha256) { throw "the backup does not verify ($(t1PathOf $e)); refusing to restore from it" } }
    $distLive = Join-Path $t1App 'frontend\dist'; if (Test-Path -LiteralPath $distLive) { Remove-Item -LiteralPath $distLive -Recurse -Force }
    foreach ($e in @($entries)) { $live = Join-Path $t1App (t1PathOf $e).Replace('/', '\'); New-Item -ItemType Directory -Force (Split-Path $live) | Out-Null; Copy-Item -LiteralPath (Join-Path $dir (t1PathOf $e).Replace('/', '\')) -Destination $live -Force }
    $nf = Join-Path $dir '_new_files.txt'
    if (Test-Path -LiteralPath $nf) { foreach ($rel in @(Get-Content -LiteralPath $nf)) { if ($rel) { $live = Join-Path $t1App $rel.Replace('/', '\'); if (Test-Path -LiteralPath $live) { Remove-Item -LiteralPath $live -Force } } } }
    Write-Host ("  restored " + @($entries).Count + " files from $dir")
  }
  function t1Restart([string]$why) {
    Write-Host ("  restarting $t1Svc ($why)"); & nssm restart $t1Svc | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Seconds 3; $r = t1Get '/health' 10; if ($r.code -eq 200 -and $r.body.Contains('"ok"')) { $ok = $true; break } }
    if (-not $ok) { throw 'the service did not answer /health (public, includes a DB check) within 90 s after the restart' }
    Write-Host '  /health 200 (database connected)'
  }
  function t1Health {   # auth is enabled on the server: only /health and /safety are public; every /api/* route answers 401 without a login
    $r = t1Get '/health' 30; if ($r.code -ne 200 -or -not $r.body.Contains('"ok"')) { throw ('/health answered ' + $r.code) }; Write-Host '  /health 200 (database connected)'
    $r = t1Get '/safety' 30; if ($r.code -ne 200 -or -not $r.body.Contains('"commit"')) { throw ('/safety answered ' + $r.code + ' (the new build serves this route; the old build returned the SPA page)') }; Write-Host ('  /safety 200: ' + $r.body)
    $r = t1Get '/auth/status' 30; if ($r.code -ne 200) { throw ('/auth/status answered ' + $r.code) }; Write-Host '  /auth/status 200 (auth router mounted)'
    foreach ($gone in @('/api/ai-trade/board', '/api/chatgpt-intraday/board')) { $r = t1Get $gone 10; if ($r.code -eq 200) { throw "$gone still answers 200: the old code is still running" } }
    Write-Host '  removed routes answer non-200 (login-protected routes are checked in the browser: scoreboard-legacy, earnings-monitor/health, smart-move/live, etf-strip, strategy-trades)'
    $rc = & $t1Py (Join-Path $t1App 'backend\release_check.py'); if ($LASTEXITCODE -ne 0) { throw ('release_check.py FAILED on the installed tree: ' + ($rc -join ' ')) }; Write-Host '  release_check.py PASS'
  }
  function t1Newest { @(Get-ChildItem -LiteralPath $t1App -Directory -Filter '_backup_t1_*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1) }

  switch ($Mode) {
    'PreCheck' {
      Write-Host '--- 1/3  Smart Money Move schedule guard'; t1Guard
      Write-Host '--- 2/3  what the install would change (read only)'
      $d = t1Diff; Write-Host ("  backend files: " + $d.changed + " changed, " + $d.new + " new, " + $d.same + " identical; retired modules present to remove: " + $d.to_remove + "; frontend\dist replaced as a whole")
      Write-Host ("  service: " + (Get-Service $t1Svc).Status + "; installed commit: " + (git -C $t1App rev-parse HEAD) + "; free disk C: " + [math]::Round((Get-PSDrive C).Free / 1GB, 1) + " GB")
      Write-Host '--- 3/3  release_check on the package tree'
      $rc = & $t1Py (Join-Path $t1Root 'backend\release_check.py'); if ($LASTEXITCODE -ne 0) { throw 'release_check.py fails on the package tree' }; Write-Host ('  ' + ($rc -join ' '))
      Write-Host 'RESULT: PRECHECK PASS - nothing in production was changed.'
    }
    'Update' {
      Write-Host '--- M UPDATE (all confirmations matched)'; t1Guard
      if (Test-Path -LiteralPath $t1Rec) { throw 'an M install record already exists; run PostCheck, or Rollback first. Nothing was done.' }
      $bk = Join-Path $t1App ('_backup_t1_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
      Write-Host '--- backup'; t1Backup $bk
      Write-Host '--- apply'
      try {
        foreach ($t in (t1Targets)) { New-Item -ItemType Directory -Force (Split-Path $t.live) | Out-Null; Copy-Item -LiteralPath $t.src -Destination $t.live -Force }
        foreach ($r in $t1Removed) { $live = Join-Path $t1App $r.Replace('/', '\'); if (Test-Path -LiteralPath $live) { Remove-Item -LiteralPath $live -Force } }
        $distLive = Join-Path $t1App 'frontend\dist'; if (Test-Path -LiteralPath $distLive) { Remove-Item -LiteralPath $distLive -Recurse -Force }
        Copy-Item -LiteralPath (Join-Path $t1Pkg 'frontend_dist') -Destination $distLive -Recurse
        if ($pkgReq -ne $liveReq) { Write-Host '  pip install -r requirements.txt'; & $t1Py -m pip install -q -r (Join-Path $t1App 'backend\requirements.txt'); if ($LASTEXITCODE -ne 0) { throw 'pip install failed' } }
        @{ commit = $t1Sha; manifest = $t1Anchor; backup_path = $bk; at = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $t1Rec -Encoding UTF8
        t1Restart 'new build'
        t1Health
      } catch {
        Write-Host ('!!! install failed: ' + $_.Exception.Message + ' -- rolling back automatically') -ForegroundColor Red
        t1Restore $bk; Remove-Item -LiteralPath $t1Rec -Force -ErrorAction SilentlyContinue; t1Restart 'rollback'
        throw ('UPDATE FAILED AND WAS ROLLED BACK: ' + $_.Exception.Message)
      }
      Write-Host 'RESULT: UPDATE COMPLETED. Now run -Mode PostCheck (read only).'
    }
    'PostCheck' {
      $d = t1Diff; if ($d.changed -ne 0 -or $d.new -ne 0 -or $d.to_remove -ne 0) { throw ("PostCheck: " + $d.changed + " changed / " + $d.new + " missing / " + $d.to_remove + " retired modules still present") }
      Write-Host ("  every package file is in place (" + $d.same + " identical), retired modules gone, install record " + $(if (Test-Path -LiteralPath $t1Rec) { 'present' } else { 'MISSING' }))
      t1Health
      Write-Host 'RESULT: POSTCHECK PASS - nothing in production was changed.'
    }
    'Rollback' {
      if (-not (Test-Path -LiteralPath $t1Rec)) { throw 'no M install record: nothing to roll back (use RecoveryInfo for an interrupted install). Nothing was done.' }
      $rec = Get-Content -LiteralPath $t1Rec -Raw | ConvertFrom-Json
      Write-Host ('--- ROLLBACK (confirmed) from ' + $rec.backup_path); t1Restore $rec.backup_path
      Move-Item -LiteralPath $t1Rec -Destination ($t1Rec + '.rolled_back_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
      t1Restart 'rollback'
      $r = t1Get '/health' 30; Write-Host ('  /health ' + $r.code)
      Write-Host 'RESULT: ROLLBACK COMPLETED (the previous build is running again).'
    }
    'RecoveryInfo' {
      $bk = t1Newest; if (-not $bk) { Write-Host 'INFO  no M backup folder exists: nothing was ever started. RESULT: NOTHING TO RECOVER.'; break }
      $mp = Join-Path $bk.FullName 'backup_manifest.json'
      if (-not (Test-Path -LiteralPath $mp)) { Write-Host 'The backup has no manifest: the update stopped while still making its backup, BEFORE changing anything. RESULT: NOTHING TO RECOVER.'; break }
      $entries = Get-Content -LiteralPath $mp -Raw | ConvertFrom-Json; $ok = $true; $differs = 0
      foreach ($e in @($entries)) { $b = Join-Path $bk.FullName (t1PathOf $e).Replace('/', '\'); if (-not (Test-Path -LiteralPath $b) -or (t1RawHash $b) -ne $e.sha256) { $ok = $false }; $live = Join-Path $t1App (t1PathOf $e).Replace('/', '\'); if (-not (Test-Path -LiteralPath $live) -or (t1RawHash $live) -ne $e.sha256) { $differs++ } }
      $recorded = $false; if (Test-Path -LiteralPath $t1Rec) { $recorded = ((Get-Content -LiteralPath $t1Rec -Raw | ConvertFrom-Json).backup_path.TrimEnd('\') -eq $bk.FullName.TrimEnd('\')) }
      Write-Host ('newest backup ' + $bk.FullName + '  verifies=' + $ok + '  live files differing=' + $differs + '  recorded as a completed install=' + $recorded)
      if (-not $ok) { throw 'RESULT: the backup does NOT verify. Do not recover from it. Send this output.' }
      if ($recorded) { Write-Host 'The last update COMPLETED (the record points at this backup). To undo it use -Mode Rollback. RESULT: NO INTERRUPTED INSTALL.'; break }
      if ($differs -eq 0) { Write-Host 'The live files are identical to the backup. RESULT: NOTHING TO RECOVER.'; break }
      Write-Host 'RESULT: AN INTERRUPTED INSTALL IS LIKELY. To restore the pre-update state run:'
      Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File C:\t1_run.ps1 -Mode Recover -ConfirmWord YES-RECOVER-T1'
    }
    'Recover' {
      $bk = t1Newest; if (-not $bk -or -not (Test-Path -LiteralPath (Join-Path $bk.FullName 'backup_manifest.json'))) { throw 'Recover refused: no verified M backup. Nothing was changed.' }
      if (Test-Path -LiteralPath $t1Rec) { $rp = (Get-Content -LiteralPath $t1Rec -Raw | ConvertFrom-Json).backup_path; if ($rp.TrimEnd('\') -eq $bk.FullName.TrimEnd('\')) { throw 'Recover refused: the last update completed. Use -Mode Rollback. Nothing was changed.' } }
      Write-Host ('--- RECOVER (confirmed): restoring ' + $bk.FullName); t1Restore $bk.FullName
      Remove-Item -LiteralPath $t1Rec -Force -ErrorAction SilentlyContinue
      t1Restart 'recovery'
      Write-Host 'RESULT: RECOVERY COMPLETED. Run -Mode PreCheck (read only) to confirm.'
    }
  }
}
catch {
  Write-Host ''; Write-Host ('RESULT: STOPPED - ' + $_.Exception.Message) -ForegroundColor Red
  Write-Host 'Nothing was repaired automatically beyond what is printed above. Copy ALL the output back.'
  exit 1
}
